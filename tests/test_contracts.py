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
import errno
import inspect
import json
import os
import re
import signal
import stat
import sys
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
# publish_new makes the new name by a hard link, so a failing link is the file system's (POSIX).
LINKS = "publishes by a link"
# rename_new has a rename that refuses a taken name here: Linux's renameat2, macOS's renamex_np,
# Windows' rename (Issue #13 PR 3); the POSIX implementation has none on another system.
RENAMES = "renames without replacing"
_POSIX_RENAMES = sys.platform == "darwin" or sys.platform.startswith("linux")
# A folder's permissions stop a file being created in it (POSIX, as a user other than root; Windows'
# read-only attribute on a folder does not).
FOLDER_PERMISSIONS = "honours a folder's permissions"


@dataclass(frozen=True)
class Implementation:
    name: str
    make: Callable[[], object]
    runs_here: bool
    elsewhere: str  # where it runs, said when this platform skips it
    capabilities: frozenset[str]


REAL = frozenset({BETWEEN_PROCESSES, SEES_PROCESSES, INTERRUPTS})
_NOT_ROOT = os.name != "nt" and os.geteuid() != 0
IMPLEMENTATIONS: dict[str, Implementation] = {
    impl.name: impl for impl in (
        Implementation("posix", lambda: _platform.detect("posix"), os.name != "nt",
                       "the POSIX implementation runs on the Linux and macOS legs",
                       REAL | {LINKS} | ({FOLDER_PERMISSIONS} if _NOT_ROOT else set())
                       | ({RENAMES} if _POSIX_RENAMES else set())),
        Implementation("windows", lambda: _platform.detect("windows"), os.name == "nt",
                       "the Windows implementation runs on the Windows leg", REAL | {RENAMES}),
        # In this process only: its lock excludes nothing outside it, alive is False for every pid,
        # and its guard never interrupts. Its rename_new uses a process-local mutex.
        Implementation("recording", lambda: recording_platform()[0], True, "", frozenset({RENAMES})),
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


# --- FileLocks.try_exclusive (Issue #13 PR 2, design 6.12, T15) ---------------------------------------

def test_trying_a_lock_says_whether_it_made_the_file_and_keeps_its_bytes(implementation, tmp_path):
    locks = implementation.make().locks
    lock = tmp_path / "run.lock"
    with locks.try_exclusive(lock) as created:
        assert created is True and lock.is_file()
    lock.write_bytes(b"held by an older kit")
    with locks.try_exclusive(lock) as created:
        assert created is False
    with locks.try_exclusive(str(lock)) as created:
        assert created is False
    assert lock.read_bytes() == b"held by an older kit"


def test_trying_a_lock_never_makes_its_directory(implementation, tmp_path):
    locks = implementation.make().locks
    with pytest.raises(_platform.LockUnavailable):
        with locks.try_exclusive(tmp_path / ".lab" / "lock"):
            raise AssertionError("taken without its directory")
    assert not (tmp_path / ".lab").exists()


def test_trying_a_lock_is_free_after_its_body_even_one_that_raised(implementation, tmp_path):
    locks = implementation.make().locks
    lock = tmp_path / "lock"

    class Stop(Exception):
        pass

    with pytest.raises(Stop):
        with locks.try_exclusive(lock):
            raise Stop
    with locks.try_exclusive(lock):
        pass
    assert _within(10, lambda: _take(locks, lock)), "exclusive waited on a lock let go"


def _take(locks, lock) -> None:
    with locks.exclusive(lock):
        pass


def test_a_lock_held_is_busy_to_a_try_in_the_same_process(implementation, tmp_path):
    """Not re-entrant, like `exclusive`: a second try while the first is held is LockBusy (the stand-in
    sees its own holds; a real lock is held per open file, so another descriptor is refused)."""
    locks = implementation.make().locks
    lock = tmp_path / "lock"
    with locks.try_exclusive(lock):
        started = time.perf_counter()
        with pytest.raises(_platform.LockBusy):
            with locks.try_exclusive(lock):
                raise AssertionError("taken twice")
        assert time.perf_counter() - started < 5, "the try waited"


def test_a_lock_file_that_cannot_be_written_is_tried_through_reading(implementation, tmp_path):
    """A read-only lock file (a read-only source, OQ15) is opened for reading and still locks."""
    locks = implementation.make().locks
    lock = tmp_path / "lock"
    lock.write_bytes(b"")
    os.chmod(lock, stat.S_IREAD)
    try:
        with locks.try_exclusive(lock) as created:
            assert created is False
    finally:
        os.chmod(lock, stat.S_IREAD | stat.S_IWRITE)


def test_a_lock_file_that_cannot_be_created_is_unavailable(implementation, tmp_path):
    """A missing lock file in a folder where nothing can be created (a read-only source, OQ15) is
    LockUnavailable, and nothing is made there."""
    needs(implementation, FOLDER_PERMISSIONS)
    locks = implementation.make().locks
    folder = tmp_path / "read-only"
    folder.mkdir()
    os.chmod(folder, stat.S_IREAD | stat.S_IEXEC)
    try:
        with pytest.raises(_platform.LockUnavailable):
            with locks.try_exclusive(folder / "lock"):
                raise AssertionError("taken where nothing can be created")
        assert not (folder / "lock").exists()
    finally:
        os.chmod(folder, stat.S_IRWXU)


def _hold_by(name: str, how: str, lock: str, started, release) -> None:
    locks = _platform.detect(name).locks
    taking = locks.exclusive(Path(lock)) if how == "exclusive" else locks.try_exclusive(Path(lock))
    with taking:
        started.set()
        release.wait(30)


@pytest.mark.parametrize("held_by", ["exclusive", "try_exclusive"])
def test_trying_a_lock_another_process_holds_is_busy_at_once(implementation, tmp_path, held_by):
    """The same file and byte range as `exclusive` (the older kit's lock), so the two exclude each
    other across processes: a try while another process holds it is LockBusy without waiting, and
    `exclusive` waits while another process holds it by a try."""
    needs(implementation, BETWEEN_PROCESSES)
    import multiprocessing  # noqa: PLC0415 - only this case starts another process

    lock = tmp_path / "lock"
    started, release = multiprocessing.Event(), multiprocessing.Event()
    holder = multiprocessing.Process(target=_hold_by, args=(implementation.name, held_by, str(lock),
                                                            started, release))
    holder.start()
    try:
        assert started.wait(30), "the other process never took the lock"
        locks = implementation.make().locks
        began = time.perf_counter()
        with pytest.raises(_platform.LockBusy):
            with locks.try_exclusive(lock):
                raise AssertionError("taken while another process held it")
        assert time.perf_counter() - began < 5, "the try waited"
        acquired = threading.Event()

        def take():
            with locks.exclusive(lock):
                acquired.set()

        taker = threading.Thread(target=take, daemon=True)
        taker.start()
        assert not acquired.wait(0.5), "exclusive did not wait for the other process"
        release.set()
        assert acquired.wait(20), "the lock was not free once the other process let it go"
        # The thread lets the lock go when its `with` ends, after `acquired` is set; on Windows a byte
        # range one handle of this process holds also refuses another handle of it, so wait for that.
        taker.join(20)
        assert not taker.is_alive(), "the thread never let the lock go"
    finally:
        release.set()
        holder.join(20)
    with implementation.make().locks.try_exclusive(lock) as created:
        assert created is False


# --- FileLocks.exclusive_within (Issue #21, ADR-0009 decision 5) ------------------------------------

def test_waiting_within_a_limit_takes_a_free_lock_and_never_makes_its_directory(implementation,
                                                                               tmp_path):
    locks = implementation.make().locks
    lock = tmp_path / "target" / ".analytics-target.lock"
    with pytest.raises(_platform.LockUnavailable):
        with locks.exclusive_within(lock, 1):
            raise AssertionError("taken without its directory")
    assert not lock.parent.exists()
    lock.parent.mkdir()
    with locks.exclusive_within(lock, 1) as created:
        assert created is True
    with locks.exclusive_within(lock, 1) as created:
        assert created is False
    assert lock.read_bytes() == b""


def test_waiting_within_a_limit_gives_up_busy_and_a_busy_body_is_not_retried(implementation, tmp_path):
    """Contention is retried until the limit, then LockBusy; a LockBusy the body raises goes out
    at once, not mistaken for contention, and the lock is free after it."""
    locks = implementation.make().locks
    lock = tmp_path / "lock"
    with locks.try_exclusive(lock):
        began = time.perf_counter()
        with pytest.raises(_platform.LockBusy):
            with locks.exclusive_within(lock, 0.3):  # another descriptor of this process: busy too
                raise AssertionError("taken while held")
        waited = time.perf_counter() - began
        assert 0.25 <= waited < 5, waited
    calls = []
    with pytest.raises(_platform.LockBusy):
        with locks.exclusive_within(lock, 5):
            calls.append("body")
            raise _platform.LockBusy(0, "the body's own")
    assert calls == ["body"]
    with locks.try_exclusive(lock) as created:
        assert created is False


def test_a_lock_that_cannot_be_taken_at_all_is_unavailable_at_once(implementation, tmp_path):
    """ADR-0009 check 9: a file system without locks answers LockUnavailable, which is never waited
    for; the publisher then goes on without the lock and says so."""
    class Unsupported:
        def __init__(self):
            self.tries = 0

        def try_exclusive(self, path):
            self.tries += 1
            raise _platform.LockUnavailable(errno.ENOLCK, "no locks on this mount")

    locks = Unsupported()
    began = time.perf_counter()
    with pytest.raises(_platform.LockUnavailable):
        with _platform._exclusive_within(locks, tmp_path / "lock", 5):
            raise AssertionError("taken")
    assert locks.tries == 1 and time.perf_counter() - began < 1


def test_waiting_within_a_limit_takes_the_lock_another_process_lets_go(implementation, tmp_path):
    needs(implementation, BETWEEN_PROCESSES)
    import multiprocessing  # noqa: PLC0415 - only this case starts another process

    lock = tmp_path / "lock"
    started, release = multiprocessing.Event(), multiprocessing.Event()
    holder = multiprocessing.Process(target=_hold_by, args=(implementation.name, "exclusive", str(lock),
                                                            started, release))
    holder.start()
    try:
        assert started.wait(30), "the other process never took the lock"
        threading.Timer(0.5, release.set).start()
        began = time.perf_counter()
        with implementation.make().locks.exclusive_within(lock, 20) as created:
            assert created is False
            assert time.perf_counter() - began >= 0.4, "taken while the other process held it"
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


# --- AtomicFiles.publish_new, flush_handle, flush_dir (Issue #13 PR 2, design 6.12, T15) ------------

def test_publishing_a_new_name_puts_the_bytes_there_and_the_temporary_name_goes(implementation,
                                                                                tmp_path):
    files = implementation.make().files
    src, dst = tmp_path / ".mig-0-0.part", tmp_path / "data.csv"
    src.write_bytes(b"a,b\r\n1,2\r\n")
    files.publish_new(src, dst)
    assert dst.read_bytes() == b"a,b\r\n1,2\r\n" and not src.exists()
    src.write_bytes(b"x")
    files.publish_new(str(src), str(tmp_path / "other.csv"))
    assert (tmp_path / "other.csv").read_bytes() == b"x" and not src.exists()


@pytest.mark.parametrize("there", ["file", "directory", "empty file"])
def test_publishing_never_overwrites_what_is_there(implementation, tmp_path, there):
    """design 6.4 (M5): the name taken meanwhile by someone else's file keeps its bytes, and the
    temporary file stays for the ledger to name."""
    files = implementation.make().files
    src, dst = tmp_path / ".mig-0-0.part", tmp_path / "report.csv"
    src.write_bytes(b"the copy")
    if there == "directory":
        dst.mkdir()
    else:
        dst.write_bytes(b"" if there == "empty file" else b"a person's file")
    with pytest.raises(OSError) as raised:
        files.publish_new(src, dst)
    assert isinstance(raised.value, FileExistsError) or there == "directory", raised.value
    assert src.read_bytes() == b"the copy"
    if there == "directory":
        assert dst.is_dir() and not any(dst.iterdir())
    else:
        assert dst.read_bytes() == (b"" if there == "empty file" else b"a person's file")


def test_a_file_system_without_links_is_told_and_nothing_changes(implementation, tmp_path, arming):
    """FAT and exFAT give no second name (not run on a real one): publish_new says so
    (LinkUnsupported) and leaves both names as they were, for the caller's direct write."""
    bundle = implementation.make()
    if implementation.name == "recording":
        bundle.files.links = False
    else:
        needs(implementation, LINKS)

        def no_link(*_args, **_kwargs):
            raise OSError(errno.EPERM, "Operation not permitted")

        arming.setattr(_platform.os, "link", no_link)
    src, dst = tmp_path / ".mig-0-0.part", tmp_path / "x.parquet"
    src.write_bytes(b"bytes")
    with pytest.raises(_platform.LinkUnsupported):
        bundle.files.publish_new(src, dst)
    assert src.read_bytes() == b"bytes" and not dst.exists()


def test_renaming_to_a_new_name_puts_the_bytes_there_and_the_old_name_goes(implementation, tmp_path):
    """rename_new (Issue #13 PR 3, owner's decision of 2026-10-08): the marker's publication where
    the file system has no links — the bytes under the new name, the temporary name gone."""
    needs(implementation, RENAMES)
    files = implementation.make().files
    src, dst = tmp_path / ".analytics-json-0", tmp_path / "analytics.json"
    src.write_bytes(b'{"schema": "analytics.project/2"}\n')
    files.rename_new(src, dst)
    assert dst.read_bytes() == b'{"schema": "analytics.project/2"}\n' and not src.exists()
    src.write_bytes(b"x")
    files.rename_new(str(src), str(tmp_path / "other.json"))
    assert (tmp_path / "other.json").read_bytes() == b"x" and not src.exists()


@pytest.mark.parametrize("destination", ["absent", "empty_directory", "directory", "file"])
def test_directory_rename_between_parents_never_replaces(implementation, tmp_path, destination):
    """Report publication is one exclusive tree rename across parents, including on Windows."""
    needs(implementation, RENAMES)
    files = implementation.make().files
    stage = tmp_path / ".staging" / "attempt"
    stage.mkdir(parents=True)
    (stage / "content").mkdir()
    (stage / "content" / "원문.bin").write_bytes(b"immutable\x00\xff")
    (stage / "manifest.json").write_bytes(b"manifest")
    dst = tmp_path / "revision"
    if destination == "file":
        dst.write_bytes(b"person's existing file")
    elif destination != "absent":
        dst.mkdir()
        if destination == "directory":
            (dst / "existing.bin").write_bytes(b"existing")
    if destination == "absent":
        files.rename_new(stage, dst)
        assert not stage.exists()
        assert (dst / "content" / "원문.bin").read_bytes() == b"immutable\x00\xff"
    else:
        with pytest.raises(FileExistsError):
            files.rename_new(stage, dst)
        assert (stage / "content" / "원문.bin").read_bytes() == b"immutable\x00\xff"
        if destination == "file":
            assert dst.read_bytes() == b"person's existing file"
        elif destination == "directory":
            assert (dst / "existing.bin").read_bytes() == b"existing"
        else:
            assert dst.is_dir() and not any(dst.iterdir())


@pytest.mark.parametrize("there", ["file", "directory", "empty file"])
def test_renaming_never_overwrites_what_is_there(implementation, tmp_path, there):
    """A name taken meanwhile keeps what is there, and the temporary file stays for its ledger line
    to name: FileExistsError, nothing changed (a rename that replaces would lose a person's file)."""
    needs(implementation, RENAMES)
    files = implementation.make().files
    src, dst = tmp_path / ".analytics-json-0", tmp_path / "analytics.json"
    src.write_bytes(b"the marker")
    if there == "directory":
        dst.mkdir()
    else:
        dst.write_bytes(b"" if there == "empty file" else b"a person's file")
    with pytest.raises(OSError) as raised:
        files.rename_new(src, dst)
    assert isinstance(raised.value, FileExistsError) or there == "directory", raised.value
    assert not isinstance(raised.value, _platform.RenameUnsupported)
    assert src.read_bytes() == b"the marker"
    if there == "directory":
        assert dst.is_dir() and not any(dst.iterdir())
    else:
        assert dst.read_bytes() == (b"" if there == "empty file" else b"a person's file")


@pytest.mark.parametrize("why", ["no call", "ENOTSUP", "EINVAL", "ENOSYS"])
def test_a_system_without_the_exclusive_rename_is_told_and_nothing_changes(implementation, tmp_path,
                                                                           arming, why):
    """Where neither the system nor the file system has a rename that refuses a taken name — a C
    library without renameat2, a file system that does not take the flag (an exFAT disk image on
    macOS answered ENOTSUP) — rename_new says so (RenameUnsupported) and changes nothing: the
    migration then stops before its marker rather than writing it in place."""
    if implementation.name == "windows":
        pytest.skip("Windows' rename never replaces: it has no such case")
    bundle = implementation.make()
    if implementation.name == "recording":
        if why != "no call":
            pytest.skip("the stand-in has no C call to fail")
        bundle.files.renames = False
    elif why == "no call":
        bundle.files.exclusive_rename = None
    else:
        import ctypes

        code = getattr(errno, why)

        def refusing(_src, _dst):
            ctypes.set_errno(code)
            return -1

        name = "refusing-" + why
        arming.setitem(_platform._RENAME_CALLS, name, refusing)
        bundle.files.exclusive_rename = name
    src, dst = tmp_path / ".analytics-json-0", tmp_path / "analytics.json"
    src.write_bytes(b"the marker")
    with pytest.raises(_platform.RenameUnsupported):
        bundle.files.rename_new(src, dst)
    assert src.read_bytes() == b"the marker" and not dst.exists()

    tree = tmp_path / ".staging" / "attempt"
    tree.mkdir(parents=True)
    (tree / "content.bin").write_bytes(b"preserved tree")
    target = tmp_path / "revision"
    with pytest.raises(_platform.RenameUnsupported):
        bundle.files.rename_new(tree, target)
    assert (tree / "content.bin").read_bytes() == b"preserved tree" and not target.exists()


def test_flushing_writes_through_and_hides_no_error(implementation, tmp_path):
    files = implementation.make().files
    path = tmp_path / ".mig-0-0.part"
    with open(path, "xb") as fh:
        fh.write(b"bytes")
        files.flush_handle(fh)
        assert path.read_bytes() == b"bytes"
    with pytest.raises(ValueError):
        files.flush_handle(fh)  # closed
    files.flush_dir(tmp_path)
    files.flush_dir(str(tmp_path))
    with pytest.raises(OSError):
        files.flush_dir(tmp_path / "absent")
    with pytest.raises(OSError):
        files.flush_dir(path)  # a file is not a directory


# --- AtomicFiles.rename_dir_new (Issue #21, ADR-0009 decision 5, owner's OQ5 of 2026-10-27) ---------

def _version_tree(root):
    stage = root / ".staging" / "att-0"
    (stage / "pages").mkdir(parents=True)
    (stage / "pages" / "본문.html").write_bytes(b"<p>v</p>")
    (stage / "publication.json").write_bytes(b"{}")
    return stage


def _taken(dst, how):
    if how == "file":
        dst.write_bytes(b"a person's file")
    elif how != "absent":
        dst.mkdir()
        if how == "directory":
            (dst / "index.html").write_bytes(b"a version already there")


def _kept(stage, dst, how):
    assert (stage / "pages" / "본문.html").read_bytes() == b"<p>v</p>"
    if how == "file":
        assert dst.read_bytes() == b"a person's file"
    else:
        assert (dst / "index.html").read_bytes() == b"a version already there"


@pytest.mark.parametrize("how", ["absent", "empty_directory", "directory", "file"])
def test_committing_a_directory_moves_it_only_onto_nothing_or_an_empty_directory(implementation,
                                                                                 tmp_path, how):
    needs(implementation, RENAMES)
    files = implementation.make().files
    stage, dst = _version_tree(tmp_path), tmp_path / "publications" / "pub-0"
    dst.parent.mkdir()
    _taken(dst, how)
    if how in ("directory", "file"):
        with pytest.raises(FileExistsError):
            files.rename_dir_new(stage, dst)
        _kept(stage, dst, how)
        return
    method = files.rename_dir_new(stage, dst)
    assert method == ("rename_new" if how == "absent" else "rename_new_after_rmdir")
    assert not stage.exists() and (dst / "pages" / "본문.html").read_bytes() == b"<p>v</p>"


@pytest.mark.parametrize("how", ["absent", "empty_directory", "directory", "file"])
def test_without_the_exclusive_rename_a_directory_moves_by_posix_rename_never_onto_content(
        implementation, tmp_path, arming, how):
    """Where the file system does not take the flag (some SMB and NFS mounts), POSIX's rename(2)
    stands in for a directory: it never moves one onto entries or a file (OQ5)."""
    if os.name == "nt":
        pytest.skip("Windows' rename never replaces: rename_new is always there, the stand-in's too")
    bundle = implementation.make()
    if implementation.name == "recording":
        bundle.files.renames = False
    else:
        bundle.files.exclusive_rename = None
    stage, dst = _version_tree(tmp_path), tmp_path / "publications" / "pub-0"
    dst.parent.mkdir()
    _taken(dst, how)
    if how in ("directory", "file"):
        with pytest.raises(FileExistsError):
            bundle.files.rename_dir_new(stage, dst)
        _kept(stage, dst, how)
        return
    assert bundle.files.rename_dir_new(stage, dst) == "posix_rename"
    assert not stage.exists() and (dst / "pages" / "본문.html").read_bytes() == b"<p>v</p>"


def test_a_file_system_that_answers_taken_and_free_names_differently_still_falls_back(tmp_path):
    """exFAT on macOS answered EEXIST for a taken name and ENOTSUP for a free one: after the empty
    directory is removed, the second rename_new's RenameUnsupported falls back as the first's does."""
    class Answering:
        def rename_new(self, src, dst):
            if os.path.lexists(dst):
                raise FileExistsError(errno.EEXIST, "taken", str(dst))
            raise _platform.RenameUnsupported(errno.ENOTSUP, "free")

    stage, dst = _version_tree(tmp_path), tmp_path / "publications" / "pub-0"
    dst.mkdir(parents=True)
    assert _platform._rename_dir_new(Answering(), stage, dst, replacing_rename=True) == "posix_rename"
    assert not stage.exists() and (dst / "pages" / "본문.html").read_bytes() == b"<p>v</p>"
    other, again = _version_tree(tmp_path / "w"), tmp_path / "w" / "pub-1"
    again.mkdir()
    with pytest.raises(_platform.RenameUnsupported):
        _platform._rename_dir_new(Answering(), other, again, replacing_rename=False)
    assert (other / "publication.json").exists() and not again.exists()


@pytest.mark.skipif(os.name == "nt", reason="a symbolic link needs a privilege on Windows; a junction is the same rule")
def test_committing_never_removes_or_follows_a_link(implementation, tmp_path):
    """A link (or junction) to an empty directory is taken, not an empty directory: it and what it
    points to stay; a link is never the directory moved."""
    needs(implementation, RENAMES)
    files = implementation.make().files
    stage, dst = _version_tree(tmp_path), tmp_path / "publications" / "pub-0"
    dst.parent.mkdir()
    pointed = tmp_path / "elsewhere"
    pointed.mkdir()
    dst.symlink_to(pointed, target_is_directory=True)
    with pytest.raises(FileExistsError):
        files.rename_dir_new(stage, dst)
    assert dst.is_symlink() and pointed.is_dir() and (stage / "publication.json").exists()
    linked = tmp_path / "linked-stage"
    linked.symlink_to(stage, target_is_directory=True)
    with pytest.raises(NotADirectoryError):
        files.rename_dir_new(linked, tmp_path / "publications" / "pub-1")
    assert linked.is_symlink() and not (tmp_path / "publications" / "pub-1").exists()


def test_an_empty_junction_is_taken_not_emptied(tmp_path, monkeypatch):
    """Windows reports a junction to an empty folder as a directory; is_link says it is a link, and
    the commit leaves it (and what it points to) where it is."""
    class Taken:
        def rename_new(self, src, dst):
            raise FileExistsError(errno.EEXIST, "taken", str(dst))

    stage, dst = _version_tree(tmp_path), tmp_path / "publications" / "pub-0"
    dst.mkdir(parents=True)
    real = _platform.is_link
    monkeypatch.setattr(_platform, "is_link", lambda st: st.st_ino == os.lstat(dst).st_ino or real(st))
    with pytest.raises(FileExistsError):
        _platform._rename_dir_new(Taken(), stage, dst, replacing_rename=True)
    assert dst.is_dir() and (stage / "publication.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows refuses to move a folder holding a file another handle has open")
def test_a_folder_with_a_file_held_open_moves_once_it_is_let_go(implementation, tmp_path):
    """ADR-0009 check 8, measured on the Windows leg: an antivirus, indexer or sync client holding a
    file in the version for a moment is waited for within rename_new's budget, not reported as taken."""
    needs(implementation, RENAMES)
    if implementation.name == "recording":
        pytest.skip("the stand-in renames once, in this process: it has no retry budget to measure")
    files = implementation.make().files
    stage, dst = _version_tree(tmp_path), tmp_path / "publications" / "pub-0"
    dst.parent.mkdir()
    held = open(stage / "publication.json", "rb")  # noqa: SIM115 - let go by the timer
    threading.Timer(0.5, held.close).start()
    began = time.perf_counter()
    assert files.rename_dir_new(stage, dst) == "rename_new"
    assert time.perf_counter() - began < 10 and (dst / "publication.json").read_bytes() == b"{}"


def test_committing_a_file_is_refused_and_nothing_changes(implementation, tmp_path):
    files = implementation.make().files
    src, dst = tmp_path / "manifest.json", tmp_path / "pub-0"
    src.write_bytes(b"{}")
    with pytest.raises(NotADirectoryError):
        files.rename_dir_new(src, dst)
    assert src.read_bytes() == b"{}" and not dst.exists()


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


def test_the_machine_identity_is_text_or_unknown_and_stays_the_same(implementation):
    """Issue #21 (OQ4): the system's own identifier of this machine, or None where it cannot be read;
    the recording stand-in says nothing about the machine."""
    processes = implementation.make().processes
    first, second = processes.machine_identity(), processes.machine_identity()
    assert first == second
    assert first is None or (isinstance(first, str) and first.strip() == first and first)
    if implementation.name == "recording":
        assert first is None
    elif sys.platform in ("darwin", "win32"):
        assert first, "macOS and Windows always have their identifier (IOPlatformUUID, MachineGuid)"
    elif Path("/etc/machine-id").is_file() and Path("/etc/machine-id").read_text(encoding="ascii").strip() not in (
            "", "uninitialized"):
        assert first == Path("/etc/machine-id").read_text(encoding="ascii").strip()


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
                                template_dir=TEMPLATE, origin=core.Origin("test", "sdk"), platform=platform)
    data.import_file(project, source, name="sales", description="test data", origin=core.Origin("test", "sdk"))
    core.record(project, "finding", "구현을 바꿔도 같은 기록", refs=["sales"], origin=core.Origin("test", "sdk"))
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
    assert "sales.parquet" in replaced and "analytics.json" in replaced, calls
    assert ("locks.exclusive", ("lock",)) in calls, calls
    assert ("processes.alive", (DEAD_PID,)) in calls, calls


def test_a_run_goes_through_the_injected_implementation(implementation, tmp_path, arming):
    from aelix_analytics import runner  # noqa: PLC0415 - needs the analysis libraries

    chosen = implementation.make()
    _arm_all_but(arming, chosen)
    calls = _spy(arming, chosen)
    project = core.init_project(tmp_path / "p", title="실행 계약", template_dir=TEMPLATE,
                                origin=core.Origin("test", "sdk"), platform=chosen)
    project.scripts.mkdir(parents=True, exist_ok=True)
    (project.scripts / "01_min.py").write_text("print('ok')\n", encoding="utf-8")
    result = runner.execute(project, script="scripts/01_min.py", title="최소 실행", origin=core.Origin("test", "sdk"))

    assert result["run"]["status"] == "ok", result["run"].get("error")
    # A run swallows a failure after its script (the Workspace, the bookkeeping): a refused port
    # would otherwise show only as a recorded error.
    assert "workspace_error" not in result, result.get("workspace_error")
    assert not result.get("finalize_errors"), result.get("finalize_errors")
    assert not result["run"].get("finalize_errors"), result["run"].get("finalize_errors")
    assert {"runs.guard", "locks.exclusive", "files.replace"} <= {what for what, _ in calls}, calls
    assert "run.json" in [args[-1] for what, args in calls if what == "files.replace"], calls


def test_every_implementation_writes_what_the_process_default_writes(implementation, tmp_path):
    source = write_sales_csv(tmp_path / "sales.csv")
    reference = _flow(tmp_path / "default", source, core.PLATFORM)
    chosen = _flow(tmp_path / "chosen", source, implementation.make())
    assert chosen == reference


# PublicationProjection: model, memory preview and saved-build injection are executable.
# Report/collection capabilities remain explicitly deferred to PR4.
@dataclass(frozen=True)
class ProjectionImplementation:
    id: str
    profile: str
    capabilities: frozenset[str]


PROJECTION_IMPLEMENTATIONS = (
    ProjectionImplementation("read", "read", frozenset({"project", "model", "no_io", "files", "references", "preview", "full_build_injection"})),
    ProjectionImplementation("evidence", "evidence", frozenset({"project", "model", "no_io", "files", "references", "preview", "full_build_injection"})),
    ProjectionImplementation("recording", "read", frozenset({"project", "model", "no_io", "preview", "full_build_injection"})),
)


@pytest.mark.parametrize("implementation", PROJECTION_IMPLEMENTATIONS, ids=lambda i: i.id)
def test_publication_implementation_contract(implementation):
    from publication_doubles import RecordingProjection

    from aelix_analytics.publishing.model import ContentBasis, ItemKey, SnapshotBasis
    from aelix_analytics.publishing.projection import (
        ShareRequest,
        ShareSnapshot,
        SnapshotItem,
        freeze,
        project,
    )
    from aelix_analytics.publishing.scope import PROFILES, scope_closure

    snapshot = ShareSnapshot(SnapshotBasis(0, ContentBasis(0, None, None)), (
        SnapshotItem(ItemKey("project", "project"), "project", freeze({"title": "Public"}), root=True),))
    request = ShareRequest()
    recording = RecordingProjection() if implementation.id == "recording" else None
    result = project(snapshot, request, profile=implementation.profile, implementation=recording)
    assert set(result.ledger.included) <= scope_closure(snapshot, request, PROFILES[implementation.profile])
    assert result == project(snapshot, request, profile=implementation.profile, implementation=recording)
    assert result.model.title.value == "Public"
    if recording:
        assert recording.requests == [request, request]


@pytest.mark.parametrize("implementation", PROJECTION_IMPLEMENTATIONS, ids=lambda i: i.id)
@pytest.mark.parametrize("capability", ["reports", "collection"])
def test_publication_later_capability_is_not_claimed(implementation, capability):
    if capability not in implementation.capabilities:
        pytest.skip(f"{implementation.id}: {capability} is PR4 or the later build adapter, not PR1 acceptance")
    raise AssertionError("new capability needs an actual contract case before it can be advertised")


@pytest.mark.parametrize("implementation", PROJECTION_IMPLEMENTATIONS, ids=lambda i: i.id)
def test_publication_preview_uses_each_injected_implementation(implementation, tmp_path, monkeypatch):
    from publication_doubles import RecordingProjection

    from aelix_analytics import core
    from aelix_analytics.publishing.build import preview_package
    from aelix_analytics.publishing.projection import PROJECTIONS, ShareRequest

    chosen = RecordingProjection() if implementation.id == "recording" else PROJECTIONS[implementation.id]()
    project = core.init_project(tmp_path / "p", title="Public", origin=core.Origin("human", "sdk"))
    for name in PROJECTIONS:
        monkeypatch.setitem(PROJECTIONS, name, lambda: pytest.fail("preview selected a default implementation"))
    summary, files = preview_package(project, profile=implementation.profile, projection=chosen)
    assert summary["profile"]["id"] == implementation.id
    assert f"Profile: {implementation.id}.".encode() in files["index.html"]
    if implementation.id == "recording":
        assert chosen.requests == [ShareRequest()]

# ReportRenderer (Issue19 PR2): production conservative view and boundary-recording stand-in.
# The stand-in delegates format parsing; it proves replacement and input boundaries, not a second
# Markdown implementation. HTML/Office/PDF views remain download-only for both.
@dataclass(frozen=True)
class ReportRendererImplementation:
    id: str
    capabilities: frozenset[str]


REPORT_RENDERER_IMPLEMENTATIONS = (
    ReportRendererImplementation("static", frozenset({"markdown", "text", "download", "no_io"})),
    ReportRendererImplementation("recording", frozenset({"markdown", "text", "download", "no_io"})),
)


@pytest.mark.parametrize("implementation", REPORT_RENDERER_IMPLEMENTATIONS, ids=lambda i: i.id)
def test_report_renderer_replacement_contract(implementation, monkeypatch):
    import inspect
    import socket

    from report_renderer_doubles import RecordingReportRenderer

    from aelix_analytics.report_renderer import ReportRenderer
    from aelix_analytics.report_renderers import StaticReportRenderer

    renderer = RecordingReportRenderer() if implementation.id == "recording" else StaticReportRenderer()
    assert list(inspect.signature(renderer.render).parameters) == ["files", "entry", "paths"]
    assert list(inspect.signature(ReportRenderer.render).parameters) == ["self", "files", "entry", "paths"]
    files, paths = {"한글.md": b"# Exact bytes\r\n"}, {"한글.md": "original/한글.md"}
    # Import the parser before refusing external I/O (Python's module loader reads its source).
    from markdown_it import MarkdownIt
    assert MarkdownIt

    def refuse(*args, **kwargs):
        raise AssertionError("ReportRenderer looked outside its explicit byte input")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    before = dict(files)
    first = renderer.render(files, entry="한글.md", paths=paths)
    assert first == renderer.render(files, entry="한글.md", paths=paths)
    assert first.supported and files == before
    assert set(first.originals) == set(files) and all(isinstance(b, bytes) for b in first.files.values())
    with pytest.raises(TypeError):
        first.files["overwritten"] = b"bad"
    if implementation.id == "recording":
        assert renderer.calls == [(files, "한글.md", paths), (files, "한글.md", paths)]
    fallback = renderer.render({"a.pdf": b"%PDF"}, entry="a.pdf", paths={"a.pdf": "original/a.pdf"})
    assert not fallback.supported and fallback.reasons == ("format_download_only",)
    assert fallback.originals == {"a.pdf": "../original/a.pdf"}


@pytest.mark.parametrize("implementation", PROJECTION_IMPLEMENTATIONS, ids=lambda i: i.id)
def test_publication_saved_build_uses_each_injected_strategy(implementation, tmp_path, monkeypatch):
    import json
    import re

    from publication_doubles import RecordingProjection

    from aelix_analytics.publishing import build, checks
    from aelix_analytics.publishing.projection import (
        PROJECTIONS,
        EvidenceProjection,
        ReadProjection,
        ShareRequest,
    )

    assert "full_build_injection" in implementation.capabilities
    origin = core.Origin("human", "cli")
    p = core.init_project(tmp_path / "project", title="Public", origin=origin)
    baseline = build.build_publication(p, profile=implementation.profile, origin=origin)
    first = json.loads((p.root / baseline["path"] / "publication.json").read_bytes())
    chosen = (
        RecordingProjection()
        if implementation.id == "recording"
        else PROJECTIONS[implementation.profile]()
    )
    selected_method = chosen.project

    def forbidden(*args, **kwargs):
        pytest.fail("saved build fell back to a default strategy")

    monkeypatch.setattr(ReadProjection, "project", forbidden)
    monkeypatch.setattr(EvidenceProjection, "project", forbidden)
    monkeypatch.setattr(chosen, "project", selected_method)
    for name in tuple(PROJECTIONS):
        monkeypatch.setitem(PROJECTIONS, name, forbidden)
    answer = build.build_publication(
        p, profile=implementation.profile, projection=chosen, origin=origin
    )
    manifest = json.loads((p.root / answer["path"] / "publication.json").read_bytes())
    event = next(
        e.value
        for e in core.read_model(p).journal.events
        if e.kind == "publication"
        and e.value["details"]["publication_id"] == answer["publication_id"]
    )
    previous = next(
        e.value
        for e in core.read_model(p).journal.events
        if e.kind == "publication"
        and e.value["details"]["publication_id"] == baseline["publication_id"]
    )
    assert manifest["profile"]["id"] == event["details"]["profile"]["id"] == implementation.id
    assert re.fullmatch(r"pub-[0-9a-hjkmnp-tv-z]{26}", answer["publication_id"])
    assert manifest["basis"]["content_seq"] == first["basis"]["content_seq"]
    assert manifest["basis"]["context_version"] == first["basis"]["context_version"]
    assert (
        manifest["analysis_state"] == first["analysis_state"] and event["refs"] == previous["refs"]
    )
    assert (
        answer["recording_status"] == "complete"
        and checks.verify_package(p.root / answer["path"], binding=event["details"])["ok"]
    )
    if implementation.id == "recording":
        assert chosen.requests == [ShareRequest()]
    assert build.capture_snapshot(p).basis.content.content_seq == first["basis"]["content_seq"]
