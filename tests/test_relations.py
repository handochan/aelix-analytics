"""The reader of ADR-0006 (src/aelix_analytics/relations.py): the examples' judgments, and /1 records.

The expected judgments of the six examples under tests/fixtures/records/ are the reader's conformance
criteria (ADR-0006, "결과와 영향"); the request fingerprints of fingerprint-vectors.json are its
canonical form. The /1 cases are lab.json projects written record by record (tests/legacy_records.py):
the same chart id in other runs, references left unbound, several producers, tables not preserved,
references to later records and cycles, failed runs, records this reader cannot read and damaged
lines (docs/BETA-QUALITY-GATES.md, B1-b). Then the paths a kit before PATH_IDENTITY_SINCE recorded
(ADR-0006 9, Issue #12 PR-3): read as today's identity paths, inferred and never confirmed, and the
records never rewritten.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest
from conftest import LINK_KINDS, dir_link
from legacy_records import LegacyProject, at
from packaging.version import Version

from aelix_analytics import core, relations

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "records"
CASES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
DEAD_PID = 4294967295  # the examples' running record; no process has it (README)
LIVE_PID = 4242
# What a judgment may hold (no field of verification, causation or row correspondence).
JUDGMENT_KEYS = {"from", "line", "ref", "type", "relation", "target", "state", "reason", "version_binding",
                 "basis", "confirmed", "candidates", "inferred_candidates"}
# The writes of the examples a reader does not decide, and the Issue that does.
NOT_THIS_READER = {"report_save": "#19", "event_append": "#13"}
ANALYSIS = ("duckdb", "polars", "pyarrow", "numpy", "pandas", "matplotlib", "plotly", "pygments",
            "markdown_it")


def file_sha(path) -> str | None:
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def no_process(pid) -> bool:
    assert pid in (None, DEAD_PID), pid
    return False


def read(root, *, alive=no_process, sha=file_sha, legacy_path=None) -> relations.ReadModel:
    return relations.read(root, alive=alive, sha=sha, legacy_path=legacy_path)


def key_of(judged: dict) -> tuple[str, str]:
    return judged["from"], json.dumps(judged["ref"], ensure_ascii=False, sort_keys=True)


def one(model, source: str, **ref) -> dict:
    [judged] = [j for j in model.references(source) if all(j["ref"].get(k) == v for k, v in ref.items())]
    return judged


@pytest.fixture(params=CASES)
def case(request):
    expected = json.loads((FIXTURES / request.param / "expected.json").read_text(encoding="utf-8"))
    return read(FIXTURES / request.param), expected


# --- the examples ----------------------------------------------------------------------------------

def test_the_six_cases_are_read():
    assert CASES == ["conflict", "future-version", "interrupted", "missing", "mixed-version", "normal"]


def test_journal_lines_damage_and_next_seq(case):
    model, expected = case
    assert model.journal.summary() == expected["journal"]
    assert model.as_of_seq == expected["journal"]["next_seq"] - 1


def test_every_reference_gets_the_judgment_of_the_case(case):
    model, expected = case
    judged = {key_of(j): j for j in model.references()}
    want = {key_of(j): j for j in expected["references"]}
    assert len(judged) == len(model.references()), "one judgment per reference"
    assert set(judged) == set(want)
    for key, item in want.items():
        got = judged[key]
        for field in ("state", "version_binding", "basis", "confirmed"):
            assert got[field] == item[field], (key, field)
        assert got.get("reason") == item.get("reason"), key
        assert got.get("candidates") == item.get("candidates"), key


def test_no_reference_is_left_out(case):
    """Every stored reference of every readable event and saved manifest has its judgment."""
    model, _ = case
    stored = 0
    for event in model.journal.events:
        refs = event.refs or ({} if event.schema == relations.EVENT_V1 else [])
        stored += len(refs) if isinstance(refs, list) else sum(
            len(v) if isinstance(v, list) else 1 for v in refs.values())
    for path in sorted((model.root / "artifacts" / "reports").glob("*/*/manifest.json")):
        based_on = json.loads(path.read_text(encoding="utf-8")).get("based_on") or {}
        stored += len(based_on.get("refs") or [])
    assert len(model.references()) == stored


def test_confirmed_is_the_formula_and_a_judgment_holds_no_verdict(case):
    model, _ = case
    for judged in model.references():
        assert set(judged) <= JUDGMENT_KEYS, judged
        assert judged["confirmed"] == relations.confirmed(judged["version_binding"], judged["basis"],
                                                          judged["state"])
        if judged["basis"] in ("inferred", "unverified") or judged["version_binding"] != "author":
            assert judged["confirmed"] is False, judged


def test_record_states(case):
    model, expected = case
    states = model.record_states()
    for record in expected["records"]:
        assert states[record["id"]] == {"state": record["state"],
                                        "mismatched_updates": record.get("mismatched_updates", [])}, record


def test_runs_and_their_recording(case):
    model, expected = case
    assert sorted(model.runs(), key=lambda r: r["run_id"]) == sorted(expected["runs"], key=lambda r: r["run_id"])


def test_report_revisions(case):
    model, expected = case
    order = lambda r: (r["report_id"], r["revision_id"])  # noqa: E731
    assert sorted(model.report_revisions(), key=order) == sorted(expected["report_revisions"], key=order)


def test_lineage(case):
    """Producers by path and hash: every candidate, never the last run, and never a confirmed link."""
    model, expected = case
    order = lambda r: (r["run_id"], r["path"])  # noqa: E731
    lineage = sorted(model.lineage(), key=order)
    assert [{k: item[k] for k in ("run_id", "path", "producer", "candidates")} for item in lineage] == \
        sorted(expected["lineage"], key=order)
    for item in lineage:
        assert set(item) == {"run_id", "path", "sha256", "producer", "candidates", "basis"}
        assert item["basis"] == ("observed" if item["candidates"] else None)


def test_unsupported_records_are_counted_where_they_are(case):
    model, expected = case
    order = lambda i: i["location"]  # noqa: E731
    assert sorted(model.unsupported, key=order) == sorted(expected["unsupported"]["items"], key=order)
    assert len(model.unsupported) == expected["unsupported"]["count"]
    assert model.damaged_files == []


def test_the_writes_this_reader_decides(case):
    """record_update by the replay the writer reuses; saves' fingerprints by the reader's canonical
    form. The saves themselves and event_append are #19's and #13's."""
    model, expected = case
    for write in expected["writes"]:
        request = write["request"]
        if write["operation"] == "record_update":
            allowed = model.transition_allowed(request["target"], request["expected_state"],
                                               request["new_state"])
            assert write["result"] == ("saved" if allowed else "state_conflict"), write
        else:
            assert write["operation"] in NOT_THIS_READER, write
            if "request_fingerprint" in request:
                assert relations.request_fingerprint(request) == request["request_fingerprint"], write
    for path in (model.root / "artifacts" / "reports").glob("*/*/manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert relations.request_fingerprint(manifest) == manifest["request_fingerprint"], path


def test_the_examples_hold_no_write_left_unnamed():
    operations = set()
    for name in CASES:
        expected = json.loads((FIXTURES / name / "expected.json").read_text(encoding="utf-8"))
        operations |= {w["operation"] for w in expected["writes"]}
    assert operations == {"record_update", *NOT_THIS_READER}


def test_the_journal_alone_gives_the_same_events_and_states():
    """read_journal reads journal.jsonl and nothing else: what a caller that needs only the events
    (the context block, a note's checks) pays for."""
    model = read(FIXTURES / "mixed-version")
    journal = relations.read_journal(FIXTURES / "mixed-version")
    assert journal.summary() == model.journal.summary()
    assert [e.line for e in journal.events] == [e.line for e in model.journal.events]
    assert journal.replay().record_states() == model.record_states()
    assert [e.value["text"] for e in journal.events if e.id == "e7"] == ["끊긴 줄 뒤에 붙은 메모.",
                                                                        "같은 seq를 다시 받은 메모."]


def test_fingerprint_vectors():
    vectors = json.loads((FIXTURES / "fingerprint-vectors.json").read_text(encoding="utf-8"))["vectors"]
    for vector in vectors:
        assert relations.canonical(vector["intent"]).decode("utf-8") == vector["canonical"], vector["name"]
        assert relations.request_fingerprint(vector["intent"]) == vector["sha256"], vector["name"]


@pytest.mark.parametrize("change", [
    {"title": 1.5}, {"based_on": {"제목": 1}}, {"title": "\ud800"},
    {"files": [{"path": "a.md", "sha256": "0"}, {"path": "a.md", "sha256": "1"}]}])
def test_an_intent_without_a_canonical_form_has_no_fingerprint(change):
    intent = {"report_id": "r", "previous_revision": None, "title": "t", "entry": "a.md",
              "files": [{"path": "a.md", "sha256": "0"}], "based_on": None, **change}
    with pytest.raises(ValueError):
        relations.request_fingerprint(intent)


# --- /1 records ------------------------------------------------------------------------------------

def test_one_chart_id_in_other_runs_and_scripts_is_other_versions(tmp_path):
    p = LegacyProject(tmp_path / "p")
    first = p.run("20261001-091000-a", script="scripts/a.py", charts=["trend"])  # e1
    second = p.run("20261001-092000-a", script="scripts/a.py", charts=["trend"])  # e2
    other = p.run("20261001-093000-b", script="scripts/b.py", charts=["trend"])  # e3
    p.event("run", {"runs": [first], "charts": ["scripts/b.py::trend"]})  # e4: not first's script
    model = read(p.root)
    for source, run_id in (("e1", first), ("e2", second), ("e3", other)):
        judged = one(model, source, field="charts")
        assert judged["target"] == {"run_id": run_id, "visualization_id": "trend"}
        assert (judged["state"], judged["version_binding"], judged["basis"], judged["confirmed"]) == \
            ("resolved", "author", "observed", True)
    assert model.visualization_versions("scripts/a.py::trend") == [f"{first}/trend", f"{second}/trend"]
    assert model.visualization_versions("scripts/b.py::trend") == [f"{other}/trend"]
    elsewhere = one(model, "e4", field="charts")
    assert (elsewhere["state"], elsewhere["reason"], elsewhere["confirmed"]) == ("missing", "record_absent", False)


def test_unbound_references_list_only_the_versions_before_them(tmp_path):
    p = LegacyProject(tmp_path / "p")
    first = p.run("20261001-091000-a", charts=["trend"])  # e1
    p.event("finding", {"charts": ["scripts/a.py::trend"], "data": ["sales"], "other": ["지난주 메모"]})  # e2
    second = p.run("20261001-092000-a", charts=["trend"])  # e3, after the finding
    p.event("finding", {"charts": ["scripts/a.py::trend"]})  # e4
    model = read(p.root)
    chart = one(model, "e2", field="charts")
    assert (chart["state"], chart["version_binding"], chart["confirmed"]) == ("version_unbound", "none", False)
    assert chart["candidates"] == [f"{first}/trend"]  # not the latest version, which came after
    data = one(model, "e2", field="data")
    assert (data["state"], data["type"], "candidates" in data) == ("version_unbound", "data_observation", False)
    other = one(model, "e2", field="other")
    assert (other["state"], other["reason"]) == ("unknown", "unresolved_name")
    later = one(model, "e4", field="charts")
    # Both versions existed before e4; it stays unbound to either.
    assert (later["state"], later["candidates"]) == ("version_unbound", [f"{first}/trend", f"{second}/trend"])


def test_producers_are_every_successful_run_not_started_after_the_consumer(tmp_path):
    p = LegacyProject(tmp_path / "p")
    digest = p.file("data/derived/x.csv", b"x\n1\n")
    out = [("data/derived/x.csv", digest)]
    # The consumer starts at 01:00 UTC. Text order is not instant order across offsets.
    p.run("20261001-093000-earlier-kst", outputs=out, started_at="2026-10-01T09:30:00.000+09:00")
    p.run("20261001-005900-earlier-utc", outputs=out, started_at="2026-10-01T00:59:00.000+00:00")
    p.run("20261001-100000-same-instant", outputs=out, started_at="2026-10-01T10:00:00.000+09:00")
    p.run("20260930-233000-later", outputs=out, started_at="2026-09-30T23:30:00.000-05:00")
    p.run("20261001-000000-failed", outputs=out, status="error", started_at=at(0, hour=0, offset="+00:00"))
    p.run("20261001-000100-no-time", outputs=out, started_at="sometime")
    consumer = p.run("20261001-010000-consumer", inputs=out, started_at="2026-10-01T01:00:00.000+00:00")
    model = read(p.root)
    judged = model.producer(consumer, "data/derived/x.csv")
    assert judged == {"run_id": consumer, "path": "data/derived/x.csv", "sha256": digest,
                      "producer": "ambiguous", "basis": "observed",
                      "candidates": ["20261001-000100-no-time", "20261001-005900-earlier-utc",
                                     "20261001-093000-earlier-kst", "20261001-100000-same-instant"]}
    assert "20260930-233000-later" in model.producers("data/derived/x.csv", digest)
    assert "20261001-000000-failed" not in model.producers("data/derived/x.csv", digest)

    q = LegacyProject(tmp_path / "q")
    one_out = [("data/derived/y.csv", q.file("data/derived/y.csv", b"y"))]
    q.run("20261001-090000-maker", outputs=one_out, started_at=at(0))
    reader = q.run("20261001-091000-reader", inputs=one_out + [("data/raw.csv", "f" * 64)], started_at=at(10))
    model = read(q.root)
    assert model.producer(reader, "data/derived/y.csv")["producer"] == "resolved"
    assert model.producer(reader, "data/raw.csv")["producer"] == "none"


def test_tables_not_preserved(tmp_path):
    p = LegacyProject(tmp_path / "p")
    kept = p.file("data/derived/t.csv", b"v1")
    gone = p.file("data/derived/gone.csv", b"g")
    run = p.run("20261001-091000-t", outputs=[("data/derived/t.csv", kept), ("data/derived/gone.csv", gone)],
                inputs=[("data/in.csv", None)])
    p.file("data/derived/t.csv", b"v2")
    (p.root / "data" / "derived" / "gone.csv").unlink()
    model = read(p.root)
    assert model.observation(run, "output", "data/derived/t.csv") == {"state": "content_changed"}
    assert model.observation(run, "output", "data/derived/gone.csv") == {"state": "missing", "reason": "file_absent"}
    assert model.observation(run, "input", "data/in.csv") == {"state": "unknown", "reason": "no_digest"}
    assert model.producer(run, "data/in.csv")["producer"] == "unknown"


def test_references_to_later_records_are_missing_and_mutual_ones_end(tmp_path):
    """A /1 refs.events or refs.resolves target at or after the citing record's seq did not exist when
    the reference was written (ADR-0006 4 step 2, as this reader reads it)."""
    p = LegacyProject(tmp_path / "p")
    p.event("finding", {"events": ["e2"]})  # e1 names e2 before it exists
    p.event("finding", {"events": ["e1"]})  # e2
    p.event("note", {"events": ["e9"], "resolves": "e5"})  # e3: a mistyped id, a later question
    p.event("note")  # e4
    p.event("question")  # e5
    for _ in range(4):
        p.event("note")  # e6..e9: now there is an e9
    model = read(p.root)
    forward = one(model, "e1", field="events")
    assert (forward["state"], forward["reason"], forward["confirmed"]) == ("missing", "record_absent", False)
    back = one(model, "e2", field="events")
    assert (back["state"], back["confirmed"]) == ("resolved", True)
    for field in ("events", "resolves"):
        judged = one(model, "e3", field=field)
        assert (judged["state"], judged["reason"], judged["confirmed"]) == ("missing", "record_absent", False)
    assert model.record_states()["e5"]["state"] == "open"


def test_a_diamond_is_not_a_cycle_and_a_true_cycle_is(tmp_path):
    p = LegacyProject(tmp_path / "p")
    a = [("data/a.csv", p.file("data/a.csv", b"a"))]
    b = [("data/b.csv", p.file("data/b.csv", b"b"))]
    c = [("data/c.csv", p.file("data/c.csv", b"c"))]
    p.run("20261001-090000-root", outputs=a, started_at=at(0))
    p.run("20261001-090500-root-again", outputs=a, started_at=at(5))  # same bytes: two producers
    p.run("20261001-091000-left", inputs=a, outputs=b, started_at=at(10))
    p.run("20261001-092000-right", inputs=a, outputs=c, started_at=at(20))
    top = p.run("20261001-093000-top", inputs=b + c, started_at=at(30))
    model = read(p.root)
    assert model.upstream(top) == {"run_id": top, "cycle": False,
                                   "upstream": ["20261001-090000-root", "20261001-090500-root-again",
                                                "20261001-091000-left", "20261001-092000-right"]}
    assert model.producer("20261001-091000-left", "data/a.csv")["producer"] == "ambiguous"

    q = LegacyProject(tmp_path / "q")
    one_ = [("data/one.csv", q.file("data/one.csv", b"1"))]
    two = [("data/two.csv", q.file("data/two.csv", b"2"))]
    # Two runs that started at one instant and read each other's output: each is the other's candidate.
    q.run("20261001-100000-one", inputs=two, outputs=one_, started_at=at(0, hour=10))
    q.run("20261001-100000-two", inputs=one_, outputs=two, started_at=at(0, hour=10))
    model = read(q.root)
    assert model.upstream("20261001-100000-one") == {"run_id": "20261001-100000-one", "cycle": True,
                                                     "upstream": ["20261001-100000-two"]}


def test_failed_and_interrupted_runs(tmp_path):
    p = LegacyProject(tmp_path / "p")
    error = p.run("20261001-090000-error", status="error")
    timeout = p.run("20261001-090100-timeout", status="timeout")
    cancelled = p.run("20261001-090200-cancelled", status="cancelled")
    dead = p.run("20261001-090300-dead", status="running", pid=DEAD_PID, completion=False)
    live = p.run("20261001-090400-live", status="running", pid=LIVE_PID, completion=False)
    unfinished = p.run("20261001-090500-unfinished", finalize_errors=["journal: OSError"], completion=False)
    p.event("finding", {"runs": [error, dead]})
    model = read(p.root, alive=lambda pid: pid == LIVE_PID)
    assert {r["run_id"]: (r["execution_status"], r["recording_status"], r["reasons"]) for r in model.runs()} == {
        error: ("error", "complete", []), timeout: ("timeout", "complete", []),
        cancelled: ("cancelled", "complete", []),
        dead: ("incomplete", "incomplete", ["not_final", "no_completion_event"]),
        live: ("running", "incomplete", ["not_final", "no_completion_event"]),
        unfinished: ("ok", "incomplete", ["no_completion_event", "finalize_errors"])}
    assert [j["state"] for j in model.references("e4")] == ["resolved", "resolved"]


def test_records_this_reader_cannot_read_and_damaged_lines(tmp_path):
    p = LegacyProject(tmp_path / "p")
    p.event("note")  # line 1, e1
    p.line({"schema": "vendor.custom/1", "seq": 2, "id": "e2"})
    p.line({"schema": "lab.event/2", "seq": 3, "id": "e3", "kind": "finding", "refs": {}})
    p.line({"schema": "lab.event/1", "seq": 4, "id": "e4", "kind": "annotation", "refs": {}})
    p.seq = 4
    p.raw(b'{"schema": "lab.event/1", "seq": 5, "id": "e5", "kind": "note", "te')  # cut off
    p.event("note")  # line 5: e5, appended onto the cut-off line
    p.event("note", seq=5)  # line 6: e5 again, the next seq the /1 writer gave before #49
    p.event("finding", {"events": ["e5", "e3", "e2", "e4"]})  # line 7, e6
    p.run("20261001-090000-newer", schema="lab.run/2", completion=False)
    p.dataset("future", imported_at=at(1), schema="lab.dataset/2")
    damaged = p.root / "runs" / "20261001-090100-damaged"
    damaged.mkdir(parents=True)
    (damaged / "run.json").write_text("{not json", encoding="utf-8")
    (p.root / "runs" / "20261001-090200-empty").mkdir()
    p.event("finding", {"runs": ["20261001-090000-newer", "20261001-090100-damaged"]})  # line 8, e7
    p.raw(b'{"schema": "lab.event/1", "seq": 8, "id": "e8"')  # line 9, cut off at the end
    model = read(p.root)
    # The seq left in the cut-off piece is not counted: that write never returned an id.
    assert model.journal.summary() == {"lines": 9, "damaged": [5, 9], "recovered": [5], "next_seq": 8}
    assert model.unsupported == [
        {"location": "journal:2", "reason": "unknown_family"},
        {"location": "journal:3", "reason": "unknown_version"},
        {"location": "journal:4", "reason": "unknown_kind"},
        {"location": "runs/20261001-090000-newer/run.json", "reason": "unknown_version"},
        {"location": "data/raw/future.meta.json", "reason": "unknown_version"}]
    assert model.damaged_files == [{"location": "runs/20261001-090100-damaged/run.json", "reason": "unparsable"},
                                   {"location": "runs/20261001-090200-empty/run.json", "reason": "absent"}]
    states = [(j["ref"]["value"], j["state"], j.get("candidates")) for j in model.references("e6")]
    assert states == [("e5", "ambiguous", ["journal:5", "journal:6"]), ("e3", "unsupported", None),
                      ("e2", "unsupported", None), ("e4", "unsupported", None)]
    assert [j["state"] for j in model.references("e7")] == ["unsupported", "missing"]
    assert {j["from"] for j in model.references()} == {"e6", "e7"}  # the two e5 notes cite nothing
    assert [model.journal.source_of(e) for e in model.journal.events if e.id == "e5"] == ["journal:5", "journal:6"]


def test_hand_edited_records_with_odd_fields_are_read_not_raised(tmp_path):
    """A list or an object where an id belongs names nothing: the reader judges, it does not fail."""
    p = LegacyProject(tmp_path / "p")
    p.write_json("runs/20261001-090000-odd/run.json", {
        "schema": "lab.run/1", "id": "x", "status": "running", "pid": [1], "started_at": ["x"],
        "script": {"path": ["s"], "snapshot": {"a": 1}}, "modules": [{"snapshot": [1]}],
        "inputs": [{"path": ["a"], "sha256": ["b"]}, "not a dict"],
        "outputs": [{"path": {"a": 1}, "sha256": "f" * 64}], "charts": [{"id": {"a": 1}}, {"id": ["b"]}]})
    p.event("run", {"runs": ["20261001-090000-odd", ["nested"]], "charts": ["scripts/a.py::c"]})
    p.line({"schema": "lab.event/1", "seq": 2, "id": "e2", "kind": ["finding"], "refs": {}})
    p.line({"schema": "analytics.event/2", "seq": 3, "id": "e3", "kind": "finding", "recorded_at": at(3),
            "refs": [ref("run", "cites", run_id=["x"]), {"type": ["event"], "relation": "cites"},
                     ref("data_observation", "uses", run_id="20261001-090000-odd", role="input", path=["a"]),
                     ref("code_snapshot", "cites", run_id="20261001-090000-odd", path={"a": 1}),
                     ref("report_revision", "cites", report_id=["r"], revision_id="x"),
                     ref("visualization_version", "cites", "none", token=["t"]),
                     ref("visualization_version", "cites", "none", key=["k"]), "not a dict"]})
    p.line({"schema": "analytics.event/2", "seq": 4, "id": "e4", "kind": "record_update", "recorded_at": at(4),
            "refs": [ref("event", "updates", event_id=["e3"])], "details": ["x"]})
    # A script that is a string, not an object: its chart key, its snapshot and the chart index.
    p.write_json("runs/20261001-090100-str/run.json", {
        "schema": "lab.run/1", "id": "20261001-090100-str", "status": "ok", "started_at": at(1),
        "script": "scripts/a.py", "charts": [{"id": "c", "files": {}}]})
    p.line({"schema": "lab.event/1", "seq": 5, "id": "e5", "ts": at(5), "kind": "run",
            "refs": {"runs": ["20261001-090100-str"], "charts": ["scripts/a.py::c"]}})
    p.line({"schema": "analytics.event/2", "seq": 6, "id": "e6", "kind": "finding", "recorded_at": at(6),
            "refs": [ref("code_snapshot", "cites", run_id="20261001-090100-str", path="scripts/a.py",
                         digest={"sha256": "0" * 64}),
                     ref("visualization_version", "cites", "none", key="scripts/a.py::c")]})
    calls = []
    model = read(p.root, alive=lambda pid: calls.append(pid) or False)
    assert calls == [None]
    assert model.unsupported == [{"location": "journal:2", "reason": "unknown_kind"}]
    states = [j["state"] for j in model.references()]
    # e1's two runs and chart key, e3's eight items, e4's one, e5's run and chart key, e6's two
    assert len(states) == 3 + 8 + 1 + 2 + 2
    assert states[:2] == ["resolved", "unsupported"]
    assert [(j["state"], j.get("reason")) for j in model.references("e5")] == [
        ("resolved", None), ("missing", "record_absent")]  # no script path: not this key's run
    assert [(j["state"], j.get("reason"), j.get("candidates")) for j in model.references("e6")] == [
        ("missing", "record_absent", None), ("version_unbound", None, [])]
    assert model.runs()[0]["execution_status"] == "incomplete"
    assert model.lineage() == []
    assert model.upstream("20261001-090000-odd")["upstream"] == []
    assert model.journal.replay().unapplied == [{"from": "e4", "reason": "missing"}]
    model.report_revisions()
    model.imports()


def test_imports_and_their_completion_events(tmp_path):
    p = LegacyProject(tmp_path / "p")
    sales = p.dataset("sales", imported_at=at(10))
    p.event("import", {"data": [sales]}, ts=at(10))
    orders = p.dataset("orders", imported_at=at(20))
    p.event("import", {"data": [orders]}, ts=at(15))  # before the record: not its completion
    stores = p.dataset("stores", imported_at="2026-10-01T09:30:00.000+09:00")
    p.event("import", {"data": [stores]}, ts="2026-10-01T00:31:00.000+00:00")  # 09:31 KST
    model = read(p.root)
    assert {i["name"]: (i["recording_status"], i["reasons"]) for i in model.imports()} == {
        "sales": ("complete", []), "orders": ("incomplete", ["no_completion_event"]),
        "stores": ("complete", [])}
    # The data of an import event is the path of the import record it completes (ADR-0006 6), unbound.
    data = one(model, "e1", field="data")
    assert (data["type"], data["relation"], data["target"], data["state"], data["version_binding"],
            "candidates" in data) == ("dataset_import", "about", {"path": sales}, "version_unbound", "none", False)


def test_a_v1_reference_to_a_run_that_started_after_it_is_missing(tmp_path):
    """The run a /1 refs.runs names must not have started after the event, as instants. A time that
    cannot be read, or a naive one against an aware one, excludes nothing."""
    p = LegacyProject(tmp_path / "p")
    early = p.run("20261001-090000-early", started_at=at(0), ts=at(1))
    same = p.run("20261001-001000-same", started_at="2026-10-01T00:10:00.000+00:00", ts=at(11))  # 09:10 KST
    late = p.run("20261001-001100-late", started_at="2026-10-01T00:11:00.000+00:00", ts=at(12))  # 09:11 KST
    unread = p.run("20261001-095000-unread", started_at="sometime", ts=at(51))
    naive = p.run("20261001-095100-naive", started_at="2026-10-01T09:51:00", ts=at(52))
    finding = p.event("finding", {"runs": [early, same, late, unread, naive]}, ts=at(10))
    model = read(p.root)
    judged = {j["target"]["run_id"]: (j["state"], j.get("reason"), j["confirmed"])
              for j in model.references(finding)}
    assert judged == {early: ("resolved", None, True), same: ("resolved", None, True),
                      late: ("missing", "record_absent", False), unread: ("resolved", None, True),
                      naive: ("resolved", None, True)}


def test_a_run_event_naming_several_runs_binds_its_charts_to_none(tmp_path):
    """The runner names one run in its completion event. A line naming several does not say whose chart
    it is, in whatever order; one run that started after the event is not its run."""
    p = LegacyProject(tmp_path / "p")
    a = p.run("20261001-090000-a", charts=["trend"], started_at=at(0), ts=at(1))
    b = p.run("20261001-091000-b", charts=["trend"], started_at=at(10), ts=at(11))
    both = p.event("run", {"runs": [a, b], "charts": ["scripts/a.py::trend"]}, ts=at(20))
    reversed_ = p.event("run", {"runs": [b, a, b], "charts": ["scripts/a.py::trend"]}, ts=at(21))
    late = p.run("20261001-095000-late", charts=["trend"], started_at=at(50), ts=at(51))
    too_early = p.event("run", {"runs": [late], "charts": ["scripts/a.py::trend"]}, ts=at(30))
    model = read(p.root)
    for source in (both, reversed_):
        judged = one(model, source, field="charts")
        assert (judged["state"], judged["target"], judged["candidates"], judged["confirmed"]) == (
            "ambiguous", {"run_id": None, "visualization_id": "trend"}, [f"{a}/trend", f"{b}/trend"], False)
    for field in ("runs", "charts"):
        judged = one(model, too_early, field=field)
        assert (judged["state"], judged["reason"], judged["confirmed"]) == ("missing", "record_absent", False)
    assert one(model, "e5", field="charts")["confirmed"] is True  # late's own completion event


def test_an_id_on_several_lines_is_ambiguous_whatever_their_seqs_and_a_line_without_seq_is_not_found(tmp_path):
    p = LegacyProject(tmp_path / "p")
    p.event("note")  # line 1: e1
    p.event("finding", {"events": ["e1"]})  # line 2: e2
    p.line({"schema": "lab.event/1", "seq": 3, "id": "e1", "ts": at(3), "kind": "note", "refs": {}})  # hand-edited
    p.line({"schema": "lab.event/1", "id": "e9", "ts": at(4), "kind": "note", "refs": {}})  # line 4: no seq
    p.event("finding", {"events": ["e9"]}, seq=5)  # line 5
    model = read(p.root)
    duplicated = one(model, "e2", field="events")
    assert (duplicated["state"], duplicated["candidates"], duplicated["confirmed"]) == (
        "ambiguous", ["journal:1", "journal:3"], False)
    unplaced = one(model, "e5", field="events")
    assert (unplaced["state"], unplaced["reason"], unplaced["confirmed"]) == ("missing", "record_absent", False)


def test_a_list_of_resolves_closes_every_question_it_names(tmp_path):
    """references() and the replay read a list-valued /1 refs.resolves alike: every name in it."""
    p = LegacyProject(tmp_path / "p")
    p.event("question")  # e1
    p.event("todo")  # e2
    p.event("question")  # e3
    p.event("note", {"resolves": ["e1", "e2", ["e3"]]})  # e4: a hand-edited list
    model = read(p.root)
    assert [(j["state"], j["confirmed"]) for j in model.references("e4")] == [
        ("resolved", True), ("resolved", True), ("unsupported", False)]
    states = model.record_states()
    assert [states[e]["state"] for e in ("e1", "e2", "e3")] == ["resolved", "resolved", "open"]


def ref(type_, relation, binding="author", basis="declared", **fields) -> dict:
    return {"type": type_, **fields, "relation": relation, "basis": basis, "version_binding": binding}


def v2(p: LegacyProject, seq: int, kind: str, refs=(), details=None, id_: str | None = None) -> str:
    """Append an analytics.event/2 line as a test states it (no writer of /2 exists before #13)."""
    line = {"schema": "analytics.event/2", "seq": seq, "id": id_ or f"e{seq}", "recorded_at": at(seq % 60),
            "kind": kind, "actor": "test", "text": kind, "refs": list(refs)}
    if details is not None:
        line["details"] = details
    p.line(line)
    return line["id"]


# --- analytics.event/2 and Report manifest references ---------------------------------------------

def test_bound_event_references_find_only_targets_before_the_record(tmp_path):
    """The /1 reading of step 2 holds for /2 events (their seq) and manifests (as_of_seq + 1)."""
    p = LegacyProject(tmp_path / "p")
    v2(p, 1, "finding", [ref("event", "cites", event_id="e2")])  # names e2 before it exists
    v2(p, 2, "finding", [ref("event", "cites", event_id="e1"), ref("analysis_focus", "cites", event_id="e3")])
    v2(p, 3, "analysis_focus")
    p.write_json("artifacts/reports/weekly/R1/manifest.json", {
        "schema": "analytics.report_revision/1", "report_id": "weekly", "revision_id": "R1", "files": [],
        "based_on": {"as_of_seq": 1, "context_version": None,
                     "refs": [ref("event", "cites", event_id="e1"), ref("event", "cites", event_id="e2")]}})
    model = read(p.root)
    got = {(j["from"], j["ref"]["index"]): (j["state"], j.get("reason"), j["confirmed"]) for j in model.references()}
    assert got == {("e1", 0): ("missing", "record_absent", False), ("e2", 0): ("resolved", None, True),
                   ("e2", 1): ("missing", "record_absent", False),
                   ("report:weekly/R1", 0): ("resolved", None, True),
                   ("report:weekly/R1", 1): ("missing", "record_absent", False)}


def test_a_typed_event_reference_needs_a_record_of_that_type_and_logical_id(tmp_path):
    """ADR-0006 2: a metric definition by metric_id, a reuse proposal by proposal_id, an analysis focus
    by kind (it has no logical id)."""
    p = LegacyProject(tmp_path / "p")
    v2(p, 1, "metric_definition", details={"metric_id": "revenue"})
    v2(p, 2, "note")
    v2(p, 3, "reuse_proposal", details={"proposal_id": "p1"})
    v2(p, 4, "analysis_focus")
    v2(p, 5, "finding", [ref("metric_definition", "uses", metric_id="revenue", event_id="e1"),
                         ref("metric_definition", "uses", metric_id="cost", event_id="e1"),
                         ref("metric_definition", "uses", metric_id="revenue", event_id="e2"),
                         ref("reuse_proposal", "cites", proposal_id="p1", event_id="e3"),
                         ref("reuse_proposal", "cites", proposal_id="p2", event_id="e3"),
                         ref("analysis_focus", "about", event_id="e4"),
                         ref("analysis_focus", "about", event_id="e2"),
                         ref("event", "cites", event_id="e2")])
    model = read(p.root)
    assert [(j["state"], j.get("reason"), j["confirmed"]) for j in model.references("e5")] == [
        ("resolved", None, True), ("missing", "record_absent", False), ("missing", "record_absent", False),
        ("resolved", None, True), ("missing", "record_absent", False),
        ("resolved", None, True), ("missing", "record_absent", False), ("resolved", None, True)]


# --- the state replay ------------------------------------------------------------------------------

def test_replay_of_report_revisions_reuse_proposals_and_mismatched_updates(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    revision = ref("report_revision", "about", basis="observed", report_id="weekly", revision_id="R1",
                   digest={"sha256": "0" * 64})

    def update(target: dict, expected: str, new: str) -> tuple:
        return "record_update", [target], {"expected_state": expected, "new_state": new}

    report = ref("report_revision", "updates", report_id="weekly", revision_id="R1", digest={"sha256": "0" * 64})
    proposal = ref("reuse_proposal", "updates", proposal_id="p1", event_id="e5")
    rows = [("report_revision", [revision], None), update(report, "current", "withdrawn"),
            update(report, "withdrawn", "current"), update(report, "withdrawn", "current"),
            ("reuse_proposal", [], {"proposal_id": "p1"}), update(proposal, "proposed", "trial"),
            update(proposal, "trial", "registered"), update(proposal, "trial", "validated"),
            update(proposal, "validated", "deferred"), update(proposal, "deferred", "proposed"),
            ("finding", [], None), update(ref("event", "updates", event_id="e11"), "current", "superseded"),
            update(ref("event", "updates", event_id="e99"), "current", "withdrawn")]
    with open(root / "journal.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for seq, (kind, refs, details) in enumerate(rows, 1):
            line = {"schema": "analytics.event/2", "seq": seq, "id": f"e{seq}", "recorded_at": at(seq),
                    "kind": kind, "actor": "test", "text": kind, "refs": refs}
            if details:
                line["details"] = details
            fh.write(json.dumps(line) + "\n")
    model = read(root)
    states = model.record_states()
    assert states["report:weekly/R1"] == {"state": "current", "mismatched_updates": ["e4"]}
    assert states["e5"] == {"state": "proposed", "mismatched_updates": ["e7"]}
    assert states["e11"] == {"state": "current", "mismatched_updates": ["e12"]}
    assert model.journal.replay().unapplied == [{"from": "e13", "reason": "missing"}]
    assert model.transition_allowed("report:weekly/R1", "current", "withdrawn")
    assert model.transition_allowed("e5", "proposed", "trial")
    assert not model.transition_allowed("e5", "proposed", "registered")
    assert not model.transition_allowed("e99", "current", "withdrawn")


def test_records_that_share_an_id_each_keep_a_state_and_references_to_the_id_change_none(tmp_path):
    """ADR-0006 6: every record with the id is read; a reference to the id is ambiguous and changes no
    state. Each record's state is under `journal:<line>`."""
    p = LegacyProject(tmp_path / "p")
    p.event("note")  # line 1: e1
    p.raw(b'{"schema": "lab.event/1", "seq": 2, "id": "e2", "kind": "question", "te')  # cut off
    p.event("question", text="first question")  # e2, appended onto the cut-off line 2
    p.event("question", seq=2, text="second question")  # line 3: e2 again, as the /1 writer did before #49
    p.event("finding", {"events": ["e2"], "resolves": "e2"})  # line 4: e3
    model = read(p.root)
    states = model.record_states()
    assert states["journal:2"] == states["journal:3"] == {"state": "open", "mismatched_updates": []}
    assert "e2" not in states
    assert [(j["state"], j["candidates"]) for j in model.references("e3")] == [
        ("ambiguous", ["journal:2", "journal:3"])] * 2
    mixed = read(FIXTURES / "mixed-version").record_states()
    assert mixed["journal:7"] == mixed["journal:8"] == {"state": "current", "mismatched_updates": []}


def test_the_replay_changes_a_state_only_through_a_reference_that_resolves_before_the_event(tmp_path):
    """The references a transition goes through are judged as references() judges them: bound, one
    record of the type named, before the event, never the event itself."""
    p = LegacyProject(tmp_path / "p")
    p.event("question", {"resolves": "e1"})  # e1 closes itself
    model = read(p.root)
    assert one(model, "e1", field="resolves")["state"] == "missing"
    assert model.record_states()["e1"]["state"] == "open"

    q = LegacyProject(tmp_path / "q")
    withdraw = {"expected_state": "current", "new_state": "withdrawn"}
    v2(q, 1, "finding")
    v2(q, 2, "record_update", [ref("event", "updates", "none", event_id="e1", key="e1")], withdraw)
    v2(q, 3, "metric_definition", details={"metric_id": "revenue"})
    v2(q, 4, "record_update", [ref("metric_definition", "updates", metric_id="cost", event_id="e3")], withdraw)
    v2(q, 5, "note")
    v2(q, 6, "record_update", [ref("metric_definition", "updates", metric_id="revenue", event_id="e5")], withdraw)
    v2(q, 7, "record_update", [ref("event", "updates", event_id="e8")], withdraw)  # a later record
    v2(q, 8, "finding")
    v2(q, 9, "record_update", [ref("event", "updates", event_id="e1"), ref("event", "updates", event_id="e5")],
       withdraw)
    v2(q, 10, "record_update", [ref("event", "updates", event_id="e9")], withdraw)  # a record_update: no state
    v2(q, 11, "question", [ref("event", "resolves", event_id="e11")])  # closes itself
    v2(q, 12, "record_update", [ref("metric_definition", "updates", metric_id="revenue", event_id="e3")],
       withdraw)
    model = read(q.root)
    states = model.record_states()
    assert {key: states[key]["state"] for key in ("e1", "e3", "e5", "e8", "e11")} == {
        "e1": "current", "e3": "withdrawn", "e5": "current", "e8": "current", "e11": "open"}
    assert model.journal.replay().unapplied == [
        {"from": "e2", "reason": "version_unbound"}, {"from": "e4", "reason": "missing"},
        {"from": "e6", "reason": "missing"}, {"from": "e7", "reason": "missing"},
        {"from": "e9", "reason": "not_one_target"}, {"from": "e10", "reason": "no_state"}]
    assert [one(model, source, index=0)["state"] for source in ("e2", "e4", "e6", "e7", "e11")] == [
        "version_unbound", "missing", "missing", "missing", "missing"]


def test_two_corrections_that_share_an_id_are_two_corrections(tmp_path):
    p = LegacyProject(tmp_path / "p")
    v2(p, 1, "finding")
    v2(p, 2, "finding", [ref("event", "supersedes", event_id="e1")])
    v2(p, 2, "finding", [ref("event", "supersedes", event_id="e1")])  # another line with e2 and its seq
    states = read(p.root).record_states()
    assert states["e1"]["state"] == "conflict"
    assert states["journal:2"]["state"] == states["journal:3"]["state"] == "current"


# --- names that leave the project ------------------------------------------------------------------

def test_names_that_leave_the_project_name_nothing_and_are_not_hashed(tmp_path):
    """A manifest file path is relative to its content directory (no root, drive or `..`); a CONTEXT
    version is a bare file name on every platform (`C:x.md` is drive-relative on Windows)."""
    p = LegacyProject(tmp_path / "p")
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"outside")
    digest = hashlib.sha256(b"outside").hexdigest()
    paths = {"R1": "../../../../../../outside.md", "R2": str(outside), "R3": "index.md"}
    for revision, path in paths.items():
        p.file(f"artifacts/reports/weekly/{revision}/content/index.md", b"outside")
        p.write_json(f"artifacts/reports/weekly/{revision}/manifest.json", {
            "schema": "analytics.report_revision/1", "report_id": "weekly", "revision_id": revision,
            "files": [{"path": path, "sha256": digest}]})
    good = "20261001-090000-000000_aaaaaaaaaaaa.md"
    p.file(f".analytics/context/{good}", b"context")
    if os.name != "nt":
        p.file(".analytics/context/C:x.md", b"context")  # a file name here, a drive-relative path on Windows
    p.event("context", {"context": good})  # e1
    p.event("context", {"context": "C:x.md"})  # e2
    calls: list = []
    model = read(p.root, sha=counting(calls))
    assert {r["revision_id"]: r["content"] for r in model.report_revisions()} == {
        "R1": "missing", "R2": "missing", "R3": "resolved"}
    assert [one(model, e, field="context")["state"] for e in ("e1", "e2")] == ["resolved", "missing"]
    root = p.root.resolve()
    assert calls and all(Path(c).resolve().is_relative_to(root) for c in calls), calls


# --- costs -----------------------------------------------------------------------------------------

def test_the_import_index_and_the_producer_edges_are_built_once_per_read(tmp_path):
    p = LegacyProject(tmp_path / "p")
    imports = [p.dataset(f"d{i}", imported_at=at(i)) for i in range(3)]
    v2(p, 1, "finding", [ref("dataset_import", "uses", path=path, digest={"sha256": file_sha(p.root / path)})
                         for path in imports * 3])
    p.seq = 1
    chain, previous = [], []
    for i, name in enumerate("abcd"):
        output = [(f"data/{name}.csv", p.file(f"data/{name}.csv", name.encode()))]
        chain.append(p.run(f"20261001-09{i}000-{name}", inputs=previous, outputs=output, started_at=at(10 * i)))
        previous = output
    model = read(p.root)

    class Counting(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    model._metas = Counting(model._metas)
    assert [j["state"] for j in model.references("e1")] == ["resolved"] * 9
    assert model._metas.iterations == 1
    calls: list = []
    producers = model.producers
    model.producers = lambda path, sha256, consumer=None: calls.append((path, consumer)) or producers(
        path, sha256, consumer)
    assert [model.upstream(run)["upstream"] for run in chain] == [[], chain[:1], chain[:2], chain[:3]]
    assert len(calls) == len(set(calls)) == 3, calls


# --- the source revision ---------------------------------------------------------------------------

def test_the_source_revision_follows_the_files_and_not_where_they_are(tmp_path):
    first = tmp_path / "a" / "normal"
    shutil.copytree(FIXTURES / "normal", first)
    moved = tmp_path / "elsewhere" / "b"
    shutil.copytree(first, moved)  # copy2 keeps sizes and mtimes
    token = read(first).source_revision
    assert len(token) == 16 and read(first).source_revision == token
    assert read(moved).source_revision == token
    assert str(tmp_path) not in json.dumps(relations._snapshot(first))
    run_json = first / "runs" / "20261001-091000-region-summary" / "run.json"
    data = run_json.read_bytes()
    replacement = run_json.with_name("run.json.tmp")
    replacement.write_bytes(data)
    stat = run_json.stat()
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    os.replace(replacement, run_json)
    replaced = read(first).source_revision
    assert replaced != token
    with open(first / "journal.jsonl", "ab") as fh:
        fh.write(b'{"schema": "analytics.event/2", "seq": 16, "id": "e16", "recorded_at": "2026-10-01T11:00:00.000+09:00", '
                 b'"kind": "note", "actor": "test", "text": "x", "refs": []}\n')
    appended = read(first)
    assert appended.source_revision not in (token, replaced) and appended.as_of_seq == 16


def test_a_read_that_keeps_changing_says_updating(tmp_path):
    p = LegacyProject(tmp_path / "p")
    p.run("20261001-090000-live", status="running", pid=LIVE_PID, completion=False)
    calls = []

    def appending(pid) -> bool:
        calls.append(pid)
        p.event("note")  # a writer appends while the reader reads
        return True

    model = read(p.root, alive=appending)
    assert model.updating and len(calls) == 3
    assert len(set(model.observed_revisions)) == 3 and model.source_revision == model.observed_revisions[-1]
    steady = read(p.root, alive=lambda pid: True)
    assert not steady.updating and steady.observed_revisions == [steady.source_revision]


# --- what is injected ------------------------------------------------------------------------------

def counting(calls: list):
    def sha_(path):
        calls.append(path)
        return file_sha(path)
    return sha_


def test_reading_lab_references_hashes_no_file(tmp_path):
    p = LegacyProject(tmp_path / "p")
    out = [("data/derived/x.csv", p.file("data/derived/x.csv", b"x"))]
    run = p.run("20261001-090000-a", charts=["trend"], outputs=out)
    p.event("context", {"context": "20261001-090000-000000_aaaaaaaaaaaa.md"})
    p.event("finding", {"runs": [run], "events": ["e1"], "charts": ["scripts/a.py::trend"],
                        "data": ["data/derived/x.csv"], "other": ["memo"], "resolves": "e1"})
    calls: list = []
    model = read(p.root, sha=counting(calls))
    model.references()
    model.runs()
    model.record_states()
    model.lineage()
    assert calls == []


@pytest.mark.parametrize("name", ["normal", "missing"])
def test_another_sha_gives_the_same_judgments(name):
    calls: list = []
    plain = read(FIXTURES / name, sha=counting(calls))
    judged, revisions = plain.references(), plain.report_revisions()
    assert calls, "these cases hold references whose targets keep a hash"

    def chunked(path):
        path = Path(path)
        if not path.is_file():
            return None
        with open(path, "rb") as fh:
            return hashlib.file_digest(fh, "sha256").hexdigest()

    other = read(FIXTURES / name, sha=chunked)
    assert other.references() == judged and other.report_revisions() == revisions


# --- paths an older kit recorded (ADR-0006 9, Issue #12 PR-3) -------------------------------------

OLD, NEW = "0.4.0", relations.PATH_IDENTITY_SINCE


def files_of(root: Path) -> dict[str, str | None]:
    """Every file under `root` with the sha256 of its bytes, but the kit's hash cache."""
    return {path.relative_to(root).as_posix(): file_sha(path) for path in sorted(root.rglob("*"))
            if path.is_file() and ".lab/cache" not in path.relative_to(root).as_posix()}


def test_kit_versions_are_ordered_as_pep_440_says():
    """ADR-0006 1.3's form, ordered as packaging orders it; anything else is no version, and a run
    with none is an older kit's."""
    texts = ["0.3.0.dev1", "0.3.0a1.dev2", "0.3.0a1", "0.3.0b2", "0.3.0rc1", "0.3", "0.3.0", "0.3.0.0",
             "0.3.0.post1.dev1", "0.3.0.post1", "0.3.1.dev1", "0.4.0", "0.4.10", "0.5.0rc1", "0.5.0",
             "0.5.0.post2", "0.10.0", "1", "1.0", "10.0.0a3"]
    for first in texts:
        for second in texts:
            want = (Version(first) > Version(second)) - (Version(first) < Version(second))
            got = relations.version_key(first), relations.version_key(second)
            assert (got[0] > got[1]) - (got[0] < got[1]) == want, (first, second)
    for text in ("0.3.0-beta.1", "v0.3.0", "0.3.0+local", "1!0.3.0", "0.3.0.beta1", "03.0", "", None, 5,
                 "0.5.0 ", "dev"):
        assert relations.version_key(text) is None, text
    # A number longer than Python reads as an integer (sys.get_int_max_str_digits) is no version
    # either: an older kit's, never an error that stops the read.
    huge = "9" * 5000
    for text in (f"0.5.{huge}", huge, f"0.5.0rc{huge}", f"0.5.0.post{huge}", f"0.5.0.dev{huge}"):
        assert relations.version_key(text) is None and relations.older_kit(text), text[:12]
    assert relations.PATH_IDENTITY_SINCE == core.PATH_IDENTITY_SINCE
    assert [v for v in ("0.4.9", "0.5.0rc1", "0.5.0.dev3", None, "dev", "0.5.0", "0.5.0.post1", "0.5.1",
                        "0.10.0", "1.0") if relations.older_kit(v)] == \
        ["0.4.9", "0.5.0rc1", "0.5.0.dev3", None, "dev"]


def older_kit_project(tmp_path) -> tuple[LegacyProject, dict]:
    """An older kit's runs whose input paths are not the identity path of the table they read,
    today's run with the same path, and paths that are read as nothing. The mapping stands for
    Project.legacy_identity (a real file system's answers are in the last test of this part)."""
    p = LegacyProject(tmp_path / "p")
    agg = ("data/derived/agg.parquet", p.file("data/derived/agg.parquet", b"agg"))
    absolute = "/an/older/place/data/derived/agg.parquet"
    elsewhere = "/today/s/place/data/derived/agg.parquet"
    runs = {
        "maker": p.run("20261001-091000-maker", started_at=at(10), ts=at(11), outputs=[agg],
                       kit_version=OLD),
        "absolute": p.run("20261001-092000-absolute", script="scripts/b.py", started_at=at(20),
                          ts=at(21), inputs=[(absolute, agg[1])], kit_version=OLD),
        "today": p.run("20261001-093000-today", script="scripts/c.py", started_at=at(30), ts=at(31),
                       inputs=[(elsewhere, agg[1])], kit_version=NEW),
        "spelling": p.run("20261001-094000-spelling", script="scripts/d.py", started_at=at(40),
                          ts=at(41), inputs=[("data/derived/AGG.parquet", agg[1])]),  # no env
        "nowhere": p.run("20261001-095000-nowhere", script="scripts/e.py", started_at=at(50),
                         ts=at(51), inputs=[("/elsewhere/x.parquet", "0" * 64),
                                            ("data/derived/made-up.parquet", "1" * 64)],
                         kit_version="0.3.0"),
    }
    mapping = {absolute: agg[0], "data/derived/AGG.parquet": agg[0], elsewhere: agg[0],
               # A mapping that names no entry and no run's output is not taken: never invented.
               "data/derived/made-up.parquet": "data/derived/never-written.parquet"}
    return p, {"runs": runs, "mapping": mapping, "agg": agg, "absolute": absolute, "elsewhere": elsewhere}


def test_an_older_kit_s_path_is_read_as_its_identity_and_the_producer_is_inferred(tmp_path):
    p, case = older_kit_project(tmp_path)
    runs, (path, digest) = case["runs"], case["agg"]
    before = files_of(p.root)
    model = read(p.root, legacy_path=case["mapping"].get)
    judged = model.producer(runs["absolute"], case["absolute"])
    assert judged == {"run_id": runs["absolute"], "path": case["absolute"], "sha256": digest,
                      "producer": "resolved", "candidates": [runs["maker"]], "basis": "inferred",
                      "inferred_candidates": [runs["maker"]], "read_as": path}
    spelled = model.producer(runs["spelling"], "data/derived/AGG.parquet")
    assert (spelled["basis"], spelled["candidates"], spelled["read_as"]) == ("inferred", [runs["maker"]], path)
    # Today's run is read as it recorded, whatever the mapping says.
    today = model.producer(runs["today"], case["elsewhere"])
    assert (today["producer"], today["basis"], "read_as" in today) == ("none", None, False)
    assert model.read_as(runs["today"], case["elsewhere"]) is None
    # A path that maps to nothing, or to a path nothing knows of, is read as recorded.
    for recorded in ("/elsewhere/x.parquet", "data/derived/made-up.parquet"):
        assert model.read_as(runs["nowhere"], recorded) is None
        assert model.producer(runs["nowhere"], recorded)["producer"] == "none"
    # Lineage and the upstream walk follow the inferred edges; nothing in them is observed or
    # confirmed through a mapping.
    assert model.upstream(runs["absolute"])["upstream"] == [runs["maker"]]
    inferred = [item for item in model.lineage() if item.get("inferred_candidates")]
    assert len(inferred) == 2 and all(item["basis"] == "inferred" for item in inferred), inferred
    assert all(not j["confirmed"] or j["basis"] in ("observed", "declared") for j in model.references())
    # The table's producer read from its own path: the maker recorded that path, so it is observed.
    assert model.producer_candidates(path, digest) == ([runs["maker"]], [])
    assert files_of(p.root) == before, "reading rewrites no record"
    # Without the mapping the paths are read as recorded, as before this reader read them.
    plain = read(p.root)
    assert plain.producer(runs["absolute"], case["absolute"]) == {
        "run_id": runs["absolute"], "path": case["absolute"], "sha256": digest, "producer": "none",
        "candidates": [], "basis": None}


def test_an_inferred_reading_that_finds_several_runs_stays_ambiguous(tmp_path):
    p = LegacyProject(tmp_path / "p")
    two = ("data/derived/two.parquet", p.file("data/derived/two.parquet", b"two"))
    old_maker = p.run("20261001-091000-old-maker", started_at=at(10), ts=at(11), outputs=[two],
                      kit_version=OLD)
    new_maker = p.run("20261001-092000-new-maker", started_at=at(20), ts=at(21), outputs=[two],
                      kit_version=NEW)
    spelled_maker = p.run("20261001-093000-spelled-maker", started_at=at(30), ts=at(31),
                          outputs=[("data/derived/TWO.parquet", two[1])], kit_version="0.2.0")
    consumer = p.run("20261001-094000-consumer", script="scripts/b.py", started_at=at(40), ts=at(41),
                     inputs=[("/old/root/data/derived/two.parquet", two[1])], kit_version=OLD)
    mapping = {"/old/root/data/derived/two.parquet": two[0], "data/derived/TWO.parquet": two[0]}
    model = read(p.root, legacy_path=mapping.get)
    judged = model.producer(consumer, "/old/root/data/derived/two.parquet")
    makers = sorted([old_maker, new_maker, spelled_maker])
    assert (judged["producer"], judged["candidates"], judged["inferred_candidates"], judged["basis"]) == \
        ("ambiguous", makers, makers, "inferred")
    # Read from the table's own path, only the maker that recorded another spelling is inferred.
    assert model.producer_candidates(two[0], two[1]) == (makers, [spelled_maker])


def test_an_older_kit_s_script_spelling_is_a_version_of_today_s_key_by_inference(tmp_path):
    p = LegacyProject(tmp_path / "p")
    p.file("scripts/plot.py", b"# plot\n")
    old = p.run("20261001-091000-old", script="Scripts/Plot.py", started_at=at(10), ts=at(11),
                charts=["trend"])  # e1, no env: an older kit's
    new = p.run("20261001-092000-new", script="scripts/plot.py", started_at=at(20), ts=at(21),
                charts=["trend"], kit_version=NEW)  # e2
    p.event("finding", {"charts": ["scripts/plot.py::trend"]})  # e3
    p.event("finding", {"charts": ["Scripts/Plot.py::trend"]})  # e4
    v2(p, 5, "finding", [ref("visualization_version", "cites", "none", token="trend")])
    mapping = {"Scripts/Plot.py": "scripts/plot.py"}
    model = read(p.root, legacy_path=mapping.get)
    versions = [f"{old}/trend", f"{new}/trend"]
    assert model.chart_key("Scripts/Plot.py::trend") == "scripts/plot.py::trend"
    assert model.visualization_versions("scripts/plot.py::trend") == versions
    assert model.inferred_versions("scripts/plot.py::trend") == [f"{old}/trend"]
    assert model.visualization_versions("Scripts/Plot.py::trend") == versions
    assert model.inferred_versions("Scripts/Plot.py::trend") == [f"{new}/trend"]
    today_key = one(model, "e3", field="charts")
    assert (today_key["state"], today_key["candidates"], today_key["inferred_candidates"],
            today_key["confirmed"]) == ("version_unbound", versions, [f"{old}/trend"], False)
    old_key = one(model, "e4", field="charts")
    assert (old_key["candidates"], old_key["inferred_candidates"]) == (versions, [f"{new}/trend"])
    [token] = model.references("e5")
    assert (token["state"], token["candidates"], token["inferred_candidates"], token["confirmed"]) == \
        ("version_unbound", versions, [f"{old}/trend"], False)
    # The old run's own chart reference is bound to that run as recorded: no inference in it.
    bound = one(model, "e1", field="charts")
    assert (bound["state"], bound["confirmed"], "inferred_candidates" in bound) == ("resolved", True, False)
    # Read as recorded, the two spellings are two keys, and the name is ambiguous between them.
    plain = read(p.root)
    assert plain.visualization_versions("scripts/plot.py::trend") == [f"{new}/trend"]
    [token] = plain.references("e5")
    assert (token["state"], "inferred_candidates" in token) == ("ambiguous", False)


def test_older_kit_paths_read_through_the_file_system_and_today_s_links(tmp_path):
    """Project.legacy_identity on a real file system (core.read_model passes it): an absolute path
    under the root, one under a directory link's target, and a relative path in another case or
    Unicode normalization where the file system takes it as the stored entry — and read as recorded
    where it does not, rather than matched by folding the text."""
    for kind in LINK_KINDS:
        p = LegacyProject(tmp_path / kind / "p")
        agg = ("data/derived/agg.parquet", p.file("data/derived/agg.parquet", b"agg"))
        nfc = unicodedata.normalize("NFC", "data/derived/café.parquet")
        cafe = (nfc, p.file(nfc, b"cafe"))
        ext = tmp_path / kind / "ext"
        ext.mkdir()
        (ext / "x.parquet").write_bytes(b"x")
        x_sha = file_sha(ext / "x.parquet")
        dir_link(kind, p.root / "data" / "linked", ext)
        project = core.Project(p.root)
        makers = [p.run("20261001-090000-maker", started_at=at(1), ts=at(2), kit_version=OLD,
                        outputs=[agg, cafe, ("data/linked/x.parquet", x_sha)])]
        under_root = os.path.join(str(project.root), "data", "derived", "agg.parquet")
        through_link = os.path.join(os.path.realpath(ext), "x.parquet")
        other_case = "data/derived/AGG.parquet"
        nfd = unicodedata.normalize("NFD", nfc)
        consumer = p.run("20261001-091000-consumer", script="scripts/b.py", started_at=at(10),
                         ts=at(11), kit_version=OLD,
                         inputs=[(under_root, agg[1]), (through_link, x_sha), (other_case, agg[1]),
                                 (nfd, cafe[1])])
        # Today's run read the same tables by texts no older run recorded (an older run's text
        # that a run of today's kit also recorded is the next test's case).
        (ext / "y.parquet").write_bytes(b"x")
        today_link = os.path.join(os.path.realpath(ext), "y.parquet")
        today_case = "data/derived/Agg.parquet"
        today = p.run("20261001-092000-today", script="scripts/c.py", started_at=at(20), ts=at(21),
                      kit_version=NEW, inputs=[(today_link, x_sha), (today_case, agg[1])])
        before = files_of(p.root)
        model = core.read_model(project)
        folds_case = core.same_file(p.root / agg[0], p.root / other_case)
        folds_form = core.same_file(p.root / nfc, p.root / nfd)
        expected = {under_root: agg[0], through_link: "data/linked/x.parquet",
                    other_case: agg[0] if folds_case else None, nfd: nfc if folds_form else None}
        for recorded, identity in expected.items():
            assert model.read_as(consumer, recorded) == identity, (kind, recorded)
            judged = model.producer(consumer, recorded)
            if identity is None:
                assert (judged["producer"], "read_as" in judged) == ("none", False), (kind, recorded)
            else:
                assert (judged["candidates"], judged["basis"], judged["read_as"]) == \
                    (makers, "inferred", identity), (kind, recorded)
        for recorded in (today_link, today_case):
            assert model.read_as(today, recorded) is None
            assert model.producer(today, recorded)["producer"] == "none"
        assert files_of(p.root) == before, "reading rewrites no record"


def test_a_path_text_a_run_of_today_s_kit_also_recorded_is_read_as_recorded_for_every_run(tmp_path):
    """An older kit's run and a run of PATH_IDENTITY_SINCE or later that recorded the same path text
    match by that text, as observed, even when what the text names changed since (a case-only
    rename, a link made later): the older run's text is not read as another, so neither run loses
    the other, and the Visualization key both recorded keeps both versions (reviews of 926adb0)."""
    p = LegacyProject(tmp_path / "p")
    # Since today's run: the table and the script renamed (case only), the folder outside linked.
    agg = p.file("data/derived/Agg.parquet", b"agg")
    x = p.file("data/ext/x.parquet", b"x")
    p.file("scripts/Plot.py", b"# plot\n")
    outside = "/outside/ext/x.parquet"
    base = ("data/raw/base.parquet", p.file("data/raw/base.parquet", b"base"))
    old = p.run("20261001-091000-old", script="scripts/plot.py", started_at=at(10), ts=at(11),
                inputs=[base], outputs=[("data/derived/agg.parquet", agg), (outside, x)], charts=["trend"],
                kit_version=OLD)  # e1
    new = p.run("20261001-092000-new", script="scripts/plot.py", started_at=at(20), ts=at(21),
                inputs=[("data/derived/agg.parquet", agg), (outside, x)], charts=["trend"],
                kit_version=NEW)  # e2
    p.event("finding", {"charts": ["scripts/plot.py::trend"]})  # e3
    mapping = {"data/derived/agg.parquet": "data/derived/Agg.parquet", outside: "data/ext/x.parquet",
               "scripts/plot.py": "scripts/Plot.py"}
    model = read(p.root, legacy_path=mapping.get)
    for recorded, digest in (("data/derived/agg.parquet", agg), (outside, x)):
        assert model.read_as(old, recorded) is None, recorded
        assert model.producer(new, recorded) == {
            "run_id": new, "path": recorded, "sha256": digest, "producer": "resolved", "candidates": [old],
            "basis": "observed"}, recorded
    assert model.upstream(new)["upstream"] == [old]
    versions = [f"{old}/trend", f"{new}/trend"]
    assert model.chart_key("scripts/plot.py::trend") == "scripts/plot.py::trend"
    assert model.visualization_versions("scripts/plot.py::trend") == versions
    assert model.inferred_versions("scripts/plot.py::trend") == []
    cited = one(model, "e3", field="charts")
    assert (cited["candidates"], cited.get("inferred_candidates")) == (versions, None)
    # As every run read as recorded, which is how a reader without the mapping reads them.
    plain = read(p.root)
    assert [plain.producer(new, path) for path in ("data/derived/agg.parquet", outside)] == \
        [model.producer(new, path) for path in ("data/derived/agg.parquet", outside)]
    assert plain.visualization_versions("scripts/plot.py::trend") == versions


def test_today_s_kit_s_text_keeps_its_producer_after_a_rename_or_a_new_link(tmp_path):
    """The same on a real file system through core (the reviews' cases of 926adb0): a 0.4.0 run
    output a table and a file outside the root, and a 0.5.0 run read both by the same texts; then
    the table and the script are renamed (case only) and the folder outside is linked into data/.
    The 0.5.0 run's producer is still the older run, observed; the older run's changed input still
    makes the 0.5.0 run's chart stale (a stale chart is not shown current); the script both runs
    recorded keeps one gallery key with both versions."""
    for kind in LINK_KINDS:
        p = LegacyProject(tmp_path / kind / "p")
        ext = tmp_path / kind / "ext"
        ext.mkdir()
        (ext / "x.parquet").write_bytes(b"x")
        x_sha = file_sha(ext / "x.parquet")
        outside = os.path.join(os.path.realpath(ext), "x.parquet")
        agg = p.file("data/derived/agg.parquet", b"agg")
        p.file("scripts/plot.py", b"# plot\n")
        base = ("data/raw/base.parquet", p.file("data/raw/base.parquet", b"base"))
        old = p.run("20261001-091000-old", script="scripts/plot.py", started_at=at(10), ts=at(11),
                    inputs=[base], outputs=[("data/derived/agg.parquet", agg), (outside, x_sha)],
                    charts=["trend"], kit_version=OLD)
        new = p.run("20261001-092000-new", script="scripts/plot.py", started_at=at(20), ts=at(21),
                    inputs=[("data/derived/agg.parquet", agg), (outside, x_sha)], charts=["trend"],
                    kit_version=NEW)
        p.file(base[0], b"base changed")  # the older run's input changed: it is stale
        for folder, name, renamed in (("data/derived", "agg.parquet", "Agg.parquet"),
                                      ("scripts", "plot.py", "Plot.py")):
            os.rename(p.root / folder / name, p.root / folder / "renaming")
            os.rename(p.root / folder / "renaming", p.root / folder / renamed)
        dir_link(kind, p.root / "data" / "ext", ext)
        before = files_of(p.root)
        project = core.Project(p.root)
        model = core.read_model(project)
        for recorded in ("data/derived/agg.parquet", outside):
            judged = model.producer(new, recorded)
            assert (judged["producer"], judged["candidates"], judged["basis"], "read_as" in judged) == \
                ("resolved", [old], "observed", False), (kind, recorded)
        st = core.status(project)
        [chart] = [c for c in st["charts"] if c["run"] == new]
        assert (chart["key"], chart["versions"], chart["inferred_versions"], chart["stale"],
                chart["stale_upstream"], chart["inferred_upstream"], chart["inferred_inputs"]) == \
            ("scripts/plot.py::trend", 2, 0, True, [old], [], []), kind
        assert model.chart_key("scripts/plot.py::trend") == "scripts/plot.py::trend"
        assert files_of(p.root) == before, "reading rewrites no record"


def test_a_kit_version_too_long_to_read_is_an_older_kit_s_and_the_read_goes_on(tmp_path):
    """A run record whose kit version has a number longer than Python reads as an integer is read
    as an older kit's, and the project is read (Codex's review of 926adb0)."""
    p = LegacyProject(tmp_path / "p")
    t = ("data/derived/t.parquet", p.file("data/derived/t.parquet", b"t"))
    run = p.run("20261001-091000-a", started_at=at(10), ts=at(11), outputs=[t],
                kit_version="0.5." + "9" * 5000)
    project = core.Project(p.root)
    assert core.read_model(project).older_kit(run)
    assert [r["id"] for r in core.status(project)["runs"]] == [run]


# --- where it runs ---------------------------------------------------------------------------------

def test_the_module_imports_the_standard_library_only_in_3_11_syntax():
    source = (ROOT / "src" / "aelix_analytics" / "relations.py").read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 11))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no other module of the package"
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            assert top == "__future__" or top in sys.stdlib_module_names, name


def test_the_reader_runs_alone_from_its_file(tmp_path):
    """ADR-0001 check 1: the read model's representative call with no host and no analysis library,
    in an environment where the analysis libraries could be imported."""
    code = f"""
import hashlib, importlib.util, json, sys
spec = importlib.util.spec_from_file_location("relations_alone", {str(ROOT / "src" / "aelix_analytics" / "relations.py")!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def sha(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None
model = module.read({str(FIXTURES / "mixed-version")!r}, alive=lambda pid: False, sha=sha)
[judged] = [j for j in model.references("e9") if j["ref"] == {{"index": 4}}]
analysis = {ANALYSIS!r}
print(json.dumps({{"state": judged["state"], "candidates": judged["candidates"],
                  "importable": [n for n in analysis if importlib.util.find_spec(n) is not None],
                  "loaded": sorted(m for m in sys.modules
                                   if m.split(".")[0] in analysis + ("labkit", "aelix_analytics", "aelix"))}}))
"""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                          cwd=tmp_path, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["state"] == "ambiguous" and len(result["candidates"]) == 3
    assert "polars" in result["importable"], result
    assert result["loaded"] == []
