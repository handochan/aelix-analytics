"""Project model for Aelix Analytics projects. Standard library only.

The aelix extension loads this file inside the aelix process, so it must not import polars, duckdb,
plotting libraries or any other aelix_analytics module except `_platform`, which is standard library only for
the same reason. Code that needs the analysis libraries runs inside a project's own uv environment
(runner, data, report).

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

from . import _platform

KIT_VERSION = "0.2.0"
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
    """Paths of one lab project, and the platform ports its operations use.

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
                           hint="a newer version of the lab kit made this project; update the kit "
                                "instead of reading it with this one")
        return data

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


def write_json(path, obj, *, platform: _platform.Platform | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
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
    with open(path, "a", encoding="utf-8") as fh:
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


class HashCache:
    """sha256 of files keyed by (size, mtime_ns), persisted in .lab/cache/hashes.json."""

    def __init__(self, project: Project):
        self.file = project.cache / "hashes.json"
        self.platform = project.platform
        data = read_json(self.file, {})
        self.data = data if isinstance(data, dict) else {}
        self.dirty = False

    def sha(self, path) -> str | None:
        path = Path(path)
        try:
            st = path.stat()
        except OSError:
            return None
        if not path.is_file():
            return None
        key = str(path)
        entry = self.data.get(key)
        if isinstance(entry, list) and len(entry) == 3 and entry[0] == st.st_size \
                and entry[1] == st.st_mtime_ns:
            return entry[2]
        try:
            digest = sha256_file(path)
        except OSError:
            return None
        self.data[key] = [st.st_size, st.st_mtime_ns, digest]
        self.dirty = True
        return digest

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
    explicit = os.environ.get("AELIX_LAB_TRUST_FILE")
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


def read_events(project: Project) -> list[dict]:
    return [e for e in read_jsonl(project.journal) if e.get("schema") == SCHEMA_EVENT]


def unreadable_records(project: Project) -> dict[str, int]:
    """Records this kit version skipped, by schema — journal entries and runs from a newer kit."""
    counts: dict[str, int] = {}
    for event in read_jsonl(project.journal):
        if newer_schema(event.get("schema"), SCHEMA_EVENT):
            counts[event["schema"]] = counts.get(event["schema"], 0) + 1
    if project.runs.is_dir():
        for d in sorted(project.runs.iterdir()):
            record = read_json(d / "run.json") if d.is_dir() else None
            if isinstance(record, dict) and newer_schema(record.get("schema"), SCHEMA_RUN):
                counts[record["schema"]] = counts.get(record["schema"], 0) + 1
    return counts


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
                               hint="lab_status lists open questions and todos with their ids")
            ref_map["resolves"] = resolves
        return _append_event_locked(project, kind, text, ref_map, actor, session)


# --- CONTEXT.md history ---

def context_versions(project: Project) -> list[Path]:
    if not project.context_history.is_dir():
        return []
    return sorted(project.context_history.glob("*.md"))


def sync_context(project: Project, actor="system", session=None) -> dict | None:
    """Snapshot CONTEXT.md into .lab/context/ and log a `context` event when it changed."""
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
            old = versions[-1].read_text(encoding="utf-8", errors="replace").splitlines()
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
    """First content line under a Goal/목표 heading, else the first paragraph line."""
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
            continue  # written by a newer kit; unreadable_records() reports it
        if not isinstance(rec, dict):
            rec = {"id": d.name, "status": "incomplete", "title": "(run.json missing)"}
        rec["id"] = d.name
        if rec.get("status") == "running" \
                and not project.platform.processes.alive(rec.get("pid")):
            rec["status"] = "incomplete"
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
    """Whether a run's recorded inputs and code still match the files on disk (transitively)."""

    def __init__(self, project: Project, runs: list[dict], cache: HashCache | None = None):
        self.project = project
        self.cache = cache or HashCache(project)
        self.runs = {r["id"]: r for r in runs}
        self.producers: dict[tuple[str, str], str] = {}
        for r in runs:
            if r.get("status") != "ok":
                continue
            for out in r.get("outputs") or []:
                if out.get("sha256"):
                    self.producers[(out["path"], out["sha256"])] = r["id"]
        self._memo: dict[str, dict] = {}

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

    def of(self, run_id: str, _depth: int = 0) -> dict:
        if run_id in self._memo:
            return self._memo[run_id]
        run = self.runs.get(run_id) or {}
        inputs, upstream = [], []
        own = {o.get("path"): o.get("sha256") for o in run.get("outputs") or []
               if o.get("change") != "deleted"}
        for entry in run.get("inputs") or []:
            state = self.input_state(entry, own)
            inputs.append({"path": entry.get("path"), "state": state,
                           "sha256": entry.get("sha256")})
            producer = self.producers.get((entry.get("path"), entry.get("sha256")))
            if producer and producer != run_id and _depth < 20:
                if self.of(producer, _depth + 1)["stale"]:
                    upstream.append(producer)
        direct = [i["path"] for i in inputs if i["state"] in ("changed", "missing")]
        result = {"inputs": inputs, "changed_inputs": direct, "stale_upstream": upstream,
                  "code": self.code_state(run), "stale": bool(direct or upstream)}
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

def list_datasets(project: Project, runs: list[dict] | None = None) -> list[dict]:
    runs = list_runs(project) if runs is None else runs
    produced: dict[str, list[dict]] = {}
    used: dict[str, list[str]] = {}
    for r in runs:
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
            if isinstance(meta, dict):
                item.update(rows=meta.get("rows"), columns=len(meta.get("columns") or []),
                            sha256=meta.get("sha256"), description=meta.get("description"),
                            source=(meta.get("source") or {}).get("path"),
                            imported_at=meta.get("imported_at"))
            history = produced.get(rel)
            if history:
                last = history[-1]
                item["producer"] = last["run"]
                item["producers"] = [h["run"] for h in history]
                item.setdefault("rows", last.get("rows"))
                item.setdefault("columns", len(last.get("schema") or []) or None)
                item.setdefault("sha256", last.get("sha256"))
            item["used_by"] = sorted(set(used.get(rel, [])))
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
    events = read_events(project)
    runs = list_runs(project)
    datasets = list_datasets(project, runs)
    cache = HashCache(project)
    fresh = Freshness(project, runs, cache)
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
                           "stale_upstream": f["stale_upstream"], "code": f["code"]})
    run_rows = []
    for r in runs[-limit:][::-1]:
        run_rows.append({"id": r["id"], "status": r.get("status"), "title": r.get("title"),
                         "duration_s": r.get("duration_s"), "charts": len(r.get("charts") or []),
                         "error": (r.get("error") or {}).get("summary"),
                         "rerun_of": r.get("rerun_of"),
                         "verdict": (r.get("reproduction") or {}).get("verdict")})
    open_items = [e for e in events if e.get("kind") in OPEN_KINDS and e["id"] not in resolved]
    key_items = [e for e in events if e.get("kind") in KEY_KINDS and e["id"] not in resolved]
    gaps = [e for e in events if e.get("kind") == "gap"]
    cache.save()
    versions = context_versions(project)
    return {
        "project": {"root": str(project.root), "title": project.title(),
                    "created_at": project.config().get("created_at"),
                    "goal": context_goal(_newlines(context_text)),
                    "context_chars": len(_newlines(context_text)),
                    "context_omitted": [s["name"] for s in context_omitted],
                    "context_truncated": context_truncated,
                    "context_updated": _version_time(versions[-1]) if versions else None},
        "report": str(project.report / "index.html"),
        "versions": locked_versions(project),
        "counts": {"datasets": len(datasets), "runs": len(runs),
                   "runs_failed": sum(1 for r in runs if r.get("status") != "ok"),
                   "charts": len(current_charts), "events": len(events),
                   "open": len(open_items), "gaps": len(gaps)},
        "unreadable": unreadable_records(project),
        "datasets": datasets,
        "views": view_names(datasets),
        "runs": run_rows,
        "charts": chart_rows,
        "open": [_brief(e) for e in open_items[-limit * 2:]],
        "key": [_brief(e) for e in key_items[-limit * 2:]],
        "recent": [_brief(e) for e in events[-10:]],
    }


def _version_time(path: Path) -> str | None:
    try:
        return datetime.strptime(path.stem.split("_")[0], "%Y%m%d-%H%M%S-%f").isoformat(
            timespec="seconds")
    except ValueError:
        return None


def _brief(event: dict) -> dict:
    return {"id": event.get("id"), "ts": event.get("ts"), "kind": event.get("kind"),
            "actor": event.get("actor"), "text": event.get("text"),
            "refs": event.get("refs") or {}}


def _one_line(text, width=160) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= width else s[:width - 1] + "…"


def _fmt_int(value) -> str:
    return f"{value:,}" if isinstance(value, int) else "?"


# How long lab_status's overview may be (the extension's reply adds its first line), not counting
# the lines that only say what a list leaves out and where it is (and the journal's path): they are
# budgeted apart, a few short lines at most, and never make a list shorter.
STATUS_CHARS = 9000
# The fewest entries an overview longer than STATUS_CHARS keeps of each list that has no limit of
# its own: the CONTEXT.md sections left out of the system prompt, the query views, the datasets,
# and the changed inputs of a chart.
STATUS_FLOORS = {"context": 20, "views": 40, "datasets": 20, "stale": 5}
# How to list every dataset view with lab_query: by name, a page of 50 (its default limit) at a
# time.
VIEWS_SQL = "SELECT table_name FROM information_schema.tables ORDER BY table_name LIMIT 50 OFFSET 0"
VIEWS_PAGES = (f"call lab_query with \"{VIEWS_SQL}\" and again with OFFSET 50, 100, … until it "
               "returns fewer than 50 rows")
STATUS_PARTS = {"head": "lines about the project", "datasets": "datasets", "runs": "runs",
                "charts": "charts", "open": "open questions/todos", "key": "key information",
                "recent": "recent journal"}


def status_text(st: dict, max_chars: int = STATUS_CHARS) -> str:
    """The overview lab_status replies with. Each list is whole, or says how many it leaves out and
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
    if older_runs > 0 or more_charts > 0 or older_open > 0 or key_at_limit or earlier > 0:
        # Where the entries the lists below leave out are: said once, and the lists refer to it.
        add("head", f"journal: {journal}", note=True)
    if p.get("goal"):
        add("head", f"goal: {_one_line(p['goal'])}")
    if st.get("versions"):
        add("head", f"environment: {versions_line(st['versions'])} — use APIs of these versions")
    for schema, count in sorted((st.get("unreadable") or {}).items()):
        add("head", f"{count} record(s) written as {schema} are skipped everywhere, including the "
                    "report — this kit reads version 1; update the lab kit to read them")
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
                    "<!-- 항상 --> to pin it")
    views = list(st["views"])
    shown_views = _capped(views, caps.get("views"))
    view_text = ", ".join(shown_views) or "none"
    if len(shown_views) < len(views):
        view_text += (f" and {len(views) - len(shown_views):,} more ({VIEWS_PAGES} to list every "
                      "view)")
    add("datasets", f"datasets ({counts['datasets']}) — query views: {view_text}")
    datasets = _capped(st["datasets"], caps.get("datasets"))
    for ds in datasets:
        origin = (f"← run {ds['producer']}" if ds.get("producer")
                  else f"from {ds['source']}" if ds.get("source")
                  else "NO PRODUCING RUN (written outside lab_run: no code or inputs recorded)")
        add("datasets", f"  {ds['kind']:<7} {ds['name']}: {_fmt_int(ds.get('rows'))} rows × "
                        f"{ds.get('columns') or '?'} cols · {ds['path']} · {origin} · used by "
                        f"{len(ds.get('used_by') or [])} run(s)")
    if len(datasets) < len(st["datasets"]):
        add("datasets", f"  … {len(st['datasets']) - len(datasets):,} more dataset(s) not listed: "
                        f"{VIEWS_PAGES} to list every view; an imported one's record is "
                        "data/raw/<name>.meta.json")
    add("runs", f"runs ({counts['runs']} total, {counts['runs_failed']} not ok; latest first):")
    for r in st["runs"]:
        extra = f" — {_one_line(r['error'], 100)}" if r.get("error") else ""
        if r.get("rerun_of"):
            extra += f" [rerun of {r['rerun_of']}: {r.get('verdict')}]"
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
            changed = c["changed_inputs"] + [f"upstream {u}" for u in c["stale_upstream"]]
            shown = _capped(changed, caps.get("stale"))
            more = ""
            if len(shown) < len(changed):
                more = (f" and {len(changed) - len(shown):,} more, among the run's inputs in "
                        f"runs/{c['run']}/run.json")
            flags.append("STALE(" + ", ".join(shown) + more + ")")
        if c["code"] == "changed":
            flags.append("script edited since")
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
            add("open", f"  {e['id']} {e['kind']}: {_one_line(e['text'])}")
        if older_open > 0:
            add("open", f"  … {older_open:,} older open item(s) not listed: in the journal",
                note=True)
    if st["key"]:
        add("key", "key information (findings, decisions, assumptions):")
        for e in st["key"]:
            add("key", f"  {e['id']} {e['kind']}: {_one_line(e['text'])}")
        if key_at_limit:
            add("key", f"  … only the latest {len(st['key'])} are listed; older unresolved ones, if "
                       "any, are in the journal", note=True)
    add("recent", "recent journal:")
    for e in st["recent"]:
        add("recent", f"  {e['id']} {str(e.get('ts', ''))[:16].replace('T', ' ')} {e['kind']}: "
                      f"{_one_line(e['text'], 120)}")
    if earlier > 0:
        add("recent", f"  … {earlier:,} earlier entries not listed: in the journal", note=True)
    return out


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


PIN_RE = re.compile(r"<!--\s*(항상|always)\s*-->", re.IGNORECASE)


def context_file_text(project: Project) -> str:
    """CONTEXT.md as aelix's read tool reads it: its bytes as UTF-8 (undecodable bytes replaced) and
    its line ends as they are. Text mode would turn a CRLF and a lone CR into "\\n", and number the
    lines differently from the read tool."""
    if not project.context.is_file():
        return ""
    return project.context.read_bytes().decode("utf-8", errors="replace")


def _newlines(text: str) -> str:
    """`text` with its CRLF and lone CR line ends as "\\n", as text mode reads a file."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


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
        sections.append({"name": _one_line(PIN_RE.sub("", heading), 48) or f"섹션 {n + 1}",
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
        "# Active analysis lab project",
        f"root: {project.root} (title: {project.title()})",
        f"report: {project.report / 'index.html'} (rebuilt after runs, imports and notes)",
        "Call lab_status for datasets, runs, charts and the recent journal.",
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

TEMPLATE_FILES = ("CONTEXT.md", "pyproject.toml", "gitignore")


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
    fill = {"title": title, "date": created[:10],
            "goal": (goal or "").strip() or "- (이 분석으로 답하려는 질문과, 결과를 쓸 사람/의사결정)",
            "slug": slugify(title, 40) or "analysis"}
    for src, dest in (("CONTEXT.md", project.context), ("pyproject.toml", "pyproject.toml"),
                      ("gitignore", ".gitignore")):
        text = texts[src]
        for k, v in fill.items():
            text = text.replace("{{" + k + "}}", v)
        project.path(dest).write_text(text, encoding="utf-8")
    project.path(".python-version").write_text("3.12\n", encoding="utf-8")
    project.journal.touch()
    append_event(project, "project", f"project created: {title}", actor=actor, session=session)
    sync_context(project, actor=actor, session=session)
    with contextlib.suppress(OSError):
        trust_project(project.root, platform=project.platform)
    return project
