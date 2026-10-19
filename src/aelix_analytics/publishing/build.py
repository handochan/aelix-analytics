"""Shared publication assembly, private build writing and journal binding; preview stays read-only."""

from __future__ import annotations

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
        rules_version=strategy_rules,
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
        rules_version=strategy_rules,
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
    project, request=None, *, profile="read", projection=None, exclude=(), snapshot=None
):
    """Full byte preview, preserving its public two-tuple and its no-write/no-lock boundary."""
    summary, files, _private = _assemble(
        project, request, profile=profile, projection=projection, exclude=exclude, snapshot=snapshot
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
        "scope": {"kind": assembled["request"].scope, "reports": []},
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


def build_publication(
    project, request=None, *, profile="read", projection=None, exclude=(), zip_path=None, origin
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
    with project.platform.locks.exclusive(lock):
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
        )
        refs = _context_refs(project, snapshot)
        exclusions = _exclusion_refs(snapshot, assembled["request"])
        manifest = _manifest(summary, files, assembled, identity)
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
