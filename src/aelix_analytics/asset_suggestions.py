"""Project-local deterministic suggestion bookkeeping; no host, model or analysis imports.

Scores remain the ProposalPolicy's responsibility. This application keeps user choices,
identity, the journal window and publication independent of a replacement policy.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import UTC, datetime, timedelta

from . import asset_contract as contract
from . import relations, writer

SINCE = "0.12.0"
HOST_SINCE = relations.ASSET_HOST_SINCE
SCORE_MAX = {"demand": 3, "checks": 3, "scope": 2, "benefit": 2}
DEFAULTS = {"mode": "on_demand", "min_score": 8, "cooldown_hours": 24,
            "measurement_targets": {"input_tokens_per_review": 1024, "output_tokens_per_review": 256}}


def _finite(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def settings(marker, *, mode=None):
    """Validate known settings without changing old keys or silently enabling a mode."""
    marker = marker if isinstance(marker, dict) else {}
    errors = []
    for key in ("reuse_proposals", "skill_proposals"):
        if key in marker:
            errors.append({"code": "legacy_asset_settings", "field": key,
                           "message": "Use asset_suggestions; legacy settings never enable automatic suggestions."})
    raw = marker.get("asset_suggestions", {})
    if not isinstance(raw, dict):
        errors.append({"code": "bad_asset_settings", "field": "asset_suggestions",
                       "message": "asset_suggestions is an object."})
        raw = {}
    result = {**copy.deepcopy(DEFAULTS), **copy.deepcopy(raw)}
    if mode is not None:
        result["mode"] = mode
    if result["mode"] not in ("on_demand", "always_on"):
        errors.append({"code": "bad_asset_settings", "field": "mode",
                       "message": "mode is on_demand or always_on."})
    if type(result["min_score"]) is not int or not 0 <= result["min_score"] <= 10:
        errors.append({"code": "bad_asset_settings", "field": "min_score",
                       "message": "min_score is an integer from 0 to 10."})
    hours = result["cooldown_hours"]
    if not _finite(hours) or hours < 24:
        errors.append({"code": "bad_asset_settings", "field": "cooldown_hours",
                       "message": "cooldown_hours is finite and at least 24; bool is not a number."})
    targets = result["measurement_targets"]
    if not isinstance(targets, dict):
        errors.append({"code": "bad_asset_settings", "field": "measurement_targets",
                       "message": "measurement_targets is an object."})
        targets = {}
    targets = {**DEFAULTS["measurement_targets"], **targets}
    for key in DEFAULTS["measurement_targets"]:
        value = targets[key]
        if not _finite(value) or value < 0:
            errors.append({"code": "bad_asset_settings", "field": f"measurement_targets.{key}",
                           "message": "A measurement target is a finite nonnegative number, not bool."})
    result["measurement_targets"] = targets
    try:
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError):
        errors.append({"code": "bad_asset_settings", "field": "asset_suggestions",
                       "message": "Settings contain only finite JSON values."})
    return result, errors


def instant(value):
    if not isinstance(value, (str, datetime)):
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _records(journal):
    return [event for event in journal.in_order()
            if event.seq is not None and journal.source_of(event) == event.id]


def _ambiguous_history(journal):
    """Shared or unbound history cannot erase a choice/window or split an identity group.

    Diagnose the original IDs without choosing an occurrence; all original lines remain readable.
    Unrelated generic record ambiguity is outside asset suggestion bookkeeping.
    """
    kinds = {"asset_candidate", "asset_notification", "asset_mode", "reuse_proposal", "reuse_notification", "reuse_mode"}
    ambiguous = set()
    for event in journal.events:
        if event.seq is not None and journal.source_of(event) == event.id:
            continue
        details = event.value.get("details")
        choice = event.kind == "record_update" and isinstance(details, dict) and (
            details.get("contract") == contract.CONTRACT or "asset_id" in details or "proposal_id" in details)
        if event.kind in kinds or choice:
            ambiguous.add(event.id if event.id is not None else journal.source_of(event))
    return sorted(ambiguous)


def _mode(journal):
    return next((event.value for event in reversed(_records(journal))
                 if event.kind == "asset_mode" and event.value.get("actor") == "human"
                 and not contract.event_problem(event.value)
                 and event.value["details"].get("scope") == "project"), None)


def _session_mode(journal, session):
    if not isinstance(session, str) or not session:
        return None
    return next((event.value for event in reversed(_records(journal))
                 if event.kind == "asset_mode" and event.value.get("actor") == "human"
                 and event.value.get("means") == "command" and event.value.get("session") == session
                 and not contract.event_problem(event.value) and event.value["details"].get("scope") == "session"
                 and event.value["details"].get("session") == session), None)


def _mode_errors(marker, journal, config):
    latest = _mode(journal)
    if latest is None:
        return []
    desired = latest["details"].get("settings")
    if not isinstance(desired, dict) or marker.get("asset_suggestions") != desired or config["mode"] != latest["details"]["mode"]:
        return [{"code": "asset_mode_incomplete", "field": "asset_suggestions",
                 "event_id": latest["id"],
                 "message": "Mode history and marker differ; repeat the human mode command to repair them."}]
    return []


def set_mode(assets, mode, *, scope="project"):
    assets.writer.admit("set_asset_mode")
    if assets.writer.origin.actor != "human":
        raise writer.AnalyticsError("human_mode_required", "Only a human changes asset suggestion mode.")
    if mode not in ("on_demand", "always_on"):
        raise writer.AnalyticsError("bad_asset_settings", "mode is on_demand or always_on")
    if scope not in ("project", "session"):
        raise writer.AnalyticsError("bad_asset_settings", "scope is project or session")
    session = assets.writer.origin.session
    if scope == "session" and (assets.writer.origin.means != "command" or not isinstance(session, str) or not session):
        raise writer.AnalyticsError("asset_session_required", "Session mode needs the current Aelix human command session.")
    with assets.writer.locked("set_asset_mode") as held:
        config, errors = settings(held.state.value, mode=mode)
        if errors:
            raise writer.AnalyticsError(errors[0]["code"], "Asset suggestion settings are invalid.", details={"settings_errors": errors})
        if scope == "session":
            latest = _session_mode(held.journal, session)
            if latest is not None and latest["details"]["mode"] == mode:
                return {"mode": mode, "scope": scope, "changed": False, "repaired": False, "event": latest, "model_calls": 0}
            event = held.append("asset_mode", f"Asset suggestion session mode: {mode}",
                                details={"contract": contract.CONTRACT, "mode": mode, "scope": scope, "session": session},
                                requires=HOST_SINCE)
            return {"mode": mode, "scope": scope, "changed": True, "repaired": False, "event": event, "model_calls": 0}
        latest = _mode(held.journal)
        same = latest is not None and latest["details"].get("settings") == config
        if same and held.state.value.get("asset_suggestions") == config:
            return {"mode": mode, "scope": "project", "changed": False, "repaired": False, "event": latest, "model_calls": 0}
        if same:
            event = latest
            held.require(SINCE)
        else:
            event = held.append("asset_mode", f"Asset suggestion mode: {mode}",
                                details={"contract": contract.CONTRACT, "mode": mode, "scope": "project", "settings": config},
                                requires=SINCE)
        value = {**held.state.value, "asset_suggestions": config}
        try:
            writer.write_json(assets.writer.marker, value, platform=assets.writer.platform)
        except OSError as exc:
            raise writer.AnalyticsError("asset_mode_incomplete", "Mode event was recorded but marker update failed; repeat the mode command.",
                                        details={"event_id": event["id"], "mode": mode, "marker_updated": False}) from exc
        return {"mode": mode, "scope": "project", "changed": not same, "repaired": same, "event": event, "model_calls": 0}


def _key(kind, identity):
    return f"{kind}:{identity}"


def _candidate_key(value):
    details = value.get("details") or {}
    return _key("reuse_proposal", details.get("proposal_id")) if value.get("kind") == "reuse_proposal" else _key("asset_candidate", details.get("asset_id"))


def groups(model):
    """Union only confirmed, prior, exact identity links. Logical namespaces never collide."""
    journal = model.journal
    parents, candidates = {}, {}
    for event in _records(journal):
        if event.kind == "asset_candidate" and not contract.event_problem(event.value):
            candidates[event.id] = event.value
            parents[_candidate_key(event.value)] = _candidate_key(event.value)
        elif event.kind == "reuse_proposal" and isinstance(event.value.get("details"), dict) and event.value["details"].get("proposal_id"):
            candidates[event.id] = event.value
            parents[_candidate_key(event.value)] = _candidate_key(event.value)

    def find(key):
        while parents[key] != key:
            key = parents[key]
        return key

    def join(source, target):
        if source in parents and target in parents:
            a, b = find(source), find(target)
            parents[max(a, b)] = min(a, b)

    unapplied = {entry.get("event_id") for entry in journal.replay().unapplied}
    for event in _records(journal):
        details = event.value.get("details") or {}
        source, target = None, None
        if event.kind == "asset_candidate" and event.id in candidates:
            source, target = event.value, details.get("derived_from")
        elif (event.kind == "record_update" and event.id not in unapplied
              and not contract.event_problem(event.value) and isinstance(details.get("identity"), dict)):
            source, target = candidates.get(details.get("candidate_event_id")), details["identity"].get("target")
        if source is None or not isinstance(target, dict):
            continue
        found = candidates.get(target.get("event_id"))
        if found is not None and model.reference(target, before=event.seq)["confirmed"]:
            join(_candidate_key(source), _candidate_key(found))
    return {key: find(key) for key in parents}


def _evidence(values):
    if not isinstance(values, list):
        return None
    output = set()
    for ref in values:
        if not isinstance(ref, dict):
            return None
        output.add(json.dumps(contract.target(ref), sort_keys=True, ensure_ascii=False))
    return output


def _scores(values):
    if not isinstance(values, dict) or set(values) != set(SCORE_MAX):
        return None
    return values if all(type(values[key]) is int and 0 <= values[key] <= maximum for key, maximum in SCORE_MAX.items()) else None


def identity_clear(model, candidate, assessed):
    identities = groups(model)
    group = identities[_candidate_key(candidate)]
    evidence = _evidence(assessed["evidence"])
    return not evidence or not any(entry["group"] != group and entry["evidence"] is not None
                                   and evidence <= entry["evidence"] for entry in history(model, identities))


def history(model, identities):
    journal = model.journal
    output = []
    unapplied = {entry.get("event_id") for entry in journal.replay().unapplied}
    for event in _records(journal):
        details = event.value.get("details")
        if not isinstance(details, dict):
            continue
        legacy = event.kind == "reuse_notification"
        notification = event.kind == "asset_notification"
        choice = event.kind == "record_update" and details.get("new_state") in ("rejected", "deferred")
        if not (legacy or notification or choice) or (choice and event.id in unapplied):
            continue
        if not legacy and contract.event_problem(event.value):
            continue
        key = _key("reuse_proposal", details.get("proposal_id")) if legacy else _key("asset_candidate", details.get("asset_id"))
        if key not in identities:
            continue
        if not legacy:
            target = contract.candidate_ref(details, "about" if notification else "updates")
            if not model.reference(target, before=event.seq)["confirmed"]:
                continue
        evidence, scores = _evidence(details.get("evidence")), _scores(details.get("scores"))
        output.append({"event": event.value, "group": identities[key], "evidence": evidence, "scores": scores,
                       "requirements": (details.get("requirements", {}) if notification or
                                        details.get("suggestion_history") == "analysis_assets.suggestions/1" else {}),
                       "choice": choice, "valid_snapshot": evidence is not None and scores is not None,
                       "automatic": (legacy or notification) and details.get("trigger") == "automatic"})
    return output


def _fresh(assessed, prior, *, reasons=None):
    if not prior["valid_snapshot"]:
        return False
    current = _evidence(assessed["evidence"])
    if not current - prior["evidence"]:
        return False
    items = reasons if reasons is not None else (*SCORE_MAX, "source", "identity")
    return any((assessed["scores"].get(key, 0) > prior["scores"].get(key, 0) if key in SCORE_MAX else
                prior["requirements"].get(key) is False and assessed["requirements"].get(key) is True)
               for key in items)


def _base(assets, model, config):
    records, _ = assets._records(model.journal)
    versions = {}
    for record in records:
        if record["kind"] == "asset_candidate":
            versions.setdefault(record["details"]["asset_id"], []).append(record)
    output = []
    for identity, chain in sorted(versions.items()):
        if not all(value["details"].get("previous_event_id") == (chain[i - 1]["id"] if i else None) for i, value in enumerate(chain)):
            continue
        proposed = chain[-1]
        if assets._state(model.journal, proposed) in ("registered", "deprecated"):
            continue
        files = contract.files(proposed["details"])
        if not files or not all(model.reference(item["ref"])["confirmed"] for item in files):
            continue
        assessed = assets.assessment(proposed, model, min_score=config["min_score"])
        if assessed["qualified"]:
            output.append({"asset_id": identity, "candidate": proposed, "assessment": assessed})
    return output


def evaluate(assets, model, config):
    ambiguous = _ambiguous_history(model.journal)
    if ambiguous:
        return {"eligible": [], "suppressed": [], "history": [],
                "history_errors": [{"code": "ambiguous_asset_history", "event_ids": ambiguous}]}
    identities = groups(model)
    prior = history(model, identities)
    eligible, suppressed = [], []
    for item in _base(assets, model, config):
        group = identities[_candidate_key(item["candidate"])]
        own = [entry for entry in prior if entry["group"] == group]
        choice = next((entry for entry in reversed(own) if entry["choice"]), None)
        item["assessment"]["requirements"]["identity"] = identity_clear(model, item["candidate"], item["assessment"])
        reason = None
        if own and not _fresh(item["assessment"], own[-1]):
            reason = "no_new_scored_evidence"
        if choice is not None:
            reasons = choice["event"]["details"].get("reason_items", [])
            if not reasons or not _fresh(item["assessment"], choice, reasons=reasons):
                reason = "choice_requires_new_reason_evidence"
        evidence = _evidence(item["assessment"]["evidence"])
        if evidence and any(entry["group"] != group and entry["evidence"] is not None and evidence <= entry["evidence"] for entry in prior):
            reason = "duplicate_suspicion"
        if reason:
            suppressed.append({"asset_id": item["asset_id"], "reason": reason})
            continue
        sequences = sorted({event.seq for event in _records(model.journal)
                            if event.kind in ("asset_candidate", "asset_check", "asset_effect", "record_update", "run")})
        first = model.as_of_seq
        for seq in sequences:
            if any(entry["asset_id"] == item["asset_id"] for entry in _base(assets, model.through(seq), config)):
                first = seq
                break
        eligible.append({**item, "first_threshold_seq": first})
    eligible.sort(key=lambda item: (-item["assessment"]["total"], item["first_threshold_seq"], item["asset_id"]))
    return {"eligible": eligible, "suppressed": suppressed, "history": prior}


def window(journal, config, now):
    ambiguous = _ambiguous_history(journal)
    if ambiguous:
        return {"allowed": False, "wait_reason": "ambiguous_asset_history", "next_automatic_at": None,
                "ambiguous_event_ids": ambiguous}
    automatic = [event.value for event in _records(journal)
                 if event.kind in ("asset_notification", "reuse_notification")
                 and isinstance(event.value.get("details"), dict)
                 and event.value["details"].get("trigger") == "automatic"]
    if now is None:
        return {"allowed": False, "wait_reason": "clock_unavailable", "next_automatic_at": None}
    if not automatic:
        return {"allowed": True, "wait_reason": None, "next_automatic_at": None}
    times = [instant(event.get("recorded_at")) for event in automatic]
    if any(value is None for value in times):
        return {"allowed": False, "wait_reason": "malformed_automatic_history", "next_automatic_at": None}
    last = max(times)
    if now < last:
        return {"allowed": False, "wait_reason": "clock_backwards", "next_automatic_at": last.isoformat()}
    try:
        next_at = (last + timedelta(hours=config["cooldown_hours"])).isoformat()
    except OverflowError:
        next_at = None
    allowed = (now - last).total_seconds() / 3600 >= config["cooldown_hours"]
    return {"allowed": allowed, "wait_reason": None if allowed else "window", "next_automatic_at": next_at}


def pending(journal):
    """Previously recorded automatic output is readable; reading never consumes a window."""
    answered = {}
    unapplied = {entry.get("event_id") for entry in journal.replay().unapplied}
    for event in _records(journal):
        details = event.value.get("details") or {}
        if (event.kind == "record_update" and event.id not in unapplied and not contract.event_problem(event.value)
                and details.get("new_state") in ("rejected", "deferred", "registered", "deprecated")):
            answered[details.get("asset_id")] = event.seq
    return [{"event_id": event.id, "asset_id": event.value["details"].get("asset_id"),
             "candidate_event_id": event.value["details"].get("candidate_event_id"),
             "text": event.value.get("text"), "surface": event.value["details"].get("surface"),
             "recorded_at": event.value.get("recorded_at")}
            for event in _records(journal) if event.kind == "asset_notification"
            and not contract.event_problem(event.value) and event.value["details"].get("trigger") == "automatic"
            and journal.event_target("asset_candidate", contract.candidate_ref(event.value["details"], "about"), event.seq)[0] == "resolved"
            and answered.get(event.value["details"].get("asset_id"), -1) < event.seq]


def status(assets, *, journal=None, marker=None, at=None, session=None):
    journal = journal if journal is not None else relations.read_journal(assets.root)
    marker = marker if marker is not None else writer.marker_state(assets.root).value
    marker = marker if isinstance(marker, dict) else {}
    config, errors = settings(marker)
    errors += _mode_errors(marker, journal, config)
    selected = _session_mode(journal, session)
    if selected is not None:
        config = {**config, "mode": selected["details"]["mode"]}
    state = window(journal, config, instant(assets.writer.clock() if at is None else at)) if not errors else {"allowed": False, "wait_reason": "settings", "next_automatic_at": None}
    if config["mode"] != "always_on":
        state.update(allowed=False, wait_reason="on_demand")
    return {"mode": config["mode"], "scope": "session" if selected is not None else "project",
            "mode_event_id": selected["id"] if selected is not None else None,
            "settings": config, "settings_errors": errors,
            **state, "pending": pending(journal), "as_of_seq": journal.as_of_seq, "model_calls": 0}


def preassess(assets):
    """Regenerable progress/counts only; this cache never authorizes a publication."""
    current = status(assets, session=assets.writer.origin.session)
    if current["mode"] != "always_on" or current["settings_errors"]:
        return {"assessed": False, "reason": current["wait_reason"], "model_calls": 0}
    model = relations.read(assets.root, alive=assets.writer.platform.processes.alive,
                           sha=assets._sha, legacy_path=assets.writer.legacy_path)
    basis = {"source_revision": model.source_revision, "as_of_seq": model.as_of_seq,
             "settings": current["settings"], "session": assets.writer.origin.session}
    cache = assets._safe(".analytics/cache/asset-progress.json")
    previous = None
    try:
        previous = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    counts = previous.get("counts") if isinstance(previous, dict) else None
    valid = (isinstance(counts, dict) and set(counts) == {"eligible", "suppressed"}
             and all(type(v) is int and v >= 0 for v in counts.values()))
    if (valid and previous.get("contract") == "analysis_assets.progress/1" and previous.get("basis") == basis
            and not (current["allowed"] and previous.get("window_allowed") is False)):
        return {"assessed": False, "cached": True, "counts": counts, "basis": basis, "model_calls": 0}
    evaluated = evaluate(assets, model, current["settings"])
    counts = {name: len(evaluated[name]) for name in ("eligible", "suppressed")}
    value = {"contract": "analysis_assets.progress/1", "basis": basis, "counts": counts,
             "window_allowed": current["allowed"]}
    admitted = writer.admit(assets.root, "asset progress cache", side=True,
                            writer_version=assets.writer.writer_version, writable=assets.writer.WRITES)
    if admitted is not None and not model.updating:
        writer.write_json(cache, value, platform=assets.writer.platform)
    return {"assessed": True, "cached": False, "counts": counts, "basis": basis, "model_calls": 0}


def publish(assets, *, surface="cli_text_status"):
    """Only the human overview boundary calls this; append succeeds before output is returned."""
    allowed = {("cli_text_status", "human", "cli"), ("aelix_command_status", "human", "command"),
               ("aelix_context", "system", "hook")}
    if (surface, assets.writer.origin.actor, assets.writer.origin.means) not in allowed:
        return {"published": None, "reason": "read_only_surface", "model_calls": 0}
    session = assets.writer.origin.session
    initial = status(assets, session=session)
    if initial["settings_errors"] or initial["mode"] != "always_on":
        return {"published": None, "status": initial, "model_calls": 0}
    with assets.writer.locked("publish_asset_suggestion") as held:
        current = status(assets, journal=held.journal, marker=held.state.value, session=session)
        if current["settings_errors"] or current["mode"] != "always_on" or not current["allowed"]:
            return {"published": None, "status": current, "model_calls": 0}
        model = assets.model(held.journal)
        evaluated = evaluate(assets, model, current["settings"])
        if not evaluated["eligible"]:
            return {"published": None, "status": current, "suppressed": evaluated["suppressed"], "model_calls": 0}
        item = evaluated["eligible"][0]
        proposed, assessed = item["candidate"], item["assessment"]
        details = {"contract": contract.CONTRACT, "asset_id": item["asset_id"], "candidate_event_id": proposed["id"],
                   "trigger": "automatic", "surface": surface,
                   **{key: assessed[key] for key in ("as_of_seq", "context_version", "evidence", "scores", "requirements", "policy_version")}}
        # Check the exact timestamp that will be stored, after potentially slow assessment.
        observed = assets.writer.clock()
        if not isinstance(observed, datetime):
            return {"published": None, "reason": "clock_unavailable", "model_calls": 0}
        recorded_at = observed.isoformat(timespec="milliseconds")
        final_window = window(held.journal, current["settings"], instant(recorded_at))
        if not final_window["allowed"]:
            return {"published": None, "status": {**current, **final_window}, "model_calls": 0}
        event = held.append("asset_notification", f"Asset suggestion: {proposed['details']['title']}",
                            refs=contract.semantic_refs("asset_notification", details), details=details,
                            requires=HOST_SINCE if surface != "cli_text_status" else SINCE,
                            recorded_at=recorded_at)
        return {"published": {"candidate": proposed, "assessment": assessed, "notification": event}, "model_calls": 0}
