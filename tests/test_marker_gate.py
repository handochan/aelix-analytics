"""Which format a directory holds and whether this kit writes there (Issue #13 PR 1).

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
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import KIT, kit_cli, kit_env, write_sales_csv

from aelix_analytics import _platform, core, writer

ORIGIN = core.Origin("test", "sdk")
TEMPLATE = KIT / "aelix_analytics" / "template"
GATE_CODES = {"no_project", "project_too_new", "marker_invalid", "marker_conflict"}

NEW = {"schema": "analytics.project/2", "project_id": "prj_7f3a9c2e41d84b06", "title": "Regional revenue",
       "created_at": "2026-09-01T09:00:00.000+09:00", "min_writer_version": "0.7.0",
       "kit_version": "0.7.0"}


def write_marker(directory: Path, name: str, value) -> None:
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    (directory / name).write_bytes(data)


# --- the marker judgment (T12) -----------------------------------------------------------------

# name -> (lab.json, analytics.json, state, reason, format, write refusal in this PR or None)
STATES = {
    "none": (None, None, "none", None, None, "no_project"),
    "lab_project_1": ({"schema": "lab.project/1", "title": "t"}, None, "legacy", None, "lab.project/1", None),
    "lab_no_schema": ({"title": "t"}, None, "legacy", "no_schema", "lab.project/1", None),
    "lab_unparsable": (b"{not json", None, "legacy", "unparsable", "lab.project/1", None),
    "lab_not_object": (b"[1, 2]", None, "legacy", "not_object", "lab.project/1", None),
    "lab_project_9": ({"schema": "lab.project/9"}, None, "unknown", None, "lab.project/9",
                      "project_too_new"),
    "lab_other_family": ({"schema": "other.project/1"}, None, "unknown", None, "other.project/1",
                         "project_too_new"),
    "analytics_2": (None, NEW, "current", None, "analytics.project/2", "project_too_new"),
    "analytics_2_too_old": (None, {**NEW, "min_writer_version": "99.0"}, "current", None,
                            "analytics.project/2", "project_too_new"),
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
    assert writer.WRITABLE == frozenset({"lab.project/1"})
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


def test_the_new_writer_s_constant_turns_the_table(tmp_path):
    """PR 3 makes WRITABLE {analytics.project/2}: then the new format is written when the marker's
    min_writer_version allows this kit, and refused with writer_too_old when it does not
    (ADR-0006 1.3). Until then a lab.json this kit does not write is project_too_new — the
    `migration_required` of PR 3 is not this PR's."""
    root = tmp_path / "d"
    root.mkdir()
    new = frozenset({writer.PROJECT})
    write_marker(root, "analytics.json", NEW)
    assert writer.admit(root, "record", writer_version="0.7.0", writable=new).state == "current"
    with pytest.raises(core.AnalyticsError) as caught:
        writer.admit(root, "record", writer_version="0.6.0", writable=new)
    assert caught.value.code == "writer_too_old"
    assert caught.value.details == {"min_writer_version": "0.7.0", "kit_version": "0.6.0"}
    (root / "analytics.json").unlink()
    write_marker(root, "lab.json", {"schema": "lab.project/1"})
    with pytest.raises(core.AnalyticsError) as caught:
        writer.admit(root, "record", writer_version="0.7.0", writable=new)
    assert caught.value.code == "project_too_new"


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
    write_marker(root, "lab.json", {"schema": "lab.project/1"})
    assert core.admit(root, "record").state == "legacy"
    write_marker(root, "analytics.json", NEW)
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


def test_a_markerless_migration_destination_does_not_stop_the_search_yet(tmp_path):
    """design 4.3: from PR 2 the search also stops at a directory holding `.analytics/migration/`
    without a marker (`incomplete`); in this PR it does not, and such a directory with no project
    above it is no project (no_project)."""
    above = tmp_path / "p"
    (above / "dest" / ".analytics" / "migration").mkdir(parents=True)
    write_marker(above, "lab.json", {"schema": "lab.project/1"})
    assert core.find_project(above / "dest") == above
    lone = tmp_path / "lone"
    (lone / ".analytics" / "migration").mkdir(parents=True)
    assert writer.marker_state(lone).state == "none"


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
    """A lab.json project with a dataset, a script and one run, made once: every column copies it."""
    root = tmp_path_factory.mktemp("matrix") / "base"
    # Module scope runs before the function-scoped isolation of conftest: its own trust list.
    patch = pytest.MonkeyPatch()
    patch.setenv("AELIX_ANALYTICS_TRUST_FILE", str(root.parent / "trust" / "lab-projects.json"))
    try:
        core.init_project(root, title="matrix", goal="g", template_dir=TEMPLATE, origin=ORIGIN)
        source = write_sales_csv(root.parent / "sales.csv", rows=12)
        assert kit_cli(root, "import", source, "--name", "sales")[0] == 0
        (root / "scripts" / "head.py").write_text(SCRIPT, encoding="utf-8")
        assert kit_cli(root, "run", "scripts/head.py", "--title", "head")[0] == 0
        (root / "scripts" / "again.py").write_text(SCRIPT, encoding="utf-8")
    finally:
        patch.undo()
    return root


def _column(base: Path, where: Path, column: str) -> tuple[Path, Path]:
    """(the directory a command is pointed at, the tree whose bytes are compared) for a column."""
    root = where / column
    shutil.copytree(base, root, copy_function=shutil.copy2)
    lab = root / "lab.json"
    if column == "nested":
        sub = root / "sub"
        sub.mkdir()
        write_marker(sub, "analytics.json", NEW)
        return sub, root
    lab_value, new_value = MATRIX_MARKERS[column]
    lab.unlink()
    if lab_value is not None:
        write_marker(root, "lab.json", lab_value)
    if new_value is not None:
        write_marker(root, "analytics.json", new_value)
    if column == "incomplete":
        (root / ".analytics" / "migration").mkdir(parents=True)
    return root, root


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
WRITABLE_COLUMNS = ("lab_project_1", "lab_unparsable")
# What a write is refused with in each other column; a read is refused only where no format can be
# read (no marker, or two).
REFUSED = {"lab_project_9": "project_too_new", "analytics_2": "project_too_new",
           "analytics_2_too_old": "project_too_new", "analytics_3": "project_too_new",
           "analytics_invalid": "marker_invalid", "both": "marker_conflict", "none": "no_project",
           "incomplete": "no_project", "nested": "project_too_new"}
READ_REFUSED = {"both": "marker_conflict", "none": "no_project", "incomplete": "no_project"}

WRITES = {
    "record": ("record", "finding", "a finding"),
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
    A refused write changes nothing — not the journal, not a lock file, not a cache, not .lab/ — and
    a read where the kit does not write skips its side writes (the hash cache, the query log), so
    it changes nothing either. A write where the kit writes is admitted (it may still fail for a
    reason of its own: the demo asks for an empty project)."""
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
    record_matrix("kit_cli", column, seen)


def record_matrix(rows: str, column: str, seen: dict) -> None:
    from conftest import record_observation

    record_observation(f"issue-13-gate/{rows}-{column}", seen)


def test_a_read_of_a_project_this_kit_does_not_write_says_so(base, tmp_path):
    target, _ = _column(base, tmp_path, "analytics_2")
    code, result = kit_cli(target, "status")
    st = result["status"]
    assert code == 0 and st["project"]["format"] == "analytics.project/2"
    assert st["project"]["writable"] is False and st["project"]["marker"] == "analytics.json"
    assert st["project"]["write_refusal"]["code"] == "project_too_new"
    assert st["project"]["title"] == NEW["title"] and st["project"]["created_at"] == NEW["created_at"]
    assert result["text"].splitlines()[2].startswith("read only: project_too_new — ")
    code, result = kit_cli(target, "context")
    assert "\nread only: project_too_new — " in result["context"]
    target, _ = _column(base, tmp_path, "lab_project_1")
    st = kit_cli(target, "status")[1]["status"]
    assert (st["project"]["format"], st["project"]["writable"]) == ("lab.project/1", True)
    assert "write_refusal" not in st["project"] and st["writes_skipped"] == []


# --- the script API (T8) -------------------------------------------------------------------------

SDK_CALLS = {
    "record": "analytics.record('note', 'from the script API')",
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


@pytest.mark.parametrize("column", ["lab_project_1", "analytics_2", "analytics_invalid", "both",
                                    "nested"])
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
        else:
            assert seen[name] == refused, (name, result)
            assert snapshot(tree) == before, name
    record_matrix("sdk", column, seen)


IN_RUN = """\
import json
from pathlib import Path
import aelix_analytics as analytics
from aelix_analytics import core

# The directory becomes one with two markers while the run goes on: every SDK write after that is
# refused in the script, before it writes (design 4.3, T8 'the marker changes during the call').
Path("analytics.json").write_text(json.dumps(MARKER), encoding="utf-8")
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
    refused where they are called, and the run's completion event is refused under the journal
    lock: the journal is the same bytes, no dataset or chart is written, and the run is `incomplete`
    (no_completion_event) with the refusal in finalize_errors (design 4.3 'run이면 정본만 있고')."""
    root, _ = _column(base, tmp_path, "lab_project_1")
    script = IN_RUN.replace("MARKER", repr(NEW))
    (root / "scripts" / "late.py").write_text(script, encoding="utf-8")
    journal = (root / "journal.jsonl").read_bytes()
    code, result = kit_cli(root, "run", "scripts/late.py", "--title", "late")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert 'CODES={"record": "marker_conflict", "save": "marker_conflict", "visualize": "marker_conflict"}' \
        in result["stdout_tail"]
    assert (root / "journal.jsonl").read_bytes() == journal
    assert not (root / "data" / "derived" / "late.parquet").exists()
    run_dir = root / "runs" / result["run"]["id"]
    assert not (run_dir / "charts").exists()
    assert any("journal: AnalyticsError" in p for p in result["run"]["finalize_errors"])
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert record["schema"] == "lab.run/1" and record["finalize_errors"]
    (root / "analytics.json").unlink()
    st = core.status(core.Project(root))
    row = next(r for r in st["runs"] if r["id"] == result["run"]["id"])
    assert row["recording_status"] == "incomplete"
    assert set(row["reasons"]) == {"no_completion_event", "finalize_errors"}


def test_a_journal_write_is_judged_again_under_the_lock(tmp_path, monkeypatch):
    """S8: a journal write is judged before the lock (its file is the first side effect) and again
    once the lock is held, so a marker made in between refuses the write and nothing is appended."""
    project = core.init_project(tmp_path / "p", template_dir=TEMPLATE, origin=ORIGIN)
    journal = project.journal.read_bytes()
    real = core.project_lock

    def lock_then_change(p):
        write_marker(p.root, "analytics.json", NEW)
        return real(p)

    monkeypatch.setattr(core, "project_lock", lock_then_change)
    with pytest.raises(core.AnalyticsError) as caught:
        core.add_note(project, "note", "between", origin=ORIGIN)
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
        write_marker(p.root, "analytics.json", NEW)
        return real(p)

    monkeypatch.setattr(core, "run_lock", lock_then_change)
    with pytest.raises(core.AnalyticsError) as caught:
        runner.execute(project, script="scripts/a.py", title="a", origin=ORIGIN)
    assert caught.value.code == "marker_conflict"
    assert project.journal.read_bytes() == journal
    assert not project.runs.exists() or not any(project.runs.iterdir())


def test_the_init_of_a_new_project_is_judged_by_its_own_rules(tmp_path):
    """design 11.3: `init` is judged by the destination's rules, not by this matrix. In this PR it
    keeps them (a directory that does not exist or is empty); a marker above the destination is
    OQ9, PR 3, so a new lab.json project inside an analytics.json one is still made."""
    above = tmp_path / "new"
    above.mkdir()
    write_marker(above, "analytics.json", NEW)
    with pytest.raises(core.AnalyticsError) as caught:
        core.init_project(above, template_dir=TEMPLATE, origin=ORIGIN)
    assert caught.value.code == "init_not_empty"
    inner = core.init_project(above / "inner", template_dir=TEMPLATE, origin=ORIGIN)
    assert writer.marker_state(inner.root).state == "legacy"
    assert sorted(p.name for p in above.iterdir()) == ["analytics.json", "inner"]
