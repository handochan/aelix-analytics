"""Run lifecycle: snapshot the code, execute it with capture hooks, record provenance.

A run executes the exact bytes it snapshots (compiled under the script's own path, so tracebacks and
`__file__` stay natural). Inputs come from four sources, each recorded in `via`: runtime hooks
(Polars/DuckDB/labkit reads), a static scan of path literals in the code, and inputs the caller
declared. Outputs are every file under the project that the run created or modified (outside the
code, run and report directories). This is a record, not a sandbox or a determinism guarantee.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import io
import os
import platform
import re
import shutil
import sys
import time
import traceback
import types
from pathlib import Path

from . import _platform, capture, core

WRITE_CALLS = {"write_parquet", "sink_parquet", "write_csv", "sink_csv", "write_ipc", "sink_ipc",
               "write_ndjson", "sink_ndjson", "write_json", "write_excel", "write_delta",
               "write_database", "to_parquet", "to_csv", "to_json", "to_excel", "savefig",
               "write_html", "write_image", "save", "copy", "copyfile", "copy2", "move", "rename",
               "replace", "write_text", "write_bytes", "mkdir", "touch", "unlink", "rmtree", "chart"}
PROJECT_FILES = {"lab.json", "journal.jsonl", "CONTEXT.md", "pyproject.toml", "uv.lock",
                 ".python-version", ".gitignore"}
PACKAGES = ("duckdb", "polars", "pyarrow", "matplotlib", "plotly", "numpy", "pygments",
            "markdown-it-py")
OUTPUT_TAIL = 4000


def scan_literals(source: str) -> tuple[set[str], set[str]]:
    """String literals of the code, split into (read candidates, write targets)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set(), set()
    in_write = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in WRITE_CALLS:
                in_write.update(id(sub) for sub in ast.walk(node)
                                if isinstance(sub, ast.Constant) and isinstance(sub.value, str))
    reads, writes = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            (writes if id(node) in in_write else reads).add(node.value)
    for literal in list(reads):
        for target in capture.SQL_COPY_TO_RE.findall(literal):
            writes.add(target)
    return reads, writes


def static_input_files(project: core.Project, literals: set[str], writes: set[str]) -> list[Path]:
    import glob

    found: dict[str, Path] = {}
    for literal in literals:
        tokens = {literal, *capture.SQL_FILE_RE.findall(literal)}
        for token in tokens:
            if len(token) > 400 or "\n" in token or token in writes:
                continue
            if "/" not in token and not token.lower().endswith(core.DATA_SUFFIXES):
                continue
            path = Path(token).expanduser()
            path = path if path.is_absolute() else project.root / path
            text = str(path)
            candidates = [Path(m) for m in glob.glob(text, recursive=True)[:200]] \
                if any(ch in text for ch in "*?[") else [path]
            for c in candidates:
                if c.is_file() and c.suffix.lower() in core.DATA_SUFFIXES:
                    found[str(c)] = c
    return list(found.values())


def tree_stat(project: core.Project) -> dict[str, tuple[int, int]]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(project.root):
        if Path(dirpath) == project.root:
            dirnames[:] = [d for d in dirnames if d not in core.IGNORED_DIRS]
        else:
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".ipynb_checkpoints")]
        for name in filenames:
            path = os.path.join(dirpath, name)
            with contextlib.suppress(OSError):
                st = os.stat(path)
                out[os.path.relpath(path, project.root)] = (st.st_size, st.st_mtime_ns)
    return out


def parquet_info(path: Path) -> dict:
    with contextlib.suppress(Exception):
        import polars as pl

        lf = pl.scan_parquet(path)
        schema = lf.collect_schema()
        return {"rows": lf.select(pl.len()).collect().item(),
                "schema": [[name, str(dtype)] for name, dtype in schema.items()]}
    return {}


def diff_outputs(project, before, after, cache) -> list[dict]:
    outputs = []
    for rel, st in sorted(after.items()):
        if rel in PROJECT_FILES or before.get(rel) == st:
            continue
        path = project.root / rel
        entry = {"path": Path(rel).as_posix(), "change": "created" if rel not in before else "modified",
                 "bytes": st[0], "sha256": cache.sha(path)}
        if path.suffix.lower() == ".parquet":
            entry.update(parquet_info(path))
        if not rel.startswith("data" + os.sep):
            entry["outside_data"] = True
        outputs.append(entry)
    for rel in sorted(set(before) - set(after)):
        if rel not in PROJECT_FILES:
            outputs.append({"path": Path(rel).as_posix(), "change": "deleted"})
    return outputs


_KIT_SHA = None


def kit_sha() -> str:
    global _KIT_SHA
    if _KIT_SHA is None:
        import hashlib

        digest = hashlib.sha256()
        for p in sorted(Path(__file__).parent.rglob("*")):
            if p.is_file() and p.suffix in (".py", ".css", ".js", ".md", ".toml") \
                    and "__pycache__" not in p.parts:
                digest.update(p.name.encode())
                digest.update(p.read_bytes())
        _KIT_SHA = digest.hexdigest()
    return _KIT_SHA


def environment(project: core.Project) -> dict:
    from importlib import metadata

    packages = {}
    for name in PACKAGES:
        with contextlib.suppress(metadata.PackageNotFoundError):
            packages[name] = metadata.version(name)
    lock = project.root / "uv.lock"
    return {"python": platform.python_version(), "executable": sys.executable,
            "platform": platform.platform(), "packages": packages,
            "uv_lock_sha256": core.sha256_file(lock) if lock.is_file() else None,
            "kit_version": core.KIT_VERSION, "kit_sha256": kit_sha()}


def _tail(path: Path, limit: int = OUTPUT_TAIL) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - limit))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return ("…" + text) if size > limit else text


@contextlib.contextmanager
def _redirected(stdout_path: Path, stderr_path: Path):
    """Send this process's output, and that of anything it starts, to the run's log files.

    The descriptors are redirected so a C library or a child process lands in the same file, and
    the Python-level streams are rebound on top of them. A console stream keeps writing to the
    console it was built for: on Windows `sys.stdout` can wrap a _WindowsConsoleIO that calls
    WriteConsoleW, which fails once fd 1 points at a file. Rebinding also fixes the encoding of a
    run's own log, which would otherwise follow the locale."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved_fds = os.dup(1), os.dup(2)
    saved_streams = sys.stdout, sys.stderr
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        os.dup2(out.fileno(), 1)
        os.dup2(err.fileno(), 2)
        sys.stdout = io.TextIOWrapper(io.FileIO(1, "w", closefd=False), line_buffering=True,
                                      encoding=_platform.TEXT_ENCODING, errors="replace")
        sys.stderr = io.TextIOWrapper(io.FileIO(2, "w", closefd=False), line_buffering=True,
                                      encoding=_platform.TEXT_ENCODING, errors="replace")
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                sys.stdout.flush()
                sys.stderr.flush()
            sys.stdout, sys.stderr = saved_streams
            os.dup2(saved_fds[0], 1)
            os.dup2(saved_fds[1], 2)
            os.close(saved_fds[0])
            os.close(saved_fds[1])


def _exec_script(code_bytes: bytes, filename: Path, script_dir: Path) -> None:
    module = types.ModuleType("__main__")
    module.__file__ = str(filename)
    module.__builtins__ = builtins  # type: ignore[attr-defined]
    saved_main, saved_argv, saved_path = sys.modules.get("__main__"), sys.argv[:], sys.path[:]
    saved_streams = sys.stdout, sys.stderr
    sys.modules["__main__"] = module
    sys.argv = [str(filename)]
    sys.path.insert(0, str(script_dir))
    try:
        exec(compile(code_bytes, str(filename), "exec", dont_inherit=True), module.__dict__)
    finally:
        if saved_main is not None:
            sys.modules["__main__"] = saved_main
        sys.argv = saved_argv
        sys.path[:] = saved_path
        # A script may replace sys.stdout/stderr; the CLI result must still reach the real stdout.
        sys.stdout, sys.stderr = saved_streams


# Failures seen in real sessions: a model reaches for pandas habits or a Polars API that moved.
# The version line alone does not stop this, so the failure says what to write instead.
API_HINTS = (
    (r"No module named 'pandas'",
     "this project has no pandas: process data with Polars expressions or DuckDB SQL "
     "(lab.sql(...).pl()); .to_pandas() exists only if you add pandas to the project"),
    (r"'DataFrame' object has no attribute 'to_string'",
     "Polars prints itself: print(df), or df.glimpse() for a column summary"),
    (r"'GroupBy' object has no attribute 'pivot'",
     "pivot on the DataFrame, not the GroupBy: df.group_by([a, b]).agg(...).pivot("
     "on=..., index=..., values=...)"),
    (r"'Expr' object has no attribute 'mask'",
     "Polars has no Expr.mask: use pl.when(cond).then(x).otherwise(y)"),
    (r"'Series' object has no attribute 'map'",
     "Polars renamed it: Series.map_elements(fn, return_dtype=...), or "
     "replace_strict(mapping) for a lookup table"),
    (r"'DataFrame' object has no attribute '(apply|iterrows|to_dict|melt|append)'",
     "that is a pandas method; the Polars equivalents are map_rows / iter_rows / to_dicts / "
     "unpivot / concat"),
    (r"'(float|int|str|bool)' object has no attribute 'alias'",
     "operator precedence: .alias() bound to the literal, not the expression — wrap it, "
     "e.g. (100.0 * pl.col('a') / pl.col('b')).alias('pct')"),
    (r"\bcount\b.*deprecated|pl\.count\(\)",
     "pl.count() is deprecated: pl.len() counts rows, pl.col(x).count() counts non-nulls"),
)


def api_hint(summary: str) -> str | None:
    for pattern, hint in API_HINTS:
        if re.search(pattern, summary):
            return hint
    return None


def _error_info(exc: BaseException, filename: str) -> dict:
    line = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == filename:
            line = frame.lineno
    message = str(exc)
    summary = f"{type(exc).__name__}: {message}".strip()[:300]
    info = {"type": type(exc).__name__, "message": message[:2000], "line": line,
            "summary": summary}
    hint = api_hint(summary)
    if hint:
        info["hint"] = hint
    return info


def snapshot_modules(project: core.Project, code_dir: Path, main_rel: str) -> list[dict]:
    kit_dir = Path(__file__).resolve().parent
    modules = []
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if not filename or not filename.endswith(".py"):
            continue
        path = Path(filename).resolve()
        if kit_dir in path.parents or not project.inside(path):
            continue
        rel = project.rel(path)
        if rel == main_rel or rel.split("/")[0] in (".venv", "runs", ".lab", "report"):
            continue
        dest = code_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            shutil.copyfile(path, dest)
            modules.append({"path": rel, "sha256": core.sha256_file(dest),
                            "snapshot": project.rel(dest)})
    return sorted(modules, key=lambda m: m["path"])


DIFFERENT = {"different data", "missing", "new", "bytes differ", "image bytes differ", "not written"}


def compare_runs(project: core.Project, old: dict, new: dict) -> dict:
    """Compare a rerun with the run it re-executed. A rerun that did not finish ok is `failed`;
    a chart or output that differs, appears or disappears makes it `mismatch`."""
    import json

    import polars as pl

    old_inputs = {i["path"]: i.get("sha256") for i in old.get("inputs") or []}
    new_inputs = {i["path"]: i.get("sha256") for i in new.get("inputs") or []}
    charts: dict[str, str] = {}
    old_charts = {c["id"]: c for c in old.get("charts") or []}
    for chart in new.get("charts") or []:
        before = old_charts.pop(chart["id"], None)
        if before is None:
            charts[chart["id"]] = "new"
            continue
        state = "not comparable"
        with contextlib.suppress(Exception):
            if chart.get("data") and before.get("data"):
                same = pl.read_parquet(project.path(chart["data"])).equals(
                    pl.read_parquet(project.path(before["data"])))
                state = "same data" if same else "different data"
            elif chart["files"].get("plotly") and before["files"].get("plotly"):
                a = json.loads(project.path(chart["files"]["plotly"]).read_text("utf-8"))["data"]
                b = json.loads(project.path(before["files"]["plotly"]).read_text("utf-8"))["data"]
                state = "same data" if a == b else "different data"
            elif chart["files"].get("png") and before["files"].get("png"):
                same = core.sha256_file(project.path(chart["files"]["png"])) == \
                    core.sha256_file(project.path(before["files"]["png"]))
                state = "same image" if same else "image bytes differ"
        charts[chart["id"]] = state
    for cid in old_charts:
        charts[cid] = "missing"
    written = {o["path"]: o.get("sha256") for o in new.get("outputs") or []
               if o.get("change") != "deleted"}
    outputs = {}
    for out in old.get("outputs") or []:
        if out.get("change") == "deleted":
            continue
        if out["path"] not in written:
            outputs[out["path"]] = "not written"
        else:
            outputs[out["path"]] = ("same bytes" if written[out["path"]] == out.get("sha256")
                                    else "bytes differ")
    compared = [*charts.values(), *outputs.values()]
    if new.get("status") != "ok":
        verdict = "failed"
    elif any(s in DIFFERENT for s in compared):
        verdict = "mismatch"
    elif any(s.startswith("same") for s in compared):
        verdict = "match"
    else:
        verdict = "inconclusive"
    return {"of": old["id"], "verdict": verdict, "inputs_same": old_inputs == new_inputs,
            "charts": charts, "outputs": outputs}


def reconcile_inputs(entries: dict, outputs: list[dict]) -> list[dict]:
    """Inputs as recorded, minus files that are really this run's own outputs."""
    written = {o["path"]: o for o in outputs if o.get("change") != "deleted"}
    kept = []
    for entry in entries.values():
        via = sorted(entry["via"])
        out = written.get(entry["path"])
        if out is not None:
            if via == ["scan"] or out["change"] == "created":
                continue  # a literal the run wrote to, or a read-back of a file it created
            if entry.get("sha256") and entry["sha256"] == out.get("sha256"):
                continue  # read after the run rewrote it: an intermediate, not an input
        kept.append({**entry, "via": via})
    return sorted(kept, key=lambda i: i["path"])


def _script_source(project: core.Project, script) -> tuple[str, Path]:
    if not script:
        raise core.LabError("run_needs_script", "no script given")
    path = project.path(str(script))
    if not project.inside(path):
        raise core.LabError("script_outside", f"{script} is outside the project")
    if not path.is_file() or path.suffix != ".py":
        raise core.LabError("script_missing", f"{script} is not a .py file in the project",
                            hint="write the script under scripts/ first")
    script_rel = project.rel(path)
    if script_rel.split("/")[0] in (".venv", ".lab", "runs", "report"):
        raise core.LabError("script_location", f"{script_rel} is inside a managed directory",
                            hint="keep analysis code under scripts/")
    return script_rel, path


def _rerun_source(project: core.Project, rerun_of: str) -> tuple[dict, str, Path]:
    if not core.RUN_ID_RE.match(rerun_of or ""):
        raise core.LabError("rerun_id", f"{rerun_of!r} is not a run id",
                            hint="run ids look like 20260916-143012-monthly-sales; lab_status lists them")
    record = core.read_json(project.runs / rerun_of / "run.json")
    script = record.get("script") if isinstance(record, dict) else None
    if not isinstance(script, dict) or not script.get("snapshot") or not script.get("path"):
        raise core.LabError("rerun_unknown", f"no recorded run {rerun_of!r} with a code snapshot",
                            hint="lab_status lists run ids")
    code_root = (project.runs / rerun_of / "code").resolve()
    snapshot = project.path(str(script["snapshot"])).resolve()
    rel = Path(str(script["path"]))
    if code_root not in snapshot.parents or not snapshot.is_file():
        raise core.LabError("rerun_snapshot", f"run {rerun_of} has no code snapshot under "
                            f"runs/{rerun_of}/code")
    if rel.is_absolute() or ".." in rel.parts or rel.parts[:1] in ((".venv",), ("runs",), (".lab",)):
        raise core.LabError("rerun_snapshot", f"run {rerun_of} records an invalid script path {rel}")
    return record, rel.as_posix(), snapshot


def execute(project: core.Project, *, script: str | None, title: str | None,
            declared_inputs=(), timeout: float = 600.0, rerun_of: str | None = None,
            actor: str = "agent", session: str | None = None) -> dict:
    core.sync_context(project, actor="system", session=session)
    source_run = None
    if rerun_of:
        source_run, script_rel, exec_file = _rerun_source(project, rerun_of)
        title = title or source_run.get("title")
    else:
        script_rel, exec_file = _script_source(project, script)
    title = str(title or "").strip() or Path(script_rel).stem
    code_bytes = exec_file.read_bytes()
    # Runs in one project are serialized: outputs are found by comparing the project tree before
    # and after a run, which would otherwise attribute a concurrent run's files to this one.
    with core.run_lock(project):
        return _execute_locked(project, script_rel=script_rel, exec_file=exec_file,
                               code_bytes=code_bytes, title=title,
                               declared_inputs=declared_inputs, timeout=timeout,
                               source_run=source_run, actor=actor, session=session)


def _execute_locked(project, *, script_rel, exec_file, code_bytes, title, declared_inputs,
                    timeout, source_run, actor, session) -> dict:
    rerun_of = source_run["id"] if source_run else None
    run_id = core.new_run_dir(project, Path(script_rel).stem)
    run_dir = project.runs / run_id
    code_dir = run_dir / "code"
    snapshot = code_dir / script_rel
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(code_bytes)
    record = {"schema": core.SCHEMA_RUN, "id": run_id, "title": title, "status": "running",
              "pid": os.getpid(), "started_at": core.iso(), "actor": actor, "session": session,
              "rerun_of": rerun_of,
              "script": {"path": script_rel, "sha256": core.sha256_bytes(code_bytes),
                         "snapshot": project.rel(snapshot)}}
    core.write_json(run_dir / "run.json", record, platform=project.platform)

    # Code of a rerun keeps the snapshot path so tracebacks show the lines that actually ran.
    filename = exec_file if rerun_of else project.path(script_rel)
    state = capture.RunState(project, run_id, run_dir, filename)
    with contextlib.suppress(Exception):
        reads, writes = scan_literals(code_bytes.decode("utf-8", errors="replace"))
        for p in static_input_files(project, reads, writes):
            state.record_read(str(p), "scan")
    for item in declared_inputs or ():
        state.record_read(str(project.path(item)), "declared")
    before = tree_stat(project)
    stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"

    signals = {"timeout": False, "cancel": False}
    # An interrupt that arrives once the script has stopped only marks the run cancelled: the
    # status _classify chose stands and the record must still be written. This scope covers the
    # capture cleanup and the whole of _finalize without a gap, and _run_script nests the raising
    # scope for the script itself inside it.
    with project.platform.runs.cancel_scope(lambda: signals.__setitem__("cancel", True),
                                            interrupt=False):
        status, error, duration = _run_script(project, state, code_bytes, filename, script_rel,
                                              timeout, stdout_path, stderr_path, signals)
        return _finalize(project, state, record, source_run=source_run, before=before,
                         status=status, error=error, duration=duration, code_dir=code_dir,
                         stdout_path=stdout_path, stderr_path=stderr_path, actor=actor,
                         session=session)


def _run_script(project, state, code_bytes, filename, script_rel, timeout, stdout_path,
                stderr_path, signals) -> tuple[str, dict | None, float]:
    env_backup = {k: os.environ.get(k) for k in ("LAB_RUN_ID", "LAB_RUN_DIR", "LAB_PROJECT",
                                                 "MPLBACKEND")}
    os.environ.update({"LAB_RUN_ID": state.run_id, "LAB_RUN_DIR": str(state.run_dir),
                       "LAB_PROJECT": str(project.root)})
    os.environ.setdefault("MPLBACKEND", "Agg")
    cwd = os.getcwd()
    finder = None
    failure: BaseException | None = None
    runs = project.platform.runs
    t0 = time.perf_counter()
    try:
        with _redirected(stdout_path, stderr_path):
            try:
                os.chdir(project.root)
                finder = capture.install(state)
                # Both callbacks only record that the event happened; the port interrupts the main
                # thread itself. Leaving these scopes restores the flag-only handlers the caller
                # installed, so the cleanup below is never interrupted.
                with runs.cancel_scope(lambda: signals.__setitem__("cancel", True),
                                       interrupt=True), \
                        runs.deadline(timeout, lambda: signals.__setitem__("timeout", True)):
                    _exec_script(code_bytes, filename, project.path(script_rel).parent)
            except BaseException as exc:  # noqa: BLE001 - classified below with the timer off
                failure = exc
            # Libraries such as DuckDB turn the interrupt into their own error; the flags decide.
            if failure is not None and not isinstance(failure, SystemExit):
                with contextlib.suppress(Exception):
                    traceback.print_exception(failure)
            with contextlib.suppress(Exception):
                state.capture_open_figures("auto", close=True)
            with contextlib.suppress(Exception):
                state.register_loose_files()
    finally:
        capture.uninstall(finder)
        os.chdir(cwd)
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    duration = time.perf_counter() - t0
    status, error = _classify(failure, signals, timeout, str(filename))
    return status, error, duration


def _classify(failure, signals, timeout, filename) -> tuple[str, dict | None]:
    if signals["timeout"]:
        return "timeout", {"type": "Timeout", "message": f"exceeded {timeout:.0f} s", "line": None,
                           "summary": f"timed out after {timeout:.0f} s"}
    if signals["cancel"] or isinstance(failure, (KeyboardInterrupt, _platform.Interrupted)):
        return "cancelled", {"type": "Cancelled", "message": "interrupted", "line": None,
                             "summary": "cancelled"}
    if failure is None:
        return "ok", None
    if isinstance(failure, SystemExit):
        if failure.code in (None, 0):
            return "ok", None
        return "error", {"type": "SystemExit", "message": str(failure.code), "line": None,
                         "summary": f"SystemExit({failure.code})"}
    return "error", _error_info(failure, filename)


def _finalize(project, state, record, *, source_run, before, status, error, duration, code_dir,
              stdout_path, stderr_path, actor, session) -> dict:
    """Write the run record even when parts of the bookkeeping fail; failures are listed."""
    problems: list[str] = []

    def guarded(label, fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - recorded in finalize_errors
            problems.append(f"{label}: {type(exc).__name__}: {exc}")
            return default

    run_id, run_dir = record["id"], state.run_dir
    outputs = guarded("outputs", lambda: diff_outputs(project, before, tree_stat(project),
                                                      state.cache), [])
    for out in outputs:
        out["via"] = state.saved.get(out["path"], "diff")
    inputs = guarded("inputs", lambda: reconcile_inputs(state.inputs, outputs), [])
    record.update({
        "status": status, "ended_at": core.iso(), "duration_s": round(duration, 3),
        "modules": guarded("modules", lambda: snapshot_modules(project, code_dir,
                                                               record["script"]["path"]), []),
        "inputs": inputs, "outputs": outputs, "charts": state.charts, "error": error,
        "stdout": project.rel(stdout_path), "stderr": project.rel(stderr_path),
        "env": guarded("environment", lambda: environment(project), {}),
    })
    record.pop("pid", None)
    record["warnings"] = guarded("warnings", lambda: run_warnings(record, project), [])
    if source_run is not None:
        record["reproduction"] = guarded("reproduction",
                                         lambda: compare_runs(project, source_run, record), None)
    if problems:
        record["finalize_errors"] = list(problems)
    core.write_json(run_dir / "run.json", record, platform=project.platform)
    guarded("hash cache", state.cache.save, None)

    ok = status == "ok"
    written = sorted({o["path"] for o in outputs if o.get("change") != "deleted"})
    read = sorted({i["path"] for i in inputs} - set(written))
    data_refs = (written + read)[:core.MAX_EVENT_REFS]
    refs = {"runs": [run_id],
            "charts": [core.chart_key(record, c) for c in state.charts] if ok else [],
            "data": data_refs}
    text = f"{record['title']} — {status}"
    if error:
        text += f" ({error['summary']})"
    if len(written) + len(read) > len(data_refs):
        text += f"; {len(read):,} input file(s), {len(written)} output(s) — see the run page"
    if record.get("reproduction"):
        text += f"; rerun of {source_run['id']}: {record['reproduction']['verdict']}"
    event = guarded("journal", lambda: core.append_event(project, "run", text, refs=refs,
                                                         actor=actor, session=session), None)
    for kind, note_text in state.notes:
        guarded(f"note {kind}", lambda k=kind, t=note_text: core.add_note(
            project, k, t, refs=[run_id], actor=actor, session=session), None)

    if problems and problems != (record.get("finalize_errors") or []):
        record["finalize_errors"] = list(problems)
        with contextlib.suppress(OSError):
            core.write_json(run_dir / "run.json", record, platform=project.platform)
    result = {"run": record, "event": event["id"] if event else None,
              "stdout_tail": _tail(stdout_path), "stderr_tail": _tail(stderr_path),
              "page": project.rel(project.report / "runs" / f"{run_id}.html")}
    try:
        from . import report

        result["report"] = report.build(project)
    except Exception as exc:  # noqa: BLE001 - a report failure must not lose the run record
        result["report_error"] = f"{type(exc).__name__}: {exc}"
    if problems:
        result["finalize_errors"] = problems
    return result


MAX_LISTED_PATHS = 3


def imported_sources(project: core.Project) -> dict[str, str]:
    """Absolute source path -> dataset name, for everything data/raw was imported from."""
    out = {}
    for meta_file in sorted(project.raw.glob("*.meta.json")) if project.raw.is_dir() else []:
        meta = core.read_json(meta_file)
        source = (meta or {}).get("source") or {}
        if isinstance(source.get("path"), str) and meta.get("name"):
            out[source["path"]] = meta["name"]
    return out


def run_warnings(record: dict, project: core.Project | None = None) -> list[str]:
    warnings = []
    imported = imported_sources(project) if project is not None else {}
    if record["status"] == "ok" and not record["inputs"] and (record["charts"] or any(
            o.get("change") != "deleted" for o in record["outputs"])):
        warnings.append("no input files were detected: read data with lab.read/lab.scan/lab.sql "
                        "or literal paths, or declare inputs")
    if record["status"] != "ok" and record["charts"]:
        warnings.append(f"{len(record['charts'])} chart(s) were recorded but the run is "
                        f"{record['status']}, so they stay out of the report gallery")
    for chart in record["charts"]:
        if chart["kind"] == "matplotlib" and not chart.get("data"):
            warnings.append(f"chart {chart['id']}: plotted data not recorded "
                            "(pass data=... to lab.chart)")
    external = [e["path"] for e in record["inputs"] if e.get("external")]
    for path in sorted({p for p in external if imported.get(p)}):
        name = imported[path]
        warnings.append(f"input outside the project: {path} — already imported as dataset "
                        f"'{name}'; read lab.read('{name}') instead so the run stops depending on "
                        "a file that keeps changing")
    rest = [p for p in external if not imported.get(p)]
    # One warning per file would be hundreds of lines for a run over a log directory.
    if len(rest) > MAX_LISTED_PATHS:
        warnings.append(f"{len(rest):,} inputs outside the project, e.g. "
                        + ", ".join(rest[:MAX_LISTED_PATHS])
                        + " (import them with lab_import so the analysis does not depend on files "
                          "that keep changing)")
    else:
        warnings += [f"input outside the project: {p} (import it with lab_import so the analysis "
                     "does not depend on it)" for p in rest]
    for out in record["outputs"]:
        if out.get("outside_data") and out.get("change") != "deleted":
            warnings.append(f"file written outside data/: {out['path']}")
        elif out["path"].startswith("data/raw/"):
            warnings.append(f"run changed imported data {out['path']} (data/raw is meant to be "
                            "immutable)")
        elif out.get("change") != "deleted" and not out["path"].endswith((".parquet", ".json")):
            warnings.append(f"non-Parquet data written: {out['path']}")
    if "pandas" in sys.modules:
        warnings.append("pandas was imported; this project processes data with DuckDB and Polars")
    return warnings
