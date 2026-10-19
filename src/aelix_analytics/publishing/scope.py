"""Independent scope closure and reviewed field allow-lists (PUBLICATION-BUILD 5)."""
from __future__ import annotations

import re
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

# Fixed reader words/structural names are public vocabulary, not an excluded object's name.
# This is an independent allow-list: it is never assembled from renderer output or record data.
KIT_VOCABULARY = frozenset({
    "publication", "stylesheet", "viewport", "charset", "en", "utf-8", "data", "layout", "config",
    "index.html", "about.html", "README.txt", "read", "evidence", "schema", "scope",
    "assets", "files", "pages", "figures", "runs", "timeline", "context", "code", "observations", "reports",
    "inspected", "pinned_scanned", "pinned scanned", "not_inspectable", "not inspectable", "in_progress", "complete", "declared", "AnalyticsFigures",
    "General path pattern warning", "An excluded diagnostic line also appears in included content",
    "GPS metadata", "Author/software metadata", "Device serial metadata", "Image metadata",
    "Untitled project", "Shared snapshot — a fixed copy that does not update",
    "Included analysis and evidence at this point.", "Scope and limits", "About this copy",
    "Code and data are not included in this publication; it cannot be re-run from this copy.",
    "Input data is not included.", "Interactive figure", "Included visualization",
    "Preview only. No publication has been saved or recorded.",
    "Copy identity", "Built at", "Records through", "Resource counts exclude publication.json, which is checked separately when a copy is saved.",
    "This copy is a fixed snapshot. Its source may have changed.",
})


def request_check(request, profile: Profile) -> None:
    if request.scope not in ("project", "reports", "collection"):
        raise PublicationError("scope_unknown", "unknown publication scope")
    if request.scope not in profile.capabilities or request.reports or request.allow_withdrawn:
        raise PublicationError("scope_unsupported", "Report and collection scopes are not available in this preview")
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


def leak_policy(snapshot, request, profile, projection, *, environment=()):
    """Independent forbidden values: snapshot minus (claimed ledger intersected with closure)."""
    from dataclasses import fields, is_dataclass

    from .model import BanToken, LeakPolicy, Text

    included = frozenset(projection.ledger.included) & scope_closure(snapshot, request, profile)
    labels = set(KIT_VOCABULARY) | set(PROJECT_KINDS) | set(EVIDENCE_FIELDS)
    for names in (*READ_FIELDS.values(), *EVIDENCE_FIELDS.values(), *METRIC_FIELDS.values()):
        labels.update(name.removeprefix("details.").replace("_", " ").replace(".", " ") for name in names)
    allowed = labels | {word for label in labels for word in re.findall(r"[\w-]+", label)}
    allowed |= {value.lower() for value in allowed}

    def allow(value):
        if isinstance(value, Text):
            if value.origin == "kit" or value.source and value.source[0] in included:
                allowed.add(value.value)
        elif is_dataclass(value):
            for f in fields(value):
                allow(getattr(value, f.name))
        elif isinstance(value, tuple):
            for part in value:
                allow(part)

    allow(projection.model)
    for item in projection.model.items:
        if item.key in included:
            for ref in item.references:
                allowed.update(value for value in (ref.relation, ref.binding, ref.basis, ref.state, ref.reason, ref.category)
                               if isinstance(value, str))
    tokens = set()
    short = 0

    def add(value, mode, key=None, field=None, exempt=True):
        nonlocal short
        if not isinstance(value, str) or not value or exempt and value in allowed:
            return
        if mode != "weak" and len(value) < 8:
            short += 1
            return
        tokens.add(BanToken(value, mode, key, field))

    def flatten(value, path=""):
        if isinstance(value, str):
            yield path, value
        elif hasattr(value, "entries"):
            for name, part in value.entries:
                yield from flatten(part, (path + "." if path else "") + name)
        elif isinstance(value, tuple):
            for n, part in enumerate(value):
                yield from flatten(part, f"{path}[{n}]")

    for item in snapshot.items:
        # Completion events accompany included objects. Their private text never gets rendered,
        # but an included run's own ID is not a forbidden identifier merely due to its run event.
        completion = item.kind in ("run", "import", "context", "report_revision") and any(
            r.relation == "about" and r.target in included for r in item.references
        )
        outside = item.key not in included and not completion
        if outside:
            if item.key.category in ("run", "visualization", "context", "report"):
                add(item.key.identity, "strong", item.key, "identity")
            elif item.key.category == "event":
                add(item.key.identity, "weak", item.key, "identity")
        for field, value in flatten(item.values):
            if outside:
                if field == "name":
                    add(value, "strong" if len(value) >= 8 else "weak", item.key, field)
                if field.endswith(
                    (
                        "path",
                        "snapshot",
                        "version",
                        "revision_id",
                        "report_id",
                        "visualization_id",
                        "run_id",
                    )
                ):
                    add(
                        value,
                        "strong" if "/" in value or "\\" in value or len(value) >= 8 else "weak",
                        item.key,
                        field,
                    )
                if field == "text":
                    for line in value.splitlines():
                        if len(" ".join(line.split())) >= 24:
                            add(line, "sentence", item.key, field)
            # Private environment fields are blocked even when their source item is included.
            if field in (
                "env.platform",
                "env.executable",
                "request_id",
                "request_fingerprint",
                "session",
            ) or field.endswith(".session"):
                add(value, "strong", item.key, field, exempt=False)
            if item.key in included and (
                field.startswith(
                    (
                        "stdout",
                        "stderr",
                        "warnings",
                        "finalize_errors",
                        "error.message",
                        "error.summary",
                        "error.hint",
                    )
                )
            ):
                for line in value.splitlines():
                    if len(" ".join(line.split())) >= 24:
                        add(line, "warning", item.key, field)
    for value in environment:
        add(value, "strong", exempt=False)
        if isinstance(value, str):
            add(value.replace("\\", "/"), "strong", exempt=False)
            add(value.replace("/", "\\"), "strong", exempt=False)
    return LeakPolicy(
        tuple(sorted(tokens, key=lambda t: (t.mode, t.value, str(t.source), t.field or ""))), short
    )
