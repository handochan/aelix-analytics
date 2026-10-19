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
