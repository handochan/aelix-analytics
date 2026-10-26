"""Independent scope closure and reviewed field allow-lists (PUBLICATION-BUILD 5)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .checks import check_names, normalized
from .model import ItemKey, PublicationError, page_key

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
# A Report version (5.3) with the owner's 2026-10-26 choices: its four descriptions (OD1) and a
# collection's member keys, statuses and reasons (OD2). Its body files are copied, never fields.
REPORT_FIELDS = ("report_id", "revision_id", "title", "created_at", "actor_role", "entry", "state", "number",
                 "total", "based_on.as_of_seq", "based_on.context_version", "summary", "purpose", "change_reason",
                 "content_status", "collection.members")
READ_FIELDS = {
    "project": ("title",), "context": ("text", "version", "recorded_at"), "report": REPORT_FIELDS,
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
    capabilities: frozenset[str] = frozenset({"project", "reports", "collection"})

    def fields(self, item) -> tuple[str, ...]:
        if item.kind == "context_history" and not self.evidence or item.kind == "report_attempt":
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
    "AnalyticsPublication", "window.AnalyticsPublication",
    "Copy identity", "Built at", "Records through", "Resource counts exclude publication.json, which is checked separately when a copy is saved.",
    "This copy is a fixed snapshot. Its source may have changed.",
    # #20 PR5 attachment links.
    "Made by run", "Code snapshot", "Input or output", "Attached",
    # #20 PR4 Report pages.
    "Report version", "Version identity", "Entry file", "Author role", "State at this point", "Created", "Summary",
    "Purpose", "Reason for this version", "Content status (as the author declared)", "Based on", "Records up to",
    "Based on CONTEXT version", "(not included in this publication)", "A saved Report is not a review of its conclusions.",
    "A later version existed when this copy was built — not included.", "Withdrawn",
    "this version was withdrawn when this copy was built.", "Collection", "failed", "not generated",
    "Member", "Status", "Reason", "Open member version", "Not included in this publication",
    "Membership is the collection's structure, not evidence for its text.", "Evidence",
    "This version does not point to any evidence (no links).", "Report body", "Open the report body on its own page",
    "If this frame is empty, its file is missing from this copy.", "Parts of this body are offered as downloads only",
    "This body is not displayed in this copy. Download its files below.", "Files", "bytes",
    "Compared with the hashes the Report recorded.", "saved", "at an unknown time", "based on records up to",
})


def request_check(request, profile: Profile) -> None:
    if request.scope not in ("project", "reports", "collection"):
        raise PublicationError("scope_unknown", "unknown publication scope")
    if request.scope not in profile.capabilities:
        raise PublicationError("scope_unsupported", "this profile does not support the requested scope")
    if request.scope != "project" and not request.reports:
        raise PublicationError("scope_invalid", "a Report scope needs a selected Report version")
    if request.allow_withdrawn and not request.reports:
        raise PublicationError("scope_invalid", "--allow-withdrawn applies only to a selected Report version")
    if (not isinstance(request.reports, tuple) or len(set(request.reports)) != len(request.reports)
            or not all(isinstance(r, tuple) and len(r) == 2 and all(isinstance(v, str) for v in r)
                       and r[0] for r in request.reports)):
        raise PublicationError("scope_invalid", "selected Reports must be distinct report_id[@revision_id] values")
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


def _not_shareable(reason, report, **details):
    raise PublicationError("report_not_shareable", "the selected Report version cannot be shared as it is",
                           details={"reason": reason, "report": report, **details})


def selected_reports(snapshot, request) -> frozenset[ItemKey]:
    """The exact selected Report versions, each shareable whole or the build stops (6.2, OQ9-OQ10, OQ23)."""
    items = {item.key: item for item in snapshot.items}
    selected = set()
    for report_id, revision_id in request.reports:
        where = f"{report_id}@{revision_id}"
        key = ItemKey("report", f"{report_id}/{revision_id}")
        item = items.get(key)
        if not revision_id or item is None:
            raise PublicationError("scope_invalid", "unknown Report version", details={"report": where})
        if key in request.exclude:
            raise PublicationError("scope_invalid", "a selected Report version cannot be excluded",
                                   details={"report": where})
        if item.kind != "report_revision":
            _not_shareable("not_saved", where)
        if item.values.get("_problem"):
            _not_shareable(item.values.get("_problem"), where)
        if item.values.get("_content") != "resolved":
            _not_shareable(item.values.get("_content") or "content_unknown", where)
        if item.values.get("state") == "withdrawn" and not request.allow_withdrawn:
            _not_shareable("withdrawn", where)
        directory = item.values.get("_directory") or ""
        for file in item.files:
            name = file.source.removeprefix(f"{directory}/content/") if file.source else None
            if file.reason:
                _not_shareable("path" if file.reason == "outside_project" else file.reason, where, file=name)
            if not file.current_sha256 or file.current_sha256 != file.recorded_sha256:
                _not_shareable("file_absent" if not file.current_sha256 else "content_changed", where, file=name)
            if file.format == "unsupported":
                _not_shareable("format", where, file=name)
            try:
                check_names([f"reports/{page_key(key)}/content/{name}"])
            except PublicationError:
                _not_shareable("path", where, file=name)
        selected.add(key)
    return frozenset(selected)


def scope_closure(snapshot, request, profile: Profile) -> frozenset[ItemKey]:
    """Roots + bound direct targets + their allowed attachments. Never recurse into lineage."""
    request_check(request, profile)
    items = {item.key: item for item in snapshot.items}
    if any(k in request.exclude for k in items if k.category in ("project", "context")):
        raise PublicationError("exclusion_invalid", "project title and basis CONTEXT cannot be excluded")
    selected = selected_reports(snapshot, request)
    if request.scope == "project":
        roots = {item.key for item in snapshot.items if item.root and not exclusion_reason(item, request, profile)}
        roots |= {item.key for item in snapshot.items if item.key.category == "event"
                  and (item.kind in PROJECT_KINDS or (profile.evidence and item.kind == "note"))
                  and not exclusion_reason(item, request, profile)}
    else:
        # A Report-centred copy adds no other project content (5.1, PUBLISH 7): only the title its
        # snapshot band names. CONTEXT enters when a selected version cites it.
        roots = {ItemKey("project", "project")} & set(items)
    roots |= selected
    included = set(roots)
    for key in sorted(roots):
        for ref in items[key].references:
            if (ref.binding in ("author", "inferred") and ref.target in items
                    and ref.state in ("resolved", "unknown", "content_changed")
                    and ref.reason != "digest_mismatch"
                    # Another Report version is shared only as a selection, never as a cited target.
                    and (ref.target.category != "report" or ref.target in selected)):
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

    shared = []

    def allow(value):
        if isinstance(value, Text):
            if value.origin == "kit" or value.source and value.source[0] in included:
                allowed.add(value.value)
                shared.append(" ".join(normalized(value.value).split()))
        elif is_dataclass(value):
            for f in fields(value):
                allow(getattr(value, f.name))
        elif isinstance(value, tuple):
            for part in value:
                allow(part)

    allow(projection.model)
    # The copy's own basis (6.4) is named in every scope, even when that CONTEXT version's text is
    # not included: a basis value, not an excluded object's content.
    if snapshot.basis.content.context_version:
        allowed.add(snapshot.basis.content.context_version)
    for item in projection.model.items:
        if item.key in included:
            for ref in item.references:
                allowed.update(value for value in (ref.relation, ref.binding, ref.basis, ref.state, ref.reason, ref.category)
                               if isinstance(value, str))
    tokens = set()
    short = 0
    # A sentence that included text also says is not an outside item's own words (an earlier CONTEXT
    # version repeats most of the basis version's lines).
    shared_text = "\n".join(shared)

    def add(value, mode, key=None, field=None, exempt=True):
        nonlocal short
        if not isinstance(value, str) or not value or exempt and value in allowed:
            return
        if mode == "sentence" and exempt and " ".join(normalized(value).split()) in shared_text:
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
