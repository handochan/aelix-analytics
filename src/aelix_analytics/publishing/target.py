"""The publish target folder (Issue #21, docs/design/PUBLISH-FOLDER.md 2, 3 and 9.1): its names, the
formats of its files, the read-only judgment of a folder before a publish, the marker's creation and
the channel rules. Nothing here copies a version or moves a pointer; that is the publish (PR-2)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePath

from .. import _platform, core
from . import checks
from .model import PublicationError

MARKER = "analytics-target.json"
LOCK = ".analytics-target.lock"
PUBLICATIONS = "publications"
PROJECTS = "projects"
STAGING = ".staging"
LATEST = "latest.json"
CHANNEL_PAGE = "index.html"
MANAGED = (MARKER, LOCK, PUBLICATIONS, PROJECTS, STAGING)

MARKER_SCHEMA = "analytics.publish_target/1"
CHANNEL_SCHEMA = "analytics.publish_channel/1"
STAGING_SCHEMA = "analytics.publish_staging/1"

ATTEMPT_ID = re.compile(r"att-[0-9a-hjkmnp-tv-z]{26}\Z")
# A publisher's own temporary file in a channel directory names its attempt (design 8).
PUBLISHER_TEMP = re.compile(r"\.att-[0-9a-hjkmnp-tv-z]{26}\.[A-Za-z0-9_.-]+\.tmp\Z")
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"


def new_attempt_id() -> str:
    """`att-` and a lowercase ULID, as a publication_id is made: no two attempts share one, on a folder
    that ignores case too."""
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "att-" + "".join(_ALPHABET[(value >> (5 * n)) & 31] for n in reversed(range(26)))


def canonical(value) -> bytes:
    """The bytes the kit writes for a target file: UTF-8, LF, keys sorted, one final newline (ADR-0007)."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def digest(value: str | None) -> str | None:
    """What a shared folder may hold of a machine's or host's identity: not the value (design 2)."""
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(("aelix-analytics/publish\0" + value).encode("utf-8")).hexdigest()[:32]


def refuse(reason: str, message: str, **details):
    raise PublicationError("target_refused", message, details={"reason": reason, **details})


# --- reading the target's files ---------------------------------------------------------------------

def _read(path: Path, schema: str, required: tuple[str, ...]) -> dict:
    """A target file of the kit's: refused when it is a link, not UTF-8 JSON, of an unknown schema,
    or without a required field (target_unsupported: the kit does not write over what it cannot read)."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise PublicationError("target_unsupported", "a target file cannot be read",
                               details={"path": os.fspath(path), "cause": type(exc).__name__}) from exc
    if _platform.is_link(st) or not stat.S_ISREG(st.st_mode):
        refuse("managed_link", "a target file is a link or not a file", path=os.fspath(path))
    try:
        value = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PublicationError("target_unsupported", "a target file is not the kit's JSON",
                               details={"path": os.fspath(path), "cause": type(exc).__name__}) from exc
    if not isinstance(value, dict) or value.get("schema") != schema or any(k not in value for k in required):
        raise PublicationError("target_unsupported", "a target file of a format this kit does not know",
                               details={"path": os.fspath(path), "schema": value.get("schema")
                                        if isinstance(value, dict) else None})
    return value


def read_marker(path: Path) -> dict:
    return _read(path, MARKER_SCHEMA, ("schema", "created_at", "kit_version"))


def read_channel(path: Path) -> dict:
    value = _read(path, CHANNEL_SCHEMA, ("schema", "channel", "project_id", "latest", "history"))
    if not isinstance(value["history"], list) or not all(isinstance(h, dict) for h in value["history"]):
        raise PublicationError("target_unsupported", "a channel's history is not a list of entries",
                               details={"path": os.fspath(path)})
    return value


def read_staging(path: Path) -> dict:
    return _read(path, STAGING_SCHEMA, ("schema", "attempt_id", "project_id", "publication_id", "machine",
                                        "host", "user", "pid", "started_at", "updated_at"))


# --- judging a folder (design 3) ----------------------------------------------------------------------

@dataclass(frozen=True)
class TargetView:
    given: Path  # the path received, made absolute as it stands
    resolved: Path  # the folder's real place, links resolved
    exists: bool
    marker: dict | None  # the marker's contents, or None for a new target


def _parts(path: PurePath) -> tuple[str, ...]:
    fold = os.name == "nt" or sys.platform == "darwin"  # Windows and macOS's default ignore case
    return tuple(unicodedata.normalize("NFC", part).casefold() if fold else unicodedata.normalize("NFC", part)
                 for part in path.parts)


def _within(inner: PurePath, outer: PurePath) -> bool:
    a, b = _parts(inner), _parts(outer)
    return len(a) >= len(b) and a[: len(b)] == b


def _is_link(path: Path) -> bool:
    try:
        return _platform.is_link(os.lstat(path))
    except OSError:
        return False


def judge(target, project_root) -> TargetView:
    """Design 3: only reads. A refusal is target_refused with its reason; the marker of a format this
    kit does not know is target_unsupported."""
    given = Path(target).absolute()
    resolved = Path(target).resolve(strict=False)
    root = Path(project_root)
    for project in {root.absolute(), root.resolve(strict=False)}:
        for place in (given, resolved):
            if _within(place, project) or _within(project, place):
                refuse("overlaps_project", "a publish target and the project may not be inside each other",
                       target=os.fspath(place), project=os.fspath(project))
    for parent in resolved.parents:
        if os.path.lexists(parent / MARKER):
            refuse("inside_target", "the folder is inside another publish target", marker=os.fspath(parent / MARKER))
    if not os.path.lexists(resolved):
        return TargetView(given, resolved, False, None)
    if not resolved.is_dir():
        refuse("not_directory", "the publish target is not a folder", target=os.fspath(resolved))
    for name in MANAGED:
        if _is_link(resolved / name):
            refuse("managed_link", "a folder the publisher manages is a link or junction", path=os.fspath(resolved / name))
    marker = None
    if os.path.lexists(resolved / MARKER):
        marker = read_marker(resolved / MARKER)
    else:
        others = sorted(name for name in os.listdir(resolved)
                        if name != LOCK and not core.empty_directory_entry(resolved / name))
        if others:
            refuse("not_empty", "the folder holds files and is no publish target; give an empty folder",
                   target=os.fspath(resolved), entries=others[:20], count=len(others))
    staging, versions = resolved / STAGING, resolved / PUBLICATIONS
    if staging.is_dir() and versions.is_dir() and os.stat(staging).st_dev != os.stat(versions).st_dev:
        refuse("cross_volume", "the target's staging and versions are on different volumes")
    return TargetView(given, resolved, True, marker)


def managed_child(path: Path) -> Path:
    """A directory or file the publisher is about to use under a managed folder: never through a link
    (ADR-0009 decision 4)."""
    if _is_link(path):
        refuse("managed_link", "a folder the publisher manages is a link or junction", path=os.fspath(path))
    return path


# --- the marker (design 2) ---------------------------------------------------------------------------

def create_marker(folder: Path, files, *, created_at: str) -> str:
    """The marker, made only when there is none and never written in place: a temporary file given
    the name by `publish_new`, else `rename_new` — the migration's order (owner's decision of
    2026-10-08) — else no_exclusive_rename. Returns how ("publish_new", "rename_new") or "exists" when
    another made it first, whose contents are then read as a marker."""
    marker = folder / MARKER
    temporary = folder / f".{MARKER}.{new_attempt_id()}.tmp"
    with open(temporary, "xb") as fh:
        fh.write(canonical({"schema": MARKER_SCHEMA, "created_at": created_at, "kit_version": core.KIT_VERSION}))
    try:
        try:
            files.publish_new(temporary, marker)
            return "publish_new"
        except _platform.LinkUnsupported:
            pass
        try:
            files.rename_new(temporary, marker)
            return "rename_new"
        except _platform.RenameUnsupported as exc:
            raise PublicationError(
                "no_exclusive_rename",
                "this folder's file system can neither link nor rename without replacing, so no marker can be made",
                details={"target": os.fspath(folder), "cause": str(exc)},
            ) from exc
    except FileExistsError:
        read_marker(marker)
        return "exists"
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


# --- channels (design 9.1) ---------------------------------------------------------------------------

def channel_name(name: str) -> str:
    """A channel's name as it is written: NFC, one name every platform can use (ADR-0003 decision 6)."""
    if not isinstance(name, str):
        refuse("channel_name", "a channel name is text")
    value = unicodedata.normalize("NFC", name)
    if not value or value.startswith(".") or "/" in value or "\\" in value or value != value.strip():
        refuse("channel_name", "a channel name is one folder name, not hidden, without separators", channel=name)
    try:
        checks.check_names([f"{PROJECTS}/{value}/{CHANNEL_PAGE}"])
    except PublicationError as exc:
        refuse("channel_name", "a channel name every platform can use", channel=name, cause=exc.message)
    return value


def channel_key(name: str) -> str:
    """Two names are one channel when they differ only by Unicode normalization or case."""
    return unicodedata.normalize("NFC", name).casefold()


def default_channel(title: str | None, project_id: str) -> str:
    """The title's slug, else `project-` and the first 8 hex digits of the project_id (a Korean title
    has no slug): two projects with no slug get two names; copies of one project share theirs."""
    slug = core.slugify(title or "", 40)
    if slug:
        return slug
    digits = project_id.removeprefix("prj_")[:8]
    return f"project-{digits}"


def channels(projects: Path) -> dict[str, list[str]]:
    """The channel folders of a target by their key; more than one name for a key is refused by the
    caller (design 9.1)."""
    out: dict[str, list[str]] = {}
    if not projects.is_dir():
        return out
    for name in sorted(os.listdir(projects)):
        if core.empty_directory_entry(projects / name):
            continue
        out.setdefault(channel_key(name), []).append(name)
    return out


@dataclass(frozen=True)
class ChannelState:
    state: str  # "absent", "free", "own", "other", "foreign"
    latest: dict | None  # latest.json when there is one
    foreign: tuple[str, ...] = ()  # files the publisher did not write


def channel_state(folder: Path, project_id: str) -> ChannelState:
    """Whose a channel folder is (ADR-0009 decision 4): absent; free (only system metadata and
    publisher temporary files); own (its latest.json is this project's); other (another project's);
    foreign (a file the publisher did not write — never taken, not even by --reassign-channel)."""
    managed_child(folder)
    if not os.path.lexists(folder):
        return ChannelState("absent", None)
    if not folder.is_dir():
        return ChannelState("foreign", None, (folder.name,))
    names = sorted(os.listdir(folder))
    # A channel page without its latest.json proves no owner: it is not taken either.
    own_files = (LATEST, CHANNEL_PAGE) if LATEST in names else ()
    foreign = tuple(n for n in names if n not in own_files and not PUBLISHER_TEMP.match(n)
                    and not core.empty_directory_entry(folder / n))
    if foreign:
        return ChannelState("foreign", None, foreign)
    if LATEST not in names:
        return ChannelState("free", None)
    latest = read_channel(folder / LATEST)
    return ChannelState("own" if latest["project_id"] == project_id else "other", latest)
