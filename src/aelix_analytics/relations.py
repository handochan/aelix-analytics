"""The records of a project read as ADR-0006 says: relations, reference states and record states.
Standard library only.

This module reads; it never writes a record, a cache or an index. It imports neither `core` nor
`_platform` (nor any other module of the package), so it can be loaded by its file path alone, and
`core` can import it without a cycle. What differs by platform or by caller is passed in:

- `alive(pid) -> bool`: whether a process of that id still runs (the platform's process port).
- `sha(path) -> str | None`: the sha256 of a file's current bytes, None when there is no such file.
  It is called when a reference whose target keeps a hash of its own is judged, and for every file
  of every saved and prepared Report revision when `report_revisions()` is called; reading a
  `lab.event/1` reference never hashes a file.
- `legacy_path(recorded) -> str | None` (optional): the identity path (ADR-0006 9) a path that a
  kit before PATH_IDENTITY_SINCE recorded is now, as the namespace is linked and spelled now, or
  None when it is none (`core` passes `Project.legacy_identity`). Without it every path is read as
  recorded.

Paths of runs recorded by a kit before PATH_IDENTITY_SINCE (ADR-0006 9, and the owner's decision
of 2026-10-06 on Issue #12): such a kit recorded the real path, links followed, relative to the root
inside it and absolute outside, each name as it was typed where the file system ignores case or
Unicode normalization. A path such a run recorded — absolute under the root or a link's target, or
relative in another spelling than the stored one — is read as the identity path `legacy_path` gives
it, when that differs from the recorded one and names an entry now or a path some run recorded as
an output: never a path made up. A path text that a run of PATH_IDENTITY_SINCE or later also
recorded (an input, an output or a script) is not read as another: it was an identity path then, and
every run that recorded it, older or not, keeps matching it by text even after a rename or a new
link changed what it is now. A run's kit is its `env.kit_version` in PEP 440 order; a record
without one (a run still running, or one whose environment could not be recorded, included), or
with one this reader cannot order, is an older kit's. A match made through such a reading — a
producer candidate, a Visualization version under a logical key — is `inferred`: the judgment says
so (`basis: inferred`, `inferred_candidates`) and it is never `observed` and never a confirmed link.
Any two records that hold the same path text still match as observed. Several runs found so stay
`ambiguous`. The records are not rewritten. Runs of PATH_IDENTITY_SINCE or later are read as
recorded.

The rules are those of ADR-0006 (docs/decisions/0006-common-record-contract.md), checked against the
examples under tests/fixtures/records/. Readings this module makes where the decision is silent:

- An event target of a `lab.event/1` reference (`refs.events`, `refs.resolves`) or of a bound
  `analytics.event/2` or Report manifest reference (type `event`, `analysis_focus`,
  `metric_definition`, `reuse_proposal`) is found only at a seq below the referring record's (its
  own seq; a manifest's `as_of_seq` + 1, or its completion event). A target at or after that seq,
  or a line without a seq, is `missing` (`record_absent`): it did not exist, or is not shown to have
  existed, when the reference was written. This is how section 4 step 2 ("find the target record")
  is read here. Neither writer checked those targets, so a mistyped id would otherwise become a
  confirmed link once the journal grew that far. Several lines with the id are `ambiguous` whatever
  their seqs.
- A typed event target must be a record of that type: `metric_definition` and `reuse_proposal` a
  record of that kind whose `details.metric_id` / `details.proposal_id` is the reference's, and
  `analysis_focus` a record of that kind (it has no logical id, section 2). Anything else is
  `missing` (`record_absent`), as a run's file is not another run's target.
- A `lab.event/1` `refs.runs` run whose `started_at` is after the event's `ts` (as instants) is
  `missing` (`record_absent`); when either time cannot be read, or one is naive and the other
  aware, nothing is excluded. A `kind=run` event's `refs.charts` are bound only when its `refs.runs`
  names exactly one run; with several they are `ambiguous` (`<run_id>/<visualization_id>` of each).
- The state replay changes a record's state only through a reference judged as above that resolves
  to one record before the event, with a bound version (`author` or `inferred`). Records that share
  an id each have their own state under `journal:<line>`; a reference to that id changes neither.
- A cycle among producers is a walk that comes back to a run still on its own path. A run reached
  twice through two producers (a diamond) is not a cycle.

Costs that grow with the project: every judgment is built once per read and kept (only one event's
when only its references are asked for, as a screen showing a few records asks); an unbound
reference lists every candidate version before it, so the judgments grow as references times
versions of a key (a screen shows a count and where to read the rest, CONTRACTS A1); `upstream()`
of every run of a producer chain returns sets that grow with the chain; an older kit's path is given
to `legacy_path` once per distinct path per read, and only when a judgment matches paths.

A producer judgment is `{run_id, path, sha256, producer, candidates, basis}`, with `read_as` (the
identity path an older kit's input path is read as) and `inferred_candidates` (the candidates found
only through such a reading, of the input's path or of their output's) when there are any. Its
basis is `observed` (same path and hash as a successful run's output), or `inferred` when a
candidate was found only through an older kit's path. It says which runs could have written those
bytes; it is never a confirmed link and never counted as one.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

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
# ADR-0006 2: the logical id a typed event target holds in its details (analysis_focus has none).
EVENT_LOGICAL_IDS = {"metric_definition": "metric_id", "reuse_proposal": "proposal_id"}
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
# The kit version from which a run record holds a path by one identity across links, case and
# Unicode normalization (Issue #36, ADR-0006 9): core.PATH_IDENTITY_SINCE, which a test keeps equal.
PATH_IDENTITY_SINCE = "0.5.0"
# ADR-0006 1.3: a kit version is a PEP 440 public version in its normal form, without an epoch or a
# local part (the record schema's kit_version pattern).
VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)((?:\.(?:0|[1-9][0-9]*))*)(?:(a|b|rc)(0|[1-9][0-9]*))?"
                        r"(?:\.post(0|[1-9][0-9]*))?(?:\.dev(0|[1-9][0-9]*))?$")
PRE_RELEASES = {"a": 0, "b": 1, "rc": 2}


def version_key(text) -> tuple | None:
    """The PEP 440 order of a kit version written as ADR-0006 1.3 says, or None for any other text.
    Trailing zeros of the release do not count (`1.0` is `1`); a development release of a final
    comes before its pre-releases, a final after them, a post-release after the final, and a
    development release before the release it leads to (`0.3.0.dev1 < 0.3.0a1 < 0.3.0rc1 < 0.3.0 <
    0.3.0.post1`). A number too long for Python to read as an integer (sys.get_int_max_str_digits)
    is a text this reader cannot order: None."""
    match = VERSION_RE.match(text) if isinstance(text, str) else None
    if match is None:
        return None
    try:
        release = [int(match[1]), *(int(part) for part in match[2].split(".")[1:])]
        pre, post, dev = match[3], match[5], match[6]
        pre_number = int(match[4]) if pre is not None else 0
        post_number = int(post) if post is not None else 0
        dev_number = int(dev) if dev is not None else 0
    except ValueError:
        return None
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    if pre is not None:
        pre_key = (1, PRE_RELEASES[pre], pre_number)
    else:
        pre_key = (0, 0, 0) if dev is not None and post is None else (2, 0, 0)
    post_key = (0, 0) if post is None else (1, post_number)
    dev_key = (1, 0) if dev is None else (0, dev_number)
    return tuple(release), pre_key, post_key, dev_key


def older_kit(version) -> bool:
    """Whether a run record's `env.kit_version` is a kit's before PATH_IDENTITY_SINCE: a version
    below it, or none this reader can order (missing, not a string, another form)."""
    key = version_key(version)
    return key is None or key < version_key(PATH_IDENTITY_SINCE)


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


def _script(run: dict) -> dict:
    """A run record's `script`, or {} when a hand-edited record holds something else there."""
    script = run.get("script")
    return script if isinstance(script, dict) else {}


def _inside(rel) -> bool:
    """A relative path that stays under the directory it is joined to, read as POSIX and as Windows:
    no root, no drive (`C:x.md` included) and no `..`."""
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        return False
    return all(not pure.anchor and ".." not in pure.parts
               for pure in (PurePosixPath(rel), PureWindowsPath(rel)))


def _file_name(name) -> bool:
    """A bare file name on every platform: its own last part, with no drive."""
    if not isinstance(name, str) or name in ("", ".", "..") or "\x00" in name:
        return False
    return all(pure.name == name and not pure.drive for pure in (PurePosixPath(name), PureWindowsPath(name)))


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
        self._event_at: dict[int, Event] = {}
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
                self._event_at[number] = self.events[-1]
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

    def event_target(self, type_, ref: dict, before: int) -> tuple:
        """(state, reason, candidates) of step 2 of ADR-0006 4 for an event-family target, as the
        reference judgments and the state replay both read it. Several lines with the id are
        `ambiguous` whatever their seqs. One line is found only when its seq is below `before`, the
        referring record's boundary; a line at or after it, or without a seq, did not exist (or is
        not shown to have existed) when the reference was written: `missing` (`record_absent`). A
        typed target must be a record of that type with the reference's logical id."""
        event_id = ref.get("event_id")
        lines = self.lines_of.get(event_id, []) if isinstance(event_id, str) else []
        if len(lines) > 1:
            return "ambiguous", None, [f"journal:{n}" for n in lines]
        seq = self.seq_of.get(lines[0]) if lines else None
        if seq is None or seq >= before:
            return "missing", "record_absent", None
        if self.is_unsupported(lines[0]):
            return "unsupported", None, None
        target = self._event_at[lines[0]]
        if type_ != "event" and target.kind != type_:
            return "missing", "record_absent", None
        field = EVENT_LOGICAL_IDS.get(type_)
        if field is not None:
            details = target.value.get("details")
            held = _str(details.get(field)) if isinstance(details, dict) else None
            if held is None or held != ref.get(field):
                return "missing", "record_absent", None
        return "resolved", None, None

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
    """Every record's state from one pass over the journal in seq order (line order among equal
    seqs). An event applies to the states before it, its own included only after its references
    are applied, and a later event never undoes a transition applied earlier.

    A reference changes a state only when it resolves, as `references()` judges it, to one record
    before the event (seq below the event's, or below the next seq for a line without one) with a
    bound version; anything else (absent, later, several lines with its id, the wrong type, unbound,
    or a record without a state) changes nothing. Records that share an id each get their own state
    under `journal:<line>`."""

    def __init__(self, journal: Journal):
        self.states: dict[str, dict] = {}
        # record_update events that changed no state, each with why: the state of its target's
        # judgment (`missing`, `ambiguous`, `unsupported`, `version_unbound`), `no_state` (the target
        # has none) or `not_one_target` (not exactly one `updates` reference). Shown, never applied.
        self.unapplied: list[dict] = []
        for event in journal.in_order():
            before = event.seq if event.seq is not None else journal.next_seq
            name = journal.source_of(event)
            for relation, ref in self._links(event):
                state, _ = self._target(journal, ref, before)
                if state is None:
                    continue
                if relation == "supersedes" and state["group"] == "content":
                    state["by"].add(name)
                elif relation == "resolves" and state["group"] == "question":
                    state["resolved"] = True
            if event.kind == "record_update" and event.schema == EVENT_V2:
                self._update(journal, event, name, before)
            if event.kind in CONTENT_KINDS:
                self.states[name] = {"group": "content", "withdrawn": False, "by": set(), "mismatched": []}
            elif event.kind in QUESTION_KINDS:
                self.states[name] = {"group": "question", "resolved": False, "mismatched": []}
            elif event.kind == "reuse_proposal" and event.schema == EVENT_V2:
                self.states[name] = {"group": "reuse", "stage": "proposed", "mismatched": []}
            elif event.kind == "report_revision" and event.schema == EVENT_V2:
                for ref in _dicts(event.refs):
                    if _known_ref(ref) and ref["type"] == "report_revision" and ref["relation"] == "about":
                        key = report_key(ref.get("report_id"), ref.get("revision_id"))
                        self.states.setdefault(key, {"group": "report", "withdrawn": False, "mismatched": [],
                                                     "seq": event.seq})

    @staticmethod
    def _links(event: Event) -> list[tuple[str, dict]]:
        """(relation, reference) of the references that can close or correct a record."""
        if event.schema == EVENT_V1:
            refs = event.refs if isinstance(event.refs, dict) else {}
            return [("resolves", {"type": "event", "event_id": value, "relation": "resolves",
                                  "basis": "declared", "version_binding": "author"})
                    for value in _strings(refs.get("resolves")) if isinstance(value, str)]
        return [(ref["relation"], ref) for ref in _dicts(event.refs)
                if _known_ref(ref) and ref["type"] in EVENT_TARGETS
                and ref["relation"] in ("supersedes", "resolves")]

    def _target(self, journal: Journal, ref: dict, before: int) -> tuple[dict | None, str | None]:
        """(the state a reference reaches, None) or (None, why it reaches none)."""
        if not _known_ref(ref):
            return None, "unsupported"
        if ref["version_binding"] == "none":
            return None, "version_unbound"
        if ref["type"] in EVENT_TARGETS:
            state, _, _ = journal.event_target(ref["type"], ref, before)
            if state != "resolved":
                return None, state
            found = self.states.get(ref["event_id"])
        elif ref["type"] == "report_revision":
            found = self.states.get(report_key(ref.get("report_id"), ref.get("revision_id")))
            if found is None or found["seq"] is None or found["seq"] >= before:
                return None, "missing"
        else:
            return None, "no_state"
        return (found, None) if found is not None else (None, "no_state")

    def _update(self, journal: Journal, event: Event, name: str, before: int) -> None:
        updates = [ref for ref in _dicts(event.refs) if ref.get("relation") == "updates"]
        state, why = None, "not_one_target"
        if len(updates) == 1:
            state, why = self._target(journal, updates[0], before)
        if state is None:
            self.unapplied.append({"from": name, "reason": why})
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
    in completion order, the successful runs that output each path and hash, the import records of
    each path, the producer candidates of each run) are built once, on first use, and kept."""

    def __init__(self, root: Path, journal: Journal, *, alive, sha, legacy_path=None):
        self.root = root
        self.journal = journal
        self._sha = sha
        self._legacy_path = legacy_path
        self._older: dict[str, bool] = {}
        self._read_as: dict[str, str | None] = {}
        self._recorded_outputs: set[str] | None = None
        self._recorded_now: set[str] | None = None
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
        self._imports_of: dict[str, list[dict]] | None = None
        self._references: list[dict] | None = None
        self._by_source: dict[str, list[dict]] = {}
        self._one_source: dict[str, list[dict]] = {}
        self._edges_of: dict[str, list[str]] = {}
        self._instants: dict[str, datetime | None] = {}
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

    # --- paths an older kit recorded (ADR-0006 9) --------------------------------------------------

    def older_kit(self, run_id) -> bool:
        """Whether a run this reader read was recorded by a kit before PATH_IDENTITY_SINCE."""
        run = self._runs.get(run_id) if isinstance(run_id, str) else None
        if run is None:
            return False
        if run_id not in self._older:
            env = run.get("env")
            self._older[run_id] = older_kit(env.get("kit_version") if isinstance(env, dict) else None)
        return self._older[run_id]

    def read_as(self, run_id, path) -> str | None:
        """The identity path a path that run recorded is read as, when the run is an older kit's,
        no run of PATH_IDENTITY_SINCE or later recorded the same text, and `legacy_path` gives
        another path that names an entry now or a path some run recorded as an output (an
        inference, never an observation); else None, and the path is read as recorded."""
        if self._legacy_path is None or not isinstance(path, str) or not self.older_kit(run_id):
            return None
        if path not in self._read_as:
            self._read_as[path] = self._mapped(path)
        return self._read_as[path]

    def _mapped(self, path: str) -> str | None:
        if path in self._recorded_by_current_kits():
            # An identity path when that run recorded it: every run that recorded this text keeps
            # matching it by text, as before, whatever it names now (a rename, a new link since).
            return None
        try:
            mapped = self._legacy_path(path)
        except (OSError, ValueError):
            mapped = None
        if not isinstance(mapped, str) or mapped == path or not _inside(mapped):
            return None
        if mapped in self._outputs_recorded() or os.path.lexists(self.root / mapped):
            return mapped
        return None  # it names nothing anyone knows of: not invented

    def _recorded_by_current_kits(self) -> set[str]:
        """Every input, output and script path a readable run of PATH_IDENTITY_SINCE or later
        recorded, as recorded (once per read)."""
        if self._recorded_now is None:
            self._recorded_now = {
                path for run_id, run in self._runs.items() if not self.older_kit(run_id)
                for entry in (*_dicts(run.get("inputs")), *_dicts(run.get("outputs")), _script(run))
                if (path := _str(entry.get("path"))) is not None}
        return self._recorded_now

    def _outputs_recorded(self) -> set[str]:
        """Every output path a readable run recorded, as recorded."""
        if self._recorded_outputs is None:
            self._recorded_outputs = {path for run in self._runs.values()
                                      for out in _dicts(run.get("outputs"))
                                      if (path := _str(out.get("path"))) is not None}
        return self._recorded_outputs

    def _as_read(self, run_id, path):
        """`path` as this reader matches it: read_as when there is one, else as recorded."""
        return self.read_as(run_id, path) or path

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
        """`missing`, `content_changed` or `resolved` for the files a manifest names. A path that is
        not relative to the revision's content directory (absolute, with a drive, or with `..`)
        names no preserved file of it and is not hashed: `missing`."""
        states = set()
        for item in _dicts(manifest.get("files")):
            path = item.get("path")
            current = self._sha(self.root / directory / "content" / path) if _inside(path) else None
            states.add("missing" if current is None else "resolved" if current == item.get("sha256")
                       else "content_changed")
        for state in ("missing", "content_changed"):
            if state in states:
                return state
        return "resolved"

    # --- Visualization versions ------------------------------------------------------------------

    def _version_index(self) -> tuple[dict, dict, dict]:
        """Logical key -> [(completion seq, version, the key as its run recorded it)], Visualization
        id -> [(completion seq, script, version, the script as its run recorded it)], both in
        completion order, from the successful runs with a completion event; and the key an older
        kit's run recorded -> the logical key it is read as. A run's script path is read as
        read_as gives it, so an older kit's spelling of a script is the key of today's."""
        if self._versions is None:
            by_key: dict[str, list] = {}
            by_name: dict[str, list] = {}
            read_as: dict[str, str] = {}
            for run_id, run in self._runs.items():
                done = self.completion_seq(run_id)
                if run.get("status") != "ok" or done is None:
                    continue
                recorded = _str(_script(run).get("path")) or "?"
                script = self._as_read(run_id, recorded) if recorded != "?" else recorded
                for chart in _dicts(run.get("charts")):
                    chart_id = _str(chart.get("id"))
                    if chart_id is None:
                        continue
                    version = f"{run_id}/{chart_id}"
                    key, recorded_key = f"{script}::{chart_id}", f"{recorded}::{chart_id}"
                    by_key.setdefault(key, []).append((done, version, recorded_key))
                    by_name.setdefault(chart_id, []).append((done, script, version, recorded))
                    if key != recorded_key:
                        read_as[recorded_key] = key
            for table in (by_key, by_name):
                for versions in table.values():
                    versions.sort()
            self._versions = (by_key, by_name, read_as)
        return self._versions

    def chart_key(self, key):
        """The logical key a Visualization key is read as: the key an older kit's run recorded in
        another spelling of its script is today's (inferred); any other key is itself."""
        return self._version_index()[2].get(key, key) if isinstance(key, str) else key

    def _key_versions(self, key, before: int | None) -> tuple[list[str], list[str]]:
        """(versions, inferred versions) of a logical key, sorted: those whose completion event
        comes before seq `before` (all of them when None). A version is inferred when its run
        recorded the key in another spelling than `key`: it is the key's only through an older
        kit's path."""
        versions = self._version_index()[0].get(self.chart_key(key), []) if isinstance(key, str) else []
        end = len(versions) if before is None else bisect.bisect_left(versions, (before,))
        found = sorted(version for _, version, _ in versions[:end])
        inferred = sorted(version for _, version, recorded in versions[:end] if recorded != key)
        return found, inferred

    def visualization_versions(self, key: str, before: int | None = None) -> list[str]:
        """`<run_id>/<visualization_id>` of the versions of a logical key whose completion event comes
        before seq `before` (all of them when None), sorted. A key an older kit's run recorded in
        another spelling of its script is read as today's (chart_key)."""
        return self._key_versions(key, before)[0]

    def inferred_versions(self, key: str, before: int | None = None) -> list[str]:
        """The versions of visualization_versions that are the key's only through an older kit's
        path (basis inferred)."""
        return self._key_versions(key, before)[1]

    def _by_name(self, name, before: int) -> list[tuple[str, str, bool]]:
        """(script, version, inferred) of the versions with this Visualization id before `before`:
        inferred when the run recorded its script in another spelling than the one it is read as."""
        if not isinstance(name, str):
            return []
        versions = self._version_index()[1].get(name, [])
        end = bisect.bisect_left(versions, (before,))
        return [(script, version, script != recorded) for _, script, version, recorded in versions[:end]]

    # --- reference judgments (ADR-0006 3 and 4) --------------------------------------------------

    def references(self, source: str | None = None) -> list[dict]:
        """One judgment for every reference of every readable event and saved Report manifest, in
        journal order and then manifest order; only those of `source` (`e<seq>`, `journal:<line>` or
        `report:<report_id>/<revision_id>`) when given.

        Asked for the references of one event before every judgment is made, it judges that event's
        references alone: one record's judgments do not depend on another's, so they are the same
        judgments, and a screen that shows a few records does not judge the whole project."""
        if source is not None and self._references is None:
            event = self._event_of(source)
            if event is not None:
                if source not in self._one_source:
                    before = event.seq if event.seq is not None else self.journal.next_seq
                    self._one_source[source] = (
                        self._judge_v1(event, source, before) if event.schema == EVENT_V1
                        else self._judge_v2(event.refs, source, event.line, before))
                return list(self._one_source[source])
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

    def _event_of(self, source: str) -> Event | None:
        """The readable event `source` names as `source_of` names it, or None (a Report revision, a
        line that holds no readable event, an id on several lines given as `e<seq>`)."""
        if source.startswith("journal:"):
            number = source[len("journal:"):]
            event = self.journal._event_at.get(int(number)) if number.isdigit() else None
        else:
            lines = self.journal.lines_of.get(source, [])
            event = self.journal._event_at.get(lines[0]) if len(lines) == 1 else None
        return event if event is not None and self.journal.source_of(event) == source else None

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
                  candidates=None, inferred=None) -> dict:
        out = {"from": source, "line": line, "ref": ref, "type": type_, "relation": relation,
               "target": target, "state": state, "version_binding": binding, "basis": basis,
               "confirmed": confirmed(binding, basis, state)}
        if reason is not None:
            out["reason"] = reason
        if candidates is not None:
            out["candidates"] = candidates
        if inferred:
            # Candidates that are the target's only through an older kit's path (ADR-0006 9).
            out["inferred_candidates"] = inferred
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
            inferred = None
            if not _known_ref(ref):
                state, reason, candidates = "unsupported", None, None
            elif binding == "none":
                state, reason, candidates, inferred = self._unbound(ref, before)
            else:
                state, reason, candidates = self._resolve(ref, before)
            out.append(self._judgment(source, line, {"index": index}, ref.get("type"),
                                      ref.get("relation"), target, binding, basis, state, reason,
                                      candidates, inferred))
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
            if self._started_after(value, event):
                judged = ("missing", "record_absent", None)
            else:
                judged = self._resolve({"type": "run", "run_id": value})
            return ("run", "about" if about else "cites", {"run_id": value}, "author", "declared", *judged)
        if field in ("events", "resolves"):
            relation = "resolves" if field == "resolves" else "cites"
            return ("event", relation, {"event_id": value}, "author", "declared",
                    *self.journal.event_target("event", {"event_id": value}, before))
        if field == "context":
            found = self._context_file(value) is not None
            return ("context_version", "about" if event.kind == "context" else "cites",
                    {"version": value}, "author", "declared",
                    *(("resolved", None, None) if found else ("missing", "record_absent", None)))
        if field == "charts" and event.kind == "run":
            runs = sorted({r for r in _strings(event.refs.get("runs", [])) if isinstance(r, str)})
            script, _, chart_id = value.rpartition("::")
            run_id = runs[0] if len(runs) == 1 else None
            target = {"run_id": run_id, "visualization_id": chart_id}
            run = self._runs.get(run_id)
            if len(runs) > 1:
                # The runner names one run; a line naming several does not say which run's chart.
                judged = ("ambiguous", None, [f"{r}/{chart_id}" for r in runs])
            elif run is not None and (_script(run).get("path") != script or self._started_after(run_id, event)):
                judged = ("missing", "record_absent", None)
            else:
                judged = self._resolve({"type": "visualization_version", "run_id": run_id,
                                        "visualization_id": chart_id})
            return ("visualization_version", "about", target, "author", "observed", *judged)
        if field == "charts":
            return ("visualization_version", "cites", {"key": value}, "none", "declared",
                    "version_unbound", None, *self._key_versions(value, before))
        if field == "data" and event.kind == "import":
            # ADR-0006 6: the path of the import record the event completes; left unbound (ADR-0006 4).
            return ("dataset_import", "about", {"path": value}, "none", "declared", "version_unbound",
                    None, None)
        if field == "data":
            # ADR-0006 4: a name or path, left unbound, with no candidates.
            return ("data_observation", "about" if event.kind == "run" else "cites",
                    {"key": value}, "none", "declared", "version_unbound", None, None)
        return None, "cites", {"token": value}, "none", "declared", "unknown", "unresolved_name", None

    def _unbound(self, ref: dict, before: int) -> tuple:
        """ADR-0006 3: a reference left unbound stays unbound when it is read. Only Visualization
        versions are listed as candidates; the Issue that first writes an unbound reference of
        another type adds its candidates. (state, reason, candidates, inferred candidates)."""
        if ref["type"] == "visualization_version":
            if isinstance(ref.get("key"), str):
                return "version_unbound", None, *self._key_versions(ref["key"], before)
            if "token" in ref:
                found = self._by_name(ref["token"], before)
                scripts = {script for script, _, _ in found}
                if not scripts:
                    return "unknown", "unresolved_name", None, None
                state = "ambiguous" if len(scripts) > 1 else "version_unbound"
                return (state, None, sorted(version for _, version, _ in found),
                        sorted(version for _, version, inferred in found if inferred))
            return "unknown", "unresolved_name", None, None
        if any(field in ref for field in LOGICAL_KEYS):
            return "version_unbound", None, None, None
        return "unknown", "unresolved_name", None, None

    def _started_after(self, run_id, event: Event) -> bool:
        """Whether a run started after the event that names it, start and event times compared as
        instants. A time that cannot be read, or a naive time against an aware one, excludes
        nothing."""
        run = self._runs.get(run_id) if isinstance(run_id, str) else None
        if run is None:
            return False
        return not _not_after(self._at(run.get("started_at")), self._at(event.recorded_at))

    def _at(self, text) -> datetime | None:
        """`_instant` of a time, parsed once per distinct text per read."""
        if not isinstance(text, str):
            return None
        if text not in self._instants:
            self._instants[text] = _instant(text)
        return self._instants[text]

    def _context_file(self, name) -> Path | None:
        """A CONTEXT version by file name, in .analytics/context/ and then .lab/context/. A name that
        is not a bare file name on every platform (a separator, a drive as in `C:x.md`, `.`, `..`)
        names no version."""
        if not _file_name(name):
            return None
        for directory in CONTEXT_DIRS:
            path = self.root / directory / name
            if path.is_file():
                return path
        return None

    def _imports(self, path) -> list[dict]:
        """The import records of a path, in file-name order (one index per read)."""
        if self._imports_of is None:
            index: dict[str, list[dict]] = {}
            for _, meta in self._metas:
                if isinstance(meta.get("path"), str):
                    index.setdefault(meta["path"], []).append(meta)
            self._imports_of = index
        return self._imports_of.get(path, []) if isinstance(path, str) else []

    def _resolve(self, ref: dict, before: int | None = None) -> tuple:
        """(state, reason, candidates) of one bound reference, in the order of ADR-0006 4. `before`
        is the referring record's boundary for an event target (the whole journal when None)."""
        kind = ref["type"]
        if kind in EVENT_TARGETS:
            return self.journal.event_target(kind, ref, self.journal.next_seq if before is None else before)
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
            metas = self._imports(path)
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
        script = _script(run)
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

    def _output_index(self) -> dict[tuple, list[tuple[str, datetime | None, str]]]:
        """(path as read, sha256) -> [(run, start, the path as the run recorded it)] of the outputs
        of the successful runs, an older kit's path read as read_as gives it."""
        if self._outputs is None:
            index: dict[tuple, list] = {}
            for run_id, run in self._runs.items():
                if run.get("status") != "ok":
                    continue
                started = _instant(run.get("started_at"))
                for out in _dicts(run.get("outputs")):
                    path, digest = _str(out.get("path")), _str(out.get("sha256"))
                    if out.get("change") != "deleted" and path and digest:
                        index.setdefault((self._as_read(run_id, path), digest), []).append(
                            (run_id, started, path))
            self._outputs = index
        return self._outputs

    def producer_candidates(self, path: str, sha256: str,
                            consumer: str | None = None) -> tuple[list[str], list[str]]:
        """(candidates, inferred candidates), sorted: the successful runs that output `path` with
        these bytes and, of them, those found only through an older kit's path — the consumer's
        `path` or the run's output read as another identity path (read_as). With a consumer, `path`
        is a path that run recorded, and the candidates are not that run and none that started
        after it (start times compared as instants); without one, `path` is an identity path."""
        if not (isinstance(path, str) and isinstance(sha256, str)):
            return [], []
        started = _instant((self._runs.get(consumer) or {}).get("started_at")) if consumer else None
        key = self._as_read(consumer, path) if consumer else path
        exact: set[str] = set()
        through: set[str] = set()
        for run_id, at, recorded in self._output_index().get((key, sha256), []):
            if run_id != consumer and (consumer is None or _not_after(at, started)):
                (exact if recorded == path else through).add(run_id)
        return sorted(exact | through), sorted(through - exact)

    def producers(self, path: str, sha256: str, consumer: str | None = None) -> list[str]:
        """The successful runs that output `path` with these bytes, sorted (producer_candidates).
        With a consumer, not that run and none that started after it (start times compared as
        instants)."""
        return self.producer_candidates(path, sha256, consumer)[0]

    def producer(self, run_id: str, path: str) -> dict | None:
        """The producer judgment of one input of a run: `resolved` (one candidate), `ambiguous`
        (several, all listed), `none`, or `unknown` (the input has no hash). Never the last run.
        Its basis is `inferred` when a candidate was found only through an older kit's path."""
        run = self._runs.get(run_id) if isinstance(run_id, str) else None
        entries = [e for e in _dicts((run or {}).get("inputs")) if e.get("path") == path]
        if not entries:
            return None
        sha256 = _str(entries[0].get("sha256"))
        read_as = self.read_as(run_id, path)
        if not sha256:
            judged = {"run_id": run_id, "path": path, "sha256": None, "producer": "unknown",
                      "candidates": [], "basis": None}
        else:
            candidates, inferred = self.producer_candidates(path, sha256, consumer=run_id)
            state = "none" if not candidates else "resolved" if len(candidates) == 1 else "ambiguous"
            judged = {"run_id": run_id, "path": path, "sha256": sha256, "producer": state,
                      "candidates": candidates,
                      "basis": "inferred" if inferred else "observed" if candidates else None}
            if inferred:
                judged["inferred_candidates"] = inferred
        if read_as is not None:
            judged["read_as"] = read_as
        return judged

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
        """The producer candidates of every input of a run (once per run per read)."""
        if run_id not in self._edges_of:
            found: set[str] = set()
            for entry in _dicts((self._runs.get(run_id) or {}).get("inputs")):
                found.update(self.producers(entry.get("path"), entry.get("sha256"), consumer=run_id))
            self._edges_of[run_id] = sorted(found)
        return self._edges_of[run_id]

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


def read(root, *, alive, sha, legacy_path=None) -> ReadModel:
    """Read the project at `root` as it is. The files read are compared before and after; when they
    differ the read is made again, twice at most, and then the model says `updating` and keeps every
    source_revision it observed. `legacy_path` reads the paths of an older kit's runs (module
    docstring); without it they are read as recorded."""
    root = Path(root)
    observed: list[str] = []
    for _ in range(3):
        before = _snapshot(root)
        model = ReadModel(root, read_journal(root), alive=alive, sha=sha, legacy_path=legacy_path)
        after = _snapshot(root)
        observed.append(_token(after, model.as_of_seq))
        if before == after:
            break
    else:
        model.updating = True
    model.observed_revisions = observed
    model.source_revision = observed[-1]
    return model
