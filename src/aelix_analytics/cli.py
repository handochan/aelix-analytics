"""`analytics`, the human command line of Aelix Analytics, as its distribution installs it.

It keeps the contract of `bin/analytics`, a checkout's command — the commands and their arguments, the
usage text, the kit's output passed through untouched, and the exit statuses — on every platform:
installers make it `analytics`, and `analytics.exe` on Windows, where the POSIX shell script cannot run
(ADR-0002 decision 6, ADR-0004). Its usage and the messages `bin/analytics` prints too are the same
bytes (before kit 0.3.0 the script was `bin/lab`, and they differed by that name); where `bin/analytics`
leaves an error to the shell (no uv, a program that cannot start, a current directory that cannot be
read), it prints one of its own. The measured differences are in docs/CONTRACTS.md, section F;
tests/test_launcher.py holds the two to one contract.

The kit's old names stop here before anything else runs (ADR-0005 decision 7): the subcommands `note`
and `report` (now `record` and `render`) are usage errors, and an environment variable this command
read under an old name (OLD_ENV) is an error that names the new one. Neither is an alias.

Every command runs the kit in the project's own environment, as `bin/analytics` does:
`uv run --project <project> python -m aelix_analytics ...` with PYTHONPATH set to exactly the
runtime root this distribution installed (aelix_analytics/kit/, which holds a copy of this package;
the package this file is in refuses `python -m`, see __main__). Three run it outside any environment
(`uv run --no-project`), since an environment made there would be a write: `init`, before the
project is there; `migrate`, which reads its source and makes its destination by its own rules, and
prepares the destination's environment only once it is committed (`uv sync --locked`, design 6.1);
and every command in a migration's destination that has not committed, which the kit refuses
(migration_incomplete, design 4.3). This process uses the standard
library only, on Python 3.11 or later, and imports only this package's stdlib-only __init__ and
_install.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from . import _install

# bin/analytics prints its header comment as its usage; this is the same text.
USAGE = """\
analytics — human CLI for Aelix Analytics projects (the same kit the aelix extension drives).

  analytics init DIR [--title T] [--goal G]   create a project and its uv environment
  analytics demo DIR                          new project with synthetic data and example runs
  analytics status [--part records|relations|context] [--query WORDS|--id ID] [--cursor TOKEN]
  analytics status --part relations --id ID --direction sources|uses [--depth N]
  analytics context                           the context block agents receive
  analytics run SCRIPT --title T [--input P] [--timeout S]
  analytics run --rerun-of RUN_ID --title T   re-execute a run's code snapshot and compare
  analytics import FILE [--name N] [--description D]
  analytics query "SQL" [--limit N]
  analytics checkpoint [TEXT] [--ref REF] [--from-seq N] [--limit N]
                       [--as-of-seq N --context-version VERSION|--context-unknown]
  analytics record KIND "TEXT" [--ref R] [--resolves eN] [--details-json JSON] [--refs-json JSON]
  analytics reports save ID --files DIR --entry PATH --title TEXT --previous REV|none [--request-id ID] [--metadata JSON_FILE]
  analytics reports list | show ID@REV [--file PATH] | update ID@REV --expected-state STATE --state STATE --reason TEXT
  analytics reports clean --prepared ID@REV    remove one explicitly named prepared attempt
  analytics build [--preview] [--profile read|evidence] [--exclude REF]... [--zip PATH]
  analytics build --list | --verify FOLDER_OR_ZIP | --zip-of ID --zip PATH
  analytics assets list | show ID | review [ID] | update EVENT --expected STATE --to STATE --reason TEXT
  analytics assets register EVENT --name NAME    copy a validated asset into this project
  analytics assets mode on_demand|always_on      human project suggestion mode
  analytics render | open                     rebuild | open the Workspace page
  analytics migrate --to DIR [--dry-run]      copy a lab.json project into a new folder DIR

Commands other than init/demo use the project in AELIX_ANALYTICS_PROJECT, else the nearest directory
at or above the current one with an analytics.json or a lab.json, or with the record of a migration
not finished there; the kit judges it. migrate copies that project into DIR in the current format and
leaves it as it is (try --dry-run first).
Output ends with one JSON line (status and context print text); a run that got past its setup
puts an empty line before it. Nothing here is a sandbox.
"""

COMMANDS = ("init", "demo", "status", "context", "open", "run", "import", "query", "record", "checkpoint", "render",
            "migrate", "assets", "reports", "build")

# The environment variables this command read under the kit's old names (before kit 0.3.0), each with
# its new name; bin/analytics refuses the same three in the same order with the same words. The kit
# refuses the others it read (core.RENAMED_ENV), and a test holds the three to that table.
OLD_ENV = {"LAB_PROJECT": "AELIX_ANALYTICS_PROJECT", "LAB_ACTOR": "AELIX_ANALYTICS_ACTOR",
           "AELIX_LAB_UV": "AELIX_ANALYTICS_UV"}
PROJECT_NOTE = ("the analytics command and the aelix extension both read AELIX_ANALYTICS_PROJECT, "
                "ahead of the working directory")


def main(argv: list[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else list(argv))
    except KeyboardInterrupt:
        # A Ctrl+C before the handlers are in place, or after they are given back: no child is
        # running then, and a shell would end without a traceback too.
        return 130


def run(args: list[str], kit: Path | None = None, uv: list[str] | None = None) -> int:
    """`kit` is the kit directory to run, by default the one this distribution installed; `uv` the
    argv prefix that starts uv, by default the program found as `bin/analytics` finds it."""
    with _Children() as children:
        return _command(list(args), kit, uv, children)


def _command(args: list[str], kit: Path | None, uv_argv: list[str] | None,
             children: _Children) -> int:
    command = args.pop(0) if args else "status"
    if command in ("-h", "--help", "help"):
        _write(sys.stdout, USAGE)
        return 0
    if command not in COMMANDS:
        _write(sys.stderr, USAGE)
        return 2
    if command in ("init", "demo") and not args:
        _say(f"usage: analytics {command} DIR")
        return 2
    for old, new in OLD_ENV.items():
        if os.environ.get(old):
            note = f" ({PROJECT_NOTE})" if new == "AELIX_ANALYTICS_PROJECT" else ""
            _say(f"analytics: {old} was renamed {new} in kit 0.3.0{note}; set {new} instead and "
                 f"unset {old}")
            return 2
    try:
        kit = kit or _install.kit_dir()
    except RuntimeError as exc:
        _say(str(exc))
        return 1
    processes = _load_platform(kit).detect().processes
    env = child_env(kit)

    if command in ("init", "demo"):
        uv = uv_argv or _uv(processes)
        if uv is None:
            return 127
        directory, rest = args[0], args[1:]
        code = children.run([*uv, "run", "--no-project", "--python", "3.12", "--quiet", "python",
                             "-m", "aelix_analytics", "init", directory, *rest], env)
        if code or children.interrupted:
            return code or children.stopped
        project = str(Path(directory).resolve())
        _say("analytics: syncing the project environment (first time can take a minute) ...")
        code = children.run([*uv, "sync", "--project", project, "--quiet"], env)
        if code and not children.interrupted:
            code = children.run([*uv, "sync", "--project", project, "--quiet", "--offline"], env)
        if code or children.interrupted:
            return code or children.stopped
        if command == "demo":
            code = children.run(kit_argv(uv, project, "demo"), env)
        else:
            code = children.run(kit_argv(uv, project, "render"), env, stdout=subprocess.DEVNULL)
        if code or children.interrupted:
            return code or children.stopped
        index = Path(project) / "workspace" / "index.html"
        _say(f"analytics: Workspace at {index} (analytics open, from inside the project)")
        return 0

    if command == "migrate":
        return _migrate(args, uv_argv, processes, env, children)

    if command == "build" and any(a in ("--list", "--verify", "--zip-of") or a.startswith(("--verify=", "--zip-of=")) for a in args):
        # Read-only delivery inspection runs on this launcher's stdlib interpreter, including
        # standalone verification outside every Project. The kit finds an optional Project.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return children.run([sys.executable, "-m", "aelix_analytics", "build", *args], env)
    project = find_project()
    if project is None:
        return 1
    # A migration's destination that has not committed: the kit refuses every command there
    # (migration_incomplete) and is asked outside any environment, so uv makes none in it (design 4.3).
    outside = unfinished_migration(project)
    if command == "open":
        # `open` reads no record itself: it opens the page of the directory the kit takes for the
        # project, by the kit's presence rule (kit_marker). From where the search stopped, a
        # directory whose marker entries all lead nowhere is passed over, as the kit passes it over;
        # AELIX_ANALYTICS_PROJECT is taken as it is given, as before.
        if not os.environ.get("AELIX_ANALYTICS_PROJECT"):
            project = kit_root(project)
        if outside:
            # No page there: the step that would build one is the kit's answer, shown as it is.
            uv = uv_argv or _uv(processes)
            if uv is None:
                return 127
            code = children.run(kit_outside(uv, project, "render"), env)
            if code or children.interrupted:
                return code or children.stopped
        if all(kit_marker(Path(project), marker) for marker in MARKERS):
            # Two markers: the kit reads neither (marker_conflict), and neither page is opened. Only
            # their presence is looked at, not what they hold.
            _say(two_markers_message(project))
            return 1
        # The page of the project's layout, by its marker's name (the kit reads what it holds): an
        # analytics.json project's workspace/, a lab.json project's report/, which this kit opens
        # when it is there and does not build or update (migration_required).
        older = not kit_marker(Path(project), MARKERS[0])
        index = Path(project) / ("report" if older else "workspace") / "index.html"
        if older:
            # Opened when it is there; never built, so no environment is made for it either.
            if not index.is_file():
                _say(older_unbuilt_message(project))
                return 1
            _say(older_page_message(project))
        if not index.is_file():
            # uv only to build the report: opening one needs none, as with bin/analytics. The kit's
            # answer is shown when it builds none (a project it does not write to, say).
            uv = uv_argv or _uv(processes)
            if uv is None:
                return 127
            code, answer = _captured(children, kit_argv(uv, project, "render"), env)
            if code or children.interrupted:
                _write_bytes(sys.stdout, answer)
                return code or children.stopped
        if children.interrupted:  # the opener is a step too
            return children.stopped
        try:
            status = exit_status(processes.open_document(index, wait=True) or 0)
        except OSError as exc:
            _say(f"analytics: could not open {index} ({exc}); open it in a browser yourself")
            return 127 if isinstance(exc, FileNotFoundError) else 1
        return children.stopped or status
    if command == "build":
        # A preview must not let uv create/sync .venv or change the project's lock. Use the
        # already prepared project interpreter, and suppress import bytecode side writes.
        python = Path(project) / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            _say(f"analytics: {'preview' if '--preview' in args else 'build'} needs an existing project environment; run uv sync first")
            return 1
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return children.run([str(python), "-m", "aelix_analytics", "--project", project, command, *args], env)
    uv = uv_argv or _uv(processes)
    if uv is None:
        return 127
    if command in ("assets", "reports"):
        return children.run(kit_outside(uv, project, command, *args), env)
    kit_call = kit_outside if outside else kit_argv
    if command in ("status", "context"):
        return children.run(kit_call(uv, project, "--text", command, *args), env)
    return children.run(kit_call(uv, project, command, *args), env, cancellable=command == "run")


def _migrate(args: list[str], uv_argv, processes, env, children: _Children) -> int:
    """`migrate`: the kit's own, outside any environment — it finds the source as every command
    finds its project (AELIX_ANALYTICS_PROJECT, else the working directory) and makes the destination
    by its rules — then, once it committed (exit 0, not a dry run), the destination's environment
    from its lock as it is (`uv sync --locked`, offline as a second try, as init does): a lock that
    does not fit is left as it is and the sync fails. That failure leaves the migration done, and
    the project's next command prepares its environment as every command does (design 6.1). The
    destination is the one the kit's answer names (committed_destination): the kit reads `--to` by
    its rules (`~` included), and an environment is prepared only in a folder that holds the
    analytics.json the migration published, so uv never looks for a project above it."""
    uv = uv_argv or _uv(processes)
    if uv is None:
        return 127
    code, answer = _captured(children, [*uv, "run", "--no-project", "--python", "3.12", "--quiet",
                                        "python", "-m", "aelix_analytics", "migrate", *args], env)
    _write_bytes(sys.stdout, answer)
    if code or children.interrupted:
        return code or children.stopped
    project = committed_destination(answer)
    if project is None:
        return 0
    if not (os.path.isdir(project) and kit_marker(Path(project), MARKERS[0])):
        _say(migrated_unprepared_message(project))
        return 0
    _say("analytics: syncing the project environment (first time can take a minute) ...")
    code = children.run([*uv, "sync", "--project", project, "--locked", "--quiet"], env)
    if code and not children.interrupted:
        code = children.run([*uv, "sync", "--project", project, "--locked", "--quiet", "--offline"],
                            env)
    if children.interrupted:
        return children.stopped
    if code:
        _say(migrated_unsynced_message(project, code))
        return 0
    _say(migrated_message(project))
    return 0


def _captured(children: _Children, argv: list[str], env) -> tuple[int, bytes]:
    """A child's exit status and what it wrote to stdout (stderr goes through)."""
    with tempfile.TemporaryFile() as out:
        code = children.run(argv, env, stdout=out)
        out.seek(0)
        return code, out.read()


def committed_destination(answer: bytes) -> str | None:
    """The destination a migration's answer — the kit's last JSON line — names when it went on
    (`ok`, not a dry run): committed now or before (already_migrated). None for any other answer."""
    lines = [line for line in answer.decode("utf-8", "surrogateescape").split("\n")
             if line.startswith("{")]
    try:
        value = json.loads(lines[-1]) if lines else None
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("ok") is not True or value.get("dry_run"):
        return None
    destination = value.get("destination")
    return destination if isinstance(destination, str) and destination else None


def migrated_message(project: str) -> str:
    return (f"analytics: the project is {project} now, and the source is as it was "
            f"(in aelix: /analytics use {project})")


def migrated_unsynced_message(project: str, code: int) -> str:
    return (f"analytics: the migration is done, but preparing the environment of {project} failed "
            f"(uv sync --locked, exit {code}); the project's next command prepares it")


def migrated_unprepared_message(project: str) -> str:
    return (f"analytics: the kit's answer names {project}, which holds no analytics.json: its "
            "environment is not prepared; analytics status there says what it is")


def child_env(kit: Path) -> dict[str, str]:
    """The caller's environment, as `bin/analytics` passes it on, and the kit exactly: PYTHONPATH is
    replaced rather than extended, so an analysis environment sees the runtime root's
    `aelix_analytics` and nothing else of the environment this command is installed in. Children write no bytecode into the installed kit,
    which an uninstall would otherwise leave behind."""
    env = {name: value for name, value in os.environ.items() if name != "VIRTUAL_ENV"}
    # AELIX_ANALYTICS_ACTOR goes on as the caller set it, and only then: without it the kit records
    # `human` as inferred, not declared (design 4.2, OQ1).
    env.update({"PYTHONPATH": str(kit), "MPLBACKEND": "Agg", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def kit_argv(uv: list[str], project: str, *args: str) -> list[str]:
    return [*uv, "run", "--project", project, "--quiet", "python", "-m", "aelix_analytics",
            "--project", project, *args]


def kit_outside(uv: list[str], project: str, *args: str) -> list[str]:
    """The kit outside any environment (`uv run --no-project`), as init runs it: for a migration's
    destination that has not committed, where an environment uv made would be a stray entry the
    migration's resumption has to pass over (design 4.3, 6.10)."""
    return [*uv, "run", "--no-project", "--python", "3.12", "--quiet", "python", "-m",
            "aelix_analytics", "--project", project, *args]


# The project markers, the new format's and the one the kit writes before it (Issue #13): the nearest
# directory with either is the project, and the kit judges what it holds (core.writer.marker_state).
MARKERS = ("analytics.json", "lab.json")


def has_marker(directory: Path, name: str) -> bool:
    """Whether `directory` holds an entry named `name` that is not a directory: a file, a FIFO, a
    socket, a link that leads nowhere or loops. The kit's own search (writer.present_marker) counts
    every one of these but a link that leads nowhere (kit_marker), and judges what it found; this one
    stops at least where the kit's does, so a directory the kit refuses is never passed over for one
    above it. From where it stops the kit searches upward again (`--project` names a start), so a
    dangling link here is passed over there. bin/analytics has the same rule."""
    path = directory / name
    return os.path.lexists(path) and not os.path.isdir(path)


def kit_marker(directory: Path, name: str) -> bool:
    """Whether `directory` holds a marker named `name` as the kit counts one (writer._look): an entry
    stat finds that is not a directory, or one stat cannot look at for another reason than its
    absence (a link that loops). A link that leads nowhere is no marker. `open` judges by this, as
    it opens a page without the kit; bin/analytics has the same rule (kit_marker there)."""
    try:
        st = os.stat(directory / name)
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return False
    except OSError:
        return True
    return not stat.S_ISDIR(st.st_mode)


def kit_root(found: str) -> str:
    """The directory the kit takes for the project when it searches upward from `found`, where the
    search (find_project) stopped (writer.find_project): `found`, unless its marker entries are only
    links that lead nowhere, and `found` again when no directory up from it has a marker or a
    migration's record (the kit then says there is no project)."""
    start = Path(found)
    for directory in (start, *start.parents):
        if any(kit_marker(directory, marker) for marker in MARKERS) or migration_record(directory):
            return str(directory)
    return found


# What a migration keeps in its destination (writer.MIGRATION_DIR, writer.MIGRATION_LOCK): without a
# marker beside it, the folder is a migration's destination that has not committed.
MIGRATION_RECORD = (".analytics/migration", ".analytics/migration.lock")


def migration_record(directory: Path) -> bool:
    """Whether `directory` holds a migration's record as the kit counts one (writer.migration_record):
    an entry of either name, a link being itself, or one that cannot be looked at (a .analytics/ this
    account cannot search). bin/analytics has the same rule."""
    for name in MIGRATION_RECORD:
        try:
            os.lstat(directory / name)
        except (FileNotFoundError, NotADirectoryError, ValueError):
            continue
        except OSError:
            return True
        return True
    return False


def unfinished_migration(project: str) -> bool:
    """Whether the kit takes `project` — the directory given it, from which it searches upward again —
    for a migration's destination that has not committed: the first directory at or above it with a
    marker as the kit counts one (kit_marker) or a migration's record has the record and no marker
    (writer.find_project, marker_state's `incomplete`)."""
    start = Path(os.path.abspath(project))
    for directory in (start, *start.parents):
        if any(kit_marker(directory, marker) for marker in MARKERS):
            return False
        if migration_record(directory):
            return True
    return False


def older_page_message(project: str) -> str:
    return (f"analytics: {project} uses the older record format (lab.json): the kit reads it and no "
            "longer updates this page; analytics status says how to migrate it")


def older_unbuilt_message(project: str) -> str:
    return (f"analytics: {project} uses the older record format (lab.json) and has no page to open: "
            "the kit reads it and builds no page for it. Copy it into the current format with "
            "`analytics migrate --to <new empty folder>` run inside it (preview with --dry-run)")


def two_markers_message(project: str) -> str:
    return (f"analytics: {project} holds two project markers, analytics.json and lab.json, and the kit "
            "reads neither; analytics status says more")


def find_project() -> str | None:
    """AELIX_ANALYTICS_PROJECT as it is given (the kit reports one that is not a project), or the nearest
    directory at or above the working directory that holds a project marker (MARKERS), whatever it
    holds, or a migration's record (MIGRATION_RECORD): the kit says whether it reads or writes there."""
    configured = os.environ.get("AELIX_ANALYTICS_PROJECT")
    if configured:
        return configured
    try:
        here = Path(os.getcwd())
    except OSError as exc:
        _say(f"analytics: the current directory cannot be read ({exc}); cd into a project or set "
             "AELIX_ANALYTICS_PROJECT")
        return None
    for directory in (here, *here.parents):
        if any(has_marker(directory, marker) for marker in MARKERS) or migration_record(directory):
            return str(directory)
    _say(f"analytics: no analytics.json or lab.json in {here} or its parents (cd into a project or set "
         "AELIX_ANALYTICS_PROJECT)")
    return None


def exit_status(returncode: int) -> int:
    """This process's exit status for a child's return code: the code itself; 128+N for a child a
    POSIX signal N ended, as a shell reports it; and a Windows status of 2**31 or more (an NTSTATUS
    such as 0xC000013A) as the negative value it is as a signed 32-bit number, which `sys.exit`
    turns back into the same status — Python 3.11 and 3.12 overflow on the unsigned one."""
    if returncode < 0:
        return 128 - returncode
    if returncode >= 2**31:
        return returncode - 2**32
    return returncode


class _Children:
    """Runs one child at a time with this process's stdin, stdout and stderr, and waits for it.

    A terminal sends Ctrl+C to its whole foreground process group, and a Windows console to every
    process attached to it: the child — uv, and the kit under it — has it already. So this process
    only notes it: it neither passes it on (the kit would get two) nor ends (the kit's record and
    its JSON line are still on their way), and exits with the child's status. It uses a handler,
    not SIG_IGN, because an ignored SIGINT is inherited across exec and the kit could then not be
    stopped; exec resets a handler. A signal this process inherited ignored — a background job's
    SIGINT — stays ignored, and so does its children's, as a shell leaves it. On Windows the C
    runtime reports every console event other than Ctrl+C (Ctrl+Break, closing the window) as
    SIGBREAK, which is noted the same way; the wait there is not interrupted by console events, so
    the handler runs once the child has exited. The handlers are in place from the start of main();
    a Ctrl+C before that, while the interpreter itself starts, is not.

    SIGTERM sent to this process alone reaches a kit `run` (uv passes it on, and the run's guard
    records a cancel, CONTRACTS D1). Any other child is left to finish: killing an import or an init
    part way would leave what it wrote unrecorded, and `bin/analytics`'s shell leaves it running too.
    This process then exits 143. That is POSIX's SIGTERM: on Windows another process cannot send
    one to a handler (`os.kill` and `taskkill /F` end this process at once).

    Once either signal has been noted, no further child is started, nor the program `analytics open` opens
    the report with. One that arrives while a child is being started may not stop it — a Ctrl+C to the
    group can land on the copy of this process before exec — so once the child runs a Ctrl+C is sent
    to it again, where the platform can (Windows sends SIGINT to no single process), and a SIGTERM if
    it is a kit `run`; the opener is sent neither, and the report can open. A child the terminal's
    Ctrl+C reached as well then gets it twice, as a kit does when uv's stdin is not a terminal (uv
    passes it on); the kit interrupts a run once per region (CONTRACTS D1)."""

    def __init__(self) -> None:
        self.stopped = 0
        self.terminated = False
        self._child: subprocess.Popen | None = None
        self._cancellable = False
        self._saved: list[tuple[int, object]] = []

    @property
    def interrupted(self) -> bool:
        return bool(self.stopped)

    def _note(self, signum, frame) -> None:
        self.stopped = self.stopped or 130

    def _terminate(self, signum, frame) -> None:
        self.stopped = self.stopped or 128 + signum
        self.terminated = True
        if self._cancellable:
            self._send(signum)

    def _send(self, signum) -> None:
        child = self._child
        if child is not None and child.returncode is None:
            with contextlib.suppress(OSError, ValueError):
                child.send_signal(signum)

    def __enter__(self) -> _Children:
        for name, handler in (("SIGINT", self._note), ("SIGBREAK", self._note),
                              ("SIGTERM", self._terminate)):
            number = getattr(signal, name, None)
            if number is None:
                continue
            current = signal.getsignal(number)
            if current is signal.SIG_IGN:
                continue
            self._saved.append((number, current))
            signal.signal(number, handler)
        return self

    def __exit__(self, *exc_info) -> bool:
        for number, previous in reversed(self._saved):
            if previous is not None:
                signal.signal(number, previous)
        return False

    def run(self, argv: list[str], env: dict[str, str], stdout=None, *,
            cancellable: bool = False) -> int:
        """The child's exit status as this process's own (exit_status); `stopped` without starting
        it once a signal has arrived, and 143 after a SIGTERM the child was left to finish.
        `cancellable`: the child is a kit `run`, which SIGTERM may reach."""
        if self.stopped:
            return self.stopped
        self._cancellable = cancellable
        try:
            self._child = subprocess.Popen(argv, env=env, stdout=stdout)
        except OSError as exc:
            _say(f"analytics: could not start {argv[0]}: {exc}")
            return 127
        if self.stopped == 130:
            self._send(signal.SIGINT)
        elif self.terminated and cancellable:
            self._send(signal.SIGTERM)
        try:
            # A handler that raises nothing leaves the wait to be retried (PEP 475).
            status = exit_status(self._child.wait())
        finally:
            self._child = None
            self._cancellable = False
        if self.terminated and not cancellable:
            return self.stopped
        return status


def _load_platform(kit: Path):
    """The kit's _platform module — standard library only, importing no other module of the kit — loaded
    from `kit` under a name of its own, and in sys.modules only while it executes, as the aelix
    extension loads it (ADR-0003). It chooses how this platform opens a document and finds a
    program, so this file has no platform branch of its own."""
    path = kit / "aelix_analytics" / "_platform.py"
    name = f"aelix_analytics_platform_{hashlib.sha256(str(kit).encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _uv(processes) -> list[str] | None:
    configured = os.environ.get("AELIX_ANALYTICS_UV") or "uv"
    found = processes.find_program(configured)
    if found is None:
        _say(f"analytics: uv not found ({configured}); install uv (https://docs.astral.sh/uv/) or set "
             "AELIX_ANALYTICS_UV to its path")
        return None
    return [found]


def _write(stream, text: str) -> None:
    # The text's UTF-8 bytes, whatever the stream's encoding is. In a path or value, what the
    # filesystem encoding read comes out as UTF-8 and the bytes it could not read as they came, so
    # where that encoding is UTF-8 (macOS; Linux under a UTF-8 locale, or the C locale in Python's
    # default UTF-8 mode) every path and value it prints itself is the bytes bin/analytics would print.
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(text)
    else:
        stream.flush()
        buffer.write(text.encode("utf-8", "surrogateescape"))
    stream.flush()


def _write_bytes(stream, data: bytes) -> None:
    """A child's output as it came, byte for byte."""
    if not data:
        return
    buffer = getattr(stream, "buffer", None)
    stream.flush()
    if buffer is None:
        stream.write(data.decode("utf-8", "replace"))
    else:
        buffer.write(data)
        buffer.flush()
    stream.flush()


def _say(text: str) -> None:
    _write(sys.stderr, text + "\n")


if __name__ == "__main__":
    sys.exit(main())
