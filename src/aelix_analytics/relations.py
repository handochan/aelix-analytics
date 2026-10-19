"""The records of a project read as ADR-0006 says: relations, reference states and record states.
Standard library only.

This module reads; it never writes a record, a cache or an index. It imports neither `core` nor
`_platform` (nor any other package module), so it can be loaded by its file path alone.
The pure metric payload contracts below also have no I/O or host dependencies (ADR-0011).
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

import base64
import bisect
import copy
import difflib
import hashlib
import json
import math
import os
import re
import stat
import unicodedata
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

# --- pure metric payload contracts (ADR-0011; no I/O or package imports) ---

METRIC_KINDS = ("metric_definition", "analysis_focus", "metric_observation")
METRIC_SINCE = "0.9.0"
METRIC_EVENT_ID_RE = re.compile(r"e[1-9][0-9]*")
METRIC_RUN_ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[a-z0-9][a-z0-9-]{0,60}")
METRIC_STATE_RELATIONS = frozenset({"supersedes", "updates", "resolves"})


class MetricContractError(ValueError):
    pass


def _metric_string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise MetricContractError(f"{name} is a non-empty string")


def _metric_strings(value, name):
    if not isinstance(value, list):
        raise MetricContractError(f"{name} is a list of strings")
    for item in value:
        _metric_string(item, name)


def _metric_event(value, name):
    if not isinstance(value, str) or not METRIC_EVENT_ID_RE.fullmatch(value):
        raise MetricContractError(f"{name} is an exact event id, e.g. e3")


def _metric_definitions(value, name, *, role=False):
    if not isinstance(value, list):
        raise MetricContractError(f"{name} is a list of exact metric definitions")
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise MetricContractError(f"{name} entries are objects")
        _metric_string(item.get("metric_id"), f"{name}.metric_id")
        _metric_event(item.get("event_id"), f"{name}.event_id")
        if role:
            _metric_string(item.get("role"), f"{name}.role")
        key = (item["metric_id"], item["event_id"])
        if key in seen:
            raise MetricContractError(f"{name} contains a duplicate definition")
        seen.add(key)


def validate_metric(kind, details) -> dict:
    """A private finite-JSON copy, or MetricContractError. No canonical record is mutated."""
    if kind not in METRIC_KINDS:
        raise MetricContractError(f"{kind!r} is not a structured metric record")
    if not isinstance(details, dict) or details.get("contract") != f"{kind}/1":
        raise MetricContractError(f"details.contract is {kind}/1")
    try:
        def json_value(value):
            if value is None or type(value) in (str, int, float, bool):
                return
            if isinstance(value, list):
                for item in value:
                    json_value(item)
                return
            if isinstance(value, dict) and all(isinstance(k, str) for k in value):
                for item in value.values():
                    json_value(item)
                return
            raise ValueError("not a JSON type")

        json_value(details)
        json.dumps(details, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise MetricContractError("details holds finite JSON values encoded as UTF-8") from exc
    for name in ("change_reason",):
        _metric_string(details.get(name), name)
    if kind in ("metric_definition", "analysis_focus"):
        _metric_strings(details.get("unresolved"), "unresolved")
    if kind == "metric_definition":
        for name in ("metric_id", "name"):
            _metric_string(details.get(name), name)
        if details.get("status") not in ("draft", "specified"):
            raise MetricContractError("status is draft or specified, not an analysis-check verdict")
        if not isinstance(details.get("definition"), dict):
            raise MetricContractError("definition is a JSON object (an empty draft is allowed)")
        _metric_definitions(details.get("derived_from"), "derived_from")
        if "previous_definition" in details:
            _metric_event(details["previous_definition"], "previous_definition")
        if any(p["metric_id"] == details["metric_id"] for p in details["derived_from"]):
            raise MetricContractError("a derived metric has a different logical id from its parents")
    elif kind == "analysis_focus":
        for name in ("goal", "question", "activity", "next_check"):
            _metric_string(details.get(name), name)
        _metric_definitions(details.get("metrics"), "metrics", role=True)
        if "previous_focus" in details:
            _metric_event(details["previous_focus"], "previous_focus")
    else:
        _metric_string(details.get("metric_id"), "metric_id")
        _metric_event(details.get("definition_event_id"), "definition_event_id")
        if "run_id" in details and (not isinstance(details["run_id"], str)
                                    or not METRIC_RUN_ID_RE.fullmatch(details["run_id"])):
            raise MetricContractError("run_id is an exact recorded run id")
        if not isinstance(details.get("scope"), dict):
            raise MetricContractError("scope is a JSON object (empty means unspecified scope)")
        if "result" not in details:
            raise MetricContractError("result is explicit, including null for a result_ref")
        if details["result"] is None and "result_ref" not in details:
            raise MetricContractError("a null result requires result_ref")
        if "result_ref" in details:
            ref = details["result_ref"]
            if not isinstance(ref, dict) or ref.get("type") not in (
                    "data_observation", "visualization_version"):
                raise MetricContractError("result_ref is a run output or Visualization version")
        if "recomputed_from" in details:
            _metric_event(details["recomputed_from"], "recomputed_from")
    return copy.deepcopy(details)


def metric_projection(events, states) -> dict:
    """All tagged versions and their current state, reconstructed from original event objects."""
    result = {"definitions": [], "focus_versions": [], "observations": [], "unstructured": [],
              "invalid": [], "current_focus": None, "focus_state": "absent"}
    for event in events:
        kind = event.get("kind")
        if kind not in METRIC_KINDS:
            continue
        details = event.get("details")
        if not isinstance(details, dict) or "contract" not in details:
            result["unstructured"].append(event.get("id"))
            continue
        try:
            payload = validate_metric(kind, details)
            problem = metric_event_problem(kind, payload, event.get("refs"))
            if problem:
                raise MetricContractError(problem)
        except MetricContractError as exc:
            result["invalid"].append({"event_id": event.get("id"), "reason": str(exc)})
            continue
        item = {"event_id": event.get("id"), "details": payload,
                "state": (states.get(event.get("id")) or {}).get("state", "unknown")}
        collection = {"metric_definition": "definitions", "analysis_focus": "focus_versions",
                      "metric_observation": "observations"}[kind]
        result[collection].append(item)
    focuses = [f for f in result["focus_versions"] if f["state"] == "current"]
    if len(focuses) == 1:
        result.update(current_focus=focuses[0], focus_state="current")
    elif focuses:
        result["focus_state"] = "conflict"
    return result


def metric_event_problem(kind, details, refs) -> str | None:
    """Tagged payloads may change state only through their one authoritative prior version.

    None for historical unstructured records, whose original replay contract remains intact.
    Invalid or unsupported tagged records remain readable but may change no target state.
    """
    if kind not in METRIC_KINDS or not isinstance(details, dict) or "contract" not in details:
        return None
    try:
        validate_metric(kind, details)
    except MetricContractError as exc:
        return str(exc)
    state_refs = [ref for ref in refs or () if isinstance(ref, dict)
                  and ref.get("relation") in METRIC_STATE_RELATIONS]
    previous = details.get("previous_definition" if kind == "metric_definition" else "previous_focus")
    if kind == "metric_observation":
        previous = None
    if not previous:
        return "a structured record has no caller state-changing references" if state_refs else None
    if len(state_refs) != 1:
        return "a structured revision has exactly its previous-version supersedes reference"
    ref = state_refs[0]
    wanted = {"type": kind, "event_id": previous, "relation": "supersedes", "version_binding": "author"}
    if kind == "metric_definition":
        wanted["metric_id"] = details["metric_id"]
    if any(ref.get(k) != v for k, v in wanted.items()) or ref.get("basis") not in ("declared", "observed"):
        return "the state-changing reference does not match the structured previous version"
    return None


# The version of this reader's rules, part of every source_revision it gives (ADR-0006 5). 2 since
# Issue #13 PR 1: analytics.run/2 and analytics.dataset/1 are read, and data/imported/ with them. 3
# since its PR 2: a migrated project's older paths are read through the migration's mapping
# (core.mapped_identity, design 5.4), and analytics.migration/1 is a family this reader knows.
# 4 since ADR-0011: invalid/unsupported tagged metric content cannot change another record state.
# 5 since Issue #15: CONTEXT version stat changes and bounded historical query knowledge.
# 6 since Issue #20 PR3: publication bindings resolve independently of current package folders.
READER_FORMAT = 6

EVENT_V1 = "lab.event/1"
EVENT_V2 = "analytics.event/2"
RUN_V1 = "lab.run/1"
# The run record of the new writer (design 2.6, OQ5): a Visualization is visualizations[] with its
# visualization_id (the same values a lab.run/1 run's charts[] ids are), its stored bytes' sha256 and
# its attached data's data_sha256; a rerun's comparison is reproduction.visualizations.
RUN_V2 = "analytics.run/2"
DATASET_V1 = "lab.dataset/1"
# The import record of the new writer (design 2.7): lab.dataset/1's fields, under data/imported/.
DATASET_V2 = "analytics.dataset/1"
REPORT_V1 = "analytics.report_revision/1"
PUBLICATION_V1 = "analytics.publication/1"
PUBLICATION_WRITER_SINCE = "0.24.0"  # provisional; assigned by actual integration order
PUBLICATION_ID = re.compile(r"pub-[0-9a-hjkmnp-tv-z]{26}\Z")
# A migration's plan, mapping and report (design 2.7 and 6.8), told apart by their `record`.
MIGRATION_V1 = "analytics.migration/1"
# Where import records are, each with the family it holds (design 2.3).
IMPORT_DIRS = (("data/imported", DATASET_V2), ("data/raw", DATASET_V1))
# ADR-0006 1.4: the family versions a reader of the contract knows.
KNOWN = frozenset({"lab.project/1", RUN_V1, EVENT_V1, DATASET_V1, "analytics.project/2", EVENT_V2,
                   REPORT_V1, RUN_V2, DATASET_V2, MIGRATION_V1})
FAMILIES = frozenset({name.rpartition("/")[0] for name in KNOWN})
KINDS = {
    # The kinds the /1 writer accepts (core.EVENT_KINDS).
    EVENT_V1: frozenset({"project", "import", "run", "context", "finding", "decision", "assumption",
                         "question", "todo", "note", "gap", "resolve"}),
    # ADR-0006 1.2 and the record schema.
    EVENT_V2: frozenset({"project", "import", "run", "context", "finding", "decision", "assumption",
                         "question", "todo", "note", "resolve", "workflow_feedback", "analysis_focus",
                         "metric_definition", "metric_observation", "record_update", "checkpoint",
                         "reuse_proposal", "reuse_notification", "reuse_mode", "report_revision",
                         "publication", "publish_receipt", "asset_candidate", "asset_check", "asset_effect",
                         "asset_notification", "asset_mode"}),
}
REF_TYPES = frozenset({"event", "run", "visualization_version", "data_observation", "dataset_import",
                       "code_snapshot", "context_version", "metric_definition", "analysis_focus",
                       "reuse_proposal", "report_revision", "publication", "asset_candidate"})
RELATIONS = frozenset({"supports", "contradicts", "cites", "uses", "derived_from", "generated_by",
                       "about", "includes", "resolves", "supersedes", "updates"})
BASES = frozenset({"observed", "declared", "inferred", "unverified"})
BINDINGS = frozenset({"author", "inferred", "none"})
EVENT_TARGETS = frozenset({"event", "analysis_focus", "metric_definition", "reuse_proposal", "asset_candidate"})
# ADR-0006 2: the logical id a typed event target holds in its details (analysis_focus has none).
EVENT_LOGICAL_IDS = {"metric_definition": "metric_id", "reuse_proposal": "proposal_id", "asset_candidate": "asset_id"}
# Fields that hold a logical key when a reference is left unbound (ADR-0006 2 and 3).
LOGICAL_KEYS = ("key", "path", "metric_id", "proposal_id", "report_id", "asset_id")
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


# How a run that did not succeed ended (a run's `status`); `incomplete` and `running` have not ended.
ENDED_FAILED = ("error", "timeout", "cancelled")


def _order(text) -> tuple:
    """A time as a key that orders instants: an aware time by its instant, anything else after
    every aware time (a naive time cannot be compared with one)."""
    at = _instant(text)
    return (0, at.timestamp()) if at is not None and at.tzinfo is not None else (1, 0.0)


def _input_change(before: tuple, after: tuple) -> dict:
    """The input paths read with other bytes or newly (`changed`), and those no longer read
    (`removed`), between two runs' (path, sha256) pairs."""
    held = set(before)
    return {"changed": sorted({path for path, digest in after if (path, digest) not in held}),
            "removed": sorted({path for path, _ in before} - {path for path, _ in after})}


def _line_changes(old: list[str], new: list[str]) -> tuple[int, int]:
    """(lines added, lines removed) from one text's lines to another's."""
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


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


# --- one view of a run's Visualizations (design 5.3, S13) -------------------------------------------

def run_visualizations(run) -> list[dict]:
    """A run's Visualizations, as one view whichever record holds them: a lab.run/1 run's charts[]
    (each named by `id`) or an analytics.run/2 run's visualizations[] (by `visualization_id`). Each
    is its record's fields as stored, and `id` (the Visualization id: the same values in both, the
    `chart(id=…)`/`visualize(id=…)` of one script), `key` (`<script path>::<id>`, the logical key) and
    `field` (where the record holds it: `charts` or `visualizations`). `sha256` and `data_sha256` are
    there only in an analytics.run/2 record. Every consumer of a run's Visualizations reads them
    here, so none reads one form only (tests/test_run_views.py's static check)."""
    if not isinstance(run, dict):
        return []
    v2 = run.get("schema") == RUN_V2
    field, id_field = ("visualizations", "visualization_id") if v2 else ("charts", "id")
    script = _str(_script(run).get("path")) or "?"
    out = []
    for item in _dicts(run.get(field)):
        vis_id = _str(item.get(id_field))
        out.append({**item, "id": vis_id, "key": f"{script}::{vis_id}", "field": field})
    return out


def run_reproduction(run) -> dict | None:
    """A rerun's comparison with the run it re-executed, as one view whichever record holds it, or
    None: `of`, `verdict`, `inputs_same`, `visualizations` (Visualization id -> its result: a
    lab.run/1 run's reproduction.charts, an analytics.run/2 run's reproduction.visualizations),
    `outputs` (path -> its result) and `field` (the name the record holds the Visualizations' results
    under). The values are as stored (`same data`, `missing`, `new`, …)."""
    rep = run.get("reproduction") if isinstance(run, dict) else None
    if not isinstance(rep, dict):
        return None
    field = "visualizations" if run.get("schema") == RUN_V2 else "charts"
    results = rep.get(field)
    outputs = rep.get("outputs")
    return {"of": rep.get("of"), "verdict": rep.get("verdict"), "inputs_same": rep.get("inputs_same"),
            "visualizations": dict(results) if isinstance(results, dict) else {},
            "outputs": dict(outputs) if isinstance(outputs, dict) else {}, "field": field}


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
        except (ValueError, RecursionError):
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
        self.data = data
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
        # Include uninterpretable rows: an unknown publication format must be unsupported,
        # rather than disappearing from logical-ID lookup as though no record existed.
        self._publications: dict[str, list[tuple[int, dict]]] = {}
        self._ordered: list[Event] | None = None
        self._replay: Replay | None = None
        top = 0
        for number, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except (ValueError, RecursionError):
                # RecursionError: a line nested deeper than the parser goes, which no kit writes; it is
                # damaged like a cut-off one, so it stops neither reading nor the next append (#49).
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
            if isinstance(value, dict) and value.get("kind") == "publication":
                details = value.get("details")
                pid = details.get("publication_id") if isinstance(details, dict) else None
                if isinstance(pid, str):
                    self._publications.setdefault(pid, []).append((number, value))
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
        if target.kind in ASSET_KINDS and asset_event_problem(target.value):
            return "unsupported", "invalid_asset_record", None
        if type_ != "event" and target.kind != type_:
            return "missing", "record_absent", None
        field = EVENT_LOGICAL_IDS.get(type_)
        if field is not None:
            details = target.value.get("details")
            held = _str(details.get(field)) if isinstance(details, dict) else None
            if held is None or held != ref.get(field):
                return "missing", "record_absent", None
        return "resolved", None, None

    def event_of(self, source: str) -> Event | None:
        """The readable event `source` names as `source_of` names it (`e<seq>` of an id one line
        holds, `journal:<line>`), or None: a line that holds no readable event, an id on several
        lines given as `e<seq>`."""
        if not isinstance(source, str):
            return None
        if source.startswith("journal:"):
            number = source[len("journal:"):]
            event = self._event_at.get(int(number)) if number.isdigit() else None
        else:
            lines = self.lines_of.get(source, [])
            event = self._event_at.get(lines[0]) if len(lines) == 1 else None
        return event if event is not None and self.source_of(event) == source else None

    def in_order(self) -> list[Event]:
        """Seq order, line order among equal seqs (a record without a seq goes last)."""
        if self._ordered is None:
            self._ordered = sorted(self.events, key=lambda e: (e.seq is None, e.seq or 0, e.line))
        return self._ordered

    def through(self, seq: int) -> Journal:
        """The journal known through seq, with original line locations retained. No readable
        record without a seq becomes a historical fact; undated diagnostics cannot be time-bound."""
        lines = self.data.split(b"\n")
        return Journal(b"\n".join(raw if self.seq_of.get(n) is not None
                                  and self.seq_of[n] <= seq else b""
                                  for n, raw in enumerate(lines, 1)))

    def prefix_revision(self, seq: int) -> str:
        """Original line identities and bytes known through seq; future blank placeholders and
        appended seqs above the boundary do not change it. Edited/inserted held records do."""
        digest = hashlib.sha256(str(READER_FORMAT).encode("ascii"))
        for line, raw in enumerate(self.data.split(b"\n"), 1):
            known = self.seq_of.get(line)
            if known is not None and known <= seq:
                digest.update(str(line).encode("ascii") + b":" + raw + b"\n")
        return digest.hexdigest()

    def legacy_resolutions(self) -> dict[str, list[str]]:
        """The older kit's resolves convention, separately from /2 correction/withdrawal states.
        A valid earlier unique target only; no present-day correction semantics are inferred."""
        out: dict[str, list[str]] = {}
        for event in self.in_order():
            if event.schema != EVENT_V1 or event.seq is None or not isinstance(event.refs, dict):
                continue
            for target in _strings(event.refs.get("resolves")):
                state, _, _ = self.event_target("event", {"event_id": target}, event.seq)
                if state == "resolved":
                    out.setdefault(target, []).append(self.source_of(event))
        return out

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
    if group in ("reuse", "asset"):
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
    if group == "asset":
        return expected == new or (expected, new) in REUSE_STEPS
    if group == "reuse":
        return (expected, new) in REUSE_STEPS
    if new == "withdrawn":
        return expected != "withdrawn"
    return expected == "withdrawn" and new == _by_corrections(state)


def _apply(state: dict, new) -> None:
    group = state["group"]
    if group == "question":
        state["resolved"] = new == "resolved"
    elif group in ("reuse", "asset"):
        state["stage"] = new
    else:
        state["withdrawn"] = new == "withdrawn"


# Every state a record's display can take (ADR-0006 6), the states a transition may lead to.
RECORD_STATES = ("current", "superseded", "conflict", "withdrawn", "open", "resolved", "proposed", "trial",
          "validated", "registered", "deferred", "rejected", "deprecated")


def _transitions(state: dict) -> list[str]:
    """The states a record_update may take a record to from the state it is in now."""
    shown = _display(state)
    return [new for new in RECORD_STATES if _allowed(state, shown, new)]


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
    under `journal:<line>`. Each state keeps the event that last changed what it shows (`state_of`'s
    `made_by`), which a writer reports with a conflict (design 4.6)."""

    def __init__(self, journal: Journal):
        self.states: dict[str, dict] = {}
        # record_update events that changed no state, each with why: the state of its target's
        # judgment (`missing`, `ambiguous`, `unsupported`, `version_unbound`), `no_state` (the target
        # has none) or `not_one_target` (not exactly one `updates` reference). Shown, never applied.
        self.unapplied: list[dict] = []
        for event in journal.in_order():
            before = event.seq if event.seq is not None else journal.next_seq
            name = journal.source_of(event)
            if event.kind in ASSET_KINDS and asset_event_problem(event.value):
                self.unapplied.append({"event_id": name, "reason": "invalid_asset_record"})
                continue
            if event.kind == "record_update" and asset_update_targets(event.value, journal):
                if asset_update_context_problem(event.value, journal):
                    self.unapplied.append({"event_id": name, "reason": "invalid_asset_update"})
                    continue
            for relation, ref in self._links(event):
                state, _ = self._target(journal, ref, before)
                if state is None:
                    continue
                shown = _display(state)
                if relation == "supersedes" and state["group"] == "content":
                    state["by"].add(name)
                elif relation == "resolves" and state["group"] == "question":
                    state["resolved"] = True
                if _display(state) != shown:
                    state["made_by"] = name
            if event.kind == "record_update" and event.schema == EVENT_V2:
                self._update(journal, event, name, before)
            if event.kind in CONTENT_KINDS:
                self.states[name] = {"group": "content", "withdrawn": False, "by": set(), "mismatched": []}
            elif event.kind in QUESTION_KINDS:
                self.states[name] = {"group": "question", "resolved": False, "mismatched": []}
            elif event.kind == "reuse_proposal" and event.schema == EVENT_V2:
                self.states[name] = {"group": "reuse", "stage": "proposed", "mismatched": []}
            elif event.kind == "asset_candidate" and event.schema == EVENT_V2:
                self.states[name] = {"group": "asset", "stage": "proposed", "mismatched": []}
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
        if metric_event_problem(event.kind, event.value.get("details"), _dicts(event.refs)):
            return []  # invalid/unsupported tagged content cannot retire a valid past record
        details = event.value.get("details")
        if (event.kind == "checkpoint" and isinstance(details, dict)
                and details.get("contract") == "analysis_checkpoint/1"
                and any(ref.get("relation") in ("supersedes", "updates", "resolves") for ref in _dicts(event.refs))):
            return []  # this known checkpoint contract never changes another record's state
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
            shown = _display(state)
            _apply(state, details["new_state"])
            if _display(state) != shown:
                state["made_by"] = name
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

    def state_of(self, target: str) -> dict | None:
        """One record's replayed state, or None when no record has a state under that name: `state`
        (as record_states shows it), `made_by` (the event that last changed it, by its record name;
        None while it is as the record made it), `corrected_by` (a content record's corrections, by
        their record names, sorted), `transitions` (the states a record_update may take it to now)
        and `mismatched_updates`."""
        state = self.states.get(target)
        if state is None:
            return None
        return {"state": _display(state), "made_by": state.get("made_by"),
                "corrected_by": sorted(state.get("by") or ()), "transitions": _transitions(state),
                "mismatched_updates": list(state["mismatched"])}


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
    def directories(path):
        if not _report_plain(root, path):
            return []
        return [name for name in _subdirs(path) if _report_plain(root, path / name)]

    for report in directories(base):
        for revision in directories(base / report):
            if revision == ".staging":
                out += [(f"artifacts/reports/{report}/.staging/{name}", True)
                        for name in directories(base / report / ".staging")]
            else:
                out.append((f"artifacts/reports/{report}/{revision}", False))
    return out


def _report_plain(root, path):
    current = root
    try:
        for part in path.relative_to(root).parts:
            current = current / part
            value = current.lstat()
            if stat.S_ISLNK(value.st_mode) or getattr(value, "st_reparse_tag", 0) == 0xA0000003:
                return False
    except OSError:
        return False
    return True


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


def context_file(root, name) -> Path | None:
    """A CONTEXT version of the project at `root` by its file name, in .analytics/context/ and then
    .lab/context/. A name that is not a bare file name on every platform (a separator, a drive as in
    `C:x.md`, `.`, `..`) names no version."""
    if not _file_name(name):
        return None
    for directory in CONTEXT_DIRS:
        path = Path(root) / directory / name
        if path.is_file():
            return path
    return None


def _snapshot(root: Path, state=None) -> list:
    """What a source_revision is made of: the reader format, and the project-relative path, size and
    mtime_ns of every record file this reader reads, with the names of the CONTEXT versions, and the
    caller's `state` of what it reads paths through when it gives one (`read`'s `frame`). No
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
    for directory, _ in IMPORT_DIRS:
        out += [stat(f"{directory}/{name}") for name in _files(root / directory, ".meta.json")]
    out += [stat(f"{directory}/{name}") for directory in CONTEXT_DIRS
            for name in _files(root / directory, ".md")]
    if state is not None:
        out.append(["frame", state])
    return out


def _token(snapshot: list, as_of_seq: int) -> str:
    text = json.dumps([snapshot, as_of_seq], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]


# --- common pure reference shape validation (writer and Report manifests) ---

BOUND_TARGET = {"event": ("event_id",), "analysis_focus": ("event_id",), "run": ("run_id",),
                "publication": ("publication_id",), "visualization_version": ("run_id", "visualization_id"),
                "data_observation": ("run_id", "role", "path"), "dataset_import": ("path", "digest"),
                "code_snapshot": ("run_id", "path", "digest"), "context_version": ("version", "digest"),
                "metric_definition": ("metric_id", "event_id"), "reuse_proposal": ("proposal_id", "event_id"),
                "asset_candidate": ("asset_id", "event_id"),
                "report_revision": ("report_id", "revision_id", "digest")}
ALWAYS_BOUND = frozenset({"event", "run", "analysis_focus", "publication"})
UNBOUND_KEYS = ("key", "path", "metric_id", "proposal_id", "report_id", "token", "asset_id")
def _reference_digest(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("sha256"), str) and bool(
        re.fullmatch(r"[0-9a-f]{64}", value["sha256"]))


def reference_problem(ref) -> str | None:
    """What makes `ref` no reference of the record schema (its `reference`), or None. The schema is
    the rule; this is what the Writer checks before it writes, standard library only."""
    if not isinstance(ref, dict):
        return "a reference is an object"
    kind, binding = ref.get("type"), ref.get("version_binding")
    for name, allowed in (("type", REF_TYPES), ("relation", RELATIONS),
                          ("basis", BASES), ("version_binding", BINDINGS)):
        if not isinstance(ref.get(name), str) or ref[name] not in allowed:
            return f"{name} {ref.get(name)!r} is not one of {', '.join(sorted(allowed))}"
    for name, value in ref.items():
        if name in ("event_id",) and not (isinstance(value, str) and re.fullmatch(r"e[1-9][0-9]*", value)):
            return f"event_id {value!r} is not an event id"
        if name == "run_id" and not (isinstance(value, str) and re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-z0-9][a-z0-9-]{0,60}", value)):
            return f"run_id {value!r} is not a run id"
        if name == "digest" and not _reference_digest(value):
            return f"digest {value!r} is not {{'sha256': <64 hex>}}"
        if name == "role" and value not in ("input", "output"):
            return f"role {value!r} is not input or output"
        if name == "report_id" and not report_name(value):
            return f"report_id {value!r} is not a portable Report identity"
        if name in ("revision_id", "publication_id") and not (
                isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)):
            return f"{name} {value!r} is not an opaque identity"
        if name in ("token", "key", "path", "visualization_id", "version", "metric_id",
                    "proposal_id", "asset_id") and not (isinstance(value, str) and value):
            return f"{name} is a non-empty string"
    if binding == "none":
        if kind in ALWAYS_BOUND:
            return f"a reference to a {kind} is bound"
        if not any(name in ref for name in UNBOUND_KEYS):
            return "an unbound reference names its target by " + ", ".join(UNBOUND_KEYS)
    else:
        missing = [name for name in BOUND_TARGET[kind] if name not in ref]
        if binding == "inferred" and "token" not in ref:
            missing.append("token")
        if missing:
            return f"a {binding} reference to a {kind} holds " + ", ".join(missing)
    return None


def publication_details_problem(value, *, refs=None, seq=None, validate_reference=None) -> str | None:
    """Pure ADR-0014 completion fields shared by writer, reference reader and content basis.

    Profile IDs identify registered strategies (public CLI: read/evidence). Reading a binding
    never executes that strategy or treats its recorded analysis declaration as a verification.
    Exclusions remain ordinary typed references; no new reference enum or fingerprint is added.
    """
    fields = {"publication_id", "format", "package_sha256", "content_sha256", "profile", "scope",
              "basis", "analysis_state", "exclusions", "location", "checks"}
    if not isinstance(value, dict) or not fields <= value.keys():
        return "missing publication completion fields"
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        return "publication completion is not finite UTF-8 JSON"
    pid = value["publication_id"]
    if not isinstance(pid, str) or not PUBLICATION_ID.fullmatch(pid):
        return "invalid publication identity"
    if value["format"] != PUBLICATION_V1:
        return "unknown publication format"
    if any(not _reference_digest({"sha256": value[k]}) for k in ("package_sha256", "content_sha256")):
        return "invalid publication digest"
    profile = value["profile"]
    if (not isinstance(profile, dict) or set(profile) != {"id", "rules_version"}
            or not isinstance(profile["id"], str) or not profile["id"]
            or type(profile["rules_version"]) is not int or profile["rules_version"] < 1):
        return "invalid publication profile"
    basis = value["basis"]
    if (not isinstance(basis, dict) or set(basis) != {"as_of_seq", "content_seq", "context_version"}
            or type(basis["as_of_seq"]) is not int or type(basis["content_seq"]) is not int
            or not 0 <= basis["content_seq"] <= basis["as_of_seq"]
            or seq is not None and (type(seq) is not int or basis["as_of_seq"] >= seq)
            or basis["context_version"] is not None and not _file_name(basis["context_version"])):
        return "invalid publication basis"
    if value["scope"] not in ("project", "reports"):
        return "invalid publication scope"
    if value["analysis_state"] not in ({"value": "in_progress", "basis": "declared"},
                                       {"value": "complete", "basis": "declared"}):
        return "invalid declared analysis state"
    if value["location"] != f".analytics/publications/{pid}":
        return "invalid publication location"
    checks = value["checks"]
    if (not isinstance(checks, dict) or type(checks.get("format")) is not int
            or checks["format"] != 1 or checks.get("result") != "pass"):
        return "invalid publication checks"
    inspection = checks.get("inspection")
    if (not isinstance(inspection, dict)
            or set(inspection) != {"inspected", "pinned_scanned", "not_inspectable"}
            or any(type(v) is not int or v < 0 for v in inspection.values())
            or inspection["not_inspectable"] != 0):
        return "invalid publication inspection counts"
    validate_reference = reference_problem if validate_reference is None else validate_reference
    exclusions = value["exclusions"]
    try:
        if not isinstance(exclusions, list) or any(validate_reference(r) for r in exclusions):
            return "invalid publication exclusion reference"
        if refs is not None:
            if not isinstance(refs, list) or any(validate_reference(r) for r in refs):
                return "invalid publication completion reference"
            for ref in refs:
                if (ref["type"] not in ("context_version", "report_revision")
                        or ref["relation"] != "includes" or ref["version_binding"] != "author"
                        or ref["basis"] != ("observed" if ref["type"] == "context_version" else "declared")
                        or not _reference_digest(ref.get("digest"))):
                    return "invalid publication completion reference meaning"
            contexts = [r for r in refs if r["type"] == "context_version"]
            if (len(contexts) != (0 if basis["context_version"] is None else 1)
                    or contexts and contexts[0]["version"] != basis["context_version"]):
                return "publication CONTEXT reference differs from its basis"
    except Exception:
        return "publication references cannot be interpreted"
    return None


def publication_event_problem(event, *, validate_reference=None) -> str | None:
    """A complete understood /2 publication row; folder bytes are deliberately not consulted."""
    if (not isinstance(event, dict) or event.get("schema") != EVENT_V2
            or event.get("kind") != "publication" or type(event.get("seq")) is not int
            or event["seq"] < 1 or event.get("id") != f"e{event['seq']}"
            or not isinstance(event.get("actor"), str) or not event["actor"]
            or not isinstance(event.get("text"), str)):
        return "invalid publication event"
    try:
        json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        return "publication event is not finite UTF-8 JSON"
    stamp = _instant(event.get("recorded_at"))
    if stamp is None or stamp.utcoffset() is None:
        return "invalid publication record time"
    if not isinstance(event.get("refs"), list):
        return "invalid publication completion references"
    return publication_details_problem(event.get("details"), refs=event["refs"], seq=event["seq"],
                                       validate_reference=validate_reference)


def publication_record(journal, publication_id, *, before=None, validate_reference=None):
    """(state, reason, candidates, event) for one logical completed identity, without file I/O."""
    if not isinstance(publication_id, str):
        return "unsupported", "invalid_publication_identity", None, None
    boundary = journal.next_seq if before is None else before
    rows = [(line, value) for line, value in journal._publications.get(publication_id, ())
            if journal.seq_of[line] is not None and journal.seq_of[line] < boundary]
    if not rows:
        return "missing", "record_absent", None, None
    if len(rows) > 1:
        return "ambiguous", None, [f"journal:{line}" for line, _ in rows], None
    line, value = rows[0]
    if (line in journal.recovered or journal.is_unsupported(line)
            or publication_event_problem(value, validate_reference=validate_reference)
            or len(journal.lines_of.get(value["id"], ())) != 1):
        return "unsupported", "invalid_publication_record", None, None
    return "resolved", None, None, value



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


def report_name(value) -> bool:
    return (isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value) is not None
            and value not in ASSET_DEVICES)


def report_path(value) -> bool:
    """ADR-0006 §7: canonical, portable NFC paths, without repairing authored names."""
    if not isinstance(value, str) or not value or unicodedata.normalize("NFC", value) != value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return all(part not in ("", ".", "..") and not part.endswith((".", " "))
               and not re.search(r'[<>:"\\|?*\x00-\x1f]', part)
               and part.partition(".")[0].rstrip(" ").casefold() not in (
                   ASSET_DEVICES | {"conin$", "conout$", "com¹", "com²", "com³", "lpt¹", "lpt²", "lpt³"})
               for part in value.split("/"))


def report_paths(paths) -> bool:
    """Every path prefix has one spelling, including shared directory names on Windows/macOS."""
    prefixes = {}
    folded = set()
    for path in paths:
        if not report_path(path) or path.casefold() in folded:
            return False
        folded.add(path.casefold())
        parts = path.split("/")
        for n in range(1, len(parts) + 1):
            prefix = "/".join(parts[:n])
            previous = prefixes.setdefault(prefix.casefold(), prefix)
            if previous != prefix:
                return False
    return not any("/".join(path.split("/")[:n]) in folded
                   for path in folded for n in range(1, len(path.split("/"))))


REPORT_METADATA_FILE = "_analytics/metadata.json"
REPORT_BASE_FIELDS = frozenset({"schema", "report_id", "revision_id", "previous_revision", "request_id",
                                "request_fingerprint", "created_at", "actor", "title", "entry", "files", "based_on",
                                "metadata_file"})


def finite_json(value) -> bool:
    """JSON values only: no coercion of Python keys, tuples, NaN or Unicode surrogates."""
    if value is None or type(value) in (bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, str):
        value.encode("utf-8")
        return True
    if isinstance(value, list):
        return all(finite_json(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and finite_json(key) and finite_json(item) for key, item in value.items())
    return False


def report_metadata_bytes(value) -> bytes:
    """ADR-0013 sidecar vectors; deliberately separate from ADR-0006 canonical intent."""
    if not isinstance(value, dict) or not finite_json(value):
        raise ValueError("Report metadata must be an object of finite JSON values")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def report_metadata_problem(value) -> str | None:
    try:
        report_metadata_bytes(value)
        if REPORT_BASE_FIELDS.intersection(value):
            return "reserved_metadata_field"
        if any(key in value and not isinstance(value[key], str) for key in ("summary", "purpose", "change_reason")):
            return "invalid_metadata_text"
        if "content_status" in value and value["content_status"] not in ("partial", "complete"):
            return "invalid_content_status"
        if "collection" in value:
            collection = value["collection"]
            if not isinstance(collection, dict) or collection.get("contract") != "report_collection/1" or not isinstance(collection.get("members"), list):
                return "invalid_collection"
            seen = set()
            for member in collection["members"]:
                if not isinstance(member, dict) or not isinstance(member.get("member_key"), str) or not member["member_key"] or member["member_key"] in seen:
                    return "invalid_collection_member"
                seen.add(member["member_key"])
                if "reason" in member and (not isinstance(member["reason"], str) or not member["reason"]):
                    return "invalid_collection_reason"
                if member.get("status") == "complete":
                    if not report_name(member.get("report_id")) or not isinstance(member.get("revision_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", member["revision_id"]):
                        return "invalid_collection_target"
                elif member.get("status") in ("failed", "not_generated"):
                    if any(key in member for key in ("report_id", "revision_id")) or not isinstance(member.get("reason"), str) or not member["reason"]:
                        return "invalid_collection_reason"
                else:
                    return "invalid_collection_status"
    except (ValueError, TypeError, OverflowError, RecursionError):
        return "invalid_metadata"
    return None


def report_metadata(manifest) -> dict:
    return {key: value for key, value in manifest.items() if key not in REPORT_BASE_FIELDS}


def collection_counts(collection) -> dict:
    members = collection["members"]
    return {"requested": len(members), **{status: sum(member["status"] == status for member in members)
                                          for status in ("complete", "failed", "not_generated")}}


def report_manifest_problem(value, directory=None) -> str | None:
    """Pure validation of the stored manifest; completion and content integrity stay separate."""
    try:
        if not isinstance(value, dict) or value.get("schema") != REPORT_V1 or not report_name(value.get("report_id")):
            return "invalid_manifest"
        for key in ("revision_id", "request_id"):
            name = value.get(key)
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9_-])?", name) or name.partition(".")[0].lower() in ASSET_DEVICES:
                return "invalid_identity"
        previous = value["previous_revision"]
        if previous is not None and (not isinstance(previous, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", previous)):
            return "invalid_previous_revision"
        if directory is not None:
            parts = directory.split("/")
            if parts[2] != value["report_id"] or parts[-1] != value["revision_id"]:
                return "manifest_identity_mismatch"
        if not _instant(value.get("created_at")) or any(not isinstance(value.get(key), str) or not value[key] for key in ("actor", "title")):
            return "invalid_manifest"
        files = value["files"]
        if not isinstance(files, list) or not files:
            return "empty_files"
        paths = []
        for item in files:
            if not isinstance(item, dict) or not report_path(item.get("path")) or type(item.get("bytes")) is not int or item["bytes"] < 0 or not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                return "invalid_file"
            paths.append(item["path"])
        if not report_paths(paths) or value["entry"] not in paths:
            return "invalid_paths"
        if value["entry"].split("/")[0].casefold() == "_analytics":
            return "reserved_entry"
        metadata = report_metadata(value)
        if metadata or "metadata_file" in value:
            problem = report_metadata_problem(metadata)
            if problem:
                return problem
            data = report_metadata_bytes(metadata)
            sidecar = next((item for item in files if item["path"] == REPORT_METADATA_FILE), None)
            if (value.get("metadata_file") != REPORT_METADATA_FILE or sidecar is None
                    or sidecar["sha256"] != hashlib.sha256(data).hexdigest() or sidecar["bytes"] != len(data)):
                return "metadata_binding_mismatch"
        if any(path.split("/")[0].casefold() == "_analytics" and
               (path != REPORT_METADATA_FILE or "metadata_file" not in value) for path in paths):
            return "reserved_metadata_path"
        if "based_on" in value:
            basis = value["based_on"]
            if not isinstance(basis, dict) or "as_of_seq" not in basis or "context_version" not in basis:
                return "invalid_basis"
            seq, context = basis["as_of_seq"], basis["context_version"]
            if seq is not None and (type(seq) is not int or seq < 0):
                return "invalid_basis"
            if context is not None and (not isinstance(context, str) or not context):
                return "invalid_basis"
            if "refs" in basis and (not isinstance(basis["refs"], list) or any(reference_problem(ref) for ref in basis["refs"])):
                return "invalid_basis"
        if request_fingerprint(value) != value.get("request_fingerprint"):
            return "fingerprint_mismatch"
        # Also rejects lone surrogates in fields excluded from the request fingerprint.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
        return "invalid_manifest"
    return None


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
        self._alive = alive
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
            reason = unsupported_reason(value, frozenset({RUN_V1, RUN_V2}))
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
        self._prepared_unavailable: list[tuple[str, str]] = []
        self._saved: dict[tuple, tuple[str, dict]] = {}
        self._unsupported_reports: set[tuple[str, str]] = set()
        for directory, staging in _report_dirs(root):
            location = f"{directory}/manifest.json"
            value, problem = _load(root / location) if _report_plain(root, root / location) else (None, "absent_or_link")
            if problem:
                if not staging:  # a staging directory being prepared has no manifest yet
                    self.damaged_files.append({"location": location, "reason": problem})
                else:
                    self._prepared_unavailable.append((directory, problem))
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
        for directory, family in IMPORT_DIRS:
            for name in _files(root / directory, ".meta.json"):
                location = f"{directory}/{name}"
                value, problem = _load(root / location)
                if problem:
                    self.damaged_files.append({"location": location, "reason": problem})
                    continue
                reason = unsupported_reason(value, frozenset({family}))
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
        self._by_script: dict[str, list[str]] | None = None
        self._by_path: dict[str, dict] | None = None
        self._rerun_index: dict[str, list[str]] | None = None
        self._cited: tuple[dict, dict] | None = None
        self._histories: dict[tuple, dict] = {}
        self._correction_index: tuple[dict, dict, dict] | None = None

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

    def through(self, seq: int) -> ReadModel:
        """Journal knowledge and completed target versions through seq; physical files are read
        now. Later completion facts cannot be used to resolve an earlier query's reference."""
        historical = ReadModel(self.root, self.journal.through(seq), alive=self._alive,
                               sha=self._sha, legacy_path=self._legacy_path)
        historical._runs = {key: value for key, value in historical._runs.items()
                            if historical.completion_seq(key) is not None}
        historical._unsupported_runs = set()
        complete_imports = {row["location"] for row in historical.imports()
                            if row["recording_status"] == "complete"}
        historical._metas = [(location, value) for location, value in historical._metas
                             if location in complete_imports]
        done = historical._completion_seqs()
        historical._manifests = [(directory, value, staging) for directory, value, staging in historical._manifests
                                 if not staging and (value.get("report_id"), value.get("revision_id")) in done]
        historical._saved = {key: value for key, value in historical._saved.items() if key in done}
        return historical

    def record_payload(self, source: str) -> tuple[dict, str] | None:
        """A complete readable record and its project-relative location; no latest-version lookup."""
        event = self.journal.event_of(source)
        if event is not None:
            return event.value, f"journal:{event.line}"
        if source.startswith("run:"):
            run_id = source[4:]
            if (value := self.run(run_id)) is not None:
                return value, f"runs/{run_id}/run.json"
        if source.startswith("report:"):
            for directory, value, staging in self._manifests:
                if not staging and report_key(value.get("report_id"), value.get("revision_id")) == source:
                    return value, f"{directory}/manifest.json"
        return None

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
        """Only unique, bound completion events whose manifest digest and request agree."""
        out, counts = [], {}
        for event in self.journal.in_order():
            if event.kind == "report_revision" and event.schema == EVENT_V2:
                refs = [r for r in _dicts(event.refs) if r.get("type") == "report_revision" and r.get("relation") == "about"]
                for ref in refs:
                    key = (_str(ref.get("report_id")), _str(ref.get("revision_id")))
                    counts[key] = counts.get(key, 0) + 1
                if len(refs) != 1:
                    continue
                ref = refs[0]
                key = (_str(ref.get("report_id")), _str(ref.get("revision_id")))
                saved = self._saved.get(key)
                if not saved or not _known_ref(ref) or ref.get("version_binding") == "none":
                    continue
                directory, manifest = saved
                if (report_manifest_problem(manifest, directory) or event.id != f"e{event.seq}"
                        or len(self.journal.lines_of.get(event.id, [])) != 1
                        or list(self.journal.seq_of.values()).count(event.seq) != 1
                        or _digest(ref) != self._sha(self.root / directory / "manifest.json")
                        or not isinstance(event.value.get("request_id"), str)
                        or event.value["request_id"].casefold() != manifest["request_id"].casefold()
                        or not isinstance(event.value.get("details"), dict)
                        or "previous_revision" not in event.value["details"]
                        or event.value["details"]["previous_revision"] != manifest["previous_revision"]):
                    continue
                out.append(key)
        return [key for key in out if counts[key] == 1]

    def report_revisions(self, *, verify_content=True) -> list[dict]:
        """Every saved and prepared Report revision (ADR-0006 7): its recording status, whether its
        files are the bytes its manifest names, and whether it is the head of its Report."""
        order = self._report_completions()
        completed, heads = set(order), {report_id: revision_id for report_id, revision_id in order}
        out = []
        for directory, problem in self._prepared_unavailable:
            parts = directory.split("/")
            out.append({"report_id": parts[2], "revision_id": parts[-1], "request_id": None,
                        "recording_status": "prepared", "content": "not_checked", "head": False,
                        "problem": "manifest_unavailable", "manifest_problem": problem})
        for directory, manifest, staging in self._manifests:
            key = (_str(manifest.get("report_id")), _str(manifest.get("revision_id")))
            recording = "prepared" if staging else "complete" if key in completed else "incomplete"
            problem = report_manifest_problem(manifest, directory) or self._metadata_binding(directory, manifest)
            claimed = any(event.kind == "report_revision" and any(
                (r.get("report_id"), r.get("revision_id")) == key for r in _dicts(event.refs))
                for event in self.journal.events)
            if not staging and claimed and key not in completed:
                problem = problem or "invalid_completion"
            out.append({"report_id": key[0], "revision_id": key[1],
                        "request_id": manifest.get("request_id"), "recording_status": recording,
                        "content": self._content(directory, manifest) if verify_content else "not_checked",
                        "head": not staging and heads.get(key[0]) == key[1],
                        **({"collection_counts": collection_counts(manifest["collection"])}
                           if not problem and "collection" in manifest else {}),
                        **({"problem": problem} if problem else {})})
        return out

    def _metadata_binding(self, directory, manifest):
        """Read only the small kit sidecar, never the authored body for a metadata query."""
        if "metadata_file" not in manifest:
            return None
        if report_manifest_problem(manifest, directory):
            return "metadata_binding_mismatch"
        target = self.root / directory / "content" / REPORT_METADATA_FILE
        try:
            if not _report_plain(self.root, target) or target.read_bytes() != report_metadata_bytes(report_metadata(manifest)):
                return "metadata_sidecar_mismatch"
        except OSError:
            return "metadata_sidecar_missing"
        return None

    def _content(self, directory: str, manifest: dict) -> str:
        """`missing`, `content_changed` or `resolved` for the files a manifest names. A path that is
        not relative to the revision's content directory (absolute, with a drive, or with `..`)
        names no preserved file of it and is not hashed: `missing`."""
        states = set()
        if not isinstance(manifest.get("files"), list) or not manifest["files"]:
            return "missing"
        for item in _dicts(manifest.get("files")):
            path = item.get("path")
            target = self.root / directory / "content" / path if report_path(path) else None
            current = self._sha(target) if target and _report_plain(self.root, target) else None
            if current is not None and "bytes" in item and target.stat().st_size != item["bytes"]:
                current = "size_mismatch"
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
                for vis in run_visualizations(run):
                    chart_id = vis["id"]
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

    # --- what a writer binds a name to (design 4.7) ----------------------------------------------

    def latest_version(self, key) -> tuple[str, bool] | None:
        """The newest successful version of a logical key — `<run_id>/<visualization_id>` of the run
        whose completion event has the highest seq (ADR-0006 3) — and whether it is the key's only
        through an older kit's path; None when the key has none."""
        versions = self._version_index()[0].get(self.chart_key(key), []) if isinstance(key, str) else []
        if not versions:
            return None
        _, version, recorded = versions[-1]
        return version, recorded != key

    def keys_named(self, name) -> dict[str, list[str]]:
        """Logical key -> its successful versions, for every script that has a Visualization of this
        id (a name given without its script), each list in completion order."""
        out: dict[str, list[str]] = {}
        for script, version, _ in self._by_name(name, self.journal.next_seq):
            out.setdefault(f"{script}::{name}", []).append(version)
        return out

    def runs_of_request(self, request_id) -> list[str]:
        """The runs whose record holds this request_id, compared without ASCII case (ADR-0006 8)."""
        if not isinstance(request_id, str):
            return []
        wanted = request_id.lower()
        return sorted(run_id for run_id, run in self._runs.items()
                      if isinstance(run.get("request_id"), str) and run["request_id"].lower() == wanted)

    def import_record(self, path=None, *, name=None) -> dict | None:
        """The import record of a path, or of a dataset name (data/imported/ before data/raw/, as the
        views take their names, design 2.3) — the last in file-name order when several have it — or
        None."""
        if path is not None:
            found = self._imports(path)
            return found[-1] if found else None
        for directory, _ in IMPORT_DIRS:
            found = [meta for location, meta in self._metas
                     if location.startswith(f"{directory}/") and meta.get("name") == name]
            if found:
                return found[-1]
        return None

    def latest_output(self, path=None, *, name=None) -> tuple[str, str, str | None] | None:
        """(run, path, sha256) of the newest successful run with a completion event that output this
        path (as recorded, not deleted) — or, for a dataset `name`, a parquet file under
        data/derived/ of that stem, as the dataset list names one — or None."""
        best = None
        for run_id, run in self._runs.items():
            done = self.completion_seq(run_id)
            if run.get("status") != "ok" or done is None:
                continue
            for out in _dicts(run.get("outputs")):
                held = _str(out.get("path"))
                if held is None or out.get("change") == "deleted":
                    continue
                pure = PurePosixPath(held)
                if (held == path if name is None else (
                        pure.stem == name and pure.suffix.lower() == ".parquet"
                        and pure.parts[:2] == ("data", "derived"))):
                    if best is None or done > best[0]:
                        best = (done, run_id, held, _str(out.get("sha256")))
        return best[1:] if best else None

    # --- reference judgments (ADR-0006 3 and 4) --------------------------------------------------

    def reference(self, ref: dict, *, before: int | None = None) -> dict:
        """The same judgment as references(), for one proposed exact reference without a write."""
        return self._judge_v2([ref], None, None, self.journal.next_seq if before is None else before)[0]

    def incoming_sources(self, target: str) -> list[str]:
        """Cheap journal and saved Report candidates for one query identity; exact judgments
        still come from references(), so unsupported/unbound references are never confirmed."""
        out = incoming_sources(self.journal, target)
        for _directory, manifest, staging in self._manifests:
            based_on = manifest.get("based_on")
            if not staging and (isinstance(based_on, dict) and any(
                    _query_ref_targets(ref, target) for ref in _dicts(based_on.get("refs"))) or any(
                    _query_ref_targets(ref, target) for ref in self._collection_refs(manifest))):
                out.append(report_key(manifest.get("report_id"), manifest.get("revision_id")))
        return out

    def _collection_refs(self, manifest):
        if "collection" not in manifest or report_manifest_problem(manifest):
            return []
        refs, digests = [], {}
        for event in self.journal.in_order():
            if event.kind == "report_revision":
                for ref in _dicts(event.refs):
                    if ref.get("type") == "report_revision" and ref.get("relation") == "about":
                        key = (_str(ref.get("report_id")), _str(ref.get("revision_id")))
                        digests.setdefault(key, _digest(ref))
        for member in manifest["collection"]["members"]:
            if member["status"] == "complete":
                # The completion's recorded digest binds the manifest; never take a new hash
                # of damaged files as authored evidence. Target existence is judged separately.
                digest = digests.get((member["report_id"], member["revision_id"]))
                refs.append({"type": "report_revision", "report_id": member["report_id"], "revision_id": member["revision_id"],
                             **({"digest": {"sha256": digest}} if digest else {}),
                             "relation": "includes", "basis": "declared", "version_binding": "author"})
        return refs

    def _manifest_references(self, directory, manifest, source):
        based_on = manifest.get("based_on")
        basis = based_on if isinstance(based_on, dict) else {}
        as_of = _int(basis.get("as_of_seq"))
        completion = self._completion_seqs().get((_str(manifest.get("report_id")), _str(manifest.get("revision_id"))), self.journal.next_seq)
        out = self._judge_v2(basis.get("refs"), source, None, as_of + 1 if as_of is not None else completion)
        if not self._metadata_binding(directory, manifest):
            refs = self._collection_refs(manifest)
            completed = set(self._report_completions()) if refs else set()
            seqs = self._completion_seqs() if refs else {}
            indices = [index for index, member in enumerate(manifest["collection"]["members"])
                       if member["status"] == "complete"] if refs else []
            for index, judged in zip(indices, self._judge_v2(refs, source, None, completion), strict=True):
                # Structural membership is kept apart from authored document evidence.
                judged["structural"] = True
                judged["ref"] = {"field": "collection.members", "index": index}
                key = (judged["target"].get("report_id"), judged["target"].get("revision_id"))
                if key not in completed or seqs.get(key, completion) >= completion:
                    judged.update(state="missing", reason="record_absent", confirmed=False)
                out.append(judged)
        return out

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
        if source is not None and self._references is None:
            for _directory, manifest, staging in self._manifests:
                if staging or report_key(manifest.get("report_id"), manifest.get("revision_id")) != source:
                    continue
                return self._manifest_references(_directory, manifest, source)
        if self._references is None:
            out: list[dict] = []
            for event in self.journal.events:
                source_id = self.journal.source_of(event)
                before = event.seq if event.seq is not None else self.journal.next_seq
                if event.schema == EVENT_V1:
                    out += self._judge_v1(event, source_id, before)
                else:
                    out += self._judge_v2(event.refs, source_id, event.line, before)
            for _directory, manifest, staging in self._manifests:
                if staging:
                    continue
                source_id = report_key(manifest.get("report_id"), manifest.get("revision_id"))
                out += self._manifest_references(_directory, manifest, source_id)
            self._references = out
            for judged in out:
                self._by_source.setdefault(judged["from"], []).append(judged)
        if source is None:
            return list(self._references)
        return list(self._by_source.get(source, []))

    def prepare_reference_neighborhoods(self) -> None:
        """Index an already global read for static generation; bounded reads stay lazy."""
        order = {self.journal.source_of(event): n
                 for n, event in enumerate(self.journal.in_order())}
        incoming: dict[str, list[dict]] = {}
        for judged in sorted(self.references(), key=lambda row: order.get(row["from"], len(order))):
            target = query_target(judged)
            if isinstance(target, str):
                incoming.setdefault(target, []).append(judged)
        self._incoming_references = incoming

    def reference_neighborhood(self, source: str, direction: str = "both") -> list[dict]:
        """One-hop reference rows shared by bounded retrieval and static Workspace pages.

        Exact query identities and qualification remain unchanged. This is reference navigation,
        not new inferred lineage. Without a prepared global index, judge only candidate sources.
        """
        rows = ([{"direction": "outgoing", **row} for row in self.references(source)]
                if direction != "incoming" else [])
        if direction != "outgoing":
            if hasattr(self, "_incoming_references"):
                incoming = self._incoming_references.get(source, [])
            else:
                incoming = [row for candidate in self.incoming_sources(source)
                            for row in self.references(candidate) if query_target(row) == source]
            rows += [{"direction": "incoming", **row} for row in incoming]
        return rows

    def _event_of(self, source: str) -> Event | None:
        """The readable event `source` names (Journal.event_of); None for a Report revision."""
        return self.journal.event_of(source)

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
            if not _known_ref(ref) or (ref.get("type") == "publication" and reference_problem(ref)):
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
        return context_file(self.root, name)

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
            state, reason, candidates, value = publication_record(self.journal, ref.get("publication_id"), before=before)
            if state != "resolved":
                return state, reason, candidates
            if _digest(ref) != value["details"]["package_sha256"]:
                return "missing", "digest_mismatch", None
            return "resolved", None, None
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
            found = [v for v in run_visualizations(run) if v["id"] == ref.get("visualization_id")]
            if not found:
                return "missing", "record_absent", None
            files = found[0].get("files")
            files = list(files.values()) if isinstance(files, dict) else []
            recorded = _str(found[0].get("sha256"))
            if found[0]["field"] == "visualizations" and recorded and len(files) == 1:
                # An analytics.run/2 Visualization keeps the hash of its stored file (design 2.6):
                # judged against it as any target that keeps one.
                return self._against_record(recorded, ref, files[0])
            if "digest" in ref:
                # A lab.run/1 chart record holds no hash: this digest is one the record never held.
                return "missing", "digest_mismatch", None
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

    # --- version histories (design WORKSPACE-READABILITY 5, #99) -----------------------------------
    #
    # A version number is a value for people to read, counted from the records each time they are
    # read: the same records give the same numbers, groups and uses. Versions are counted in the order
    # their runs started, their imports were made or their snapshots were taken, so a version keeps its
    # number while later ones are added; a run that started earlier but ended after a later one takes
    # its place before it and moves the later numbers on (which is why nothing cites a number). Addresses and citations keep the identifiers the records hold:
    # run ids, sha256, snapshot names. Only what compares the records with a file now (which version
    # the file is) is an observation of this read.

    def _script_runs(self) -> dict[str, list[str]]:
        """Script path as read (read_as) -> its runs in run order (the order they started); `?` holds
        the runs that recorded no script path, as the gallery names their keys."""
        if self._by_script is None:
            index: dict[str, list[str]] = {}
            for run_id, run in self._runs.items():
                recorded = _str(_script(run).get("path"))
                index.setdefault(self._as_read(run_id, recorded) if recorded else "?", []).append(run_id)
            self._by_script = index
        return self._by_script

    def scripts(self) -> list[str]:
        """Every script path a run recorded, as read (read_as), sorted."""
        return sorted(script for script in self._script_runs() if script != "?")

    def _ended(self, run_id) -> str | None:
        """How a run ended: `ok` or one of ENDED_FAILED; None while it runs, or when its process is
        gone with no end recorded (`incomplete`). A run that has not ended makes no version."""
        status = self._execution.get(run_id)
        return status if status == "ok" or status in ENDED_FAILED else None

    def _inputs_of(self, run_id: str) -> tuple:
        """The (path as read, sha256) of a run's inputs, once each and sorted."""
        run = self._runs.get(run_id) or {}
        return tuple(sorted({(self._as_read(run_id, path), _str(entry.get("sha256")) or "")
                             for entry in _dicts(run.get("inputs")) if (path := _str(entry.get("path")))}))

    def _reruns(self) -> dict[str, list[str]]:
        """Run id -> the runs that re-executed it (`rerun_of`), and the runs that re-executed those, in
        run order: a rerun of a rerun re-executes the same snapshot."""
        if self._rerun_index is not None:
            return self._rerun_index
        direct: dict[str, list[str]] = {}
        for run_id, run in self._runs.items():
            of = _str(run.get("rerun_of"))
            if of:
                direct.setdefault(of, []).append(run_id)
        order = {run_id: n for n, run_id in enumerate(self._runs)}
        index: dict[str, list[str]] = {}
        for origin in direct:
            found, todo = set(), list(direct[origin])
            while todo:
                rerun = todo.pop()
                if rerun not in found and rerun != origin:
                    found.add(rerun)
                    todo += direct.get(rerun, [])
            index[origin] = sorted(found, key=lambda r: order.get(r, len(order)))
        self._rerun_index = index
        return index

    def _rerun_entries(self, run_id: str, vis_id=None) -> list[dict]:
        """The ended reruns of a run (_reruns), each with how it ended, its comparison and, for a
        Visualization id, the records citing its version of it."""
        bound = self._citations()[0]
        out = []
        for rerun in self._reruns().get(run_id, []):
            if self._ended(rerun) is None:
                continue
            record = self._runs[rerun]
            out.append({"run": rerun, "status": self._ended(rerun), "of": _str(record.get("rerun_of")),
                        "started_at": _str(record.get("started_at")),
                        "verdict": (run_reproduction(record) or {}).get("verdict"),
                        "cited_by": bound.get(f"{rerun}/{vis_id}", []) if vis_id is not None else []})
        return out

    def _kit_completion(self, source: str) -> bool:
        """Whether a record is the kit's own completion of what it recorded (a run, an import, a
        CONTEXT.md version): its `about` references name its subject, not something it cites."""
        event = self.journal.event_of(source)
        return event is not None and event.kind in ("run", "import", "context")

    def _citations(self) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """(identity -> the records citing it with a version, name -> the records naming it without
        one), each record once, in journal order. A version is named by its query identity
        (`<run_id>/<visualization_id>`, …); a name left unbound by its logical key (a Visualization key
        as chart_key reads it), and a Visualization name given without its script counts for every
        key its candidates are versions of. The kit's own records of what it recorded (`about`,
        `observed`; and a lab.event/1 run's or import's `about` its data) cite and name nothing."""
        if self._cited is None:
            key_of = {version: key for key, versions in self._version_index()[0].items()
                      for _, version, _ in versions}
            bound: dict[str, dict] = {}
            unbound: dict[str, dict] = {}
            for judged in self.references():
                source = judged["from"]
                if judged.get("relation") == "about" and (judged.get("basis") == "observed"
                                                          or self._kit_completion(source)):
                    continue
                if judged.get("version_binding") == "none":
                    target = judged.get("target") or {}
                    visualization = judged.get("type") == "visualization_version"
                    names = set()
                    if isinstance(target.get("key"), str):
                        names.add(self.chart_key(target["key"]) if visualization else target["key"])
                    if isinstance(target.get("path"), str):
                        names.add(target["path"])
                    if visualization:
                        names.update(key_of[c] for c in judged.get("candidates") or [] if c in key_of)
                    for name in names:
                        unbound.setdefault(name, {})[source] = None
                    continue
                target = query_target(judged)
                if isinstance(target, str):
                    bound.setdefault(target, {})[source] = None
            self._cited = ({k: list(v) for k, v in bound.items()}, {k: list(v) for k, v in unbound.items()})
        return self._cited

    def _failure(self, run_id: str, after: int) -> dict:
        """A run that ended without success where a history shows it, with the reruns of it."""
        run = self._runs[run_id]
        error = run.get("error") if isinstance(run.get("error"), dict) else {}
        return {"run": run_id, "status": self._ended(run_id), "started_at": _str(run.get("started_at")),
                "code": _str(_script(run).get("sha256")), "error": _str(error.get("summary")), "after": after,
                "reruns": self._rerun_entries(run_id)}

    def visualization_history(self, key: str) -> dict:
        """The versions of a logical Visualization key (design 5.2), counted as the gallery counts
        them (core.chart_index): a successful run that is not a rerun and recorded a Visualization of
        the key makes the next version, in run order; a run an older kit recorded in another spelling
        of its script counts for today's key (`inferred`, ADR-0006 9).

        `versions`: `number`, `version` (`<run_id>/<visualization_id>`, as a reference names it),
        `run`, `started_at`, `code` (the script's sha256), `inputs` ((path, sha256) pairs), `reasons`
        (what differs from the version before: `code` with `from` and `to`, `inputs` with the
        `changed` and `removed` paths), `cited_by` (the records citing that version) and `reruns` (the
        runs that re-executed its run, and reruns of those, which make no version: `run`, `status`,
        `of`, `verdict`, `started_at`, `cited_by`). `failed`: the script's runs that are not reruns and
        ended without success, each after `after` versions, with `visualization` (whether it recorded
        one of this id) and its `reruns`; none makes a version. `named_by`: the records naming the key without a
        version. `groups`: the version numbers in order, joined while the code and the inputs stay the
        same and no failed run comes between. `latest`: the last version's `number`, whether a record
        cites it, and the last version one does (`last_cited`)."""
        key = self.chart_key(key)
        cached = self._histories.get(("visualization", key))
        if cached is not None:
            return cached
        script, _, vis_id = key.rpartition("::")
        bound, unbound = self._citations()
        versions: list[dict] = []
        failed: list[dict] = []
        groups: list[list[int]] = []
        joined = False
        for run_id in self._script_runs().get(script, []):
            run = self._runs[run_id]
            ended = self._ended(run_id)
            if ended is None or _str(run.get("rerun_of")):
                continue
            if ended != "ok":
                failed.append({**self._failure(run_id, len(versions)),
                               "visualization": any(v["id"] == vis_id for v in run_visualizations(run))})
                joined = False
                continue
            if not any(v["id"] == vis_id for v in run_visualizations(run)):
                continue
            code, inputs = _str(_script(run).get("sha256")), self._inputs_of(run_id)
            before = versions[-1] if versions else None
            reasons = []
            if before is not None and before["code"] != code:
                reasons.append({"kind": "code", "from": before["code"], "to": code})
            if before is not None and before["inputs"] != inputs:
                reasons.append({"kind": "inputs", **_input_change(before["inputs"], inputs)})
            number = len(versions) + 1
            again = self._rerun_entries(run_id, vis_id)
            versions.append({"number": number, "version": f"{run_id}/{vis_id}", "run": run_id,
                             "started_at": _str(run.get("started_at")), "code": code, "inputs": inputs,
                             "reasons": reasons, "cited_by": bound.get(f"{run_id}/{vis_id}", []),
                             "reruns": again,
                             "inferred": _str(_script(run).get("path")) not in (script, None)})
            if joined and before is not None and not reasons:
                groups[-1].append(number)
            else:
                groups.append([number])
            joined = True
        cited = [v["number"] for v in versions if v["cited_by"]]
        result = {"key": key, "versions": versions, "failed": failed, "groups": groups,
                  "named_by": unbound.get(key, []),
                  "latest": {"number": len(versions), "cited": bool(versions and versions[-1]["cited_by"]),
                             "last_cited": cited[-1] if cited else None}}
        self._histories[("visualization", key)] = result
        return result

    def _path_entries(self) -> dict[str, dict[str, list[tuple[str, dict]]]]:
        """Identity path -> the (run, entry) of every output (not deleted) and every input runs recorded
        at it, in run order, an older kit's path read as read_as gives it: one pass per read."""
        if self._by_path is None:
            index: dict[str, dict[str, list]] = {}
            for run_id, run in self._runs.items():
                for role, field in (("outputs", "outputs"), ("inputs", "inputs")):
                    for entry in _dicts(run.get(field)):
                        held = _str(entry.get("path"))
                        if held is None or (role == "outputs" and entry.get("change") == "deleted"):
                            continue
                        index.setdefault(self._as_read(run_id, held), {}).setdefault(role, []).append((run_id, entry))
            self._by_path = index
        return self._by_path

    def _import_completions(self, path: str) -> list[tuple[str, str]]:
        """(sha256, recorded_at) of the import completion records (analytics.event/2, `about` a
        `dataset_import` of this path with its digest) in journal order: an import whose record a
        later import of the same name replaced is still here. A lab.event/1 import records no digest."""
        out = []
        for event in self.journal.in_order():
            if event.kind != "import" or event.schema == EVENT_V1:
                continue
            for ref in _dicts(event.refs):
                if (_known_ref(ref) and ref["type"] == "dataset_import" and ref["relation"] == "about"
                        and ref.get("path") == path and _digest(ref)):
                    out.append((_digest(ref), _str(event.recorded_at)))
        return out

    def data_history(self, path: str) -> dict:
        """The byte versions of a data file at an identity path (design 5.2). A successful run that
        is not a rerun and output the path with bytes no version had makes the next version; the same
        bytes written again (an earlier version's too) are a rewrite of that version. A path with an
        import record also gets a version for each import's bytes (the import completion records and
        the import record now) and for bytes a run recorded reading that no import, no successful run
        and no failed run wrote there (changed outside an import). Counted in time order: a run's
        start, an import's time (a time that cannot be read comes last).

        `versions`: `number`, `sha256`, `started_at`, `source` (`write`, `import` or `read`: how the
        version was first seen), `run` (that run, or None), `script` (the first writer's, as read),
        `writes` (the runs that wrote these bytes, in order, the first included), `imports`, `code`
        and `inputs` (of the first writer), `bytes` and `rows` as recorded, `inferred` (first seen only
        through an older kit's path, ADR-0006 9), `reasons` (what differs from the version before:
        `script` when another script wrote it, else `code` and `inputs`; `imported`, `outside`),
        `readers` (one per run that recorded reading these bytes: `run`, `status`, `via`, `inferred`)
        and `reruns` (runs re-executing a writer, and reruns of those: `run`, `sha256`, `verdict`).
        `failed`: the runs that ended without success and are not reruns, of a script that wrote the
        path or that recorded an output at it themselves, each after `after` versions, with the bytes
        they recorded (`wrote`, and `wrote_version` when those are a version's) and their reruns —
        never a version. `other_reads`: reads of bytes that are no version (`sha256`, "" when the read
        recorded none; `failed_output`: the failed run that recorded those bytes). `named_by`: records
        naming the path without a version. `scripts`: the scripts that wrote it (`writers`) and read
        it (`readers`). `groups` as visualization_history's. `latest`: the last `number`, whether a
        run read it and the last version one did (`last_read`). `now`: what the file is now —
        `version` (with its `number`), `failed_output` (bytes a failed run recorded, `run`), `other`,
        `missing` — an observation of this read."""
        cached = self._histories.get(("data", path))
        if cached is not None:
            return cached
        _bound, unbound = self._citations()
        seen: list[tuple] = []  # (instant, order, source, sha256, run, detail)
        writers: set[str] = set()
        imports = self._imports(path)
        recorded_imports = self._import_completions(path)
        for digest, at in recorded_imports:
            seen.append((_order(at), len(seen), "import", digest, None, {"imported_at": at}))
        for meta in imports:
            digest = _str(meta.get("sha256"))
            if digest and digest not in {d for d, _ in recorded_imports}:
                seen.append((_order(meta.get("imported_at")), len(seen), "import", digest, None, meta))
        rerun_writes: list[tuple] = []
        failed_runs: dict[str, str | None] = {}
        entries = self._path_entries().get(path, {})
        for run_id, out in entries.get("outputs", []):
            run = self._runs[run_id]
            writers.add(self._as_read(run_id, _str(_script(run).get("path")) or "?"))
            digest = _str(out.get("sha256"))
            if self._ended(run_id) in ENDED_FAILED and not _str(run.get("rerun_of")):
                failed_runs.setdefault(run_id, digest)
            if run.get("status") != "ok" or not digest:
                continue
            if _str(run.get("rerun_of")):
                rerun_writes.append((run_id, digest))
            else:
                seen.append((_order(run.get("started_at")), len(seen), "write", digest, run_id, out))
        for script in sorted(writers - {"?"}):
            for run_id in self._script_runs().get(script, []):
                if self._ended(run_id) in ENDED_FAILED and not _str(self._runs[run_id].get("rerun_of")):
                    failed_runs.setdefault(run_id, None)
        failed_bytes = {digest: run_id for run_id, digest in failed_runs.items() if digest}
        reads: dict[tuple[str, str], dict] = {}
        for run_id, entry in entries.get("inputs", []):
            if self._ended(run_id) is None:
                continue
            digest = _str(entry.get("sha256")) or ""
            via = [v for v in entry.get("via") if isinstance(v, str)] if isinstance(entry.get("via"), list) else []
            inferred = entry.get("path") != path
            held = reads.get((run_id, digest))
            if held is None:
                reads[(run_id, digest)] = {"run": run_id, "status": self._ended(run_id), "via": via,
                                           "inferred": inferred}
            else:
                held["via"] = list(dict.fromkeys(held["via"] + via))
                held["inferred"] = held["inferred"] and inferred
        if imports or recorded_imports:
            known = {item[3] for item in seen} | set(failed_bytes)
            for (run_id, digest) in reads:
                if digest and digest not in known:
                    known.add(digest)
                    seen.append((_order(self._runs[run_id].get("started_at")), len(seen), "read", digest,
                                 run_id, None))
        seen.sort(key=lambda item: item[:2])
        versions: list[dict] = []
        by_sha: dict[str, dict] = {}
        timeline: list[tuple] = []
        for at, order, source, digest, run_id, detail in seen:
            if digest in by_sha:
                if run_id is not None and source == "write":
                    by_sha[digest]["writes"].append(run_id)
                elif source == "import":
                    by_sha[digest]["imports"] += 1
                continue
            run = self._runs.get(run_id) or {}
            writer = self._as_read(run_id, _str(_script(run).get("path")) or "?") if source == "write" else None
            code = _str(_script(run).get("sha256")) if source == "write" else None
            inputs = self._inputs_of(run_id) if source == "write" else ()
            before = versions[-1] if versions else None
            reasons = []
            if before is not None and source == "write" and before["source"] == "write":
                if before["script"] != writer:
                    reasons.append({"kind": "script", "from": before["script"], "to": writer})
                else:
                    if before["code"] != code:
                        reasons.append({"kind": "code", "from": before["code"], "to": code})
                    if before["inputs"] != inputs:
                        reasons.append({"kind": "inputs", **_input_change(before["inputs"], inputs)})
            elif before is not None:
                reasons.append({"kind": "imported" if source == "import" else "outside" if source == "read"
                                else "written"})
            detail = detail or {}
            version = {"number": len(versions) + 1, "sha256": digest, "source": source, "run": run_id,
                       "script": writer,
                       "started_at": _str(run.get("started_at")) if run_id else _str(detail.get("imported_at")),
                       "writes": [run_id] if source == "write" else [], "imports": int(source == "import"),
                       "code": code, "inputs": inputs, "bytes": _int(detail.get("bytes")),
                       "rows": _int(detail.get("rows")),
                       "inferred": source == "write" and detail.get("path") != path,
                       "reasons": reasons, "readers": [], "reruns": []}
            versions.append(version)
            by_sha[digest] = version
            timeline.append((at, order, version))
        for run_id, digest in rerun_writes:
            origin = run_id
            for _ in range(len(self._runs)):  # the run a chain of reruns re-executes
                of = _str(self._runs.get(origin, {}).get("rerun_of"))
                if not of or of not in self._runs:
                    break
                origin = of
            target = next((v for v in versions if origin in v["writes"]), None)
            if target is not None:
                target["reruns"].append({"run": run_id, "sha256": digest,
                                         "verdict": (run_reproduction(self._runs[run_id]) or {}).get("verdict")})
        other_reads = []
        for (_, digest), entry in reads.items():
            if digest in by_sha:
                by_sha[digest]["readers"].append(entry)
            else:
                other_reads.append({**entry, "sha256": digest, "failed_output": failed_bytes.get(digest)})
        failed = []
        for run_id in sorted(failed_runs, key=lambda r: (_order(self._runs[r].get("started_at")), r)):
            at = _order(self._runs[run_id].get("started_at"))
            wrote = failed_runs[run_id]
            failed.append({**self._failure(run_id, sum(1 for t in timeline if t[0] <= at)), "wrote": wrote,
                           "wrote_version": by_sha[wrote]["number"] if wrote in by_sha else None})
        groups: list[list[int]] = []
        breaks = {f["after"] for f in failed}
        for version in versions:
            n = version["number"]
            if groups and not version["reasons"] and (n - 1) not in breaks:
                groups[-1].append(n)
            else:
                groups.append([n])
        current = self._sha(self.root / path) if _inside(path) else None
        if current is None:
            now = {"state": "missing", "sha256": None}
        elif current in by_sha:
            now = {"state": "version", "number": by_sha[current]["number"], "sha256": current}
        else:
            failed_output = failed_bytes.get(current)
            now = ({"state": "failed_output", "run": failed_output, "sha256": current} if failed_output
                   else {"state": "other", "sha256": current})
        read = [v["number"] for v in versions if v["readers"]]
        named = list(dict.fromkeys(unbound.get(path, []) + unbound.get(PurePosixPath(path).stem, [])))
        result = {"path": path, "versions": versions, "failed": failed, "other_reads": other_reads,
                  "named_by": named, "groups": groups,
                  "scripts": {"writers": sorted(writers - {"?"}),
                              "readers": sorted({self._as_read(r, _str(_script(self._runs[r]).get("path")) or "?")
                                                 for r, _ in reads} - {"?"})},
                  "latest": {"number": len(versions), "read": bool(versions and versions[-1]["readers"]),
                             "last_read": read[-1] if read else None},
                  "now": now}
        self._histories[("data", path)] = result
        return result

    def code_history(self, script: str) -> dict:
        """The code versions of a script path as read (design 5.2): each sha256 of the script's
        snapshot a run that ended recorded, numbered by the run that first ran it. A failed run's code
        is a version too (the snapshot is kept before the script runs); a run that has not ended makes
        none. Runs in a row with the same code are one segment, and code that comes back keeps its
        number (`again`). A rerun runs the snapshot of the run it re-executes: it is listed with that
        run's segment (`reruns`), never as code coming back.

        `versions`: `number`, `sha256`, `snapshot` (the first run's copy), `run` and `started_at` (the
        first run), `runs`, `failed` (every run of it failed), `inferred` (the first run recorded the
        script in an older kit's spelling, ADR-0006 9), `lines`, and `added`, `removed` and
        `against` (the lines changed from the code of the segment before it first ran; None when
        there is none or a snapshot cannot be read). `segments`, in run order: `number`, `again`,
        `runs` (`run`, `status`, `started_at`), `reruns` (the same, for the reruns of its runs and of
        those), `visualizations` and `data` (key or path ->
        the version numbers its runs made: visualization_history's and data_history's), and `inputs`
        (`changed` and `removed` input paths against the segment before, when they differ: paths, so a
        new input reads as one, not new bytes at the same path). `latest`: the number of the last
        segment."""
        cached = self._histories.get(("code", script))
        if cached is not None:
            return cached
        by_sha: dict[str, dict] = {}
        versions: list[dict] = []
        segments: list[dict] = []
        texts: dict[str, list[str] | None] = {}

        def lines(snapshot) -> list[str] | None:
            if snapshot not in texts:
                try:
                    texts[snapshot] = ((self.root / snapshot).read_bytes().decode("utf-8", errors="replace")
                                       .splitlines() if _inside(snapshot) else None)
                except OSError:
                    texts[snapshot] = None
            return texts[snapshot]

        reruns: list[str] = []
        for run_id in self._script_runs().get(script, []):
            ended = self._ended(run_id)
            if ended is None:
                continue
            run = self._runs[run_id]
            if _str(run.get("rerun_of")):
                reruns.append(run_id)
                continue
            recorded = _script(run)
            digest = _str(recorded.get("sha256")) or ""
            version = by_sha.get(digest)
            if version is None:
                snapshot = _str(recorded.get("snapshot"))
                text = lines(snapshot)
                against = versions[segments[-1]["number"] - 1] if segments else None
                old = lines(against["snapshot"]) if against else None
                added, removed = (_line_changes(old, text) if old is not None and text is not None
                                  else (None, None))
                version = {"number": len(versions) + 1, "sha256": digest or None, "snapshot": snapshot,
                           "run": run_id, "started_at": _str(run.get("started_at")), "runs": [],
                           "failed": True, "lines": len(text) if text is not None else None,
                           "inferred": _str(recorded.get("path")) not in (script, None),
                           "added": added, "removed": removed,
                           "against": against["number"] if against and added is not None else None}
                versions.append(version)
                by_sha[digest] = version
            version["runs"].append(run_id)
            version["failed"] = version["failed"] and ended != "ok"
            entry = {"run": run_id, "status": ended, "started_at": _str(run.get("started_at"))}
            if segments and segments[-1]["number"] == version["number"]:
                segments[-1]["runs"].append(entry)
                continue
            inputs = {path for path, _ in self._inputs_of(run_id)}
            change = {}
            if segments:
                earlier = {path for path, _ in self._inputs_of(segments[-1]["runs"][-1]["run"])}
                if earlier != inputs:
                    change = {"changed": sorted(inputs - earlier), "removed": sorted(earlier - inputs)}
            segments.append({"number": version["number"], "again": version["run"] != run_id,
                             "runs": [entry], "reruns": [], "inputs": change, "visualizations": {}, "data": {}})
        place = {entry["run"]: segment for segment in segments for entry in segment["runs"]}
        for run_id in reruns:
            origin = run_id
            for _ in range(len(self._runs)):  # the run a chain of reruns re-executes
                of = _str(self._runs.get(origin, {}).get("rerun_of"))
                if not of or of not in self._runs:
                    break
                origin = of
            if origin in place:
                run = self._runs[run_id]
                place[origin]["reruns"].append({"run": run_id, "status": self._ended(run_id),
                                                "started_at": _str(run.get("started_at"))})
        for segment in segments:
            for entry in segment["runs"]:
                run = self._runs[entry["run"]]
                if entry["status"] != "ok":
                    continue
                for vis in run_visualizations(run):
                    key = f"{script}::{vis['id']}"
                    numbers = {v["run"]: v["number"] for v in self.visualization_history(key)["versions"]}
                    if entry["run"] in numbers:
                        segment["visualizations"].setdefault(self.chart_key(key), []).append(numbers[entry["run"]])
                for out in _dicts(run.get("outputs")):
                    held = _str(out.get("path"))
                    if held is None or out.get("change") == "deleted":
                        continue
                    path = self._as_read(entry["run"], held)
                    numbers = {v["sha256"]: v["number"] for v in self.data_history(path)["versions"]}
                    digest = _str(out.get("sha256"))
                    if digest in numbers:
                        segment["data"].setdefault(path, []).append(numbers[digest])
        result = {"script": script, "versions": versions, "segments": segments,
                  "latest": {"number": segments[-1]["number"] if segments else 0}}
        self._histories[("code", script)] = result
        return result

    def context_history(self, pinned=None, kinds=()) -> dict:
        """The CONTEXT.md versions (design 5.2): every snapshot of the two state directories, in name
        (= time) order, a name in both read from the first (CONTEXT_DIRS, as a reference resolves it).
        `pinned(text)` gives what of a version's text is pinned (the caller's reading of its sections:
        this module does not parse CONTEXT.md); `kinds` are the record kinds counted as insights.

        `versions`: `number`, `name` (the snapshot's file name, how references name it), `event` (the
        record of its completion, or None) and `recorded_at`, `lines`, `added` and `removed` (against
        the version before), `pinned` (how many sections are pinned) and `pinned_changed` (None for
        the first, or without `pinned`), and `insights`: the records of `kinds` written while it was
        the latest version — after its completion record and before the next version's — or None when
        it or the next version has no completion record (then when it stopped being the latest is not
        recorded). They are records written then, not records that used its text. `groups`: the
        numbers in order, joined while the pinned sections stay the same (the first version alone).
        `latest`: the last `number` and the version that last changed the pinned sections
        (`pinned_since`; None when no version changed them)."""
        cached = self._histories.get(("context", tuple(kinds), pinned is not None))
        if cached is not None:
            return cached
        found: dict[str, Path] = {}
        for directory in CONTEXT_DIRS:
            place = self.root / directory
            if place.is_dir():
                for entry in sorted(place.glob("*.md")):
                    if entry.is_file():
                        found.setdefault(entry.name, entry)
        completed: dict[str, Event] = {}
        insight_seqs: list[int] = []
        for event in self.journal.in_order():
            if event.kind in kinds and event.seq is not None:
                insight_seqs.append(event.seq)
            if event.kind != "context":
                continue
            if event.schema == EVENT_V1:
                refs = event.refs if isinstance(event.refs, dict) else {}
                names = [v for v in _strings(refs.get("context")) if isinstance(v, str)]
            else:
                names = [_str(r.get("version")) for r in _dicts(event.refs) if _known_ref(r)
                         and r["type"] == "context_version" and r["relation"] == "about"]
            for name in filter(None, names):
                completed.setdefault(name, event)
        names = sorted(found)
        starts = sorted((completed[n].seq, n) for n in names if n in completed and completed[n].seq is not None)
        counts: dict[str, int | None] = {name: 0 for _, name in starts}
        for seq in insight_seqs:
            at = bisect.bisect_right(starts, (seq,)) - 1
            if at >= 0:
                counts[starts[at][1]] += 1
        timed = {name for _, name in starts}
        for name, following in zip(names, names[1:], strict=False):
            if following not in timed:
                counts[name] = None
        versions: list[dict] = []
        groups: list[list[int]] = []
        previous_lines: list[str] | None = None
        previous_pins = None
        for n, name in enumerate(names, 1):
            text = found[name].read_bytes().decode("utf-8", errors="replace")
            current = text.splitlines()
            pins = pinned(text) if pinned is not None else None
            added, removed = (_line_changes(previous_lines, current) if previous_lines is not None
                              else (len(current), 0))
            changed = None if n == 1 or pinned is None else pins != previous_pins
            event = completed.get(name)
            versions.append({"number": n, "name": name, "event": self.journal.source_of(event) if event else None,
                             "recorded_at": event.recorded_at if event else None, "lines": len(current),
                             "added": added, "removed": removed,
                             "pinned": len(pins) if pins is not None else None, "pinned_changed": changed,
                             "insights": counts.get(name)})
            if groups and n > 2 and changed is False and groups[-1][-1] == n - 1 and \
                    versions[n - 2]["pinned_changed"] is False:
                groups[-1].append(n)
            else:
                groups.append([n])
            previous_lines, previous_pins = current, pins
        since = max((v["number"] for v in versions if v["pinned_changed"]), default=None)
        result = {"versions": versions, "groups": groups,
                  "latest": {"number": len(versions), "pinned_since": since}}
        self._histories[("context", tuple(kinds), pinned is not None)] = result
        return result

    def _corrections(self) -> tuple[dict, dict, dict]:
        """(record -> the records correcting it, record -> the records it corrects, record -> its place
        in the journal), from the state replay, once per read."""
        if self._correction_index is None:
            replay = self.journal.replay()
            by = {name: sorted(state["by"]) for name, state in replay.states.items()
                  if state.get("group") == "content" and state["by"]}
            corrects: dict[str, list[str]] = {}
            for name, correctors in by.items():
                for corrector in correctors:
                    corrects.setdefault(corrector, []).append(name)
            order = {self.journal.source_of(event): n for n, event in enumerate(self.journal.in_order())}
            self._correction_index = (by, corrects, order)
        return self._correction_index

    def correction_history(self, source: str) -> list[dict]:
        """The records a record's corrections join it to (ADR-0006 6): those it corrects
        (`supersedes`) and those that correct it, each in turn, in journal order — `source`, `state`,
        `corrects` and `corrected_by` of each. Empty when nothing corrects it and it corrects nothing.
        A correction history has no version numbers."""
        by, corrects, order = self._corrections()
        if source not in by and source not in corrects:
            return []
        joined, todo = {source}, [source]
        while todo:
            name = todo.pop()
            for other in by.get(name, []) + corrects.get(name, []):
                if other not in joined:
                    joined.add(other)
                    todo.append(other)
        replay = self.journal.replay()

        def place(name: str) -> int:
            return order.get(name, len(order))

        return [{"source": name, "state": (replay.state_of(name) or {}).get("state"),
                 "corrects": sorted(corrects.get(name, []), key=place),
                 "corrected_by": sorted(by.get(name, []), key=place)}
                for name in sorted(joined, key=place)]

    # --- record states -----------------------------------------------------------------------------

    def record_states(self) -> dict[str, dict]:
        return self.journal.replay().record_states()

    def transition_allowed(self, target: str, expected, new) -> bool:
        return self.journal.replay().transition_allowed(target, expected, new)


def read(root, *, alive, sha, legacy_path=None, frame=None) -> ReadModel:
    """Read the project at `root` as it is. The files read are compared before and after; when they
    differ the read is made again, twice at most, and then the model says `updating` and keeps every
    source_revision it observed. `legacy_path` reads the paths of an older kit's runs (module
    docstring); without it they are read as recorded. `frame`, when given, is called before and
    after each attempt and gives `(legacy_path, state)`: the reading of an older kit's paths for that
    attempt, and a JSON value of what that reading is made of, compared and part of the
    source_revision like the files (core: a migrated project's mapping, design 5.4)."""
    root = Path(root)
    observed: list[str] = []
    for _ in range(3):
        legacy, state = frame() if frame is not None else (legacy_path, None)
        before = _snapshot(root, state)
        model = ReadModel(root, read_journal(root), alive=alive, sha=sha, legacy_path=legacy)
        after = _snapshot(root, frame()[1] if frame is not None else None)
        observed.append(_token(after, model.as_of_seq))
        if before == after:
            break
    else:
        model.updating = True
    model.observed_revisions = observed
    model.source_revision = observed[-1]
    model.source_snapshot = after
    return model


# --- analysis asset record/value rules (ADR-0010), shared with the Writer and application ---

ASSET_CONTRACT = "analysis_asset/1"
ASSET_WRITER_SINCE = "0.10.0"
ASSET_AUTOMATIC_SINCE = "0.12.0"
ASSET_HOST_SINCE = "0.13.0"
ASSET_PUBLIC_KINDS = ("asset_candidate", "asset_check", "asset_effect")
ASSET_SYSTEM_KINDS = ("asset_mode", "asset_notification")
ASSET_KINDS = ASSET_PUBLIC_KINDS + ASSET_SYSTEM_KINDS
ASSET_ARTIFACT_KINDS = ("function", "module", "package", "skill")
ASSET_REASONS = ("demand", "checks", "scope", "benefit", "source", "identity")
ASSET_STATES = (
    "proposed",
    "trial",
    "validated",
    "registered",
    "deferred",
    "rejected",
    "deprecated",
)
ASSET_STATE_RELATIONS = frozenset({"supersedes", "updates", "resolves"})
ASSET_SHA = re.compile(r"[0-9a-f]{64}")
ASSET_EVENT = re.compile(r"e[1-9][0-9]*")
ASSET_RUN = re.compile(r"[0-9]{8}-[0-9]{6}-[a-z0-9][a-z0-9-]{0,60}")
ASSET_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
ASSET_DEVICES = frozenset(
    {"con", "prn", "aux", "nul", *[f"{stem}{i}" for stem in ("com", "lpt") for i in range(1, 10)]}
)


class AssetRecordError(ValueError):
    """A value would make an invalid asset record; the Writer turns it into a handled error."""


def _asset_fail(message: str):
    raise AssetRecordError(message)


def asset_name(value) -> str:
    if not isinstance(value, str) or not ASSET_NAME.fullmatch(value) or value in ASSET_DEVICES:
        _asset_fail(
            "asset_id/name is a portable lower-case ASCII name of 1 to 63 letters, digits, '_' or '-'"
        )
    return value


def _asset_text(details: dict, key: str, *, empty: bool = False) -> str:
    value = details.get(key)
    if not isinstance(value, str) or (not empty and not value.strip()):
        _asset_fail(f"{key} is {'a' if empty else 'a non-empty'} string")
    value.encode("utf-8")
    return value


def _asset_event(value, key="candidate_event_id") -> str:
    if not isinstance(value, str) or not ASSET_EVENT.fullmatch(value):
        _asset_fail(f"{key} is an exact event id")
    return value


def asset_exact(ref, kind: str) -> dict:
    if not isinstance(ref, dict) or ref.get("type") != kind:
        _asset_fail(f"an exact {kind} reference is required")
    if "project_id" in ref:
        _asset_fail("asset references are local to this project; a project_id-qualified target is unsupported")
    out = {
        "relation": "cites",
        "basis": "declared",
        "version_binding": "author",
        **copy.deepcopy(ref),
    }
    if out["version_binding"] != "author" or out["basis"] not in ("declared", "observed"):
        _asset_fail(f"{kind} is author-bound with observed or declared basis")
    if kind in ("code_snapshot", "data_observation", "run"):
        if not isinstance(out.get("run_id"), str) or not ASSET_RUN.fullmatch(out["run_id"]):
            _asset_fail(f"{kind} needs an exact run_id")
    if kind in ("code_snapshot", "data_observation"):
        if not isinstance(out.get("path"), str) or not out["path"]:
            _asset_fail(f"{kind} needs a path")
        asset_bundle_path(out["path"])
        digest = out.get("digest")
        if (
            not isinstance(digest, dict)
            or not isinstance(digest.get("sha256"), str)
            or not ASSET_SHA.fullmatch(digest["sha256"])
        ):
            _asset_fail(f"{kind} needs its recorded sha256 digest")
    if kind == "data_observation" and out.get("role") not in ("input", "output"):
        _asset_fail("a data observation's role is input or output")
    if kind in ("asset_candidate", "reuse_proposal"):
        if kind == "asset_candidate":
            asset_name(out.get("asset_id"))
        elif not isinstance(out.get("proposal_id"), str) or not out["proposal_id"]:
            _asset_fail("a legacy proposal reference keeps its original non-empty proposal_id")
        _asset_event(out.get("event_id"), "event_id")
    return out


def asset_bundle_path(value) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or PurePosixPath(value).is_absolute()
        or str(PurePosixPath(value)) != value
        or any(
            part in (".", "..")
            or not part
            or part.endswith((".", " "))
            or part.split(".")[0].lower() in ASSET_DEVICES
            or ":" in part
            for part in value.split("/")
        )
    ):
        _asset_fail(
            "a bundle path is a portable relative file path without links or parent traversal"
        )
    return value


def asset_files(details: dict) -> list[dict]:
    values = details.get("files")
    if (
        values is None
        and details.get("artifact_kind") in ("function", "module")
        and details.get("code")
    ):
        code = asset_exact(details["code"], "code_snapshot")
        values = [{"path": PurePosixPath(code["path"]).name, "ref": code}]
    if values is None:
        return []
    if not isinstance(values, list) or not values:
        _asset_fail("files is a non-empty array of {path, ref}")
    output, paths = [], set()
    for item in values:
        if not isinstance(item, dict):
            _asset_fail("each file is {path, ref}")
        path = asset_bundle_path(item.get("path"))
        folded = unicodedata.normalize("NFC", path).casefold()
        if folded in paths or any(
            folded.startswith(p + "/") or p.startswith(folded + "/") for p in paths
        ):
            _asset_fail("bundle paths are distinct, including case and parent/file conflicts")
        paths.add(folded)
        ref = item.get("ref")
        kind = ref.get("type") if isinstance(ref, dict) else None
        if kind not in ("code_snapshot", "data_observation"):
            _asset_fail("a file refers to a code snapshot or data observation")
        output.append({"path": path, "ref": asset_exact(ref, kind)})
    if details.get("artifact_kind") in ("function", "module") and len(output) != 1:
        _asset_fail("a function/module is one code file; use package for a tree")
    if details.get("artifact_kind") in ("function", "module", "package") and details.get("code"):
        code_target = asset_target(asset_exact(details["code"], "code_snapshot"))
        if not any(asset_target(f["ref"]) == code_target for f in output):
            _asset_fail("the calculation code version is one of the candidate's artifact files")
    if details.get("artifact_kind") == "skill" and "SKILL.md" not in [f["path"] for f in output]:
        _asset_fail("a skill bundle includes SKILL.md at its root")
    return sorted(output, key=lambda f: f["path"])


def asset_bundle_digest(details: dict) -> str | None:
    entries = asset_files(details)
    if not entries:
        return None
    intent = [{"path": f["path"], "sha256": f["ref"]["digest"]["sha256"]} for f in entries]
    return hashlib.sha256(
        json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def asset_validate(kind: str, details, *, system: bool = False) -> dict:
    if kind not in (ASSET_KINDS if system else ASSET_PUBLIC_KINDS):
        _asset_fail(f"{kind!r} is not a public asset record kind")
    if not isinstance(details, dict):
        _asset_fail("asset details is an object")
    try:
        json.dumps(details, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        _asset_fail(f"asset details is a finite JSON object: {exc}")
    out = copy.deepcopy(details)
    if out.get("contract", ASSET_CONTRACT) != ASSET_CONTRACT:
        _asset_fail("unknown analysis asset details contract")
    out["contract"] = ASSET_CONTRACT
    if kind == "asset_mode":
        if out.get("mode") not in ("on_demand", "always_on") or out.get("scope") not in (
            "project",
            "session",
        ):
            _asset_fail("asset mode is on_demand/always_on with project/session scope")
        if out["scope"] == "session":
            _asset_text(out, "session")
        return out
    asset_name(out.get("asset_id"))
    if kind == "asset_candidate":
        for key in ("title", "problem"):
            _asset_text(out, key)
        for key in ("conditions", "exclusions", "next_check"):
            _asset_text(out, key, empty=True)
        if out.get("artifact_kind") not in ASSET_ARTIFACT_KINDS:
            _asset_fail("artifact_kind is function, module, package or skill")
        for key in ("input_contract", "output_contract"):
            if key in out:
                _asset_text(out, key, empty=True)
        if "code" in out:
            out["code"] = asset_exact(out["code"], "code_snapshot")
        if "files" in out:
            out["files"] = asset_files(out)
        if "previous_event_id" in out:
            _asset_event(out["previous_event_id"], "previous_event_id")
        if "derived_from" in out:
            asset_target = out["derived_from"]
            kind_ = asset_target.get("type") if isinstance(asset_target, dict) else None
            if kind_ not in ("asset_candidate", "reuse_proposal"):
                _asset_fail("derived_from is an exact asset candidate or legacy proposal")
            out["derived_from"] = asset_exact(asset_target, kind_)
        if "used_in" in out:
            if not isinstance(out["used_in"], list):
                _asset_fail("used_in is an array of exact declared run references")
            out["used_in"] = [asset_exact(ref, "run") for ref in out["used_in"]]
        return out
    _asset_event(out.get("candidate_event_id"))
    if kind == "asset_check":
        if out.get("result") not in ("passed", "failed") or type(out.get("boundary")) is not bool:
            _asset_fail("an asset check has passed/failed result and boolean boundary")
        _asset_text(out, "check_scope")
        out["check_run"] = asset_exact(out.get("check_run"), "run")
        out["check_code"] = asset_exact(out.get("check_code"), "code_snapshot")
        if out["check_code"]["run_id"] != out["check_run"]["run_id"]:
            _asset_fail("check_code is the checking run's snapshot")
        if "code" in out:
            out["code"] = asset_exact(out["code"], "code_snapshot")
        if not isinstance(out.get("artifact_digest"), str) or not ASSET_SHA.fullmatch(
            out["artifact_digest"]
        ):
            _asset_fail("an asset check names the tested bundle's sha256 artifact_digest")
    elif kind == "asset_effect":
        if out.get("effect_basis") not in ("estimated", "observed"):
            _asset_fail("effect_basis is estimated or observed")
        _asset_text(out, "description")
        if out["effect_basis"] == "observed":
            for key in ("authoring_cost", "maintenance_cost", "measurement"):
                _asset_text(out, key)
    else:
        if out.get("trigger") not in ("requested", "automatic"):
            _asset_fail("notification trigger is requested or automatic")
        for key in ("surface", "policy_version"):
            _asset_text(out, key)
        if type(out.get("as_of_seq")) is not int or out["as_of_seq"] < 0:
            _asset_fail("notification as_of_seq is a non-negative integer")
        if "context_version" not in out or (
            out["context_version"] is not None and not isinstance(out["context_version"], str)
        ):
            _asset_fail("notification context_version is a version string or null")
        if (
            not isinstance(out.get("evidence"), list)
            or not isinstance(out.get("scores"), dict)
            or not isinstance(out.get("requirements"), dict)
        ):
            _asset_fail("notification keeps evidence, scores and requirements")
    return out


def asset_candidate_ref(details: dict, relation: str = "about") -> dict:
    return {
        "type": "asset_candidate",
        "asset_id": details["asset_id"],
        "event_id": details["candidate_event_id"],
        "relation": relation,
        "basis": "declared",
        "version_binding": "author",
    }


def asset_semantic_refs(kind: str, details: dict) -> list[dict]:
    """Only the feature's producer creates these relationships; caller references are citations."""
    refs = []
    if kind == "asset_candidate":
        if details.get("previous_event_id"):
            refs.append(
                asset_candidate_ref(
                    {**details, "candidate_event_id": details["previous_event_id"]}, "supersedes"
                )
            )
        if details.get("derived_from"):
            refs.append({**details["derived_from"], "relation": "derived_from"})
        if details.get("code"):
            refs.append({**details["code"], "relation": "uses"})
        refs.extend({**f["ref"], "relation": "uses"} for f in asset_files(details))
        refs.extend({**ref, "relation": "uses"} for ref in details.get("used_in", []))
    elif kind in ("asset_check", "asset_effect", "asset_notification"):
        refs.append(asset_candidate_ref(details))
        if kind == "asset_check":
            refs.extend({**details[key], "relation": "uses"} for key in ("check_run", "check_code"))
            if details.get("code"):
                refs.append({**details["code"], "relation": "uses"})
    seen, result = set(), []
    for ref in refs:
        token = json.dumps(ref, sort_keys=True, ensure_ascii=False)
        if token not in seen:
            result.append(ref)
            seen.add(token)
    return result


def asset_input_refs(refs) -> None:
    for ref in refs:
        if isinstance(ref, dict) and ref.get("relation") in ASSET_STATE_RELATIONS:
            _asset_fail(
                "asset state-changing references are generated from details; caller refs are citations"
            )
        if isinstance(ref, dict) and ref.get("relation") in ("about", "uses"):
            _asset_fail(
                "asset about/uses references are generated from details; cite sources with cites/supports"
            )


def asset_record_problem(kind: str, details, refs) -> str | None:
    """Reader check of structural values and producer-owned edges, without reference resolution."""
    try:
        if not isinstance(details, dict) or details.get("contract") != ASSET_CONTRACT:
            _asset_fail("an asset record declares its analysis_asset/1 contract")
        value = asset_validate(kind, details, system=True)
        expected = asset_semantic_refs(kind, value)
        if not isinstance(refs, list):
            _asset_fail("asset refs is an array")
        used = []
        for ref in refs:
            if not isinstance(ref, dict):
                _asset_fail("asset refs contains an object")
            if ref.get("relation") in (*ASSET_STATE_RELATIONS, "about", "uses"):
                if ref not in expected or ref in used:
                    _asset_fail("asset semantic reference conflicts with details or appears twice")
                used.append(ref)
        if any(ref not in refs for ref in expected):
            _asset_fail("asset record is missing a generated semantic reference")
    except (AssetRecordError, UnicodeError, TypeError) as exc:
        return str(exc)
    return None


def asset_target(ref: dict) -> dict:
    """Version identity, independent of the sentence's relation and declared/observed label."""
    return {k: v for k, v in ref.items() if k not in ("relation", "basis", "version_binding")}


def asset_input_set(run: dict) -> tuple | None:
    """Unknown input digests never establish independent use or a distinct checking input."""
    entries = run.get("inputs")
    if not isinstance(entries, list):
        return None
    result = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("sha256"), str)
            or not ASSET_SHA.fullmatch(entry["sha256"])
        ):
            return None
        result.append((entry["path"], entry["sha256"]))
    return tuple(sorted(set(result)))


def asset_candidate(journal, asset_id: str, event_id: str) -> dict:
    event = journal.event_of(event_id)
    d = event.value.get("details") if event is not None else None
    if (
        event is None
        or event.kind != "asset_candidate"
        or not isinstance(d, dict)
        or d.get("asset_id") != asset_id
        or asset_event_problem(event.value)
    ):
        _asset_fail("the exact candidate version does not exist or does not match asset_id")
    return event.value


def asset_candidate_versions(journal, asset_id: str) -> list[dict]:
    result = []
    for event in journal.in_order():
        d = event.value.get("details")
        if (
            event.kind == "asset_candidate"
            and isinstance(d, dict)
            and d.get("asset_id") == asset_id
        ):
            if (
                not asset_event_problem(event.value)
                and journal.source_of(event) == event.id
            ):
                result.append(event.value)
    return result


def asset_original_run(details: dict, model) -> dict | None:
    ref = details.get("code") or (asset_files(details)[0]["ref"] if asset_files(details) else None)
    return model.run(ref["run_id"]) if ref else None


def _asset_source_file(ref: dict, model) -> tuple[str, str] | None:
    if ref["type"] == "data_observation":
        return ref["path"], ref["digest"]["sha256"]
    run = model.run(ref["run_id"])
    if not run:
        return None
    entries = [run.get("script", {}), *(run.get("modules") or [])]
    for entry in entries:
        if (
            isinstance(entry, dict)
            and entry.get("snapshot") == ref["path"]
            and entry.get("sha256") == ref["digest"]["sha256"]
        ):
            return entry.get("path"), entry["sha256"]
    return None


def asset_check_matches(details: dict, proposed: dict, model, *, passed: bool = False) -> bool:
    """The declared result belongs to this artifact version and an actually recorded checking run."""
    if details.get("artifact_digest") != asset_bundle_digest(proposed) or not asset_files(proposed):
        return False
    if proposed.get("code") and asset_target(details.get("code") or {}) != asset_target(
        proposed["code"]
    ):
        return False
    run = model.run(details["check_run"]["run_id"])
    if not run or run.get("id") != details["check_run"]["run_id"] or (passed and (details["result"] != "passed" or run.get("status") != "ok")):
        return False
    script = run.get("script") or {}
    check_code = details["check_code"]
    if (
        script.get("snapshot") != check_code["path"]
        or script.get("sha256") != check_code["digest"]["sha256"]
    ):
        return False
    required = [
        details["check_run"],
        check_code,
        *([details["code"]] if details.get("code") else []),
    ]
    required += [f["ref"] for f in asset_files(proposed)]
    if not all(model.reference(ref)["confirmed"] for ref in required):
        return False
    observed = {
        (item.get("path"), item.get("sha256"))
        for item in [
            script,
            *(run.get("modules") or []),
            *(run.get("inputs") or []),
            *(run.get("outputs") or []),
        ]
        if isinstance(item, dict)
    }
    return all(_asset_source_file(f["ref"], model) in observed for f in asset_files(proposed))


def asset_validate_context(kind: str, details: dict, journal, model) -> None:
    """Checks that need other records, repeated under the same journal lock as the append."""
    if kind == "asset_candidate":
        versions = asset_candidate_versions(journal, details["asset_id"])
        previous = details.get("previous_event_id")
        if (bool(versions) != bool(previous)) or (versions and versions[-1]["id"] != previous):
            _asset_fail("a candidate revision names its same asset_id's single latest version")
        if previous:
            asset_candidate(journal, details["asset_id"], previous)
        if (
            details.get("derived_from")
            and not model.reference(details["derived_from"])["confirmed"]
        ):
            _asset_fail("derived_from is a confirmed prior candidate")
        return
    if kind in ("asset_check", "asset_effect", "asset_notification"):
        found = asset_candidate(journal, details["asset_id"], details["candidate_event_id"])
        if kind == "asset_check" and not asset_check_matches(details, found["details"], model):
            _asset_fail(
                "the check's run, checking code and observed artifact files do not match this candidate version"
            )


def asset_passed_checks(journal, proposed: dict, model) -> list[dict]:
    output = []
    for event in journal.in_order():
        d = event.value.get("details")
        if (
            event.kind != "asset_check"
            or not isinstance(d, dict)
            or d.get("asset_id") != proposed["details"]["asset_id"]
            or d.get("candidate_event_id") != proposed["id"]
            or asset_event_problem(event.value)
        ):
            continue
        if (not asset_event_problem(event.value) and model.reference({"type": "event", "event_id": event.id,
                "relation": "supports", "basis": "declared", "version_binding": "author"})["confirmed"]
                and all(r["confirmed"] for r in model.references(event.id))
                and asset_check_matches(d, proposed["details"], model, passed=True)):
            output.append(event.value)
    return output


def asset_promotion(proposed: dict, new: str, journal, model) -> None:
    d = proposed["details"]
    if new == "trial":
        if (
            not asset_files(d)
            or (d["artifact_kind"] != "skill" and not d.get("code"))
            or not all(
                isinstance(d.get(k), str) and d[k].strip()
                for k in ("input_contract", "output_contract", "next_check")
            )
        ):
            _asset_fail("trial needs the artifact version, input/output contracts and next check")
        if not all(model.reference(f["ref"])["confirmed"] for f in asset_files(d)):
            _asset_fail("trial's artifact files are confirmed exact versions")
        if (
            asset_original_run(d, model) is None
            or asset_input_set(asset_original_run(d, model)) is None
        ):
            _asset_fail("trial needs the source run's known input set")
    elif new == "validated":
        checks = asset_passed_checks(journal, proposed, model)
        original = asset_original_run(d, model)
        original_inputs = asset_input_set(original) if original else None
        independent = [
            c
            for c in checks
            if asset_input_set(model.run(c["details"]["check_run"]["run_id"])) is not None
            and asset_input_set(model.run(c["details"]["check_run"]["run_id"])) != original_inputs
        ]
        if not checks or not independent or not any(c["details"]["boundary"] for c in checks):
            _asset_fail(
                "validated needs passed success, different-input and boundary checks for this artifact version"
            )


def asset_promotion_proof(proposed: dict, new: str, journal, model) -> dict:
    asset_promotion(proposed, new, journal, model)
    run = asset_original_run(proposed["details"], model)
    return {
        "artifact_digest": asset_bundle_digest(proposed["details"]),
        "source_run_id": run.get("id") if run else None,
        "check_events": [c["id"] for c in asset_passed_checks(journal, proposed, model)]
        if new == "validated"
        else [],
    }


def asset_update_problem(details, refs) -> str | None:
    """The asset form of a record_update; unrelated generic record updates keep their own contract."""
    try:
        if not isinstance(details, dict) or details.get("contract") != ASSET_CONTRACT:
            _asset_fail("an asset update declares its analysis_asset/1 contract")
        asset_name(details.get("asset_id"))
        _asset_event(details.get("candidate_event_id"))
        expected, new = details.get("expected_state"), details.get("new_state")
        if expected not in ASSET_STATES or new not in ASSET_STATES:
            _asset_fail("an asset update names the expected and new asset state")
        if refs != [asset_candidate_ref(details, "updates")]:
            _asset_fail("an asset update has one generated updates target matching its details")
        if "demand" in details and (
            not isinstance(details["demand"], dict)
            or type(details["demand"].get("repeated_need")) is not bool
        ):
            _asset_fail("demand.repeated_need is boolean")
        if "identity" in details:
            identity = details["identity"]
            if not isinstance(identity, dict) or identity.get("relation") not in (
                "duplicate_of",
                "merged_into",
            ):
                _asset_fail("identity relation is duplicate_of or merged_into")
            ref = identity.get("target")
            kind = ref.get("type") if isinstance(ref, dict) else None
            if kind not in ("asset_candidate", "reuse_proposal"):
                _asset_fail("identity target is an exact candidate")
            asset_exact(ref, kind)
        if expected == new and not any(key in details for key in ("demand", "identity")):
            _asset_fail("state-maintaining asset updates carry demand or identity metadata")
        reasons = details.get("reason_items", [])
        if not isinstance(reasons, list) or any(r not in ASSET_REASONS for r in reasons):
            _asset_fail("reason_items names adopted required/score items")
        if "suggestion_history" in details:
            requirements = details.get("requirements")
            if (details["suggestion_history"] != "analysis_assets.suggestions/1"
                    or new not in ("rejected", "deferred") or not isinstance(requirements, dict)
                    or not {"source", "identity"} <= set(requirements)
                    or any(type(value) is not bool for value in requirements.values())):
                _asset_fail("suggestion choice history preserves boolean requirements of its known contract")
        if new in ("rejected", "deferred"):
            if (
                type(details.get("as_of_seq")) is not int
                or details["as_of_seq"] < 0
                or "context_version" not in details
                or not isinstance(details.get("evidence"), list)
                or not isinstance(details.get("scores"), dict)
            ):
                _asset_fail(
                    "rejection/deferral keeps the read basis, evidence versions and item scores"
                )
        if expected != new and new in ("trial", "validated"):
            proof = details.get("promotion")
            if (
                not isinstance(proof, dict)
                or not isinstance(proof.get("artifact_digest"), str)
                or not ASSET_SHA.fullmatch(proof["artifact_digest"])
                or not isinstance(proof.get("check_events"), list)
                or (new == "validated" and not proof["check_events"])
            ):
                _asset_fail("asset promotion preserves its artifact version and checking event IDs")
            for event_id in proof["check_events"]:
                _asset_event(event_id, "check_events")
        if new == "registered":
            registration = details.get("registration")
            if not isinstance(registration, dict):
                _asset_fail("registered preserves the explicit registration receipt")
            registration_id = registration.get("registration_id")
            if (registration.get("schema") != "analytics.asset_registration/1" or
                    not isinstance(registration_id, str) or not re.fullmatch(r"[0-9a-f]{32}", registration_id) or
                    registration.get("candidate_event_id") != details["candidate_event_id"] or
                    registration.get("asset_id") != details["asset_id"] or registration.get("state") != "published" or
                    registration.get("source") != f"assets/candidates/{details['asset_id']}/{details['candidate_event_id']}" or
                    registration.get("receipt") != f".analytics/asset-registrations/{registration_id}/receipt.json" or
                    registration.get("artifact_kind") not in ASSET_ARTIFACT_KINDS):
                _asset_fail("registration preserves this candidate's owned published receipt and paths")
            asset_bundle_path(registration.get("destination"))
            digest = registration.get("artifact_digest")
            if not isinstance(digest, str) or not ASSET_SHA.fullmatch(digest):
                _asset_fail("registration preserves the exact artifact bundle digest")
            entries = registration.get("files")
            if (not isinstance(entries, list) or not entries or
                    any(not isinstance(item, dict) or not isinstance(item.get("sha256"), str) or
                        not ASSET_SHA.fullmatch(item["sha256"]) for item in entries)):
                _asset_fail("registration preserves file paths and sha256 values")
            for item in entries:
                asset_bundle_path(item.get("path"))
            checks = registration.get("check_events")
            if not isinstance(checks, list) or not checks:
                _asset_fail("registration preserves its checking event IDs")
            for event_id in checks:
                _asset_event(event_id, "check_events")
    except (AssetRecordError, TypeError, UnicodeError) as exc:
        return str(exc)
    return None


def asset_update_targets(value: dict, journal) -> bool:
    details = value.get("details")
    tag = details.get("contract") if isinstance(details, dict) else None
    if isinstance(tag, str) and tag.startswith("analysis_asset/"):
        return True
    refs = value.get("refs")
    if not isinstance(refs, list):
        return False
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("relation") != "updates":
            continue
        if ref.get("type") == "asset_candidate":
            return True
        found = journal.event_of(ref.get("event_id"))
        if found is not None and found.kind == "asset_candidate":
            return True
    return False


def asset_event_problem(value: dict) -> str | None:
    """New asset records retain their full envelope as well as their typed payload semantics."""
    if (value.get("schema") != EVENT_V2 or type(value.get("seq")) is not int or value["seq"] < 1
            or not isinstance(value.get("id"), str) or not ASSET_EVENT.fullmatch(value["id"])
            or not isinstance(value.get("actor"), str) or not value["actor"]
            or not isinstance(value.get("text"), str) or not value["text"].strip()
            or value.get("actor_basis") not in ("declared", "inferred")
            or value.get("means") not in ("cli", "sdk", "tool", "command", "hook")):
        return "asset record envelope has a positive seq, exact id, actor/origin and text"
    try:
        at = datetime.fromisoformat(value.get("recorded_at", ""))
        if at.tzinfo is None:
            return "asset record time has a timezone"
        value["text"].encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        return "asset record time/text is not a timestamp/UTF-8 string"
    if value.get("kind") == "record_update":
        return asset_update_problem(value.get("details"), value.get("refs"))
    return asset_record_problem(value.get("kind"), value.get("details"), value.get("refs"))


def asset_update_context_problem(value: dict, journal) -> str | None:
    """Durable proof identity, using only prior journal bytes (never current file contents).

Full actual file/run/check admission is the Writer's pre-write job. Historical replay checks
that the stored proof belongs to its candidate/source/check versions, so later file drift
does not undo a valid historical transition and an invented proof cannot advance a draft.
"""
    problem = asset_event_problem(value)
    if problem:
        return problem
    d = value["details"]
    if len(journal.lines_of.get(value["id"], [])) != 1:
        return "asset update identity is ambiguous"
    ref = asset_candidate_ref(d, "updates")
    state, _, _ = journal.event_target("asset_candidate", ref, value["seq"])
    if state != "resolved":
        return "asset update needs its unique prior candidate version"
    proposed = journal.event_of(d["candidate_event_id"]).value
    candidate = proposed["details"]
    entries = asset_files(candidate)
    digest = asset_bundle_digest(candidate)
    if d["expected_state"] != d["new_state"] and d["new_state"] in ("trial", "validated"):
        proof = d["promotion"]
        if (not entries or (candidate["artifact_kind"] != "skill" and not candidate.get("code"))
                or not all(isinstance(candidate.get(k), str) and candidate[k].strip()
                           for k in ("input_contract", "output_contract", "next_check"))
                or proof["artifact_digest"] != digest):
            return "asset promotion proof differs from its candidate artifact/contract version"
        source = candidate.get("code") or entries[0]["ref"]
        if proof.get("source_run_id") != source["run_id"]:
            return "asset promotion proof differs from its candidate source run"
        for event_id in proof["check_events"]:
            checked = journal.event_of(event_id)
            if (checked is None or checked.kind != "asset_check" or checked.seq is None or
                    checked.seq >= value["seq"] or asset_event_problem(checked.value)):
                return "asset promotion proof needs unique prior checking records"
            check = checked.value["details"]
            if (check["asset_id"] != d["asset_id"] or check["candidate_event_id"] != proposed["id"] or
                    check["artifact_digest"] != digest or check["result"] != "passed" or
                    (candidate.get("code") and asset_target(check.get("code") or {}) != asset_target(candidate["code"]))):
                return "asset promotion proof checking record belongs to another artifact version"
    if d["new_state"] == "registered":
        registration = d["registration"]
        wanted = [{"path": entry["path"], "sha256": entry["ref"]["digest"]["sha256"]} for entry in entries]
        if registration["artifact_digest"] != digest or registration["files"] != wanted:
            return "asset registration proof differs from its candidate artifact version"
    return None


# --- bounded journal queries (Issue #15, ADR-0006 5) -----------------------------------------------

class QueryError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def query_text(value) -> str:
    """Unicode NFC/casefold substring vocabulary; SQL/FTS syntax is never a query language."""
    return unicodedata.normalize("NFC", str(value)).casefold()


class QueryPolicy:
    """Replaceable lexical ranking and pagination, independent of storage and host adapters.
    Full records are authoritative. Summaries say what they omit; detail fields are indivisible."""
    version = "lexical/4"

    def terms(self, query: str) -> list[str]:
        return query_text(query).split()

    def document(self, event: Event) -> str:
        return query_text(json.dumps(event.value, ensure_ascii=False, sort_keys=True))

    def matches(self, document: str, query: str) -> bool:
        return all(term in document for term in self.terms(query))

    def select(self, journal: Journal, *, query="", kind=None, state=None, since=None, until=None,
               candidates=None) -> list[Event]:
        replay = journal.replay()
        legacy = journal.legacy_resolutions()
        out = []
        for event in journal.in_order():
            source = journal.source_of(event)
            if candidates is not None and source not in candidates:
                continue
            judgment = replay.state_of(source) or {}
            if kind is not None and event.kind != kind:
                continue
            if state is not None and judgment.get("state") != state:
                continue
            if since is not None or until is not None:
                at = _instant(event.recorded_at)
                lo, hi = _instant(since), _instant(until)
                if at is None or at.utcoffset() is None or (lo is not None and not _not_after(lo, at)) or (
                        hi is not None and not _not_after(at, hi)):
                    continue
            if self.matches(self.document(event), query):
                out.append(event)
        if query:
            terms = self.terms(query)
            def rank(event):
                source = journal.source_of(event)
                text = query_text(event.value.get("text", ""))
                relevance = (sum(term in text for term in terms)
                             + int(query_text(source) == query_text(query))
                             + int(query_text(query) in text))
                current = (replay.state_of(source) or {}).get("state") in ("current", "open") and source not in legacy
                evidence = (any(bool(values) for values in event.refs.values())
                            if isinstance(event.refs, dict) else bool(_dicts(event.refs)))
                return (-relevance, -int(current), -int(evidence))
            groups: dict[tuple, list[Event]] = {}
            for event in sorted(out, key=lambda e: (rank(e), e.seq is None, e.seq or 0, e.line)):
                groups.setdefault(rank(event), []).append(event)
            out = []
            # Equal lexical/state/evidence scores cover both ends of recorded history. Recency
            # alone must not bury every older relevant judgment; all middle records stay paged.
            for group in groups.values():
                left, right = 0, len(group) - 1
                while left <= right:
                    out.append(group[left])
                    if left < right:
                        out.append(group[right])
                    left, right = left + 1, right - 1
        return out

    def page(self, rows: list, *, revision: str, request: dict, cursor=None, limit=8,
             journal_order=False, as_of_seq=None, boundary_revision=None) -> dict:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise QueryError("bad_query", "limit must be an integer from 1 to 100")
        fingerprint = hashlib.sha256(json.dumps([self.version, request, limit], sort_keys=True,
                                                ensure_ascii=True).encode("ascii")).hexdigest()
        position = 0
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > 4096:
                    raise ValueError()
                held = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
                if held["query"] != fingerprint:
                    raise QueryError("cursor_query_mismatch", "cursor belongs to another query")
                unchanged_prefix = (journal_order and as_of_seq is not None
                                    and held.get("boundary") == boundary_revision)
                if (journal_order and as_of_seq is not None and not unchanged_prefix) or (
                        held["revision"] != revision and not unchanged_prefix):
                    raise QueryError("stale_cursor", "project changed; restart this query", source_revision=revision)
                position = held["position"]
                if not isinstance(position, int) or isinstance(position, bool) or not 0 <= position <= len(rows):
                    raise ValueError()
            except QueryError:
                raise
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise QueryError("bad_cursor", "cursor cannot be read; restart this query") from None
        selected = rows[position:position + limit]
        end, more = position + len(selected), position + len(selected) < len(rows)
        token = (base64.urlsafe_b64encode(json.dumps({"revision": revision, "query": fingerprint,
                                                    "position": end, "boundary": boundary_revision},
                                                   separators=(",", ":")).encode()).decode()
                 if more else None)
        return {"items": selected, "returned": len(selected), "total": len(rows), "has_more": more,
                "cursor": token, "omitted": {"count": len(rows) - end,
                                               "reason": "page_limit" if more else None},
                "read_more": {**request, "limit": limit, "cursor": token} if more else None}


def query_target(judgment: dict) -> str | None:
    """A version-bound target's query identity. Unbound names remain names, never version IDs."""
    target = judgment.get("target") or {}
    if target.get("event_id") is not None:
        return target["event_id"]
    if judgment.get("type") == "run" and isinstance(target.get("run_id"), str):
        return "run:" + target["run_id"]
    if judgment.get("type") == "context_version" and isinstance(target.get("version"), str):
        return "context:" + target["version"]
    if target.get("visualization_id") is not None:
        return f"{target.get('run_id')}/{target['visualization_id']}"
    if target.get("report_id") is not None:
        return report_key(target.get("report_id"), target.get("revision_id"))
    for key in ("run_id", "version", "path", "key", "token"):
        if isinstance(target.get(key), str):
            return target[key]
    return None


def _query_ref_targets(ref: dict, target: str) -> bool:
    if query_target({"type": ref.get("type"), "target": ref}) == target:
        return True
    return target in [ref.get(k) for k in ("event_id", "path", "key", "token")]


def incoming_sources(journal: Journal, target: str) -> list[str]:
    """Cheap candidate sources before judging references; no hashing all unrelated run files."""
    out = []
    raw = target.partition(":")[2] if target.startswith(("run:", "context:")) else target
    for event in journal.in_order():
        refs = event.refs
        if event.schema == EVENT_V1:
            values = [v for key in V1_FIELDS for v in _strings((refs or {}).get(key))] if isinstance(refs, dict) else []
            found = raw in values
        else:
            found = any(_query_ref_targets(ref, target) for ref in _dicts(refs))
        if found:
            out.append(journal.source_of(event))
    return out


def observed_basis(root, frame=None) -> tuple[list, str]:
    """The cheap current snapshot and revision token used by read's before/after rule."""
    snapshot = _snapshot(Path(root), frame)
    return snapshot, _token(snapshot, read_journal(root).as_of_seq)
