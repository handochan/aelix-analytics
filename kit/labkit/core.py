"""Project model for aelix-analytics-baseline lab projects. Standard library only.

The aelix extension loads this file by path inside the aelix process, so it must not import polars,
duckdb, plotting libraries or other labkit modules. Code that needs those runs inside a project's own
uv environment (runner, data, report).
"""

from __future__ import annotations

import contextlib
import difflib
import fcntl
import hashlib
import json
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path

KIT_VERSION = "0.1.0"
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

# Directories that are never data inputs or outputs of a run.
IGNORED_DIRS = {".venv", ".lab", "runs", "report", "scripts", ".git", "__pycache__",
                ".pytest_cache", ".ruff_cache", ".omc", ".aelix", "node_modules",
                ".ipynb_checkpoints"}


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


class Project:
    """Paths of one lab project. Construction does not touch the file system."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
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
        p = Path(rel).expanduser()
        return p if p.is_absolute() else self.root / p

    def rel(self, path) -> str:
        """Project-relative POSIX path, or the absolute path for files outside the project."""
        p = self.path(path)
        with contextlib.suppress(OSError):
            p = p.resolve()
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return str(p)

    def inside(self, path) -> bool:
        p = self.path(path)
        with contextlib.suppress(OSError):
            p = p.resolve()
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


def write_json(path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")
    os.replace(tmp, path)


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
                write_json(self.file, self.data)
            self.dirty = False


@contextlib.contextmanager
def project_lock(project: Project):
    """Exclusive inter-process lock for journal and context-history writes. Not re-entrant."""
    project.lab.mkdir(parents=True, exist_ok=True)
    with open(project.lab / "lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@contextlib.contextmanager
def run_lock(project: Project):
    """Serializes runs of one project (see runner.execute)."""
    project.lab.mkdir(parents=True, exist_ok=True)
    with open(project.lab / "run.lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


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


def trust_project(root) -> None:
    """Remember a project the user created or chose, so starting aelix inside it activates it."""
    resolved = str(Path(root).expanduser().resolve())
    roots = trusted_roots()
    if resolved in roots:
        return
    write_json(trust_file(), {"schema": "lab.trust/1", "projects": sorted(roots | {resolved})})


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

def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


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
        if rec.get("status") == "running" and not _pid_alive(rec.get("pid")):
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


def status(project: Project, limit: int = 8) -> dict:
    events = read_events(project)
    runs = list_runs(project)
    datasets = list_datasets(project, runs)
    cache = HashCache(project)
    fresh = Freshness(project, runs, cache)
    charts = chart_index(runs)
    resolved = resolved_targets(events)
    context_text = (project.context.read_text(encoding="utf-8", errors="replace")
                    if project.context.is_file() else "")
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
                    "goal": context_goal(context_text),
                    "context_chars": len(context_text),
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


def status_text(st: dict, max_chars: int = 9000) -> str:
    p, counts = st["project"], st["counts"]
    lines = [f"project: {p['title']} ({p['root']})", f"report: {st['report']}"]
    if p.get("goal"):
        lines.append(f"goal: {_one_line(p['goal'])}")
    if st.get("versions"):
        lines.append(f"environment: {versions_line(st['versions'])} — use APIs of these versions")
    for schema, count in sorted((st.get("unreadable") or {}).items()):
        lines.append(f"{count} record(s) written as {schema} are skipped everywhere, including the "
                     "report — this kit reads version 1; update the lab kit to read them")
    if p.get("context_truncated"):
        lines.append(f"CONTEXT.md has {p['context_chars']:,} characters above its first `## ` "
                     f"heading, and only {CONTEXT_PROMPT_CHARS:,} reach the system prompt — split "
                     "it into `## ` sections so the rest can be read on demand")
    if p.get("context_omitted"):
        lines.append("CONTEXT.md sections not in the system prompt (read them from the file when "
                     f"they matter): {', '.join(p['context_omitted'])} — move a section earlier or "
                     "mark its heading <!-- 항상 --> to pin it")
    lines.append(f"datasets ({counts['datasets']}) — query views: "
                 + (", ".join(st["views"]) or "none"))
    for ds in st["datasets"]:
        origin = (f"← run {ds['producer']}" if ds.get("producer")
                  else f"from {ds['source']}" if ds.get("source")
                  else "NO PRODUCING RUN (written outside lab_run: no code or inputs recorded)")
        lines.append(f"  {ds['kind']:<7} {ds['name']}: {_fmt_int(ds.get('rows'))} rows × "
                     f"{ds.get('columns') or '?'} cols · {ds['path']} · {origin} · used by "
                     f"{len(ds.get('used_by') or [])} run(s)")
    lines.append(f"runs ({counts['runs']} total, {counts['runs_failed']} not ok; latest first):")
    for r in st["runs"]:
        extra = f" — {_one_line(r['error'], 100)}" if r.get("error") else ""
        if r.get("rerun_of"):
            extra += f" [rerun of {r['rerun_of']}: {r.get('verdict')}]"
        dur = f"{r['duration_s']:.1f}s" if isinstance(r.get("duration_s"), (int, float)) else "-"
        lines.append(f"  {r['id']} {r.get('status')} {dur} charts={r['charts']} "
                     f"\"{_one_line(r.get('title'), 80)}\"{extra}")
    lines.append(f"charts (latest version per script::chart, {counts['charts']}):")
    for c in st["charts"]:
        flags = []
        if c["stale"]:
            flags.append("STALE(" + ", ".join(c["changed_inputs"] + [
                f"upstream {u}" for u in c["stale_upstream"]]) + ")")
        if c["code"] == "changed":
            flags.append("script edited since")
        elif c["code"] == "missing":
            flags.append("script file no longer exists")
        if not c["has_data"] and c.get("kind") == "matplotlib":
            flags.append("plotted data not recorded")
        lines.append(f"  {c['key']} \"{_one_line(c.get('title'), 70)}\" run {c['run']} "
                     f"v{c['versions']}" + (" · " + "; ".join(flags) if flags else ""))
    if st["open"]:
        lines.append(f"open questions/todos ({counts['open']}):")
        lines += [f"  {e['id']} {e['kind']}: {_one_line(e['text'])}" for e in st["open"]]
    if st["key"]:
        lines.append("key information (findings, decisions, assumptions):")
        lines += [f"  {e['id']} {e['kind']}: {_one_line(e['text'])}" for e in st["key"]]
    lines.append("recent journal:")
    lines += [f"  {e['id']} {str(e.get('ts', ''))[:16].replace('T', ' ')} {e['kind']}: "
              f"{_one_line(e['text'], 120)}" for e in st["recent"]]
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars - 40] + "\n… (truncated)"


PIN_RE = re.compile(r"<!--\s*(항상|always)\s*-->", re.IGNORECASE)


def context_sections(text: str) -> tuple[str, list[dict]]:
    """CONTEXT.md split at `## ` headings: (preamble, sections with 1-based line numbers)."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    preamble = "\n".join(lines[:starts[0]] if starts else lines).strip()
    sections = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        heading = lines[start][3:].strip()
        body = "\n".join(lines[start:end]).rstrip()
        sections.append({"name": _one_line(PIN_RE.sub("", heading), 48) or f"섹션 {n + 1}",
                         "pinned": bool(PIN_RE.search(heading)), "line": start + 1,
                         "lines": end - start, "chars": len(body), "text": body})
    return preamble, sections


def context_selection(text: str,
                      budget: int = CONTEXT_PROMPT_CHARS) -> tuple[str, list, list, bool]:
    """Which CONTEXT.md sections go into the system prompt: pinned ones, then as many as the
    budget allows in file order. The rest are listed with their line ranges so the agent can
    read exactly the one it needs — context is never hidden, only fetched on demand.

    Returns (preamble, included, omitted, preamble_truncated). Text above the first `## ` heading
    cannot be fetched by section, so an oversized one is the only thing ever cut."""
    preamble, sections = context_sections(text)
    truncated = len(preamble) > budget
    if truncated:
        preamble = preamble[:budget].rstrip() + (
            f"\n… (머리말이 {budget:,}자를 넘어 잘렸습니다 — `## 제목`으로 섹션을 나누면 "
            "필요한 부분만 읽어 갈 수 있습니다)")
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


def context_block(project: Project, max_context_chars: int = CONTEXT_PROMPT_CHARS,
                  max_items: int = 12) -> str:
    """Project context appended to the system prompt. Only slow-changing parts, so it stays
    stable across prompts: CONTEXT.md, unresolved key information and open questions."""
    events = read_events(project)
    resolved = resolved_targets(events)
    context_text = ""
    if project.context.is_file():
        context_text = project.context.read_text(encoding="utf-8", errors="replace").strip()
    preamble, included, omitted, _ = context_selection(context_text, max_context_chars)
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
        parts += [f"- {e['id']} {e['kind']}: {_one_line(e['text'], 300)}"
                  + _refs_suffix(e) for e in key[-max_items:]]
    if open_items:
        parts += ["", "## Open questions and todos"]
        parts += [f"- {e['id']} {e['kind']}: {_one_line(e['text'], 300)}"
                  for e in open_items[-max_items:]]
    return "\n".join(parts)


def _refs_suffix(event: dict) -> str:
    runs = (event.get("refs") or {}).get("runs")
    return f" (runs: {', '.join(runs)})" if runs else ""


# --- project creation ---

def init_project(target, *, title=None, goal=None, template_dir, actor="human",
                 session=None) -> Project:
    root = Path(target).expanduser()
    if root.exists():
        if not root.is_dir():
            raise LabError("init_not_directory", f"{root} exists and is not a directory")
        leftovers = [p.name for p in root.iterdir() if p.name not in (".DS_Store",)]
        if leftovers:
            raise LabError("init_not_empty", f"{root} is not empty",
                           hint="choose a new directory; init never adopts existing files",
                           details={"entries": sorted(leftovers)[:10]})
    root.mkdir(parents=True, exist_ok=True)
    project = Project(root)
    title = (title or "").strip() or project.root.name
    template_dir = Path(template_dir)
    for d in (project.raw, project.derived, project.scripts, project.runs, project.report,
              project.context_history, project.cache):
        d.mkdir(parents=True, exist_ok=True)
    created = iso()
    write_json(project.marker, {"schema": SCHEMA_PROJECT, "title": title, "created_at": created,
                                "kit_version": KIT_VERSION})
    fill = {"title": title, "date": created[:10],
            "goal": (goal or "").strip() or "- (이 분석으로 답하려는 질문과, 결과를 쓸 사람/의사결정)",
            "slug": slugify(title, 40) or "analysis"}
    for src, dest in (("CONTEXT.md", project.context), ("pyproject.toml", "pyproject.toml"),
                      ("gitignore", ".gitignore")):
        text = (template_dir / src).read_text(encoding="utf-8")
        for k, v in fill.items():
            text = text.replace("{{" + k + "}}", v)
        project.path(dest).write_text(text, encoding="utf-8")
    project.path(".python-version").write_text("3.12\n", encoding="utf-8")
    project.journal.touch()
    append_event(project, "project", f"project created: {title}", actor=actor, session=session)
    sync_context(project, actor=actor, session=session)
    with contextlib.suppress(OSError):
        trust_project(project.root)
    return project
