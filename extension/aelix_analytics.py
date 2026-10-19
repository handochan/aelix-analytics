"""Aelix Analytics: analysis extension built only from the basic aelix extension contract.
(setup, register_flag, register_tool, register_command, on).

Installed (the aelix-analytics wheel in aelix's environment): aelix finds it through the
`aelix.extensions` entry point, as the module aelix_analytics.extension, with its manifest.
From a checkout, for one session:  aelix -e <pack>/extension/aelix_analytics.py [--analytics-project DIR]
From a checkout, globally:         ln -s <pack>/extension/aelix_analytics.py ~/.aelix/agent/extensions/aelix_analytics.py
Use one of these at a time: a second copy in the same aelix does not set itself up (see setup).

Inside aelix this file imports only the standard library, the aelix API and the kit's stdlib-only
core.py with its _platform and relations siblings, from the runtime root (the directory that holds the package
aelix_analytics) — installed, also its own package, whose __init__ imports only the standard library,
and the stdlib-only aelix_analytics._install, which says where the runtime root is. From a checkout
the runtime root is the checkout's src/ (so symlink this file in a checkout rather than copying it).
Analysis code never runs in the aelix process: analytics_run, analytics_import, analytics_query and
Workspace renders are `uv run --project <project> python -m aelix_analytics ...` subprocesses in the
project's own environment.
Neither side is a sandbox, and aelix does not ask for approval before extension tools run.
The one thing it changes in the aelix process itself is the environment: AELIX_ANALYTICS_ACTOR=agent
and the session, which the kit's command line run through aelix's shell records as its author, and a
person's `!` line that is one kit command runs with `human` instead (design 4.2; see on_user_bash).

Before kit 0.3.0 this file was extension/aelix_lab.py, with the command /lab, the tools lab_*, the
flag --lab-project and the variables AELIX_LAB_*. None of them is registered or read any more. The old
flag, and an old variable this file read (AELIX_LAB_PROJECT, AELIX_LAB_UV, AELIX_LAB_TRUST_FILE, and
AELIX_LAB_KIT on a file load), stop the load with an error that names the new one
(_refuse_old_names). A name this file never read (the kit's LAB_*) is not looked at here: the reader
that read it refuses it (docs/CONTRACTS.md A, D).
"""

import asyncio
import base64
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from aelix_agent_core.harness.hooks import BeforeAgentStartResult, UserBashResult
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import ImageContent, TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult


def _kit() -> tuple[Path, str]:
    """(the kit directory, how it was found). The kit directory is the runtime root, the directory
    that holds the package `aelix_analytics`: children get exactly this directory on PYTHONPATH, and
    core.py and its siblings are loaded from its package."""
    if __name__ == "aelix_analytics.extension":
        # Imported by name from the installed distribution (its `aelix.extensions` entry point, or
        # `aelix -e aelix_analytics.extension`). It runs the kit installed with it, found through
        # importlib.resources, as the installed `analytics` command does: an adapter and a kit of
        # different versions would not fit each other.
        from aelix_analytics._install import kit_dir

        return kit_dir(), "installed"
    configured = os.environ.get("AELIX_ANALYTICS_RUNTIME_PATH")
    if configured:
        return Path(configured).expanduser().resolve(), "AELIX_ANALYTICS_RUNTIME_PATH"
    here = Path(__file__).resolve().parent
    if (here / "kit" / "aelix_analytics" / "core.py").is_file():
        # The installed module loaded as a file (`aelix -e <site-packages>/aelix_analytics/extension.py`).
        return (here / "kit").resolve(), "installed file"
    # A checkout's extension/aelix_analytics.py, loaded as a file (`aelix -e`, or a symlink in aelix's
    # extensions directory): its runtime root is the checkout's src/, next to its directory.
    return (here.parent / "src").resolve(), "checkout"


KIT, KIT_ROUTE = _kit()


def _load_core():
    """Load the pack's stdlib-only project model into the aelix process.

    core.py binds its stdlib project-model and asset siblings while loading, so it is loaded as a
    submodule of a synthetic package whose search path is this pack's kit. The
    package name carries a digest of the resolved kit path, so two packs, or one pack before and
    after AELIX_ANALYTICS_RUNTIME_PATH changes, never share a cached submodule. The sys.modules entries exist only while the modules
    execute and are removed afterwards: the loaded objects stay alive through the reference this
    returns, an aelix_analytics installed in the host environment (the distribution's own package) is
    never shadowed, and a failed load leaves nothing behind. Reloading a changed kit needs a new aelix process, as it did before.
    """
    path = KIT / "aelix_analytics" / "core.py"
    if not path.is_file():
        remedy = {"AELIX_ANALYTICS_RUNTIME_PATH": "Point AELIX_ANALYTICS_RUNTIME_PATH at a directory "
                                                  "that holds aelix_analytics/, or unset it.",
                  "checkout": "Symlink extension/aelix_analytics.py instead of copying it, or set "
                              "AELIX_ANALYTICS_RUNTIME_PATH=<pack>/src.",
                  "installed file": "Reinstall the aelix-analytics package.",
                  "installed": "Reinstall the aelix-analytics package."}[KIT_ROUTE]
        if KIT_ROUTE != "installed" and os.environ.get("AELIX_LAB_KIT"):
            # Before the kit is found the renamed-variable error (_refuse_old_names) cannot be given.
            remedy += (" AELIX_LAB_KIT is not read: it was renamed AELIX_ANALYTICS_RUNTIME_PATH in "
                       "kit 0.3.0.")
        raise RuntimeError(f"aelix-analytics: kit not found at {path}. {remedy}")
    package = f"aelix_analytics_runtime_{hashlib.sha256(str(KIT).encode()).hexdigest()[:12]}"
    spec = importlib.machinery.ModuleSpec(package, None, is_package=True)
    spec.submodule_search_locations = [str(path.parent)]
    sys.modules[package] = importlib.util.module_from_spec(spec)
    try:
        core_spec = importlib.util.spec_from_file_location(f"{package}.core", path)
        module = importlib.util.module_from_spec(core_spec)
        sys.modules[core_spec.name] = module
        core_spec.loader.exec_module(module)
        return module
    finally:
        # Everything under the synthetic name, whether the load finished or raised partway: a
        # sibling that core imported before failing would otherwise be reused by the next attempt.
        for name in [n for n in list(sys.modules)
                     if n == package or n.startswith(f"{package}.")]:
            sys.modules.pop(name, None)


core = _load_core()

# The aelix flag this file registered before the public names became analytics (kit 0.3.0, Issue #9).
OLD_FLAG = "--lab-project"


def _refuse_old_names(argv) -> None:
    """Stop the load, before anything is registered, when an old name of something this file read is
    given: an environment variable (core.RENAMED_ENV; AELIX_LAB_KIT only where this file read it, which
    the installed module never did) or the old flag on aelix's command line. aelix 0.1.0b2 takes a flag
    nobody registered without a word (cli/args.py), and get_flag answers None for it, so the old flag
    would be ignored and the project picked from the working directory instead: the record would land
    in another project. The command line is what aelix was started with; a host that embeds aelix
    without one has no flag to give either. Its tokens are read as they are, without aelix's parser,
    so another option's value equal to --lab-project or --lab-project=… (`--append-system-prompt
    --lab-project`) stops the load too: a known limit (CONTRACTS A). aelix shows this as the
    extension's load error and registers nothing, as with a missing kit — the prompt hook, which
    swallows errors, would leave the context block out without a word. There are no aliases
    (ADR-0005 decision 7)."""
    names = ["AELIX_LAB_PROJECT", "AELIX_LAB_UV", "AELIX_LAB_TRUST_FILE"]
    if KIT_ROUTE != "installed":
        names.append("AELIX_LAB_KIT")
    for name in names:
        if os.environ.get(name):
            raise RuntimeError(f"aelix-analytics: {core.renamed_env_message(name)}")
    for token in argv:
        if token == OLD_FLAG or token.startswith(f"{OLD_FLAG}="):
            raise RuntimeError(
                f"aelix-analytics: {OLD_FLAG} was renamed --analytics-project in kit "
                f"{core.NAMES_RENAMED_IN}; start aelix with --analytics-project DIR instead "
                f"({OLD_FLAG} is not read, so the project would not be the one given)")


_refuse_old_names(sys.argv[1:])

GRACE_SECONDS = 10
MAX_TEXT = 14_000
MAX_IMAGE_BYTES = 1_500_000
MAX_IMAGES = 3
# A run may read hundreds of files; listing them all would push stdout out of the reply.
MAX_LIST = 10
MAX_CHART_LIST = 12
MAX_COLUMNS = 12
# How much of a run's stdout and stderr tails an analytics_run reply shows; an ok run's stderr is shown
# only when it is shorter than OK_STDERR (a warning, not a traceback).
MAX_LOG = 3000
OK_STDERR = 1500
# How much of the kit's stderr a kit_failed error keeps, and of an error's details its text shows.
MAX_STDERR = 1500
MAX_DETAILS = 2000
# render_table's width of a cell.
MAX_CELL = 48
ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
             "TZ", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "SSL_CERT_FILE",
             "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
             "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy")
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|CREDENTIAL|_KEY$", re.IGNORECASE)
PYTHON_IN_SHELL = re.compile(r"(^|[\s;&|(`])(python3?(\.\d+)?|uv\s+run|pip3?|ipython|jupyter)\b")
# A shell command that runs the kit itself, which is not python_via_bash: `-m aelix_analytics` given to
# python (or `uv run`) itself, after its options only; or an `import aelix_analytics` / `from
# aelix_analytics import` statement where a statement starts — at the start of the command or of a
# line (a heredoc), after `;`, or right after the quote that opens a `-c` argument. A path or a
# project name that merely contains the word is not the kit, nor is the word in a comment or a string.
# Each option has one reading (-X and -W take a value, other short flags do not), so a long command
# that does not match fails in linear time.
KIT_IN_SHELL = re.compile(
    r"(?:^|[\s;&|(`])(?:[^\s;&|()`'\"]*[/\\])?(?:python3?(?:\.\d+)?(?:\.exe)?|uv\s+run)"
    r"(?:\s+(?:--[\w-]+(?:=\S*)?|-[XW]\s*[^\s-]\S*|-[A-VYZa-z][A-Za-z]*))*\s+-m\s*aelix_analytics\b"
    r"|(?:^|;|-c\s*['\"])[ \t]*(?:import\s+(?:[\w.]+(?:\s+as\s+\w+)?\s*,\s*)*aelix_analytics\b"
    r"|from\s+aelix_analytics(?:\.\w+)*\s+import\b)",
    re.MULTILINE)
# An `import aelix_analytics` / `from aelix_analytics import` statement where one starts: KIT_IN_SHELL's
# second half, for the code of a `python -c` a person's line gives (is_single_kit_command).
KIT_IMPORT = re.compile(
    r"(?:^|;)[ \t]*(?:import\s+(?:[\w.]+(?:\s+as\s+\w+)?\s*,\s*)*aelix_analytics\b"
    r"|from\s+aelix_analytics(?:\.\w+)*\s+import\b)",
    re.MULTILINE)

# Module-level state of one aelix runtime. setup() starts it over: the file loader makes a new module
# for every session aelix builds, but the installed module is imported once per process.
STATE = {"api": None, "project": None, "session": None, "starts": {}, "render_tasks": {}}

NO_PROJECT = ("No analytics project is active (checked /analytics use, --analytics-project, "
              "AELIX_ANALYTICS_PROJECT, and analytics.json or lab.json in the working directory and "
              "its parents).")


def _error(code, message, hint=None, details=None):
    return core.AnalyticsError(code, message, hint=hint, details=details).to_json()


# --- resolution ---

def resolve_project():
    """(Project, how it was found). Explicit choices that are not projects are errors. A project
    found only because aelix runs inside it must be trusted (created or chosen by the user)."""
    try:
        return _resolve_project()
    except core.AnalyticsError:
        raise
    except Exception as exc:  # e.g. "~typo/dir" makes expanduser raise RuntimeError
        raise core.AnalyticsError("project_invalid", f"cannot resolve the project path: {exc}",
                                  hint="check --analytics-project / AELIX_ANALYTICS_PROJECT") from exc


def _resolve_project():
    # An explicit project is that directory itself, judged by its markers like any other (core's
    # writer.marker_state): a directory with neither is not one, and one with both is refused here,
    # since nothing can be read from it (Issue #13). Writes are judged again where they are made.
    explicit = STATE["project"]
    if explicit:
        project = core.Project(explicit)
        if not core.present_marker(project.root):
            _refuse_incomplete(project)
            raise core.AnalyticsError("project_missing", f"active project {explicit} has no "
                                      "analytics.json or lab.json",
                                      hint="/analytics use DIR to pick another project")
        core.readable(project, "use the project")
        return project, "/analytics use"
    flag = None
    with contextlib.suppress(Exception):
        flag = STATE["api"].get_flag("analytics-project") if STATE["api"] else None
    for value, via in ((flag, "--analytics-project"), (os.environ.get("AELIX_ANALYTICS_PROJECT"),
                                                       "AELIX_ANALYTICS_PROJECT")):
        if isinstance(value, str) and value.strip():
            project = core.Project(value.strip())
            if not core.present_marker(project.root):
                _refuse_incomplete(project)
                raise core.AnalyticsError("project_invalid", f"{via} {project.root} is not an analytics project "
                                          "(no analytics.json or lab.json)",
                                          hint="create it with /analytics init DIR")
            core.readable(project, "use the project")
            return project, via
    root = core.find_project(os.getcwd())
    if root is not None:
        if not core.is_trusted(root):
            raise core.AnalyticsError(
                "project_untrusted",
                f"{root} has a project marker but is not a project this user created or chose, so it "
                "was not activated (its CONTEXT.md would enter the prompt and analytics_run would "
                "execute its code)",
                hint=f"a human can run /analytics use {root} to trust it")
        core.readable(root, "use the project")
        return core.Project(root), "working directory"
    raise core.AnalyticsError("no_project", NO_PROJECT,
                              hint="ask the user where the project should live, then call analytics_init; a "
                                   "human can run /analytics init DIR or /analytics use DIR")


def _refuse_incomplete(project) -> None:
    """A named directory without a marker that holds a migration's record is a migration's
    destination that has not committed: refused as the kit refuses it (migration_incomplete, with
    how far its migration went), not as a directory that is no project (design 4.3)."""
    if core.migration_record(project.root):
        core.readable(project, "use the project")


def current_session(ctx):
    """Only this command/hook context identifies its session; no stale STATE fallback."""
    try:
        value = ctx.session_manager.get_session().session_file
        return str(value) if value else None
    except Exception:  # noqa: BLE001 - unavailable context must clear, never reuse, a prior session
        return None


def command_origin(ctx):
    return core.Origin("human", "command", "declared", current_session(ctx))


def origin(actor: str, means: str):
    """Who a write this extension makes is by, and how (design 4.2): the model's tools `agent`·`tool`,
    `/analytics` `human`·`command`, a hook `system`·`hook` (the context sync inside analytics_status
    `system`·`tool`), declared, with this aelix session."""
    session = STATE["session"]
    return core.Origin(actor, means, "declared", str(session) if session else None)


def resolve_uv(processes=None) -> str:
    """AELIX_ANALYTICS_UV (a path, or a name) or `uv`, found where the platform looks for programs
    (ProcessControl.find_program: on Windows PATH alone, never the working directory)."""
    processes = processes or core.PLATFORM.processes
    found = processes.find_program(os.environ.get("AELIX_ANALYTICS_UV") or "uv")
    if not found:
        raise core.AnalyticsError("uv_missing", "uv was not found",
                                  hint="install uv (https://docs.astral.sh/uv/) or set AELIX_ANALYTICS_UV=/path/to/uv")
    return found


def child_env(processes, actor: str, means: str | None = None) -> dict:
    """A child's environment: the allowed list, the kit (PYTHONPATH), who it writes for
    (AELIX_ANALYTICS_ACTOR, declared) and this session, and for a kit command how this extension
    came to start it (AELIX_ANALYTICS_MEANS: `tool`, `command` or `hook`, design 4.2) — an internal
    protocol, as AELIX_ANALYTICS_CANCEL_FILE (CONTRACTS D)."""
    # What the platform needs to start a process at all goes in first: the allowed list is about
    # what a run may see, not about what the operating system requires to launch one.
    env = processes.base_env()
    env.update({k: os.environ[k] for k in ENV_ALLOW if k in os.environ})
    env.update({k: v for k, v in os.environ.items()
                if k.startswith("UV_") and not SECRET_NAME.search(k)})
    env.update({"PYTHONPATH": str(KIT), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "MPLBACKEND": "Agg", "AELIX_ANALYTICS_ACTOR": actor})
    if means is not None:
        env["AELIX_ANALYTICS_MEANS"] = means
    if STATE["session"]:
        env["AELIX_ANALYTICS_SESSION"] = str(STATE["session"])
    return env


# --- subprocesses ---

class _CancelRequest:
    """The file through which a kit command is asked to stop where no signal can ask it (see
    _platform's ProcessControl.interrupt and RunControl.watch_for_cancel), in a private temporary
    directory. Only a platform whose ProcessControl `asks_by_request` gets one.

    Once _terminate has started it owns the directory, so a second cancellation of the tool call,
    which ends _spawn at once, cannot delete the request before the kit has seen it; and it removes
    it only once the kit has finished. A _terminate that is itself cancelled — aelix exiting while a
    command is still stopping — leaves it in place for a kit that has not looked yet: a leftover
    temporary directory costs less than a lost cancel. If no _terminate started, run_kit removes
    it."""

    def __init__(self) -> None:
        try:
            self.directory = Path(tempfile.mkdtemp(prefix="aelix-analytics-"))
        except OSError as exc:
            raise core.AnalyticsError("spawn_failed",
                                      f"could not create a temporary directory: {exc}") from exc
        self.path = self.directory / "cancel"
        self.handed_over = False

    def remove(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


async def _drain(stream) -> None:
    while stream is not None and await stream.read(65536):
        pass


async def _finished(proc) -> None:
    """Return once the child has exited and nothing holds its pipes any more; output is discarded.

    `Process.wait` alone is not that: called after the child exited it returns at once, even while
    a descendant still holds the pipes (measured on CPython 3.11 and 3.12). Both pipes are read at
    once, so a child blocked writing one cannot hold up the end of the other."""
    await asyncio.gather(_drain(proc.stdout), _drain(proc.stderr))
    await proc.wait()


async def _terminate(proc, processes, request: _CancelRequest | None = None) -> None:
    """Stop a child and everything it started: ask first, then end by force only what is left.

    Cancelled itself — aelix exiting runs asyncio.run's shutdown, which cancels this task — it
    stops where it is: on POSIX the ask has already been sent, on Windows the request stays for the
    kit to find; nothing ends the tree by force once aelix has gone."""
    finished = False
    try:
        if processes.interrupt(proc, request.path if request is not None else None):
            try:
                await asyncio.wait_for(_finished(proc), GRACE_SECONDS)
                finished = True
                return  # nothing is left holding the pipes, so there is nothing to end
            except TimeoutError:
                pass
        # The child did not stop in time, or it exited and something it started still holds the
        # pipes. Only now is the tree ended, while the wait is unfinished (see
        # ProcessControl.kill_tree). It may block, so it runs off the event loop, with a pid.
        await asyncio.to_thread(processes.kill_tree, proc.pid)
        with contextlib.suppress(TimeoutError):
            # Bounded: if the tree outlived a forced kill there is nothing further to try, and
            # this returning leaves it running rather than holding the session's event loop open.
            await asyncio.wait_for(_finished(proc), GRACE_SECONDS)
            finished = True
    finally:
        # Only once nothing is left to read it: a kit still running — after this task was
        # cancelled, or after a forced end it survived — can still find it and stop itself.
        if request is not None and finished:
            request.remove()


async def _spawn(argv, cwd, env, timeout, processes, request: _CancelRequest | None = None):
    """(timed_out, returncode, stdout, stderr). `request` is the kit's cancel request, if any."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **processes.spawn_kwargs())
    except OSError as exc:
        raise core.AnalyticsError("spawn_failed", f"could not start {argv[0]}: {exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        await asyncio.shield(_stopping(proc, processes, request))
        return True, proc.returncode, b"", b""
    except asyncio.CancelledError:
        # A second cancellation ends this await at once; the shielded task carries on, and owns
        # the request from here.
        await asyncio.shield(_stopping(proc, processes, request))
        raise
    return False, proc.returncode, stdout, stderr


# The loop keeps only weak references to tasks, and a termination outlives the _spawn that a second
# cancellation ended; this keeps it alive until it is done.
STOPPING: set = set()


def _stopping(proc, processes, request):
    """_terminate as a task that owns the request from the moment it exists."""
    if request is not None:
        request.handed_over = True
    task = asyncio.ensure_future(_terminate(proc, processes, request))
    STOPPING.add(task)
    task.add_done_callback(STOPPING.discard)
    return task


async def run_kit(project, args, *, timeout: float, actor: str = "agent", means: str = "tool"):
    """Run `python -m aelix_analytics` in the project's environment, for `actor` by `means` (the
    model's tools: agent·tool; /analytics: human·command). Returns (ok, result object)."""
    processes = project.platform.processes
    try:
        if args[:1] == ["build"]:
            python = project.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if not python.is_file():
                raise core.AnalyticsError("renderer_unavailable", "preview needs an existing project environment; run uv sync first")
            argv = [str(python), "-m", "aelix_analytics", "--project", str(project.root), *args]
        else:
            uv = resolve_uv(processes)
            argv = [uv, "run", "--project", str(project.root), "--quiet", "python", "-m", "aelix_analytics",
                    "--project", str(project.root), *args]
        # Only where the platform asks through it: POSIX asks with a signal and needs no file.
        request = _CancelRequest() if processes.asks_by_request and args[:1] != ["build"] else None
        try:
            env = child_env(processes, actor, means)
            if args[:1] == ["build"]:
                env["PYTHONDONTWRITEBYTECODE"] = "1"
            if request is not None:
                env["AELIX_ANALYTICS_CANCEL_FILE"] = str(request.path)
            timed_out, code, stdout, stderr = await _spawn(argv, project.root, env, timeout,
                                                           processes, request)
        finally:
            if request is not None and not request.handed_over:
                request.remove()
    except core.AnalyticsError as exc:
        return False, exc.to_json()
    if timed_out:
        return False, _error("timeout", f"aelix_analytics {args[0]} did not finish within {timeout:.0f} s "
                             "and was interrupted", hint="analytics_status shows whether a run was recorded")
    # Lines end at LF alone (strip() takes a CR before it): str.splitlines() would also end one at
    # U+2028, U+2029 or U+0085, which a kit before Issue #32 item 1 wrote raw inside its result line
    # (CONTRACTS D). The kit escapes them now; this keeps an older kit's line whole as well. A line
    # still holding a CR is not a result: every kit escapes one inside a string, and splitlines()
    # ended a line there, so a thread's text holding one was not taken for the result before either.
    for line in reversed(stdout.decode("utf-8", errors="replace").split("\n")):
        line = line.strip()
        if line.startswith("{") and "\r" not in line:
            with contextlib.suppress(ValueError):
                obj = json.loads(line)
                if isinstance(obj, dict):
                    return bool(obj.get("ok")), obj
    whole = stderr.decode("utf-8", errors="replace")
    tail = whole[-MAX_STDERR:]
    hint = ("read details.stderr" + (f" — the last {len(tail):,} characters of the kit's stderr "
                                     f"({len(whole):,} in all)" if len(tail) < len(whole) else "")
            + "; analytics_status shows whether a run was recorded")
    if re.search(r"(?i)\b(resolv|sync|lock|no solution|interpreter|python)\b", tail):
        hint += f"; if it is an environment error run `uv sync --project {project.root}`"
    return False, _error("kit_failed", f"aelix_analytics {args[0]} exited {code} without a result",
                         hint=hint, details={"stderr": tail})


async def sync_environment(project):
    processes = project.platform.processes
    uv = resolve_uv(processes)
    env = child_env(processes, "system")
    last = b""
    for extra in ([], ["--offline"]):
        timed_out, code, _out, err = await _spawn([uv, "sync", "--project", str(project.root),
                                                   "--quiet", *extra], project.root, env, 900,
                                                  processes)
        if not timed_out and code == 0:
            return True, "offline cache" if extra else "network"
        last = err
    said = last.decode("utf-8", errors="replace")
    if len(said) > MAX_STDERR:
        said = f"(the last {MAX_STDERR:,} characters of {len(said):,})\n{said[-MAX_STDERR:]}"
    return False, said


def schedule_workspace_render(project, means: str = "hook") -> None:
    """Start a detached `render --if-changed` build unless one for this project is still running.

    Deliberately not an asyncio task: a cancelled `uv run` whose Python child still holds the pipes
    keeps Process.wait() pending, which would stall aelix's loop shutdown. The kit serializes builds
    with a file lock and skips when nothing changed, so a missed trigger is caught by the next one
    (agent_end fires after every prompt). `means` is what scheduled it: the `hook` agent_end, or the
    `tool` or `command` that recorded something; the build is the kit's own (`system`).
    """
    key = str(project.root)
    running = STATE["render_tasks"].get(key)
    if running is not None and running.poll() is None:
        return
    try:
        # A side write: not even started where this kit does not write (Issue #13, design 4.10).
        if core.admit(project, "render the Workspace", side=True) is None:
            return
        processes = project.platform.processes
        argv = [resolve_uv(processes), "run", "--project", key, "--quiet", "python", "-m", "aelix_analytics",
                "--project", key, "render", "--if-changed"]
        STATE["render_tasks"][key] = subprocess.Popen(
            argv, cwd=key, env=child_env(processes, "system", means), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **processes.spawn_kwargs())
    except Exception:  # noqa: BLE001 - a derived build; it must not fail what was just recorded
        return


# --- rendering ---
# A reply that leaves something out says how much, and where to read it (docs/CONTRACTS.md, A1).

def _clip(text: str, where: str, limit: int = MAX_TEXT) -> str:
    """`text` whole, or cut to fit in `limit` characters, with a last line that says how much was
    cut and `where` it can be read. The cut is at the last line end that fits, unless that is more
    than half the room back (a line that long): then it is inside that line, at the limit."""
    if len(text) <= limit:
        return text

    def marker(rest: int) -> str:
        return f"… (truncated: {rest:,} more characters; {where})"

    room = limit - len(marker(len(text))) - 1
    cut = text.rfind("\n", 0, room + 1)
    if cut < room // 2:  # no line end near the limit: a line that long is cut where it is
        cut = room
    return text[:cut] + "\n" + marker(len(text) - cut)


def _one(text, width=160) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= width else s[:width - 1] + "…"


def _more(items, shown: int, label: str, record: Path) -> list[str]:
    rest = len(items) - shown
    return [f"  … and {rest:,} more {label} — the run's record lists them all: read {record}"] \
        if rest > 0 else []


def render_error(label: str, value: dict) -> str:
    err = value.get("error") or {}
    lines = [f"{label}: error [{err.get('code', 'unknown')}]", f"message: {err.get('message', '')}"]
    if err.get("hint"):
        lines.append(f"hint: {err['hint']}")
    if err.get("details"):
        details = json.dumps(err["details"], ensure_ascii=False)
        if len(details) > MAX_DETAILS:
            details = (f"{details[:MAX_DETAILS]}… (truncated: {len(details) - MAX_DETAILS:,} more "
                       "characters)")
        lines.append("details: " + details)
    return "\n".join(lines)


def _log(name: str, tail, path: Path) -> list[str]:
    """A run's log in an analytics_run reply: whole, or its last MAX_LOG characters said to be cut, with
    the path of the whole log. The kit's tail of a log larger than core.OUTPUT_TAIL bytes begins
    with a "…" of its own (runner._tail): that "…" is taken off, and only then, so a log whose own
    text begins with "…" keeps it."""
    text = tail or ""
    try:
        cut = path.stat().st_size > core.OUTPUT_TAIL
    except OSError:
        cut = False
    if cut:
        text = text.removeprefix("…")
    text = text.strip()
    if not text:
        return []
    if not cut and len(text) <= MAX_LOG:
        return [f"{name} (tail):", text]
    shown = text[-MAX_LOG:]
    return [f"{name} (truncated: the last {len(shown):,} characters; the whole log: read {path}):",
            shown]


def render_run(project, value: dict, attached: tuple[int, int] | None = None) -> str:
    run = value["run"]
    status = run.get("status")
    record = project.runs / run["id"] / "run.json"
    head = f"analytics_run: {status} — run {run['id']} ({run.get('duration_s', '?')} s)"
    if run.get("error"):
        err = run["error"]
        head += f" — {err.get('summary')}" + (f" at line {err['line']}" if err.get("line") else "")
    script = run.get("script") or {}
    lines = [head, f"title: {run.get('title')}",
             f"code: {script.get('path')} sha256 {str(script.get('sha256'))[:12]} "
             f"(snapshot {script.get('snapshot')})"]
    if (run.get("error") or {}).get("hint"):
        lines.insert(1, f"fix: {run['error']['hint']}")
    inputs = run.get("inputs") or []
    lines.append(f"inputs ({len(inputs)}):" if inputs else "inputs: none detected")
    for i in inputs[:MAX_LIST]:
        lines.append(f"  {i['path']} sha256 {str(i.get('sha256'))[:12]} via {','.join(i['via'])}")
    lines += _more(inputs, MAX_LIST, "input file(s)", record)
    outputs = [o for o in run.get("outputs") or []]
    if outputs:
        lines.append(f"outputs ({len(outputs)}):")
    for o in outputs[:MAX_LIST]:
        names = [c[0] for c in (o.get("schema") or [])]
        cols = ", ".join(names[:MAX_COLUMNS])
        if len(names) > MAX_COLUMNS:
            cols += f", +{len(names) - MAX_COLUMNS:,} more columns"
        rows = f" {o['rows']:,} rows [{cols}]" if isinstance(o.get("rows"), int) else ""
        lines.append(f"  {o['path']} {o.get('change')}{rows}")
    lines += _more(outputs, MAX_LIST, "output file(s)", record)
    # The run's Visualizations and its rerun comparison, whichever record holds them (a lab.run/1
    # run's charts, an analytics.run/2 run's visualizations: core.relations, design 5.3).
    charts = core.relations.run_visualizations(run)
    lines.append(f"charts ({len(charts)}):" if charts else "charts: none")
    for c in charts[:MAX_CHART_LIST]:
        data = f"data {c['data_rows']:,} rows" if c.get("data") else "no plotted data recorded"
        files = ", ".join((c.get("files") or {}).values())
        lines.append(f"  {c['key']} \"{_one(c.get('title'), 80)}\" "
                     f"{c.get('kind')} via {c.get('via')} · {data} · {files}")
    lines += _more(charts, MAX_CHART_LIST, "chart(s)", record)
    if attached and attached[1]:
        shown, total = attached
        line = f"images: {shown} of {total} chart PNG(s) attached below"
        if shown < total:
            line += "; open the rest with the read tool at the paths above"
            unlisted = sum(1 for c in charts[MAX_CHART_LIST:] if (c.get("files") or {}).get("png"))
            if unlisted:
                line += f" and, for the {unlisted} chart(s) not listed, at the paths in read {record}"
        lines.append(line)
    repro = core.relations.run_reproduction(run)
    if repro:
        parts = [f"{k}: {v}" for k, v in {**repro["visualizations"], **repro["outputs"]}.items()]
        lines.append(f"reproduction of {repro.get('of')}: {repro.get('verdict')} (inputs "
                     f"{'same' if repro.get('inputs_same') else 'differ'}; {'; '.join(parts)})")
    if status != "ok":
        packages = (run.get("env") or {}).get("packages") or {}
        pinned = {k: packages[k] for k in core.ANALYSIS_PACKAGES if k in packages}
        if pinned:
            lines.append(f"environment: {core.versions_line(pinned)} — check the failing call "
                         "against these versions' APIs before retrying")
    problems = value.get("finalize_errors") or run.get("finalize_errors")
    if problems:
        lines.append("RECORDING FAILED for part of this run (the rest is recorded):")
        lines += [f"  - {p}" for p in problems]
    if run.get("warnings"):
        lines.append("warnings:")
        lines += [f"  - {w}" for w in run["warnings"][:MAX_LIST]]
        lines += _more(run["warnings"], MAX_LIST, "warning(s)", record)
    logs = {name: project.path(run.get(name) or f"runs/{run['id']}/{name}.txt")
            for name in ("stdout", "stderr")}
    lines += _log("stdout", value.get("stdout_tail"), logs["stdout"])
    stderr = (value.get("stderr_tail") or "").strip()
    if stderr and (status != "ok" or len(stderr) < OK_STDERR):
        lines += _log("stderr", stderr, logs["stderr"])
    elif stderr:
        lines.append(f"stderr (not shown, the run is ok; the whole log: read {logs['stderr']})")
    page = project.root / value.get("page", "")
    lines.append(f"journal: {value.get('event')} · report: {page}")
    if value.get("workspace_error"):
        lines.append(f"report build failed: {value['workspace_error']}")
    return _clip("\n".join(lines), f"the run page is {page}; the whole record: read {record}")


def chart_images(project, run: dict, ctx) -> tuple[list, int]:
    """Chart PNGs to attach, and how many charts had one to attach at all."""
    charts = [c for c in core.relations.run_visualizations(run) if (c.get("files") or {}).get("png")]
    model = getattr(ctx, "model", None)
    if model is None or "image" not in (getattr(model, "input", None) or []):
        return [], len(charts)
    images = []
    for chart in charts:
        path = project.path(chart["files"]["png"])
        with contextlib.suppress(OSError):
            if path.stat().st_size <= MAX_IMAGE_BYTES:
                images.append(ImageContent(mime_type="image/png",
                                           data=base64.b64encode(path.read_bytes()).decode()))
        if len(images) >= MAX_IMAGES:
            break
    return images, len(charts)


def _cell(value, width: int = MAX_CELL) -> str:
    s = "null" if value is None else str(value)
    return s if len(s) <= width else s[:width - 1] + "…"


def _table(columns, rows, width=MAX_CELL) -> tuple[list[str], list[str]]:
    """A query result as render_table lays it out: its three head lines, and a string for each row
    (a value with a line break in it makes its row more than one line)."""
    names = [c["name"] for c in columns]
    types = [_cell(c["type"], width) for c in columns]
    cells = [[_cell(v, width) for v in row] for row in rows]
    widths = [min(width, max([len(n), len(t)] + [len(r[i]) for r in cells]))
              for i, (n, t) in enumerate(zip(names, types, strict=False))]
    head = [" | ".join(n.ljust(w) for n, w in zip(names, widths, strict=False)),
            " | ".join(t.ljust(w) for t, w in zip(types, widths, strict=False)),
            "-+-".join("-" * w for w in widths)]
    return head, [" | ".join(v.ljust(w) for v, w in zip(r, widths, strict=False)) for r in cells]


def render_table(columns, rows, width=MAX_CELL) -> str:
    head, body = _table(columns, rows, width)
    return "\n".join(head + body)


# How to read a value whole that render_table cut: in parts that each fit a cell.
QUERY_CELLS = (f"a value cut to fit its cell ends in …: read it whole in parts of {MAX_CELL} "
               f"characters, substr(<column>, <start>, {MAX_CELL}) for <start> = 1, {MAX_CELL + 1}, "
               f"{2 * MAX_CELL + 1}, … up to length(<column>)")


def render_query(value: dict) -> str:
    """An analytics_query reply: the result's rows, as many whole rows as fit in MAX_TEXT. When that is
    fewer than the kit fetched, the first line says how many are shown of how many, and a last line
    how to page to the rest: query results are not stored."""
    rows = value["rows"]
    head, body = _table(value["columns"], rows)  # a row at a time, its line breaks and all
    limit = " (truncated at limit)" if value["truncated"] else ""
    tail = (f" in {value['elapsed_ms']} ms; views used: "
            f"{', '.join(value.get('tables') or []) or '-'}")

    def compose(n: int) -> str:
        notes = []
        if n == len(rows):
            first = f"analytics_query: ok — {n} row(s){limit}{tail}"
        else:
            first = f"analytics_query: ok — {n} row(s) shown of {len(rows)} fetched{limit}{tail}"
            notes.append(f"… {len(rows) - n:,} more row(s) not shown, after the first {n:,}: query "
                         "results are not stored — select fewer columns, or ORDER BY and page with "
                         "LIMIT/OFFSET")
        if any(v is not None and len(str(v)) > MAX_CELL for row in rows[:n] for v in row):
            notes.append(QUERY_CELLS)
        return "\n".join([first, *head, *body[:n], *notes])

    n = len(rows)
    text = compose(n)
    if len(text) > MAX_TEXT:
        room, n = MAX_TEXT - len(compose(0)), 0
        while n < len(body) and room >= len(body[n]) + 1:
            room -= len(body[n]) + 1
            n += 1
        text = compose(n)
        while n > 0 and len(text) > MAX_TEXT:
            n -= 1
            text = compose(n)
    return _clip(text, "query results are not stored: select fewer columns")


def _int(args, name, default, low, high) -> int:
    value = args.get(name)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(low, min(high, value))


# --- tools ---

def bypass_note(project) -> str:
    """Runs made with bash instead of analytics_run leave no code, inputs or charts behind."""
    flagged = 0
    with contextlib.suppress(OSError):
        for row in core.read_jsonl(project.telemetry):
            flagged += "python_via_bash" in (row.get("flags") or [])
    if not flagged:
        return ""
    return (f"\nnote: python was run through bash {flagged} time(s) in this project; anything it "
            "wrote has no recorded code, inputs or charts — use analytics_run for analysis steps")


async def tool_status(args, ctx):
    project, via = resolve_project()
    if args:
        st = await asyncio.to_thread(lambda: core.status(project, **args))
        return "analytics_status: ok\n" + core.retrieval_text(st), st
    await asyncio.to_thread(lambda: core.sync_context(project, origin=origin("system", "tool")))
    st = await asyncio.to_thread(lambda: core.status(project, asset_session=STATE["session"]))
    text = f"analytics_status: ok (project found via {via})\n" + core.status_text(st)
    return text + await asyncio.to_thread(bypass_note, project), st


async def tool_query(args, ctx):
    project, _ = resolve_project()
    sql = str(args.get("sql") or "").strip()
    if not sql:
        raise core.AnalyticsError("query_empty", "sql is empty")
    limit = _int(args, "limit", 50, 1, 500)
    ok, value = await run_kit(project, ["query", sql, "--limit", str(limit)], timeout=180)
    if not ok:
        return render_error("analytics_query", value), value, True
    return render_query(value), value


async def tool_import(args, ctx):
    project, _ = resolve_project()
    source = str(args.get("source") or "").strip()
    if not source:
        raise core.AnalyticsError("import_source_missing", "source is empty")
    src = core.expand_home(Path(source))  # the rule `analytics import` and the kit's paths follow
    src = src if src.is_absolute() else Path(os.getcwd()) / src
    cmd = ["import", str(src)]
    if args.get("name"):
        cmd += ["--name", str(args["name"])]
    if args.get("description"):
        cmd += ["--description", str(args["description"])]
    # Refused here, in the aelix process, before uv makes or syncs the project's environment.
    await asyncio.to_thread(core.admit, project, "import")
    ok, value = await run_kit(project, cmd, timeout=900)
    if not ok:
        return render_error("analytics_import", value), value, True
    ds, prof = value["dataset"], value["profile"]
    lines = [f"analytics_import: ok — dataset {ds['name']} ({ds['rows']:,} rows × {len(ds['columns'])} "
             f"columns) at {ds['path']} · sha256 {ds['sha256'][:12]} · journal {value['event']}",
             f"source: {ds['source']['path']} ({ds['source']['format']}, sha256 "
             f"{ds['source']['sha256'][:12]})", "columns:"]
    for c in prof["columns"]:
        stats = ", ".join(f"{k} {c[k]}" for k in ("min", "max", "mean", "distinct")
                          if c.get(k) is not None)
        lines.append(f"  {c['name']}: {c['dtype']} · nulls {c['nulls']}" + (f" · {stats}" if stats else ""))
    preview = prof["preview"]
    cols = [{"name": n, "type": ""} for n in preview["columns"]]
    lines += ["preview:", render_table(cols, preview["rows"][:5])]
    if value.get("warnings"):
        lines.append("warnings:")
        lines += [f"  - {w}" for w in value["warnings"]]
    lines.append(f"Query it as view `{ds['name']}` (analytics_query) or analytics.read(\"{ds['name']}\") in scripts.")
    meta = project.path(ds["path"]).with_suffix(".meta.json")
    page = project.workspace_dir / "data" / f"{ds['name']}.html"
    return _clip("\n".join(lines), f"the dataset page is {page}; every column and its type: read "
                                   f"{meta}"), value


async def tool_run(args, ctx):
    project, _ = resolve_project()
    title = str(args.get("title") or "").strip()
    if not title:
        raise core.AnalyticsError("run_needs_title", "title is required: say what this run is for")
    script, code, name = args.get("script"), args.get("code"), args.get("name")
    rerun_of = str(args.get("rerun_of") or "").strip()
    given = sum(bool(x) for x in (script, code, rerun_of))
    if given != 1:
        raise core.AnalyticsError("run_args", "pass exactly one of script, code (with name) or rerun_of")
    note = ""
    # Refused here, in the aelix process, before an inline script is written and before uv makes or
    # syncs the project's environment (design 7).
    await asyncio.to_thread(core.admit, project, "run")
    if rerun_of:
        if not core.RUN_ID_RE.match(rerun_of):
            raise core.AnalyticsError("rerun_id", f"{rerun_of!r} is not a run id",
                                      hint="analytics_status lists run ids such as 20260916-143012-monthly-sales")
        cmd = ["run", "--rerun-of", rerun_of]
    elif code:
        name = str(name or "").strip()
        if not core.SCRIPT_NAME_RE.match(name):
            raise core.AnalyticsError("run_name", "code needs a name like 02_monthly_sales",
                                      hint="lowercase letters, digits and _; the code is saved to scripts/<name>.py")
        target = project.scripts / f"{name}.py"
        core.reject_unportable_name(name, kind="script", exists=target.exists())
        note = f" (overwrote scripts/{name}.py)" if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        # Inline code is text the kit writes: LF on every platform, inside as well (ADR-0007
        # decision 2). Python reads CRLF, CR and LF alike as line ends, so it runs the same.
        core.write_text(target, core.newlines_as_lf(str(code).rstrip()) + "\n")
        cmd = ["run", f"scripts/{name}.py"]
    else:
        cmd = ["run", str(script)]
    timeout = _int(args, "timeout_s", 600, 10, 3600)
    cmd += ["--title", title, "--timeout", str(timeout)]
    for item in args.get("inputs") or []:
        cmd += ["--input", str(item)]
    ok, value = await run_kit(project, cmd, timeout=timeout + 180)
    if not ok:
        return render_error("analytics_run", value), value, True
    images, with_png = ((await asyncio.to_thread(chart_images, project, value["run"], ctx))
                        if args.get("attach_images", True) else ([], 0))
    text = render_run(project, value, (len(images), with_png))
    if note:
        text = text.replace("\n", f"{note}\n", 1)
    return [TextContent(text=text), *images], value, value["run"].get("status") != "ok"


async def tool_record(args, ctx):
    project, _ = resolve_project()
    kind = str(args.get("kind") or "")
    text = str(args.get("text") or "")
    refs = list(args.get("refs") or [])
    details = args.get("details")
    resolves = args.get("resolves") or None
    authored = origin("agent", "tool")
    # What the record refuses before its lock is refused before the context sync: a refused record
    # changes nothing (core.check_record).
    await asyncio.to_thread(lambda: core.check_record(project, kind, text, refs=refs, resolves=resolves,
                                                      details=details, origin=authored))
    if kind != "checkpoint":
        await asyncio.to_thread(lambda: core.sync_context(project, origin=core.system_origin(authored)))
    written = await asyncio.to_thread(
        lambda: core.record(project, kind, text, refs=refs, resolves=resolves,
                            details=details, origin=authored))
    schedule_workspace_render(project, "tool")
    return f"analytics_record: ok — {recorded_line(written, kind)}", written.to_json()


def recorded_line(written, kind: str) -> str:
    """A record's reply: its id and kind, the record it resolved or corrected with that record's
    state before and after (Written.targets: an already resolved one says which event resolved it),
    and each name given as a reference with how it was bound (author, inferred, none — an inferred
    binding is a guess the kit made, not a confirmed link: Q4). The request_id is in the details
    (design 4.9), not in the text: no input takes one yet (OQ11)."""
    event = written.event
    parts = [f"{event['id']} {kind} recorded"]
    for target in written.targets or []:
        said = (f"{target['relation']} {target['event_id']} "
                f"({target.get('state_before')} -> {target.get('state_after')}")
        made = target.get("made_by") or {}
        if target.get("state_before") == "resolved" and made.get("id"):
            said += f"; already resolved by {made['id']}"
        parts.append(said + ")")
    if written.bindings:
        parts.append("refs: " + ", ".join(f"{b['input']} ({b['binding']})" for b in written.bindings))
    return "; ".join(parts)


async def tool_init(args, ctx):
    raw = str(args.get("path") or "").strip()
    if not raw:
        raise core.AnalyticsError("init_path", "path is required")
    target = Path(raw).expanduser()
    target = target if target.is_absolute() else Path(os.getcwd()) / target
    project = await asyncio.to_thread(
        lambda: core.init_project(target, title=args.get("title"), goal=args.get("goal"),
                                  origin=origin("agent", "tool")))
    STATE["project"] = str(project.root)
    synced, detail = await sync_environment(project)
    lines = [f"analytics_init: ok — project {project.title()} at {project.root} (now the active project)"]
    if synced:
        await run_kit(project, ["render"], timeout=240, actor="agent", means="tool")
        lines.append(f"environment: synced from {detail} (DuckDB, Polars, PyArrow, matplotlib, plotly)")
    else:
        lines.append("environment: uv sync FAILED — runs will fail until it succeeds. uv said:\n" + detail)
    lines += [f"report: {project.workspace_dir / 'index.html'}",
              "next: fill CONTEXT.md (goal, questions, data sources), analytics_import the data, then write "
              "scripts/01_<topic>.py and analytics_run it."]
    return "\n".join(lines), {"project": str(project.root), "synced": synced,
                              "request_id": project.request_id}


def _tool(name, description, properties, required, handler, snippet, guidelines=()):
    async def execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        try:
            out = await handler(args or {}, ctx)
        except core.AnalyticsError as exc:
            value = exc.to_json()
            return ToolResult(content=[TextContent(text=render_error(name, value))], details=value,
                              is_error=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - report instead of failing the turn
            value = _error("internal_error", f"{type(exc).__name__}: {exc}",
                           hint="this is a bug in the analytics extension; analytics_status shows what was recorded")
            return ToolResult(content=[TextContent(text=render_error(name, value))], details=value,
                              is_error=True)
        content, details = out[0], out[1]
        is_error = out[2] if len(out) > 2 else False
        if isinstance(content, str):
            content = [TextContent(text=content)]
        return ToolResult(content=content, details=details, is_error=is_error)

    parameters = {"type": "object", "additionalProperties": False, "properties": properties}
    if required:
        parameters["required"] = required
    return AgentTool(name=name, description=description, parameters=parameters, execute=execute,
                     prompt_snippet=snippet, prompt_guidelines=tuple(guidelines),
                     execution_mode="sequential")


STR = {"type": "string"}

TOOLS = [
    _tool(
        "analytics_status",
        "Overview of the active analytics project: datasets (Parquet) with query view names, recent "
        "runs, the latest version of every chart with staleness (inputs changed since the chart was "
        "made), open questions/todos, key findings/decisions/assumptions and the recent journal. "
        "Read-only. With part=records, search journal judgments by words or read an exact ID; "
        "part=relations reads one-hop evidence; part=context reads complete CONTEXT sections. "
        "Pages share source_revision; restart on stale_cursor. Old judgments and missing evidence "
        "are not current facts. Default overview is unchanged.",
        {"part": {"type": "string", "enum": ["records", "relations", "context"]},
         "query": {**STR, "description": "Literal words, including short Korean/code names; records only."},
         "id": {**STR, "description": "Exact record ID (eN, journal:line, run:ID, report:ID/version), or context:version."},
         "cursor": {**STR, "description": "Opaque next-page token from this same query."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 100},
         "kind": {**STR, "description": "Journal kind filter; records only."},
         "state": {**STR, "description": "Replayed record-state filter; records only."},
         "since": {**STR, "description": "Inclusive ISO recorded timestamp with UTC offset; records only."},
         "until": {**STR, "description": "Inclusive ISO recorded timestamp with UTC offset; records only."},
         "direction": {"type": "string", "enum": ["both", "incoming", "outgoing"]},
         "as_of_seq": {"type": "integer", "minimum": 0,
                       "description": "Journal knowledge boundary; physical evidence is checked now."}},
        [], tool_status,
        "overview of the analysis project: datasets, runs, charts (with staleness), journal",
    ),
    _tool(
        "analytics_query",
        "Run ONE read-only DuckDB statement over the project's datasets, for exploration only. Every "
        "Parquet dataset is a view named after its file (data/imported/sales.parquet -> sales; a derived "
        "dataset whose name collides gets _derived). SELECT/WITH, DESCRIBE <view>, SUMMARIZE <view> "
        "and EXPLAIN are allowed; anything that writes is refused. Returns at most `limit` rows. "
        "Queries are logged but are not runs: anything that feeds a chart, a derived dataset or a "
        "recorded finding must be done with analytics_run.",
        {"sql": {**STR, "description": "One DuckDB SQL statement."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                   "description": "Maximum rows to return (default 50)."}},
        ["sql"], tool_query,
        "read-only DuckDB SQL over dataset views for exploration (not recorded as a run)",
    ),
    _tool(
        "analytics_import",
        "Import a local data file into the project as an immutable Parquet dataset "
        "data/imported/<name>.parquet. CSV/TSV/JSON/NDJSON are converted with DuckDB; Parquet is copied. "
        "Records the source path and sha256 and the schema, and logs an import event. An existing "
        "name is refused (imported data never changes; use a new name such as sales_v2).",
        {"source": {**STR, "description": "Path of the file to import (absolute, or relative to the "
                                          "aelix working directory)."},
         "name": {**STR, "description": "Dataset name: lowercase letters, digits, _ (default: file stem)."},
         "description": {**STR, "description": "What the data is: origin, period, grain, caveats."}},
        ["source"], tool_import,
        "import a CSV/JSON/Parquet file as an immutable Parquet dataset under data/imported/",
    ),
    _tool(
        "analytics_run",
        "Execute a Python analysis script in the project's own uv environment (DuckDB, Polars, PyArrow, "
        "matplotlib, plotly) with the project root as working directory, and record its provenance: "
        "exact code snapshot + sha256, input files + sha256, files it wrote, charts with their plotted "
        "data, stdout/stderr, timing and package versions. The HTML report is rebuilt afterwards; "
        "every chart links to that code and data. Give exactly one of: `script` (a .py under scripts/, "
        "preferred: write it with the write tool, then run it; edit and re-run the same script to "
        "version its charts), `code` + `name` (saved to scripts/<name>.py, overwriting), or `rerun_of` "
        "(re-execute a recorded run's code snapshot and compare charts and outputs).\n"
        "Script API: import aelix_analytics as analytics; analytics.read('sales') -> Polars DataFrame; analytics.scan('sales') -> "
        "LazyFrame; analytics.sql(\"SELECT ... FROM sales\") -> DuckDB relation over all dataset views (.pl() "
        "for Polars); analytics.save(df, 'monthly_sales') -> data/derived/monthly_sales.parquet; "
        "analytics.visualize(fig, 'monthly-revenue', title=..., caption=..., data=plotted_df) for a matplotlib "
        "Figure/Axes or plotly Figure (id: short ASCII, stable across versions); analytics.record('finding', "
        "text) links a journal entry to this run. Plain pl.read_parquet('data/...'), DuckDB SQL over "
        "'data/...parquet' literals, plt.show() and fig.show() are captured too, but only analytics.visualize "
        "with data= records exactly what was plotted. matplotlib has a Korean-capable font preset.",
        {"title": {**STR, "description": "What this run is for, in one line (shown in the journal)."},
         "script": {**STR, "description": "Project-relative path of the script, e.g. scripts/02_monthly_sales.py."},
         "code": {**STR, "description": "Full script source; requires name."},
         "name": {**STR, "description": "Script name for code, e.g. 02_monthly_sales."},
         "rerun_of": {**STR, "description": "Run id to re-execute from its code snapshot."},
         "inputs": {"type": "array", "items": STR,
                    "description": "Input files the script reads that detection might miss."},
         "timeout_s": {"type": "integer", "minimum": 10, "maximum": 3600,
                       "description": "Seconds before the run is stopped (default 600)."},
         "attach_images": {"type": "boolean",
                           "description": "Return PNG charts as images when the model accepts images "
                                          "(default true)."}},
        ["title"], tool_run,
        "run a Python analysis script with recorded code, data and charts; rebuilds the HTML report",
        guidelines=(
            "Analysis data is Parquet: data/imported is imported and never modified; derived tables go to "
            "data/derived via analytics.save. Process data with DuckDB and Polars, not pandas.",
            "Make every chart and derived dataset with analytics_run, never by running python through bash, "
            "so its code and input data are recorded and linked from the report.",
            "In scripts prefer analytics.read/analytics.sql for data and analytics.visualize(fig, id, title=, caption=, "
            "data=plotted_df) for charts; one script per question, re-run it to version its charts.",
            "Record findings, decisions, assumptions, questions and todos with analytics_record as soon as they "
            "are established, citing run ids; keep CONTEXT.md current by editing it.",
            "If the analytics tools block you or force a workaround, record it with analytics_record "
            "kind=workflow_feedback (this tooling is being evaluated).",
        ),
    ),
    _tool(
        "analytics_record",
        "Append an entry to the project journal, the time-ordered history shown in the report. kind: "
        "finding (an established result), decision, assumption, question, todo, note, "
        "workflow_feedback (a missing capability or friction in this analysis tooling), metric_definition "
        "(a versioned draft or specified definition), analysis_focus (purpose and exact definitions), "
        "metric_observation (a declared result, exact definition, run and scope), asset_candidate, "
        "asset_check, asset_effect (analysis_asset/1 details), checkpoint (an authored summary with exact basis), or resolve "
        "(closes the question/todo named in `resolves` without other content). Ordinary journal kinds may also "
        "resolve an earlier entry, e.g. a finding that answers question e7. Structured metric and asset kinds "
        "derive state relationships from details and reject caller state-changing refs. Cite evidence in refs: "
        "run ids, chart keys (scripts/x.py::chart-id), dataset names or event ids; the reply says "
        "how each was bound.",
        {"kind": {"type": "string", "enum": list(core.RECORD_KINDS)},
         "text": {**STR, "description": "The entry, self-contained. Kept whole up to 32 KiB of UTF-8; "
                                        "a longer one is refused, never cut."},
         "refs": {"type": "array", "items": {"anyOf": [STR, {"type": "object"}]},
                  "description": "Run ids, chart keys, dataset names, event ids, or exact typed references."},
         "details": {"type": "object", "description": "Structured metric_definition/1, analysis_focus/1, "
                     "metric_observation/1, analysis_asset/1, or analysis_checkpoint/1 payload. Exact versions and explicit scope; "
                     "state relationships are generated from these fields. See the metric/asset contract docs."},
         "resolves": {**STR, "description": "Event id this entry answers or closes, e.g. e7."}},
        ["kind", "text"], tool_record,
        "record a finding/decision/assumption/question/todo/workflow_feedback in the project journal",
    ),
    _tool(
        "analytics_init",
        "Create a new analytics project directory (analytics.json, CONTEXT.md, journal.jsonl, data/, "
        "scripts/, runs/, workspace/) with its own uv environment (DuckDB, Polars, PyArrow, "
        "matplotlib, plotly) and make it the active project. Only when the user asked to start a new "
        "analysis; if they did not say where, ask them first. The directory must not exist or must "
        "be empty, and must not be inside another analytics project.",
        {"path": {**STR, "description": "Directory to create."},
         "title": {**STR, "description": "Project title."},
         "goal": {**STR, "description": "What the analysis should answer and for whom."}},
        ["path"], tool_init,
        "create a new analysis project with its own environment (only when the user asks)",
    ),
]


# --- who calls the kit through aelix's shell (design 4.2, OQ1) ---
#
# The kit's command line records AELIX_ANALYTICS_ACTOR as its author (declared), and `human`
# (inferred) when it is not set. An aelix agent runs it through aelix's bash tool, whose child gets a
# copy of the aelix process's environment on every call (aelix 0.1.0b2, the design's R9 probe), so
# this extension puts the author there: `agent`, and this session. A person's `!`/`!!` command gets the
# same environment, so for a line that is one kit command the user_bash handler hands aelix its own
# local shell with AELIX_ANALYTICS_ACTOR=human for that command alone. A record of origin, not
# authentication: a command may change the variable, and is recorded as it says (CONTRACTS A).

def put_the_author_in_the_environment() -> None:
    """At setup: the agent writes through this aelix process's shells (overwriting a value the shell
    that started aelix had: an aelix agent's command is the agent's, owner's decision Q17-b), and no
    session until this session's is known (aelix sets extensions up again for every session)."""
    os.environ["AELIX_ANALYTICS_ACTOR"] = "agent"
    put_the_session_in_the_environment(None)


def put_the_session_in_the_environment(session) -> None:
    if session:
        os.environ["AELIX_ANALYTICS_SESSION"] = str(session)
    else:
        os.environ.pop("AELIX_ANALYTICS_SESSION", None)


# Which `!` line runs in a person's shell (design 4.2; the owner's decision of 2026-10-09 as corrected,
# #13 issuecomment-6072183477): only a line that is, as a whole, one simple command calling a kit entry
# point, read as the shell aelix runs it in reads it. aelix picks that shell by its own rule ($SHELL; on
# Windows $SHELL when it names a file, then PowerShell, then cmd: the_shell), and two families are read
# here: the POSIX shells (sh, bash, zsh, dash, ksh, mksh, ash) and PowerShell (pwsh, powershell). Under
# any other shell — cmd, fish, nu, csh, one this reading does not know — every line is left alone.
#
# A kit entry point is the program `analytics` (any directory: `analytics`, `./bin/analytics`,
# `analytics.exe`), or Python given `-m aelix_analytics[.…]` (also bundled, `-Im`) or a `-c` that
# imports it, also after `uv run` and the options it is known to take. In a POSIX shell the program may
# follow `NAME=value` words, `env` with the options it is known to take, `exec`, `command`, `time` and
# `nohup`; in PowerShell its call operator `&` at the start. The command may carry redirections to or
# from a file or a descriptor (`> out.txt`, `2>&1`; in PowerShell output ones only).
#
# Anything else makes the line no single kit command: an operator — `;` `&` `&&` `|` `||` `|&`, a new
# line, `(` `)` (a subshell, `$(…)`, `<(…)`), `<<`, `<<<`, `&>` — wherever it is not quoted or escaped,
# and whatever this reading leaves open: `$` outside single quotes (an expansion: `$'…'`, `$"…"`,
# `${…}`, `$[…]`, `$((…))`, even `$HOME`), a backtick, a quote not closed, a backslash ending the line,
# a comment (`#` at a word's start) that a new line follows, a control character but a tab and a new
# line, `env -S` (env runs the first word of its string), any option of env or `uv run` not known
# here (it may take the next word as its value, and the program is another word then), and a pattern or
# a brace the shell expands into words (`*` `?` `[` `{` `^`, `#` `~` inside a word: `env -u {X,touch}
# analytics` runs `touch`). `#` and what follows it are read as well, so an operator there counts,
# while the words judged end at it (the shell runs the words before a comment). In PowerShell, where a
# backslash is a character and not an escape, also: a new line, a backtick (its escape), a quote it
# takes besides ' and " (‘ ’ “ ” …), a space besides a space and a tab, `--%`, `{` `}`, `<` (`<#` opens
# a comment), a `>&` but `n>&1` written so, a line its POSIX reading would refuse, and a program word
# that does not start with a letter, `_` or a path (`1/…` `@…` `[…]` are expressions there;
# `-c/analytics` is left alone too). Where PowerShell's words are not a POSIX shell's, too: a quoted
# text starting a word that goes on after its closing quote (`& 'touch'/x/analytics` runs `touch`
# there), a quote after other text but in a word starting with `-` (`.'x'` dot-sources `x`), a
# parameter-like word with an unquoted `.` (`uv run -p3.12 analytics` passes `-p3` and `.12`, so uv
# runs `.12`), a `,` (an array), `@` or `]` starting a word, a `>` inside a word, a redirection before
# the program (its name there) and `2>& 1`. On Windows, in any shell, also a word holding a character
# cmd reads (CMD_METACHARACTERS): a program found by its bare name may be a batch file there. A
# program that is not the kit (`grep analytics notes.txt`, `sh -c '…'`, `python a.py`) is none either.
# Left alone as well, though they run the kit and no second command: `uv run -m`/`--module` (the kit runs
# as `uv run … python -m aelix_analytics` or `analytics`), `sudo analytics …` (sudo takes options of its
# own), a trailing `&` (a list in the background, not a simple command) and a trailing `;` (an
# operator here, though nothing follows it), and in PowerShell a mid-word quote in a program word
# (`ana'lytics'`, which it does run as `analytics`) and its all-streams redirection `*>`, `*>>`,
# `*>&1` (`analytics status *> log.txt` is one command with one redirection there, pwsh 7.6's
# parser; both readings here take its `*` for a wildcard first: a conservative leave, CONTRACTS A).
# For such a line the handler returns nothing: aelix runs it as it would, another extension's block or
# shell included, and a kit call in it records `agent`/declared from the process environment. So the
# handler sets another extension's answer aside for that one kit command only (CONTRACTS A).
POSIX_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash"})
POWERSHELL_SHELLS = frozenset({"pwsh", "powershell"})
KIT_PROGRAMS = frozenset({"analytics"})
# Not a batch file (`analytics.cmd`, `.bat`): Windows runs one through cmd, which reads its arguments
# again by its own rules (`'a" & x & "b'` runs `x`), and the kit ships none.
KIT_PROGRAMS_WINDOWS = frozenset({"analytics.exe"})
# Words that run the word after them, as the same one command (a POSIX shell's).
COMMAND_PREFIXES = frozenset({"env", "exec", "command", "time", "nohup"})
# env's options (GNU and BSD env) this reading knows: those that take no value, and those that take the
# next word — or the rest of their own (`-uNAME`, `--unset=NAME`) — as it. `-S`/`--split-string` is not
# among them: env splits its string into words and runs the first, so the program is the string's.
ENV_FLAGS = frozenset({"-", "-i", "--ignore-environment", "-0", "--null", "-v", "--debug"})
ENV_VALUE_OPTIONS = frozenset({"-u", "--unset", "-C", "--chdir", "-P", "-a", "--argv0"})
PYTHON_PROGRAM = re.compile(r"(?:python[\d.]*w?|py)(?:\.exe)?", re.IGNORECASE)
# Python's one-letter options that take no value, which may be bundled before -m or -c (`-Im`).
PYTHON_FLAGS = frozenset("bBdEhiIOPqRsSuvVx")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# `uv run`'s options (uv 0.11, `uv run --help`) this reading knows: those that take no value, and those
# that take the next word — or the rest of their own (`--with=polars`, `-p3.12`) — as it. `-m`/`--module`,
# `-s`/`--script` and `--gui-script` are not among them (the next word is no program then), nor is any
# option uv adds later.
UV_RUN_FLAGS = frozenset({
    "--active", "--all-extras", "--all-groups", "--all-packages", "--compile-bytecode", "--exact",
    "--frozen", "--isolated", "--locked", "--managed-python", "--no-binary", "--no-build",
    "--no-build-isolation", "--no-cache", "-n", "--no-config", "--no-default-groups", "--no-dev",
    "--no-editable", "--no-env-file", "--no-index", "--no-managed-python", "--no-progress",
    "--no-project", "--no-python-downloads", "--no-sources", "--no-sync", "--offline", "--only-dev",
    "--refresh", "--reinstall", "--system-certs", "--upgrade", "-U", "--quiet", "--verbose"})
UV_RUN_COUNTED = re.compile(r"-(?:q+|v+)")
UV_RUN_VALUE_OPTIONS = frozenset({
    "--extra", "--no-extra", "--group", "--no-group", "--only-group", "--no-editable-package",
    "--env-file", "-w", "--with", "--with-editable", "--with-requirements", "--package",
    "--python-platform", "--index", "--default-index", "-i", "--index-url", "--extra-index-url", "-f",
    "--find-links", "--index-strategy", "--keyring-provider", "-P", "--upgrade-package",
    "--upgrade-group", "--resolution", "--prerelease", "--fork-strategy", "--exclude-newer",
    "--exclude-newer-package", "--no-sources-package", "--reinstall-package", "--link-mode", "-C",
    "--config-setting", "--config-settings-package", "--no-build-isolation-package",
    "--no-build-package", "--no-binary-package", "--cache-dir", "--refresh-package", "-p", "--python",
    "--python-preference", "--color", "--allow-insecure-host", "--directory", "--project",
    "--config-file"})
SHELL_OPERATOR_CHARS = frozenset("();<>|&")
POWERSHELL_OPERATOR_CHARS = frozenset("();<>|&{}")
# What a POSIX shell expands into any number of words when it is not quoted: a pathname pattern
# (`*` `?` `[`), a brace (`{a,b}` in bash, zsh and ksh; `{ab}` with zsh's BRACE_CCL) and zsh's
# EXTENDED_GLOB patterns (`^`, and `#` `~` inside a word), which a startup file may turn on. An option's
# value made so may become two words, and the next one the program (`env -u {X,touch} analytics` runs
# `touch`). PowerShell expands `*` `?` `[` on Unix too (its native globbing); a `*` right before `>`
# is its all-streams redirection there, not a pattern, and is left alone all the same (see above).
SHELL_PATTERN_CHARS = frozenset("*?[{^")
SHELL_PATTERN_CHARS_IN_A_WORD = frozenset("#~")
# The redirections one simple command may carry, with a descriptor number before them: to or from a
# file or a descriptor. A here-document or here-string (`<<`, `<<<`), `&>` (in a POSIX shell, `&` and
# then `>`), `<(` and `>(` are not among them. PowerShell's are a stream's to a file (`>`, `2>>`) and
# its merge `n>&1` (`<` is reserved there, and a `>&` it does not take may leave `&` its background
# operator: `x>&touch y` runs `touch y`); its all-streams `*>`, `*>>`, `*>&1` are not taken (the
# readings stop at the `*` first, see above).
REDIRECTION = re.compile(r"[0-9]*(?:>>|>\||>&|<&|<>|<|>)")
POWERSHELL_REDIRECTION = re.compile(r"[1-6]?(?:>>|>)")
POWERSHELL_MERGE = re.compile(r"[1-6]>&")
DESCRIPTOR = re.compile(r"[0-9]+")
# Characters no line is read with: control characters but a tab and a new line (a carriage return ends
# a command in PowerShell and is a letter in a POSIX shell), and the Unicode line ends.
UNREAD_CHARACTERS = re.compile("[\x00-\x08\x0b-\x1f\x7f\x85  ]")
# What PowerShell takes as a quote besides ' and " (its tokenizer's single and double quotes), and its
# stop-parsing token `--%`, which it also takes with the dashes it knows (– — ―).
POWERSHELL_QUOTES = frozenset("‘’‚‛“”„‟")
POWERSHELL_STOP_PARSING = re.compile("[-–—―]{2}%")
# How a PowerShell program word this reading takes starts: a letter or `_`, a dot and a letter
# (`.venv\Scripts\python.exe`), or a path (`.\` `..\` `./` `\` `/` `~`). A digit, `.5`, `@`, `[`, `+`
# or `,` starts an expression there, not a command (pwsh 7's parser); `-`, `!`, `=`, `*`, `%` start a
# command name PowerShell does run (`-c/analytics` runs `./-c/analytics`) but are left alone too.
POWERSHELL_COMMAND_START = re.compile(r"[^\W\d]|\.(?:[^\W\d]|\.?[/\\])|[/\\~]")
# A word so far that PowerShell may read as a parameter, whose name an unquoted `.` ends: it passes
# `-p3.12` to a program as `-p3` and `.12` (pwsh 7.6; `[` ends it too, and is a wildcard there). A dash
# (or – — ―) not followed by another `-` or a digit, and no `:` yet (`-p:3.12`, `--python=3.12`, `-3.12`
# and `-a'b'.c` pass whole). Wider than PowerShell's rule (`-@a.b` passes whole there).
POWERSHELL_PARAMETER = re.compile(r"[-–—―](?![-\d])[^:]*")
# What cmd reads in a batch file's arguments: its operators `&` `|` `<` `>`, its escape `^`, its
# expansions `%` and `!`, its quote `"` and its blocks `(` `)`. On Windows a program a shell finds by
# its bare name through PATH and PATHEXT (`analytics`, `python`, `py`, `uv`) may be a batch file — a
# version manager's shim — which Windows starts through cmd, and cmd reads the arguments again by its own
# rules: the quoted `'x&y'` a shell passed as one word runs `y` there. These reach a word only through
# quotes or escapes, so a plain kit command holds none.
CMD_METACHARACTERS = frozenset('&|<>^%!"()')


def cmd_may_read_the_arguments() -> bool:
    """Whether the program a line names may be a batch file whose arguments cmd reads again: on
    Windows, whichever shell aelix runs the line in."""
    return sys.platform == "win32"


def the_shell() -> str:
    """aelix's name (aelix_ai.utils._shell.shell_basename: `bash`, `zsh`, `pwsh`, `cmd`, …) for the shell
    it runs a person's `!` line in: cli/repl.handle_user_bash runs it with create_local_bash_operations()
    and the environment get_shell_env() makes, whose shell tools/bash._resolve_shell picks — the shell
    PersonShell runs the line in, too."""
    from aelix_ai.utils._shell import shell_basename
    from aelix_coding_agent.tools.bash import _resolve_shell
    from aelix_coding_agent.util.shell_env import get_shell_env

    return shell_basename(_resolve_shell(get_shell_env()).path)


class _Reader:
    """Words and operators of one line, as a list of (text, whether it is an operator)."""

    def __init__(self) -> None:
        self.tokens: list[tuple[str, bool]] = []
        self.word: list[str] = []
        self.in_word = False
        self.plain = True  # the word so far has no quote and no escape: its digits may be a descriptor

    def add(self, text: str, *, plain: bool = True) -> None:
        self.word.append(text)
        self.in_word = True
        self.plain = self.plain and plain

    def end_word(self) -> None:
        if self.in_word:
            self.tokens.append(("".join(self.word), False))
        self.word.clear()
        self.in_word, self.plain = False, True

    def operator(self, line: str, i: int, chars: frozenset) -> int:
        """The run of operator characters at `i`, with the descriptor number a redirection starts
        with (`2>&`) when the word so far is only that number; returns where the run ends."""
        number = "".join(self.word) if self.in_word and self.plain and line[i] in "<>" else ""
        if DESCRIPTOR.fullmatch(number):
            self.word.clear()
            self.in_word, self.plain = False, True
        else:
            number = ""
            self.end_word()
        j = i
        while j < len(line) and line[j] in chars:
            j += 1
        self.tokens.append((number + line[i:j], True))
        return j


def _posix_words(line: str) -> list[tuple[str, bool]]:
    """The line's words and operators as a POSIX shell (sh, bash, zsh) splits them. An operator — a
    run of `;&|()<>` (with a redirection's descriptor number), and a new line (as `;`) — is one only
    where it is neither quoted nor escaped. Quotes and escapes go as the shell removes them: single
    quotes keep everything, a backslash keeps the character after it (inside double quotes only `$`
    `` ` `` `"` `\\`, before others it stays), and a backslash before a new line is a line continuation,
    gone (`uv run \\<LF>analytics` is `uv run analytics`). `#` is a character as any other. ValueError
    for what this reading leaves open: `$` or a backtick outside single quotes, a quote not closed, a
    backslash ending the line, a comment (`#` at a word's start) that a new line follows, and a
    pattern or a brace the shell would expand into words (SHELL_PATTERN_CHARS, the check of 2026-10-09
    of ab45982)."""
    reader, i, n = _Reader(), 0, len(line)
    while i < n:
        c = line[i]
        if c in " \t":
            reader.end_word()
            i += 1
        elif c == "\n":
            reader.end_word()
            reader.tokens.append((";", True))
            i += 1
        elif c in "$`":
            raise ValueError("an expansion")
        elif c == "#" and not reader.in_word and "\n" in line[i:]:
            raise ValueError("a comment a new line follows")
        elif c in SHELL_OPERATOR_CHARS:
            i = reader.operator(line, i, SHELL_OPERATOR_CHARS)
        elif c in SHELL_PATTERN_CHARS or (c in SHELL_PATTERN_CHARS_IN_A_WORD and reader.in_word):
            raise ValueError("a pattern or a brace the shell expands")
        elif c == "\\":
            following = line[i + 1:i + 2]
            if not following:
                raise ValueError("a backslash ending the line")
            if following != "\n":  # a line continuation is neither a word nor a separator
                reader.add(following, plain=False)
            i += 2
        elif c == "'":
            j = line.find("'", i + 1)
            if j < 0:
                raise ValueError("no closing quotation")
            reader.add(line[i + 1:j], plain=False)
            i = j + 1
        elif c == '"':
            j, text = i + 1, []
            while j < n and line[j] != '"':
                if line[j] in "$`":
                    raise ValueError("an expansion")
                if line[j] == "\\" and line[j + 1:j + 2] in ("$", "`", '"', "\\", "\n"):
                    text.append(line[j + 1] if line[j + 1] != "\n" else "")
                    j += 2
                else:
                    text.append(line[j])
                    j += 1
            if j >= n:
                raise ValueError("no closing quotation")
            reader.add("".join(text), plain=False)
            i = j + 1
        else:
            reader.add(c)
            i += 1
    reader.end_word()
    return reader.tokens


def _powershell_words(line: str) -> list[tuple[str, bool]]:
    """The line's words and operators as PowerShell splits them, as far as this reading needs: a
    backslash is a character, a quoted text ends at its quote when the next character is not the same
    quote (`''` and `""` inside are one quote), and an operator is a run of `;&|()<>{}` outside
    quotes. ValueError for what it leaves open: a new line or a carriage return, a backtick (its
    escape), `$` outside single quotes, a quote it takes besides ' and " (‘ ’ “ ” …), a space outside
    quotes besides a space and a tab (it splits words on a no-break space, which a POSIX shell does
    not), a quote not closed, and `--%` (what follows it would be passed on unread).

    Also where its words are not a POSIX shell's (pwsh 7.6, the check of 2026-10-09 of ab45982): a
    quoted text that starts a word but does not end it (what follows its closing quote is another word
    there — `& 'touch'/x/analytics` runs `touch` — or an expression: `'a'.Length`); a quote after other
    text in a word that does not start with `-` (`.'x'` dot-sources `x`), so that a program word holds a
    quote only as one quoted text; an unquoted `.` in a word it reads as a parameter
    (POWERSHELL_PARAMETER: `-p3.12` reaches a program as `-p3` and `.12`); an unquoted `,` (its array
    operator: an array reaches a program as its source text split at spaces, `a, "b c"` as `a,` `"b`
    `c"`); `@` starting a word (splatting: `@args` is no word at all); `]` starting a word (another
    word there); a wildcard `*` `?` `[` (it expands them on Unix; the `*` of its all-streams
    redirection `*>` `*>>` `*>&1` is read so too, which leaves that redirection alone); a `>` or `<`
    right after other text in a word but a descriptor number (`x>y` is one word there, and
    `analytics>out` its command name); and a merge `>&` not followed by 1 right away (`2>& 1`,
    `2>&'1'` are parse errors there)."""
    reader, i, n = _Reader(), 0, len(line)
    while i < n:
        c = line[i]
        if c in "\r\n`$," or c in POWERSHELL_QUOTES or (c.isspace() and c not in " \t"):
            raise ValueError("a character this reading leaves open")  # a space it splits on, too
        if c in " \t":
            reader.end_word()
            i += 1
        elif c in POWERSHELL_OPERATOR_CHARS:
            number = reader.plain and DESCRIPTOR.fullmatch("".join(reader.word))
            if c in "<>" and reader.in_word and not number:
                raise ValueError("a redirection inside a word")
            i = reader.operator(line, i, POWERSHELL_OPERATOR_CHARS)
            if reader.tokens[-1][0].endswith(">&") and line[i:i + 1] != "1":
                raise ValueError("a merge PowerShell does not take")
        elif c in "'\"":
            starts_the_word = not reader.in_word
            if not starts_the_word and not "".join(reader.word).startswith("-"):
                raise ValueError("a quote after other text")
            j, text = i + 1, []
            while True:
                if j >= n:
                    raise ValueError("no closing quotation")
                d = line[j]
                if d in "\r\n`" or d in POWERSHELL_QUOTES or (d == "$" and c == '"'):
                    raise ValueError("a character this reading leaves open")
                if d == c:
                    if line[j + 1:j + 2] != c:
                        break
                    j += 1  # a doubled quote is one
                text.append(d)
                j += 1
            following = line[j + 1:j + 2]
            if starts_the_word and following and following not in " \t" and (
                    following not in POWERSHELL_OPERATOR_CHARS):
                raise ValueError("a quoted text that starts a word but does not end it")
            reader.add("".join(text), plain=False)
            i = j + 1
        else:
            if c in "*?[" or (c in "@]" and not reader.in_word):
                raise ValueError("a wildcard, a splat or a bracket starting a word")
            if c == "." and reader.plain and POWERSHELL_PARAMETER.fullmatch("".join(reader.word)):
                raise ValueError("a parameter PowerShell splits")
            reader.add(c)
            i += 1
    reader.end_word()
    if any(not operator and POWERSHELL_STOP_PARSING.match(text) for text, operator in reader.tokens):
        raise ValueError("the stop-parsing token")
    return reader.tokens


def _redirection(text: str, target: str, family: str) -> bool:
    if family == "posix":
        return bool(REDIRECTION.fullmatch(text))
    return bool(POWERSHELL_REDIRECTION.fullmatch(text)) or (
        bool(POWERSHELL_MERGE.fullmatch(text)) and target == "1")


def _one_command(tokens: list[tuple[str, bool]], family: str) -> list[str] | None:
    """The words of one simple command, its redirections and their targets left out; None if an
    operator is anything else (a second command, a pipeline, a list, a subshell)."""
    words, i = [], 0
    while i < len(tokens):
        text, operator = tokens[i]
        if operator:
            if i + 1 >= len(tokens) or tokens[i + 1][1] or not _redirection(text, tokens[i + 1][0],
                                                                             family):
                return None
            i += 2  # a redirection and its target, which is no word of the command
            continue
        words.append(text)
        i += 1
    return words


def _program(word: str, family: str) -> str:
    """A program word's last part: after a slash, and in PowerShell a backslash too (a POSIX shell
    took its backslashes as escapes already)."""
    return re.split(r"[/\\]" if family == "powershell" else "/", word)[-1]


def _kit_program(program: str) -> bool:
    return program in KIT_PROGRAMS or program.lower() in KIT_PROGRAMS_WINDOWS


def _python_runs_the_kit(words: list[str]) -> bool:
    """Python's own options up to the first word that is not one: `-m aelix_analytics[.…]`, or a `-c`
    whose code imports it, also after bundled one-letter options (`-Im`, `-I -c`)."""
    i = 0
    while i < len(words):
        word = words[i]
        if not word.startswith("-") or word == "-":
            return False  # a script, the standard input, or the end of Python's options
        if word.startswith("--"):
            i += 2 if word == "--check-hash-based-pycs" else 1
            continue
        letters = word[1:]
        while letters and letters[0] in PYTHON_FLAGS:
            letters = letters[1:]
        if letters[:1] in ("m", "c"):
            value = letters[1:] or (words[i + 1] if i + 1 < len(words) else "")
            if letters[0] == "m":
                return value == "aelix_analytics" or value.startswith("aelix_analytics.")
            return bool(KIT_IMPORT.search(value))
        if letters[:1] in ("X", "W"):
            i += 1 if letters[1:] else 2
        else:
            i += 1
    return False


def _options_end(words: list[str], i: int, flags, values, counted=None) -> int | None:
    """Where the program's word is after a command's options at `i` (`--` ends them): those in
    `flags` take no value; those in `values` take the next word, or the rest of their own (`--name=v`,
    `-xv`). None for any other option: it may take the next word as its value."""
    while i < len(words) and words[i].startswith("-"):
        option = words[i]
        if option == "--":
            return i + 1
        if option in flags or (counted is not None and counted.fullmatch(option)):
            i += 1
        elif option in values:
            i += 2
        elif ("=" in option and option.split("=", 1)[0] in values) or (
                not option.startswith("--") and option[:2] in values):
            i += 1
        else:
            return None
    return i


def _runs_the_kit(words: list[str], family: str) -> bool:
    """Whether one simple command's words run a kit entry point: its program, after what may come
    before it in that shell (see above)."""
    i = 0
    if family == "posix":
        while i < len(words) and ASSIGNMENT.match(words[i]):
            i += 1
        while i < len(words) and words[i] in COMMAND_PREFIXES:
            if words[i] == "env":
                i = _options_end(words, i + 1, ENV_FLAGS, ENV_VALUE_OPTIONS)
                if i is None:
                    return False
                while i < len(words) and ASSIGNMENT.match(words[i]):
                    i += 1
            else:
                i += 1
    if i >= len(words):
        return False
    if family == "powershell" and not POWERSHELL_COMMAND_START.match(words[i]):
        return False  # an expression there, or a command name this reading leaves alone
    program = _program(words[i], family)
    if program == "uv" and words[i + 1:i + 2] == ["run"]:
        i = _options_end(words, i + 2, UV_RUN_FLAGS, UV_RUN_VALUE_OPTIONS, UV_RUN_COUNTED)
        if i is None or i >= len(words):
            return False
        program = _program(words[i], family)
    if _kit_program(program):
        return True
    return bool(PYTHON_PROGRAM.fullmatch(program)) and _python_runs_the_kit(words[i + 1:])


def is_single_kit_command(command: str, shell: str) -> bool:
    """Whether a person's `!` line is, as a whole, one simple command that calls a kit entry point,
    read as `shell` — aelix's name for the shell it runs the line in (`bash`, `zsh`, `pwsh`, …,
    the_shell) — reads it (see above)."""
    family = ("posix" if shell in POSIX_SHELLS else "powershell" if shell in POWERSHELL_SHELLS
              else None)
    line = command.strip()
    if family is None or not line or UNREAD_CHARACTERS.search(line):
        return False
    try:
        posix = _posix_words(line)
        if family == "posix":
            tokens = posix
        else:
            tokens = _powershell_words(line)
            if tokens[:1] == [("&", True)]:  # its call operator, at the start only
                tokens = tokens[1:]
                posix = posix[1:] if posix[:1] == [("&", True)] else posix
            elif line[0] in "'\"":
                return False  # a quoted text first is a value in PowerShell, not a program
            if tokens[:1] and tokens[0][1]:
                return False  # a redirection first is the command's name there (`2>&1 x` runs `2>&1`)
            if _one_command(posix, "posix") is None:
                return False
        words = _one_command(tokens, family)
    except ValueError:  # what this reading leaves open: no judgment, the line runs as aelix runs it
        return False
    if words is None:
        return False
    # A comment (`#` starting a word) ends the command: the shell runs the words before it (`uv run
    # --with #x analytics` runs `uv run --with`). A quoted `#…` word ends the words judged too, which
    # only leaves more alone; operators after it counted above.
    words = words[:next((k for k, word in enumerate(words) if word.startswith("#")), len(words))]
    if cmd_may_read_the_arguments() and any(
            CMD_METACHARACTERS.intersection(text) for text, operator in tokens if not operator):
        return False  # cmd may read the words again (a batch file): any of them may start a command
    return _runs_the_kit(words, family)


class PersonShell:
    """aelix's own local shell for one person's command, with AELIX_ANALYTICS_ACTOR=human in that
    command's environment alone; everything else as aelix runs a `!` command (cli/repl.handle_user_bash:
    create_local_bash_operations(), the environment get_shell_env() makes when none is given)."""

    def __init__(self, local, shell_env) -> None:
        self._local = local
        self._shell_env = shell_env

    async def exec(self, command, cwd, *, on_data, signal=None, timeout=None, env=None):
        env = dict(env) if env is not None else self._shell_env()
        env["AELIX_ANALYTICS_ACTOR"] = "human"
        return await self._local.exec(command, cwd, on_data=on_data, signal=signal, timeout=timeout,
                                      env=env)


def on_user_bash(event, ctx):
    """A person's `!`/`!!` line: aelix's local shell with the person as the author when the whole line
    is one kit command in the shell aelix runs it in, else nothing (aelix keeps the last result a
    handler returns, of any kind, so a result for any other line would set another extension's block or
    shell aside for all of it). Never raises: a person's command must run whatever this judgment
    meets."""
    try:
        if not is_single_kit_command(str(getattr(event, "command", "") or ""), the_shell()):
            return None
        from aelix_coding_agent.tools.bash import create_local_bash_operations
        from aelix_coding_agent.util.shell_env import get_shell_env

        return UserBashResult(operations=PersonShell(create_local_bash_operations(), get_shell_env))
    except Exception:  # noqa: BLE001
        return None


# --- hooks ---

async def on_before_agent_start(event, ctx):
    session = current_session(ctx)
    hook_origin = core.Origin("system", "hook", "declared", session)
    STATE["session"] = session
    # The shells the agent's commands run in carry this session from here (design 4.2).
    put_the_session_in_the_environment(STATE["session"])
    try:
        project, _ = resolve_project()
    except Exception:  # noqa: BLE001 - no active project must never block a prompt
        return None
    with contextlib.suppress(Exception):
        await asyncio.to_thread(lambda: core.sync_context(project, origin=hook_origin))
    try:
        block = await asyncio.to_thread(core.context_block, project)
    except Exception:  # noqa: BLE001 - a broken journal must not stop the prompt
        return None
    if session is not None:
        with contextlib.suppress(Exception):
            result = await asyncio.to_thread(lambda: core.publish_asset_suggestion(
                project, origin=hook_origin, surface="aelix_context"))
            block += core.asset_context_notice(result.get("published"))
    return BeforeAgentStartResult(system_prompt=f"{event.system_prompt}\n\n{block}")


def on_tool_start(event, ctx):
    STATE["starts"][event.tool_call_id] = (time.monotonic(), event.tool_name, dict(event.args or {}))


def summarize_call(tool: str, args: dict, project) -> tuple[str, list]:
    flags = []
    if tool == "bash":
        command = str(args.get("command") or "")
        if PYTHON_IN_SHELL.search(command) and not KIT_IN_SHELL.search(command) \
                and "bin/analytics" not in command:
            flags.append("python_via_bash")
        return command[:300], flags
    if tool in ("write", "edit"):
        path = str(args.get("path") or "")
        # Where the write went below the root, from the path's text (else its real path): a write
        # through a link that leads out of data/ is still a write under data/. No walk and no
        # directory listing: this runs on aelix's event loop after every tool call.
        below = project.below(path) if path else None
        layout = project.layout
        if below and (below[0] in ("data", "runs", layout.workspace_dir, layout.state_dir)
                      or (not below[1] and below[0] in ("journal.jsonl", layout.marker))):
            flags.append("wrote_managed_path")
        return path[:300], flags
    if tool == "read":
        return str(args.get("path") or "")[:300], flags
    if tool.startswith("analytics_"):
        keep = {k: args[k] for k in ("title", "script", "name", "kind", "rerun_of", "source", "path")
                if args.get(k)}
        for k, n in (("sql", 200), ("text", 120)):
            if args.get(k):
                keep[k] = str(args[k])[:n]
        return json.dumps(keep, ensure_ascii=False)[:300], flags
    return ",".join(sorted(map(str, args)))[:200], flags


def on_tool_end(event, ctx):
    start = STATE["starts"].pop(event.tool_call_id, None)
    try:
        project, _ = resolve_project()
    except Exception:  # noqa: BLE001
        return None
    args = start[2] if start else {}
    try:
        summary, flags = summarize_call(event.tool_name, args, project)
    except Exception:  # noqa: BLE001
        summary, flags = "", []
    # A side write: skipped where this kit does not write (Issue #13, design 4.10).
    if core.admit(project, "log the tool call", side=True) is None:
        return None
    with contextlib.suppress(OSError):
        core.append_jsonl(project.telemetry, {
            "ts": core.iso(), "tool": event.tool_name, "error": bool(event.is_error),
            "ms": round((time.monotonic() - start[0]) * 1000) if start else None,
            "summary": summary, "flags": flags, "session": STATE["session"]})
    return None


def on_agent_end(event, ctx):
    with contextlib.suppress(Exception):
        project = resolve_project()[0]
        session = current_session(ctx)
        if session is not None:
            hook_origin = core.Origin("system", "hook", "declared", session)
            core.preassess_asset_suggestions(project, origin=hook_origin)
    with contextlib.suppress(Exception):
        schedule_workspace_render(resolve_project()[0], "hook")
    return None


# --- human command ---

HELP = """/analytics — Aelix Analytics project
  /analytics                    status of the active project
  /analytics where              project, kit and uv in use
  /analytics init DIR [title]   create a project + environment and make it active
  /analytics demo DIR           new project with synthetic data and example runs
  /analytics use DIR            make DIR the active project for this aelix process
  /analytics render | open      rebuild the report | open it in the browser
  /analytics build --preview    inspect a sharing preview without writing a publication
  /analytics record KIND TEXT   journal entry as a human (finding, decision, question, ...);
                                KIND workflow_feedback: something this analysis workflow lacked
                                or made awkward
  /analytics context            the context block injected into prompts
  /analytics assets list | show ID | review [ID]
  /analytics assets mode on_demand|always_on [--scope project|session]
  /analytics assets update EVENT --expected STATE --to STATE --reason TEXT [--details-json JSON]
  /analytics assets register EVENT --name NAME
A project of the older record format (lab.json) is read only here: a person copies it into the
current format in a terminal (analytics migrate --to DIR, preview with --dry-run), then
/analytics use DIR."""


async def analytics_command(args, ctx=None):
    text = (args or "").strip()
    sub, _, rest = text.partition(" ")
    rest = rest.strip()
    try:
        if sub in ("", "status"):
            project, via = resolve_project()
            options = human_options(rest, {"part", "query", "id", "cursor", "kind", "state", "since", "until",
                                           "direction", "as_of_seq", "limit"})
            authored = command_origin(ctx)
            session = authored.session
            STATE["session"] = session
            put_the_session_in_the_environment(session)
            st = await asyncio.to_thread(lambda: core.status(project, asset_session=session, **options))
            publication = None
            if session is not None and not options.get("part"):
                result = await asyncio.to_thread(lambda: core.publish_asset_suggestion(
                    project, origin=authored, surface="aelix_command_status"))
                publication = result.get("published")
                if publication is not None:
                    st = await asyncio.to_thread(lambda: core.status(project, asset_session=session, **options))
            shown = core.retrieval_text(st) if options.get("part") else core.status_text(st)
            if publication is not None:
                notice = publication["notification"]
                shown = f"Asset suggestion (new, {notice['id']}): {core._one_line(publication['candidate']['details']['title'], 140)}\n{shown}"
            return f"(project via {via})\n" + shown
        if sub == "assets":
            return await human_assets(rest, ctx)
        if sub in ("help", "-h", "--help"):
            return HELP
        if sub == "where":
            lines = []
            try:
                project, via = resolve_project()
                lines.append(f"project: {project.root} (via {via})")
            except core.AnalyticsError as exc:
                lines.append(f"project: none — {exc.message}")
            lines.append(f"extension: {Path(__file__).resolve()} "
                         f"({'installed package' if __package__ else 'checkout file'})")
            lines.append(f"kit: {KIT} (aelix_analytics {core.KIT_VERSION}; {KIT_ROUTE})")
            try:
                lines.append(f"uv: {resolve_uv()}")
            except core.AnalyticsError as exc:
                lines.append(f"uv: {exc.message}")
            except Exception as exc:  # noqa: BLE001 - this command is how a broken setup is diagnosed
                lines.append(f"uv: cannot be looked up with this kit ({type(exc).__name__}: {exc})")
            return "\n".join(lines)
        if sub in ("init", "demo"):
            if not rest:
                return f"usage: /analytics {sub} DIR" + (" [title]" if sub == "init" else "")
            raw, _, title = rest.partition(" ")
            target = Path(raw).expanduser()
            target = target if target.is_absolute() else Path(os.getcwd()) / target
            project = await asyncio.to_thread(
                lambda: core.init_project(target, title=title.strip() or None,
                                          origin=origin("human", "command")))
            STATE["project"] = str(project.root)
            _notify(ctx, f"analytics: syncing the environment of {project.root} …")
            synced, detail = await sync_environment(project)
            if not synced:
                return f"created {project.root}, but uv sync failed:\n{detail}"
            if sub == "demo":
                _notify(ctx, "analytics: generating synthetic data and example runs …")
                ok, value = await run_kit(project, ["demo"], timeout=900, actor="human",
                                          means="command")
                if not ok:
                    return render_error("/analytics demo", value)
            else:
                await run_kit(project, ["render"], timeout=240, actor="human", means="command")
            return (f"created {project.root} (active). Report: {project.workspace_dir / 'index.html'}"
                    " — /analytics open to view it.")
        if sub == "use":
            if not rest:
                return "usage: /analytics use DIR"
            root = core.find_project(Path(rest).expanduser())
            if root is None:
                return f"{rest} is not inside an analytics project (no analytics.json or lab.json)"
            await asyncio.to_thread(core.readable, root, "use the project")
            STATE["project"] = str(root)
            # Off the event loop: on Windows a record being read elsewhere makes the replace wait
            # up to about two seconds before it fails (_platform.WindowsAtomicFiles).
            await asyncio.to_thread(core.trust_project, root)
            return f"active project: {root} (trusted: starting aelix inside it activates it)"
        if sub in ("render", "open"):
            project, _ = resolve_project()
            index = project.workspace_dir / "index.html"
            if sub == "render" or not index.is_file():
                # Refused here, before uv makes or syncs the project's environment.
                await asyncio.to_thread(core.admit, project, "render")
                ok, value = await run_kit(project, ["render"], timeout=240, actor="human",
                                          means="command")
                if not ok:
                    return render_error("/analytics render", value)
            if sub == "open":
                try:
                    project.platform.processes.open_document(index, wait=False)
                except OSError as exc:
                    return f"could not open {index} ({exc}); open it in a browser yourself"
                if core.admit(project, "render", side=True) is None:
                    # A page this kit reads and does not build any more (a lab.json project's).
                    return (f"opened {index} (not updated: this kit reads {project.root} and does "
                            "not write to it; /analytics status says why)")
                return f"opened {index}"
            return f"report rebuilt: {index}"
        if sub == "build":
            project, _ = resolve_project()
            await asyncio.to_thread(core.readable, project, "preview publication")
            try:
                args = shlex.split(rest)
            except ValueError as exc:
                raise core.AnalyticsError("scope_invalid", "quote the preview arguments") from exc
            ok, value = await run_kit(project, ["build", *args], timeout=1200, actor="human", means="command")
            return json.dumps(value, ensure_ascii=False, indent=2) if ok else render_error("/analytics build", value)
        if sub == "record":
            # Kinds as the common writer has them (writer.RECORD_KINDS): `/analytics record gap TEXT`,
            # what `/lab gap TEXT` was, is bad_kind with its new name, workflow_feedback (design 2.8).
            project, _ = resolve_project()
            kind, _, body = rest.partition(" ")
            details, refs = None, []
            argv = []
            # Metadata options follow one explicitly quoted text argument. Preserve existing
            # prose verbatim, including literal flag mentions, quotes and unmatched apostrophes.
            if body.lstrip().startswith(("'", '"')):
                with contextlib.suppress(ValueError):
                    argv = shlex.split(rest)
            if len(argv) > 2 and argv[2] in ("--details-json", "--refs-json"):
                if len(argv) < 2 or (len(argv) - 2) % 2:
                    raise core.AnalyticsError("bad_record_input", "quote the text and JSON option values")
                kind, body = argv[:2]
                for flag, value in zip(argv[2::2], argv[3::2], strict=True):
                    try:
                        payload = json.loads(value)
                    except ValueError as exc:
                        raise core.AnalyticsError("bad_record_input", "record options hold JSON values") from exc
                    if flag == "--details-json":
                        details = payload
                    elif flag == "--refs-json" and isinstance(payload, list):
                        refs += payload
                    else:
                        raise core.AnalyticsError("bad_record_input", "use --details-json or --refs-json array")
            written = await asyncio.to_thread(
                lambda: core.record(project, kind, body, refs=refs, details=details,
                                    origin=origin("human", "command")))
            schedule_workspace_render(project, "command")
            return recorded_line(written, kind)
        if sub == "context":
            project, _ = resolve_project()
            return await asyncio.to_thread(core.context_block, project)
        return HELP
    except core.AnalyticsError as exc:
        return render_error(f"/analytics {sub}".strip(), exc.to_json())
    except Exception as exc:  # noqa: BLE001 - a command must not crash the TUI
        return f"/analytics {sub}: internal error {type(exc).__name__}: {exc}"


def _notify(ctx, message: str) -> None:
    with contextlib.suppress(Exception):
        ctx.ui.notify(message, "info")


def human_options(text, allowed):
    """Flags are parsed as values, never as shell code; the shared core validates semantics."""
    try:
        words = shlex.split(text)
    except ValueError as exc:
        raise core.AnalyticsError("bad_query", "quote option values") from exc
    if len(words) % 2:
        raise core.AnalyticsError("bad_query", "options are --name VALUE pairs")
    result = {}
    for flag, value in zip(words[::2], words[1::2], strict=True):
        name = flag[2:].replace("-", "_") if flag.startswith("--") else ""
        if name not in allowed:
            raise core.AnalyticsError("bad_query", f"unsupported option {flag!r}")
        if name in ("limit", "as_of_seq"):
            try:
                value = int(value)
            except ValueError as exc:
                raise core.AnalyticsError("bad_query", f"{flag} is an integer") from exc
        result[name] = value
    return result


async def human_assets(text, ctx):
    try:
        words = shlex.split(text)
    except ValueError as exc:
        raise core.AnalyticsError("bad_asset", "quote asset option values") from exc
    if not words:
        return "usage: /analytics assets list|show|review|mode|update|register"
    action, args = words[0], words[1:]
    project, _ = resolve_project()
    authored = command_origin(ctx)
    STATE["session"] = authored.session
    put_the_session_in_the_environment(authored.session)
    if action == "mode" and args:
        mode, tail = args[0], args[1:]
        options = human_options(shlex.join(tail), {"scope"})
        result = await asyncio.to_thread(lambda: core.set_asset_mode(project, mode, origin=authored,
                                                                    scope=options.get("scope", "project")))
    elif action in ("list", "show"):
        if (action == "list" and args) or (action == "show" and len(args) != 1):
            raise core.AnalyticsError("bad_asset", "list has no argument; show needs one asset_id")
        result = await asyncio.to_thread(core.assets, project, args[0] if args else None)
    elif action == "review" and len(args) <= 1:
        result = await asyncio.to_thread(lambda: core.review_assets(project, args[0] if args else None,
                                                                   origin=authored, surface="aelix_command"))
    elif action in ("update", "register") and args:
        event_id, tail = args[0], args[1:]
        allowed = {"name"} if action == "register" else {"expected", "to", "reason", "details_json"}
        options = human_options(shlex.join(tail), allowed)
        if action == "register" and set(options) == {"name"}:
            result = await asyncio.to_thread(lambda: core.register_asset(project, event_id, name=options["name"],
                                                                         origin=authored, skill_discovery=_discover_project_skill))
        elif action == "update" and {"expected", "to", "reason"} <= set(options):
            try:
                details = json.loads(options["details_json"]) if "details_json" in options else None
            except ValueError as exc:
                raise core.AnalyticsError("bad_asset", "details-json is a JSON object") from exc
            result = await asyncio.to_thread(lambda: core.update_asset(project, event_id, expected=options["expected"],
                new=options["to"], text=options["reason"], details=details, origin=authored))
        else:
            raise core.AnalyticsError("bad_asset", "register needs --name; update needs --expected/--to/--reason")
    else:
        raise core.AnalyticsError("bad_asset", "unknown asset action or arguments")
    return json.dumps(result, ensure_ascii=False, indent=2)


def _discover_project_skill(directory):
    from aelix_agent_core.harness.skills import load_skills

    loaded = load_skills([directory])
    expected = directory / "SKILL.md"
    found = [skill for skill in loaded.skills if Path(skill.file_path) == expected]
    return {"result": "pass" if found else "failed", "scope": "project",
            "loader": "aelix 0.1.0b2 load_skills", "files": [str(expected)],
            "diagnostics": [str(value) for value in loaded.diagnostics]}


# The channel on aelix's shared event bus through which a copy of this extension that is already set
# up in one aelix runtime answers a later copy (see setup).
INSTANCE_CHANNEL = "aelix-analytics:instance"


def _where_this_copy_is() -> str:
    return f"{Path(__file__).resolve()} ({KIT_ROUTE})"


def _answer_as_the_active_copy(probe) -> None:
    if isinstance(probe, dict) and probe.get("active") is None:
        probe["active"] = _where_this_copy_is()


def setup(aelix):
    # One copy per aelix runtime. A checkout's file (a symlink in aelix's extensions directory, or
    # `aelix -e`) next to the installed package's entry point is two copies to aelix, which loads
    # both: both would register, and both copies' hooks would fire — every tool call telemetered
    # twice, the context block added to the prompt twice. The copy set up first answers on the
    # runtime's event bus, shared by every extension of that runtime, and a later copy stays out.
    # It answers only once it has registered everything, so a copy whose setup failed does not keep
    # the next one out. A copy older than this check does not answer, so it is not detected (README);
    # a host without an event bus gets no check.
    events = getattr(aelix, "events", None)
    if events is not None:
        probe = {"active": None}
        events.emit(INSTANCE_CHANNEL, probe)
        if probe["active"] is not None:
            print(f"aelix-analytics: {_where_this_copy_is()} is not set up: this aelix already "
                  f"loaded {probe['active']}. Keep one of them — remove the checkout's symlink or "
                  "-e, or uninstall the aelix-analytics package.", file=sys.stderr)
            return
    # aelix sets extensions up again for every session it builds (/new, /resume, /fork, /reload).
    # A file load gets a new module each time; the installed module is imported once per process
    # and keeps its globals, so what belongs to one session starts over here — otherwise a /analytics record
    # before a new session's first prompt would record the previous session's file.
    STATE.update({"api": aelix, "project": None, "session": None, "starts": {},
                  "render_tasks": {}})
    put_the_author_in_the_environment()
    aelix.register_flag("analytics-project", type="str",
                        description="Analytics project directory (holds analytics.json, or an "
                                    "older lab.json)")
    for tool in TOOLS:
        aelix.register_tool(tool)
    aelix.register_command("analytics", handler=analytics_command,
                           description="Analytics: status | where | init | demo | use | render | "
                                       "open | record | context | build --preview")
    aelix.on("before_agent_start", on_before_agent_start)
    aelix.on("tool_execution_start", on_tool_start)
    aelix.on("tool_execution_end", on_tool_end)
    aelix.on("agent_end", on_agent_end)
    aelix.on("user_bash", on_user_bash)
    if events is not None:
        events.on(INSTANCE_CHANNEL, _answer_as_the_active_copy)
