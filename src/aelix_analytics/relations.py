"""The records of a project read as ADR-0006 says: relations, reference states and record states.
Standard library only.

This module reads; it never writes a record, a cache or an index. It imports neither `core` nor
`_platform` (nor any other module of the package), so it can be loaded by its file path alone, and
`core` can import it without a cycle. What differs by platform or by caller is passed in:

- `alive(pid) -> bool`: whether a process of that id still runs (the platform's process port).
- `sha(path) -> str | None`: the sha256 of a file's current bytes, None when there is no such file.
  It is called only for a reference whose target keeps a hash of its own, when that reference is
  judged; reading a `lab.event/1` reference never hashes a file.

The rules are those of ADR-0006 (docs/decisions/0006-common-record-contract.md), checked against the
examples under tests/fixtures/records/. Two readings this module makes where the decision is silent:

- A `lab.event/1` `refs.events` or `refs.resolves` target whose seq is not below the seq of the
  record that cites it is `missing` (`record_absent`): it did not exist when the reference was
  written, which is how section 4 step 2 ("find the target record") is read here. The `/1` writer
  never checked those targets, so a mistyped id would otherwise become a confirmed link once the
  journal grew that far.
- A cycle among producers is a walk that comes back to a run still on its own path. A run reached
  twice through two producers (a diamond) is not a cycle.

A producer judgment is `{run_id, path, sha256, producer, candidates, basis}`. Its basis is
`observed` (same path and hash as a successful run's output). It says which runs could have written
those bytes; it is never a confirmed link and never counted as one.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

# The version of this reader's rules, part of every source_revision it gives (ADR-0006 5).
READER_FORMAT = 1

EVENT_V1 = "lab.event/1"
EVENT_V2 = "analytics.event/2"
RUN_V1 = "lab.run/1"
DATASET_V1 = "lab.dataset/1"
REPORT_V1 = "analytics.report_revision/1"
# ADR-0006 1.4: the family versions a reader of the contract knows, and the family names #13 gives
# its next run and dataset records (any version of those is a version this reader does not know).
KNOWN = frozenset({"lab.project/1", RUN_V1, EVENT_V1, DATASET_V1, "analytics.project/2", EVENT_V2,
                   REPORT_V1})
FAMILIES = frozenset({name.rpartition("/")[0] for name in KNOWN} | {"analytics.run", "analytics.dataset"})
KINDS = {
    # The kinds the /1 writer accepts (core.EVENT_KINDS).
    EVENT_V1: frozenset({"project", "import", "run", "context", "finding", "decision", "assumption",
                         "question", "todo", "note", "gap", "resolve"}),
    # ADR-0006 1.2 and the record schema.
    EVENT_V2: frozenset({"project", "import", "run", "context", "finding", "decision", "assumption",
                         "question", "todo", "note", "resolve", "workflow_feedback", "analysis_focus",
                         "metric_definition", "metric_observation", "record_update", "checkpoint",
                         "reuse_proposal", "reuse_notification", "reuse_mode", "report_revision",
                         "publication", "publish_receipt"}),
}
REF_TYPES = frozenset({"event", "run", "visualization_version", "data_observation", "dataset_import",
                       "code_snapshot", "context_version", "metric_definition", "analysis_focus",
                       "reuse_proposal", "report_revision", "publication"})
RELATIONS = frozenset({"supports", "contradicts", "cites", "uses", "derived_from", "generated_by",
                       "about", "includes", "resolves", "supersedes", "updates"})
BASES = frozenset({"observed", "declared", "inferred", "unverified"})
BINDINGS = frozenset({"author", "inferred", "none"})
EVENT_TARGETS = frozenset({"event", "analysis_focus", "metric_definition", "reuse_proposal"})
# Fields that hold a logical key when a reference is left unbound (ADR-0006 2 and 3).
LOGICAL_KEYS = ("key", "path", "metric_id", "proposal_id", "report_id")
# ADR-0006 6: the records that have a state, by kind (`gap` is the /1 name of workflow_feedback).
CONTENT_KINDS = frozenset({"finding", "decision", "assumption", "note", "workflow_feedback", "gap",
                           "metric_definition", "analysis_focus"})
QUESTION_KINDS = frozenset({"question", "todo"})
REUSE_STEPS = frozenset({("proposed", "trial"), ("trial", "validated"), ("validated", "registered"),
                         *((stage, end) for stage in ("proposed", "trial", "validated")
                           for end in ("deferred", "rejected")),
                         ("deferred", "proposed"), ("rejected", "proposed"),
                         ("registered", "deprecated")})
V1_FIELDS = ("runs", "events", "resolves", "context", "charts", "data", "other")
RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z0-9][a-z0-9-]{0,60}$")  # as core.RUN_ID_RE
JOURNAL_FAMILIES = frozenset(KINDS)


def confirmed(binding, basis, state) -> bool:
    """ADR-0006 3: a confirmed link is an author binding with an observed or declared basis that
    resolves. Nothing else counts, and it is not a review of the conclusion."""
    return binding == "author" and basis in ("observed", "declared") and state == "resolved"


def unsupported_reason(value, readable: frozenset[str]) -> str | None:
    """Why a record cannot be read here (ADR-0006 1.4), or None. `readable` holds the family
    versions this location holds; a known family version found elsewhere is a kind of record this
    reader does not know there."""
    schema = value.get("schema") if isinstance(value, dict) else None
    if not isinstance(schema, str) or schema.rpartition("/")[0] not in FAMILIES:
        return "unknown_family"
    if schema not in KNOWN:
        return "unknown_version"
    if schema not in readable:
        return "unknown_kind"
    kinds = KINDS.get(schema)
    if kinds is not None and _str(value.get("kind")) not in kinds:
        return "unknown_kind"
    return None


def _instant(text) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _not_after(first: datetime | None, second: datetime | None) -> bool:
    """first <= second as instants (offsets applied). A time that cannot be read, or a naive time
    compared with an aware one, excludes nothing."""
    if first is None or second is None:
        return True
    try:
        return first <= second
    except TypeError:
        return True


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value) -> str | None:
    """A field that names something, or None: a hand-edited record may hold a list or an object where
    an id belongs, and that names nothing."""
    return value if isinstance(value, str) else None


def _dicts(items) -> list[dict]:
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _strings(value) -> list:
    """The items of a /1 refs field: a list, or one value for the fields that hold one."""
    return list(value) if isinstance(value, list) else [value]


def _digest(ref: dict):
    digest = ref.get("digest")
    return digest.get("sha256") if isinstance(digest, dict) else digest


# --- the journal ----------------------------------------------------------------------------------

def _recover(line: bytes) -> dict | None:
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


class Event:
    """One readable journal record: a `lab.event/1` or `analytics.event/2` line of a known kind."""

    __slots__ = ("line", "value", "schema", "seq", "id", "kind")

    def __init__(self, line: int, value: dict):
        self.line = line
        self.value = value
        self.schema = value["schema"]
        self.seq = _int(value.get("seq"))
        self.id = value.get("id") if isinstance(value.get("id"), str) else None
        self.kind = value.get("kind")

    @property
    def refs(self):
        return self.value.get("refs")

    @property
    def recorded_at(self):
        return self.value.get("ts" if self.schema == EVENT_V1 else "recorded_at")


class Journal:
    """journal.jsonl read as bytes and split at LF (a CR before it is JSON whitespace). Nothing else
    of the project is read, so a caller that needs only the events pays for the journal alone.

    A line that does not parse is `damaged`; the record a /1 writer appended onto it is recovered and
    the line stays damaged. Lines holding only whitespace hold no record and are not counted as
    damaged. Every parsed or recovered line keeps its place, unsupported ones included, and several
    lines with one id are all kept."""

    def __init__(self, data: bytes):
        lines = data.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        self.lines = len(lines)
        self.damaged: list[int] = []
        self.recovered: list[int] = []
        self.unsupported: list[dict] = []
        self.events: list[Event] = []
        self.lines_of: dict[str, list[int]] = {}
        self.seq_of: dict[int, int | None] = {}
        self._unsupported_lines: set[int] = set()
        self._ordered: list[Event] | None = None
        self._replay: Replay | None = None
        top = 0
        for number, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except ValueError:
                self.damaged.append(number)
                value = _recover(raw)
                if value is None:
                    continue
                self.recovered.append(number)
            seq = _int(value.get("seq")) if isinstance(value, dict) else None
            self.seq_of[number] = seq
            if seq is not None:
                top = max(top, seq)
            if isinstance(value, dict) and isinstance(value.get("id"), str):
                self.lines_of.setdefault(value["id"], []).append(number)
            reason = unsupported_reason(value, JOURNAL_FAMILIES)
            if reason:
                self.unsupported.append({"location": f"journal:{number}", "reason": reason})
                self._unsupported_lines.add(number)
            else:
                self.events.append(Event(number, value))
        # ADR-0006 6: the next seq follows every parsed and recovered line, unsupported ones too.
        self.as_of_seq = top
        self.next_seq = top + 1

    def summary(self) -> dict:
        return {"lines": self.lines, "damaged": list(self.damaged), "recovered": list(self.recovered),
                "next_seq": self.next_seq}

    def is_unsupported(self, line: int) -> bool:
        return line in self._unsupported_lines

    def source_of(self, event: Event) -> str:
        """`e<seq>` as the record names itself, or `journal:<line>` when another line has its id."""
        if event.id is not None and len(self.lines_of.get(event.id, [])) == 1:
            return event.id
        return f"journal:{event.line}"

    def in_order(self) -> list[Event]:
        """Seq order, line order among equal seqs (a record without a seq goes last)."""
        if self._ordered is None:
            self._ordered = sorted(self.events, key=lambda e: (e.seq is None, e.seq or 0, e.line))
        return self._ordered

    def replay(self) -> Replay:
        if self._replay is None:
            self._replay = Replay(self)
        return self._replay


def read_journal(root) -> Journal:
    """The journal of the project at `root` and nothing else."""
    try:
        data = (Path(root) / "journal.jsonl").read_bytes()
    except OSError:
        data = b""
    return Journal(data)


# --- record states (ADR-0006 6) --------------------------------------------------------------------

def _by_corrections(state: dict) -> str:
    count = len(state["by"])
    return "current" if count == 0 else "superseded" if count == 1 else "conflict"


def _display(state: dict) -> str:
    group = state["group"]
    if group == "question":
        return "resolved" if state["resolved"] else "open"
    if group == "reuse":
        return state["stage"]
    if state["withdrawn"]:
        return "withdrawn"
    return "current" if group == "report" else _by_corrections(state)


def _allowed(state: dict, expected, new) -> bool:
    """The transitions of the table in ADR-0006 6, from the state the record is in."""
    if expected != _display(state):
        return False
    group = state["group"]
    if group == "question":
        return (expected, new) == ("resolved", "open")
    if group == "report":
        return (expected, new) in (("current", "withdrawn"), ("withdrawn", "current"))
    if group == "reuse":
        return (expected, new) in REUSE_STEPS
    if new == "withdrawn":
        return expected != "withdrawn"
    return expected == "withdrawn" and new == _by_corrections(state)


def _apply(state: dict, new) -> None:
    group = state["group"]
    if group == "question":
        state["resolved"] = new == "resolved"
    elif group == "reuse":
        state["stage"] = new
    else:
        state["withdrawn"] = new == "withdrawn"


def report_key(report_id, revision_id) -> str:
    return f"report:{report_id}/{revision_id}"


def _known_ref(ref) -> bool:
    return (isinstance(ref, dict) and _str(ref.get("type")) in REF_TYPES
            and _str(ref.get("relation")) in RELATIONS and _str(ref.get("basis")) in BASES
            and _str(ref.get("version_binding")) in BINDINGS)


class Replay:
    """Every record's state from one pass over the journal in seq order. An event applies to the
    state before it, and a later event never undoes a transition applied earlier. A reference whose
    target is not one record (absent, several lines with its id, or a record without a state)
    changes nothing."""

    def __init__(self, journal: Journal):
        self.states: dict[str, dict] = {}
        # record_update events whose target has no state: shown, never applied.
        self.unapplied: list[str] = []
        lines_of = journal.lines_of

        def target(event_id) -> dict | None:
            if isinstance(event_id, str) and len(lines_of.get(event_id, [])) == 1:
                return self.states.get(event_id)
            return None

        for event in journal.in_order():
            unique = event.id is not None and len(lines_of.get(event.id, [])) == 1
            if unique and event.kind in CONTENT_KINDS:
                self.states[event.id] = {"group": "content", "withdrawn": False, "by": set(),
                                         "mismatched": []}
            elif unique and event.kind in QUESTION_KINDS:
                self.states[event.id] = {"group": "question", "resolved": False, "mismatched": []}
            elif unique and event.kind == "reuse_proposal" and event.schema == EVENT_V2:
                self.states[event.id] = {"group": "reuse", "stage": "proposed", "mismatched": []}
            if event.kind == "report_revision" and event.schema == EVENT_V2:
                for ref in _dicts(event.refs):
                    if _known_ref(ref) and ref["type"] == "report_revision" and ref["relation"] == "about":
                        key = report_key(ref.get("report_id"), ref.get("revision_id"))
                        self.states.setdefault(key, {"group": "report", "withdrawn": False,
                                                     "mismatched": []})
            for relation, target_id in self._links(event):
                state = target(target_id)
                if state is None:
                    continue
                if relation == "supersedes" and state["group"] == "content":
                    state["by"].add(event.id or f"journal:{event.line}")
                elif relation == "resolves" and state["group"] == "question":
                    state["resolved"] = True
            if event.kind == "record_update" and event.schema == EVENT_V2:
                self._update(event, target)

    @staticmethod
    def _links(event: Event) -> list[tuple[str, str]]:
        """(relation, event id) of the references that can change a record's state."""
        if event.schema == EVENT_V1:
            refs = event.refs if isinstance(event.refs, dict) else {}
            resolves = refs.get("resolves")
            return [("resolves", resolves)] if isinstance(resolves, str) else []
        return [(ref["relation"], ref.get("event_id")) for ref in _dicts(event.refs)
                if _known_ref(ref) and ref["type"] in EVENT_TARGETS
                and ref["relation"] in ("supersedes", "resolves")]

    def _update(self, event: Event, target) -> None:
        name = event.id or f"journal:{event.line}"
        updates = [ref for ref in _dicts(event.refs) if _known_ref(ref) and ref["relation"] == "updates"]
        state = None
        if len(updates) == 1:
            ref = updates[0]
            if ref["type"] in EVENT_TARGETS:
                state = target(ref.get("event_id"))
            elif ref["type"] == "report_revision":
                state = self.states.get(report_key(ref.get("report_id"), ref.get("revision_id")))
        if state is None:
            self.unapplied.append(name)
            return
        details = event.value.get("details")
        details = details if isinstance(details, dict) else {}
        if _allowed(state, details.get("expected_state"), details.get("new_state")):
            _apply(state, details["new_state"])
        else:
            state["mismatched"].append(name)

    def record_states(self) -> dict[str, dict]:
        """Record id (`e<seq>`, or `report:<report_id>/<revision_id>`) -> its state and the
        record_update events that did not apply to it."""
        return {key: {"state": _display(state), "mismatched_updates": list(state["mismatched"])}
                for key, state in self.states.items()}

    def transition_allowed(self, target: str, expected, new) -> bool:
        """Whether a record_update of `target` from `expected` to `new` applies now (ADR-0006 6)."""
        state = self.states.get(target)
        return state is not None and _allowed(state, expected, new)


# --- the source revision (ADR-0006 5) ------------------------------------------------------------

def _run_dirs(root: Path) -> list[str]:
    try:
        entries = os.scandir(root / "runs")
    except OSError:
        return []
    with entries:
        return sorted(e.name for e in entries if RUN_ID_RE.match(e.name) and e.is_dir())


def _report_dirs(root: Path) -> list[tuple[str, bool]]:
    """(project-relative directory, staging) of every saved and prepared Report revision."""
    base = root / "artifacts" / "reports"
    out = []
    for report in _subdirs(base):
        for revision in _subdirs(base / report):
            if revision == ".staging":
                out += [(f"artifacts/reports/{report}/.staging/{name}", True)
                        for name in _subdirs(base / report / ".staging")]
            else:
                out.append((f"artifacts/reports/{report}/{revision}", False))
    return out


def _subdirs(path: Path) -> list[str]:
    try:
        entries = os.scandir(path)
    except OSError:
        return []
    with entries:
        return sorted(e.name for e in entries if e.is_dir())


def _files(path: Path, suffix: str) -> list[str]:
    try:
        entries = os.scandir(path)
    except OSError:
        return []
    with entries:
        return sorted(e.name for e in entries if e.name.endswith(suffix) and e.is_file())


CONTEXT_DIRS = (".analytics/context", ".lab/context")  # ADR-0006 4: in this order


def _snapshot(root: Path) -> list:
    """What a source_revision is made of: the reader format, and the project-relative path, size and
    mtime_ns of every record file this reader reads, with the names of the CONTEXT versions. No
    absolute path is part of it."""

    def stat(rel: str) -> list:
        try:
            st = os.stat(root / rel)
        except OSError:
            return [rel, None, None]
        return [rel, st.st_size, st.st_mtime_ns]

    out: list = [READER_FORMAT, stat("journal.jsonl"), stat("CONTEXT.md")]
    out += [stat(f"runs/{name}/run.json") for name in _run_dirs(root)]
    out += [stat(f"{directory}/manifest.json") for directory, _ in _report_dirs(root)]
    out += [stat(f"data/raw/{name}") for name in _files(root / "data" / "raw", ".meta.json")]
    out += [[directory, _files(root / directory, ".md")] for directory in CONTEXT_DIRS]
    return out


def _token(snapshot: list, as_of_seq: int) -> str:
    text = json.dumps([snapshot, as_of_seq], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]


# --- request fingerprints (ADR-0006 8) -------------------------------------------------------------

INTENT_FIELDS = ("report_id", "previous_revision", "title", "entry", "based_on")


def _plain(value) -> None:
    """The parts of JSON a save intent may hold: ASCII keys, integers, strings without a lone
    surrogate, null, booleans, lists and objects."""
    if isinstance(value, float):
        raise ValueError("a save intent holds no floating-point number")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise ValueError(f"a save intent has ASCII keys only: {key!r}")
            _plain(item)
    elif isinstance(value, list):
        for item in value:
            _plain(item)
    elif isinstance(value, str):
        value.encode("utf-8")  # UnicodeEncodeError (a ValueError) for a lone surrogate


def canonical(request: dict) -> bytes:
    """The RFC 8785 form of a Report save's intent: report_id, previous_revision, title, entry,
    files as {path, sha256} sorted by path, and based_on (null when absent). For an object of ASCII
    keys, integers and strings that is sorted keys, no whitespace, only the escapes JSON requires
    (lowercase \\u00xx) and UTF-8. Two files with one path is a request ADR-0006 7 refuses."""
    intent = {key: request.get(key) for key in INTENT_FIELDS}
    files = [{"path": f["path"], "sha256": f["sha256"]} for f in request["files"]]
    paths = [f["path"] for f in files]
    if len(set(paths)) != len(paths):
        raise ValueError("two files with one path have no fingerprint")
    intent["files"] = sorted(files, key=lambda f: f["path"])  # str order is code point order
    _plain(intent)
    return json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def request_fingerprint(request: dict) -> str:
    """ADR-0006 8: the sha256 of the canonical intent of a Report save request or manifest. Times
    and request_id are not part of it."""
    return hashlib.sha256(canonical(request)).hexdigest()


# --- the read model --------------------------------------------------------------------------------

def _load(path: Path) -> tuple[object, str | None]:
    """(value, None), or (None, why) when there is no such file or it is not one JSON object."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, "absent"
    except OSError:
        return None, "unreadable"
    try:
        value = json.loads(data)
    except ValueError:
        return None, "unparsable"
    return (value, None) if isinstance(value, dict) else (None, "unparsable")


class ReadModel:
    """One read of a project's records. Built by `read`; every judgment comes from what that read
    found. The indexes it needs (completion seq of each run, the versions of each Visualization key
    in completion order, the successful runs that output each path and hash) are built once, on
    first use, and kept."""

    def __init__(self, root: Path, journal: Journal, *, alive, sha):
        self.root = root
        self.journal = journal
        self._sha = sha
        self.as_of_seq = journal.as_of_seq
        self.source_revision: str | None = None
        self.updating = False
        self.observed_revisions: list[str] = []
        self.damaged_files: list[dict] = []
        files_unsupported: list[dict] = []
        self._runs: dict[str, dict] = {}
        self._execution: dict[str, str] = {}
        self._unsupported_runs: set[str] = set()
        for name in _run_dirs(root):
            location = f"runs/{name}/run.json"
            value, problem = _load(root / location)
            if problem:
                self.damaged_files.append({"location": location, "reason": problem})
                continue
            reason = unsupported_reason(value, frozenset({RUN_V1}))
            if reason:
                files_unsupported.append({"location": location, "reason": reason})
                self._unsupported_runs.add(name)
                continue
            self._runs[name] = value
            status = value.get("status")
            pid = value.get("pid")
            dead = status == "running" and not alive(pid if isinstance(pid, (int, str)) else None)
            self._execution[name] = "incomplete" if dead else status
        self._manifests: list[tuple[str, dict, bool]] = []
        self._saved: dict[tuple, tuple[str, dict]] = {}
        self._unsupported_reports: set[tuple[str, str]] = set()
        for directory, staging in _report_dirs(root):
            location = f"{directory}/manifest.json"
            value, problem = _load(root / location)
            if problem:
                if not staging:  # a staging directory being prepared has no manifest yet
                    self.damaged_files.append({"location": location, "reason": problem})
                continue
            reason = unsupported_reason(value, frozenset({REPORT_V1}))
            if reason:
                files_unsupported.append({"location": location, "reason": reason})
                if not staging:
                    parts = directory.split("/")
                    self._unsupported_reports.add((parts[2], parts[3]))
                continue
            self._manifests.append((directory, value, staging))
            if not staging:
                key = (_str(value.get("report_id")), _str(value.get("revision_id")))
                self._saved[key] = (directory, value)
        self._metas: list[tuple[str, dict]] = []
        for name in _files(root / "data" / "raw", ".meta.json"):
            location = f"data/raw/{name}"
            value, problem = _load(root / location)
            if problem:
                self.damaged_files.append({"location": location, "reason": problem})
                continue
            reason = unsupported_reason(value, frozenset({DATASET_V1}))
            if reason:
                files_unsupported.append({"location": location, "reason": reason})
                continue
            self._metas.append((location, value))
        self.unsupported = list(journal.unsupported) + files_unsupported
        self._completion: dict[str, int] | None = None
        self._versions: tuple[dict, dict] | None = None
        self._outputs: dict[tuple, list[tuple[str, datetime | None]]] | None = None
        self._references: list[dict] | None = None
        self._by_source: dict[str, list[dict]] = {}
        self._upstream: dict[str, dict] = {}

    # --- runs, imports and Report revisions --------------------------------------------------------

    def completion_seq(self, run_id: str) -> int | None:
        """The seq of a run's completion event (ADR-0006 6), or None."""
        if self._completion is None:
            done: dict[str, int] = {}
            for event in self.journal.events:
                if event.kind != "run" or event.seq is None:
                    continue
                if event.schema == EVENT_V1:
                    refs = event.refs if isinstance(event.refs, dict) else {}
                    ids = [r for r in _strings(refs.get("runs", [])) if isinstance(r, str)]
                else:
                    ids = [_str(r.get("run_id")) for r in _dicts(event.refs) if _known_ref(r)
                           and r["type"] == "run" and r["relation"] == "about"]
                for run_id_ in filter(None, ids):
                    if run_id_ not in done or event.seq < done[run_id_]:
                        done[run_id_] = event.seq
            self._completion = done
        return self._completion.get(run_id)

    def runs(self) -> list[dict]:
        """Execution and recording status of every run this reader can read (ADR-0006 6)."""
        out = []
        for run_id, run in self._runs.items():
            reasons = []
            if run.get("status") == "running":
                reasons.append("not_final")
            if self.completion_seq(run_id) is None:
                reasons.append("no_completion_event")
            if run.get("finalize_errors"):
                reasons.append("finalize_errors")
            out.append({"run_id": run_id, "execution_status": self._execution[run_id],
                        "recording_status": "incomplete" if reasons else "complete", "reasons": reasons})
        return out

    def run(self, run_id: str) -> dict | None:
        """The run record as it was read, or None."""
        return self._runs.get(run_id)

    def imports(self) -> list[dict]:
        """The recording status of every import record (`data/raw/<name>.meta.json`). Its /1
        completion event is a `kind=import` event whose `refs.data` holds the record's path, recorded
        at or after its `imported_at`; its /2 one an `import` event with a `dataset_import` `about`
        reference to that path (and hash, when given)."""
        events: dict[str, list[datetime | None]] = {}
        digests: dict[str, list] = {}
        for event in self.journal.events:
            if event.kind != "import":
                continue
            if event.schema == EVENT_V1:
                refs = event.refs if isinstance(event.refs, dict) else {}
                for path in _strings(refs.get("data", [])):
                    if isinstance(path, str):
                        events.setdefault(path, []).append(_instant(event.recorded_at))
            else:
                for ref in _dicts(event.refs):
                    if _known_ref(ref) and ref["type"] == "dataset_import" and ref["relation"] == "about":
                        digests.setdefault(_str(ref.get("path")), []).append(_digest(ref))
        out = []
        for location, meta in self._metas:
            path = _str(meta.get("path"))
            imported = _instant(meta.get("imported_at"))
            done = any(_not_after(imported, at) for at in events.get(path, [])) or any(
                d is None or d == meta.get("sha256") for d in digests.get(path, []))
            out.append({"location": location, "name": meta.get("name"), "path": path,
                        "recording_status": "complete" if done else "incomplete",
                        "reasons": [] if done else ["no_completion_event"]})
        return out

    def _report_completions(self) -> list[tuple[str, str]]:
        """(report_id, revision_id) of the Report completion events, in seq order."""
        out = []
        for event in self.journal.in_order():
            if event.kind == "report_revision" and event.schema == EVENT_V2:
                out += [(_str(r.get("report_id")), _str(r.get("revision_id"))) for r in _dicts(event.refs)
                        if _known_ref(r) and r["type"] == "report_revision" and r["relation"] == "about"]
        return out

    def report_revisions(self) -> list[dict]:
        """Every saved and prepared Report revision (ADR-0006 7): its recording status, whether its
        files are the bytes its manifest names, and whether it is the head of its Report."""
        order = self._report_completions()
        completed, heads = set(order), {report_id: revision_id for report_id, revision_id in order}
        out = []
        for directory, manifest, staging in self._manifests:
            key = (_str(manifest.get("report_id")), _str(manifest.get("revision_id")))
            recording = "prepared" if staging else "complete" if key in completed else "incomplete"
            out.append({"report_id": key[0], "revision_id": key[1],
                        "request_id": manifest.get("request_id"), "recording_status": recording,
                        "content": self._content(directory, manifest),
                        "head": not staging and heads.get(key[0]) == key[1]})
        return out

    def _content(self, directory: str, manifest: dict) -> str:
        states = set()
        for item in _dicts(manifest.get("files")):
            current = self._sha(self.root / directory / "content" / str(item.get("path")))
            states.add("missing" if current is None else "resolved" if current == item.get("sha256")
                       else "content_changed")
        for state in ("missing", "content_changed"):
            if state in states:
                return state
        return "resolved"

    # --- Visualization versions ------------------------------------------------------------------

    def _version_index(self) -> tuple[dict, dict]:
        """Logical key -> [(completion seq, version)], and Visualization id -> [(completion seq,
        script, version)], both in completion order, from the successful runs with a completion
        event."""
        if self._versions is None:
            by_key: dict[str, list] = {}
            by_name: dict[str, list] = {}
            for run_id, run in self._runs.items():
                done = self.completion_seq(run_id)
                if run.get("status") != "ok" or done is None:
                    continue
                script = _str((run.get("script") or {}).get("path")) or "?"
                for chart in _dicts(run.get("charts")):
                    chart_id = _str(chart.get("id"))
                    if chart_id is None:
                        continue
                    version = f"{run_id}/{chart_id}"
                    by_key.setdefault(f"{script}::{chart_id}", []).append((done, version))
                    by_name.setdefault(chart_id, []).append((done, script, version))
            for table in (by_key, by_name):
                for versions in table.values():
                    versions.sort()
            self._versions = (by_key, by_name)
        return self._versions

    def visualization_versions(self, key: str, before: int | None = None) -> list[str]:
        """`<run_id>/<visualization_id>` of the versions of a logical key whose completion event comes
        before seq `before` (all of them when None), sorted."""
        versions = self._version_index()[0].get(key, []) if isinstance(key, str) else []
        end = len(versions) if before is None else bisect.bisect_left(versions, (before,))
        return sorted(version for _, version in versions[:end])

    def _by_name(self, name, before: int) -> list[tuple[str, str]]:
        if not isinstance(name, str):
            return []
        versions = self._version_index()[1].get(name, [])
        end = bisect.bisect_left(versions, (before,))
        return [(script, version) for _, script, version in versions[:end]]

    # --- reference judgments (ADR-0006 3 and 4) --------------------------------------------------

    def references(self, source: str | None = None) -> list[dict]:
        """One judgment for every reference of every readable event and saved Report manifest, in
        journal order and then manifest order; only those of `source` (`e<seq>`, `journal:<line>` or
        `report:<report_id>/<revision_id>`) when given."""
        if self._references is None:
            out: list[dict] = []
            for event in self.journal.events:
                source_id = self.journal.source_of(event)
                before = event.seq if event.seq is not None else self.journal.next_seq
                if event.schema == EVENT_V1:
                    out += self._judge_v1(event, source_id, before)
                else:
                    out += self._judge_v2(event.refs, source_id, event.line, before)
            completions = None
            for _directory, manifest, staging in self._manifests:
                based_on = manifest.get("based_on")
                if staging or not isinstance(based_on, dict):
                    continue
                as_of = _int(based_on.get("as_of_seq"))
                if as_of is not None:
                    before = as_of + 1
                else:  # before the revision's completion event, or what the journal holds
                    if completions is None:
                        completions = self._completion_seqs()
                    before = completions.get((_str(manifest.get("report_id")),
                                              _str(manifest.get("revision_id"))), self.journal.next_seq)
                source_id = report_key(manifest.get("report_id"), manifest.get("revision_id"))
                out += self._judge_v2(based_on.get("refs"), source_id, None, before)
            self._references = out
            for judged in out:
                self._by_source.setdefault(judged["from"], []).append(judged)
        if source is None:
            return list(self._references)
        return list(self._by_source.get(source, []))

    def _completion_seqs(self) -> dict[tuple, int]:
        out: dict[tuple, int] = {}
        for event in self.journal.in_order():
            if event.kind == "report_revision" and event.schema == EVENT_V2 and event.seq is not None:
                for r in _dicts(event.refs):
                    if _known_ref(r) and r["type"] == "report_revision" and r["relation"] == "about":
                        out.setdefault((_str(r.get("report_id")), _str(r.get("revision_id"))), event.seq)
        return out

    @staticmethod
    def _judgment(source, line, ref, type_, relation, target, binding, basis, state, reason=None,
                  candidates=None) -> dict:
        out = {"from": source, "line": line, "ref": ref, "type": type_, "relation": relation,
               "target": target, "state": state, "version_binding": binding, "basis": basis,
               "confirmed": confirmed(binding, basis, state)}
        if reason is not None:
            out["reason"] = reason
        if candidates is not None:
            out["candidates"] = candidates
        return out

    def _judge_v2(self, refs, source, line, before) -> list[dict]:
        if not isinstance(refs, list):
            return [self._judgment(source, line, {"index": None}, None, None, None, None, None,
                                   "unsupported")] if refs is not None else []
        out = []
        for index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                out.append(self._judgment(source, line, {"index": index}, None, None, None, None, None,
                                          "unsupported"))
                continue
            target = {k: v for k, v in ref.items()
                      if k not in ("type", "relation", "basis", "version_binding")}
            binding, basis = ref.get("version_binding"), ref.get("basis")
            if not _known_ref(ref):
                state, reason, candidates = "unsupported", None, None
            elif binding == "none":
                state, reason, candidates = self._unbound(ref, before)
            else:
                state, reason, candidates = self._resolve(ref)
            out.append(self._judgment(source, line, {"index": index}, ref.get("type"),
                                      ref.get("relation"), target, binding, basis, state, reason,
                                      candidates))
        return out

    def _judge_v1(self, event: Event, source: str, before: int) -> list[dict]:
        """A lab.event/1 refs object read as ADR-0006 4 says, without rewriting the record."""
        refs = event.refs
        if not isinstance(refs, dict):
            return [self._judgment(source, event.line, {"field": None}, None, None, None, None, None,
                                   "unsupported")] if refs is not None else []
        out = []
        about = event.kind in ("run", "import", "context")
        fields = [f for f in V1_FIELDS if f in refs] + sorted(f for f in refs if f not in V1_FIELDS)
        for field in fields:
            for value in _strings(refs[field]):
                ref = {"field": field, "value": value}
                judged = self._v1(event, field, value, about, before)
                out.append(self._judgment(source, event.line, ref, *judged))
        return out

    def _v1(self, event: Event, field, value, about: bool, before: int) -> tuple:
        """(type, relation, target, binding, basis, state, reason, candidates) of one /1 item."""
        if field not in V1_FIELDS or not isinstance(value, str):
            return None, None, None, None, None, "unsupported", None, None
        if field == "runs":
            ref = {"type": "run", "run_id": value}
            return ("run", "about" if about else "cites", {"run_id": value}, "author", "declared",
                    *self._resolve(ref))
        if field in ("events", "resolves"):
            relation = "resolves" if field == "resolves" else "cites"
            return ("event", relation, {"event_id": value}, "author", "declared",
                    *self._event_target(value, before))
        if field == "context":
            found = self._context_file(value) is not None
            return ("context_version", "about" if event.kind == "context" else "cites",
                    {"version": value}, "author", "declared",
                    *(("resolved", None, None) if found else ("missing", "record_absent", None)))
        if field == "charts" and event.kind == "run":
            refs = event.refs
            runs = [r for r in _strings(refs.get("runs", [])) if isinstance(r, str)]
            script, _, chart_id = value.rpartition("::")
            run_id = runs[0] if runs else None
            target = {"run_id": run_id, "visualization_id": chart_id}
            run = self._runs.get(run_id)
            if run is not None and (run.get("script") or {}).get("path") != script:
                judged = ("missing", "record_absent", None)
            else:
                judged = self._resolve({"type": "visualization_version", "run_id": run_id,
                                        "visualization_id": chart_id})
            return ("visualization_version", "about", target, "author", "observed", *judged)
        if field == "charts":
            return ("visualization_version", "cites", {"key": value}, "none", "declared",
                    "version_unbound", None, self.visualization_versions(value, before))
        if field == "data":
            # ADR-0006 4: a name or path, left unbound, with no candidates.
            return ("data_observation", "about" if event.kind in ("run", "import") else "cites",
                    {"key": value}, "none", "declared", "version_unbound", None, None)
        return None, "cites", {"token": value}, "none", "declared", "unknown", "unresolved_name", None

    def _unbound(self, ref: dict, before: int) -> tuple:
        """ADR-0006 3: a reference left unbound stays unbound when it is read. Only Visualization
        versions are listed as candidates; the Issue that first writes an unbound reference of
        another type adds its candidates."""
        if ref["type"] == "visualization_version":
            if isinstance(ref.get("key"), str):
                return "version_unbound", None, self.visualization_versions(ref["key"], before)
            if "token" in ref:
                found = self._by_name(ref["token"], before)
                scripts = {script for script, _ in found}
                if not scripts:
                    return "unknown", "unresolved_name", None
                state = "ambiguous" if len(scripts) > 1 else "version_unbound"
                return state, None, sorted(version for _, version in found)
            return "unknown", "unresolved_name", None
        if any(field in ref for field in LOGICAL_KEYS):
            return "version_unbound", None, None
        return "unknown", "unresolved_name", None

    def _event_target(self, event_id, before: int | None = None) -> tuple:
        """Step 2 of ADR-0006 4 for an event. With `before` (a /1 reference), a line whose seq is not
        below it did not exist when the reference was written."""
        lines = self.journal.lines_of.get(event_id, []) if isinstance(event_id, str) else []
        if before is not None:
            seq_of = self.journal.seq_of
            lines = [n for n in lines if seq_of.get(n) is None or seq_of[n] < before]
        if not lines:
            return "missing", "record_absent", None
        if len(lines) > 1:
            return "ambiguous", None, [f"journal:{n}" for n in lines]
        if self.journal.is_unsupported(lines[0]):
            return "unsupported", None, None
        return "resolved", None, None

    def _context_file(self, name) -> Path | None:
        """A CONTEXT version by file name, in .analytics/context/ and then .lab/context/."""
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
            return None
        for directory in CONTEXT_DIRS:
            path = self.root / directory / name
            if path.is_file():
                return path
        return None

    def _resolve(self, ref: dict) -> tuple:
        """(state, reason, candidates) of one bound reference, in the order of ADR-0006 4."""
        kind = ref["type"]
        if kind in EVENT_TARGETS:
            return self._event_target(ref.get("event_id"))
        if kind == "publication":
            return "unsupported", None, None  # publications are #20's family, unknown here
        if kind == "context_version":
            path = self._context_file(ref.get("version"))
            if path is None:
                return "missing", "record_absent", None
            digest = _digest(ref)
            return self._compare(path, digest) if digest is not None else ("resolved", None, None)
        if kind == "report_revision":
            return self._report(ref)
        if kind == "dataset_import":
            path = _str(ref.get("path"))
            metas = [m for _, m in self._metas if path is not None and m.get("path") == path]
            if not metas:
                return "missing", "record_absent", None
            return self._against_record(metas[-1].get("sha256"), ref, path)
        run_id = _str(ref.get("run_id"))
        run = self._runs.get(run_id)
        if run is None:
            if run_id in self._unsupported_runs:
                return "unsupported", None, None
            return "missing", "record_absent", None
        if kind == "run":
            return "resolved", None, None
        if kind == "visualization_version":
            charts = [c for c in _dicts(run.get("charts")) if c.get("id") == ref.get("visualization_id")]
            if not charts:
                return "missing", "record_absent", None
            if "digest" in ref:
                # A lab.run/1 chart record holds no hash: this digest is one the record never held.
                return "missing", "digest_mismatch", None
            files = charts[0].get("files")
            files = list(files.values()) if isinstance(files, dict) else []
            if all(isinstance(f, str) and (self.root / f).is_file() for f in files):
                return "resolved", None, None
            return "missing", "file_absent", None
        if kind == "data_observation":
            entries = run.get("inputs") if ref.get("role") == "input" else run.get("outputs")
            path = _str(ref.get("path"))
            entries = [e for e in _dicts(entries) if path is not None and e.get("path") == path]
            if not entries or ref.get("role") not in ("input", "output"):
                return "missing", "record_absent", None
            return self._against_record(entries[0].get("sha256"), ref, path)
        # code_snapshot: only this run's snapshots, whatever bytes another run's snapshot holds.
        script = run.get("script") or {}
        held = {_str(script.get("snapshot")): script.get("sha256"),
                **{_str(m.get("snapshot")): m.get("sha256") for m in _dicts(run.get("modules"))}}
        held.pop(None, None)
        path = _str(ref.get("path"))
        if path not in held:
            return "missing", "record_absent", None
        return self._against_record(held[path], ref, path)

    def _against_record(self, recorded, ref: dict, path) -> tuple:
        """Steps 3–5 of ADR-0006 4 for a target whose record keeps a hash of its own."""
        if "digest" in ref and _digest(ref) != recorded:
            return "missing", "digest_mismatch", None
        if not recorded:
            return "unknown", "no_digest", None
        return self._compare(self.root / str(path), recorded)

    def _compare(self, path: Path, digest) -> tuple:
        current = self._sha(path)
        if current is None:
            return "missing", "file_absent", None
        return ("resolved" if current == digest else "content_changed"), None, None

    def _report(self, ref: dict) -> tuple:
        key = (_str(ref.get("report_id")), _str(ref.get("revision_id")))
        found = self._saved.get(key)
        if found is None:
            if key in self._unsupported_reports:
                return "unsupported", None, None
            return "missing", "record_absent", None
        directory, manifest = found
        digest = _digest(ref)
        if digest is not None and self._sha(self.root / directory / "manifest.json") != digest:
            return "content_changed", None, None
        state = self._content(directory, manifest)
        return state, "file_absent" if state == "missing" else None, None

    def observation(self, run_id: str, role: str, path: str) -> dict:
        """The state of one input or output a run recorded, as a reference to it would be judged."""
        state, reason, _ = self._resolve({"type": "data_observation", "run_id": run_id, "role": role,
                                          "path": path})
        return {"state": state, **({"reason": reason} if reason else {})}

    # --- producers (ADR-0006 4) --------------------------------------------------------------------

    def _output_index(self) -> dict[tuple, list[tuple[str, datetime | None]]]:
        if self._outputs is None:
            index: dict[tuple, list] = {}
            for run_id, run in self._runs.items():
                if run.get("status") != "ok":
                    continue
                started = _instant(run.get("started_at"))
                for out in _dicts(run.get("outputs")):
                    path, digest = _str(out.get("path")), _str(out.get("sha256"))
                    if out.get("change") != "deleted" and path and digest:
                        index.setdefault((path, digest), []).append((run_id, started))
            self._outputs = index
        return self._outputs

    def producers(self, path: str, sha256: str, consumer: str | None = None) -> list[str]:
        """The successful runs that output `path` with these bytes, sorted. With a consumer, not that
        run and none that started after it (start times compared as instants)."""
        started = _instant((self._runs.get(consumer) or {}).get("started_at")) if consumer else None
        if not (isinstance(path, str) and isinstance(sha256, str)):
            return []
        found = {run_id for run_id, at in self._output_index().get((path, sha256), [])
                 if run_id != consumer and (consumer is None or _not_after(at, started))}
        return sorted(found)

    def producer(self, run_id: str, path: str) -> dict | None:
        """The producer judgment of one input of a run: `resolved` (one candidate), `ambiguous`
        (several, all listed), `none`, or `unknown` (the input has no hash). Never the last run."""
        run = self._runs.get(run_id) if isinstance(run_id, str) else None
        entries = [e for e in _dicts((run or {}).get("inputs")) if e.get("path") == path]
        if not entries:
            return None
        sha256 = _str(entries[0].get("sha256"))
        if not sha256:
            return {"run_id": run_id, "path": path, "sha256": None, "producer": "unknown",
                    "candidates": [], "basis": None}
        candidates = self.producers(path, sha256, consumer=run_id)
        state = "none" if not candidates else "resolved" if len(candidates) == 1 else "ambiguous"
        return {"run_id": run_id, "path": path, "sha256": sha256, "producer": state,
                "candidates": candidates, "basis": "observed" if candidates else None}

    def lineage(self) -> list[dict]:
        """The producer judgment of every input of every run."""
        out = []
        for run_id, run in self._runs.items():
            seen = set()
            for entry in _dicts(run.get("inputs")):
                path = _str(entry.get("path"))
                if path is not None and path not in seen:
                    seen.add(path)
                    out.append(self.producer(run_id, path))
        return out

    def _edges(self, run_id: str) -> list[str]:
        found: set[str] = set()
        for entry in _dicts((self._runs.get(run_id) or {}).get("inputs")):
            found.update(self.producers(entry.get("path"), entry.get("sha256"), consumer=run_id))
        return sorted(found)

    def upstream(self, run_id: str) -> dict:
        """Every run reached by following all producer candidates from `run_id`, and whether the walk
        met a cycle: a candidate still on the path being walked. A run reached again after its walk
        ended (a diamond) is not a cycle. Ends by visiting each run once, not at a depth."""
        if run_id in self._upstream:
            return self._upstream[run_id]
        on_path, done, reached, cycle = {run_id}, set(), set(), False
        stack = [(run_id, iter(self._edges(run_id)))]
        while stack:
            node, edges = stack[-1]
            nxt = next(edges, None)
            if nxt is None:
                stack.pop()
                on_path.discard(node)
                done.add(node)
            elif nxt in on_path:
                cycle = True
                reached.add(nxt)
            elif nxt not in done:
                reached.add(nxt)
                on_path.add(nxt)
                stack.append((nxt, iter(self._edges(nxt))))
        reached.discard(run_id)
        result = {"run_id": run_id, "upstream": sorted(reached), "cycle": cycle}
        self._upstream[run_id] = result
        return result

    # --- record states -----------------------------------------------------------------------------

    def record_states(self) -> dict[str, dict]:
        return self.journal.replay().record_states()

    def transition_allowed(self, target: str, expected, new) -> bool:
        return self.journal.replay().transition_allowed(target, expected, new)


def read(root, *, alive, sha) -> ReadModel:
    """Read the project at `root` as it is. The files read are compared before and after; when they
    differ the read is made again, twice at most, and then the model says `updating` and keeps every
    source_revision it observed."""
    root = Path(root)
    observed: list[str] = []
    for _ in range(3):
        before = _snapshot(root)
        model = ReadModel(root, read_journal(root), alive=alive, sha=sha)
        after = _snapshot(root)
        observed.append(_token(after, model.as_of_seq))
        if before == after:
            break
    else:
        model.updating = True
    model.observed_revisions = observed
    model.source_revision = observed[-1]
    return model
