"""Structured metric/focus payloads (ADR-0011). Pure, standard-library domain rules.

No project, filesystem, host, renderer or analysis-library dependencies. The writer checks these
rules before side effects; the read projection applies them without upgrading historical payloads.
"""

from __future__ import annotations

import copy
import json
import re

KINDS = ("metric_definition", "analysis_focus", "metric_observation")
SINCE = "0.9.0"
EVENT_ID = re.compile(r"e[1-9][0-9]*")
RUN_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[a-z0-9][a-z0-9-]{0,60}")
STATE_RELATIONS = frozenset({"supersedes", "updates", "resolves"})


class ContractError(ValueError):
    pass


def _string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} is a non-empty string")


def _strings(value, name):
    if not isinstance(value, list):
        raise ContractError(f"{name} is a list of strings")
    for item in value:
        _string(item, name)


def _event(value, name):
    if not isinstance(value, str) or not EVENT_ID.fullmatch(value):
        raise ContractError(f"{name} is an exact event id, e.g. e3")


def _definitions(value, name, *, role=False):
    if not isinstance(value, list):
        raise ContractError(f"{name} is a list of exact metric definitions")
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise ContractError(f"{name} entries are objects")
        _string(item.get("metric_id"), f"{name}.metric_id")
        _event(item.get("event_id"), f"{name}.event_id")
        if role:
            _string(item.get("role"), f"{name}.role")
        key = (item["metric_id"], item["event_id"])
        if key in seen:
            raise ContractError(f"{name} contains a duplicate definition")
        seen.add(key)


def validate(kind, details) -> dict:
    """A private finite-JSON copy, or ContractError. No canonical record is mutated."""
    if kind not in KINDS:
        raise ContractError(f"{kind!r} is not a structured metric record")
    if not isinstance(details, dict) or details.get("contract") != f"{kind}/1":
        raise ContractError(f"details.contract is {kind}/1")
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
        raise ContractError("details holds finite JSON values encoded as UTF-8") from exc
    for name in ("change_reason",):
        _string(details.get(name), name)
    if kind in ("metric_definition", "analysis_focus"):
        _strings(details.get("unresolved"), "unresolved")
    if kind == "metric_definition":
        for name in ("metric_id", "name"):
            _string(details.get(name), name)
        if details.get("status") not in ("draft", "specified"):
            raise ContractError("status is draft or specified, not an analysis-check verdict")
        if not isinstance(details.get("definition"), dict):
            raise ContractError("definition is a JSON object (an empty draft is allowed)")
        _definitions(details.get("derived_from"), "derived_from")
        if "previous_definition" in details:
            _event(details["previous_definition"], "previous_definition")
        if any(p["metric_id"] == details["metric_id"] for p in details["derived_from"]):
            raise ContractError("a derived metric has a different logical id from its parents")
    elif kind == "analysis_focus":
        for name in ("goal", "question", "activity", "next_check"):
            _string(details.get(name), name)
        _definitions(details.get("metrics"), "metrics", role=True)
        if "previous_focus" in details:
            _event(details["previous_focus"], "previous_focus")
    else:
        _string(details.get("metric_id"), "metric_id")
        _event(details.get("definition_event_id"), "definition_event_id")
        if "run_id" in details and (not isinstance(details["run_id"], str)
                                    or not RUN_ID.fullmatch(details["run_id"])):
            raise ContractError("run_id is an exact recorded run id")
        if not isinstance(details.get("scope"), dict):
            raise ContractError("scope is a JSON object (empty means unspecified scope)")
        if "result" not in details:
            raise ContractError("result is explicit, including null for a result_ref")
        if details["result"] is None and "result_ref" not in details:
            raise ContractError("a null result requires result_ref")
        if "result_ref" in details:
            ref = details["result_ref"]
            if not isinstance(ref, dict) or ref.get("type") not in (
                    "data_observation", "visualization_version"):
                raise ContractError("result_ref is a run output or Visualization version")
        if "recomputed_from" in details:
            _event(details["recomputed_from"], "recomputed_from")
    return copy.deepcopy(details)


def projection(events, states) -> dict:
    """All tagged versions and their current state, reconstructed from original event objects."""
    result = {"definitions": [], "focus_versions": [], "observations": [], "unstructured": [],
              "invalid": [], "current_focus": None, "focus_state": "absent"}
    for event in events:
        kind = event.get("kind")
        if kind not in KINDS:
            continue
        details = event.get("details")
        if not isinstance(details, dict) or "contract" not in details:
            result["unstructured"].append(event.get("id"))
            continue
        try:
            payload = validate(kind, details)
        except ContractError as exc:
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
