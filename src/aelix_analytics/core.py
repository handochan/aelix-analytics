"""Project model for Aelix Analytics projects. Standard library only.

The aelix extension loads this file inside the aelix process, so it must not import polars, duckdb,
plotting libraries or any other aelix_analytics module except `_platform` and `relations` (the
records read as ADR-0006 says), which are standard library only for the same reason. Code that
needs the analysis libraries runs inside a project's own uv environment (runner, data, report).

Every operation that behaves differently on Linux, macOS and Windows goes through the bundle of
ports a `Project` carries, so nothing in this file tests `sys.platform`. See `_platform`.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path

from . import _platform, relations

KIT_VERSION = "0.4.0"
MARKER = "lab.json"
SCHEMA_PROJECT = "lab.project/1"
SCHEMA_RUN = "lab.run/1"
SCHEMA_EVENT = "lab.event/1"
SCHEMA_DATASET = "lab.dataset/1"

NOTE_KINDS = ("finding", "decision", "assumption", "question", "todo", "note", "gap", "resolve")
EVENT_KINDS = ("project", "import", "run", "context", *NOTE_KINDS)
KEY_KINDS = ("finding", "decision", "assumption")
OPEN_KINDS = ("question", "todo")
DATA_SUFFIXES = (".parquet", ".csv", ".tsv", ".json", ".ndjson", ".jsonl", ".arrow", ".feather",
                 ".ipc", ".xlsx")
CHART_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".html")

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z0-9][a-z0-9-]{0,60}$")
EVENT_ID_RE = re.compile(r"^e\d+$")
DATASET_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SCRIPT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
MAX_EVENT_TEXT = 4000
# A run that reads hundreds of files must not turn its journal entry into a path dump: run.json
# keeps every path, the event keeps a readable head of them.
MAX_EVENT_REFS = 24
# How much of CONTEXT.md goes into the system prompt every turn. Sections past this budget are
# listed with their line ranges instead, for the agent to read on demand.
CONTEXT_PROMPT_CHARS = 4000
# How many bytes of a run's stdout and stderr the kit hands back (runner._tail): a log larger than
# this is shown in part.
OUTPUT_TAIL = 4000

# Directories that are never data inputs or outputs of a run.
IGNORED_DIRS = {".venv", ".lab", "runs", "report", "scripts", ".git", "__pycache__",
                ".pytest_cache", ".ruff_cache", ".omc", ".aelix", "node_modules",
                ".ipynb_checkpoints"}

# The OS-dependent operations of this process. A Project takes this unless a caller — in practice a
# test — hands it another bundle; nothing here looks one up by name or by environment variable.
PLATFORM = _platform.detect()


class LabError(Exception):
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


# The kit version that renamed the public names — commands, tools, SDK functions and environment
# variables (Issue #9). Fixed: it names the boundary, not the current version.
NAMES_RENAMED_IN = "0.3.0"


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
            raise LabError("env_renamed", renamed_env_message(old, remedy=False),
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


def _resolved(path: Path) -> Path:
    """`path` with its links followed as far as they lead. `os.path.realpath` rather than
    `Path.resolve`, which on CPython 3.12 raises for a link loop where 3.13 returns the path
    resolved up to the loop: the same answer on every version."""
    with contextlib.suppress(OSError):
        return Path(os.path.realpath(path))
    return path


class Project:
    """Paths of one analytics project, and the platform ports its operations use.

    Construction does not touch the file system. `platform` is the bundle from `_platform`: this
    process's (`PLATFORM`) unless a test passes another implementation, so the same project code
    runs unchanged on Linux, macOS and Windows."""

    def __init__(self, root, *, platform: _platform.Platform | None = None):
        self.root = Path(root).expanduser().resolve()
        self.platform = platform or PLATFORM
        self.marker = self.root / MARKER
        self.context = self.root / "CONTEXT.md"
        self.journal = self.root / "journal.jsonl"
        self.data = self.root / "data"
        self.raw = self.data / "raw"
        self.derived = self.data / "derived"
        self.scripts = self.root / "scripts"
        self.runs = self.root / "runs"
        self.report = self.root / "report"
        self.lab = self.root / ".lab"
        self.cache = self.lab / "cache"
        self.context_history = self.lab / "context"
        self.telemetry = self.lab / "telemetry.jsonl"
        self.queries = self.lab / "queries.jsonl"

    def path(self, rel) -> Path:
        p = expand_home(Path(rel))
        return p if p.is_absolute() else self.root / p

    def rel(self, path) -> str:
        """Project-relative POSIX path, or the absolute path for files outside the project."""
        p = _resolved(self.path(path))
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return str(p)

    def inside(self, path) -> bool:
        p = _resolved(self.path(path))
        return p == self.root or self.root in p.parents

    def config(self) -> dict:
        data = read_json(self.marker)
        data = data if isinstance(data, dict) else {}
        if newer_schema(data.get("schema"), SCHEMA_PROJECT):
            raise LabError("project_too_new",
                           f"{self.root}/{MARKER} is {data['schema']}, and this kit reads "
                           f"{SCHEMA_PROJECT}",
                           hint="a newer version of the analytics kit made this project; update the kit "
                                "instead of reading it with this one")
        return data

    def title(self) -> str:
        return str(self.config().get("title") or self.root.name)


# --- small file helpers ---

def now() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="milliseconds")


def open_text(path, mode: str = "w"):
    """Open a text file the kit writes (ADR-0007 decision 2): UTF-8 without a BOM, and "\\n" written
    as "\\n" on every platform — text mode's default (newline=None) writes "\\r\\n" on Windows. The
    text itself is not changed: a "\\r" the caller passes is written as it is (newlines_as_lf is
    the caller's to apply where a file's content has to be LF too). Every text file the kit writes
    is opened here or through write_text (tests/test_line_endings.py checks)."""
    if mode not in ("w", "a", "x"):
        raise ValueError(f"open_text writes text: {mode!r}")
    return open(path, mode, encoding=_platform.TEXT_ENCODING, newline=_platform.TEXT_NEWLINE)


def write_text(path, text: str, *, append: bool = False) -> None:
    """Write (or with `append`, append) `text` as a text file the kit writes: see open_text."""
    with open_text(path, "a" if append else "w") as fh:
        fh.write(text)


def newlines_as_lf(text: str) -> str:
    """`text` with its CRLF and lone CR line ends as "\\n", as text mode reads a file."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, obj, *, platform: _platform.Platform | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open_text(tmp) as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")
    (platform or PLATFORM).files.replace(tmp, path)


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


def append_jsonl(path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_text(path, "a") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def reject_unportable_name(name, *, kind: str, exists: bool = False) -> None:
    """Refuse a NEW name that Windows cannot use as a file name.

    `con`, `nul`, `aux`, `com1` and their relatives are device names on Windows whatever the
    extension is, so a project created on Linux with one of them cannot be opened there. A name the
    project already has is never refused: existing records stay readable and re-runnable on every
    platform, because a record outlives the machine that wrote it."""
    if exists or not _platform.reserved_name(name):
        return
    raise LabError("name_not_portable",
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
    """sha256 of files keyed by (size, mtime_ns), persisted in .lab/cache/hashes.json.

    An entry is [size, mtime_ns, sha256], and once line_endings_only has read the file at that size
    and time a fourth value: [lf, crlf] (line_ending_digests) or null (a file the rule does not
    judge: over the size limit or a NUL in its head). A file that could not be read gets no fourth
    value, so the next look reads it again. A kit that does not know the fourth value reads such an
    entry as missing and hashes the file again, which drops it: a cost, nothing more (.lab/cache/ is
    derived)."""

    def __init__(self, project: Project):
        self.file = project.cache / "hashes.json"
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

    def save(self) -> None:
        if self.dirty:
            with contextlib.suppress(OSError):
                write_json(self.file, self.data, platform=self.platform)
            self.dirty = False


def project_lock(project: Project):
    """Exclusive inter-process lock for journal and context-history writes. Not re-entrant."""
    return project.platform.locks.exclusive(project.lab / "lock")


def run_lock(project: Project):
    """Serializes runs of one project (see runner.execute)."""
    return project.platform.locks.exclusive(project.lab / "run.lock")


# --- trust: which projects an aelix session may activate just by being inside them ---

def trust_file() -> Path:
    refuse_renamed_env(("AELIX_LAB_TRUST_FILE",))
    explicit = os.environ.get("AELIX_ANALYTICS_TRUST_FILE")
    if explicit:
        return Path(explicit).expanduser()
    agent_dir = os.environ.get("AELIX_CODING_AGENT_DIR")
    base = Path(agent_dir).expanduser() if agent_dir else Path.home() / ".aelix" / "agent"
    return base / "lab-projects.json"


def trusted_roots() -> set[str]:
    data = read_json(trust_file(), {})
    roots = data.get("projects") if isinstance(data, dict) else None
    return {str(r) for r in roots} if isinstance(roots, list) else set()


def is_trusted(root) -> bool:
    try:
        return str(Path(root).expanduser().resolve()) in trusted_roots()
    except (OSError, RuntimeError, ValueError):
        return False


def trust_project(root, *, platform: _platform.Platform | None = None) -> None:
    """Remember a project the user created or chose, so starting aelix inside it activates it."""
    resolved = str(Path(root).expanduser().resolve())
    roots = trusted_roots()
    if resolved in roots:
        return
    write_json(trust_file(), {"schema": "lab.trust/1", "projects": sorted(roots | {resolved})},
               platform=platform)


def find_project(start=None) -> Path | None:
    try:
        here = Path(start or os.getcwd()).expanduser().resolve()
    except OSError:
        return None
    for candidate in (here, *here.parents):
        if (candidate / MARKER).is_file():
            return candidate
    return None


# --- journal ---

def _last_seq(journal: Path) -> int:
    try:
        size = journal.stat().st_size
    except OSError:
        return 0
    with open(journal, "rb") as fh:
        fh.seek(max(0, size - 65536))
        tail = fh.read()
    for line in reversed(tail.splitlines()):
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            return int(json.loads(line).get("seq", 0))
    return max((int(e.get("seq", 0)) for e in read_jsonl(journal)), default=0)


def classify_refs(items) -> dict:
    """Sort free-form references into runs, events, charts (script::chart) and datasets."""
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


def resolve_ref_aliases(project: Project, ref_map: dict, runs: list[dict] | None = None) -> dict:
    """Turn a bare chart id (`monthly-revenue`, how a writer refers to it) into the
    `script.py::id` key the report links to. Unknown names stay in `other`."""
    unknown = list(ref_map.get("other") or [])
    if not unknown:
        return ref_map
    by_id: dict[str, str] = {}
    for chart in chart_index(list_runs(project) if runs is None else runs):  # newest first
        by_id.setdefault(str(chart["key"]).split("::")[-1], chart["key"])
    charts, rest = list(ref_map.get("charts") or []), []
    for item in unknown:
        key = by_id.get(item)
        if key is None:
            rest.append(item)
        elif key not in charts:
            charts.append(key)
    out = {**ref_map, "charts": charts, "other": rest}
    return {k: v for k, v in out.items() if v}


def _append_event_locked(project: Project, kind: str, text: str, refs=None, actor="agent",
                         session=None) -> dict:
    seq = _last_seq(project.journal) + 1
    event = {"schema": SCHEMA_EVENT, "seq": seq, "id": f"e{seq}", "ts": iso(), "kind": kind,
             "actor": actor or "agent", "text": (text or "").strip()[:MAX_EVENT_TEXT],
             "refs": {k: v for k, v in (refs or {}).items() if v not in (None, [], "", {})}}
    if session:
        event["session"] = session
    append_jsonl(project.journal, event)
    return event


def append_event(project: Project, kind: str, text: str, *, refs=None, actor="agent",
                 session=None) -> dict:
    if kind not in EVENT_KINDS:
        raise LabError("bad_kind", f"unknown journal kind {kind!r}",
                       hint="use one of: " + ", ".join(NOTE_KINDS))
    with project_lock(project):
        return _append_event_locked(project, kind, text, refs, actor, session)


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


def read_events(project: Project, journal: relations.Journal | None = None) -> list[dict]:
    """The lab.event/1 records of the journal, in line order, as relations.Journal reads it: a line
    that does not parse is left out and the record a /1 writer appended onto it is recovered, and
    every line of an id several lines share is kept. Records of another family or version, or of a
    kind this kit does not know, are not here: status() counts them. Only journal.jsonl is read
    (this runs before every prompt, through context_block), unless `journal` is one already read."""
    journal = relations.read_journal(project.root) if journal is None else journal
    return [event.value for event in journal.events if event.schema == SCHEMA_EVENT]


def read_model(project: Project, cache: HashCache | None = None) -> relations.ReadModel:
    """One read of the project's records as ADR-0006 says (relations.read): this platform's process
    port says whether a running run's process is alive, and the project's hash cache hashes the
    current files."""
    cache = cache or HashCache(project)
    return relations.read(project.root, alive=project.platform.processes.alive, sha=cache.sha)


def resolved_targets(events) -> dict[str, dict]:
    """Event id -> the later event that resolved it (refs.resolves)."""
    out = {}
    for ev in events:
        target = (ev.get("refs") or {}).get("resolves")
        if target:
            out[str(target)] = ev
    return out


def add_note(project: Project, kind: str, text: str, *, refs=None, resolves=None, actor="agent",
             session=None) -> dict:
    if kind not in NOTE_KINDS:
        raise LabError("bad_kind", f"unknown note kind {kind!r}",
                       hint="use one of: " + ", ".join(NOTE_KINDS))
    text = (text or "").strip()
    if not text:
        raise LabError("empty_note", "note text is empty")
    ref_map = resolve_ref_aliases(project, classify_refs(refs))
    if kind == "resolve" and not resolves:
        raise LabError("resolve_needs_target", "kind 'resolve' needs resolves=<event id>",
                       hint="pass the id of the question or todo being closed, e.g. e7")
    with project_lock(project):
        if resolves:
            resolves = str(resolves).strip()
            events = read_events(project)
            target = next((e for e in events if e.get("id") == resolves), None)
            if target is None:
                raise LabError("unknown_event", f"no journal event {resolves!r}",
                               hint="analytics_status lists open questions and todos with their ids")
            ref_map["resolves"] = resolves
        return _append_event_locked(project, kind, text, ref_map, actor, session)


# --- CONTEXT.md history ---

def context_versions(project: Project) -> list[Path]:
    if not project.context_history.is_dir():
        return []
    return sorted(project.context_history.glob("*.md"))


def sync_context(project: Project, actor="system", session=None) -> dict | None:
    """Snapshot CONTEXT.md into .lab/context/ and log a `context` event when it changed. The version
    is CONTEXT.md's bytes as they are; when only their line ends differ from the last version's
    (the last version's whole sha256 is one of the new bytes' line_ending_digests_of), the event
    says so instead of counting lines (ADR-0007 decision 4)."""
    try:
        data = project.context.read_bytes()
    except OSError:
        return None
    digest = sha256_bytes(data)[:12]
    with project_lock(project):
        versions = context_versions(project)
        if versions and versions[-1].stem.rsplit("_", 1)[-1] == digest:
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
        return _append_event_locked(project, "context", text, {"context": dest.name}, actor,
                                    session)


def context_goal(text: str) -> str:
    """First content line under a Goal heading (or 목표, the heading of earlier templates, which
    projects keep), else the first paragraph line."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^#{1,3}\s*(목표|goal|objective)", line.strip(), re.IGNORECASE):
            for follow in lines[i + 1:]:
                s = follow.strip()
                if s.startswith("#"):
                    break
                s = s.lstrip("-*> ").strip()
                if s and not s.startswith("("):
                    return s[:200]
    for line in lines:
        s = line.strip().lstrip("-* ").strip()
        if s and not s.startswith(("#", ">", "|", "<!--", "(")):
            return s[:200]
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
        if isinstance(rec, dict) and newer_schema(rec.get("schema"), SCHEMA_RUN):
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
        state or `stale`."""
        if run_id in self._memo:
            return self._memo[run_id]
        run = self.runs.get(run_id) or {}
        inputs, upstream, ambiguous, reasons = [], [], [], {}
        own = self._own(run)
        for entry in run.get("inputs") or []:
            state = self.input_state(entry, own)
            item = {"path": entry.get("path"), "state": state, "sha256": entry.get("sha256")}
            reason = (self.change_reason(entry.get("path", ""), entry.get("sha256"))
                      if state == "changed" else None)
            if reason:
                item["reason"] = reasons[entry.get("path")] = reason
            inputs.append(item)
            candidates = self.model.producers(entry.get("path"), entry.get("sha256"),
                                              consumer=run_id)
            if len(candidates) > 1 and entry.get("path") not in ambiguous:
                ambiguous.append(entry.get("path"))
            upstream += [c for c in candidates if c not in upstream and self.stale_run(c)]
        direct = [i["path"] for i in inputs if i["state"] in ("changed", "missing")]
        code = self.code_state(run)
        script = run.get("script") or {}
        code_reason = (self.change_reason(script["path"], script.get("sha256"))
                       if code == "changed" else None)
        result = {"inputs": inputs, "changed_inputs": direct, "stale_upstream": upstream,
                  "ambiguous_inputs": ambiguous, "cycle": self.model.upstream(run_id)["cycle"],
                  "code": code, "stale": bool(direct or upstream), "change_reasons": reasons,
                  "code_reason": code_reason}
        self._memo[run_id] = result
        return result


def chart_index(runs: list[dict]) -> list[dict]:
    """Latest version of every script::chart across successful runs, newest first."""
    by_key: dict[str, list[dict]] = {}
    last_ok_by_script: dict[str, dict] = {}
    for r in runs:
        if r.get("status") != "ok" or r.get("rerun_of"):
            continue
        script = (r.get("script") or {}).get("path") or "?"
        last_ok_by_script[script] = r
        for c in r.get("charts") or []:
            by_key.setdefault(chart_key(r, c), []).append(
                {**c, "key": chart_key(r, c), "run": r["id"], "script": script,
                 "run_title": r.get("title"), "started_at": r.get("started_at")})
    out = []
    for versions in by_key.values():
        latest = dict(versions[-1])
        last_run = last_ok_by_script.get(latest["script"]) or {}
        latest["retired"] = last_run.get("id") != latest["run"]
        latest["versions"] = [{"run": v["run"], "started_at": v.get("started_at")}
                              for v in versions]
        out.append(latest)
    out.sort(key=lambda c: c.get("started_at") or "", reverse=True)
    return out


# --- datasets ---

def list_datasets(project: Project, runs: list[dict] | None = None, *,
                  model: relations.ReadModel | None = None, sha=None) -> list[dict]:
    """Every dataset under data/raw and data/derived. `producers` are the successful runs that output
    its path, in run order, whatever bytes they wrote; `used_by` the successful runs that read it.

    With a read model and `sha` (the hash of a file's current bytes), each dataset also says which
    runs may have made its current bytes (ADR-0006 4): `producer_state` is `resolved` (one successful
    run output this path with these bytes), `ambiguous` (several, all in `producer_candidates`),
    `none` or `unknown` (the file could not be hashed), `producer` is the one candidate when there is
    one, and `written_by` holds every run that output the path, whatever its status. A candidate is
    a run whose output had the same path and bytes (basis observed), never a confirmed link. Without
    them nothing is hashed: the query views, imports and the SDK list datasets at no more cost.

    An import record of a family or version this kit cannot read (relations.unsupported_reason) is
    not used: the file is listed by its path and bytes, and `unsupported_record` says where that
    record is and why, as the read model counts it."""
    runs = list_runs(project) if runs is None else runs
    produced: dict[str, list[dict]] = {}
    used: dict[str, list[str]] = {}
    written: dict[str, list[str]] = {}
    for r in runs:
        for out in r.get("outputs") or []:
            if out.get("change") != "deleted":
                written.setdefault(out.get("path"), []).append(r["id"])
        if r.get("status") != "ok":
            continue
        for out in r.get("outputs") or []:
            produced.setdefault(out.get("path"), []).append({**out, "run": r["id"]})
        for entry in r.get("inputs") or []:
            used.setdefault(entry.get("path"), []).append(r["id"])
    items = []
    for base, kind in ((project.raw, "raw"), (project.derived, "derived")):
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.parquet")):
            rel = project.rel(p)
            try:
                size = p.stat().st_size
            except OSError:
                continue
            item = {"name": p.stem, "path": rel, "kind": kind, "bytes": size}
            meta = read_json(p.with_suffix(".meta.json")) if kind == "raw" else None
            unsupported = (relations.unsupported_reason(meta, frozenset({SCHEMA_DATASET}))
                           if isinstance(meta, dict) else None)
            if unsupported:
                item["unsupported_record"] = {"location": project.rel(p.with_suffix(".meta.json")),
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
            if model is not None and sha is not None:
                current = sha(p)
                candidates = model.producers(rel, current) if current else []
                item["producer_state"] = ("unknown" if not current else "none" if not candidates
                                          else "resolved" if len(candidates) == 1 else "ambiguous")
                item["producer_candidates"] = candidates
                if len(candidates) == 1:
                    item["producer"] = candidates[0]
                item["written_by"] = list(dict.fromkeys(written.get(rel, [])))
            items.append(item)
    return items


def dataset_name_taken(project: Project, name: str) -> str | None:
    """Path of an existing dataset that `name` would shadow or collide with."""
    for ds in list_datasets(project):
        if ds["name"] == name:
            return ds["path"]
    return None


def view_names(datasets: list[dict]) -> dict[str, str]:
    """Dataset view name -> path. Raw datasets keep their name; a colliding derived one gets _derived."""
    views: dict[str, str] = {}
    for ds in sorted(datasets, key=lambda d: (d["kind"] != "raw", d["path"])):
        name = re.sub(r"[^a-z0-9_]", "_", ds["name"].lower()) or "dataset"
        if name[0].isdigit():
            name = f"t_{name}"
        if name in views:
            name = f"{name}_derived" if ds["kind"] == "derived" else name
            n = 2
            while name in views:
                name = f"{name.rstrip('0123456789_')}_{n}"
                n += 1
        views[name] = ds["path"]
    return views


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


def status(project: Project, limit: int = STATUS_LIMIT) -> dict:
    """The project overview analytics_status, /analytics status and the kit's `status` give, from
    one read of the records (read_model): the identifiers and the read's `as_of_seq` and
    `source_revision` are those the Workspace shows."""
    cache = HashCache(project)
    model = read_model(project, cache)
    journal = model.journal
    listed = [e for e in journal.events if e.schema == SCHEMA_EVENT]
    events = [e.value for e in listed]
    source = {id(e.value): journal.source_of(e) for e in listed}
    runs = model_runs(project, model)
    datasets = list_datasets(project, runs, model=model, sha=cache.sha)
    fresh = Freshness(project, runs, cache, model)
    charts = chart_index(runs)
    resolved = resolved_targets(events)
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
                           "code": f["code"], "change_reasons": f["change_reasons"],
                           "code_reason": f["code_reason"]})
    recording = {r["run_id"]: r for r in model.runs()}
    run_rows = []
    for r in runs[-limit:][::-1]:
        rec = recording.get(r["id"]) or {}
        run_rows.append({"id": r["id"], "status": r.get("status"), "title": r.get("title"),
                         "duration_s": r.get("duration_s"), "charts": len(r.get("charts") or []),
                         "error": (r.get("error") or {}).get("summary"),
                         "rerun_of": r.get("rerun_of"),
                         "verdict": (r.get("reproduction") or {}).get("verdict"),
                         "recording_status": rec.get("recording_status"),
                         "reasons": rec.get("reasons", [])})
    open_items = [e for e in events if e.get("kind") in OPEN_KINDS and e["id"] not in resolved]
    key_items = [e for e in events if e.get("kind") in KEY_KINDS and e["id"] not in resolved]
    gaps = [e for e in events if e.get("kind") == "gap"]
    key_rows = []
    for e in key_items[-limit * 2:]:
        name = source[id(e)]
        key_rows.append({**_brief(e, name), "references": references_summary(model.references(name))})
    cache.save()
    versions = context_versions(project)
    return {
        "project": {"root": str(project.root), "title": project.title(),
                    "created_at": project.config().get("created_at"),
                    "goal": context_goal(newlines_as_lf(context_text)),
                    "context_chars": len(newlines_as_lf(context_text)),
                    "context_omitted": [s["name"] for s in context_omitted],
                    "context_truncated": context_truncated,
                    "context_updated": _version_time(versions[-1]) if versions else None},
        "report": str(project.report / "index.html"),
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
        "not_listed": [f"journal:{e.line}" for e in journal.events if e.schema != SCHEMA_EVENT],
        "shared_ids": {i: [f"journal:{n}" for n in lines]
                       for i, lines in journal.lines_of.items() if len(lines) > 1},
        "datasets": datasets,
        "views": view_names(datasets),
        "runs": run_rows,
        "charts": chart_rows,
        "open": [_brief(e, source[id(e)]) for e in open_items[-limit * 2:]],
        "key": key_rows,
        "recent": [_brief(e, source[id(e)]) for e in events[-10:]],
    }


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


def _stale_reasons(shown: list[str], direct: int, line_endings=frozenset()) -> str:
    """Why a chart is stale: its run's changed or missing inputs (the first `direct` of `shown`), then
    the stale runs upstream. Those output the same bytes as an input: producer candidates, not known
    producers (ADR-0006 4). An input in `line_endings` differs from its record only in its line ends
    and says so."""
    paths = [f"{p} {LINE_ENDINGS_NOTE}" if p in line_endings else p for p in shown[:direct]]
    runs = shown[direct:]
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
    add("head", f"report: {st['report']}")
    if st.get("source_revision"):
        add("head", _read_line(st))
    if older_runs > 0 or more_charts > 0 or older_open > 0 or key_at_limit or earlier > 0:
        # Where the entries the lists below leave out are: said once, and the lists refer to it.
        add("head", f"journal: {journal}", note=True)
    if p.get("goal"):
        add("head", f"goal: {_one_line(p['goal'])}")
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
    datasets = _capped(st["datasets"], caps.get("datasets"))
    for ds in datasets:
        add("datasets", f"  {ds['kind']:<7} {ds['name']}: {_fmt_int(ds.get('rows'))} rows × "
                        f"{ds.get('columns') or '?'} cols · {ds['path']} · {dataset_origin(ds)} · "
                        f"used by {len(ds.get('used_by') or [])} run(s)")
    if len(datasets) < len(st["datasets"]):
        add("datasets", f"  … {len(st['datasets']) - len(datasets):,} more dataset(s) not listed: "
                        f"{VIEWS_PAGES} to list every view; an imported one's record is "
                        "data/raw/<name>.meta.json")
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
            flags.append(f"STALE({_stale_reasons(shown, len(c['changed_inputs']), line_endings)}"
                         f"{more})")
        if c.get("ambiguous_inputs"):
            flags.append(f"producer ambiguous for {len(c['ambiguous_inputs'])} input(s)")
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
                      f"v{c['versions']}" + (" · " + "; ".join(flags) if flags else ""))
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


def _records_lines(st: dict, journal: Path) -> list[str]:
    """What the lists below cannot show: records this kit cannot read, lines that do not parse,
    record files not read, records of the next format, and ids that several lines share."""
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
    not_listed = st.get("not_listed") or []
    if not_listed:
        out.append(f"not listed: {len(not_listed):,} analytics.event/2 record(s) "
                   f"({_places(not_listed, STATUS_PLACES, EVERY_RECORD)}) — this kit lists "
                   f"lab.event/1 records only; they are in {journal}")
    shared = st.get("shared_ids") or {}
    if shared:
        listed, cut = shared_ids_text(shared, STATUS_PLACES)
        if cut:
            listed += " (the kit's `status` command lists every line in its JSON `shared_ids`)"
        out.append(f"ids on several lines: {listed} — each line is its own record, and a reference "
                   "to the id is ambiguous")
    return out


def dataset_origin(ds: dict) -> str:
    """Where a dataset's current bytes come from, as far as the records say (ADR-0006 4): the runs
    whose recorded output had this path and these bytes, the file it was imported from, or neither.
    A run is named as having output the same bytes, not as having made them."""
    state = ds.get("producer_state")
    candidates = ds.get("producer_candidates") or []
    if state == "resolved":
        return f"same bytes as run {candidates[0]}'s output"
    if state == "ambiguous":
        listed = _places(candidates, STATUS_CANDIDATES,
                         "the kit's `status` command lists every one in its JSON `producer_candidates`")
        return f"same bytes as {len(candidates):,} runs: {listed}"
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
                f"not succeed); runs that wrote this path, latest first: {listed}")
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
    events = read_events(project)
    resolved = resolved_targets(events)
    preamble, included, omitted, _ = context_selection(
        context_file_text(project), max_context_chars, path=project.context)
    key = [e for e in events if e.get("kind") in KEY_KINDS and e["id"] not in resolved]
    open_items = [e for e in events if e.get("kind") in OPEN_KINDS and e["id"] not in resolved]
    parts = [
        "# Active analytics project",
        f"root: {project.root} (title: {project.title()})",
        f"report: {project.report / 'index.html'} (rebuilt after runs, imports and notes)",
        "Call analytics_status for datasets, runs, charts and the recent journal.",
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
    if key:
        parts += ["", "## Key information (unresolved journal entries)"]
        parts += _journal_entries(key, max_items, project.journal, refs=True)
    if open_items:
        parts += ["", "## Open questions and todos"]
        parts += _journal_entries(open_items, max_items, project.journal)
    return "\n".join(parts)


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


def init_project(target, *, title=None, goal=None, template_dir=None, actor="human", session=None,
                 platform: _platform.Platform | None = None) -> Project:
    """`template_dir` is a path or an importlib.resources Traversable. Left out, it is the template
    next to this file: the aelix process loads this module by path, where the resource API cannot
    work (ADR-0004), and the kit's own package data is where this file is. The CLI hands over the
    same template through the resource API."""
    root = Path(target).expanduser()
    if root.exists():
        if not root.is_dir():
            raise LabError("init_not_directory", f"{root} exists and is not a directory")
        leftovers = [p.name for p in root.iterdir() if p.name not in (".DS_Store",)]
        if leftovers:
            raise LabError("init_not_empty", f"{root} is not empty",
                           hint="choose a new directory; init never adopts existing files",
                           details={"entries": sorted(leftovers)[:10]})
    # Read before anything is created: a kit installed without its template must not leave a
    # half-made project that a second init then refuses as not empty.
    if template_dir is None:
        template_dir = Path(__file__).resolve().parent / "template"
    template = Path(template_dir) if isinstance(template_dir, (str, os.PathLike)) else template_dir
    try:
        texts = {src: (template / src).read_text(encoding="utf-8") for src in TEMPLATE_FILES}
    except OSError as exc:
        raise LabError("template_missing", f"the project template cannot be read: {exc}",
                       hint="reinstall aelix-analytics; in a checkout, restore src/aelix_analytics/template"
                       ) from exc
    root.mkdir(parents=True, exist_ok=True)
    project = Project(root, platform=platform)
    title = (title or "").strip() or project.root.name
    for d in (project.raw, project.derived, project.scripts, project.runs, project.report,
              project.context_history, project.cache):
        d.mkdir(parents=True, exist_ok=True)
    created = iso()
    write_json(project.marker, {"schema": SCHEMA_PROJECT, "title": title, "created_at": created,
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
    append_event(project, "project", f"project created: {title}", actor=actor, session=session)
    sync_context(project, actor=actor, session=session)
    with contextlib.suppress(OSError):
        trust_project(project.root, platform=project.platform)
    return project
