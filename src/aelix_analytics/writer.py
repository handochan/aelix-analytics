"""Who may write a project's records, and how: the first part of the common record writer (Issue #13,
docs/design/RECORD-WRITER-AND-MIGRATION.md sections 3 and 4). Standard library only.

- Which project format a directory holds (`marker_state`): its two markers, `analytics.json` and
  `lab.json`, read afresh every time, nothing cached (design 4.3); with neither, a migration's
  record (`.analytics/migration/` or `.analytics/migration.lock`) makes it a migration's destination
  that has not committed (`incomplete`, Issue #13 PR 2), which no kit writes to or reads.
- Whether this kit writes there (`admit`): every write operation asks before its first side effect —
  a lock file, a directory, a cache, a log line included — and a journal write asks once more under
  the journal lock (core). A write it refuses raises; a side write of a read (`side=True`) is
  skipped instead. Which marker format this kit writes is the one constant WRITABLE: `lab.project/1`
  until the new writer (PR 3 of #13) makes it `analytics.project/2`.
- Who writes and how (`Origin`): an entry point says it, the writer never guesses it nor reads the
  environment (design 4.1). Records of this kit (`lab.event/1`) hold its actor and session only.
- The error class every refusal is (AnalyticsError), the JSON helpers the kit writes records with
  (`write_json`, `append_jsonl`), the names of the stored layout this kit writes (LEGACY_LAYOUT) and
  the sorting of free-form references (`classify_refs`).

This module imports `_platform` (ports) and `relations` (the read model) and nothing else of the
package: `core` imports it and gives its names under the same names (core.AnalyticsError,
core.write_json, …), so the aelix process, which loads core, loads this file as core's sibling.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import _platform, relations


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


# --- the stored layout --------------------------------------------------------------------------

@dataclass(frozen=True)
class LegacyLayout:
    """The stored names of a `lab.json` project, the format this kit writes (design 2.3). The new
    format's Layout comes with the writer that writes it (PR 3 of #13). What core reads from here:
    `managed_dirs` and `ignored_dirs` (core.MANAGED_DIRS, core.IGNORED_DIRS), `marker` and `schema`
    (the markers below) and `imported_dir` (core.LEGACY_DATASET_DIRS). The other names are still
    spelled where paths are built (core.Project, capture) until that Layout replaces them."""

    marker: str = "lab.json"
    schema: str = "lab.project/1"
    state_dir: str = ".lab"
    workspace_dir: str = "report"
    imported_dir: str = "data/raw"
    visualizations_dir: str = "charts"
    # The top-level directories the kit itself manages: a path below one is named by this name
    # whatever case it is stored in and wherever it leads (core.MANAGED_DIRS).
    managed_dirs: tuple[str, ...] = (".venv", ".lab", "runs", "report")
    # The directories that are never data inputs or outputs of a run (core.IGNORED_DIRS).
    ignored_dirs: frozenset[str] = frozenset({
        ".venv", ".lab", "runs", "report", "scripts", ".git", "__pycache__", ".pytest_cache",
        ".ruff_cache", ".omc", ".aelix", "node_modules", ".ipynb_checkpoints"})


LEGACY_LAYOUT = LegacyLayout()

# --- markers --------------------------------------------------------------------------------------

MARKER = "analytics.json"
LEGACY_MARKER = LEGACY_LAYOUT.marker
PROJECT = "analytics.project/2"
LEGACY_PROJECT = LEGACY_LAYOUT.schema
# The marker formats this kit writes to. PR 3 of #13 makes it {PROJECT} with the writer of that format.
WRITABLE = frozenset({LEGACY_PROJECT})
# ADR-0006 1: the fields an analytics.project/2 marker holds (the record schema's project_2).
PROJECT_FIELDS = ("schema", "project_id", "title", "created_at", "min_writer_version")

# What a directory is, by its markers (design 4.3):
NONE, LEGACY, CURRENT, UNKNOWN, INVALID, CONFLICT, INCOMPLETE = (
    "none", "legacy", "current", "unknown", "invalid", "conflict", "incomplete")
# What a migration leaves in its destination before the marker, its commit point (design 6.3): the
# destination's lock and the migrations' own directories, each with its ledger (progress.jsonl).
MIGRATION_DIR = ".analytics/migration"
MIGRATION_LOCK = ".analytics/migration.lock"
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
