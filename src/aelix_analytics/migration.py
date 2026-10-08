"""The copy migration: a `lab.json` project copied into a new folder in the current format, the
original left as it was (Issue #13, docs/design/RECORD-WRITER-AND-MIGRATION.md section 6). Standard
library only.

    migrate(source, destination, origin=Origin(...))           # copy, check, commit
    migrate(None, destination, origin=...)                       # resume a checked copy, or confirm
    migrate(source, destination, origin=..., dry_run=True)      # what would happen; writes nothing

What it keeps (design section 1):

- The source is read, never written (S1): the only things it may make there are the older kit's two
  lock files when they are missing (`.lab/run.lock`, `.lab/lock`), which it holds without waiting
  while it plans, copies and checks the source again (steps 3-7) and which the ledger and the report
  name. A source without `.lab/`, or whose lock files can be neither made nor opened, is copied without
  them and kept only by the comparison before and after (OQ15).
- Every file it makes in the destination is named in its ledger (`progress.jsonl`), or in a plan a
  ledger line binds by its sha256, before it is made, and made only where nothing is (`O_EXCL`, the
  port's create-if-absent `publish_new`): nothing of anyone else's is overwritten, and what it removes
  is only what its ledger named as its own (S6, design 6.3 and 6.10). Its own directory under
  `.analytics/migration/` is the exception the design makes: the plans, the mapping, the report and
  the proposals there are written whole by a replace.
- The commit point is `analytics.json`, published last where none is (step 11, S2), after every copy
  is checked against the source read again under its locks (step 7) and the report and the mapping
  are written and flushed. Before it no kit takes the folder for a project it may write or read
  (`migration_incomplete`); an interrupted migration resumes from the state its ledger's lines define
  (6.10), and the same call on a committed destination of the same source is `already_migrated` (6.9).
  The marker is never written in place: where the file system has no links it is published by the
  port's rename that refuses a taken name (`rename_new`), and where it has neither the destination is
  refused at step 2's probe, before any copy, and again at step 11, before the marker (owner's decision
  of 2026-10-08: `destination_invalid`, reason `no_exclusive_rename`). A copy there is still written
  directly (design 6.12, D19).

The kit's command `migrate` runs it (`python -m aelix_analytics migrate --to DEST [--dry-run]`, which
the launchers start outside any project environment, design 6.1); the aelix process never loads it:
`core` does not import it. `on_step(name, info)` (private) is the tests' fault injection (design 11.1):
it is called by name before and after each ledger line (`ledger:<step>`, `<step>`) and at the points in
between where an interruption leaves something of its own (a temporary file half written, a file
published before its line).
"""

from __future__ import annotations

import ast
import base64
import contextlib
import difflib
import hashlib
import heapq
import json
import os
import secrets
import shutil
import stat
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from . import _platform, core, relations, writer
from .writer import AnalyticsError, Origin

FAMILY = relations.MIGRATION_V1
STATE_DIR = ".analytics"
LEGACY_STATE_DIR = writer.LEGACY_LAYOUT.state_dir  # .lab
MIGRATIONS = writer.MIGRATION_DIR  # .analytics/migration
DEST_LOCK = writer.MIGRATION_LOCK  # .analytics/migration.lock
LEDGER = writer.LEDGER  # progress.jsonl
SOURCE_MARKER = "source-marker.json"
CHUNK = 1 << 20
# The older runner's order: its run lock, then inside it the journal lock (design 6.3 step 3).
SOURCE_LOCKS = ("run.lock", "lock")
# What is not copied (OQ4): the environment and the Workspace at the root, the cache and the older
# kit's locks in .lab/, Python's caches and the views an operating system makes of a folder anywhere.
OS_METADATA = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
CACHE_NAMES = frozenset({"__pycache__"})
EXCLUDED_AT_ROOT = {".venv": "environment", "report": "workspace"}
EXCLUDED_IN_STATE = {"cache": "cache", "lock": "lock", "run.lock": "lock", "report.lock": "lock"}
# What a person's tools leave in a destination meanwhile, passed over when no plan names it (OQ16).
IGNORED_AT_ROOT = frozenset({".venv"})
# The current layout's managed names under .analytics/ that a file of .lab/ must not take (design
# 6.2): compared as a file system that folds case and normalization would.
NEW_STATE_NAMES = ("migration", "publications", "run.lock", "workspace.lock", "migration.lock")
MANAGED_AT_ROOT = writer.LEGACY_LAYOUT.managed_dirs  # .venv .lab runs report
# A destination path longer than this is a warning (ADR-0003 decision 6, Windows' MAX_PATH less its
# terminator): the operating system decides, and its refusal is destination_write_failed.
LONG_PATH = 259
# Room the destination's disk keeps beyond the copy: 1% of it and 64 MiB (design 6.2).
SPACE_SHARE, SPACE_MARGIN = 0.01, 64 << 20
# Files kept by appending: a destination compared with its report compares their first bytes (6.9).
APPENDED = ("journal.jsonl", f"{STATE_DIR}/telemetry.jsonl", f"{STATE_DIR}/queries.jsonl")
# The ledger's lines (design 6.8).
STEPS = ("start", "probed", "locked", "plan", "begin", "direct_intent", "direct", "copied",
         "source_checked", "verified", "transform_begin", "transformed", "reported", "durable",
         "marker_begin", "committed", "trusted", "failed")
COPY_STEPS = ("begin", "direct_intent", "direct", "copied")
# .gitignore (design 6.13): an older template line the source has and its new name, and the new
# files' lines added always.
GITIGNORE_RENAMED = ((".lab/cache/", ".analytics/cache/"), (".lab/lock", ".analytics/lock"),
                     (".lab/adhoc/", ".analytics/adhoc/"), ("report/", "workspace/"),
                     ("runs/*/charts/*.data.parquet", "runs/*/visualizations/*.data.parquet"))
GITIGNORE_ALWAYS = (".analytics/run.lock", ".analytics/workspace.lock", ".analytics/migration.lock",
                    ".analytics/publications/")
# The script API's names before kit 0.2.0 and 0.3.0 (Issue #9) a script proposal renames (6.13).
OLD_MODULE, NEW_MODULE = "labkit", "aelix_analytics"
RENAMED_CALLS = {"note": "record", "chart": "visualize"}
TEMPLATE = Path(core.__file__).resolve().parent / "template"
MARKER_TEMP = ".analytics-json-"
# Step 2's test names, inside the migration's own directory (its `start` line names them): the file
# that tells whether the destination folds names, the link that tells whether it holds one, and the
# name the file is published under to tell how the marker is published there.
PROBE, PROBE_LINK, PROBE_PUBLISH = ".probe-Ab\u00e9.tmp", ".probe-link.tmp", ".probe-publish.tmp"
# How a destination publishes its marker (the `probed` line's `publish`): the port's publish_new (a
# link, or Windows' rename), or where the file system has no links its rename_new.
PUBLICATIONS = ("publish_new", "rename_new")


def _no_step(_name: str, _info: dict) -> None:
    return None


# --- small helpers ---------------------------------------------------------------------------------

def _now() -> str:
    return core.iso()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value) -> bytes:
    """A record as the kit writes one: UTF-8, two-space indent, LF, a line end after it."""
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _posix(*parts: str) -> str:
    return "/".join(p for p in parts if p)


def _parent(rel: str) -> str:
    return rel.rpartition("/")[0]


def _name(rel: str) -> str:
    return rel.rpartition("/")[2]


def _folded(name: str, case: bool, normalization: bool) -> str:
    """`name` as a file system that folds case and/or Unicode normalization compares it."""
    if normalization:
        name = unicodedata.normalize("NFC", name)
    return name.casefold() if case else name


def _key(st: os.stat_result) -> list:
    return [st.st_dev, st.st_ino]


def _loads(data):
    try:
        return json.loads(data)
    except (ValueError, RecursionError, TypeError):
        return None


def _utf8(name: str) -> bool:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _escaped(name: str) -> str:
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def _resolved(path) -> str:
    """`path` as a real path: its nearest existing ancestor's real path in its stored spelling, the
    rest joined to it as given (design 6.2: a destination that does not exist yet). A `..` is the
    operating system's: after a link it is the parent of the link's target (the existing part is
    resolved by realpath, which reads it so), and in the part that does not exist yet, where there is
    no link, it is the parent by its text."""
    path = os.fspath(core.expand_home(Path(path)))
    path = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
    rest: list[str] = []
    here = path
    while not os.path.lexists(here):
        parent = os.path.dirname(here)
        if parent == here:
            break
        rest.append(os.path.basename(here))
        here = parent
    real = core.canonical(here)
    return os.path.normpath(os.path.join(real, *reversed(rest))) if rest else real


def _existing(path: str) -> str:
    while not os.path.lexists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return path
        path = parent
    return path


def _ancestors(path: str) -> list[str]:
    out, here = [], path
    while True:
        parent = os.path.dirname(here)
        if parent == here:
            return out
        out.append(parent)
        here = parent


def _same(a, b) -> bool:
    return core.same_file(a, b)


def _same_entry(a, b) -> bool:
    try:
        x, y = os.lstat(a), os.lstat(b)
    except OSError:
        return False
    return bool(x.st_ino) and _key(x) == _key(y)


def _remove_own(path) -> None:
    """Remove a file this migration made and its ledger named, read-only or not (Windows refuses to
    remove a read-only file, design 6.4). A link there is removed as itself: nothing it leads to is
    looked at, its mode included (FileNotFoundError when nothing is there)."""
    st = os.lstat(path)
    if _platform.is_link(st):
        try:
            os.unlink(path)
        except (IsADirectoryError, PermissionError):
            os.rmdir(path)  # a directory link or junction on Windows is removed as a directory
        return
    if not st.st_mode & stat.S_IWRITE:
        with contextlib.suppress(OSError, NotImplementedError):
            if os.chmod in os.supports_follow_symlinks:
                os.chmod(path, stat.S_IMODE(st.st_mode) | stat.S_IWRITE, follow_symlinks=False)
            else:
                os.chmod(path, stat.S_IMODE(st.st_mode) | stat.S_IWRITE)
    os.unlink(path)


# Never follow a link where the migration makes a file of its own (O_NOFOLLOW where the platform has
# it; on Windows the judgment refuses a link in the paths the migration keeps, design 6.3).
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _create(path, flags: int = 0) -> int:
    """A new file at `path`, made only where nothing is (O_EXCL: an entry there, a link included, is
    FileExistsError), for writing."""
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | getattr(os, "O_BINARY", 0)
                   | flags, 0o666)


def _hash_path(path) -> str | None:
    """The sha256 of the bytes at `path`, None when it cannot be read."""
    try:
        with open(path, "rb") as fh:
            digest = hashlib.sha256()
            for block in iter(lambda: fh.read(CHUNK), b""):
                digest.update(block)
            return digest.hexdigest()
    except OSError:
        return None


class _SourceChanged(Exception):
    """An entry of the source is not the one the walk saw (found where a file is opened)."""

    def __init__(self, path):
        super().__init__(str(path))
        self.path = str(path)


class _Unreadable(Exception):
    def __init__(self, path, exc: OSError):
        super().__init__(f"{path}: {exc}")
        self.path = str(path)
        self.exc = exc


def _open_source(path, key) -> int:
    """A descriptor of the source file `path` that is the regular file the walk saw (`key`,
    `[st_dev, st_ino]`): checked on the open handle, so an entry swapped for a FIFO, a device, a link
    or another file since is _SourceChanged, and a FIFO is never waited on (design 6.4). Opened by
    _platform.open_for_reading, so a refusal keeps Windows' own error (_unreadable_error)."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise _SourceChanged(path)
        fd = _platform.open_for_reading(path)
    except _SourceChanged:
        raise
    except FileNotFoundError:
        raise _SourceChanged(path) from None
    except OSError as exc:
        raise _Unreadable(path, exc) from exc
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or (key is not None and _key(opened) != list(key)):
        os.close(fd)
        raise _SourceChanged(path)
    return fd


def _hash_source(path, key) -> tuple[str, int]:
    fd = _open_source(path, key)
    with open(fd, "rb", closefd=True) as fh:
        digest, size = hashlib.sha256(), 0
        try:
            for block in iter(lambda: fh.read(CHUNK), b""):
                digest.update(block)
                size += len(block)
        except OSError as exc:
            raise _Unreadable(path, exc) from exc
    return digest.hexdigest(), size


def _unreadable_error(problem: _Unreadable) -> AnalyticsError:
    """source_unreadable: `in_use` when Windows says another program has the file (a sharing
    violation, 32, which the open reports; a lock violation, 33, has a Windows code only where a
    call keeps one, and a read through the C runtime does not), `permission` for any other
    PermissionError, else `os_error`."""
    exc = problem.exc
    reason = ("in_use" if getattr(exc, "winerror", None) in (32, 33)
              else "permission" if isinstance(exc, PermissionError) else "os_error")
    return AnalyticsError("source_unreadable", f"the source cannot be read at {problem.path}: {exc}",
                          hint="make it readable (another program may hold it open, or its "
                               "permissions forbid reading it), then run the migration again",
                          details={"reason": reason, "path": problem.path, "error": str(exc)})


def _invalid(reason: str, message: str, hint: str, **details) -> AnalyticsError:
    return AnalyticsError("destination_invalid", message, hint=hint,
                          details={"reason": reason, **details})


def _mismatch(message: str, **details) -> AnalyticsError:
    return AnalyticsError("destination_mismatch", message,
                          hint="the destination holds what this migration's ledger does not account "
                               "for; nothing was removed or overwritten. Remove the destination and "
                               "start again, or use another destination",
                          details=details)


def _occupied(rel: str) -> AnalyticsError:
    return AnalyticsError("destination_mismatch", f"{rel} was taken in the destination by something "
                          "else: it is not overwritten",
                          hint="something else writes to the destination; nothing was overwritten. "
                               "Remove that entry if it is not yours, or use another destination, "
                               "and run the migration again",
                          details={"reason": "occupied", "path": rel})


def _no_exclusive_rename(dest, exc: OSError, *, copied: bool) -> AnalyticsError:
    """The destination's file system can neither link a file nor rename one without replacing what
    is there (owner's decision of 2026-10-08, Issue #13): the marker is not written in place there,
    so the migration stops before it — at step 2, before any copy, or at step 11."""
    where = ("its copies are there, without the marker; " if copied
             else "nothing was copied: ")
    return _invalid("no_exclusive_rename", f"the file system of {dest} can neither link a file nor "
                    "rename one without replacing what is there, so the migration cannot publish its "
                    "marker there in one step",
                    f"migrate to a folder on another file system (APFS, ext4 or NTFS, say; FAT and "
                    f"exFAT may have neither); {where}what the migration made is in this folder, "
                    "which you can remove", path=str(dest), error=str(exc))


def _write_failed(path, exc: OSError) -> AnalyticsError:
    return AnalyticsError("destination_write_failed", f"the destination refused {path}: {exc}",
                          hint="a path too long, a name the destination's system reserves, or its "
                               "permissions: the migration stopped there, before its marker",
                          details={"path": str(path), "error": str(exc)})


def _changed(paths) -> AnalyticsError:
    paths = sorted(set(paths))
    return AnalyticsError("source_changed", f"the source changed while it was copied: "
                          f"{', '.join(paths[:5])}" + (f" and {len(paths) - 5} more" if len(paths) > 5
                                                       else ""),
                          hint="an older kit or a person wrote to it during the copy (an older aelix "
                               "session left open?). Close it and run the migration again; nothing "
                               "in the destination was overwritten. A file copied already that changed "
                               "since cannot be copied again: then remove the destination or use "
                               "another one",
                          details={"paths": paths})


# --- the ledger ------------------------------------------------------------------------------------

def append_line(path: Path, value: dict) -> None:
    """Append one ledger line by the writer's rule (writer.append_line): an LF first when the last
    byte is not one (a cut-off append before it, design 6.8), then the line and its LF in one write,
    a link at the ledger's name not followed. Not flushed: a resumed migration hashes every copy
    again, so a ledger behind the disk judges nothing wrongly."""
    writer.append_line(path, json.dumps(value, ensure_ascii=False).encode("utf-8"), follow=False)


class LedgerError(Exception):
    """A ledger whose lines are not one of the states design 6.10 defines."""


class Ledger:
    """A migration's ledger read as design 6.8 and 6.10 say. A line that does not parse is a cut-off
    append and skipped: what it was to say did not happen, and the file it was to name was not made
    (the ledger comes first). A line that parses and is no step, or comes out of order, is
    LedgerError. It keeps what the steps need: the start, the plan lines, the paths begun, copied or
    written directly, every temporary file named, the source check, the transform, the last report,
    the marker's bytes and the commit."""

    def __init__(self, data: bytes, directory: Path):
        self.directory = directory
        self.lines: list[dict] = []
        self.start: dict | None = None
        self.plans: list[dict] = []
        self.locked: dict | None = None
        self.probed: dict | None = None
        self.begun: dict[str, dict] = {}
        self.begun_temps: dict[str, list[str]] = {}  # every temporary name each path's lines named
        self.copied: dict[str, dict] = {}
        self.direct_intent: set[str] = set()
        self.direct: dict[str, dict] = {}
        self.temps: set[str] = set()
        self.source_checked: dict | None = None
        self.transform: dict | None = None
        self.transformed: dict | None = None
        self.reported: dict | None = None
        self.marker: dict | None = None
        self.marker_temps: list[str] = []  # every temporary name a marker_begin line named
        self.committed = False
        self.trusted = False
        self.occupied: set[str] = set()  # paths whose publication found something else there
        # Temporary names a line named whose exclusive creation found something there: not this
        # migration's, never removed (S6), and refused where they are found (design 6.10 ①).
        self.foreign: set[str] = set()
        self.last: str | None = None
        for raw in data.split(b"\n"):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            if not isinstance(value, dict) or value.get("step") not in STEPS:
                raise LedgerError(f"a line the ledger does not know: {raw[:160]!r}")
            self.add(value)

    @classmethod
    def read(cls, directory: Path) -> Ledger:
        try:
            data = (directory / LEDGER).read_bytes()
        except FileNotFoundError:
            data = b""
        return cls(data, directory)

    @property
    def migration_id(self) -> str:
        return self.directory.name

    @property
    def project_id(self) -> str | None:
        return self.plans[0].get("project_id") if self.plans else None

    def add(self, value: dict) -> None:
        step, last, path = value["step"], self.last, value.get("path")
        self.lines.append(value)
        if step == "failed":
            if last is None:
                raise LedgerError("a failure before the start")
            if value.get("reason") == "occupied" and isinstance(path, str):
                (self.foreign if path in self.temps else self.occupied).add(path)
            return
        copying = last in ("plan", *COPY_STEPS)
        allowed = {
            "start": last is None,
            "probed": last in ("start", "probed", "locked") and not self.plans,
            "locked": (last == "probed" or copying) and self.source_checked is None,
            "plan": last == "locked",
            "begin": copying,
            # A direct write (no links on this file system): a copy's only. The marker is published
            # by a rename there, never written in place (owner's decision of 2026-10-08).
            "direct_intent": copying,
            "direct": last == "direct_intent" and path in self.direct_intent,
            "copied": copying and (path in self.begun or path in self.direct),
            "source_checked": copying,
            "verified": last in ("source_checked", "verified"),
            "transform_begin": last in ("verified", "transform_begin"),
            "transformed": last == "transform_begin",
            "reported": last in ("verified", "transformed", "reported", "durable", "marker_begin"),
            "durable": last in ("reported", "durable", "marker_begin"),
            "marker_begin": last in ("durable", "marker_begin", "committed", "trusted"),
            "committed": last == "marker_begin",
            "trusted": last in ("committed", "trusted"),
        }[step]
        if not allowed:
            raise LedgerError(f"{step} after {last}")
        if step == "start":
            self.start = value
        elif step == "probed":
            self.probed = value
        elif step == "locked":
            self.locked = value
        elif step == "plan":
            self.plans.append(value)
        elif step == "begin":
            self.begun[path] = value
            self.occupied.discard(path)
            if value.get("temp"):
                self.temps.add(_posix(_parent(path), value["temp"]))
                self.begun_temps.setdefault(path, []).append(value["temp"])
        elif step == "direct_intent":
            self.direct_intent.add(path)
        elif step == "direct":
            self.direct[path] = value
        elif step == "copied":
            self.copied[path] = value
        elif step == "source_checked":
            self.source_checked = value
        elif step == "transform_begin":
            self.transform, self.transformed = value, None
            self.temps.add(value["temp"])
        elif step == "transformed":
            self.transformed = value
        elif step == "reported":
            self.reported = value
        elif step == "marker_begin":
            self.marker = value
            self.temps.add(value["temp"])
            self.marker_temps.append(value["temp"])
        elif step == "committed":
            self.committed = True
        elif step == "trusted":
            self.trusted = True
        self.last = step

    def owns(self, temp: str) -> bool:
        """Whether the temporary file `temp` (a destination path) is this migration's: a line named
        it, and its exclusive creation did not find something there (S6, design 6.10)."""
        return temp in self.temps and temp not in self.foreign


def _contentless(directory: Path) -> bool:
    """A migration directory holding nothing but a ledger that is missing, empty or one cut-off first
    line: interrupted in step 2 before its `start` line ended (design 6.10)."""
    try:
        names = [e.name for e in os.scandir(directory)]
    except OSError:
        return False
    if any(n != LEDGER for n in names):
        return False
    if not names:
        return True
    try:
        data = (directory / LEDGER).read_bytes()
    except OSError:
        return False
    lines = [raw for raw in data.split(b"\n") if raw.strip()]
    if not lines:
        return True
    if len(lines) > 1:
        return False
    return _loads(lines[0]) is None


# --- the source's walk -----------------------------------------------------------------------------

class Inventory:
    """What one walk of the source found (design 6.3 step 4, 6.5, 6.6): the files and the directories
    the plan copies by their namespace path, the links made again, what is left out and why, the
    places a copy reads beyond the root (R), and what refuses the migration — a link outside whose
    target is not there, a name that is not UTF-8, a nested project, a name the current layout
    manages."""

    def __init__(self) -> None:
        self.files: list[dict] = []  # {rel, real, size, mtime_ns, atime_ns, mode, key[, sha256]}
        self.links: list[dict] = []  # links made again: {rel, text, dir}
        self.dirs: list[dict] = []  # {rel, real, mode, mtime_ns}
        self.excluded: dict[tuple[str, str], list[int]] = {}
        self.not_copied: list[dict] = []
        self.boundaries: list[dict] = []  # links whose bytes are copied: {path, kind, text, target_real, treatment}
        self.reach: list[dict] = []  # R beyond the root: {path, via}
        self.missing_targets: list[dict] = []
        self.not_utf8: list[dict] = []
        self.nested: list[str] = []
        self.collisions: list[list[str]] = []
        self.state_not_folder: list[dict] = []  # a root .lab that is no folder there: {path, text?}
        self.warnings: list[dict] = []

    def exclude(self, rel: str, reason: str, files: int, size: int) -> None:
        counts = self.excluded.setdefault((rel, reason), [0, 0])
        counts[0] += files
        counts[1] += size

    def excluded_list(self) -> list[dict]:
        return [{"path": rel, "reason": reason, "files": n, "bytes": size}
                for (rel, reason), (n, size) in sorted(self.excluded.items())]

    def refusals(self) -> list[AnalyticsError]:
        """The source-side refusals of design 6.2, the targets missing first (OQ12)."""
        out = []
        if self.missing_targets:
            out.append(AnalyticsError(
                "source_unreadable", f"{len(self.missing_targets)} link(s) in the source lead outside "
                "it to a target that is not there", details={"reason": "link_target_missing",
                                                             "links": self.missing_targets},
                hint="connect the target (a disk not mounted?) or remove the link, then run the "
                     "migration again; nothing was written"))
        if self.not_utf8:
            out.append(AnalyticsError(
                "source_unreadable", f"{len(self.not_utf8)} name(s) in the source are not UTF-8, which "
                "a record cannot hold", details={"reason": "name_not_utf8", "paths": self.not_utf8},
                hint="rename them, then run the migration again; nothing was written"))
        if self.nested:
            out.append(AnalyticsError(
                "nested_project", f"the source holds another project's marker or {STATE_DIR}/: "
                f"{', '.join(self.nested[:5])}", details={"paths": self.nested},
                hint="move the nested project out of the source, or migrate it on its own: a copy "
                     "would put a project an older kit writes inside the new one"))
        if self.collisions:
            out.append(_invalid("name_collision", "a file of the source's .lab/ would take a name the "
                                f"current layout manages under {STATE_DIR}/",
                                "rename it or move it out of .lab/, then run the migration again",
                                pairs=self.collisions))
        if self.state_not_folder:
            out.append(_invalid("name_collision", f"the source's {LEGACY_STATE_DIR} is not a folder of "
                                "its own (a link to a place inside the source, a file, or a link to "
                                f"one): the copy keeps {LEGACY_STATE_DIR}/ as {STATE_DIR}/, the folder "
                                "the current layout manages, and cannot put that there",
                                f"make {LEGACY_STATE_DIR} a folder (move what it leads to into it) or a "
                                "link to a folder outside the source, then run the migration again; "
                                "nothing was written",
                                pairs=[[LEGACY_STATE_DIR, STATE_DIR]], state_dir=self.state_not_folder))
        return out


class _Walker:
    """The walk of the source's namespace (design 6.3 step 4, 6.6): the root's tree, then each link
    leading outside it once, in code point order of its namespace path — the boundaries Project.walk
    finds — a directory link's tree walked once per target. With `hashed`, every file is hashed on
    a handle checked to be the file the walk saw (step 7)."""

    def __init__(self, root: str, spellings: list[str], *, hashed: bool = False):
        self.root = root
        self.spellings = spellings
        self.hashed = hashed
        self.spelling = core.Spelling()
        self.out = Inventory()
        self.claimed: dict[tuple, str] = {}
        self.managed: set[str] = set()
        for name in MANAGED_AT_ROOT:
            with contextlib.suppress(OSError, ValueError):
                self.managed.add(self.spelling.path(os.path.realpath(os.path.join(root, name),
                                                                     strict=True)))

    def run(self) -> Inventory:
        self._claim(self.root, "")
        pending: list[tuple] = []
        self._tree(self.root, "", pending, in_root=True)
        while pending:
            self._boundary(*heapq.heappop(pending), pending)
        return self.out

    def _claim(self, path: str, ns: str) -> str | None:
        """Claim a directory for the walk: None when it is new, else the namespace path that did."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (st.st_dev, st.st_ino) if st.st_ino else ("path", self.spelling.path(path))
        if key in self.claimed:
            return self.claimed[key]
        self.claimed[key] = ns
        return None

    @staticmethod
    def _count(path: str) -> tuple[int, int]:
        """Files and bytes at or below an excluded entry, links not followed."""
        try:
            st = os.lstat(path)
        except OSError:
            return 0, 0
        if not stat.S_ISDIR(st.st_mode):
            return 1, st.st_size if stat.S_ISREG(st.st_mode) else 0
        files = size = 0
        for directory, _dirs, names in os.walk(path):
            for name in names:
                with contextlib.suppress(OSError):
                    entry = os.lstat(os.path.join(directory, name))
                    files += 1
                    size += entry.st_size if stat.S_ISREG(entry.st_mode) else 0
        return files, size

    @staticmethod
    def _excluded(rel: str, name: str) -> str | None:
        """Why an entry is not copied (OQ4), by its namespace path: so a .lab/ that is a link leading
        outside leaves out its cache and locks as the folder does."""
        if rel in EXCLUDED_AT_ROOT:
            return EXCLUDED_AT_ROOT[rel]
        if _parent(rel) == LEGACY_STATE_DIR and name in EXCLUDED_IN_STATE:
            return EXCLUDED_IN_STATE[name]
        if name in CACHE_NAMES:
            return "cache"
        if name in OS_METADATA:
            return "os_metadata"
        return None

    def _tree(self, top: str, top_ns: str, pending: list, *, in_root: bool) -> None:
        stack = [(top, top_ns)]
        while stack:
            directory, ns = stack.pop()
            try:
                with os.scandir(directory) as listing:
                    entries = sorted(listing, key=lambda e: e.name)
            except OSError as exc:
                raise _Unreadable(directory, exc) from exc
            subdirectories = []
            at_root = in_root and not ns
            for entry in entries:
                rel = _posix(ns, entry.name)
                if not _utf8(entry.name):
                    self.out.not_utf8.append({"path": _escaped(rel), "base64": base64.b64encode(
                        os.fsencode(entry.path)).decode("ascii")})
                    continue
                reason = self._excluded(rel, entry.name)
                if reason:
                    self.out.exclude(rel, reason, *self._count(entry.path))
                    continue
                try:
                    st = os.lstat(entry.path)
                except OSError as exc:
                    raise _Unreadable(entry.path, exc) from exc
                if at_root and entry.name == writer.MARKER:
                    continue  # the source's markers are judged before the walk (marker_state)
                if at_root and entry.name == STATE_DIR:
                    self.out.nested.append(rel)
                    continue
                if at_root and entry.name == ".git" and stat.S_ISREG(st.st_mode):
                    self.out.not_copied.append({"path": rel, "reason": "git_worktree_pointer"})
                    self.out.warnings.append({"path": rel, "reason": "git_worktree_pointer"})
                    continue
                if at_root and entry.name == LEGACY_STATE_DIR and stat.S_ISREG(st.st_mode):
                    self.out.state_not_folder.append({"path": rel})
                    continue
                if not at_root and entry.name in (writer.MARKER, writer.LEGACY_MARKER):
                    self.out.nested.append(rel)
                if ns == LEGACY_STATE_DIR and any(
                        core.folded(entry.name) == core.folded(n) for n in NEW_STATE_NAMES):
                    self.out.collisions.append([rel, f"{STATE_DIR}/{entry.name}"])
                if _platform.is_link(st):
                    self._link(entry.path, rel, st, pending,
                               managed_entry=at_root and entry.name in MANAGED_AT_ROOT)
                elif stat.S_ISDIR(st.st_mode):
                    first = self._claim(entry.path, rel)
                    if first is None:
                        self.out.dirs.append({"rel": rel, "real": entry.path, "mode": st.st_mode,
                                              "mtime_ns": st.st_mtime_ns})
                        subdirectories.append((entry.path, rel))
                    else:  # a folder an outside link led to already, copied there once (OQ3)
                        self.out.not_copied.append({"path": rel, "reason": "copied_elsewhere",
                                                    "at": first})
                elif stat.S_ISREG(st.st_mode):
                    self._file(rel, entry.path, st)
                else:
                    self.out.not_copied.append({"path": rel, "reason": "special_file"})
            stack.extend(reversed(subdirectories))

    def _file(self, rel: str, real: str, st: os.stat_result) -> None:
        item = {"rel": rel, "real": real, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                "atime_ns": st.st_atime_ns, "mode": st.st_mode, "key": _key(st)}
        if self.hashed:
            try:
                item["sha256"], item["size"] = _hash_source(real, item["key"])
            except _SourceChanged:
                item["sha256"] = None
        self.out.files.append(item)

    def _folded_target(self, ns: str, text: str) -> list[str] | None:
        """A relative link's target as namespace parts, its text joined to the link's directory and
        folded as text from the front, or None when it leaves the root or takes `..` after a component
        that is a link in the source (POSIX resolves that `..` from the link's target, design 6.6)."""
        if not text or os.path.isabs(text) or os.path.splitdrive(text)[0]:
            return None
        parts = [p for p in ns.split("/") if p][:-1]
        for part in text.replace(os.altsep, os.sep).split(os.sep) if os.altsep else text.split(os.sep):
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    return None
                try:
                    if _platform.is_link(os.lstat(os.path.join(self.root, *parts))):
                        return None
                except OSError:
                    pass
                parts.pop()
            else:
                parts.append(part)
        return parts

    def _made_again(self, rel: str, text: str) -> bool:
        """Whether a link inside is made again with its text in the destination: its text stays in the
        root (above), and the place it names is not under .lab/, which the destination calls
        .analytics/ (a link through it would lead nowhere there)."""
        parts = self._folded_target(rel, text)
        return parts is not None and (not parts or parts[0] != LEGACY_STATE_DIR)

    def _link(self, path: str, rel: str, st: os.stat_result, pending: list, *,
              managed_entry: bool) -> None:
        try:
            # The link's text as it was made: Windows' readlink gives the reparse point's
            # substitute name, which has an extended-length prefix (`\\?\C:\…`) for an absolute
            # target and always for a junction. A relative text, the only one made again, has none.
            text = _platform.plain_path(os.readlink(path))
        except OSError as exc:
            raise _Unreadable(path, exc) from exc
        state_dir = managed_entry and rel == LEGACY_STATE_DIR
        try:
            real = self.spelling.path(os.path.realpath(path, strict=True))
            target = os.stat(real)
        except (OSError, ValueError):
            if state_dir and self._made_again(rel, text):
                self.out.state_not_folder.append({"path": rel, "text": text})
                return
            self._dangling(path, rel, text, st)
            return
        if state_dir and not core._under(self.root, real) and (
                core._under(real, self.root) or not stat.S_ISDIR(target.st_mode)):
            # .lab a link to a place inside the source, or to a file: made again it would be a link
            # at .analytics, and copied it would be no folder there (a folder outside is walked as
            # .lab below, design 6.6)
            self.out.state_not_folder.append({"path": rel, "text": text})
            return
        if core._under(self.root, real):  # the root or one of its ancestors
            self.out.not_copied.append({"path": rel, "reason": "link_to_root", "text": text})
        elif core._under(real, self.root):
            if real in self.managed and not managed_entry:
                self.out.not_copied.append({"path": rel, "reason": "link_to_managed_directory",
                                            "text": text})
            elif self._made_again(rel, text):
                self.out.links.append({"rel": rel, "text": text, "dir": stat.S_ISDIR(target.st_mode)})
            elif stat.S_ISREG(target.st_mode):
                self.out.boundaries.append({"path": rel, "kind": "file", "text": text,
                                            "target_real": real, "treatment": "copied_as_file"})
                self._file(rel, real, target)
            else:
                self.out.not_copied.append({"path": rel, "reason": "inside_link_directory"
                                            if stat.S_ISDIR(target.st_mode) else "special_file",
                                            "text": text})
        elif not managed_entry and any(core._under(real, place) for place in self.managed):
            self.out.not_copied.append({"path": rel, "reason": "link_to_managed_directory",
                                        "text": text})
        elif stat.S_ISDIR(target.st_mode) or stat.S_ISREG(target.st_mode):
            kind = "dir" if stat.S_ISDIR(target.st_mode) else "file"
            heapq.heappush(pending, (rel, kind, real, text))
        else:
            self.out.not_copied.append({"path": rel, "reason": "special_file", "text": text})

    def _dangling(self, path: str, rel: str, text: str, st: os.stat_result) -> None:
        """A link whose target is not there (or that loops): made again when its text stays inside,
        not copied when its text names a place inside, and refusing the migration when it names a
        place outside (OQ12)."""
        if self._made_again(rel, text):
            self.out.links.append({"rel": rel, "text": text, "dir": False, "dangling": True})
            return
        where = text if os.path.isabs(text) else os.path.normpath(os.path.join(os.path.dirname(path),
                                                                               text))
        plain = _platform.plain_path(where)
        if core._under(plain, self.root) or any(core._under(plain, s) for s in self.spellings):
            self.out.not_copied.append({"path": rel, "reason": "target_missing", "text": text})
            return
        self.out.missing_targets.append({"path": rel, "text": text})

    def _boundary(self, ns: str, kind: str, target: str, text: str, pending: list) -> None:
        """A link leading outside, in code point order of its namespace path: its target's bytes are
        copied at its path, a directory's tree once per target (design 6.6, OQ3), and the place it
        reads joins R."""
        try:
            st = os.stat(target)
        except OSError as exc:
            raise _Unreadable(target, exc) from exc
        if kind == "file":
            self.out.boundaries.append({"path": ns, "kind": "file", "text": text, "target_real": target,
                                        "treatment": "copied_as_file"})
            self.out.reach.append({"path": target, "via": ns})
            self._file(ns, target, st)
            return
        first = self._claim(target, ns)
        if first is not None:
            self.out.not_copied.append({"path": ns, "reason": "copied_elsewhere", "text": text,
                                        "at": first})
            return
        self.out.boundaries.append({"path": ns, "kind": "directory", "text": text,
                                    "target_real": target, "treatment": "copied_as_directory"})
        self.out.reach.append({"path": target, "via": ns})
        self.out.dirs.append({"rel": ns, "real": target, "mode": st.st_mode, "mtime_ns": st.st_mtime_ns})
        self._tree(target, ns, pending, in_root=False)


def _dest_rel(rel: str, migration_id: str) -> str:
    """Where the destination keeps a file of the source (design 6.5): lab.json's bytes as this
    migration's source-marker.json, .lab/ as .analytics/, anything else at the same path."""
    if rel == writer.LEGACY_MARKER:
        return f"{MIGRATIONS}/{migration_id}/{SOURCE_MARKER}"
    if rel == LEGACY_STATE_DIR:
        return STATE_DIR
    if rel.startswith(LEGACY_STATE_DIR + "/"):
        return STATE_DIR + rel[len(LEGACY_STATE_DIR):]
    return rel


def _source_rel(rel: str) -> str:
    """The source's path of a destination path a plan names (the inverse of _dest_rel for a folder
    or a file outside this migration's own directory)."""
    if rel == STATE_DIR:
        return LEGACY_STATE_DIR
    if rel.startswith(STATE_DIR + "/"):
        return LEGACY_STATE_DIR + rel[len(STATE_DIR):]
    return rel


def _ignored(rel: str) -> bool:
    """A name a person's tools leave in a destination (OQ16): .venv at the root, __pycache__ and the
    OS's views anywhere, and what is below them."""
    parts = rel.split("/")
    return parts[0] in IGNORED_AT_ROOT or any(p in CACHE_NAMES or p in OS_METADATA for p in parts)


def _walk_destination(dest: Path):
    """Every entry under the destination as (POSIX path, kind), links not followed, a directory
    before what is in it; an ignored name and .analytics/migration/ are given and not gone into."""
    stack = [""]
    while stack:
        rel = stack.pop()
        try:
            with os.scandir(dest / rel if rel else dest) as listing:
                entries = sorted(listing, key=lambda e: e.name)
        except OSError:
            continue
        below = []
        for entry in entries:
            path = _posix(rel, entry.name)
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                yield path, "file"
                continue
            if _platform.is_link(st):
                yield path, "link"
            elif stat.S_ISDIR(st.st_mode):
                yield path, "dir"
                if not _ignored(path) and path != MIGRATIONS:
                    below.append(path)
            else:
                yield path, "file"
        stack.extend(reversed(below))


def _inventory_digest(items) -> str:
    """One sha256 over what a copy holds of the source: each path, its kind and size and its bytes'
    sha256, or its link's text, in code point order (design 6.8's inventory digest)."""
    digest = hashlib.sha256()
    for path, kind, what in sorted(items):
        digest.update(f"{kind}\t{path}\t{what}\n".encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


# --- the destination: what is there ----------------------------------------------------------------

class Judgment:
    """What the destination is for this call (design 6.2 step 1, 6.9, 6.10): `new` (with `clear`, the
    content-less migration directories step 2 left), `resume` (with its ledger), or `committed` (with
    its ledger and the marker's bytes). A refusal is raised instead."""

    def __init__(self, kind: str, *, ledger: Ledger | None = None, clear=(), marker: bytes | None = None,
                 rewritten: bool = False, entries=()):
        self.kind = kind
        self.ledger = ledger
        self.clear = list(clear)
        self.marker = marker
        self.rewritten = rewritten
        self.entries = list(entries)  # what makes a `new` destination not empty (step 3 refuses it)

    @property
    def signature(self) -> tuple:
        ledger = self.ledger
        return (self.kind, ledger.migration_id if ledger else None, len(ledger.lines) if ledger else 0,
                tuple(self.clear), self.marker, tuple(self.entries))

    @property
    def reads_source(self) -> bool:
        """Whether this call reads the source (design 6.2 step 2): a new migration, and a resumed one
        whose ledger has not checked it yet."""
        return self.kind == "new" or (self.kind == "resume" and self.ledger.source_checked is None
                                      and not self.ledger.committed)


# --- the migration ---------------------------------------------------------------------------------

def probe_destination(probe: Path, link: Path) -> dict:
    """What the destination's file system does, by the test file `probe` made already
    (`.probe-Ab\u00e9.tmp`, the accent composed): whether the same file answers to the name with its
    letters' case changed, or with the accent decomposed (it folds case, normalization), and whether
    `link` can be made a symbolic link there (design 6.3 step 2). A file system without inode numbers
    that answers to a variant is taken to fold."""
    st = os.lstat(probe)
    out = {}
    for what, variant in (("case_folds", ".probe-aB\u00e9.tmp"),
                          ("normalization_folds", ".probe-Abe\u0301.tmp")):
        try:
            other = os.lstat(probe.with_name(variant))
            out[what] = _key(other) == _key(st) if st.st_ino else True
        except OSError:
            out[what] = False
    try:
        os.symlink("probe-target", link)
        out["links"] = True
    except (OSError, NotImplementedError):
        out["links"] = False
    return out


class Migration:
    def __init__(self, source, destination, *, origin: Origin, platform=None, on_step=None,
                 free_space=None, trust=None):
        if not isinstance(origin, Origin):
            raise TypeError("a migration is made by an Origin")
        self.origin = origin
        self.platform = platform or core.PLATFORM
        self.files = self.platform.files
        self.step = on_step or _no_step
        self.free_space = free_space
        self.trust = trust or (lambda root: core.trust_project(root, platform=self.platform))
        self.dest_given = os.fspath(destination)
        self.dest = Path(_resolved(destination))
        self.source_given = None if source is None else os.fspath(source)
        self.source: Path | None = None
        if source is not None:
            self.source = Path(core.canonical(core.expand_home(Path(source))))
            if self.source.exists() and _same(self.source, self.dest):
                self.source = None  # called inside the destination itself (design 6.1)
        self.ledger: Ledger | None = None
        self.mdir: Path | None = None
        self.inventory: Inventory | None = None
        self.plan: dict | None = None
        self.leftover: list[str] = []
        self.published: list[str] = []  # paths found published without their `copied` line (F6)
        self.wrote = False  # whether this call wrote a ledger line (then a refusal adds `failed`)
        self._planned: tuple[int, set[str]] | None = None

    # -- entry

    def run(self, dry_run: bool) -> dict:
        if os.path.lexists(self.dest) and not self.dest.is_dir():
            raise _invalid("not_a_directory", f"{self.dest} exists and is not a directory",
                           "give a new or empty folder as the destination", path=str(self.dest))
        judged = self._judge()
        if dry_run:
            return self._dry_run(judged)
        self._checks(judged)
        try:
            self.dest.mkdir(parents=True, exist_ok=True)
            (self.dest / STATE_DIR).mkdir(exist_ok=True)
        except OSError as exc:
            raise _write_failed(self.dest / STATE_DIR, exc) from exc
        with contextlib.ExitStack() as stack:
            try:
                stack.enter_context(self.platform.locks.try_exclusive(self.dest / DEST_LOCK))
            except _platform.LockBusy as exc:
                raise AnalyticsError("destination_busy", f"another migration holds "
                                     f"{self.dest / DEST_LOCK}",
                                     hint="wait for it to end, then run the migration again (on "
                                          "Windows a lock may stay a moment after its process ends)",
                                     details={"lock": str(self.dest / DEST_LOCK)}) from exc
            except _platform.LockUnavailable as exc:
                raise _write_failed(self.dest / DEST_LOCK, exc) from exc
            self.step("dest_locked", {})
            again = self._judge()
            if again.signature != judged.signature:
                self._checks(again)  # what changed before the lock is judged again under it
            return self._act(again)

    # -- the destination's judgment (design 6.2 step 1, 6.9, 6.10): read only

    def _judge(self) -> Judgment:
        if not self.dest.exists():
            return Judgment("new")
        state = writer.marker_state(self.dest)
        if state.state not in (writer.NONE, writer.INCOMPLETE):
            return self._judge_marked(state)
        self._refuse_managed_links()
        try:
            top = sorted(e.name for e in os.scandir(self.dest))
        except OSError as exc:
            raise _invalid("not_a_directory", f"the destination cannot be listed: {exc}",
                           "give a folder this account can read", path=str(self.dest)) from exc
        others = [n for n in top if n not in core.EMPTY_DIRECTORY_NAMES and n != STATE_DIR]
        if STATE_DIR in top and not (self.dest / STATE_DIR).is_dir():
            others.append(STATE_DIR)
        dirs = self._migration_dirs()
        full = [d for d in dirs if not _contentless(d)]
        if len(full) > 1:
            raise _mismatch(f"{self.dest} holds {len(full)} migrations' records",
                            migrations=[d.name for d in full])
        if not full:
            stray = [f"{STATE_DIR}/{n}" for n in self._state_entries()
                     if n not in ("migration", "migration.lock")]
            return Judgment("new", clear=[d.name for d in dirs], entries=sorted(others + stray))
        directory = full[0]
        if len(dirs) > 1:
            raise _mismatch(f"{self.dest} holds migration directories besides {directory.name}",
                            migrations=[d.name for d in dirs])
        try:
            ledger = Ledger.read(directory)
        except LedgerError as exc:
            raise _mismatch(f"the ledger of {directory.name} is not a state a migration leaves: {exc}",
                            migration=directory.name) from exc
        except OSError as exc:
            raise _mismatch(f"the ledger of {directory.name} cannot be read: {exc}",
                            migration=directory.name) from exc
        if ledger.start is None:
            raise _mismatch(f"the ledger of {directory.name} has no start", migration=directory.name)
        self._same_source(ledger)
        return Judgment("resume", ledger=ledger)

    def _managed_links(self) -> list[str]:
        """The places the migration keeps its records at — .analytics/, .analytics/migration/, the
        destination's lock, each migration directory and its ledger — that are a link or a junction
        (a ledger that is not a file too): never followed, since a link there would lead the
        migration's writes elsewhere, into the source among other places (S1). Read only."""
        found = []
        for rel in (STATE_DIR, MIGRATIONS, DEST_LOCK):
            try:
                st = os.lstat(self.dest / rel)
            except OSError:
                continue
            if _platform.is_link(st):
                found.append(rel)
        if found:
            return found
        try:
            with os.scandir(self.dest / MIGRATIONS) as listing:
                entries = sorted(listing, key=lambda e: e.name)
        except OSError:
            return found
        for entry in entries:
            rel = f"{MIGRATIONS}/{entry.name}"
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if _platform.is_link(st):
                found.append(rel)
            elif stat.S_ISDIR(st.st_mode):
                try:
                    ledger = os.lstat(Path(entry.path) / LEDGER)
                except OSError:
                    continue
                if not stat.S_ISREG(ledger.st_mode):
                    found.append(f"{rel}/{LEDGER}")
        return found

    def _refuse_managed_links(self) -> None:
        found = self._managed_links()
        if found:
            raise _invalid("managed_link", f"{self.dest} has a link where a migration keeps its "
                           f"records: {', '.join(found[:5])}",
                           "a migration writes only inside its destination and never follows a "
                           "link there; remove the link, or give a new or empty folder as the "
                           "destination. Nothing was written",
                           paths=found[:20])

    def _migration_dirs(self) -> list[Path]:
        try:
            with os.scandir(self.dest / MIGRATIONS) as listing:
                return sorted((Path(e.path) for e in listing if e.is_dir(follow_symlinks=False)),
                              key=lambda p: p.name)
        except OSError:
            return []

    def _state_entries(self) -> list[str]:
        try:
            return sorted(e.name for e in os.scandir(self.dest / STATE_DIR))
        except OSError:
            return []

    def _same_source(self, ledger: Ledger) -> None:
        """A source given that is not the ledger's is another source's migration (design 6.1, 6.9,
        6.10): never reported as a source that changed."""
        recorded = (ledger.start.get("source") or {}).get("path")
        if self.source is not None and not (recorded and _same(self.source, recorded)):
            raise _invalid("not_empty", f"{self.dest} holds the migration of another source "
                           f"({recorded})", "give the source that migration copies, call it without a "
                           "source to finish it, or use another destination",
                           source=recorded, migration=ledger.migration_id)

    def _judge_marked(self, state: writer.MarkerState) -> Judgment:
        """design 6.9: a destination with a marker is judged by the marker first."""
        if state.state in (writer.CONFLICT, writer.INVALID, writer.UNKNOWN):
            raise writer.refusal(state, "migrate", writer_version=core.KIT_VERSION, writable=frozenset())
        if state.state == writer.LEGACY:
            raise _invalid("not_empty", f"{self.dest} is a project of the older format (lab.json)",
                           "give a new or empty folder as the destination", marker=writer.LEGACY_MARKER)
        field = state.value.get("migration")
        ledger = None
        if isinstance(field, dict) and core._record_path(field.get("id")) and "/" not in field["id"]:
            directory = self.dest / MIGRATIONS / field["id"]
            if (directory / LEDGER).is_file():
                with contextlib.suppress(LedgerError, OSError):
                    ledger = Ledger.read(directory)
        if ledger is None or ledger.start is None:
            raise _invalid("not_empty", f"{self.dest} is an analytics project that no migration "
                           "recorded there made", "give a new or empty folder as the destination",
                           marker=writer.MARKER)
        self._same_source(ledger)
        if field.get("report") != f"{MIGRATIONS}/{field['id']}/report.json":
            # the marker, its migration and the report it binds belong together (design 2.4)
            raise _mismatch(f"{self.dest}'s marker names the report {field.get('report')!r}, not its "
                            f"migration {field['id']}'s", reason="invalid", migration=field["id"])
        self._one_migration(field)
        data = (self.dest / writer.MARKER).read_bytes()
        if ledger.committed or (ledger.marker and _sha(data) == ledger.marker.get("sha256")):
            return Judgment("committed", ledger=ledger, marker=data)
        if ledger.marker is not None:
            written = _loads(ledger.marker.get("text")) or {}
            if state.value.get("project_id") == ledger.project_id and \
                    state.value.get("migration") == written.get("migration"):
                return Judgment("committed", ledger=ledger, marker=data, rewritten=True)
        raise _mismatch(f"{self.dest}'s marker is not the one its migration published",
                        migration=ledger.migration_id)

    def _one_migration(self, field: dict) -> None:
        """design 2.4, in the reader's order (core._migration_mapping_of): the report whose bytes the
        marker binds, and the mapping whose bytes that report binds, are the marker's migration's —
        its id in each, the mapping in its directory — else destination_mismatch (invalid). A report or
        a mapping whose bytes are not the bound ones is not judged here: the call says it differs, as
        the reader says `report_changed` or `mapping_changed`."""
        mid, own = field["id"], f"{MIGRATIONS}/{field['id']}"

        def bound(rel: str, sha256) -> object:
            try:
                data = (self.dest / rel).read_bytes()
            except OSError:
                return None
            return _loads(data) if _sha(data) == sha256 else None

        def refuse(what: str) -> AnalyticsError:
            return _mismatch(f"{self.dest}'s marker names migration {mid}, but {what}", reason="invalid",
                             migration=mid)

        report = bound(field["report"], field.get("report_sha256"))
        if not isinstance(report, dict):
            return
        if report.get("schema") != FAMILY or report.get("record") != "report" \
                or report.get("migration_id") != mid:
            raise refuse(f"the report it binds is migration {report.get('migration_id')!r}'s")
        mapping = report.get("mapping")
        path = mapping.get("path") if isinstance(mapping, dict) else None
        if path != f"{own}/mapping.json":
            raise refuse(f"the report it binds names the mapping {path!r}")
        value = bound(path, mapping.get("sha256"))
        if isinstance(value, dict) and (value.get("schema") != FAMILY or value.get("record") != "mapping"
                                        or value.get("migration_id") != mid):
            raise refuse(f"the mapping its report binds is migration {value.get('migration_id')!r}'s")

    # -- the checks before anything is written (design 6.2)

    def _checks(self, judged: Judgment) -> None:
        if judged.kind == "committed":
            return
        if judged.reads_source:
            self._source_checks()
        self._destination_checks(judged)

    def _require_source(self) -> Path:
        if self.source is None:
            if self.source_given is not None:
                state = writer.marker_state(self.source_given).state
                raise AnalyticsError("source_not_legacy", f"{self.source_given} is the destination "
                                     "itself, not a project to migrate",
                                     hint="run the migration from the lab.json project it copies",
                                     details={"state": state})
            raise AnalyticsError("no_project", "no source project was given",
                                 hint="run the migration from the lab.json project it copies; only a "
                                      "destination whose source was checked already goes on "
                                      "without one")
        return self.source

    def _source_checks(self) -> Inventory:
        source = self._require_source()
        if not source.is_dir():
            raise AnalyticsError("no_project", f"{source} is not a folder",
                                 hint="give the lab.json project to migrate")
        state = writer.marker_state(source)
        if state.state != writer.LEGACY:
            raise AnalyticsError("source_not_legacy", f"{source} is not a project of the older format "
                                 f"(lab.json): its markers say {state.state}",
                                 hint="a migration copies a lab.json project; this one needs none, or "
                                      "its markers need a person's look first",
                                 details={"state": state.state})
        self.inventory = self._walk()
        for problem in self.inventory.refusals():
            raise problem
        self._overlap(self.inventory)
        return self.inventory

    def _spellings(self) -> list[str]:
        out = [str(self.source)]
        if self.source_given is not None:
            given = os.path.abspath(os.fspath(core.expand_home(Path(self.source_given))))
            out += [given, os.path.realpath(given)]
        return list(dict.fromkeys(_platform.plain_path(s) for s in out))

    def _walk(self, *, hashed: bool = False) -> Inventory:
        try:
            return _Walker(str(self.source), self._spellings(), hashed=hashed).run()
        except _Unreadable as problem:
            raise _unreadable_error(problem) from problem

    def _overlap(self, inventory: Inventory) -> None:
        """The destination is none of R's places, inside none, and above none (design 6.2, M1): told
        by the files they lead to through every ancestor, so another case, normalization, link or
        junction of one place is that place."""
        dest = str(self.dest)
        existing = _existing(dest)
        for place in [{"path": str(self.source), "via": None}, *inventory.reach]:
            r = place["path"]
            hit = any(_same(a, r) for a in (existing, *_ancestors(existing)))
            if not hit and existing == dest:
                hit = any(_same(a, existing) for a in (r, *_ancestors(r)))
            if hit:
                raise _invalid("overlaps_source", f"the destination {dest} overlaps a place the "
                               f"migration reads: {r}",
                               "give a destination outside the source and outside every place its "
                               "links lead to: a migration never writes where it reads",
                               **{"with": place})

    def _destination_checks(self, judged: Judgment) -> None:
        if judged.entries:
            raise _invalid("not_empty", f"{self.dest} is not empty",
                           "give a new or empty folder as the destination; nothing was written",
                           entries=judged.entries[:20])
        dest = str(self.dest)
        existing = _existing(dest)
        # The rule init follows too (writer.project_above, OQ9).
        above = writer.project_above(dest)
        if above is not None:
            raise _invalid("inside_project", f"the destination {dest} is inside the project {above}",
                           "give a destination outside every analytics project: an older kit run there "
                           "would write to the project above it", project=above)
        needed = self._needed(judged)
        if needed is not None:
            free = self._free(existing)
            want = needed + int(needed * SPACE_SHARE) + SPACE_MARGIN
            if free < want:
                raise AnalyticsError("insufficient_space", f"the destination's disk has {free:,} bytes "
                                     f"free; the copy needs {want:,} with its margin",
                                     hint="free space or choose another disk; nothing was written",
                                     details={"free": free, "needed": needed, "with_margin": want})

    def _free(self, existing: str) -> int:
        if self.free_space is not None:
            return int(self.free_space(existing) if callable(self.free_space) else self.free_space)
        return shutil.disk_usage(existing).free

    def _needed(self, judged: Judgment) -> int | None:
        """The bytes still to copy (design 6.2 step 3): the source's for a new migration, those of the
        files not copied yet for one resumed before its source check, none after it."""
        if not judged.reads_source:
            return None
        inventory = self.inventory or self._walk()
        mid = judged.ledger.migration_id if judged.ledger else ""
        copied = judged.ledger.copied if judged.ledger else {}
        return sum(f["size"] for f in inventory.files if _dest_rel(f["rel"], mid) not in copied)

    # -- acting under the destination's lock

    def _act(self, judged: Judgment) -> dict:
        if judged.kind == "committed":
            return self._already(judged)
        if judged.kind == "new":
            for name in judged.clear:  # step 2's own, with nothing of anyone's in it (design 6.10)
                directory = self.dest / MIGRATIONS / name
                with contextlib.suppress(FileNotFoundError):
                    _remove_own(directory / LEDGER)
                os.rmdir(directory)
            return self._start()
        self.ledger, self.mdir = judged.ledger, judged.ledger.directory
        try:
            return self._resume(judged.ledger)
        except AnalyticsError as exc:
            self._failed(exc)
            raise

    def _line(self, step: str, **fields) -> None:
        """A ledger line, appended before what it names is made (design 6.3)."""
        value = {"step": step, "at": _now(), **fields}
        self.step(f"ledger:{step}", value)
        try:
            append_line(self.mdir / LEDGER, value)
        except OSError as exc:
            raise _write_failed(self.mdir / LEDGER, exc) from exc
        self.wrote = True
        self.ledger.add(value)
        self.step(step, value)

    def _failed(self, exc: AnalyticsError) -> None:
        """A `failed` line: the attempt's result, which closes no state (design 6.8) — written only
        by an attempt that wrote a line; a refusal the judgment gives (design 6.10) writes nothing."""
        if not self.wrote or self.mdir is None or self.ledger is None or self.ledger.start is None:
            return
        details = exc.details if isinstance(exc.details, dict) else {}
        keep = {k: details[k] for k in ("reason", "path", "paths", "created") if k in details}
        with contextlib.suppress(OSError):
            append_line(self.mdir / LEDGER, {"step": "failed", "at": _now(), "code": exc.code, **keep})

    # -- step 2: the migration's directory, its first line and the name probe

    def _start(self) -> dict:
        source = self._require_source()
        migration_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4)
        self.mdir = self.dest / MIGRATIONS / migration_id
        try:
            self.mdir.mkdir(parents=True)
        except OSError as exc:
            raise _write_failed(self.mdir, exc) from exc
        self.step("migration_dir", {"id": migration_id})
        self.ledger = Ledger(b"", self.mdir)
        inventory = self.inventory or self._walk()
        try:
            self._line("start", migration_id=migration_id, kit_version=core.KIT_VERSION,
                       origin=self._origin(),
                       source={"path": str(source), "given": self.source_given,
                               "spellings": self._spellings()},
                       destination={"path": str(self.dest), "given": self.dest_given},
                       reach=[{"path": str(source), "via": None}, *inventory.reach],
                       locks=[{"path": f"{LEGACY_STATE_DIR}/{name}",
                               "existed": os.path.lexists(source / LEGACY_STATE_DIR / name)}
                              for name in SOURCE_LOCKS],
                       probe=PROBE, probe_link=PROBE_LINK, probe_publish=PROBE_PUBLISH)
            return self._from_probe()
        except AnalyticsError as exc:
            self._failed(exc)
            raise

    def _origin(self) -> dict:
        o = self.origin
        return {"actor": o.actor, "actor_basis": o.actor_basis, "means": o.means,
                **({"session": o.session} if o.session else {})}

    def _from_probe(self) -> dict:
        """Step 2's probe (design 6.2, 6.3): whether the destination folds case and Unicode
        normalization, whether it holds a symbolic link, and how it publishes the marker
        (_publication), judged by a test file, a test link and a test name the `start` line named,
        removed after."""
        start = self.ledger.start
        probe = self.mdir / start["probe"]
        link = self.mdir / start["probe_link"]
        published = self.mdir / start.get("probe_publish", PROBE_PUBLISH)
        for leftover in (probe, link, published):
            if os.path.lexists(leftover):
                _remove_own(leftover)
        with open(probe, "xb"):
            pass
        self.step("probe_made", {})
        try:
            folds = probe_destination(probe, link)
            folds["publish"] = self._publication(probe, published)
        finally:
            for made in (probe, published):
                if os.path.lexists(made):
                    _remove_own(made)
            if os.path.lexists(link):
                os.unlink(link)
        self._line("probed", **folds)
        return self._from_locks()

    def _publication(self, probe: Path, published: Path) -> str:
        """How the marker is published here (owner's decision of 2026-10-08), tried on the probe file,
        which then has the name `published`: the port's publish_new where the file system links, else
        its rename_new — a rename that refuses a taken name — else destination_invalid
        (no_exclusive_rename), before any copy: the marker is never written in place."""
        try:
            self.files.publish_new(probe, published)
            return "publish_new"
        except _platform.LinkUnsupported:
            pass
        except OSError as exc:
            raise _write_failed(published, exc) from exc
        try:
            self.files.rename_new(probe, published)
            return "rename_new"
        except _platform.RenameUnsupported as exc:
            raise _no_exclusive_rename(self.dest, exc, copied=False) from exc
        except OSError as exc:
            raise _write_failed(published, exc) from exc

    # -- steps 3-7: under the source's locks, then 8-12

    def _from_locks(self) -> dict:
        source = self._require_source()
        state_dir = source / LEGACY_STATE_DIR
        with contextlib.ExitStack() as stack:
            if not state_dir.is_dir():
                locked = {"locks": "not_taken", "reason": "no_state_dir"}
            else:
                taken: list[dict] = []
                for name in SOURCE_LOCKS:
                    try:
                        created = stack.enter_context(self.platform.locks.try_exclusive(state_dir / name))
                    except _platform.LockBusy as exc:
                        error = self._source_busy(name)
                        error.details["created"] = taken
                        if any(t.get("created") for t in taken):
                            self.wrote = True  # a lock file made now is named, by the `failed` line
                        raise error from exc
                    except _platform.LockUnavailable as exc:
                        cause = exc.__cause__ or exc
                        taken.append({"path": f"{LEGACY_STATE_DIR}/{name}",
                                      "not_taken": f"{type(cause).__name__}: {cause}"})
                        continue
                    taken.append({"path": f"{LEGACY_STATE_DIR}/{name}", "created": created})
                locked = ({"locks": "not_taken", "reason": taken[0]["not_taken"]}
                          if all("not_taken" in t for t in taken) else {"locks": taken})
            made = isinstance(locked["locks"], list) and any(t.get("created") for t in locked["locks"])
            if not self.ledger.plans or made:
                self._line("locked", **locked)
                locked = None
            self._under_locks(locked)
        return self._after_check()

    def _source_busy(self, name: str) -> AnalyticsError:
        running = []
        for run_json in sorted((self.source / "runs").glob("*/run.json")):
            value = core.read_json(run_json, {})
            if isinstance(value, dict) and value.get("status") == "running":
                running.append({"id": run_json.parent.name, "pid": value.get("pid")})
        return AnalyticsError("source_busy", f"the source's {LEGACY_STATE_DIR}/{name} is held by another "
                              "process" + (" (a run)" if name == "run.lock" else ""),
                              hint="wait for the run or the write to end, then run the migration again "
                                   "(an older aelix session holds the journal lock only while it "
                                   "writes; on Windows a lock may stay a moment after its process ends)",
                              details={"lock": f"{LEGACY_STATE_DIR}/{name}",
                                       **({"running": running} if running else {})})

    def _under_locks(self, locked: dict | None) -> None:
        """Steps 4-7: walk the source again, check it, accept what a resumed migration copied already
        (6.10 ②), plan, copy, and check the source again before the locks go. A resumed migration
        that made no lock file now writes its `locked` line once ② holds (`locked`), so a refusal of
        ② leaves the destination as it was (design 11.2 T6)."""
        inventory = self._walk()
        for problem in inventory.refusals():
            raise problem
        self._overlap(inventory)
        self.inventory = inventory
        accepted = self._accept_copies(inventory)
        if locked is not None:
            self._line("locked", **locked)
        self.plan = self._make_plan(inventory)
        self._record_accepted(accepted)
        self._copy()
        self._recheck()

    # -- step 5: the plan

    def _layout(self, inventory: Inventory, links_ok: bool) -> dict:
        """What a plan copies of one walk (design 6.5, 6.6): the entries (files and links, by their
        destination path), the folders, what is not copied and the links copied as their target's
        bytes. The plan is made of it, and step 7 makes it again of the walk under the same locks to
        compare the two (S5)."""
        mid = self.ledger.migration_id
        entries, not_copied, links = [], list(inventory.not_copied), list(inventory.boundaries)
        for item in inventory.files:
            entries.append({"from": item["rel"], "to": _dest_rel(item["rel"], mid), "kind": "file",
                            "real": item["real"], "bytes": item["size"], "key": item["key"],
                            "mode": stat.S_IMODE(item["mode"]), "mtime_ns": item["mtime_ns"],
                            "atime_ns": item["atime_ns"]})
        for link in inventory.links:
            if links_ok:
                entries.append({"from": link["rel"], "to": _dest_rel(link["rel"], mid), "kind": "link",
                                "text": link["text"], "dir": link["dir"]})
                continue
            # A destination that cannot hold a link (Windows without the privilege): a file's bytes
            # are copied, a directory is left out with a warning (design 6.6).
            real = os.path.join(str(self.source), *link["rel"].split("/"))
            try:
                st = os.stat(real)
            except OSError:
                st = None
            if st is not None and stat.S_ISREG(st.st_mode):
                target = core.canonical(real)
                links.append({"path": link["rel"], "kind": "file", "text": link["text"],
                              "target_real": target, "treatment": "copied_as_file"})
                entries.append({"from": link["rel"], "to": _dest_rel(link["rel"], mid), "kind": "file",
                                "real": target, "bytes": st.st_size, "key": _key(st),
                                "mode": stat.S_IMODE(st.st_mode), "mtime_ns": st.st_mtime_ns,
                                "atime_ns": st.st_atime_ns})
            else:
                not_copied.append({"path": link["rel"], "reason": "link_not_made", "text": link["text"]})
        entries.sort(key=lambda e: e["to"])
        dirs = sorted({_dest_rel(d["rel"], mid) for d in inventory.dirs} - {STATE_DIR})
        return {"entries": entries, "dirs": dirs, "not_copied": not_copied, "links": links}

    def _make_plan(self, inventory: Inventory) -> dict:
        ledger = self.ledger
        probed = ledger.probed or {}
        folds = (bool(probed.get("case_folds")), bool(probed.get("normalization_folds")))
        links_ok = bool(probed.get("links"))
        mid = ledger.migration_id
        layout = self._layout(inventory, links_ok)
        entries, dirs = layout["entries"], layout["dirs"]
        not_copied, links = layout["not_copied"], layout["links"]
        if folds[0] or folds[1]:  # names the destination folds together (D26)
            seen: dict[str, str] = {}
            pairs = []
            for rel in [*dirs, *(e["to"] for e in entries)]:
                key = "/".join(_folded(p, *folds) for p in rel.split("/"))
                if key in seen and seen[key] != rel:
                    pairs.append([seen[key], rel])
                seen.setdefault(key, rel)
            if pairs:
                raise _invalid("name_collision", "the destination's file system takes names that "
                               "differ only in case or normalization for one name, and the source has "
                               "such names", "rename one of each pair in the source, or give a "
                               "destination on a file system that tells them apart",
                               pairs=pairs, folds={"case": folds[0], "normalization": folds[1]})
        # Temporary names: short, in the file's own directory, apart from every name the plan puts
        # there and every temporary name a line named before (design 6.4).
        taken: dict[str, set[str]] = {}
        for rel in [*dirs, *(e["to"] for e in entries), *ledger.temps]:
            taken.setdefault(_parent(rel), set()).add(_folded(_name(rel), *folds))
        for entry in entries:
            if entry["kind"] != "file":
                continue
            names = taken.setdefault(_parent(entry["to"]), set())
            stem = _sha(entry["to"].encode("utf-8", "surrogateescape"))[:8]
            n = 0
            while _folded(f".mig-{stem}-{n}.part", *folds) in names:
                n += 1
            entry["temp"] = f".mig-{stem}-{n}.part"
            names.add(_folded(entry["temp"], *folds))
        warnings = list(inventory.warnings)
        for entry in entries:
            full = os.path.join(str(self.dest), *entry["to"].split("/"))
            if len(full) > LONG_PATH:
                warnings.append({"path": entry["to"], "reason": "long_path", "length": len(full)})
            bad = [p for p in entry["from"].split("/") if not _platform.portable_name(p)]
            if bad:
                warnings.append({"path": entry["from"], "reason": "unportable_name", "name": bad[0]})
        project_id = ledger.project_id or writer.new_project_id()
        transforms = [{"path": ".gitignore", "kind": "gitignore_append"}] if any(
            e["to"] == ".gitignore" and e["kind"] == "file" for e in entries) else []
        plan = {"schema": FAMILY, "record": "plan", "migration_id": mid, "project_id": project_id,
                "ledger": LEDGER, "source": str(self.source), "destination": str(self.dest),
                "folds": {"case": folds[0], "normalization": folds[1]}, "links_made": links_ok,
                "directories": dirs, "files": entries, "bytes": sum(e.get("bytes", 0) for e in entries),
                "excluded": inventory.excluded_list(),
                "not_copied": sorted(not_copied, key=lambda n: n["path"]),
                "links": sorted(links, key=lambda b: b["path"]), "reach": inventory.reach,
                "warnings": warnings, "transforms": transforms}
        name = "plan.json" if not ledger.plans else f"plan-{len(ledger.plans) + 1}.json"
        data = _json_bytes(plan)
        self._write_own(self.mdir / name, data)
        self.step("plan_written", {"file": name})
        self._line("plan", file=name, sha256=_sha(data), project_id=project_id, files=len(entries),
                   bytes=plan["bytes"])
        return plan

    def _write_own(self, path: Path, data: bytes) -> None:
        """A file of this migration's own directory, written whole by a replace (design 6.3: its
        directory is this migration's, and its files may be written again). Its temporary file is
        made new where nothing is — a leftover one removed first, a link as itself — so no write
        goes through a link (S1); the replace puts the file at the name, a link there included."""
        self._own_dir(path.parent)
        temp = path.with_name(path.name + ".tmp")
        try:
            with contextlib.suppress(FileNotFoundError):
                _remove_own(temp)
            with open(_create(temp), "wb", closefd=True) as fh:
                fh.write(data)
                self.files.flush_handle(fh)
            self.files.replace(temp, path)
        except OSError as exc:
            raise _write_failed(path, exc) from exc

    def _own_dir(self, directory: Path) -> None:
        """A folder in this migration's own directory (proposals/ and below), made where it is
        missing; a link or a file there is refused, never followed (S1)."""
        rel = directory.relative_to(self.mdir).parts
        here = self.mdir
        for part in ("", *rel):
            here = here / part if part else here
            try:
                if part:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(here)
                st = os.lstat(here)
            except OSError as exc:
                raise _write_failed(here, exc) from exc
            if _platform.is_link(st) or not stat.S_ISDIR(st.st_mode):
                raise _invalid("managed_link", f"{here} is not a folder of this migration's own",
                               "a migration never follows a link where it keeps its records; remove "
                               "it and run the migration again",
                               paths=[here.relative_to(self.dest).as_posix()])

    def _planned_paths(self) -> set[str]:
        """Every destination path a plan the ledger binds names, its files and folders (a plan that
        cannot be read names none here: the steps that judge copies read them strictly)."""
        count = len(self.ledger.plans)
        if self._planned is None or self._planned[0] != count:
            out: set[str] = set()
            for line in self.ledger.plans:
                try:
                    data = (self.mdir / line.get("file", "plan.json")).read_bytes()
                except OSError:
                    continue
                plan = _loads(data) if _sha(data) == line.get("sha256") else None
                if isinstance(plan, dict):
                    out.update(e["to"] for e in plan.get("files") or [])
                    out.update(plan.get("directories") or [])
            self._planned = (count, out)
        return self._planned[1]

    def _removable(self, temp: str) -> bool:
        """Whether a temporary file a line named may be removed: this migration's (not one found
        there when it was to be made) and not a path a plan copies (S6)."""
        return self.ledger.owns(temp) and temp not in self._planned_paths()

    def _remove_temp(self, path: Path) -> None:
        """Remove a temporary file of this migration's, in a folder the copy made read-only too (its
        mode made writable for the moment and put back, design 6.4); a failure is
        destination_write_failed."""
        parent = path.parent
        mode = None
        try:
            if parent != self.dest and not os.access(parent, os.W_OK):
                st = os.lstat(parent)
                if not _platform.is_link(st) and stat.S_ISDIR(st.st_mode):
                    mode = stat.S_IMODE(st.st_mode)
                    os.chmod(parent, mode | stat.S_IWUSR)
            try:
                _remove_own(path)
            finally:
                if mode is not None:
                    os.chmod(parent, mode)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise _write_failed(path, exc) from exc

    def _remove_old_temps(self, rel: str) -> None:
        """design 6.10: a path copied again, or found published and accepted, has its earlier
        temporary files removed once it is published — before the copy puts its folder's mode back."""
        for temp in self.ledger.begun_temps.get(rel, []):
            temp_rel = _posix(_parent(rel), temp)
            if self._removable(temp_rel) and os.path.lexists(self.dest / temp_rel):
                self._remove_temp(self.dest / temp_rel)

    def _root_temp(self, prefix: str, n: int) -> str:
        """A temporary name at the destination's root for the .gitignore transform or the marker,
        apart from every name a plan puts there and every temporary name a line named, as the
        destination folds names (design 6.3 step 5, 6.4)."""
        folds = (bool(self.plan["folds"]["case"]), bool(self.plan["folds"]["normalization"])) \
            if self.plan else (True, True)
        taken = {_folded(rel, *folds) for rel in (*self._planned_paths(), *self.ledger.temps,
                                                  writer.MARKER, writer.LEGACY_MARKER)
                 if "/" not in rel}
        while _folded(f"{prefix}{n}.part", *folds) in taken:
            n += 1
        return f"{prefix}{n}.part"

    def _plans(self) -> list[dict]:
        """Every plan the ledger's lines bind, each as its line bound it (a plan file is not written
        again once a line names it)."""
        out = []
        for line in self.ledger.plans:
            path = self.mdir / line.get("file", "plan.json")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise _mismatch(f"the plan {path.name} cannot be read: {exc}", path=path.name) from exc
            if _sha(data) != line.get("sha256"):
                raise _mismatch(f"the plan {path.name} is not the one its ledger line binds",
                                path=path.name)
            out.append(json.loads(data))
        return out

    # -- step 6: the copy

    def _copy(self) -> None:
        for rel in self.plan["directories"]:
            path = self.dest / rel
            try:
                path.mkdir(parents=True, exist_ok=True)
                st = os.lstat(path)
            except FileExistsError:
                raise _occupied(rel) from None
            except OSError as exc:
                raise _write_failed(path, exc) from exc
            if _platform.is_link(st) or not stat.S_ISDIR(st.st_mode):
                raise _occupied(rel)  # a link made there meanwhile would lead the copies elsewhere
        for entry in self.plan["files"]:
            if entry["to"] in self.ledger.copied:
                continue
            try:
                if entry["kind"] == "link":
                    self._make_link(entry)
                else:
                    self._copy_file(entry)
            except _SourceChanged:
                raise _changed([entry["from"]]) from None
            except _Unreadable as problem:
                raise _unreadable_error(problem) from problem
        self._directory_times()

    def _make_link(self, entry: dict) -> None:
        final = self.dest / entry["to"]
        self._line("begin", path=entry["to"], link=entry["text"])
        try:
            os.symlink(entry["text"], final, target_is_directory=entry["dir"])
        except FileExistsError:
            raise _occupied(entry["to"]) from None
        except OSError as exc:
            raise _write_failed(final, exc) from exc
        self._line("copied", path=entry["to"], link=entry["text"])

    def _copy_file(self, entry: dict) -> None:
        final = self.dest / entry["to"]
        temp = final.with_name(entry["temp"])
        self._writable(final.parent)
        self._line("begin", path=entry["to"], temp=entry["temp"], bytes=entry["bytes"], key=entry["key"])
        fd = _open_source(entry["real"], entry["key"])
        with open(fd, "rb", closefd=True) as src:
            try:
                out = open(_create(temp), "wb", closefd=True)  # O_EXCL: never a file that is there
            except FileExistsError:
                # Something else made the name the `begin` line gave: not this migration's, never
                # removed (the `failed` line says so, S6), and refused where it is found again.
                raise _occupied(_posix(_parent(entry["to"]), entry["temp"])) from None
            except OSError as exc:
                raise _write_failed(temp, exc) from exc
            digest, size = hashlib.sha256(), 0
            with out:
                while True:
                    try:
                        block = src.read(CHUNK)
                    except OSError as exc:
                        raise _Unreadable(entry["real"], exc) from exc
                    if not block:
                        break
                    digest.update(block)
                    size += len(block)
                    try:
                        out.write(block)
                    except OSError as exc:
                        raise _write_failed(temp, exc) from exc
                    self.step("temp_partial", {"path": entry["to"], "bytes": size})
                try:
                    self.files.flush_handle(out)
                except OSError as exc:
                    raise _write_failed(temp, exc) from exc
        self.step("temp_full", {"path": entry["to"], "temp": str(temp)})
        self._publish(temp, final, entry["to"])
        self.step("published", {"path": entry["to"]})
        with contextlib.suppress(OSError):
            os.utime(final, ns=(entry["atime_ns"], entry["mtime_ns"]))
            os.chmod(final, entry["mode"])
        self._line("copied", path=entry["to"], bytes=size, sha256=digest.hexdigest())
        self._remove_old_temps(entry["to"])

    def _writable(self, directory: Path) -> None:
        """A planned directory this migration made writable again for a file it still copies there
        (the source's permissions go back on when the copy ends). A link there is not followed."""
        if directory != self.dest and not os.access(directory, os.W_OK):
            with contextlib.suppress(OSError):
                st = os.lstat(directory)
                if not _platform.is_link(st) and stat.S_ISDIR(st.st_mode):
                    os.chmod(directory, stat.S_IMODE(st.st_mode) | stat.S_IWUSR)

    def _publish(self, temp: Path, final: Path, rel: str) -> None:
        """The create-if-absent publication (design 6.4): the port's publish_new, or where the file
        system has no links the direct write its ledger names first (6.12, R20)."""
        try:
            self.files.publish_new(temp, final)
            return
        except FileExistsError:
            raise _occupied(rel) from None
        except _platform.LinkUnsupported:
            pass
        except OSError as exc:
            raise _write_failed(final, exc) from exc
        self._line("direct_intent", path=rel)
        try:
            fd = _create(final)
        except FileExistsError:
            raise _occupied(rel) from None
        except OSError as exc:
            raise _write_failed(final, exc) from exc
        try:
            with open(fd, "wb", closefd=True) as out:
                self.step("direct_created", {"path": rel})
                self._line("direct", path=rel, key=_key(os.fstat(out.fileno())))
                with open(temp, "rb") as src:
                    shutil.copyfileobj(src, out, CHUNK)
                self.files.flush_handle(out)
            _remove_own(temp)
        except OSError as exc:
            raise _write_failed(final, exc) from exc

    def _directory_times(self) -> None:
        """A directory's permissions and times after its files (design 6.4), but the root's and
        .analytics/'s, where the migration and the kit after it write."""
        source_dirs = {_dest_rel(d["rel"], self.ledger.migration_id): d for d in self.inventory.dirs}
        for rel in sorted(self.plan["directories"], key=lambda r: (-r.count("/"), r)):
            d = source_dirs.get(rel)
            if d is None or rel == STATE_DIR or rel.startswith(MIGRATIONS):
                continue
            with contextlib.suppress(OSError):
                if _platform.is_link(os.lstat(self.dest / rel)):
                    continue  # never followed: verification refuses what is there (design 6.3)
                os.utime(self.dest / rel, ns=(d["mtime_ns"], d["mtime_ns"]))
                os.chmod(self.dest / rel, stat.S_IMODE(d["mode"]))

    # -- step 7: the source checked again

    def _recheck(self) -> None:
        """Step 7 (S5): the source walked again under the same locks, every file hashed, and what a
        plan would copy of it compared with the plan the copies follow — each entry (its kind, its
        bytes or its link's text), every entry and folder the plan lacks or has more, the links
        copied as their target's bytes (path, text, target), what is not copied and why — and what
        would refuse a migration now (a link's target gone, a nested project, a name). Any
        difference is source_changed with those paths."""
        self.step("rechecking", {})
        again = self._walk(hashed=True)
        layout = self._layout(again, bool(self.plan["links_made"]))
        planned = {e["from"]: e for e in self.plan["files"]}
        found = {e["from"]: e for e in layout["entries"]}
        hashed = {f["rel"]: f for f in again.files}
        changed, now = [], {}
        for entry in self.plan["files"]:
            rel = entry["from"]
            there = found.get(rel)
            if there is None or there["kind"] != entry["kind"]:
                now[rel] = None  # gone from the source, or no longer what the plan copied it as
            elif entry["kind"] == "link":
                now[rel] = there["text"]
            elif rel in hashed:
                item = hashed[rel]
                now[rel] = f"{item['size']}:{item['sha256']}" if item.get("sha256") else None
            else:  # a link this destination cannot hold, copied as its file's bytes
                try:
                    now[rel] = "{1}:{0}".format(*_hash_source(there["real"], there["key"]))
                except (_SourceChanged, _Unreadable):
                    now[rel] = None
            if now[rel] is None or now[rel] != self._copied_what(entry):
                changed.append(rel)
        changed += [rel for rel in found if rel not in planned]

        def boundary(b):
            return b["path"], b["kind"], b["text"], b["target_real"], b["treatment"]

        changed += [b[0] for b in {boundary(b) for b in self.plan["links"]} ^
                    {boundary(b) for b in layout["links"]}]
        changed += [_source_rel(d) for d in set(self.plan["directories"]) ^ set(layout["dirs"])]
        changed += [p for p, _ in {(n["path"], n["reason"]) for n in self.plan["not_copied"]} ^
                    {(n["path"], n["reason"]) for n in layout["not_copied"]}]
        changed += [m["path"] for m in again.missing_targets] + list(again.nested)
        changed += [n["path"] for n in again.not_utf8] + [pair[0] for pair in again.collisions]
        changed += [s["path"] for s in again.state_not_folder]
        if changed:
            raise _changed(changed)
        journal_data = self._dest_bytes("journal.jsonl")
        journal = relations.Journal(journal_data)
        root_from, spelled = self._root_spellings()
        copied_items = [(e["from"], e["kind"], self._copied_what(e)) for e in self.plan["files"]]
        now_items = [(e["from"], e["kind"], now[e["from"]]) for e in self.plan["files"]]
        self._line("source_checked", inventory={"before": _inventory_digest(copied_items),
                                                "after": _inventory_digest(now_items)},
                   source_checked_at=_now(),
                   journal={"bytes": len(journal_data), "lines": journal.lines,
                            "last_seq": journal.as_of_seq, "damaged": len(journal.damaged),
                            "recovered": len(journal.recovered), "unsupported": len(journal.unsupported)},
                   marker_sha256=_sha(self._dest_bytes(_dest_rel(writer.LEGACY_MARKER,
                                                                 self.ledger.migration_id))),
                   root_from=root_from, links=spelled)

    def _copied_what(self, entry: dict) -> str:
        line = self.ledger.copied.get(entry["to"]) or {}
        return line["link"] if "link" in line else f"{line.get('bytes')}:{line.get('sha256')}"

    def _dest_bytes(self, rel: str) -> bytes:
        try:
            return (self.dest / rel).read_bytes()
        except FileNotFoundError:
            return b""

    def _root_spellings(self) -> tuple[list[str], list[dict]]:
        """The spellings of the source's root and of each copied link's target (design 6.7): the
        real path, the spelling given, and every prefix of an absolute path the source's run and
        import records hold that leads to the same place, decided here under the source's locks so a
        resumed migration never reads the source for them."""
        root = str(self.source)
        root_from = list(self._spellings())
        boundaries = list(self.plan["links"])
        targets = {b["path"]: list(dict.fromkeys(
            [b["target_real"], *([b["text"]] if os.path.isabs(b["text"]) else [])]))
            for b in boundaries}
        for path in sorted({_platform.plain_path(p) for p in self._recorded_paths()}):
            for prefix in (path, *_ancestors(path)):
                if _same(prefix, root):
                    if prefix not in root_from:
                        root_from.append(prefix)
                    break
                hit = next((b for b in boundaries if not core._under(b["target_real"], root)
                            and _same(prefix, b["target_real"])), None)
                if hit is not None:
                    if prefix not in targets[hit["path"]]:
                        targets[hit["path"]].append(prefix)
                    break
        return root_from, [{"path": b["path"], "target_spellings": targets[b["path"]]}
                           for b in boundaries]

    def _recorded_paths(self):
        """Every absolute path the source's run and import records hold (read from their copies,
        which have the same bytes)."""
        for run_json in sorted((self.dest / "runs").glob("*/run.json")):
            value = core.read_json(run_json, {})
            if not isinstance(value, dict):
                continue
            script = value.get("script") if isinstance(value.get("script"), dict) else {}
            for entry in (*_dicts(value.get("inputs")), *_dicts(value.get("outputs")), script):
                path = entry.get("path")
                if isinstance(path, str) and os.path.isabs(path):
                    yield path
        for meta in sorted((self.dest / "data").glob("*/*.meta.json")):
            value = core.read_json(meta, {})
            source = value.get("source") if isinstance(value, dict) else None
            path = source.get("path") if isinstance(source, dict) else None
            if isinstance(path, str) and os.path.isabs(path):
                yield path

    # -- resuming (design 6.10)

    def _resume(self, ledger: Ledger) -> dict:
        self.step("resuming", {"from": ledger.last})
        if ledger.committed:
            return self._republish(ledger)
        if not ledger.plans:  # start, probed, locked: before the plan
            self._account([], before_plan=True)
            return self._from_probe()
        plans = self._plans()
        self.plan = plans[-1]
        self._account(plans)
        if ledger.source_checked is None:
            return self._from_locks()
        missing = [e["to"] for e in self.plan["files"] if e["to"] not in ledger.copied]
        if missing:
            raise _mismatch("the ledger checked the source before every copy was made",
                            paths=missing[:20])
        return self._after_check()

    def _account(self, plans: list[dict], *, before_plan: bool = False, remove: bool = True) -> None:
        """① of design 6.10: every entry of the destination is a directory a plan names, a copy its
        `copied` line names with the same bytes (.gitignore: the transform's before or after, after
        once it is `transformed`), the published file of a path only begun, a temporary file of this
        migration's a line named, a file a `direct` line names by its identity — removed here,
        unfinished, unless `remove` is False (the dry run) — this migration's own directory, or a
        name a person's tools leave (OQ16); and every copy a `copied` line names is there, and after
        the source check every folder the last plan names (step 8, S3). Anything else is
        destination_mismatch, and nothing is removed."""
        ledger = self.ledger
        mid = ledger.migration_id
        planned_files = {e["to"]: e for plan in plans for e in plan["files"]}
        planned_dirs: set[str] = set()
        for rel in [d for plan in plans for d in plan["directories"]] + [_parent(r) for r in planned_files]:
            while rel:
                planned_dirs.add(rel)
                rel = _parent(rel)
        own = f"{MIGRATIONS}/{mid}"
        transform = ledger.transform
        gitignore = ((transform.get("after"),) if ledger.transformed is not None else
                     (transform.get("before"), transform.get("after"))) if transform else ()
        strays, changed, unfinished, published, seen = [], [], [], [], set()
        for rel, kind in _walk_destination(self.dest):
            seen.add(rel)
            if rel in (STATE_DIR, DEST_LOCK, MIGRATIONS, own) or rel.startswith(own + "/"):
                continue
            if _ignored(rel) and rel not in planned_files and rel not in planned_dirs:
                continue
            if kind == "dir":
                if rel not in planned_dirs:
                    strays.append(rel)
                continue
            if before_plan or rel not in planned_files:
                if rel in ledger.foreign:
                    raise _occupied(rel)  # made by something else where a temporary file was to be
                if rel not in ledger.temps:
                    strays.append(rel)
                continue
            line = ledger.copied.get(rel)
            if line is not None:
                if "link" in line:
                    try:
                        same = kind == "link" and os.readlink(self.dest / rel) == line["link"]
                    except OSError:
                        same = False
                elif rel == ".gitignore" and gitignore:
                    same = kind == "file" and _hash_path(self.dest / rel) in gitignore
                else:
                    same = kind == "file" and _hash_path(self.dest / rel) == line.get("sha256")
                if not same:
                    changed.append(rel)
            elif rel in ledger.direct:
                try:
                    same = _key(os.lstat(self.dest / rel)) == list(ledger.direct[rel].get("key") or [])
                except OSError:
                    same = False
                (unfinished if same else strays).append(rel)
            elif rel in ledger.direct_intent or rel in ledger.occupied:
                # made between its intent and its identity (F15, R20), or found there when this
                # migration was to publish it (T19): not this migration's, never overwritten
                raise _occupied(rel)
            elif rel in ledger.begun:
                published.append(rel)  # published before its `copied` line (F6)
            else:
                strays.append(rel)
        for rel in sorted(r for r in planned_files if r.startswith(own + "/")):
            # A copy kept in this migration's own directory (lab.json's bytes): judged as the others.
            line = ledger.copied.get(rel)
            there = os.path.lexists(self.dest / rel)
            if line is not None:
                if not there or _hash_path(self.dest / rel) != line.get("sha256"):
                    changed.append(rel)
            elif there and rel in ledger.begun and not before_plan:
                published.append(rel)
        # A copy its `copied` line names that is gone (or behind a link the walk does not follow).
        missing = [rel for rel in ledger.copied if rel in planned_files and rel not in seen
                   and not rel.startswith(own + "/")]
        if plans and ledger.source_checked is not None:
            # After the source check (step 8), every folder the last plan names is there: the copy made
            # them all before that line, and none is made again without the source. A folder that is
            # there as something else is a stray above.
            missing += [rel for rel in plans[-1]["directories"] if rel not in seen and not _ignored(rel)
                        and not rel.startswith(MIGRATIONS + "/")]
        missing = sorted(set(missing))
        if strays or changed or missing:
            raise _mismatch("the destination holds what its ledger does not account for: "
                            f"{', '.join((changed + missing + strays)[:5])}",
                            reason="unaccounted", changed=changed, missing=missing[:50],
                            paths=strays[:50])
        if remove:
            for rel in unfinished:
                self._remove_temp(self.dest / rel)  # this migration's direct write, cut short
        self.published = published

    def _accept_copies(self, inventory: Inventory) -> list:
        """② of design 6.10, under the source's locks again: every copy the ledger names, and every
        file published without its `copied` line, has the bytes the source has now — else
        source_changed (a copy is never overwritten). Gives the files published and equal to the
        source, which _record_accepted records once the new plan's line is written."""
        ledger = self.ledger
        if not (ledger.copied or self.published):
            return []
        mid = ledger.migration_id
        files = {_dest_rel(f["rel"], mid): f for f in inventory.files}
        links = {_dest_rel(link["rel"], mid): link for link in inventory.links}
        changed, accepted = [], []
        for rel in sorted(set(ledger.copied) | set(self.published)):
            line = ledger.copied.get(rel) or ledger.begun[rel]
            if "link" in line:
                if rel not in links or links[rel]["text"] != line["link"]:
                    changed.append(rel)
                elif rel not in ledger.copied:
                    accepted.append((rel, {"link": line["link"]}))
                continue
            item = files.get(rel)
            want = line.get("sha256") if rel in ledger.copied else _hash_path(self.dest / rel)
            try:
                same = item is not None and _hash_source(item["real"], item["key"])[0] == want
            except (_SourceChanged, _Unreadable):
                same = False
            if not same:
                changed.append(rel)
            elif rel not in ledger.copied:
                accepted.append((rel, {"bytes": os.path.getsize(self.dest / rel), "sha256": want}))
        if changed:
            raise _changed(changed)
        return accepted

    def _record_accepted(self, accepted: list) -> None:
        """A file found published, equal to the source, recorded as the copy it is; its temporary name,
        if one is left (POSIX between link and unlink), removed then (design 6.10)."""
        for rel, fields in accepted:
            self._line("copied", path=rel, **fields)
            self._remove_old_temps(rel)

    # -- steps 8-12

    def _after_check(self) -> dict:
        self._verify()  # before any temporary file goes: a copy missing is refused with them there
        self._clean_temps()
        if self.plan["transforms"] and self.ledger.transformed is None:
            self._transform()
        report = self._report_current()
        self._flush()
        return self._commit(report)

    def _clean_temps(self) -> None:
        """The temporary files a line named (design 6.10): a copy's, once its path is copied, removed;
        one whose path no plan copies now kept, `leftover_temp` in the report."""
        planned = {e["to"] for e in self.plan["files"]}
        self.leftover = []
        for rel, temps in sorted(self.ledger.begun_temps.items()):
            for temp in temps:
                temp_rel = _posix(_parent(rel), temp)
                if not os.path.lexists(self.dest / temp_rel) or not self._removable(temp_rel):
                    continue
                if rel in self.ledger.copied:
                    self._remove_temp(self.dest / temp_rel)
                elif rel not in planned:
                    self.leftover.append(temp_rel)

    def _verify(self) -> None:
        """Step 8: every copy read again against its `copied` line, and nothing in the destination
        that no plan names (this migration's own files, the temporary files kept, and the names a
        person's tools leave apart)."""
        self.step("verifying", {})
        missing = [e["to"] for e in self.plan["files"] if e["to"] not in self.ledger.copied]
        if missing:
            raise _mismatch("copies the plan names were not made", paths=missing[:50])
        self._account(self._plans())
        if self.ledger.last in ("source_checked", "verified"):
            self._line("verified", files=len(self.plan["files"]))

    # -- step 9: .gitignore, the mapping, the proposals and the report

    def _transform(self) -> None:
        """design 6.13: the block of new names appended to the copy of .gitignore — the transform's
        line (with both hashes) first, then its temporary file, then a replace of this migration's copy
        only (read-only for the moment of the replace on Windows)."""
        ledger = self.ledger
        path = self.dest / ".gitignore"
        copied = ledger.copied[".gitignore"]["sha256"]
        line = ledger.transform
        if line is not None:
            temp = self.dest / line["temp"]
            if os.path.lexists(temp) and self._removable(line["temp"]):
                self._remove_temp(temp)  # named by its line, made again from its bytes (design 6.10)
            now = _hash_path(path)
            if now == line["after"]:
                self._line("transformed", path=".gitignore", sha256=now)
                return
            if now != line["before"]:
                raise _mismatch(".gitignore is neither the copy nor its transform", paths=[".gitignore"])
        before = path.read_bytes()
        if _sha(before) != copied:
            raise _mismatch(".gitignore is not the copy the ledger names", paths=[".gitignore"])
        after, added = gitignore_block(before, ledger.migration_id)
        if after == before:
            return
        self._write_own(self.mdir / "gitignore.diff", _unified(".gitignore", before, after))
        n = sum(1 for v in ledger.lines if v["step"] == "transform_begin")
        name = self._root_temp(".mig-gitignore-", n)
        temp = self.dest / name
        self._line("transform_begin", path=".gitignore", temp=name, before=_sha(before),
                   after=_sha(after), added=added)
        try:
            with open(_create(temp), "wb", closefd=True) as fh:
                self.step("transform_temp", {})
                fh.write(after)
                self.files.flush_handle(fh)
        except FileExistsError:
            raise _occupied(name) from None  # not this migration's: never removed (S6)
        except OSError as exc:
            raise _write_failed(temp, exc) from exc
        self.step("transform_written", {})
        if _hash_path(path) != copied:
            raise _mismatch(".gitignore changed under the migration", paths=[".gitignore"])
        try:
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode):
                raise _mismatch(".gitignore is not the copy the ledger names", paths=[".gitignore"])
            mode = stat.S_IMODE(st.st_mode)
            readonly = not mode & stat.S_IWUSR
            if readonly:
                os.chmod(path, mode | stat.S_IWUSR)
            self.files.replace(temp, path)
            if readonly:
                os.chmod(path, mode)
        except OSError as exc:
            raise _write_failed(path, exc) from exc
        self.step("transform_replaced", {})
        self._line("transformed", path=".gitignore", sha256=_sha(after))

    def _report_current(self) -> dict:
        """The report as the ledger, the plan and the copies make it (design 6.8): kept when the last
        `reported` line binds the one there and every file it binds (the mapping, the diff, the
        proposals, report.md) has its sha256, else written again with them and bound by a new
        `reported` line."""
        path = self.mdir / "report.json"
        reported = self.ledger.reported
        if reported is not None and self.ledger.last in ("reported", "durable", "marker_begin"):
            data = self._dest_bytes(path.relative_to(self.dest).as_posix())
            report = _loads(data) if _sha(data) == reported.get("sha256") else None
            if isinstance(report, dict) and not self._bound_differ(report):
                return report
        self.step("reporting", {})
        report, files = self._build_report()
        for name, data in files.items():
            self._write_own(self.mdir / name, data)
            if name == "mapping.json":
                self.step("mapping_written", {})
        self.step("report_written", {})
        self._line("reported", sha256=_sha(files["report.json"]))
        return report

    def _bound_files(self) -> tuple[dict, list, dict[str, bytes]]:
        """(the proposals, the `added` entries, the files by their path in this migration's
        directory) that a report binds, made in memory from the ledger, the plan and the copies: the
        mapping, the .gitignore diff, the proposals (design 6.7, 6.13). The same ledger, plan and
        copies give the same bytes."""
        own = f"{MIGRATIONS}/{self.ledger.migration_id}"
        files = {"mapping.json": _json_bytes(self._mapping())}
        added = [{"path": f"{own}/mapping.json", "sha256": _sha(files["mapping.json"])}]
        if self.ledger.transformed is not None:
            files["gitignore.diff"] = self._gitignore_diff()
            added.append({"path": f"{own}/gitignore.diff", "sha256": _sha(files["gitignore.diff"])})
        proposals = self._proposals(added, files)
        return proposals, added, files

    def _build_report(self) -> tuple[dict, dict[str, bytes]]:
        """The report and every file it binds (_bound_files, report.md, report.json last), in the
        order they are written."""
        own = f"{MIGRATIONS}/{self.ledger.migration_id}"
        proposals, added, files = self._bound_files()
        report = self._report(added, proposals, files["mapping.json"])
        files["report.md"] = _summary(report)
        report["added"].append({"path": f"{own}/report.md", "sha256": _sha(files["report.md"])})
        files["report.json"] = _json_bytes(report)
        return report, files

    def _bound_differ(self, report: dict) -> list[str]:
        """The files `report` binds (`added`, `mapping`) that are not there as a file with their
        sha256, or that are not in this migration's directory."""
        own = f"{MIGRATIONS}/{self.ledger.migration_id}"
        out = []
        for item in [*(report.get("added") or []), report.get("mapping") or {}]:
            rel = item.get("path") if isinstance(item, dict) else None
            if not core._record_path(rel) or not rel.startswith(own + "/"):
                out.append(str(rel))
                continue
            try:
                regular = stat.S_ISREG(os.lstat(self.dest / rel).st_mode)
            except OSError:
                regular = False
            if not regular or _hash_path(self.dest / rel) != item.get("sha256"):
                out.append(rel)
        return out

    def _gitignore_diff(self) -> bytes:
        """The .gitignore transform's diff (design 6.13), made again from the copy as transformed:
        its bytes before are its bytes after without the block the transform line names, checked
        by the line's sha256."""
        line = self.ledger.transform
        after = self._dest_bytes(".gitignore")
        if _sha(after) == line["after"]:
            for before in _gitignore_befores(after, self.ledger.migration_id, line.get("added") or []):
                if _sha(before) == line["before"]:
                    return _unified(".gitignore", before, after)
        raise _mismatch(".gitignore is not the transform its line names", paths=[".gitignore"])

    def _mapping(self) -> dict:
        checked = self.ledger.source_checked
        mid = self.ledger.migration_id
        spelled = {link["path"]: link["target_spellings"] for link in checked.get("links") or []}
        return {"schema": FAMILY, "record": "mapping", "migration_id": mid,
                "root": {"from": checked.get("root_from") or [], "to": str(self.dest)},
                "prefixes": [{"from": f"{LEGACY_STATE_DIR}/", "to": f"{STATE_DIR}/"}],
                "renamed": [{"from": writer.LEGACY_MARKER, "to": _dest_rel(writer.LEGACY_MARKER, mid)}],
                "links": [{"path": b["path"], "kind": b["kind"], "text": b["text"],
                           "target_real": b["target_real"],
                           "target_spellings": spelled.get(b["path"], [b["target_real"]]),
                           "treatment": b["treatment"]} for b in self.plan["links"]],
                "kinds": [{"from": "gap", "to": "workflow_feedback", "where": "reader"}]}

    def _proposals(self, added: list, files: dict[str, bytes]) -> dict:
        """What the report proposes and does not apply (design 6.13): .gitattributes, the scripts with
        the older SDK names renamed, and .gitignore where it is a link or there is none — their
        bytes put in `files` by their path in this migration's directory."""
        own = f"{MIGRATIONS}/{self.ledger.migration_id}"
        base = "proposals"
        out: dict = {}

        def propose(name: str, data: bytes) -> None:
            files[name] = data
            added.append({"path": f"{own}/{name}", "sha256": _sha(data)})

        attributes = (TEMPLATE / "gitattributes").read_bytes()
        if self._dest_bytes(".gitattributes") != attributes:
            propose(f"{base}/gitattributes", attributes)
            out["gitattributes"] = f"{own}/proposals/gitattributes"
        gitignore = self.dest / ".gitignore"
        if not self.plan["transforms"]:
            why = "link" if os.path.islink(gitignore) else ("absent" if not os.path.lexists(gitignore)
                                                            else None)
            if why is not None:
                block, lines = gitignore_block(b"", self.ledger.migration_id)
                propose(f"{base}/gitignore", block)
                out["gitignore"] = {"path": f"{own}/proposals/gitignore", "why": why, "lines": lines}
        scripts, diffs = [], []
        for entry in self.plan["files"]:
            rel = entry["to"]
            if entry["kind"] != "file" or not rel.startswith("scripts/") or not rel.endswith(".py"):
                continue
            data = (self.dest / rel).read_bytes()
            new, changes, reasons = script_proposal(data)
            if new is None and not reasons:
                continue
            item = {"path": rel, "changes": changes, "reasons": reasons}
            if new is not None:
                below = rel[len("scripts/"):]
                propose(f"{base}/scripts/{below}", new)
                item["proposal"] = f"{own}/proposals/scripts/{below}"
                diffs.append(_unified(rel, data, new))
            scripts.append(item)
        if diffs:
            propose(f"{base}/scripts.diff", b"".join(diffs))
            out["scripts_diff"] = f"{own}/proposals/scripts.diff"
        out["scripts"] = scripts
        return out

    def _report(self, added: list, proposals: dict, mapping: bytes) -> dict:
        ledger, plan = self.ledger, self.plan
        start, checked, locked = ledger.start, ledger.source_checked, ledger.locked or {}
        mid = ledger.migration_id
        marker_rel = _dest_rel(writer.LEGACY_MARKER, mid)
        marker = self._dest_bytes(marker_rel)
        value = _loads(marker)
        value = value if isinstance(value, dict) else {}
        files = []
        for entry in plan["files"]:
            line = ledger.copied[entry["to"]]
            item = {"path": entry["from"]}
            if entry["to"] != entry["from"]:
                item["to"] = entry["to"]
            if "link" in line:
                item["link"] = line["link"]
            else:
                item.update(bytes=line["bytes"], sha256=line["sha256"])
            files.append(item)
        transformed = []
        if ledger.transformed is not None:
            t = ledger.transform
            transformed.append({"path": ".gitignore", "before": t["before"], "after": t["after"],
                                "added": t.get("added", []),
                                "diff": f"{MIGRATIONS}/{mid}/gitignore.diff"})
        times = {}
        for v in ledger.lines:
            times.setdefault(v["step"], v["at"])
            times[v["step"] + ":last"] = v["at"]
        journal = checked["journal"]
        notes = [
            "The source stays a lab.json project. What was written to it after source_checked_at "
            f"(its journal past byte {journal['bytes']}, line {journal['lines']}, seq "
            f"{journal['last_seq']}) is not in this copy; migrating again into a new destination copies "
            "it (ADR-0005 decision 5).",
            "Records with CRLF line ends are copied as they are; nothing was rewritten (ADR-0007 "
            "decision 6).",
            "Only bytes are kept: extended attributes and access lists are not copied; permissions and "
            "modification times are copied where the system allows, but not onto the destination's "
            "root and .analytics/.",
        ]
        if any(e["from"] == ".git" or e["from"].startswith(".git/") for e in plan["files"]):
            notes.append("The source's .git/ was copied: the destination is a second repository with "
                         "the same remotes, and git there shows the files of .lab/ as deleted and the "
                         "same files under .analytics/ as new.")
        title = value.get("title")
        if not isinstance(title, str) or not title.strip():
            notes.append("The source's lab.json has no title: the marker takes the source folder's "
                         "name (not in the source).")
        if not isinstance(value.get("created_at"), str):
            notes.append("The source's lab.json has no created_at: the marker takes the migration's "
                         "start (not in the source).")
        locks = locked.get("locks")
        held = _ms(times.get("locked:last"), checked["at"]) if "at" in checked else None
        origin = start["origin"]
        return {
            "schema": FAMILY, "record": "report", "migration_id": mid, "kit_version": core.KIT_VERSION,
            "started_at": start["at"], "source_checked_at": checked["source_checked_at"],
            "actor": origin["actor"], "actor_basis": origin["actor_basis"], "means": origin["means"],
            **({"session": origin["session"]} if origin.get("session") else {}),
            "source": {
                "path": start["source"]["path"], "spellings": start["source"]["spellings"],
                "given": start["source"]["given"] if self.source is not None else "not_given",
                "marker": {"file": writer.LEGACY_MARKER, "schema": value.get("schema"),
                           "bytes": len(marker), "sha256": _sha(marker), "kept_at": marker_rel},
                "journal": journal, "inventory": checked["inventory"],
                "files": sum(1 for f in files if "sha256" in f),
                "bytes": sum(f.get("bytes", 0) for f in files),
                "locks": ({"taken": locks, "held_ms": held} if isinstance(locks, list)
                          else {"taken": "not_taken", "reason": locked.get("reason")}),
            },
            "destination": {"path": str(self.dest), "project_id": plan["project_id"],
                            "folds": plan["folds"], "links_made": plan["links_made"]},
            "files": files, "transformed": transformed, "added": added,
            "excluded": plan["excluded"], "not_copied": plan["not_copied"], "links": plan["links"],
            "warnings": plan["warnings"], "leftover_temp": sorted(self.leftover),
            "mapping": {"path": f"{MIGRATIONS}/{mid}/mapping.json", "sha256": _sha(mapping)},
            "proposals": proposals,
            "checks": {"source_before_after": "equal", "copies": "equal", "compared": len(files)},
            "notes": notes,
            "timings": {"plan_ms": _ms(times.get("locked:last"), times.get("plan:last")),
                        "copy_ms": _ms(times.get("plan:last"), times.get("copied:last")),
                        "recheck_ms": _ms(times.get("copied:last"), times.get("source_checked")),
                        "verify_ms": _ms(times.get("source_checked"), times.get("verified:last"))},
        }

    # -- steps 10-12

    def _flush(self) -> None:
        """Step 10: every directory that holds a copy or a file of this migration flushed."""
        if self.ledger.last in ("durable", "marker_begin"):
            return
        directories = {"", STATE_DIR, MIGRATIONS}
        for entry in self.plan["files"]:
            rel = _parent(entry["to"])
            while rel:
                directories.add(rel)
                rel = _parent(rel)
        for root, dirs, _ in os.walk(self.mdir):
            directories.add(Path(root).relative_to(self.dest).as_posix())
            directories.update(Path(root, d).relative_to(self.dest).as_posix() for d in dirs)
        for rel in sorted(directories, key=lambda r: (-r.count("/"), r)):
            try:
                self.files.flush_dir(self.dest / rel if rel else self.dest)
            except OSError as exc:
                raise _write_failed(self.dest / rel, exc) from exc
        self._line("durable", directories=len(directories))

    def _commit(self, report: dict) -> dict:
        report_data = (self.mdir / "report.json").read_bytes()
        marker = self.ledger.marker
        written = _loads(marker["text"]) if marker else None
        if isinstance(written, dict) and (written.get("migration") or {}).get(
                "report_sha256") == _sha(report_data):
            data = marker["text"].encode("utf-8")  # the same bytes again (design 6.10)
        else:
            data = _json_bytes(self._marker_value(report_data))
        return self._publish_marker(data, report)

    def _marker_value(self, report_data: bytes) -> dict:
        mid = self.ledger.migration_id
        value = _loads(self._dest_bytes(_dest_rel(writer.LEGACY_MARKER, mid)))
        value = value if isinstance(value, dict) else {}
        title, created = value.get("title"), value.get("created_at")
        return {"schema": writer.PROJECT, "project_id": self.plan["project_id"],
                "title": title if isinstance(title, str) and title.strip()
                else Path(self.ledger.start["source"]["path"]).name,
                "created_at": created if isinstance(created, str) else self.ledger.start["at"],
                "min_writer_version": core.KIT_VERSION, "kit_version": core.KIT_VERSION,
                "migration": {"id": mid, "report": f"{MIGRATIONS}/{mid}/report.json",
                              "report_sha256": _sha(report_data)}}

    def _publish_marker(self, data: bytes, report: dict) -> dict:
        """Step 11, under the destination's lock: the marker's line with its bytes, its temporary file,
        and its create-if-absent publication — the commit point — then the directory flushed and
        `committed`. A marker's temporary file an earlier line named goes first."""
        ledger = self.ledger
        for old in sorted(set(ledger.marker_temps)):
            if os.path.lexists(self.dest / old) and self._removable(old):
                self._remove_temp(self.dest / old)
        n = sum(1 for v in ledger.lines if v["step"] == "marker_begin")
        name = self._root_temp(MARKER_TEMP, n)
        temp = self.dest / name
        self._line("marker_begin", temp=name, text=data.decode("utf-8"), sha256=_sha(data))
        try:
            with open(_create(temp), "wb", closefd=True) as fh:
                self.step("marker_temp", {})
                fh.write(data)
                self.files.flush_handle(fh)
        except FileExistsError:
            raise _occupied(name) from None  # not this migration's: never removed (S6)
        except OSError as exc:
            raise _write_failed(temp, exc) from exc
        self.step("marker_written", {})
        self._publish_marker_file(temp)
        self.step("marker_published", {})
        try:
            self.files.flush_dir(self.dest)
        except OSError as exc:
            raise _write_failed(self.dest, exc) from exc
        self._line("committed")
        return self._trust(report, already=False, write_line=True)

    def _publish_marker_file(self, temp: Path) -> None:
        """The commit point (step 11): the marker's temporary file given its name only where none is —
        by the port's publish_new, or where the file system has no links by its rename_new, a rename
        that refuses a taken name — never written in place, so the marker is there whole or not at
        all (owner's decision of 2026-10-08). Where neither is there the migration stops before the
        marker; its temporary file stays, named by its line, for the next call to remove."""
        final = self.dest / writer.MARKER
        try:
            self.files.publish_new(temp, final)
            return
        except FileExistsError:
            raise _occupied(writer.MARKER) from None
        except _platform.LinkUnsupported:
            pass
        except OSError as exc:
            raise _write_failed(final, exc) from exc
        try:
            self.files.rename_new(temp, final)
        except FileExistsError:
            raise _occupied(writer.MARKER) from None
        except _platform.RenameUnsupported as exc:
            raise _no_exclusive_rename(self.dest, exc, copied=True) from exc
        except OSError as exc:
            raise _write_failed(final, exc) from exc

    def _trust(self, report: dict, *, already: bool, write_line: bool) -> dict:
        """Step 12: the destination added to the trust list this kit writes (design 6.14). A failure
        does not undo the commit: the response says it, and calling again adds it."""
        trusted, error = True, None
        try:
            if not core.is_trusted(self.dest):
                self.trust(self.dest)
        except OSError as exc:
            trusted, error = False, f"{type(exc).__name__}: {exc}"
        if trusted and write_line and not self.ledger.trusted:
            self._line("trusted")
        return self._response(report, already=already, trusted=trusted, trust_error=error)

    def _response(self, report: dict, *, already: bool, trusted: bool, trust_error=None) -> dict:
        mid = self.ledger.migration_id
        report_rel = f"{MIGRATIONS}/{mid}/report.json"
        out = {"ok": True, "already_migrated": already, "migration_id": mid,
               "destination": str(self.dest),
               "source": {"path": report["source"]["path"],
                          "given": "not_given" if self.source is None else "given"},
               "project_id": report["destination"]["project_id"], "report": report_rel,
               "report_sha256": _sha(self._dest_bytes(report_rel)),
               "files": report["source"]["files"], "bytes": report["source"]["bytes"],
               "excluded": len(report["excluded"]), "not_copied": len(report["not_copied"]),
               "warnings": report["warnings"], "leftover_temp": report["leftover_temp"],
               "trusted": trusted}
        if trust_error:
            out["trust_error"] = trust_error
        return out

    # -- a committed destination (design 6.9), and one whose marker went (6.10's last row)

    def _republish(self, ledger: Ledger) -> dict:
        """`committed` and no marker: the bytes its `marker_begin` line holds published again where the
        report is the one the last `reported` line binds and every copy has its report's bytes, else
        destination_mismatch. A file the report binds that differs is made again where that gives its
        bytes (the mapping, the diff, the proposals, report.md), else destination_mismatch too. The
        source is not read (design 6.10)."""
        report = self._republish_checks(ledger)
        unmade, remade = self._remade_bound(report)
        if unmade:
            raise _mismatch("files the report binds differ and cannot be made again with its bytes",
                            reason="unaccounted", paths=unmade[:50])
        for rel, data in remade.items():
            self._write_own(self.dest / rel, data)
        if remade:
            for root, _dirs, _files in os.walk(self.mdir):
                try:
                    self.files.flush_dir(root)
                except OSError as exc:
                    raise _write_failed(root, exc) from exc
        result = self._publish_marker(ledger.marker["text"].encode("utf-8"), report)
        result.update(already_migrated=True, republished=True)
        return result

    def _republish_checks(self, ledger: Ledger) -> dict:
        """The report of a committed migration whose marker went, when it is the one the last
        `reported` line binds and every copy has its bytes (read only)."""
        report_data = self._dest_bytes(f"{MIGRATIONS}/{ledger.migration_id}/report.json")
        if ledger.reported is None or ledger.marker is None or \
                _sha(report_data) != ledger.reported.get("sha256"):
            raise _mismatch("the report is not the one its ledger bound", paths=["report.json"])
        report = json.loads(report_data)
        compared = compare_copies(self.dest, report)
        if any(v != "same" for v in compared.values()):
            raise _mismatch("copies differ from the report",
                            paths=sorted(p for p, v in compared.items() if v != "same")[:50])
        return report

    def _remade_bound(self, report: dict) -> tuple[list[str], dict[str, bytes]]:
        """(the files `report` binds that differ and cannot be made again with the bytes it binds,
        the bytes of those that can, by destination path): made in memory from the ledger, the plan
        and the copies (design 6.10's last row keeps the report and the marker as they are)."""
        differ = self._bound_differ(report)
        if not differ:
            return [], {}
        own = f"{MIGRATIONS}/{self.ledger.migration_id}"
        try:
            if self.plan is None:
                self.plan = self._plans()[-1]
            _, _, files = self._bound_files()
        except AnalyticsError:
            return differ, {}
        files["report.md"] = _summary(report)
        want = {item.get("path"): item.get("sha256")
                for item in [*(report.get("added") or []), report.get("mapping") or {}]
                if isinstance(item, dict)}
        unmade, remade = [], {}
        for rel in differ:
            name = rel[len(own) + 1:] if rel.startswith(own + "/") else None
            if name in files and _sha(files[name]) == want.get(rel):
                remade[rel] = files[name]
            else:
                unmade.append(rel)
        return unmade, remade

    def _already(self, judged: Judgment) -> dict:
        """design 6.9's `already_migrated`: nothing copied. Written only: the trust entry when it is
        missing, the missing `committed` line, and a marker's temporary file its line named removed
        where it is the marker or holds the same bytes. The copies are compared with the report and the
        source — when given and there — with the report's boundary, read only."""
        ledger = judged.ledger
        self.ledger, self.mdir = ledger, ledger.directory
        report_rel = f"{MIGRATIONS}/{ledger.migration_id}/report.json"
        report_data = self._dest_bytes(report_rel)
        report = _loads(report_data)
        warnings = ["marker_rewritten"] if judged.rewritten else []
        if not ledger.committed:
            self._refuse_managed_links()  # the line is appended where the ledger is, never via a link
            self._line("committed")
        marker = self.dest / writer.MARKER
        for temp in sorted(set(ledger.marker_temps)):
            path = self.dest / temp
            if not os.path.lexists(path) or not self._removable(temp):
                continue
            if _same_entry(path, marker) or _hash_path(path) == _hash_path(marker):
                self._remove_temp(path)
            else:
                warnings.append(f"kept {temp}: not the marker's bytes")
        if not isinstance(report, dict):
            raise _mismatch("the report of this migration cannot be read", paths=[report_rel])
        compared = compare_copies(self.dest, report)
        bound = ((_loads(judged.marker) or {}).get("migration") or {}).get("report_sha256")
        if _sha(report_data) != bound:
            compared[report_rel] = "changed"
        result = self._trust(report, already=True, write_line=False)
        result.update(compared=_counts(compared),
                      differs=sorted(p for p, v in compared.items() if v != "same")[:50],
                      source_since=self._source_since(report), marker_warnings=warnings)
        return result

    def _source_since(self, report: dict) -> dict:
        """What the source holds that the report's boundary did not (design 6.9): the journal's lines
        and seq range past it and the files that differ from the report, read only, when the source was
        given and is there."""
        if self.source is None or not self.source.is_dir():
            return {"source": "not_given" if self.source is None else "absent",
                    "path": (report.get("source") or {}).get("path")}
        boundary = (report.get("source") or {}).get("journal") or {}
        journal = self.source / "journal.jsonl"
        data = journal.read_bytes() if journal.is_file() else b""
        tail = data[boundary.get("bytes", 0):]
        appended = relations.Journal(tail) if tail.strip() else None
        seqs = [s for s in (appended.seq_of.values() if appended else ()) if s is not None]
        known = {f.get("path"): f for f in report.get("files") or []}
        differing = None
        with contextlib.suppress(AnalyticsError):
            now = {f["rel"]: f for f in self._walk(hashed=True).files}
            differing = sum(1 for p, f in known.items() if "sha256" in f
                            and (now.get(p) or {}).get("sha256") != f["sha256"])
            differing += sum(1 for p in now if p not in known)
        return {"journal_lines": appended.lines if appended else 0,
                "seq": [min(seqs), max(seqs)] if seqs else None, "files_differing": differing}

    # -- the dry run (design 6.9)

    def _dry_run(self, judged: Judgment) -> dict:
        out: dict = {"ok": True, "dry_run": True, "destination": str(self.dest),
                     "estimate": "what the migration would do now; the source and the destination may "
                                 "change before it runs"}
        try:
            if judged.kind == "committed":
                ledger = judged.ledger
                todo = []
                if not ledger.committed:
                    todo.append("write the committed line")
                if any(os.path.lexists(self.dest / t) for t in ledger.marker_temps
                       if ledger.owns(t)):
                    todo.append("remove the marker's temporary file")
                if not core.is_trusted(self.dest):
                    todo.append("add the destination to the trust list")
                out.update(state="committed", already_migrated=True, migration_id=ledger.migration_id,
                           todo=todo, **({"warnings": ["marker_rewritten"]} if judged.rewritten else {}))
                return out
            if judged.reads_source:
                self._source_checks()
            self._destination_checks(judged)
            todo = self._dry_resume(judged) if judged.kind == "resume" else []
        except AnalyticsError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            exc.details = {**details, "dry_run": True, "state": "refused",
                           **({"plan": self._dry_plan(self.inventory)} if self.inventory else {})}
            raise
        if judged.kind == "new":
            out.update(state="new", plan=self._dry_plan(self.inventory))
        else:
            ledger = judged.ledger
            out.update(state="resumable", migration_id=ledger.migration_id, step=ledger.last,
                       source_checked=ledger.source_checked is not None, copied=len(ledger.copied),
                       **({"todo": todo} if todo else {}))
            if self.inventory is not None:
                out["plan"] = self._dry_plan(self.inventory)
        return out

    def _dry_resume(self, judged: Judgment) -> list[str]:
        """The checks a resumed call makes of the destination before it acts (design 6.9: the same
        rules), read only: ① of 6.10 for a migration not committed, the report and the copies for one
        committed whose marker went. Gives what such a call would write besides going on."""
        ledger = judged.ledger
        self.ledger, self.mdir = ledger, ledger.directory
        if ledger.committed:
            report = self._republish_checks(ledger)
            unmade, remade = self._remade_bound(report)
            if unmade:
                raise _mismatch("files the report binds differ and cannot be made again with its "
                                "bytes", reason="unaccounted", paths=unmade[:50])
            return [f"make {rel} again" for rel in remade] + ["publish the marker again"]
        if not ledger.plans:
            self._account([], before_plan=True, remove=False)
            return []
        plans = self._plans()
        self.plan = plans[-1]
        self._account(plans, remove=False)
        if ledger.source_checked is not None:
            missing = [e["to"] for e in self.plan["files"] if e["to"] not in ledger.copied]
            if missing:
                raise _mismatch("the ledger checked the source before every copy was made",
                                paths=missing[:20])
        return []

    def _dry_plan(self, inventory: Inventory) -> dict:
        source = str(self.source)
        groups: dict[str, list[str]] = {}
        for rel in [f["rel"] for f in inventory.files] + [x["rel"] for x in inventory.links] + \
                [d["rel"] for d in inventory.dirs]:
            groups.setdefault("/".join(_folded(p, True, True) for p in rel.split("/")), []).append(rel)
        lines: list[str] = []
        gitignore = os.path.join(source, ".gitignore")
        if os.path.isfile(gitignore) and not os.path.islink(gitignore):
            with open(gitignore, "rb") as fh:
                lines = gitignore_block(fh.read(), "<id>")[1]
        scripts = 0
        for f in inventory.files:
            if f["rel"].startswith("scripts/") and f["rel"].endswith(".py"):
                with contextlib.suppress(OSError), open(f["real"], "rb") as fh:
                    new, _, reasons = script_proposal(fh.read())
                    scripts += 1 if (new is not None or reasons) else 0
        running = []
        for run_json in sorted((Path(source) / "runs").glob("*/run.json")):
            value = core.read_json(run_json, {})
            if isinstance(value, dict) and value.get("status") == "running":
                running.append(run_json.parent.name)
        needed = sum(f["size"] for f in inventory.files)
        return {"links_outside": [{"path": m["path"], "text": m["text"], "target": "missing"}
                                  for m in inventory.missing_targets]
                + [{"path": b["path"], "text": b["text"], "target": b["target_real"]}
                   for b in inventory.boundaries if not core._under(b["target_real"], source)],
                "files": len(inventory.files), "bytes": needed, "links_made": len(inventory.links),
                "excluded": inventory.excluded_list(), "not_copied": inventory.not_copied,
                "folding_pairs": [v for v in groups.values() if len(v) > 1],
                # Neither is judged without writing to the destination: step 2's probe does both.
                "folding_judged": False, "publication_judged": False,
                "gitignore_lines": lines, "script_proposals": scripts,
                "needed": needed + int(needed * SPACE_SHARE) + SPACE_MARGIN,
                "free": self._free(_existing(str(self.dest))), "running_runs": running,
                "warnings": inventory.warnings}


def _dicts(value) -> list:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _counts(compared: dict) -> dict:
    out: dict[str, int] = {}
    for value in compared.values():
        out[value] = out.get(value, 0) + 1
    return out


def _ms(start, end) -> int | None:
    try:
        return max(0, round((datetime.fromisoformat(end) - datetime.fromisoformat(start))
                            .total_seconds() * 1000))
    except (TypeError, ValueError):
        return None


def compare_copies(dest: Path, report: dict) -> dict[str, str]:
    """Each copy a report names, now: `same`, `changed` or `missing` (design 6.9). A file kept by
    appending is compared up to the length the report gives it; .gitignore by its transform."""
    out = {}
    after = next((t.get("after") for t in report.get("transformed") or []
                  if t.get("path") == ".gitignore"), None)
    for item in report.get("files") or []:
        rel = item.get("to") or item.get("path")
        path = dest / rel
        if "link" in item:
            try:
                out[rel] = "same" if os.readlink(path) == item["link"] else "changed"
            except OSError:
                out[rel] = "missing"
            continue
        if not os.path.lexists(path):
            out[rel] = "missing"
        elif rel in APPENDED:
            try:
                with open(path, "rb") as fh:
                    head = fh.read(item.get("bytes", 0))
                out[rel] = "same" if _sha(head) == item.get("sha256") else "changed"
            except OSError:
                out[rel] = "missing"
        elif rel == ".gitignore" and after is not None:
            out[rel] = "same" if _hash_path(path) == after else "changed"
        else:
            out[rel] = "same" if _hash_path(path) == item.get("sha256") else "changed"
    return out


# --- .gitignore (design 6.13) -----------------------------------------------------------------------

def gitignore_block(before: bytes, migration_id: str) -> tuple[bytes, list[str]]:
    """(the copy's bytes with the block appended, the lines it adds): each older template line the
    file has, under its new name, and the current layout's new files always, where the file does not
    have that line already. A line it does not have stays the source's choice. The block takes the
    file's line end (CRLF where it has one)."""
    present = {raw.strip() for raw in before.decode("utf-8", "surrogateescape").splitlines()}
    lines = [new for old, new in GITIGNORE_RENAMED if old in present and new not in present]
    lines += [new for new in GITIGNORE_ALWAYS if new not in present]
    if not lines:
        return before, []
    eol = b"\r\n" if b"\r\n" in before else b"\n"
    body = before if not before or before.endswith((b"\n", b"\r")) else before + eol
    return body + (eol if body else b"") + _gitignore_lines(migration_id, lines, eol), lines


def _gitignore_lines(migration_id: str, lines: list[str], eol: bytes) -> bytes:
    head = f"# Added by analytics migrate {migration_id}: the new names of the paths ignored above"
    return eol.join([head.encode("utf-8"), *(line.encode("utf-8") for line in lines)]) + eol


def _gitignore_befores(after: bytes, migration_id: str, lines: list[str]):
    """The bytes a .gitignore may have had before gitignore_block appended `lines` to it, as `after`
    is now: the caller checks each by its sha256 (the line end, and whether the last line had one,
    are not known from `after` alone)."""
    for eol in (b"\n", b"\r\n"):
        block = _gitignore_lines(migration_id, lines, eol)
        if not after.endswith(block):
            continue
        rest = after[:-len(block)]
        if not rest:
            yield b""
            continue
        if not rest.endswith(eol):
            continue
        body = rest[:-len(eol)]
        yield body
        if body.endswith(eol):
            yield body[:-len(eol)]


def _unified(rel: str, before: bytes, after: bytes) -> bytes:
    a = before.decode("utf-8", "surrogateescape").splitlines(keepends=True)
    b = after.decode("utf-8", "surrogateescape").splitlines(keepends=True)
    text = "".join(difflib.unified_diff(a, b, f"a/{rel}", f"b/{rel}"))
    return text.encode("utf-8", "surrogateescape")


# --- script proposals (design 6.13) -----------------------------------------------------------------

def _imports_by_name(func) -> bool:
    """Whether a call's function is one that imports a module named by a string."""
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) \
        else None
    return name in ("__import__", "import_module")


def script_proposal(data: bytes) -> tuple[bytes | None, list[dict], list[dict]]:
    """(the script with the script API's older names renamed, or None; the changes; what a person has
    to change by hand). `import labkit` and `from labkit import …` name aelix_analytics (the local
    names kept), `note` and `chart` called through it become `record` and `visualize`, and a record of
    kind "gap" becomes "workflow_feedback". A dynamic import of labkit, or a name of the API read with
    getattr, is `manual`. The bytes outside the edits stay as they are, line ends included."""
    try:
        tree = ast.parse(data.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError, ValueError):
        return None, [], []
    starts = [0]
    for line in data.split(b"\n")[:-1]:
        starts.append(starts[-1] + len(line) + 1)

    def at(lineno: int, col: int) -> int:
        return starts[lineno - 1] + col

    def span(node, name: str) -> tuple[int, int] | None:
        start, end = at(node.lineno, node.col_offset), at(node.end_lineno, node.end_col_offset)
        found = data.find(name.encode("utf-8"), start, end)
        return None if found < 0 else (found, found + len(name.encode("utf-8")))

    edits: list[tuple[int, int, bytes, dict]] = []
    reasons: list[dict] = []
    modules: set[str] = set()
    functions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in (OLD_MODULE, NEW_MODULE):
                    modules.add(alias.asname or alias.name)
                if alias.name == OLD_MODULE and (where := span(alias, OLD_MODULE)):
                    new = NEW_MODULE if alias.asname else f"{NEW_MODULE} as {OLD_MODULE}"
                    edits.append((*where, new.encode(), {"line": node.lineno, "from": OLD_MODULE,
                                                         "to": new}))
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module in (OLD_MODULE,
                                                                                       NEW_MODULE):
            if node.module == OLD_MODULE and (where := span(node, OLD_MODULE)):
                edits.append((*where, NEW_MODULE.encode(), {"line": node.lineno, "from": OLD_MODULE,
                                                            "to": NEW_MODULE}))
            for alias in node.names:
                functions[alias.asname or alias.name] = RENAMED_CALLS.get(alias.name, alias.name)
                if alias.name in RENAMED_CALLS and (where := span(alias, alias.name)):
                    new = RENAMED_CALLS[alias.name]
                    new = new if alias.asname else f"{new} as {alias.name}"
                    edits.append((*where, new.encode(), {"line": node.lineno, "from": alias.name,
                                                         "to": new}))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func, api = node.func, None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in modules:
            api = RENAMED_CALLS.get(func.attr, func.attr)
            if func.attr in RENAMED_CALLS:
                end = at(func.end_lineno, func.end_col_offset)
                edits.append((end - len(func.attr), end, api.encode(),
                              {"line": node.lineno, "from": f"{func.value.id}.{func.attr}",
                               "to": f"{func.value.id}.{api}"}))
        elif isinstance(func, ast.Name) and func.id in functions:
            api = functions[func.id]
        if _imports_by_name(func) and any(isinstance(a, ast.Constant) and a.value == OLD_MODULE
                                          for a in node.args):
            reasons.append({"line": node.lineno, "reason": "manual", "text": "a dynamic import of labkit"})
        if isinstance(func, ast.Name) and func.id == "getattr" and node.args and \
                isinstance(node.args[0], ast.Name) and node.args[0].id in modules:
            reasons.append({"line": node.lineno, "reason": "manual",
                            "text": "a name of the script API read with getattr"})
        first = node.args[0] if node.args else None
        if api == "record" and isinstance(first, ast.Constant) and first.value == "gap":
            start, end = at(first.lineno, first.col_offset), at(first.end_lineno, first.end_col_offset)
            quote = data[start:start + 1]
            edits.append((start, end, quote + b"workflow_feedback" + quote,
                          {"line": first.lineno, "from": "gap", "to": "workflow_feedback"}))
    if not edits:
        return None, [], reasons
    out = bytearray(data)
    for start, end, new, _ in sorted(edits, key=lambda e: -e[0]):
        out[start:end] = new
    return bytes(out), [e[3] for e in sorted(edits, key=lambda e: e[0])], reasons


# --- the English summary (design 6.8) ---------------------------------------------------------------

def _summary(report: dict) -> bytes:
    src, dst = report["source"], report["destination"]
    lines = [f"# Migration {report['migration_id']}", "",
             f"- Source: `{src['path']}` (lab.json, kept as it was)",
             f"- Destination: `{dst['path']}` (analytics.json, project `{dst['project_id']}`)",
             f"- By: {report['actor']} ({report['actor_basis']}), {report['means']}; kit "
             f"{report['kit_version']}",
             f"- Started {report['started_at']}; the source was last checked "
             f"{report['source_checked_at']}",
             f"- Copied: {src['files']} files, {src['bytes']:,} bytes, each with its sha256 in "
             "report.json",
             f"- Journal copied: {src['journal']['lines']} lines, {src['journal']['bytes']:,} bytes, "
             f"last seq {src['journal']['last_seq']}",
             f"- Not copied: {len(report['excluded'])} excluded, {len(report['not_copied'])} other "
             "(see report.json)",
             f"- Changed: {', '.join(t['path'] for t in report['transformed']) or 'nothing'}"
             + (" (lines appended; see its diff)" if report["transformed"] else ""), ""]
    if report["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w.get('reason')}: `{w.get('path')}`"
                                        for w in report["warnings"]] + [""]
    lines += ["## Notes", ""] + [f"- {note}" for note in report["notes"]] + [""]
    return "\n".join(lines).encode("utf-8")


# --- the entry --------------------------------------------------------------------------------------

def migrate(source, destination, *, origin: Origin, dry_run: bool = False, platform=None,
            on_step=None, free_space=None, trust=None) -> dict:
    """Copy the lab.json project at `source` into `destination` in the current format (design 6): the
    response on success — `already_migrated` when this source's migration committed there before —
    or an AnalyticsError (design 4.11). `source` None goes on with a migration whose ledger checked
    its source, or confirms a committed one, without reading the source (6.1). With `dry_run`
    nothing is written and no lock is taken: the answer is what would happen now. `platform` is the
    bundle of ports (core.PLATFORM by default); `free_space` (bytes, or a function of a path) and
    `trust` (a function of the destination) stand for the disk and the trust list in tests;
    `on_step` is the tests' fault injection (design 11.1)."""
    return Migration(source, destination, origin=origin, platform=platform, on_step=on_step,
                     free_space=free_space, trust=trust).run(dry_run)
