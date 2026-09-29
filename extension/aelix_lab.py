"""Aelix Analytics: analysis extension built only from the basic aelix extension contract.
(setup, register_flag, register_tool, register_command, on).

Installed (the aelix-analytics wheel in aelix's environment): aelix finds it through the
`aelix.extensions` entry point, as the module aelix_analytics.extension, with its manifest.
From a checkout, for one session:  aelix -e <pack>/extension/aelix_lab.py [--lab-project DIR]
From a checkout, globally:         ln -s <pack>/extension/aelix_lab.py ~/.aelix/agent/extensions/aelix_lab.py
Use one of these at a time: a second copy in the same aelix does not set itself up (see setup).

Inside aelix this file imports only the standard library, the aelix API and the kit's stdlib-only
labkit/core.py with its _platform sibling, from the kit next to it (so symlink this file in a
checkout rather than copying it). Analysis code never runs in the aelix process: lab_run,
lab_import, lab_query and report builds are `uv run --project <project> python -m labkit ...`
subprocesses in the project's own environment.
Neither side is a sandbox, and aelix does not ask for approval before extension tools run.
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
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from aelix_agent_core.harness.hooks import BeforeAgentStartResult
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import ImageContent, TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult


def _kit() -> tuple[Path, str]:
    """(the kit directory, how it was found). The kit is where `labkit` is: children get exactly
    this directory on PYTHONPATH, and core.py and _platform.py are loaded from it."""
    if __name__ == "aelix_analytics.extension":
        # Imported by name from the installed distribution (its `aelix.extensions` entry point, or
        # `aelix -e aelix_analytics.extension`). It runs the kit installed with it, found through
        # importlib.resources, as the `lab` command does: an adapter and a kit of different versions
        # would not fit each other.
        from aelix_analytics._install import kit_dir

        return kit_dir(), "installed"
    configured = os.environ.get("AELIX_LAB_KIT")
    if configured:
        return Path(configured).expanduser().resolve(), "AELIX_LAB_KIT"
    here = Path(__file__).resolve().parent
    if (here / "kit" / "labkit" / "core.py").is_file():
        # The installed module loaded as a file (`aelix -e <site-packages>/aelix_analytics/extension.py`).
        return (here / "kit").resolve(), "installed file"
    # A checkout's extension/aelix_lab.py, loaded as a file (`aelix -e`, or a symlink in aelix's
    # extensions directory): its kit is next to its directory.
    return (here.parent / "kit").resolve(), "checkout"


KIT, KIT_ROUTE = _kit()


def _load_core():
    """Load the pack's stdlib-only project model into the aelix process.

    core.py imports one sibling — _platform, stdlib only for the same reason — so it is loaded as a
    submodule of a synthetic package whose search path is this pack's kit. The package name carries
    a digest of the resolved kit path, so two packs, or one pack before and after AELIX_LAB_KIT
    changes, never share a cached submodule. The sys.modules entries exist only while the modules
    execute and are removed afterwards: the loaded objects stay alive through the reference this
    returns, a labkit installed in the host environment is never shadowed, and a failed load leaves
    nothing behind. Reloading a changed kit needs a new aelix process, as it did before.
    """
    path = KIT / "labkit" / "core.py"
    if not path.is_file():
        remedy = {"AELIX_LAB_KIT": "Point AELIX_LAB_KIT at a directory that holds labkit/, or unset it.",
                  "checkout": "Symlink extension/aelix_lab.py instead of copying it, or set "
                              "AELIX_LAB_KIT=<pack>/kit.",
                  "installed file": "Reinstall the aelix-analytics package.",
                  "installed": "Reinstall the aelix-analytics package."}[KIT_ROUTE]
        raise RuntimeError(f"aelix-analytics: kit not found at {path}. {remedy}")
    package = f"aelix_lab_kit_{hashlib.sha256(str(KIT).encode()).hexdigest()[:12]}"
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

GRACE_SECONDS = 10
MAX_TEXT = 14_000
MAX_IMAGE_BYTES = 1_500_000
MAX_IMAGES = 3
# A run may read hundreds of files; listing them all would push stdout out of the reply.
MAX_LIST = 10
MAX_CHART_LIST = 12
ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
             "TZ", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "SSL_CERT_FILE",
             "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
             "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy")
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|CREDENTIAL|_KEY$", re.IGNORECASE)
PYTHON_IN_SHELL = re.compile(r"(^|[\s;&|(`])(python3?(\.\d+)?|uv\s+run|pip3?|ipython|jupyter)\b")

# Module-level state of one aelix runtime. setup() starts it over: the file loader makes a new module
# for every session aelix builds, but the installed module is imported once per process.
STATE = {"api": None, "project": None, "session": None, "starts": {}, "report_tasks": {}}

NO_PROJECT = ("No analysis lab project is active (checked /lab use, --lab-project, AELIX_LAB_PROJECT, "
              "and lab.json in the working directory and its parents).")


def _error(code, message, hint=None, details=None):
    return core.LabError(code, message, hint=hint, details=details).to_json()


# --- resolution ---

def resolve_project():
    """(Project, how it was found). Explicit choices that are not projects are errors. A project
    found only because aelix runs inside it must be trusted (created or chosen by the user)."""
    try:
        return _resolve_project()
    except core.LabError:
        raise
    except Exception as exc:  # e.g. "~typo/dir" makes expanduser raise RuntimeError
        raise core.LabError("project_invalid", f"cannot resolve the project path: {exc}",
                            hint="check --lab-project / AELIX_LAB_PROJECT") from exc


def _resolve_project():
    explicit = STATE["project"]
    if explicit:
        project = core.Project(explicit)
        if not project.marker.is_file():
            raise core.LabError("project_missing", f"active project {explicit} has no lab.json",
                                hint="/lab use DIR to pick another project")
        return project, "/lab use"
    flag = None
    with contextlib.suppress(Exception):
        flag = STATE["api"].get_flag("lab-project") if STATE["api"] else None
    for value, via in ((flag, "--lab-project"), (os.environ.get("AELIX_LAB_PROJECT"),
                                                  "AELIX_LAB_PROJECT")):
        if isinstance(value, str) and value.strip():
            project = core.Project(value.strip())
            if not project.marker.is_file():
                raise core.LabError("project_invalid", f"{via} {project.root} is not a lab project "
                                    "(no lab.json)", hint="create it with /lab init DIR")
            return project, via
    root = core.find_project(os.getcwd())
    if root is not None:
        if not core.is_trusted(root):
            raise core.LabError(
                "project_untrusted",
                f"{root} has a lab.json but is not a project this user created or chose, so it "
                "was not activated (its CONTEXT.md would enter the prompt and lab_run would "
                "execute its code)",
                hint=f"a human can run /lab use {root} to trust it")
        return core.Project(root), "working directory"
    raise core.LabError("no_project", NO_PROJECT,
                        hint="ask the user where the project should live, then call lab_init; a "
                             "human can run /lab init DIR or /lab use DIR")


def resolve_uv(processes=None) -> str:
    """AELIX_LAB_UV (a path, or a name) or `uv`, found where the platform looks for programs
    (ProcessControl.find_program: on Windows PATH alone, never the working directory)."""
    processes = processes or core.PLATFORM.processes
    found = processes.find_program(os.environ.get("AELIX_LAB_UV") or "uv")
    if not found:
        raise core.LabError("uv_missing", "uv was not found",
                            hint="install uv (https://docs.astral.sh/uv/) or set AELIX_LAB_UV=/path/to/uv")
    return found


def child_env(processes, actor: str) -> dict:
    # What the platform needs to start a process at all goes in first: the allowed list is about
    # what a run may see, not about what the operating system requires to launch one.
    env = processes.base_env()
    env.update({k: os.environ[k] for k in ENV_ALLOW if k in os.environ})
    env.update({k: v for k, v in os.environ.items()
                if k.startswith("UV_") and not SECRET_NAME.search(k)})
    env.update({"PYTHONPATH": str(KIT), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "MPLBACKEND": "Agg", "LAB_ACTOR": actor})
    if STATE["session"]:
        env["LAB_SESSION"] = str(STATE["session"])
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
            self.directory = Path(tempfile.mkdtemp(prefix="aelix-lab-"))
        except OSError as exc:
            raise core.LabError("spawn_failed",
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
        raise core.LabError("spawn_failed", f"could not start {argv[0]}: {exc}") from exc
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


async def run_kit(project, args, *, timeout: float, actor: str = "agent"):
    """Run `python -m labkit` in the project's environment. Returns (ok, result object)."""
    processes = project.platform.processes
    try:
        uv = resolve_uv(processes)
        argv = [uv, "run", "--project", str(project.root), "--quiet", "python", "-m", "labkit",
                "--project", str(project.root), *args]
        # Only where the platform asks through it: POSIX asks with a signal and needs no file.
        request = _CancelRequest() if processes.asks_by_request else None
        try:
            env = child_env(processes, actor)
            if request is not None:
                env["LAB_CANCEL_FILE"] = str(request.path)
            timed_out, code, stdout, stderr = await _spawn(argv, project.root, env, timeout,
                                                           processes, request)
        finally:
            if request is not None and not request.handed_over:
                request.remove()
    except core.LabError as exc:
        return False, exc.to_json()
    if timed_out:
        return False, _error("timeout", f"labkit {args[0]} did not finish within {timeout:.0f} s "
                             "and was interrupted", hint="lab_status shows whether a run was recorded")
    for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            with contextlib.suppress(ValueError):
                obj = json.loads(line)
                if isinstance(obj, dict):
                    return bool(obj.get("ok")), obj
    tail = stderr.decode("utf-8", errors="replace")[-1500:]
    hint = "read details.stderr; lab_status shows whether a run was recorded"
    if re.search(r"(?i)\b(resolv|sync|lock|no solution|interpreter|python)\b", tail):
        hint += f"; if it is an environment error run `uv sync --project {project.root}`"
    return False, _error("kit_failed", f"labkit {args[0]} exited {code} without a result",
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
    return False, last.decode("utf-8", errors="replace")[-1500:]


def schedule_report(project) -> None:
    """Start a detached `report --if-changed` build unless one for this project is still running.

    Deliberately not an asyncio task: a cancelled `uv run` whose Python child still holds the pipes
    keeps Process.wait() pending, which would stall aelix's loop shutdown. The kit serializes builds
    with a file lock and skips when nothing changed, so a missed trigger is caught by the next one
    (agent_end fires after every prompt).
    """
    key = str(project.root)
    running = STATE["report_tasks"].get(key)
    if running is not None and running.poll() is None:
        return
    try:
        processes = project.platform.processes
        argv = [resolve_uv(processes), "run", "--project", key, "--quiet", "python", "-m", "labkit",
                "--project", key, "report", "--if-changed"]
        STATE["report_tasks"][key] = subprocess.Popen(
            argv, cwd=key, env=child_env(processes, "system"), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **processes.spawn_kwargs())
    except Exception:  # noqa: BLE001 - a derived build; it must not fail what was just recorded
        return


# --- rendering ---

def _clip(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[:limit - 60] + "\n… (truncated; see the report page)"


def _one(text, width=160) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= width else s[:width - 1] + "…"


def _more(items, shown: int, label: str) -> list[str]:
    rest = len(items) - shown
    return [f"  … and {rest:,} more {label} — the run page lists them all"] if rest > 0 else []


def render_error(label: str, value: dict) -> str:
    err = value.get("error") or {}
    lines = [f"{label}: error [{err.get('code', 'unknown')}]", f"message: {err.get('message', '')}"]
    if err.get("hint"):
        lines.append(f"hint: {err['hint']}")
    if err.get("details"):
        lines.append("details: " + json.dumps(err["details"], ensure_ascii=False)[:2000])
    return "\n".join(lines)


def render_run(project, value: dict, attached: tuple[int, int] | None = None) -> str:
    run = value["run"]
    status = run.get("status")
    head = f"lab_run: {status} — run {run['id']} ({run.get('duration_s', '?')} s)"
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
    lines += _more(inputs, MAX_LIST, "input file(s)")
    outputs = [o for o in run.get("outputs") or []]
    if outputs:
        lines.append(f"outputs ({len(outputs)}):")
    for o in outputs[:MAX_LIST]:
        cols = ", ".join(c[0] for c in (o.get("schema") or [])[:12])
        rows = f" {o['rows']:,} rows [{cols}]" if isinstance(o.get("rows"), int) else ""
        lines.append(f"  {o['path']} {o.get('change')}{rows}")
    lines += _more(outputs, MAX_LIST, "output file(s)")
    charts = run.get("charts") or []
    lines.append(f"charts ({len(charts)}):" if charts else "charts: none")
    for c in charts[:MAX_CHART_LIST]:
        data = f"data {c['data_rows']:,} rows" if c.get("data") else "no plotted data recorded"
        files = ", ".join((c.get("files") or {}).values())
        lines.append(f"  {(script.get('path') or '?')}::{c['id']} \"{_one(c.get('title'), 80)}\" "
                     f"{c.get('kind')} via {c.get('via')} · {data} · {files}")
    lines += _more(charts, MAX_CHART_LIST, "chart(s)")
    if attached and attached[1]:
        shown, total = attached
        lines.append(f"images: {shown} of {total} chart PNG(s) attached below"
                     + ("" if shown >= total else "; open the rest with the read tool at the paths "
                                                  "above"))
    repro = run.get("reproduction")
    if repro:
        parts = [f"{k}: {v}" for k, v in {**repro.get("charts", {}), **repro.get("outputs", {})}.items()]
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
        lines += _more(run["warnings"], MAX_LIST, "warning(s)")
    stdout = (value.get("stdout_tail") or "").strip()
    if stdout:
        lines += ["stdout (tail):", stdout[-3000:]]
    stderr = (value.get("stderr_tail") or "").strip()
    if stderr and (status != "ok" or len(stderr) < 1500):
        lines += ["stderr (tail):", stderr[-3000:]]
    lines.append(f"journal: {value.get('event')} · report: {project.root / value.get('page', '')}")
    if value.get("report_error"):
        lines.append(f"report build failed: {value['report_error']}")
    return _clip("\n".join(lines))


def chart_images(project, run: dict, ctx) -> tuple[list, int]:
    """Chart PNGs to attach, and how many charts had one to attach at all."""
    charts = [c for c in run.get("charts") or [] if (c.get("files") or {}).get("png")]
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


def render_table(columns, rows, width=48) -> str:
    names = [c["name"] for c in columns]

    def text(v):
        s = "null" if v is None else str(v)
        return s if len(s) <= width else s[:width - 1] + "…"

    cells = [[text(v) for v in row] for row in rows]
    widths = [min(width, max([len(n)] + [len(r[i]) for r in cells])) for i, n in enumerate(names)]
    out = [" | ".join(n.ljust(w) for n, w in zip(names, widths, strict=False)),
           " | ".join(c["type"].ljust(w)[:max(w, 1)] for c, w in zip(columns, widths, strict=False)),
           "-+-".join("-" * w for w in widths)]
    out += [" | ".join(v.ljust(w) for v, w in zip(r, widths, strict=False)) for r in cells]
    return "\n".join(out)


def _int(args, name, default, low, high) -> int:
    value = args.get(name)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(low, min(high, value))


# --- tools ---

def bypass_note(project) -> str:
    """Runs made with bash instead of lab_run leave no code, inputs or charts behind."""
    flagged = 0
    with contextlib.suppress(OSError):
        for row in core.read_jsonl(project.telemetry):
            flagged += "python_via_bash" in (row.get("flags") or [])
    if not flagged:
        return ""
    return (f"\nnote: python was run through bash {flagged} time(s) in this project; anything it "
            "wrote has no recorded code, inputs or charts — use lab_run for analysis steps")


async def tool_status(args, ctx):
    project, via = resolve_project()
    await asyncio.to_thread(core.sync_context, project, "system", STATE["session"])
    st = await asyncio.to_thread(core.status, project)
    text = f"lab_status: ok (project found via {via})\n" + core.status_text(st)
    return text + await asyncio.to_thread(bypass_note, project), st


async def tool_query(args, ctx):
    project, _ = resolve_project()
    sql = str(args.get("sql") or "").strip()
    if not sql:
        raise core.LabError("query_empty", "sql is empty")
    limit = _int(args, "limit", 50, 1, 500)
    ok, value = await run_kit(project, ["query", sql, "--limit", str(limit)], timeout=180)
    if not ok:
        return render_error("lab_query", value), value, True
    head = (f"lab_query: ok — {len(value['rows'])} row(s){' (truncated at limit)' if value['truncated'] else ''}"
            f" in {value['elapsed_ms']} ms; views used: {', '.join(value.get('tables') or []) or '-'}")
    return _clip(head + "\n" + render_table(value["columns"], value["rows"])), value


async def tool_import(args, ctx):
    project, _ = resolve_project()
    source = str(args.get("source") or "").strip()
    if not source:
        raise core.LabError("import_source_missing", "source is empty")
    src = core.expand_home(Path(source))  # the rule `lab import` and the kit's paths follow
    src = src if src.is_absolute() else Path(os.getcwd()) / src
    cmd = ["import", str(src)]
    if args.get("name"):
        cmd += ["--name", str(args["name"])]
    if args.get("description"):
        cmd += ["--description", str(args["description"])]
    ok, value = await run_kit(project, cmd, timeout=900)
    if not ok:
        return render_error("lab_import", value), value, True
    ds, prof = value["dataset"], value["profile"]
    lines = [f"lab_import: ok — dataset {ds['name']} ({ds['rows']:,} rows × {len(ds['columns'])} "
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
    lines.append(f"Query it as view `{ds['name']}` (lab_query) or lab.read(\"{ds['name']}\") in scripts.")
    return _clip("\n".join(lines)), value


async def tool_run(args, ctx):
    project, _ = resolve_project()
    title = str(args.get("title") or "").strip()
    if not title:
        raise core.LabError("run_needs_title", "title is required: say what this run is for")
    script, code, name = args.get("script"), args.get("code"), args.get("name")
    rerun_of = str(args.get("rerun_of") or "").strip()
    given = sum(bool(x) for x in (script, code, rerun_of))
    if given != 1:
        raise core.LabError("run_args", "pass exactly one of script, code (with name) or rerun_of")
    note = ""
    if rerun_of:
        if not core.RUN_ID_RE.match(rerun_of):
            raise core.LabError("rerun_id", f"{rerun_of!r} is not a run id",
                                hint="lab_status lists run ids such as 20260916-143012-monthly-sales")
        cmd = ["run", "--rerun-of", rerun_of]
    elif code:
        name = str(name or "").strip()
        if not core.SCRIPT_NAME_RE.match(name):
            raise core.LabError("run_name", "code needs a name like 02_monthly_sales",
                                hint="lowercase letters, digits and _; the code is saved to scripts/<name>.py")
        target = project.scripts / f"{name}.py"
        core.reject_unportable_name(name, kind="script", exists=target.exists())
        note = f" (overwrote scripts/{name}.py)" if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(code).rstrip() + "\n", encoding="utf-8")
        cmd = ["run", f"scripts/{name}.py"]
    else:
        cmd = ["run", str(script)]
    timeout = _int(args, "timeout_s", 600, 10, 3600)
    cmd += ["--title", title, "--timeout", str(timeout)]
    for item in args.get("inputs") or []:
        cmd += ["--input", str(item)]
    ok, value = await run_kit(project, cmd, timeout=timeout + 180)
    if not ok:
        return render_error("lab_run", value), value, True
    images, with_png = ((await asyncio.to_thread(chart_images, project, value["run"], ctx))
                        if args.get("attach_images", True) else ([], 0))
    text = render_run(project, value, (len(images), with_png))
    if note:
        text = text.replace("\n", f"{note}\n", 1)
    return [TextContent(text=text), *images], value, value["run"].get("status") != "ok"


async def tool_note(args, ctx):
    project, _ = resolve_project()
    kind = str(args.get("kind") or "")
    refs = [str(r) for r in args.get("refs") or []]
    await asyncio.to_thread(core.sync_context, project, "system", STATE["session"])
    event = await asyncio.to_thread(
        lambda: core.add_note(project, kind, str(args.get("text") or ""), refs=refs,
                              resolves=args.get("resolves") or None, actor="agent",
                              session=STATE["session"]))
    schedule_report(project)
    extra = f"; resolves {event['refs']['resolves']}" if event["refs"].get("resolves") else ""
    refs_text = json.dumps({k: v for k, v in event["refs"].items() if k != "resolves"},
                           ensure_ascii=False)
    return f"lab_note: ok — {event['id']} {kind} recorded{extra}; refs {refs_text}", event


async def tool_init(args, ctx):
    raw = str(args.get("path") or "").strip()
    if not raw:
        raise core.LabError("init_path", "path is required")
    target = Path(raw).expanduser()
    target = target if target.is_absolute() else Path(os.getcwd()) / target
    project = await asyncio.to_thread(
        lambda: core.init_project(target, title=args.get("title"), goal=args.get("goal"),
                                  actor="agent",
                                  session=STATE["session"]))
    STATE["project"] = str(project.root)
    synced, detail = await sync_environment(project)
    lines = [f"lab_init: ok — project {project.title()} at {project.root} (now the active project)"]
    if synced:
        await run_kit(project, ["report"], timeout=240)
        lines.append(f"environment: synced from {detail} (DuckDB, Polars, PyArrow, matplotlib, plotly)")
    else:
        lines.append("environment: uv sync FAILED — runs will fail until it succeeds. uv said:\n" + detail)
    lines += [f"report: {project.report / 'index.html'}",
              "next: fill CONTEXT.md (goal, questions, data sources), lab_import the data, then write "
              "scripts/01_<topic>.py and lab_run it."]
    return "\n".join(lines), {"project": str(project.root), "synced": synced}


def _tool(name, description, properties, required, handler, snippet, guidelines=()):
    async def execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        try:
            out = await handler(args or {}, ctx)
        except core.LabError as exc:
            value = exc.to_json()
            return ToolResult(content=[TextContent(text=render_error(name, value))], details=value,
                              is_error=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - report instead of failing the turn
            value = _error("internal_error", f"{type(exc).__name__}: {exc}",
                           hint="this is a bug in the lab extension; lab_status shows what was recorded")
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
        "lab_status",
        "Overview of the active analysis lab project: datasets (Parquet) with query view names, recent "
        "runs, the latest version of every chart with staleness (inputs changed since the chart was "
        "made), open questions/todos, key findings/decisions/assumptions and the recent journal. "
        "Read-only. Call it first in a session and whenever you are unsure what already exists.",
        {}, [], tool_status,
        "overview of the analysis project: datasets, runs, charts (with staleness), journal",
    ),
    _tool(
        "lab_query",
        "Run ONE read-only DuckDB statement over the project's datasets, for exploration only. Every "
        "Parquet dataset is a view named after its file (data/raw/sales.parquet -> sales; a derived "
        "dataset whose name collides gets _derived). SELECT/WITH, DESCRIBE <view>, SUMMARIZE <view> "
        "and EXPLAIN are allowed; anything that writes is refused. Returns at most `limit` rows. "
        "Queries are logged but are not runs: anything that feeds a chart, a derived dataset or a "
        "recorded finding must be done with lab_run.",
        {"sql": {**STR, "description": "One DuckDB SQL statement."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                   "description": "Maximum rows to return (default 50)."}},
        ["sql"], tool_query,
        "read-only DuckDB SQL over dataset views for exploration (not recorded as a run)",
    ),
    _tool(
        "lab_import",
        "Import a local data file into the project as an immutable Parquet dataset "
        "data/raw/<name>.parquet. CSV/TSV/JSON/NDJSON are converted with DuckDB; Parquet is copied. "
        "Records the source path and sha256 and the schema, and logs an import event. An existing "
        "name is refused (imported data never changes; use a new name such as sales_v2).",
        {"source": {**STR, "description": "Path of the file to import (absolute, or relative to the "
                                          "aelix working directory)."},
         "name": {**STR, "description": "Dataset name: lowercase letters, digits, _ (default: file stem)."},
         "description": {**STR, "description": "What the data is: origin, period, grain, caveats."}},
        ["source"], tool_import,
        "import a CSV/JSON/Parquet file as an immutable Parquet dataset under data/raw/",
    ),
    _tool(
        "lab_run",
        "Execute a Python analysis script in the project's own uv environment (DuckDB, Polars, PyArrow, "
        "matplotlib, plotly) with the project root as working directory, and record its provenance: "
        "exact code snapshot + sha256, input files + sha256, files it wrote, charts with their plotted "
        "data, stdout/stderr, timing and package versions. The HTML report is rebuilt afterwards; "
        "every chart links to that code and data. Give exactly one of: `script` (a .py under scripts/, "
        "preferred: write it with the write tool, then run it; edit and re-run the same script to "
        "version its charts), `code` + `name` (saved to scripts/<name>.py, overwriting), or `rerun_of` "
        "(re-execute a recorded run's code snapshot and compare charts and outputs).\n"
        "Script API: import labkit as lab; lab.read('sales') -> Polars DataFrame; lab.scan('sales') -> "
        "LazyFrame; lab.sql(\"SELECT ... FROM sales\") -> DuckDB relation over all dataset views (.pl() "
        "for Polars); lab.save(df, 'monthly_sales') -> data/derived/monthly_sales.parquet; "
        "lab.chart(fig, 'monthly-revenue', title=..., caption=..., data=plotted_df) for a matplotlib "
        "Figure/Axes or plotly Figure (id: short ASCII, stable across versions); lab.note('finding', "
        "text) links a journal entry to this run. Plain pl.read_parquet('data/...'), DuckDB SQL over "
        "'data/...parquet' literals, plt.show() and fig.show() are captured too, but only lab.chart "
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
            "Analysis data is Parquet: data/raw is imported and never modified; derived tables go to "
            "data/derived via lab.save. Process data with DuckDB and Polars, not pandas.",
            "Make every chart and derived dataset with lab_run, never by running python through bash, "
            "so its code and input data are recorded and linked from the report.",
            "In scripts prefer lab.read/lab.sql for data and lab.chart(fig, id, title=, caption=, "
            "data=plotted_df) for charts; one script per question, re-run it to version its charts.",
            "Record findings, decisions, assumptions, questions and todos with lab_note as soon as they "
            "are established, citing run ids; keep CONTEXT.md current by editing it.",
            "If the lab tools block you or force a workaround, record it with lab_note kind=gap "
            "(this tooling is being evaluated).",
        ),
    ),
    _tool(
        "lab_note",
        "Append an entry to the project journal, the time-ordered history shown in the report. kind: "
        "finding (an established result), decision, assumption, question, todo, note, gap (a missing "
        "capability or friction in this analysis tooling), or resolve (closes the question/todo named "
        "in `resolves` without other content). Any kind may also resolve an earlier entry, e.g. a "
        "finding that answers question e7. Cite evidence in refs: run ids, chart keys "
        "(scripts/x.py::chart-id), dataset names or event ids.",
        {"kind": {"type": "string", "enum": list(core.NOTE_KINDS)},
         "text": {**STR, "description": "The entry, self-contained (max 4000 characters)."},
         "refs": {"type": "array", "items": STR, "description": "Run ids, chart keys, dataset names, event ids."},
         "resolves": {**STR, "description": "Event id this entry answers or closes, e.g. e7."}},
        ["kind", "text"], tool_note,
        "record a finding/decision/assumption/question/todo/gap in the project journal",
    ),
    _tool(
        "lab_init",
        "Create a new analysis lab project directory (lab.json, CONTEXT.md, journal.jsonl, data/, "
        "scripts/, runs/, report/) with its own uv environment (DuckDB, Polars, PyArrow, matplotlib, "
        "plotly) and make it the active project. Only when the user asked to start a new analysis; if "
        "they did not say where, ask them first. The directory must not exist or must be empty.",
        {"path": {**STR, "description": "Directory to create."},
         "title": {**STR, "description": "Project title."},
         "goal": {**STR, "description": "What the analysis should answer and for whom."}},
        ["path"], tool_init,
        "create a new analysis project with its own environment (only when the user asks)",
    ),
]


# --- hooks ---

async def on_before_agent_start(event, ctx):
    with contextlib.suppress(Exception):
        session = ctx.session_manager.get_session()
        STATE["session"] = getattr(session, "session_file", None) or STATE["session"]
    try:
        project, _ = resolve_project()
    except Exception:  # noqa: BLE001 - no active project must never block a prompt
        return None
    with contextlib.suppress(Exception):
        await asyncio.to_thread(core.sync_context, project, "system", STATE["session"])
    try:
        block = await asyncio.to_thread(core.context_block, project)
    except Exception:  # noqa: BLE001 - a broken journal must not stop the prompt
        return None
    return BeforeAgentStartResult(system_prompt=f"{event.system_prompt}\n\n{block}")


def on_tool_start(event, ctx):
    STATE["starts"][event.tool_call_id] = (time.monotonic(), event.tool_name, dict(event.args or {}))


def summarize_call(tool: str, args: dict, project) -> tuple[str, list]:
    flags = []
    if tool == "bash":
        command = str(args.get("command") or "")
        if PYTHON_IN_SHELL.search(command) and "labkit" not in command and "bin/lab" not in command:
            flags.append("python_via_bash")
        return command[:300], flags
    if tool in ("write", "edit"):
        path = str(args.get("path") or "")
        if path and project.inside(path):
            rel = project.rel(path)
            if rel.split("/")[0] in ("data", "runs", "report", ".lab") or rel in ("journal.jsonl",
                                                                                "lab.json"):
                flags.append("wrote_managed_path")
        return path[:300], flags
    if tool == "read":
        return str(args.get("path") or "")[:300], flags
    if tool.startswith("lab_"):
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
    with contextlib.suppress(OSError):
        core.append_jsonl(project.telemetry, {
            "ts": core.iso(), "tool": event.tool_name, "error": bool(event.is_error),
            "ms": round((time.monotonic() - start[0]) * 1000) if start else None,
            "summary": summary, "flags": flags, "session": STATE["session"]})
    return None


def on_agent_end(event, ctx):
    with contextlib.suppress(Exception):
        schedule_report(resolve_project()[0])
    return None


# --- human command ---

HELP = """/lab — Aelix Analytics project
  /lab                      status of the active project
  /lab where                project, kit and uv in use
  /lab init DIR [title]     create a project + environment and make it active
  /lab demo DIR             new project with synthetic data and example runs
  /lab use DIR              make DIR the active project for this aelix process
  /lab report | open        rebuild the report | open it in the browser
  /lab note KIND TEXT       journal entry as a human (finding, decision, question, ...)
  /lab gap TEXT             record a gap: something this analysis workflow lacked or made awkward
  /lab context              the context block injected into prompts"""


async def lab_command(args, ctx=None):
    text = (args or "").strip()
    sub, _, rest = text.partition(" ")
    rest = rest.strip()
    try:
        if sub in ("", "status"):
            project, via = resolve_project()
            st = await asyncio.to_thread(core.status, project)
            return f"(project via {via})\n" + core.status_text(st)
        if sub in ("help", "-h", "--help"):
            return HELP
        if sub == "where":
            lines = []
            try:
                project, via = resolve_project()
                lines.append(f"project: {project.root} (via {via})")
            except core.LabError as exc:
                lines.append(f"project: none — {exc.message}")
            lines.append(f"extension: {Path(__file__).resolve()} "
                         f"({'installed package' if __package__ else 'checkout file'})")
            lines.append(f"kit: {KIT} (labkit {core.KIT_VERSION}; {KIT_ROUTE})")
            try:
                lines.append(f"uv: {resolve_uv()}")
            except core.LabError as exc:
                lines.append(f"uv: {exc.message}")
            except Exception as exc:  # noqa: BLE001 - this command is how a broken setup is diagnosed
                lines.append(f"uv: cannot be looked up with this kit ({type(exc).__name__}: {exc})")
            return "\n".join(lines)
        if sub in ("init", "demo"):
            if not rest:
                return f"usage: /lab {sub} DIR" + (" [title]" if sub == "init" else "")
            raw, _, title = rest.partition(" ")
            target = Path(raw).expanduser()
            target = target if target.is_absolute() else Path(os.getcwd()) / target
            project = await asyncio.to_thread(
                lambda: core.init_project(target, title=title.strip() or None,
                                          actor="human",
                                          session=STATE["session"]))
            STATE["project"] = str(project.root)
            _notify(ctx, f"lab: syncing the environment of {project.root} …")
            synced, detail = await sync_environment(project)
            if not synced:
                return f"created {project.root}, but uv sync failed:\n{detail}"
            if sub == "demo":
                _notify(ctx, "lab: generating synthetic data and example runs …")
                ok, value = await run_kit(project, ["demo"], timeout=900, actor="human")
                if not ok:
                    return render_error("/lab demo", value)
            else:
                await run_kit(project, ["report"], timeout=240, actor="human")
            return (f"created {project.root} (active). Report: {project.report / 'index.html'}"
                    " — /lab open to view it.")
        if sub == "use":
            if not rest:
                return "usage: /lab use DIR"
            root = core.find_project(Path(rest).expanduser())
            if root is None:
                return f"{rest} is not inside a lab project (no lab.json)"
            STATE["project"] = str(root)
            # Off the event loop: on Windows a record being read elsewhere makes the replace wait
            # up to about two seconds before it fails (_platform.WindowsAtomicFiles).
            await asyncio.to_thread(core.trust_project, root)
            return f"active project: {root} (trusted: starting aelix inside it activates it)"
        if sub in ("report", "open"):
            project, _ = resolve_project()
            index = project.report / "index.html"
            if sub == "report" or not index.is_file():
                ok, value = await run_kit(project, ["report"], timeout=240, actor="human")
                if not ok:
                    return render_error("/lab report", value)
            if sub == "open":
                try:
                    project.platform.processes.open_document(index, wait=False)
                except OSError as exc:
                    return f"could not open {index} ({exc}); open it in a browser yourself"
                return f"opened {index}"
            return f"report rebuilt: {index}"
        if sub in ("note", "gap"):
            project, _ = resolve_project()
            kind, body = ("gap", rest) if sub == "gap" else (rest.partition(" ")[0],
                                                             rest.partition(" ")[2])
            event = await asyncio.to_thread(
                lambda: core.add_note(project, kind, body, actor="human", session=STATE["session"]))
            schedule_report(project)
            return f"{event['id']} {kind} recorded"
        if sub == "context":
            project, _ = resolve_project()
            return await asyncio.to_thread(core.context_block, project)
        return HELP
    except core.LabError as exc:
        return render_error(f"/lab {sub}".strip(), exc.to_json())
    except Exception as exc:  # noqa: BLE001 - a command must not crash the TUI
        return f"/lab {sub}: internal error {type(exc).__name__}: {exc}"


def _notify(ctx, message: str) -> None:
    with contextlib.suppress(Exception):
        ctx.ui.notify(message, "info")


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
    # and keeps its globals, so what belongs to one session starts over here — otherwise a /lab note
    # before a new session's first prompt would record the previous session's file.
    STATE.update({"api": aelix, "project": None, "session": None, "starts": {},
                  "report_tasks": {}})
    aelix.register_flag("lab-project", type="str",
                        description="Analysis lab project directory (holds lab.json)")
    for tool in TOOLS:
        aelix.register_tool(tool)
    aelix.register_command("lab", handler=lab_command,
                           description="Analysis lab: status | init | demo | use | report | open | "
                                       "note | gap | context")
    aelix.on("before_agent_start", on_before_agent_start)
    aelix.on("tool_execution_start", on_tool_start)
    aelix.on("tool_execution_end", on_tool_end)
    aelix.on("agent_end", on_agent_end)
    if events is not None:
        events.on(INSTANCE_CHANNEL, _answer_as_the_active_copy)
