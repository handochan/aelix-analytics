"""The record-contract examples of ADR-0006 hold together.

Each directory under tests/fixtures/records/ is a project as a reader would find it, with the judgments
a reader of the contract has to report in expected.json. This module is not that reader
(src/aelix_analytics/relations.py is, checked against the same judgments by tests/test_relations.py). It
computes what the rules of ADR-0006 give for each case — the schemas, every record
against them, hashes and request fingerprints against the bytes, reference states in the order section 4
gives, the state replay of section 6 for findings and the like and for questions and to-dos (the cases
hold no Report revision or reuse proposal transition) and the Report saves and retries of section 7 —
and compares that with the judgments, so an example cannot drift from the contract or from its own
files unnoticed.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "records"
CASES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
CONTRACT = {"analytics.project/2", "analytics.event/2", "analytics.report_revision/1"}
# ADR-0006 1.4: the family versions a reader of this contract knows. analytics.run and analytics.dataset
# are names #13 gives its next run and dataset families, so a version of them is an unknown version.
KNOWN = CONTRACT | {"lab.project/1", "lab.run/1", "lab.event/1", "lab.dataset/1"}
FAMILIES = {name.rpartition("/")[0] for name in KNOWN} | {"analytics.run", "analytics.dataset"}
EXACT_V1 = {"runs", "events", "context", "resolves"}  # ADR-0006 4: read as exact references
CONTENT_KINDS = {"finding", "decision", "assumption", "note", "workflow_feedback", "metric_definition",
                 "analysis_focus"}  # ADR-0006 6: current, superseded, withdrawn, conflict
QUESTION_KINDS = {"question", "todo"}  # ADR-0006 6: open, resolved
DEAD_PID = 4294967295  # no process has it on any supported platform (README)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(intent: dict) -> bytes:
    """ADR-0006 8: the RFC 8785 form of a save intent. Its keys are ASCII and its numbers integers, so
    that is sorted keys, no whitespace, only the escapes JSON requires and UTF-8; files go by path."""

    def plain(value) -> None:
        assert not isinstance(value, float), "a save intent holds no floating-point number"
        if isinstance(value, dict):
            for key, item in value.items():
                assert key.isascii(), key
                plain(item)
        elif isinstance(value, list):
            for item in value:
                plain(item)
        elif isinstance(value, str):
            value.encode("utf-8")  # a lone surrogate has no UTF-8 form

    data = dict(intent)
    data["files"] = sorted(intent["files"], key=lambda f: f["path"])  # str order is code point order
    plain(data)
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fingerprint(intent: dict) -> str:
    return sha(canonical(intent))


def intent_of(value: dict) -> dict:
    """The fingerprinted part of a save request or a manifest (ADR-0006 8)."""
    out = {k: value.get(k) for k in ("report_id", "previous_revision", "title", "entry", "based_on")}
    out["files"] = [{"path": f["path"], "sha256": f["sha256"]} for f in value["files"]]
    return out


def validator(path: Path) -> Draft202012Validator:
    schema = load(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


RECORD = validator(ROOT / "docs" / "schemas" / "analytics-records.schema.json")
EXPECTED = validator(FIXTURES / "expected.schema.json")
DEFS = RECORD.schema["$defs"]
REF = DEFS["reference"]["properties"]
REF_TYPES, RELATIONS = set(REF["type"]["enum"]), set(REF["relation"]["enum"])
BASES, BINDINGS = set(REF["basis"]["enum"]), set(REF["version_binding"]["enum"])
KINDS = set(DEFS["event_2"]["properties"]["kind"]["enum"])
PATTERN = {name: re.compile(DEFS[name]["pattern"])
           for name in ("content_path", "report_id", "request_id", "kit_version")}


def confirmed(binding: str, basis: str, state: str) -> bool:
    """ADR-0006 3: only an author binding with an observed or declared basis that resolves."""
    return binding == "author" and basis in ("observed", "declared") and state == "resolved"


def exact(type_: str, **fields) -> dict:
    """A /1 exact reference in the shape ADR-0006 4 reads it as."""
    return {"type": type_, **fields, "relation": "cites", "basis": "declared", "version_binding": "author"}


def unsupported_reason(value: dict) -> str | None:
    """Why a reader of this contract cannot read a record (ADR-0006 1.4), or None when it can."""
    family = value.get("schema", "")
    name = family.rpartition("/")[0]
    if name not in FAMILIES:
        return "unknown_family"
    if family not in KNOWN:
        return "unknown_version"
    if family == "analytics.event/2" and value.get("kind") not in KINDS:
        return "unknown_kind"
    return None


def recover(line: bytes) -> dict | None:
    """ADR-0006 6: the record a /1 writer appended onto a cut-off line — the first `{"schema"` after
    the start of the line from which the rest of the line is one JSON object."""
    start = line.find(b'{"schema"', 1)
    while start != -1:
        try:
            value = json.loads(line[start:])
        except ValueError:
            start = line.find(b'{"schema"', start + 1)
            continue
        return value if isinstance(value, dict) else None
    return None


def portable_paths(paths: list[str]) -> bool:
    """ADR-0006 7: Report paths are portable POSIX paths in NFC, and no two are one file where case is
    ignored."""
    for path in paths:
        if not PATTERN["content_path"].fullmatch(path) or not unicodedata.is_normalized("NFC", path):
            return False
    folded = [path.casefold() for path in paths]
    return len(set(folded)) == len(folded)


def refuse(request: dict) -> str | None:
    """What the kit refuses before it prepares a save (ADR-0006 7 and 8), or None."""
    if not (PATTERN["report_id"].fullmatch(request["report_id"])
            and PATTERN["request_id"].fullmatch(request["request_id"])):
        return "name_not_portable"
    paths = [f["path"] for f in request["files"]]
    if not portable_paths(paths) or request["entry"] not in paths:
        return "report_path_invalid"
    return None


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


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
        self.unsupported = {item["location"]: item["reason"] for item in self.expected["unsupported"]["items"]}
        self.damaged: list[int] = []
        self.recovered: list[int] = []
        self.parsed: dict[int, dict] = {}  # every record by line, recovered and unsupported ones included
        for number, line in enumerate(self.lines, 1):
            try:
                value = json.loads(line)
            except ValueError:
                self.damaged.append(number)
                value = recover(line)
                if value is None:
                    continue
                self.recovered.append(number)
            self.parsed[number] = value
        self.events = [(n, v) for n, v in self.parsed.items() if f"journal:{n}" not in self.unsupported]
        self.lines_of: dict[str, list[int]] = {}
        for number, value in self.parsed.items():
            self.lines_of.setdefault(value["id"], []).append(number)
        self.runs, self.unsupported_runs = {}, set()
        for path in sorted((self.root / "runs").glob("*/run.json")):
            if f"runs/{path.parent.name}/run.json" in self.unsupported:
                self.unsupported_runs.add(path.parent.name)
            else:
                self.runs[path.parent.name] = load(path)

    def file(self, rel: str) -> bytes | None:
        path = self.root / rel
        return path.read_bytes() if path.is_file() else None

    def context_version(self, name: str) -> bytes | None:
        """ADR-0006 4: a CONTEXT version by file name, in .analytics/context/ and then .lab/context/."""
        return self.file(f".analytics/context/{name}") or self.file(f".lab/context/{name}")

    def event(self, eid: str) -> dict:
        [number] = self.lines_of[eid]
        return self.parsed[number]

    def refs_of(self, source: str) -> tuple[list, dict | None, int]:
        """The references of a source, its event, and the seq its candidates have to come before."""
        if source.startswith("report:"):
            report_id, revision_id = source.removeprefix("report:").split("/")
            manifest = load(self.root / "artifacts/reports" / report_id / revision_id / "manifest.json")
            as_of = manifest["based_on"]["as_of_seq"]
            if as_of is None:  # ADR-0006 3: then before the revision's completion event
                [seq] = [e["seq"] for _, e in self.events if e.get("kind") == "report_revision"
                         and any(r.get("revision_id") == revision_id for r in e["refs"])]
                return manifest["based_on"]["refs"], None, seq
            return manifest["based_on"]["refs"], None, as_of + 1
        event = self.event(source)
        return event["refs"], event, event["seq"]

    # --- runs and visualization versions ------------------------------------------------------------

    def completion_seq(self, run_id: str) -> int | None:
        """The seq of a run's completion event (ADR-0006 6), or None."""
        for _, event in self.events:
            if event.get("kind") != "run":
                continue
            if event["schema"] == "lab.event/1":
                if run_id in (event.get("refs") or {}).get("runs", []):
                    return event["seq"]
            elif any(r.get("type") == "run" and r.get("relation") == "about" and r.get("run_id") == run_id
                     for r in event["refs"]):
                return event["seq"]
        return None

    def versions(self, chart_id: str, before: int, script: str | None = None) -> list[tuple[str, str]]:
        """(script, `<run_id>/<visualization_id>`) of the versions of a visualization name that existed
        before a record: successful runs whose completion event comes earlier (ADR-0006 3 and 4)."""
        out = []
        for run_id, run in self.runs.items():
            done = self.completion_seq(run_id)
            if run.get("status") != "ok" or done is None or done >= before:
                continue
            if script is not None and run["script"]["path"] != script:
                continue
            if any(chart["id"] == chart_id for chart in run["charts"]):
                out.append((run["script"]["path"], f"{run_id}/{chart_id}"))
        return out

    def unbound(self, ref: dict, before: int) -> tuple[str, str | None, list | None]:
        """ADR-0006 3: a reference left unbound stays unbound when it is read."""
        assert ref["type"] == "visualization_version", f"no rule in this check for unbound {ref['type']}"
        if "key" in ref:
            script, _, chart_id = ref["key"].rpartition("::")
            return "version_unbound", None, sorted(v for _, v in self.versions(chart_id, before, script))
        found = self.versions(ref["token"], before)
        keys = {script for script, _ in found}
        if not keys:
            return "unknown", "unresolved_name", None
        return ("ambiguous" if len(keys) > 1 else "version_unbound"), None, sorted(v for _, v in found)

    def check_inferred(self, ref: dict, before: int) -> None:
        """ADR-0006 3: a name binds to the latest successful version when the record is written, and
        only when one script has that name."""
        assert ref["type"] == "visualization_version", f"no rule in this check for inferred {ref['type']}"
        found = self.versions(ref["token"], before)
        assert len({script for script, _ in found}) == 1, "a name in no script or in two stays unbound"
        latest = max(found, key=lambda item: self.completion_seq(item[1].split("/")[0]))[1]
        assert latest == f"{ref['run_id']}/{ref['visualization_id']}", (latest, ref)

    # --- reference states ---------------------------------------------------------------------------

    def resolve(self, ref: dict) -> tuple[str, str | None, list | None]:
        """(state, reason, candidates) of one bound reference, in the order of ADR-0006 4."""
        kind = ref["type"]
        if kind in ("event", "analysis_focus", "metric_definition", "reuse_proposal"):
            lines = self.lines_of.get(ref["event_id"], [])
            if not lines:
                return "missing", "record_absent", None
            if len(lines) > 1:
                return "ambiguous", None, [f"journal:{n}" for n in lines]
            if f"journal:{lines[0]}" in self.unsupported:
                return "unsupported", None, None
            return "resolved", None, None
        if kind == "context_version":
            data = self.context_version(ref["version"])
            if data is None:
                return "missing", "record_absent", None
            return self.compare(data, ref["digest"]["sha256"])
        if kind == "report_revision":
            directory = self.root / "artifacts/reports" / ref["report_id"] / ref["revision_id"]
            data = self.file(str((directory / "manifest.json").relative_to(self.root)))
            if data is None:
                return "missing", "record_absent", None
            if sha(data) != ref["digest"]["sha256"]:
                return "content_changed", None, None
            state = self.content_state(directory, json.loads(data))
            return state, "file_absent" if state == "missing" else None, None
        run_id = ref["run_id"]
        if run_id not in self.runs:
            if run_id in self.unsupported_runs:
                return "unsupported", None, None
            return "missing", "record_absent", None
        run = self.runs[run_id]
        if kind == "run":
            return "resolved", None, None
        if kind == "visualization_version":
            charts = [c for c in run["charts"] if c["id"] == ref["visualization_id"]]
            if not charts:
                return "missing", "record_absent", None
            if "digest" in ref:
                # A lab.run/1 chart record holds no hash, so this digest is one the record never held.
                return "missing", "digest_mismatch", None
            files = list(charts[0]["files"].values())
            if all(self.file(f) is not None for f in files):
                return "resolved", None, None
            return "missing", "file_absent", None
        if kind == "data_observation":
            entries = run["inputs"] if ref["role"] == "input" else run["outputs"]
            entries = [e for e in entries if e["path"] == ref["path"]]
            if not entries:
                return "missing", "record_absent", None
            return self.against_record(entries[0]["sha256"], ref, ref["path"])
        if kind == "code_snapshot":
            # Membership: only this run's snapshots, whatever bytes another run's snapshot holds.
            held = {run["script"]["snapshot"]: run["script"]["sha256"],
                    **{m["snapshot"]: m["sha256"] for m in run.get("modules", [])}}
            if ref["path"] not in held:
                return "missing", "record_absent", None
            return self.against_record(held[ref["path"]], ref, ref["path"])
        pytest.fail(f"no rule in this check for reference type {kind}")

    def against_record(self, recorded: str | None, ref: dict, path: str) -> tuple[str, str | None, None]:
        """Steps 3–5 of ADR-0006 4 for a target whose record keeps a hash of its own."""
        if "digest" in ref and ref["digest"]["sha256"] != recorded:
            return "missing", "digest_mismatch", None
        if recorded is None:
            return "unknown", "no_digest", None
        return self.compare(self.file(path), recorded)

    @staticmethod
    def compare(data: bytes | None, digest: str) -> tuple[str, str | None, None]:
        if data is None:
            return "missing", "file_absent", None
        return ("resolved" if sha(data) == digest else "content_changed"), None, None

    # --- record states ------------------------------------------------------------------------------

    def links(self, event: dict) -> list[tuple[str, str]]:
        """(relation, target id) of the references that change a record's state."""
        if event["schema"] == "lab.event/1":
            target = (event.get("refs") or {}).get("resolves")
            return [("resolves", target)] if target else []
        return [(r["relation"], r["event_id"]) for r in event["refs"]
                if r.get("type") == "event" and r.get("relation") in ("supersedes", "resolves")]

    def replay(self) -> dict[str, dict]:
        """ADR-0006 6: every record's state from one pass over the journal in seq order. An event applies
        to the state before it, and a later event never undoes a transition applied earlier."""
        states: dict[str, dict] = {}

        def target(eid: str | None) -> dict | None:
            return states.get(eid) if eid and len(self.lines_of.get(eid, [])) == 1 else None

        for _, event in sorted(self.events, key=lambda item: item[1]["seq"]):  # stable: line order on a tie
            eid, kind = event["id"], event.get("kind")
            if len(self.lines_of[eid]) == 1 and kind in CONTENT_KINDS:
                states[eid] = {"group": "content", "withdrawn": False, "by": set(), "mismatched": []}
            elif len(self.lines_of[eid]) == 1 and kind in QUESTION_KINDS:
                states[eid] = {"group": "question", "resolved": False, "mismatched": []}
            for relation, target_id in self.links(event):
                state = target(target_id)
                if state is None:
                    continue
                if relation == "supersedes" and state["group"] == "content":
                    state["by"].add(eid)
                elif relation == "resolves" and state["group"] == "question":
                    state["resolved"] = True
            if kind == "record_update":
                [ref] = [r for r in event["refs"] if r["relation"] == "updates"]
                state = target(ref.get("event_id"))
                assert state is not None, f"{eid} updates a record without a state"
                details = event["details"]
                if self.allowed(state, details["expected_state"], details["new_state"]):
                    self.apply(state, details["new_state"])
                else:
                    state["mismatched"].append(eid)  # shown, never applied and never skipped silently
        return states

    @staticmethod
    def unwithdrawn(state: dict) -> str:
        count = len(state["by"])
        return "current" if count == 0 else "superseded" if count == 1 else "conflict"

    @classmethod
    def display(cls, state: dict) -> str:
        if state["group"] == "question":
            return "resolved" if state["resolved"] else "open"
        return "withdrawn" if state["withdrawn"] else cls.unwithdrawn(state)

    @classmethod
    def allowed(cls, state: dict, expected: str, new: str) -> bool:
        """The transitions of ADR-0006 6, from the state the record is in."""
        if expected != cls.display(state):
            return False
        if state["group"] == "question":
            return (expected, new) == ("resolved", "open")
        if new == "withdrawn":
            return expected != "withdrawn"
        return expected == "withdrawn" and new == cls.unwithdrawn(state)

    @staticmethod
    def apply(state: dict, new: str) -> None:
        if state["group"] == "question":
            state["resolved"] = new == "resolved"
        else:
            state["withdrawn"] = new == "withdrawn"

    # --- runs and Report revisions --------------------------------------------------------------------

    def run_completed(self, run_id: str) -> bool:
        return self.completion_seq(run_id) is not None

    def completions(self) -> list[dict]:
        """The Report revision references of the completion events, in journal order."""
        return [r for _, event in self.events if event.get("kind") == "report_revision"
                for r in event["refs"] if r.get("type") == "report_revision" and r.get("relation") == "about"]

    def revision_completed(self, report_id: str, revision_id: str) -> bool:
        return any((r["report_id"], r["revision_id"]) == (report_id, revision_id) for r in self.completions())

    def head(self, report_id: str) -> str | None:
        heads = [r["revision_id"] for r in self.completions() if r["report_id"] == report_id]
        return heads[-1] if heads else None

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

    def save(self, request: dict) -> str:
        """ADR-0006 7, as the kit decides it inside the journal lock."""
        saved = [m for p, m in self.manifests().items() if p.parent.parent.name != ".staging"
                 and m["report_id"] == request["report_id"]
                 and m["request_id"].lower() == request["request_id"].lower()]
        head = self.head(request["report_id"])
        if saved:
            [manifest] = saved
            if manifest["request_fingerprint"] != request["request_fingerprint"]:
                return "request_conflict"
            if self.revision_completed(manifest["report_id"], manifest["revision_id"]):
                return "existing"
            return "resumed" if manifest["previous_revision"] == head else "revision_conflict"
        return "saved" if request["previous_revision"] == head else "revision_conflict"


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
    dropped = {(r["from"], r["ref"].get("index")) for r in case.expected["references"]
               if r["state"] == "unsupported"}
    for number, value in case.parsed.items():
        location = f"journal:{number}"
        assert unsupported_reason(value) == case.unsupported.get(location), number
        if location in case.unsupported:
            continue
        assert value["id"] == f"e{value['seq']}", number
        if value["schema"] == "analytics.event/2":
            refs = [r for i, r in enumerate(value["refs"]) if (value["id"], i) not in dropped]
            errors = [e.message for e in RECORD.iter_errors({**value, "refs": refs})]
            assert not errors, (number, errors)
    for path, manifest in case.manifests().items():
        assert not list(RECORD.iter_errors(manifest)), path
        paths = [f["path"] for f in manifest["files"]]
        assert portable_paths(paths) and manifest["entry"] in paths, path
        assert fingerprint(intent_of(manifest)) == manifest["request_fingerprint"], path
    for path in (case.root / "runs").glob("*/run.json"):
        location = f"runs/{path.parent.name}/run.json"
        assert unsupported_reason(load(path)) == case.unsupported.get(location), location


def test_journal_lines_seq_and_damage(case):
    journal = case.expected["journal"]
    assert journal["lines"] == len(case.lines)
    assert journal["damaged"] == case.damaged
    assert journal["recovered"] == case.recovered
    seqs = [(n, v["seq"]) for n, v in case.parsed.items() if isinstance(v.get("seq"), int)]
    for (first, seq), (second, after) in zip(seqs, seqs[1:], strict=False):
        # seq rises line by line. The /1 writer before #49 gave one seq twice: to the record it appended
        # onto a cut-off line and to the next line.
        assert seq < after or (seq == after and first in case.recovered and second == first + 1), (first, second)
    # ADR-0006 6: the next seq follows every parsed and recovered line, unsupported ones included.
    assert journal["next_seq"] == max(seq for _, seq in seqs) + 1
    for number in case.damaged:
        # The cut-off end of an interrupted append (no newline after it), or a line the /1 writer
        # before #49 appended onto one.
        cut_off_end = number == len(case.lines) and not case.raw.endswith(b"\n")
        assert number in case.recovered or cut_off_end, number


def test_reference_judgments_agree_with_the_case(case):
    for judged in case.expected["references"]:
        refs, event, before = case.refs_of(judged["from"])
        where = f"{judged['from']} {judged['ref']}"
        if "index" in judged["ref"]:
            stored = refs[judged["ref"]["index"]]
            assert (judged["version_binding"], judged["basis"]) == (stored["version_binding"], stored["basis"]), where
            if (stored["type"] not in REF_TYPES or stored["relation"] not in RELATIONS
                    or stored["basis"] not in BASES or stored["version_binding"] not in BINDINGS):
                state, reason, candidates = "unsupported", None, None
            elif stored["version_binding"] == "none":
                state, reason, candidates = case.unbound(stored, before)
            else:
                state, reason, candidates = case.resolve(stored)
                if stored["version_binding"] == "inferred":
                    case.check_inferred(stored, before)
        else:
            field, value = judged["ref"]["field"], judged["ref"]["value"]
            held = refs.get(field)
            assert value == held or value in (held or []), where
            run_chart = field == "charts" and event["kind"] == "run"
            binding, basis = ("author", "observed") if run_chart else (
                ("author", "declared") if field in EXACT_V1 else ("none", "declared"))
            assert (judged["version_binding"], judged["basis"]) == (binding, basis), where
            candidates = None
            if field == "runs":
                state, reason, candidates = case.resolve(exact("run", run_id=value))
            elif field in ("events", "resolves"):
                state, reason, candidates = case.resolve(exact("event", event_id=value))
            elif field == "context":
                found = case.context_version(value) is not None
                state, reason = ("resolved", None) if found else ("missing", "record_absent")
            elif run_chart:
                script, _, chart_id = value.rpartition("::")
                run_id = refs["runs"][0]
                assert case.runs[run_id]["script"]["path"] == script, where
                state, reason, candidates = case.resolve(exact("visualization_version", run_id=run_id,
                                                               visualization_id=chart_id))
            elif field == "other":
                state, reason = "unknown", "unresolved_name"
            else:
                # One logical key: never bound to the latest version (ADR-0006 4); versions that existed
                # before the event are only candidates.
                state, reason = "version_unbound", None
                if field == "charts":
                    script, _, chart_id = value.rpartition("::")
                    candidates = sorted(v for _, v in case.versions(chart_id, event["seq"], script))
        assert (judged["state"], judged.get("reason")) == (state, reason), where
        assert judged.get("candidates") == candidates, where
        assert judged["confirmed"] == confirmed(judged["version_binding"], judged["basis"], judged["state"]), where


def test_record_states(case):
    states = case.replay()
    for record in case.expected["records"]:
        state = states[record["id"]]
        assert case.display(state) == record["state"], record
        assert state["mismatched"] == record.get("mismatched_updates", []), record


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
    judged = {(r["report_id"], r["revision_id"]): r for r in case.expected["report_revisions"]}
    found = set()
    for path, manifest in case.manifests().items():
        key = (manifest["report_id"], manifest["revision_id"])
        found.add(key)
        expected = judged[key]
        staging = path.parent.parent.name == ".staging"
        # ADR-0006 7: staging or saved, a directory is named by the revision_id of its own attempt.
        assert path.parent.name == manifest["revision_id"], path
        assert expected["request_id"] == manifest["request_id"], key
        recording = "prepared" if staging else (
            "complete" if case.revision_completed(*key) else "incomplete")
        assert expected["recording_status"] == recording, key
        assert expected["content"] == case.content_state(path.parent, manifest), key
        assert expected["head"] == (case.head(manifest["report_id"]) == manifest["revision_id"]), key
    assert found == set(judged)


def test_lineage(case):
    for expected in case.expected["lineage"]:
        consumer = case.runs[expected["run_id"]]
        [entry] = [e for e in consumer["inputs"] if e["path"] == expected["path"]]
        # ADR-0006 4: successful runs that did not start after the consumer, start times compared as
        # instants.
        candidates = sorted(rid for rid, run in case.runs.items()
                            if rid != consumer["id"] and run.get("status") == "ok"
                            and instant(run["started_at"]) <= instant(consumer["started_at"])
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
            refused = refuse(request)
            if refused:
                assert "request_fingerprint" not in request, write
                result = refused
            else:
                assert fingerprint(intent_of(request)) == request["request_fingerprint"], write
                result = case.save(request)
        elif write["operation"] == "record_update":
            state = case.replay()[request["target"]]
            allowed = case.allowed(state, request["expected_state"], request["new_state"])
            result = "saved" if allowed else "state_conflict"
        else:
            writer, needed = request["writer_version"], case.marker["min_writer_version"]
            assert PATTERN["kit_version"].fullmatch(writer) and PATTERN["kit_version"].fullmatch(needed)
            result = "writer_too_old" if Version(writer) < Version(needed) else "saved"
        assert write["result"] == result, write


def test_fingerprint_vectors():
    """ADR-0006 8: the canonical bytes and sha256 another implementation has to reproduce."""
    vectors = {v["name"]: v for v in load(FIXTURES / "fingerprint-vectors.json")["vectors"]}
    for name, vector in vectors.items():
        text = canonical(vector["intent"])
        assert text.decode("utf-8") == vector["canonical"], name
        assert sha(text) == vector["sha256"], name
    korean, ordered = vectors["korean-title-files-out-of-order"], vectors["files-in-order"]
    assert korean["sha256"] == ordered["sha256"]
    assert "지역 매출 검토" in korean["canonical"]
    escaped = json.dumps(json.loads(korean["canonical"]), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert sha(escaped.encode("utf-8")) != korean["sha256"], "escaping the title gives another fingerprint"
    assert "\\u0001" in vectors["escapes"]["canonical"] and "\u2028" in vectors["escapes"]["canonical"]


@pytest.mark.parametrize(("path", "ok"), [
    ("index.md", True), ("data/region_summary.csv", True), ("그림/지역 매출.png", True), (".hidden", True),
    ("console.txt", True), ("com10.csv", True), ("con", False), ("CON.txt", False), ("con .txt", False),
    ("com1.csv", False), ("lpt\u00b9", False), ("conin$.txt", False), ("data/nul.parquet", False),
    ("../outside.md", False), ("a/../b", False), ("./a", False), ("/abs", False), ("a//b", False),
    ("a/", False), ("C:/elsewhere/index.md", False), ("a\\b", False), ("x.", False), ("x ", False),
    ("a:b", False), ("tab\there", False)])
def test_report_path_rule(path, ok):
    """ADR-0006 7 with ADR-0003 decision 6: what the schema's content_path accepts."""
    assert bool(PATTERN["content_path"].fullmatch(path)) is ok
    assert portable_paths([path]) is ok


def test_report_paths_collide_where_case_or_normalization_is_ignored():
    assert not portable_paths(["Index.md", "index.md"])
    assert not portable_paths([unicodedata.normalize("NFD", "보고서.md")])
    assert portable_paths(["index.md", "region_summary.csv"])


@pytest.mark.parametrize(("name", "ok"), [("req-normal-0001", True), ("REQ.1_a", True), ("nul.1", False),
                                          ("Con", False), ("req.", False), ("-x", False), ("a" * 129, False)])
def test_request_id_rule(name, ok):
    assert bool(PATTERN["request_id"].fullmatch(name)) is ok


def test_writer_versions_follow_pep_440():
    """ADR-0006 1.3: the order of min_writer_version, and the forms the marker does not take."""
    ordered = ["0.3.0.dev1", "0.3.0a1", "0.3.0b2", "0.3.0rc1", "0.3.0", "0.3.0.post1", "0.3.1.dev1", "1.0"]
    assert all(PATTERN["kit_version"].fullmatch(v) for v in ordered)
    assert sorted(ordered, key=Version) == ordered
    for text in ("0.3.0-beta.1", "v0.3.0", "0.3.0+local", "1!0.3.0", "0.3.0.beta1", "03.0"):
        assert not PATTERN["kit_version"].fullmatch(text), text


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
