"""labkit CLI — shared by the aelix extension (JSON) and people (bin/lab).

    python -m labkit [--project DIR] <command> ...

The last line of stdout is one JSON object: {"ok": true, ...} or {"ok": false, "error": {...}}.
Exit codes: 0 success, 1 handled error, 2 usage error, 3 run recorded but not ok, 130 interrupted.
Run commands with PYTHONPATH=<pack>/kit inside the project's uv environment (see bin/lab).

LAB_CANCEL_FILE names the file through which the aelix extension asks a command it started to
stop, where the platform cannot ask with a signal (see _platform.RunControl.watch_for_cancel).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import _platform, core

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"


def _emit(obj: dict) -> None:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    # Once a script may have run, through the runner: what a thread it left running still writes
    # through stdout goes before the result or after it, never inside (runner.write_result).
    runner = sys.modules.get(f"{__package__}.runner")
    if runner is not None:
        runner.write_result(text)
        return
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _project(args) -> core.Project:
    start = args.project or os.environ.get("LAB_PROJECT") or os.getcwd()
    root = core.find_project(start)
    if root is None:
        raise core.LabError("no_project", f"no lab project at or above {start}",
                            hint="create one with: lab init DIR")
    return core.Project(root)


def main(argv=None) -> int:
    # Before anything can print: the extension reads this process's stdout through a pipe, and a
    # pipe follows the locale rather than the console, so the records' non-ASCII text — and a
    # usage error that quotes it — needs this to survive.
    _platform.configure_stdio()
    # Taken out of the environment so a script's own subprocesses do not watch the same file.
    request = os.environ.pop("LAB_CANCEL_FILE", None)
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
        # A run's stdout stream stood in for this process's own to carry the result line
        # (runner.write_result); the process's own are back before its shutdown flushes them.
        runner = sys.modules.get(f"{__package__}.runner")
        if runner is not None:
            runner.give_back_stdout()


def _command(argv) -> int:
    parser = argparse.ArgumentParser(prog="labkit", description=(__doc__ or "labkit").splitlines()[0])
    parser.add_argument("--project", help="project directory (default: nearest lab.json above cwd)")
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

    p = sub.add_parser("note", help="append a journal entry")
    p.add_argument("kind", choices=core.NOTE_KINDS)
    p.add_argument("text")
    p.add_argument("--ref", action="append", default=[], dest="refs")
    p.add_argument("--resolves")

    p = sub.add_parser("report", help="rebuild the HTML report")
    p.add_argument("--if-changed", action="store_true")

    p = sub.add_parser("demo", help="fill a new project with synthetic data and example runs")

    args = parser.parse_args(argv)
    actor = os.environ.get("LAB_ACTOR") or "human"
    session = os.environ.get("LAB_SESSION") or None
    try:
        if args.command == "init":
            project = core.init_project(args.path, title=args.title, goal=args.goal,
                                        template_dir=TEMPLATE_DIR, actor=actor, session=session)
            _emit({"ok": True, "project": str(project.root), "title": project.title()})
            return 0
        project = _project(args)
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
                raise core.LabError("run_args", "give either a script or --rerun-of RUN_ID")
            result = runner.execute(project, script=args.script, title=args.title,
                                    declared_inputs=args.inputs, timeout=args.timeout,
                                    rerun_of=args.rerun_of, actor=actor, session=session,
                                    result_follows=True)
            _emit({"ok": True, **result})
            return 0 if result["run"]["status"] == "ok" else 3
        elif args.command == "import":
            from . import data, report

            core.sync_context(project, session=session)
            result = data.import_file(project, args.source, name=args.name,
                                      description=args.description, actor=actor, session=session)
            result["report"] = _safe_report(report, project)
            _emit({"ok": True, **result})
        elif args.command == "query":
            from . import data

            _emit({"ok": True, **data.query(project, args.sql, args.limit)})
        elif args.command == "note":
            from . import report

            core.sync_context(project, session=session)
            event = core.add_note(project, args.kind, args.text, refs=args.refs,
                                  resolves=args.resolves, actor=actor, session=session)
            _emit({"ok": True, "event": event, "report": _safe_report(report, project)})
        elif args.command == "report":
            from . import report

            core.sync_context(project, session=session)
            _emit({"ok": True, "report": report.build(project, if_changed=args.if_changed)})
        elif args.command == "demo":
            from . import demo

            _emit({"ok": True, **demo.populate(project, actor=actor, session=session)})
        return 0
    except core.LabError as exc:
        _emit(exc.to_json())
        return 1


def _safe_report(report, project) -> dict:
    try:
        return report.build(project)
    except Exception as exc:  # noqa: BLE001 - the mutation already happened; report is derived
        return {"error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    sys.exit(main())
