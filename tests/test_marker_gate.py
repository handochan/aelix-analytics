"""Which format a directory holds and whether this kit writes there (Issue #13 PR 1, and PR 3: the
kit writes analytics.json projects, and a lab.json project is read only — migration_required).

docs/design/RECORD-WRITER-AND-MIGRATION.md 4.3 and 11.2–11.3: every row of the marker table
(T12), the nearest-marker search, and the entry-point matrix (T8) for the kit's command line and the
script API — each write entry point is judged before its first side effect, and where it is refused
(or where a read skips its side writes) the project's tree is the same before and after, to the
byte, the modification time and the lock file. The aelix extension's rows are in
tests/test_extension.py (they need aelix), the human command line's search in tests/test_launcher.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import KIT, LINK_KINDS, dir_link, kit_cli, kit_env, write_sales_csv
from legacy_records import LegacyProject, at

from aelix_analytics import _platform, core, writer

ORIGIN = core.Origin("test", "sdk")
TEMPLATE = KIT / "aelix_analytics" / "template"
GATE_CODES = {"no_project", "project_too_new", "marker_invalid", "marker_conflict",
              "migration_incomplete", "migration_required", "writer_too_old"}

NEW = {"schema": "analytics.project/2", "project_id": "prj_7f3a9c2e41d84b06", "title": "Regional revenue",
       "created_at": "2026-09-01T09:00:00.000+09:00", "min_writer_version": "0.7.0",
       "kit_version": "0.7.0"}


def write_marker(directory: Path, name: str, value) -> None:
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    (directory / name).write_bytes(data)


# --- the marker judgment (T12) -----------------------------------------------------------------

# name -> (lab.json, analytics.json, state, reason, format, write refusal (PR 3) or None)
STATES = {
    "none": (None, None, "none", None, None, "no_project"),
    "lab_project_1": ({"schema": "lab.project/1", "title": "t"}, None, "legacy", None, "lab.project/1",
                      "migration_required"),
    "lab_no_schema": ({"title": "t"}, None, "legacy", "no_schema", "lab.project/1", "migration_required"),
    "lab_unparsable": (b"{not json", None, "legacy", "unparsable", "lab.project/1", "migration_required"),
    "lab_not_object": (b"[1, 2]", None, "legacy", "not_object", "lab.project/1", "migration_required"),
    "lab_project_9": ({"schema": "lab.project/9"}, None, "unknown", None, "lab.project/9",
                      "project_too_new"),
    "lab_other_family": ({"schema": "other.project/1"}, None, "unknown", None, "other.project/1",
                         "project_too_new"),
    "analytics_2": (None, NEW, "current", None, "analytics.project/2", None),
    "analytics_2_too_old": (None, {**NEW, "min_writer_version": "99.0"}, "current", None,
                            "analytics.project/2", "writer_too_old"),
    "analytics_3": (None, {**NEW, "schema": "analytics.project/3"}, "unknown", None,
                    "analytics.project/3", "project_too_new"),
    "analytics_unparsable": (None, b'{"schema": "analytics.pro', "invalid", "unparsable", None,
                             "marker_invalid"),
    "analytics_not_object": (None, b'"analytics.project/2"', "invalid", "not_object", None,
                             "marker_invalid"),
    "analytics_no_schema": (None, {k: v for k, v in NEW.items() if k != "schema"}, "invalid",
                            "missing_field", None, "marker_invalid"),
    "analytics_missing_field": (None, {k: v for k, v in NEW.items() if k != "project_id"}, "invalid",
                                "missing_field", None, "marker_invalid"),
    "analytics_bad_version": (None, {**NEW, "min_writer_version": "v1.0"}, "invalid", "bad_version",
                              None, "marker_invalid"),
    "both": ({"schema": "lab.project/1"}, NEW, "conflict", None, None, "marker_conflict"),
}


@pytest.mark.parametrize("name", STATES)
def test_every_row_of_the_marker_table(tmp_path, name):
    lab, new, state, reason, fmt, refused = STATES[name]
    root = tmp_path / "d"
    root.mkdir()
    if lab is not None:
        write_marker(root, "lab.json", lab)
    if new is not None:
        write_marker(root, "analytics.json", new)
    judged = writer.marker_state(root)
    assert (judged.state, judged.reason, judged.format) == (state, reason, fmt)
    problem = writer.refusal(judged, "record", writer_version=core.KIT_VERSION)
    assert (problem.code if problem else None) == refused
    assert writer.WRITABLE == frozenset({"analytics.project/2"})
    if refused:
        with pytest.raises(core.AnalyticsError) as caught:
            core.admit(root, "record")
        assert caught.value.code == refused
        assert core.admit(root, "a side write", side=True) is None
    else:
        assert core.admit(root, "record").state == state
    if state in ("none", "conflict"):
        with pytest.raises(core.AnalyticsError) as caught:
            core.readable(root, "status")
        assert caught.value.code == refused
    else:
        assert core.readable(root, "status").state == state
    assert sorted(p.name for p in root.iterdir()) == sorted(
        n for n, v in (("lab.json", lab), ("analytics.json", new)) if v is not None)


def test_the_kit_writes_the_new_format_and_reads_a_lab_json_project_only(tmp_path):
    """PR 3 makes WRITABLE {analytics.project/2} (design 4.3, 7): the new format is written when the
    marker's min_writer_version allows this kit, and refused with writer_too_old when it does not
    (ADR-0006 1.3). A lab.json project is migration_required, its message and hint the design's
    (7): the command that copies it, its preview, that the original stays, and what a person does
    in aelix — the same words for a person and a model. The gate itself still takes a set of
    formats (a kit before PR 3 wrote lab.project/1)."""
    root = tmp_path / "d"
    root.mkdir()
    write_marker(root, "analytics.json", NEW)
    assert core.admit(root, "record").state == "current"
    with pytest.raises(core.AnalyticsError) as caught:
        writer.admit(root, "record", writer_version="0.6.0")
    assert caught.value.code == "writer_too_old"
    assert caught.value.details == {"min_writer_version": "0.7.0", "kit_version": "0.6.0"}
    (root / "analytics.json").unlink()
    write_marker(root, "lab.json", {"schema": "lab.project/1"})
    with pytest.raises(core.AnalyticsError) as caught:
        core.admit(root, "record")
    error = caught.value
    assert error.code == "migration_required"
    assert error.message == (f"{root} uses the older record format (lab.json, lab.project/1). This kit "
                             "reads it and does not write to it.")
    assert error.hint == (f"Copy it into the current format with `analytics migrate --to <new empty "
                          f"folder>` run inside {root} (preview with --dry-run). The original stays as "
                          "it is. In aelix, a person runs that in a terminal, then `/analytics use <new "
                          "folder>`.")
    assert error.details == {"marker": "lab.json", "format": "lab.project/1", "operation": "record",
                             "command": "analytics migrate --to DEST"}
    assert core.admit(root, "a side write", side=True) is None
    assert core.readable(root, "status").state == "legacy"
    legacy = frozenset({writer.LEGACY_PROJECT})
    assert writer.admit(root, "record", writer_version="0.7.0", writable=legacy).state == "legacy"


def test_two_markers_say_both_and_choose_neither(tmp_path):
    root = tmp_path / "d"
    root.mkdir()
    write_marker(root, "lab.json", {"schema": "lab.project/1", "title": "old"})
    write_marker(root, "analytics.json", {**NEW, "migration": {"id": "m1", "report": "r.json",
                                                              "report_sha256": "0" * 64}})
    with pytest.raises(core.AnalyticsError) as caught:
        core.admit(root, "record")
    error = caught.value
    assert error.code == "marker_conflict" and "does not choose" in error.hint
    markers = {m["file"]: m for m in error.details["markers"]}
    for name in ("lab.json", "analytics.json"):
        data = (root / name).read_bytes()
        assert markers[name]["sha256"] == hashlib.sha256(data).hexdigest()
        assert markers[name]["bytes"] == len(data)
    assert markers["analytics.json"]["migration"]["id"] == "m1"
    assert markers["lab.json"]["schema"] == "lab.project/1"


def test_a_directory_of_a_marker_s_name_is_no_marker(tmp_path):
    """As before #13 (`is_file`): a directory named lab.json made no project, and does not now."""
    root = tmp_path / "d"
    (root / "lab.json").mkdir(parents=True)
    assert writer.marker_state(root).state == "none"
    assert core.find_project(root) is None or core.find_project(root) != root


def test_a_marker_that_cannot_be_read_now_is_read_again(tmp_path, monkeypatch):
    """design 4.3 and D27: Windows refuses to open a file another process is replacing. A marker
    that fails to open is read again within the replace's budget; one that never opens is
    `invalid` (unreadable) with the system's error, never `none` and never a broken marker."""
    root = tmp_path / "d"
    root.mkdir()
    write_marker(root, "analytics.json", NEW)
    real_open = open
    failures = {"left": 3}

    def flaky(path, *args, **kwargs):
        if Path(path).name == "analytics.json" and failures["left"]:
            failures["left"] -= 1
            raise PermissionError(13, "The process cannot access the file")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(_platform, "REPLACE_POLL_SECONDS", 0.001)
    monkeypatch.setattr("builtins.open", flaky)
    assert writer.marker_state(root).state == "current" and failures["left"] == 0
    failures["left"] = 10_000
    judged = writer.marker_state(root)
    assert (judged.state, judged.reason, judged.marker) == ("invalid", "unreadable", "analytics.json")
    assert "PermissionError" in judged.details["error"]
    with pytest.raises(core.AnalyticsError) as caught:
        core.admit(root, "record")
    assert caught.value.code == "marker_invalid" and caught.value.details["reason"] == "unreadable"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a FIFO is POSIX's")
def test_a_marker_that_is_no_regular_file_is_not_opened(tmp_path):
    """Opening a FIFO waits for a writer: a FIFO under a marker's name is judged without opening it
    (unreadable at once), so no command waits on it."""
    root = tmp_path / "d"
    root.mkdir()
    os.mkfifo(root / "analytics.json")
    judged = writer.marker_state(root)
    assert (judged.state, judged.reason) == ("invalid", "unreadable")
    assert judged.details["error"] == "it is not a regular file"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a FIFO and the links are POSIX's here")
@pytest.mark.parametrize("kind", ["fifo", "loop", "dangling"])
def test_the_kit_refuses_an_odd_marker_below_a_project_from_where_the_launchers_stop(tmp_path, kind):
    """Review of PR 1: the launchers hand the kit the directory they stopped at (`--project`), and
    the kit searches upward from there by its own rule. A FIFO or a link that loops under a marker's
    name stops that search and is refused (marker_invalid), the project above untouched; a link that
    leads nowhere is no marker (stat says it is not there, design 4.3), so the search goes on."""
    outer = tmp_path / "outer"
    inner = outer / "inner"
    core.init_project(outer, template_dir=TEMPLATE, origin=ORIGIN)
    inner.mkdir()
    marker = inner / "analytics.json"
    if kind == "fifo":
        os.mkfifo(marker)
    elif kind == "loop":
        marker.symlink_to(marker.name)
    else:
        marker.symlink_to("nowhere.json")
    before = snapshot(outer)
    code, result = kit_cli(inner, "record", "note", "from below")
    if kind == "dangling":
        assert code == 0 and result["event"]["text"] == "from below", result
        return
    assert (code, result["error"]["code"]) == (1, "marker_invalid"), result
    assert result["error"]["details"]["reason"] == "unreadable"
    assert snapshot(outer) == before


@pytest.mark.parametrize(("value", "own"), [
    (b"pageviews,10\n", True), (b"[1, 2]", True), ({"pageviews": 10}, True), ({"schema": 2}, True),
    ({"schema": "analytics.project/2"}, False), ({**NEW, "min_writer_version": "soon"}, False)])
def test_an_analytics_json_that_names_no_format_may_be_the_person_s_own(tmp_path, value, own):
    """Review of PR 1 (an owner question): an analytics.json that names no project format — not
    JSON, not an object, no `schema` string — is still the nearest marker and refused (D13, design
    4.3), and its hint says it may be a file of the person's own and how to name the project above
    instead. A marker of the format that lacks a field is a broken marker: restore it."""
    root = tmp_path / "d"
    root.mkdir()
    write_marker(root, "analytics.json", value)
    with pytest.raises(core.AnalyticsError) as caught:
        core.admit(root, "record")
    assert caught.value.code == "marker_invalid"
    hint = caught.value.hint
    assert ("a file of your own" in hint) is own
    if own:
        # Each entry point by the way it names a project (fix check of PR 1): `analytics --project`
        # is a usage error, so --project is named only for python -m aelix_analytics.
        assert "AELIX_ANALYTICS_PROJECT=ROOT for the analytics command" in hint
        assert "/analytics use ROOT or --analytics-project ROOT in aelix" in hint
        assert "--project ROOT for python -m aelix_analytics" in hint and "rename it" in hint
    else:
        assert "Restore it from where it came from" in hint


def test_the_judgment_is_read_again_every_time(tmp_path):
    """No cache (design 4.3): a marker changed between two writes is judged as it is now."""
    root = tmp_path / "d"
    root.mkdir()
    write_marker(root, "analytics.json", NEW)
    assert core.admit(root, "record").state == "current"
    write_marker(root, "lab.json", {"schema": "lab.project/1"})
    with pytest.raises(core.AnalyticsError) as caught:
        core.admit(root, "record")
    assert caught.value.code == "marker_conflict"


# --- finding a project (T12) ---------------------------------------------------------------------

def test_the_nearest_marker_decides_whichever_it_is(tmp_path):
    """design 4.3: the first directory up from the start that holds either marker is the project,
    judged afterwards. An analytics.json directory below a lab.json project is that directory —
    never the project above, which a kit before #13 wrote to (the state map's P4) — and the other
    way round as well."""
    old = tmp_path / "old"
    (old / "new" / "deeper").mkdir(parents=True)
    write_marker(old, "lab.json", {"schema": "lab.project/1"})
    write_marker(old / "new", "analytics.json", NEW)
    assert core.find_project(old / "new" / "deeper") == old / "new"
    assert core.find_project(old) == old
    new = tmp_path / "new"
    (new / "old" / "deeper").mkdir(parents=True)
    write_marker(new, "analytics.json", NEW)
    write_marker(new / "old", "lab.json", {"schema": "lab.project/1"})
    assert core.find_project(new / "old" / "deeper") == new / "old"
    assert core.find_project(new) == new
    both = tmp_path / "both"
    (both / "x").mkdir(parents=True)
    write_marker(both, "analytics.json", NEW)
    write_marker(both, "lab.json", {"schema": "lab.project/1"})
    assert core.find_project(both / "x") == both


@pytest.mark.parametrize("record", ["migration directory", "migration lock", "both"])
def test_a_markerless_migration_destination_stops_the_search_and_is_refused(tmp_path, record):
    """design 4.3 and 5.5 (PR 2): with no marker, `.analytics/migration/` or its lock
    `.analytics/migration.lock` makes a directory a migration's destination that has not committed
    (`incomplete`): the search stops there, as at a marker, and every write and every read is
    migration_incomplete, its details saying how far each migration's ledger went; a side write is
    skipped. Nothing is written. A marker wins over the record (the committed destination keeps
    both)."""
    above = tmp_path / "p"
    dest = above / "dest"
    (dest / "deeper").mkdir(parents=True)
    write_marker(above, "lab.json", {"schema": "lab.project/1"})
    if record != "migration lock":
        ledger = dest / ".analytics" / "migration" / "20261008T010203Z-a1b2c3d4" / "progress.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_bytes(b'{"step": "start", "at": "x", "source": {"path": "/src"}}\n'
                           b'{"step": "probed", "at": "x"}\n{"step": "plan", "at')
    if record != "migration directory":
        (dest / ".analytics").mkdir(exist_ok=True)
        (dest / ".analytics" / "migration.lock").write_bytes(b"")
    before = snapshot(dest)
    assert core.find_project(dest / "deeper") == dest
    judged = writer.marker_state(dest)
    assert (judged.state, judged.marker, judged.format) == ("incomplete", None, None)
    for call in (lambda: core.admit(dest, "record"), lambda: core.readable(dest, "status")):
        with pytest.raises(core.AnalyticsError) as caught:
            call()
        assert caught.value.code == "migration_incomplete"
        details = caught.value.details
        assert details["lock"] is (record != "migration directory")
        if record == "migration lock":
            assert details["migrations"] == []
        else:
            assert details["migrations"] == [{"id": "20261008T010203Z-a1b2c3d4", "step": "probed",
                                              "source": "/src"}]
    assert core.admit(dest, "a side write", side=True) is None
    assert snapshot(dest) == before
    write_marker(dest, "analytics.json", NEW)
    assert writer.marker_state(dest).state == "current"
    lone = tmp_path / "lone"
    (lone / ".analytics").mkdir(parents=True)
    assert writer.marker_state(lone).state == "none", "a state directory alone is no record"


# --- the entry-point matrix (T8): the kit's command line ---------------------------------------

def snapshot(root: Path) -> dict:
    """Every entry under `root`: a file's size, modification time and sha256, a directory's
    modification time (an entry made and removed inside it moves it), a link's target. Nothing left
    out: a lock file the kit makes is a change."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            st = os.lstat(path)
            rel = path.relative_to(root).as_posix()
            if stat.S_ISLNK(st.st_mode):
                out[rel] = ("link", os.readlink(path))
            elif stat.S_ISDIR(st.st_mode):
                out[rel] = ("dir", st.st_mtime_ns)
            elif not stat.S_ISREG(st.st_mode):  # a FIFO: reading it would wait for a writer
                out[rel] = ("other", stat.S_IFMT(st.st_mode), st.st_mtime_ns)
            else:
                out[rel] = ("file", st.st_size, st.st_mtime_ns,
                            hashlib.sha256(path.read_bytes()).hexdigest())
    st = os.stat(root)
    out["."] = ("dir", st.st_mtime_ns)
    return out


SCRIPT = ("import aelix_analytics as analytics\n"
          "sales = analytics.read('sales')\n"
          "analytics.save(sales.head(3), 'head')\n")


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """An analytics.json project (the new format, as init makes it) with a dataset, a script and one
    run, made once, and a lab.json project an older kit left with the same dataset (in data/raw), the
    scripts and a run: every column copies the one its markers belong to."""
    root = tmp_path_factory.mktemp("matrix") / "base"
    # Module scope runs before the function-scoped isolation of conftest: its own trust list.
    patch = pytest.MonkeyPatch()
    patch.setenv("AELIX_ANALYTICS_TRUST_FILE", str(root.parent / "trust" / "analytics-projects.json"))
    try:
        core.init_project(root, title="matrix", goal="g", template_dir=TEMPLATE, origin=ORIGIN)
        source = write_sales_csv(root.parent / "sales.csv", rows=12)
        assert kit_cli(root, "import", source, "--name", "sales")[0] == 0
        (root / "scripts" / "head.py").write_text(SCRIPT, encoding="utf-8")
        assert kit_cli(root, "run", "scripts/head.py", "--title", "head")[0] == 0
        (root / "scripts" / "again.py").write_text(SCRIPT, encoding="utf-8")
    finally:
        patch.undo()
    legacy = LegacyProject(root.parent / "legacy", title="matrix")
    data = (root / "data" / "imported" / "sales.parquet").read_bytes()
    sales = "data/raw/sales.parquet"
    digest = legacy.file(sales, data)
    legacy.write_json("data/raw/sales.meta.json", {
        "schema": "lab.dataset/1", "name": "sales", "path": sales, "sha256": digest, "bytes": len(data),
        "rows": 12, "columns": [], "description": None, "imported_at": at(1),
        "source": {"path": str(source)}})
    legacy.event("project")
    legacy.event("import", {"data": [sales]})
    for name in ("head.py", "again.py"):
        legacy.file(f"scripts/{name}", SCRIPT.encode())
    legacy.run("20261008-090000-head", script="scripts/head.py", inputs=[(sales, digest)],
               kit_version="0.7.0")
    return {"new": root, "legacy": legacy.root}


def _column(base: dict, where: Path, column: str) -> tuple[Path, Path]:
    """(the directory a command is pointed at, the tree whose bytes are compared) for a column: a
    copy of the base its markers belong to, with the column's markers in place of its own."""
    lab_value, new_value = MATRIX_MARKERS[column]
    older = column == "nested" or (lab_value is not None and new_value is None)
    root = where / column
    shutil.copytree(base["legacy" if older else "new"], root, copy_function=shutil.copy2)
    for name in ("lab.json", "analytics.json"):
        (root / name).unlink(missing_ok=True)
    if column == "nested":
        # A lab.json project with an analytics.json project below it (design 4.3): the one below
        # holds a copy of the data and the scripts, so a run there has what it reads.
        write_marker(root, "lab.json", {"schema": "lab.project/1", "title": "matrix"})
        sub = root / "sub"
        sub.mkdir()
        write_marker(sub, "analytics.json", NEW)
        shutil.copytree(root / "data", sub / "data")
        shutil.copytree(root / "scripts", sub / "scripts")
        return sub, root
    if lab_value is not None:
        write_marker(root, "lab.json", lab_value)
    if new_value is not None:
        write_marker(root, "analytics.json", new_value)
    if column == "incomplete":
        (root / ".analytics" / "migration").mkdir(parents=True)
    return root, root


def _outside(tree: dict, sub: str = "sub/") -> dict:
    """A snapshot without the project below (`nested`): the lab.json project around it."""
    return {rel: value for rel, value in tree.items() if rel != "sub" and not rel.startswith(sub)}


# The columns of design 11.3, each with its markers (lab.json, analytics.json), `nested` being a
# lab.json project with an analytics.json directory below it.
MATRIX_MARKERS = {
    "lab_project_1": ({"schema": "lab.project/1", "title": "matrix", "created_at": "2026-10-08T00:00:00+09:00"}, None),
    "lab_unparsable": (b"{not json", None),
    "lab_project_9": ({"schema": "lab.project/9", "title": "matrix"}, None),
    "analytics_2": (None, NEW),
    "analytics_2_too_old": (None, {**NEW, "min_writer_version": "99.0"}),
    "analytics_3": (None, {**NEW, "schema": "analytics.project/3"}),
    "analytics_invalid": (None, b'{"schema": "analytics.pro'),
    "both": ({"schema": "lab.project/1"}, NEW),
    "none": (None, None),
    "incomplete": (None, None),
    "nested": (None, None),
}
# Where this kit writes (PR 3): an analytics.json project whose min_writer_version it meets, and the
# analytics.json project below a lab.json one — never the lab.json project around it.
WRITABLE_COLUMNS = ("analytics_2", "nested")
# What a write is refused with in each other column; a read is refused only where no format can be
# read (no marker, or two).
REFUSED = {"lab_project_1": "migration_required", "lab_unparsable": "migration_required",
           "lab_project_9": "project_too_new", "analytics_2_too_old": "writer_too_old",
           "analytics_3": "project_too_new", "analytics_invalid": "marker_invalid",
           "both": "marker_conflict", "none": "no_project", "incomplete": "migration_incomplete"}
READ_REFUSED = {"both": "marker_conflict", "none": "no_project", "incomplete": "migration_incomplete"}

WRITES = {
    "record": ("record", "finding", "a finding"),
    "checkpoint": ("checkpoint", "a checkpoint"),
    "asset_candidate_record": ("record", "asset_candidate", "An early candidate", "--details-json", json.dumps({
        "asset_id": "candidate", "title": "Candidate", "problem": "A recurring question", "artifact_kind": "skill",
        "conditions": "", "exclusions": "", "next_check": ""})),
    "import": ("import", "{csv}", "--name", "more"),
    "render": ("render",),
    "run": ("run", "scripts/again.py", "--title", "again"),
    "demo": ("demo",),
}
READS = {
    "status": ("status",),
    "context": ("context",),
    "query": ("query", "SELECT count(*) FROM sales"),
}


def _expected(column: str, command: str) -> str | None:
    if command in WRITES:
        return None if column in WRITABLE_COLUMNS else REFUSED[column]
    return READ_REFUSED.get(column)


@pytest.mark.parametrize("column", MATRIX_MARKERS)
def test_the_kit_command_line_writes_only_where_it_may(base, tmp_path, column):
    """T8, the kit's command line: every write command in every column, each in a copy of its own.
    A refused write changes nothing — not the journal, not a lock file, not a cache, not the state
    directory — and a read where the kit does not write skips its side writes (the hash cache, the
    query log), so it changes nothing either. A write where the kit writes is admitted (it may still
    fail for a reason of its own: the demo asks for an empty project); below a lab.json project it
    writes to the analytics.json project there, and the lab.json project's bytes stay as they were."""
    csv = write_sales_csv(tmp_path / "more.csv", rows=6)
    seen = {}
    for command, args in {**WRITES, **READS}.items():
        target, tree = _column(base, tmp_path / command, column)
        before = snapshot(tree)
        code, result = kit_cli(target, *(str(csv) if a == "{csv}" else a for a in args))
        refused = _expected(column, command)
        got = None if result["ok"] else result["error"]["code"]
        seen[command] = got
        if refused is not None:
            assert (code, got) == (1, refused), (command, result)
            assert snapshot(tree) == before, command
        else:
            assert got not in GATE_CODES, (command, result)
            if command == "demo":
                assert got == "demo_not_empty", result
            elif command in WRITES:
                assert code == 0 and snapshot(tree) != before, (command, result)
            elif column not in WRITABLE_COLUMNS:
                assert snapshot(tree) == before, command  # side writes skipped
            if column == "nested":
                assert _outside(snapshot(tree)) == _outside(before), command
    record_matrix("kit_cli", column, seen)


# The migration's row (design 11.3: `migrate` is judged by its own rules, Issue #13 PR 3): every
# column as the source — read by the migration's rules, never admitted, so only a lab.json project is
# copied and every other column is source_not_legacy with its judgment (no_project where none is) —
# and as the folder above a destination, which a marker of any kind, or a migration's record without
# one (`incomplete`, the owner's decision of 2026-10-09), makes inside_project (OQ9).
MIGRATE_SOURCE = {"lab_project_1": None, "lab_unparsable": None, "lab_project_9": "unknown",
                  "analytics_2": "current", "analytics_2_too_old": "current", "analytics_3": "unknown",
                  "analytics_invalid": "invalid", "both": "conflict", "none": "no_project",
                  "incomplete": "incomplete", "nested": "current"}


@pytest.mark.parametrize("column", MATRIX_MARKERS)
def test_the_migration_copies_only_a_lab_json_project_and_never_inside_one(base, tmp_path, column):
    """T8's migrate row: from each column, `migrate --to` a new folder outside — a lab.json project
    (lab.project/1 or a broken lab.json, the older kit's) is copied and its bytes stay as they were;
    every other column is refused before anything is written: no destination, the column as it was.
    And `--to` a folder inside each column with a marker or a migration's record is
    destination_invalid (inside_project)."""
    target, tree = _column(base, tmp_path / "source", column)
    before = snapshot(tree)
    dest = tmp_path / "new"
    code, result = kit_cli(target, "migrate", "--to", dest)
    expected = MIGRATE_SOURCE[column]
    seen = {"source": None if result["ok"] else result["error"]["code"]}
    if expected is None:
        assert code == 0 and result["ok"] and writer.marker_state(dest).state == "current", result
    elif expected == "no_project":
        assert (code, result["error"]["code"]) == (1, "no_project"), result
    else:
        assert (code, result["error"]["code"]) == (1, "source_not_legacy"), result
        assert result["error"]["details"] == {"state": expected}
    if expected is not None:
        assert not dest.exists()
    assert snapshot(tree) == before
    legacy, _ = _column(base, tmp_path / "legacy", "lab_project_1")
    inside = tree / "inner"
    code, result = kit_cli(legacy, "migrate", "--to", inside)
    if column != "none":
        assert writer.present_marker(tree) or writer.migration_record(tree), column
        assert (code, result["error"]["code"]) == (1, "destination_invalid"), result
        assert result["error"]["details"] == {"reason": "inside_project", "project": str(tree)}
        assert not inside.exists() and snapshot(tree) == before
        seen["destination_inside"] = "inside_project"
    else:
        assert code == 0 and writer.marker_state(inside).state == "current", result
        seen["destination_inside"] = "migrated"
    record_matrix("migrate", column, seen)


def record_matrix(rows: str, column: str, seen: dict) -> None:
    from conftest import record_observation

    record_observation(f"issue-13-gate/{rows}-{column}", seen)


def test_a_read_of_a_project_this_kit_does_not_write_says_so(base, tmp_path):
    """design 7: the overview of a lab.json project says it is read only here until a person
    migrates it — the status JSON's format, writable, write_refusal and migration_hint, and a line
    at the head of its text and of the context block — so a model tells a person rather than trying
    the writes again. An analytics.json project the kit writes says none of it."""
    target, _ = _column(base, tmp_path / "older", "lab_project_1")
    code, result = kit_cli(target, "status")
    st = result["status"]
    assert code == 0 and (st["project"]["format"], st["project"]["marker"]) == ("lab.project/1", "lab.json")
    assert st["project"]["writable"] is False
    assert st["project"]["write_refusal"]["code"] == "migration_required"
    assert st["project"]["migration_hint"] == writer.migration_hint(target)
    assert "analytics migrate --to <new empty folder>" in st["project"]["migration_hint"]
    lines = result["text"].splitlines()
    assert lines[1] == core.OLDER_FORMAT_LINE == ("Older record format: read only here until a person "
                                                  "migrates it (analytics migrate --to DEST).")
    assert lines[3].startswith("read only: migration_required — ")
    code, result = kit_cli(target, "context")
    assert f"\n{core.OLDER_FORMAT_LINE}\nread only: migration_required — " in result["context"]
    target, _ = _column(base, tmp_path / "newer", "analytics_3")
    code, result = kit_cli(target, "status")
    st = result["status"]
    assert st["project"]["write_refusal"]["code"] == "project_too_new"
    assert "migration_hint" not in st["project"] and core.OLDER_FORMAT_LINE not in result["text"]
    target, _ = _column(base, tmp_path / "current", "analytics_2")
    st = kit_cli(target, "status")[1]["status"]
    assert (st["project"]["format"], st["project"]["writable"]) == ("analytics.project/2", True)
    assert "write_refusal" not in st["project"] and "migration_hint" not in st["project"]
    assert st["writes_skipped"] == []


def test_a_project_below_a_lab_json_one_is_warned_of_and_written(base, tmp_path):
    """design 4.3: an analytics.json project below a lab.json one is the project this kit writes (the
    nearest marker), and its overview warns that a kit before 0.7.0 — which looks for lab.json only —
    would write to the project above. It is a warning: the write goes on."""
    target, root = _column(base, tmp_path, "nested")
    code, result = kit_cli(target, "status")
    st = result["status"]
    assert code == 0 and st["project"]["older_project_above"] == str(root)
    assert f"warning: {root} above this project holds a lab.json: a kit older than 0.7.0 run here would " \
           "write to that project, not this one" in result["text"]
    target, _ = _column(base, tmp_path / "alone", "analytics_2")
    assert "older_project_above" not in kit_cli(target, "status")[1]["status"]["project"]


# --- the script API (T8) -------------------------------------------------------------------------

SDK_CALLS = {
    "record": "analytics.record('note', 'from the script API')",
    "checkpoint": "analytics.checkpoint('from the script API')",
    "asset_candidate_record": "analytics.record('asset_candidate', 'Early candidate', details={'asset_id':'candidate',"
                              "'title':'Candidate','problem':'A recurring question','artifact_kind':'skill',"
                              "'conditions':'','exclusions':'','next_check':''})",
    "save": "analytics.save(__import__('polars').DataFrame({'a': [1, 2]}), 'saved')",
    "visualize": ("plt = __import__('matplotlib.pyplot').pyplot; fig = plt.figure(); "
                  "plt.plot([1, 2]); analytics.visualize(fig, 'drawn')"),
}


def _sdk(project: Path, call: str) -> dict:
    code = ("import json, sys\nimport aelix_analytics as analytics\nfrom aelix_analytics import core\n"
            f"try:\n    {call}\n    print(json.dumps({{'ok': True}}))\n"
            "except core.AnalyticsError as exc:\n    print(json.dumps(exc.to_json()))\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, cwd=project,
                          env=kit_env(AELIX_ANALYTICS_PROJECT=str(project)), timeout=300,
                          **_platform.text_kwargs())
    lines = [line for line in done.stdout.split("\n") if line.startswith("{")]
    assert lines, done.stderr
    return json.loads(lines[-1])


@pytest.mark.parametrize("column", ["lab_project_1", "analytics_2", "analytics_2_too_old",
                                    "analytics_invalid", "both", "nested"])
def test_the_script_api_outside_a_run_writes_only_where_it_may(base, tmp_path, column):
    seen = {}
    for name, call in SDK_CALLS.items():
        target, tree = _column(base, tmp_path / name, column)
        before = snapshot(tree)
        result = _sdk(target, call)
        refused = None if column in WRITABLE_COLUMNS else REFUSED[column]
        seen[name] = None if result["ok"] else result["error"]["code"]
        if refused is None:
            assert result == {"ok": True} and snapshot(tree) != before, (name, result)
            if column == "nested":
                assert _outside(snapshot(tree)) == _outside(before), name
        else:
            assert seen[name] == refused, (name, result)
            assert snapshot(tree) == before, name
    record_matrix("sdk", column, seen)


@pytest.mark.parametrize("column", ["lab_project_1", "lab_unparsable", "lab_project_9", "analytics_2_too_old",
                                    "analytics_3", "analytics_invalid", "both", "none", "incomplete"])
def test_new_asset_write_actions_are_admitted_before_any_side_effect(base, tmp_path, column):
    actions = {
        "mode": (("assets", "mode", "always_on"), "analytics.set_asset_mode('always_on')"),
        "review": (("assets", "review"), "analytics.review_assets()"),
        "update": (("assets", "update", "e3", "--expected", "proposed", "--to", "trial", "--reason", "Try"),
                   "analytics.update_asset('e3', expected='proposed', new='trial', text='Try')"),
        "register": (("assets", "register", "e3", "--name", "selected"),
                     "analytics.register_asset('e3', name='selected')"),
    }
    for name, (cli, sdk) in actions.items():
        for entry in ("cli", "sdk"):
            if entry == "sdk" and column == "none":
                # project() has always raised RuntimeError outside a project; that SDK lookup
                # boundary is separate from these handled marker refusals. CLI covers this row.
                continue
            target, tree = _column(base, tmp_path / f"asset-{name}-{entry}", column)
            before = snapshot(tree)
            if entry == "cli":
                code, result = kit_cli(target, *cli)
                assert code == 1
            else:
                result = _sdk(target, sdk)
            assert result["error"]["code"] == REFUSED[column], (name, entry, result)
            assert snapshot(tree) == before, (name, entry)


@pytest.mark.parametrize("column", ["lab_project_1", "lab_unparsable", "lab_project_9", "analytics_2_too_old",
                                    "analytics_3", "analytics_invalid", "both", "none", "incomplete"])
def test_automatic_publication_cannot_make_a_first_effect_in_a_refused_project(base, tmp_path, column):
    target, tree = _column(base, tmp_path / "automatic-publication", column)
    marker = target / "analytics.json"
    if marker.is_file():
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except ValueError:
            value = None
        if isinstance(value, dict):
            value["asset_suggestions"] = {"mode": "always_on"}
            write_marker(target, "analytics.json", value)
    before = snapshot(tree)
    app = core.asset_application(core.Project(target), core.Origin("human", "cli"))
    try:
        result = app.publish_suggestion()
    except core.AnalyticsError as exc:
        assert exc.code in GATE_CODES | {"migration_incomplete"}
    else:
        assert result["published"] is None
    assert snapshot(tree) == before


IN_RUN = """\
import json
from pathlib import Path
import aelix_analytics as analytics
from aelix_analytics import core

# The directory becomes one with two markers while the run goes on: every SDK write after that is
# refused in the script, before it writes (design 4.3, T8 'the marker changes during the call').
Path("lab.json").write_text(json.dumps(MARKER), encoding="utf-8")
codes = {}
for name, call in (("record", lambda: analytics.record("note", "too late")),
                   ("save", lambda: analytics.save(analytics.read("sales").head(2), "late")),
                   ("visualize", lambda: analytics.visualize("scripts/head.py", "late"))):
    try:
        call()
        codes[name] = None
    except core.AnalyticsError as exc:
        codes[name] = exc.code
print("CODES=" + json.dumps(codes))
"""


def test_a_marker_that_changes_during_a_run_refuses_what_comes_after(base, tmp_path):
    """The run was admitted; the directory then gets a second marker. The script's SDK writes are
    refused where they are called, the run's last record write is refused by the writer, and so is
    its completion event under the journal lock: the journal is the same bytes, no dataset or chart
    is written, the run's record is the one written before its script ran, and the run is
    `incomplete`, without its completion event (design 4.3 'run이면 정본만 있고'); the answer lists
    the refusals in finalize_errors."""
    root, _ = _column(base, tmp_path, "analytics_2")
    script = IN_RUN.replace("MARKER", repr({"schema": "lab.project/1"}))
    (root / "scripts" / "late.py").write_text(script, encoding="utf-8")
    journal = (root / "journal.jsonl").read_bytes()
    code, result = kit_cli(root, "run", "scripts/late.py", "--title", "late")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert 'CODES={"record": "marker_conflict", "save": "marker_conflict", "visualize": "marker_conflict"}' \
        in result["stdout_tail"]
    assert (root / "journal.jsonl").read_bytes() == journal
    assert not (root / "data" / "derived" / "late.parquet").exists()
    run_dir = root / "runs" / result["run"]["id"]
    assert not (run_dir / "visualizations").exists()
    problems = result["finalize_errors"]
    assert any(p.startswith("run record: marker_conflict") for p in problems), problems
    assert any("journal: AnalyticsError" in p for p in problems), problems
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert record["schema"] == "analytics.run/2" and record["status"] == "running"
    (root / "lab.json").unlink()
    st = core.status(core.Project(root))
    row = next(r for r in st["runs"] if r["id"] == result["run"]["id"])
    assert row["recording_status"] == "incomplete"
    assert "no_completion_event" in row["reasons"], row


def test_a_journal_write_is_judged_again_under_the_lock(tmp_path, monkeypatch):
    """S8: a journal write is judged before the lock (its file is the first side effect) and again
    once the lock is held, so a marker made in between refuses the write and nothing is appended."""
    project = core.init_project(tmp_path / "p", template_dir=TEMPLATE, origin=ORIGIN)
    journal = project.journal.read_bytes()
    locks = project.platform.locks
    real = locks.exclusive

    def lock_then_change(path):
        write_marker(project.root, "lab.json", {"schema": "lab.project/1"})
        return real(path)

    monkeypatch.setattr(locks, "exclusive", lock_then_change)
    with pytest.raises(core.AnalyticsError) as caught:
        core.record(project, "note", "between", origin=ORIGIN)
    assert caught.value.code == "marker_conflict"
    assert project.journal.read_bytes() == journal


def test_a_run_is_judged_again_once_the_run_lock_is_held(tmp_path, monkeypatch):
    """A run that waits for another's run lock may find the directory changed when it gets it: it is
    judged again then, before anything of the run is written (no run directory, no event)."""
    from aelix_analytics import runner

    project = core.init_project(tmp_path / "p", template_dir=TEMPLATE, origin=ORIGIN)
    (project.root / "scripts" / "a.py").write_text("print('hi')\n", encoding="utf-8")
    journal = project.journal.read_bytes()
    real = core.run_lock

    def lock_then_change(p):
        write_marker(p.root, "lab.json", {"schema": "lab.project/1"})
        return real(p)

    monkeypatch.setattr(core, "run_lock", lock_then_change)
    with pytest.raises(core.AnalyticsError) as caught:
        runner.execute(project, script="scripts/a.py", title="a", origin=ORIGIN)
    assert caught.value.code == "marker_conflict"
    assert project.journal.read_bytes() == journal
    assert not project.runs.exists() or not any(project.runs.iterdir())


@pytest.mark.parametrize("marker", ["analytics.json", "lab.json"])
def test_the_init_of_a_new_project_is_judged_by_its_own_rules(tmp_path, marker):
    """design 11.3 and OQ9: `init` is judged by the destination's rules, not by this matrix — a
    directory that does not exist or is empty, and inside no project: below a directory with either
    marker it is destination_invalid (reason inside_project, the migration's rule) and nothing is
    made, as an older kit run there would write to the project above. Outside every project it
    makes the new format: an analytics.json of analytics.project/2 with a new project_id and this
    kit's min_writer_version, and e1, the project record holding that id."""
    above = tmp_path / "above"
    above.mkdir()
    write_marker(above, marker, NEW if marker == "analytics.json" else {"schema": "lab.project/1"})
    with pytest.raises(core.AnalyticsError) as caught:
        core.init_project(above, template_dir=TEMPLATE, origin=ORIGIN)
    assert caught.value.code == "init_not_empty"
    before = snapshot(above)
    for target in (above / "inner", above / "deeper" / "still"):
        with pytest.raises(core.AnalyticsError) as caught:
            core.init_project(target, template_dir=TEMPLATE, origin=ORIGIN)
        assert caught.value.code == "destination_invalid", caught.value.to_json()
        assert caught.value.details == {"reason": "inside_project", "project": str(above)}
    (above / "empty").mkdir()
    before = snapshot(above)
    with pytest.raises(core.AnalyticsError) as caught:
        core.init_project(above / "empty", template_dir=TEMPLATE, origin=ORIGIN)
    assert caught.value.details["reason"] == "inside_project"
    assert snapshot(above) == before
    made = core.init_project(tmp_path / "outside", title="새 형식", template_dir=TEMPLATE, origin=ORIGIN)
    value = json.loads(made.marker.read_text(encoding="utf-8"))
    assert made.marker.name == "analytics.json" and writer.marker_state(made.root).state == "current"
    assert value["schema"] == "analytics.project/2" and value["title"] == "새 형식"
    assert value["min_writer_version"] == value["kit_version"] == core.KIT_VERSION
    assert re.fullmatch(r"prj_[0-9a-f]{16}", value["project_id"])
    first = json.loads(made.journal.read_text(encoding="utf-8").splitlines()[0])
    assert (first["schema"], first["id"], first["kind"]) == ("analytics.event/2", "e1", "project")
    assert first["details"] == {"project_id": value["project_id"]}
    assert not (made.root / "lab.json").exists() and not (made.root / ".lab").exists()


@pytest.mark.parametrize("record", ["lock", "migration directory", "both"])
def test_no_project_is_made_inside_a_migration_s_destination_not_committed(tmp_path, record):
    """OQ9 as the owner widened it (2026-10-09): a directory above with a migration's record and no
    marker — what find_project finds as a project and refuses (migration_incomplete) — refuses an init
    below it as a marker does (destination_invalid, inside_project, naming it), nothing made; the
    directory itself is not empty. A migration's record further up counts as one next to it does."""
    above = tmp_path / "unfinished"
    (above / ".analytics").mkdir(parents=True)
    if record in ("lock", "both"):
        (above / ".analytics" / "migration.lock").write_bytes(b"")
    if record in ("migration directory", "both"):
        (above / ".analytics" / "migration").mkdir()
    assert writer.find_project(above / "x") == above and writer.project_above(above / "x") == str(above)
    before = snapshot(above)
    for target in (above / "inner", above / "deeper" / "still"):
        with pytest.raises(core.AnalyticsError) as caught:
            core.init_project(target, template_dir=TEMPLATE, origin=ORIGIN)
        assert caught.value.code == "destination_invalid", caught.value.to_json()
        assert caught.value.details == {"reason": "inside_project", "project": str(above)}
        assert "a migration that has not committed" in caught.value.message
    with pytest.raises(core.AnalyticsError) as caught:
        core.init_project(above, template_dir=TEMPLATE, origin=ORIGIN)
    assert caught.value.code == "init_not_empty"
    assert snapshot(above) == before
    # A project's .analytics/ holds no migration record unless a migration made it: no refusal.
    plain = tmp_path / "plain"
    (plain / ".analytics").mkdir(parents=True)
    assert writer.project_above(plain / "x") is None


def test_init_takes_a_directory_of_operating_system_views_as_empty(tmp_path):
    """design 6.2, the rule a migration's destination follows too: .DS_Store and macOS's AppleDouble
    files `._<name>` (the owner's decision of 2026-10-09) leave a directory empty, and stay; any other
    name, Thumbs.db among them, does not."""
    target = tmp_path / "finder"
    target.mkdir()
    for name in (".DS_Store", "._.DS_Store", "._.Trashes"):
        (target / name).write_bytes(b"\x00\x05\x16\x07")
    made = core.init_project(target, template_dir=TEMPLATE, origin=ORIGIN)
    assert writer.marker_state(made.root).state == "current"
    assert all((target / name).read_bytes() == b"\x00\x05\x16\x07" for name in (".DS_Store", "._.DS_Store",
                                                                               "._.Trashes"))
    other = tmp_path / "other"
    other.mkdir()
    (other / "._x").write_bytes(b"\x00")
    (other / "Thumbs.db").write_bytes(b"\x00")
    with pytest.raises(core.AnalyticsError) as caught:
        core.init_project(other, template_dir=TEMPLATE, origin=ORIGIN)
    assert caught.value.code == "init_not_empty" and caught.value.details == {"entries": ["Thumbs.db"]}


@pytest.mark.parametrize("what", ["folder", "link"])
def test_init_takes_a_folder_or_a_link_named_appledouble_as_an_entry(tmp_path, what):
    """The owner's correction of 2026-10-09 (#13 issuecomment-6072183477): only a regular file
    `._<name>` is the OS's; a folder or a link with that name is an entry, so init refuses the
    directory (init_not_empty) and leaves it as it was."""
    target = tmp_path / "finder"
    target.mkdir()
    (target / "._ok").write_bytes(b"\x00\x05\x16\x07")
    outside = tmp_path / "outside"
    outside.mkdir()
    if what == "folder":
        (target / "._d").mkdir()
    else:
        dir_link(LINK_KINDS[0], target / "._d", outside)
    with pytest.raises(core.AnalyticsError) as caught:
        core.init_project(target, template_dir=TEMPLATE, origin=ORIGIN)
    assert caught.value.code == "init_not_empty" and caught.value.details == {"entries": ["._d"]}
    assert sorted(p.name for p in target.iterdir()) == ["._d", "._ok"]
