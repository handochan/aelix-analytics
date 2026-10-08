"""The kit's command line — shared by the aelix extension (JSON) and people (bin/analytics, or `analytics` installed).

    python -m aelix_analytics [--project DIR] <command> ...

The last line of stdout is one JSON object: {"ok": true, ...} or {"ok": false, "error": {...}}.
Exit codes: 0 success, 1 handled error, 2 usage error, 3 run recorded but not ok, 130 interrupted.
Run commands with PYTHONPATH set to the runtime root (the directory holding aelix_analytics: a
checkout's src/, an installation's aelix_analytics/kit/) inside the project's uv environment, as
bin/analytics, the installed `analytics` and the extension do. The installed distribution's own package is
not a runtime root, and refuses to run here (not_runtime_root; see _not_the_runtime_root).

AELIX_ANALYTICS_CANCEL_FILE names the file through which the aelix extension asks a command it
started to stop, where the platform cannot ask with a signal (see _platform.RunControl.watch_for_cancel):
an internal protocol between the two, not a setting.

An environment variable of the kit's old names (core.RENAMED_ENV: LAB_PROJECT, LAB_ACTOR, LAB_SESSION,
LAB_STYLE, AELIX_LAB_TRUST_FILE) that is set stops every command with env_renamed before it does
anything: there are no aliases (ADR-0005 decision 7).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from importlib import resources
from pathlib import Path

from . import _platform, core

# Package data of this package, wherever it is (a checkout's src/, or an installation's runtime root).
TEMPLATE_DIR = resources.files(__package__) / "template"
# The old names of what this command line and a run in it read (the runner, the SDK, the chart style).
OLD_ENV = ("LAB_PROJECT", "LAB_ACTOR", "LAB_SESSION", "LAB_STYLE", "AELIX_LAB_TRUST_FILE")


# The line ends json.dumps(ensure_ascii=False) leaves raw inside a string: str.splitlines() ends a
# line at each, so a reader that splits the output with it (the extension did) cut the result line in
# two. Escaped, they are the same JSON value. json.dumps escapes the other line ends splitlines()
# knows (\n, \r, \x0b, \x0c, \x1c-\x1e) itself. Issue #32 item 1, CONTRACTS D.
LINE_END_ESCAPES = {0x85: "\\u0085", 0x2028: "\\u2028", 0x2029: "\\u2029"}


def _result_line(obj: dict) -> str:
    """The result line without its line end: one JSON object holding no line end any reader knows.
    Every other character is as json.dumps(ensure_ascii=False) writes it."""
    return json.dumps(obj, ensure_ascii=False, default=str).translate(LINE_END_ESCAPES)


def _emit(obj: dict) -> None:
    text = _result_line(obj)
    # Once a script may have run, through the runner: what a thread it left running still writes
    # through stdout goes before the result or after it, never inside (runner.write_result).
    runner = sys.modules.get(f"{__package__}.runner")
    if runner is not None:
        runner.write_result(text)
        return
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


# The commands that write to a project, and the one each is refused as: they are judged before
# their first side effect (core.admit, Issue #13). The others read (status, context, query) and
# skip their side writes where this kit does not write; `init` makes a new directory (core.init_project).
WRITES = ("run", "import", "record", "render", "demo")


def _origin() -> core.Origin:
    """Who a command line's writes are by (design 4.2): AELIX_ANALYTICS_ACTOR as given (declared),
    else `human`, which the kit fills in (inferred); the session AELIX_ANALYTICS_SESSION."""
    actor = os.environ.get("AELIX_ANALYTICS_ACTOR") or None
    return core.Origin(actor or "human", "cli", "declared" if actor else "inferred",
                       os.environ.get("AELIX_ANALYTICS_SESSION") or None)


def _project(args) -> core.Project:
    start = args.project or os.environ.get("AELIX_ANALYTICS_PROJECT") or os.getcwd()
    root = core.find_project(start)
    if root is None:
        raise core.AnalyticsError("no_project", f"no analytics project at or above {start}",
                                  hint="create one with: analytics init DIR")
    return core.Project(root)


def _not_the_runtime_root() -> dict | None:
    """The error that keeps this command from running here, or None where it may run.

    The kit runs from a runtime root: the one directory an entry point puts on PYTHONPATH (a
    checkout's src/, an installation's aelix_analytics/kit/), inside the project's environment. The
    installed distribution's own package — the one with that runtime root as kit/ beside its modules
    — is importable from wherever it is installed, so `python -m aelix_analytics` from that
    environment would run an analysis script outside the project's environment, with what is
    installed there (ADR-0005 confirmation 1, owner's decision of 2026-10-06): refused, in any
    environment. So is a copy run under another name (`python -m aelix_analytics.kit.aelix_analytics`):
    a script's `import aelix_analytics` would not be the package running it, and would not see its
    run (ADR-0004). A call that names the runtime root on PYTHONPATH runs, from any environment, as
    the entry points, a checkout and the floor tests do. Nothing is written before this."""
    here = Path(__file__).resolve().parent
    if __package__ != "aelix_analytics":
        why = f"this copy of the kit runs as {__package__}, so a script could not see its run"
    elif (here / "kit" / "aelix_analytics" / "__main__.py").is_file():
        why = f"{here} is the installed distribution's own package"
    else:
        return None
    return core.AnalyticsError(
        "not_runtime_root", f"the kit runs only from a runtime root: {why}",
        hint="use the analytics command (analytics --help), which runs the kit in the project's "
             "environment. To run the module itself, run it in the project's environment with the "
             "runtime root on PYTHONPATH (uv run --project <project> python -m aelix_analytics ...); "
             "in this environment, python -c \"from aelix_analytics._install import kit_dir; "
             "print(kit_dir())\" prints the root").to_json()


def main(argv=None) -> int:
    # Before anything can print: the extension reads this process's stdout through a pipe, and a
    # pipe follows the locale rather than the console, so the records' non-ASCII text — and a
    # usage error that quotes it — needs this to survive.
    _platform.configure_stdio()
    refused = _not_the_runtime_root()
    if refused is not None:
        _emit(refused)
        return 1
    try:
        core.refuse_renamed_env(OLD_ENV)
    except core.AnalyticsError as exc:
        _emit(exc.to_json())
        return 1
    # Taken out of the environment so a script's own subprocesses do not watch the same file.
    request = os.environ.pop("AELIX_ANALYTICS_CANCEL_FILE", None)
    stop_watching = None
    try:
        if request:
            stop_watching = core.PLATFORM.runs.watch_for_cancel(Path(request))
        return _command(argv)
    except KeyboardInterrupt:
        _emit({"ok": False, "error": {"code": "interrupted", "message": "interrupted"}})
        return 130
    finally:
        # Bound to this call: nothing is delivered after main() returns.
        if stop_watching is not None:
            stop_watching()
        # A run's stdout stream stood in for this process's own until the result line was out, so a
        # leftover thread's print() shared the buffer the line is written through
        # (runner.write_result); the process's own are back before its shutdown flushes them —
        # also when a second Ctrl+C is handled as the give-back starts, which would otherwise
        # leave the run's stream as sys.stdout for a leftover thread to hold at shutdown.
        runner = sys.modules.get(f"{__package__}.runner")
        if runner is not None:
            try:
                runner.give_back_stdout()
            except KeyboardInterrupt:
                runner.give_back_stdout()
                raise


def _command(argv) -> int:
    parser = argparse.ArgumentParser(prog="python -m aelix_analytics",
                                     description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--project", help="project directory (default: AELIX_ANALYTICS_PROJECT, else the "
                                          "nearest analytics.json or lab.json at or above cwd)")
    parser.add_argument("--text", action="store_true", help="human-readable output where supported")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a project directory (no environment sync)")
    p.add_argument("path")
    p.add_argument("--title")
    p.add_argument("--goal")

    sub.add_parser("status", help="project overview")
    sub.add_parser("context", help="the context block injected into agent prompts")

    p = sub.add_parser("run", help="run a script and record provenance")
    p.add_argument("script", nargs="?")
    p.add_argument("--title")
    p.add_argument("--input", action="append", default=[], dest="inputs")
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--rerun-of")

    p = sub.add_parser("import", help="import a data file as data/raw/<name>.parquet")
    p.add_argument("source")
    p.add_argument("--name")
    p.add_argument("--description")

    p = sub.add_parser("query", help="read-only DuckDB SQL over dataset views")
    p.add_argument("sql")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("record", help="append a journal entry")
    p.add_argument("kind", choices=core.NOTE_KINDS)
    p.add_argument("text")
    p.add_argument("--ref", action="append", default=[], dest="refs")
    p.add_argument("--resolves")

    p = sub.add_parser("render", help="rebuild the HTML report")
    p.add_argument("--if-changed", action="store_true")

    p = sub.add_parser("demo", help="fill a new project with synthetic data and example runs")

    args = parser.parse_args(argv)
    origin = _origin()
    try:
        if args.command == "init":
            project = core.init_project(args.path, title=args.title, goal=args.goal,
                                        template_dir=TEMPLATE_DIR, origin=origin)
            _emit({"ok": True, "project": str(project.root), "title": project.title()})
            return 0
        project = _project(args)
        # Before anything is written — a lock file, a context version, a cache — and before the
        # analysis libraries are imported: a write where this kit does not write is refused, a read
        # where it cannot tell which format to read (two markers) too.
        if args.command in WRITES:
            core.admit(project, args.command)
        else:
            core.readable(project, args.command)
        if args.command == "status":
            st = core.status(project)
            if args.text:
                print(core.status_text(st))
                return 0
            _emit({"ok": True, "status": st, "text": core.status_text(st)})
        elif args.command == "context":
            block = core.context_block(project)
            if args.text:
                print(block)
                return 0
            _emit({"ok": True, "context": block})
        elif args.command == "run":
            from . import runner

            if bool(args.script) == bool(args.rerun_of):
                raise core.AnalyticsError("run_args", "give either a script or --rerun-of RUN_ID")
            result = runner.execute(project, script=args.script, title=args.title,
                                    declared_inputs=args.inputs, timeout=args.timeout,
                                    rerun_of=args.rerun_of, origin=origin, result_follows=True)
            _emit({"ok": True, **result})
            return 0 if result["run"]["status"] == "ok" else 3
        elif args.command == "import":
            from . import data, workspace

            core.sync_context(project, origin=core.system_origin(origin))
            result = data.import_file(project, args.source, name=args.name,
                                      description=args.description, origin=origin)
            result["workspace"] = _safe_render(workspace, project)
            _emit({"ok": True, **result})
        elif args.command == "query":
            from . import data

            _emit({"ok": True, **data.query(project, args.sql, args.limit)})
        elif args.command == "record":
            from . import workspace

            core.sync_context(project, origin=core.system_origin(origin))
            event = core.add_note(project, args.kind, args.text, refs=args.refs,
                                  resolves=args.resolves, origin=origin)
            _emit({"ok": True, "event": event, "workspace": _safe_render(workspace, project)})
        elif args.command == "render":
            from . import workspace

            core.sync_context(project, origin=core.system_origin(origin))
            _emit({"ok": True, "workspace": workspace.build(project, if_changed=args.if_changed)})
        elif args.command == "demo":
            from . import demo

            _emit({"ok": True, **demo.populate(project, origin=origin)})
        return 0
    except core.AnalyticsError as exc:
        _emit(exc.to_json())
        return 1


def _safe_render(workspace, project) -> dict:
    try:
        return workspace.build(project)
    except Exception as exc:  # noqa: BLE001 - the mutation already happened; the Workspace is derived
        return {"error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    sys.exit(main())
