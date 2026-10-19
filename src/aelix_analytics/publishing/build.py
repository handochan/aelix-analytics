"""Read-only preview assembly. Actual folder/manifest/binding is Issue20 PR3."""

from __future__ import annotations

import os
import platform
import socket
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path, PureWindowsPath

from .. import core
from . import checks
from .extract import parquet_strings
from .model import (
    ContentBasis,
    ItemKey,
    PublicationError,
    PublicationIdentity,
    ReaderNotice,
    RenderFacts,
)
from .package import prepare_files
from .projection import ShareRequest, SnapshotItem, freeze
from .projection import project as project_projection
from .render import render
from .scope import PROFILES, leak_policy, request_check
from .snapshot import capture_snapshot


def _pending_context(project, snapshot):
    path = project.root / "CONTEXT.md"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return snapshot, "recorded" if snapshot.basis.content.context_version else "not_recorded"
    except OSError as exc:
        raise PublicationError("context_missing", "cannot read the pending CONTEXT") from exc
    current = next((i for i in snapshot.items if i.kind == "context"), None)
    text = current.values.get("text") if current else None
    if isinstance(text, str) and data == text.encode("utf-8"):
        return snapshot, "recorded"
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise PublicationError("context_unsupported", "pending CONTEXT is not UTF-8") from exc
    items = tuple(
        replace(i, kind="context_history") if i.kind == "context" else i for i in snapshot.items
    )
    pending = SnapshotItem(
        ItemKey("context", "pending"),
        "context",
        freeze({"text": text, "version": None, "recorded_at": None, "observation": "not_recorded"}),
        root=True,
    )
    basis = replace(
        snapshot.basis,
        content=ContentBasis(
            snapshot.basis.content.content_seq, snapshot.basis.content.content_recorded_at, None
        ),
    )
    return replace(snapshot, basis=basis, items=(*items, pending)), "pending"


def resolve_exclusions(snapshot, values):
    keys = set()
    items = {i.key: i for i in snapshot.items}
    for value in values:
        matches = {
            i.key
            for i in snapshot.items
            if i.key.identity == value
            or i.key.category == "dataset"
            and i.values.get("name") == value
        }
        if "::" in value:
            script, visualization_id = value.rsplit("::", 1)
            for i in snapshot.items:
                if (
                    i.key.category != "visualization"
                    or i.values.get("visualization_id") != visualization_id
                ):
                    continue
                run = items.get(ItemKey("run", i.key.identity.split("/", 1)[0]))
                desc = run.values.get("script") if run else None
                if desc and hasattr(desc, "get") and desc.get("path") == script:
                    matches.add(i.key)
        if not matches:
            raise PublicationError(
                "scope_invalid", "exclude target is unknown", details={"target": value}
            )
        keys.update(matches)
    return frozenset(keys)


def _environment(project, snapshot):
    root = project.root
    # gettempdir() probes writability with a file on its first call. Preview observes the
    # cached/configured location instead so even environment collection stays read-only.
    temporary = (
        os.fsdecode(tempfile.tempdir)
        if tempfile.tempdir is not None
        else (
            os.environ.get("TMPDIR")
            or os.environ.get("TEMP")
            or os.environ.get("TMP")
            or (str(Path.home() / "AppData/Local/Temp") if os.name == "nt" else "/tmp")
        )
    )
    values = {
        str(root),
        str(root.resolve()),
        str(Path.home()),
        temporary,
        socket.gethostname(),
        platform.node(),
        os.environ.get("VIRTUAL_ENV", ""),
        os.environ.get("AELIX_ANALYTICS_SESSION", ""),
        sys.executable,
        sys.prefix if sys.prefix != sys.base_prefix else "",
        *snapshot.private_values,
    }

    def walk(value, path=""):
        if isinstance(value, str):
            if path.endswith("path") or path == "env.executable":
                if Path(value).is_absolute() or PureWindowsPath(value).anchor:
                    values.add(value)
        elif hasattr(value, "entries"):
            for k, v in value.entries:
                walk(v, (path + "." if path else "") + k)
        elif isinstance(value, tuple):
            for v in value:
                walk(v, path)

    for item in snapshot.items:
        walk(item.values)
    for value in tuple(values):
        try:
            relative = Path(value).relative_to(Path.home())
            if relative.as_posix() != ".":
                values.add("~/" + relative.as_posix())
        except ValueError:
            pass
    return tuple(sorted(values))


def _checked_files(files, roles, policy, pinned, model):
    try:
        return checks.check_package(
            files, roles, policy=policy, parquet_extractor=parquet_strings, pinned=pinned
        )
    except PublicationError as exc:
        path = exc.details.get("path")
        sources = [
            {"category": item.key.category, "identity": item.key.identity, "role": file.role}
            for item in model.items
            for file in item.files
            if file.package_path == path
        ]
        if sources or roles.get(path) == "library":
            raise PublicationError(
                exc.code,
                exc.message,
                details={
                    **exc.details,
                    "sources": sources,
                    "cause": "copied_file" if sources else "bundled_file",
                },
            ) from exc
        raise


def _assemble(
    project,
    request=None,
    *,
    profile="read",
    projection=None,
    exclude=(),
    snapshot=None,
    pending=True,
    identity=None,
):
    """Full in-memory byte path, injectable strategy, no project writes, locks or context sync."""
    if profile not in PROFILES:
        raise PublicationError("profile_unknown", "unknown publication profile")
    request = ShareRequest() if request is None else request
    request_check(request, PROFILES[profile])
    strategy_id = getattr(projection, "id", profile)
    strategy_rules = getattr(projection, "rules_version", PROFILES[profile].rules_version)
    snapshot = capture_snapshot(project) if snapshot is None else snapshot
    if identity is not None and (
        pending
        or not isinstance(identity, PublicationIdentity)
        or identity.as_of_seq != snapshot.basis.as_of_seq
    ):
        raise PublicationError(
            "package_check_failed", "copy identity and recorded snapshot basis differ"
        )
    if pending:
        snapshot, context_status = _pending_context(project, snapshot)
    else:
        context_status = "recorded" if snapshot.basis.content.context_version else "not_recorded"
    if exclude:
        request = replace(request, exclude=request.exclude | resolve_exclusions(snapshot, exclude))
    result = project_projection(snapshot, request, profile=profile, implementation=projection)
    policy = leak_policy(
        snapshot, request, PROFILES[profile], result, environment=_environment(project, snapshot)
    )
    model_warnings = checks.check_model(result.model, policy)
    prepared, files, roles, pinned, details = prepare_files(project, result)
    pages = render(
        prepared.model,
        profile=strategy_id,
        context_status=context_status,
        identity=identity,
        file_sizes=tuple((p, len(b)) for p, b in files.items()),
    )
    if set(pages) & set(files):
        raise PublicationError(
            "package_check_failed", "renderer tried to replace a copied or pinned resource"
        )
    generated_paths = frozenset(pages)
    files.update(pages)
    roles.update({p: "identity" if p in checks.IDENTITY_FILES else "generated" for p in pages})
    checked = _checked_files(files, roles, policy, pinned, prepared.model)
    # Counts and closed warning labels are the only post-inspection renderer inputs.
    notices = set()
    for warning in (*model_warnings, *checked["warnings"]):
        code = warning["reason"]
        if code not in ("path_pattern", "excluded_field_line", "image_metadata"):
            continue
        key = warning.get("key", "").lower()
        kind = (
            "gps"
            if "gps" in key
            else "serial"
            if "serial" in key
            else "author"
            if any(
                s in key
                for s in ("author", "artist", "creator", "copyright", "make", "model", "software")
            )
            else "other"
        )
        notices.add(ReaderNotice(warning.get("path", "model"), code, kind))
    no_hash = sum(f.verified == "no_recorded_hash" for i in prepared.model.items for f in i.files)
    facts = RenderFacts(
        tuple(sorted(checked["inspection"].items())),
        tuple(sorted(notices, key=lambda n: (n.path, n.code, n.metadata_kind))),
        no_hash,
        tuple(sorted(details["points"].items())),
    )
    pages = render(
        replace(prepared.model, facts=facts),
        profile=strategy_id,
        context_status=context_status,
        identity=identity,
        file_sizes=tuple((p, len(b)) for p, b in files.items()),
    )
    if frozenset(pages) != generated_paths:
        raise PublicationError(
            "package_check_failed", "inspection summary changed the renderer's file set"
        )
    files.update(pages)
    checked = _checked_files(files, roles, policy, pinned, prepared.model)
    warnings = [*model_warnings, *checked["warnings"]]
    if policy.short_markers:
        warnings.append({"reason": "short_markers_not_scanned", "count": policy.short_markers})
    counts = Counter(i.key.category for i in prepared.model.items)
    summary = {
        "ok": True,
        "preview": True,
        "profile": {
            "id": strategy_id,
            "rules_version": strategy_rules,
        },
        "basis": {
            **asdict(snapshot.basis.content),
            "as_of_seq": snapshot.basis.as_of_seq,
            "context_status": context_status,
        },
        "analysis_state": {"value": request.analysis_state, "basis": "declared"},
        "included": dict(sorted(counts.items())),
        "not_included": [asdict(o) for o in prepared.model.not_included],
        "files": len(files),
        "bytes": sum(map(len, files.values())),
        "content_sha256": checks.content_hash(files),
        "checks": {"format": 1, "result": "pass", "inspection": checked["inspection"]},
        "renderer": {"kit_version": core.KIT_VERSION, "format": 1, **details["renderer"]},
        "limits": {
            "no_recorded_hash": no_hash,
            "longest_relative_path": checked["longest_relative_path"],
            "interactive_figure_points": sum(details["points"].values()),
        },
        "warnings": warnings,
        "remote_links": checked["remote_links"],
    }
    # Byte view is an internal return only. CLI serializes summary, never file data or source paths.
    return (
        summary,
        files,
        {
            "snapshot": snapshot,
            "projection": prepared,
            "original_projection": result,
            "roles": roles,
            "pinned": pinned,
            "details": details,
            "policy": policy,
            "checked": checked,
            "context_status": context_status,
        },
    )


def preview_package(
    project, request=None, *, profile="read", projection=None, exclude=(), snapshot=None
):
    """Full byte preview, preserving its public two-tuple and its no-write/no-lock boundary."""
    summary, files, _private = _assemble(
        project, request, profile=profile, projection=projection, exclude=exclude, snapshot=snapshot
    )
    return summary, files
