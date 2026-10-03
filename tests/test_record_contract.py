"""The record-contract examples of ADR-0006 hold together.

Each directory under tests/fixtures/records/ is a project as a reader would find it, with the judgments
a reader of the contract has to report in expected.json. This module is not that reader (Issue #12
builds it). It checks what holds without one: the schemas, every record against them, hashes and
request fingerprints against the bytes, and each judgment against the rule ADR-0006 states for it, so
an example cannot drift from the contract or from its own files unnoticed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "records"
CASES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
FAMILIES = {"analytics.project", "analytics.event", "analytics.report_revision", "analytics.run",
            "analytics.dataset", "lab.project", "lab.event", "lab.run", "lab.dataset"}
CONTRACT = {"analytics.project/2", "analytics.event/2", "analytics.report_revision/1"}
EXACT_V1 = {"runs", "events", "context", "resolves"}  # ADR-0006 4: read as exact references
DEAD_PID = 4294967295  # no process has it on any supported platform (README)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(intent: dict) -> str:
    """ADR-0006 8: sorted keys, no whitespace, UTF-8."""
    text = json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha(text.encode("utf-8"))


def validator(path: Path) -> Draft202012Validator:
    schema = load(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


RECORD = validator(ROOT / "docs" / "schemas" / "analytics-records.schema.json")
EXPECTED = validator(FIXTURES / "expected.schema.json")
DEFS = RECORD.schema["$defs"]
REF_TYPES = set(DEFS["reference"]["properties"]["type"]["enum"])
RELATIONS = set(DEFS["reference"]["properties"]["relation"]["enum"])
KINDS = set(DEFS["event_2"]["properties"]["kind"]["enum"])


def confirmed(binding: str, basis: str, state: str) -> bool:
    """ADR-0006 3: only an author binding with an observed or declared basis that resolves."""
    return binding == "author" and basis in ("observed", "declared") and state == "resolved"


def version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("+")[0].split("-")[0].split("."))


def unsupported_reason(value: dict) -> str | None:
    """Why a reader of this contract cannot read a record (ADR-0006 1), or None when it can."""
    family = value.get("schema", "")
    name = family.rpartition("/")[0]
    if name not in FAMILIES:
        return "unknown_family"
    if family not in CONTRACT | {"lab.event/1", "lab.run/1"}:
        return "unknown_version"
    if family == "analytics.event/2" and value.get("kind") not in KINDS:
        return "unknown_kind"
    return None


class Case:
    def __init__(self, name: str):
        self.name = name
        self.root = FIXTURES / name
        self.expected = load(self.root / "expected.json")
        self.marker = load(self.root / "analytics.json")
        self.raw = (self.root / "journal.jsonl").read_bytes()
        self.lines = self.raw.split(b"\n")
        if self.lines[-1] == b"":
            self.lines.pop()
        unsupported = {item["location"] for item in self.expected["unsupported"]["items"]}
        self.parsed: dict[int, dict] = {}
        self.events: dict[str, dict] = {}  # readable events by id, in journal order
        for number, line in enumerate(self.lines, 1):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            self.parsed[number] = value
            if f"journal:{number}" not in unsupported:
                self.events[value["id"]] = value
        self.runs = {}
        for path in sorted((self.root / "runs").glob("*/run.json")):
            if f"runs/{path.parent.name}/run.json" not in unsupported:
                self.runs[path.parent.name] = load(path)

    def file(self, rel: str) -> bytes | None:
        path = self.root / rel
        return path.read_bytes() if path.is_file() else None

    def refs_of(self, source: str):
        if source.startswith("report:"):
            report_id, revision_id = source.removeprefix("report:").split("/")
            manifest = load(self.root / "artifacts/reports" / report_id / revision_id / "manifest.json")
            return manifest["based_on"]["refs"], None
        event = self.events[source]
        return event["refs"], event

    def v2_refs(self, event: dict) -> list[dict]:
        return event["refs"] if event["schema"] == "analytics.event/2" else []

    def targets(self, relation: str, legacy_field: str | None = None) -> list[tuple[str, str]]:
        """(source id, target event id) for every readable reference of `relation`."""
        out = []
        for eid, event in self.events.items():
            if event["schema"] == "lab.event/1":
                target = (event.get("refs") or {}).get(legacy_field) if legacy_field else None
                if target:
                    out.append((eid, target))
            for ref in self.v2_refs(event):
                if ref.get("relation") == relation and ref.get("type") == "event":
                    out.append((eid, ref["event_id"]))
        return out

    def record_state(self, eid: str) -> str:
        """ADR-0006 6, computed from the journal without changing a line."""
        event = self.events[eid]
        if event["kind"] in ("question", "todo"):
            return "resolved" if any(t == eid for _, t in self.targets("resolves", "resolves")) else "open"
        state = "current"
        superseding = {source for source, target in self.targets("supersedes") if target == eid}
        if len(superseding) > 1:
            state = "conflict"
        elif superseding:
            state = "superseded"
        for event in self.events.values():
            if event.get("kind") != "record_update":
                continue
            [target] = [r for r in event["refs"] if r["relation"] == "updates"]
            if target["event_id"] == eid and event["details"]["expected_state"] == state:
                state = event["details"]["new_state"]
        return state

    def completion(self, kind: str, match) -> bool:
        for event in self.events.values():
            if event.get("kind") != kind:
                continue
            if event["schema"] == "lab.event/1":
                if match(None, event.get("refs") or {}):
                    return True
            elif any(r.get("relation") == "about" and match(r, None) for r in event["refs"]):
                return True
        return False

    def run_completed(self, run_id: str) -> bool:
        return self.completion("run", lambda r, legacy: (r or {}).get("run_id") == run_id
                               or run_id in (legacy or {}).get("runs", []))

    def revision_completed(self, report_id: str, revision_id: str) -> bool:
        return self.completion("report_revision", lambda r, _: r is not None
                               and r.get("type") == "report_revision"
                               and (r["report_id"], r["revision_id"]) == (report_id, revision_id))

    def head(self, report_id: str) -> str | None:
        head = None
        for event in self.events.values():
            if event.get("kind") == "report_revision":
                [ref] = [r for r in event["refs"] if r["type"] == "report_revision"]
                if ref["report_id"] == report_id:
                    head = ref["revision_id"]
        return head

    def manifests(self) -> dict[Path, dict]:
        """Saved revisions and prepared staging directories alike."""
        base = self.root / "artifacts" / "reports"
        paths = sorted(base.glob("*/*/manifest.json")) + sorted(base.glob("*/.staging/*/manifest.json"))
        return {path: load(path) for path in paths}

    def content_state(self, directory: Path, manifest: dict) -> str:
        states = []
        for item in manifest["files"]:
            data = self.file(str((directory / "content" / item["path"]).relative_to(self.root)))
            states.append("missing" if data is None else "resolved" if sha(data) == item["sha256"]
                          else "content_changed")
        for state in ("missing", "content_changed"):
            if state in states:
                return state
        return "resolved"

    def resolve(self, ref: dict) -> tuple[str, str | None]:
        """The resolution state of one bound reference (ADR-0006 4)."""
        kind = ref["type"]
        if kind == "event":
            return ("resolved", None) if ref["event_id"] in self.events else ("missing", "record_absent")
        if kind == "context_version":
            data = (self.file(f".analytics/context/{ref['version']}")
                    or self.file(f".lab/context/{ref['version']}"))
            return self.compare(data, ref)
        if kind == "report_revision":
            directory = self.root / "artifacts/reports" / ref["report_id"] / ref["revision_id"]
            data = self.file(str((directory / "manifest.json").relative_to(self.root)))
            if data is None:
                return "missing", "record_absent"
            if sha(data) != ref["digest"]["sha256"]:
                return "content_changed", None
            state = self.content_state(directory, json.loads(data))
            return state, "file_absent" if state == "missing" else None
        run = self.runs.get(ref["run_id"])
        if run is None:
            return "missing", "record_absent"
        if kind == "run":
            return "resolved", None
        if kind == "visualization_version":
            charts = [c for c in run["charts"] if c["id"] == ref["visualization_id"]]
            if not charts:
                return "missing", "record_absent"
            files = list(charts[0]["files"].values())
            if all(self.file(f) is not None for f in files):
                return "resolved", None
            return "missing", "file_absent"
        if kind == "data_observation":
            entries = run["inputs"] if ref["role"] == "input" else run["outputs"]
            entries = [e for e in entries if e["path"] == ref["path"]]
            if not entries:
                return "missing", "record_absent"
            if entries[0]["sha256"] is None:
                assert "digest" not in ref, "a digest the run never recorded"
                return "unknown", "no_digest"
            assert ref["digest"]["sha256"] == entries[0]["sha256"], "digest differs from the observation"
            return self.compare(self.file(ref["path"]), ref)
        if kind == "code_snapshot":
            return self.compare(self.file(ref["path"]), ref)
        pytest.fail(f"no rule in this check for reference type {kind}")

    @staticmethod
    def compare(data: bytes | None, ref: dict) -> tuple[str, str | None]:
        if data is None:
            return "missing", "file_absent"
        return ("resolved", None) if sha(data) == ref["digest"]["sha256"] else ("content_changed", None)

    def chart_candidates(self, key: str, before: str) -> list[str]:
        script, _, chart_id = key.rpartition("::")
        return sorted(f"{rid}/{chart_id}" for rid, run in self.runs.items()
                      if run.get("status") == "ok" and run["script"]["path"] == script
                      and run["started_at"] < before and any(c["id"] == chart_id for c in run["charts"]))


@pytest.fixture(params=CASES)
def case(request) -> Case:
    return Case(request.param)


def test_the_six_cases_are_present():
    assert CASES == ["conflict", "future-version", "interrupted", "missing", "mixed-version", "normal"]


def test_expected_judgments_follow_their_schema(case):
    EXPECTED.validate(case.expected)
    assert case.expected["case"] == case.name
    assert case.expected["unsupported"]["count"] == len(case.expected["unsupported"]["items"])


def test_records_follow_the_contract_unless_judged_unsupported(case):
    assert not list(RECORD.iter_errors(case.marker))
    reasons = {item["location"]: item["reason"] for item in case.expected["unsupported"]["items"]}
    dropped = {(r["from"], r["ref"].get("index")) for r in case.expected["references"]
               if r["state"] == "unsupported"}
    for number, value in case.parsed.items():
        assert unsupported_reason(value) == reasons.get(f"journal:{number}"), number
        if f"journal:{number}" in reasons:
            continue
        assert value["id"] == f"e{value['seq']}", number
        if value["schema"] == "analytics.event/2":
            refs = [r for i, r in enumerate(value["refs"]) if (value["id"], i) not in dropped]
            errors = [e.message for e in RECORD.iter_errors({**value, "refs": refs})]
            assert not errors, (number, errors)
    for path, manifest in case.manifests().items():
        assert not list(RECORD.iter_errors(manifest)), path
        assert manifest["entry"] in {f["path"] for f in manifest["files"]}
        intent = {k: manifest.get(k) for k in ("report_id", "previous_revision", "title", "entry", "based_on")}
        intent["files"] = [{"path": f["path"], "sha256": f["sha256"]} for f in manifest["files"]]
        assert fingerprint(intent) == manifest["request_fingerprint"], path
    for path in (case.root / "runs").glob("*/run.json"):
        location = f"runs/{path.parent.name}/run.json"
        assert unsupported_reason(load(path)) == reasons.get(location), location


def test_journal_lines_seq_and_damage(case):
    journal = case.expected["journal"]
    assert journal["lines"] == len(case.lines)
    unparsed = [n for n in range(1, len(case.lines) + 1) if n not in case.parsed]
    assert journal["damaged"] == unparsed
    seqs = [value["seq"] for value in case.parsed.values() if isinstance(value.get("seq"), int)]
    assert seqs == sorted(set(seqs)), "seq must increase line by line"
    # ADR-0006 6: the next seq follows every parsed line, unsupported ones included.
    assert journal["next_seq"] == max(seqs) + 1
    if unparsed:
        # A damaged line is the cut-off end of an interrupted append: no newline after it.
        assert unparsed == [len(case.lines)] and not case.raw.endswith(b"\n")


def test_reference_judgments_agree_with_the_case(case):
    for judged in case.expected["references"]:
        refs, event = case.refs_of(judged["from"])
        where = f"{judged['from']} {judged['ref']}"
        if "index" in judged["ref"]:
            stored = refs[judged["ref"]["index"]]
            stored_axes = (stored["version_binding"], stored["basis"])
            assert (judged["version_binding"], judged["basis"]) == stored_axes, where
            if judged["state"] == "unsupported":
                assert stored["type"] not in REF_TYPES or stored["relation"] not in RELATIONS, where
                state, reason = "unsupported", None
            elif stored["version_binding"] == "none":
                state, reason = judged["state"], judged.get("reason")
                assert state in ("version_unbound", "ambiguous", "unknown"), where
            else:
                state, reason = case.resolve(stored)
        else:
            field, value = judged["ref"]["field"], judged["ref"]["value"]
            held = refs.get(field)
            assert value == held or value in (held or []), where
            run_chart = field == "charts" and event["kind"] == "run"
            binding, basis = ("author", "observed") if run_chart else (
                ("author", "declared") if field in EXACT_V1 else ("none", "declared"))
            assert (judged["version_binding"], judged["basis"]) == (binding, basis), where
            if field == "runs":
                state, reason = case.resolve({"type": "run", "run_id": value})
            elif field in ("events", "resolves"):
                state, reason = case.resolve({"type": "event", "event_id": value})
            elif field == "context":
                data = case.file(f".lab/context/{value}") or case.file(f".analytics/context/{value}")
                state, reason = ("resolved", None) if data is not None else ("missing", "file_absent")
            elif run_chart:
                script, _, chart_id = value.rpartition("::")
                run_id = refs["runs"][0]
                assert case.runs[run_id]["script"]["path"] == script, where
                state, reason = case.resolve({"type": "visualization_version", "run_id": run_id,
                                              "visualization_id": chart_id})
            elif field == "other":
                state, reason = "unknown", "unresolved_name"
            else:
                # One logical key: never bound to the latest version (ADR-0006 4); versions that existed
                # when the event was written are only candidates.
                state, reason = "version_unbound", None
                if field == "charts":
                    assert judged["candidates"] == case.chart_candidates(value, event["ts"]), where
        assert (judged["state"], judged.get("reason")) == (state, reason), where
        rule = confirmed(judged["version_binding"], judged["basis"], judged["state"])
        assert judged["confirmed"] == rule, where


def test_record_states(case):
    for record in case.expected["records"]:
        assert case.record_state(record["id"]) == record["state"], record


def test_run_states(case):
    order = ("not_final", "no_completion_event", "finalize_errors")
    for expected in case.expected["runs"]:
        run = case.runs[expected["run_id"]]
        running = run["status"] == "running"
        if running:
            assert run.get("pid") in (None, DEAD_PID)
        reasons = {"not_final": running, "no_completion_event": not case.run_completed(run["id"]),
                   "finalize_errors": bool(run.get("finalize_errors"))}
        actual = [r for r in order if reasons[r]]
        assert expected["execution_status"] == ("incomplete" if running else run["status"]), expected
        assert expected["recording_status"] == ("incomplete" if actual else "complete"), expected
        assert expected["reasons"] == actual, expected


def test_report_revisions(case):
    judged = {(r["report_id"], r["request_id"]): r for r in case.expected["report_revisions"]}
    found = set()
    for path, manifest in case.manifests().items():
        key = (manifest["report_id"], manifest["request_id"])
        found.add(key)
        expected = judged[key]
        staging = path.parent.parent.name == ".staging"
        assert expected["revision_id"] == manifest["revision_id"]
        assert path.parent.name == (manifest["request_id"] if staging else manifest["revision_id"])
        recording = "prepared" if staging else (
            "complete" if case.revision_completed(*[manifest[k] for k in ("report_id", "revision_id")])
            else "incomplete")
        assert expected["recording_status"] == recording, key
        assert expected["content"] == case.content_state(path.parent, manifest), key
        assert expected["head"] == (case.head(manifest["report_id"]) == manifest["revision_id"]), key
    assert found == set(judged)


def test_lineage(case):
    for expected in case.expected["lineage"]:
        consumer = case.runs[expected["run_id"]]
        [entry] = [e for e in consumer["inputs"] if e["path"] == expected["path"]]
        candidates = sorted(rid for rid, run in case.runs.items()
                            if rid != consumer["id"] and run.get("status") == "ok"
                            and run["started_at"] < consumer["started_at"]
                            and any((o["path"], o["sha256"]) == (entry["path"], entry["sha256"])
                                    for o in run["outputs"]))
        producer = ("unknown" if entry["sha256"] is None else "none" if not candidates
                    else "resolved" if len(candidates) == 1 else "ambiguous")
        assert expected["producer"] == producer, expected
        assert expected["candidates"] == ([] if producer == "unknown" else candidates), expected


def test_writes(case):
    for write in case.expected["writes"]:
        request = write["request"]
        if write["operation"] == "report_save":
            intent = {k: request.get(k) for k in ("report_id", "previous_revision", "title", "entry", "files",
                                                  "based_on")}
            assert fingerprint(intent) == request["request_fingerprint"]
            same = [(p, m) for p, m in case.manifests().items()
                    if (m["report_id"], m["request_id"]) == (request["report_id"], request["request_id"])
                    and p.parent.parent.name != ".staging"]
            if same:
                [(path, manifest)] = same
                completed = case.revision_completed(manifest["report_id"], manifest["revision_id"])
                same_intent = manifest["request_fingerprint"] == request["request_fingerprint"]
                result = ("request_conflict" if not same_intent
                          else "existing" if completed else "resumed")
            else:
                result = ("saved" if request["previous_revision"] == case.head(request["report_id"])
                          else "revision_conflict")
        elif write["operation"] == "record_update":
            result = ("saved" if case.record_state(request["target"]) == request["expected_state"]
                      else "state_conflict")
        else:
            too_old = version(request["writer_version"]) < version(case.marker["min_writer_version"])
            result = "writer_too_old" if too_old else "saved"
        assert write["result"] == result, write


def test_journal_bytes_keep_their_line_endings():
    """ADR-0007 6: the converted lab.event/1 lines stay CRLF; the analytics.event/2 lines are LF."""
    mixed = Case("mixed-version")
    endings = [line.endswith(b"\r") for line in mixed.lines]
    families = [mixed.parsed[n]["schema"] for n in range(1, len(mixed.lines) + 1)]
    assert endings == [family == "lab.event/1" for family in families]
    assert True in endings and False in endings
    for name in CASES:
        if name != "mixed-version":
            assert b"\r" not in (FIXTURES / name / "journal.jsonl").read_bytes(), name
