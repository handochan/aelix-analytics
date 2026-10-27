"""Shared publication assembly, private build writing and journal binding; preview stays read-only."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path, PureWindowsPath

from .. import _platform, core, relations, writer
from . import checks, package
from .extract import parquet_strings
from .model import (
    LOCK_WAIT_SECONDS,
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
from .render import key_name, render
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
            # <report_id>@<revision_id>, the form the screens and the output use (OQ5).
            or i.key.category == "report"
            and i.key.identity == value.replace("@", "/", 1)
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


def resolve_reports(snapshot, request):
    """Exact selected Report versions: a report_id alone is its head (the output names the version
    it chose, 8.1); a collection version adds its complete members' exact versions (5.1)."""
    if not request.reports:
        return request
    versions, attempts = {}, set()
    for item in snapshot.items:
        if item.key.category == "report":
            report_id, _, revision_id = item.key.identity.partition("/")
            if item.kind == "report_revision":
                versions.setdefault(report_id, []).append((item.values.get("number") or 0, revision_id, item))
            else:
                attempts.add(report_id)
    chosen = []
    for report_id, revision_id in request.reports:
        if not revision_id:
            if report_id not in versions:
                if report_id in attempts:
                    raise PublicationError("report_not_shareable", "the selected Report has no saved version",
                                           details={"reason": "not_saved", "report": report_id})
                raise PublicationError("scope_invalid", "unknown Report", details={"report": report_id})
            revision_id = max(versions[report_id])[1]
        chosen.append((report_id, revision_id))
    if request.scope == "collection":
        if len(request.reports) != 1 or not request.reports[0][1]:
            raise PublicationError("scope_invalid", "--collection takes one exact report_id@revision_id")
        items = {item.key: item for item in snapshot.items}
        item = items.get(ItemKey("report", "/".join(chosen[0])))
        collection = item.values.get("collection") if item is not None else None
        if item is None or item.kind != "report_revision" or collection is None:
            raise PublicationError("scope_invalid", "the selected version is not a Report collection",
                                   details={"report": "@".join(chosen[0])})
        for member in collection.get("members") or ():
            pair = (member.get("report_id"), member.get("revision_id"))
            if member.get("status") == "complete" and pair not in chosen:
                if not all(isinstance(v, str) for v in pair):
                    raise PublicationError("report_not_shareable", "a complete member names no Report version",
                                           details={"reason": "member_missing", "report": "@".join(chosen[0])})
                chosen.append(pair)
    return replace(request, reports=tuple(chosen))


def _report_views(model, files, renderer):
    """Each included Report version's body document from the injected ReportRenderer (7.5): only its
    bytes and {manifest path: package path}, never the project. Its output is checked like a
    user document (role report_view), not trusted as a kit page."""
    views, facts = {}, []
    for item in model.items:
        if item.key.category != "report":
            continue
        if renderer is None:
            raise PublicationError("renderer_unavailable", "no ReportRenderer was given for a selected Report")
        folder = f"reports/{key_name(item.key)}/"
        data = {slot.package_path[len(folder) + len("content/"):]: files[slot.package_path] for slot in item.files}
        entry = next((f.value.value for f in item.fields if f.name == "entry"), None)
        shown = next((s.format for s in item.files if s.package_path == f"{folder}content/{entry}"), None)
        if shown in ("html", "svg", "png", "jpeg", "gif", "webp"):
            continue  # the checked original itself is the body (7.5); no derived document is needed
        try:
            view = renderer.render(data, entry=entry, paths={name: f"content/{name}" for name in data})
        except ValueError:
            facts.append((key_name(item.key), "", False, ("renderer_refused",)))
            continue
        documents = dict(view.files)
        if (not all(isinstance(name, str) and isinstance(body, bytes) for name, body in documents.items())
                or any(name.startswith("content/") or name in ("index.html",) or name.startswith("/")
                       or ".." in name.split("/") for name in documents)):
            raise PublicationError("package_check_failed", "the ReportRenderer returned an unusable view path")
        for name, body in documents.items():
            views[folder + name] = body
        page = next((name for name in documents if name.endswith(".html")), "")
        facts.append((key_name(item.key), folder + page if page else "", bool(view.supported and page),
                      tuple(str(r) for r in view.reasons)))
    return views, tuple(sorted(facts))


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
    report_renderer=None,
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
    request = resolve_reports(snapshot, request)
    result = project_projection(snapshot, request, profile=profile, implementation=projection)
    policy = leak_policy(
        snapshot, request, PROFILES[profile], result, environment=_environment(project, snapshot)
    )
    model_warnings = checks.check_model(result.model, policy)
    prepared, files, roles, pinned, details = prepare_files(project, result)
    views, view_facts = _report_views(prepared.model, files, report_renderer)
    if set(views) & set(files):
        raise PublicationError("package_check_failed", "a Report view tried to replace a copied file")
    files.update(views)
    roles.update({p: "report_view" for p in views})
    prepared = replace(prepared, model=replace(prepared.model, facts=RenderFacts(report_views=view_facts)))
    pages = render(
        prepared.model,
        profile=strategy_id,
        rules_version=strategy_rules,
        context_status=context_status,
        identity=identity,
        file_sizes=tuple((p, len(b)) for p, b in files.items()),
        scope=request.scope,
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
        view_facts,
    )
    pages = render(
        replace(prepared.model, facts=facts),
        profile=strategy_id,
        rules_version=strategy_rules,
        context_status=context_status,
        identity=identity,
        file_sizes=tuple((p, len(b)) for p, b in files.items()),
        scope=request.scope,
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
        # The exact versions chosen, so a head selection never silently means "the latest" (8.1).
        "scope": {"kind": request.scope, "reports": [f"{r}@{v}" for r, v in request.reports]},
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
            "request": request,
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
    project, request=None, *, profile="read", projection=None, exclude=(), snapshot=None,
    report_renderer=None,
):
    """Full byte preview, preserving its public two-tuple and its no-write/no-lock boundary."""
    summary, files, _private = _assemble(
        project, request, profile=profile, projection=projection, exclude=exclude, snapshot=snapshot,
        report_renderer=report_renderer,
    )
    return summary, files


def new_publication_id():
    alphabet = "0123456789abcdefghjkmnpqrstvwxyz"
    milliseconds = int(time.time() * 1000)
    if not 0 <= milliseconds < 1 << 48:
        raise PublicationError("package_check_failed", "clock is outside the publication ID range")
    value = (milliseconds << 80) | secrets.randbits(80)
    return "pub-" + "".join(alphabet[(value >> (5 * n)) & 31] for n in reversed(range(26)))


def _managed(root, path):
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        try:
            if _platform.is_link(current.lstat()):
                raise PublicationError(
                    "package_check_failed", "publication storage cannot follow a link"
                )
        except FileNotFoundError:
            pass


def _json_bytes(value):
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode()


def _exclusion_refs(snapshot, request):
    items = {item.key: item for item in snapshot.items}
    types = {
        "code": "code_snapshot",
        "context": "context_version",
        "dataset": "dataset_import",
        "data_observation": "data_observation",
        "report": "report_revision",
    }
    result = []
    for key in sorted(request.exclude):
        item = items.get(key)
        if item is None:
            raise PublicationError("scope_invalid", "exclude target is unknown")
        ref = {"relation": "about", "basis": "declared", "version_binding": "author"}
        if key.category == "event":
            ref.update(type="event", event_id=item.values.get("id"), token=key.identity)
        elif key.category == "run":
            ref.update(type="run", run_id=key.identity)
        elif key.category == "visualization":
            rid, _, vid = key.identity.partition("/")
            ref.update(type="visualization_version", run_id=rid, visualization_id=vid)
        elif key.category in types:
            # A negative selector is not an invented historical file digest. Its exact immutable
            # snapshot key stays in the existing unbound token vocabulary of TypedRef.
            ref.update(type=types[key.category], version_binding="none", token=key.identity)
        else:
            raise PublicationError(
                "scope_invalid", "target cannot be excluded", details={"target": key.identity}
            )
        if relations.reference_problem(ref):
            raise PublicationError(
                "scope_invalid",
                "excluded target has no supported exact reference",
                details={"target": key.identity},
            )
        result.append(ref)
    return result


def _context_refs(project, snapshot):
    version = snapshot.basis.content.context_version
    if version is None:
        return []
    item = next(
        (i for i in snapshot.items if i.kind == "context" and i.key.identity == version), None
    )
    path = relations.context_file(project.root, version)
    if path is None or item is None or not isinstance(item.values.get("text"), str):
        raise PublicationError("context_missing", "the exact preserved CONTEXT is unavailable")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PublicationError("context_missing", "cannot read the preserved CONTEXT") from exc
    if data != item.values.get("text").encode():
        raise PublicationError("source_changed", "preserved CONTEXT changed after snapshot capture")
    return [
        {
            "type": "context_version",
            "version": version,
            "digest": {"sha256": hashlib.sha256(data).hexdigest()},
            "relation": "includes",
            "basis": "observed",
            "version_binding": "author",
        }
    ]


def _manifest(summary, files, assembled, identity):
    def package_key(key):
        # Use the existing package page/anchor key. A selected visualization's private ID
        # contains its producer run ID, which may itself be explicitly excluded.
        return {"category": key.category, "identity": key_name(key)}

    projection = assembled["projection"]
    slots = {}
    for item in projection.model.items:
        for slot in item.files:
            slots.setdefault(slot.package_path, []).append((item.key, slot))
    rows = []
    for path, data in sorted(files.items()):
        observed = assembled["checked"]["files"][path]
        role = assembled["roles"][path]
        origin = (
            "identity"
            if path in checks.IDENTITY_FILES
            else "bundled"
            if role == "library"
            else "copied"
            if path in slots and path not in assembled["details"]["derived"]
            else "generated"
        )
        row = {
            "path": path,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "origin": origin,
            "format": observed["format"],
            "inspection": observed["inspection"],
        }
        if origin == "copied":
            sources = []
            for key, slot in sorted(slots[path], key=lambda pair: pair[0]):
                entry = next(
                    e for e in projection.copy_plan if e.item == key and e.package_path == path
                )
                sources.append(
                    {
                        "item": package_key(key),
                        "role": entry.role,
                        "verified": slot.verified,
                        "recorded_sha256": entry.recorded_sha256,
                        "current_sha256": entry.current_sha256,
                    }
                )
            row["sources"] = sources
        if path in assembled["details"]["derived"]:
            row["derived_from"] = [
                {
                    "item": package_key(d["item"]),
                    "role": d["role"],
                    "recorded_sha256": d["recorded_sha256"],
                    "source_sha256": d["source_sha256"],
                }
                for d in assembled["details"]["derived"][path]
            ]
        rows.append(row)
    warning_counts = Counter()
    for warning in summary["warnings"]:
        warning_counts[warning["reason"]] += warning.get("count", 1)
    inspection = dict(summary["checks"]["inspection"])
    inspection["inspected"] += (
        1  # publication.json is checked as the last file, not listed in files[].
    )
    return {
        "schema": relations.PUBLICATION_V1,
        "publication_id": identity.publication_id,
        "built_at": identity.built_at,
        "entry": "index.html",
        "basis": {
            k: summary["basis"][k]
            for k in ("as_of_seq", "content_seq", "content_recorded_at", "context_version")
        },
        "analysis_state": summary["analysis_state"],
        "ui_locale": "en",
        "profile": summary["profile"],
        "scope": {"kind": "project" if assembled["request"].scope == "project" else "reports",
                  "reports": _scope_reports(assembled)},
        "included": [package_key(k) for k in sorted(projection.ledger.included)],
        "not_included": summary["not_included"],
        "reader_features": [
            "read",
            "relations",
            "downloads",
            *(["interactive_figures"] if assembled["details"]["points"] else []),
        ],
        "renderer": summary["renderer"],
        "remote_links": summary["remote_links"],
        "limits": {
            **summary["limits"],
            "warnings": [
                {"reason": reason, "count": count}
                for reason, count in sorted(warning_counts.items())
            ],
        },
        "checks": {"format": 1, "result": "pass", "inspection": inspection},
        "content_sha256": checks.content_hash(files),
        "files": rows,
    }


def _scope_reports(assembled):
    """ADR-0014 decision 4: each selected exact version, its Version n and state at this basis, its
    manifest bytes' SHA-256 and where each of its files is in this copy."""
    items = {item.key: item for item in assembled["snapshot"].items}
    model = {item.key: item for item in assembled["projection"].model.items}
    out = []
    for report_id, revision_id in assembled["request"].reports:
        key = ItemKey("report", f"{report_id}/{revision_id}")
        source, shared = items[key], model[key]
        directory = source.values.get("_directory")
        files = []
        for slot in shared.files:
            entry = next(e for e in assembled["projection"].copy_plan if e.item == key and e.package_path == slot.package_path)
            files.append({"path": entry.source.removeprefix(f"{directory}/content/"), "sha256": entry.recorded_sha256,
                          "package_path": slot.package_path})
        out.append({"report_id": report_id, "revision_id": revision_id, "version": source.values.get("number"),
                    "state": source.values.get("state"), "manifest_sha256": source.values.get("_manifest_sha256"),
                    "files": sorted(files, key=lambda f: f["path"])})
    return out


def _report_refs(manifest):
    return [{"type": "report_revision", "report_id": r["report_id"], "revision_id": r["revision_id"],
             "digest": {"sha256": r["manifest_sha256"]}, "relation": "includes", "basis": "declared",
             "version_binding": "author"} for r in manifest["scope"]["reports"]]


def _completion_details(manifest, digest, exclusions):
    pid = manifest["publication_id"]
    return {
        "publication_id": pid,
        "format": manifest["schema"],
        "package_sha256": digest,
        "content_sha256": manifest["content_sha256"],
        "profile": manifest["profile"],
        "scope": manifest["scope"]["kind"],
        "basis": {k: manifest["basis"][k] for k in ("as_of_seq", "content_seq", "context_version")},
        "analysis_state": manifest["analysis_state"],
        "exclusions": exclusions,
        "location": f".analytics/publications/{pid}",
        "checks": manifest["checks"],
    }


def _observe_completion(record_writer, pid, digest):
    # Read-only reconciliation must also work after another writer raises the marker. It takes
    # the common journal lock but neither admits a new write nor raises a floor.
    try:
        with record_writer.platform.locks.exclusive(
            record_writer.root / writer.LAYOUT.state_dir / writer.LAYOUT.journal_lock
        ):
            journal = relations.Journal(writer._journal_bytes(record_writer.journal))
            state, _reason, _candidates, value = relations.publication_record(journal, pid)
            if state == "resolved":
                if value["details"]["package_sha256"] == digest:
                    return "complete", value, False
                return "unbound", None, False
            return ("unbound" if state == "missing" else "unknown"), None, False

    except KeyboardInterrupt:
        return "unknown", None, True
    except Exception:
        return "unknown", None, False


@contextlib.contextmanager
def _build_lock(project, lock):
    """One build of a project at a time (BUILD 9.5 R18), waiting at most LOCK_WAIT_SECONDS (#21 OQ3)."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(project.platform.locks.exclusive_within(lock, LOCK_WAIT_SECONDS))
        except _platform.LockBusy as exc:
            raise PublicationError(
                "build_busy",
                "another build of this project still holds its lock; run the build again",
                details={"waited_seconds": LOCK_WAIT_SECONDS},
            ) from exc
        yield


def build_publication(
    project, request=None, *, profile="read", projection=None, exclude=(), zip_path=None, origin,
    report_renderer=None,
):
    """Build privately, bind once, reconcile uncertain append results; never execute analysis."""
    if profile not in PROFILES:
        raise PublicationError("profile_unknown", "unknown publication profile")
    request = ShareRequest() if request is None else request
    request_check(request, PROFILES[profile])
    w = core.writer_for(project, origin)
    w.admit("build_publication")
    base = project.root / ".analytics/publications"
    lock = base / ".lock"
    _managed(project.root, lock)
    with _build_lock(project, lock):
        w.admit("build_publication")
        core.sync_context(project, origin=core.system_origin(origin))
        snapshot = capture_snapshot(project)
        pid = new_publication_id()
        if not isinstance(pid, str) or not relations.PUBLICATION_ID.fullmatch(pid):
            raise PublicationError("package_check_failed", "invalid generated publication identity")
        directory = base / pid
        _managed(project.root, directory)
        if (
            directory.exists()
            or pid in relations.Journal(writer._journal_bytes(w.journal))._publications
        ):
            raise PublicationError("package_check_failed", "publication identity already exists")
        identity = PublicationIdentity(pid, core.iso(), snapshot.basis.as_of_seq)
        summary, files, assembled = _assemble(
            project,
            request,
            profile=profile,
            projection=projection,
            exclude=exclude,
            snapshot=snapshot,
            pending=False,
            identity=identity,
            report_renderer=report_renderer,
        )
        exclusions = _exclusion_refs(snapshot, assembled["request"])
        manifest = _manifest(summary, files, assembled, identity)
        refs = _context_refs(project, snapshot) + _report_refs(manifest)
        files = {**files, "publication.json": _json_bytes(manifest)}
        roles = {**assembled["roles"], "publication.json": "identity"}
        checked = _checked_files(
            files, roles, assembled["policy"], assembled["pinned"], assembled["projection"].model
        )
        if checked["inspection"] != manifest["checks"]["inspection"]:
            raise PublicationError(
                "package_check_failed", "manifest inspection counts differ from observed files"
            )
        verified = checks.verify_package(files)
        digest = verified["package_sha256"]
        details = _completion_details(manifest, digest, exclusions)
        problem = relations.publication_details_problem(details, refs=refs)
        if problem:
            raise PublicationError("package_check_failed", problem)
        created = False
        binding_started = False
        event = None
        recovered = None
        try:
            directory.mkdir()
            created = True
            owned = directory.stat().st_dev, directory.stat().st_ino
            package.write_files(
                directory,
                {name: data for name, data in files.items() if name != "publication.json"},
            )
            package.write_files(directory, {"publication.json": files["publication.json"]})
            checks.verify_package(directory)
            binding_started = True
            try:
                with w.locked("build_publication") as held:
                    if pid in held.journal._publications:
                        raise ValueError("publication identity already recorded")
                    event = held.append(
                        "publication",
                        f"Built publication {pid} ({summary['profile']['id']}; records up to e{snapshot.basis.as_of_seq})",
                        refs=refs,
                        details=details,
                        requires=relations.PUBLICATION_WRITER_SINCE,
                    )
            except BaseException as exc:
                if isinstance(exc, writer.AnalyticsError) and exc.code == "text_too_long":
                    # Writer checks this before raising the floor or appending any bytes.
                    # This is a definite pre-binding rejection, not an uncertain append.
                    binding_started = False
                    raise PublicationError(exc.code, exc.message, details=exc.details) from exc
                state, event, reread_interrupted = _observe_completion(w, pid, digest)
                if state != "complete":
                    raise PublicationError(
                        "publication_not_recorded",
                        "publication folder retained; completion was not confirmed",
                        details={
                            "binding": state,
                            "interrupted": isinstance(exc, KeyboardInterrupt) or reread_interrupted,
                            "publication_id": pid,
                            "path": details["location"],
                            "package_sha256": digest,
                            "cause": getattr(exc, "code", type(exc).__name__),
                        },
                    ) from exc
                recovered = exc
        except BaseException as exc:
            if created and not binding_started:
                try:
                    current = directory.lstat()
                    if _platform.is_link(current) or (current.st_dev, current.st_ino) != owned:
                        raise OSError("created folder identity changed")
                    shutil.rmtree(directory)
                except OSError as cleanup:
                    if isinstance(exc, PublicationError):
                        exc.details.update(
                            cleanup_path=details["location"], cleanup_error=str(cleanup)
                        )
                    else:
                        raise PublicationError(
                            "package_check_failed",
                            "failed build could not be cleaned up",
                            details={
                                "path": details["location"],
                                "cause": type(exc).__name__,
                                "cleanup_error": str(cleanup),
                            },
                        ) from exc
            if isinstance(exc, OSError):
                raise PublicationError(
                    "package_check_failed",
                    "cannot write publication files",
                    details={"path": details["location"], "cause": type(exc).__name__},
                ) from exc
            raise
        result = {
            **summary,
            "preview": False,
            "publication_id": pid,
            "path": details["location"],
            "package_sha256": digest,
            "content_sha256": manifest["content_sha256"],
            "files": len(files),
            "bytes": sum(map(len, files.values())),
            "checks": manifest["checks"],
            "recording_status": "complete",
            "record": {"id": event["id"], "seq": event["seq"]},
            "zip": None,
            "review": f"Open {details['location']}/index.html to check it before you share it.",
        }
        if recovered is not None:
            result["binding_recovered"] = True
            result["interrupted"] = isinstance(recovered, KeyboardInterrupt)
        if zip_path is not None and not result.get("interrupted"):
            try:
                result["zip"] = package.write_zip(
                    directory, zip_path, project_root=project.root, binding=details
                )
            except BaseException as exc:
                if isinstance(exc, (SystemExit, GeneratorExit)):
                    raise
                result.update(
                    ok=False,
                    error={
                        "code": getattr(exc, "code", "package_check_failed"),
                        "message": str(exc),
                        "details": {
                            **getattr(exc, "details", {}),
                            "recording_status": "complete",
                            "publication_id": pid,
                        },
                    },
                    interrupted=isinstance(exc, KeyboardInterrupt),
                )
        return result
