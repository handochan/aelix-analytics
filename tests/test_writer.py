"""The common writer of the new format (Issue #13 PR 3; docs/design/RECORD-WRITER-AND-MIGRATION.md
2.3–2.9, 4.4–4.9 and 8): writer.Writer, its Layout and helpers, and the thin entry core.record.

What each test shows, by the design's numbering: the two layouts and the families (2.3, 4.4), the
kinds a person or an agent records and `gap` refused (2.8), a writer with no author of its own and
no environment (4.1), the line of a record (2.5, S12), refusals that write nothing (4.5, S8), the
whole text up to its limits (2.9, OQ2, T14), the request_id (4.9), binding names at write time (4.7),
the journal lock with the marker judged again under it (4.3, T10), state changes judged by the
reader's own replay and corrections (4.6, 8, T13), raising min_writer_version (4.8, ADR-0006 1.3),
run and import records with their completion events (4.8), and the record examples' `writes`
reproduced by the writer itself (tests/fixtures/records).
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import KIT, analytics_project, kit_env
from jsonschema import Draft202012Validator, FormatChecker
from legacy_records import LegacyProject

from aelix_analytics import core, migration, relations, writer

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "records"
SCHEMA = json.loads((ROOT / "docs" / "schemas" / "analytics-records.schema.json").read_text(encoding="utf-8"))
RECORD = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
REFERENCE = Draft202012Validator({"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"],
                                  "$ref": "#/$defs/reference"}, format_checker=FormatChecker())
ORIGIN = core.Origin("human", "cli", "inferred")
SEOUL = timezone(timedelta(hours=9))


def fixed_clock() -> datetime:
    return datetime(2026, 10, 8, 9, 0, 0, tzinfo=SEOUL)


def make(root, *, origin: core.Origin = ORIGIN, version: str | None = None, **kwargs) -> writer.Writer:
    return writer.Writer(root, platform=kwargs.pop("platform", core.PLATFORM), origin=origin,
                         writer_version=version or core.KIT_VERSION, **kwargs)


def case(tmp_path: Path, name: str) -> Path:
    """A copy of a record example (tests/fixtures/records) to write to."""
    root = tmp_path / name
    shutil.copytree(FIXTURES / name, root, ignore=shutil.ignore_patterns("expected.json"))
    return root


def tree(root: Path) -> dict:
    """Every entry under `root`, links as links: a file's size and sha256, a directory, a link's
    text. Lock files are entries like any other, so a lock file a refusal made is a difference."""
    out = {}
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                out[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                out[rel] = ("dir",)
            else:
                data = path.read_bytes()
                out[rel] = (len(data), hashlib.sha256(data).hexdigest())
    return out


def lines(root: Path) -> list[bytes]:
    return (root / "journal.jsonl").read_bytes().split(b"\n")[:-1]


def last(root: Path) -> dict:
    return json.loads(lines(root)[-1])


def refused(code: str, call) -> core.AnalyticsError:
    with pytest.raises(core.AnalyticsError) as caught:
        call()
    assert caught.value.code == code, caught.value.to_json()
    return caught.value


def valid(record) -> None:
    errors = [e.message for e in RECORD.iter_errors(record)]
    assert not errors, (errors, record)


def escaped(text: str) -> int:
    """The bytes a text takes inside a JSON line the writer writes (ensure_ascii=False)."""
    return len(json.dumps(text, ensure_ascii=False).encode("utf-8")) - 2


# --- the layouts, the families and the kinds (design 2.3, 2.8, 4.4) -------------------------------

def test_the_two_layouts_are_design_2_3():
    persons = {"scripts", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".omc", ".aelix",
               "node_modules", ".ipynb_checkpoints"}
    assert dataclasses.asdict(writer.LAYOUT) == {
        "marker": "analytics.json", "schema": "analytics.project/2", "state_dir": ".analytics",
        "workspace_dir": "workspace", "imported_dir": "data/imported", "visualizations_dir": "visualizations",
        "managed_dirs": (".venv", ".analytics", "runs", "workspace"),
        "ignored_dirs": frozenset({".venv", ".analytics", "runs", "workspace"} | persons),
        "journal_lock": "lock", "run_lock": "run.lock", "workspace_lock": "workspace.lock",
        "migration_lock": "migration.lock"}
    # The older layout's names stay as every kit before the new writer wrote them.
    assert dataclasses.asdict(writer.LEGACY_LAYOUT) == {
        "marker": "lab.json", "schema": "lab.project/1", "state_dir": ".lab", "workspace_dir": "report",
        "imported_dir": "data/raw", "visualizations_dir": "charts",
        "managed_dirs": (".venv", ".lab", "runs", "report"),
        "ignored_dirs": frozenset({".venv", ".lab", "runs", "report"} | persons),
        "journal_lock": "lock", "run_lock": "run.lock", "workspace_lock": "report.lock"}
    # D21: one layout's managed directories are not the other's (no union).
    assert not {".lab", "report"} & set(writer.LAYOUT.ignored_dirs)
    assert not {".analytics", "workspace"} & set(writer.LEGACY_LAYOUT.ignored_dirs)
    assert (writer.MARKER, writer.PROJECT, writer.MIGRATION_DIR, writer.MIGRATION_LOCK) == (
        "analytics.json", "analytics.project/2", ".analytics/migration", ".analytics/migration.lock")


LAYOUT_OF = {
    "none": (None, None, "LAYOUT"),
    "legacy": ({"schema": "lab.project/1"}, None, "LEGACY_LAYOUT"),
    "legacy_unknown": ({"schema": "lab.project/9"}, None, "LEGACY_LAYOUT"),
    "current": (None, {"schema": "analytics.project/2", "project_id": "prj_x", "title": "t",
                       "created_at": "2026-10-08T09:00:00.000+09:00", "min_writer_version": "0.1"}, "LAYOUT"),
    "unknown": (None, {"schema": "analytics.project/3"}, "LAYOUT"),
    "invalid": (None, b"{", "LAYOUT"),
    "conflict": ({"schema": "lab.project/1"}, {"schema": "analytics.project/2"}, None),
}


@pytest.mark.parametrize("name", LAYOUT_OF)
def test_a_marker_picks_its_layout(tmp_path, name):
    lab, new, expected = LAYOUT_OF[name]
    root = tmp_path / name
    root.mkdir()
    for file, value in (("lab.json", lab), ("analytics.json", new)):
        if value is not None:
            (root / file).write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    layout = writer.layout_of(writer.marker_state(root))
    assert layout is (getattr(writer, expected) if expected else None)


def test_a_migration_s_destination_has_the_new_layout(tmp_path):
    (tmp_path / ".analytics" / "migration").mkdir(parents=True)
    state = writer.marker_state(tmp_path)
    assert state.state == "incomplete" and writer.layout_of(state) is writer.LAYOUT


def test_the_families_the_writer_writes_are_named_by_what_they_are():
    assert writer.FAMILIES == {"project": "analytics.project/2", "event": "analytics.event/2",
                               "run": "analytics.run/2", "dataset": "analytics.dataset/1",
                               "trust": "analytics.trust/1", "migration": "analytics.migration/1"}
    # Every family a project holds is one the reader knows; the trust list is the host's, not the project's.
    assert {v for k, v in writer.FAMILIES.items() if k != "trust"} <= relations.KNOWN


def test_the_kinds_a_person_or_an_agent_records():
    assert writer.RECORD_KINDS == ("finding", "decision", "assumption", "question", "todo", "note",
                                   "workflow_feedback", "metric_definition", "analysis_focus",
                                   "metric_observation", "asset_candidate", "asset_check", "asset_effect", "checkpoint", "resolve")
    assert writer.KIT_KINDS == ("project", "import", "run", "context")
    assert set(writer.RECORD_KINDS + writer.KIT_KINDS) <= relations.KINDS[relations.EVENT_V2]
    assert "gap" not in writer.RECORD_KINDS and "record_update" not in writer.RECORD_KINDS
    assert core.RECORD_KINDS is writer.RECORD_KINDS


@pytest.mark.parametrize("kind", ["gap", "lab_note", "record_update", "", None])
def test_gap_and_other_kinds_are_refused_and_nothing_is_written(tmp_path, kind):
    root = analytics_project(tmp_path / "p")
    before = tree(root)
    for call in (lambda: make(root).record(kind, "text"),
                 lambda: core.record(core.Project(root), kind, "text", origin=ORIGIN)):
        error = refused("bad_kind", call)
        if kind == "gap":  # design 2.8: the older name says the new one, with no alias
            assert error.details["use"] == "workflow_feedback" and "workflow_feedback" in error.hint
        assert error.details["kinds"] in (list(writer.RECORD_KINDS),
                                          list(writer.RECORD_KINDS + writer.KIT_KINDS))
    # The kit's own kinds are no input of a person or an agent.
    refused("bad_kind", lambda: core.record(core.Project(root), "run", "text", origin=ORIGIN))
    assert tree(root) == before


def test_the_writer_has_no_author_of_its_own_and_reads_no_environment():
    """Design 4.1 and 3.2: an entry point hands the writer its Origin; the writer reads no
    environment variable and does not import core (whose Project it has none of)."""
    source = (KIT / "aelix_analytics" / "writer.py").read_text(encoding="utf-8")
    tree_ = ast.parse(source)
    reads = [node.lineno for node in ast.walk(tree_)
             if (isinstance(node, ast.Attribute) and node.attr in ("environ", "environb", "getenv",
                                                                   "putenv", "unsetenv"))
             or (isinstance(node, ast.ImportFrom) and node.module == "os"
                 and any(a.name in ("environ", "getenv") for a in node.names))]
    assert not reads, f"writer.py reads the environment at lines {reads}"
    imported = {alias.name for node in ast.walk(tree_) if isinstance(node, ast.ImportFrom)
                and node.level == 1 for alias in node.names}
    assert imported == {"_platform", "checkpoints", "relations"}
    with pytest.raises(TypeError):
        writer.Writer(KIT, platform=core.PLATFORM, writer_version=core.KIT_VERSION)  # no origin
    with pytest.raises(TypeError):
        writer.Writer(KIT, platform=core.PLATFORM, origin=None, writer_version=core.KIT_VERSION)
    with pytest.raises(TypeError):
        core.Origin()  # no default author


# --- the line (design 2.5, S12) -----------------------------------------------------------------

def test_a_record_s_line_holds_every_origin_field_in_the_order_of_design_2_5(tmp_path):
    root = analytics_project(tmp_path / "p")
    origin = core.Origin("agent", "tool", "declared", "/s/session-1.jsonl", "req-line-1")
    text = "서울 매출이 가장 높다 — “인용”, \\ 백슬래시,   줄 구분, \x01 제어 문자"
    written = make(root, origin=origin, clock=fixed_clock).record("finding", f"  {text}\n", refs=["e1"])
    data = (root / "journal.jsonl").read_bytes()
    assert data.endswith(b"\n") and b"\r" not in data
    value = json.loads(lines(root)[-1])
    assert list(value) == ["schema", "seq", "id", "recorded_at", "kind", "actor", "actor_basis", "means",
                           "session", "request_id", "text", "refs"]
    assert value == {"schema": "analytics.event/2", "seq": 3, "id": "e3",
                     "recorded_at": "2026-10-08T09:00:00.000+09:00", "kind": "finding", "actor": "agent",
                     "actor_basis": "declared", "means": "tool", "session": "/s/session-1.jsonl",
                     "request_id": "req-line-1", "text": text,
                     "refs": [{"type": "event", "event_id": "e1", "relation": "cites", "basis": "declared",
                               "version_binding": "author"}]}
    assert lines(root)[-1] == json.dumps(value, ensure_ascii=False).encode("utf-8")
    assert written.event == value and written.request_id == "req-line-1"
    valid(value)
    # No session: none written (Q17); no request_id given: one in the answer, none in the record.
    other = make(root, origin=core.Origin("human", "cli", "inferred"), clock=fixed_clock).record("note", "x")
    value = last(root)
    assert list(value) == ["schema", "seq", "id", "recorded_at", "kind", "actor", "actor_basis", "means",
                           "text", "refs"]
    assert writer.REQUEST_ID_RE.fullmatch(other.request_id) and "request_id" not in other.event
    valid(value)
    # A kit's own record keeps its details last.
    w = make(root, clock=fixed_clock)
    w.record("project", "project created again", details={"project_id": "prj_0000000000000001"})
    assert list(last(root))[-1] == "details"
    valid(last(root))


def test_every_line_of_the_writer_follows_the_record_schema(tmp_path):
    root = analytics_project(tmp_path / "p")
    p = core.Project(root)
    for kind in writer.RECORD_KINDS:
        if kind in ("metric_definition", "analysis_focus", "metric_observation", "asset_candidate", "asset_check", "asset_effect"):
            # These kinds need structured details and their schema/semantic coverage is in
            # test_metrics.py/test_assets.py; a kind alone is deliberately no valid record.
            continue
        if kind == "resolve":
            core.record(p, kind, "closed", resolves="e3", origin=ORIGIN)
        else:
            core.record(p, kind, f"a {kind}", refs=["e1", "e2"], origin=ORIGIN)
    for line in lines(root):
        valid(json.loads(line))


# --- refusals write nothing (design 4.5, S8) ------------------------------------------------------

def _lab(root: Path) -> None:
    (root / "analytics.json").unlink()
    (root / "lab.json").write_text('{"schema": "lab.project/1"}\n', encoding="utf-8")


def _both(root: Path) -> None:
    (root / "lab.json").write_text('{"schema": "lab.project/1"}\n', encoding="utf-8")


def _higher(root: Path) -> None:
    marker = json.loads((root / "analytics.json").read_text(encoding="utf-8"))
    writer.write_json(root / "analytics.json", {**marker, "min_writer_version": "99.0"}, platform=core.PLATFORM)


def _incomplete(root: Path) -> None:
    (root / "analytics.json").unlink()
    (root / ".analytics" / "migration").mkdir()


def _none(root: Path) -> None:
    (root / "analytics.json").unlink()


# name -> (change to the project, origin, call, code, refused before the lock)
REFUSALS = {
    "no_project": (_none, ORIGIN, lambda w, p: w.record("note", "x"), "no_project", True),
    "older_format": (_lab, ORIGIN, lambda w, p: w.record("note", "x"), "migration_required", True),
    "two_markers": (_both, ORIGIN, lambda w, p: w.record("note", "x"), "marker_conflict", True),
    "too_old": (_higher, ORIGIN, lambda w, p: w.record("note", "x"), "writer_too_old", True),
    "incomplete": (_incomplete, ORIGIN, lambda w, p: w.record("note", "x"), "migration_incomplete", True),
    "gap": (None, ORIGIN, lambda w, p: w.record("gap", "x"), "bad_kind", True),
    "empty": (None, ORIGIN, lambda w, p: w.record("note", " \n\t "), "empty_note", True),
    "text_too_long": (None, ORIGIN, lambda w, p: w.record("note", "a" * (writer.TEXT_LIMIT + 1)),
                      "text_too_long", True),
    "request_id": (None, core.Origin("human", "cli", "inferred", request_id="nul.1"),
                   lambda w, p: w.record("note", "x"), "name_not_portable", True),
    "resolve_without_target": (None, ORIGIN, lambda w, p: core.record(p, "resolve", "x", origin=ORIGIN),
                               "resolve_needs_target", True),
    "resolves_no_event_id": (None, ORIGIN, lambda w, p: core.record(p, "note", "x", resolves="x1",
                                                                    origin=ORIGIN), "unknown_event", True),
    "resolves_absent_event": (None, ORIGIN, lambda w, p: core.record(p, "note", "x", resolves="e99",
                                                                     origin=ORIGIN), "unknown_event", True),
    "update_conflict": (None, ORIGIN, lambda w, p: w.update("e1", expected="current", new="withdrawn",
                                                            text="x"), "state_conflict", True),
    "update_too_long": (None, ORIGIN, lambda w, p: w.update("e3", expected="current", new="withdrawn",
                                                            text="a" * (writer.TEXT_LIMIT + 1)),
                        "text_too_long", True),
}


@pytest.mark.parametrize("name", REFUSALS)
def test_a_refused_write_leaves_the_project_as_it_was(tmp_path, name):
    change, origin, call, code, before_lock = REFUSALS[name]
    root = analytics_project(tmp_path / "p")
    if change is not None:
        change(root)
    if before_lock:  # refused before the lock: not even the lock file is made
        (root / ".analytics" / "lock").unlink()
    before = tree(root)
    refused(code, lambda: call(make(root, origin=origin), core.Project(root)))
    assert tree(root) == before


# --- the whole text, up to its limits (design 2.9, OQ2, T14) ---------------------------------------

@pytest.mark.parametrize("unit", ["a", "가", "😀"])
def test_a_text_of_32_kib_is_kept_whole_and_one_byte_more_is_refused(tmp_path, unit):
    root = analytics_project(tmp_path / "p")
    size = len(unit.encode("utf-8"))
    text = unit * (writer.TEXT_LIMIT // size) + "a" * (writer.TEXT_LIMIT % size)
    assert len(text.encode("utf-8")) == writer.TEXT_LIMIT
    make(root).record("note", text)
    assert last(root)["text"] == text  # kept whole, not cut
    before = tree(root)
    error = refused("text_too_long", lambda: make(root).record("note", text + "a"))
    assert error.details["text_bytes"] == writer.TEXT_LIMIT + 1
    assert (error.details["text_limit"], error.details["line_limit"]) == (32768, 51200)
    assert "Report" in error.hint and "nothing was cut" in error.message
    assert tree(root) == before


@pytest.mark.parametrize("filler", ["\x01", '"', "\\"], ids=["control", "quote", "backslash"])
def test_a_line_of_50_kib_is_refused_whatever_makes_it_long(tmp_path, filler):
    """The line, not only the text: escapes (a control character takes 6 bytes, a quote or a
    backslash 2) and 24 long references make a line of 51,200 bytes from a text far under 32 KiB."""
    first = analytics_project(tmp_path / "p")
    probe, over = tmp_path / "probe", tmp_path / "over"
    shutil.copytree(first, probe)
    shutil.copytree(first, over)
    refs = [f"{'v' * 150}-{i:02d}" for i in range(24)]
    make(probe, clock=fixed_clock).record("note", "x", refs=refs)
    overhead = len(lines(probe)[-1]) + 1 - escaped("x")
    target = writer.LINE_LIMIT - 1 - overhead  # the escaped text of a line of 51,199 bytes
    count, rest = divmod(target, escaped(filler))
    text = filler * count + "a" * rest
    assert len(text.encode("utf-8")) < writer.TEXT_LIMIT
    written = make(first, clock=fixed_clock).record("note", text, refs=refs)
    assert len(lines(first)[-1]) + 1 == writer.LINE_LIMIT - 1 and written.event["text"] == text
    assert len(written.event["refs"]) == 24
    before = tree(over)
    error = refused("text_too_long", lambda: make(over, clock=fixed_clock).record("note", text + "a", refs=refs))
    assert error.details["line_bytes"] == writer.LINE_LIMIT
    assert error.details["text_bytes"] == len(text.encode("utf-8")) + 1
    assert tree(over) == before


# --- the request_id (design 4.9, ADR-0006 8) -----------------------------------------------------

def test_the_request_id_rule_is_the_schema_s():
    assert writer.REQUEST_ID_RE.pattern == SCHEMA["$defs"]["request_id"]["pattern"]


@pytest.mark.parametrize("request_id", ["nul.1", "Con", "req.", "-x", "a" * 129, "con.txt", "a b", "req\n"])
def test_a_request_id_that_is_no_portable_name_is_refused(tmp_path, request_id):
    root = analytics_project(tmp_path / "p")
    before = tree(root)
    origin = core.Origin("human", "cli", "inferred", request_id=request_id)
    refused("name_not_portable", lambda: make(root, origin=origin).record("note", "x"))
    refused("name_not_portable", lambda: make(root, origin=origin).update("e1", expected="current",
                                                                          new="withdrawn", text="x"))
    assert tree(root) == before


def test_a_request_id_given_is_in_the_record_and_one_made_is_in_the_answer_only(tmp_path):
    root = analytics_project(tmp_path / "p")
    given = make(root, origin=core.Origin("human", "cli", "inferred", request_id="REQ.1_a")).record("note", "x")
    assert given.request_id == "REQ.1_a" and last(root)["request_id"] == "REQ.1_a"
    made = make(root).record("note", "y")
    assert made.request_id != given.request_id and writer.REQUEST_ID_RE.fullmatch(made.request_id)
    assert "request_id" not in last(root)
    assert made.to_json() == {"event": last(root), "request_id": made.request_id, "bindings": []}


# --- binding names at write time (design 4.7) ------------------------------------------------------

SAME = "20261008-090000-same"
OTHER = "20261008-091000-other"


def _run_2(root: Path, run_id: str, script: str, vis_id: str, *, request_id=None, outputs=()) -> str:
    """An analytics.run/2 run of `script` with one Visualization (its file and sha256), completed
    by its run event (the writer's own)."""
    picture = f"runs/{run_id}/visualizations/{vis_id}.png".encode()
    vis_file = root / "runs" / run_id / "visualizations" / f"{vis_id}.png"
    vis_file.parent.mkdir(parents=True)
    vis_file.write_bytes(picture)
    record = {"id": run_id, "title": run_id, "status": "ok", "started_at": "2026-10-08T09:00:00.000+09:00",
              "actor": "human", "actor_basis": "inferred", "means": "cli",
              "script": {"path": script, "sha256": "0" * 64, "snapshot": f"runs/{run_id}/code/{script}"},
              "inputs": [], "outputs": [{"path": path, "change": "created", "sha256": sha, "via": "diff"}
                                        for path, sha in outputs],
              "visualizations": [{"visualization_id": vis_id, "kind": "png", "title": vis_id, "via": "file",
                                  "files": {"png": vis_file.relative_to(root).as_posix()},
                                  "sha256": hashlib.sha256(picture).hexdigest()}]}
    if request_id:
        record["request_id"] = request_id
    w = make(root, origin=core.Origin("test", "sdk"))
    w.write_run(root / "runs" / run_id, record)
    w.record("run", f"{run_id} — ok", refs=[{"type": "run", "run_id": run_id, "relation": "about",
                                              "basis": "observed"}])
    return hashlib.sha256(picture).hexdigest()


@pytest.fixture
def bound(tmp_path) -> dict:
    """The `normal` example with an import, and two analytics.run/2 runs: one of the request
    `req-same-1` that makes scripts/region_summary.py::region-revenue anew and outputs a parquet, and
    one of another script with a Visualization `monthly-trend` (so that id is in two scripts)."""
    root = case(tmp_path, "normal")
    parquet = b"PAR1sales"
    (root / "data" / "imported").mkdir(parents=True)
    (root / "data" / "imported" / "sales.parquet").write_bytes(parquet)
    sales = hashlib.sha256(parquet).hexdigest()
    make(root).write_dataset(root / "data" / "imported" / "sales.meta.json", {
        "name": "sales", "path": "data/imported/sales.parquet", "sha256": sales, "bytes": len(parquet),
        "rows": 1, "columns": [], "description": None, "imported_at": "2026-10-08T09:00:00.000+09:00",
        "source": {"path": "/elsewhere/sales.csv", "sha256": sales, "bytes": 1, "format": "csv"}})
    rank = hashlib.sha256(b"rank").hexdigest()
    same = _run_2(root, SAME, "scripts/region_summary.py", "region-revenue", request_id="req-same-1",
                  outputs=[("data/derived/region_rank.parquet", rank)])
    other = _run_2(root, OTHER, "scripts/other.py", "monthly-trend")
    context = "20261001-090000-000000_c3c0b6b900db.md"
    return {"root": root, "sales": sales, "rank": rank, "same": same, "other": other, "context": context,
            "context_sha": hashlib.sha256((root / ".analytics" / "context" / context).read_bytes()).hexdigest()}


def _ref(**fields) -> dict:
    return {**fields, "relation": "cites", "basis": "declared"}


def _rows(b: dict) -> dict:
    """input -> (request_id, the reference written, its binding, candidates)."""
    def same_vis(token=None) -> dict:  # in the order the writer writes a reference's fields
        return {"type": "visualization_version", "run_id": SAME, "visualization_id": "region-revenue",
                **({"token": token} if token else {}), "digest": {"sha256": b["same"]}}

    return {
        "a run": ("20261001-091000-region-summary", None,
                  _ref(type="run", run_id="20261001-091000-region-summary"), "author", None),
        "an event": ("e3", None, _ref(type="event", event_id="e3"), "author", None),
        "a context version": (b["context"], None, _ref(type="context_version", version=b["context"],
                                                       digest={"sha256": b["context_sha"]}), "author", None),
        "a key of the same request": ("scripts/region_summary.py::region-revenue", "req-same-1",
                                      _ref(**same_vis()), "author", None),
        "a key's newest version": ("scripts/region_summary.py::region-revenue", None,
                                   _ref(**same_vis("scripts/region_summary.py::region-revenue")),
                                   "inferred", None),
        "a key of a lab.run/1 run": ("scripts/monthly_trend.py::monthly-trend", None,
                                     _ref(type="visualization_version", run_id="20261001-092000-monthly-trend",
                                          visualization_id="monthly-trend",
                                          token="scripts/monthly_trend.py::monthly-trend"), "inferred", None),
        "a key without a version": ("scripts/none.py::nothing", None,
                                    _ref(type="visualization_version", key="scripts/none.py::nothing"), "none",
                                    None),
        "an id of one key": ("region-revenue", None, _ref(**same_vis("region-revenue")),
                             "inferred", None),
        "an id of two keys": ("monthly-trend", None, _ref(type="visualization_version", token="monthly-trend"),
                              "none", [f"{OTHER}/monthly-trend", "20261001-092000-monthly-trend/monthly-trend"]),
        "a dataset name": ("sales", None, _ref(type="dataset_import", path="data/imported/sales.parquet",
                                               token="sales", digest={"sha256": b["sales"]}), "inferred", None),
        "a dataset path": ("data/imported/sales.parquet", None,
                           _ref(type="dataset_import", path="data/imported/sales.parquet",
                                token="data/imported/sales.parquet", digest={"sha256": b["sales"]}),
                           "inferred", None),
        "a run's output": ("region_rank", None, _ref(type="data_observation", run_id=SAME, role="output",
                                                     path="data/derived/region_rank.parquet", token="region_rank",
                                                     digest={"sha256": b["rank"]}), "inferred", None),
        "a dataset name nothing has": ("nothing_here", None,
                                       _ref(type="visualization_version", token="nothing_here"), "none", None),
        "a parquet path nothing has": ("missing.parquet", None,
                                       _ref(type="dataset_import", token="missing.parquet"), "none", None),
        "anything else (D25)": ("?!", None, _ref(type="visualization_version", token="?!"), "none", None),
    }


BINDINGS = ["a run", "an event", "a context version", "a key of the same request", "a key's newest version",
            "a key of a lab.run/1 run", "a key without a version", "an id of one key", "an id of two keys",
            "a dataset name", "a dataset path", "a run's output", "a dataset name nothing has",
            "a parquet path nothing has", "anything else (D25)"]


@pytest.mark.parametrize("row", BINDINGS)
def test_a_name_is_bound_when_it_is_written(bound, row):
    root = bound["root"]
    name, request_id, ref, binding, candidates = _rows(bound)[row]
    ref = {**ref, "version_binding": binding}
    origin = core.Origin("human", "cli", "inferred", request_id=request_id)
    written = make(root, origin=origin).record("finding", "bound", refs=[name])
    entry = {"input": name, "ref": ref, "binding": binding}
    if candidates:
        entry["candidates"] = sorted(candidates)
    assert written.bindings == [entry]
    value = last(root)
    assert value["refs"] == [ref] and list(value["refs"][0]) == list(ref)
    assert not list(REFERENCE.iter_errors(ref)), list(REFERENCE.iter_errors(ref))
    valid(value)
    # Q4: only an author binding can be a confirmed link; an inferred one never reads as one.
    [judged] = core.read_model(core.Project(root)).references(value["id"])
    assert judged["version_binding"] == binding
    assert judged["confirmed"] is (binding == "author")
    if binding == "author":
        assert judged["state"] == "resolved", judged


def test_names_keep_their_order_and_a_repeated_one_is_written_once(bound):
    root = bound["root"]
    written = make(root).record("note", "several", refs=["e3", " e3 ", "sales", "", "e1"])
    assert [b["input"] for b in written.bindings] == ["e3", "sales", "e1"]
    assert [r.get("event_id") or r.get("path") for r in last(root)["refs"]] == [
        "e3", "data/imported/sales.parquet", "e1"]


def test_a_run_that_completes_between_binding_and_the_lock_is_bound_under_the_lock(bound):
    """Step 4 of design 4.5: names are bound outside the lock at seq N; when the journal under the
    lock has grown, they are bound again there, so the record names the newest version as of its
    own seq."""
    root = bound["root"]
    newer = "20261008-100000-newer"
    w = make(root)
    real = w.locked

    @contextlib.contextmanager
    def racing(op="write"):
        _run_2(root, newer, "scripts/region_summary.py", "region-revenue")
        with real(op) as held:
            yield held

    w.locked = racing
    written = w.record("finding", "raced", refs=["scripts/region_summary.py::region-revenue"])
    assert written.event["refs"][0]["run_id"] == newer
    assert written.bindings[0]["ref"]["run_id"] == newer


# --- the journal lock and the marker judged again (design 4.3, 4.5, T10) ---------------------------

class HookedLocks:
    """The platform's locks, with something done once right before the journal lock is taken."""

    def __init__(self, hook):
        self.hook = hook

    @contextlib.contextmanager
    def exclusive(self, path):
        hook, self.hook = self.hook, None
        if hook is not None:
            hook()
        with core.PLATFORM.locks.exclusive(path):
            yield


def hooked(hook) -> types.SimpleNamespace:
    real = core.PLATFORM
    return types.SimpleNamespace(name=real.name, locks=HookedLocks(hook), files=real.files,
                                 processes=real.processes, runs=real.runs)


@pytest.mark.parametrize("change, code", [(_lab, "migration_required"), (_both, "marker_conflict"),
                                          (_higher, "writer_too_old")], ids=["lab_json", "two", "higher"])
def test_a_marker_changed_between_two_writes_is_judged_again(tmp_path, change, code):
    root = analytics_project(tmp_path / "p")
    w = make(root)
    w.record("note", "before the change")
    change(root)
    before = tree(root)
    refused(code, lambda: w.record("note", "after the change"))
    refused(code, lambda: w.update("e3", expected="current", new="withdrawn", text="x"))
    assert tree(root) == before


def test_an_older_writer_that_waited_for_the_lock_is_refused_under_it(tmp_path):
    """A newer kit raises min_writer_version under the journal lock while an older one, admitted
    already, waits for the lock: the older one judges the marker again once it holds the lock and is
    refused (writer_too_old), with nothing written (design 4.3)."""
    root = analytics_project(tmp_path / "p", version="0.8.0")
    newer = make(root, version="0.9.0")
    older = make(root, version="0.8.0",
                 platform=hooked(lambda: newer.record("note", "needs 0.9.0", requires="0.9.0")))
    older.admit("record")  # admitted before the newer writer's change
    error = refused("writer_too_old", lambda: older.record("note", "the older writer's"))
    assert error.details == {"min_writer_version": "0.9.0", "kit_version": "0.8.0"}
    assert [json.loads(line)["text"] for line in lines(root)][-1] == "needs 0.9.0"
    assert all(b"older writer" not in line for line in lines(root))


def test_a_run_whose_completion_an_older_writer_cannot_write_stays_incomplete(tmp_path):
    root = analytics_project(tmp_path / "p", version="0.8.0")
    run_id = "20261008-090000-a"
    newer = make(root, version="0.9.0")
    older = make(root, version="0.8.0",
                 platform=hooked(lambda: newer.record("note", "needs 0.9.0", requires="0.9.0")))
    older.write_run(root / "runs" / run_id, {**RUN, "id": run_id})
    refused("writer_too_old", lambda: older.record("run", "a — ok", refs=[
        {"type": "run", "run_id": run_id, "relation": "about", "basis": "observed"}]))
    [run] = core.read_model(core.Project(root)).runs()
    assert (run["recording_status"], run["reasons"]) == ("incomplete", ["no_completion_event"])


def test_locked_yields_the_journal_as_it_is_under_the_lock(tmp_path):
    root = analytics_project(tmp_path / "p")
    w = make(root)
    with w.locked("save a report") as held:
        assert held.state.state == "current" and held.journal.next_seq == 3
        assert held.replay.state_of("e1") is None  # a project record has no state
        first = held.append("note", "inside the lock")
        second = held.append("note", "and again")
        assert (first["id"], second["id"]) == ("e3", "e4") and held.journal.next_seq == 5
    assert [last(root)["id"]] == ["e4"]
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "lab.json").write_text('{"schema": "lab.project/1"}', encoding="utf-8")
    with pytest.raises(core.AnalyticsError) as caught, make(lab).locked():
        pass
    assert caught.value.code == "migration_required" and sorted(os.listdir(lab)) == ["lab.json"]


# --- state changes (design 4.6 and 8, T13) ---------------------------------------------------------

RECORD_UPDATES = [(name, index, write) for name in ("normal", "conflict")
                  for index, write in enumerate(json.loads((FIXTURES / name / "expected.json").read_text(
                      encoding="utf-8"))["writes"]) if write["operation"] == "record_update"]


@pytest.mark.parametrize("name, index, write", RECORD_UPDATES,
                         ids=[f"{n}-{i}" for n, i, _ in RECORD_UPDATES])
def test_the_record_examples_record_updates_are_what_the_writer_does(tmp_path, name, index, write):
    """The examples' `writes` of record_update (tests/fixtures/records), done by the writer on a copy:
    `saved` writes one record_update and the reader replays the new state; `state_conflict` writes
    nothing."""
    root = case(tmp_path, name)
    request = write["request"]
    w = make(root)
    if write["result"] == "saved":
        written = w.update(request["target"], expected=request["expected_state"], new=request["new_state"],
                           text="as the example asks")
        value = last(root)
        valid(value)
        assert value["kind"] == "record_update" and value["details"] == {
            "expected_state": request["expected_state"], "new_state": request["new_state"]}
        assert value["refs"] == [{"type": "event", "event_id": request["target"], "relation": "updates",
                                  "basis": "declared", "version_binding": "author"}]
        assert written.targets == [{"event_id": request["target"], "relation": "updates",
                                    "state_before": request["expected_state"],
                                    "state_after": request["new_state"]}]
        states = relations.read_journal(root).replay().record_states()
        assert states[request["target"]] == {"state": request["new_state"], "mismatched_updates": []}
    else:
        before = tree(root)
        refused(write["result"], lambda: w.update(request["target"], expected=request["expected_state"],
                                                  new=request["new_state"], text="x"))
        assert tree(root) == before


@pytest.mark.parametrize("name, target, expected, new, details", [
    ("normal", "e9", "withdrawn", "current",
     {"state": "withdrawn", "allowed": ["superseded"], "made_by": {"id": "e10"},
      "reason": "transition_not_allowed"}),
    ("normal", "e3", "open", "withdrawn",
     {"state": "open", "allowed": [], "made_by": {"id": "e13"}, "reason": "transition_not_allowed"}),
    ("normal", "e9", "current", "withdrawn",
     {"state": "withdrawn", "allowed": ["superseded"], "made_by": {"id": "e10"}, "reason": "state_differs"}),
    ("normal", "e99", "current", "withdrawn", {"reason": "target_missing"}),
    ("normal", "journal:5", "current", "withdrawn", {"reason": "target_missing"}),
    ("normal", "e4", "current", "withdrawn", {"reason": "no_state"}),
    ("mixed-version", "e7", "current", "withdrawn",
     {"reason": "target_ambiguous", "candidates": ["journal:7", "journal:8"]}),
])
def test_a_state_conflict_says_what_the_record_is_and_what_made_it_so(tmp_path, name, target, expected,
                                                                        new, details):
    root = case(tmp_path, name)
    before = tree(root)
    error = refused("state_conflict", lambda: make(root).update(target, expected=expected, new=new, text="x"))
    assert error.details == {"target": target, "expected_state": expected, "new_state": new, **details}
    assert tree(root) == before


UPDATE = """
import json, sys
from aelix_analytics import core, writer
w = writer.Writer(sys.argv[1], platform=core.PLATFORM, writer_version=core.KIT_VERSION,
                  origin=core.Origin("test", "sdk", request_id=sys.argv[2]))
try:
    w.update(sys.argv[3], expected="current", new="withdrawn", text="withdrawn by " + sys.argv[2])
    print(json.dumps({"ok": True}))
except core.AnalyticsError as exc:
    print(json.dumps(exc.to_json()))
"""


def test_two_processes_withdrawing_one_record_write_one_update(tmp_path):
    """The journal lock orders them; the later one replays the state the earlier one made and is
    refused with that update's id and request_id (design 8)."""
    root = analytics_project(tmp_path / "p")
    target = core.record(core.Project(root), "decision", "to withdraw", origin=ORIGIN).event["id"]
    procs = [subprocess.Popen([sys.executable, "-c", UPDATE, str(root), f"req-race-{i}", target],
                              stdout=subprocess.PIPE, env=kit_env()) for i in range(4)]
    outs = [json.loads(p.communicate(timeout=120)[0].decode().strip().splitlines()[-1]) for p in procs]
    assert [p.returncode for p in procs] == [0, 0, 0, 0]
    updates = [json.loads(line) for line in lines(root) if json.loads(line)["kind"] == "record_update"]
    assert len(updates) == 1 and sum(out.get("ok") is True for out in outs) == 1
    winner = updates[0]
    for out in outs:
        if not out.get("ok"):
            assert out["error"]["code"] == "state_conflict"
            assert out["error"]["details"]["made_by"] == {"id": winner["id"], "request_id": winner["request_id"]}


def test_a_retried_update_is_refused_with_its_own_request_id(tmp_path):
    """A record_update written and its answer lost: the same request again is state_conflict, and
    the event that made the state has the request's own request_id, so the caller knows its write."""
    root = analytics_project(tmp_path / "p")
    target = core.record(core.Project(root), "finding", "to withdraw", origin=ORIGIN).event["id"]
    w = make(root, origin=core.Origin("agent", "tool", request_id="req-retry-1"))
    first = w.update(target, expected="current", new="withdrawn", text="withdrawn")
    error = refused("state_conflict", lambda: w.update(target, expected="current", new="withdrawn",
                                                       text="withdrawn"))
    assert error.details["made_by"] == {"id": first.event["id"], "request_id": "req-retry-1"}
    assert "req-retry-1" in error.hint


def test_two_corrections_of_one_record_are_written_and_say_conflict(tmp_path):
    """ADR-0006 6: a correction has no expected state; two corrections of one record make it
    `conflict`, never the later one by time (design 4.6)."""
    root = analytics_project(tmp_path / "p")
    w = make(root)
    original = w.record("decision", "compare by region").event["id"]
    correction = {"type": "event", "event_id": original, "relation": "supersedes"}
    one = w.record("decision", "compare by region and channel", refs=[correction])
    two = w.record("decision", "compare by channel", refs=[correction])
    assert one.targets == [{"event_id": original, "relation": "supersedes", "state_before": "current",
                            "state_after": "superseded", "corrected_by": [one.event["id"]]}]
    assert two.targets == [{"event_id": original, "relation": "supersedes", "state_before": "superseded",
                            "state_after": "conflict", "made_by": {"id": one.event["id"]},
                            "corrected_by": [one.event["id"], two.event["id"]]}]
    assert core.read_model(core.Project(root)).record_states()[original]["state"] == "conflict"


def test_closing_a_question_twice_writes_both_and_says_it_was_closed(tmp_path):
    """--resolves (OQ11): not refused — a new record has no expected state — but the answer says the
    question was resolved already, and by which record and request."""
    root = analytics_project(tmp_path / "p")
    p = core.Project(root)
    question = core.record(p, "question", "which region leads?", origin=ORIGIN).event["id"]
    one = core.record(p, "finding", "Seoul leads", resolves=question,
                      origin=core.Origin("agent", "tool", request_id="req-close-1"))
    two = core.record(p, "resolve", "closed again", resolves=question, origin=ORIGIN)
    assert one.targets == [{"event_id": question, "relation": "resolves", "state_before": "open",
                            "state_after": "resolved"}]
    assert two.targets == [{"event_id": question, "relation": "resolves", "state_before": "resolved",
                            "state_after": "resolved",
                            "made_by": {"id": one.event["id"], "request_id": "req-close-1"}}]
    assert [json.loads(line)["id"] for line in lines(root)][-2:] == [one.event["id"], two.event["id"]]
    no_state = core.record(p, "note", "closing the project record", resolves="e1", origin=ORIGIN)
    assert no_state.targets == [{"event_id": "e1", "relation": "resolves", "state_before": None,
                                 "state_after": None, "reason": "no_state"}]


# --- min_writer_version (design 4.8, ADR-0006 1.3) -------------------------------------------------

def _changed_lines(before: bytes, after: bytes) -> list[tuple[str, str]]:
    old, new = before.decode("utf-8").splitlines(), after.decode("utf-8").splitlines()
    assert len(old) == len(new)
    return [(a, b) for a, b in zip(old, new, strict=True) if a != b]


def test_requires_raises_min_writer_version_and_changes_nothing_else_of_the_marker(tmp_path):
    root = case(tmp_path, "normal")
    marker = root / "analytics.json"
    before = marker.read_bytes()
    make(root, version="0.4.0").record("note", "a record a 0.4.0 writer means", requires="0.4.0")
    after = marker.read_bytes()
    assert _changed_lines(before, after) == [('  "min_writer_version": "0.3.0",', '  "min_writer_version": "0.4.0",'),
                                             ('  "kit_version": "0.3.0"', '  "kit_version": "0.4.0"')]
    assert list(json.loads(after)) == list(json.loads(before))
    assert last(root)["text"] == "a record a 0.4.0 writer means"
    refused("writer_too_old", lambda: make(root, version="0.3.0").record("note", "x"))
    unchanged = marker.read_bytes()
    make(root, version="0.4.0").record("note", "no higher", requires="0.3.0")
    assert marker.read_bytes() == unchanged
    with pytest.raises(ValueError):
        make(root, version="0.4.0").record("note", "x", requires="0.5.0")  # above the writer itself
    assert marker.read_bytes() == unchanged


def test_a_record_refused_under_the_lock_raises_no_marker(tmp_path):
    """Design 4.5 steps 5 and 6: the marker is raised only for a line that is written. A line found
    too long under the lock (here: a text that fits only the line the writer measured before the
    lock) leaves the marker as it was."""
    root = case(tmp_path, "normal")
    marker = root / "analytics.json"
    before, journal = marker.read_bytes(), (root / "journal.jsonl").read_bytes()
    w = make(root, version="0.4.0")
    with w.locked() as held:
        with pytest.raises(core.AnalyticsError) as caught:
            held.append("note", "a" * (writer.TEXT_LIMIT + 1), requires="0.4.0")
        assert caught.value.code == "text_too_long"
        with pytest.raises(ValueError):
            held.append("gap", "x")  # no kind of the format
        with pytest.raises(ValueError):
            held.append("note", "x", refs=[{"type": "event", "event_id": "e1"}])  # no relation, basis, binding
    assert marker.read_bytes() == before and (root / "journal.jsonl").read_bytes() == journal


def test_raising_a_migrated_project_s_marker_keeps_its_migration(tmp_path):
    """The marker's `migration` field stays as it was, so the mapping is still read (design 4.8,
    5.4): the status overview's migration and every reference judgment of the records before are
    the same after the raise."""
    source = LegacyProject(tmp_path / "src")
    source.file("data/raw/a.parquet", b"PAR1a")
    source.run("20261001-091000-old", inputs=[(str(source.root / "data" / "raw" / "a.parquet"),
                                               hashlib.sha256(b"PAR1a").hexdigest())], kit_version="0.4.0")
    source.event("finding", {"runs": ["20261001-091000-old"]}, text="from the old run")
    dest = tmp_path / "dest"
    assert migration.migrate(source.root, dest, origin=ORIGIN)["ok"]
    p = core.Project(dest)
    marker = dest / "analytics.json"
    before, status_before = marker.read_bytes(), core.status(p)["project"]
    judged_before = core.read_model(p).references()
    version = f"{core.KIT_VERSION}.post1"
    make(dest, version=version).record("note", "after the migration", requires=version)
    after = marker.read_bytes()
    assert [line[1].split(":")[0] for line in _changed_lines(before, after)] == [
        '  "min_writer_version"', '  "kit_version"']
    assert json.loads(after)["migration"] == json.loads(before)["migration"]
    assert status_before["migration"]["applied"] is True
    assert core.status(p)["project"]["migration"] == status_before["migration"]
    assert core.read_model(p).references()[:len(judged_before)] == judged_before


EVENT_APPENDS = [(name, index, write) for name in ("normal", "future-version", "interrupted")
                 for index, write in enumerate(json.loads((FIXTURES / name / "expected.json").read_text(
                     encoding="utf-8"))["writes"]) if write["operation"] == "event_append"]


@pytest.mark.parametrize("name, index, write", EVENT_APPENDS, ids=[f"{n}-{i}" for n, i, _ in EVENT_APPENDS])
def test_the_record_examples_event_appends_are_what_the_writer_does(tmp_path, name, index, write):
    """The examples' `writes` of event_append: a writer of each version against the marker's
    min_writer_version in PEP 440 order (rc, dev and post releases), and `interrupted`'s cut line 6
    closed by an LF before e6 on line 7."""
    root = case(tmp_path, name)
    request = write["request"]
    w = make(root, version=request["writer_version"])
    if write["result"] == "writer_too_old":
        before = tree(root)
        error = refused("writer_too_old", lambda: w.record(request["kind"], "appended"))
        assert error.details["kit_version"] == request["writer_version"]
        assert tree(root) == before
        return
    data = (root / "journal.jsonl").read_bytes()
    written = w.record(request["kind"], "appended")
    journal = relations.read_journal(root)
    assert journal.lines_of[written.event["id"]] == [journal.lines]
    assert journal.damaged == relations.Journal(data).damaged
    if name == "interrupted":
        assert (written.event["id"], journal.lines, journal.damaged) == ("e6", 7, [6])


# --- run and import records (design 4.8) -----------------------------------------------------------

RUN = {"id": "20261008-090000-a", "title": "a", "status": "ok", "started_at": "2026-10-08T09:00:00.000+09:00",
       "actor": "human", "actor_basis": "inferred", "means": "cli", "session": None, "rerun_of": None,
       "script": {"path": "scripts/a.py", "sha256": "0" * 64, "snapshot": "runs/20261008-090000-a/code/scripts/a.py"},
       "inputs": [], "outputs": [], "visualizations": []}


def test_a_run_record_and_its_completion_event(tmp_path):
    root = analytics_project(tmp_path / "p")
    w = make(root)
    run_dir = root / "runs" / RUN["id"]
    w.write_run(run_dir, RUN)
    value = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert list(value)[0] == "schema" and value["schema"] == "analytics.run/2"
    valid(value)
    [run] = core.read_model(core.Project(root)).runs()
    assert run["reasons"] == ["no_completion_event"]
    w.write_run(run_dir, {**value, "ended_at": "2026-10-08T09:00:01.000+09:00"})  # written whole again
    w.record("run", "a — ok", refs=[{"type": "run", "run_id": RUN["id"], "relation": "about", "basis": "observed"}])
    valid(last(root))
    [run] = core.read_model(core.Project(root)).runs()
    assert (run["execution_status"], run["recording_status"]) == ("ok", "complete")


@pytest.mark.parametrize("where, record", [
    ("elsewhere", RUN), ("runs/not-a-run-id", RUN), ("runs", {**RUN, "schema": "lab.run/1"}),
    ("runs", {**RUN, "means": "bash"})])
def test_a_run_record_the_writer_does_not_write(tmp_path, where, record):
    root = analytics_project(tmp_path / "p")
    run_dir = root / where / RUN["id"] if where != "runs/not-a-run-id" else root / where
    with pytest.raises(ValueError):
        make(root).write_run(run_dir, record)
    assert not (root / "runs").exists() and not (root / "elsewhere").exists()


def test_the_writer_writes_no_run_or_import_record_into_an_older_project(tmp_path):
    root = tmp_path / "lab"
    LegacyProject(root)
    before = tree(root)
    refused("migration_required", lambda: make(root).write_run(root / "runs" / RUN["id"], RUN))
    refused("migration_required", lambda: make(root).write_dataset(root / "data" / "imported" / "a.meta.json", {}))
    assert tree(root) == before


KILLED = """
import json, os, sys
from aelix_analytics import core, writer
root = sys.argv[1]
w = writer.Writer(root, platform=core.PLATFORM, origin=core.Origin("test", "sdk"), writer_version=core.KIT_VERSION)
w.write_run(os.path.join(root, "runs", sys.argv[2]), json.loads(sys.argv[3]))
os._exit(9)
"""


def test_a_run_killed_before_its_completion_event_stays_incomplete(tmp_path):
    root = analytics_project(tmp_path / "p")
    journal = (root / "journal.jsonl").read_bytes()
    done = subprocess.run([sys.executable, "-c", KILLED, str(root), RUN["id"], json.dumps(RUN)],
                          env=kit_env(), timeout=120)
    assert done.returncode == 9
    [run] = core.read_model(core.Project(root)).runs()
    assert (run["recording_status"], run["reasons"]) == ("incomplete", ["no_completion_event"])
    assert (root / "journal.jsonl").read_bytes() == journal


# A writer killed while it writes a journal line (design 8, the row "줄을 쓰다 중단", T13): the writer's
# os.write writes the first half of the line, and the process ends there, as a kill would.
CUT = """
import os as real_os, sys
from aelix_analytics import core, writer


class Cut:
    def __getattr__(self, name):
        return getattr(real_os, name)

    @staticmethod
    def write(fd, data):
        data = bytes(data)
        real_os.write(fd, data[:len(data) // 2])
        real_os._exit(9)


writer.os = Cut()
w = writer.Writer(sys.argv[1], platform=core.PLATFORM, origin=core.Origin("test", "sdk"),
                  writer_version=core.KIT_VERSION)
if sys.argv[2] == "record":
    w.record("finding", "cut while it was written", refs=[{"type": "event", "event_id": sys.argv[3],
                                                           "relation": "supersedes"}])
else:
    w.update(sys.argv[3], expected="current", new="withdrawn", text="withdrawn, cut")
"""


@pytest.mark.parametrize("call", ["record", "update"])
def test_a_writer_killed_while_it_writes_a_line_leaves_it_damaged_and_the_state_as_it_was(tmp_path, call):
    """T13 with a real end of the process mid-write: the cut line is damaged (no record is read from
    it), the target's state is what it was, and the next writer writes an LF first and its own line
    whole, with the next seq (a seq in a cut line is not counted, #49)."""
    root = analytics_project(tmp_path / "p")
    target = core.record(core.Project(root), "decision", "to withdraw", origin=ORIGIN).event
    before = (root / "journal.jsonl").read_bytes()
    done = subprocess.run([sys.executable, "-c", CUT, str(root), call, target["id"]], env=kit_env(),
                          timeout=120)
    assert done.returncode == 9
    cut = (root / "journal.jsonl").read_bytes()
    assert cut.startswith(before) and len(cut) > len(before) and not cut.endswith(b"\n")
    journal = relations.read_journal(root)
    assert journal.damaged == [journal.lines] and journal.recovered == []
    assert journal.replay().state_of(target["id"])["state"] == "current"
    assert journal.next_seq == target["seq"] + 1
    after = make(root, clock=fixed_clock).record("note", "after the cut")
    assert after.event["seq"] == target["seq"] + 1
    assert (root / "journal.jsonl").read_bytes() == cut + b"\n" + writer._encode(after.event) + b"\n"
    again = relations.read_journal(root)
    assert again.damaged == [journal.lines] and again.replay().state_of(target["id"])["state"] == "current"


def test_an_import_record_and_its_event(tmp_path):
    root = analytics_project(tmp_path / "p")
    data = b"PAR1sales"
    sha = hashlib.sha256(data).hexdigest()
    (root / "data" / "imported").mkdir(parents=True)
    (root / "data" / "imported" / "sales.parquet").write_bytes(data)
    meta = root / "data" / "imported" / "sales.meta.json"
    w = make(root)
    w.write_dataset(meta, {"name": "sales", "path": "data/imported/sales.parquet", "sha256": sha,
                           "bytes": len(data), "rows": 1, "columns": [], "description": None,
                           "imported_at": "2026-10-08T09:00:00.000+09:00",
                           "source": {"path": "/x/sales.csv", "sha256": sha, "bytes": 1, "format": "csv"}})
    value = json.loads(meta.read_text(encoding="utf-8"))
    assert list(value)[0] == "schema" and value["schema"] == "analytics.dataset/1"
    valid(value)
    # The record before its event: the reader shows the import as not complete (a kill here).
    [imported] = core.read_model(core.Project(root)).imports()
    assert imported["recording_status"] == "incomplete"
    w.record("import", "imported sales", refs=[{"type": "dataset_import", "path": "data/imported/sales.parquet",
                                                "digest": {"sha256": sha}, "relation": "about",
                                                "basis": "observed"}])
    valid(last(root))
    [imported] = core.read_model(core.Project(root)).imports()
    assert imported["recording_status"] == "complete"
    with pytest.raises(ValueError):
        w.write_dataset(root / "data" / "raw" / "sales.meta.json", value)  # the older layout's place
    with pytest.raises(ValueError):
        w.record("import", "no reference", refs=[])  # an import record's event names its record


# --- core.record, and what migration and init share with the writer (design 9, G1-16/17) -----------

def test_core_record_is_the_writer_s_and_refuses_where_the_writer_does_not_write(tmp_path):
    root = analytics_project(tmp_path / "p")
    written = core.record(core.Project(root), "finding", "via core", refs=["e1"], origin=ORIGIN)
    assert isinstance(written, core.Written) and written.event == last(root)
    LegacyProject(tmp_path / "lab").event("project")
    lab = core.Project(tmp_path / "lab")
    before = tree(lab.root)
    refused("migration_required", lambda: core.record(lab, "note", "x", origin=ORIGIN))
    assert tree(lab.root) == before


def test_core_gives_the_writer_a_migrated_project_s_reading_of_older_paths(tmp_path):
    source = LegacyProject(tmp_path / "src")
    source.file("data/raw/a.parquet", b"PAR1a")
    recorded = str(source.root.resolve() / "data" / "raw" / "a.parquet")
    source.run("20261001-091000-old", inputs=[(recorded, hashlib.sha256(b"PAR1a").hexdigest())],
               kit_version="0.4.0")
    dest = tmp_path / "dest"
    assert migration.migrate(source.root, dest, origin=ORIGIN)["ok"]
    p = core.Project(dest)
    mapping, _info = core.project_migration(p)
    w = core.writer_for(p, ORIGIN)
    assert w.legacy_path(recorded) == core.mapped_identity(p, mapping)(recorded) == "data/raw/a.parquet"
    assert (w.writer_version, w.root, w.origin) == (core.KIT_VERSION, p.root, ORIGIN)


def test_a_project_id_is_made_in_one_place(tmp_path, monkeypatch):
    made = writer.new_project_id()
    assert made.startswith("prj_") and len(made) == 20 and int(made[4:], 16) >= 0
    assert not list(Draft202012Validator(SCHEMA["$defs"]["opaque_id"]).iter_errors(made))
    monkeypatch.setattr(writer, "new_project_id", lambda: "prj_00000000000000ff")
    source = LegacyProject(tmp_path / "src")
    source.event("note", text="n")
    assert migration.migrate(source.root, tmp_path / "dest", origin=ORIGIN)["ok"]
    assert json.loads((tmp_path / "dest" / "analytics.json").read_text(encoding="utf-8"))["project_id"] == \
        "prj_00000000000000ff"


# --- append_line (design 4.4) ----------------------------------------------------------------------

@pytest.mark.parametrize("before, after", [
    (None, b"x\n"), (b"", b"x\n"), (b"a\n", b"a\nx\n"), (b"a", b"a\nx\n"), (b"a\r", b"a\r\nx\n"),
    (b"a\r\n", b"a\r\nx\n")])
def test_append_line_ends_a_cut_line_first_and_writes_the_line_and_its_lf_once(tmp_path, monkeypatch,
                                                                              before, after):
    path = tmp_path / "file.jsonl"
    if before is not None:
        path.write_bytes(before)
    writes = []
    real = os.write
    monkeypatch.setattr(writer.os, "write", lambda fd, data: writes.append(bytes(data)) or real(fd, data))
    writer.append_line(path, b"x")
    monkeypatch.undo()
    assert path.read_bytes() == after and len(writes) == 1
    with pytest.raises(ValueError):
        writer.append_line(path, b"two\nlines")
    assert path.read_bytes() == after


@pytest.mark.skipif(os.name == "nt", reason="a symbolic link needs a privilege on Windows")
def test_append_line_does_not_follow_a_link_when_told_not_to(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"t\n")
    (tmp_path / "link").symlink_to(target)
    with pytest.raises(OSError):
        writer.append_line(tmp_path / "link", b"x", follow=False)
    writer.append_line(tmp_path / "link", b"x")  # a journal that is a link is followed, as before
    assert target.read_bytes() == b"t\nx\n"


# --- what the reader gives the writer (relations) ------------------------------------------------

def test_the_replay_says_what_made_a_state_and_where_it_may_go():
    journal = relations.read_journal(FIXTURES / "normal")
    replay = journal.replay()
    assert replay.state_of("e9") == {"state": "withdrawn", "made_by": "e10", "corrected_by": ["e12"],
                                     "transitions": ["superseded"], "mismatched_updates": []}
    assert replay.state_of("e3") == {"state": "open", "made_by": "e13", "corrected_by": [],
                                     "transitions": [], "mismatched_updates": []}
    assert replay.state_of("e6")["state"] == "superseded" and replay.state_of("e6")["made_by"] == "e8"
    assert replay.state_of("e4") is None and replay.state_of("e99") is None
    assert journal.event_of("e9").kind == "decision" and journal.event_of("e99") is None
    mixed = relations.read_journal(FIXTURES / "mixed-version")
    assert mixed.event_of("e7") is None and mixed.event_of("journal:8").kind == "note"
