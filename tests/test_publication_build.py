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


def test_manifest_uses_opaque_package_keys_when_an_included_figure_has_an_excluded_run(tmp_path):
    import json

    from aelix_analytics import runner
    from aelix_analytics.publishing.model import ItemKey
    from aelix_analytics.publishing.projection import ShareRequest

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    (p.root / "source.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>Public</text></svg>', encoding="utf-8"
    )
    (p.scripts / "show.py").write_text(
        'from pathlib import Path\nimport aelix_analytics as a\na.visualize(Path("source.svg"),"one",title="Public figure")\n',
        encoding="utf-8",
    )
    rid = runner.execute(p, script="scripts/show.py", title="Private run details", origin=ORIGIN)[
        "run"
    ]["id"]
    request = ShareRequest(exclude=frozenset({ItemKey("run", rid)}))
    _preview, view = build.preview_package(p, request)
    assert any(name.endswith(".svg") for name in view)
    answer = build.build_publication(p, request, origin=ORIGIN)
    folder = p.root / answer["path"]
    assert rid.encode() not in b"".join(
        path.read_bytes() for path in folder.rglob("*") if path.is_file()
    )
    manifest = json.loads((folder / "publication.json").read_bytes())
    assert not any(row["category"] == "run" for row in manifest["included"])
    assert _publications(p)[0]["details"]["exclusions"][0]["run_id"] == rid
    assert checks.verify_package(folder, binding=_publications(p)[0]["details"])["ok"]


@pytest.mark.parametrize("after_write", [False, True])
def test_binding_keyboard_interrupt_preserves_record_or_unbound_folder(
    tmp_path, monkeypatch, after_write
):
    import json

    from aelix_analytics import writer

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    original = writer.append_line

    def interrupt(path, line):
        if json.loads(line)["kind"] == "publication":
            if after_write:
                original(path, line)
            raise KeyboardInterrupt
        return original(path, line)

    monkeypatch.setattr(writer, "append_line", interrupt)
    if after_write:
        result = build.build_publication(p, origin=ORIGIN)
        assert (
            result["interrupted"]
            and result["binding_recovered"]
            and result["recording_status"] == "complete"
        )
        path = p.root / result["path"]
    else:
        with pytest.raises(PublicationError) as error:
            build.build_publication(p, origin=ORIGIN)
        assert error.value.details["binding"] == "unbound" and error.value.details["interrupted"]
        path = p.root / error.value.details["path"]
    assert checks.verify_package(path)["ok"] and len(_publications(p)) == int(after_write)


def test_uncertain_reread_failure_is_unknown_and_retains_folder(tmp_path, monkeypatch):
    import json

    from aelix_analytics import writer

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    read = writer._journal_bytes
    append = writer.append_line
    failed = False

    def unreadable(path):
        if failed:
            raise PermissionError("injected reread failure")
        return read(path)

    def fail(path, line):
        nonlocal failed
        if json.loads(line)["kind"] == "publication":
            failed = True
            raise OSError("append result unavailable")
        return append(path, line)

    monkeypatch.setattr(writer, "_journal_bytes", unreadable)
    monkeypatch.setattr(writer, "append_line", fail)
    with pytest.raises(PublicationError) as error:
        build.build_publication(p, origin=ORIGIN)
    assert error.value.details["binding"] == "unknown"
    assert checks.verify_package(p.root / error.value.details["path"])["ok"]


def test_reread_after_commit_does_not_write_admit_a_future_marker(tmp_path, monkeypatch):
    import json

    from aelix_analytics import writer

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    append = writer.append_line

    def future(path, line):
        append(path, line)
        if json.loads(line)["kind"] == "publication":
            marker = json.loads((p.root / "analytics.json").read_bytes())
            marker["min_writer_version"] = "999.0.0"
            (p.root / "analytics.json").write_text(json.dumps(marker), encoding="utf-8")
            raise OSError("response lost after a future writer upgraded")

    monkeypatch.setattr(writer, "append_line", future)
    result = build.build_publication(p, origin=ORIGIN)
    assert result["recording_status"] == "complete" and result["binding_recovered"]
    assert json.loads((p.root / "analytics.json").read_bytes())["min_writer_version"] == "999.0.0"


def test_cleanup_refuses_replaced_folder_and_preserves_other_builds(tmp_path, monkeypatch):
    from aelix_analytics.publishing import package

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    old = build.build_publication(p, origin=ORIGIN)
    kept = tmp_path / "original-owned-folder"

    def replace_folder(path, files):
        path.rename(kept)
        path.mkdir()
        (path / "foreign.txt").write_bytes(b"preserve")
        raise PublicationError("package_check_failed", "injected replacement")

    monkeypatch.setattr(package, "write_files", replace_folder)
    with pytest.raises(PublicationError) as error:
        build.build_publication(p, origin=ORIGIN)
    assert "identity changed" in error.value.details["cleanup_error"]
    assert (
        p.root / error.value.details["cleanup_path"] / "foreign.txt"
    ).read_bytes() == b"preserve"
    assert kept.exists() and checks.verify_package(p.root / old["path"])["ok"]


def test_known_publication_identity_cannot_be_reused_after_folder_removal(tmp_path, monkeypatch):
    import shutil

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    old = build.build_publication(p, origin=ORIGIN)
    shutil.rmtree(p.root / old["path"])
    monkeypatch.setattr(build, "new_publication_id", lambda: old["publication_id"])
    with pytest.raises(PublicationError, match="already exists"):
        build.build_publication(p, origin=ORIGIN)
    assert len(_publications(p)) == 1 and not (p.root / old["path"]).exists()


@pytest.mark.skipif(
    __import__("os").name == "nt", reason="symlink setup requires privileges on Windows"
)
def test_managed_publication_link_is_refused_before_any_target_write(tmp_path):
    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    target = tmp_path / "outside"
    target.mkdir()
    (p.root / ".analytics/publications").symlink_to(target, target_is_directory=True)
    with pytest.raises(PublicationError):
        build.build_publication(p, origin=ORIGIN)
    assert list(target.iterdir()) == [] and not _publications(p)


@pytest.mark.parametrize("after_bind", [False, True])
def test_process_kill_before_or_after_binding_preserves_observable_commit(tmp_path, after_bind):
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    from aelix_analytics.publishing import state

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    ready = tmp_path / "ready"
    script = """
import sys,time,json
from pathlib import Path
from aelix_analytics import core,writer
from aelix_analytics.publishing import build,checks
project=core.Project(sys.argv[1]); ready=Path(sys.argv[2]); after=sys.argv[3]=='True'
def wait():
 ready.write_bytes(b'ready')
 time.sleep(90)
if after:
 original=writer.append_line
 def append(path,line):
  original(path,line)
  if json.loads(line)['kind']=='publication': wait()
 writer.append_line=append
else:
 original=checks.verify_package
 def verify(path,*args,**kwargs):
  result=original(path,*args,**kwargs)
  if isinstance(path,Path) and path.is_dir(): wait()
  return result
 checks.verify_package=verify
build.build_publication(project,origin=core.Origin('human','cli'))
"""
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(p.root), str(ready), str(after_bind)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), proc.communicate(timeout=2)
        proc.kill()
        proc.communicate(timeout=10)
        assert proc.returncode != 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)
    rows = state.list_publications(core.read_model(p))["publications"]
    assert len(rows) == 1 and rows[0]["folder_status"] == "same"
    assert rows[0]["recording_status"] == ("complete" if after_bind else "incomplete")
    assert len(_publications(p)) == int(after_bind)


def test_two_process_builds_serialize_snapshot_through_binding(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    command = [sys.executable, "-m", "aelix_analytics", "--project", str(p.root), "build"]
    processes = [
        subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(2)
    ]
    results = []
    try:
        for process in processes:
            out, err = process.communicate(timeout=30)
            assert process.returncode == 0, err.decode()
            results.append(json.loads(out))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
    results.sort(key=lambda r: r["record"]["seq"])
    assert results[1]["basis"]["as_of_seq"] >= results[0]["record"]["seq"]
    assert results[1]["basis"]["content_seq"] >= results[0]["basis"]["content_seq"]
    assert results[1]["content_sha256"] == results[0]["content_sha256"]


def test_interrupt_during_uncertain_reread_keeps_unknown_result_and_interrupt_status(
    tmp_path, monkeypatch
):
    import json

    from aelix_analytics import writer

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    read, append = writer._journal_bytes, writer.append_line
    failed = False

    def interrupted_read(path):
        if failed:
            raise KeyboardInterrupt
        return read(path)

    def lose_response(path, line):
        nonlocal failed
        if json.loads(line)["kind"] == "publication":
            failed = True
            raise OSError("append result lost")
        return append(path, line)

    monkeypatch.setattr(writer, "_journal_bytes", interrupted_read)
    monkeypatch.setattr(writer, "append_line", lose_response)
    with pytest.raises(PublicationError) as caught:
        build.build_publication(p, origin=ORIGIN)
    assert caught.value.details["binding"] == "unknown" and caught.value.details["interrupted"]
    assert checks.verify_package(p.root / caught.value.details["path"])["ok"]


def test_zip_cleanup_never_deletes_a_replacement_target(tmp_path, monkeypatch):
    from pathlib import Path

    from aelix_analytics.publishing import package

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    result = build.build_publication(p, origin=ORIGIN)
    target, moved = tmp_path / "transport.zip", tmp_path / "owned.zip"
    original = checks.verify_package

    def replaced(path, *args, **kwargs):
        if Path(path) == target:
            target.rename(moved)
            target.write_bytes(b"foreign")
            raise PublicationError("package_check_failed", "injected ZIP replacement")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(checks, "verify_package", replaced)
    with pytest.raises(PublicationError) as caught:
        package.write_zip(
            p.root / result["path"],
            target,
            project_root=p.root,
            binding=_publications(p)[0]["details"],
        )
    assert target.read_bytes() == b"foreign" and moved.exists()
    assert "identity changed" in caught.value.details["cleanup_error"]


def test_actual_build_large_exclusion_record_cleans_its_folder_without_raising_floor(tmp_path):
    import json

    from aelix_analytics import relations

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    marker_path = p.root / "analytics.json"
    marker = json.loads(marker_path.read_bytes())
    marker.update(min_writer_version="0.23.0", kit_version="0.23.0")
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    rows = [
        {
            "schema": "analytics.event/2",
            "seq": n,
            "id": f"e{n}",
            "kind": "finding",
            "actor": "human",
            "recorded_at": "2026-10-19T00:00:00+00:00",
            "text": f"Private synthetic finding number {n}.",
            "refs": [],
        }
        for n in range(1, 501)
    ]
    p.journal.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    with pytest.raises(PublicationError) as caught:
        build.build_publication(p, exclude=[row["id"] for row in rows], origin=ORIGIN)
    assert (
        caught.value.code == "text_too_long"
    )
    assert (
        not _publications(p) and not list((p.root / ".analytics/publications").glob("pub-*"))
    )
    marker = json.loads((p.root / "analytics.json").read_bytes())
    assert relations.version_key(marker["min_writer_version"]) < relations.version_key(relations.PUBLICATION_WRITER_SINCE)


def test_zip_target_cannot_use_another_case_spelling_of_project(tmp_path):
    from aelix_analytics.publishing import package

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    alias = p.root.with_name("PROJECT")
    if not core.same_file(alias, p.root):
        pytest.skip("requires a case-insensitive filesystem")
    result = build.build_publication(p, origin=ORIGIN)
    with pytest.raises(PublicationError) as caught:
        package.write_zip(p.root / result["path"], alias / "inside.zip",
                          project_root=p.root, binding=_publications(p)[0]["details"])
    assert caught.value.code == "zip_path_invalid"
    assert not (p.root / "inside.zip").exists()


@pytest.mark.parametrize("corruption", ["deflate", "utf8_name"])
def test_damaged_zip_is_a_package_failure_without_traceback(tmp_path, corruption):
    import struct
    import zipfile

    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    path = tmp_path / "package.zip"
    build.build_publication(p, origin=ORIGIN, zip_path=path)
    with zipfile.ZipFile(path) as z:
        info = z.infolist()[0]
    raw = bytearray(path.read_bytes())
    offset = info.header_offset
    name_size, extra_size = struct.unpack_from("<HH", raw, offset + 26)
    if corruption == "deflate":
        # Reserved DEFLATE block type: always rejected by zlib, independent of its version.
        raw[offset + 30 + name_size + extra_size] = 7
    else:
        raw[offset + 30] = 255
    path.write_bytes(raw)
    with pytest.raises(PublicationError) as caught:
        checks.verify_package(path)
    assert caught.value.code == "package_check_failed"


def test_publication_status_docs_match_build_commands_and_deferred_scope():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    architecture = (root / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    status = (root / "docs/design/PUBLICATION-BUILD.md").read_text(encoding="utf-8").split("- 작업:")[0]
    assert "manifest 결박·Report 범위는 아직 구현 전" not in architecture
    assert "공유 패키지 생성은 아직 없다" not in status
    for text in (architecture, status):
        assert "PR3" in text and "`analytics build`" in text and "ZIP" in text
        assert "PR4" in text and "PR5" in text and "#21" in text


def test_a_build_waits_a_bounded_time_for_another_build_and_writes_nothing(tmp_path, monkeypatch):
    """BUILD 9.5 R18 with #21's OQ3: the build lock is waited for LOCK_WAIT_SECONDS, then build_busy."""
    p = core.init_project(tmp_path / "p", title="Busy", origin=ORIGIN)
    monkeypatch.setattr(build, "LOCK_WAIT_SECONDS", 0.2)
    lock = p.root / ".analytics/publications/.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    journal = p.journal.read_bytes()
    with p.platform.locks.try_exclusive(lock):
        with pytest.raises(PublicationError) as caught:
            build.build_publication(p, origin=ORIGIN)
    assert caught.value.code == "build_busy"
    assert [path.name for path in lock.parent.iterdir()] == [".lock"]
    assert p.journal.read_bytes() == journal
    assert build.build_publication(p, origin=ORIGIN)["recording_status"] == "complete"


def test_a_folder_that_differs_from_its_manifest_names_the_files(tmp_path):
    p = core.init_project(tmp_path / "p", title="Names", origin=ORIGIN)
    folder = p.root / build.build_publication(p, origin=ORIGIN)["path"]
    (folder / "pages" / "extra.html").write_bytes(b"<p>not listed</p>")
    (folder / "about.html").unlink()
    with pytest.raises(PublicationError) as caught:
        checks.verify_package(folder)
    assert caught.value.details["unlisted"] == ["pages/extra.html"]
    assert caught.value.details["missing"] == ["about.html"]
