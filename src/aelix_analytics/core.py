"""Project model for Aelix Analytics projects. Standard library only.

The aelix extension loads this file inside the aelix process, so it must not import polars, duckdb,
plotting libraries or any other aelix_analytics module except `_platform`, `relations` (the records
read as ADR-0006 says) and `writer` (who may write a project's records, and how: Issue #13), which
are standard library only for the same reason. Code that needs the analysis libraries runs inside a
project's own uv environment (runner, data, workspace).

Every operation that behaves differently on Linux, macOS and Windows goes through the bundle of
ports a `Project` carries, so nothing in this file tests `sys.platform`. See `_platform`.
"""

from __future__ import annotations

import collections
import contextlib
import difflib
import hashlib
import heapq
import json
import os
import re
import sqlite3
import stat
import time
import tomllib
import unicodedata
from datetime import datetime
from pathlib import Path, PureWindowsPath

from . import _platform, asset_app, asset_suggestions, checkpoints, relations, writer
from ._platform import newlines_as_lf as newlines_as_lf
from ._platform import open_text as open_text
from ._platform import write_text as write_text
from .writer import DATASET_NAME_RE as DATASET_NAME_RE
from .writer import EVENT_ID_RE as EVENT_ID_RE
from .writer import RECORD_KINDS as RECORD_KINDS
from .writer import RUN_ID_RE as RUN_ID_RE
from .writer import AnalyticsError as AnalyticsError
from .writer import Origin as Origin
from .writer import Written as Written
from .writer import append_jsonl as append_jsonl
from .writer import classify_refs as classify_refs
from .writer import migration_record as migration_record
from .writer import new_request_id as new_request_id
from .writer import present_marker as present_marker

KIT_VERSION = "0.19.0"
# The marker and the record families of the projects this kit writes: the new format's, through
# the common writer (writer.Writer, Issue #13 PR 3; writer.FAMILIES). A project is found by either
# marker, analytics.json or lab.json, and judged by writer.marker_state: a lab.json project
# (lab.project/1 and its lab.*/1 records) is read and not written (migration_required, design 7).
MARKER = writer.MARKER
SCHEMA_PROJECT = writer.FAMILIES["project"]
SCHEMA_RUN = writer.FAMILIES["run"]
SCHEMA_EVENT = writer.FAMILIES["event"]
SCHEMA_DATASET = writer.FAMILIES["dataset"]
SCHEMA_TRUST = writer.FAMILIES["trust"]

# The order the Workspace lists event kinds in: the kit's own, then those a person or an agent
# records (writer.RECORD_KINDS), with a lab.event/1 record's `gap` beside its new name.
EVENT_KINDS = (*writer.KIT_KINDS, *writer.RECORD_KINDS[:-1], "gap", writer.RECORD_KINDS[-1])
KEY_KINDS = ("finding", "decision", "assumption")
OPEN_KINDS = ("question", "todo")
DATA_SUFFIXES = (".parquet", ".csv", ".tsv", ".json", ".ndjson", ".jsonl", ".arrow", ".feather",
                 ".ipc", ".xlsx")
CHART_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".html")

SCRIPT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
# How much of CONTEXT.md goes into the system prompt every turn. Sections past this budget are
# listed with their line ranges instead, for the agent to read on demand.
CONTEXT_PROMPT_CHARS = 4000
# How many bytes of a run's stdout and stderr the kit hands back (runner._tail): a log larger than
# this is shown in part.
OUTPUT_TAIL = 4000

# Directories a walk of the project's tree leaves out at any depth.
SKIPPED_DIRS = ("__pycache__", ".ipynb_checkpoints")
# Which top-level directories the kit manages, and which are never a run's data inputs or outputs,
# is each project's layout (Project.layout, design 2.3, S14): writer.Layout's for an analytics.json
# project — runs/ (the run records, code snapshots, logs and Visualizations it writes), .venv/ (the
# project environment uv keeps for it), .analytics/ (its own state: caches, telemetry, context
# versions, locks) and workspace/ (the Workspace it builds) — and writer.LegacyLayout's, with .lab/
# and report/ in place of .analytics/ and workspace/, for a lab.json project. A path below a managed
# directory is named by that directory's name, whatever case it is stored in and wherever it leads,
# so the kit's own exclusions hold (a run's own files, the environment); a script is never taken
# from one. The other ignored directories are the person's (scripts/, .git/, caches) and are named
# as stored; in an analytics.json project report/ and .lab/ are a person's folders too.

# The OS-dependent operations of this process. A Project takes this unless a caller — in practice a
# test — hands it another bundle; nothing here looks one up by name or by environment variable.
PLATFORM = _platform.detect()


# The kit version that renamed the public names — commands, tools, SDK functions and environment
# variables (Issue #9). Fixed: it names the boundary, not the current version.
NAMES_RENAMED_IN = "0.3.0"
# The kit version from which a record holds a path by one identity across links, case and Unicode
# normalization (Issue #36, ADR-0006 9). Fixed, like NAMES_RENAMED_IN: a reader tells a run recorded
# by an earlier kit by its env.kit_version (PEP 440 order), and reads its paths — absolute under the
# root or a link's target, or relative in another spelling than the stored one — through
# Project.legacy_identity as inferred (Issue #12). relations.PATH_IDENTITY_SINCE is the same.
PATH_IDENTITY_SINCE = "0.5.0"


# The environment variables the kit, its SDK, the human command line and the aelix extension read
# before the public names became analytics (Issue #9, kit 0.3.0), each with the name that replaced it.
# A reader that finds an old one set (not empty) stops with an error that names the new one before it
# does anything, also when the new one is set too: there are no aliases, and ignoring one would record
# into another project (LAB_PROJECT), as the wrong actor (LAB_ACTOR) or into the user's real trust list
# instead of an isolated one (AELIX_LAB_TRUST_FILE) — ADR-0005 decision 7. The command line and the
# checkout's bin/analytics, which do not load this module, keep their own three of them.
RENAMED_ENV = {
    "LAB_PROJECT": "AELIX_ANALYTICS_PROJECT",
    "LAB_ACTOR": "AELIX_ANALYTICS_ACTOR",
    "LAB_SESSION": "AELIX_ANALYTICS_SESSION",
    "LAB_STYLE": "AELIX_ANALYTICS_STYLE",
    "AELIX_LAB_PROJECT": "AELIX_ANALYTICS_PROJECT",
    "AELIX_LAB_TRUST_FILE": "AELIX_ANALYTICS_TRUST_FILE",
    "AELIX_LAB_KIT": "AELIX_ANALYTICS_RUNTIME_PATH",
    "AELIX_LAB_UV": "AELIX_ANALYTICS_UV",
}
# What one project variable now does for both readers that had a name each.
PROJECT_NOTE = ("the analytics command and the aelix extension both read AELIX_ANALYTICS_PROJECT, "
                "ahead of the working directory")


def renamed_env_message(old: str, *, remedy: bool = True) -> str:
    """What an old variable's error says: its new name, and with `remedy` what to do."""
    new = RENAMED_ENV[old]
    note = f" ({PROJECT_NOTE})" if new == "AELIX_ANALYTICS_PROJECT" else ""
    said = f"{old} was renamed {new} in kit {NAMES_RENAMED_IN}{note}"
    return f"{said}; set {new} instead and unset {old}" if remedy else said


def refuse_renamed_env(names) -> None:
    """Raise env_renamed for the first of `names` (old names, RENAMED_ENV's keys) that is set."""
    for old in names:
        if os.environ.get(old):
            raise AnalyticsError("env_renamed", renamed_env_message(old, remedy=False),
                                 hint=f"set {RENAMED_ENV[old]} instead and unset {old}",
                                 details={"renamed": {old: RENAMED_ENV[old]}})


def expand_home(path: Path) -> Path:
    """`path` with a leading `~name` expanded where that home exists, and otherwise as written, on
    every platform. POSIX finds no home for a user nobody has (pathlib then refuses to expand it),
    and Windows' Python guesses one beside the current user's profile where USERNAME is that profile
    directory's name: an Office lock file `~$budget.xlsx` was C:\\Users\\$budget.xlsx there (Issue
    #8, Windows CI run 35993026796). A home is a directory: a file where the guess lands is not
    one."""
    head = path.parts[0] if path.parts else ""
    if head.startswith("~"):
        with contextlib.suppress(RuntimeError, OSError):
            if os.path.isdir(os.path.expanduser(head)):
                return path.expanduser()
    return path


# --- path identity (ADR-0006 9, Issue #36) ---
#
# A record names a file by one path whichever way a script reached it. Inside the project that is
# the project-relative POSIX path through the namespace: the tree under the root, where a link that
# leads inside is followed to where it leads and a link that leads outside is a boundary, the place
# of a mount, under which its target's tree is seen. Outside it is the absolute path, every name in
# the spelling the file system stores. Three pieces make it: the stored spelling (Spelling), a
# judgment of a path's components (Project._judge) and a walk of the namespace (Project.walk), whose
# table answers which boundary a file outside the root is seen under.


# How long after a directory's last change a listing of it does not trust its modification time:
# a file system stamps a change from a clock tick (Windows' 15.6 ms, Linux' few ms) or keeps coarse
# times (FAT's two seconds), so a change soon after another can leave the time as it was.
RACY_NS = 2_000_000_000


def _settled(mtime: int | None) -> bool:
    """Whether a directory last changed at `mtime` changed long enough ago that a later change will
    show in its modification time (RACY_NS)."""
    return mtime is not None and time.time_ns() - mtime >= RACY_NS


def folded(name: str) -> str:
    """`name` as a file system that ignores case and Unicode normalization compares it."""
    return unicodedata.normalize("NFC", name).casefold()


def _under(child: str, parent: str) -> bool:
    """Whether `child` is `parent` or a path below it, as text (both real paths, spelled alike)."""
    child, parent = _platform.plain_path(child), _platform.plain_path(parent)
    if child == parent:
        return True
    return child.startswith(parent if parent.endswith(os.sep) else parent + os.sep)


def within(child, parent) -> bool:
    """Whether the real path `child` is `parent` or below it (both from canonical): the containment
    the security judgments use."""
    return _under(os.fspath(child), os.fspath(parent))


def _below(child: str, parent: str) -> str:
    """The POSIX path of `child` under `parent`, "" for `parent` itself. `_under` must hold."""
    child, parent = _platform.plain_path(child), _platform.plain_path(parent)
    return child[len(parent):].lstrip(os.sep).replace(os.sep, "/")


def _ns_join(ns: str, rest: str) -> str:
    return f"{ns}/{rest}" if ns and rest else ns or rest


def _split(path: str) -> tuple[str, list[str]]:
    """An absolute path's anchor (`/`, a drive, a UNC share) and its component names, split on
    every separator this platform takes; `.` and `..` are kept."""
    drive, rest = os.path.splitdrive(path)
    seps = os.sep + (os.altsep or "")
    anchor = drive + (os.sep if rest[:1] and rest[0] in seps else "")
    return anchor, [name for name in re.split(f"[{re.escape(seps)}]", rest) if name]


def file_key(path, spelling: Spelling | None = None) -> tuple:
    """Which file `path` leads to, its links followed: the target's `(st_dev, st_ino)`. A file
    system that gives no inode number (`st_ino` 0) is told by the target's real path in its stored
    spelling instead (ADR-0006 9). Raises OSError for a path that leads nowhere."""
    st = os.stat(path)
    if st.st_ino:
        return (st.st_dev, st.st_ino)
    return ("path", canonical(path, spelling))


def same_file(a, b) -> bool:
    """Whether `a` and `b` lead to the same file (file_key); False when either leads nowhere."""
    try:
        return file_key(a) == file_key(b)
    except (OSError, ValueError):
        return False


def same_entry(a, b) -> bool:
    """Whether `a` and `b` name one directory entry, a link being itself (the entry's
    `(st_dev, st_ino)`; same_file where there are no inode numbers)."""
    try:
        x, y = os.lstat(a), os.lstat(b)
    except (OSError, ValueError):
        return False
    if x.st_ino and y.st_ino:
        return (x.st_dev, x.st_ino) == (y.st_dev, y.st_ino)
    return same_file(a, b)


def _kind(entry: os.DirEntry) -> str:
    try:
        if _platform.is_link_entry(entry):
            return "link"
        return "dir" if entry.is_dir(follow_symlinks=False) else "file"
    except OSError:
        return "file"


def _stat_kind(st: os.stat_result) -> str:
    if _platform.is_link(st):
        return "link"
    return "dir" if stat.S_ISDIR(st.st_mode) else "file"


class Spelling:
    """The names a file system stores, read from directory listings (ADR-0006 9).

    A name a directory lists as written is that name. Otherwise, among the names whose NFC case fold
    is the same, the first in code point order that is the same directory entry (the same file where
    there is no inode number). Without a listing or such a name, the name as written. So whether a
    file system ignores case or Unicode normalization is found where it is asked, not assumed per
    platform: where it minds them, another spelling is a file that does not exist.

    One belongs to a Project — one command, one run, one tool call of the extension — and keeps the
    listings it read. A name missing from one is first looked for among the listed names' other
    spellings, and the directory is read again when it changed since (a file a run made), less
    often once it has been read again many times (_read_again). A walk that lists a directory
    refreshes it. A name only its case
    changed while the listing was kept, or one a run made and read under another spelling between
    two readings, can keep the spelling it was read by (docs/CONTRACTS.md G)."""

    def __init__(self) -> None:
        self._listed: dict[str, tuple[int | None, dict[str, str] | None]] = {}
        self._folds: dict[str, dict[str, list[str]]] = {}
        self._paths: dict[str, str] = {}
        self._misses: dict[str, tuple[int, int]] = {}

    def forget_paths(self) -> None:
        self._paths.clear()

    def seed(self, spelled: str) -> None:
        """Take `spelled`, a real path in its stored spelling, as spelled: paths below it are spelled
        from there."""
        self._paths[spelled] = spelled

    def _read(self, directory: str) -> dict[str, str] | None:
        try:
            mtime = os.stat(directory).st_mtime_ns
            with os.scandir(directory) as listing:
                names = {entry.name: _kind(entry) for entry in listing}
        except (OSError, ValueError):
            mtime, names = None, None
        # A listing read soon after the directory changed keeps no time, as a walk's: a name made
        # since may have left the time as it was (RACY_NS).
        self._listed[directory] = (mtime if _settled(mtime) else None, names)
        self._folds.pop(directory, None)
        return names

    def remember(self, directory: str, kinds: dict[str, str]) -> None:
        """Keep a listing a walk read ({name: kind}), with no time: a later miss reads it again."""
        self._listed[directory] = (None, kinds)
        self._folds.pop(directory, None)

    def lookup(self, directory: str, typed: str) -> tuple[str, str | None, bool]:
        """(stored name, kind, listed) of `typed` in the real directory `directory`: kind is
        `link`, `dir` or `file`, None for a name that is not there; `listed` whether the name came
        from a listing (an 8.3 short name Windows opens is not in one)."""
        names = self._listed[directory][1] if directory in self._listed else self._read(directory)
        if names is not None and typed in names:
            return typed, names[typed], True
        try:
            st = os.lstat(os.path.join(directory, typed))
        except (OSError, ValueError):
            return typed, None, False
        kind = _stat_kind(st)
        if names is None:
            return typed, kind, False
        stored = self._variant(directory, names, typed, st)
        if stored is None and self._read_again(directory):
            names = self._read(directory)
            if names is not None and typed in names:
                return typed, names[typed], True
            stored = self._variant(directory, names, typed, st) if names is not None else None
        if stored is not None:
            return stored, names[stored], True
        return typed, kind, False

    def _variant(self, directory: str, names: dict[str, str], typed: str,
                 st: os.stat_result) -> str | None:
        """The listed name that is another spelling of `typed` and the same directory entry."""
        for name in self._index(directory, names).get(folded(typed), ()):
            if self._same_entry(directory, typed, st, name):
                return name
        return None

    # How many times a directory is read again for names it lacked before the readings thin out.
    READS_AT_ONCE = 8

    def _read_again(self, directory: str) -> bool:
        """Whether a name the listing lacks is a reason to read `directory` again: the directory
        changed since the listing (a walk's listing, and one read soon after a change, has no
        time), and this is one of its first
        READS_AT_ONCE such names, or the 2nd, 4th, 8th… after the last reading. So a run that makes
        and reads thousands of files there one at a time lists it a few dozen times, not once per
        file; a name made since and read in between is taken as written (docs/CONTRACTS.md G)."""
        mtime = self._listed[directory][0]
        try:
            if mtime is not None and os.stat(directory).st_mtime_ns == mtime:
                return False
        except OSError:
            return False
        misses, reads = self._misses.get(directory, (0, 0))
        misses += 1
        if misses < 2 ** max(0, reads - self.READS_AT_ONCE + 1):
            self._misses[directory] = (misses, reads)
            return False
        self._misses[directory] = (0, reads + 1)
        return True

    def _index(self, directory: str, names: dict[str, str]) -> dict[str, list[str]]:
        index = self._folds.get(directory)
        if index is None:
            index = {}
            for name in sorted(names):
                index.setdefault(folded(name), []).append(name)
            self._folds[directory] = index
        return index

    @staticmethod
    def _same_entry(directory: str, typed: str, st: os.stat_result, name: str) -> bool:
        try:
            other = os.lstat(os.path.join(directory, name))
        except (OSError, ValueError):
            return False
        if st.st_ino and other.st_ino:
            return (st.st_dev, st.st_ino) == (other.st_dev, other.st_ino)
        return same_file(os.path.join(directory, typed), os.path.join(directory, name))

    def name(self, directory: str, typed: str) -> str:
        return self.lookup(directory, typed)[0]

    def path(self, absolute) -> str:
        """`absolute` (no `.` or `..`) with every component in its stored spelling. The anchor (`/`,
        a drive, a UNC share, an extended-length prefix) is kept as written."""
        absolute = os.fspath(absolute)
        pending = []
        cur = absolute
        while True:
            hit = self._paths.get(cur)
            if hit is not None:
                base = hit
                break
            parent, name = os.path.split(cur)
            if not name or parent == cur:
                base = cur
                break
            pending.append((cur, name))
            cur = parent
        for full, name in reversed(pending):
            base = os.path.join(base, self.name(base, name))
            self._paths[full] = base
        return base


def canonical(path, spelling: Spelling | None = None) -> str:
    """`path` with its links followed as far as they lead and every component in its stored
    spelling: `os.path.realpath` (the same answer on CPython 3.12 and 3.13 for a link loop, where
    `Path.resolve` differs), then Spelling. The security judgments — a script's place, a rerun's,
    a module snapshot's — compare this, never a record's path (ADR-0006 9). Without a Spelling the
    answer is the process's remembered one while the directories it listed are unchanged
    (_spelled)."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        real = os.path.abspath(path)
    return spelling.path(real) if spelling is not None else _spelled(real)


# The stored name of each component of the paths spelled without a Spelling of their own, by
# (directory, name as written), with the directory's modification time: the aelix extension finds
# its project, and asks whether it is trusted, after every tool call on aelix's event loop, and a
# root whose parent holds 200,000 entries would list it each time. A directory is listed again only
# when it changed — the one that changed, not every directory above it.
_NAMES: dict[tuple[str, str], tuple[int, str]] = {}
_NAMES_MAX = 4096


def _stored_name(directory: str, typed: str) -> str:
    """Spelling().name(directory, typed), remembered while the directory keeps its modification
    time; not remembered while that time is less than RACY_NS old."""
    try:
        mtime = os.stat(directory).st_mtime_ns
    except (OSError, ValueError):
        return typed  # no listing to read: the name as written, as Spelling gives it
    kept = _NAMES.get((directory, typed))
    if kept is not None and kept[0] == mtime:
        return kept[1]
    stored = Spelling().name(directory, typed)
    if _settled(mtime):  # else a change soon after may not show in the time
        if len(_NAMES) >= _NAMES_MAX:
            _NAMES.clear()
        _NAMES[(directory, typed)] = (mtime, stored)
    return stored


def _spelled(real: str) -> str:
    """Spelling().path(real), each component's directory listed again only when it changed
    (_stored_name): a stat per parent, not a listing per parent."""
    pending = []
    cur = real
    while True:
        parent, name = os.path.split(cur)
        if not name or parent == cur:
            break
        pending.append(name)
        cur = parent
    for name in reversed(pending):
        cur = os.path.join(cur, _stored_name(cur, name))
    return cur


# A path's place after some of its components: the real directory or file reached (`cur`), whether
# that is in the project's tree ("in"), below a boundary it crossed ("under") or elsewhere ("out"),
# the boundaries crossed ((namespace path, target), ...), and whether `cur` exists.
_State = collections.namedtuple("_State", "cur where crossed exists")


class _Table:
    """What a walk of the namespace found: each directory it walked by file key and by real path,
    with its namespace path; the files it gave through a file link, by (parent key, name); and the
    boundaries it walked, in order."""

    def __init__(self) -> None:
        self.keys: dict[tuple, str] = {}
        self.paths: dict[str, str] = {}
        self.key_of_path: dict[str, tuple] = {}
        self.marks: dict[tuple, str] = {}
        self.boundaries: list[tuple[str, str]] = []
        self.ids: dict[str, str | None] = {}


class Project:
    """Paths of one analytics project, and the platform ports its operations use.

    Construction finds the root's real path in its stored spelling (it lists the root's parents).
    `platform` is the bundle from `_platform`: this process's (`PLATFORM`) unless a test passes
    another implementation, so the same project code runs unchanged on Linux, macOS and Windows."""

    def __init__(self, root, *, platform: _platform.Platform | None = None):
        self.spelling = Spelling()
        self.root = Path(canonical(Path(root).expanduser()))
        self._root = str(self.root)
        self.platform = platform or PLATFORM
        # Both markers a project is found by: the new format's and the older one's.
        self.markers = (self.root / writer.MARKER, self.root / writer.LEGACY_MARKER)
        # The stored names the project's records follow, by its marker now (design 2.3): the older
        # layout for a lab.json project, which this kit reads only, the new format's for every
        # other directory (an analytics.json project, a directory an init is about to make one of).
        self.lay_out(writer.layout_of(writer.marker_state(self.root)) or writer.LAYOUT)
        self.context = self.root / "CONTEXT.md"
        self.journal = self.root / writer.JOURNAL
        self.data = self.root / "data"
        self.derived = self.data / "derived"
        self.scripts = self.root / "scripts"
        self.runs = self.root / "runs"
        # The older layout's state directory, which readers read beside the new one (its CONTEXT
        # versions and logs, design 5.3): never written by this kit.
        self.legacy_state_dir = self.root / writer.LEGACY_LAYOUT.state_dir
        # The request_id of the init that made this project (init_project), for that init's answer.
        self.request_id: str | None = None
        self._judged: dict[str, _State] = {}
        self._keys: dict[str, tuple | None] = {}
        self._managed_places: tuple[list, list] | None = None
        self._kit_names: dict[str, str | None] = {}
        self._table: _Table | None = None
        self._seed()

    def lay_out(self, layout) -> None:
        """The paths of the stored names that differ between the two layouts (design 2.3), by
        `layout`: at construction, and again when a write is admitted under a marker of the other
        layout (admit, design 4.3 and S9: what a write writes follows the marker it was admitted
        by, not the one the Project saw when it was made)."""
        self.layout = layout
        self.marker = self.root / layout.marker
        self.imported = self.root / layout.imported_dir
        self.workspace_dir = self.root / layout.workspace_dir
        self.state_dir = self.root / layout.state_dir
        self.cache = self.state_dir / "cache"
        self.context_history = self.state_dir / "context"
        self.telemetry = self.state_dir / "telemetry.jsonl"
        self.queries = self.state_dir / "queries.jsonl"
        # The managed directories a judgment names are the layout's (_managed, _kit_name).
        self._managed_places = None
        self._kit_names = {}

    def path(self, rel) -> Path:
        p = expand_home(Path(rel))
        return p if p.is_absolute() else self.root / p

    def canonical(self, path) -> str:
        return canonical(path, self.spelling)

    def rel(self, path) -> str:
        """The path a record holds for `path` (ADR-0006 9): project-relative POSIX through the
        namespace, or absolute outside the project."""
        return self.locate(path)[0]

    def locate(self, path) -> tuple[str, str]:
        """(the path a record holds, the real path) for `path`, as the namespace is now: what was
        judged through a link is not kept (_judge), so a link a run repoints leads where it leads
        when the run reads through it."""
        state = self._judge(os.fspath(self.path(path)))
        return self._identity(state), state.cur

    def inside(self, path) -> bool:
        """Whether `path` leads, its links followed, into the project: a security judgment, so by
        the real path (canonical), never by the namespace."""
        return _under(self.canonical(self.path(path)), self._root)

    def below(self, path) -> tuple[str, str] | None:
        """`path` below the root as (its first name, the rest as POSIX), told from its text without
        a walk or a directory listing: the path made absolute with `.` and `..` folded as text, under
        the root (in any case or normalization that is the root's directory), else its real path
        (links followed) under the root. A first name that is a managed directory in another
        spelling is that directory's (the layout's managed_dirs). None for a path not below the root. The
        extension's telemetry asks this on aelix's event loop after every write: an estimate of
        where a write went, never a record's path."""
        absolute = os.path.normpath(os.fspath(self.path(path)))
        names = self._names_below(absolute)
        if names is None:
            with contextlib.suppress(OSError, ValueError):
                names = self._names_below(os.path.realpath(absolute))
        if not names:
            return None
        return self._kit_name(names[0]) or names[0], "/".join(names[1:])

    def _names_below(self, absolute: str) -> list[str] | None:
        anchor, names = _split(absolute)
        root_anchor, root_names = _split(self._root)
        depth = len(root_names)
        if len(names) < depth or folded(anchor) != folded(root_anchor) \
                or list(map(folded, names[:depth])) != list(map(folded, root_names)):
            return None
        if (anchor, names[:depth]) != (root_anchor, root_names) \
                and not same_file(anchor + os.sep.join(names[:depth]), self._root):
            return None
        return names[depth:]

    def forget(self, *, listings: bool = True) -> None:
        """Drop what was judged so far, and with `listings` the directory listings, keeping the
        walk's table: a run's script may make, remove or repoint a link, and what it reads is
        judged as the namespace is when it reads (docs/CONTRACTS.md G)."""
        self._judged.clear()
        self._keys.clear()
        self._managed_places = None
        self._kit_names.clear()
        if listings:
            self.spelling = Spelling()
        else:
            self.spelling.forget_paths()
        self._seed()

    def _seed(self) -> None:
        """Start the judgments and the spelling at the root, already spelled (canonical): a path
        written below it is judged without listing the root's parents, as main resolved it."""
        if os.path.isdir(self._root):
            self._judged[self._root] = _State(self._root, "in", (), True)
            self.spelling.seed(self._root)

    def identity_of_real(self, real) -> str | None:
        """The path a record holds for a real path (canonical) inside the project or below a
        boundary the walk found; None for any other."""
        real = os.fspath(real)
        if _under(real, self._root):
            return self._inside_identity(real)
        return self._from_table(real)

    def legacy_identity(self, recorded) -> str | None:
        """What a path a kit before PATH_IDENTITY_SINCE recorded is now: those kits recorded the
        real path (links followed), relative to the root (POSIX) inside it and absolute outside,
        each name as typed where the file system ignores case or Unicode normalization. Spelled as
        stored, it is the project-relative path inside the root, the path below the first boundary
        that reaches it as the namespace is linked now, else None. A relative path is one under the
        root (the owner's decision of 2026-10-06 on Issue #12); one such a kit could not have
        written — with an empty, `.` or `..` part, a backslash (those kits wrote `/` between the
        parts on every platform; a POSIX name with one is left as recorded too), a drive (`C:x`), a
        URI, a path absolute on another platform — is None. An inference from today's links and
        names: the read model (relations) reads it as `basis: inferred`, and only when it names an
        entry or a run's recorded output."""
        if not isinstance(recorded, str) or not recorded or "\x00" in recorded:
            return None
        if os.path.isabs(recorded):
            absolute = recorded
        elif "://" in recorded or "\\" in recorded or PureWindowsPath(recorded).drive \
                or _platform.absolute_record_path(recorded) \
                or any(part in ("", ".", "..") for part in recorded.split("/")):
            return None
        else:
            absolute = os.path.join(self._root, *recorded.split("/"))
        try:
            return self.identity_of_real(self.spelling.path(os.path.normpath(absolute)))
        except (OSError, ValueError):
            return None

    def boundaries(self) -> tuple:
        """The boundaries the last walk of the namespace went through, (namespace path, target)."""
        return tuple(self._table.boundaries) if self._table is not None else ()

    # -- the judgment of one path's components

    def _key(self, path: str) -> tuple | None:
        if path not in self._keys:
            try:
                self._keys[path] = file_key(path, self.spelling)
            except (OSError, ValueError):
                self._keys[path] = None
        return self._keys[path]

    def _judge(self, absolute: str) -> _State:
        """Where `absolute` leads, decided a component at a time from the front: links before the
        root are followed, a link inside it that leads inside is followed to where it leads, one
        that leads outside is a boundary crossed, and `..` is the parent of the directory reached
        (on Windows already folded as text, as Win32 opens it). The answer for each directory
        reached through plain directories alone is kept; one at or after a link is not, so a link
        repointed since is followed to where it leads now."""
        anchor, names = _split(_platform.opened_path(absolute))
        prefixes = [anchor + os.sep.join(names[:i]) if i else anchor for i in range(len(names) + 1)]
        i, state = len(names), None
        while i >= 0 and state is None:
            state = self._judged.get(prefixes[i])
            if state is None:
                i -= 1
        if state is None:
            start = self.canonical(anchor or os.sep)
            state, i = _State(start, "in" if start == self._root else "out", (), True), 0
        kept = True
        for j in range(i, len(names)):
            state, through_link = self._step(state, names[j])
            kept = kept and not through_link
            if kept and state.exists:
                self._judged[prefixes[j + 1]] = state
        return state

    def _step(self, state: _State, name: str) -> tuple[_State, bool]:
        """The state past `name`, and whether that step went through a link."""
        cur, where, crossed, exists = state
        if not exists:
            return _State(os.path.join(cur, name), where, crossed, False), False
        if name == ".":
            return state, False
        if name == "..":
            return self._placed(os.path.dirname(cur), crossed), False
        stored, kind, listed = self.spelling.lookup(cur, name)
        nxt = os.path.join(cur, stored)
        if kind is None:
            return _State(nxt, where, crossed, False), False
        if kind == "link":
            target = self.canonical(nxt)
            if _under(target, self._root):
                return _State(target, "in", crossed, True), True
            if where != "out" and not _under(self._root, target):
                boundary = (_ns_join(self._ns_of(state), stored), target)
                return _State(target, "under", (*crossed, boundary), True), True
            return self._placed(target, crossed), True
        if not listed:
            nxt = self.canonical(nxt)  # a name no listing holds (an 8.3 short name): the real one
        if where == "out" and (nxt == self._root or (kind == "dir" and self._is_root(nxt))):
            return _State(self._root, "in", crossed, True), False  # the root under another name
        return _State(nxt, where, crossed, True), False

    def _is_root(self, path: str) -> bool:
        key = self._key(path)
        return key is not None and key == self._key(self._root)

    def _placed(self, cur: str, crossed: tuple) -> _State:
        if _under(cur, self._root):
            return _State(cur, "in", crossed, True)
        if any(_under(cur, target) for _, target in crossed):
            return _State(cur, "under", crossed, True)
        return _State(cur, "out", crossed, True)

    def _ns_of(self, state: _State) -> str:
        """The namespace path of a directory reached in or under the project, from where it is."""
        if state.where == "in":
            ns = self._inside_identity(state.cur)
            return "" if ns == "." else ns
        for ns, target in reversed(state.crossed):
            if _under(state.cur, target):
                return _ns_join(ns, _below(state.cur, target))
        return state.cur

    def _identity(self, state: _State) -> str:
        if state.where == "in":
            return self._inside_identity(state.cur)
        if state.where == "under":
            found = self._from_table(state.cur)
            if found is not None:
                return found
            # Below a boundary no walk reached (one inside an ignored directory, or one made since
            # the walk): the first, in code point order, of the boundaries this path crossed.
            reaching = [(ns, target) for ns, target in state.crossed if _under(state.cur, target)]
            if reaching:
                ns, target = min(reaching)
                return _ns_join(ns, _below(state.cur, target))
        return state.cur

    def _managed(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """The managed top-level directories (the layout's managed_dirs) that are links, by where
        they lead: ([(name, real path)] inside the root, [(name, real path)] outside it)."""
        if self._managed_places is None:
            inward: list[tuple[str, str]] = []
            outward: list[tuple[str, str]] = []
            for name in sorted(self.layout.managed_dirs):
                place = os.path.join(self._root, name)
                try:
                    if not _platform.is_link(os.lstat(place)):
                        continue
                    real = self.spelling.path(os.path.realpath(place, strict=True))
                except (OSError, ValueError):
                    continue
                (inward if _under(real, self._root) else outward).append((name, real))
            self._managed_places = (inward, outward)
        return self._managed_places

    def _kit_name(self, name: str) -> str | None:
        """The managed directory (the layout's managed_dirs) the top-level entry `name` is when it
        is stored in another case or normalization of that directory's name (`Runs` where the file
        system ignores case): its name folds to it and it is the same entry. None for any other
        name."""
        managed = self.layout.managed_dirs
        if name in managed or folded(name) not in managed:
            return None
        if name not in self._kit_names:
            kit = folded(name)
            self._kit_names[name] = kit if same_entry(os.path.join(self._root, name),
                                                      os.path.join(self._root, kit)) else None
        return self._kit_names[name]

    def _inside_identity(self, real: str) -> str:
        """The path a record holds for a real path inside the root: its project-relative path, but
        below a managed directory by that directory's name — where it is a link to this place, and
        where it is stored in another case — so the run's own `runs/<id>/` is told as such wherever
        runs/ leads and however it is spelled."""
        for name, place in self._managed()[0]:
            if _under(real, place):
                return _ns_join(name, _below(real, place))
        rel = _below(real, self._root)
        if not rel:
            return "."
        first, sep, rest = rel.partition("/")
        kit = self._kit_name(first)
        return f"{kit}{sep}{rest}" if kit else rel

    def _from_table(self, real: str) -> str | None:
        """The namespace path the walk gave the real path `real`: the file link that gave the file,
        else the closest directory it walked above it (or the directory itself). None when the walk
        reached none of them."""
        table = self._ensure_table()
        if table.marks:
            parent, name = os.path.split(real)
            key = self._key(parent)
            mark = table.marks.get((key, name)) if key is not None else None
            if mark is not None:
                return mark
        rest: list[str] = []
        place = real
        while True:
            if place in table.ids:
                ns = table.ids[place]
            else:
                ns = table.paths.get(place)
                if ns is None:
                    key = self._key(place)
                    ns = table.keys.get(key) if key is not None else None
                table.ids[place] = ns
            if ns is not None:
                return _ns_join(ns, "/".join(reversed(rest)))
            parent, name = os.path.split(place)
            if not name or parent == place:
                return None
            rest.append(name)
            place = parent

    # -- the walk

    def walk(self, start=None):
        """The files of the namespace, each once, as (path a record holds, real path, directory
        entry or None).

        Without `start`: the project's tree (the ignored top-level names, directories or links,
        and `__pycache__` and `.ipynb_checkpoints` at any depth, left out), then each boundary — a
        link in the namespace that leads outside the root, not to the root, one of its ancestors or
        a place a managed directory leads to — in code point order of its namespace path, once per
        target: a directory boundary whose target a walk reached already is not walked again, nor is
        a file link whose file it gave. Links that lead inside, dangling links and loops are not
        followed. The walk keeps its table, which `rel` reads, so the path the walk gives a file is
        the path `rel` gives it, whichever way it is named.

        With `start` (a path, relative to the root or absolute): the tree under the directory it
        leads to, by the same rule, as (path a record holds, real path, name, entry or None, place)
        — the name is the entry's own in the namespace, a link's for a file link, and the place is
        where the walk listed that entry (its namespace path; absolute outside the project). Depth
        first, a directory's entries in code point order of their names, so a caller can stop early.
        A file link leading inside is given too, as the file it leads to. Nothing is left out by
        name, and a file is given once per (name, path). A start that leads to the root or one of
        its ancestors gives nothing, as a link there is not followed, and a link back to the start's
        directory or above it is not followed either."""
        if start is None:
            return self._walk_namespace()
        return self._walk_from(start)

    def _ensure_table(self) -> _Table:
        if self._table is None:
            for _ in self._walk_namespace():
                pass
        return self._table

    def _claim(self, table: _Table, path: str, ns: str) -> bool:
        key = self._key(path)
        if key is None or key in table.keys:
            return False
        table.keys[key] = ns
        table.paths[path] = ns
        table.key_of_path[path] = key
        return True

    def _classify(self, link: str) -> tuple[str, str] | None:
        """("inward" | "dir" | "file", real target) of a link, None for one the walk does not
        follow: dangling, a loop, to the root or an ancestor of it, or to a place a managed
        directory leads to."""
        try:
            target = self.spelling.path(os.path.realpath(link, strict=True))
        except (OSError, ValueError):
            return None
        if _under(target, self._root):
            return "inward", target
        if _under(self._root, target) or any(_under(target, place)
                                              for _, place in self._managed()[1]):
            return None
        return ("dir" if os.path.isdir(target) else "file"), target

    def _listed(self, directory: str) -> list[tuple[os.DirEntry, str]]:
        """The entries of `directory` in code point order of their names, each with its kind; the
        listing is kept for the spelling. Nothing for a directory that cannot be listed."""
        try:
            with os.scandir(directory) as listing:
                entries = sorted(listing, key=lambda entry: entry.name)
        except (OSError, ValueError):
            return []
        kinds = {entry.name: _kind(entry) for entry in entries}
        self.spelling.remember(directory, kinds)
        return [(entry, kinds[entry.name]) for entry in entries]

    def _walk_namespace(self):
        table = _Table()
        self.forget(listings=False)
        self._claim(table, self._root, "")
        # What a managed directory is, wherever it leads, is that directory: no boundary is walked
        # into it (so a run's own files are never seen under data/).
        for name in sorted(self.layout.managed_dirs):
            with contextlib.suppress(OSError, ValueError):
                self._claim(table, self.spelling.path(os.path.realpath(
                    os.path.join(self._root, name), strict=True)), name)
        pending: list[tuple[str, str, str]] = []
        yield from self._walk_tree(table, self._root, "", pending)
        while pending:
            ns, kind, target = heapq.heappop(pending)
            if kind == "dir":
                if self._claim(table, target, ns):
                    table.boundaries.append((ns, target))
                    yield from self._walk_tree(table, target, ns, pending)
                continue
            parent, name = os.path.split(target)
            key = self._key(parent)
            if key is None or key in table.keys or (key, name) in table.marks:
                continue  # given already, under a directory walked or through an earlier link
            table.marks[(key, name)] = ns
            table.boundaries.append((ns, target))
            yield ns, target, None
        self._table = table

    def _walk_tree(self, table: _Table, top: str, top_ns: str, pending: list):
        stack = [(top, top_ns)]
        while stack:
            directory, ns = stack.pop()
            key = table.key_of_path.get(directory)
            subdirectories = []
            for entry, kind in self._listed(directory):
                # An ignored top-level name is left out as a directory or a link, as on main; a
                # managed directory stored in another case is that directory.
                if not ns and (self._kit_name(entry.name) or entry.name) in self.layout.ignored_dirs:
                    continue
                entry_ns = _ns_join(ns, entry.name)
                if kind == "link":
                    found = self._classify(entry.path)
                    if found is not None and found[0] != "inward":
                        heapq.heappush(pending, (entry_ns, *found))
                elif kind == "dir":
                    if entry.name in SKIPPED_DIRS:
                        continue
                    if self._claim(table, entry.path, entry_ns):
                        subdirectories.append((entry.path, entry_ns))
                elif not (table.marks and (key, entry.name) in table.marks):
                    yield entry_ns, entry.path, entry
            stack.extend(reversed(subdirectories))

    def _through(self, state: _State, name: str, target: str) -> _State:
        """The state past a link named `name` in the directory `state` reached, to an outward
        `target`."""
        if state.where != "out":
            boundary = (_ns_join(self._ns_of(state), name), target)
            return _State(target, "under", (*state.crossed, boundary), True)
        return self._placed(target, state.crossed)

    def _frame(self, state: _State) -> tuple[_State, str | None, object]:
        """A directory the walk from a start goes into: its state, its namespace path (None outside
        the project) and its entries still to visit."""
        ns = None if state.where == "out" else self._ns_of(state)
        return state, ns, iter(self._listed(state.cur))

    def _walk_from(self, start):
        top = self._judge(os.fspath(self.path(start)))
        # The rule a link in the walk is not followed by holds for the start too: `data/raw → /`
        # would walk the whole file system.
        if not top.exists or _under(self._root, top.cur) or not os.path.isdir(top.cur):
            return
        seen_directories = {self._key(top.cur)}
        given: set[tuple[str, str]] = set()
        stack = [self._frame(top)]
        while stack:
            state, ns, entries = stack[-1]
            listed = next(entries, None)
            if listed is None:
                stack.pop()
                continue
            entry, kind = listed
            place = os.path.join(state.cur, entry.name) if ns is None else _ns_join(ns, entry.name)
            if kind == "dir":
                key = self._key(entry.path)
                if key is not None and key not in seen_directories:
                    seen_directories.add(key)
                    stack.append(self._frame(state._replace(cur=entry.path)))
                continue
            if kind == "link":
                found = self._classify(entry.path)
                if found is None:
                    continue
                how, target = found
                if how == "dir":
                    if _under(top.cur, target):
                        continue  # back up to the start or above it: walked already, or not ours
                    key = self._key(target)
                    if key is not None and key not in seen_directories:
                        seen_directories.add(key)
                        stack.append(self._frame(self._through(state, entry.name, target)))
                    continue
                if how == "inward":
                    if os.path.isdir(target):
                        continue
                    item = (self._inside_identity(target), target, entry.name, None, place)
                else:
                    item = (self._identity(self._through(state, entry.name, target)), target,
                            entry.name, None, place)
            else:
                item = (self._identity(state._replace(cur=entry.path)), entry.path, entry.name,
                        entry, place)
            if (item[2], item[0]) not in given:
                given.add((item[2], item[0]))
                yield item

    def marker_state(self) -> writer.MarkerState:
        """What this directory is by its markers, read now (writer.marker_state)."""
        return writer.marker_state(self.root)

    def config(self) -> dict:
        """The marker's JSON object, as it is read: a lab.json that is not one is read as an empty
        one (as before #13), and a marker this kit does not write or know gives what it holds — the
        title, the creation time — to the readers that show it; no marker gives an empty one. Two
        markers are refused (marker_conflict): which one is the project's cannot be told."""
        state = self.marker_state()
        if state.state == writer.CONFLICT:
            raise writer.refusal(state, "read", writer_version=KIT_VERSION, writable=frozenset())
        return dict(state.value) if isinstance(state.value, dict) else {}

    def title(self) -> str:
        return str(self.config().get("title") or self.root.name)


# --- small file helpers ---

def now() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="milliseconds")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, obj, *, platform: _platform.Platform | None = None,
               through_link: bool = False) -> None:
    """writer.write_json with this process's platform by default (PLATFORM)."""
    writer.write_json(path, obj, platform=platform or PLATFORM, through_link=through_link)


def read_jsonl(path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError:
        pass
    return out


def reject_unportable_name(name, *, kind: str, exists: bool = False) -> None:
    """Refuse a NEW name that Windows cannot use as a file name.

    `con`, `nul`, `aux`, `com1` and their relatives are device names on Windows whatever the
    extension is, so a project created on Linux with one of them cannot be opened there. A name the
    project already has is never refused: existing records stay readable and re-runnable on every
    platform, because a record outlives the machine that wrote it."""
    if exists or not _platform.reserved_name(name):
        return
    raise AnalyticsError("name_not_portable",
                         f"{kind} name {name!r} is a reserved device name on Windows",
                         hint=f"choose another name, for example {name}_1; records that already use this "
                              "name keep working, but a new one has to be usable on Linux, macOS and "
                              "Windows")


def slugify(text, max_len: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s[:max_len].strip("-")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


# A change of line endings alone (ADR-0007 decision 4): judged only for a file of at most
# LINE_ENDINGS_MAX_BYTES with no NUL in its first LINE_ENDINGS_SNIFF_BYTES, read in blocks of
# LINE_ENDINGS_BLOCK so the aelix process's memory stays bounded.
LINE_ENDINGS_ONLY = "line_endings_only"
LINE_ENDINGS_MAX_BYTES = 64 << 20
LINE_ENDINGS_SNIFF_BYTES = 8 << 10
LINE_ENDINGS_BLOCK = 1 << 20


def _line_ending_digests(blocks) -> tuple[str, str] | None:
    """The two digests line_ending_digests describes, of the bytes `blocks` yields in order (the
    first one is the head the NUL rule reads)."""
    as_lf, as_crlf = hashlib.sha256(), hashlib.sha256()
    held, total, first = b"", 0, True
    for block in blocks:
        total += len(block)
        if total > LINE_ENDINGS_MAX_BYTES:
            return None
        if first and b"\0" in block[:LINE_ENDINGS_SNIFF_BYTES]:
            return None
        first = False
        data = held + block
        # A CR at the end of a block may start a CRLF the next block ends: keep it for that block.
        held, data = (b"\r", data[:-1]) if data.endswith(b"\r") else (b"", data)
        lf = data.replace(b"\r\n", b"\n")
        as_lf.update(lf)
        as_crlf.update(lf.replace(b"\n", b"\r\n"))
    as_lf.update(held)
    as_crlf.update(held)
    return as_lf.hexdigest(), as_crlf.hexdigest()


def line_ending_digests(path) -> tuple[str, str] | None:
    """sha256 of a file's bytes (1) with every CRLF as LF and (2) with every line end of (1) as CRLF,
    or None for a file this rule does not judge: larger than LINE_ENDINGS_MAX_BYTES or a NUL in its
    first LINE_ENDINGS_SNIFF_BYTES. (2) is made from (1), not from the bytes, so a CRLF already there
    does not become CR CR LF, and a file whose line ends are mixed now is matched by a record of
    all-LF or all-CRLF bytes. A lone CR stays a lone CR in both. A file that cannot be read raises
    OSError: that says nothing of the file's bytes, so the caller keeps nothing for it."""
    path = Path(path)
    if path.stat().st_size > LINE_ENDINGS_MAX_BYTES:
        return None
    with open(path, "rb") as fh:
        def blocks():
            yield fh.read(LINE_ENDINGS_SNIFF_BYTES)
            while block := fh.read(LINE_ENDINGS_BLOCK):
                yield block
        return _line_ending_digests(blocks())


def line_ending_digests_of(data: bytes) -> tuple[str, str] | None:
    """line_ending_digests of bytes already read (CONTEXT.md in sync_context), by the same rules."""
    head, rest = data[:LINE_ENDINGS_SNIFF_BYTES], data[LINE_ENDINGS_SNIFF_BYTES:]
    return _line_ending_digests(iter((head, rest)))


class HashCache:
    """sha256 of files keyed by (size, mtime_ns), persisted in <state_dir>/cache/hashes.json.

    An entry is [size, mtime_ns, sha256], and once line_endings_only has read the file at that size
    and time a fourth value: [lf, crlf] (line_ending_digests) or null (a file the rule does not
    judge: over the size limit or a NUL in its head). A file that could not be read gets no fourth
    value, so the next look reads it again. A kit that does not know the fourth value reads such an
    entry as missing and hashes the file again, which drops it: a cost, nothing more (cache/ is
    derived)."""

    def __init__(self, project: Project):
        self.file = project.cache / "hashes.json"
        self.root = project.root
        self.platform = project.platform
        data = read_json(self.file, {})
        self.data = data if isinstance(data, dict) else {}
        self.dirty = False

    def _entry(self, key: str, st) -> list | None:
        entry = self.data.get(key)
        if isinstance(entry, list) and len(entry) in (3, 4) and entry[0] == st.st_size \
                and entry[1] == st.st_mtime_ns:
            return entry
        return None

    def sha(self, path) -> str | None:
        path = Path(path)
        try:
            st = path.stat()
        except OSError:
            return None
        if not path.is_file():
            return None
        key = str(path)
        entry = self._entry(key, st)
        if entry is not None:
            return entry[2]
        try:
            digest = sha256_file(path)
        except OSError:
            return None
        self.data[key] = [st.st_size, st.st_mtime_ns, digest]
        self.dirty = True
        return digest

    def line_endings_only(self, path, recorded) -> bool:
        """Whether a file whose bytes differ from `recorded` (a sha256) differs only in its line
        ends: the CRLF-as-LF or all-CRLF form of its bytes (line_ending_digests) hashes to
        `recorded` (ADR-0007 decision 4). The caller asks only for a file it found `changed`. The two
        digests are kept with the file's entry, but only when the file's size and time were the
        same before and after the read: a file that changed while it was read gets no reason, and
        so does one that could not be read, with nothing kept for it."""
        if not recorded:
            return False
        path = Path(path)
        try:
            before = path.stat()
        except OSError:
            return False
        key = str(path)
        entry = self._entry(key, before)
        if entry is not None and len(entry) == 4:
            variants = entry[3]
            return isinstance(variants, list) and recorded in variants
        try:
            digests = line_ending_digests(path)
            after = path.stat()
        except OSError:
            return False  # not read: no reason, and nothing kept for this size and time
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            return False
        entry = self._entry(key, after)
        if entry is not None:
            self.data[key] = [*entry[:3], list(digests) if digests else None]
            self.dirty = True
        return bool(digests) and recorded in digests

    def save(self) -> bool:
        """Keep what was hashed, when anything was: a side write, skipped where this kit does not
        write (admit, design 4.10). Whether it was skipped."""
        if not self.dirty:
            return False
        skipped = admit(self.root, "keep the hash cache", side=True) is None
        if not skipped:
            with contextlib.suppress(OSError):
                write_json(self.file, self.data, platform=self.platform)
        self.dirty = False
        return skipped


def run_lock(project: Project):
    """Serializes runs of one project (see runner.execute): the layout's run lock."""
    return project.platform.locks.exclusive(project.state_dir / project.layout.run_lock)


# --- trust: which projects an aelix session may activate just by being inside them ---

# The trust list this kit writes (analytics.trust/1, design 2.7), and the one kits before the new
# writer wrote (lab.trust/1), which this kit reads and never writes (design 6.14).
TRUST_FILE = "analytics-projects.json"
LEGACY_TRUST_FILE = "lab-projects.json"


def _agent_dir() -> Path:
    agent_dir = os.environ.get("AELIX_CODING_AGENT_DIR")
    return Path(agent_dir).expanduser() if agent_dir else Path.home() / ".aelix" / "agent"


def trust_file() -> Path:
    """The trust list this kit reads and writes: AELIX_ANALYTICS_TRUST_FILE, the one list when it is
    set, else analytics-projects.json in the host's agent directory."""
    refuse_renamed_env(("AELIX_LAB_TRUST_FILE",))
    explicit = os.environ.get("AELIX_ANALYTICS_TRUST_FILE")
    if explicit:
        return Path(explicit).expanduser()
    return _agent_dir() / TRUST_FILE


def legacy_trust_file() -> Path | None:
    """The trust list of the kits before the new writer (lab-projects.json beside trust_file), read
    only and only for lab.json projects (design 6.14); None where AELIX_ANALYTICS_TRUST_FILE names
    the one list."""
    refuse_renamed_env(("AELIX_LAB_TRUST_FILE",))
    if os.environ.get("AELIX_ANALYTICS_TRUST_FILE"):
        return None
    return _agent_dir() / LEGACY_TRUST_FILE


def _listed_roots(path: Path | None) -> set[str]:
    """The `projects` of a trust list of either family (analytics.trust/1, lab.trust/1)."""
    data = read_json(path, {}) if path is not None else {}
    roots = data.get("projects") if isinstance(data, dict) else None
    return {str(r) for r in roots} if isinstance(roots, list) else set()


def trusted_roots() -> set[str]:
    """The roots the trust list this kit writes holds (trust_file)."""
    return _listed_roots(trust_file())


# What is_trusted answered, for as long as the trust lists are what they were then: the aelix
# extension asks before every prompt and after every tool call.
_TRUSTED: dict = {}


def _stamp(path: Path | None) -> tuple:
    if path is None:
        return (None,)
    try:
        st = os.stat(path)
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None, None)


def is_trusted(root) -> bool:
    """Whether a trust list holds `root`: its real path in its stored spelling (canonical), or an
    entry that is that path once spelled as stored — an entry a kit before 0.5.0 wrote as typed
    (another case on macOS). An entry was a real path when written, so it is not followed again.
    The list this kit writes (trust_file) trusts any project; the older kits' list
    (legacy_trust_file) only a lab.json project, which this kit reads only: a path chosen for an
    older kit never activates a project of the new format by itself (design 6.14)."""
    try:
        resolved = canonical(Path(root).expanduser())
    except (OSError, RuntimeError, ValueError):
        return False
    file, legacy_file = trust_file(), legacy_trust_file()
    stamp = (_stamp(file), _stamp(legacy_file))
    if _TRUSTED.get("stamp") != stamp:
        _TRUSTED.clear()
        _TRUSTED["stamp"] = stamp
    older = writer.layout_of(writer.marker_state(resolved)) is writer.LEGACY_LAYOUT
    answers = _TRUSTED.setdefault("answers", {})
    if (resolved, older) not in answers:
        roots = _listed_roots(file) | (_listed_roots(legacy_file) if older else set())
        spelling = Spelling()
        answers[(resolved, older)] = resolved in roots or any(
            spelling.path(os.path.normpath(r)) == resolved for r in roots if os.path.isabs(r))
    return answers[(resolved, older)]


def trust_project(root, *, platform: _platform.Platform | None = None) -> None:
    """Remember a project the user created or chose, so starting aelix inside it activates it: in
    the list this kit writes (trust_file), as analytics.trust/1 whichever family it held, never in
    the older kits' list. A list that holds the root already is left as it is. A trust list that is
    a link to a file (kept with dotfiles, say) is written at that file."""
    resolved = canonical(Path(root).expanduser())
    roots = trusted_roots()
    if resolved in roots:
        return
    write_json(trust_file(), {"schema": SCHEMA_TRUST, "projects": sorted(roots | {resolved})},
               platform=platform, through_link=True)


def find_project(start=None) -> Path | None:
    """The nearest directory at or above `start` (the working directory by default) that holds a
    project marker, analytics.json or lab.json (writer.find_project): it is judged afterwards, so a
    directory with an analytics.json below a lab.json project is found, never the project above."""
    try:
        here = Path(canonical(Path(start or os.getcwd()).expanduser()))
    except OSError:
        return None
    return writer.find_project(here)


def system_origin(origin: Origin) -> Origin:
    """Who the kit's own writes are by when an entry point's request makes them (the context sync
    before a write, design 4.1): `system`, with the entry point's means and session."""
    return Origin("system", origin.means, "declared", origin.session)


# --- the gate (Issue #13, design 4.3) ---

def admit(project, op: str, *, side: bool = False) -> writer.MarkerState | None:
    """writer.admit for this kit (KIT_VERSION) and a Project or a root: before the first side effect
    of the write `op`. A refused write raises; a refused side write (`side`) gives None. A Project
    admitted under a marker of another layout than the one it was made with (a lab.json replaced
    by an analytics.json meanwhile) takes that marker's paths before the write (Project.lay_out)."""
    root = project.root if isinstance(project, Project) else project
    state = writer.admit(root, op, side=side, writer_version=KIT_VERSION)
    if isinstance(project, Project) and state is not None:
        layout = writer.layout_of(state)
        if layout is not None and layout is not project.layout:
            project.lay_out(layout)
    return state


def readable(project, op: str) -> writer.MarkerState:
    """writer.readable for a Project or a root: no_project and marker_conflict are refused."""
    root = project.root if isinstance(project, Project) else project
    return writer.readable(root, op)


# --- journal ---

def _journal_bytes(journal: Path) -> bytes:
    """The journal's bytes; none when there is no journal yet."""
    try:
        return journal.read_bytes()
    except FileNotFoundError:
        return b""


def _last_seq(journal: Path) -> int:
    """The highest seq of the journal as the reader counts it (relations.Journal.as_of_seq, ADR-0006
    6): of every parsed line and every record recovered from a line a /1 writer appended onto,
    unsupported ones included; 0 when there is none. A seq left in a cut-off line is not counted."""
    return relations.Journal(_journal_bytes(journal)).as_of_seq


def schema_version(value) -> tuple[str, int] | None:
    """('lab.event', 1) for 'lab.event/1'; None for anything else."""
    if not isinstance(value, str) or "/" not in value:
        return None
    family, _, version = value.rpartition("/")
    return (family, int(version)) if version.isdigit() else None


def newer_schema(value, known: str) -> bool:
    """True when `value` is the same schema family as `known`, at a version this kit cannot read.

    Records are written by whichever kit version was installed at the time, and projects outlive
    kit versions, so a record must never be read as if it were a version it is not."""
    got, want = schema_version(value), schema_version(known)
    return bool(got and want and got[0] == want[0] and got[1] > want[1])


# The kinds of the lists: Workflow feedback is `gap` in lab.event/1 and `workflow_feedback` in
# analytics.event/2 (TERMINOLOGY 4).
FEEDBACK_KINDS = ("gap", "workflow_feedback")


def _refs_view(refs) -> dict:
    """An analytics.event/2 record's references in the shape of a lab.event/1 `refs` object, for the
    lists and pages only (design 5.2): runs, events, charts (a logical key, or `<run>/<id>` of a bound
    version), data, context, resolves and the rest under `other`. What each reference is, is the
    read model's judgment (relations.ReadModel.references), not this."""
    out: dict[str, list] = {}
    for index, ref in enumerate(refs if isinstance(refs, list) else []):
        if not isinstance(ref, dict):
            continue
        kind, relation = ref.get("type"), ref.get("relation")
        if kind == "run" and isinstance(ref.get("run_id"), str):
            out.setdefault("runs", []).append(ref["run_id"])
        elif kind in relations.EVENT_TARGETS and isinstance(ref.get("event_id"), str):
            if relation == "resolves":
                out["resolves"] = ref["event_id"]
            else:
                out.setdefault("events", []).append(ref["event_id"])
        elif kind == "context_version" and isinstance(ref.get("version"), str):
            out["context"] = ref["version"]
        elif kind == "visualization_version":
            out.setdefault("charts", []).append(reference_label({"target": ref}))
        elif kind in ("dataset_import", "data_observation"):
            out.setdefault("data", []).append(reference_label({"target": ref}))
        else:
            target = {k: v for k, v in ref.items()
                      if k not in ("type", "relation", "basis", "version_binding")}
            out.setdefault("other", []).append(reference_label({"target": target,
                                                                "ref": {"index": index}}))
    return out


def event_view(event: relations.Event) -> dict:
    """A journal record as the lists, the context block and the Workspace show it (design 5.2): a
    lab.event/1 record as it is; an analytics.event/2 record as `schema, seq, id, ts` (its
    `recorded_at`, kept under that name too), `kind, actor`, `means`, `actor_basis`, `session` and
    `request_id` when it has them, `text` and its references in the /1 shape (_refs_view)."""
    if event.schema == relations.EVENT_V1:
        return event.value
    value = event.value
    view = {"schema": event.schema, "seq": value.get("seq"), "id": value.get("id"),
            "ts": value.get("recorded_at"), "recorded_at": value.get("recorded_at"),
            "kind": value.get("kind"), "actor": value.get("actor")}
    for name in ("means", "actor_basis", "session", "request_id"):
        if name in value:
            view[name] = value[name]
    view.update(text=value.get("text"), refs=_refs_view(value.get("refs")))
    return view


def read_events(project: Project, journal: relations.Journal | None = None) -> list[dict]:
    """The journal's records, lab.event/1 and analytics.event/2 alike, in seq order (line order
    among equal seqs, a record without a seq last), as event_view shows them (design 5.2). A line
    that does not parse is left out and the record a /1 writer appended onto it is recovered, and
    every line of an id several lines share is kept. Records of another family or version, or of a
    kind this kit does not know, are not here: status() counts them. Only journal.jsonl is read
    (this runs before every prompt, through context_block), unless `journal` is one already read."""
    journal = relations.read_journal(project.root) if journal is None else journal
    return [event_view(event) for event in journal.in_order()]


class JournalLists:
    """The journal as the lists read it, from one read (design 5.2): every record in seq order
    (`events`, event views), the name each is read by (`source`: its id, or `journal:<line>` when
    several lines have it), the open questions and todos (`open`), the key information (`key`) and
    the Workflow feedback (`feedback`). A lab.event/1 record is open, or key information, when no
    record resolves it (refs.resolves, as before #13); an analytics.event/2 one when, besides, the
    state the read model replays for it (ADR-0006 6) is `open`, or `current` (not withdrawn,
    superseded or in conflict)."""

    def __init__(self, journal: relations.Journal):
        ordered = journal.in_order()
        self.events = [event_view(e) for e in ordered]
        self.source = {id(view): journal.source_of(e) for e, view in zip(ordered, self.events, strict=True)}
        resolved = resolved_targets(self.events)
        states = (journal.replay().record_states()
                  if any(e.schema != relations.EVENT_V1 for e in ordered) else {})

        def live(view: dict, wanted: str) -> bool:
            if view.get("schema") == relations.EVENT_V1:
                return True
            return (states.get(self.source[id(view)]) or {}).get("state") == wanted

        self.open = [e for e in self.events if e.get("kind") in OPEN_KINDS
                     and e.get("id") not in resolved and live(e, "open")]
        self.key = [e for e in self.events if e.get("kind") in KEY_KINDS
                    and e.get("id") not in resolved and live(e, "current")]
        self.feedback = [e for e in self.events if e.get("kind") in FEEDBACK_KINDS]


def read_model(project: Project, cache: HashCache | None = None) -> relations.ReadModel:
    """One read of the project's records as ADR-0006 says (relations.read): this platform's process
    port says whether a running run's process is alive, the project's hash cache hashes the
    current files, and Project.legacy_identity reads the paths a kit before PATH_IDENTITY_SINCE
    recorded as today's identity paths (inferred, ADR-0006 9) — in a project a migration made,
    through the mapping it recorded first (design 5.4, migration_mapping)."""
    cache = cache or HashCache(project)
    if project.marker_state().state != writer.CURRENT:  # no migration's mapping: judged once
        return relations.read(project.root, alive=project.platform.processes.alive, sha=cache.sha,
                              legacy_path=project.legacy_identity)

    def frame():
        # Read again for each attempt of the read, and part of its source_revision: a mapping
        # removed, changed or bound anew changes what the read is.
        mapping, _info, state = _project_migration(project)
        legacy = project.legacy_identity if mapping is None else mapped_identity(project, mapping)
        return legacy, state

    return relations.read(project.root, alive=project.platform.processes.alive, sha=cache.sha,
                          frame=frame)


# --- the mapping of a migrated project (Issue #13 PR 2, design 5.4 and 6.7) ---

class MigrationMapping:
    """The places a migration moved (design 6.7) as the reader reads an older kit's paths through
    them (5.4): an absolute path under one of the source root's spellings (`root.from`) is the
    path below the root now; one under a copied link's target or a spelling of it (`links`) is that
    path below the link's path; and a relative path under `.lab/` — given so, or got so — is under
    `.analytics/` (`prefixes`). "Under" is by path components, as `_under` says, the longest
    spelling first. An absolute path and a spelling are compared as legacy_identity reads an
    absolute path — `os.path.normpath`, so on Windows `C:\\a\\proj/x.csv` (both separators) is
    under `C:\\a\\proj` — and without an extended-length prefix. Nothing is read or written here:
    `translate` is text."""

    def __init__(self, root_from, links, prefixes) -> None:
        def longest(pairs):
            return tuple(sorted(pairs, key=lambda pair: (-len(pair[0]), pair[0])))

        self.root_from = longest((_mapping_text(s), "") for s in root_from)
        self.links = longest((_mapping_text(s), link) for s, link in links)
        self.prefixes = tuple((frm.rstrip("/"), to.rstrip("/")) for frm, to in prefixes)

    def translate(self, recorded) -> str | None:
        """The project-relative path an older kit's `recorded` path is through the mapping, or None
        when no rule of it applies (the path is then read as Project.legacy_identity reads it)."""
        if not isinstance(recorded, str) or not recorded or "\x00" in recorded:
            return None
        moved = False
        if os.path.isabs(recorded):
            plain = _mapping_text(recorded)
            rel = None
            for spelling, link in (*self.root_from, *self.links):
                if spelling and _under(plain, spelling):
                    rest = _below(plain, spelling)
                    rel = _ns_join(link, rest)
                    break
            if not rel:
                return None
            moved = True
        else:
            rel = recorded
        for frm, to in self.prefixes:
            if rel == frm or rel.startswith(frm + "/"):
                rel, moved = to + rel[len(frm):], True
                break
        return rel if moved else None


def _mapping_text(text: str) -> str:
    """An absolute path or a spelling as MigrationMapping compares it (an empty one stays empty)."""
    return os.path.normpath(_platform.plain_path(text)) if text else text


def mapped_identity(project: Project, mapping: MigrationMapping):
    """Project.legacy_identity through a migration's mapping (design 5.4): the path the mapping
    gives an older kit's path, judged by today's root, links and spelling; a path no rule of the
    mapping applies to is judged as it is."""
    def legacy(recorded):
        moved = mapping.translate(recorded)
        return project.legacy_identity(recorded if moved is None else moved)

    return legacy


def _record_path(text) -> bool:
    """Whether `text` names a file below a project's root as a migration records it: relative,
    POSIX, without an empty, `.` or `..` part."""
    return (isinstance(text, str) and bool(text) and "\\" not in text and "\x00" not in text
            and not os.path.isabs(text) and not PureWindowsPath(text).drive
            and all(part not in ("", ".", "..") for part in text.split("/")))


def _bound_json(root: Path, rel, digest) -> tuple[dict | None, str | None]:
    """(the JSON object at `rel` below `root`, None) when its bytes have the sha256 `digest`, else
    (None, why): missing, changed or unreadable."""
    try:
        data = (root / rel).read_bytes()
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if hashlib.sha256(data).hexdigest() != digest:
        return None, "changed"
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        return None, "invalid"
    return (value, None) if isinstance(value, dict) else (None, "invalid")


def migration_mapping(root, marker) -> tuple[MigrationMapping | None, dict | None]:
    """(the mapping to read an older kit's paths through, or None; what the status overview says of
    it, or None) for a project whose marker `marker` (its JSON object) holds a `migration` field
    (design 2.4 and 5.4): the report the marker binds by its sha256, and the mapping the report
    binds by its sha256 — the three of one migration (its id in each, the report and the mapping in
    that migration's directory). A report or a mapping that is missing or whose bytes changed is not
    read, and no path is mapped through it: the overview says why (`applied: false`, `reason`)."""
    mapping, info, _ = _migration_mapping(root, marker)
    return mapping, info


def _migration_mapping(root, marker) -> tuple[MigrationMapping | None, dict | None, list | None]:
    """migration_mapping, and what a read made through it is made of (part of its source_revision):
    the marker's `migration` field and the mapping's sha256 the report binds, with the overview."""
    field = marker.get("migration") if isinstance(marker, dict) else None
    if field is None:
        return None, None, None
    mapping, info, bound = _migration_mapping_of(Path(root), field)
    return mapping, info, [field, bound, info]


def _migration_mapping_of(root: Path, field) -> tuple[MigrationMapping | None, dict, str | None]:
    info: dict = {"applied": False}
    if not isinstance(field, dict) or not all(isinstance(field.get(k), str)
                                              for k in ("id", "report", "report_sha256")):
        return None, {**info, "reason": "invalid"}, None
    info = {"id": field["id"], "report": field["report"], **info}
    own = f"{writer.MIGRATION_DIR}/{field['id']}"
    if not _record_path(field["id"]) or "/" in field["id"] or field["report"] != f"{own}/report.json":
        return None, {**info, "reason": "invalid"}, None
    report, problem = _bound_json(root, field["report"], field["report_sha256"])
    if problem:
        return None, {**info, "reason": "report_" + problem if problem in ("missing", "changed")
                      else problem}, None
    bound = report.get("mapping")
    if report.get("schema") != "analytics.migration/1" or report.get("record") != "report" \
            or report.get("migration_id") != field["id"] \
            or not isinstance(bound, dict) or bound.get("path") != f"{own}/mapping.json" \
            or not isinstance(bound.get("sha256"), str):
        return None, {**info, "reason": "invalid"}, None
    info["mapping"] = bound["path"]
    value, problem = _bound_json(root, bound["path"], bound["sha256"])
    if problem:
        return None, {**info, "reason": "mapping_" + problem if problem in ("missing", "changed")
                      else problem}, bound["sha256"]
    try:
        source_root = value["root"]["from"]
        links = [(spelling, link["path"]) for link in value.get("links") or []
                 for spelling in (link["target_real"], *(link.get("target_spellings") or []))]
        prefixes = [(p["from"], p["to"]) for p in value.get("prefixes") or []]
        if value.get("schema") != "analytics.migration/1" or value.get("record") != "mapping" \
                or value.get("migration_id") != field["id"] \
                or not isinstance(source_root, list) \
                or not all(isinstance(t, str) and t for pair in links for t in pair) \
                or not all(isinstance(t, str) and t for t in source_root) \
                or not all(isinstance(t, str) and _record_path(t.rstrip("/"))
                           for pair in prefixes for t in pair):
            raise TypeError("not a mapping")
    except (KeyError, TypeError, AttributeError):
        return None, {**info, "reason": "invalid"}, bound["sha256"]
    return MigrationMapping(source_root, links, prefixes), {**info, "applied": True}, bound["sha256"]


def project_migration(project: Project) -> tuple[MigrationMapping | None, dict | None]:
    """migration_mapping for the project's marker as it is now: only an analytics.project/2 marker
    (writer.CURRENT) holds a migration this reader knows."""
    mapping, info, _ = _project_migration(project)
    return mapping, info


def _project_migration(project: Project) -> tuple[MigrationMapping | None, dict | None, list | None]:
    state = project.marker_state()
    if state.state != writer.CURRENT:
        return None, None, None
    return _migration_mapping(project.root, state.value)


def resolved_targets(events) -> dict[str, dict]:
    """Event id -> the later event that resolved it (refs.resolves)."""
    out = {}
    for ev in events:
        target = (ev.get("refs") or {}).get("resolves")
        if target:
            out[str(target)] = ev
    return out


# --- the new format's records (Issue #13 PR 3, design 4.5 and 9) ---

def writer_for(project: Project, origin: Origin) -> writer.Writer:
    """The common writer of a project for one request by `origin`: this kit's version, the
    project's platform, and how a binding reads the paths an older kit recorded — the project's own
    links and spellings, or a migrated project's mapping (design 5.4) — looked up only when a binding
    reads them (design 3.2: the writer has no Project of its own)."""
    reading: list = []

    def legacy(recorded):
        if not reading:
            mapping, _info, _state = _project_migration(project)
            reading.append(project.legacy_identity if mapping is None
                           else mapped_identity(project, mapping))
        return reading[0](recorded)

    # The kit's clock (now(), looked up when a record is written: a caller that sets core.now — a
    # fixture generator's planned clock — dates the records by it).
    return writer.Writer(project.root, platform=project.platform, origin=origin,
                         writer_version=KIT_VERSION, legacy_path=legacy, clock=lambda: now())


def asset_application(project: Project, origin: Origin, *, policy=None, skill_discovery=None):
    """Construct the stdlib asset application for this project and explicit writing origin."""
    return asset_app.Assets(writer_for(project, origin), policy=policy, skill_discovery=skill_discovery)


def assets(project: Project, asset_id=None) -> dict:
    readable(project, "read analysis assets")
    return asset_application(project, Origin("system", "sdk")).list(asset_id)


def review_assets(project: Project, asset_id=None, *, origin: Origin, surface="sdk") -> dict:
    return asset_application(project, origin).review(asset_id, surface=surface)


def update_asset(project: Project, event_id: str, *, expected: str, new: str, text: str,
                 details=None, origin: Origin) -> dict:
    return asset_application(project, origin).update(event_id, expected=expected, new=new,
                                                   text=text, details=details)


def register_asset(project: Project, event_id: str, *, name: str, origin: Origin,
                   skill_discovery=None) -> dict:
    return asset_application(project, origin, skill_discovery=skill_discovery).register(event_id, name=name)


def set_asset_mode(project: Project, mode: str, *, origin: Origin, scope="project") -> dict:
    return asset_application(project, origin).set_mode(mode, scope=scope)


def asset_suggestion_status(project: Project, *, journal=None, marker=None, session=None) -> dict:
    return asset_application(project, Origin("system", "sdk")).suggestion_status(journal=journal, marker=marker, session=session)


def publish_asset_suggestion(project: Project, *, origin: Origin, surface="cli_text_status") -> dict:
    # A read-only/older project keeps its status readable and skips this side write.
    if admit(project, "publish an asset suggestion", side=True) is None:
        return {"published": None, "reason": "read_only_project", "model_calls": 0}
    return asset_application(project, origin).publish_suggestion(surface=surface)


def preassess_asset_suggestions(project: Project, *, origin: Origin) -> dict:
    return asset_suggestions.preassess(asset_application(project, origin))


def asset_context_notice(publication) -> str:
    """The recorded notice is relayed without requesting a model review or claiming human visibility."""
    if not publication:
        return ""
    notice = publication["notification"]
    title = _one_line(publication["candidate"]["details"]["title"], 120)
    refs = list(dict.fromkeys(ref.get("event_id") or ref.get("run_id") for ref in notice["details"]["evidence"]
                             if ref.get("event_id") or ref.get("run_id")))
    ids = ", ".join(refs[:4]) or "recorded source references"
    more = f" (+{len(refs) - 4} more in {notice['id']})" if len(refs) > 4 else ""
    return (f"\n\n## Asset suggestion ({notice['id']})\n{title}\n"
            f"Repeated need, recorded checks, documented scope and benefit. Evidence: {ids}{more}.\n"
            "Relay this suggestion; review only when the user requests it.")


def _record_items(refs, resolves) -> list:
    items = [refs] if isinstance(refs, (str, dict)) else list(refs or [])
    if resolves:
        items.append({"type": "event", "event_id": str(resolves).strip(), "relation": "resolves"})
    return items


def check_record(project: Project, kind: str, text: str, *, refs=None, resolves=None,
                 details=None, origin: Origin) -> None:
    """What `record` refuses before its lock, judged now and nothing written (writer.Writer.check):
    an entry point that syncs the context before a record calls this first, so a record refused —
    a kind it does not take, a text or a line over the limits (OQ2), a closed record that is not
    there, a `resolve` without one — changes nothing, not even the context. The line is judged with
    the seq it gets after the context event the sync is about to write."""
    admit(project, "record")
    writer.check_kind(kind)
    if kind == "checkpoint":
        if resolves:
            raise AnalyticsError("bad_checkpoint_record", "a checkpoint cannot resolve another record")
        prepare_checkpoint(project, text, refs=refs, details=details, origin=origin, sync=False)
        return
    try:
        writer_for(project, origin).check(kind, text, refs=_record_items(refs, resolves), details=details,
                                          ahead=1 if context_pending(project) else 0)
    except ValueError as exc:
        raise AnalyticsError("bad_record_input", str(exc)) from exc


def record(project: Project, kind: str, text: str, *, refs=None, resolves=None,
           details=None, origin: Origin) -> writer.Written:
    """Record a journal entry by `origin` through the common writer (writer.Writer.record): judged
    before anything else (the writer's gate), a kind a person or an agent records (RECORD_KINDS;
    `gap` is refused with its new name), the names in `refs` bound as they are written, and
    `resolves` the question or to-do it closes (an event that must be there). The answer holds the
    record, the request_id, the bindings and the closed record's state before and after."""
    w = writer_for(project, origin)
    w.admit("record")
    writer.check_kind(kind)
    if kind == "checkpoint":
        if resolves:
            raise AnalyticsError("bad_checkpoint_record", "a checkpoint cannot resolve another record")
        prepared = prepare_checkpoint(project, text, refs=refs, details=details, origin=origin)
        try:
            written = w.record(kind, prepared["text"], refs=prepared["refs"], details=prepared["details"])
            return writer.Written(written.event, written.request_id, prepared.get("bindings", written.bindings), written.targets)
        except AnalyticsError as exc:
            if prepared.get("context_synced") is not None:
                exc.details = {**(exc.details or {}), "context_synced": prepared["context_synced"]}
            raise
    try:
        return w.record(kind, text, refs=_record_items(refs, resolves), details=details)
    except ValueError as exc:
        raise AnalyticsError("bad_record_input", str(exc)) from exc


def prepare_checkpoint(project: Project, text=None, *, refs=(), details=None, as_of_seq=None,
                       context_version=checkpoints.UNSET, from_seq=0, limit=8, origin: Origin,
                       sync=True, pending_run=None) -> dict:
    """Capture a complete call-time payload for all entries, including SDK pending writes.

    Current requests alone sync CONTEXT. Fully tagged and explicit historical requests keep their
    basis. Known refusals are preflighted before sync; its separate partial effect is reported.
    """
    admit(project, "checkpoint")
    w = writer_for(project, origin)
    refs = _record_items(refs, None)
    if text is not None:
        writer.check_text(text)
    try:
        checkpoints.integer(limit, "limit", 1, 100)
        if details is not None and not isinstance(details, dict):
            raise checkpoints.CheckpointError("bad_checkpoint_record", "details is an object")
        supplied = dict(details or {})
        if "contract" in supplied:
            normalized = checkpoints.validate(supplied)
            if text is None:
                raise checkpoints.CheckpointError("bad_checkpoint_record", "tagged records include their complete text")
            w.check("checkpoint", text, refs=refs, details=normalized, pending_run=pending_run)
            model = read_model(project)
            normalized, generated, historical = w._checkpoint(normalized, refs, model.journal, pending_run=pending_run)
            items = writer._items(refs)
            bound = w._bind(items, model=historical)
            return {"text": text, "refs": [*writer._refs(items, bound), *generated], "details": normalized,
                    "bindings": [entry for _, entry in bound.values()], "context_synced": None}
        if "as_of_seq" in supplied:
            as_of_seq = supplied["as_of_seq"]
            checkpoints.integer(as_of_seq, "as_of_seq")
        if "from_seq" in supplied:
            from_seq = supplied["from_seq"]
        if "context_version" in supplied:
            context_version = supplied["context_version"]
        if as_of_seq is not None:
            checkpoints.integer(as_of_seq, "as_of_seq")
            if context_version is checkpoints.UNSET:
                raise checkpoints.CheckpointError("bad_checkpoint_record", "historical as_of_seq requires exact context_version or explicit null")
        elif context_version is not checkpoints.UNSET:
            raise checkpoints.CheckpointError("bad_checkpoint_record", "explicit context_version requires as_of_seq")
        current = as_of_seq is None

        def payload(model, version, *, preview=False):
            top = model.as_of_seq if current else as_of_seq
            if top > model.as_of_seq:
                raise checkpoints.CheckpointError("checkpoint_reference", "as_of_seq is beyond the observed journal")
            historical = model.through(top)
            bound = w._bind(writer._items(refs), model=historical)
            typed = writer._refs(writer._items(refs), bound)
            goal = None
            if preview and current:
                goal = checkpoint_goal(context_file_text(project))
            if version is not None:
                path = relations.context_file(project.root, version)
                if path is None:
                    raise checkpoints.CheckpointError("checkpoint_reference", "no exact preserved CONTEXT version")
                goal = checkpoint_goal(path.read_text(encoding="utf-8"))
            summary, omitted = checkpoints.select(historical, reader=relations, goal=goal, from_seq=from_seq, limit=limit,
                                                  explicit=[r.get("event_id") for r in typed if r.get("event_id")],
                                                  authored=text is not None)
            if "summary" in supplied:
                summary = supplied["summary"]
            normalized = {**supplied, "contract": checkpoints.CONTRACT, "as_of_seq": top,
                          "context_version": version, "from_seq": from_seq,
                          "summary_source": "authored" if text is not None else "deterministic",
                          "summary": summary, "omitted": omitted,
                          "journal_basis": {"reader_format": relations.READER_FORMAT,
                                            "prefix_revision": model.journal.prefix_revision(top)}}
            body = text if text is not None else checkpoints.text_of(normalized, historical.journal)
            w.check("checkpoint", body, refs=refs, details=normalized,
                    ahead=1 if current and context_pending(project) else 0, pending_run=pending_run)
            # Typed refs retain their call-time binding/digest for pending finalization.
            normalized, generated, _ = w._checkpoint(normalized, typed, model.journal, pending_run=pending_run)
            return {"text": body, "details": normalized, "refs": [*typed, *generated],
                    "bindings": [entry for _, entry in bound.values()]}

        model = read_model(project)
        # Known input, line and evidence errors are rejected before a current CONTEXT sync.
        prepared = payload(model, None if current else context_version, preview=True)
        context_synced = None
        if current and sync:
            context_synced = sync_context(project, origin=system_origin(origin))
            model = read_model(project)
            versions = context_versions(project)
            version = versions[-1].name if versions and not context_pending(project) else None
            try:
                prepared = payload(model, version)
            except (AnalyticsError, checkpoints.CheckpointError) as exc:
                if isinstance(exc, checkpoints.CheckpointError):
                    exc = AnalyticsError(exc.code, str(exc))
                exc.details = {**(exc.details or {}), "context_synced": context_synced}
                raise exc
        prepared["context_synced"] = context_synced
        return prepared
    except checkpoints.CheckpointError as exc:
        raise AnalyticsError(exc.code, str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise AnalyticsError("bad_checkpoint_record", str(exc)) from exc


def checkpoint_goal(text):
    """The exact purpose line from a preserved CONTEXT version, without display clipping."""
    return context_goal(text, max_chars=None) or None


def checkpoint(project: Project, text=None, *, refs=(), as_of_seq=None,
               context_version=checkpoints.UNSET, from_seq=0, limit=8, origin: Origin) -> dict:
    prepared = prepare_checkpoint(project, text, refs=refs, as_of_seq=as_of_seq,
                                  context_version=context_version, from_seq=from_seq, limit=limit, origin=origin)
    try:
        written = writer_for(project, origin).record("checkpoint", prepared["text"], refs=prepared["refs"],
                                                     details=prepared["details"])
    except AnalyticsError as exc:
        exc.details = {**(exc.details or {}), "context_synced": prepared["context_synced"]}
        raise
    return {**written.to_json(), "bindings": prepared.get("bindings", written.bindings),
            "basis": {key: prepared["details"][key] for key in
            ("as_of_seq", "context_version", "journal_basis")}, "context_synced": prepared["context_synced"]}


def checkpoint_view(model, event=None, *, limit=8, cursor=None):
    events = [e for e in model.journal.in_order() if e.kind == "checkpoint"]
    event = event or (events[-1] if events else None)
    if event is None:
        return None
    evaluated = checkpoints.evaluate(event, model, reader=relations)
    updates = evaluated.pop("updates", [])
    evaluated.pop("text", None)
    details = evaluated.pop("details", None)
    reasons = evaluated["review_reasons"]
    evaluated["review_reasons"] = reasons[:limit]
    evaluated["review_reasons_omitted"] = {"count": max(0, len(reasons)-limit),
                                         "reason": "item_limit" if len(reasons)>limit else None,
                                         "read_more": {"part": "context", "id": model.journal.source_of(event), "limit": 100}}
    evaluated["basis"] = {key: details.get(key) for key in ("as_of_seq", "context_version", "journal_basis")} if isinstance(details, dict) else None
    evaluated["summary_read_more"] = {"part": "records", "id": model.journal.source_of(event)}
    evaluated["updates"] = relations.QueryPolicy().page(
        updates, revision=model.source_revision or model.journal.prefix_revision(model.as_of_seq),
        request={"part": "context", "id": model.journal.source_of(event), "as_of_seq": model.as_of_seq},
        cursor=cursor, limit=limit, journal_order=True, as_of_seq=model.as_of_seq,
        boundary_revision=model.journal.prefix_revision(model.as_of_seq))
    return evaluated


def metric_records(project: Project, *, model=None) -> dict:
    """ADR-0011 structured versions, distinct from historical unstructured metric events."""
    model = model or read_model(project)
    journal = model.journal
    view = relations.metric_projection([e.value for e in journal.events], journal.replay().record_states())
    shared = set(i for i, lines in journal.lines_of.items() if len(lines) != 1)
    for name in ("definitions", "focus_versions", "observations"):
        for item in view[name]:
            if item["event_id"] in shared:
                item["state"] = "ambiguous"
            item["references"] = model.references(item["event_id"])
    focuses = [f for f in view["focus_versions"] if f["state"] == "current"]
    view["current_focus"] = focuses[0] if len(focuses) == 1 else None
    view["focus_state"] = "current" if len(focuses) == 1 else "conflict" if focuses else "absent"
    run_states = {r["run_id"]: r for r in model.runs()}
    for item in view["observations"]:
        run = run_states.get(item["details"].get("run_id")) or {}
        item.update(execution_status=run.get("execution_status", "missing"),
                    recording_status=run.get("recording_status", "incomplete"),
                    recording_reasons=run.get("reasons", ["run_absent"]) if not run else run["reasons"],
                    calculation_check="unverified")
    return {"as_of_seq": model.as_of_seq, **view}


def request_id_of(origin: Origin) -> str:
    """The request_id of a write's answer whose records the writer answers one by one (init, the
    demo, render, migrate; design 4.9): the caller's, else one the kit makes for this answer only."""
    return origin.request_id or writer.new_request_id()


# --- CONTEXT.md history ---

def context_versions(project: Project) -> list[Path]:
    """The CONTEXT.md versions a reader shows (status, the Workspace), in name (= time) order: those
    of .analytics/context/ (where this kit writes them, design 5.3) and of .lab/context/ (an older
    kit's, or a migrated project's copy of them), read in the order relations.CONTEXT_DIRS gives, so
    a name in both is the one a context reference resolves to (relations.ReadModel._context_file).
    Nothing is written or made."""
    found: dict[str, Path] = {}
    for directory in relations.CONTEXT_DIRS:
        place = project.root / directory
        if place.is_dir():
            for version in place.glob("*.md"):
                if version.is_file():
                    found.setdefault(version.name, version)
    return [found[name] for name in sorted(found)]


def context_history(model: relations.ReadModel) -> dict:
    """The CONTEXT.md versions as ReadModel.context_history counts them, with the pinned sections
    as the prompt selects them (context_sections: `<!-- pin -->` headings and their text) and the
    key-information kinds (KEY_KINDS) as the insights written while each version was the latest
    (design WORKSPACE-READABILITY 5.2)."""
    return model.context_history(pinned=pinned_sections, kinds=KEY_KINDS)


def pinned_sections(text: str) -> list[tuple[str, str]]:
    """(name, text) of the pinned sections of a CONTEXT.md text, in file order."""
    return [(s["name"], s["text"]) for s in context_sections(text)[1] if s["pinned"]]


def kit_reference(kind: str, **target) -> dict:
    """A reference the kit's own record gives to what it records (a run's, an import's, a CONTEXT
    version's completion event, design 4.8): `about`, `observed`, bound by its author."""
    return writer.typed_reference({"type": kind, **target, "relation": "about", "basis": "observed"})


def sync_context(project: Project, *, origin: Origin) -> dict | None:
    """Snapshot CONTEXT.md into .analytics/context/ and record a `context` event when it changed
    since the last version of either state directory (design 4.8: a migrated project's copy of the
    older versions counts, so the same CONTEXT.md is not versioned again). The version is
    CONTEXT.md's bytes as they are; when only their line ends differ from the last version's (the
    last version's whole sha256 is one of the new bytes' line_ending_digests_of), the event says so
    instead of counting lines (ADR-0007 decision 4). Both are written under the journal lock of the
    common writer (Writer.locked), the event citing the version by its sha256. `origin` is the
    caller's: init's and the demo's own, and `system` with the entry point's means where the kit
    syncs on its own before a write or in a read (system_origin, design 4.1). A side write: skipped
    where this kit does not write (admit, design 4.10), and then None as when nothing changed;
    otherwise the event written."""
    try:
        data = project.context.read_bytes()
    except OSError:
        return None
    sha = sha256_bytes(data)
    digest = sha[:12]

    def recorded() -> bool:
        return _context_recorded(project, digest)

    # Unchanged — the common case, before every prompt in aelix: nothing is locked or read.
    if recorded():
        return None
    op = "sync the context"
    if admit(project, op, side=True) is None:
        return None
    try:
        with writer_for(project, origin).locked(op) as held:
            versions = context_versions(project)
            if recorded():  # another process versioned it meanwhile
                return None
            project.context_history.mkdir(parents=True, exist_ok=True)
            # Microseconds keep same-second versions in chronological (= lexical) order.
            dest = project.context_history / f"{now().strftime('%Y%m%d-%H%M%S-%f')}_{digest}.md"
            dest.write_bytes(data)
            if versions:
                previous = versions[-1].read_bytes()
                if sha256_bytes(previous) in (line_ending_digests_of(data) or ()):
                    text = "CONTEXT.md line endings changed"
                else:
                    old = previous.decode("utf-8", errors="replace").splitlines()
                    new = data.decode("utf-8", errors="replace").splitlines()
                    added = removed = 0
                    for line in difflib.unified_diff(old, new, lineterm="", n=0):
                        if line.startswith("+") and not line.startswith("+++"):
                            added += 1
                        elif line.startswith("-") and not line.startswith("---"):
                            removed += 1
                    text = f"CONTEXT.md updated (+{added} -{removed} lines)"
            else:
                text = "CONTEXT.md recorded"
            return held.append("context", text, refs=[kit_reference(
                "context_version", version=dest.name, digest={"sha256": sha})])
    except AnalyticsError:
        # The directory changed between the judgment and the lock (judged again under it): a side
        # write, skipped.
        return None


def _context_recorded(project: Project, digest: str) -> bool:
    versions = context_versions(project)
    return bool(versions) and versions[-1].stem.rsplit("_", 1)[-1] == digest


def context_pending(project: Project) -> bool:
    """Whether sync_context would version CONTEXT.md now (it changed since the last version), as
    far as can be told without the lock; nothing is written."""
    try:
        data = project.context.read_bytes()
    except OSError:
        return False
    return not _context_recorded(project, sha256_bytes(data)[:12])


# A heading's words that name the goal (the Workspace's Overview finds the goal's section by them).
GOAL_RE = re.compile(r"^(목표|goal|objective)", re.IGNORECASE)


def context_goal(text: str, *, max_chars: int | None = 200) -> str:
    """First content line under a Goal heading (or 목표, the heading of earlier templates, which
    projects keep), else the first paragraph line."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        heading = re.match(r"^#{1,3}\s*(.*)", line.strip())
        if heading and GOAL_RE.match(heading.group(1)):
            for follow in lines[i + 1:]:
                s = follow.strip()
                if s.startswith("#"):
                    break
                s = s.lstrip("-*> ").strip()
                if s and not s.startswith("("):
                    return s if max_chars is None else s[:max_chars]
    for line in lines:
        s = line.strip().lstrip("-* ").strip()
        if s and not s.startswith(("#", ">", "|", "<!--", "(")):
            return s if max_chars is None else s[:max_chars]
    return ""


# --- runs ---

def list_runs(project: Project) -> list[dict]:
    runs = []
    if not project.runs.is_dir():
        return runs
    for d in sorted(project.runs.iterdir()):
        if not d.is_dir() or not RUN_ID_RE.match(d.name):
            continue
        rec = read_json(d / "run.json")
        if isinstance(rec, dict) and (newer_schema(rec.get("schema"), relations.RUN_V1)
                                      or newer_schema(rec.get("schema"), relations.RUN_V2)):
            continue  # written by a newer kit; status() counts it as unsupported
        if not isinstance(rec, dict):
            rec = {"id": d.name, "status": "incomplete", "title": "(run.json missing)"}
        rec["id"] = d.name
        if rec.get("status") == "running" \
                and not project.platform.processes.alive(rec.get("pid")):
            rec["status"] = "incomplete"
        runs.append(rec)
    return runs


def model_runs(project: Project, model: relations.ReadModel) -> list[dict]:
    """The rows list_runs gives, from the run records one read of the project found: a running run
    whose process is gone is `incomplete`, and a run directory whose run.json is missing or does not
    parse is the row list_runs makes for it. A run.json of a family or version this kit does not
    know is not here; the read counts it as unsupported. Nothing is read again."""
    runs: list[dict] = []
    if not project.runs.is_dir():
        return runs
    damaged = {item["location"] for item in model.damaged_files}
    execution = {r["run_id"]: r["execution_status"] for r in model.runs()}
    for d in sorted(project.runs.iterdir()):
        if not d.is_dir() or not RUN_ID_RE.match(d.name):
            continue
        record = model.run(d.name)
        if record is not None:
            rec = {**record, "id": d.name}
            if rec.get("status") == "running" and execution.get(d.name) == "incomplete":
                rec["status"] = "incomplete"
        elif f"runs/{d.name}/run.json" in damaged:
            rec = {"id": d.name, "status": "incomplete", "title": "(run.json missing)"}
        else:
            continue  # unsupported, or made after the read: the next read has it
        runs.append(rec)
    return runs


def new_run_dir(project: Project, label: str) -> str:
    """Create runs/<YYYYMMDD-HHMMSS-slug>/ atomically and return its id."""
    project.runs.mkdir(parents=True, exist_ok=True)
    base = f"{now().strftime('%Y%m%d-%H%M%S')}-{slugify(label, 40) or 'run'}"
    candidate, n = base, 2
    while True:
        try:
            (project.runs / candidate).mkdir()
            return candidate
        except FileExistsError:
            candidate = f"{base}-{n}"
            n += 1


def chart_key(run: dict, chart: dict) -> str:
    """`<script path>::<id>` of a Visualization this kit is recording (capture's chart records)."""
    return f"{(run.get('script') or {}).get('path') or '?'}::{chart.get('id')}"


class Freshness:
    """Whether a run's recorded inputs and code still match the files on disk, and whether a run
    that may have made one of its inputs is stale (ADR-0006 4).

    The runs that may have made an input are its producer candidates (ReadModel.producers): every
    successful run, other than the consumer, that output the input's path with its recorded bytes
    and did not start after the consumer. None of them is chosen as the producer: each is followed.
    A run is stale when one of its inputs changed or is missing, or when a producer candidate of
    one of its inputs is stale, which is to say when a run reached through producer candidates has
    a changed or missing input. A cycle among the candidates (only runs that started at the same
    instant, or whose start time cannot be read, can make one) ends that walk and is said
    (`cycle`); a run reached twice is no cycle."""

    def __init__(self, project: Project, runs: list[dict], cache: HashCache | None = None,
                 model: relations.ReadModel | None = None):
        self.project = project
        self.cache = cache or HashCache(project)
        self.model = model or read_model(project, self.cache)
        self.runs = {r["id"]: r for r in runs}
        self._memo: dict[str, dict] = {}
        self._changed: dict[str, bool] = {}

    def input_state(self, entry: dict, own_outputs: dict | None = None) -> str:
        recorded = entry.get("sha256")
        if not recorded:
            return "unknown"
        path = self.project.path(entry.get("path", ""))
        if path.is_dir():
            return "unknown"
        current = self.cache.sha(path)
        if current is None:
            return "missing"
        if current == recorded:
            return "same"
        # Read, then rewritten by the same run (incremental update): current content is its own.
        if own_outputs and own_outputs.get(entry.get("path")) == current:
            return "same"
        return "changed"

    def code_state(self, run: dict) -> str:
        script = run.get("script") or {}
        if not script.get("path") or run.get("rerun_of"):
            return "n/a"
        current = self.cache.sha(self.project.path(script["path"]))
        if current is None:
            return "missing"
        return "same" if current == script.get("sha256") else "changed"

    @staticmethod
    def _own(run: dict) -> dict:
        return {o.get("path"): o.get("sha256") for o in run.get("outputs") or []
                if o.get("change") != "deleted"}

    def _input_changed(self, run_id: str) -> bool:
        """Whether one of a run's own inputs changed or is missing (once per run)."""
        if run_id not in self._changed:
            run = self.runs.get(run_id) or self.model.run(run_id) or {}
            own = self._own(run)
            self._changed[run_id] = any(self.input_state(entry, own) in ("changed", "missing")
                                        for entry in run.get("inputs") or [])
        return self._changed[run_id]

    def stale_run(self, run_id: str) -> bool:
        """Whether a run is stale: its own input changed, or that of a run its producer candidates
        reach. The walk is the read model's, which visits each run once."""
        return self._input_changed(run_id) or any(
            self._input_changed(u) for u in self.model.upstream(run_id)["upstream"])

    def change_reason(self, path: str, recorded) -> str | None:
        """Why a `changed` file differs from its record, when that is known: LINE_ENDINGS_ONLY when
        only its line ends differ (ADR-0007 decision 4). The state stays `changed`."""
        if self.cache.line_endings_only(self.project.path(path), recorded):
            return LINE_ENDINGS_ONLY
        return None

    def of(self, run_id: str) -> dict:
        """The freshness of a run: each input's state, the changed or missing ones, the stale runs
        upstream, and the code's state. `change_reasons` maps a changed input to why it differs when
        that is known, and `code_reason` says the same of the code (else None); neither changes a
        state or `stale`. `inferred_inputs` are the inputs a producer candidate of which was found
        only through a path a kit before PATH_IDENTITY_SINCE recorded (ADR-0006 9), and
        `inferred_upstream` the stale runs upstream that are such a candidate of an input."""
        if run_id in self._memo:
            return self._memo[run_id]
        run = self.runs.get(run_id) or {}
        inputs, upstream, ambiguous, reasons = [], [], [], {}
        inferred_inputs: list[str] = []
        inferred_runs: set[str] = set()
        observed_runs: set[str] = set()
        own = self._own(run)
        for entry in run.get("inputs") or []:
            state = self.input_state(entry, own)
            item = {"path": entry.get("path"), "state": state, "sha256": entry.get("sha256")}
            reason = (self.change_reason(entry.get("path", ""), entry.get("sha256"))
                      if state == "changed" else None)
            if reason:
                item["reason"] = reasons[entry.get("path")] = reason
            inputs.append(item)
            candidates, inferred = self.model.producer_candidates(entry.get("path"),
                                                                  entry.get("sha256"),
                                                                  consumer=run_id)
            if len(candidates) > 1 and entry.get("path") not in ambiguous:
                ambiguous.append(entry.get("path"))
            if inferred and entry.get("path") not in inferred_inputs:
                inferred_inputs.append(entry.get("path"))
            inferred_runs.update(inferred)
            observed_runs.update(c for c in candidates if c not in inferred)
            upstream += [c for c in candidates if c not in upstream and self.stale_run(c)]
        direct = [i["path"] for i in inputs if i["state"] in ("changed", "missing")]
        code = self.code_state(run)
        script = run.get("script") or {}
        code_reason = (self.change_reason(script["path"], script.get("sha256"))
                       if code == "changed" else None)
        result = {"inputs": inputs, "changed_inputs": direct, "stale_upstream": upstream,
                  "ambiguous_inputs": ambiguous, "cycle": self.model.upstream(run_id)["cycle"],
                  "inferred_inputs": inferred_inputs,
                  "inferred_upstream": [c for c in upstream
                                        if c in inferred_runs and c not in observed_runs],
                  "code": code, "stale": bool(direct or upstream), "change_reasons": reasons,
                  "code_reason": code_reason}
        self._memo[run_id] = result
        return result


def chart_index(runs: list[dict], read_as=None) -> list[dict]:
    """Latest version of every script::chart across successful runs, newest first.

    A run's Visualizations are read through relations.run_visualizations, so a lab.run/1 run's
    charts and an analytics.run/2 run's visualizations are versions of the same keys.

    `read_as(run_id, path)` (the read model's) gives the identity path a kit before
    PATH_IDENTITY_SINCE recorded a run's script by, read in today's spelling (ADR-0006 9): that
    run's charts are versions of today's key, each marked `inferred` and counted in
    `inferred_versions`. Without it a script is its path as recorded."""
    by_key: dict[str, list[dict]] = {}
    last_ok_by_script: dict[str, dict] = {}
    for r in runs:
        if r.get("status") != "ok" or r.get("rerun_of"):
            continue
        recorded = (r.get("script") or {}).get("path") or "?"
        read = read_as(r["id"], recorded) if read_as is not None and recorded != "?" else None
        script = read or recorded
        last_ok_by_script[script] = r
        for c in relations.run_visualizations(r):
            key = f"{script}::{c['id']}"
            entry = {**c, "key": key, "run": r["id"], "script": script,
                     "run_title": r.get("title"), "started_at": r.get("started_at")}
            if read:
                entry["inferred"] = True
            by_key.setdefault(key, []).append(entry)
    out = []
    for versions in by_key.values():
        latest = dict(versions[-1])
        last_run = last_ok_by_script.get(latest["script"]) or {}
        latest["retired"] = last_run.get("id") != latest["run"]
        latest["versions"] = [{"run": v["run"], "started_at": v.get("started_at"),
                               **({"inferred": True} if v.get("inferred") else {})}
                              for v in versions]
        latest["inferred_versions"] = sum(1 for v in versions if v.get("inferred"))
        out.append(latest)
    out.sort(key=lambda c: c.get("started_at") or "", reverse=True)
    return out


# --- datasets ---

def list_datasets(project: Project, runs: list[dict] | None = None, *,
                  model: relations.ReadModel | None = None, sha=None) -> list[dict]:
    """Every dataset under the project's dataset directories (dataset_dirs: data/raw and data/derived,
    with data/imported first in an analytics.json project): each `.parquet` file (the suffix in any
    case) the walk from those directories finds (Project.walk), named by the stem of the name the
    namespace lists it under and with the path a record holds for it (ADR-0006 9), its import record
    the one beside that entry (an analytics.dataset/1 record in data/imported, a lab.dataset/1 one in
    data/raw), in the order of the directories, and each kind in the code point order of its path's
    components. `producers` are the successful runs that output its path, in run order, whatever
    bytes they wrote; `used_by` the successful runs that read it.

    With a read model and `sha` (the hash of a file's current bytes), each dataset also says which
    runs may have made its current bytes (ADR-0006 4): `producer_state` is `resolved` (one successful
    run output this path with these bytes), `ambiguous` (several, all in `producer_candidates`),
    `none` or `unknown` (the file could not be hashed), `producer` is the one candidate when there is
    one, and `written_by` holds every run that output the path, whatever its status. A candidate is
    a run whose output had the same path and bytes (basis observed), never a confirmed link. Without
    them nothing is hashed: the query views, imports and the SDK list datasets at no more cost.

    An import record of a family or version this kit cannot read (relations.unsupported_reason) is
    not used: the file is listed by its path and bytes, and `unsupported_record` says where that
    record is and why, as the read model counts it.

    With a read model, a path a kit before PATH_IDENTITY_SINCE recorded is read as the identity
    path the model reads it as (ReadModel.read_as, ADR-0006 9): the runs that are a dataset's only
    through such a reading are in `inferred` (`producer_candidates`, `producers`, `used_by`,
    `written_by`, each as listed beside it), an inference and never an observation."""
    runs = list_runs(project) if runs is None else runs
    produced: dict[str, list[dict]] = {}
    used: dict[str, list[str]] = {}
    written: dict[str, list[str]] = {}
    # (path as read, list) -> the runs in it through a recorded path and through an inferred one.
    exact: dict[tuple[str, str], set[str]] = {}
    through: dict[tuple[str, str], set[str]] = {}

    def as_read(run_id: str, path):
        return (model.read_as(run_id, path) if model is not None else None) or path

    def note(name: str, run_id: str, recorded, path) -> None:
        (exact if path == recorded else through).setdefault((path, name), set()).add(run_id)

    for r in runs:
        for out in r.get("outputs") or []:
            if out.get("change") != "deleted":
                path = as_read(r["id"], out.get("path"))
                written.setdefault(path, []).append(r["id"])
                note("written_by", r["id"], out.get("path"), path)
        if r.get("status") != "ok":
            continue
        for out in r.get("outputs") or []:
            path = as_read(r["id"], out.get("path"))
            produced.setdefault(path, []).append({**out, "run": r["id"]})
            note("producers", r["id"], out.get("path"), path)
        for entry in r.get("inputs") or []:
            path = as_read(r["id"], entry.get("path"))
            used.setdefault(path, []).append(r["id"])
            note("used_by", r["id"], entry.get("path"), path)

    def inferred_in(path: str, name: str) -> set[str]:
        return through.get((path, name), set()) - exact.get((path, name), set())

    items = []
    for base, kind in dataset_dirs(project):
        # Once per (name, path) within a kind: a raw file a derived link leads to is in both, as
        # on main.
        found: dict[tuple[str, str], tuple] = {}
        for rel, real, name, _entry, place in project.walk(start=base):
            if not name.lower().endswith(".parquet") or (name, rel) in found:
                continue
            try:
                size = os.stat(real).st_size
            except OSError:
                continue
            found[(name, rel)] = (rel.split("/"), rel, name, size, place)
        for _, rel, name, size, place in sorted(found.values(), key=lambda f: (f[0], f[2])):
            p = project.path(rel)
            # The name is the one the namespace lists (a link's own), the path the file's identity,
            # and the import record the one beside the entry, where import wrote it.
            item = {"name": Path(name).stem, "path": rel, "kind": kind, "bytes": size}
            family = IMPORT_FAMILIES.get(kind)
            beside = project.path(place).with_suffix(".meta.json") if family else None
            meta = read_json(beside) if beside else None
            unsupported = (relations.unsupported_reason(meta, frozenset({family}))
                           if isinstance(meta, dict) else None)
            if unsupported:
                item["unsupported_record"] = {"location": project.rel(beside),
                                              "reason": unsupported}
            elif isinstance(meta, dict):
                item.update(rows=meta.get("rows"), columns=len(meta.get("columns") or []),
                            sha256=meta.get("sha256"), description=meta.get("description"),
                            source=(meta.get("source") or {}).get("path"),
                            imported_at=meta.get("imported_at"))
            history = produced.get(rel)
            if history:
                last = history[-1]
                item["producers"] = [h["run"] for h in history]
                item.setdefault("rows", last.get("rows"))
                item.setdefault("columns", len(last.get("schema") or []) or None)
                item.setdefault("sha256", last.get("sha256"))
            item["used_by"] = sorted(set(used.get(rel, [])))
            inferred: dict[str, list[str]] = {}
            if model is not None and sha is not None:
                current = sha(p)
                candidates, through_candidates = (model.producer_candidates(rel, current)
                                                  if current else ([], []))
                item["producer_state"] = ("unknown" if not current else "none" if not candidates
                                          else "resolved" if len(candidates) == 1 else "ambiguous")
                item["producer_candidates"] = candidates
                if len(candidates) == 1:
                    item["producer"] = candidates[0]
                item["written_by"] = list(dict.fromkeys(written.get(rel, [])))
                if through_candidates:
                    inferred["producer_candidates"] = through_candidates
            for name in ("producers", "used_by", "written_by"):
                runs_in = inferred_in(rel, name)
                if runs_in and name in item:
                    inferred[name] = [r for r in dict.fromkeys(item[name]) if r in runs_in]
            if inferred:
                item["inferred"] = inferred
            items.append(item)
    return items


# The import record each kind of dataset has beside it (design 2.3 and 2.7).
IMPORT_FAMILIES = {"imported": relations.DATASET_V2, "raw": relations.DATASET_V1}
# The order the dataset kinds take view names in: imported data first where there is any (design D24).
KIND_ORDER = {"imported": 0, "raw": 1, "derived": 2}
# The dataset directories of each layout, each with its kind, in the order they are listed and take
# view names in. A lab.json project's are data/raw (its imports) and data/derived, as before #13: a
# data/imported/ there is a directory of the person's, not the kit's, and lists nothing. The new
# format's layout (design 2.3) adds data/imported/, the new writer's imports, ahead of data/raw (D24).
LEGACY_DATASET_DIRS = ((writer.LEGACY_LAYOUT.imported_dir, "raw"), ("data/derived", "derived"))
DATASET_DIRS = (("data/imported", "imported"), *LEGACY_DATASET_DIRS)


def dataset_dirs(project: Project) -> tuple[tuple[str, str], ...]:
    """The dataset directories of `project` and their kinds, by its layout (Project.layout, the one
    its marker picked when it was opened, design 2.3): a lab.json project's (a lab.json of any
    schema) or the new format's (an analytics.json, whatever it holds, and a directory with no
    marker or two, which no read but a run's own reaches — a run reads its project as it opened
    it)."""
    return LEGACY_DATASET_DIRS if project.layout is writer.LEGACY_LAYOUT else DATASET_DIRS


def dataset_name_taken(project: Project, name: str) -> str | None:
    """Path of an existing dataset that `name` would shadow or collide with."""
    for ds in list_datasets(project):
        if ds["name"] == name:
            return ds["path"]
    return None


def _views(datasets: list[dict]) -> list[tuple[str, dict]]:
    """(view name, dataset) in the order names are given: a stem that follows the kit's dataset
    name rule (DATASET_NAME_RE, the names import and save give) first, then imported (data/imported,
    listed in an analytics.json project only, design D24) before raw before derived, then by path.
    So a file a person put at data/raw/Sales.parquet never takes `sales` from the dataset the kit
    imported or saved as `sales` (ADR-0006 9). A later name that collides gets `_derived` (derived)
    and then `_2`, `_3`…"""
    out: list[tuple[str, dict]] = []
    taken: set[str] = set()
    for ds in sorted(datasets, key=lambda d: (not DATASET_NAME_RE.match(d["name"]),
                                              KIND_ORDER.get(d["kind"], len(KIND_ORDER)), d["path"])):
        name = re.sub(r"[^a-z0-9_]", "_", ds["name"].lower()) or "dataset"
        if name[0].isdigit():
            name = f"t_{name}"
        if name in taken:
            name = f"{name}_derived" if ds["kind"] == "derived" else name
            n = 2
            while name in taken:
                name = f"{name.rstrip('0123456789_')}_{n}"
                n += 1
        taken.add(name)
        out.append((name, ds))
    return out


def view_names(datasets: list[dict]) -> dict[str, str]:
    """Dataset view name -> path (_views)."""
    return {name: ds["path"] for name, ds in _views(datasets)}


def case_only_datasets(datasets: list[dict]) -> list[list[dict]]:
    """The datasets whose names differ only in case (or Unicode normalization), each group with
    every member's name, path and view: a file system that ignores case holds one of them, so
    the project means another file there."""
    groups: dict[str, list[dict]] = {}
    for view, ds in _views(datasets):
        groups.setdefault(folded(ds["name"]), []).append(
            {"name": ds["name"], "path": ds["path"], "view": view})
    return [sorted(group, key=lambda d: (d["name"], d["path"])) for group in groups.values()
            if len({d["name"] for d in group}) > 1]


# --- overview for tools, prompts and the report ---

ANALYSIS_PACKAGES = ("duckdb", "polars", "pyarrow", "matplotlib", "plotly", "numpy")


def locked_versions(project: Project) -> dict[str, str]:
    """Versions of the analysis libraries pinned in the project's uv.lock."""
    try:
        with open(project.root / "uv.lock", "rb") as fh:
            lock = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    found = {p.get("name"): p.get("version") for p in lock.get("package", [])}
    return {name: found[name] for name in ANALYSIS_PACKAGES if found.get(name)}


def versions_line(versions: dict[str, str]) -> str:
    return ", ".join(f"{k} {v}" for k, v in versions.items())


# How many runs status() lists, latest first; charts, open items and key information get twice as
# many.
STATUS_LIMIT = 8


def status(project: Project, limit: int = STATUS_LIMIT, *, part=None, asset_session=None, **options) -> dict:
    """The project overview analytics_status, /analytics status and the kit's `status` give, from
    one read of the records (read_model): the identifiers and the read's `as_of_seq` and
    `source_revision` are those the Workspace shows."""
    if part is not None:
        return retrieve(project, part=part, limit=limit, **options)
    if options:
        raise AnalyticsError("bad_query", "retrieval arguments require part")
    marker = readable(project, "status")
    cache = HashCache(project)
    model = read_model(project, cache)
    journal = model.journal
    lists = JournalLists(journal)
    events, source = lists.events, lists.source
    runs = model_runs(project, model)
    datasets = list_datasets(project, runs, model=model, sha=cache.sha)
    fresh = Freshness(project, runs, cache, model)
    charts = chart_index(runs, model.read_as)
    context_text = context_file_text(project)
    _, _, context_omitted, context_truncated = context_selection(context_text)
    current_charts = [c for c in charts if not c.get("retired")]
    chart_rows = []
    for c in current_charts[:limit * 2]:
        f = fresh.of(c["run"])
        chart_rows.append({"key": c["key"], "title": c.get("title"), "run": c["run"],
                           "kind": c.get("kind"), "versions": len(c.get("versions") or []),
                           "has_data": bool(c.get("data")), "stale": f["stale"],
                           "changed_inputs": f["changed_inputs"],
                           "stale_upstream": f["stale_upstream"],
                           "ambiguous_inputs": f["ambiguous_inputs"], "cycle": f["cycle"],
                           "inferred_versions": c["inferred_versions"],
                           "inferred_inputs": f["inferred_inputs"],
                           "inferred_upstream": f["inferred_upstream"],
                           "code": f["code"], "change_reasons": f["change_reasons"],
                           "code_reason": f["code_reason"]})
    recording = {r["run_id"]: r for r in model.runs()}
    run_rows = []
    for r in runs[-limit:][::-1]:
        rec = recording.get(r["id"]) or {}
        run_rows.append({"id": r["id"], "status": r.get("status"), "title": r.get("title"),
                         "duration_s": r.get("duration_s"),
                         "charts": len(relations.run_visualizations(r)),
                         "error": (r.get("error") or {}).get("summary"),
                         "rerun_of": r.get("rerun_of"),
                         "verdict": (relations.run_reproduction(r) or {}).get("verdict"),
                         "recording_status": rec.get("recording_status"),
                         "reasons": rec.get("reasons", [])})
    open_items, key_items, gaps = lists.open, lists.key, lists.feedback
    key_rows = []
    for e in key_items[-limit * 2:]:
        name = source[id(e)]
        key_rows.append({**_brief(e, name), "references": references_summary(model.references(name))})
    refused = writer.refusal(marker, "writes", writer_version=KIT_VERSION)
    _, migrated = (migration_mapping(project.root, marker.value) if marker.state == writer.CURRENT
                   else (None, None))
    older_above = older_project_above(project.root) if marker.state == writer.CURRENT else None
    skipped = ["hash_cache"] if cache.save() else []
    versions = context_versions(project)
    config = dict(marker.value) if isinstance(marker.value, dict) else {}
    metric_view = metric_records(project, model=model)
    metric_counts = {name: len(metric_view[name]) for name in ("definitions", "focus_versions", "observations")}
    metric_view["counts"] = metric_counts
    for name in metric_counts:
        metric_view[name] = metric_view[name][-limit * 2:]
    metric_view["omitted"] = {name: metric_counts[name] - len(metric_view[name]) for name in metric_counts}
    metric_view["read_more"] = str(project.journal)
    suggestion_view = (asset_suggestion_status(project, journal=journal, marker=config, session=asset_session)
                       if any(key in config for key in ("asset_suggestions", "reuse_proposals", "skill_proposals"))
                       or any(event.kind in ("asset_notification", "asset_mode") for event in journal.events) else None)
    return {
        "project": {"root": str(project.root),
                    "title": str(config.get("title") or project.root.name),
                    "created_at": config.get("created_at"),
                    "marker": marker.marker, "format": marker.format,
                    "writable": refused is None,
                    **({"write_refusal": refused.to_json()["error"]} if refused else {}),
                    **({"migration_hint": writer.migration_hint(project.root)}
                       if marker.state == writer.LEGACY else {}),
                    **({"older_project_above": older_above} if older_above else {}),
                    **({"migration": migrated} if migrated else {}),
                    "goal": context_goal(newlines_as_lf(context_text)),
                    "context_chars": len(newlines_as_lf(context_text)),
                    "context_omitted": [s["name"] for s in context_omitted],
                    "context_truncated": context_truncated,
                    "context_updated": _version_time(versions[-1]) if versions else None},
        "workspace": str(project.workspace_dir / "index.html"),
        "versions": locked_versions(project),
        "counts": {"datasets": len(datasets), "runs": len(runs),
                   "runs_failed": sum(1 for r in runs if r.get("status") != "ok"),
                   "charts": len(current_charts), "events": len(events),
                   "open": len(open_items), "gaps": len(gaps)},
        "as_of_seq": model.as_of_seq,
        "source_revision": model.source_revision,
        "updating": model.updating,
        "unsupported": list(model.unsupported),
        "damaged": {"journal": [f"journal:{n}" for n in journal.damaged],
                    "recovered": [f"journal:{n}" for n in journal.recovered],
                    "files": list(model.damaged_files)},
        "writes_skipped": skipped,
        "shared_ids": {i: [f"journal:{n}" for n in lines]
                       for i, lines in journal.lines_of.items() if len(lines) > 1},
        "datasets": datasets,
        "views": view_names(datasets),
        "case_only_datasets": case_only_datasets(datasets),
        "runs": run_rows,
        "charts": chart_rows,
        "open": [_brief(e, source[id(e)]) for e in open_items[-limit * 2:]],
        "key": key_rows,
        "recent": [_brief(e, source[id(e)]) for e in events[-10:]],
        "metrics": metric_view,
        **({"asset_suggestions": suggestion_view} if suggestion_view is not None else {}),
    }


class QueryIndex:
    """Disposable SQLite lexical adapter. Its entire projection is checked against source
    documents before it can narrow a query; neither stored text nor ghost IDs are answers."""
    format = "retrieval/1"

    def __init__(self, project: Project, *, fts=True):
        self.project, self.fts = project, fts
        self.path = project.cache / "retrieval.sqlite3"

    def candidates(self, documents: dict[str, str], query: str, revision: str,
                   policy: relations.QueryPolicy) -> tuple[set[str] | None, dict]:
        expected = sorted(documents.items())
        status = {"adapter": "sqlite", "state": "ready", "fts": False, "rebuilt": False}
        connection = None
        try:
            if self.path.is_symlink() or self.path.parent.is_symlink():
                raise OSError("unsafe_cache_path")
            if not self.path.is_file():
                status["state"] = "missing"
            else:
                connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
                metadata = dict(connection.execute("SELECT key, value FROM metadata"))
                if metadata.get("revision") != revision or metadata.get("format") != self.format or metadata.get("policy") != policy.version:
                    status["state"] = "stale"
                elif list(connection.execute("SELECT id, text FROM documents ORDER BY id")) != expected:
                    status["state"] = "corrupt"
                else:
                    status["fts"] = metadata.get("fts") == "1"
            if status["state"] != "ready":
                if connection is not None:
                    connection.close()
                    connection = None
                if admit(self.project, "rebuild the retrieval index", side=True) is None:
                    status["write_skipped"] = True
                    return None, status
                self.project.cache.mkdir(parents=True, exist_ok=True)
                # File replacement goes through the project's platform port. A unique temporary
                # database means concurrent readers/builders never share an in-place rebuild.
                temporary = self.project.cache / ("retrieval-" + writer.new_request_id() + ".tmp")
                try:
                    with contextlib.closing(sqlite3.connect(temporary, timeout=0.1)) as build, build:
                        build.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, text TEXT NOT NULL)")
                        build.executemany("INSERT INTO documents VALUES (?, ?)", expected)
                        try:
                            if self.fts:
                                build.execute("CREATE VIRTUAL TABLE vocabulary USING fts5(id UNINDEXED, text)")
                                build.executemany("INSERT INTO vocabulary VALUES (?, ?)", expected)
                                status["fts"] = True
                        except sqlite3.DatabaseError:
                            status["fts"] = False
                        build.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                        build.executemany("INSERT INTO metadata VALUES (?, ?)",
                                          [("revision", revision), ("format", self.format),
                                           ("policy", policy.version), ("fts", str(int(status["fts"])))] )
                    self.project.platform.files.replace(temporary, self.path)
                finally:
                    with contextlib.suppress(OSError):
                        temporary.unlink()
                status["rebuilt"] = True
                # The rebuilding call answers from source, and explicitly says so. The next call
                # can use the validated cache. A cache error cannot change a fact or its state.
                return None, status
            terms = policy.terms(query)
            sql = "SELECT id FROM documents" + (" WHERE " + " AND ".join("instr(text, ?) > 0" for _ in terms) if terms else "")
            return {row[0] for row in connection.execute(sql, terms)}, status
        except (OSError, sqlite3.DatabaseError, ValueError):
            # Bad SQL schema, a truncated DB and permissions are all cache failures, not source
            # failures. Rebuild on the next call after detecting an unreadable database.
            status.update(state="corrupt", fallback="source")
            if connection is not None:
                connection.close()
                connection = None
            if admit(self.project, "discard a corrupt retrieval index", side=True) is not None:
                if not self.path.is_symlink() and not self.path.parent.is_symlink():
                    with contextlib.suppress(OSError):
                        self.path.unlink()
            else:
                status["write_skipped"] = True
            return None, status
        finally:
            if connection is not None:
                connection.close()


def _query_record(model, source):
    if (found := model.record_payload(source)) is not None:
        return found
    if len(model.journal.lines_of.get(source, [])) > 1:
        raise AnalyticsError("ambiguous_id", "several journal lines share this ID",
                             details={"candidates": [f"journal:{n}" for n in model.journal.lines_of[source]]})
    raise AnalyticsError("record_not_found", "no readable record with this ID", details={"id": source})


def retrieve(project: Project, *, part="records", query="", id=None, cursor=None, limit=8,
             kind=None, state=None, since=None, until=None, direction="both", as_of_seq=None,
             policy=None, index=None) -> dict:
    """Shared bounded read for the CLI, SDK and existing analytics_status tool. Journal search,
    full selected records, CONTEXT sections and one-hop reference judgments share one read basis.
    as_of_seq replays journal knowledge; physical evidence availability is checked now."""
    readable(project, "retrieve")
    policy = policy or relations.QueryPolicy()
    if part not in ("records", "relations", "context") or direction not in ("both", "outgoing", "incoming"):
        raise AnalyticsError("bad_query", "unknown retrieval part or direction")
    for value in (query, id, cursor, kind, state, since, until):
        if value is not None and not isinstance(value, str):
            raise AnalyticsError("bad_query", "query arguments must be strings")
    if as_of_seq is not None and (not isinstance(as_of_seq, int) or isinstance(as_of_seq, bool) or as_of_seq < 0):
        raise AnalyticsError("bad_query", "as_of_seq must be a nonnegative integer")
    for value in (since, until):
        if value is not None:
            instant = relations._instant(value)
            if instant is None or instant.utcoffset() is None:
                raise AnalyticsError("bad_query", "since and until must be ISO timestamps with UTC offsets",
                                     hint="use a bound such as 2030-01-01T00:00:00+00:00; timezone is required")
    if part != "records" and (query or kind or state or since or until):
        raise AnalyticsError("bad_query", "search filters belong to records")
    if id is not None and (query or kind or state or since or until):
        raise AnalyticsError("bad_query", "selected ID and search filters are separate reads")
    if part == "relations" and not id:
        raise AnalyticsError("bad_query", "relations requires an ID")
    checkpoint_id = part == "context" and isinstance(id, str) and (id.startswith("e") or id.startswith("journal:"))
    if part == "context" and as_of_seq is not None and not checkpoint_id:
        raise AnalyticsError("bad_query", "historical context requires its exact context version ID")
    observed = []
    for _attempt in range(3):
        cache = HashCache(project)
        model = read_model(project, cache)
        revision, snapshot = model.source_revision, model.source_snapshot
        reader_updating = model.updating
        if as_of_seq is not None:
            if as_of_seq > model.as_of_seq:
                raise AnalyticsError("bad_query", "as_of_seq is beyond the observed journal")
            model = model.through(as_of_seq)
        legacy_resolved = model.journal.legacy_resolutions()
        request = {"part": part, "query": query, "id": id, "kind": kind, "state": state,
                   "since": since, "until": until, "direction": direction, "as_of_seq": as_of_seq}
        index_state = {"adapter": "source", "state": "not_needed"}
        if part == "records" and id is None:
            candidates = None
            if query and as_of_seq is None:
                documents = {model.journal.source_of(event): policy.document(event) for event in model.journal.events}
                candidates, index_state = (index or QueryIndex(project)).candidates(documents, query, revision, policy)
                index_state["answered_from"] = "index_candidates_and_source" if candidates is not None else "source"
            selected = policy.select(model.journal, query=query, kind=kind, state=state,
                                     since=since, until=until, candidates=candidates)
            rows = [{"id": model.journal.source_of(event), "kind": event.kind, "seq": event.seq,
                     "recorded_at": event.recorded_at, "summary": _one_line(event.value.get("text"), 240),
                     "text_chars": len(str(event.value.get("text") or "")),
                     "summary_omitted": len(str(event.value.get("text") or "")) > 240,
                     "record_state": model.journal.replay().state_of(model.journal.source_of(event)),
                     "legacy_resolved_by": legacy_resolved.get(model.journal.source_of(event), []),
                     "read_more": {"part": "records", "id": model.journal.source_of(event),
                                   **({"as_of_seq": as_of_seq} if as_of_seq is not None else {})}} for event in selected]
        elif part == "records":
            record, location = _query_record(model, id)
            rows = [{"field": key, "value": value} for key, value in record.items()]
        elif part == "relations":
            # Validate the source except exact target identities (run IDs and Visualization IDs)
            # that appear as reference targets. Incoming lookup never invents a version.
            sources = model.incoming_sources(id) if direction != "outgoing" else []
            if not sources:
                _query_record(model, id)
            rows = model.reference_neighborhood(id, direction)
        elif checkpoint_id:
            event = model.journal.event_of(id)
            if event is None or event.kind != "checkpoint":
                raise AnalyticsError("record_not_found", "no checkpoint with this exact ID")
            try:
                result = checkpoint_view(model, event, limit=limit, cursor=cursor)
            except relations.QueryError as error:
                raise AnalyticsError(error.code, str(error), details=error.details) from None
            result = {**result["updates"], "checkpoint": {key: value for key, value in result.items() if key != "updates"}}
            rows = None
        else:
            if id is None or id == "context:current":
                text, location = context_file_text(project), "CONTEXT.md"
            elif id.startswith("context:"):
                name = id[len("context:"):]
                path = relations.context_file(project.root, name)
                if path is None:
                    raise AnalyticsError("record_not_found", "no preserved CONTEXT version", details={"id": id})
                text, location = path.read_text(encoding="utf-8"), str(path.relative_to(project.root))
            else:
                raise AnalyticsError("bad_query", "context ID must be context:current or context:<version>")
            preamble, sections = context_sections(text)
            rows = ([{"name": "Preamble", "text": preamble, "line": 1}] if preamble else []) + sections
        try:
            result = result if checkpoint_id else policy.page(rows, revision=revision, request=request, cursor=cursor, limit=limit,
                                 journal_order=part == "records" and id is None and not query,
                                 as_of_seq=as_of_seq,
                                 boundary_revision=model.journal.prefix_revision(as_of_seq)
                                 if as_of_seq is not None else None)
        except relations.QueryError as error:
            raise AnalyticsError(error.code, str(error), details=error.details) from None
        if part == "records" and id is None:
            for row in result["items"]:
                row["evidence"] = references_summary(model.references(row["id"]))
                row["interpretation"] = "recorded judgment; data may have changed; conclusion review is separate"
        elif part == "records":
            result.update(id=id, location=location, record_state=model.journal.replay().state_of(id),
                          legacy_resolved_by=legacy_resolved.get(id, []),
                          evidence=references_summary(model.references(id)),
                          interpretation="recorded judgment; data may have changed; conclusion review is separate")
        elif part == "context":
            if not checkpoint_id:
                result.update(location=location, checkpoint=checkpoint_view(model, limit=limit),
                              context_pending=context_pending(project))
        if part == "records" and id is not None:
            event = model.journal.event_of(id)
            if event is not None and event.kind == "checkpoint":
                result["checkpoint"] = checkpoint_view(model, event, limit=limit)
        frame = _project_migration(project)[2] if project.marker_state().state == writer.CURRENT else None
        after, current_revision = relations.observed_basis(project.root, frame)
        observed.append(current_revision)
        changed = snapshot != after
        if not changed and not reader_updating:
            break
    else:
        result["items"], result["returned"], result["cursor"] = [], 0, None
        result["has_more"], result["read_more"] = False, None
        result["omitted"] = {"count": "unknown", "reason": "updating"}
    result.update(source_revision=observed[-1], as_of_seq=as_of_seq if as_of_seq is not None else model.as_of_seq,
                  updating=changed or reader_updating, observed_revisions=observed,
                  index=index_state, unsupported=list(model.unsupported),
                  damaged={"journal": model.journal.damaged, "files": model.damaged_files},
                  evidence_availability="checked now; as_of_seq bounds recorded knowledge, not file snapshots",
                  time_filter_scope="only parseable UTC-offset recorded timestamps" if since or until else None,
                  search_scope="readable journal records" if part == "records" and id is None else part)
    if result["updating"]:
        result["total"] = "unknown"
        if result.get("checkpoint") is not None:
            result["checkpoint"].update(needs_review=None, updates_available=None)
            result["checkpoint"]["qualification"].append("checkpoint_knowledge_updating")
        for field in ("record_state", "legacy_resolved_by", "evidence", "interpretation"):
            result.pop(field, None)
    result["absence_is_not_proof"] = "no matches means no lexical match in this scope; unreadable or differently worded records may exist"
    if since or until:
        result["absence_is_not_proof"] += "; records with unknown or naive timestamps are excluded from this period scope"
    return result


def retrieval_text(result: dict) -> str:
    """Whole structured fields and explicit next-page instructions, never clipping JSON/text."""
    return json.dumps(result, ensure_ascii=False, indent=2)


# The order a judgment's states are counted in (ADR-0006 4).
REFERENCE_STATES = ("resolved", "content_changed", "missing", "version_unbound", "ambiguous",
                    "unknown", "unsupported")


def reference_label(judgment: dict) -> str:
    """The identifier a screen gives a reference's target, the same on every screen: a logical key
    (`script::chart`, a path), an event id, `<run_id>/<visualization_id>`, a run id, a CONTEXT
    version, a Report revision, or the name a writer gave. A reference this kit cannot read is named
    by where it is in the record: `refs.<field>` (/1) or `refs[<index>]` (/2)."""
    target = judgment.get("target") or {}
    if "key" in target:
        return str(target["key"])
    if target.get("visualization_id") is not None:
        run = target.get("run_id")
        return f"{run}/{target['visualization_id']}" if run else str(target["visualization_id"])
    if "report_id" in target:
        return f"report:{target.get('report_id')}/{target.get('revision_id')}"
    for field in ("event_id", "run_id", "version", "path", "token"):
        if field in target:
            return str(target[field])
    ref = judgment.get("ref") or {}
    if ref.get("field") is not None:
        return f"refs.{ref['field']}"
    return f"refs[{ref['index']}]" if ref.get("index") is not None else "refs"


def references_summary(judgments: list[dict]) -> dict:
    """How many of a record's references are in each state, how many are confirmed links (ADR-0006
    3), and the target of every one that is not `resolved`."""
    states: dict[str, int] = {}
    for judged in judgments:
        states[judged["state"]] = states.get(judged["state"], 0) + 1
    return {"states": {s: states[s] for s in REFERENCE_STATES if s in states},
            "confirmed": sum(1 for judged in judgments if judged["confirmed"]),
            "not_resolved": [{"state": judged["state"], "target": reference_label(judged)}
                             for judged in judgments if judged["state"] != "resolved"]}


def _version_time(path: Path) -> str | None:
    try:
        return datetime.strptime(path.stem.split("_")[0], "%Y%m%d-%H%M%S-%f").isoformat(
            timespec="seconds")
    except ValueError:
        return None


def _brief(event: dict, source: str | None = None) -> dict:
    """An entry of a status list. `source` is how the read names the record: its id, or
    `journal:<line>` when several lines have that id."""
    return {"id": event.get("id"), "source": source or event.get("id"), "ts": event.get("ts"),
            "kind": event.get("kind"), "actor": event.get("actor"), "text": event.get("text"),
            "refs": event.get("refs") or {}}


def _one_line(text, width=160) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= width else s[:width - 1] + "…"


def _fmt_int(value) -> str:
    return f"{value:,}" if isinstance(value, int) else "?"


# How long analytics_status's overview may be (the extension's reply adds its first line), not counting
# the lines that only say what a list leaves out and where it is (and the journal's path): they are
# budgeted apart, a few short lines at most, and never make a list shorter.
STATUS_CHARS = 9000
# The fewest entries an overview longer than STATUS_CHARS keeps of each list that has no limit of
# its own: the CONTEXT.md sections left out of the system prompt, the query views, the datasets,
# and the changed inputs of a chart.
STATUS_FLOORS = {"context": 20, "views": 40, "datasets": 20, "stale": 5}
# How to list every dataset view with analytics_query: by name, a page of 50 (its default limit) at a
# time.
VIEWS_SQL = "SELECT table_name FROM information_schema.tables ORDER BY table_name LIMIT 50 OFFSET 0"
VIEWS_PAGES = (f"call analytics_query with \"{VIEWS_SQL}\" and again with OFFSET 50, 100, … until it "
               "returns fewer than 50 rows")
STATUS_PARTS = {"head": "lines about the project", "datasets": "datasets", "runs": "runs",
                "charts": "charts", "open": "open questions/todos", "key": "key information",
                "recent": "recent journal"}


def status_text(st: dict, max_chars: int = STATUS_CHARS) -> str:
    """The overview analytics_status replies with. Each list is whole, or says how many it leaves out and
    where they are.

    `max_chars` bounds the overview without the lines that only say what a list leaves out and
    where (_status_lines' notes): those are budgeted apart, so an overview that fitted before they
    were added is never shortened for them. Within `max_chars`, the lists status() limits (runs,
    charts, open items, key information, the recent journal) are the only ones shortened. A longer
    overview shortens the lists that have no limit of their own, the one that saves the most
    first, each to the most entries that fit (at least its STATUS_FLOORS) and a count of the rest
    with where to find them, until it fits. If it still does not, it ends at a line end with a line
    that names what it leaves out and where to read it."""
    lines = _status_lines(st, {})
    full = _counted(lines)
    if full <= max_chars:
        return _joined(lines)
    saved = {name: full - _counted(_status_lines(st, {name: floor}))
             for name, floor in STATUS_FLOORS.items()}
    caps: dict[str, int] = {}
    for name in sorted(saved, key=lambda n: -saved[n]):
        if saved[name] <= 0:
            continue

        def fits(cap: int, name: str = name) -> bool:
            return _counted(_status_lines(st, {**caps, name: cap})) <= max_chars

        low, high = STATUS_FLOORS[name], _status_list_len(st, name) - 1
        if fits(low):
            while low < high:  # the most entries that fit: one more does not
                mid = (low + high + 1) // 2
                low, high = (mid, high) if fits(mid) else (low, mid - 1)
            return _joined(_status_lines(st, {**caps, name: low}))
        caps[name] = low
    return _status_cut(_status_lines(st, caps), Path(st["project"]["root"]), max_chars)


def _joined(lines: list[tuple[str, str, bool]]) -> str:
    return "\n".join(line for _, line, _ in lines)


def _counted(lines: list[tuple[str, str, bool]]) -> int:
    """How long the overview is as STATUS_CHARS counts it: its lines but the notes."""
    return len("\n".join(line for _, line, note in lines if not note))


def _capped(items: list, cap: int | None) -> list:
    return items if cap is None or len(items) <= cap else items[:cap]


def _status_list_len(st: dict, name: str) -> int:
    """How many entries the list STATUS_FLOORS calls `name` has (for "stale", the longest)."""
    if name == "context":
        return len(st["project"].get("context_omitted") or [])
    if name == "views":
        return len(st["views"])
    if name == "datasets":
        return len(st["datasets"])
    return max((len(c["changed_inputs"]) + len(c["stale_upstream"])
                for c in st["charts"] if c["stale"]), default=0)


# How a screen says a `changed` file differs only in its line ends (ADR-0007 decision 4).
LINE_ENDINGS_NOTE = "(line endings only)"
# How a screen says a relation was found only by reading a path a kit before PATH_IDENTITY_SINCE
# recorded as today's identity path (ADR-0006 9, basis inferred): never an observed or confirmed one.
INFERRED_NOTE = "inferred from an older kit's path"


def _stale_reasons(shown: list[str], direct: int, line_endings=frozenset(),
                   inferred=frozenset()) -> str:
    """Why a chart is stale: its run's changed or missing inputs (the first `direct` of `shown`), then
    the stale runs upstream. Those output the same bytes as an input: producer candidates, not known
    producers (ADR-0006 4). An input in `line_endings` differs from its record only in its line ends
    and says so; a run in `inferred` is a candidate only through an older kit's path and says so."""
    paths = [f"{p} {LINE_ENDINGS_NOTE}" if p in line_endings else p for p in shown[:direct]]
    runs = [f"{r} ({INFERRED_NOTE})" if r in inferred else r for r in shown[direct:]]
    if not runs:
        return ", ".join(paths)
    upstream = ("upstream candidate " if len(runs) == 1 else "upstream candidates ") + ", ".join(runs)
    return "; ".join([", ".join(paths), upstream] if paths else [upstream])


def _lone_cr_lines(path: Path) -> bool:
    """Whether a file's lines end in a lone CR: it has a CR and no LF."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return b"\r" in data and b"\n" not in data


def _status_lines(st: dict, caps: dict[str, int]) -> list[tuple[str, str, bool]]:
    """The overview's lines, each with the part of it (STATUS_PARTS) it belongs to and whether it
    is a note: a line that only says what a list leaves out and where (the journal's path and the
    count of the entries the lists status() limits leave out), or what CONTEXT.md's text above its
    first heading leaves out. `caps` shortens the lists STATUS_FLOORS names."""
    p, counts = st["project"], st["counts"]
    root = Path(p["root"])
    journal = root / "journal.jsonl"
    out: list[tuple[str, str, bool]] = []

    def add(part: str, line: str, note: bool = False) -> None:
        out.append((part, line, note))

    older_runs = counts["runs"] - len(st["runs"])
    more_charts = counts["charts"] - len(st["charts"])
    older_open = counts["open"] - len(st["open"])
    key_at_limit = len(st["key"]) >= 2 * STATUS_LIMIT
    earlier = counts["events"] - len(st["recent"])
    add("head", f"project: {p['title']} ({p['root']})")
    if p.get("migration_hint"):
        add("head", OLDER_FORMAT_LINE)
    add("head", f"report: {st['workspace']}")
    if p.get("older_project_above"):
        add("head", older_project_above_line(p["older_project_above"]))
    if p.get("write_refusal"):
        add("head", read_only_line(p["write_refusal"], st.get("writes_skipped")))
    migrated = p.get("migration")
    if migrated and not migrated.get("applied"):
        add("head", f"migration {migrated.get('id')}: its mapping is not applied "
                    f"({migrated.get('reason')}), so the paths an older kit recorded in the "
                    "source project are read as recorded — see the migration report "
                    f"{migrated.get('report')}")
    if st.get("source_revision"):
        add("head", _read_line(st))
    if older_runs > 0 or more_charts > 0 or older_open > 0 or key_at_limit or earlier > 0:
        # Where the entries the lists below leave out are: said once, and the lists refer to it.
        add("head", f"journal: {journal}", note=True)
    if p.get("goal"):
        add("head", f"goal: {_one_line(p['goal'])}")
    for line in metric_focus_text(st.get("metrics") or {}, journal):
        add("head", line)
    suggestions = st.get("asset_suggestions")
    if suggestions:
        add("head", f"Asset suggestions: {suggestions['mode']} (model-free; project window)")
        for error in suggestions.get("settings_errors", []):
            add("head", f"  {error['code']}: {error['message']}")
        if suggestions.get("wait_reason") in ("window", "clock_backwards", "malformed_automatic_history", "clock_unavailable", "ambiguous_asset_history"):
            add("head", f"  waiting: {suggestions['wait_reason']}; next automatic time: {suggestions.get('next_automatic_at') or 'unknown'}")
        if suggestions.get("ambiguous_event_ids"):
            add("head", f"  ambiguous asset history: {', '.join(suggestions['ambiguous_event_ids'][:3])}; inspect the original journal records")
        pending = suggestions.get("pending", [])
        for notice in pending[-3:]:
            add("head", f"  pending {notice['event_id']} / {notice['asset_id']}: {_one_line(notice['text'], 140)}")
        if len(pending) > 3:
            add("head", f"  {len(pending) - 3} more recorded suggestions; analytics status --part records --kind asset_notification", note=True)
    if st.get("versions"):
        add("head", f"environment: {versions_line(st['versions'])} — use APIs of these versions")
    for line in _records_lines(st, journal):
        add("head", line)
    if p.get("context_truncated"):
        reach = (f"only the first {CONTEXT_PROMPT_CHARS:,} reach the system prompt, which says "
                 "where to read the rest")
        if _lone_cr_lines(root / "CONTEXT.md"):
            # A lone CR is no line end to the read tool: the file is one line, all of it above
            # any heading (context_sections).
            add("head", f"CONTEXT.md ({p['context_chars']:,} characters) ends its lines in a lone "
                        "CR, which is no line end to the read tool, so it is one line, all of it "
                        f"above any `## ` heading, and {reach} — save it with LF or CRLF line "
                        "ends", note=True)
        else:
            add("head", f"CONTEXT.md ({p['context_chars']:,} characters) has more than "
                        f"{CONTEXT_PROMPT_CHARS:,} characters above its first `## ` heading, and "
                        f"{reach} — split it into `## ` sections so the rest can be read on "
                        "demand", note=True)
    omitted = p.get("context_omitted") or []
    if omitted:
        names = _capped(omitted, caps.get("context"))
        listed = ", ".join(names)
        if len(names) < len(omitted):
            listed += (f" and {len(omitted) - len(names):,} more (the system prompt lists every "
                       f"one with its line range in read {root / 'CONTEXT.md'})")
        add("head", "CONTEXT.md sections not in the system prompt (read them from the file when "
                    f"they matter): {listed} — move a section earlier or mark its heading "
                    "<!-- pin --> to pin it")
    views = list(st["views"])
    shown_views = _capped(views, caps.get("views"))
    view_text = ", ".join(shown_views) or "none"
    if len(shown_views) < len(views):
        view_text += (f" and {len(views) - len(shown_views):,} more ({VIEWS_PAGES} to list every "
                      "view)")
    add("datasets", f"datasets ({counts['datasets']}) — query views: {view_text}")
    for group in st.get("case_only_datasets") or []:
        named = ", ".join(f"{d['name']} ({d['path']}, view {d['view']})" for d in group)
        add("datasets", f"dataset names that differ only in case: {named} — a file system that "
                        "ignores case keeps one of them; rename one")
    raw = {d["name"]: d["path"] for d in st["datasets"] if d.get("kind") == "raw"}
    both = [(d, raw[d["name"]]) for d in st["datasets"]
            if d.get("kind") == "imported" and d["name"] in raw]
    if both:
        # design D24 (R13): the imported data takes the view name, the older import the next one.
        by_path = {path: view for view, path in dict(st["views"]).items()}
        named = ", ".join(f"{d['name']} (view {by_path.get(d['path'])}: {d['path']}; "
                          f"{other} is view {by_path.get(other)})" for d, other in both)
        add("datasets", f"dataset names in both data/imported and data/raw: {named}")
    datasets = _capped(st["datasets"], caps.get("datasets"))
    for ds in datasets:
        add("datasets", f"  {ds['kind']:<7} {ds['name']}: {_fmt_int(ds.get('rows'))} rows × "
                        f"{ds.get('columns') or '?'} cols · {ds['path']} · {dataset_origin(ds)} · "
                        f"used by {len(ds.get('used_by') or [])} run(s)"
                        + _inferred_count((ds.get("inferred") or {}).get("used_by")))
    if len(datasets) < len(st["datasets"]):
        add("datasets", f"  … {len(st['datasets']) - len(datasets):,} more dataset(s) not listed: "
                        f"{VIEWS_PAGES} to list every view; an imported one's record is the "
                        "<name>.meta.json beside its .parquet")
    add("runs", f"runs ({counts['runs']} total, {counts['runs_failed']} not ok; latest first):")
    for r in st["runs"]:
        extra = f" — {_one_line(r['error'], 100)}" if r.get("error") else ""
        if r.get("rerun_of"):
            extra += f" [rerun of {r['rerun_of']}: {r.get('verdict')}]"
        if r.get("recording_status") == "incomplete":
            extra += f" · recording incomplete ({', '.join(r.get('reasons') or [])})"
        dur = f"{r['duration_s']:.1f}s" if isinstance(r.get("duration_s"), (int, float)) else "-"
        add("runs", f"  {r['id']} {r.get('status')} {dur} charts={r['charts']} "
                    f"\"{_one_line(r.get('title'), 80)}\"{extra}")
    if older_runs > 0:
        add("runs", f"  … {older_runs:,} older run(s) not listed: in the journal, and each in "
                    "runs/<id>/run.json", note=True)
    add("charts", f"charts (latest version per script::chart, {counts['charts']}):")
    for c in st["charts"]:
        flags = []
        if c["stale"]:
            changed = c["changed_inputs"] + c["stale_upstream"]
            shown = _capped(changed, caps.get("stale"))
            more = ""
            if len(shown) < len(changed):
                more = (f" and {len(changed) - len(shown):,} more, among the run's inputs in "
                        f"runs/{c['run']}/run.json")
            line_endings = frozenset(p for p, reason in (c.get("change_reasons") or {}).items()
                                     if reason == LINE_ENDINGS_ONLY)
            inferred = frozenset(c.get("inferred_upstream") or ())
            flags.append(f"STALE({_stale_reasons(shown, len(c['changed_inputs']), line_endings, inferred)}"
                         f"{more})")
        if c.get("ambiguous_inputs"):
            flags.append(f"producer ambiguous for {len(c['ambiguous_inputs'])} input(s)")
        if c.get("inferred_inputs"):
            flags.append(f"producer {INFERRED_NOTE} for {len(c['inferred_inputs'])} input(s)")
        if c.get("cycle"):
            flags.append("producer cycle")
        if c["code"] == "changed":
            flags.append("script edited since" + (f" {LINE_ENDINGS_NOTE}"
                                                  if c.get("code_reason") == LINE_ENDINGS_ONLY else ""))
        elif c["code"] == "missing":
            flags.append("script file no longer exists")
        if not c["has_data"] and c.get("kind") == "matplotlib":
            flags.append("plotted data not recorded")
        add("charts", f"  {c['key']} \"{_one_line(c.get('title'), 70)}\" run {c['run']} "
                      f"v{c['versions']}"
                      + (f" ({c['inferred_versions']} {INFERRED_NOTE})" if c.get("inferred_versions")
                         else "")
                      + (" · " + "; ".join(flags) if flags else ""))
    if more_charts > 0:
        add("charts", f"  … {more_charts:,} more chart(s) not listed: the journal entry of the run "
                      "that made each names it", note=True)
    if st["open"]:
        add("open", f"open questions/todos ({counts['open']}):")
        for e in st["open"]:
            add("open", f"  {_entry_name(e)} {e['kind']}: {_one_line(e['text'])}")
        if older_open > 0:
            add("open", f"  … {older_open:,} older open item(s) not listed: in the journal",
                note=True)
    if st["key"]:
        add("key", "key information (findings, decisions, assumptions):")
        for e in st["key"]:
            add("key", f"  {_entry_name(e)} {e['kind']}: {_one_line(e['text'])}"
                       f"{_references_text(e.get('references'))}")
        if key_at_limit:
            add("key", f"  … only the latest {len(st['key'])} are listed; older unresolved ones, if "
                       "any, are in the journal", note=True)
    add("recent", "recent journal:")
    for e in st["recent"]:
        add("recent", f"  {_entry_name(e)} {str(e.get('ts', ''))[:16].replace('T', ' ')} "
                      f"{e['kind']}: {_one_line(e['text'], 120)}")
    if earlier > 0:
        add("recent", f"  … {earlier:,} earlier entries not listed: in the journal", note=True)
    return out


# How many places, producer candidates or targets one line of the overview names before it says how
# many more there are. An id several lines share is named with SHARED_LINES of its lines at most.
STATUS_PLACES = 5
STATUS_CANDIDATES = 3
STATUS_TARGETS = 3
SHARED_LINES = 2
# Where every unsupported or damaged record is listed when a line names only the first ones.
EVERY_RECORD = "the kit's `status` command lists every one in its JSON"


def _places(items: list[str], cap: int, where: str) -> str:
    shown = ", ".join(items[:cap])
    return shown + (f" and {len(items) - cap:,} more ({where})" if len(items) > cap else "")


def shared_ids_text(shared: dict[str, list[str]], cap: int) -> tuple[str, bool]:
    """The ids several journal lines share, each with SHARED_LINES of its lines at most and `cap`
    lines in all, and whether an id or a line is left out (the status JSON's `shared_ids` has them
    all)."""
    parts, left, cut = [], cap, False
    for name, lines in shared.items():
        if left <= 0:
            break
        shown = lines[:min(SHARED_LINES, left)]
        left -= len(shown)
        more = len(lines) - len(shown)
        cut = cut or more > 0
        parts.append(f"{_one_line(name, 80)} ({', '.join(shown)}"
                     + (f" and {more:,} more line(s))" if more else ")"))
    rest = len(shared) - len(parts)
    return ", ".join(parts) + (f" and {rest:,} more id(s)" if rest else ""), cut or rest > 0


def _read_line(st: dict) -> str:
    """The read the overview was made from, as the Workspace's pages name it."""
    line = f"read: as_of_seq {st.get('as_of_seq')} · source_revision {st.get('source_revision')}"
    if st.get("updating"):
        line += (" · updating: the records changed while they were read (read three times); "
                 "ask again for a settled overview")
    return line


# The line the overview and the context block start with in a lab.json project (design 7), so a
# model tells a person instead of trying the refused writes again.
OLDER_FORMAT_LINE = ("Older record format: read only here until a person migrates it "
                     "(analytics migrate --to DEST).")


def older_project_above(root) -> str | None:
    """The nearest directory above an analytics.json project's root that holds a lab.json (design
    4.3): a kit before the new format's marker (kit 0.7.0) looks for lab.json only, so run inside
    this project it would write to that one. Told, never refused."""
    for parent in Path(root).parents:
        with contextlib.suppress(OSError, ValueError):
            if os.path.lexists(parent / writer.LEGACY_MARKER):
                return str(parent)
    return None


def older_project_above_line(above: str) -> str:
    return (f"warning: {above} above this project holds a {writer.LEGACY_MARKER}: a kit older than "
            f"0.7.0 run here would write to that project, not this one")


def read_only_line(refusal: dict, skipped=()) -> str:
    """The line the overview and the context block start with where this kit does not write to the
    project (design 4.10 and 5.2): why, and that the side writes of a read are skipped."""
    line = f"read only: {refusal['code']} — {refusal['message'].rstrip('.')}"
    if refusal.get("hint"):
        line += f"; {refusal['hint'].rstrip('.')}"
    return line + ("; skipped: " + ", ".join(skipped) if skipped else "")


def _records_lines(st: dict, journal: Path) -> list[str]:
    """What the lists below cannot show: records this kit cannot read, lines that do not parse,
    record files not read, and ids that several lines share."""
    out = []
    unsupported = st.get("unsupported") or []
    if unsupported:
        places = [f"{u['location']} ({u['reason']})" for u in unsupported]
        # Only a known family's newer version is one a newer kit may read; another family or an
        # unknown kind of a known version is not.
        newer = sum(1 for u in unsupported if u["reason"] == "unknown_version")
        out.append(f"unsupported: {len(unsupported):,} record(s) this kit cannot read are in no list "
                   f"here and not in the report — {_places(places, STATUS_PLACES, EVERY_RECORD)}"
                   + ("; a newer kit may read them" if newer == len(unsupported) else
                      f"; a newer kit may read the {newer:,} of an unknown version" if newer else ""))
    damaged = st.get("damaged") or {}
    parts = []
    if damaged.get("journal"):
        parts.append(f"{len(damaged['journal']):,} journal line(s) do not parse "
                     f"({_places(damaged['journal'], STATUS_PLACES, EVERY_RECORD)})")
    if damaged.get("recovered"):
        parts.append(f"the record a later write appended onto {len(damaged['recovered']):,} of them "
                     f"is read ({_places(damaged['recovered'], STATUS_PLACES, EVERY_RECORD)})")
    if damaged.get("files"):
        files = [f"{f['location']} ({f['reason']})" for f in damaged["files"]]
        parts.append(f"{len(files):,} record file(s) not read "
                     f"({_places(files, STATUS_PLACES, EVERY_RECORD)})")
    if parts:
        out.append("damaged: " + "; ".join(parts))
    shared = st.get("shared_ids") or {}
    if shared:
        listed, cut = shared_ids_text(shared, STATUS_PLACES)
        if cut:
            listed += " (the kit's `status` command lists every line in its JSON `shared_ids`)"
        out.append(f"ids on several lines: {listed} — each line is its own record, and a reference "
                   "to the id is ambiguous")
    return out


def _inferred_count(runs) -> str:
    """` (N inferred from an older kit's path)` for the runs of a count found only so, else ''."""
    return f" ({len(runs):,} {INFERRED_NOTE})" if runs else ""


def _inferred_names(runs, field: str) -> str:
    """`; inferred from an older kit's path: A, B` naming the runs of a list found only so (three,
    and how many more and where), else ''."""
    if not runs:
        return ""
    return f"; {INFERRED_NOTE}: " + _places(
        runs, STATUS_CANDIDATES, f"the kit's `status` command lists every one in its JSON `inferred.{field}`")


def dataset_origin(ds: dict) -> str:
    """Where a dataset's current bytes come from, as far as the records say (ADR-0006 4): the runs
    whose recorded output had this path and these bytes, the file it was imported from, or neither.
    A run is named as having output the same bytes, not as having made them, and one found only
    through a path a kit before PATH_IDENTITY_SINCE recorded says so (ADR-0006 9)."""
    state = ds.get("producer_state")
    candidates = ds.get("producer_candidates") or []
    inferred = ds.get("inferred") or {}
    if state == "resolved":
        return (f"same bytes as run {candidates[0]}'s output"
                + (f" ({INFERRED_NOTE})" if inferred.get("producer_candidates") else ""))
    if state == "ambiguous":
        listed = _places(candidates, STATUS_CANDIDATES,
                         "the kit's `status` command lists every one in its JSON `producer_candidates`")
        return (f"same bytes as {len(candidates):,} runs: {listed}"
                + _inferred_names(inferred.get("producer_candidates"), "producer_candidates"))
    if ds.get("unsupported_record"):
        record = ds["unsupported_record"]
        return f"import record not read ({record['location']}: {record['reason']})"
    if ds.get("source"):
        return f"from {ds['source']}"
    if state == "unknown":
        return "producer unknown: the file could not be hashed"
    written = ds.get("written_by") or []
    if written:
        listed = _places(written[::-1], STATUS_CANDIDATES,
                         "the kit's `status` command lists every one in its JSON `written_by`")
        return (f"no successful run output these bytes (changed since, or written by a run that did "
                f"not succeed); runs that wrote this path, latest first: {listed}"
                + _inferred_names((inferred.get("written_by") or [])[::-1], "written_by"))
    return "NO PRODUCING RUN (written outside analytics_run: no code or inputs recorded)"


def _entry_name(entry: dict) -> str:
    """An entry's id, and the line that names it when several lines have that id."""
    source = entry.get("source")
    return entry["id"] if not source or source == entry["id"] else f"{entry['id']} ({source})"


# The reference states whose targets the overview names: the ones that need a look. A /1 note's
# chart and data references are left unbound and its other names unresolved as a rule, so those are
# counted only (their targets are in the entry's refs, and in the JSON's not_resolved).
NAMED_STATES = ("missing", "ambiguous", "content_changed", "unsupported")


def _references_text(summary: dict | None) -> str:
    """A key entry's references when one of them needs a look (NAMED_STATES): how many are in each
    state, how many are confirmed links, and the targets of those missing, ambiguous, changed or
    unsupported (the first STATUS_TARGETS of a state; the rest are in the entry's refs in the
    journal). An entry whose references are all resolved, version_unbound or unknown says nothing
    here: its counts are in the JSON's `references`, and the overview keeps its room for entries."""
    if not summary or not any(state in NAMED_STATES for state in summary.get("states") or {}):
        return ""
    targets: dict[str, list[str]] = {}
    for item in summary.get("not_resolved") or []:
        targets.setdefault(item["state"], []).append(_one_line(item["target"], 80))
    parts = []
    for state, count in summary["states"].items():
        if state == "resolved":
            parts.append(f"{count} resolved ({summary.get('confirmed', 0)} confirmed)")
        elif state in NAMED_STATES:
            named = _places(targets.get(state, []), STATUS_TARGETS, "in the entry's refs in the journal")
            parts.append(f"{count} {state} ({named})")
        else:
            parts.append(f"{count} {state}")
    return " · refs: " + ", ".join(parts)


def _status_cut(lines: list[tuple[str, str, bool]], root: Path, max_chars: int) -> str:
    """The overview cut at a line end so that it and a last line naming what it leaves out, and
    where to read that, fit in `max_chars` (its notes not counted)."""
    sizes = [0 if note else len(line) + 1 for _, line, note in lines]
    for keep in range(len(lines) - 1, -1, -1):
        marker = _status_cut_marker(lines[keep:], root, max_chars)
        if sum(sizes[:keep]) + len(marker) <= max_chars:
            return "\n".join([*(line for _, line, _ in lines[:keep]), marker])
    return marker[:max_chars]


def _status_cut_marker(lost: list[tuple[str, str, bool]], root: Path, max_chars: int) -> str:
    parts: dict[str, list[str]] = {}
    for part, line, _ in lost:
        parts.setdefault(part, []).append(line)
    said = []
    for part, lines in parts.items():
        listed = sum(1 for line in lines if line.startswith("  ") and not line.startswith("  … "))
        label = STATUS_PARTS[part]
        if part == "head":
            said.append(f"{len(lines)} {label}")
        elif lines[0].startswith("  "):
            said.append(f"the last {listed} of the {label}" if listed else f"the end of the {label}")
        else:
            said.append(f"the {label}" + (f" ({listed} listed)" if listed else ""))
    where = [f"the journal has an entry for every run, import and note: read "
             f"{root / 'journal.jsonl'}"]
    if "datasets" in parts:
        where.append(f"{VIEWS_PAGES} to list every dataset view")
    return (f"… (truncated at {max_chars:,} characters — not shown: {', '.join(said)}; "
            + "; ".join(where) + ")")


# A `## ` heading marked with this always reaches the system prompt. `<!-- pin -->` is what the
# template writes; `<!-- always -->` and `<!-- 항상 -->` are the marks of earlier templates, which
# projects keep, so they still pin (TERMINOLOGY 6).
PIN_RE = re.compile(r"<!--\s*(pin|always|항상)\s*-->", re.IGNORECASE)


def context_file_text(project: Project) -> str:
    """CONTEXT.md as aelix's read tool reads it: its bytes as UTF-8 (undecodable bytes replaced) and
    its line ends as they are. Text mode would turn a CRLF and a lone CR into "\\n", and number the
    lines differently from the read tool."""
    if not project.context.is_file():
        return ""
    return project.context.read_bytes().decode("utf-8", errors="replace")


def _file_lines(text: str) -> list[str]:
    """A file's lines as aelix's read tool numbers them from 1: split at "\\n" alone, blank lines
    kept, and no line after a last "\\n"."""
    lines = text.split("\n")
    return lines[:-1] if lines[-1] == "" else lines


def _shown(lines: list[str]) -> str:
    """Lines of CONTEXT.md as the system prompt shows them: every line end str.splitlines knows
    (a CRLF, a lone CR, a form feed or U+2028 inside a line too) as "\\n"."""
    return "\n".join("\n".join(lines).splitlines())


def context_sections(text: str) -> tuple[str, list[dict]]:
    """CONTEXT.md split at `## ` headings: (preamble, sections).

    `text` is the file as context_file_text reads it. A section's `line` and `lines` are an offset
    and a limit for aelix's read tool: lines are split at "\\n" alone and numbered from 1, leading
    blank lines included, so a CRLF, a lone CR, a form feed or U+2028 does not move them. A lone CR
    is no line end there, so a file whose lines end in lone CRs is one line, all of it preamble.
    The last section ends at the file's last line that is not blank, as it did when the file was
    stripped before it was split. Headings are found and named without a CRLF's "\\r". The preamble
    and a section's `text` show every line end as "\\n" (_shown), and `chars` is the length of that
    text."""
    lines = _file_lines(text)
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    preamble = _shown(lines[:starts[0]] if starts else lines).strip()
    sections = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        while n + 1 == len(starts) and end > start + 1 and not lines[end - 1].strip():
            end -= 1
        heading = lines[start][3:].strip()
        body = _shown(lines[start:end]).rstrip()
        sections.append({"name": _one_line(PIN_RE.sub("", heading), 48) or f"Section {n + 1}",
                         "pinned": bool(PIN_RE.search(heading)), "line": start + 1,
                         "lines": end - start, "chars": len(body), "text": body})
    return preamble, sections


def context_selection(text: str, budget: int = CONTEXT_PROMPT_CHARS, *,
                      path: Path | str | None = None) -> tuple[str, list, list, bool]:
    """Which CONTEXT.md sections go into the system prompt: pinned ones, then as many as the
    budget allows in file order. The rest are listed with their line ranges so the agent can
    read exactly the one it needs — context is never hidden, only fetched on demand.

    Returns (preamble, included, omitted, preamble_truncated). Text above the first `## ` heading
    cannot be fetched by section, so an oversized one is the only thing ever cut: its first
    `budget` characters are kept, and a line says how much more there is and which lines of the
    file (`path`) hold all of it."""
    preamble, sections = context_sections(text)
    truncated = len(preamble) > budget
    if truncated:
        kept = preamble[:budget].rstrip()
        lines = sections[0]["line"] - 1 if sections else len(_file_lines(text))
        preamble = (f"{kept}\n… (Omitted from current context: {len(preamble) - len(kept):,} more "
                    f"characters above the first `## ` heading — all of it: read "
                    f"{path or 'CONTEXT.md'} offset 1 limit {lines})")
    left = budget - len(preamble)
    included = [s for s in sections if s["pinned"]]
    left -= sum(s["chars"] for s in included)
    omitted = []
    for section in sections:
        if section["pinned"]:
            continue
        if section["chars"] <= left:
            included.append(section)
            left -= section["chars"]
        else:
            omitted.append(section)
    included.sort(key=lambda s: s["line"])
    return preamble, included, omitted, truncated


# How much of a journal entry's text the context block shows.
ENTRY_CHARS = 300


def context_block(project: Project, max_context_chars: int = CONTEXT_PROMPT_CHARS,
                  max_items: int = 12) -> str:
    """Project context appended to the system prompt. Only slow-changing parts, so it stays
    stable across prompts: CONTEXT.md, unresolved key information and open questions."""
    marker = readable(project, "context")
    model = read_model(project)
    lists = JournalLists(model.journal)
    preamble, included, omitted, _ = context_selection(
        context_file_text(project), max_context_chars, path=project.context)
    key, open_items = lists.key, lists.open
    config = dict(marker.value) if isinstance(marker.value, dict) else {}
    parts = [
        "# Active analytics project",
        f"root: {project.root} (title: {str(config.get('title') or project.root.name)})",
    ]
    refused = writer.refusal(marker, "writes", writer_version=KIT_VERSION)
    if marker.state == writer.LEGACY:
        parts.append(OLDER_FORMAT_LINE)
    if refused is None:
        parts.append(f"report: {project.workspace_dir / 'index.html'} (rebuilt after runs, imports "
                     "and notes)")
    else:
        # This kit builds no Workspace where it does not write: no report line, the reason instead.
        parts.append(read_only_line(refused.to_json()["error"]))
    checkpoint = checkpoint_view(model, limit=4)
    if checkpoint is not None:
        parts += ["Checkpoint handoff: " + json.dumps(checkpoint, ensure_ascii=False)]
    parts += [
        "Call analytics_status for datasets, runs, charts and the recent journal.",
        "part=records query=words finds older judgments; "
        "part=records id=ID reads full fields; part=relations id=ID reads evidence.",
        f"Journal knowledge: as_of_seq {model.as_of_seq}; source_revision {model.source_revision}; "
        f"updating {model.updating}. "
        "past judgments are records, not proof of current data or reviewed conclusions.",
        "",
        "## CONTEXT.md",
        preamble or "(empty — fill in goal, questions, data sources and definitions)",
    ]
    parts += [s["text"] for s in included]
    if omitted:
        parts += ["", "### CONTEXT.md sections not repeated here",
                  f"They are part of this project's context — read the one you need from "
                  f"{project.context}:"]
        parts += [f"- {s['name']} ({s['lines']} lines): read offset {s['line']} limit {s['lines']}"
                  for s in omitted]
    view = metric_records(project, model=model)
    if view["focus_state"] != "absent":
        parts += ["", "## Current analysis focus", *metric_focus_text(view, project.journal)]
    if key:
        parts += ["", "## Key information (unresolved journal entries)"]
        parts += _journal_entries(key, max_items, project.journal, refs=True)
    if open_items:
        parts += ["", "## Open questions and todos"]
        parts += _journal_entries(open_items, max_items, project.journal)
    return "\n".join(parts)


def metric_focus_text(view, journal) -> list[str]:
    """A bounded direction summary, with exact IDs and the location of the whole payload."""
    focus = view.get("current_focus")
    if focus is None:
        return ([f"analysis focus: {view.get('focus_state')} — read {journal}"]
                if view.get("focus_state") == "conflict" else [])
    details = focus["details"]
    lines = [f"analysis focus: {focus['event_id']} (purpose; calculation check separate)"]
    for name in ("goal", "question", "activity", "next_check", "change_reason"):
        value = details[name]
        lines.append(f"{name}: {_one_line(value, 240)}")
    selected = details["metrics"]
    lines += [f"metric: {m['metric_id']} @ {m['event_id']} ({_one_line(m['role'], 80)})"
              for m in selected[:8]]
    lines += [f"unresolved: {_one_line(u, 160)}" for u in details["unresolved"][:4]]
    lines.append(f"whole focus and definition versions: read {journal}; "
                 f"{len(selected)} metric(s), {len(details['unresolved'])} unresolved item(s)")
    return lines


def _journal_entries(entries: list[dict], max_items: int, journal: Path,
                     refs: bool = False) -> list[str]:
    """The latest `max_items` entries, a line each with its text cut at ENTRY_CHARS, and when any
    are left out or cut, a line with how many and where to read them."""
    shown = entries[-max_items:]
    lines = [f"- {e['id']} {e['kind']}: {_one_line(e['text'], ENTRY_CHARS)}"
             + (_refs_suffix(e) if refs else "") for e in shown]
    left_out = []
    if len(entries) > len(shown):
        left_out.append(f"{len(entries) - len(shown):,} older entries (the latest {len(shown)} of "
                        f"{len(entries):,} are above)")
    if any(len(" ".join(str(e.get("text") or "").split())) > ENTRY_CHARS for e in shown):
        left_out.append(f"the rest of each entry cut at {ENTRY_CHARS} characters (…)")
    if left_out:
        lines.append(f"- … Omitted from current context: {' and '.join(left_out)} — read "
                     f"{journal} and find them by id")
    return lines


def _refs_suffix(event: dict) -> str:
    runs = (event.get("refs") or {}).get("runs")
    return f" (runs: {', '.join(runs)})" if runs else ""


# --- project creation ---

TEMPLATE_FILES = ("CONTEXT.md", "pyproject.toml", "gitignore", "gitattributes")
# The names a directory init takes as empty may hold at its top, and so may a migration's
# destination (design 6.2: the same rule, empty_directory_entry): Finder makes one where a folder was
# only looked at; macOS also writes an AppleDouble file (is_appledouble) beside a file or folder whose
# attributes a volume cannot keep.
EMPTY_DIRECTORY_NAMES = (".DS_Store",)


def appledouble_name(name: str) -> bool:
    """Whether a name is an AppleDouble file's, `._<name>`: the name only (is_appledouble also asks
    what the entry is)."""
    return len(name) > 2 and name.startswith("._")


def is_appledouble(path) -> bool:
    """Whether `path` is an AppleDouble file: a regular file (by lstat, a link not followed) named
    `._<name>`, what macOS writes beside a file or folder on a volume that keeps no extended
    attributes (FAT, exFAT, some network shares). Operating system metadata as .DS_Store is (the
    owner's decision of 2026-10-09 as corrected, #13 issuecomment-6072183477): a migration does not
    copy it (OQ4), passes over it in its destination (OQ16), and an empty directory may hold it (init,
    a migration's destination). A directory or a link with such a name is an ordinary entry, and so is
    one that cannot be looked at."""
    if not appledouble_name(os.path.basename(path)):
        return False
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def empty_directory_entry(path) -> bool:
    """Whether an entry at the top of a directory leaves it empty for init and for a migration's
    destination (design 6.2): .DS_Store, or an AppleDouble file (a regular file, is_appledouble)."""
    return os.path.basename(path) in EMPTY_DIRECTORY_NAMES or is_appledouble(path)


def init_project(target, *, title=None, goal=None, template_dir=None, origin: Origin,
                 platform: _platform.Platform | None = None) -> Project:
    """A new project of the format this kit writes (analytics.json, analytics.project/2: a new
    project_id, min_writer_version and kit_version this kit's), its first records — `project`, then
    `context` — by `origin`, through the common writer. `target` is a directory that does not exist
    or is empty, and inside no project: a directory above it with either marker, or a migration's
    destination that has not committed, refuses it before anything is made (destination_invalid,
    reason inside_project — the migration's destination rule, OQ9 as the owner widened it on
    2026-10-09), as an older kit run in it would write to the project above. `template_dir` is a path or
    an importlib.resources Traversable. Left out, it is the template next to this file: the aelix
    process loads this module by path, where the resource API cannot work (ADR-0004), and the kit's
    own package data is where this file is. The CLI hands over the same template through the
    resource API."""
    root = Path(target).expanduser()
    if root.exists():
        if not root.is_dir():
            raise AnalyticsError("init_not_directory", f"{root} exists and is not a directory")
        leftovers = [p.name for p in root.iterdir() if not empty_directory_entry(p)]
        if leftovers:
            raise AnalyticsError("init_not_empty", f"{root} is not empty",
                                 hint="choose a new directory; init never adopts existing files",
                                 details={"entries": sorted(leftovers)[:10]})
    above = writer.project_above(os.path.abspath(root))
    if above is not None:
        raise AnalyticsError("destination_invalid", f"{root} is inside {writer.inside(above)}",
                             hint="create the project outside every analytics project and every "
                                  "migration's destination: an older kit run inside it would write to "
                                  "the project above it, and a migration goes on only where nothing "
                                  "but its own copy is; nothing was made",
                             details={"reason": "inside_project", "project": above})
    # Read before anything is created: a kit installed without its template must not leave a
    # half-made project that a second init then refuses as not empty.
    if template_dir is None:
        template_dir = Path(__file__).resolve().parent / "template"
    template = Path(template_dir) if isinstance(template_dir, (str, os.PathLike)) else template_dir
    try:
        texts = {src: (template / src).read_text(encoding="utf-8") for src in TEMPLATE_FILES}
    except OSError as exc:
        raise AnalyticsError("template_missing", f"the project template cannot be read: {exc}",
                             hint="reinstall aelix-analytics; in a checkout, restore src/aelix_analytics/template"
                             ) from exc
    root.mkdir(parents=True, exist_ok=True)
    project = Project(root, platform=platform)
    title = (title or "").strip() or project.root.name
    for d in (project.imported, project.derived, project.scripts, project.runs,
              project.workspace_dir, project.context_history, project.cache):
        d.mkdir(parents=True, exist_ok=True)
    created = iso()
    project_id = writer.new_project_id()
    write_json(project.marker, {"schema": SCHEMA_PROJECT, "project_id": project_id, "title": title,
                                "created_at": created, "min_writer_version": KIT_VERSION,
                                "kit_version": KIT_VERSION}, platform=project.platform)
    # The copies are text the kit writes, LF on every platform (ADR-0007 decision 2): the template is
    # read in text mode, and a line end inside a value filled in is made LF as well.
    fill = {"title": title, "date": created[:10],
            "goal": (goal or "").strip() or "- (The question this analysis answers, and who will use the "
                                             "result for which decision)",
            "slug": slugify(title, 40) or "analysis"}
    for src, dest in (("CONTEXT.md", project.context), ("pyproject.toml", "pyproject.toml"),
                      ("gitignore", ".gitignore"), ("gitattributes", ".gitattributes")):
        text = texts[src]
        for k, v in fill.items():
            text = text.replace("{{" + k + "}}", newlines_as_lf(v))
        write_text(project.path(dest), text)
    write_text(project.path(".python-version"), "3.12\n")
    project.journal.touch()
    # The title whole is the marker's and CONTEXT.md's; the record quotes it (writer.excerpt), so
    # a long one cannot leave a project made without its first record.
    written = writer_for(project, origin).record("project", f"project created: {writer.excerpt(title)}",
                                                 details={"project_id": project_id})
    project.request_id = written.request_id
    sync_context(project, origin=origin)
    with contextlib.suppress(OSError):
        trust_project(project.root, platform=project.platform)
    return project
