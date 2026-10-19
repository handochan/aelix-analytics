"""PR3 assembly/build acceptance; memory-only cases do not claim a stored/bound publication."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aelix_analytics import core
from aelix_analytics.publishing import build, checks
from aelix_analytics.publishing.model import PublicationError, PublicationIdentity
from aelix_analytics.publishing.snapshot import capture_snapshot

ORIGIN = core.Origin("human", "cli")


def test_memory_assembly_keeps_fresh_identity_out_of_content_files(tmp_path):
    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    core.sync_context(p, origin=core.system_origin(ORIGIN))
    snapshot = capture_snapshot(p)
    later = replace(
        snapshot, basis=replace(snapshot.basis, as_of_seq=snapshot.basis.as_of_seq + 100)
    )
    first = PublicationIdentity(
        "pub-" + "0" * 26, "2026-10-19T00:00:00+00:00", snapshot.basis.as_of_seq
    )
    second = PublicationIdentity(
        "pub-" + "1" * 26, "2026-10-20T00:00:00+00:00", later.basis.as_of_seq
    )
    before = {
        x.relative_to(p.root).as_posix(): x.read_bytes() for x in p.root.rglob("*") if x.is_file()
    }
    _, a, _ = build._assemble(p, snapshot=snapshot, pending=False, identity=first)
    _, b, _ = build._assemble(p, snapshot=later, pending=False, identity=second)
    _, preview = build.preview_package(p, snapshot=snapshot)
    assert a.keys() == b.keys() == preview.keys()
    assert {name for name in a if a[name] != b[name]} == {"about.html", "assets/identity.js"}
    assert {name for name in a if a[name] != preview[name]} <= checks.IDENTITY_FILES
    assert checks.content_hash(a) == checks.content_hash(b) == checks.content_hash(preview)
    assert b"null;" in preview["assets/identity.js"]
    assert (
        first.publication_id.encode() in a["about.html"]
        and first.built_at.encode() in a["assets/identity.js"]
    )
    assert before == {
        x.relative_to(p.root).as_posix(): x.read_bytes() for x in p.root.rglob("*") if x.is_file()
    }


def test_memory_assembly_rejects_identity_mismatch_without_writing_a_folder(tmp_path):
    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    snapshot = capture_snapshot(p)
    for value in (
        {"publication_id": "private"},
        PublicationIdentity("pub-" + "0" * 26, "now", snapshot.basis.as_of_seq + 1),
    ):
        with pytest.raises(PublicationError, match="basis differ"):
            build._assemble(p, snapshot=snapshot, pending=False, identity=value)
    assert not (p.root / ".analytics/publications").exists()


def _events(p):
    import json

    return [json.loads(line) for line in p.journal.read_bytes().splitlines() if line.strip()]


def _publications(p):
    return [e for e in _events(p) if e.get("kind") == "publication"]


def test_actual_build_manifest_binding_and_repeated_content(tmp_path):
    from aelix_analytics import relations

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    first = build.build_publication(p, origin=ORIGIN)
    event = _publications(p)[0]
    assert first["ok"] and first["recording_status"] == "complete" and event["actor"] == "human"
    assert event["details"]["package_sha256"] == first["package_sha256"]
    verified = checks.verify_package(p.root / first["path"], binding=event["details"])
    assert verified["ok"] and relations.publication_event_problem(event) is None
    manifest = verified["manifest"]
    assert (
        manifest["checks"]["inspection"]["inspected"]
        + manifest["checks"]["inspection"]["pinned_scanned"]
        == len(manifest["files"]) + 1
    )
    assert all(r["type"] == "context_version" for r in event["refs"])
    second = build.build_publication(p, origin=ORIGIN)
    assert (
        first["publication_id"] != second["publication_id"]
        and first["package_sha256"] != second["package_sha256"]
    )
    assert first["content_sha256"] == second["content_sha256"]
    assert second["basis"]["as_of_seq"] >= first["record"]["seq"]
    assert second["basis"]["content_seq"] == first["basis"]["content_seq"]
    ref = {
        "type": "publication",
        "publication_id": first["publication_id"],
        "digest": {"sha256": first["package_sha256"]},
        "relation": "cites",
        "basis": "declared",
        "version_binding": "author",
    }
    assert core.read_model(p).reference(ref)["state"] == "resolved"


def test_actual_evidence_manifest_keeps_copy_provenance_without_private_paths(tmp_path):
    import json

    from aelix_analytics import runner

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    outside = tmp_path / "private-input.csv"
    outside.write_text("x\n1\n", encoding="utf-8")
    (p.root / "source.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>Public</text></svg>', encoding="utf-8"
    )
    (p.scripts / "show.py").write_text(
        'from pathlib import Path\nimport aelix_analytics as a\na.visualize(Path("source.svg"),"one",title="Public figure")\n',
        encoding="utf-8",
    )
    assert (
        runner.execute(
            p,
            script="scripts/show.py",
            title="Public chart",
            declared_inputs=[str(outside)],
            origin=ORIGIN,
        )["run"]["status"]
        == "ok"
    )
    answer = build.build_publication(p, profile="evidence", origin=ORIGIN)
    folder = p.root / answer["path"]
    manifest = json.loads((folder / "publication.json").read_bytes())
    copied = [row for row in manifest["files"] if row["origin"] == "copied"]
    assert {source["role"] for row in copied for source in row["sources"]} == {
        "code_snapshot",
        "figure",
    }
    assert all(
        source["recorded_sha256"] and source["verified"] == "recorded_hash"
        for row in copied
        for source in row["sources"]
    )
    data = (
        b"".join((folder / row["path"]).read_bytes() for row in manifest["files"])
        + (folder / "publication.json").read_bytes()
    )
    assert str(outside).encode() not in data and str(p.root).encode() not in data
    assert json.loads(p.marker.read_bytes())["project_id"].encode() not in data
    assert checks.verify_package(folder, binding=_publications(p)[0]["details"])["ok"]


def test_actual_plotly_manifest_records_derivation_instead_of_false_raw_copy(tmp_path):
    import json

    from aelix_analytics import runner

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    (p.scripts / "show.py").write_text(
        'import aelix_analytics as a\nimport plotly.graph_objects as go\na.visualize(go.Figure(go.Scatter(y=[1,2])),"one",title="Public figure")\n',
        encoding="utf-8",
    )
    assert (
        runner.execute(p, script="scripts/show.py", title="Public chart", origin=ORIGIN)["run"][
            "status"
        ]
        == "ok"
    )
    answer = build.build_publication(p, origin=ORIGIN)
    manifest = json.loads((p.root / answer["path"] / "publication.json").read_bytes())
    rows = [r for r in manifest["files"] if r["format"] == "payload"]
    assert (
        len(rows) == 1
        and rows[0]["origin"] == "generated"
        and rows[0]["derived_from"][0]["source_sha256"]
    )
    assert not any(
        "source" in source for row in manifest["files"] for source in row.get("derived_from", [])
    )
    assert checks.verify_package(p.root / answer["path"], binding=_publications(p)[0]["details"])[
        "ok"
    ]


def test_actual_build_zip_is_checked_deterministic_and_never_overwrites(tmp_path):
    import zipfile

    from aelix_analytics.publishing import package

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    first = tmp_path / "one.zip"
    result = build.build_publication(p, origin=ORIGIN, zip_path=first)
    assert result["ok"] and first.is_file()
    details = _publications(p)[0]["details"]
    second = tmp_path / "two.zip"
    package.write_zip(p.root / result["path"], second, project_root=p.root, binding=details)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as z:
        assert all(
            i.flag_bits & 0x800 and i.date_time == (1980, 1, 1, 0, 0, 0) and i.create_system == 3
            for i in z.infolist()
        )
        assert not any(i.is_dir() for i in z.infolist())
    before = first.read_bytes()
    with pytest.raises(PublicationError) as caught:
        package.write_zip(p.root / result["path"], first, project_root=p.root, binding=details)
    assert caught.value.code == "zip_path_invalid" and first.read_bytes() == before
    with pytest.raises(PublicationError):
        package.write_zip(
            p.root / result["path"], p.root / "private.zip", project_root=p.root, binding=details
        )
    assert not (p.root / "private.zip").exists()


def test_prebinding_write_failure_only_cleans_its_new_folder(tmp_path, monkeypatch):
    from aelix_analytics.publishing import package

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    complete = build.build_publication(p, origin=ORIGIN)

    def fail(*args, **kwargs):
        raise OSError("injected write failure")

    monkeypatch.setattr(package, "write_files", fail)
    with pytest.raises(PublicationError):
        build.build_publication(p, origin=ORIGIN)
    assert len(_publications(p)) == 1
    assert sorted(x.name for x in (p.root / ".analytics/publications").iterdir()) == [
        ".lock",
        complete["publication_id"],
    ]


@pytest.mark.parametrize("after_write", [False, True])
def test_uncertain_append_rechecks_actual_record_and_never_removes_binding_stage_folder(
    tmp_path, monkeypatch, after_write
):
    import json

    from aelix_analytics import writer

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    original = writer.append_line

    def unreliable(path, line):
        if json.loads(line)["kind"] == "publication":
            if after_write:
                original(path, line)
            raise OSError("lost append response")
        return original(path, line)

    monkeypatch.setattr(writer, "append_line", unreliable)
    if after_write:
        result = build.build_publication(p, origin=ORIGIN)
        assert result["recording_status"] == "complete" and result["binding_recovered"]
        assert len(_publications(p)) == 1
        folder = p.root / result["path"]
    else:
        with pytest.raises(PublicationError) as caught:
            build.build_publication(p, origin=ORIGIN)
        assert (
            caught.value.code == "publication_not_recorded"
            and caught.value.details["binding"] == "unbound"
        )
        folder = p.root / caught.value.details["path"]
        assert not _publications(p)
    assert (folder / "publication.json").is_file() and checks.verify_package(folder)["ok"]


def test_zip_failure_reports_completed_build_without_rollback(tmp_path):
    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    result = build.build_publication(p, origin=ORIGIN, zip_path=p.root / "forbidden.zip")
    assert not result["ok"] and result["recording_status"] == "complete"
    assert result["error"]["code"] == "zip_path_invalid"
    assert len(_publications(p)) == 1 and (p.root / result["path"] / "publication.json").is_file()


def test_recorded_publication_reference_stays_outside_shared_content(tmp_path):
    from aelix_analytics import writer
    from aelix_analytics.publishing.projection import project

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    first = build.build_publication(p, origin=ORIGIN)
    ref = {
        "type": "publication",
        "publication_id": first["publication_id"],
        "digest": {"sha256": first["package_sha256"]},
        "relation": "cites",
        "basis": "declared",
        "version_binding": "author",
    }
    assert core.read_model(p).reference(ref)["state"] == "resolved"
    finding = core.record(
        p,
        "finding",
        "A public finding cites a prior copy.",
        refs=[writer.typed_reference(ref)],
        origin=ORIGIN,
    ).event
    snapshot = capture_snapshot(p)
    projected = project(snapshot)
    item = next(item for item in projected.model.items if item.key.identity == finding["id"])
    assert item.references[0].state == "not_included" and item.references[0].target is None
    second = build.build_publication(p, origin=ORIGIN)
    folder = p.root / second["path"]
    assert first["publication_id"].encode() not in b"".join(
        path.read_bytes() for path in folder.rglob("*") if path.is_file()
    )


def test_saved_build_freezes_selected_strategy_metadata_before_projection(tmp_path):
    import json

    from publication_doubles import RecordingProjection

    class Changing(RecordingProjection):
        id = "recording"
        rules_version = 2

        def project(self, snapshot, request):
            result = super().project(snapshot, request)
            self.id = "changed"
            self.rules_version = 99
            return result

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    answer = build.build_publication(p, projection=Changing(), origin=ORIGIN)
    folder = p.root / answer["path"]
    manifest = json.loads((folder / "publication.json").read_bytes())
    assert (
        manifest["profile"]
        == _publications(p)[0]["details"]["profile"]
        == {"id": "recording", "rules_version": 2}
    )
    assert b"Rules version 2." in (folder / "index.html").read_bytes()
