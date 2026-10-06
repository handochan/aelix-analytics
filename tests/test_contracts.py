"""Every implementation of a replaceable part keeps one contract (ADR-0001 confirmation 2, Issue #43).

A replaceable part is an interface with more than one implementation, one of which a caller chooses or a
test injects. Today these are the four platform ports of ADR-0003 (`FileLocks`, `AtomicFiles`,
`ProcessControl`, `RunControl`), carried by a `Project` (`Project(root, platform=…)`), with three
implementations: POSIX, Windows, and the recording stand-in tests inject (tests/platform_doubles.py).
Policy Strategies are not here yet: ProposalPolicy (#17) and PublicationProjection (#20) add their own
table of implementations and their cases in this shape when they exist.

Each case runs once per implementation, where that implementation runs: the POSIX one on the Linux and
macOS legs, the Windows one on the Windows leg. Elsewhere the case is skipped and says so; a skip is
not a pass. An implementation declares what it does (`capabilities`), and a case that needs what it
does not declare is skipped with that reason (ADR-0001: a new implementation states what it supports).

Two kinds of case:

- the ports' contract as their Protocols in _platform.py state it: what each method promises, and that
  every member of the Protocol is there with the same calling convention;
- the assembly's: injected into a Project, the implementation is the one used — the process default
  (`core.PLATFORM`'s objects) and every other implementation are armed to fail, so a path that quietly
  falls back cannot pass by writing the same record — and a representative flow writes the same
  records as with the process default: ids, versions, references and states (ADR-0001 confirmation 2).
  This is Issue #8's method (tests/test_platform.py, `real_ports_refuse`) widened to every
  implementation; the real ones are armed by instance, as the injected one shares their classes.

`relations` is not a second implementation of `core`'s reading: core reads through it
(`core.read_model`). What it takes from outside is `alive` and `sha`; the flow below includes a running
record of a dead process, so its `alive` is the injected implementation's.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import re
import signal
import textwrap
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import write_sales_csv
from platform_doubles import IMPLEMENTATIONS as REAL_CLASSES
from platform_doubles import port_methods, recording_platform

from aelix_analytics import _platform, core

PACK = Path(__file__).resolve().parents[1]
TEMPLATE = PACK / "src" / "aelix_analytics" / "template"

BETWEEN_PROCESSES = "excludes other processes"
SEES_PROCESSES = "sees running processes"
INTERRUPTS = "interrupts a region"


@dataclass(frozen=True)
class Implementation:
    name: str
    make: Callable[[], object]
    runs_here: bool
    elsewhere: str  # where it runs, said when this platform skips it
    capabilities: frozenset[str]


REAL = frozenset({BETWEEN_PROCESSES, SEES_PROCESSES, INTERRUPTS})
IMPLEMENTATIONS: dict[str, Implementation] = {
    impl.name: impl for impl in (
        Implementation("posix", lambda: _platform.detect("posix"), os.name != "nt",
                       "the POSIX implementation runs on the Linux and macOS legs", REAL),
        Implementation("windows", lambda: _platform.detect("windows"), os.name == "nt",
                       "the Windows implementation runs on the Windows leg", REAL),
        # In this process only: its lock excludes nothing outside it, alive is False for every pid,
        # and its guard never interrupts.
        Implementation("recording", lambda: recording_platform()[0], True, "", frozenset()),
    )}

PORTS = {"locks": _platform.FileLocks, "files": _platform.AtomicFiles,
         "processes": _platform.ProcessControl, "runs": _platform.RunControl}


@pytest.fixture(params=sorted(IMPLEMENTATIONS))
def implementation(request) -> Implementation:
    impl = IMPLEMENTATIONS[request.param]
    if not impl.runs_here:
        pytest.skip(impl.elsewhere)
    return impl


def needs(impl: Implementation, capability: str) -> None:
    if capability not in impl.capabilities:
        pytest.skip(f"the {impl.name} implementation does not declare that it {capability}")


def test_every_implementation_of_the_ports_is_in_the_tables():
    """A new implementation is not left out: every class of _platform named after the port it
    implements is armed by the assembly cases (REAL_CLASSES), and every bundle detect() can build
    runs these cases (IMPLEMENTATIONS)."""
    ports = tuple(protocol.__name__ for protocol in PORTS.values())
    classes = {obj for name, obj in vars(_platform).items()
               if inspect.isclass(obj) and obj.__module__ == _platform.__name__
               and name not in ports and name.endswith(ports)}
    missing = sorted(cls.__name__ for cls in classes - set(REAL_CLASSES))
    assert classes and not missing, f"not in platform_doubles.IMPLEMENTATIONS: {missing}"
    detect = ast.parse(textwrap.dedent(inspect.getsource(_platform.detect)))
    built = {node.args[0].value for node in ast.walk(detect)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "_Bundle"}
    unlisted = sorted(built - set(IMPLEMENTATIONS))
    assert built and not unlisted, f"detect() builds bundles IMPLEMENTATIONS lacks: {unlisted}"
    assert {_platform.detect(name).name for name in built} == built
    assert _platform.detect().name in built


# --- every member of the Protocol, called the Protocol's way ---------------------------------------

def _members(protocol) -> dict[str, inspect.Signature | None]:
    """The Protocol's attributes (None) and methods (their signatures), dunders of a context manager
    included."""
    members: dict[str, inspect.Signature | None] = dict.fromkeys(inspect.get_annotations(protocol))
    for name, value in vars(protocol).items():
        if inspect.isfunction(value) and (not name.startswith("_")
                                          or name in ("__enter__", "__exit__")):
            members[name] = inspect.signature(value)
    return members


def _calls(signature: inspect.Signature):
    """The ways a caller of the Protocol may call it: the required arguments alone, by position, and
    all of them, the optional ones by name."""
    params = list(signature.parameters.values())[1:]  # without self
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return [((None, None, None), {})]  # __exit__(*exc_info), as `with` calls it
    required = [p for p in params if p.default is p.empty]
    positional = [object() for p in required if p.kind is not p.KEYWORD_ONLY]
    keywords = {p.name: object() for p in required if p.kind is p.KEYWORD_ONLY}
    every = {p.name: object() for p in params if p.default is not p.empty}
    return [(tuple(positional), keywords), (tuple(positional), {**keywords, **every})]


def _conforms(obj, protocol, what: str) -> list[str]:
    missing = []
    for name, signature in _members(protocol).items():
        if not hasattr(obj, name):
            missing.append(f"{what}.{name} is missing")
            continue
        if signature is None:
            continue
        member = getattr(obj, name)
        if not callable(member):
            missing.append(f"{what}.{name} is not callable")
            continue
        for args, kwargs in _calls(signature):
            try:
                inspect.signature(member).bind(*args, **kwargs)
            except TypeError as exc:
                missing.append(f"{what}.{name} cannot be called as {protocol.__name__} says "
                               f"({len(args)} positional, keywords {sorted(kwargs)}): {exc}")
    return missing


def test_every_implementation_offers_each_port_whole(implementation):
    bundle = implementation.make()
    problems = _conforms(bundle, _platform.Platform, implementation.name)
    for port, protocol in PORTS.items():
        problems += _conforms(getattr(bundle, port), protocol, f"{implementation.name}.{port}")
    problems += _conforms(bundle.runs.guard(), _platform.RunGuard,
                          f"{implementation.name}.runs.guard()")
    assert not problems, "\n".join(problems)


# --- FileLocks ---------------------------------------------------------------------------------------

def _within(seconds: float, action: Callable[[], None]) -> bool:
    """Whether `action` finished within `seconds`; a lock that is never released would otherwise
    hold the suite until its timeout."""
    done = threading.Event()

    def run():
        action()
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return done.wait(seconds)


def test_a_lock_makes_its_directory_and_file_and_keeps_their_bytes(implementation, tmp_path):
    locks = implementation.make().locks
    lock = tmp_path / "missing" / "lock"
    with locks.exclusive(lock):
        pass
    assert lock.is_file()
    # Written between two holds: on Windows a second handle cannot write the byte the lock holds.
    lock.write_bytes(b"content")
    with locks.exclusive(lock):
        pass
    assert lock.read_bytes() == b"content"


def test_a_lock_is_free_after_its_body_even_one_that_raised(implementation, tmp_path):
    locks = implementation.make().locks
    lock = tmp_path / "lock"

    class Stop(Exception):
        pass

    def take_and_let_go():
        with locks.exclusive(lock):
            pass

    with pytest.raises(Stop):
        with locks.exclusive(lock):
            raise Stop
    # Another holder — on POSIX another open file description, on Windows another handle — gets it.
    assert _within(10, take_and_let_go), "the lock was kept after its body raised"
    with locks.exclusive(lock):
        pass
    assert _within(10, take_and_let_go), "the lock was kept after its body ended"


def _hold(name: str, lock: str, started, release) -> None:
    with _platform.detect(name).locks.exclusive(Path(lock)):
        started.set()
        release.wait(30)


def test_a_lock_excludes_another_process(implementation, tmp_path):
    needs(implementation, BETWEEN_PROCESSES)
    import multiprocessing  # noqa: PLC0415 - only this case starts another process

    lock = tmp_path / "lock"
    started, release = multiprocessing.Event(), multiprocessing.Event()
    holder = multiprocessing.Process(target=_hold, args=(implementation.name, str(lock), started,
                                                         release))
    holder.start()
    try:
        assert started.wait(30), "the other process never took the lock"
        acquired = threading.Event()

        def take():
            with implementation.make().locks.exclusive(lock):
                acquired.set()

        threading.Thread(target=take, daemon=True).start()
        assert not acquired.wait(0.5), "the lock did not exclude the other process"
        release.set()
        assert acquired.wait(20), "the lock was not free once the other process let it go"
    finally:
        release.set()
        holder.join(20)


# --- AtomicFiles -------------------------------------------------------------------------------------

def test_a_replacement_puts_the_new_bytes_in_place(implementation, tmp_path):
    files = implementation.make().files
    src, dst = tmp_path / "record.json.tmp", tmp_path / "record.json"
    dst.write_bytes(b"old")
    src.write_bytes(b"new")
    files.replace(src, dst)
    assert dst.read_bytes() == b"new" and not src.exists()
    src.write_bytes(b"newer")
    files.replace(str(src), str(dst))
    assert dst.read_bytes() == b"newer" and not src.exists()


def test_a_replacement_that_cannot_happen_is_raised_not_lost(implementation, tmp_path):
    files = implementation.make().files
    with pytest.raises(OSError):
        files.replace(tmp_path / "absent.tmp", tmp_path / "record.json")
    assert not (tmp_path / "record.json").exists()


# --- ProcessControl ----------------------------------------------------------------------------------

@pytest.mark.parametrize("pid", [0, -1, 2**40, "x", None, True])
def test_a_value_that_cannot_be_a_pid_is_not_alive(implementation, pid):
    assert implementation.make().processes.alive(pid) is False


def test_this_process_is_alive(implementation):
    needs(implementation, SEES_PROCESSES)
    assert implementation.make().processes.alive(os.getpid()) is True


def test_a_child_s_settings_are_plain_values(implementation):
    processes = implementation.make().processes
    assert isinstance(processes.asks_by_request, bool)
    assert isinstance(processes.spawn_kwargs(), dict)
    env = processes.base_env()
    assert isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str)
                                         for k, v in env.items())


# --- RunControl --------------------------------------------------------------------------------------

def _handlers() -> dict:
    names = ("SIGINT", "SIGTERM", "SIGALRM", "SIGBREAK")
    return {n: signal.getsignal(getattr(signal, n)) for n in names if hasattr(signal, n)}


def test_a_guard_starts_unmarked_and_puts_back_what_it_replaced(implementation):
    before = _handlers()
    with implementation.make().runs.guard() as guard:
        assert (guard.cancelled, guard.expired) == (False, False)
    assert _handlers() == before


def test_a_region_nothing_disturbs_leaves_no_mark(implementation):
    with implementation.make().runs.guard() as guard:
        with guard.interruptible(deadline=30.0):
            pass
        assert guard.settle() is None
    assert (guard.cancelled, guard.expired) == (False, False)


def test_a_deadline_interrupts_its_region_and_is_marked(implementation):
    needs(implementation, INTERRUPTS)
    started = time.perf_counter()
    with implementation.make().runs.guard() as guard:
        with pytest.raises(_platform.Interrupted):
            with guard.interruptible(deadline=1.0):
                time.sleep(10)
    assert time.perf_counter() - started < 5, "the sleep was not interrupted"
    assert (guard.expired, guard.cancelled) == (True, False)


def test_watching_for_a_cancel_stops_when_asked(implementation, tmp_path):
    stop = implementation.make().runs.watch_for_cancel(tmp_path / "cancel")
    assert callable(stop) and stop() is None


# --- the assembly: injected, it is the one used, and the records are the same -------------------

# No process has this pid: it is beyond any pid Linux (at most 2**22), macOS (at most 99998) or
# Windows assigns. Windows ignores a pid's two low bits and asks about 2**31 - 4, still beyond.
DEAD_PID = 2**31 - 2
CONTEXT_VERSION_RE = re.compile(r"^\d{8}-\d{6}-\d{6}_")


def _arm_all_but(monkeypatch, chosen) -> None:
    """Every implementation other than `chosen` refuses: every real class `chosen` is not built from,
    and the process default's own objects (of the same classes as `chosen` when it is this
    platform's)."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("an implementation other than the injected one was used")

    chosen_classes = {type(getattr(chosen, port)) for port in PORTS}
    for cls in REAL_CLASSES:
        if cls not in chosen_classes:
            for name in port_methods(cls):
                monkeypatch.setattr(cls, name, refuse)
    for port in PORTS:
        default = getattr(core.PLATFORM, port)
        assert default is not getattr(chosen, port), "the case needs an instance of its own"
        for name in port_methods(type(default)):
            _patch_object(monkeypatch, default, name, refuse)


def _patch_object(monkeypatch, obj, name: str, value) -> None:
    """Put `value` in the object's own dict, from where undoing removes it again.

    `monkeypatch.setattr(obj, …)` keeps `getattr(obj, name)` to put back, and once the class is
    armed that is the refusal itself: undoing then left the process default refusing for every later
    test (Windows CI run 37422827250, where the recording case is the first armed one)."""
    monkeypatch.setitem(vars(obj), name, value)


def _spy(monkeypatch, bundle) -> list[tuple[str, tuple]]:
    """Record each call of the bundle's port methods: (port.method, its arguments, a path as its
    name)."""
    calls: list[tuple[str, tuple]] = []
    for port in PORTS:
        obj = getattr(bundle, port)
        for name in port_methods(type(obj)):
            original = getattr(obj, name)

            def spy(*args, _original=original, _what=f"{port}.{name}", **kwargs):
                calls.append((_what, tuple(Path(a).name if isinstance(a, (str, os.PathLike))
                                           else a for a in args)))
                return _original(*args, **kwargs)

            _patch_object(monkeypatch, obj, name, spy)
    return calls


def _armed() -> list[str]:
    """What arming leaves behind when it is not undone: a method of the process default's objects
    in their own dict (they keep none there), or a real class's method that is not its own."""
    left = [f"core.PLATFORM.{port}.{name}" for port in PORTS
            for name in port_methods(type(getattr(core.PLATFORM, port)))
            if name in vars(getattr(core.PLATFORM, port))]
    return left + [f"{cls.__name__}.{name}" for (cls, name), value in _class_methods().items()
                   if value is not _CLASS_METHODS[(cls, name)]]


def _class_methods() -> dict[tuple[type, str], object]:
    return {(cls, name): cls.__dict__.get(name) for cls in REAL_CLASSES for name in port_methods(cls)}


_CLASS_METHODS = _class_methods()  # as the real classes define them, before any case runs


@pytest.fixture
def arming():
    """The MonkeyPatch a case arms with: undone when the case ends, passed or failed, and then
    checked — the real classes and the process default are as they were. Not `monkeypatch`:
    tests/conftest.py's autouse fixture takes that one first, so it is undone only after every
    fixture here is torn down, too late for a check here."""
    patch = pytest.MonkeyPatch()
    yield patch
    patch.undo()
    left = _armed()
    assert not left, f"still armed after the case: {left}"


@pytest.fixture(autouse=True)
def nothing_stays_armed():
    """After every case of this file, nothing is armed: a case that arms with `monkeypatch` instead
    of `arming` fails here, before that would be undone."""
    yield
    left = _armed()
    assert not left, f"still armed after the case (arm with the `arming` fixture): {left}"


def test_arming_is_undone_whichever_implementation_is_armed_first(tmp_path):
    """Armed for each implementation in turn and undone, as a case does, the real classes and the
    process default are as they were and the default still writes. On Windows the recording case
    was the first armed one (the POSIX one is skipped there), with the default's classes armed
    before its objects (CI run 37422827250)."""
    for name, impl in sorted(IMPLEMENTATIONS.items(), key=lambda item: item[0] != "recording"):
        with pytest.MonkeyPatch.context() as monkeypatch:
            _arm_all_but(monkeypatch, impl.make())
            assert _armed(), f"arming for {name} armed nothing"
        assert not _armed(), f"arming for {name} outlived its context: {_armed()}"
    core.write_json(tmp_path / "record.json", {"written": True})
    assert json.loads((tmp_path / "record.json").read_text(encoding="utf-8")) == {"written": True}


def _stable(value):
    """A context version without its wall-clock part; the content digest after it is kept."""
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    if isinstance(value, str):
        return CONTEXT_VERSION_RE.sub("<when>_", value)
    return value


def _flow(root: Path, source: Path, platform) -> dict:
    """A project made, data imported, a finding recorded and a running record of a dead process
    left, read back through status (and so through relations): what the records mean."""
    from aelix_analytics import data  # noqa: PLC0415 - needs the analysis libraries, unlike core

    project = core.init_project(root, title="교체 계약", goal="어느 구현이든 같은 기록",
                                template_dir=TEMPLATE, actor="test", platform=platform)
    data.import_file(project, source, name="sales", description="test data", actor="test")
    core.add_note(project, "finding", "구현을 바꿔도 같은 기록", refs=["sales"], actor="test")
    stuck = project.runs / "20260922-101010-left-running"
    stuck.mkdir(parents=True)
    (stuck / "run.json").write_text(json.dumps({"schema": core.SCHEMA_RUN, "status": "running",
                                                "pid": DEAD_PID}), encoding="utf-8")
    status = core.status(project)
    return _stable({
        "events": [{k: e[k] for k in ("id", "kind", "actor", "text", "refs")}
                   for e in core.read_events(project)],
        "datasets": [{k: d.get(k) for k in ("name", "path", "kind", "rows", "sha256")}
                     for d in status["datasets"]],
        "views": status["views"], "counts": status["counts"], "as_of_seq": status["as_of_seq"],
        "runs": [{k: r.get(k) for k in ("id", "status", "recording_status", "reasons")}
                 for r in status["runs"]],
        "listed": [(r["id"], r["status"]) for r in core.list_runs(project)],
    })


def test_the_injected_implementation_is_the_one_used(implementation, tmp_path, arming):
    chosen = implementation.make()
    _arm_all_but(arming, chosen)
    calls = _spy(arming, chosen)
    seen = _flow(tmp_path / "p", write_sales_csv(tmp_path / "sales.csv"), chosen)

    assert seen["listed"] == [("20260922-101010-left-running", "incomplete")], seen["listed"]
    replaced = [args[-1] for what, args in calls if what == "files.replace"]
    assert "sales.parquet" in replaced and "lab.json" in replaced, calls
    assert ("locks.exclusive", ("lock",)) in calls, calls
    assert ("processes.alive", (DEAD_PID,)) in calls, calls


def test_a_run_goes_through_the_injected_implementation(implementation, tmp_path, arming):
    from aelix_analytics import runner  # noqa: PLC0415 - needs the analysis libraries

    chosen = implementation.make()
    _arm_all_but(arming, chosen)
    calls = _spy(arming, chosen)
    project = core.init_project(tmp_path / "p", title="실행 계약", template_dir=TEMPLATE,
                                actor="test", platform=chosen)
    project.scripts.mkdir(parents=True, exist_ok=True)
    (project.scripts / "01_min.py").write_text("print('ok')\n", encoding="utf-8")
    result = runner.execute(project, script="scripts/01_min.py", title="최소 실행", actor="test")

    assert result["run"]["status"] == "ok", result["run"].get("error")
    # A run swallows a failure after its script (the Workspace, the bookkeeping): a refused port
    # would otherwise show only as a recorded error.
    assert "report_error" not in result, result.get("report_error")
    assert not result.get("finalize_errors"), result.get("finalize_errors")
    assert not result["run"].get("finalize_errors"), result["run"].get("finalize_errors")
    assert {"runs.guard", "locks.exclusive", "files.replace"} <= {what for what, _ in calls}, calls
    assert "run.json" in [args[-1] for what, args in calls if what == "files.replace"], calls


def test_every_implementation_writes_what_the_process_default_writes(implementation, tmp_path):
    source = write_sales_csv(tmp_path / "sales.csv")
    reference = _flow(tmp_path / "default", source, core.PLATFORM)
    chosen = _flow(tmp_path / "chosen", source, implementation.make())
    assert chosen == reference
