"""Independent scope closure and reviewed field allow-lists (PUBLICATION-BUILD 5)."""
from __future__ import annotations

from dataclasses import dataclass

from .model import ItemKey, PublicationError

PROJECT_KINDS = frozenset({"finding", "decision", "assumption", "question", "todo", "analysis_focus",
                           "metric_definition", "metric_observation"})
COPY_ROLES = frozenset({"figure", "attached_data", "code_snapshot", "report_file", "library"})
EVENT_FIELDS = ("id", "text", "recorded_at", "actor_role", "state", "state_history")
METRIC_FIELDS = {
    "metric_definition": ("details.metric_id", "details.name", "details.status", "details.definition",
                          "details.unresolved", "details.change_reason", "details.previous_definition",
                          "details.derived_from"),
    "analysis_focus": ("details.goal", "details.question", "details.activity", "details.next_check",
                       "details.change_reason", "details.metrics", "details.unresolved", "details.previous_focus"),
    "metric_observation": ("details.metric_id", "details.definition_event_id", "details.run_id",
                           "details.scope", "details.result", "details.change_reason", "details.recomputed_from"),
}
READ_FIELDS = {
    "project": ("title",), "context": ("text", "version", "recorded_at"),
    "run": ("id", "title", "status", "recording_status", "recording_reasons", "started_at", "ended_at",
            "actor_role", "error.type", "error.line", "warning_count", "finalize_error_count"),
    "visualization": ("visualization_id", "title", "caption", "kind"),
    "dataset": ("name",),
}
EVIDENCE_FIELDS = {
    **READ_FIELDS,
    "run": (*READ_FIELDS["run"], "script.path", "env.python", "env.packages", "env.kit_version",
            "reproduction", "inputs", "outputs"),
    "visualization": (*READ_FIELDS["visualization"], "data_rows", "data_columns", "line"),
    "dataset": (*READ_FIELDS["dataset"], "path", "sha256", "bytes", "rows", "columns", "description"),
    "data_observation": ("path", "role", "sha256", "bytes", "rows", "schema", "observation"),
    "code": ("path", "sha256", "observation"),
    "context": (*READ_FIELDS["context"], "observation"),
}


@dataclass(frozen=True)
class Profile:
    id: str
    evidence: bool
    rules_version: int = 1
    capabilities: frozenset[str] = frozenset({"project"})

    def fields(self, item) -> tuple[str, ...]:
        if item.kind == "context_history" and not self.evidence:
            return ()
        if item.key.category == "event":
            if item.kind not in PROJECT_KINDS and not (self.evidence and item.kind == "note"):
                return ()
            details = item.values.get("details")
            contract = details.get("contract") if hasattr(details, "get") else None
            extra = METRIC_FIELDS.get(item.kind, ()) if contract == f"{item.kind}/1" else ()
            return (*EVENT_FIELDS, *extra)
        return (EVIDENCE_FIELDS if self.evidence else READ_FIELDS).get(item.key.category, ())


PROFILES = {"read": Profile("read", False), "evidence": Profile("evidence", True)}


def request_check(request, profile: Profile) -> None:
    if request.scope not in ("project", "reports", "collection"):
        raise PublicationError("scope_unknown", "unknown publication scope")
    if request.scope not in profile.capabilities or request.reports:
        raise PublicationError("scope_unsupported", "Report and collection scopes require Issue20 PR4")
    if request.analysis_state not in ("in_progress", "complete"):
        raise PublicationError("analysis_state_invalid", "analysis state is explicitly declared")
    if request.ui_locale != "en":
        raise PublicationError("locale_unsupported", "publication labels currently use English")


def exclusion_reason(item, request, profile) -> str | None:
    if item.key in request.exclude:
        return "excluded"
    if item.problem:
        return item.problem
    if not item.complete:
        return "in_progress"
    if not profile.fields(item):
        return "profile"
    return None


def scope_closure(snapshot, request, profile: Profile) -> frozenset[ItemKey]:
    """Roots + bound direct targets + their allowed attachments. Never recurse into lineage."""
    request_check(request, profile)
    items = {item.key: item for item in snapshot.items}
    if any(k in request.exclude for k in items if k.category in ("project", "context")):
        raise PublicationError("exclusion_invalid", "project title and basis CONTEXT cannot be excluded")
    roots = {item.key for item in snapshot.items if item.root and not exclusion_reason(item, request, profile)}
    roots |= {item.key for item in snapshot.items if item.key.category == "event"
              and (item.kind in PROJECT_KINDS or (profile.evidence and item.kind == "note"))
              and not exclusion_reason(item, request, profile)}
    included = set(roots)
    for key in sorted(roots):
        for ref in items[key].references:
            if (ref.binding in ("author", "inferred") and ref.target in items
                    and ref.state in ("resolved", "unknown", "content_changed")
                    and ref.reason != "digest_mismatch"):
                target = items[ref.target]
                if not exclusion_reason(target, request, profile):
                    included.add(target.key)
    # Follow fixed attachment edges only (Visualization -> run header -> evidence). References
    # of those attachments are never traversed; cycles terminate in the visited set.
    pending = list(sorted(included))
    while pending:
        key = pending.pop()
        for target_key in items[key].attachments:
            target = items.get(target_key)
            if target and target_key not in included and not exclusion_reason(target, request, profile):
                included.add(target_key)
                pending.append(target_key)
    return frozenset(included)


def validate_scope(snapshot, request, profile, included) -> None:
    if not frozenset(included) <= scope_closure(snapshot, request, profile):
        raise PublicationError("leak_found", "projection_out_of_scope")
