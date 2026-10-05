"""Run lifecycle: snapshot the code, execute it with capture hooks, record provenance.

A run executes the exact bytes it snapshots (compiled under the script's own path, so tracebacks and
`__file__` stay natural). Inputs come from four sources, each recorded in `via`: runtime hooks
(Polars/DuckDB/aelix_analytics reads), a static scan of path literals in the code, and inputs the caller
declared. Outputs are every file under the project that the run created or modified (outside the
code, run and report directories). This is a record, not a sandbox or a determinism guarantee.
"""

from __future__ import annotations

import ast
import atexit
import builtins
import collections
import contextlib
import errno
import io
import os
import platform
import re
import shutil
import sys
import threading
import time
import traceback
import types
import weakref
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
OUTPUT_TAIL = core.OUTPUT_TAIL


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
    found: dict[str, Path] = {}
    for literal in literals:
        # The literal itself, and the quoted data-file names in it: a SQL text's
        # 'data/raw/sales.parquet', and as well one a message or a docstring quotes.
        for token in {literal, *capture.sql_file_literals(literal)}:
            # One the kit cannot use — a name longer than the file system takes, say — is skipped
            # on its own, and does not take the inputs the other literals found with it.
            with contextlib.suppress(Exception):
                if len(token) <= 400 and "\n" not in token and token not in writes and (
                        "/" in token or token.lower().endswith(core.DATA_SUFFIXES)):
                    found.update(_token_inputs(project, token))
    return list(found.values())


def _token_inputs(project: core.Project, token: str) -> dict[str, Path]:
    import glob

    # Project.path takes a `~name` whose home does not exist (an Office lock file's `~$budget.xlsx`)
    # as written, so such a file in the project is an input like any other.
    path = project.path(token)
    text = str(path)
    candidates = [Path(m) for m in glob.glob(text, recursive=True)[:200]] \
        if any(ch in text for ch in "*?[") else [path]
    return {str(c): c for c in candidates
            if c.is_file() and c.suffix.lower() in core.DATA_SUFFIXES and _recordable(c)}


def _recordable(path: Path) -> bool:
    """Whether a record can hold the name: it is written as UTF-8, and POSIX hands over a name that
    is not UTF-8 with surrogates in it, which would lose the whole record where it is written
    (docs/CONTRACTS.md G). The scan only guesses, so it leaves such a name out."""
    try:
        str(path).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


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
    """The last `limit` bytes of a log as text, from the first whole UTF-8 character: a tail that
    leaves the start out begins with "…"."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - limit))
            data = fh.read()
    except OSError:
        return ""
    if size > limit:
        # Skip the continuation bytes (0x80-0xBF) of a character the seek cut. A character has at
        # most three; more is not UTF-8, and is decoded as it is.
        start = next((i for i in range(min(4, len(data))) if not 0x80 <= data[i] <= 0xBF), 0)
        data = data[start:]
    text = data.decode("utf-8", errors="replace")
    return ("…" + text) if size > limit else text


STREAM_NAMES = ("stdout", "stderr", "__stdout__", "__stderr__")
# The run streams of earlier runs in this process. A module a script imported keeps its place in
# sys.modules, and with it any stream it took from sys.stdout; a later run's output through it lands
# in that stream's buffer, which is flushed with the later run's own. A thread a script left running
# can write through one after its run. So they are flushed when a run starts and ends, when the CLI
# prints its result (flush_run_streams) — main, whose scripts wrote through the CLI's own stream,
# had such lines ahead of the result, which the extension takes from the last line that parses as
# JSON — and when the interpreter exits. The latest run's two streams are also held here: a stream
# whose last other holder is gone, such as a module caught in a reference cycle, would otherwise be
# collected with its text unwritten, which the collector can do by finalizing its layers in either
# order. An older run's streams are flushed for as long as something else holds them; text written
# through one after its last flush can still be lost that way. Holding every run's would make each
# run's own work grow with the runs before it.
_EARLIER_RUN_STREAMS: weakref.WeakSet = weakref.WeakSet()
_HELD_RUN_STREAMS: collections.deque = collections.deque(maxlen=2)


def flush_run_streams() -> bool:
    """Write out what earlier runs' streams still hold (see _EARLIER_RUN_STREAMS). True when this
    process has run a script, whether or not anything was left to write."""
    streams = list(_EARLIER_RUN_STREAMS)
    _flush(streams)
    return bool(streams)


# How long the interpreter's exit waits for earlier runs' streams to be written out. A thread a
# script left running can hold one of their buffers' locks, blocked writing to a stdout nobody
# reads any more; a flush from the exiting thread would wait for it without end, as atexit runs
# before CPython's own one-second grace for such locks (review 13, result-1). So the flush runs in a
# thread of its own, and the exit goes on after this long, as main's did: what that thread still
# had to write is lost, and the shutdown can end with SIGABRT where it flushes that buffer (D).
EXIT_FLUSH_WAIT = 1.0


def _flush_run_streams_at_exit() -> None:
    flusher = threading.Thread(target=flush_run_streams, name="aelix-analytics-exit-flush", daemon=True)
    try:
        flusher.start()
    except RuntimeError:  # an interpreter that starts no thread any more
        return
    flusher.join(EXIT_FLUSH_WAIT)


atexit.register(_flush_run_streams_at_exit)

# What a run the CLI started with `result_follows` leaves for its result line (_redirected): the
# host's own sys.stdout and sys.__stdout__; the run stdout's buffer where it can carry the line, else
# None; the descriptor fd 1 was when the run started, kept open until the line is out; and whether
# the run's text stream stands in as sys.stdout and sys.__stdout__ until then.
_Route = collections.namedtuple("_Route", "host buffer fd stood_in")
_ROUTE: _Route | None = None
_STDOUT_NAMES = ("stdout", "__stdout__")


def write_result(text: str) -> None:
    """Print the CLI's result line. What earlier runs' streams still hold goes out first: the
    extension takes the last line that parses as JSON, and main had those lines ahead of it, as its
    scripts wrote through this process's own stream. A thread's text need not end its line, so after
    a run the result starts a line of its own.

    After a run the CLI started with `result_follows` (_redirected), the line is the kit's own: it
    is encoded here and written as one write of the run stdout's buffer, the object the kit built and
    kept, through its type's methods, so nothing the script did to the run's text stream or set on
    the buffer reaches it. A thread the script left running writes through that buffer too — through
    the run's text stream it took, through sys.stdout until the host's is given back, or through a
    stream it built over the buffer — and the buffer's lock, held for a whole write however long,
    keeps the thread's text before the line or after it, never inside. The host's own stream, which
    a thread may have taken before the run, is flushed first. Where the buffer cannot carry the line
    (closed, or its raw file detached), the line goes through the host's stream, as before 3.6; where
    fd 1 no longer leads where it did when the run started (a stream the script opened on it, closed
    when collected after the run), through the descriptor kept for it."""
    lead = "\n" if flush_run_streams() else ""
    route = _ROUTE
    if route is None:
        sys.stdout.write(lead + text + "\n")
        sys.stdout.flush()
        return
    data = (lead + text + "\n").replace("\n", os.linesep).encode(_platform.TEXT_ENCODING,
                                                                 errors="replace")
    ours = _same_file(1, route.fd)
    if ours and route.buffer is not None:
        _flush(route.host[:1])
        if _write_through(route.buffer, data):
            return
    _restore_host(route)
    if ours:
        try:
            route.host[0].write(lead + text + "\n")
            route.host[0].flush()
            return
        except (OSError, ValueError):
            pass
    _write_fd(route.fd, data)


def give_back_stdout() -> None:
    """End what a run left for the CLI's result line: the host's own sys.stdout and sys.__stdout__
    are back where the run's stream stood in for them — before the interpreter's shutdown flushes
    sys.stdout, where a daemon thread holding the run stream's buffer would abort the process
    (main's did) — and the descriptor kept for the line is closed."""
    global _ROUTE
    route = _ROUTE
    if route is None:
        return
    # The route goes only once the host's streams are back: a signal handled as this starts or as
    # it calls (a second Ctrl+C) leaves it in place for the caller to give back again (main() does).
    _restore_host(route)
    _ROUTE = None
    try:  # no call but the close's own: a signal handled here comes after the close
        os.close(route.fd)
    except OSError:
        pass


def _keep_for_the_line(route: _Route) -> None:
    global _ROUTE
    earlier, _ROUTE = _ROUTE, route
    if earlier is not None:  # a caller that never gave the last one back
        with contextlib.suppress(OSError):
            os.close(earlier.fd)


def _restore_host(route: _Route) -> None:
    if route.stood_in:
        sys.stdout, sys.__stdout__ = route.host


def _can_carry_the_line(buffer, host) -> bool:
    """Whether the run stdout's buffer can carry the CLI's result line after the run: there is one
    (under -u or PYTHONUNBUFFERED there is not), it is open with its raw file attached, and the
    host's own stdout writes to fd 1 through a plain file layer — a pipe, a file, a terminal on
    POSIX. A Windows console is not one: the host writes to it as text through the console. It runs
    in the run's last cleanup, before the record is written, so nothing it meets may raise out of it
    (3.6's check raised for a stream the script had detached, and the run lost its record)."""
    try:
        if buffer is None or buffer.closed:
            return False
        layer = getattr(host, "buffer", None)
        raw = getattr(layer, "raw", layer)
        return isinstance(raw, io.FileIO) and not raw.closed and raw.fileno() == 1
    except Exception:  # noqa: BLE001 - see the docstring
        return False


def _still_attached(stream, buffer) -> bool:
    """Whether the run's text stream still writes to `buffer` (not detached) and is open. Nothing it
    meets may raise out of it, as above."""
    try:
        return stream.buffer is buffer and not stream.closed
    except Exception:  # noqa: BLE001
        return False


def _same_file(fd: int, other: int) -> bool:
    """Whether descriptor `fd` leads to the file, pipe or terminal that `other` does. On Windows
    os.fstat gives a pipe or a console no device or inode (both 0), so there any two of them look
    the same: a closed fd 1, or one reopened on a disk file, is told apart, and one reopened on
    another pipe is not."""
    try:
        a, b = os.fstat(fd), os.fstat(other)
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _write_through(buffer, data: bytes) -> bool:
    """Write `data` as one write of `buffer` and flush it, through the type's own methods. False
    when the buffer cannot take it: closed, its raw file detached, or the write failed."""
    try:
        io.BufferedWriter.write(buffer, data)
        io.BufferedWriter.flush(buffer)
    except (OSError, ValueError):
        return False
    return True


def _write_fd(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _flush(streams) -> list:
    """Flush each stream and return the ones that took it. One that is closed or refuses is
    skipped: this is housekeeping, and a stream a script installed may raise anything, so nothing
    it raises may stop the caller."""
    flushed = []
    for stream in streams:
        if stream is None:
            continue
        with contextlib.suppress(BaseException):  # noqa: BLE001 - see the docstring
            stream.flush()
            flushed.append(stream)
    return flushed


def _run_stream(fd: int, replaced) -> io.TextIOWrapper:
    """A text stream over `fd`, layered and buffered as the stream it replaces was — what main's
    run output had, since main wrote through the interpreter's own streams. CPython builds them
    over a buffered writer, stderr line-buffered and stdout line-buffered only on a terminal; under
    `-u` or PYTHONUNBUFFERED both write straight through to the raw file. Copying the layering and
    both flags keeps the order against child processes, what a forced end loses and the speed of a
    print-heavy script as they were. A replaced object that is not a text stream gives CPython's
    defaults for a file."""
    raw = io.FileIO(fd, "w", closefd=False)
    raw.name = "<stdout>" if fd == 1 else "<stderr>"
    unbuffered, line_buffering, write_through = False, fd == 2, False
    if isinstance(replaced, io.TextIOWrapper):
        unbuffered = replaced.write_through and not isinstance(replaced.buffer, io.BufferedIOBase)
        line_buffering, write_through = replaced.line_buffering, replaced.write_through
    stream = io.TextIOWrapper(raw if unbuffered else io.BufferedWriter(raw),
                              encoding=_platform.TEXT_ENCODING, errors="replace",
                              line_buffering=line_buffering, write_through=write_through)
    stream.mode = "w"
    return stream


@contextlib.contextmanager
def _redirected(stdout_path: Path, stderr_path: Path, result_follows: bool = False):
    """Send this process's output, and that of anything it starts, to the run's log files.

    The descriptors are redirected so a C library or a child process lands in the same file, and
    the Python-level streams are rebound on top of them — the interpreter's original ones as well
    (`sys.__stdout__`, `sys.__stderr__`), so whatever name a script writes through reaches the log,
    and nothing it writes shares a buffer with bytes the host wrote before the run. A console
    stream keeps writing to the console it was built for: on Windows `sys.stdout` can wrap a
    _WindowsConsoleIO that calls WriteConsoleW, which fails once fd 1 points at a file. Rebinding
    also fixes the encoding of a run's own log, which would otherwise follow the locale.

    In-process callers: writes through a reference to a stream taken before the run, or during an
    earlier run, reach this run's log when that stream is flushed at the end — but after what the
    script printed, not interleaved with it, because they sit in another buffer. So what a thread
    a script left running writes through its run's stream while a later run runs goes into that
    run's log, as it did on main; what it writes between runs reaches the host's output when the
    next run starts, when the CLI prints its result, or when the interpreter exits.

    With `result_follows` (the CLI), the run leaves a route for the result line (write_result): the
    run stdout's buffer, taken before the script runs, where it can carry the line; the run's text
    stream as sys.stdout and sys.__stdout__ from the restore on, where it still writes to that
    buffer, so print() takes the buffer's lock too; and the duplicate of fd 1 taken to restore it,
    kept open. The caller ends it with give_back_stdout."""
    saved = tuple(getattr(sys, name) for name in STREAM_NAMES)
    # Before fd 1 becomes the log: the host's own streams (an SDK caller may have replaced
    # sys.stdout while the real one still holds bytes it wrote), and earlier runs' streams. A host
    # stream that refused keeps its bytes for the host's own output: it is not flushed again below.
    host_flushed = _flush(saved)
    _flush(list(_EARLIER_RUN_STREAMS))
    saved_fds = os.dup(1), os.dup(2)
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        os.dup2(out.fileno(), 1)
        os.dup2(err.fileno(), 2)
        run_out, run_err = _run_stream(1, saved[0]), _run_stream(2, saved[1])
        run_buf = run_out.buffer if type(run_out.buffer) is io.BufferedWriter else None
        sys.stdout = sys.__stdout__ = run_out
        sys.stderr = sys.__stderr__ = run_err
        try:
            yield
        finally:
            try:
                # While fd 1 is still the log, so none of it lands in front of the CLI's JSON line
                # after the restore: whatever the four names hold now — a script's own stream, when
                # a signal cut its restore short — the run's streams, the streams of earlier runs,
                # and the host streams that took the flush before the run.
                _flush((*(getattr(sys, name) for name in STREAM_NAMES), run_out, run_err,
                        *list(_EARLIER_RUN_STREAMS), *host_flushed))
            finally:
                # A caller that prints a result line next (the CLI, write_result): the run's stream
                # stands in for the host's from here on, not after a moment of the host's own, so a
                # thread the script left running that looks sys.stdout up shares the line's buffer.
                buffer = run_buf if result_follows and _can_carry_the_line(run_buf, saved[0]) \
                    else None
                stand_in = buffer is not None and _still_attached(run_out, buffer)
                for name, stream in zip(STREAM_NAMES, saved, strict=True):
                    setattr(sys, name, run_out if stand_in and name in _STDOUT_NAMES else stream)
                os.dup2(saved_fds[0], 1)
                os.dup2(saved_fds[1], 2)
                if not result_follows:
                    os.close(saved_fds[0])
                os.close(saved_fds[1])
                _EARLIER_RUN_STREAMS.update((run_out, run_err))
                _HELD_RUN_STREAMS.extend((run_out, run_err))
                if result_follows:
                    _keep_for_the_line(_Route((saved[0], saved[2]), buffer, saved_fds[0],
                                              stand_in))


def _exec_script(code_bytes: bytes, filename: Path, script_dir: Path) -> None:
    module = types.ModuleType("__main__")
    module.__file__ = str(filename)
    module.__builtins__ = builtins  # type: ignore[attr-defined]
    saved_main, saved_argv, saved_path = sys.modules.get("__main__"), sys.argv[:], sys.path[:]
    saved_streams = tuple(getattr(sys, name) for name in STREAM_NAMES)
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
        # A script may replace any of the four; the runner flushes and restores the ones it knows.
        for name, stream in zip(STREAM_NAMES, saved_streams, strict=True):
            setattr(sys, name, stream)


# Failures seen in real sessions: a model reaches for pandas habits or a Polars API that moved.
# The version line alone does not stop this, so the failure says what to write instead.
API_HINTS = (
    (r"No module named 'pandas'",
     "this project has no pandas: process data with Polars expressions or DuckDB SQL "
     "(analytics.sql(...).pl()); .to_pandas() exists only if you add pandas to the project"),
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
                a = json.loads(project.path(chart["files"]["plotly"])
                               .read_text(encoding="utf-8"))["data"]
                b = json.loads(project.path(before["files"]["plotly"])
                               .read_text(encoding="utf-8"))["data"]
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


# Directories the kit manages; a script is never taken from one.
MANAGED_DIRS = (".venv", ".lab", "runs", "report")


def _in_managed_dir(rel: str) -> bool:
    """Whether `rel` names a file in a managed directory on some platform: split on both
    separators, because Windows reads a backslash in a recorded path as one, and compared without
    case, because macOS and Windows file systems ignore it by default."""
    return re.split(r"[\\/]", rel)[0].casefold() in MANAGED_DIRS


def _in_managed_dir_here(project: core.Project, rel: str) -> bool:
    """Whether `rel` is in a managed directory on this machine's file system: `RUNS/x.py` is, where
    the file system ignores case."""
    first, _, rest = rel.partition("/")
    if first in MANAGED_DIRS:
        return True
    if not rest or first.casefold() not in MANAGED_DIRS:
        return False
    with contextlib.suppress(OSError):
        return os.path.samefile(project.root / first, project.root / first.casefold())
    return False


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
    if _in_managed_dir_here(project, script_rel):
        raise core.LabError("script_location", f"{script_rel} is inside a managed directory",
                            hint="keep analysis code under scripts/")
    # Refused before it is recorded, so the kit never writes a path it would not rerun. That
    # includes a name another platform would read as inside a managed directory (`runs\\x.py` on
    # Windows, `RUNS/x.py` where the file system minds case).
    if not _platform.record_relative_path(script_rel) or _in_managed_dir(script_rel):
        raise core.LabError("script_location",
                            f"{script_rel} is not a path a record can hold on every platform",
                            hint="keep analysis code under scripts/ with a plain name")
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
    code_root = core._resolved(project.runs / rerun_of / "code")
    snapshot = core._resolved(project.path(str(script["snapshot"])))
    if code_root not in snapshot.parents or not snapshot.is_file():
        raise core.LabError("rerun_snapshot", f"run {rerun_of} has no code snapshot under "
                            f"runs/{rerun_of}/code")
    # The recorded path decides the directory that goes onto sys.path, so it is checked as the kit
    # writes one rather than by the running platform (`/tmp/x.py` is not absolute on Windows), and
    # then where it leads once joined: a directory inside the project may be a link to outside it.
    recorded = script["path"]
    target = project.root / recorded
    if not _platform.record_relative_path(recorded) or _in_managed_dir(recorded) \
            or not project.inside(target) or _in_managed_dir(project.rel(target)):
        raise core.LabError("rerun_snapshot",
                            f"run {rerun_of} records an invalid script path {recorded!r}")
    return record, recorded, snapshot


def _remove_run_dir(run_dir: Path) -> None:
    shutil.rmtree(run_dir, ignore_errors=True)
    with contextlib.suppress(OSError):
        run_dir.rmdir()  # empty, but made unreadable to the kit by its own umask: rmtree cannot list it


# Windows' errors for a path beyond its length limit: "path not found", "file name too long". Only
# its os functions give them, and not for every such path: os.mkdir answered a 300-character name
# with "invalid name" (123, Issue #8 measurement), which it gives for a reserved character as
# well, and open() there reports a C runtime errno and no Windows error. So a name longer than a
# file system holds is told by its own length, in the UTF-16 units NTFS and APFS count; ext4's
# 255 bytes the system says itself (ENAMETOOLONG).
_WINDOWS_PATH_TOO_LONG = (3, 206)
_NAME_MAX = 255


def _name_units(name: str) -> int:
    return len(name.encode("utf-16-le", "surrogatepass")) // 2  # beyond the BMP, two each


def _write_failure_hint(exc: Exception, path: Path) -> str | None:
    if isinstance(exc, UnicodeError):
        # POSIX hands over arguments, names and variables it cannot decode with surrogates in them.
        return "use a title, a script name and LAB_ACTOR/LAB_SESSION values that are valid UTF-8"
    if not isinstance(exc, OSError):
        return None
    if (exc.errno == errno.ENAMETOOLONG or getattr(exc, "winerror", None) in _WINDOWS_PATH_TOO_LONG
            or any(_name_units(part) > _NAME_MAX for part in path.parts if part != path.anchor)):
        return "shorten the script's path"
    if exc.errno == errno.ENOSPC:
        return "free some space on the disk that holds the project"
    return "check that the project's runs/ directory can be written"


def execute(project: core.Project, *, script: str | None, title: str | None,
            declared_inputs=(), timeout: float = 600.0, rerun_of: str | None = None,
            actor: str = "agent", session: str | None = None,
            result_follows: bool = False) -> dict:
    """Run a script (or rerun a recorded one) and record it. `result_follows`: the caller prints a
    result line through write_result after this returns and then calls give_back_stdout (the CLI);
    see _redirected."""
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
                               source_run=source_run, actor=actor, session=session,
                               result_follows=result_follows)


def _execute_locked(project, *, script_rel, exec_file, code_bytes, title, declared_inputs,
                    timeout, source_run, actor, session, result_follows=False) -> dict:
    # The run's signal handling is installed once, here, and taken out after the record is
    # written. Between the two regions below it only records: nothing it does can stop the path to
    # the record, and inside a region it raises once at most (see _platform.RunControl).
    with project.platform.runs.guard() as guard:
        rerun_of = source_run["id"] if source_run else None
        run_id = core.new_run_dir(project, Path(script_rel).stem)
        run_dir = project.runs / run_id
        code_dir = run_dir / "code"
        snapshot = code_dir / script_rel
        record = {"schema": core.SCHEMA_RUN, "id": run_id, "title": title, "status": "running",
                  "pid": os.getpid(), "started_at": core.iso(), "actor": actor, "session": session,
                  "rerun_of": rerun_of,
                  "script": {"path": script_rel, "sha256": core.sha256_bytes(code_bytes),
                             "snapshot": project.rel(snapshot)}}
        # Nothing has run yet, and until the record exists the directory would stay without one: a
        # name longer than the file system allows, a path beyond Windows' limit, a full disk, a
        # directory the kit cannot write, a record holding text that cannot be written as UTF-8.
        # Removed, and refused.
        code, what = "snapshot_failed", "code snapshot"
        try:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(code_bytes)
            code, what = "record_failed", "run record"
            core.write_json(run_dir / "run.json", record, platform=project.platform)
        except (OSError, ValueError) as exc:
            _remove_run_dir(run_dir)
            raise core.LabError(code, f"could not write the {what} of {script_rel}: {exc}",
                                hint=_write_failure_hint(exc, snapshot)) from exc

        # Code of a rerun keeps the snapshot path so tracebacks show the lines that actually ran.
        filename = exec_file if rerun_of else project.path(script_rel)
        state = capture.RunState(project, run_id, run_dir, filename)
        stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"
        before, setup_interrupted = None, False
        try:
            # Hashing the inputs and walking the project can take long for a big project, so a
            # cancel stops this too, and the run is recorded as cancelled before its script.
            with guard.interruptible():
                with contextlib.suppress(Exception):
                    reads, writes = scan_literals(code_bytes.decode("utf-8", errors="replace"))
                    for p in static_input_files(project, reads, writes):
                        state.record_read(str(p), "scan")
                for item in declared_inputs or ():
                    state.record_read(str(project.path(item)), "declared")
                before = tree_stat(project)
        except _platform.Interrupted:
            setup_interrupted = True
        finally:
            guard.settle()  # also when a signal cut the region's own exit short
        if setup_interrupted:
            for log in (stdout_path, stderr_path):
                with contextlib.suppress(OSError):
                    log.touch()
            status, error, duration = "cancelled", _cancelled("cancelled before the script "
                                                              "started"), 0.0
        else:
            status, error, duration = _run_script(project, state, code_bytes, filename,
                                                  script_rel, timeout, stdout_path, stderr_path,
                                                  guard, result_follows)
        return _finalize(project, state, record, source_run=source_run, before=before,
                         status=status, error=error, duration=duration, code_dir=code_dir,
                         stdout_path=stdout_path, stderr_path=stderr_path, actor=actor,
                         session=session)


def _run_script(project, state, code_bytes, filename, script_rel, timeout, stdout_path,
                stderr_path, guard, result_follows=False) -> tuple[str, dict | None, float]:
    """Run the script in the guard's interruptible region and classify how it ended."""
    env_backup = {k: os.environ.get(k) for k in ("LAB_RUN_ID", "LAB_RUN_DIR", "LAB_PROJECT",
                                                 "MPLBACKEND")}
    os.environ.update({"LAB_RUN_ID": state.run_id, "LAB_RUN_DIR": str(state.run_dir),
                       "LAB_PROJECT": str(project.root)})
    os.environ.setdefault("MPLBACKEND", "Agg")
    cwd = os.getcwd()
    failure: BaseException | None = None
    t0 = time.perf_counter()
    try:
        with _redirected(stdout_path, stderr_path, result_follows):
            try:
                # A cancel recorded before this point raises as the region is entered, so nothing
                # of the script runs after one: not the capture hooks, not the script.
                with guard.interruptible(deadline=timeout):
                    os.chdir(project.root)
                    capture.install(state)
                    _exec_script(code_bytes, filename, (project.root / script_rel).parent)
            except BaseException as exc:  # noqa: BLE001 - classified below with the timer off
                failure = exc
            # Whatever the region raised, and even when a signal cut its exit short: this stops the
            # raising, counts a deadline that passed and a cancel whose handler what the region
            # raised cut short, disarms, takes the handlers back, and returns what a handler the
            # script left installed raised meanwhile. From here the guard only records, which is
            # also why a library such as DuckDB turning the interrupt into an error of its own does
            # not change the outcome: the flags decide.
            settled = guard.settle(failure)
            if failure is None:
                failure = settled
            if failure is not None and not isinstance(failure, SystemExit):
                with contextlib.suppress(Exception):
                    traceback.print_exception(failure)
            with contextlib.suppress(Exception):
                state.capture_open_figures("auto", close=True)
            with contextlib.suppress(Exception):
                state.register_loose_files()
    finally:
        capture.uninstall()
        os.chdir(cwd)
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    duration = time.perf_counter() - t0
    status, error = _classify(failure, guard, timeout, str(filename))
    return status, error, duration


def _cancelled(message: str = "interrupted") -> dict:
    return {"type": "Cancelled", "message": message, "line": None, "summary": "cancelled"}


def _classify(failure, guard, timeout, filename) -> tuple[str, dict | None]:
    # By the guard's flags (see _platform.RunControl), not by what the script ended with. A deadline
    # counts when its signal or its time came while the script could still be interrupted —
    # whether or not that stopped the script — and a cancel that came before it takes the region's
    # one interruption, after which the deadline no longer counts. So a run with both flags set
    # reached its deadline before anything stopped it: `timeout`.
    if guard.expired:
        return "timeout", {"type": "Timeout", "message": f"exceeded {timeout:.0f} s", "line": None,
                           "summary": f"timed out after {timeout:.0f} s"}
    if guard.cancelled or isinstance(failure, (KeyboardInterrupt, _platform.Interrupted)):
        return "cancelled", _cancelled()
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
    # No `before` means the run was cancelled before its script: it wrote nothing.
    outputs = [] if before is None else guarded(
        "outputs", lambda: diff_outputs(project, before, tree_stat(project), state.cache), [])
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


def reserved_name_warnings(paths) -> list[str]:
    """Warnings for recorded paths that use a Windows device name in any component.

    Grouped by the first such component, because `data/derived/con/value.parquet` is as unopenable
    on Windows as `con.parquet` is, and a directory of a thousand partitions is one problem, not a
    thousand. Beyond MAX_LISTED_PATHS groups it is one warning, like external inputs."""
    groups: dict[str, list[str]] = {}
    for rel in paths:
        parts = str(rel).split("/")
        for depth, part in enumerate(parts):
            if _platform.reserved_name(part):
                groups.setdefault("/".join(parts[:depth + 1]), []).append(str(rel))
                break
    tail = "this project cannot be opened there until it is renamed"
    if len(groups) > MAX_LISTED_PATHS:
        return [f"{len(groups):,} paths use a reserved device name on Windows, e.g. "
                + ", ".join(sorted(groups)[:MAX_LISTED_PATHS]) + f"; {tail}"]
    out = []
    for prefix, members in sorted(groups.items()):
        if members == [prefix]:
            out.append(f"{prefix} uses a reserved device name on Windows; {tail}")
        else:
            out.append(f"{prefix}/ uses a reserved device name on Windows ({len(members):,} "
                       f"file(s) under it, e.g. {sorted(members)[0]}); {tail}")
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
                        f"'{name}'; read analytics.read('{name}') instead so the run stops depending on "
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
    # Last of all, so every warning main gave comes first: the agent's reply shows ten and the run
    # page twelve, and a partitioned write under a reserved directory would otherwise push them out.
    written = [out["path"] for out in record["outputs"] if out.get("change") != "deleted"]
    written += [rel for chart in record["charts"] for rel in (chart.get("files") or {}).values()]
    warnings += reserved_name_warnings(written)
    return warnings
