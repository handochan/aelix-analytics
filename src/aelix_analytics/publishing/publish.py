"""Publishing a bound build to a folder (Issue #21, docs/design/PUBLISH-FOLDER.md 4, 6 and 8).

The steps: judge (read only) → binding → start record → target folder → lock → judge again → marker →
managed folders → earlier traces → transfer → check → commit → final check → switch → cleanup →
receipt. Only a version checked where it lies reaches a pointer; nothing here rereads the project's
analysis or changes a version with content. The receipt is written last (ADR-0006's commit rule).
A switch (`--switch-to`, design 8) is the same attempt without the transfer: the version already on
the target is checked where it lies, then only the pointers move."""

from __future__ import annotations

import base64
import contextlib
import errno
import getpass
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import socket
import stat
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

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
    try:
        with open(temporary, "wb") as fh:
            fh.write(target.canonical(value))
        files.replace(temporary, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


TEMPORARY = re.compile(r"\.(att-[0-9a-hjkmnp-tv-z]{26})\..+\.tmp")


def _read_regular(build_folder: Path, name: str) -> bytes:
    with _open_regular(build_folder, name) as fh:
        return fh.read()


def _open_regular(build_folder: Path, name: str):
    """A build file opened for reading, never through a link at any level under the build folder (design 4,
    transfer (3)). Where the platform opens relative to a directory, each folder is opened from the
    one before without following a link; elsewhere (Windows) each level is looked at first."""
    def refused():
        return PublicationError("build_changed", "a build file is a link or not a file",
                                details={"path": os.fspath(build_folder / name)})

    parts = name.split("/")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    st = os.lstat(build_folder)
    if _platform.is_link(st) or not stat.S_ISDIR(st.st_mode):
        raise refused()
    leaf_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | nofollow | getattr(os, "O_NONBLOCK", 0)
    if nofollow and hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd:
        folder_flags = os.O_RDONLY | os.O_DIRECTORY | nofollow
        fd = os.open(build_folder, folder_flags)
        try:
            for index, part in enumerate(parts):
                try:
                    opened = os.open(part, folder_flags if index < len(parts) - 1 else leaf_flags, dir_fd=fd)
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR, getattr(errno, "EMLINK", errno.ELOOP)):
                        raise refused() from exc
                    raise
                os.close(fd)
                fd = opened
            leaf, fd = fd, None
        finally:
            if fd is not None:
                os.close(fd)
    else:
        at = build_folder
        for part in parts[:-1]:
            at = at / part
            st = os.lstat(at)
            if _platform.is_link(st) or not stat.S_ISDIR(st.st_mode):
                raise refused()
        st = os.lstat(at / parts[-1])
        if _platform.is_link(st) or not stat.S_ISREG(st.st_mode):
            raise refused()
        leaf = os.open(at / parts[-1], leaf_flags)
    fh = os.fdopen(leaf, "rb")
    if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
        fh.close()
        raise refused()
    return fh


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


# --- the channel page (design 7) -------------------------------------------------------------------

# Before it moves to the newest version, the page loads what that version's entry loads itself and a
# browser reports without running it: stylesheets (media "not all", so nothing applies here) and
# images. Measured for #21 PR-3 in Chromium, Firefox and WebKit over file:// and an HTTP subpath: each
# gives "load" for a file that is there and "error" for one that is not, and 'self' admits the
# version's files over file:// too. One that answers neither in ARRIVAL_SECONDS counts as not arrived.
ARRIVAL_SECONDS = 15
ARRIVAL_SCRIPT = (
    '(function(){var box=document.getElementById("arrival");if(!box)return;var data=null;'
    'try{data=JSON.parse(box.getAttribute("data-check"));}catch(e){}'
    'var left=data&&data.check?data.check.length:0,ended=false,timer=null;'
    'function stop(){if(ended)return;ended=true;clearTimeout(timer);'
    'document.getElementById("arrival-checking").hidden=true;document.getElementById("arrival-failed").hidden=false;}'
    'if(!left){stop();return;}timer=setTimeout(stop,data.seconds*1000);'
    'data.check.forEach(function(c){var el=c.kind==="image"?new Image():document.createElement("link");'
    'el.onload=function(){if(!ended&&--left===0){ended=true;clearTimeout(timer);location.replace(data.entry);}};'
    'el.onerror=stop;if(c.kind==="image"){el.src=c.href;}'
    'else{el.rel="stylesheet";el.media="not all";el.href=c.href;document.head.appendChild(el);}});})();'
)
PAGE_POLICY = (
    "default-src 'none'; script-src 'sha256-"
    + base64.b64encode(hashlib.sha256(ARRIVAL_SCRIPT.encode()).digest()).decode()
    + "'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'none'"
)
NOT_ARRIVED = "The newest version may not have fully arrived on this device."


class _Loads(HTMLParser):
    """The stylesheets and images a page loads itself, in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link" and "stylesheet" in (values.get("rel") or "").lower().split() and values.get("href"):
            self.found.append(("stylesheet", values["href"]))
        elif tag == "img" and values.get("src"):
            self.found.append(("image", values["src"]))


def arrival_resources(version: Path, manifest: dict) -> list[dict]:
    """What the channel page loads before it moves to this checked version (design 7): the entry's
    own stylesheets and images that are files of the version, as links from the channel page. An
    entry that cannot be read gives none, and the page then does not move."""
    try:
        text = _read_regular(version, manifest.get("entry") or "index.html").decode("utf-8")
        loads = _Loads()
        loads.feed(text)
        loads.close()
    except (OSError, UnicodeError, PublicationError, AssertionError):
        return []
    listed = {f.get("path") for f in manifest.get("files") or () if isinstance(f, dict)}
    out, seen = [], set()
    for kind, url in loads.found:
        parts = urlsplit(url)
        if parts.scheme or parts.netloc or parts.query or url.startswith("/"):
            continue
        name = posixpath.normpath(unquote(parts.path))
        if name in seen or name not in listed:
            continue
        seen.add(name)
        out.append({"kind": kind, "href": f"../../{target.PUBLICATIONS}/{version.name}/{parts.path}"})
    return out  # every one: a version is opened only once all it loads itself has come


def _same_content(a: dict, b: dict | None) -> bool:
    """Two versions of the same content (ADR-0014 decision 1): one renderer, one content_sha256."""
    return (b is not None and a.get("renderer") is not None and a.get("renderer") == b.get("renderer")
            and isinstance(a.get("content_sha256"), str) and a.get("content_sha256") == b.get("content_sha256"))


def channel_page(latest: dict, arrival: list[dict]) -> bytes:
    """The channel page (design 7), drawn from latest.json and what its checked newest version's entry
    loads. Its one script is the kit's (by hash); it never runs the version's scripts."""
    pid = latest["latest"]
    if not relations.PUBLICATION_ID.fullmatch(pid):
        raise ValueError("a channel page points only to a publication_id")
    entries = [h for h in latest["history"] if isinstance(h.get("publication_id"), str)
               and relations.PUBLICATION_ID.fullmatch(h["publication_id"])]
    history = sorted(entries, key=lambda h: str(h.get("latest_since") or ""), reverse=True)
    pointed = next((h for h in entries if h["publication_id"] == pid), {})
    newest_build = max((h for h in entries if isinstance(h.get("build_seq"), int)
                        and h.get("project_id") == pointed.get("project_id")),
                       key=lambda h: h["build_seq"], default=None)

    def href(publication_id):
        return f"../../{target.PUBLICATIONS}/{publication_id}/index.html"

    rows = []
    for at, entry in enumerate(history):
        other = entry["publication_id"]
        rows.append(f'<li><a href="{href(other)}">{html.escape(other)}</a>{" (latest)" if other == pid else ""}'
                    f' · built {html.escape(str(entry.get("built_at") or "unknown"))}'
                    f' · latest since {html.escape(str(entry.get("latest_since") or "unknown"))}'
                    + (" · same content as the version below" if _same_content(entry, (history[at + 1:] or [None])[0])
                       else "") + "</li>")
    previous = next((h for h in history if h["publication_id"] != pid), None)
    older = newest_build is not None and newest_build["publication_id"] != pid
    title = latest.get("project_title") or "Untitled project"
    check = {"entry": href(pid), "seconds": ARRIVAL_SECONDS, "check": arrival}
    parts = [
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta http-equiv="Content-Security-Policy" content="{html.escape(PAGE_POLICY)}">',
        '<meta name="referrer" content="no-referrer">', '<meta name="robots" content="noindex">',
        '<link rel="icon" href="data:,">',
        f'<meta name="analytics-latest" content="{html.escape(pid)}">',
        f'<noscript><meta http-equiv="refresh" content="0; url={href(pid)}"></noscript>',
        f'<title lang="">{html.escape(title)}</title>', "</head>", "<body>",
        f'<h1 lang="">{html.escape(title)}</h1>',
        f'<div id="arrival" data-check="{html.escape(json.dumps(check, sort_keys=True))}">',
        '<p id="arrival-checking">Opening the newest version once its files are on this device…</p>',
        f'<p id="arrival-failed" hidden>{NOT_ARRIVED} Open it anyway'
        + (", or open the previous version.</p>" if previous else ", or come back later.</p>"),
        "</div>",
        f'<p>Latest version: <a href="{href(pid)}">{html.escape(pid)}</a></p>',
    ]
    if previous:
        parts.append(f'<p>Previous version: <a href="{href(previous["publication_id"])}">'
                     f'{html.escape(previous["publication_id"])}</a></p>')
    if older:
        parts.append("<p>This channel points to an older build, chosen by switching.</p>")
    parts += [f"<p>{ACCESS_SENTENCE}</p>", "<h2>Versions</h2>", "<ul>", *rows, "</ul>",
              f"<script>{ARRIVAL_SCRIPT}</script>", "</body>", "</html>", ""]
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
    return _run(project, publication_id, folder, origin=origin, channel=channel, reassign=reassign,
                target_name=target_name, action="publish")


def switch(project, publication_id, folder, *, origin, channel=None, target_name=None):
    """Point a channel at a version already on the target (`--switch-to`, design 8): no transfer; the
    version is checked where it lies, by this project's binding or else the channel history's hash,
    and the build order is not compared. A refusal before the start record records nothing."""
    if not isinstance(publication_id, str) or not relations.PUBLICATION_ID.fullmatch(publication_id):
        raise PublicationError("publish_input_invalid", "--switch-to takes a publication_id (pub- and 26 letters)",
                               details={"publication_id": publication_id})
    return _run(project, publication_id, folder, origin=origin, channel=channel, reassign=False,
                target_name=target_name, action="switch")


def _run(project, publication_id, folder, *, origin, channel, reassign, target_name, action):
    w = core.writer_for(project, origin)
    w.admit(OPERATION)
    if channel is not None:
        target.channel_name(channel)
    view = target.judge(folder, project.root)
    marker = core.read_json(project.marker, {})
    project_id = marker.get("project_id") if isinstance(marker, dict) else None
    title = marker.get("title") if isinstance(marker, dict) else None
    if action == "publish":
        event, build_folder, journal = binding(project, publication_id)
        details, entry = event["details"], None
    else:
        event, entry, journal = _switch_source(project, view, publication_id, channel, project_id, title)
        details, build_folder = (event["details"] if event else None), None
    digest = details["package_sha256"] if details else entry["package_sha256"]
    attempt_id = target.new_attempt_id()
    started_at = _now()
    record_target = {"name": target_name, "path": os.fspath(view.given), "resolved": os.fspath(view.resolved)}
    ref = _reference(publication_id, digest, observed=details is not None)  # the receipt's refs too (6.2)
    with w.locked(OPERATION) as held:
        shown = target_name or view.resolved.name
        held.append("publish_attempt", f"Publishing {publication_id} to {shown}" if action == "publish"
                    else f"Switching {shown} to {publication_id}",
                    refs=[ref], details={"attempt_id": attempt_id, "publication_id": publication_id,
                                         "action": action, "target": record_target, "channel": channel,
                                         "started_at": started_at},
                    requires=relations.PUBLISH_WRITER_SINCE)
    state = {
        "attempt_id": attempt_id, "publication_id": publication_id, "action": action, "outcome": "failed",
        "binding": "project_record" if details else "channel_history", "package_sha256": None,
        "target": {**record_target, "marker_created": False},
        "started_at": started_at, "steps": dict.fromkeys(relations.PUBLISH_STEPS, "not_started"),
        "lock": {"state": "not_started"}, "reports": None,
        "observations": {"metadata": [], "kept": [], "removed": [], "unrecorded_attempts": [], "remote_links": None},
        "_ref": ref, "_shown": target_name or view.resolved.name, "_entry": entry,
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
                            "paths": [os.fspath(p) for p in stop.details.get("paths", ())]}
        if stop.details.get("reason"):
            state["failure"]["reason"] = stop.details["reason"]
    except KeyboardInterrupt:
        if state["steps"]["switch"] in SWITCH_ENDED:
            _after_switch(state, "interrupted")
        else:
            state["outcome"] = "interrupted"
            state["failure"] = {"step": state.get("_at", "init"), "code": "interrupted",
                                "message": "the publish was interrupted", "paths": []}
            _observe_pointers(state)
    except Exception as exc:  # noqa: BLE001 - every end of an attempt is in its receipt
        if state["steps"]["switch"] in SWITCH_ENDED:
            _after_switch(state, f"{type(exc).__name__}: {exc}")
        else:
            for name, value in state["steps"].items():
                if value == "interrupted":
                    state["steps"][name] = "failed"
            state["outcome"] = "failed"
            state["failure"] = {"step": state.get("_at", "init"), "code": getattr(exc, "code", type(exc).__name__),
                                "message": str(getattr(exc, "message", exc))[:2048], "paths": []}
            found = getattr(exc, "details", None)
            if isinstance(found, dict):
                if found.get("reason"):
                    state["failure"]["reason"] = str(found["reason"])
                if found.get("path"):
                    state["failure"]["paths"] = [os.fspath(found["path"])]
            _observe_pointers(state)
    finally:
        _clean_own(state, staging, owner)
    if any(v == "unknown" for v in state["steps"].values()):
        state["outcome"] = "unknown"  # an unknown step comes first (design 6.2)
        if not state.get("failure"):
            state["failure"] = {"step": next(k for k, v in state["steps"].items() if v == "unknown"),
                                "code": "unknown", "message": "a step's result could not be read back", "paths": []}
    return _receipt(w, state)


SWITCH_OUTCOME = {"done": "published", "already": "published", "not_newer": "not_newer", "superseded": "superseded"}
SWITCH_ENDED = frozenset(SWITCH_OUTCOME)


def _after_switch(state, what):
    """An interruption or error once the switch has ended (the access observation, the cleanup):
    the outcome stays the one the steps gave (design 6.2); what happened is an observation."""
    state["outcome"] = SWITCH_OUTCOME[state["steps"]["switch"]]
    state["observations"]["after_switch"] = {"step": state.get("_at"), "error": what[:512]}
    state.setdefault("addresses", state["_addresses"])
    state.setdefault("access", {"reread": "not_checked", "posix": None, "windows_acl": "not_checked"})


def _page_state(state, page_path):
    """The channel page against what it held when the attempt began and this attempt's version."""
    return _pointer_state(*_page_points_to(page_path), state.get("_initial_page"), state["publication_id"])


def _observe_pointers(state):
    """After an interruption or failure inside the switch: what each pointer holds now (design 5)."""
    if state["steps"]["switch"] not in ("interrupted", "failed", "unknown") or "_channel_dir" not in state:
        return
    folder, initial, pid = state["_channel_dir"], state.get("_initial"), state["publication_id"]
    value, read = _latest_of(folder / target.LATEST)
    state["pointers"] = {"latest_json": _pointer_state((value or {}).get("latest"), read, initial, pid),
                         "channel_page": _page_state(state, folder / target.CHANNEL_PAGE)}



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
    state["_at"] = "init"
    try:
        view = target.judge(root, project.root)
    except PublicationError as exc:
        raise _Stop("init", exc.code, exc.message, reason=exc.details.get("reason")) from exc
    if view.marker is None and state["action"] == "switch":
        raise _Stop("init", "target_refused", "the folder is no publish target, so it holds no version to switch to",
                    reason="no_marker", paths=[root])
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
    state["_at"] = "channel"
    channel_dir, initial = _channel(projects, project_id, title, channel, reassign, state)
    state["_addresses"] = {"version": (versions / state["publication_id"] / "index.html").as_uri(),
                           "latest": (channel_dir / target.CHANNEL_PAGE).as_uri()}
    state["_at"] = "init"
    state["observations"]["unrecorded_attempts"] = _traces(w, state, root, project_id)
    if state["action"] == "publish":
        _transfer_and_commit(project, state, build_folder, details, versions / state["publication_id"], staging, owner)
    else:
        _check_in_place(state, versions / state["publication_id"], details)
    _switch(project, state, channel_dir, initial, event, details, project_id, title)
    state["addresses"] = state["_addresses"]
    state["_at"] = "cleanup"
    state["access"] = _access(root, versions / state["publication_id"])
    _clean_others(project, state, root, channel_dir)


def _channel_folder(projects, project_id, title, channel):
    """Which channel folder (design 9.1): the given name, else this project's own, else the default
    name. Only reads; raises PublicationError (channel_ambiguous, target_refused)."""
    keys = target.channels(projects)
    if channel is None:
        # A channel this kit cannot read may be this project's: it stops the attempt (target_unsupported).
        own = [name for names in keys.values() for name in names
               if target.channel_state(projects / name, project_id).state == "own"]
        if len(own) > 1:
            raise PublicationError("channel_ambiguous", "this project has more than one channel here; give --channel",
                                   details={"paths": [os.fspath(projects / n) for n in own]})
        name = own[0] if own else target.channel_name(target.default_channel(title, project_id))
    else:
        name = target.channel_name(channel)
    found = keys.get(target.channel_key(name), [])
    if len(found) > 1:
        raise PublicationError("channel_ambiguous", "two channel folders differ only by case or normalization",
                               details={"paths": [os.fspath(projects / n) for n in found]})
    return projects / (found[0] if found else name)


def _channel(projects, project_id, title, channel, reassign, state):
    """Which channel, and whether this project may write it (design 9.1)."""
    try:
        folder = _channel_folder(projects, project_id, title, channel)
    except PublicationError as exc:
        found = exc.details.get("paths") or ([exc.details["path"]] if exc.details.get("path") else [])
        raise _Stop("channel", exc.code, exc.message, paths=found, reason=exc.details.get("reason")) from exc
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
    state["_initial_page"] = _page_points_to(folder / target.CHANNEL_PAGE)[0]
    return folder, state["_initial"]


def _switch_source(project, view, publication_id, channel, project_id, title):
    """What a switch may point to (design 8), read before the start record: a version in this
    project's channel history, or one this project bound that the target holds. Only reads; a refusal
    records nothing. Returns the binding (or None), the history entry (or None) and the journal."""
    journal = relations.Journal(writer._journal_bytes(project.journal))
    found, _reason, _candidates, event = relations.publication_record(journal, publication_id)
    event = event if found == "resolved" else None
    entry, committed = None, False
    if view.marker is not None:
        root = view.resolved
        try:
            current = target.channel_state(_channel_folder(root / target.PROJECTS, project_id, title, channel),
                                           project_id)
        except PublicationError:
            if event is None:
                raise
            current = None  # the attempt meets it again after the lock, with a receipt
        if current is not None and current.state == "own":
            entry = next((h for h in current.latest["history"] if h.get("publication_id") == publication_id), None)
        committed = os.path.isdir(root / target.PUBLICATIONS / publication_id)
    hashed = (entry or {}).get("package_sha256")
    if event is None and entry is None:
        raise PublicationError("not_bound", "neither this project's builds nor this channel's history name that publication_id",
                               details={"publication_id": publication_id})
    if event is None and not (isinstance(hashed, str) and re.fullmatch(r"[0-9a-f]{64}", hashed)):
        raise PublicationError("not_bound", "the channel's history holds no hash to check that version by",
                               details={"publication_id": publication_id})
    if entry is None and not committed:
        raise PublicationError("not_on_target", "the target holds no version of that publication_id; publish it with --build",
                               details={"publication_id": publication_id, "target": os.fspath(view.resolved)})
    if event is not None and isinstance(hashed, str) and hashed != event["details"]["package_sha256"]:
        raise PublicationError("version_differs", "this project's build and the channel's history name two versions under that ID",
                               details={"publication_id": publication_id})
    return event, entry, journal


def _own_channels(projects: Path, project_id) -> list[str]:
    """The channels of this target whose latest.json this kit reads as this project's; only reads,
    for an observation (a channel it cannot read is left out)."""
    own = []
    for names in target.channels(projects).values():
        for name in names:
            with contextlib.suppress(PublicationError, OSError):
                if target.channel_state(projects / name, project_id).state == "own":
                    own.append(name)
    return own


def _traces(w, state, root, project_id):
    """Earlier attempts of this project on this target with no receipt, not yet reported (6.3). An
    attempt's pointer is looked for in the channel its start record names, else (it named none, the
    project's own channel then) in each of this project's channels here."""
    reported = set()
    now = relations.Journal(writer._journal_bytes(w.journal))
    attempts = relations.publish_attempts(now)
    for attempt in attempts:
        if attempt["receipt"]:
            for _n, value in relations.publish_rows(now, attempt["attempt_id"], "publish_receipt"):
                observations = value["details"].get("observations")
                traces = observations.get("unrecorded_attempts") if isinstance(observations, dict) else None
                for trace in traces if isinstance(traces, list) else ():
                    if isinstance(trace, dict) and isinstance(trace.get("attempt_id"), str):
                        reported.add(trace["attempt_id"])
    out = []
    for attempt in attempts:
        if (attempt["recording_status"] != "incomplete" or attempt["attempt_id"] in reported
                or attempt["attempt_id"] == state["attempt_id"]
                or not isinstance(attempt["target"], dict)
                or attempt["target"].get("resolved") != state["target"]["resolved"]):
            continue
        aid, pid = attempt["attempt_id"], attempt["publication_id"]
        projects = root / target.PROJECTS
        try:
            # The start record keeps the name as given; the folder is the one its key finds (design 9.1).
            names = (target.channels(projects).get(target.channel_key(target.channel_name(attempt["channel"])), [])
                     if isinstance(attempt.get("channel"), str) else _own_channels(projects, project_id))
        except (PublicationError, OSError):
            names = []  # a hand-edited start record's channel names no folder here
        pointed = [n for n in names if ((_latest_of(projects / n / target.LATEST)[0] or {}).get("latest") == pid)]
        out.append({"attempt_id": aid, "publication_id": pid,
                    "owner_record": os.path.lexists(root / target.STAGING / f"{aid}.json"),
                    "staging": os.path.lexists(root / target.STAGING / aid),
                    "version": os.path.lexists(root / target.PUBLICATIONS / pid) if isinstance(pid, str) else False,
                    "latest_is_its_version": bool(pointed)})
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
    state["_at"] = "transfer"  # looking at a version already there is the transfer's (its reuse)
    if os.path.lexists(version):
        target.managed_child(version)
        try:
            _version_folder(version)
            verified = _verify(version, pid, details)
        except PublicationError as exc:
            steps["transfer"] = "failed"
            raise _Stop("transfer", "version_differs", "the target already holds a different version of this ID",
                        paths=_mismatched(version, exc)) from exc
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
    manifest_bytes = _read_regular(build_folder, "publication.json")
    if hashlib.sha256(manifest_bytes).hexdigest() != details["package_sha256"]:
        steps["transfer"] = "failed"
        raise _Stop("transfer", "build_changed", "the build's manifest is not the bound one", paths=[build_folder])
    listed = json.loads(manifest_bytes.decode("utf-8"))["files"]
    names = [entry["path"] for entry in listed]
    checks.check_names(names)
    staging.mkdir()
    refreshed = time.monotonic()
    for name in names:
        destination = staging / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            source = _open_regular(build_folder, name)
        except PublicationError:
            steps["transfer"] = "failed"
            raise
        with source, open(destination, "xb") as fh:
            shutil.copyfileobj(source, fh)
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
        raise _Stop("check", "version_mismatch", exc.message, paths=_mismatched(staging, exc)) from exc
    except OSError as exc:
        steps["check"] = "unknown"
        raise _Stop("check", "unknown", f"the staging cannot be read back: {exc}", paths=[staging]) from exc
    steps["check"] = "done"
    _boundary(state, "commit")
    steps["commit"] = "interrupted"
    try:
        state["commit_method"] = files.rename_dir_new(staging, version)
        steps["commit"] = "done"
    except FileExistsError:
        try:
            target.managed_child(version)
            _version_folder(version)
            _verify(version, pid, details)
        except PublicationError as exc:
            steps["commit"] = "failed"
            raise _Stop("commit", "version_differs", "another attempt put a different version under this ID",
                        paths=_mismatched(version, exc)) from exc
        except OSError as exc:
            steps["commit"] = "unknown"
            raise _Stop("commit", "unknown", f"the version there cannot be read: {exc}", paths=[version]) from exc
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
        raise _Stop("final_check", "version_mismatch", exc.message, paths=_mismatched(version, exc)) from exc
    except OSError as exc:
        steps["final_check"] = "unknown"
        raise _Stop("final_check", "unknown", f"the committed version cannot be read back: {exc}", paths=[version]) from exc
    steps["final_check"] = "done"
    _observed(state, verified)


def _version_folder(version: Path) -> None:
    """A version is a folder: a file of that name (a ZIP, say) is not one, whatever it holds."""
    if not stat.S_ISDIR(os.lstat(version).st_mode):
        raise PublicationError("version_differs", "the version's name holds something that is not a folder",
                               details={"path": os.fspath(version)})


def _mismatched(folder: Path, exc) -> list[str]:
    """The folder, and the file the check names when it names one (decisions 5 and 6)."""
    named = (exc.details or {}).get("path") if isinstance(getattr(exc, "details", None), dict) else None
    return [os.fspath(folder)] + ([os.fspath(folder / named) if not os.path.isabs(str(named)) else str(named)]
                                  if isinstance(named, str) and named else [])


def _observed(state, verified):
    manifest = verified["manifest"]
    state["package_sha256"] = verified["package_sha256"]
    state["observations"]["metadata"] = sorted(verified.get("metadata") or ())
    state["observations"]["remote_links"] = {"count": len(manifest.get("remote_links") or ()), "source": "manifest"}
    state["reports"] = [{"report_id": r["report_id"], "revision_id": r["revision_id"]}
                        for r in ((manifest.get("scope") or {}).get("reports") or ())]
    state["_manifest"] = manifest


def _check_in_place(state, version, details):
    """A switch's version checked where it lies (design 8): by this project's binding when there is
    one, else by the channel history's package_sha256 and the manifest's ID."""
    pid, steps, entry = state["publication_id"], state["steps"], state["_entry"]
    _boundary(state, "final_check")
    steps["final_check"] = "interrupted"
    target.managed_child(version)
    try:
        _version_folder(version)
        if details is not None:
            verified = _verify(version, pid, details)
        else:
            verified = checks.verify_package(version)
            if verified["manifest"]["publication_id"] != pid or verified["package_sha256"] != entry["package_sha256"]:
                raise PublicationError("version_mismatch", "the version there is not the one the channel's history names",
                                       details={"path": os.fspath(version)})
    except FileNotFoundError as exc:
        steps["final_check"] = "failed"
        raise _Stop("final_check", "not_on_target", "the target holds no version of that publication_id",
                    paths=[version]) from exc
    except PublicationError as exc:
        steps["final_check"] = "failed"
        raise _Stop("final_check", "version_mismatch", exc.message, paths=_mismatched(version, exc)) from exc
    except OSError as exc:
        steps["final_check"] = "unknown"
        raise _Stop("final_check", "unknown", f"the version there cannot be read: {exc}", paths=[version]) from exc
    steps["final_check"] = "done"
    _observed(state, verified)


def _history_entry(state, event, details, project_id):
    """This version's channel history entry (design 2): from the binding, else (a switch with none
    here) the entry the history holds; with when it became latest and by which attempt."""
    if event is None:
        fields = dict(state["_entry"])
    else:
        manifest = state.get("_manifest") or {}
        fields = {"publication_id": state["publication_id"], "package_sha256": details["package_sha256"],
                  "content_sha256": details["content_sha256"], "renderer": manifest.get("renderer"),
                  "build_seq": event["seq"], "built_at": manifest.get("built_at"), "project_id": project_id}
    return {**fields, "latest_since": _now(), "attempt_id": state["attempt_id"]}


def _switch(project, state, channel_dir, initial, event, details, project_id, title):
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
        # The switch stays interrupted until the page agrees (design 8: only the page is drawn).
        state["pointers"]["latest_json"] = "new"
        state["pointers"]["channel_page"] = _page_state(state, page_path)
        if state["pointers"]["channel_page"] != "new":
            _boundary(state, "channel_page")
            _page(files, page_path, current, state, initial)
        steps["switch"] = "already" if state["pointers"]["channel_page"] == "new" else "unknown"
        state["outcome"] = "published"
        return
    if now_latest != initial:
        steps["switch"] = "superseded"
        state["pointers"] = {"latest_json": _pointer_state(now_latest, read, initial, pid),
                             "channel_page": _page_state(state, page_path)}
        state["outcome"] = "superseded"
        return
    # Only a publish that takes another project's channel over, and a switch (a person's choice),
    # skip the build order (ADR-0009 decision 5; design 8).
    if current is not None and state["action"] == "publish" and not state["channel"]["reassigned_from"]:
        entry = next((h for h in current["history"] if h.get("publication_id") == now_latest), None)
        newer = (entry is not None and entry.get("project_id") == project_id and isinstance(entry.get("build_seq"), int)
                 and event["seq"] > entry["build_seq"])
        if not newer:
            steps["switch"] = "not_newer"
            state["outcome"] = "not_newer"
            _repair_page(project, state, page_path, current)
            state["pointers"] = {"latest_json": "old", "channel_page": _page_state(state, page_path)}
            return
    history = [h for h in (current or {}).get("history", []) if h.get("publication_id") != pid]
    history.append(_history_entry(state, event, details, project_id))
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
    if state["pointers"]["latest_json"] == "other":
        # Another publish replaced it after this one (no lock here): its version is not drawn here.
        steps["switch"], state["outcome"] = "superseded", "superseded"
        state["pointers"]["channel_page"] = _page_state(state, page_path)
        return
    if state["pointers"]["latest_json"] != "new":
        steps["switch"] = "unknown"
        state["pointers"]["channel_page"] = _page_state(state, page_path)
        raise _Stop("switch", "unknown", "latest.json cannot be read back after its replace", paths=[latest_path])
    _boundary(state, "channel_page")
    _page(files, page_path, latest, state, initial)
    steps["switch"] = "done" if state["pointers"]["channel_page"] == "new" else "unknown"
    state["outcome"] = "published"


def _write_page(files, page_path, latest, attempt_id, manifest):
    """The channel page for latest.json, whose newest version was just checked (`manifest` is its)."""
    version = page_path.parent.parent.parent / target.PUBLICATIONS / latest["latest"]
    temporary = page_path.with_name(f".{attempt_id}.{target.CHANNEL_PAGE}.tmp")
    try:
        with open(temporary, "wb") as fh:
            fh.write(channel_page(latest, arrival_resources(version, manifest)))
        files.replace(temporary, page_path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _page(files, page_path, latest, state, initial):
    """The channel page from a latest.json naming this attempt's checked version; a failed write
    ends the attempt with what the page holds (design 8)."""
    try:
        _write_page(files, page_path, latest, state["attempt_id"], state.get("_manifest") or {})
    except OSError as exc:
        state["pointers"]["channel_page"] = _page_state(state, page_path)
        state["steps"]["switch"] = "unknown" if state["pointers"]["channel_page"] == "unknown" else "failed"
        raise _Stop("switch", "switch_failed", f"the channel page cannot be replaced: {exc}", paths=[page_path]) from exc
    state["pointers"]["channel_page"] = _page_state(state, page_path)


def _repair_page(project, state, page_path, current):
    """Pointers that differ (an earlier attempt stopped between them) are made to agree from
    latest.json by the channel's next publish, only after the version it names is checked here: by
    this project's binding, else by its history entry's package_sha256 (design 8). The result is an
    observation; the attempt's own outcome does not change."""
    other = current["latest"]
    pointed, read = _page_points_to(page_path)
    if read == "read" and pointed == other:
        return
    repair = state["observations"]["channel_page_repair"] = {"publication_id": other, "state": "not_verified",
                                                              "basis": None}
    if not isinstance(other, str) or not relations.PUBLICATION_ID.fullmatch(other):
        return  # a hand-edited latest.json names no version folder
    version = page_path.parent.parent.parent / target.PUBLICATIONS / other
    found, _reason, _candidates, event = relations.publication_record(
        relations.Journal(writer._journal_bytes(project.journal)), other)
    entry = next((h for h in current["history"] if h.get("publication_id") == other), None) or {}
    try:
        target.managed_child(version)
        _version_folder(version)
        if found == "resolved":
            repair["basis"] = "project_record"
            verified = _verify(version, other, event["details"])
            if entry.get("package_sha256") not in (None, event["details"]["package_sha256"]):
                return
        elif isinstance(entry.get("package_sha256"), str):
            repair["basis"] = "channel_history"
            verified = checks.verify_package(version)
            if verified["manifest"]["publication_id"] != other or verified["package_sha256"] != entry["package_sha256"]:
                return
        else:
            return
    except (PublicationError, OSError):
        return
    try:
        _write_page(project.platform.files, page_path, current, state["attempt_id"], verified["manifest"])
    except OSError as exc:
        repair["state"], repair["error"] = "failed", str(exc)[:512]
        return
    repair["state"] = "done"


def _access(root: Path, version: Path):
    """What the publishing account observed of who else may read (design 9.2): the search bit of each
    folder down to the version, and the read bit (and search bit, for a folder) under it, for other
    accounts and for the group. Nothing is changed; Windows ACLs are not read."""
    if os.name == "nt":
        return {"reread": "done", "posix": None, "windows_acl": "not_checked"}
    blocked = {"other": [], "group": []}
    bits = {"other": (stat.S_IROTH, stat.S_IXOTH), "group": (stat.S_IRGRP, stat.S_IXGRP)}

    def look(path, *, inside):
        try:
            mode = os.stat(path).st_mode
        except OSError:
            if inside:
                return
            mode = 0
        for who, (read, search) in bits.items():
            folder = stat.S_ISDIR(mode) or not inside
            if (folder and not mode & search) or (inside and not mode & read):
                blocked[who].append(os.fspath(path))

    for folder in (*reversed(root.parents), root, version.parent, version):
        look(folder, inside=False)
    for path in version.rglob("*"):
        look(path, inside=True)
    return {"reread": "done", "windows_acl": "not_checked",
            "posix": {"others_can_read": not blocked["other"], "blocked": blocked["other"][:20],
                      "blocked_count": len(blocked["other"]), "group_can_read": not blocked["group"],
                      "group_blocked": blocked["group"][:20], "group_blocked_count": len(blocked["group"])}}


def _clean_own(state, staging, owner):
    leftovers = []
    for folder in (staging.parent, state.get("_channel_dir")):
        with contextlib.suppress(OSError, TypeError):
            leftovers += [folder / n for n in os.listdir(folder)
                          if (m := TEMPORARY.fullmatch(n)) and m.group(1) == state["attempt_id"]]
    for path in (staging, owner, *leftovers):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
                state["observations"]["removed"].append(os.fspath(path))
            elif os.path.lexists(path):
                os.unlink(path)
                state["observations"]["removed"].append(os.fspath(path))
        except OSError as exc:
            state["observations"]["kept"].append({"path": os.fspath(path), "reason": str(exc)})


def _clean_others(project, state, root, channel_dir):
    """Other attempts' leftovers, only when their process is known to have ended here or 24 hours
    have passed without an update (design 4; ADR-0009 decision 5): their staging and owner record,
    and the temporary files their names hold (here and in this channel)."""
    platform = project.platform
    me = {"machine": target.digest(platform.processes.machine_identity()), "host": target.digest(socket.gethostname()),
          "user": _user()}
    now = datetime.now().astimezone()
    staging_root = root / target.STAGING
    entries = []
    for folder in (staging_root, channel_dir):
        try:
            entries += [(folder, name) for name in sorted(os.listdir(folder))]
        except OSError as exc:
            state["observations"]["kept"].append({"path": os.fspath(folder), "reason": str(exc)})
    for folder, name in entries:
        path = folder / name
        temporary = TEMPORARY.fullmatch(name)
        aid = temporary.group(1) if temporary else name.removesuffix(".json") if folder == staging_root else None
        if aid is None:
            continue
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
             ("observations", "removed"), ("observations", "unrecorded_attempts"), ("access", "posix", "blocked"),
             ("access", "posix", "group_blocked"), ("reports",)]

    def size(value):
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    for limit in (None, 20, 5, 0):
        if limit is not None:
            for *outer, key in lists:
                holder = details
                for name in outer:
                    holder = holder.get(name) if isinstance(holder, dict) else None
                if isinstance(holder, dict) and isinstance(holder.get(key), list) and len(holder[key]) > limit:
                    holder.setdefault(f"{key}_count", len(holder[key]))
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
    shown = state["_shown"]
    pid, switched = details["publication_id"], details["action"] == "switch"
    summary = ((f"Switched {shown} to {pid}" if switched else f"Published {pid} to {shown}") if outcome == "published" else
               (f"Switch of {shown} to {pid} {outcome}" if switched else f"Publish of {pid} to {shown} {outcome}") + (
                   f" at {details['failure']['step']}: {details['failure']['code']}" if details.get("failure") else ""))
    ref = state["_ref"]
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
    posix = (details.get("access") or {}).get("posix") or {}
    if posix.get("others_can_read") is False:
        # Design 9.2: a warning only; the folder's permissions are the person's to change.
        result["warnings"] = [{
            "code": "others_cannot_read",
            "message": "Other accounts on this computer may not be able to read this version"
                       + (" (its group can)" if posix.get("group_can_read") else "")
                       + "; nothing was changed. The blocked folders or files are in the receipt.",
            "paths": posix.get("blocked", [])[:5]}]
    return result


def attempts(project, folder=None, *, target_name=None) -> dict:
    """This project's publish attempts and their results, its saved targets, and with a folder that
    target's channels and their history (`analytics publish --list`). Nothing is changed."""
    journal = relations.Journal(writer._journal_bytes(project.journal))
    rows = relations.publish_attempts(journal)
    for row in rows:
        row.pop("line", None)
    out = {"ok": True, "attempts": rows}
    try:
        out["saved_targets"] = target.saved_targets(project.root)
    except PublicationError as exc:
        out["saved_targets"], out["saved_targets_error"] = None, {"code": exc.code, "message": exc.message,
                                                                    "details": exc.details}
    if folder is not None:
        out["target"] = _listing(project, Path(folder), target_name)
    return out


def _listing(project, folder: Path, name):
    """A target's channels as their latest.json gives them; read only, like the judgment."""
    resolved = folder.resolve(strict=False)
    out = {"name": name, "path": os.fspath(folder.absolute()), "resolved": os.fspath(resolved),
           "is_target": os.path.lexists(resolved / target.MARKER), "channels": []}
    if not out["is_target"]:
        return out
    marker = core.read_json(project.marker, {})
    project_id = marker.get("project_id") if isinstance(marker, dict) else None
    projects = resolved / target.PROJECTS
    for names in target.channels(projects).values():
        for channel in names:
            row = {"name": channel}
            try:
                current = target.channel_state(projects / channel, project_id)
                row["state"] = current.state
                if current.latest is not None:
                    row.update(project_id=current.latest["project_id"], project_title=current.latest.get("project_title"),
                               latest=current.latest["latest"], history=current.latest["history"])
            except PublicationError as exc:
                row.update(state="unreadable", code=exc.code)
            out["channels"].append(row)
    return out


def save_target(project, name, folder, *, origin) -> dict:
    """`analytics publish --save-target NAME --to FOLDER` (design 9.3): the folder's judgment only,
    nothing made there; saved in the project only when its .gitignore keeps the file out of git."""
    target.target_name(name)
    w = core.writer_for(project, origin)
    w.admit(OPERATION)
    view = target.judge(folder, project.root)
    if not target.ignores_saved_targets(project.root):
        raise PublicationError("targets_not_ignored",
                               f"saved targets stay out of git: add the line {target.SAVED_TARGETS} to the project's "
                               ".gitignore, then save again", details={"line": target.SAVED_TARGETS})
    with w.locked(OPERATION):  # one writer of the file at a time; no record is written
        saved = target.saved_targets(project.root)
        previous = saved.get(name)
        saved[name] = os.fspath(view.given)
        target.write_saved_targets(project.root, saved, project.platform.files)
    return {"ok": True, "saved": {"name": name, "path": os.fspath(view.given), "resolved": os.fspath(view.resolved),
                                  "is_target": view.marker is not None},
            "replaced": previous if previous not in (None, os.fspath(view.given)) else None}
