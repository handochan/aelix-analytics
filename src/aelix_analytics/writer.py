"""Who may write a project's records, and how: the common record writer (Issue #13,
docs/design/RECORD-WRITER-AND-MIGRATION.md sections 2–4 and 8). Standard library only.

- Which project format a directory holds (`marker_state`): its two markers, `analytics.json` and
  `lab.json`, read afresh every time, nothing cached (design 4.3); with neither, a migration's
  record (`.analytics/migration/` or `.analytics/migration.lock`) makes it a migration's destination
  that has not committed (`incomplete`, Issue #13 PR 2), which no kit writes to or reads.
- Whether this kit writes there (`admit`): every write operation asks before its first side effect —
  a lock file, a directory, a cache, a log line included — and a journal write asks once more under
  the journal lock (core). A write it refuses raises; a side write of a read (`side=True`) is
  skipped instead. Which marker format this kit writes is the one constant WRITABLE:
  `analytics.project/2` since the new writer (PR 3 of #13; `lab.project/1` before it). A lab.json
  project is read and not written: its writes are refused as migration_required (design 7).
- Who writes and how (`Origin`): an entry point says it, the writer never guesses it nor reads the
  environment (design 4.1). A `lab.event/1` record holds its actor and session only; the Writer's
  records hold all of it.
- The error class every refusal is (AnalyticsError), the JSON helpers the kit writes records with
  (`write_json`, `append_jsonl`, `append_line`), the stored names of both layouts (LAYOUT, the new
  format's; LEGACY_LAYOUT, lab.json's; `layout_of` picks one by the marker), the record families the
  Writer writes (FAMILIES) and the sorting of free-form references (`classify_refs`).
- The common writer of the new format (`Writer`, PR 3): analytics.event/2 lines with every Origin
  field, the kinds a person or an agent records (RECORD_KINDS), whole texts up to a limit
  (text_too_long, OQ2), references bound when they are written, the journal lock with the marker
  judged again under it (`Writer.locked`), state changes judged by the reader's own replay
  (state_conflict), and the run and import records. It writes only to analytics.project/2
  projects (Writer.WRITES, which is WRITABLE).

This module imports `_platform` (ports) and `relations` (the read model) and nothing else of the
package: `core` imports it and gives its names under the same names (core.AnalyticsError,
core.write_json, …), so the aelix process, which loads core, loads this file as core's sibling.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import _platform, checkpoints, relations


class AnalyticsError(Exception):
    """An error the kit answers with: `{"ok": false, "error": {code, message, hint?, details?}}`."""

    def __init__(self, code: str, message: str, hint: str | None = None, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details

    def to_json(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.hint:
            error["hint"] = self.hint
        if self.details is not None:
            error["details"] = self.details
        return {"ok": False, "error": error}


# --- the stored layouts -------------------------------------------------------------------------

# The directories of a project that are the person's and never a run's data inputs or outputs, in
# either layout: its scripts, version control, tools' caches and agents' folders (design 2.3).
PERSONS_IGNORED = frozenset({"scripts", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".omc",
                             ".aelix", "node_modules", ".ipynb_checkpoints"})


@dataclass(frozen=True)
class LegacyLayout:
    """The stored names of a `lab.json` project (design 2.3), which kits before the new writer write
    and every kit reads. A core.Project whose marker is `lab.json` takes its paths from here
    (layout_of): this kit only reads such a project, and its migration copies `.lab/` to
    `.analytics/`."""

    marker: str = "lab.json"
    schema: str = "lab.project/1"
    state_dir: str = ".lab"
    workspace_dir: str = "report"
    imported_dir: str = "data/raw"
    visualizations_dir: str = "charts"
    # The top-level directories the kit itself manages: a path below one is named by this name
    # whatever case it is stored in and wherever it leads (core.Project._managed).
    managed_dirs: tuple[str, ...] = (".venv", ".lab", "runs", "report")
    # The directories that are never data inputs or outputs of a run (core.Project._walk_tree).
    ignored_dirs: frozenset[str] = frozenset({".venv", ".lab", "runs", "report"}) | PERSONS_IGNORED
    # The lock files in state_dir: the journal's (and the context history's), the runs', the
    # Workspace's.
    journal_lock: str = "lock"
    run_lock: str = "run.lock"
    workspace_lock: str = "report.lock"


@dataclass(frozen=True)
class Layout:
    """The stored names of an `analytics.json` project, the format the Writer writes (design 2.3 and
    4.4): the marker and its schema, the managed directory `.analytics/` (context versions, logs,
    caches, locks, migrations), the Workspace `workspace/`, imports under `data/imported/` and a
    run's Visualizations under `runs/<id>/visualizations/`. Its managed directories are its own,
    never a union with the older layout's (D21, S14): `report/` and `.lab/` are a person's folders
    here, and a run's outputs there are recorded as outputs."""

    marker: str = "analytics.json"
    schema: str = "analytics.project/2"
    state_dir: str = ".analytics"
    workspace_dir: str = "workspace"
    imported_dir: str = "data/imported"
    visualizations_dir: str = "visualizations"
    managed_dirs: tuple[str, ...] = (".venv", ".analytics", "runs", "workspace")
    ignored_dirs: frozenset[str] = frozenset({".venv", ".analytics", "runs", "workspace"}) | PERSONS_IGNORED
    journal_lock: str = "lock"
    run_lock: str = "run.lock"
    workspace_lock: str = "workspace.lock"
    # The lock a migration holds on its destination (design 6.12).
    migration_lock: str = "migration.lock"


LEGACY_LAYOUT = LegacyLayout()
LAYOUT = Layout()
# Where the journal is, in either layout.
JOURNAL = "journal.jsonl"

# --- markers --------------------------------------------------------------------------------------

MARKER = LAYOUT.marker
LEGACY_MARKER = LEGACY_LAYOUT.marker
PROJECT = LAYOUT.schema
LEGACY_PROJECT = LEGACY_LAYOUT.schema
# The marker formats this kit's entry points write to: the new format's, by its writer (Writer),
# since PR 3 of #13 ({LEGACY_PROJECT} before it). A lab.json project is read only (design 7).
WRITABLE = frozenset({PROJECT})
# ADR-0006 1: the fields an analytics.project/2 marker holds (the record schema's project_2).
PROJECT_FIELDS = ("schema", "project_id", "title", "created_at", "min_writer_version")
# The record families the Writer writes, by what they are (design 4.4): a write names the key, never
# the tag, so renaming a family is this table.
FAMILIES = {"project": PROJECT, "event": relations.EVENT_V2, "run": relations.RUN_V2,
            "dataset": relations.DATASET_V2, "trust": "analytics.trust/1",
            "migration": relations.MIGRATION_V1}

# What a directory is, by its markers (design 4.3):
NONE, LEGACY, CURRENT, UNKNOWN, INVALID, CONFLICT, INCOMPLETE = (
    "none", "legacy", "current", "unknown", "invalid", "conflict", "incomplete")
# What a migration leaves in its destination before the marker, its commit point (design 6.3): the
# destination's lock and the migrations' own directories, each with its ledger (progress.jsonl).
MIGRATION_DIR = f"{LAYOUT.state_dir}/migration"
MIGRATION_LOCK = f"{LAYOUT.state_dir}/{LAYOUT.migration_lock}"
LEDGER = "progress.jsonl"


@dataclass(frozen=True)
class MarkerState:
    """What `marker_state` found in one directory.

    `state` is one of NONE (no marker), LEGACY (lab.json: lab.project/1, no schema, or not readable
    as JSON), CURRENT (analytics.json: analytics.project/2 with its fields), UNKNOWN (a marker of
    another family or version), INVALID (an analytics.json that is not one, or a marker that could
    not be read: `reason`), CONFLICT (both markers) and INCOMPLETE (no marker, and a migration's
    record: a destination whose migration has not committed). `marker` is the file judged (None for
    NONE, CONFLICT and INCOMPLETE), `schema` its `schema` when that is a string, `value` its JSON
    object when it is one, and `details` what a refusal and the status overview say about it."""

    root: Path
    state: str
    marker: str | None = None
    schema: str | None = None
    value: dict | None = None
    reason: str | None = None
    details: dict = field(default_factory=dict)

    @property
    def format(self) -> str | None:
        """The format the project is read as: its marker's schema, `lab.project/1` for a lab.json
        without one (an older kit's), None for no marker, two, or one that is not valid."""
        if self.state in (LEGACY, CURRENT, UNKNOWN):
            return self.schema if self.schema is not None else LEGACY_PROJECT
        return None


def _look(path: Path) -> tuple[bool | None, OSError | None]:
    """Whether a marker is there: True; False for no entry (or a directory of that name, which no
    kit took for a marker); None, with the error, when the entry could not be looked at."""
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return False, None
    except OSError as exc:
        return None, exc
    return not stat.S_ISDIR(st.st_mode), None


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return True  # looked at again by the open, as an entry that changed meanwhile


def _read(path: Path) -> tuple[bool, bytes | None, str | None]:
    """(present, its bytes, why they could not be read). A marker that is there and cannot be read —
    Windows refuses to open a file another process is replacing (_platform.WindowsAtomicFiles) — is
    read again within the budget a replace retries in (design 4.3); still unreadable, its bytes are
    None and the operating system's error says why. One that disappears meanwhile is not there: "not
    there" is a FileNotFoundError, never a failure to read."""
    error: OSError | None = None
    for attempt in range(_platform.REPLACE_ATTEMPTS):
        if attempt:
            time.sleep(_platform.REPLACE_POLL_SECONDS)
        present, error = _look(path)
        if present is False:
            return False, None, None
        if present and not _regular(path):
            # A FIFO, a socket or a device under a marker's name is no record, and opening a FIFO
            # waits for a writer: not opened, and unreadable at once.
            return True, None, "it is not a regular file"
        if present:
            try:
                with open(path, "rb") as fh:
                    return True, fh.read(), None
            except FileNotFoundError:
                error = None  # replaced or removed meanwhile: look again
            except OSError as exc:
                error = exc
    if error is None:  # gone and back again for every look: what is there now
        present, error = _look(path)
        if present is False:
            return False, None, None
    return True, None, f"{type(error).__name__}: {error}" if error else "it kept changing"


def _parse(data: bytes) -> tuple[object, bool]:
    """(value, parsed): the JSON of a marker's bytes."""
    try:
        return json.loads(data), True
    except (ValueError, RecursionError):
        return None, False


def _summary(name: str, data: bytes | None, error: str | None) -> dict:
    """What a conflict says of one marker: its bytes' size and sha256, its schema and its
    `migration` field when it has them, or why it could not be read."""
    if data is None:
        return {"file": name, "unreadable": error}
    value, _ = _parse(data)
    out = {"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if isinstance(value, dict):
        out["schema"] = value.get("schema")
        if "migration" in value:
            out["migration"] = value["migration"]
    return out


def marker_state(root) -> MarkerState:
    """What the directory `root` is by its markers (design 4.3), read now: no cache, nothing
    written. The status overview, the readers and every write entry point judge by this."""
    root = Path(root)
    new = _read(root / MARKER)
    old = _read(root / LEGACY_MARKER)
    if new[0] and old[0]:
        return MarkerState(root, CONFLICT, details={"markers": [
            _summary(MARKER, new[1], new[2]), _summary(LEGACY_MARKER, old[1], old[2])]})
    if new[0]:
        return _judge_marker(root, new[1], new[2])
    if old[0]:
        return _judge_legacy(root, old[1], old[2])
    if migration_record(root):
        return MarkerState(root, INCOMPLETE, details=migration_progress(root))
    return MarkerState(root, NONE)


def _there(path: Path) -> bool:
    """Whether an entry is at `path`, a link being itself; one that cannot be looked at counts."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return False
    except OSError:
        return True
    return True


def migration_record(directory) -> bool:
    """Whether `directory` holds a migration's record — `.analytics/migration/` or its lock
    `.analytics/migration.lock` — which, without a marker, makes it a migration's destination that
    has not committed (design 4.3, 6.10): such a directory is found as a project and refused."""
    directory = Path(directory)
    return _there(directory / MIGRATION_DIR) or _there(directory / MIGRATION_LOCK)


# How much of one ledger a refusal reads to say how far its migration went.
PROGRESS_BYTES = 4 << 20


def migration_progress(root) -> dict:
    """What a refusal and the status overview say of a destination that has not committed: each
    migration directory under `.analytics/migration/` with the last step its ledger reached (lines
    that do not parse are a cut-off append and skipped, design 6.8), the source its `start` line
    names, and whether the destination's lock is there. Reads only; a ledger that cannot be read
    says why."""
    root = Path(root)
    out: dict = {"lock": _there(root / MIGRATION_LOCK), "migrations": []}
    try:
        names = sorted(entry.name for entry in os.scandir(root / MIGRATION_DIR))
    except OSError:
        names = []
    for name in names:
        item: dict = {"id": name, "step": None}
        try:
            with open(root / MIGRATION_DIR / name / LEDGER, "rb") as fh:
                data = fh.read(PROGRESS_BYTES)
        except OSError as exc:
            item["unreadable"] = f"{type(exc).__name__}: {exc}"
            out["migrations"].append(item)
            continue
        for raw in data.split(b"\n"):
            value, parsed = _parse(raw) if raw.strip() else (None, False)
            if not parsed or not isinstance(value, dict):
                continue
            if value.get("step") == "start" and isinstance(value.get("source"), dict):
                item["source"] = value["source"].get("path")
            if isinstance(value.get("step"), str):
                item["step"] = value["step"]
        out["migrations"].append(item)
    return out


def _invalid(root: Path, marker: str, reason: str, value=None, schema=None, **details) -> MarkerState:
    return MarkerState(root, INVALID, marker, schema, value if isinstance(value, dict) else None,
                       reason, {"marker": marker, "reason": reason, **details})


def _judge_marker(root: Path, data: bytes | None, error: str | None) -> MarkerState:
    """An analytics.json: CURRENT when it is analytics.project/2 with the fields ADR-0006 1 gives it
    and a min_writer_version in its normal form, UNKNOWN for another family or version, INVALID
    otherwise (design 4.3)."""
    if data is None:
        return _invalid(root, MARKER, "unreadable", error=error)
    value, parsed = _parse(data)
    if not parsed:
        return _invalid(root, MARKER, "unparsable")
    if not isinstance(value, dict):
        return _invalid(root, MARKER, "not_object")
    schema = value.get("schema") if isinstance(value.get("schema"), str) else None
    if schema is None:  # absent, or not a string: it names no format
        return _invalid(root, MARKER, "missing_field", value, fields=["schema"])
    if schema != PROJECT:
        return MarkerState(root, UNKNOWN, MARKER, schema, value)
    missing = [name for name in PROJECT_FIELDS if name not in value]
    if missing:
        return _invalid(root, MARKER, "missing_field", value, schema, fields=missing)
    if relations.version_key(value["min_writer_version"]) is None:
        return _invalid(root, MARKER, "bad_version", value, schema,
                        min_writer_version=value["min_writer_version"])
    return MarkerState(root, CURRENT, MARKER, schema, value)


def _judge_legacy(root: Path, data: bytes | None, error: str | None) -> MarkerState:
    """A lab.json: LEGACY when it is lab.project/1, has no schema or is not a JSON object — a broken
    lab.json was read as an empty one before #13 and writes went on, so it still is (design 4.3) —
    UNKNOWN for another schema, INVALID only when it cannot be read at all."""
    if data is None:
        return _invalid(root, LEGACY_MARKER, "unreadable", error=error)
    value, parsed = _parse(data)
    if not isinstance(value, dict):
        return MarkerState(root, LEGACY, LEGACY_MARKER,
                           reason="unparsable" if not parsed else "not_object")
    schema = value.get("schema") if isinstance(value.get("schema"), str) else None
    if schema is None:
        return MarkerState(root, LEGACY, LEGACY_MARKER, None, value, "no_schema")
    if schema != LEGACY_PROJECT:
        return MarkerState(root, UNKNOWN, LEGACY_MARKER, schema, value)
    return MarkerState(root, LEGACY, LEGACY_MARKER, schema, value)


def present_marker(directory) -> bool:
    """Whether `directory` holds a marker of either name (how a project is found, design 4.3): it
    is judged afterwards, by marker_state. An entry that could not be looked at counts: the judgment
    then says it cannot be read, rather than the search going on to a directory above it."""
    directory = Path(directory)
    return any(_look(directory / name)[0] is not False for name in (MARKER, LEGACY_MARKER))


def find_project(start: Path) -> Path | None:
    """The nearest directory at or above `start` (a real path) that holds a marker, analytics.json or
    lab.json, whatever it holds, or a migration's record without one (migration_record): the first
    one up from `start` decides, so a directory with an analytics.json below a lab.json project is
    that directory, never the project above it, and so is a migration's destination that has not
    committed (Issue #13 PR 2)."""
    for candidate in (start, *start.parents):
        if present_marker(candidate) or migration_record(candidate):
            return candidate
    return None


def _deepest_existing(path: str) -> str:
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


def project_above(path) -> str | None:
    """The nearest directory above `path` that holds a marker of either name, or a migration's record
    without one (migration_record: a destination that has not committed, which find_project finds as
    a project), or None (design 6.2 and OQ9, widened by the owner's decision of 2026-10-09): a new
    project — an init's, a migration's destination — is never made inside another one, where an
    older kit run in it would write to the project above it, nor inside a migration's destination,
    which it would leave holding what its ledger does not account for and, once committed, holding a
    project. `path` is absolute and need not exist: its deepest existing directory, when that is not
    `path` itself, counts as above it, and so does every directory above that."""
    path = os.path.abspath(os.fspath(path))
    existing = _deepest_existing(path)
    for place in ([existing] if existing != path else []) + _ancestors(existing):
        if present_marker(place) or migration_record(place):
            return place
    return None


def inside(above: str) -> str:
    """How a refusal names what project_above found: a project, or a migration's destination that has
    not committed (no marker, a migration's record)."""
    if present_marker(above):
        return f"the project {above}"
    return f"{above}, the destination of a migration that has not committed"


def layout_of(state: MarkerState) -> Layout | LegacyLayout | None:
    """The stored names a directory's records follow, by its marker judgment (design 2.3): the
    older layout where the marker judged is lab.json (legacy, or unknown or invalid as that file),
    the new format's otherwise (analytics.json, a migration's destination, a directory with no
    marker yet, which an init makes in the new format). Two markers have none: nothing is read or
    written there until one of them goes."""
    if state.state == CONFLICT:
        return None
    return LEGACY_LAYOUT if state.marker == LEGACY_MARKER else LAYOUT


# --- the gate -------------------------------------------------------------------------------------

READ_HINT = "this kit still reads it: status, context, query and the Workspace pages already built"


def refusal(state: MarkerState, op: str, *, writer_version: str,
            writable: frozenset = WRITABLE) -> AnalyticsError | None:
    """Why the write `op` may not go to the directory `state` describes, or None when it may."""
    root, where = state.root, f"{state.root}/{state.marker}"
    if state.state == NONE:
        return AnalyticsError("no_project", f"no analytics project at {root} (no analytics.json or "
                              "lab.json)", hint="create one with: analytics init DIR")
    if state.state == LEGACY and LEGACY_PROJECT in writable:
        return None
    if state.state == LEGACY and PROJECT in writable:
        return migration_required(state, op)
    if state.state == CURRENT and PROJECT in writable:
        needed = state.value["min_writer_version"]
        have = relations.version_key(writer_version)
        if have is not None and relations.version_key(needed) <= have:
            return None
        return AnalyticsError("writer_too_old",
                              f"{where} asks for a kit of version {needed} or later to write to it; "
                              f"this kit is {writer_version} ({op} refused)",
                              hint=f"update the analytics kit to {needed} or later to write to it; "
                                   f"{READ_HINT}",
                              details={"min_writer_version": needed, "kit_version": writer_version})
    if state.state in (LEGACY, CURRENT):
        # A format this kit reads and does not write: analytics.project/2 until the new writer.
        return AnalyticsError("project_too_new",
                              f"{where} is {state.format}, a format this kit ({writer_version}) reads "
                              f"and does not write ({op} refused)",
                              hint=f"a newer version of the analytics kit writes this project: update "
                                   f"the kit to write to it; {READ_HINT}",
                              details={"marker": state.marker, "format": state.format})
    if state.state == UNKNOWN:
        return AnalyticsError("project_too_new",
                              f"{where} is {state.schema}, a project format this kit "
                              f"({writer_version}) does not know ({op} refused)",
                              hint="a newer version of the analytics kit made this project; update the "
                                   "kit instead of writing with this one (the records this kit knows are "
                                   "read, and the marker is reported)",
                              details={"marker": state.marker, "format": state.schema})
    if state.state == INVALID:
        return AnalyticsError("marker_invalid", f"{where} is not a valid project marker: "
                              f"{_reason_text(state)} ({op} refused)",
                              hint=_invalid_hint(state), details=dict(state.details))
    if state.state == INCOMPLETE:
        return AnalyticsError("migration_incomplete",
                              f"{root} is the destination of a migration that has not finished: it "
                              f"has no {MARKER} yet ({op} refused)",
                              hint="a migration is copying a lab.json project here, and until it "
                                   f"commits (writing {MARKER} last) no kit writes or reads here. "
                                   "Running the same migration again resumes it; the details say "
                                   "which migration it is and how far it went",
                              details=dict(state.details))
    return AnalyticsError("marker_conflict",
                          f"{root} holds two project markers, {MARKER} and {LEGACY_MARKER} "
                          f"({op} refused)",
                          hint="one of them does not belong to this directory: check both (their "
                               "sizes, sha256 and schema are in details), then move the other one "
                               "away. The kit does not choose between them, and reads neither",
                          details=dict(state.details))


# How a person copies a lab.json project into the current format (design 6.1 and 7): the command,
# its preview, and in aelix where to run it.
MIGRATE_COMMAND = "analytics migrate --to DEST"


def migration_hint(root) -> str:
    """What to do with a lab.json project this kit reads and does not write (design 7): the hint of
    migration_required, and the status overview's `migration_hint`."""
    return (f"Copy it into the current format with `analytics migrate --to <new empty folder>` run "
            f"inside {root} (preview with --dry-run). The original stays as it is. In aelix, a person "
            "runs that in a terminal, then `/analytics use <new folder>`.")


def migration_required(state: MarkerState, op: str) -> AnalyticsError:
    """A write to a lab.json project (design 7, Issue #13 PR 3): this kit reads it and writes
    nothing there, and a person copies it into the current format to write again. The message and
    the hint are the design's text, the same for a person and a model."""
    root = state.root
    return AnalyticsError("migration_required",
                          f"{root} uses the older record format ({LEGACY_MARKER}, {LEGACY_PROJECT}). "
                          "This kit reads it and does not write to it.",
                          hint=migration_hint(root),
                          details={"marker": state.marker, "format": state.format, "operation": op,
                                   "command": MIGRATE_COMMAND})


def _reason_text(state: MarkerState) -> str:
    details = state.details
    return {"unreadable": f"it cannot be read: {details.get('error')}",
            "unparsable": "it is not JSON",
            "not_object": "it is not a JSON object",
            "missing_field": "it lacks " + ", ".join(details.get("fields") or ()),
            "bad_version": f"min_writer_version {details.get('min_writer_version')!r} is not a "
                           "version in its normal form"}.get(state.reason, str(state.reason))


# What an analytics.json that names no format at all may be: a file of the person's own that has a
# marker's name, rather than a broken marker.
_NO_FORMAT = ("unparsable", "not_object")


def _invalid_hint(state: MarkerState) -> str:
    if state.reason == "unreadable":
        return ("make the marker readable (another program may hold it open, or its permissions "
                "forbid reading it), then try again")
    if state.reason in _NO_FORMAT or (state.reason == "missing_field"
                                      and "schema" in (state.details.get("fields") or ())):
        return (f"this {MARKER} names no project format: if it is a file of your own, rename it or "
                "name the project's root (AELIX_ANALYTICS_PROJECT=ROOT for the analytics command, the "
                "SDK and aelix; /analytics use ROOT or --analytics-project ROOT in aelix; --project "
                f"ROOT for python -m aelix_analytics), as the nearest directory with {MARKER} or "
                f"{LEGACY_MARKER} is the project; if it is the marker, restore it. This kit writes "
                "nothing to the directory while it is there")
    return (f"{MARKER} names a project of the analytics.project/2 format: a JSON object with "
            f"{', '.join(PROJECT_FIELDS)}. Restore it from where it came from; this kit writes "
            "nothing to the directory until it is valid")


def admit(root, op: str, *, side: bool = False, writer_version: str,
          writable: frozenset = WRITABLE) -> MarkerState | None:
    """Judge the directory `root` now (marker_state) for the write `op`, before the write's first
    side effect: the state when this kit writes there; otherwise an AnalyticsError, or None for a
    side write of a read (`side=True`), which the caller then skips (design 4.3 and 4.10).
    `writer_version` is the kit's KIT_VERSION (core passes it; this module does not import core)."""
    state = marker_state(root)
    problem = refusal(state, op, writer_version=writer_version, writable=writable)
    if problem is None:
        return state
    if side:
        return None
    raise problem


def readable(root, op: str) -> MarkerState:
    """Judge the directory `root` for the read `op`: every marker state but NONE (no_project),
    CONFLICT (marker_conflict: which format to read it as cannot be told, and the kit does not
    choose) and INCOMPLETE (migration_incomplete: a copy not finished, whose details say how far its
    migration went). A read of a marker this kit does not write, does not know, or cannot read goes
    on with the records it knows (the status overview says which marker it is)."""
    state = marker_state(root)
    if state.state in (NONE, CONFLICT, INCOMPLETE):
        raise refusal(state, op, writer_version="", writable=frozenset())
    return state


# --- who writes ------------------------------------------------------------------------------------

# How a record came to be written (design 2.5): the human command line (`analytics`, bin/analytics,
# `python -m aelix_analytics`), the script API, an aelix model tool, the `/analytics` command, and a
# write aelix made on its own (a hook).
MEANS = ("cli", "sdk", "tool", "command", "hook")
# Whether the entry point or the person said who writes (`declared`), or the kit filled in its
# default (`inferred`, OQ1).
ACTOR_BASES = ("declared", "inferred")


@dataclass(frozen=True)
class Origin:
    """Who writes or asks, and how (design 4.1, ARCHITECTURE section 4): an entry point makes one
    and hands it to every write, so nothing below it picks an author. A `lab.event/1` record holds
    `actor` and `session`; the new writer (PR 3) records the rest. `request_id` is the caller's
    (ADR-0006 8), None when it gave none."""

    actor: str
    means: str
    actor_basis: str = "declared"
    session: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.actor, str) or not self.actor:
            raise ValueError(f"an origin names its actor: {self.actor!r}")
        if self.means not in MEANS:
            raise ValueError(f"means is one of {', '.join(MEANS)}: {self.means!r}")
        if self.actor_basis not in ACTOR_BASES:
            raise ValueError(f"actor_basis is one of {', '.join(ACTOR_BASES)}: {self.actor_basis!r}")
        if self.session is not None and not isinstance(self.session, str):
            raise ValueError(f"a session is a string: {self.session!r}")


# --- the JSON helpers --------------------------------------------------------------------------------

def write_json(path, obj, *, platform: _platform.Platform, through_link: bool = False) -> None:
    """Write `obj` as JSON by an atomic replace, with a line end after it. A record that is a link is
    replaced by a file, as the link itself (docs/CONTRACTS.md G); with `through_link` (the trust
    list, ADR-0006 9) a link whose target exists is followed and the target replaced in its own
    directory."""
    path = Path(path)
    if through_link and os.path.islink(path) and os.path.exists(path):
        path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with _platform.open_text(tmp) as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")
    platform.files.replace(tmp, path)


def append_jsonl(path, obj) -> None:
    """Append `obj` as one JSON line (a log the kit keeps: telemetry, queries)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _platform.open_text(path, "a") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


# --- free-form references ----------------------------------------------------------------------------

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z0-9][a-z0-9-]{0,60}$")
EVENT_ID_RE = re.compile(r"^e\d+$")
DATASET_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def classify_refs(items) -> dict:
    """Sort free-form references into runs, events, charts (script::chart), datasets and the rest."""
    out: dict[str, list[str]] = {"runs": [], "events": [], "charts": [], "data": [], "other": []}
    for raw in items or []:
        s = str(raw).strip()
        if not s:
            continue
        if RUN_ID_RE.match(s):
            out["runs"].append(s)
        elif EVENT_ID_RE.match(s):
            out["events"].append(s)
        elif "::" in s:
            out["charts"].append(s)
        elif s.endswith(".parquet") or DATASET_NAME_RE.match(s):
            out["data"].append(s)
        else:
            out["other"].append(s)
    return {k: v for k, v in out.items() if v}


# --- the new format's records (Issue #13 PR 3, design 2.5, 2.8, 2.9, 4.4–4.9) -------------------------

# The kinds a person or an agent records (design 2.8): `gap` is `workflow_feedback` in this format,
# and the other kinds of analytics.event/2 open with the Issues of their features (#14 on).
RECORD_KINDS = ("finding", "decision", "assumption", "question", "todo", "note", "workflow_feedback",
                *relations.METRIC_KINDS, *relations.ASSET_PUBLIC_KINDS, "checkpoint", "resolve")
# The kinds the kit records on its own (design 4.8): a project's creation and the completion events of
# an import, a run and a context version.
KIT_KINDS = ("project", "import", "run", "context")
# An input name that is another kind now, with no alias (TERMINOLOGY: an old name is an error that
# names the new one).
RENAMED_KINDS = {"gap": "workflow_feedback", "reuse_proposal": "asset_candidate",
                 "reuse_notification": "asset_notification", "reuse_mode": "asset_mode"}
# OQ2: a record keeps its whole text up to this many bytes of UTF-8, in a journal line (its LF
# included) shorter than LINE_LIMIT bytes — the aelix read tool reads no longer line (CONTRACTS A).
TEXT_LIMIT = 32 * 1024
LINE_LIMIT = 50 * 1024
# ADR-0006 8 with ADR-0003 decision 6: a request_id is a name every platform can use as a file name
# (the record schema's request_id, which a test keeps equal).
REQUEST_ID_RE = re.compile(r"^(?!([Cc][Oo][Nn]|[Pp][Rr][Nn]|[Aa][Uu][Xx]|[Nn][Uu][Ll]|[Cc][Oo][Mm][1-9]"
                           r"|[Ll][Pp][Tt][1-9])(\.|$))[A-Za-z0-9]([A-Za-z0-9._-]{0,126}[A-Za-z0-9_-])?$")
# The record schema's event_id and run_id, ASCII digits only.
EVENT_ID_V2_RE = re.compile(r"e[1-9][0-9]*")
RUN_ID_V2_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[a-z0-9][a-z0-9-]{0,60}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
# ADR-0006 3 and the record schema's reference: what each target type holds when it is bound (author
# or inferred), the types that are never left unbound, and the fields an unbound one names.
BOUND_TARGET = relations.BOUND_TARGET
ALWAYS_BOUND = relations.ALWAYS_BOUND
UNBOUND_KEYS = relations.UNBOUND_KEYS
# A reference's fields in the order the Writer writes them: what it is, its target, then how it is
# related and bound (the record examples' order).
REFERENCE_HEAD = ("type", "run_id", "event_id", "visualization_id", "role", "path", "version", "key",
                  "metric_id", "proposal_id", "asset_id", "report_id", "revision_id", "publication_id", "token",
                  "digest")
REFERENCE_TAIL = ("relation", "basis", "version_binding")
# The relations through which a new record changes another's state (ADR-0006 6).
STATE_RELATIONS = ("resolves", "supersedes")


def new_project_id() -> str:
    """A project's random id (ADR-0006 1, the schema's opaque_id): `prj_` and 16 hex digits. An
    init and a migration each make a new one."""
    return "prj_" + secrets.token_hex(8)


def new_request_id() -> str:
    """The request_id the kit gives a write whose caller gave none (ADR-0006 8): in the response
    only, never in the record."""
    return "req-" + secrets.token_hex(8)


def append_line(path, line: bytes, *, follow: bool = True) -> None:
    """Append one line to a file the kit keeps by appending — a journal, a migration's ledger (design
    4.4, ADR-0006 6, #49): an LF first when the file's last byte is not one (a line an interrupted
    write cut off, or a record left without its line end), then the line and its LF in one write.
    `line` is the line's bytes without an LF. The bytes already there are never rewritten. With
    `follow=False` a link at `path` is not followed (O_NOFOLLOW, where the platform has it)."""
    if b"\n" in line:
        raise ValueError("a line holds no LF")
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    if not follow:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o666)
    try:
        data = line + b"\n"
        if os.lseek(fd, 0, os.SEEK_END):
            os.lseek(fd, -1, os.SEEK_END)
            if os.read(fd, 1) != b"\n":
                data = b"\n" + data
        view = memoryview(data)
        while view:  # one write; a regular file takes it whole, and a short write goes on from there
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)


def bad_kind(kind, kinds=RECORD_KINDS) -> AnalyticsError:
    """The error of a kind that is not one of `kinds`: an older name says the new one (design 2.8)."""
    renamed = RENAMED_KINDS.get(kind) if isinstance(kind, str) else None
    if renamed:
        return AnalyticsError("bad_kind", f"the record kind {kind!r} is called {renamed!r} now",
                              hint=f"record it as {renamed}; kinds: {', '.join(kinds)}",
                              details={"kind": kind, "use": renamed, "kinds": list(kinds)})
    return AnalyticsError("bad_kind", f"unknown record kind {kind!r}",
                          hint="use one of: " + ", ".join(kinds),
                          details={"kind": kind, "kinds": list(kinds)})


def check_kind(kind, kinds=RECORD_KINDS) -> None:
    if kind not in kinds:
        raise bad_kind(kind, kinds)


def _text(text) -> tuple[str, int]:
    """(the record's text, its UTF-8 bytes): stripped of the white space around it, as records have
    always been; empty is refused. A text that has no UTF-8 form (a lone surrogate) raises before
    anything is written."""
    text = (text or "").strip() if isinstance(text, str) or text is None else str(text).strip()
    if not text:
        raise AnalyticsError("empty_note", "the record's text is empty",
                             hint="give the finding, decision, question or note as a sentence")
    return text, len(text.encode("utf-8"))


def _too_long(text_bytes: int, line: bytes | None) -> AnalyticsError | None:
    """OQ2: text_too_long when the text is over TEXT_LIMIT bytes or its line (LF included) is not
    under LINE_LIMIT; the line is the bytes the writer would write, escapes and references included.
    Without a line (check_text) only the text is judged."""
    line_bytes = None if line is None else len(line) + 1
    if text_bytes <= TEXT_LIMIT and (line_bytes is None or line_bytes < LINE_LIMIT):
        return None
    said = f"the text is {text_bytes:,} bytes of UTF-8"
    if line_bytes is not None:
        said += f" and its journal line {line_bytes:,} bytes"
    return AnalyticsError(
        "text_too_long",
        f"{said}: a record keeps a text of up to {TEXT_LIMIT:,} bytes in a line under "
        f"{LINE_LIMIT:,} bytes (nothing was written, nothing was cut)",
        hint="save a long text as a file (a script's output, say) or a Report and record a shorter "
             "text that refers to it",
        details={"text_bytes": text_bytes, **({"line_bytes": line_bytes} if line_bytes is not None else {}),
                 "text_limit": TEXT_LIMIT, "line_limit": LINE_LIMIT})


def check_text(text) -> str:
    """The checks of a record's text that need no journal line — empty (empty_note) and over
    TEXT_LIMIT (text_too_long). The line's own limit needs the record: Writer.check judges it before
    anything is written (a record made now and written later, the SDK's in a run, with the widest
    seq, SEQ_BOUND)."""
    text, text_bytes = _text(text)
    problem = _too_long(text_bytes, None)
    if problem:
        raise problem
    return text


# A seq no journal reaches in practice (twelve digits): the line of a record made now and written
# later — a note the SDK makes in a run, written when the run finishes — is judged with it, so the
# seq it gets then cannot make it longer than judged (Writer.check).
SEQ_BOUND = 10 ** 12 - 1
# What a text the kit composes quotes of a field it keeps whole elsewhere — a run's title (its
# run.json), an import's description (its import record), a project's title (its marker) — at most:
# the characters a kit before 0.8.0 kept of every text (MAX_EVENT_TEXT). A text the kit composes so
# can never be refused for its length after what it completes was written (OQ2 concerns the text a
# caller records, which is never cut).
EXCERPT_CHARS = 4000


def excerpt(value, chars: int = EXCERPT_CHARS) -> str:
    """`value` whole when it has at most `chars` characters, else its first `chars` and `…`: what a
    text the kit composes quotes of a field kept whole elsewhere (EXCERPT_CHARS)."""
    value = str(value)
    return value if len(value) <= chars else value[:chars] + "…"


def check_request_id(request_id) -> None:
    """ADR-0006 8: a request_id a caller gives is a portable name (ADR-0003 decision 6)."""
    if not (isinstance(request_id, str) and REQUEST_ID_RE.fullmatch(request_id)):
        raise AnalyticsError("name_not_portable",
                             f"request_id {request_id!r} is not a name every platform can use",
                             hint="a request_id is 1 to 128 letters, digits, '.', '_' or '-', starts "
                                  "with a letter or a digit, does not end with '.', and is not a "
                                  "reserved device name (con, nul, com1, …)",
                             details={"request_id": request_id})


def reference_problem(ref) -> str | None:
    """The shared pure record-reference contract, also used by Report manifest readers."""
    return relations.reference_problem(ref)


def _ordered(ref: dict) -> dict:
    """A reference in the order the Writer writes it (REFERENCE_HEAD, then fields it does not know,
    then REFERENCE_TAIL)."""
    head = [name for name in REFERENCE_HEAD if name in ref]
    rest = [name for name in ref if name not in REFERENCE_HEAD and name not in REFERENCE_TAIL]
    return {name: ref[name] for name in (*head, *rest, *REFERENCE_TAIL) if name in ref}


def typed_reference(ref: dict) -> dict:
    """A reference the kit gives whole (a completion event's, a correction's, a resolved question's):
    `relation` cites, `basis` declared and `version_binding` author unless it says otherwise (design
    4.7), checked against the record schema. A reference that resolves an event that cannot be one
    is unknown_event, as any id no record has."""
    if not isinstance(ref, dict):
        raise ValueError(f"a reference is an object: {ref!r}")
    out = {"relation": "cites", "basis": "declared", "version_binding": "author", **ref}
    if out.get("type") == "event" and out["relation"] == "resolves" and not (
            isinstance(out.get("event_id"), str) and EVENT_ID_V2_RE.fullmatch(out["event_id"])):
        raise _unknown_event(out.get("event_id"))
    problem = reference_problem(out)
    if problem:
        raise ValueError(f"not a reference of the record schema: {problem}: {ref!r}")
    return _ordered(out)


def _unknown_event(event_id) -> AnalyticsError:
    return AnalyticsError("unknown_event", f"no journal event {event_id!r}",
                          hint="analytics_status lists open questions and todos with their ids")


def _now() -> datetime:
    return datetime.now().astimezone()


def _file_sha(path) -> str | None:
    """The sha256 of a file's bytes now, or None when there is none to read."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _encode(event: dict) -> bytes:
    """A journal line's bytes without its LF: one JSON object of JSON's own types, UTF-8 (S12)."""
    return json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class Written:
    """What a write gives back (design 4.5 and 4.9): the record as written, the request_id (the
    caller's, or the one the kit made: then in this answer only), what each name given as a
    reference was bound to (`bindings`: the input, the reference written, its binding and, when it
    stayed unbound with candidates, those) and, for each reference that changes another record's
    state (`resolves`, `supersedes`, a record_update's `updates`), that record's state before and
    after under the lock (`targets`), or None when there is none."""

    event: dict
    request_id: str
    bindings: list = field(default_factory=list)
    targets: list | None = None

    def to_json(self) -> dict:
        out = {"event": self.event, "request_id": self.request_id, "bindings": self.bindings}
        if self.targets is not None:
            out["targets"] = self.targets
        return out


@dataclass(frozen=True)
class _Prepared:
    """A record judged outside the lock (Writer._prepare): its text, the request_id, the references
    given, those given whole, the journal read then (None when the line was judged with a given seq)
    and the names bound."""

    text: str
    request_id: str
    items: list
    typed: list
    journal: relations.Journal | None
    bound: dict
    details: dict | None
    requires: str | None


class Locked:
    """The journal under its lock, with the marker judged again under it (Writer.locked, design 4.5):
    the marker state, the journal's bytes and their reading (`journal`, `replay`) as they are while
    the lock is held, and the appends made under it. A Report save (#19) checks and completes inside
    one of these, as the Writer's record and update do."""

    def __init__(self, writer: Writer, state: MarkerState, data: bytes, *, operation="write"):
        self.writer = writer
        self.state = state
        self.data = data
        self.operation = operation
        self._journal: relations.Journal | None = None

    @property
    def journal(self) -> relations.Journal:
        if self._journal is None:
            self._journal = relations.Journal(self.data)
        return self._journal

    @property
    def replay(self) -> relations.Replay:
        return self.journal.replay()

    @property
    def next_seq(self) -> int:
        """relations.Journal.next_seq, skipping a number whose id a readable line holds (#49)."""
        journal = self.journal
        seq = journal.next_seq
        while f"e{seq}" in journal.lines_of:
            seq += 1
        return seq

    def line(self, kind: str, text: str, refs, details, *, recorded_at=None) -> tuple[dict, bytes]:
        event = self.writer._event(self.next_seq, kind, text, refs, details, recorded_at=recorded_at)
        return event, _encode(event)

    def append(self, kind: str, text: str, *, refs=(), details=None, requires: str | None = None, recorded_at=None) -> dict:
        """Write one analytics.event/2 record with the next seq: refused as text_too_long (nothing
        written) when its text or line is over the limits (OQ2). With `requires`, the marker's
        min_writer_version is raised to it (require) once the line is known to fit, right before
        the line is written (design 4.5 steps 5 and 6). A kind the format does not have, or a
        reference the record schema does not take, is the caller's mistake: ValueError."""
        if kind not in relations.KINDS[relations.EVENT_V2]:
            raise ValueError(f"{kind!r} is no kind of {relations.EVENT_V2}")
        refs = list(refs)
        if recorded_at is not None and not (self.operation == "publish_asset_suggestion" and
                                           kind == "asset_notification" and isinstance(details, dict) and
                                           details.get("trigger") == "automatic" and isinstance(recorded_at, str)):
            raise ValueError("only automatic publication pins its checked record time")
        for ref in refs:
            problem = reference_problem(ref)
            if problem:
                raise ValueError(f"not a reference of the record schema: {problem}: {ref!r}")
        if kind == "checkpoint":
            details, generated, model = self.writer._checkpoint(details, refs, self.journal)
            if details["as_of_seq"] >= self.next_seq:
                raise AnalyticsError("checkpoint_reference", "checkpoint basis must precede its event")
            refs = _refs(_items([*refs, *generated]), {})
            refs = list({json.dumps(ref, sort_keys=True): ref for ref in refs}.values())
            requires = checkpoints.SINCE
        if kind in relations.ASSET_KINDS:
            self.writer._asset_written(kind, details, refs, self.journal, operation=self.operation)
            feature = (relations.ASSET_AUTOMATIC_SINCE if kind == "asset_mode" or
                       (kind == "asset_notification" and details.get("trigger") == "automatic")
                       else relations.ASSET_WRITER_SINCE)
            if ((kind == "asset_mode" and details.get("scope") == "session") or
                    (kind == "asset_notification" and details.get("trigger") == "automatic" and
                     details.get("surface") in ("aelix_context", "aelix_command_status"))):
                feature = relations.ASSET_HOST_SINCE
            requires = max((feature, requires or feature), key=relations.version_key)
        elif kind == "record_update" and relations.asset_update_targets({"details": details, "refs": refs}, self.journal):
            self.writer._asset_update(details, refs, self.journal, registration=self.operation == "register_asset",
                                      bookkeeping=self.operation == "update an asset")
            requires = max((relations.ASSET_WRITER_SINCE, requires or relations.ASSET_WRITER_SINCE),
                           key=relations.version_key)
        text, text_bytes = _text(text)
        event, line = self.line(kind, text, refs, details, recorded_at=recorded_at)
        if kind == "publication":
            if self.operation != "build_publication":
                raise ValueError("publication completion is written by the build application")
            problem = relations.publication_event_problem(event)
            if problem:
                raise ValueError("not a publication completion: " + problem)
            requires = max((requires or relations.PUBLICATION_WRITER_SINCE,
                            relations.PUBLICATION_WRITER_SINCE), key=relations.version_key)
        problem = _too_long(text_bytes, line)
        if problem:
            raise problem
        if requires is not None:
            self.require(requires)
        append_line(self.writer.journal, line)
        cut = bool(self.data) and not self.data.endswith(b"\n")
        self.data = self.data + (b"\n" if cut else b"") + line + b"\n"
        self._journal = None
        return event

    def require(self, version: str) -> bool:
        """Raise the marker's min_writer_version to `version` when it is lower (ADR-0006 1.3, design
        4.8): only min_writer_version and kit_version (this writer's version) change; every other
        field keeps its value and place. Returns whether the marker was written."""
        writer = self.writer
        have, wanted = self.state.value.get("min_writer_version"), relations.version_key(version)
        if wanted is None or wanted > relations.version_key(writer.writer_version):
            raise ValueError(f"a writer of {writer.writer_version} requires at most its own version: "
                             f"{version!r}")
        if wanted <= relations.version_key(have):
            return False
        value = dict(self.state.value)
        value["min_writer_version"] = version
        value["kit_version"] = writer.writer_version
        write_json(writer.marker, value, platform=writer.platform)
        self.state = marker_state(writer.root)
        return True


class Writer:
    """The common writer of the new format (Issue #13 PR 3, design 4.5): the one place that writes an
    analytics.json project's journal lines (analytics.event/2), run records (analytics.run/2) and
    import records (analytics.dataset/1), by the Origin its entry point hands it — the writer has no
    author of its own and reads no environment variable (design 4.1).

    - `record` judges the directory (admit) before anything else, checks the kind, the text (OQ2),
      the references and the request_id, binds the names given as references (design 4.7) outside
      the lock, then under the journal lock judges the marker again, binds again when the journal
      grew meanwhile, raises the marker's min_writer_version for `requires`, and appends.
    - `update` writes a record_update only when the target's state, replayed under the lock as the
      reader replays it, is the one expected and the transition is allowed; otherwise
      state_conflict, and nothing is written (design 4.6).
    - `write_run` and `write_dataset` write a run's and an import's record whole; their completion
      event is a `record` that comes last (design 4.8).
    - `locked` is the journal lock with the marker judged again under it and the journal read there.

    `legacy_path` reads the paths a kit before 0.5.0 recorded (core gives Project.legacy_identity, or
    a migrated project's mapping, design 5.4); `clock` gives the time a record is written."""

    # The marker formats the Writer writes to: the format of its Layout (the kit's WRITABLE).
    WRITES = frozenset({PROJECT})

    def __init__(self, root, *, platform: _platform.Platform, origin: Origin, writer_version: str,
                 legacy_path=None, clock=None):
        if not isinstance(origin, Origin):
            raise TypeError(f"a writer is given an Origin: {origin!r}")
        if relations.version_key(writer_version) is None:
            raise ValueError(f"a writer's version is a PEP 440 version in its normal form: {writer_version!r}")
        self.root = Path(root)
        self.platform = platform
        self.origin = origin
        self.writer_version = writer_version
        self.legacy_path = legacy_path
        self.clock = clock or _now
        self.layout = LAYOUT
        self.journal = self.root / JOURNAL
        self.marker = self.root / LAYOUT.marker

    # --- the gate --------------------------------------------------------------------------------

    def admit(self, op: str) -> MarkerState:
        """writer.admit for this writer: a refusal raises (the Writer makes no side write)."""
        return admit(self.root, op, writer_version=self.writer_version, writable=self.WRITES)

    def _format(self, op: str) -> MarkerState:
        """The directory is an analytics.project/2 project, whatever its min_writer_version: a run or
        an import admitted at its start finishes its record (a newer writer raising the version meanwhile
        refuses its completion event instead, design 4.3)."""
        state = marker_state(self.root)
        if state.state == CURRENT:
            return state
        raise refusal(state, op, writer_version=self.writer_version, writable=self.WRITES)

    @contextlib.contextmanager
    def locked(self, op: str = "write"):
        """The journal lock (`.analytics/lock`), taken after the directory is admitted (the lock file
        is the write's first side effect) and with the marker judged again once it is held (design
        4.3, S8): yields a Locked, or raises the refusal with nothing written."""
        self.admit(op)
        with self.platform.locks.exclusive(self.root / LAYOUT.state_dir / LAYOUT.journal_lock):
            state = self.admit(op)
            yield Locked(self, state, _journal_bytes(self.journal), operation=op)

    # --- records ---------------------------------------------------------------------------------

    def _request_id(self) -> str:
        """The caller's request_id, checked (step 2 of design 4.5, after the gate), or a new one."""
        if self.origin.request_id is not None:
            check_request_id(self.origin.request_id)
            return self.origin.request_id
        return new_request_id()

    def _event(self, seq: int, kind: str, text: str, refs, details, *, recorded_at=None) -> dict:
        """An analytics.event/2 record in the order design 2.5 gives: schema, seq, id, recorded_at,
        kind, actor, actor_basis, means, session (when there is one), request_id (the caller's
        only), text, refs, details (when given)."""
        origin = self.origin
        event = {"schema": FAMILIES["event"], "seq": seq, "id": f"e{seq}",
                 "recorded_at": (recorded_at if recorded_at is not None else
                                 self.clock().isoformat(timespec="milliseconds")), "kind": kind,
                 "actor": origin.actor, "actor_basis": origin.actor_basis, "means": origin.means}
        if origin.session:
            event["session"] = origin.session
        if origin.request_id:
            event["request_id"] = origin.request_id
        event["text"] = text
        event["refs"] = list(refs)
        if details is not None:
            event["details"] = details
        return event

    def record(self, kind: str, text: str, *, refs=(), details=None, requires: str | None = None) -> Written:
        """Append one record of `kind` (RECORD_KINDS, or the kit's own KIT_KINDS) by this writer's
        Origin. `refs` holds names as a person or an agent gives them (run ids, event ids, CONTEXT
        versions, `script.py::id`, Visualization ids, dataset names and paths), bound as design 4.7
        says, and references the kit gives whole (typed_reference). `requires` raises the marker's
        min_writer_version to that version under the lock before the record is written (design 4.8).
        Steps 1–7 of design 4.5; a refusal at any step writes nothing."""
        op = "record"
        refs = [refs] if isinstance(refs, (str, dict)) else list(refs or ())
        prepared = self._prepare(op, kind, text, refs, details, requires)
        text, items, typed, outside, bound = (prepared.text, prepared.items, prepared.typed,
                                              prepared.journal, prepared.bound)
        with self.locked(op) as held:
            if kind == "checkpoint":
                details, generated, model = self._checkpoint(prepared.details, refs, held.journal)
                items = _items([*refs, *generated])
                bound = self._bind(items, model=model)
            elif kind in relations.METRIC_KINDS:
                # The caller's original fields, not the generated state-changing references, are
                # checked again. Concurrent definition/focus changes cannot be overwritten.
                details, generated = self._metric(kind, details, refs, held.journal)
                items = _items([*list(refs or ()), *generated])
                bound = self._bind(items)
            elif kind in relations.ASSET_PUBLIC_KINDS:
                details, generated = self._asset(kind, details, refs, held.journal)
                items = _items([*refs, *generated])
                bound = self._bind(items)
            if kind != "checkpoint" and held.journal.as_of_seq != outside.as_of_seq:
                bound = self._bind(items)  # a run completed meanwhile, say
            _check_resolved(held.journal, typed)
            refs_written = _refs(items, bound)
            before = held.journal
            event = held.append(kind, text, refs=refs_written,
                                details=details if kind in (*relations.METRIC_KINDS, *relations.ASSET_PUBLIC_KINDS, "checkpoint") else prepared.details,
                                requires=prepared.requires)
            # The journal after the append is read again only for a record that changes another's
            # state (design 4.6): most records change none.
            targets = (_targets(before, held.journal, refs_written)
                       if any(_changes_state(ref) for ref in refs_written) else None)
        bindings = [entry for _, entry in bound.values()]
        return Written(event, prepared.request_id, bindings, targets)

    def check(self, kind: str, text: str, *, refs=(), details=None, ahead: int = 0,
              seq: int | None = None, pending_run: str | None = None) -> None:
        """What `record` refuses before its lock, judged and nothing written: the directory, the
        kind, the text and its line (OQ2), the references, the request_id, a closed record that must
        be there (unknown_event), a `resolve` without one (resolve_needs_target). For an entry point
        that writes something of its own before the record — the context sync — so a record that
        is refused changes nothing. The line is judged with the seq the record gets after `ahead`
        more records (the context event the sync is about to write), or with `seq` (SEQ_BOUND: a
        note made now and written when its run finishes)."""
        refs = [refs] if isinstance(refs, (str, dict)) else list(refs or ())
        self._prepare("record", kind, text, refs, details, None, ahead=ahead, seq=seq,
                      pending_run=pending_run)

    def _prepare(self, op: str, kind: str, text: str, refs, details, requires, *, ahead: int = 0,
                 seq: int | None = None, pending_run: str | None = None) -> _Prepared:
        """Steps 1–4 of design 4.5, outside the lock: refusals known before it write nothing, and
        make not even the lock file."""
        self.admit(op)
        check_kind(kind, RECORD_KINDS + KIT_KINDS)
        outside = None
        checkpoint_model = None
        if kind == "checkpoint":
            outside = relations.Journal(_journal_bytes(self.journal))
            details, generated, checkpoint_model = self._checkpoint(details, refs, outside, pending_run=pending_run)
            refs = [*list(refs or ()), *generated]
            requires = checkpoints.SINCE
        elif kind in relations.METRIC_KINDS:
            outside = relations.Journal(_journal_bytes(self.journal))
            details, generated = self._metric(kind, details, refs, outside, pending_run=pending_run)
            refs = [*list(refs or ()), *generated]
            requires = relations.METRIC_SINCE
        elif kind in relations.ASSET_PUBLIC_KINDS:
            outside = relations.Journal(_journal_bytes(self.journal))
            details, generated = self._asset(kind, details, refs, outside, pending_run=pending_run)
            refs = [*list(refs or ()), *generated]
            requires = relations.ASSET_WRITER_SINCE
        text, text_bytes = _text(text)
        request_id = self._request_id()
        items = _items(refs)
        typed = [ref for name, ref in items if name is None]
        _check_kind_refs(kind, typed, details)
        if requires is not None and (relations.version_key(requires) is None or relations.version_key(
                requires) > relations.version_key(self.writer_version)):
            raise ValueError(f"requires is a PEP 440 version in its normal form, at most this writer's "
                             f"{self.writer_version}: {requires!r}")
        if outside is not None or seq is None or any(ref["relation"] == "resolves" for ref in typed):
            outside = relations.Journal(_journal_bytes(self.journal))
            _check_resolved(outside, typed)
        bound = self._bind(items, model=checkpoint_model)
        number = seq if seq is not None else outside.next_seq + ahead
        line = _encode(self._event(number, kind, text, _refs(items, bound), details))
        problem = _too_long(text_bytes, line)
        if problem:
            raise problem
        return _Prepared(text, request_id, items, typed, outside, bound, details, requires)

    def _checkpoint(self, details, refs, journal, *, pending_run=None):
        try:
            details = checkpoints.validate(details)
            model = self._asset_model(journal)
            historical = model.through(details["as_of_seq"])
            items = _items(refs)
            bound = self._bind(items, model=historical)
            typed = _refs(items, bound)
            version = details["context_version"]
            context_refs = [ref for ref in typed if ref.get("type") == "context_version"]
            if any(ref.get("version") != version for ref in context_refs):
                raise checkpoints.CheckpointError("checkpoint_reference", "CONTEXT refs must match the checkpoint's exact version")
            context_path = relations.context_file(self.root, version) if version is not None else None
            digest = (context_refs[0].get("digest", {}).get("sha256") if context_refs else
                      _file_sha(context_path) if context_path is not None else None)
            if version is not None and digest is None:
                raise checkpoints.CheckpointError("checkpoint_reference", "the preserved CONTEXT digest is unavailable")
            generated = checkpoints.generated_refs(details, journal, digest)
            if any(ref.get("relation") not in ("cites", "supports", "contradicts") and ref not in generated for ref in typed):
                raise checkpoints.CheckpointError("bad_checkpoint_record", "caller checkpoint refs only cite, support or contradict evidence")
            checkpoints.validate_basis(details, model, [*typed, *generated], reader=relations, pending_run=pending_run)
            return details, generated, historical
        except checkpoints.CheckpointError as exc:
            raise AnalyticsError(exc.code, str(exc)) from exc

    def _asset_model(self, journal):
        return relations.ReadModel(self.root, journal, alive=self.platform.processes.alive,
                                   sha=_file_sha, legacy_path=self.legacy_path)

    def _asset(self, kind, details, refs, journal, *, pending_run=None):
        try:
            normalized = relations.asset_validate(kind, details)
            caller = _items(refs)
            relations.asset_input_refs([ref for _, ref in caller if ref is not None])
            model = self._asset_model(journal)
            checking = normalized.get("check_run", {}).get("run_id") if kind == "asset_check" else None
            run = model.run(checking) if checking else None
            if (kind == "asset_check" and pending_run == checking and self.origin.means == "sdk"
                    and run is not None and run.get("status") == "running"):
                # The SDK queues this declaration. All fields and prior candidate/version identity
                # are checked now; the run's final observations are checked by record(), after
                # write_run, before the declaration can become a journal record.
                proposed = relations.asset_candidate(journal, normalized["asset_id"], normalized["candidate_event_id"])
                if normalized["artifact_digest"] != relations.asset_bundle_digest(proposed["details"]):
                    raise relations.AssetRecordError("the queued check names the candidate's artifact version")
                if proposed["details"].get("code") and relations.asset_target(normalized.get("code") or {}) != relations.asset_target(proposed["details"]["code"]):
                    raise relations.AssetRecordError("the queued check names the candidate's calculation code version")
            else:
                relations.asset_validate_context(kind, normalized, journal, model)
            generated = [typed_reference(ref) for ref in relations.asset_semantic_refs(kind, normalized)]
        except relations.AssetRecordError as exc:
            raise AnalyticsError("bad_asset", str(exc)) from exc
        return normalized, generated

    def _asset_written(self, kind, details, refs, journal, *, operation):
        problem = relations.asset_record_problem(kind, details, refs)
        if problem:
            raise AnalyticsError("bad_asset", problem)
        if kind == "asset_mode" and (operation != "set_asset_mode" or self.origin.actor != "human"):
            raise AnalyticsError("bad_asset", "asset mode changes are made by the human settings application")
        if kind == "asset_notification" and operation not in ("review assets", "publish_asset_suggestion"):
            raise AnalyticsError("bad_asset", "asset notifications are written only by the shared review/publication application")
        try:
            relations.asset_validate_context(kind, details, journal, self._asset_model(journal))
        except relations.AssetRecordError as exc:
            raise AnalyticsError("bad_asset", str(exc)) from exc

    def _asset_update(self, details, refs, journal, *, registration=False, bookkeeping=False):
        if isinstance(details, dict) and "suggestion_history" in details and not bookkeeping:
            raise AnalyticsError("bad_asset", "suggestion choice snapshots are generated by the asset application")
        problem = relations.asset_update_problem(details, refs)
        if problem:
            raise AnalyticsError("bad_asset", problem)
        model = self._asset_model(journal)
        try:
            proposed = relations.asset_candidate(journal, details["asset_id"], details["candidate_event_id"])
            problem = _state_conflict(journal, proposed["id"], details["expected_state"], details["new_state"])
            if problem:
                raise problem
            new = details["new_state"]
            if new == "registered" and not registration:
                raise AnalyticsError("registration_required", "register the candidate explicitly before recording registered")
            if new in ("trial", "validated") and details["expected_state"] != new:
                proof = relations.asset_promotion_proof(proposed, new, journal, model)
                if details.get("promotion") != proof:
                    raise AnalyticsError("bad_asset", "asset promotion evidence differs from the confirmed current artifact checks")
            identity = details.get("identity")
            if identity and (not model.reference(identity["target"])["confirmed"] or
                             identity["target"].get("asset_id") == details["asset_id"]):
                raise AnalyticsError("bad_asset", "identity targets a confirmed different candidate")
        except relations.AssetRecordError as exc:
            raise AnalyticsError("bad_asset", str(exc)) from exc

    def _metric(self, kind, details, refs, journal, *, pending_run=None):
        """ADR-0011: payload-owned relationships and exact prior targets, checked under lock too."""
        try:
            details = relations.validate_metric(kind, details)
        except relations.MetricContractError as exc:
            raise AnalyticsError("bad_metric_record", str(exc)) from exc
        caller = _items(refs)
        if any(ref is not None and ref["relation"] in relations.METRIC_STATE_RELATIONS for _, ref in caller):
            raise AnalyticsError("bad_metric_record", "structured details own state-changing relationships")
        generated = []

        def metadata(event):
            value = event.value.get("details")
            return value if isinstance(value, dict) else {}

        def target(event_id, wanted, metric_id=None):
            event = journal.event_of(event_id)
            if event is None or event.kind != wanted or event.seq is None or event.seq >= journal.next_seq:
                raise AnalyticsError("metric_reference", f"{event_id!r} is not a unique prior {wanted}")
            if metric_id is not None and metadata(event).get("metric_id") != metric_id:
                raise AnalyticsError("metric_reference", f"{event_id!r} belongs to another metric")
            return event

        def current(event):
            state = journal.replay().state_of(event.id)
            if state is None or state["state"] != "current":
                raise AnalyticsError("state_conflict", f"{event.id} is no longer current", details=state)

        def definition(item, relation="uses"):
            target(item["event_id"], "metric_definition", item["metric_id"])
            generated.append(typed_reference({"type": "metric_definition", "metric_id": item["metric_id"],
                                               "event_id": item["event_id"],
                                               "relation": relation}))

        if kind == "metric_definition":
            previous = details.get("previous_definition")
            same = [event for event in journal.events if event.kind == kind
                    and metadata(event).get("metric_id") == details["metric_id"]]
            if previous:
                old = target(previous, kind, details["metric_id"])
                current(old)
                if sum((journal.replay().state_of(e.id) or {}).get("state") == "current"
                       for e in same) != 1:
                    raise AnalyticsError("state_conflict", "this metric has conflicting current definitions")
                definition({"metric_id": details["metric_id"], "event_id": previous}, "supersedes")
            elif same:
                raise AnalyticsError("state_conflict", "an existing metric needs previous_definition")
            for parent in details["derived_from"]:
                definition(parent, "derived_from")
        elif kind == "analysis_focus":
            view = relations.metric_projection([e.value for e in journal.events], journal.replay().record_states())
            previous = details.get("previous_focus")
            if previous:
                old = target(previous, kind)
                current(old)
                if view["focus_state"] != "current" or view["current_focus"]["event_id"] != previous:
                    raise AnalyticsError("state_conflict", "the current analysis focus is ambiguous or changed")
                generated.append(typed_reference({"type": "analysis_focus", "event_id": previous,
                                                   "relation": "supersedes"}))
            elif view["focus_state"] != "absent":
                raise AnalyticsError("state_conflict", "a focus change needs previous_focus")
            for item in details["metrics"]:
                definition({"metric_id": item["metric_id"], "event_id": item["event_id"]})
        else:
            definition({"metric_id": details["metric_id"], "event_id": details["definition_event_id"]})
            if "run_id" not in details:
                raise AnalyticsError("bad_metric_record", "an observation names its recorded run_id")
            run = _Lookup(self).model.run(details["run_id"])
            if run is None:
                raise AnalyticsError("metric_reference", "the observation run is missing or unsupported")
            generated.append(typed_reference({"type": "run", "run_id": run["id"],
                                               "relation": "generated_by"}))
            script = run.get("script") or {}
            if script.get("snapshot") and SHA256_RE.fullmatch(script.get("sha256") or ""):
                generated.append(typed_reference({"type": "code_snapshot", "run_id": run["id"],
                    "path": script["snapshot"], "digest": {"sha256": script["sha256"]},
                    "relation": "uses", "basis": "observed"}))
            for item in run.get("inputs") or []:
                via = item.get("via") or []
                basis = ("observed" if any(v not in ("scan", "declared") for v in via)
                         else "declared" if "declared" in via else "inferred")
                ref = {"type": "data_observation", "run_id": run["id"], "role": "input",
                       "path": item["path"], "relation": "uses", "basis": basis}
                if SHA256_RE.fullmatch(item.get("sha256") or ""):
                    ref["digest"] = {"sha256": item["sha256"]}
                generated.append(typed_reference(ref))
            result_ref = details.get("result_ref")
            if result_ref:
                result_ref = typed_reference(result_ref)
                details["result_ref"] = result_ref
                if result_ref["version_binding"] != "author" or result_ref["relation"] in relations.METRIC_STATE_RELATIONS:
                    raise AnalyticsError("metric_reference", "result_ref is an exact, non-state-changing version")
                if result_ref.get("run_id") != run["id"] or (result_ref["type"] == "data_observation"
                                                            and result_ref.get("role") != "output"):
                    raise AnalyticsError("metric_reference", "result_ref names an output of the observation run")
                if not (run.get("status") == "running" and pending_run == run["id"]):
                    if result_ref["type"] == "data_observation":
                        found = next((o for o in run.get("outputs") or []
                                      if o.get("path") == result_ref["path"] and o.get("change") != "deleted"), None)
                    else:
                        found = next((v for v in relations.run_visualizations(run)
                                      if v["id"] == result_ref["visualization_id"]), None)
                    if found is None:
                        raise AnalyticsError("metric_reference", "result_ref is not a recorded run output")
                    claimed = result_ref.get("digest")
                    if claimed and claimed["sha256"] != found.get("sha256"):
                        raise AnalyticsError("metric_reference", "result_ref digest does not match the recorded output version")
                generated.append(result_ref)
            recomputed = details.get("recomputed_from")
            if recomputed:
                old = target(recomputed, kind, details["metric_id"])
                if metadata(old).get("definition_event_id") != details["definition_event_id"]:
                    raise AnalyticsError("metric_reference", "a recomputation uses the same exact definition")
                generated.append(typed_reference({"type": "event", "event_id": recomputed,
                                                   "relation": "derived_from"}))
        return details, generated

    def update(self, target: str, *, expected: str, new: str, text: str) -> Written:
        """Change one record's state (withdraw, restore, reopen, a reuse step: ADR-0006 6) by a
        record_update, written only when, under the journal lock, the replay — the reader's own —
        finds one record `target` whose state is `expected` and allows `new` from it. Otherwise
        state_conflict, with the state it is in, the transitions allowed from it, and the event that
        made that state with its request_id, so a caller that wrote the change before and lost the
        answer knows its own write; nothing is written (design 4.6 and 8)."""
        op = "update a record"
        self.admit(op)
        for name, value in (("expected", expected), ("new", new)):
            if not (isinstance(value, str) and value):
                raise ValueError(f"{name} is a state: {value!r}")
        text, text_bytes = _text(text)
        request_id = self._request_id()
        ref = {"type": "event", "event_id": target, "relation": "updates", "basis": "declared",
               "version_binding": "author"}
        details = {"expected_state": expected, "new_state": new}
        outside = relations.Journal(_journal_bytes(self.journal))
        asset_target = outside.event_of(target)
        if asset_target is not None and asset_target.kind == "asset_candidate":
            raise AnalyticsError("registration_required" if new == "registered" else "asset_update_required",
                                 "asset state changes use the shared asset application to preserve their evidence and history")
        # Judged before the lock as well, so a refusal known then makes not even the lock file; the
        # judgment under the lock is the one that counts (another writer may change the state).
        problem = _too_long(text_bytes, _encode(self._event(outside.next_seq, "record_update", text, [ref],
                                                             details))) or _state_conflict(
            outside, target, expected, new)
        if problem:
            raise problem
        with self.locked(op) as held:
            problem = _state_conflict(held.journal, target, expected, new)
            if problem:
                raise problem
            event = held.append("record_update", text, refs=[ref], details=details)
        return Written(event, request_id, [], [{"event_id": target, "relation": "updates",
                                                "state_before": expected, "state_after": new}])

    def write_run(self, run_dir, record: dict) -> None:
        """Write a run's record (`runs/<id>/run.json`, analytics.run/2) whole by a replace (design
        4.8); a run writes it up to three times under its run lock, and its completion event — a
        `record("run", …)` with the run's `about` reference — comes after the last."""
        run_dir = Path(run_dir)
        if run_dir.parent != self.root / "runs" or not RUN_ID_V2_RE.fullmatch(run_dir.name):
            raise ValueError(f"a run's directory is runs/<run id> of {self.root}: {run_dir}")
        self._format("write a run record")
        write_json(run_dir / "run.json", _stamped(record, "run"), platform=self.platform)

    def write_dataset(self, meta, record: dict) -> None:
        """Write an import's record (`data/imported/<name>.meta.json`, analytics.dataset/1) whole by a
        replace (design 4.8): after the dataset's parquet, before its `import` event (a `record("import",
        …)` with the dataset_import `about` reference)."""
        meta = Path(meta)
        if meta.parent != self.root / LAYOUT.imported_dir or not meta.name.endswith(".meta.json"):
            raise ValueError(f"an import record is {LAYOUT.imported_dir}/<name>.meta.json of {self.root}: {meta}")
        self._format("write an import record")
        write_json(meta, _stamped(record, "dataset"), platform=self.platform)

    # --- binding names at write time (design 4.7) ------------------------------------------------

    def _bind(self, items, *, model=None) -> dict[int, tuple[dict, dict]]:
        """Position of each name in `items` -> (the reference written, its binding entry). The read
        model is read once, and only when a name needs it."""
        lookup = _Lookup(self, model)
        return {index: self._bind_name(name, lookup) for index, (name, _) in enumerate(items)
                if name is not None}

    def _bind_name(self, name: str, lookup: _Lookup) -> tuple[dict, dict]:
        if RUN_ID_V2_RE.fullmatch(name):
            return _bound(name, {"type": "run", "run_id": name}, "author")
        if EVENT_ID_V2_RE.fullmatch(name):
            return _bound(name, {"type": "event", "event_id": name}, "author")
        if "::" in name:
            return self._bind_key(name, name, lookup)
        if name.endswith(".parquet") or DATASET_NAME_RE.match(name):
            found = self._bind_data(name, lookup)
            if found is not None:
                return found
        version = relations.context_file(self.root, name)
        if version is not None and (digest := _file_sha(version)) is not None:
            return _bound(name, {"type": "context_version", "version": name,
                                 "digest": {"sha256": digest}}, "author")
        if not name.endswith(".parquet"):
            keys = lookup.model.keys_named(name)
            if len(keys) == 1:
                return self._bind_key(next(iter(keys)), name, lookup)
            if keys:
                candidates = sorted(v for versions in keys.values() for v in versions)
                return _bound(name, {"type": "visualization_version", "token": name}, "none", candidates)
            return _bound(name, {"type": "visualization_version", "token": name}, "none")
        return _bound(name, {"type": "dataset_import", "token": name}, "none")

    def _bind_key(self, key: str, token: str, lookup: _Lookup) -> tuple[dict, dict]:
        """`script.py::id`: the run of the same request when it has that Visualization (author),
        else the key's newest successful version (inferred), else unbound."""
        model = lookup.model
        script, _, vis_id = key.rpartition("::")
        if self.origin.request_id:
            for run_id in model.runs_of_request(self.origin.request_id):
                run = model.run(run_id)
                found = _visualization(run, script, vis_id, model.read_as(run_id, _script_path(run)))
                if found is not None:
                    return _bound(token, _version_ref(run_id, vis_id, found), "author")
        latest = model.latest_version(key)
        if latest is not None:
            run_id, _, _ = latest[0].partition("/")
            found = next((v for v in relations.run_visualizations(model.run(run_id)) if v["id"] == vis_id), {})
            return _bound(token, {**_version_ref(run_id, vis_id, found), "token": token}, "inferred")
        return _bound(token, {"type": "visualization_version", "key": key}, "none")

    def _bind_data(self, name: str, lookup: _Lookup) -> tuple[dict, dict] | None:
        """A dataset name or a parquet path: its import record (dataset_import), else the newest
        successful run that output it (data_observation), both inferred; None when neither is."""
        model = lookup.model
        meta = model.import_record(path=name) if name.endswith(".parquet") else model.import_record(name=name)
        if meta is not None and isinstance(meta.get("path"), str) and SHA256_RE.fullmatch(str(meta.get("sha256"))):
            return _bound(name, {"type": "dataset_import", "path": meta["path"], "token": name,
                                 "digest": {"sha256": meta["sha256"]}}, "inferred")
        output = (model.latest_output(name) if name.endswith(".parquet")
                  else model.latest_output(name=name))
        if output is None:
            return None
        run_id, path, sha = output
        ref = {"type": "data_observation", "run_id": run_id, "role": "output", "path": path, "token": name}
        if sha and SHA256_RE.fullmatch(sha):
            ref["digest"] = {"sha256": sha}
        return _bound(name, ref, "inferred")


class _Lookup:
    """The read model a binding reads, read once and only when a name needs it."""

    def __init__(self, writer: Writer, model=None):
        self.writer = writer
        self._model: relations.ReadModel | None = model

    @property
    def model(self) -> relations.ReadModel:
        if self._model is None:
            w = self.writer
            self._model = relations.read(w.root, alive=w.platform.processes.alive, sha=_file_sha,
                                         legacy_path=w.legacy_path)
        return self._model


def _bound(name: str, ref: dict, binding: str, candidates=None) -> tuple[dict, dict]:
    """A name's reference (relation cites, basis declared, design 4.7) and its binding entry."""
    ref = _ordered({**ref, "relation": "cites", "basis": "declared", "version_binding": binding})
    entry = {"input": name, "ref": ref, "binding": binding}
    if candidates:
        entry["candidates"] = candidates
    return ref, entry


def _visualization(run, script: str, vis_id: str, read_as) -> dict | None:
    """The Visualization `vis_id` of a run whose script is `script` (as recorded, or as an older
    kit's path is read), or None."""
    if script not in (_script_path(run), read_as):
        return None
    return next((v for v in relations.run_visualizations(run) if v["id"] == vis_id), None)


def _script_path(run) -> str | None:
    script = run.get("script") if isinstance(run, dict) else None
    path = script.get("path") if isinstance(script, dict) else None
    return path if isinstance(path, str) else None


def _version_ref(run_id: str, vis_id: str, found: dict) -> dict:
    """A Visualization version, with its digest when its run's record keeps one (analytics.run/2,
    design 2.6): a lab.run/1 chart has none, and a digest it never held would read as a mismatch."""
    ref = {"type": "visualization_version", "run_id": run_id, "visualization_id": vis_id}
    if found.get("field") == "visualizations" and isinstance(found.get("sha256"), str) \
            and SHA256_RE.fullmatch(found["sha256"]):
        ref["digest"] = {"sha256": found["sha256"]}
    return ref


def _items(refs) -> list[tuple[str | None, dict | None]]:
    """The references given, in order: (name, None) for a name — stripped, empty and repeated ones
    left out — and (None, reference) for a reference the kit gives whole (typed_reference)."""
    if isinstance(refs, (str, dict)):
        refs = [refs]
    out: list[tuple[str | None, dict | None]] = []
    seen: set[str] = set()
    for item in refs or ():
        if isinstance(item, dict):
            out.append((None, typed_reference(item)))
            continue
        name = str(item).strip()
        if name and name not in seen:
            seen.add(name)
            out.append((name, None))
    return out


def _refs(items, bound: dict[int, tuple[dict, dict]]) -> list[dict]:
    return [bound[index][0] if name is not None else ref for index, (name, ref) in enumerate(items)]


def _check_resolved(journal: relations.Journal, typed: list[dict]) -> None:
    """A question or a to-do a record closes is a readable record of the journal (unknown_event)."""
    for ref in typed:
        if ref["type"] == "event" and ref["relation"] == "resolves" and not any(
                event.id == ref["event_id"] for event in journal.events):
            raise _unknown_event(ref["event_id"])


def _check_kind_refs(kind: str, typed: list[dict], details) -> None:
    """What the record schema asks of a kind (event_2), before anything is written."""
    if isinstance(details, dict) and details.get("contract") in {f"{k}/1" for k in relations.METRIC_KINDS}:
        if details["contract"] != f"{kind}/1":
            raise AnalyticsError("bad_metric_record", "the structured contract matches its record kind")
    if kind == "resolve" and not any(ref["relation"] == "resolves" for ref in typed):
        raise AnalyticsError("resolve_needs_target", "kind 'resolve' needs resolves=<event id>",
                             hint="pass the id of the question or todo being closed, e.g. e7")
    need = {"import": ("dataset_import", "about"), "run": ("run", "about"),
            "context": ("context_version", None)}.get(kind)
    if need and not any(ref["type"] == need[0] and need[1] in (None, ref["relation"]) for ref in typed):
        raise ValueError(f"a {kind} record holds a {need[0]} reference")
    if kind == "project" and not (isinstance(details, dict) and isinstance(details.get("project_id"), str)):
        raise ValueError("a project record's details hold its project_id")
    if details is not None and not isinstance(details, dict):
        raise ValueError(f"details is an object: {details!r}")


def _stamped(record: dict, family: str) -> dict:
    """A record with its family's schema first (FAMILIES): another schema is refused."""
    if not isinstance(record, dict):
        raise ValueError(f"a record is an object: {record!r}")
    schema = record.get("schema", FAMILIES[family])
    if schema != FAMILIES[family]:
        raise ValueError(f"a {family} record of this writer is {FAMILIES[family]}, not {schema!r}")
    for name, allowed in (("means", MEANS), ("actor_basis", ACTOR_BASES)):
        if name in record and record[name] not in allowed:
            raise ValueError(f"{name} is one of {', '.join(allowed)}: {record[name]!r}")
    return {"schema": FAMILIES[family], **{k: v for k, v in record.items() if k != "schema"}}


def _journal_bytes(journal: Path) -> bytes:
    try:
        return journal.read_bytes()
    except FileNotFoundError:
        return b""


def _event_named(journal: relations.Journal, name) -> dict | None:
    """{id, request_id} of the event a replay names (`e<seq>` or `journal:<line>`), or None."""
    if not isinstance(name, str):
        return None
    event = journal.event_of(name)
    out = {"id": name}
    if event is not None and isinstance(event.value.get("request_id"), str):
        out["request_id"] = event.value["request_id"]
    return out


def _changes_state(ref: dict) -> bool:
    """Whether a reference resolves or corrects another record (STATE_RELATIONS)."""
    return ref["type"] in relations.EVENT_TARGETS and ref["relation"] in STATE_RELATIONS


def _targets(before: relations.Journal, after: relations.Journal, refs) -> list | None:
    """For each reference that resolves or corrects another record: its state before and after the
    record (both replayed under the lock, as the reader replays them, design 4.6). A record already
    resolved stays resolved and says which event resolved it; corrections say every correction."""
    out = []
    for ref in refs:
        if not _changes_state(ref):
            continue
        name = ref["event_id"]
        was, now = before.replay().state_of(name), after.replay().state_of(name)
        entry = {"event_id": name, "relation": ref["relation"],
                 "state_before": was["state"] if was else None, "state_after": now["state"] if now else None}
        if was is None:
            entry["reason"] = _target_reason(before, name)
        elif was["made_by"]:
            entry["made_by"] = _event_named(before, was["made_by"])
        if now and ref["relation"] == "supersedes" and now["corrected_by"]:
            entry["corrected_by"] = now["corrected_by"]
        out.append(entry)
    return out or None


def _target_reason(journal: relations.Journal, target) -> str:
    """Why a target has no state: several lines hold its id, no readable record has it, or it is a
    record without one (a run's completion event, say)."""
    named = isinstance(target, str) and EVENT_ID_V2_RE.fullmatch(target)
    lines = journal.lines_of.get(target, []) if named else []
    if len(lines) > 1:
        return "target_ambiguous"
    if not lines or journal.event_of(target) is None:
        return "target_missing"
    return "no_state"


def _state_conflict(journal: relations.Journal, target, expected: str, new: str) -> AnalyticsError | None:
    """Why a record_update of `target` from `expected` to `new` is not written now, or None."""
    details: dict = {"target": target, "expected_state": expected, "new_state": new}
    named = isinstance(target, str) and EVENT_ID_V2_RE.fullmatch(target)
    found = journal.replay().state_of(target) if named else None
    if found is None:
        details["reason"] = _target_reason(journal, target)
        if details["reason"] == "target_ambiguous":
            details["candidates"] = [f"journal:{n}" for n in journal.lines_of[target]]
        said = {"target_missing": f"no record {target!r} to update",
                "target_ambiguous": f"{target} is the id of several journal lines",
                "no_state": f"{target} is a record without a state"}[details["reason"]]
    else:
        details.update(state=found["state"], allowed=found["transitions"])
        if found["made_by"]:
            details["made_by"] = _event_named(journal, found["made_by"])
        if found["state"] == expected and new in found["transitions"]:
            return None
        details["reason"] = "state_differs" if found["state"] != expected else "transition_not_allowed"
        said = (f"{target} is {found['state']}, not {expected}" if found["state"] != expected
                else f"{target} does not go from {expected} to {new}")
    hint = "read its state again (analytics status) and decide whether the change still applies"
    if details.get("made_by"):
        made = details["made_by"]
        hint += (f"; {made['id']} made its state" + (f" (request_id {made['request_id']})"
                                                    if made.get("request_id") else ""))
    return AnalyticsError("state_conflict", f"{said}: {expected} -> {new} was not written", hint=hint,
                          details=details)
