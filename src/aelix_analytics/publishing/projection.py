"""Pure publication strategies over an eagerly frozen snapshot. No filesystem access."""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass, fields, is_dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Protocol

from .model import (
    ContentBasis,
    CopyEntry,
    Field,
    FileSlot,
    ItemKey,
    Object,
    Omission,
    Projection,
    PublicationError,
    Reference,
    ScopeLedger,
    ShareItem,
    ShareModel,
    SnapshotBasis,
    Text,
)
from .scope import COPY_ROLES, PROFILES, exclusion_reason, scope_closure, validate_scope

MISSING = object()

LABELS = frozenset({"Untitled project", "human", "agent", "system", "unknown", "current", "superseded",
                    "conflict", "withdrawn", "open", "resolved", "ok", "error", "timeout", "cancelled",
                    "abandoned", "running", "complete", "incomplete", "Outside the project", "same",
                    "changed", "missing", "not_recorded", "input", "output", "Path not included"})


@dataclass(frozen=True)
class FrozenMap:
    entries: tuple[tuple[str, object], ...] = ()

    def get(self, name, default=None):
        return next((v for k, v in self.entries if k == name), default)


def freeze(value):
    if isinstance(value, dict):
        return FrozenMap(tuple((str(k), freeze(v)) for k, v in sorted(value.items())))
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise PublicationError("records_unsupported", "snapshot values must be finite JSON")


@dataclass(frozen=True)
class SnapshotFile:
    source: str
    role: str
    suffix: str
    recorded_sha256: str | None
    current_sha256: str | None
    bytes: int = 0
    format: str = "unsupported"
    reason: str | None = None


@dataclass(frozen=True)
class SnapshotItem:
    key: ItemKey
    kind: str
    values: FrozenMap
    seq: int = 0
    root: bool = False
    complete: bool = True
    references: tuple[Reference, ...] = ()
    attachments: tuple[ItemKey, ...] = ()
    files: tuple[SnapshotFile, ...] = ()
    problem: str | None = None


@dataclass(frozen=True)
class ShareSnapshot:
    basis: SnapshotBasis
    items: tuple[SnapshotItem, ...]
    diagnostics: tuple[Omission, ...] = ()

    def current_digest(self, item: ItemKey, source: str):
        return next((f.current_sha256 for i in self.items if i.key == item
                     for f in i.files if f.source == source), None)


@dataclass(frozen=True)
class ShareRequest:
    scope: str = "project"
    exclude: frozenset[ItemKey] = frozenset()
    reports: tuple[tuple[str, str], ...] = ()
    analysis_state: str = "in_progress"
    allow_withdrawn: bool = False
    ui_locale: str = "en"


class PublicationProjection(Protocol):
    id: str
    rules_version: int

    def project(self, snapshot: ShareSnapshot, request: ShareRequest) -> Projection: ...


def _value(value, source):
    if isinstance(value, Text):
        return value
    if isinstance(value, str):
        return Text(value, "user", source)
    if isinstance(value, FrozenMap):
        return Object(tuple((Text(k, "user", source), _value(v, source)) for k, v in value.entries))
    if isinstance(value, tuple):
        return tuple(_value(v, source) for v in value)
    return value


def _at(values, path):
    for name in path.split("."):
        if not isinstance(values, FrozenMap):
            return MISSING
        values = values.get(name, MISSING)
    return values


def _columns(value):
    if not isinstance(value, tuple):
        return MISSING
    result = []
    for column in value:
        if isinstance(column, FrozenMap):
            kept = tuple((k, v) for k, v in column.entries if k in ("name", "type") and isinstance(v, str))
            if kept:
                result.append(FrozenMap(kept))
        elif isinstance(column, tuple) and len(column) >= 2 and all(isinstance(v, str) for v in column[:2]):
            result.append(column[:2])
    return tuple(result)


def _metadata(value, *, output=False):
    if not isinstance(value, FrozenMap):
        return FrozenMap()
    allowed = ("path", "sha256", "bytes", "rows", "schema", "change") if output else ("path", "sha256", "bytes")
    path = value.get("path")
    outside = not isinstance(path, str) or any(p.anchor or ".." in p.parts for p in (PurePosixPath(path), PureWindowsPath(path)))
    if value.get("external") or outside:
        return FrozenMap(tuple((k, v) for k, v in (("path", Text("Outside the project" if value.get("external") else "Path not included", "kit")),
                                                   ("sha256", value.get("sha256"))) if v is not None))
    entries = []
    for k, v in value.entries:
        if k not in allowed:
            continue
        if k == "schema":
            v = _columns(v)
            if v is MISSING:
                continue
        entries.append((k, v))
    return FrozenMap(tuple(entries))


def _safe_value(item, name, included):
    value = _at(item.values, name)
    if value is MISSING or name == "title" and value is None:
        return MISSING
    if name in ("path", "script.path") and isinstance(value, str):
        if item.values.get("_outside") or any(p.anchor or ".." in p.parts for p in (PurePosixPath(value), PureWindowsPath(value))):
            return Text("Outside the project" if item.values.get("_outside") else "Path not included", "kit")
    if name in ("columns", "schema"):
        return _columns(value)
    if name == "env.packages" and isinstance(value, FrozenMap):
        return FrozenMap(tuple((k, v) for k, v in value.entries if isinstance(v, str)))
    if name in ("inputs", "outputs"):
        return tuple(_metadata(v, output=name == "outputs") for v in (value or ()))
    def cited(key):
        return key in included and any(r.target == key and r.state == "resolved" for r in item.references)

    if name in ("details.metrics", "details.derived_from"):
        return tuple(FrozenMap(tuple((k, part) for k, part in v.entries if k in ("metric_id", "event_id", "role")))
                     for v in (value or ()) if isinstance(v, FrozenMap)
                     and cited(ItemKey("event", v.get("event_id"))))
    if name in ("details.previous_definition", "details.definition_event_id", "details.previous_focus"):
        return value if cited(ItemKey("event", value)) else MISSING
    if name == "details.run_id":
        return value if cited(ItemKey("run", value)) else MISSING
    if name == "details.metric_id" and item.kind == "metric_observation":
        definition = _at(item.values, "details.definition_event_id")
        return value if cited(ItemKey("event", definition)) else MISSING
    # Reproduction's path/id keyed maps can name versions absent from this publication. Project
    # its verdict and boolean only; exact versions remain ordinary filtered Reference objects.
    if name == "reproduction" and isinstance(value, FrozenMap):
        kept = [(k, v) for k, v in value.entries if k in ("verdict", "inputs_same")]
        original = value.get("of")
        if isinstance(original, str) and ItemKey("run", original) in included:
            kept.append(("of", original))
        return FrozenMap(tuple(sorted(kept)))
    if name == "state_history":
        return tuple(v for v in (value or ()) if isinstance(v, str) and ItemKey("event", v) in included)
    return value


def _reference(ref, included, excluded, reasons=None):
    target = ref.target if ref.target in included and ref.state not in ("missing", "unsupported") else None
    state, reason = ref.state, ref.reason
    if ref.target is not None and target is None and state == "resolved":
        state, reason = "not_included", "excluded" if ref.target in excluded else (reasons or {}).get(ref.target, "out_of_scope")
    candidates = tuple(k for k in ref.candidates if k in included)
    return Reference(ref.relation, ref.binding, ref.basis, state, reason, ref.category, target,
                     ref.confirmed if target is not None else False, candidates,
                     ref.omitted_candidates + len(ref.candidates) - len(candidates))


def _file(item, file, profile):
    if file.role not in COPY_ROLES or (file.role in ("attached_data", "code_snapshot") and not profile.evidence):
        return None, "profile"
    if file.reason:
        return None, file.reason
    if not file.current_sha256:
        return None, "file_absent"
    if file.role == "code_snapshot" and not file.recorded_sha256:
        return None, "no_digest"
    if file.recorded_sha256 and file.current_sha256 != file.recorded_sha256:
        return None, "content_changed"
    formats = {"figure": {"png", "jpeg", "gif", "webp", "svg", "html", "plotly"},
               "attached_data": {"parquet", "csv", "json"}, "code_snapshot": {"python"}}
    if file.format not in formats.get(file.role, set()):
        return None, "format"
    token = hashlib.sha256(f"{item.key.category}:{item.key.identity}:{file.role}:{file.source}".encode()).hexdigest()[:16]
    package_path = f"{'assets' if file.role == 'figure' else 'evidence'}/{token}{file.suffix}"
    entry = CopyEntry(item.key, file.source, package_path, file.role, file.recorded_sha256,
                      file.current_sha256, file.bytes)
    verified = "recorded_hash" if file.recorded_sha256 else "no_recorded_hash"
    return (FileSlot(package_path, file.role, verified), entry), None


def _field(item, name, included):
    value = _safe_value(item, name, included)
    if value is MISSING:
        return None
    if name in ("actor_role", "state", "status", "recording_status", "observation", "role"):
        value = Text(value if value in LABELS else "unknown", "kit")
    else:
        value = _value(value, (item.key, name))
    return Field(name, value)


class ReadProjection:
    id = "read"
    rules_version = 1

    def project(self, snapshot, request):
        profile = PROFILES[self.id]
        included = scope_closure(snapshot, request, profile)
        source = {i.key: i for i in snapshot.items}
        counts = Counter()
        for item in snapshot.items:
            if item.key not in included:
                counts[item.key.category, exclusion_reason(item, request, profile) or "out_of_scope"] += 1
        reasons = {k: exclusion_reason(i, request, profile) or "out_of_scope" for k, i in source.items()}
        items, copies = [], []
        for key in sorted(included, key=lambda k: (source[k].seq, k)):
            item, projected, slots = source[key], [], []
            for name in profile.fields(item):
                field = _field(item, name, included)
                if field is not None:
                    projected.append(field)
            for file in item.files:
                result, reason = _file(item, file, profile)
                if result:
                    slot, entry = result
                    slots.append(slot)
                    copies.append(entry)
                else:
                    counts["file", reason] += 1
            refs = tuple(_reference(r, included, request.exclude, reasons) for r in item.references)
            items.append(ShareItem(key, item.kind, tuple(projected), refs, tuple(slots)))
        omitted = tuple(Omission(category, reason, count) for (category, reason), count in sorted(counts.items()))
        title = next((f.value for i in items if i.key.category == "project" for f in i.fields if f.name == "title"),
                     Text("Untitled project", "kit"))
        model = ShareModel(snapshot.basis.content, request.analysis_state, title, tuple(items), omitted,
                           snapshot.diagnostics)
        ledger = ScopeLedger(tuple(sorted(included)), omitted,
                             tuple(sorted(set(source) - included)))
        result = Projection(model, tuple(sorted(copies, key=lambda f: f.package_path)), ledger, snapshot.basis)
        validate_projection(snapshot, request, profile, result)
        return result


class EvidenceProjection(ReadProjection):
    id = "evidence"


PROJECTIONS = {"read": ReadProjection, "evidence": EvidenceProjection}


def project(snapshot, request=None, *, profile="read", implementation=None):
    request = request if request is not None else ShareRequest()
    if profile not in PROFILES:
        raise PublicationError("profile_unknown", "unknown publication profile")
    strategy = implementation if implementation is not None else PROJECTIONS[profile]()
    result = strategy.project(snapshot, request)
    validate_projection(snapshot, request, PROFILES[profile], result)
    return result


def validate_projection(snapshot, request, profile, projection):
    """The coordinator's independent model/closure check, also used for injected strategies."""
    validate_scope(snapshot, request, profile, projection.ledger.included)
    if projection.basis != snapshot.basis or projection.model.basis != snapshot.basis.content:
        raise PublicationError("leak_found", "snapshot basis changed")
    if projection.model.analysis_state != request.analysis_state:
        raise PublicationError("leak_found", "declared analysis state changed")
    included = frozenset(projection.ledger.included)
    source = {i.key: i for i in snapshot.items}
    if {i.key for i in projection.model.items} != included:
        raise PublicationError("leak_found", "model and scope ledger disagree")

    def strings(value):
        if isinstance(value, str):
            return {value}
        if isinstance(value, FrozenMap):
            return {k for k, _ in value.entries} | {s for _, v in value.entries for s in strings(v)}
        if isinstance(value, tuple):
            return {s for v in value for s in strings(v)}
        return set()

    allowed_text = {(key, name): strings(_safe_value(item, name, included))
                    for key, item in source.items() if key in included for name in profile.fields(item)}
    reasons = {k: exclusion_reason(i, request, profile) or "out_of_scope" for k, i in source.items()}
    for item in projection.model.items:
        if item.references != tuple(_reference(r, included, request.exclude, reasons) for r in source[item.key].references):
            raise PublicationError("leak_found", "reference judgment changed")
        if any(field != _field(source[item.key], field.name, included) for field in item.fields):
            raise PublicationError("leak_found", "field value is not from its included allowed field")
        if item.kind != source[item.key].kind or any(f.name not in profile.fields(source[item.key]) for f in item.fields):
            raise PublicationError("leak_found", "unapproved item kind or field")

    def visit(value, technical=False):
        if isinstance(value, Text):
            if value.origin == "kit":
                if value.source is not None or value.value not in LABELS:
                    raise PublicationError("leak_found", "unregistered kit label")
            elif value.origin != "user" or not value.source or value.value not in allowed_text.get(value.source, set()):
                raise PublicationError("leak_found", "user text is not from its included allowed field")
            return
        if isinstance(value, str) and not technical:
            raise PublicationError("leak_found", "display strings must be Text values")
        if isinstance(value, (dict, list)):
            raise PublicationError("leak_found", "mutable/raw objects do not belong in the shared model")
        if is_dataclass(value):
            if isinstance(value, ItemKey):
                if value not in included:
                    raise PublicationError("leak_found", "out-of-scope item identity")
                return
            if isinstance(value, Reference):
                if value.target is not None and value.target not in included:
                    raise PublicationError("leak_found", "out-of-scope reference identity")
                if any(k not in included for k in value.candidates):
                    raise PublicationError("leak_found", "out-of-scope candidate identity")
            for f in fields(value):
                # These are finite enums, fixed field names and snapshot identifiers, not page text.
                meta = (isinstance(value, (Reference, FileSlot, Omission, ContentBasis))
                        or isinstance(value, Field) and f.name == "name"
                        or isinstance(value, ShareItem) and f.name == "kind"
                        or isinstance(value, ShareModel) and f.name == "analysis_state")
                visit(getattr(value, f.name), technical=meta)
        elif isinstance(value, tuple):
            for v in value:
                visit(v)
    visit(projection.model)
    allowed_entries, allowed_slots = set(), set()
    for key in included:
        for file in source[key].files:
            result, _ = _file(source[key], file, profile)
            if result:
                slot, entry = result
                allowed_entries.add(entry)
                allowed_slots.add((key, slot))
    if not set(projection.copy_plan) <= allowed_entries:
        raise PublicationError("leak_found", "invalid copy-plan role, file or scope")
    for item in projection.model.items:
        for slot in item.files:
            if (item.key, slot) not in allowed_slots or not any(e.item == item.key and e.package_path == slot.package_path for e in projection.copy_plan):
                raise PublicationError("leak_found", "file slot is not backed by its approved copy plan")
