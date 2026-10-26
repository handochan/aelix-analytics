"""PR3 pure completion/binding contract; these cases do not prove actual saved build yet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aelix_analytics import relations
from aelix_analytics.publishing.basis import content_basis

PID = "pub-" + "0" * 26
HASH = "a" * 64


def completion(seq=2):
    return {
        "schema": relations.EVENT_V2,
        "seq": seq,
        "id": f"e{seq}",
        "recorded_at": "2026-10-19T00:00:00+00:00",
        "kind": "publication",
        "actor": "human",
        "text": "Built publication",
        "refs": [],
        "details": {
            "publication_id": PID,
            "format": relations.PUBLICATION_V1,
            "package_sha256": HASH,
            "content_sha256": HASH,
            "profile": {"id": "read", "rules_version": 1},
            "scope": "project",
            "basis": {"as_of_seq": seq - 1, "content_seq": 0, "context_version": None},
            "analysis_state": {"value": "in_progress", "basis": "declared"},
            "exclusions": [],
            "location": ".analytics/publications/" + PID,
            "checks": {
                "format": 1,
                "result": "pass",
                "inspection": {"inspected": 1, "pinned_scanned": 0, "not_inspectable": 0},
            },
        },
    }


def journal(*rows):
    return relations.Journal(b"\n".join(json.dumps(r).encode() for r in rows) + b"\n")


def reference(digest=HASH):
    ref = {
        "type": "publication",
        "publication_id": PID,
        "relation": "cites",
        "basis": "declared",
        "version_binding": "author",
    }
    if digest is not None:
        ref["digest"] = {"sha256": digest}
    return ref


def model(tmp_path, *rows):
    return relations.ReadModel(tmp_path, journal(*rows), alive=lambda _: False, sha=lambda _: None)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.pop("checks"),
        lambda d: d.update(format="analytics.publication/99"),
        lambda d: d.update(publication_id="elsewhere"),
        lambda d: d.update(package_sha256=None),
        lambda d: d["profile"].update(rules_version=True),
        lambda d: d["profile"].update(id=[]),
        lambda d: d["basis"].update(as_of_seq=True),
        lambda d: d["basis"].update(content_seq=5),
        lambda d: d["basis"].update(context_version="../private"),
        lambda d: d.update(analysis_state={"value": "complete", "basis": "observed"}),
        lambda d: d.update(exclusions=[{"category": "run", "identity": "private"}]),
        lambda d: d.update(location="/tmp/package"),
        lambda d: d["checks"]["inspection"].update(not_inspectable=1),
        lambda d: d["checks"]["inspection"].update(inspected=False),
        lambda d: d.update(unknown=float("nan")),
    ],
)
def test_malformed_publication_completion_is_not_an_understood_binding(change):
    event = completion()
    change(event["details"])
    assert relations.publication_event_problem(event)
    basis, excluded = content_basis(journal(event), validate_reference=relations.reference_problem)
    assert basis.content.content_seq == 2 and not excluded


def test_context_and_exclusions_use_the_existing_typed_reference_contract():
    event = completion()
    event["details"]["exclusions"] = [
        {
            "type": "event",
            "event_id": "e1",
            "relation": "about",
            "basis": "declared",
            "version_binding": "author",
        }
    ]
    assert relations.publication_event_problem(event) is None
    event["details"]["basis"]["context_version"] = "context.md"
    assert relations.publication_event_problem(event)
    event["refs"] = [
        {
            "type": "context_version",
            "version": "context.md",
            "digest": {"sha256": HASH},
            "relation": "includes",
            "basis": "observed",
            "version_binding": "author",
        }
    ]
    assert relations.publication_event_problem(event) is None
    event["refs"][0]["basis"] = "declared"
    assert relations.publication_event_problem(event)


def test_registered_strategy_identity_is_preserved_without_a_new_public_cli_profile():
    event = completion()
    event["details"]["profile"]["id"] = "recording"
    assert relations.publication_event_problem(event) is None
    basis, excluded = content_basis(journal(event), validate_reference=relations.reference_problem)
    assert basis.content.content_seq == 0 and excluded == {1}


def test_publication_reference_checks_recorded_identity_and_historical_boundary(tmp_path):
    event = completion()
    r = model(tmp_path, event)
    assert r.reference(reference())["state"] == "resolved"
    assert r.reference(reference(), before=2)["state"] == "missing"
    for digest in (None, "b" * 64):
        judged = r.reference(reference(digest))
        assert judged["state"] == "missing" and judged["reason"] == "digest_mismatch"
    assert model(tmp_path).reference(reference())["reason"] == "record_absent"
    duplicate = completion(seq=3)
    r = model(tmp_path, event, duplicate)
    assert r.reference(reference())["state"] == "ambiguous"
    assert r.reference(reference(), before=3)["state"] == "resolved"
    invalid = completion()
    invalid["details"]["format"] = "analytics.publication/99"
    assert model(tmp_path, invalid).reference(reference())["state"] == "unsupported"
    invalid = completion()
    invalid["schema"] = "analytics.event/99"
    assert model(tmp_path, invalid).reference(reference())["state"] == "unsupported"


def test_publication_reference_does_not_inspect_or_depend_on_folder_bytes(tmp_path, monkeypatch):
    folder = tmp_path / ".analytics/publications" / PID
    folder.mkdir(parents=True)
    manifest = folder / "publication.json"
    manifest.write_bytes(b"not a package")
    r = model(tmp_path, completion())
    manifest.unlink()
    folder.rmdir()

    def forbidden(*args, **kwargs):
        raise AssertionError("binding reference consulted file bytes")

    for name in ("read_bytes", "read_text", "exists", "stat", "open"):
        monkeypatch.setattr(Path, name, forbidden)
    assert r.reference(reference())["state"] == "resolved"


def test_bad_completion_header_and_recovered_rows_cannot_resolve(tmp_path):
    for field, value in [
        ("actor", None),
        ("recorded_at", "2026-10-19"),
        ("refs", {}),
        ("id", "e99"),
        ("id", []),
        ("id", {}),
    ]:
        event = completion()
        event[field] = value
        assert model(tmp_path, event).reference(reference())["state"] == "unsupported"
    event = completion()
    recovered = relations.Journal(b"broken" + json.dumps(event).encode() + b"\n")
    assert recovered.recovered
    r = relations.ReadModel(tmp_path, recovered, alive=lambda _: False, sha=lambda _: None)
    assert r.reference(reference())["state"] == "unsupported"


def test_shared_writer_admits_only_the_build_completion_and_preserves_oversize_refusal(tmp_path):
    from aelix_analytics import core, writer

    origin = core.Origin("human", "sdk")
    p = core.init_project(tmp_path / "project", title="Public", origin=origin)
    # A project made before the publication writer: a new one already asks for this kit's version.
    value = json.loads(p.marker.read_bytes())
    value["min_writer_version"] = "0.25.0"
    p.marker.write_bytes(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    w = writer.Writer(
        p.root,
        platform=p.platform,
        origin=origin,
        writer_version=relations.PUBLICATION_WRITER_SINCE,
    )
    original = p.journal.read_bytes()
    marker = p.marker.read_bytes()
    with w.locked("write") as held:
        event = completion(held.next_seq)
        with pytest.raises(ValueError, match="build application"):
            held.append("publication", event["text"], refs=event["refs"], details=event["details"])
    with w.locked("build_publication") as held:
        event = completion(held.next_seq)
        event["details"]["exclusions"] = [
            {
                "type": "event",
                "event_id": f"e{n + 1}",
                "relation": "about",
                "basis": "declared",
                "version_binding": "author",
            }
            for n in range(1000)
        ]
        with pytest.raises(writer.AnalyticsError):
            held.append("publication", event["text"], refs=event["refs"], details=event["details"])
    assert p.journal.read_bytes() == original and p.marker.read_bytes() == marker
    with w.locked("build_publication") as held:
        event = completion(held.next_seq)
        written = held.append(
            "publication", event["text"], refs=event["refs"], details=event["details"]
        )
    assert written["actor"] == origin.actor
    assert (
        json.loads(p.marker.read_bytes())["min_writer_version"]
        == relations.PUBLICATION_WRITER_SINCE
    )
    with pytest.raises(writer.AnalyticsError) as caught:
        writer.Writer(p.root, platform=p.platform, origin=origin, writer_version="0.23.0").admit(
            "build_publication"
        )
    assert caught.value.code == "writer_too_old"


def test_malformed_publication_reference_uses_common_shape_validation(tmp_path):
    r = model(tmp_path, completion())
    for value in ([], {}, None, 1):
        ref = reference()
        ref["publication_id"] = value
        assert r.reference(ref)["state"] == "unsupported"
    ref = reference()
    ref["version_binding"] = "none"
    assert r.reference(ref)["state"] == "unsupported"
