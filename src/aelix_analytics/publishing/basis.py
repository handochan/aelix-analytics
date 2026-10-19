"""Pure conservative publication basis policy (ADR0014 decision2)."""
from __future__ import annotations

import re
from datetime import datetime

from .. import relations
from .model import ContentBasis, SnapshotBasis

PUBLICATION_KINDS = frozenset({"publication", "publish_receipt"})
PUBLICATION_ID = re.compile(r"pub-[0-9a-hjkmnp-tv-z]{26}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


def _time(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else None
        return value if stamp is not None and stamp.utcoffset() is not None else None
    except ValueError:
        return None


def _normal_delivery(event, journal, validate_reference):
    if event.schema != relations.EVENT_V2 or event.kind not in PUBLICATION_KINDS or event.line in journal.recovered:
        return False
    v = event.value
    if (type(v.get("seq")) is not int or v["seq"] < 1 or v.get("id") != f"e{v['seq']}"
            or len(journal.lines_of.get(v["id"], ())) != 1 or not _time(v.get("recorded_at"))
            or not isinstance(v.get("actor"), str) or not v["actor"] or not isinstance(v.get("text"), str)
            or not isinstance(v.get("refs"), list)):
        return False
    try:
        for ref in v["refs"]:
            if validate_reference(ref) is not None:
                return False
            if ref.get("relation") in ("supersedes", "resolves", "updates"):
                return False
            if event.kind == "publication" and (ref.get("type") not in ("report_revision", "context_version")
                    or ref.get("relation") != "includes" or ref.get("version_binding") != "author"
                    or ref.get("basis") not in ("observed", "declared")):
                return False
    except Exception:  # an unvalidated row is retained, never presumed harmless
        return False
    d = v.get("details")
    if not isinstance(d, dict) or not isinstance(d.get("publication_id"), str) or not PUBLICATION_ID.fullmatch(d["publication_id"]):
        return False
    # Publication's accepted binding fields are known already; an incomplete hand-written binding
    # must not be silently treated as a harmless publication. Receipt's stronger contract is #21.
    if event.kind == "publication":
        required = {"format", "package_sha256", "content_sha256", "profile", "scope", "basis",
                    "analysis_state", "exclusions", "location", "checks"}
        if not required <= d.keys() or d["format"] != "analytics.publication/1":
            return False
        if any(not isinstance(d[k], str) or not SHA.fullmatch(d[k]) for k in ("package_sha256", "content_sha256")):
            return False
        if d["profile"] not in ({"id": "read", "rules_version": 1}, {"id": "evidence", "rules_version": 1}):
            return False
        basis, state, checks = d["basis"], d["analysis_state"], d["checks"]
        if (d["scope"] not in ("project", "reports") or not isinstance(basis, dict)
                or not {"as_of_seq", "content_seq", "context_version"} <= basis.keys()
                or type(basis["as_of_seq"]) is not int or type(basis["content_seq"]) is not int
                or not 0 <= basis["content_seq"] <= basis["as_of_seq"] < event.seq
                or state not in ({"value": "in_progress", "basis": "declared"}, {"value": "complete", "basis": "declared"})
                or not isinstance(d["exclusions"], list) or d["location"] != f".analytics/publications/{d['publication_id']}"
                or not isinstance(checks, dict) or type(checks.get("format")) is not int
                or checks.get("format") != 1 or checks.get("result") != "pass"
                or basis["context_version"] is not None and not relations._file_name(basis["context_version"])):
            return False
        inspection = checks.get("inspection")
        if not isinstance(inspection, dict) or any(type(inspection.get(k)) is not int or inspection[k] < 0
                                                 for k in ("inspected", "pinned_scanned", "not_inspectable")):
            return False
        if type(d["profile"].get("rules_version")) is not int:
            return False
        try:
            if any(validate_reference(r) is not None for r in d["exclusions"]):
                return False
        except Exception:
            return False
    return True


def content_basis(journal, *, validate_reference):
    """Conservative ADR14 basis; never renumber, reinterpret as_of_seq, or invent a timestamp."""
    excluded = {e.line for e in journal.events if _normal_delivery(e, journal, validate_reference)}
    candidates = [(line, seq) for line, seq in journal.seq_of.items() if seq is not None and line not in excluded]
    seq = max((s for _, s in candidates), default=0)
    lines = [line for line, s in candidates if s == seq]
    event_at = {e.line: e for e in journal.events}
    at = _time(event_at[lines[0]].recorded_at) if len(lines) == 1 and lines[0] in event_at else None
    context = None
    for event in journal.in_order():
        if event.kind != "context" or event.seq is None or event.seq > journal.as_of_seq:
            continue
        if event.schema == relations.EVENT_V1:
            refs = event.refs if isinstance(event.refs, dict) else {}
            value = refs.get("context")
            versions = value if isinstance(value, list) else [value]
        else:
            versions = [r.get("version") for r in event.refs or () if isinstance(r, dict) and r.get("type") == "context_version"]
        context = versions[0] if len(versions) == 1 and isinstance(versions[0], str) else None
    return SnapshotBasis(journal.as_of_seq, ContentBasis(seq, at, context)), frozenset(excluded)
