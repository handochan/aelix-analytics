"""Measure the portable boundaries on the platform this runs on, and write down what happened.

    python tools/platform_smoke.py --output <file.json> [--stdlib-only] [--only NAME,...]

Each check does one thing a port in src/aelix_analytics/_platform.py or the aelix_analytics CLI promises, for real, on
this machine, and records what it observed. A check either holds or it does not. When it does not,
in exactly the form a document of the tree being measured states as a limitation of this platform,
and the document still says so in the words DECLARED quotes, the record names the declaration, so
a known limit is neither read as a pass nor hidden as a failure. An observation records a platform
fact without a promise attached.

`--stdlib-only` runs only what the aelix process itself loads (core and _platform). It needs neither
uv nor the analysis libraries, so it can run on the entry-point floor being measured.

Exit status: 0 when no check was violated, 1 otherwise, 2 on usage error. A check that holds, fails
only in a declared way, is an observation, or could not run (`not_run`: uv missing) does not count
against it. With `--gate required` the status is the `required` block's verdict instead: every
check that makes a promise among those this invocation selected has to have run and either held or
shown only a limitation the documents declare; a check `--only` or `--stdlib-only` left out is not
judged at all. The report carries the block either way. Nothing here is a test of Windows until it has actually run on
Windows; the output says where it ran.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import locale
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "src"
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))

from aelix_analytics import _platform  # noqa: E402 - the kit is found through KIT above

SCHEMA = "aelix-analytics.platform-smoke/1"
ANALYSIS_MODULES = ("polars", "duckdb", "pyarrow", "matplotlib", "plotly", "numpy")
WINDOWS = os.name == "nt"

# The limitations a document of the tree being measured states, by the check that would show them,
# with the words it states them in. A check reports one as declared only while that document says
# so (declared) and only on Windows: on POSIX every one of these promises is meant to hold. The
# last two were measured as violations first (run 35977785169); until the tree states them, a run
# that shows them reports them as violated rather than borrowing a declaration.
DECLARED = {
    "kill_tree": ("docs/CONTRACTS.md", "`taskkill /T`는 부모가 이미 끝난 자손에 닿지 않습니다"),
    "gil": ("docs/CONTRACTS.md",
            "GIL을 쥔 채 도는 C 코드 안의 주 스레드에는 그 호출이 반환할 때까지 취소가 닿지 않습니다"),
    "subprocess_wait": ("docs/CONTRACTS.md",
                        "SIGINT 이벤트를 보지 않는 대기(자식 프로세스를 기다리는 `subprocess` 호출 등)는 "
                        "취소가 와도 그 대기가 끝날 때까지 이어집니다"),
}
# Every check that makes a promise is required on every platform. Until the documents declared
# them, the two Windows limitations above were exempt instead (Issue #8, design revision 3 section
# 4); nothing is exempt now.
# How long the extension waits for a kit it asked to stop before it ends the tree by force
# (extension/aelix_lab.py GRACE_SECONDS; a test keeps the two equal). A stop that takes longer is
# a forced one there, so it is one here.
EXTENSION_GRACE_SECONDS = 10


def declared(key: str) -> str | None:
    """`document — words` for the limitation `key` while the tree being measured states it in those
    words (line breaks aside), else None: a check whose limitation the tree does not state reports
    a violation."""
    path, words = DECLARED[key]
    try:
        text = (ROOT / path).read_text(encoding="utf-8")
    except OSError:
        return None
    return f"{path} — {words}" if " ".join(words.split()) in " ".join(text.split()) else None

CHECKS: list[tuple[str, str, object]] = []


def check(group: str, *, observation: bool = False, needs_uv: bool = False,
          only_when_named: bool = False):
    def register(fn):
        fn.observation = observation
        fn.needs_uv = needs_uv
        fn.only_when_named = only_when_named
        CHECKS.append((group, fn.__name__, fn))
        return fn
    return register


# --- helpers ------------------------------------------------------------------------------------

class Context:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.bundle = _platform.detect()
        self.uv = shutil.which("uv")
        self._project: Path | None = None
        self._n = 0
        self.leftovers: list[int] = []

    def fresh(self, label: str) -> Path:
        self._n += 1
        path = self.root / f"{self._n:02d}-{label}"
        path.mkdir(parents=True)
        return path


def child_env(**extra) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(KIT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.update(extra)
    return env


def write_script(directory: Path, name: str, source: str) -> Path:
    path = directory / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return path


def spawn(path: Path, *args, env=None, **popen) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(path), *map(str, args)], env=env or child_env(),
                            stdin=subprocess.DEVNULL, **popen)


def wait_for_text(path: Path, timeout: float) -> str | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with contextlib.suppress(OSError):
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        time.sleep(0.05)
    return None


def wait_gone(ctx: Context, pid: int, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not ctx.bundle.processes.alive(pid):
            return True
        time.sleep(0.1)
    return False


def force_kill(pid: int) -> None:
    """Clean-up only, never part of a check's result."""
    with contextlib.suppress(Exception):
        if WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=30,
                           check=False)
        else:
            os.kill(pid, signal.SIGKILL)


def finish(proc: subprocess.Popen, timeout: float) -> int | None:
    try:
        return proc.wait(timeout)
    except subprocess.TimeoutExpired:
        return None


def captured_stderr(fn, *args):
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        value = fn(*args)
    return value, buffer.getvalue().strip()


# --- the ports ------------------------------------------------------------------------------------

LOCK_HOLDER = """\
import sys, time
from pathlib import Path
from aelix_analytics import _platform
lock, ready, hold = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
with _platform.detect().locks.exclusive(lock):
    ready.write_text("held", encoding="utf-8")
    time.sleep(hold)
"""

READER = """\
import sys, time
from pathlib import Path
target, ready, hold = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
with open(target, "rb") as fh:
    fh.read()
    ready.write_text("open", encoding="utf-8")
    time.sleep(hold)
"""

SLEEPER = """\
import time
time.sleep(120)
"""

INTERRUPTIBLE = """\
import signal, sys, time
from pathlib import Path
ready, marker = Path(sys.argv[1]), Path(sys.argv[2])

def graceful(signum, frame):
    marker.write_text(f"graceful:{signum}", encoding="utf-8")
    sys.exit(130)

for name in ("SIGINT", "SIGBREAK", "SIGTERM"):
    if hasattr(signal, name):
        signal.signal(getattr(signal, name), graceful)
ready.write_text("ready", encoding="utf-8")
for _ in range(1200):
    time.sleep(0.1)
"""

TREE = """\
import subprocess, sys, time
from pathlib import Path
record, mode = Path(sys.argv[1]), sys.argv[2]
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
record.write_text(str(grandchild.pid), encoding="utf-8")
if mode == "stay":
    time.sleep(120)
"""


@check("ports")
def imports_without_analysis_libraries(ctx):
    from aelix_analytics import core  # noqa: F401 - importing is the check

    loaded = sorted(name for name in ANALYSIS_MODULES if name in sys.modules)
    expected = "windows" if WINDOWS else "posix"
    return {"holds": not loaded and ctx.bundle.name == expected,
            "observed": {"bundle": ctx.bundle.name, "analysis_modules_loaded": loaded}}


@check("ports")
def lock_excludes_another_process(ctx):
    d = ctx.fresh("lock-exclude")
    holder = spawn(write_script(d, "holder", LOCK_HOLDER), d / "lock", d / "ready", 2.0)
    try:
        if wait_for_text(d / "ready", 60) is None:
            return {"holds": False, "observed": {"holder": "never took the lock"}}
        t0 = time.perf_counter()
        with ctx.bundle.locks.exclusive(d / "lock"):
            waited = time.perf_counter() - t0
        return {"holds": waited >= 1.0,
                "observed": {"waited_s": round(waited, 3), "holder_kept_it_s": 2.0}}
    finally:
        finish(holder, 30)


@check("ports")
def lock_released_when_its_holder_is_killed(ctx):
    d = ctx.fresh("lock-release")
    holder = spawn(write_script(d, "holder", LOCK_HOLDER), d / "lock", d / "ready", 120)
    if wait_for_text(d / "ready", 60) is None:
        holder.kill()
        return {"holds": False, "observed": {"holder": "never took the lock"}}
    holder.kill()
    finish(holder, 30)
    acquired = threading.Event()

    def take():
        with ctx.bundle.locks.exclusive(d / "lock"):
            acquired.set()

    threading.Thread(target=take, daemon=True).start()
    ok = acquired.wait(15)
    return {"holds": ok, "observed": {"acquired_within_15s_after_the_kill": ok}}


@check("ports")
def replace_while_a_reader_holds_the_file(ctx):
    """The promise: a short-lived reader is waited out, and a long one fails loudly, never silently."""
    observed, holds = {}, True
    for hold in (0.7, 5.0):
        d = ctx.fresh(f"replace-{hold}")
        target = d / "record.json"
        target.write_bytes(b"old")
        reader = spawn(write_script(d, "reader", READER), target, d / "ready", hold)
        error, seconds = None, None
        try:
            if wait_for_text(d / "ready", 60) is None:
                return {"holds": False, "observed": {"reader": "never opened the file"}}
            tmp = d / ".record.json.tmp"
            tmp.write_bytes(b"new")
            t0 = time.perf_counter()
            try:
                ctx.bundle.files.replace(tmp, target)
            except OSError as exc:
                error = f"{type(exc).__name__}: {exc} (winerror={getattr(exc, 'winerror', None)})"
            seconds = round(time.perf_counter() - t0, 3)
        finally:
            finish(reader, 60)
        content = target.read_bytes().decode()
        loud = (error is None and content == "new") or (error is not None and content == "old")
        observed[f"reader_holds_{hold}s"] = {"replaced": error is None, "error": error,
                                              "seconds": seconds, "content_after": content}
        holds = holds and loud and (hold > 2.0 or error is None)
    return {"holds": holds, "observed": observed}


@check("ports")
def liveness_of_a_live_an_exited_and_a_bogus_pid(ctx):
    d = ctx.fresh("alive")
    sleeper = spawn(write_script(d, "sleeper", SLEEPER), **ctx.bundle.processes.spawn_kwargs())
    try:
        live = ctx.bundle.processes.alive(sleeper.pid)
    finally:
        sleeper.kill()
        finish(sleeper, 30)
    exited = ctx.bundle.processes.alive(sleeper.pid)
    bogus = {repr(value): ctx.bundle.processes.alive(value) for value in (None, "x", 0, -1)}
    holds = live and not exited and not any(bogus.values())
    return {"holds": holds, "observed": {"live": live, "exited": exited, "bogus": bogus}}


@check("ports")
def interrupt_asks_a_python_child_or_declines(ctx):
    """POSIX asks with SIGINT and the child stops gracefully. Windows sends no signal — its one
    console event ends a `uv run` wrapper at once (see the probes below) — and without a request
    file it declines; with one, it creates the file."""
    d = ctx.fresh("interrupt")
    proc = spawn(write_script(d, "child", INTERRUPTIBLE), d / "ready", d / "marker",
                 **ctx.bundle.processes.spawn_kwargs())
    if wait_for_text(d / "ready", 60) is None:
        ctx.bundle.processes.kill_tree(proc.pid)
        return {"holds": False, "observed": {"child": "never became ready"}}
    request = d / "cancel-request"
    if WINDOWS:
        created = ctx.bundle.processes.interrupt(proc, request) and request.exists()
        if not created:
            ctx.bundle.processes.kill_tree(proc.pid)
            return {"holds": False, "observed": {"request_created": False}}
    t0 = time.perf_counter()
    asked = ctx.bundle.processes.interrupt(proc)
    code, forced = finish(proc, 15 if asked else 2), False
    if code is None:
        forced = True
        ctx.bundle.processes.kill_tree(proc.pid)
        code = finish(proc, 30)
    marker = wait_for_text(d / "marker", 1)
    graceful = bool(marker and marker.startswith("graceful"))
    holds = (asked and graceful and not forced) if not WINDOWS else (asked is False and not marker)
    return {"holds": holds,
            "observed": {"asked": asked, "exit_code": code, "marker": marker, "forced": forced,
                         "seconds": round(time.perf_counter() - t0, 3)}}


def _tree(ctx, label: str, mode: str):
    d = ctx.fresh(label)
    proc = spawn(write_script(d, "tree", TREE), d / "grandchild", mode,
                 **ctx.bundle.processes.spawn_kwargs())
    text = wait_for_text(d / "grandchild", 60)
    return proc, (int(text) if text else None)


@check("ports")
def kill_tree_ends_the_child_and_its_grandchild(ctx):
    proc, grandchild = _tree(ctx, "tree-stay", "stay")
    if grandchild is None:
        ctx.bundle.processes.kill_tree(proc.pid)
        return {"holds": False, "observed": {"child": "never started its grandchild"}}
    _, diagnostic = captured_stderr(ctx.bundle.processes.kill_tree, proc.pid)
    child_gone = finish(proc, 30) is not None
    grandchild_gone = wait_gone(ctx, grandchild, 15)
    if not grandchild_gone:
        force_kill(grandchild)
    return {"holds": child_gone and grandchild_gone,
            "observed": {"child_gone": child_gone, "grandchild_gone": grandchild_gone,
                         "diagnostic": diagnostic or None}}


# What the kit writes to stderr when taskkill could not end a tree (_platform's kill_tree).
KIT_TREE_WORDS = "could not end the process tree"


@check("ports")
def kill_tree_after_the_leader_exited(ctx):
    """The case the extension's abort path exists for: uv exits, its child keeps the pipes."""
    proc, grandchild = _tree(ctx, "tree-leave", "leave")
    finish(proc, 30)
    if grandchild is None:
        return {"holds": False, "observed": {"leader": "never started its grandchild"}}
    _, diagnostic = captured_stderr(ctx.bundle.processes.kill_tree, proc.pid)
    gone = wait_gone(ctx, grandchild, 15)
    if not gone:
        force_kill(grandchild)
    # The stated form: the descendant survived, and the kit said so, in its own words, instead of
    # counting it ended (any other text on stderr is not that).
    stated = WINDOWS and not gone and KIT_TREE_WORDS in (diagnostic or "")
    return {"holds": gone, "declared": declared("kill_tree") if stated else None,
            "observed": {"leader_exit_code": proc.returncode, "grandchild_gone": gone,
                         "diagnostic": diagnostic or None}}


def _deadline(ctx, body) -> dict:
    t0 = time.perf_counter()
    interrupted_by, failure = None, None
    with ctx.bundle.runs.guard() as guard:
        try:
            with guard.interruptible(deadline=1.0):
                body(t0)
        except (_platform.Interrupted, KeyboardInterrupt) as exc:
            interrupted_by, failure = type(exc).__name__, exc
        # As the runner settles after every region, with what the region raised: that is where a
        # cancel cut short is counted, and where revision 3.3 also counted every Windows deadline as
        # a cancel (review 9, guard-2), which this check did not see without it.
        guard.settle(failure)
    return {"interrupted_by": interrupted_by, "flag_set": guard.expired,
            "cancel_flag": guard.cancelled, "seconds": round(time.perf_counter() - t0, 3)}


@check("ports")
def deadline_interrupts_python_code(ctx):
    def busy(t0):
        while time.perf_counter() - t0 < 15:
            pass

    observed = _deadline(ctx, busy)
    return {"holds": _deadline_held(observed), "observed": observed}


@check("ports")
def deadline_interrupts_a_sleep(ctx):
    observed = _deadline(ctx, lambda _t0: time.sleep(15))
    return {"holds": _deadline_held(observed), "observed": observed}


def _deadline_held(observed: dict) -> bool:
    # Interrupted in time, counted as the deadline, and not also as a cancel: on Windows the
    # deadline arrives through the SIGINT a cancel uses (review 4 CH-16, review 6 CH-10).
    return bool(observed["interrupted_by"]) and observed["flag_set"] \
        and not observed["cancel_flag"] and observed["seconds"] < 5


@check("ports")
def the_cancel_request_wakes_a_sleeping_run(ctx):
    """The kit's half of the ask. Windows: the request file, watched by a thread, interrupts a main
    thread asleep inside a guarded region. POSIX: nothing to watch — the ask is SIGINT — so no
    thread is started."""
    d = ctx.fresh("cancel-request")
    request = d / "cancel-request"
    runs = ctx.bundle.runs
    if not WINDOWS:
        before = threading.active_count()
        stop = runs.watch_for_cancel(request)
        started = threading.active_count() - before
        stop()
        return {"holds": started == 0, "observed": {"threads_started": started}}
    stop = runs.watch_for_cancel(request)
    threading.Timer(1.0, request.touch).start()
    t0 = time.perf_counter()
    woken_by = None
    try:
        with runs.guard() as guard:
            try:
                with guard.interruptible():
                    time.sleep(15)
            except (_platform.Interrupted, KeyboardInterrupt) as exc:
                woken_by = type(exc).__name__
    finally:
        stop()
    seconds = round(time.perf_counter() - t0, 3)
    return {"holds": bool(woken_by) and seconds < 5 and guard.cancelled,
            "observed": {"woken_by": woken_by, "seconds": seconds, "timeout": guard.expired,
                         "cancel": guard.cancelled}}


@check("ports")
def the_guard_records_outside_regions_and_interrupts_inside_once(ctx):
    before = {name: signal.getsignal(getattr(signal, name))
              for name in ("SIGINT", "SIGTERM", "SIGBREAK", "SIGALRM") if hasattr(signal, name)}
    names = ["SIGINT"] + (["SIGBREAK"] if hasattr(signal, "SIGBREAK") else [])
    observed = {}
    for name in names:
        signum = getattr(signal, name)
        raised, second = None, None
        with ctx.bundle.runs.guard() as guard:
            signal.raise_signal(signum)
            time.sleep(0.2)
            recorded = guard.cancelled
            guard.cancelled = False  # or the region below would raise as it is entered
            try:
                with guard.interruptible():
                    try:
                        signal.raise_signal(signum)
                        time.sleep(0.5)
                    except (_platform.Interrupted, KeyboardInterrupt) as exc:
                        raised = type(exc).__name__  # swallowed, as a script may
                    try:
                        signal.raise_signal(signum)  # a second one in the same region
                        time.sleep(0.2)
                    except BaseException as exc:  # noqa: BLE001 - recorded as a violation
                        second = type(exc).__name__
            except BaseException as exc:  # noqa: BLE001 - recorded as a violation
                second = f"escaped the region: {type(exc).__name__}"
            try:
                signal.raise_signal(signum)
                time.sleep(0.2)
            except BaseException as exc:  # noqa: BLE001 - recorded as a violation
                third = type(exc).__name__
            else:
                third = None
        observed[name] = {"recorded_without_raising": recorded, "region_raised": raised,
                          "second_in_the_region": second or "only recorded",
                          "after_the_region": third or "only recorded"}
    after = {name: signal.getsignal(getattr(signal, name)) for name in before}
    restored = after == before
    holds = restored and all(v["recorded_without_raising"] and v["region_raised"]
                             and v["second_in_the_region"] == "only recorded"
                             and v["after_the_region"] == "only recorded"
                             for v in observed.values())
    return {"holds": holds, "observed": {**observed, "handlers_restored": restored}}


@check("ports")
def base_env_is_enough_to_start_a_child(ctx):
    path_only = {"PATH": os.environ.get("PATH", "")}
    with_base = {**ctx.bundle.processes.base_env(), **path_only}

    def trial(env, argv):
        try:
            done = subprocess.run(argv, env=env, capture_output=True, timeout=120,
                                  **_platform.text_kwargs())
            return {"exit": done.returncode, "stderr": done.stderr.strip()[-300:] or None}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"exit": None, "error": f"{type(exc).__name__}: {exc}"}

    python = [sys.executable, "-c", "print('ok')"]
    observed = {"base_env_keys": sorted(ctx.bundle.processes.base_env()),
                "python_with_base_env": trial(with_base, python),
                "python_with_path_only": trial(path_only, python),
                "python_with_empty_env": trial({}, python)}
    if ctx.uv:
        observed["uv_with_base_env"] = trial(with_base, [ctx.uv, "--version"])
        observed["uv_with_path_only"] = trial(path_only, [ctx.uv, "--version"])
    holds = observed["python_with_base_env"]["exit"] == 0 and (
        not ctx.uv or observed["uv_with_base_env"]["exit"] == 0)
    return {"holds": holds, "observed": observed}


@check("ports", observation=True)
def device_names_on_this_file_system(ctx):
    """What the file system does with the names the kit refuses or renames. No promise attached:
    this is the evidence for whether the rule matters on the Windows version it ran on."""
    d = ctx.fresh("reserved")
    observed = {}
    # AUX, COM1 and LPT1 are left out on purpose: opening a serial or printer device can block.
    # CON and NUL are safe to open: the console and the null device.
    for name in ("con", "nul", "con.txt", "nul.parquet", f"{_platform.portable_stem('con')}.png"):
        path = d / name
        try:
            with open(path, "wb") as fh:
                fh.write(b"x")
            observed[name] = {"written": True, "listed_as_a_file": name in os.listdir(d)}
        except OSError as exc:
            observed[name] = {"written": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"holds": True, "observed": observed}


def _made(make, path) -> dict:
    try:
        make(path)
        return {"made": True}
    except OSError as exc:
        return {"made": False, "type": type(exc).__name__, "errno": exc.errno,
                "winerror": getattr(exc, "winerror", None), "strerror": exc.strerror}


@check("ports", observation=True)
def long_names_on_this_file_system(ctx):
    """What open() and os.mkdir() raise for a name longer than file systems hold, and what a path
    past Windows' old 260-character limit does. No promise attached: the evidence for how the kit
    tells a snapshot that could not be written for its length (runner._write_failure_hint)."""
    d = ctx.fresh("long")
    deep = d
    while len(str(deep)) <= 260:
        deep = deep / ("d" * 40)
    observed = {"name_300.open": _made(lambda p: open(p, "wb").close(), d / ("a" * 300 + ".py")),
                "name_300.mkdir": _made(os.mkdir, d / ("a" * 300)),
                "path_over_260.makedirs": _made(os.makedirs, deep)}
    if observed["path_over_260.makedirs"]["made"]:
        observed["path_over_260.open"] = _made(lambda p: open(p, "wb").close(), deep / "a.py")
    observed["path_over_260.length"] = len(str(deep / "a.py"))
    return {"holds": True, "observed": observed}


@check("ports")
def file_url_leads_back_to_the_file(ctx):
    d = ctx.fresh("file-url")
    target = d / "입력 데이터.csv"
    target.write_text("a\n1\n", encoding="utf-8")
    url = _platform.file_url(target.resolve())
    parsed = urllib.parse.urlparse(url)
    back = Path(urllib.request.url2pathname(parsed.path if not parsed.netloc
                                            else f"//{parsed.netloc}{parsed.path}"))
    holds = parsed.scheme == "file" and back.exists() and back.resolve() == target.resolve()
    return {"holds": holds, "observed": {"url": url, "back": str(back)}}


@check("ports")
def utf8_survives_a_pipe(ctx):
    d = ctx.fresh("utf8")
    text = "한글 출력 ✓"
    configured = write_script(d, "configured",
                              "import sys\nfrom aelix_analytics import _platform\n_platform.configure_stdio()\n"
                              f"print({text!r})\n")
    plain = write_script(d, "plain", f"print({text!r})\n")
    env = child_env()
    for name in ("PYTHONIOENCODING", "PYTHONUTF8"):
        env.pop(name, None)

    def run(path):
        done = subprocess.run([sys.executable, str(path)], env=env, capture_output=True, timeout=60,
                              **_platform.text_kwargs())
        return {"exit": done.returncode, "stdout": done.stdout.strip(),
                "stderr": done.stderr.strip()[-300:] or None}

    with_configure, without = run(configured), run(plain)
    return {"holds": with_configure["exit"] == 0 and with_configure["stdout"] == text,
            "observed": {"with_configure_stdio": with_configure, "without_it": without}}


# --- the CLI, driven the way the extension drives it ----------------------------------------------

ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ",
             "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
             "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
             "AELIX_LAB_TRUST_FILE")


def kit_env(ctx) -> dict[str, str]:
    """What extension/aelix_lab.py's child_env gives the kit: the platform's base, the allow-list,
    the uv settings, and the kit's own variables. Where it differs, on purpose: the allow-list has
    AELIX_LAB_TRUST_FILE, so a measurement never touches the user's trust list, and not the
    lower-case proxy names; UV_* is passed without the extension's SECRET_NAME filter; there is no
    LAB_SESSION (no aelix session is running), and LAB_ACTOR is `smoke`. A test builds both
    environments from the same inputs and allows only these differences."""
    env = ctx.bundle.processes.base_env()
    env.update({k: os.environ[k] for k in ENV_ALLOW if k in os.environ})
    env.update({k: v for k, v in os.environ.items() if k.startswith("UV_")})
    env.update({"PYTHONPATH": str(KIT), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "MPLBACKEND": "Agg", "LAB_ACTOR": "smoke"})
    return env


def kit_cli_argv(ctx, project: Path, *args) -> list[str]:
    return [ctx.uv, "run", "--project", str(project), "--quiet", "python", "-m", "aelix_analytics",
            "--project", str(project), *map(str, args)]


def kit_cli(ctx, project: Path, *args, timeout: float = 600):
    done = subprocess.run(kit_cli_argv(ctx, project, *args), cwd=project, env=kit_env(ctx),
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                          **_platform.text_kwargs())
    result = None
    for line in reversed(done.stdout.splitlines()):
        if line.strip().startswith("{"):
            with contextlib.suppress(ValueError):
                result = json.loads(line)
                break
    return done.returncode, result, done


def project(ctx) -> Path:
    if ctx._project is not None:
        return ctx._project
    root = ctx.fresh("project") / "실측 프로젝트"
    init = subprocess.run([sys.executable, "-m", "aelix_analytics", "init", str(root), "--title", "플랫폼 실측",
                           "--goal", "포트와 CLI를 이 플랫폼에서 실제로 실행한다"],
                          env=child_env(), capture_output=True, timeout=120,
                          **_platform.text_kwargs())
    if init.returncode != 0:
        raise RuntimeError(f"aelix_analytics init failed: {init.stdout[-800:]} {init.stderr[-800:]}")
    sync = subprocess.run([ctx.uv, "sync", "--project", str(root), "--quiet"], env=kit_env(ctx),
                          capture_output=True, timeout=1200, **_platform.text_kwargs())
    if sync.returncode != 0:
        raise RuntimeError(f"uv sync failed: {sync.stderr[-1500:]}")
    csv_path = root.parent / "매출.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["order_date", "region", "revenue"])
        for i in range(120):
            writer.writerow([f"2025-{1 + i % 12:02d}-01", ["서울", "부산", "대구"][i % 3],
                             1000 + (i * 37) % 500])
    code, result, done = kit_cli(ctx, root, "import", csv_path, "--name", "sales",
                                "--description", "합성 매출")
    if code != 0:
        raise RuntimeError(f"import failed: {result} {done.stderr[-800:]}")
    ctx._project = root
    return root


def script(root: Path, name: str, source: str) -> str:
    path = root / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return f"scripts/{name}"


def run_record(root: Path, run_id: str) -> dict:
    return json.loads((root / "runs" / run_id / "run.json").read_text(encoding="utf-8"))


REGION = """\
import matplotlib.pyplot as plt
import aelix_analytics as analytics

sales = analytics.read("sales")
by_region = analytics.sql("SELECT region, sum(revenue) AS revenue FROM sales GROUP BY 1 ORDER BY 1").pl()
analytics.save(by_region, "region_revenue")
fig, ax = plt.subplots()
ax.bar(by_region["region"], by_region["revenue"])
ax.set_title("지역별 매출")
analytics.chart(fig, "region-revenue", title="지역별 매출", caption="합계", data=by_region)
analytics.note("finding", "서울 매출이 가장 크다")
print("한글 출력", by_region.height)
"""


@check("cli", needs_uv=True)
def cli_end_to_end(ctx):
    root = project(ctx)
    steps = {}

    def step(name, *args, ok=0):
        code, result, done = kit_cli(ctx, root, *args)
        steps[name] = {"exit": code, "ok": bool(result and result.get("ok")),
                       "stderr": done.stderr.strip()[-400:] or None}
        return code == ok, result

    good, result = step("query", "query", "SELECT region, sum(revenue) AS r FROM sales GROUP BY 1 "
                        "ORDER BY 1", "--limit", "5")
    regions = [row[0] for row in (result or {}).get("rows") or []]
    steps["query"]["regions"] = regions
    all_ok = good and "서울" in regions

    good, result = step("run", "run", script(root, "01_region.py", REGION), "--title", "지역별 매출")
    record = (result or {}).get("run") or {}
    steps["run"].update({"status": record.get("status"), "charts": len(record.get("charts") or []),
                         "stdout_has_korean": "한글 출력" in ((result or {}).get("stdout_tail") or "")})
    all_ok = all_ok and good and record.get("status") == "ok" and steps["run"]["stdout_has_korean"]
    run_id = record.get("id")

    good, _ = step("note", "note", "finding", "측정 완료", "--ref", run_id or "none")
    all_ok = all_ok and good
    good, result = step("rerun", "run", "--rerun-of", run_id or "none", "--title", "재실행")
    verdict = (((result or {}).get("run") or {}).get("reproduction") or {}).get("verdict")
    steps["rerun"]["verdict"] = verdict
    all_ok = all_ok and good and verdict == "match"
    good, _ = step("report", "report")
    index = root / "report" / "index.html"
    steps["report"]["index_exists"] = index.is_file()
    all_ok = all_ok and good and index.is_file()
    good, result = step("status", "status")
    steps["status"]["title"] = (((result or {}).get("status") or {}).get("project") or {}).get("title")
    all_ok = all_ok and good and steps["status"]["title"] == "플랫폼 실측"
    good, _ = step("context", "context")
    all_ok = all_ok and good
    return {"holds": all_ok, "observed": steps}


BUSY = "while True:\n    pass\n"
DUCKDB = "import duckdb\nduckdb.sql('SELECT sum(i) FROM range(200000000000) t(i)').fetchall()\n"


def _timed_run(ctx, name: str, source: str, timeout: int) -> dict:
    root = project(ctx)
    t0 = time.perf_counter()
    try:
        code, result, _done = kit_cli(ctx, root, "run", script(root, name, source), "--title", name,
                                     "--timeout", str(timeout), timeout=600)
    except subprocess.TimeoutExpired:
        return {"status": None, "exit": None, "cli_seconds": round(time.perf_counter() - t0, 1),
                "note": "the CLI itself did not return within 600 s"}
    record = (result or {}).get("run") or {}
    return {"status": record.get("status"), "exit": code, "recorded_s": record.get("duration_s"),
            "cli_seconds": round(time.perf_counter() - t0, 1)}


@check("cli", needs_uv=True)
def cli_run_timeout_in_python_code(ctx):
    observed = _timed_run(ctx, "02_busy.py", BUSY, 3)
    holds = observed["status"] == "timeout" and (observed["recorded_s"] or 99) < 13
    return {"holds": holds, "observed": observed}


@check("cli", needs_uv=True)
def cli_run_timeout_in_a_blocking_duckdb_query(ctx):
    observed = _timed_run(ctx, "03_duckdb.py", DUCKDB, 3)
    holds = observed["status"] == "timeout" and (observed["recorded_s"] or 99) < 13
    return {"holds": holds, "observed": observed}


def _cancel_a_cli_run(ctx, name: str, source: str, *, wait: float) -> dict:
    """Spawn `uv run ... aelix_analytics run` in its own group, wait until its run is recorded as running,
    ask it to stop exactly as the extension does — ProcessControl.interrupt, with a request file
    where the platform asks through one — and give it the extension's grace period."""
    root = project(ctx)
    rel = script(root, name, source)
    processes = ctx.bundle.processes
    logs = root.parent / f"{Path(name).stem}-logs"
    logs.mkdir(exist_ok=True)
    request = logs / "cancel-request" if processes.asks_by_request else None
    env = {**kit_env(ctx), **({"LAB_CANCEL_FILE": str(request)} if request else {})}
    with open(logs / "stdout.txt", "wb") as out, open(logs / "stderr.txt", "wb") as err:
        proc = subprocess.Popen(kit_cli_argv(ctx, root, "run", rel, "--title", Path(name).stem),
                                cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=err, **processes.spawn_kwargs())
        run_id, kit_pid = None, None
        end = time.monotonic() + 300
        while time.monotonic() < end and run_id is None:
            for record_path in sorted((root / "runs").glob("*/run.json")):
                with contextlib.suppress(OSError, ValueError):
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    if (record.get("script") or {}).get("path") == rel \
                            and record.get("status") == "running":
                        run_id, kit_pid = record_path.parent.name, record.get("pid")
            time.sleep(0.2)
        if run_id is None:
            processes.kill_tree(proc.pid)
            finish(proc, 30)
            return {"run": "never started within 300 s", "recorded_status": None, "forced": None}
        time.sleep(wait)
        t0 = time.perf_counter()
        asked = processes.interrupt(proc, request)  # exactly what the extension does
        code, forced = finish(proc, EXTENSION_GRACE_SECONDS), False
        if code is None:
            forced = True
            processes.kill_tree(proc.pid)
            code = finish(proc, 30)
        seconds = round(time.perf_counter() - t0, 2)
    kit_gone = wait_gone(ctx, int(kit_pid), 15) if kit_pid else None
    if kit_pid and not kit_gone:
        force_kill(int(kit_pid))
    record = run_record(root, run_id)
    listed = next((r.get("status") for r in _list_runs(root) if r.get("id") == run_id), None)
    return {"asked": asked, "recorded_status": record.get("status"), "listed_status": listed,
            "exit_code": code, "exit_code_hex": hex(code) if code is not None else None,
            "forced": forced, "seconds_to_exit": seconds, "kit_process_gone": kit_gone,
            "stdout_tail": (record.get("stdout") and _tail_text(root / record["stdout"]))}


def _tail_text(path: Path) -> str | None:
    with contextlib.suppress(OSError):
        return path.read_text(encoding="utf-8", errors="replace")[-400:]
    return None


def _cancelled_in_time(observed: dict) -> bool:
    return observed.get("recorded_status") == "cancelled" and observed.get("forced") is False \
        and bool(observed.get("kit_process_gone"))


def _ended_as_declared(observed: dict) -> bool:
    """The form docs/CONTRACTS.md G states for a cancel that cannot land on Windows: the
    extension's grace ran out, the run was ended by force, its record was left `running` — listed
    `incomplete` — and the kit process is gone. Any other outcome is not that limitation."""
    return observed.get("forced") is True and observed.get("recorded_status") == "running" \
        and observed.get("listed_status") == "incomplete" \
        and observed.get("kit_process_gone") is True


SLOW = """\
import time
print("started", flush=True)
time.sleep(300)
"""


@check("cli", needs_uv=True)
def cli_cancel_the_way_the_extension_does(ctx):
    """One long sleep: the hard case, which neither the old console event nor _thread.interrupt_main
    could wake on Windows. No declaration applies: the contracts declare that a *forced*
    termination on Windows cannot be recorded as cancelled, not that the ask fails."""
    observed = _cancel_a_cli_run(ctx, "04_slow.py", SLOW, wait=1.0)
    return {"holds": _cancelled_in_time(observed), "observed": observed}


CATASTROPHIC = """\
import re
print("started", flush=True)
re.search(r"(a+)+$", "a" * 33 + "b")
print("regex done", flush=True)
"""


@check("cli", needs_uv=True)
def cli_cancel_reaches_a_run_inside_a_regular_expression(ctx):
    """_sre holds the GIL and checks for signals. A signal's C handler needs no GIL; a Python thread
    does. So POSIX, which asks with SIGINT, stops it at once, and Windows, which asks through a
    thread watching the request file, cannot until the expression returns."""
    observed = _cancel_a_cli_run(ctx, "08_regex.py", CATASTROPHIC, wait=1.5)
    holds = _cancelled_in_time(observed)
    stated = WINDOWS and not holds and _ended_as_declared(observed)
    return {"holds": holds, "declared": declared("gil") if stated else None, "observed": observed}


WAITS_ON_A_CHILD = """\
import subprocess, sys
print("started", flush=True)
subprocess.run([sys.executable, "-c", "import time; time.sleep(120)"])
print("child done", flush=True)
"""


@check("cli", needs_uv=True)
def cli_cancel_reaches_a_run_waiting_on_a_subprocess(ctx):
    """A script blocked in `subprocess.run` with no timeout (review 6, AS-4). POSIX: the SIGINT
    interrupts the wait. Windows: the wait is WaitForSingleObject on the child alone, which the
    SIGINT event does not end, so the ask cannot land until the child exits."""
    observed = _cancel_a_cli_run(ctx, "09_child.py", WAITS_ON_A_CHILD, wait=1.5)
    holds = _cancelled_in_time(observed)
    stated = WINDOWS and not holds and _ended_as_declared(observed)
    return {"holds": holds, "declared": declared("subprocess_wait") if stated else None,
            "observed": observed}


@check("self", only_when_named=True)
def violated_by_construction(ctx):
    """For the tool's own tests: a promise that never holds, so a violation turns the exit red."""
    return {"holds": False, "observed": {"why": "this check exists to fail"}}


IGNORES_SIGINT = """\
import signal, time
from pathlib import Path
signal.signal(signal.SIGINT, signal.SIG_IGN)
t0 = time.perf_counter()
while time.perf_counter() - t0 < 8:
    pass
Path("data/derived/finished.txt").write_text("finished", encoding="utf-8")
"""


@check("cli", observation=True, needs_uv=True)
def cli_deadline_when_the_script_ignores_sigint(ctx):
    """What a 2 s deadline does to a script that ignores SIGINT and runs for 8 s. On POSIX the
    deadline is SIGALRM; on Windows it arrives through SIGINT, which this script has taken over."""
    root = project(ctx)
    marker = root / "data" / "derived" / "finished.txt"
    with contextlib.suppress(OSError):
        marker.unlink()
    observed = _timed_run(ctx, "07_ignores_sigint.py", IGNORES_SIGINT, 2)
    observed["script_finished"] = marker.exists()
    return {"holds": True, "observed": observed}


def _list_runs(root: Path) -> list[dict]:
    from aelix_analytics import core

    return core.list_runs(core.Project(root))


CONCURRENT = """\
import time
from pathlib import Path
import polars as pl
import aelix_analytics as analytics

time.sleep(2)
analytics.save(pl.DataFrame({"who": [Path(__file__).stem]}), "{name}")
"""


@check("cli", needs_uv=True)
def cli_concurrent_runs_are_serialized(ctx):
    root = project(ctx)
    logs = root.parent / "concurrent-logs"
    logs.mkdir(exist_ok=True)
    procs, handles = {}, []
    for name in ("left_side", "right_side"):
        rel = script(root, f"05_{name}.py", CONCURRENT.replace("{name}", name))
        out = open(logs / f"{name}.stdout", "wb")  # noqa: SIM115 - closed below
        handles.append(out)
        procs[name] = subprocess.Popen(kit_cli_argv(ctx, root, "run", rel, "--title", name), cwd=root,
                                       env=kit_env(ctx), stdin=subprocess.DEVNULL, stdout=out,
                                       stderr=subprocess.DEVNULL)
    observed = {}
    for name, proc in procs.items():
        if finish(proc, 600) is None:
            ctx.bundle.processes.kill_tree(proc.pid)
            observed[name] = {"status": None, "note": "did not finish within 600 s"}
    for out in handles:
        out.close()
    for name in procs:
        if name in observed:
            continue
        text = (logs / f"{name}.stdout").read_text(encoding="utf-8", errors="replace")
        line = next((raw for raw in reversed(text.splitlines()) if raw.startswith("{")), "{}")
        record = (json.loads(line) or {}).get("run") or {}
        outputs = sorted(o["path"] for o in record.get("outputs") or [] if o.get("change") != "deleted")
        observed[name] = {"status": record.get("status"), "outputs": outputs}
    holds = all(observed.get(name, {}).get("status") == "ok"
                and observed[name]["outputs"] == [f"data/derived/{name}.parquet"]
                for name in procs)
    return {"holds": holds, "observed": observed}


CHART_CON = """\
import matplotlib.pyplot as plt
import aelix_analytics as analytics

fig, ax = plt.subplots()
ax.plot([1, 2], [1, 2])
analytics.chart(fig, "con", title="예약 이름")
"""

SAVE_CON = """\
import polars as pl
import aelix_analytics as analytics

analytics.save(pl.DataFrame({"a": [1]}), "con")
"""


@check("cli", needs_uv=True)
def cli_refuses_a_new_reserved_dataset_name(ctx):
    root = project(ctx)
    source = root.parent / "매출.csv"
    code, result, _ = kit_cli(ctx, root, "import", source, "--name", "con")
    error = ((result or {}).get("error") or {}).get("code")
    return {"holds": code == 1 and error == "name_not_portable",
            "observed": {"exit": code, "error_code": error}}


@check("cli", needs_uv=True)
def cli_writes_a_reserved_chart_id_to_a_portable_file(ctx):
    root = project(ctx)
    code, result, _ = kit_cli(ctx, root, "run", script(root, "06_chart_con.py", CHART_CON),
                             "--title", "예약 차트")
    record = (result or {}).get("run") or {}
    charts = record.get("charts") or []
    png = ((charts[0].get("files") or {}).get("png") if charts else None) or ""
    readable = False
    with contextlib.suppress(OSError):
        readable = (root / png).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    holds = (record.get("status") == "ok" and [c.get("id") for c in charts] == ["con"]
             and png.endswith("/con_.png") and readable)
    return {"holds": holds, "observed": {"status": record.get("status"), "exit": code,
                                         "ids": [c.get("id") for c in charts], "png": png,
                                         "png_readable": readable}}


@check("cli", needs_uv=True)
def cli_a_script_saving_a_reserved_name_is_warned_about(ctx):
    root = project(ctx)
    code, result, _ = kit_cli(ctx, root, "run", script(root, "07_save_con.py", SAVE_CON),
                             "--title", "예약 저장")
    record = (result or {}).get("run") or {}
    warned = any("reserved device name" in w for w in record.get("warnings") or [])
    observed = {"status": record.get("status"), "exit": code, "warned": warned,
                "error": (record.get("error") or {}).get("summary")}
    if WINDOWS:
        # The contract promises the warning, not that Windows can store the file; what happened is
        # the evidence either way.
        return {"holds": True, "observation": True, "observed": observed}
    return {"holds": record.get("status") == "ok" and warned, "observed": observed}


# --- design probes ----------------------------------------------------------------------------
#
# Observations for a design question, not promises: how an interrupt behaves when it goes through
# `uv run`, and whether another thread can wake a main thread that is blocked in a sleep. They never
# fail the run; they are the evidence the next design is judged against.

PROBE_CHILD = """\
import os, signal, sys, threading, time
from pathlib import Path
ready, marker, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
WINDOWS = os.name == "nt"

def on_signal(signum, frame):
    marker.write_text(f"handler-entered:{signum}", encoding="utf-8")
    time.sleep(1.0)  # roughly what writing a run record costs
    marker.write_text(f"handler-finished:{signum}", encoding="utf-8")
    sys.exit(130)

for name in ("SIGINT", "SIGBREAK"):
    if hasattr(signal, name):
        signal.signal(getattr(signal, name), on_signal)

if mode.startswith("file:"):
    request = Path(mode[5:])
    main = threading.main_thread().ident

    def watch():
        while not request.exists():
            time.sleep(0.1)
        # Wake the main thread the way a real Ctrl+C would, so a blocking sleep returns too.
        if WINDOWS:
            signal.raise_signal(signal.SIGINT)
        else:
            signal.pthread_kill(main, signal.SIGINT)

    threading.Thread(target=watch, daemon=True).start()

ready.write_text(str(os.getpid()), encoding="utf-8")
time.sleep(120)  # one long sleep: the hard case for waking
"""


def _probe_through_uv(ctx, label: str, use_file: bool) -> dict:
    d = ctx.fresh(label)
    child = write_script(d, "child", PROBE_CHILD)
    request = d / "cancel-request"
    mode = f"file:{request}" if use_file else "signal"
    argv = [ctx.uv, "run", "--no-project", "--quiet", "--python", sys.executable, "python",
            str(child), str(d / "ready"), str(d / "marker"), mode]
    proc = subprocess.Popen(argv, env=child_env(), stdin=subprocess.DEVNULL,
                            **ctx.bundle.processes.spawn_kwargs())
    text = wait_for_text(d / "ready", 120)
    if text is None:
        ctx.bundle.processes.kill_tree(proc.pid)
        return {"started": False}
    child_pid = int(text)
    t0 = time.perf_counter()
    if use_file:
        request.write_text("cancel", encoding="utf-8")
    elif WINDOWS:
        proc.send_signal(signal.CTRL_BREAK_EVENT)  # what the port used to send
    else:
        os.kill(proc.pid, signal.SIGINT)
    code = finish(proc, 20)
    seconds = round(time.perf_counter() - t0, 2)
    if code is None:
        ctx.bundle.processes.kill_tree(proc.pid)
        code = finish(proc, 30)
    child_gone = wait_gone(ctx, child_pid, 10)
    if not child_gone:
        force_kill(child_pid)
    return {"uv_exit_code": code, "uv_exit_code_hex": hex(code) if code is not None else None,
            "seconds_to_exit": seconds, "child_marker": wait_for_text(d / "marker", 1),
            "child_gone": child_gone}


@check("probe", observation=True, needs_uv=True)
def probe_interrupt_through_uv_run(ctx):
    """What ProcessControl.interrupt does to a `uv run` child whose handler needs a second."""
    return {"holds": True, "observed": _probe_through_uv(ctx, "probe-uv-signal", use_file=False)}


@check("probe", observation=True, needs_uv=True)
def probe_cancel_request_file_through_uv_run(ctx):
    """A cooperative cancel: the child watches a file and wakes its own main thread."""
    return {"holds": True, "observed": _probe_through_uv(ctx, "probe-uv-file", use_file=True)}


@check("probe", observation=True)
def probe_waking_a_sleeping_main_thread(ctx):
    """Which primitive, called from another thread, interrupts a main thread blocked in a sleep."""
    import _thread

    main = threading.main_thread().ident
    primitives = {"_thread.interrupt_main": _thread.interrupt_main}
    if WINDOWS:
        primitives["signal.raise_signal(SIGINT)"] = lambda: signal.raise_signal(signal.SIGINT)
    else:
        primitives["signal.pthread_kill(main, SIGINT)"] = lambda: signal.pthread_kill(
            main, signal.SIGINT)
    observed = {}
    for name, wake in primitives.items():
        def raising(_signum, _frame):
            raise _platform.Interrupted("woken")

        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, raising)
        timer = threading.Timer(1.0, wake)
        t0 = time.perf_counter()
        woke_by = None
        try:
            timer.start()
            time.sleep(10)
        except (_platform.Interrupted, KeyboardInterrupt) as exc:
            woke_by = type(exc).__name__
        finally:
            timer.cancel()
            signal.signal(signal.SIGINT, previous)
        observed[name] = {"interrupted_by": woke_by, "seconds": round(time.perf_counter() - t0, 2)}
    return {"holds": True, "observed": observed}


# --- running and reporting ------------------------------------------------------------------------

def runner_settings() -> dict:
    """The settings of this machine that the platform's results depend on, read on every run so a
    record never borrows them from another run: the links, long paths and case the file system
    allows, and who is running. Observations only; none of them is a check."""
    settings: dict = {"image": {k: os.environ.get(k) for k in ("ImageOS", "ImageVersion")}}
    with tempfile.TemporaryDirectory(prefix="smoke-settings-", ignore_cleanup_errors=True) as base:
        probe = Path(base) / "Mixed.txt"
        probe.write_text("x", encoding="utf-8")
        settings["temp_ignores_case"] = (Path(base) / "mixed.txt").exists()
        try:
            os.symlink(probe, Path(base) / "link")
            settings["symlink"] = "allowed"
        except OSError as exc:
            settings["symlink"] = f"refused: {exc!r}"
    with contextlib.suppress(Exception):
        origin, _, value = subprocess.run(
            ["git", "-C", str(ROOT), "config", "--show-origin", "--get", "core.autocrlf"],
            capture_output=True, timeout=30, **_platform.text_kwargs()).stdout.strip().partition("\t")
        settings["git_autocrlf"], settings["git_autocrlf_origin"] = value or None, origin or None
    if WINDOWS:
        import ctypes
        import winreg

        def registry(key: str, name: str):
            with contextlib.suppress(OSError), winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                return winreg.QueryValueEx(k, name)[0]
            return None

        settings["long_paths_registry"] = registry(r"SYSTEM\CurrentControlSet\Control\FileSystem",
                                                   "LongPathsEnabled")
        with contextlib.suppress(Exception):
            enabled = ctypes.windll.ntdll.RtlAreLongPathsEnabled
            enabled.restype = ctypes.c_ubyte
            settings["long_paths_effective"] = bool(enabled())
        settings["edition"] = platform.win32_edition()
        settings["developer_mode"] = registry(
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock",
            "AllowDevelopmentWithoutDevLicense")
        with contextlib.suppress(Exception):
            settings["administrator"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    else:
        settings["euid"] = os.geteuid()
    return settings


def environment(ctx) -> dict:
    env = {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
        "utf8_mode": sys.flags.utf8_mode,
        "preferred_encoding": locale.getpreferredencoding(False),
        "filesystem_encoding": sys.getfilesystemencoding(),
        "stdout_encoding": getattr(sys.stdout, "encoding", None),
        "cpu_count": os.cpu_count(),
        "uv": None,
        "runner": None,
    }
    try:
        env["runner"] = runner_settings()
    except Exception as exc:  # an observation must not cost the report of the checks that ran
        env["runner"] = {"error": repr(exc)}
    if WINDOWS:
        env["windows"] = list(platform.win32_ver())
        with contextlib.suppress(Exception):
            import ctypes

            kernel32 = ctypes.windll.kernel32
            env["console_code_pages"] = {"input": kernel32.GetConsoleCP(),
                                         "output": kernel32.GetConsoleOutputCP()}
    elif sys.platform == "darwin":
        env["macos"] = platform.mac_ver()[0]
    if getattr(ctx, "stdlib_only", False):
        env["uv"] = "not used (stdlib-only mode)"
    elif ctx.uv:
        with contextlib.suppress(Exception):
            env["uv"] = subprocess.run([ctx.uv, "--version"], capture_output=True, timeout=60,
                                       **_platform.text_kwargs()).stdout.strip()
    return env


def source() -> dict:
    info = {"head": None, "dirty": None}
    with contextlib.suppress(Exception):
        info["head"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                      capture_output=True, timeout=30,
                                      **_platform.text_kwargs()).stdout.strip() or None
        status = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                                capture_output=True, timeout=30, **_platform.text_kwargs())
        info["dirty"] = bool(status.stdout.strip()) if status.returncode == 0 else None
    return info


def run_checks(ctx, selected) -> list[dict]:
    results = []
    for group, name, fn in CHECKS:
        if (selected and name not in selected) or (fn.only_when_named and name not in selected):
            continue
        entry = {"name": name, "group": group, "observation": fn.observation}
        if fn.needs_uv and not ctx.uv:
            reason = ("the stdlib-only mode does not use uv" if getattr(ctx, "stdlib_only", False)
                      else "uv is not on PATH")
            entry.update({"holds": None, "result": "not_run", "reason": reason})
            results.append(entry)
            continue
        t0 = time.perf_counter()
        print(f"… {name}", flush=True)
        try:
            outcome = fn(ctx)
            entry["observation"] = bool(outcome.get("observation", fn.observation))
            entry["holds"] = bool(outcome.get("holds"))
            entry["declared"] = outcome.get("declared")
            entry["observed"] = outcome.get("observed")
        except BaseException as exc:  # noqa: BLE001 - a crashing check is a result, not an abort
            if isinstance(exc, SystemExit):
                raise
            entry.update({"holds": False, "declared": None,
                          "error": "".join(traceback.format_exception(exc))[-3000:]})
        entry["seconds"] = round(time.perf_counter() - t0, 2)
        if entry.get("observation"):
            entry["result"] = "observed"
        elif entry["holds"]:
            entry["result"] = "holds"
        elif entry.get("declared"):
            entry["result"] = "declared_limitation"
        else:
            entry["result"] = "violated"
        print(f"  {entry['result']:<20} {name} ({entry['seconds']} s)", flush=True)
        results.append(entry)
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure the portable boundaries on this platform.")
    parser.add_argument("--output", required=True, help="where to write the evidence JSON")
    parser.add_argument("--stdlib-only", action="store_true",
                        help="only what the aelix process loads: no uv, no analysis libraries")
    parser.add_argument("--only", help="comma-separated check names, for debugging a single check")
    parser.add_argument("--gate", choices=("all", "required"), default="all",
                        help="what the exit status reflects: every promise, or the required ones")
    args = parser.parse_args(argv)
    selected = set(filter(None, (args.only or "").split(",")))
    unknown = selected - {name for _g, name, _f in CHECKS}
    if unknown:
        parser.error(f"unknown check(s): {', '.join(sorted(unknown))}")

    _platform.configure_stdio()
    started = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="aelix-analytics-smoke-", ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        # Never touch the user's real trust file, and keep every project this creates in tmp.
        os.environ["AELIX_LAB_TRUST_FILE"] = str(tmp_path / "trust" / "lab-projects.json")
        ctx = Context(tmp_path)
        if args.stdlib_only:
            ctx.uv, ctx.stdlib_only = None, True
            selected = selected or {name for group, name, fn in CHECKS
                                    if group != "cli" and not fn.only_when_named}
        results = run_checks(ctx, selected)
        report = {
            "schema": SCHEMA,
            "generated_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "command": [Path(sys.executable).name, *sys.argv],
            "mode": "stdlib-only" if args.stdlib_only else "full",
            "source": source(),
            "environment": environment(ctx),
            "summary": {key: sum(1 for r in results if r.get("result") == key)
                        for key in ("holds", "violated", "declared_limitation", "observed", "not_run")},
            "required": required_summary(results),
            "checks": results,
        }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "summary": report["summary"],
                      "required": report["required"]}, ensure_ascii=False))
    if args.gate == "required":
        return 0 if report["required"]["hold"] else 1
    return 1 if report["summary"]["violated"] else 0


def required_summary(results: list[dict]) -> dict:
    """The checks this platform is judged on among those this invocation selected: every promise.
    One that shows a limitation the documents declare holds the verdict; a selected check that was
    violated or could not run (`not_run`) fails it; one that was not selected is not judged."""
    required = [r for r in results if not r.get("observation")]
    failing = {key: [r["name"] for r in required if r.get("result") == key]
               for key in ("violated", "declared_limitation", "not_run")}
    return {"platform": "windows" if WINDOWS else "posix", "checks": [r["name"] for r in required],
            **failing, "hold": not failing["violated"] and not failing["not_run"]}


if __name__ == "__main__":
    sys.exit(main())
