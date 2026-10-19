"""Pure conservative publication basis policy (ADR0014 decision2)."""
from __future__ import annotations

from datetime import datetime

from .. import relations
from .model import ContentBasis, SnapshotBasis

PUBLICATION_KINDS = frozenset({"publication", "publish_receipt"})
PUBLICATION_ID = relations.PUBLICATION_ID


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
    if event.kind == "publication":
        return relations.publication_event_problem(v, validate_reference=validate_reference) is None
    try:
        for ref in v["refs"]:
            if validate_reference(ref) is not None or ref.get("relation") in ("supersedes", "resolves", "updates"):
                return False
    except Exception:
        return False
    d = v.get("details")
    if not isinstance(d, dict) or not isinstance(d.get("publication_id"), str) or not PUBLICATION_ID.fullmatch(d["publication_id"]):
        return False
    # Receipt's stronger contract belongs to #21. Only its currently known normal shape is ignored.

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
