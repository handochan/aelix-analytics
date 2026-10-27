"""The kit's command line — shared by the aelix extension (JSON) and people (bin/analytics, or `analytics` installed).

    python -m aelix_analytics [--project DIR] <command> ...

The last line of stdout is one JSON object: {"ok": true, ...} or {"ok": false, "error": {...}}.
Exit codes: 0 success, 1 handled error, 2 usage error, 3 run recorded but not ok, 130 interrupted.
Run commands with PYTHONPATH set to the runtime root (the directory holding aelix_analytics: a
checkout's src/, an installation's aelix_analytics/kit/) inside the project's uv environment, as
bin/analytics, the installed `analytics` and the extension do. The installed distribution's own package is
not a runtime root, and refuses to run here (not_runtime_root; see _not_the_runtime_root).

AELIX_ANALYTICS_CANCEL_FILE names the file through which the aelix extension asks a command it
started to stop, where the platform cannot ask with a signal (see _platform.RunControl.watch_for_cancel),
and AELIX_ANALYTICS_MEANS how the extension came to start it (its model tool, its /analytics command,
a hook: the records' `means`, design 4.2; without it `cli`): internal protocols between the two, not
settings.

`migrate --to DEST [--dry-run]` copies the lab.json project found as every command finds its project
into DEST in the current format, the source left as it is (Issue #13, migration.migrate): it runs on
the standard library only, as `init` does, and the launchers start it outside any project
environment (`uv run --no-project`), then prepare DEST's once it is committed (design 6.1). Found
nowhere, or found to be DEST itself, it is a call without a source, which goes on with a migration
that checked its source already or confirms a committed one.

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
# skip their side writes where this kit does not write; `init` makes a new directory
# (core.init_project) and `migrate` a new project from a lab.json one, each by its own rules for a
# destination (design 6.2, OQ9): the source is read by the migration's rules, never admitted.
WRITES = ("run", "import", "record", "checkpoint", "render", "demo")


def _origin() -> core.Origin:
    """Who a command line's writes are by, and how (design 4.2): AELIX_ANALYTICS_ACTOR as given
    (declared), else `human`, which the kit fills in (inferred); the means AELIX_ANALYTICS_MEANS (the
    aelix extension's, one of writer.MEANS), else `cli`; the session AELIX_ANALYTICS_SESSION. A
    means outside the closed list is refused (bad_means) rather than recorded as another."""
    actor = os.environ.get("AELIX_ANALYTICS_ACTOR") or None
    means = os.environ.get("AELIX_ANALYTICS_MEANS") or "cli"
    if means not in core.writer.MEANS:
        raise core.AnalyticsError("bad_means", f"AELIX_ANALYTICS_MEANS is {means!r}, not a means a "
                                  "record holds", hint="it is set by the aelix extension for the "
                                  "commands it starts; unset it to run the command as yourself",
                                  details={"means": means, "allowed": list(core.writer.MEANS)})
    return core.Origin(actor or "human", means, "declared" if actor else "inferred",
                       os.environ.get("AELIX_ANALYTICS_SESSION") or None)


def _project(args) -> core.Project:
    start = args.project or os.environ.get("AELIX_ANALYTICS_PROJECT") or os.getcwd()
    root = core.find_project(start)
    if root is None:
        raise core.AnalyticsError("no_project", f"no analytics project at or above {start}",
                                  hint="create one with: analytics init DIR")
    return core.Project(root)


def _migration_source(args) -> str | Path | None:
    """The project a migration copies, found as every command finds its project (design 6.1), or
    None where none is found. Where the start is that project itself, it is given as it was spelled:
    the migration keeps that spelling among the source's (its mapping's `root.from`)."""
    start = args.project or os.environ.get("AELIX_ANALYTICS_PROJECT") or os.getcwd()
    found = core.find_project(start)
    if found is None:
        return None
    try:
        if os.path.samefile(start, found):
            return start
    except OSError:
        pass
    return found


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

    p = sub.add_parser("status", help="project overview or bounded record/context/relationship read")
    p.add_argument("--part", choices=("records", "relations", "context"))
    for name in ("query", "id", "cursor", "kind", "state", "since", "until"):
        p.add_argument("--" + name)
    p.add_argument("--direction", choices=("both", "incoming", "outgoing", "sources", "uses"),
                   help="relations: one hop (both, incoming, outgoing) or a provenance trail (sources, uses)")
    p.add_argument("--depth", type=int, help="relations trail: how many runs away (default: as far as it goes)")
    p.add_argument("--as-of-seq", type=int)
    p.add_argument("--limit", type=int)
    sub.add_parser("context", help="the context block injected into agent prompts")

    report = sub.add_parser("reports", help="preserve and read immutable Report revisions")
    commands = report.add_subparsers(dest="report_action", required=True)
    p = commands.add_parser("save", help="register files without executing their generator")
    p.add_argument("report_id")
    p.add_argument("--entry", required=True)
    p.add_argument("--files", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--previous", required=True)
    p.add_argument("--request-id")
    p.add_argument("--based-on", help="authored evidence JSON file; never inferred")
    p.add_argument("--metadata", help="optional authored metadata/collection JSON file")
    for action in ("list", "show"):
        p = commands.add_parser(action)
        if action == "show":
            p.add_argument("target", help="exact report_id@revision_id")
            p.add_argument("--file")
            p.add_argument("--file-offset", type=int, default=0)
            p.add_argument("--file-limit", type=int, default=8192)
        p.add_argument("--limit", type=int, default=20)
        p.add_argument("--offset", type=int, default=0)
        p.add_argument("--verify-files", action="store_true", help="explicitly hash the preserved files")
    p = commands.add_parser("update")
    p.add_argument("target")
    p.add_argument("--expected-state", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--reason", required=True)
    p = commands.add_parser("clean")
    p.add_argument("--prepared", required=True)

    asset = sub.add_parser("assets", help="read, review or explicitly register analysis assets")
    commands = asset.add_subparsers(dest="asset_action", required=True)
    commands.add_parser("list", help="read candidate versions and prior legacy records")
    p = commands.add_parser("mode", help="set project-local suggestion mode as a human")
    p.add_argument("mode", choices=("on_demand", "always_on"))
    p = commands.add_parser("show", help="read one candidate and its evidence")
    p.add_argument("asset_id")
    p = commands.add_parser("review", help="explicit model-free review; record requested suggestions")
    p.add_argument("asset_id", nargs="?")
    p = commands.add_parser("update", help="change a candidate state or record demand/identity metadata")
    p.add_argument("event_id")
    p.add_argument("--expected", required=True)
    p.add_argument("--to", required=True, dest="new")
    p.add_argument("--reason", required=True)
    p.add_argument("--details-json", type=json.loads, default={})
    p = commands.add_parser("register", help="explicitly copy a validated version into this project")
    p.add_argument("event_id")
    p.add_argument("--name", required=True)

    p = sub.add_parser("run", help="run a script and record provenance")
    p.add_argument("script", nargs="?")
    p.add_argument("--title")
    p.add_argument("--input", action="append", default=[], dest="inputs")
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--rerun-of")

    p = sub.add_parser("import", help="import a data file as data/imported/<name>.parquet")
    p.add_argument("source")
    p.add_argument("--name")
    p.add_argument("--description")

    p = sub.add_parser("query", help="read-only DuckDB SQL over dataset views")
    p.add_argument("sql")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("checkpoint", help="write an authored or deterministic analysis checkpoint")
    p.add_argument("text", nargs="?")
    p.add_argument("--ref", dest="refs", action="append", default=[])
    p.add_argument("--from-seq", type=int, default=0)
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--as-of-seq", type=int)
    context = p.add_mutually_exclusive_group()
    context.add_argument("--context-version")
    context.add_argument("--context-unknown", action="store_true")

    p = sub.add_parser("record", help="append a journal entry (kind: " + ", ".join(core.RECORD_KINDS)
                       + ")")
    # Not argparse's choices: a kind it does not have is the kit's bad_kind, with the new name of an
    # older one (`gap` is workflow_feedback, design 2.8), as every other entry point answers.
    p.add_argument("kind")
    p.add_argument("text")
    p.add_argument("--ref", action="append", default=[], dest="refs")
    p.add_argument("--resolves")
    p.add_argument("--details-json", type=json.loads)
    p.add_argument("--refs-json", type=json.loads, default=[])

    p = sub.add_parser("render", help="rebuild the Workspace (HTML)")
    p.add_argument("--if-changed", action="store_true")

    p = sub.add_parser("build", allow_abbrev=False, help="build a private publication or inspect its delivery bytes")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="inspect in memory without saving a publication")
    mode.add_argument("--list", action="store_true", dest="list_publications")
    mode.add_argument("--verify", metavar="FOLDER_OR_ZIP")
    mode.add_argument("--zip-of", metavar="PUBLICATION_ID")
    p.add_argument("--zip", metavar="PATH")
    p.add_argument("--profile", default="read")
    p.add_argument("--scope", choices=("project", "reports"))
    p.add_argument("--report", action="append", default=[], metavar="REPORT_ID[@REVISION_ID]")
    p.add_argument("--collection", metavar="REPORT_ID@REVISION_ID")
    p.add_argument("--exclude", action="append", default=[])
    p.add_argument("--analysis-state", default="in_progress")
    p.add_argument("--allow-withdrawn", action="store_true")

    p = sub.add_parser("publish", allow_abbrev=False,
                       help="publish a built publication to a folder, or list this project's publish attempts")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", metavar="PUBLICATION_ID")
    mode.add_argument("--list", action="store_true", dest="list_attempts")
    p.add_argument("--to", metavar="FOLDER")
    p.add_argument("--channel", metavar="NAME")
    p.add_argument("--reassign-channel", action="store_true")

    p = sub.add_parser("demo", help="fill a new project with synthetic data and example runs")

    # No abbreviated options: the launchers read --to and --dry-run from what is passed on, and an
    # abbreviation they do not know (--dry) must not make them prepare a destination a dry run named.
    p = sub.add_parser("migrate", allow_abbrev=False,
                       help="copy this lab.json project into a new folder in the current format "
                            "(the project is left as it is)")
    p.add_argument("--to", required=True, metavar="DEST",
                   help="a new or empty folder outside every project, or the destination of a "
                        "migration to go on with")
    p.add_argument("--dry-run", action="store_true",
                   help="say what the migration would do now, and write nothing")

    args = parser.parse_args(argv)
    try:
        origin = _origin()
        if args.command == "init":
            project = core.init_project(args.path, title=args.title, goal=args.goal,
                                        template_dir=TEMPLATE_DIR, origin=origin)
            _emit({"ok": True, "project": str(project.root), "title": project.title(),
                   "request_id": project.request_id})
            return 0
        if args.command == "migrate":
            from . import migration

            _emit(migration.migrate(_migration_source(args), args.to, origin=origin,
                                    dry_run=args.dry_run))
            return 0
        if args.command == "build":
            return _publication_command(args)
        if args.command == "publish":
            return _publish_command(args)
        project = _project(args)
        # Before anything is written — a lock file, a context version, a cache — and before the
        # analysis libraries are imported: a write where this kit does not write is refused, a read
        # where it cannot tell which format to read (two markers) too.
        if args.command in WRITES:
            core.admit(project, args.command)
        else:
            core.readable(project, args.command)
        if args.command == "reports":
            action = args.report_action
            if action == "save":
                optional = {}
                for name in ("based_on", "metadata"):
                    path = getattr(args, name)
                    if path:
                        try:
                            optional[name] = json.loads(Path(path).read_bytes())
                        except (OSError, ValueError) as exc:
                            raise core.AnalyticsError("report_input_invalid", f"Cannot read --{name.replace('_', '-')}: {exc}") from exc
                result = core.save_report(project, args.report_id, files=args.files, entry=args.entry,
                                          title=args.title, previous_revision=None if args.previous == "none" else args.previous,
                                          request_id=args.request_id, **optional, origin=origin)
            elif action == "list":
                result = core.reports(project, limit=args.limit, offset=args.offset, verify_files=args.verify_files)
            else:
                target = args.prepared if action == "clean" else args.target
                if target.count("@") != 1 or not all(target.split("@")):
                    raise core.AnalyticsError("report_input_invalid", "Give an exact report_id@revision_id")
                report_id, revision_id = target.split("@")
                if action == "show":
                    result = core.reports(project, report_id, revision_id, limit=args.limit, offset=args.offset,
                                          file=args.file, file_offset=args.file_offset, file_limit=args.file_limit, verify_files=args.verify_files)
                elif action == "update":
                    result = core.update_report(project, report_id, revision_id, expected=args.expected_state,
                                                new=args.state, reason=args.reason, origin=origin)
                else:
                    result = core.clean_report_prepared(project, report_id, revision_id, origin=origin)
            _emit({"ok": True, **result})
        elif args.command == "status":
            options = {name: getattr(args, name) for name in
                       ("part", "query", "id", "cursor", "kind", "state", "since", "until", "direction", "depth",
                        "as_of_seq", "limit")
                       if getattr(args, name) is not None}
            # Validate and read the requested surface before an ordinary overview may publish.
            st = core.status(project, asset_session=origin.session, **options)
            publication = None
            if args.text and args.part is None and origin.actor == "human":
                publication = core.publish_asset_suggestion(project, origin=origin).get("published")
                if publication is not None:
                    st = core.status(project, asset_session=origin.session, **options)
            text = core.retrieval_text(st) if args.part else core.status_text(st)
            if args.text:
                if publication is not None:
                    notice = publication["notification"]
                    title = core._one_line(publication["candidate"]["details"]["title"], 140)
                    text = f"Asset suggestion (new, {notice['id']}): {title}\n{text}"
                print(text)
                return 0
            _emit({"ok": True, "status": st, "text": text})
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
        elif args.command == "checkpoint":
            options = {}
            if args.context_version is not None or args.context_unknown:
                options["context_version"] = args.context_version
            _emit({"ok": True, **core.checkpoint(project, args.text, refs=args.refs, as_of_seq=args.as_of_seq,
                                                from_seq=args.from_seq, limit=args.limit, origin=origin, **options)})
        elif args.command == "record":
            from . import workspace

            # What the record refuses before its lock is refused before the context sync too: a
            # refused record changes nothing (core.check_record).
            if not isinstance(args.refs_json, list):
                raise core.AnalyticsError("bad_record_input", "--refs-json is a JSON array")
            refs = [*args.refs, *args.refs_json]
            core.check_record(project, args.kind, args.text, refs=refs, resolves=args.resolves,
                              details=args.details_json, origin=origin)
            if args.kind != "checkpoint":
                core.sync_context(project, origin=core.system_origin(origin))
            written = core.record(project, args.kind, args.text, refs=refs,
                                  resolves=args.resolves, details=args.details_json, origin=origin)
            _emit({"ok": True, **written.to_json(), "workspace": _safe_render(workspace, project)})
        elif args.command == "assets":
            action = args.asset_action
            if action in ("list", "show"):
                result = core.assets(project, args.asset_id if action == "show" else None)
                if action == "show" and not result["assets"] and not result["legacy_candidates"]:
                    raise core.AnalyticsError("unknown_asset", f"no asset candidate {args.asset_id!r}")
            elif action == "review":
                result = core.review_assets(project, args.asset_id, origin=origin, surface="cli")
            elif action == "update":
                if not isinstance(args.details_json, dict):
                    raise core.AnalyticsError("bad_asset", "--details-json is a JSON object")
                result = core.update_asset(project, args.event_id, expected=args.expected, new=args.new,
                                           text=args.reason, details=args.details_json, origin=origin)
            elif action == "mode":
                result = core.set_asset_mode(project, args.mode, origin=origin)
            else:
                result = core.register_asset(project, args.event_id, name=args.name, origin=origin)
            _emit({"ok": True, **result})
        elif args.command == "render":
            from . import workspace

            core.sync_context(project, origin=core.system_origin(origin))
            _emit({"ok": True, "workspace": workspace.build(project, if_changed=args.if_changed),
                   "request_id": core.request_id_of(origin)})
        elif args.command == "demo":
            from . import demo

            _emit({"ok": True, **demo.populate(project, origin=origin),
                   "request_id": core.request_id_of(origin)})
        return 0
    except core.AnalyticsError as exc:
        _emit(exc.to_json())
        return 1


def _publication_scope(args):
    """--report or --collection makes a Report copy unless --scope project asks for the project page
    with those versions in it (PUBLICATION-BUILD 8.1)."""
    if args.collection is not None:
        if args.report or args.scope is not None:
            raise core.AnalyticsError("scope_invalid", "--collection takes no --report or --scope")
        return "collection"
    if args.scope is None:
        return "reports" if args.report else "project"
    if args.scope == "reports" and not args.report:
        raise core.AnalyticsError("scope_invalid", "--scope reports needs --report REPORT_ID[@REVISION_ID]")
    return args.scope


def _selected_reports(args):
    values = [args.collection] if args.collection is not None else args.report
    out = []
    for value in values:
        report_id, sep, revision_id = value.partition("@")
        if not report_id or "@" in revision_id or sep and not revision_id or args.collection and not sep:
            raise core.AnalyticsError("scope_invalid", "give REPORT_ID or REPORT_ID@REVISION_ID",
                                      details={"value": value})
        out.append((report_id, revision_id))
    return tuple(out)


def _publication_command(args):
    from . import relations
    from .publishing import state
    from .publishing.model import PublicationError

    readonly = args.list_publications or args.verify is not None or args.zip_of is not None
    if readonly and (args.exclude or args.report or args.collection or args.scope not in (None, "project")
                     or args.profile != "read" or args.analysis_state != "in_progress" or args.allow_withdrawn):
        raise core.AnalyticsError("scope_invalid", "scope and profile options apply only to build or preview")
    if args.zip and (args.preview or args.list_publications or args.verify is not None):
        raise core.AnalyticsError("zip_path_invalid", "--zip applies only to build or --zip-of")
    if args.zip_of is not None and not args.zip:
        raise core.AnalyticsError("zip_path_invalid", "--zip-of requires --zip PATH")
    project = None
    if args.verify is not None:
        start = args.project or os.environ.get("AELIX_ANALYTICS_PROJECT") or os.getcwd()
        root = core.find_project(start)
        if root is not None or args.project or os.environ.get("AELIX_ANALYTICS_PROJECT"):
            project = _project(args)
    else:
        project = _project(args)
    if project is not None:
        core.readable(project, "inspect publication")
    try:
        if args.verify is not None:
            result = state.verify(args.verify, model=core.read_model(project) if project else None)
        elif args.list_publications:
            result = state.list_publications(core.read_model(project))
        elif args.zip_of is not None:
            from .publishing.package import write_zip

            model = core.read_model(project)
            observed = state.observe(model, args.zip_of)
            if observed["recording_status"] != "complete":
                raise PublicationError("publication_not_recorded", "no unique supported completion record", details=observed)
            if not observed["folder_checked"] or observed["folder_status"] != "same":
                raise PublicationError("package_check_failed", "completed publication folder is not intact", details=observed)
            _state, _reason, _candidates, event = relations.publication_record(model.journal, args.zip_of)
            path = project.root / event["details"]["location"]
            result = {"ok": True, "publication_id": args.zip_of, "recording_status": "complete",
                      "zip": write_zip(path, args.zip, project_root=project.root, binding=event["details"])}
        else:
            from .publishing.build import build_publication, preview_package
            from .publishing.projection import ShareRequest
            from .workspace import default_report_renderer

            request = ShareRequest(scope=_publication_scope(args), reports=_selected_reports(args),
                                   analysis_state=args.analysis_state, allow_withdrawn=args.allow_withdrawn)
            # The Workspace's own ReportRenderer, injected here so the publishing layer imports no
            # renderer implementation (ADR-0013 7, PUBLICATION-BUILD 7.5).
            renderer = default_report_renderer() if request.reports else None
            if args.preview:
                result, _ = preview_package(project, request, profile=args.profile, exclude=args.exclude,
                                            report_renderer=renderer)
            else:
                result = build_publication(project, request, profile=args.profile, exclude=args.exclude,
                                           zip_path=args.zip, origin=_origin(), report_renderer=renderer)
    except PublicationError as exc:
        error = core.AnalyticsError(exc.code, exc.message, details=exc.details)
        if exc.details.get("interrupted"):
            _emit(error.to_json())
            return 130
        raise error from exc
    except (UnicodeError, RecursionError) as exc:
        raise core.AnalyticsError("records_unsupported", "publication input text or nesting is not supported") from exc
    except (ImportError, OSError) as exc:
        code = "package_check_failed" if readonly else "renderer_unavailable"
        raise core.AnalyticsError(code, "publication resources are unavailable") from exc
    _emit(result)
    return 130 if result.get("interrupted") else 0 if result.get("ok") else 1


def _publish_command(args):
    from .publishing import publish
    from .publishing.model import PublicationError

    project = _project(args)
    if args.list_attempts:
        if args.to or args.channel or args.reassign_channel:
            raise core.AnalyticsError("publish_input_invalid", "--list takes no target or channel")
        core.readable(project, "inspect publish attempts")
        _emit(publish.attempts(project))
        return 0
    if not args.to:
        raise core.AnalyticsError("publish_input_invalid", "--build needs --to FOLDER")
    core.admit(project, "publish")
    try:
        result = publish.publish(project, args.build, args.to, origin=_origin(), channel=args.channel,
                                 reassign=args.reassign_channel)
    except PublicationError as exc:
        raise core.AnalyticsError(exc.code, exc.message, details=exc.details) from exc
    _emit(result)
    if result.get("access_note"):
        print(result["access_note"], file=sys.stderr)
    return 130 if result.get("outcome") == "interrupted" else 0 if result.get("ok") else 1


def _safe_render(workspace, project) -> dict:
    try:
        return workspace.build(project)
    except Exception as exc:  # noqa: BLE001 - the mutation already happened; the Workspace is derived
        return {"error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    sys.exit(main())
