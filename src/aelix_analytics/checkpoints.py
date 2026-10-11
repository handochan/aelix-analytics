"""Model-free checkpoint structure, selection and evaluation over the shared read model."""

from __future__ import annotations

import copy
import re

CONTRACT = "analysis_checkpoint/1"
SINCE = "0.14.0"
UNSET = object()
LIST_KINDS = {"metric_event_ids": "metric_definition", "finding_event_ids": "finding",
              "change_event_ids": None, "question_event_ids": "question", "todo_event_ids": "todo"}
ID = re.compile(r"e[1-9][0-9]*\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


class CheckpointError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def integer(value, name, low=0, high=None):
    if not isinstance(value, int) or isinstance(value, bool) or value < low or (high is not None and value > high):
        raise CheckpointError("bad_checkpoint_record", f"{name} must be an integer from {low}" +
                              (f" to {high}" if high is not None else ""))
    return value


def validate(details):
    if not isinstance(details, dict) or details.get("contract") != CONTRACT:
        raise CheckpointError("bad_checkpoint_record", "new checkpoints use analysis_checkpoint/1 details")
    out = copy.deepcopy(details)
    top = integer(out.get("as_of_seq"), "as_of_seq")
    integer(out.get("from_seq"), "from_seq", high=top)
    if "context_version" not in out or (out["context_version"] is not None and
            (not isinstance(out["context_version"], str) or not out["context_version"])):
        raise CheckpointError("bad_checkpoint_record", "context_version is an exact filename or explicit null")
    if out.get("summary_source") not in ("authored", "deterministic"):
        raise CheckpointError("bad_checkpoint_record", "summary_source is authored or deterministic")
    summary = out.get("summary")
    if not isinstance(summary, dict) or "goal" not in summary or not (summary["goal"] is None or isinstance(summary["goal"], str)):
        raise CheckpointError("bad_checkpoint_record", "summary.goal is a string or null")
    if "focus_event_id" not in summary or not (summary["focus_event_id"] is None or
            isinstance(summary["focus_event_id"], str) and ID.fullmatch(summary["focus_event_id"])):
        raise CheckpointError("bad_checkpoint_record", "summary.focus_event_id is an event ID or null")
    for key in LIST_KINDS:
        values = summary.get(key)
        if not isinstance(values, list) or any(not isinstance(v, str) or not ID.fullmatch(v) for v in values) or len(set(values)) != len(values):
            raise CheckpointError("bad_checkpoint_record", f"summary.{key} holds unique event IDs")
    actions = summary.get("next_actions")
    if not isinstance(actions, list) or any(not isinstance(v, str) for v in actions):
        raise CheckpointError("bad_checkpoint_record", "summary.next_actions holds explicit strings")
    basis = out.get("journal_basis")
    if not isinstance(basis, dict):
        raise CheckpointError("bad_checkpoint_record", "journal_basis is required")
    integer(basis.get("reader_format"), "reader_format")
    if not isinstance(basis.get("prefix_revision"), str) or not SHA.fullmatch(basis["prefix_revision"]):
        raise CheckpointError("bad_checkpoint_record", "journal_basis.prefix_revision is a sha256 consistency token")
    if not isinstance(out.get("omitted"), dict):
        raise CheckpointError("bad_checkpoint_record", "omitted selections are explicit")
    return out


def selected(details):
    summary = details["summary"]
    return ([summary["focus_event_id"]] if summary["focus_event_id"] else []) + [
        value for key in LIST_KINDS for value in summary[key]]


def changes(journal, targets, after=0, through=None, *, reader):
    """Applied state differences, including intervening changes that return to the original state.

    Replay remains the sole state interpreter. Only potential incoming change events need two
    prefix replays; unrelated appends do not require replaying every prefix.
    """
    relations = reader
    targets = set(targets)
    rows = []
    for event in journal.in_order():
        if event.seq is None or event.seq <= after or (through is not None and event.seq > through):
            continue
        refs = relations.Replay._links(event)
        if event.kind == "record_update" and event.schema == relations.EVENT_V2:
            refs += [("updates", ref) for ref in relations._dicts(event.refs) if ref.get("relation") == "updates"]
        affected = {ref.get("event_id") for _, ref in refs} & targets
        if not affected:
            continue
        before = journal.through(event.seq - 1).replay()
        now = journal.through(event.seq).replay()
        for target in sorted(affected):
            old, new = before.state_of(target), now.state_of(target)
            if old != new:
                rows.append({"target_id": target, "change_event_id": journal.source_of(event),
                             "before": old, "after": new,
                             "read_more": {"part": "records", "id": journal.source_of(event)}})
    return rows


def generated_refs(details, journal, context_digest=None):
    refs = []
    for event_id in selected(details):
        event = journal.event_of(event_id)
        kind = event.kind if event else "event"
        if kind == "metric_definition" and not event.value.get("details", {}).get("metric_id"):
            kind = "event"
        ref = {"type": kind if kind in ("analysis_focus", "metric_definition") else "event",
               "event_id": event_id, "relation": "about", "basis": "declared", "version_binding": "author"}
        if kind == "metric_definition":
            ref["metric_id"] = event.value.get("details", {}).get("metric_id")
        refs.append(ref)
    if details["context_version"] is not None:
        refs.append({"type": "context_version", "version": details["context_version"],
                     "digest": {"sha256": context_digest}, "relation": "cites",
                     "basis": "observed", "version_binding": "author"})
    return refs


def validate_selection(details, journal, *, reader):
    """Shared writer/reader selection semantics, without a physical-file judgment."""
    top = details["as_of_seq"]
    historical = journal.through(top)
    summary = details["summary"]
    fields = [("focus_event_id", "analysis_focus", [summary["focus_event_id"]] if summary["focus_event_id"] else [])]
    fields += [(key, kind, summary[key]) for key, kind in LIST_KINDS.items()]
    for field, kind, values in fields:
        for event_id in values:
            event = journal.event_of(event_id)
            if event is None or event.seq is None or event.seq > top or (kind and event.kind != kind):
                raise CheckpointError("checkpoint_reference", f"{event_id!r} is not a unique prior {kind or 'change'}")
            if kind in ("question", "todo") and (historical.replay().state_of(event_id) or {}).get("state") != "open":
                raise CheckpointError("checkpoint_reference", f"{event_id} was not open at the checkpoint basis")
            if field == "change_event_ids" and not changes(journal, journal.replay().states, after=event.seq-1, through=event.seq, reader=reader):
                raise CheckpointError("checkpoint_reference", f"{event_id} is not an applied judgment change")


def validate_refs(refs, *, reader):
    """Known tagged payloads validate every raw reference before reading nested values."""
    if not isinstance(refs, list):
        raise CheckpointError("bad_checkpoint_record", "checkpoint refs are a list")
    for ref in refs:
        if (not isinstance(ref, dict) or any(not isinstance(ref.get(key), str) for key in
                ("type", "relation", "basis", "version_binding")) or not reader._known_ref(ref)):
            raise CheckpointError("bad_checkpoint_record", "invalid checkpoint reference")
        for key in ("event_id", "run_id", "version", "path", "metric_id", "asset_id", "proposal_id", "token", "key"):
            if key in ref and not isinstance(ref[key], str):
                raise CheckpointError("bad_checkpoint_record", f"checkpoint reference {key} is a string")
        if ref.get("relation") in ("supersedes", "updates", "resolves"):
            raise CheckpointError("bad_checkpoint_record", "a checkpoint cannot change another record's state")
        if "digest" in ref:
            digest = ref["digest"]
            if not isinstance(digest, dict) or not isinstance(digest.get("sha256"), str) or not SHA.fullmatch(digest["sha256"]):
                raise CheckpointError("bad_checkpoint_record", "a checkpoint digest is a sha256 object")


def validate_basis(details, model, refs, *, reader, pending_run=None):
    relations = reader
    top = details["as_of_seq"]
    journal = model.journal
    if top > journal.as_of_seq:
        raise CheckpointError("checkpoint_reference", "checkpoint basis is beyond the observed journal")
    if details["journal_basis"]["reader_format"] != relations.READER_FORMAT:
        raise CheckpointError("checkpoint_basis_changed", "checkpoint reader format cannot be compared")
    if details["journal_basis"]["prefix_revision"] != journal.prefix_revision(top):
        raise CheckpointError("checkpoint_basis_changed", "the held journal prefix changed")
    validate_selection(details, journal, reader=reader)
    validate_refs(refs, reader=reader)
    historical = model.through(top)
    for ref in refs:
        if ref.get("version_binding") == "none":
            continue
        if pending_run is not None and ref.get("run_id") and historical.completion_seq(ref["run_id"]) is None:
            # Private SDK payload only. No evidence is accepted: record() at finalization must
            # refuse the same exact reference against the held call-time basis.
            continue
        # Completion must already be present, including explicitly named unfinished/future runs.
        if ref.get("run_id") and historical.completion_seq(ref["run_id"]) is None:
            raise CheckpointError("checkpoint_reference", "the referenced run completed after the checkpoint basis or is unfinished")
        if ref.get("type") == "context_version":
            completed = any(e.kind == "context" and e.seq is not None and e.seq <= top and
                            any(r.get("type") == "context_version" and r.get("version") == ref.get("version")
                                for r in relations._dicts(e.refs)) for e in journal.events)
            if not completed:
                raise CheckpointError("checkpoint_reference", "the CONTEXT version is not recorded at this basis")
        judgment = historical.reference(ref, before=top + 1)
        if judgment["state"] in ("missing", "unsupported", "ambiguous"):
            raise CheckpointError("checkpoint_reference", f"reference is {judgment['state']} at the checkpoint basis")
    return historical


def empty_summary(goal=None):
    return {"goal": goal, "focus_event_id": None, **{key: [] for key in LIST_KINDS}, "next_actions": []}


def select(model, *, reader, goal=None, from_seq=0, limit=8, explicit=(), authored=False):
    integer(limit, "limit", 1, 100)
    journal = model.journal
    integer(from_seq, "from_seq", high=journal.as_of_seq)
    summary = empty_summary(goal)
    qualifications = []
    rows = journal.in_order()
    states = journal.replay()
    if authored:
        for event_id in explicit:
            event = journal.event_of(event_id)
            if event is None:
                continue
            if event.kind == "analysis_focus":
                if summary["focus_event_id"] not in (None, event_id):
                    raise CheckpointError("checkpoint_reference", "authored checkpoint names multiple focuses")
                summary["focus_event_id"] = event_id
            for key, kind in LIST_KINDS.items():
                if kind == event.kind and event_id not in summary[key]:
                    summary[key].append(event_id)
        return summary, {"qualification": "authored selection; unselected totals are not inferred"}
    focuses = [e for e in rows if e.kind == "analysis_focus" and journal.source_of(e) == e.id and
               (states.state_of(e.id) or {}).get("state") == "current"]
    if len(focuses) == 1:
        summary["focus_event_id"] = focuses[0].id
    elif focuses:
        qualifications.append("focus_conflict")
    omitted = {}
    for key, kind in LIST_KINDS.items():
        if key == "change_event_ids":
            ids = list(dict.fromkeys(r["change_event_id"] for r in changes(journal, states.states, after=max(0, from_seq-1), reader=reader)))
        else:
            ids = [e.id for e in rows if e.kind == kind and journal.source_of(e) == e.id and
                   (kind != "finding" or e.seq is not None and e.seq >= from_seq) and
                   (kind not in ("question", "todo") or (states.state_of(e.id) or {}).get("state") == "open")]
            if kind == "metric_definition":
                ids = [event_id for event_id in ids if (states.state_of(event_id) or {}).get("state") == "current"]
                grouped = {}
                for event_id in ids:
                    metric_id = journal.event_of(event_id).value.get("details", {}).get("metric_id")
                    if metric_id is not None:
                        grouped.setdefault(metric_id, []).append(event_id)
                conflicted = {event_id for values in grouped.values() if len(values)>1 for event_id in values}
                if conflicted:
                    qualifications.append("metric_definition_conflict")
                    ids = [event_id for event_id in ids if event_id not in conflicted]
        summary[key] = ids[:limit]
        omitted[key] = {"count": len(ids)-len(summary[key]), "reason": "selection_limit" if len(ids)>limit else None,
                        "read_more": {"part": "records", "kind": kind, "as_of_seq": journal.as_of_seq}}
    if qualifications:
        omitted["qualification"] = qualifications
    return summary, omitted


def text_of(details, journal):
    summary = details["summary"]
    lines = [f"Analysis checkpoint through journal seq {details['as_of_seq']}.",
             "Goal: " + (summary["goal"] if summary["goal"] is not None else "unknown")]
    for event_id in selected(details):
        event = journal.event_of(event_id)
        state = (journal.replay().state_of(event_id) or {}).get("state", "no state")
        lines.append(f"{event_id}: {event.kind if event else 'unknown'}; state at basis: {state}.")
    lines.extend("Next action: " + action for action in summary["next_actions"])
    lines.append("Selected identities only; read each original record for its complete authored text.")
    return "\n".join(lines)


def evaluate(event, model, *, reader):
    relations = reader
    details = event.value.get("details")
    qualification = []
    out = {"id": model.journal.source_of(event), "text": event.value.get("text"), "details": details,
           "needs_review": None, "updates_available": None, "review_reasons": [],
           "qualification": qualification, "read_more": {"part": "records", "id": model.journal.source_of(event)}}
    if event.id is not None and len(model.journal.lines_of.get(event.id, [])) != 1:
        qualification.append("shared_checkpoint_id")
    if not isinstance(details, dict) or "contract" not in details:
        qualification.append("generic_checkpoint")
        return out
    if details.get("contract") != CONTRACT:
        qualification.append("unsupported_checkpoint_contract")
        return out
    try:
        details = validate(details)
    except CheckpointError:
        qualification.append("invalid_checkpoint")
        return out
    top = details["as_of_seq"]
    if event.seq is None or top >= event.seq or top > model.as_of_seq:
        qualification.append("invalid_checkpoint_basis")
        return out
    updates = [e for e in model.journal.in_order() if e.seq is not None and e.seq > top and e.line != event.line]
    out["updates_available"] = bool(updates)
    out["updates"] = [{"id": model.journal.source_of(e), "kind": e.kind, "seq": e.seq,
                       "read_more": {"part": "records", "id": model.journal.source_of(e)}} for e in updates]
    if details["journal_basis"]["reader_format"] != relations.READER_FORMAT:
        qualification.append("checkpoint_reader_format_mismatch")
        return out
    reasons = out["review_reasons"]
    if details["journal_basis"]["prefix_revision"] != model.journal.prefix_revision(top):
        reasons.append({"reason": "checkpoint_basis_changed"})
    historical = model.through(top)
    try:
        validate_selection(details, historical.journal, reader=reader)
        validate_refs(event.refs, reader=reader)
    except CheckpointError as error:
        qualification.append("invalid_checkpoint")
        out["validation_reason"] = str(error)
        out["needs_review"] = True if reasons else None
        return out
    expected = generated_refs(details, historical.journal,
                              next((r.get("digest", {}).get("sha256") for r in relations._dicts(event.refs)
                                    if r.get("type") == "context_version" and r.get("version") == details["context_version"]), None))
    if any(ref not in relations._dicts(event.refs) for ref in expected):
        qualification.append("invalid_checkpoint_references")
        out["needs_review"] = True if reasons else None
        return out
    reasons.extend({"reason": "selected_judgment_changed", **row} for row in changes(model.journal, selected(details), after=top, reader=reader))
    for ref in relations._dicts(event.refs):
        old = historical.reference(ref, before=top+1)
        current = model.reference(ref, before=top+1)
        if old["state"] != "resolved" or current["state"] != "resolved":
            reasons.append({"reason": "checkpoint_evidence", "at_basis": old, "now": current})
        if current.get("version_binding") != "author" or current.get("basis") not in ("observed", "declared"):
            qualification.append("unconfirmed_reference")
    if details["context_version"] is None:
        qualification.append("context_unknown")
    for event_id in selected(details):
        selected_event = historical.journal.event_of(event_id)
        if selected_event is None:
            reasons.append({"reason": "selected_record_unavailable", "target_id": event_id})
        elif selected_event.kind in relations.METRIC_KINDS and not selected_event.value.get("details", {}).get("contract"):
            qualification.append("generic_selected_record")
    if model.journal.damaged or model.journal.unsupported or any(e.seq is None for e in model.journal.events) or model.updating:
        qualification.append("journal_knowledge_incomplete")
        out["needs_review"] = True if reasons else None
        out["updates_available"] = True if updates else None
    else:
        out["needs_review"] = bool(reasons)
    out["interpretation"] = "Mechanical selected changes only; conclusion review and calculation checks remain separate."
    return out
