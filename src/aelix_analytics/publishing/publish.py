"""Publishing a bound build to a folder (Issue #21, docs/design/PUBLISH-FOLDER.md 4, 6 and 8).

The steps: judge (read only) → binding → start record → target folder → lock → judge again → marker →
managed folders → earlier traces → transfer → check → commit → final check → switch → cleanup →
receipt. Only a version checked where it lies reaches a pointer; nothing here rereads the project's
analysis or changes a version with content. The receipt is written last (ADR-0006's commit rule)."""

from __future__ import annotations

import contextlib
import errno
import getpass
import hashlib
import html
import json
import os
import shutil
import socket
import stat
import time
from datetime import datetime, timedelta
from pathlib import Path

from .. import _platform, core, relations, writer
from . import checks, target
from .model import LOCK_WAIT_SECONDS, PublicationError

OPERATION = "publish"
# The owner record's updated_at is refreshed at least this often while a copy runs (ADR-0009: under
# an hour), and another attempt's leftovers go only after this long without one (24 hours).
REFRESH_SECONDS = 30 * 60
STALE = timedelta(hours=24)
NO_LOCK_ERRNOS = frozenset({errno.ENOLCK, getattr(errno, "ENOTSUP", errno.ENOLCK),
                            getattr(errno, "EOPNOTSUPP", errno.ENOLCK)})
ACCESS_SENTENCE = "Anyone who can open this folder can read every version in it."


def _reached(step: str) -> None:
    """A step boundary. Nothing happens here; tests interrupt a publish at a named boundary."""


def _boundary(state, step: str) -> None:
    state["_at"] = step
    _reached(step)


class _Stop(Exception):
    """An attempt ends at a step: the receipt records why."""

    def __init__(self, step, code, message, outcome="failed", **details):
        super().__init__(message)
        self.step, self.code, self.message, self.outcome, self.details = step, code, message, outcome, details


def _now() -> str:
    return core.iso()


def _instant(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return stamp if stamp is not None and stamp.utcoffset() is not None else None


def _user() -> str | None:
    try:
        return target.digest(f"uid:{os.getuid()}") if hasattr(os, "getuid") else target.digest(
            f"user:{getpass.getuser()}")
    except (OSError, KeyError):
        return None


def _write_json(path: Path, value, files, attempt_id: str) -> None:
    """A target JSON file by a temporary name holding the attempt and a replace (PUBLISH-FOLDER 8)."""
    temporary = path.with_name(f".{attempt_id}.{path.name}.tmp")
    with open(temporary, "wb") as fh:
        fh.write(target.canonical(value))
    files.replace(temporary, path)


def _read_regular(path: Path) -> bytes:
    """A build file's bytes, never through a link (design 4, transfer (3))."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    st = os.lstat(path)
    if _platform.is_link(st) or not stat.S_ISREG(st.st_mode):
        raise PublicationError("build_changed", "a build file is a link or not a file", details={"path": os.fspath(path)})
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


# --- the binding (design 4) ----------------------------------------------------------------------------

def binding(project, publication_id):
    """The build's completion record in this project: its event, the build folder and the order."""
    journal = relations.Journal(writer._journal_bytes(project.journal))
    state, reason, candidates, event = relations.publication_record(journal, publication_id)
    if state != "resolved":
        raise PublicationError("not_bound", "this project has no completed build of that publication_id",
                               details={"publication_id": publication_id, "binding": state, "reason": reason,
                                        "candidates": candidates})
    return event, project.root / event["details"]["location"], journal


def _reference(publication_id, digest, *, observed=True):
    ref = {"type": "publication", "publication_id": publication_id, "relation": "about",
           "basis": "observed" if observed else "declared", "version_binding": "author"}
    if digest:
        ref["digest"] = {"sha256": digest}
    return ref


def _verify(folder: Path, publication_id, details):
    """A version (or staging) against the build's binding: its whole manifest and its ID (design 4)."""
    verified = checks.verify_package(folder, binding=details)
    if verified["manifest"]["publication_id"] != publication_id:
        raise PublicationError("version_differs", "the folder holds another publication",
                               details={"path": os.fspath(folder)})
    return verified


# --- the channel page (design 7; PR-3 adds the arrival check) -------------------------------------------

def channel_page(latest: dict) -> bytes:
    history = sorted(latest["history"], key=lambda h: h.get("latest_since") or "", reverse=True)
    newest_build = max((h for h in latest["history"] if isinstance(h.get("build_seq"), int)),
                       key=lambda h: h["build_seq"], default=None)
    rows = []
    for entry in history:
        pid = entry["publication_id"]
        current = " (latest)" if pid == latest["latest"] else ""
        rows.append(f'<li><a href="../../publications/{html.escape(pid)}/index.html">{html.escape(pid)}</a>{current}'
                    f' · built {html.escape(entry.get("built_at") or "unknown")}'
                    f' · latest since {html.escape(entry.get("latest_since") or "unknown")}</li>')
    previous = next((h for h in history if h["publication_id"] != latest["latest"]), None)
    older = newest_build is not None and newest_build["publication_id"] != latest["latest"]
    title = latest.get("project_title") or "Untitled project"
    parts = [
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:">',
        '<link rel="icon" href="data:,">',
        f'<meta name="analytics-latest" content="{html.escape(latest["latest"])}">',
        f'<title lang="">{html.escape(title)}</title>', "</head>", "<body>",
        f'<h1 lang="">{html.escape(title)}</h1>',
        f'<p>Latest version: <a href="../../publications/{html.escape(latest["latest"])}/index.html">'
        f'{html.escape(latest["latest"])}</a></p>',
    ]
    if previous:
        parts.append(f'<p>Previous version: <a href="../../publications/{html.escape(previous["publication_id"])}/index.html">'
                     f'{html.escape(previous["publication_id"])}</a></p>')
    if older:
        parts.append("<p>This channel points to an older build, chosen by switching.</p>")
    parts += [f"<p>{ACCESS_SENTENCE}</p>", "<h2>Versions</h2>", "<ul>", *rows, "</ul>", "</body>", "</html>", ""]
    return "\n".join(parts).encode("utf-8")


def _page_points_to(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeError):
        return None, "unknown"
    marker = '<meta name="analytics-latest" content="'
    at = text.find(marker)
    if at < 0:
        return None, "unknown"
    return html.unescape(text[at + len(marker): text.find('"', at + len(marker))]), "read"


def _pointer_state(value, read, initial, mine):
    if read in ("missing", "unknown"):
        return read
    if value == mine:
        return "new"
    if value == initial:
        return "old"
    return "other"


def _latest_of(path: Path):
    try:
        return target.read_channel(path), "read"
    except PublicationError as exc:
        if exc.code == "target_unsupported" and isinstance(exc.__cause__, FileNotFoundError):
            return None, "missing"
        if not os.path.lexists(path):
            return None, "missing"
        return None, "unknown"


# --- the attempt ---------------------------------------------------------------------------------------

def publish(project, publication_id, folder, *, origin, channel=None, reassign=False, target_name=None):
    """Publish a bound build to a folder (design 4). Returns the result the CLI prints; a refusal
    before the start record (the folder's judgment, no binding) raises and records nothing."""
    w = core.writer_for(project, origin)
    w.admit(OPERATION)
    view = target.judge(folder, project.root)
    event, build_folder, journal = binding(project, publication_id)
    details = event["details"]
    marker = core.read_json(project.marker, {})
    project_id = marker.get("project_id") if isinstance(marker, dict) else None
    title = marker.get("title") if isinstance(marker, dict) else None
    attempt_id = target.new_attempt_id()
    started_at = _now()
    record_target = {"name": target_name, "path": os.fspath(view.given), "resolved": os.fspath(view.resolved)}
    ref = _reference(publication_id, details["package_sha256"])
    with w.locked(OPERATION) as held:
        held.append("publish_attempt", f"Publishing {publication_id} to {target_name or view.resolved.name}",
                    refs=[ref], details={"attempt_id": attempt_id, "publication_id": publication_id,
                                         "action": "publish", "target": record_target, "channel": channel,
                                         "started_at": started_at},
                    requires=relations.PUBLISH_WRITER_SINCE)
    state = {
        "attempt_id": attempt_id, "publication_id": publication_id, "action": "publish", "outcome": "failed",
        "binding": "project_record", "package_sha256": None, "target": {**record_target, "marker_created": False},
        "started_at": started_at, "steps": dict.fromkeys(relations.PUBLISH_STEPS, "not_started"),
        "lock": {"state": "not_started"}, "reports": None,
        "observations": {"metadata": [], "kept": [], "removed": [], "unrecorded_attempts": [], "remote_links": None},
    }
    root = view.resolved
    staging = root / target.STAGING / attempt_id
    owner = root / target.STAGING / f"{attempt_id}.json"
    try:
        with contextlib.ExitStack() as stack:
            _attempt(project, w, stack, state, view, build_folder, details, event, journal,
                     channel=channel, reassign=reassign, project_id=project_id, title=title,
                     staging=staging, owner=owner)
    except _Stop as stop:
        state["outcome"] = stop.outcome
        state["failure"] = {"step": stop.step, "code": stop.code, "message": stop.message[:2048],
                            "paths": [os.fspath(p) for p in stop.details.get("paths", ())][:20]}
        if stop.details.get("reason"):
            state["failure"]["reason"] = stop.details["reason"]
    except KeyboardInterrupt:
        state["outcome"] = "interrupted"
        state["failure"] = {"step": state.get("_at", "init"), "code": "interrupted",
                            "message": "the publish was interrupted", "paths": []}
        _observe_pointers(state)
    except Exception as exc:  # noqa: BLE001 - every end of an attempt is in its receipt
        for name, value in state["steps"].items():
            if value == "interrupted":
                state["steps"][name] = "failed"
        state["outcome"] = "failed"
        state["failure"] = {"step": state.get("_at", "init"), "code": getattr(exc, "code", type(exc).__name__),
                            "message": str(getattr(exc, "message", exc))[:2048], "paths": []}
        _observe_pointers(state)
    finally:
        _clean_own(state, staging, owner)
    if any(v == "unknown" for v in state["steps"].values()):
        state["outcome"] = "unknown"  # an unknown step comes first (design 6.2)
        if not state.get("failure"):
            state["failure"] = {"step": next(k for k, v in state["steps"].items() if v == "unknown"),
                                "code": "unknown", "message": "a step's result could not be read back", "paths": []}
    return _receipt(w, state)


def _observe_pointers(state):
    """After an interruption or failure inside the switch: what each pointer holds now (design 5)."""
    if state["steps"]["switch"] not in ("interrupted", "failed", "unknown") or "_channel_dir" not in state:
        return
    folder, initial, pid = state["_channel_dir"], state.get("_initial"), state["publication_id"]
    value, read = _latest_of(folder / target.LATEST)
    state["pointers"] = {"latest_json": _pointer_state((value or {}).get("latest"), read, initial, pid),
                         "channel_page": _pointer_state(*_page_points_to(folder / target.CHANNEL_PAGE), initial, pid)}



def _attempt(project, w, stack, state, view, build_folder, details, event, journal, *, channel, reassign,
             project_id, title, staging, owner):
    platform = project.platform
    root = view.resolved
    _boundary(state, "init")
    try:
        root.mkdir(exist_ok=True)
    except OSError as exc:
        raise _Stop("init", "target_refused", f"the target folder cannot be made: {exc}", paths=[root]) from exc
    lock = target.managed_child(root / target.LOCK)
    _boundary(state, "lock")
    try:
        stack.enter_context(platform.locks.exclusive_within(lock, LOCK_WAIT_SECONDS))
        state["lock"] = {"state": "held"}
    except _platform.LockBusy as exc:
        state["lock"] = {"state": "busy"}
        raise _Stop("lock", "target_busy", "another publish holds this target's lock; run it again") from exc
    except _platform.LockUnavailable as exc:
        if exc.errno in NO_LOCK_ERRNOS:
            state["lock"] = {"state": "unsupported", "error": str(exc)}
        else:
            state["lock"] = {"state": "failed", "error": str(exc)}
            raise _Stop("lock", "lock_failed", f"the target's lock cannot be taken: {exc}") from exc
    try:
        view = target.judge(root, project.root)
    except PublicationError as exc:
        raise _Stop("init", exc.code, exc.message, reason=exc.details.get("reason")) from exc
    if view.marker is None:
        try:
            method = target.create_marker(root, platform.files, created_at=_now())
        except PublicationError as exc:
            raise _Stop("init", exc.code, exc.message) from exc
        state["target"]["marker_created"] = method != "exists"
    versions, projects, staging_root = (root / target.PUBLICATIONS, root / target.PROJECTS, root / target.STAGING)
    for folder in (versions, projects, staging_root):
        target.managed_child(folder)
        folder.mkdir(exist_ok=True)
    if os.stat(versions).st_dev != os.stat(staging_root).st_dev:
        raise _Stop("init", "target_refused", "the target's staging and versions are on different volumes")
    channel_dir, initial = _channel(projects, project_id, title, channel, reassign, state)
    state["observations"]["unrecorded_attempts"] = _traces(journal, w, state, root)
    _transfer_and_commit(project, state, build_folder, details, versions / state["publication_id"], staging, owner)
    _switch(project, state, channel_dir, initial, event, details, project_id, title, reassign)
    state["access"] = _access(root, versions / state["publication_id"])
    state["addresses"] = {"version": (versions / state["publication_id"] / "index.html").as_uri(),
                          "latest": (channel_dir / target.CHANNEL_PAGE).as_uri()}
    _clean_others(project, state, root)


def _channel(projects, project_id, title, channel, reassign, state):
    """Which channel, and whether this project may write it (design 9.1)."""
    keys = target.channels(projects)
    if channel is None:
        own = []
        for names in keys.values():
            for name in names:
                if target.channel_state(projects / name, project_id).state == "own":
                    own.append(name)
        if len(own) > 1:
            raise _Stop("channel", "channel_ambiguous", "this project has more than one channel here; give --channel",
                        paths=[projects / n for n in own])
        name = own[0] if own else target.channel_name(target.default_channel(title, project_id))
    else:
        try:
            name = target.channel_name(channel)
        except PublicationError as exc:
            raise _Stop("channel", "target_refused", exc.message, reason="channel_name") from exc
    found = keys.get(target.channel_key(name), [])
    if len(found) > 1:
        raise _Stop("channel", "channel_ambiguous", "two channel folders differ only by case or normalization",
                    paths=[projects / n for n in found])
    folder = projects / (found[0] if found else name)
    current = target.channel_state(folder, project_id)
    state["channel"] = {"name": folder.name, "reassigned_from": None, "previous_latest": None}
    if current.state == "foreign":
        raise _Stop("channel", "channel_owned", "the channel folder holds files the publisher did not write",
                    paths=[folder / n for n in current.foreign])
    if current.state == "other":
        if not reassign:
            raise _Stop("channel", "channel_owned", "another project owns this channel; give --reassign-channel to take it",
                        owner=current.latest["project_id"])
        state["channel"]["reassigned_from"] = current.latest["project_id"]
    if current.latest is not None:
        state["channel"]["previous_latest"] = current.latest["latest"]
    state["_channel_dir"], state["_initial"] = folder, (current.latest or {}).get("latest")
    return folder, state["_initial"]


def _traces(journal, w, state, root):
    """Earlier attempts of this project on this target with no receipt, not yet reported (6.3)."""
    reported = set()
    now = relations.Journal(writer._journal_bytes(w.journal))
    attempts = relations.publish_attempts(now)
    for attempt in attempts:
        if attempt["receipt"]:
            for _n, value in now._publish_attempts[attempt["attempt_id"]]["publish_receipt"]:
                for trace in ((value.get("details") or {}).get("observations") or {}).get("unrecorded_attempts") or ():
                    if isinstance(trace, dict):
                        reported.add(trace.get("attempt_id"))
    out = []
    for attempt in attempts:
        if (attempt["recording_status"] != "incomplete" or attempt["attempt_id"] in reported
                or attempt["attempt_id"] == state["attempt_id"]
                or (attempt["target"] or {}).get("resolved") != state["target"]["resolved"]):
            continue
        aid, pid = attempt["attempt_id"], attempt["publication_id"]
        latest, read = _latest_of(root / target.PROJECTS / (state.get("channel") or {}).get("name", "") / target.LATEST)
        out.append({"attempt_id": aid, "publication_id": pid,
                    "owner_record": os.path.lexists(root / target.STAGING / f"{aid}.json"),
                    "staging": os.path.lexists(root / target.STAGING / aid),
                    "version": os.path.lexists(root / target.PUBLICATIONS / pid) if isinstance(pid, str) else False,
                    "latest_is_its_version": bool(latest and latest.get("latest") == pid)})
    return out


def _owner(state, platform, started, project_id):
    return {"schema": target.STAGING_SCHEMA, "attempt_id": state["attempt_id"], "project_id": project_id,
            "publication_id": state["publication_id"], "machine": target.digest(platform.processes.machine_identity()),
            "host": target.digest(socket.gethostname()), "user": _user(), "pid": os.getpid(),
            "started_at": started, "updated_at": _now()}


def _transfer_and_commit(project, state, build_folder, details, version, staging, owner):
    pid = state["publication_id"]
    files = project.platform.files
    steps = state["steps"]
    if os.path.lexists(version):
        target.managed_child(version)
        try:
            verified = _verify(version, pid, details)
        except PublicationError as exc:
            steps["transfer"] = "failed"
            raise _Stop("transfer", "version_differs", "the target already holds a different version of this ID",
                        paths=[version], cause=exc.message) from exc
        except OSError as exc:
            steps["transfer"] = "unknown"
            raise _Stop("transfer", "unknown", f"the version there cannot be read: {exc}", paths=[version]) from exc
        steps["transfer"], steps["final_check"] = "reused", "done"
        _observed(state, verified)
        return
    _boundary(state, "transfer")
    steps["transfer"] = "interrupted"
    marker = core.read_json(project.marker, {})
    record = _owner(state, project.platform, state["started_at"],
                    marker.get("project_id") if isinstance(marker, dict) else None)
    _write_json(owner, record, files, state["attempt_id"])
    manifest_bytes = _read_regular(build_folder / "publication.json")
    if hashlib.sha256(manifest_bytes).hexdigest() != details["package_sha256"]:
        steps["transfer"] = "failed"
        raise _Stop("transfer", "build_changed", "the build's manifest is not the bound one", paths=[build_folder])
    listed = json.loads(manifest_bytes.decode("utf-8"))["files"]
    names = [entry["path"] for entry in listed]
    checks.check_names(names)
    staging.mkdir()
    refreshed = time.monotonic()
    for name in names:
        source = build_folder / name
        destination = staging / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = _read_regular(source)
        except PublicationError:
            steps["transfer"] = "failed"
            raise
        with open(destination, "xb") as fh:
            fh.write(data)
        if time.monotonic() - refreshed > REFRESH_SECONDS:
            record["updated_at"] = _now()
            _write_json(owner, record, files, state["attempt_id"])
            refreshed = time.monotonic()
    with open(staging / "publication.json", "xb") as fh:
        fh.write(manifest_bytes)
    steps["transfer"] = "done"
    _boundary(state, "check")
    steps["check"] = "interrupted"
    try:
        _verify(staging, pid, details)
    except PublicationError as exc:
        steps["check"] = "failed"
        raise _Stop("check", "version_mismatch", exc.message, paths=[staging]) from exc
    steps["check"] = "done"
    _boundary(state, "commit")
    steps["commit"] = "interrupted"
    try:
        state["commit_method"] = files.rename_dir_new(staging, version)
        steps["commit"] = "done"
    except FileExistsError:
        try:
            _verify(version, pid, details)
        except PublicationError as exc:
            steps["commit"] = "failed"
            raise _Stop("commit", "version_differs", "another attempt put a different version under this ID",
                        paths=[version]) from exc
        steps["commit"], state["commit_method"] = "exists", "exists"
    except OSError as exc:
        # The rename may have happened anyway (NFS): judged by what is there now (design 4).
        if not os.path.lexists(staging) and os.path.lexists(version):
            steps["commit"], state["commit_method"] = "done", "observed_after_error"
        else:
            steps["commit"] = "failed"
            raise _Stop("commit", "commit_failed", f"the version cannot be committed: {exc}", paths=[version]) from exc
    _boundary(state, "final_check")
    steps["final_check"] = "interrupted"
    try:
        verified = _verify(version, pid, details)
    except PublicationError as exc:
        steps["final_check"] = "failed"
        raise _Stop("final_check", "version_mismatch", exc.message, paths=[version]) from exc
    except OSError as exc:
        steps["final_check"] = "unknown"
        raise _Stop("final_check", "unknown", f"the committed version cannot be read back: {exc}", paths=[version]) from exc
    steps["final_check"] = "done"
    _observed(state, verified)


def _observed(state, verified):
    manifest = verified["manifest"]
    state["package_sha256"] = verified["package_sha256"]
    state["observations"]["metadata"] = sorted(verified.get("metadata") or ())
    state["observations"]["remote_links"] = {"count": len(manifest.get("remote_links") or ()), "source": "manifest"}
    state["reports"] = [{"report_id": r["report_id"], "revision_id": r["revision_id"]}
                        for r in ((manifest.get("scope") or {}).get("reports") or ())]
    state["_manifest"] = manifest


def _switch(project, state, channel_dir, initial, event, details, project_id, title, reassign):
    pid, files, steps = state["publication_id"], project.platform.files, state["steps"]
    latest_path, page_path = channel_dir / target.LATEST, channel_dir / target.CHANNEL_PAGE
    _boundary(state, "switch")
    steps["switch"] = "interrupted"
    state["pointers"] = {"latest_json": "unknown", "channel_page": "unknown"}
    channel_dir.mkdir(exist_ok=True)
    current, read = _latest_of(latest_path)
    if read == "unknown":
        steps["switch"] = "unknown"
        raise _Stop("switch", "unknown", "the channel's latest.json cannot be read", paths=[latest_path])
    now_latest = (current or {}).get("latest")
    if current is not None and now_latest == pid:
        steps["switch"] = "already"
        _page(files, page_path, current, state, pid, initial)
        state["pointers"]["latest_json"] = "new"
        return
    if now_latest != initial:
        steps["switch"] = "superseded"
        state["pointers"] = {"latest_json": "other", "channel_page": _pointer_state(*_page_points_to(page_path), initial, pid)}
        state["outcome"] = "superseded"
        return
    if current is not None and not reassign:
        entry = next((h for h in current["history"] if h.get("publication_id") == now_latest), None)
        newer = (entry is not None and entry.get("project_id") == project_id and isinstance(entry.get("build_seq"), int)
                 and event["seq"] > entry["build_seq"])
        if not newer:
            steps["switch"] = "not_newer"
            state["pointers"] = {"latest_json": "old", "channel_page": _pointer_state(*_page_points_to(page_path), initial, pid)}
            state["outcome"] = "not_newer"
            return
    manifest = state.get("_manifest") or {}
    history = [h for h in (current or {}).get("history", []) if h.get("publication_id") != pid]
    history.append({"publication_id": pid, "package_sha256": details["package_sha256"],
                    "content_sha256": details["content_sha256"], "renderer": manifest.get("renderer"),
                    "build_seq": event["seq"], "built_at": manifest.get("built_at"), "latest_since": _now(),
                    "attempt_id": state["attempt_id"], "project_id": project_id})
    latest = {"schema": target.CHANNEL_SCHEMA, "channel": channel_dir.name, "project_id": project_id,
              "project_title": title, "latest": pid, "history": history}
    try:
        _write_json(latest_path, latest, files, state["attempt_id"])
    except OSError as exc:
        value, read = _latest_of(latest_path)
        state["pointers"]["latest_json"] = _pointer_state((value or {}).get("latest"), read, initial, pid)
        steps["switch"] = "failed" if state["pointers"]["latest_json"] != "unknown" else "unknown"
        raise _Stop("switch", "switch_failed", f"latest.json cannot be replaced: {exc}", paths=[latest_path]) from exc
    value, read = _latest_of(latest_path)
    state["pointers"]["latest_json"] = _pointer_state((value or {}).get("latest"), read, initial, pid)
    _boundary(state, "channel_page")
    _page(files, page_path, value or latest, state, pid, initial)
    steps["switch"] = "done" if state["pointers"] == {"latest_json": "new", "channel_page": "new"} else "unknown"
    state["outcome"] = "published"


def _page(files, page_path, latest, state, pid, initial):
    temporary = page_path.with_name(f".{state['attempt_id']}.{target.CHANNEL_PAGE}.tmp")
    try:
        with open(temporary, "wb") as fh:
            fh.write(channel_page(latest))
        files.replace(temporary, page_path)
    except OSError:
        pass
    state["pointers"]["channel_page"] = _pointer_state(*_page_points_to(page_path), initial, pid)
    if state["pointers"]["channel_page"] == "new" and state["steps"]["switch"] == "already":
        state["outcome"] = "published"


def _access(root: Path, version: Path):
    """What the publishing account observed of who else may read (design 9.2); nothing is changed."""
    if os.name == "nt":
        return {"reread": "done", "posix": None, "windows_acl": "not_checked"}
    blocked = []
    for folder in (*reversed(root.parents), root, version.parent, version):
        try:
            if not os.stat(folder).st_mode & stat.S_IXOTH:
                blocked.append(os.fspath(folder))
        except OSError:
            blocked.append(os.fspath(folder))
    for path in version.rglob("*"):
        try:
            mode = os.stat(path).st_mode
        except OSError:
            continue
        if not mode & stat.S_IROTH or (stat.S_ISDIR(mode) and not mode & stat.S_IXOTH):
            blocked.append(os.fspath(path))
    return {"reread": "done", "posix": {"others_can_read": not blocked, "blocked": blocked[:20],
                                        "blocked_count": len(blocked)}, "windows_acl": "not_checked"}


def _clean_own(state, staging, owner):
    for path in (staging, owner):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
                state["observations"]["removed"].append(os.fspath(path))
            elif os.path.lexists(path):
                os.unlink(path)
                state["observations"]["removed"].append(os.fspath(path))
        except OSError as exc:
            state["observations"]["kept"].append({"path": os.fspath(path), "reason": str(exc)})


def _clean_others(project, state, root):
    """Other attempts' leftovers, only when their process is known to have ended here or 24 hours
    have passed without an update (design 4; ADR-0009 decision 5)."""
    platform = project.platform
    me = {"machine": target.digest(platform.processes.machine_identity()), "host": target.digest(socket.gethostname()),
          "user": _user()}
    now = datetime.now().astimezone()
    staging_root = root / target.STAGING
    for name in sorted(os.listdir(staging_root)):
        path = staging_root / name
        aid = name.removesuffix(".json")
        if not target.ATTEMPT_ID.fullmatch(aid) or aid == state["attempt_id"]:
            if not target.ATTEMPT_ID.fullmatch(aid) and not target.system_entry(path):
                state["observations"]["kept"].append({"path": os.fspath(path), "reason": "not a publisher name"})
            continue
        record_path = staging_root / f"{aid}.json"
        removable = False
        try:
            record = target.read_staging(record_path)
            same = all(record.get(k) is not None and record.get(k) == me[k] for k in me)
            updated = _instant(record.get("updated_at"))
            removable = (same and not platform.processes.alive(record.get("pid"))) or (
                updated is not None and now - updated > STALE)
        except PublicationError:
            try:
                removable = now - datetime.fromtimestamp(os.lstat(path).st_mtime).astimezone() > STALE
            except OSError:
                removable = False
        if not removable:
            continue
        try:
            if path.is_dir() and not _platform.is_link(os.lstat(path)):
                shutil.rmtree(path)
            else:
                os.unlink(path)
            state["observations"]["removed"].append(os.fspath(path))
        except OSError as exc:
            state["observations"]["kept"].append({"path": os.fspath(path), "reason": str(exc)})


# --- the receipt (design 6.2, 6.3) --------------------------------------------------------------------

def _bounded(details: dict) -> dict:
    """Fit the receipt to a journal line: lists shrink to 20, 5 and then 0 entries with their counts,
    then the failure message to 2,048 characters; the result never shrinks (design 6.2)."""
    lists = [("failure", "paths"), ("observations", "metadata"), ("observations", "kept"),
             ("observations", "removed"), ("observations", "unrecorded_attempts"), (None, "reports")]

    def size(value):
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    for limit in (None, 20, 5, 0):
        if limit is not None:
            for outer, key in lists:
                holder = details.get(outer) if outer else details
                if isinstance(holder, dict) and isinstance(holder.get(key), list) and len(holder[key]) > limit:
                    holder[f"{key}_count"] = len(holder[key])
                    holder[key] = holder[key][:limit]
                    holder["truncated"] = True
        if size(details) < writer.LINE_LIMIT - 4096:
            return details
    if isinstance(details.get("failure"), dict):
        details["failure"]["message"] = details["failure"].get("message", "")[:2048]
    return details


def _receipt(w, state):
    _reached("receipt")  # a forced end here leaves the start record alone (design 6.3)
    state["ended_at"] = _now()
    details = {k: v for k, v in state.items() if not k.startswith("_")}
    details = _bounded(details)
    outcome = details["outcome"]
    summary = (f"Published {details['publication_id']}" if outcome == "published" else
               f"Publish of {details['publication_id']} {outcome}" + (f" at {details['failure']['step']}: "
                                                                       f"{details['failure']['code']}"
                                                                       if details.get("failure") else ""))
    ref = _reference(details["publication_id"], details.get("package_sha256"))
    result = {"ok": outcome in ("published", "not_newer", "superseded"), **{k: details.get(k) for k in (
        "attempt_id", "publication_id", "outcome", "steps", "pointers", "addresses", "channel", "failure",
        "access", "lock")}}
    try:
        with w.locked(OPERATION) as held:
            receipt = held.append("publish_receipt", summary, refs=[ref], details=details,
                                  requires=relations.PUBLISH_WRITER_SINCE)
        result["receipt"] = {"id": receipt["id"], "seq": receipt["seq"]}
    except BaseException as exc:
        if isinstance(exc, (SystemExit, GeneratorExit)):
            raise
        result["ok"] = False
        result["error"] = {"code": "receipt_not_recorded", "message": f"the publish receipt was not written: {exc}",
                           "details": {"attempt_id": details["attempt_id"]}}
    if result.get("addresses"):
        result["access_note"] = ACCESS_SENTENCE.replace("this folder", details["target"]["resolved"])
    return result


def attempts(project) -> dict:
    """This project's publish attempts and their results (`analytics publish --list`)."""
    journal = relations.Journal(writer._journal_bytes(project.journal))
    rows = relations.publish_attempts(journal)
    for row in rows:
        row.pop("line", None)
    return {"ok": True, "attempts": rows}
