"""OS-dependent operations behind small ports, so the rest of the kit has no platform branches.

This module is loaded inside the aelix process together with `core.py` (see
`extension/aelix_lab.py`), so it uses the standard library only and imports nothing from labkit.

Four operations behave differently on POSIX and Windows and are therefore ports: exclusive file
locking, atomic file replacement, child-process control, and the deadline and cancellation of a
run. Each has one implementation per platform, chosen once by `detect()`: `core.PLATFORM` holds this
process's bundle, and a `core.Project` carries it — or another one a test hands it. Call sites use
the bundle their project carries; they do not test `sys.platform` themselves.

Reserved file names, file URLs and text encoding are not ports: one rule that is correct
everywhere is better than a per-platform one, because a project directory is a record that has to
stay readable on another machine.

SUPPORT STATUS (ADR-0002). The POSIX implementations keep the kit's POSIX behaviour except where
docs/CONTRACTS.md lists a change. The Windows implementations are measured by the repository CI on
the configuration docs/CONTRACTS.md F names. What was measured, what is a declared limitation and
what was not run are in docs/CONTRACTS.md G; code being here is not evidence that it works.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol
from urllib.parse import quote_from_bytes

# --- the one exception the ports raise ------------------------------------------------------------


class Interrupted(BaseException):
    """Raised in the main thread by `RunControl` when a deadline expires or a cancel arrives.

    It derives from `BaseException` for the same reason `KeyboardInterrupt` does: analysis code
    catches `Exception` freely and must not be able to swallow its own cancellation."""


# --- ports ----------------------------------------------------------------------------------------


class FileLocks(Protocol):
    """An exclusive lock between processes, held for the body of the context manager.

    `exclusive` blocks until the lock is free, with no timeout. It creates the lock file if needed
    and never truncates it. It is **not re-entrant**: a process that already holds the lock must
    not ask for it again. Release happens when the body ends; a process that dies releases it
    through the operating system. Contention waits; any other operating-system failure is raised."""

    def exclusive(self, path: Path) -> contextlib.AbstractContextManager[None]: ...


class AtomicFiles(Protocol):
    """Replacement of one path by another within the same directory.

    `replace` is atomic for a reader that opens the destination by name: it sees the old bytes or
    the new ones, never a partial write. On Windows a reader that opens the file at the moment it is
    replaced may instead be refused, and `core.read_json` then reads the record as missing (reasoned,
    not run; docs/CONTRACTS.md G). It is not a transaction — a crash between writing the temporary
    file and replacing leaves the temporary file behind. An implementation may retry a documented,
    transient refusal, but it raises the last failure rather than losing the write."""

    def replace(self, src: Path | str, dst: Path | str) -> None: ...


class ProcessControl(Protocol):
    """Starting, observing and stopping child processes.

    These are primitives, not a lifecycle: they do not wait, except `open_document(wait=True)`. The
    caller keeps its own grace period and its own ordering, because the policy differs between the
    extension's abort path and the CLI's.

    `alive` answers about a pid the kit recorded for itself. It is False for a value that cannot be
    a pid, for no such process, and for a process another user owns: the kit records its own pid as
    the project's user, so another owner means the id has been reused. It is True when the operating
    system gives no answer, so a running record is not downgraded on a failed call. It cannot tell a
    reused pid of the same user from the original process — a limitation of pid-based liveness on
    every platform, not of one implementation.

    `spawn_kwargs` go to `subprocess`/`asyncio` so a child starts in its own group. `base_env` is a
    set of variables the platform's programs commonly need; callers add their own on top.

    `interrupt` asks a child to stop so it can record what it was doing. It returns False when it
    could not ask and the child may still be running, so the caller ends it by force; True when it
    asked, or when nothing is left to ask. Each platform asks the one way that works there; see the
    implementations. Where that is the kit's cancel request file (`asks_by_request`), the caller
    creates the file's directory, passes the path to the kit as `LAB_CANCEL_FILE` (the kit watches
    it through `RunControl.watch_for_cancel`) and gives it to `interrupt` as `request`; elsewhere it
    passes None. `kill_tree` ends the process with this pid and its descendants without warning. It
    takes a pid rather than the process object because it **may block**, so an event loop calls it
    off-thread, where an asyncio object must not be touched.

    `open_document` hands a file to the program the desktop opens documents with. `wait=True` runs
    it attached to the caller's terminal and returns its exit status (the human CLI, as `bin/lab`
    does), and raises OSError when there is no such program (FileNotFoundError: none installed).
    `wait=False` starts it detached from the caller's terminal and returns None (aelix's TUI, which
    the opener must not draw on); where the program is missing it hands the file's URL to
    `webbrowser`, as the extension always has — which, without a display, can run a text browser in
    the caller's terminal.

    `find_program` answers where the program to start is, as an absolute path, or None. A name with a
    path separator is a path, checked as it stands (`~` expanded); a bare name is looked up where
    this platform looks for programs, which on Windows is PATH alone — never the working directory,
    where a project's files are. A symbolic link is not followed: a shim (mise's, for one) is a link
    to a program that decides what to be from the name it was started by."""

    asks_by_request: bool

    def alive(self, pid: int | str | None) -> bool: ...

    def spawn_kwargs(self) -> dict: ...

    def base_env(self) -> dict[str, str]: ...

    def interrupt(self, proc, request: Path | None = None) -> bool: ...

    def kill_tree(self, pid: int) -> None: ...

    def open_document(self, path: Path, *, wait: bool) -> int | None: ...

    def find_program(self, name: str) -> str | None: ...


class RunGuard(Protocol):
    """One run's signal handling; see RunControl.guard."""

    cancelled: bool
    expired: bool

    def __enter__(self) -> RunGuard: ...

    def __exit__(self, *exc_info) -> bool: ...

    def interruptible(self, deadline: float | None = None
                      ) -> contextlib.AbstractContextManager[None]: ...

    def settle(self, failure: BaseException | None = None) -> BaseException | None: ...


class RunControl(Protocol):
    """The deadline and the cancellation of a script running in this process.

    `guard()` is entered once around a whole run. It installs the platform's handlers for the
    signals that ask a run to stop, and puts back what it replaced when it exits, raising only once
    everything is back. In between it swaps no handler — except to reassert its own after a script
    replaced one — so a signal can never land in the middle of a swap on the path to the run's
    record.

    What happened is two flags on the guard, and the caller classifies a run by them, not by the
    exception type: `cancelled`, a cancel arrived at some point of the run; `expired`, the
    deadline's signal or its time came while its region could still be interrupted — whether the
    guard's own handler interrupted the region, a script that took the deadline's signal over kept
    it from doing so, or a cancel came after the deadline had already passed. So a timeout means
    the deadline came before the script finished, not that the script stopped, and a region a
    cancel has already interrupted no longer counts its deadline. On POSIX the deadline's signal is
    SIGALRM, and any SIGALRM the guard's handler receives in a region with a deadline is taken for
    it, as main took it: also one another process sent, or the script raised or timed itself. On
    Windows only the guard's own timer counts.

    A handler sets its flag by assignment before it calls anything, so a second signal handled
    inside it — even by a handler the script left installed, which may raise — cannot take the
    flag away. Only the handler's first instruction comes before that. A cancel whose handler a
    raising handler the script left cuts short there is counted by `settle` from what that handler
    raised: its traceback passes through the handler it cut short. `settle` looks for it in the
    region's failure and in everything a handler raised while the region was left, and in what
    those lead to through `__context__`, `__cause__` and an exception group's members (the last
    member first): up to CHAIN_LIMIT exceptions from the failure, and as many again from each
    exception a handler raised. So it is lost when the script catches it and carries on, raises
    something new outside its `except`, or puts it behind more exceptions than that in one of those
    — a long chain, or a large group with it among the first members; and on Windows once the
    region's timer has raised the deadline's SIGINT, when the handler's frame may be the deadline's
    own and the run is a timeout either way. A deadline cut short there is counted by the clock when
    its region is left; a SIGALRM that is not the guard's timer is not.

    Outside an `interruptible()` region a cancel only records; a caller writes its record there.
    Inside one, the guard raises `Interrupted` in the main thread **at most once**: a handler
    decides that it is the one that raises before anything else happens, so a second signal — even
    one handled inside the first handler — only records. A cancel recorded before a region is
    raised as the region is entered. The guard never raises into its own exit: a signal handled
    while a region is being left, or while `settle` runs, only records.

    `interruptible(deadline)` arms the deadline for the region. Leaving a region stops the raising,
    counts a deadline that has passed, disarms and reasserts the guard's handlers. On POSIX,
    disarming cancels ITIMER_REAL whenever the region's arming set it: also when another SIGALRM
    ended the region while that timer, or a periodic one the script set in its place, was still
    running, and when the script caught the guard's interruption and set a timer of its own after
    it — as main cancelled it after every script. A region that set none leaves a host's own timer
    alone. `settle(failure)` does the same again and counts a cancel cut short at its
    handler's entry (above), found in `failure` — what the region raised — or in what handlers
    raised while the region was left and during the settle; the caller runs it after every region
    whatever the region raised. It never raises: it returns, once, the first thing a handler the
    script left installed raised meanwhile.

    The one interruption is spent wherever it lands. A script that catches `BaseException` — or
    calls a library that turns the interrupt into an ordinary error it then catches — is not
    interrupted again, and neither is one that the signal found running a finalizer: CPython
    reports an exception raised in a Python `__del__` or a weakref callback and drops it, and an
    I/O object's own finalizer (a gzip writer, or a zip member being written, left open) drops it
    without a report outside development mode. On every platform a script that installs its own
    handler for these signals receives them instead of the kit. None of this stops a call inside a
    C extension that does not check for signals before it returns.

    `watch_for_cancel(request)` makes this process honour the cancel request file the parent's
    `ProcessControl.interrupt` creates, and returns the function that stops watching."""

    def guard(self) -> RunGuard: ...

    def watch_for_cancel(self, request: Path) -> Callable[[], None]: ...


class Platform(Protocol):
    """The bundle of ports one process uses. `name` is for records and messages only."""

    name: str
    locks: FileLocks
    files: AtomicFiles
    processes: ProcessControl
    runs: RunControl


RESTORE_ATTEMPTS = 8
# Iterated instead of calling range(): a call is a point where a pending signal is handled, and
# the retry loops below must have none outside their `try`.
_ATTEMPTS = tuple(range(RESTORE_ATTEMPTS))
# How many chained exceptions `settle` looks through for a cancel cut short (_count_cut_short).
CHAIN_LIMIT = 64

# How the retry loops below are built. CPython handles a pending signal at a function's entry, at a
# call, at a loop's backward jump and where a generator resumes after `yield` — and can leave a
# backward jump inside a `try` out of the exception table: measured for a `while` loop's back edge
# on 3.12 and 3.13, and on 3.12-3.14 for the jump back from an `if` inside a loop, the shape of the
# loops below. On 3.13 and 3.14 a handler that raises at such a jump then skips the `except`
# (measured with a real signal; 3.12 handles it after the jump, where the `except` still catches
# it). Each loop that must survive a raising handler therefore sits in a function of its own,
# called from inside a `try` in a bounded retry: whatever such a handler raises in it, including at
# its own entry, surfaces at that call. What is left unprotected is the entry of the function
# holding the retry, the retry's own backward jump, which only an exception already caught leads
# to, and a burst that outlasts the retries.


def _swap(sig: int, handler) -> BaseException | None:
    """Make `handler` the handler of `sig`, and return what interrupted that, if anything.

    `signal.signal` runs any pending handler before it changes one, and if that handler raises, the
    change does not happen. The swap is therefore retried, and the interruption is handed back for
    the caller to raise once every handler it is responsible for is in place."""
    if handler is None:
        return None  # installed from C: Python cannot put it back
    raised = None
    for _ in _ATTEMPTS:
        try:
            signal.signal(sig, handler)
            return raised
        except BaseException as exc:  # noqa: BLE001 - handed back, raised after the swaps
            if raised is None:
                raised = exc
    return raised


class _Region:
    """An interruptible region of a guarded run; see RunControl."""

    def __init__(self, guard: _RunGuard, deadline: float | None) -> None:
        self._guard, self._deadline = guard, deadline

    def __enter__(self) -> _Region:
        guard = self._guard
        # An earlier region's deadline marks are not this region's (Windows; see _WindowsGuard).
        guard.deadline_due = guard.deadline_raised = False
        guard.raising = True
        try:
            if guard.cancelled and guard._stop(None):
                raise Interrupted("run cancelled")
            if self._deadline is not None:
                guard.arm(self._deadline)
        except BaseException:
            guard.raising = False  # `with` does not call __exit__ for a region that never started
            raise
        return self

    def __exit__(self, *exc_info) -> bool:
        self._guard._settle()
        return False


class _RunGuard:
    """The platform-independent half of RunControl.guard. A subclass lists its handlers, SIGINT
    last, and supplies arming and disarming of the deadline; `disarm_now` runs inside a signal
    handler and must take no lock.

    Class-based rather than a generator, so nothing of it is finalised later by the garbage
    collector. A signal handled at the entry of an `__exit__` can skip its body; `settle`, which the
    caller runs again whatever happened, and the one-shot rule cover that."""

    def __init__(self) -> None:
        self.raising = False     # inside a region that has not been interrupted yet
        self.cancelled = False   # a cancel arrived at some point of the run
        self.expired = False     # the deadline's signal or its time came in its region (RunControl)
        self.armed = False       # a deadline is running
        self.deadline_at: float | None = None  # when the region's deadline passes (monotonic)
        self.deadline_due = False  # the next SIGINT is the deadline's (Windows); see there
        self.deadline_raised = False  # the region's timer raised the deadline's SIGINT (Windows)
        self.handlers: list[tuple[int, Callable]] = []
        self._replaced: list[tuple[int, object]] = []
        self._installed = False
        # What handlers raised while a region was left, first first: added to with `+=`, which is
        # no check point, so an `except` that keeps one runs no call (see _ATTEMPTS).
        self._caught: tuple[BaseException, ...] = ()
        self._interrupted: BaseException | None = None

    def __enter__(self) -> _RunGuard:
        try:
            for sig, handler in self.handlers:
                # Recorded before the swap: a signal handled in the middle of the installation must
                # not leave a handler that nothing will put back.
                self._replaced.append((sig, signal.getsignal(sig)))
                signal.signal(sig, handler)
        except BaseException:
            self._restore()
            raise
        self._installed = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.raising = False
        self._installed = False
        # Before any previous handler is back: this guard's timer must not reach one of them.
        self.disarm()
        interrupted = self._restore()
        self._caught = ()
        if interrupted is not None and exc_type is None:
            raise interrupted
        return False

    def _restore(self) -> BaseException | None:
        """Put back every handler this guard replaced; return the first thing a handler raised
        meanwhile, for the caller to raise once everything is back.

        Handlers that raise nothing — the default action, ignore, one installed from C — go back
        first, and Python callables after them, each group in installation order: until the first
        callable is back, only this guard's record-only handlers and those are installed, so nothing
        can interrupt the loop. In a CLI that leaves SIGINT's alone at the end. A callable that
        raises while the rest go back is survived by doing the rest again (see _ATTEMPTS)."""
        self._interrupted = None
        for _ in _ATTEMPTS:
            try:
                self._put_back()
                break
            except BaseException as exc:  # noqa: BLE001 - raised by the caller once all are back
                if self._interrupted is None:
                    self._interrupted = exc
        interrupted, self._interrupted = self._interrupted, None
        return interrupted

    def _put_back(self) -> None:
        self._replaced.sort(key=lambda entry: callable(entry[1]))
        while self._replaced:
            sig, previous = self._replaced[0]
            raised = _swap(sig, previous)
            self._replaced.pop(0)
            if raised is not None and self._interrupted is None:
                self._interrupted = raised

    def interruptible(self, deadline: float | None = None) -> _Region:
        return _Region(self, deadline)

    def settle(self, failure: BaseException | None = None) -> BaseException | None:
        for _ in _ATTEMPTS:
            try:
                self._settle()
                # The region's failure, then what the handlers raised: each with a budget of its own.
                self._count_cut_short(failure, *self._caught)
                break
            except BaseException as exc:  # noqa: BLE001 - raised at _settle's own entry; kept
                self._caught += (exc,)
        caught, self._caught = self._caught, ()
        return caught[0] if caught else None

    def _count_cut_short(self, *roots: BaseException | None) -> None:
        """Count a cancel whose handler a raising handler the script left cut short at its first
        instruction, before its assignment (see RunControl): what that handler raised passed
        through the frame of the handler it cut short, so the frame is in its traceback, or in the
        traceback of an exception chained to it or grouped with it. Anywhere else, a frame of this
        guard's cancel handler is one whose assignment already ran, and counting it again changes
        nothing; a handler that also takes the deadline is the exception (_shows_a_cancel).

        Each root is walked up to CHAIN_LIMIT exceptions of its own: with one budget for all of
        them (3.6), a failure with that many chained or grouped exceptions hid what a handler raised
        while the region was left, and, in 3.5's order, the reverse (review 12, rest-4)."""
        for root in roots:
            if root is None:
                continue
            pending, seen = [root], set()
            while pending and len(seen) < CHAIN_LIMIT:
                exc = pending.pop()
                if id(exc) in seen:
                    continue
                seen.add(id(exc))
                tb = exc.__traceback__
                while tb is not None:
                    frame = tb.tb_frame
                    if self._shows_a_cancel(frame.f_code) and frame.f_locals.get("self") is self:
                        self.cancelled = True
                    tb = tb.tb_next
                pending.extend(chained for chained in (exc.__context__, exc.__cause__)
                               if chained is not None)
                # asyncio's TaskGroup, for one, raises what its tasks raised as a group's members.
                if isinstance(exc, BaseExceptionGroup):
                    pending.extend(exc.exceptions)

    def _shows_a_cancel(self, code) -> bool:
        # By identity: `in` on a set hashes the code object, which for a large script's module takes
        # tens of microseconds, and a traceback can hold that frame once for every re-raise.
        return code is _ON_CANCEL_SIGNAL

    def _settle(self) -> None:
        """Stop raising, count a deadline that passed while the region could still be interrupted,
        disarm, and put this guard's handlers back where a script replaced one.

        A handler the script left installed can interrupt any step, which is then done again — the
        count is an assignment, so doing it twice changes nothing — and what it raised is kept for
        `settle`, which looks through all of it and returns the first. Only this function's first
        instruction, and the retry's backward jump after something was caught, are outside that
        (see _ATTEMPTS); the guard's own handlers raise nothing here (see _in_exit)."""
        was_raising, self.raising = self.raising, False
        for _ in _ATTEMPTS:
            try:
                if was_raising and self._deadline_passed():
                    self.expired = True
                self.disarm()
                self.deadline_at = None
                if self._installed:
                    self._reassert()
                return
            except BaseException as exc:  # noqa: BLE001 - kept for settle() to return
                self._caught += (exc,)

    def _reassert(self) -> None:
        for sig, handler in self.handlers:
            if signal.getsignal(sig) is not handler:  # a script replaced it
                signal.signal(sig, handler)

    def _deadline_passed(self) -> bool:
        return self.deadline_at is not None and time.monotonic() >= self.deadline_at

    def _stop(self, frame) -> bool:
        """Whether the signal being handled interrupts the region: once per region — the raising
        stops before this returns, so a signal handled inside this one only records — and never
        inside the guard's own exit, which a signal can reach before the exit has stopped the
        raising; there nothing changes, and the exit's settle, which runs next, decides what the
        region's end counts as. A deadline that passed before this signal counts even though the
        script kept its signal from the guard: it came first."""
        if not self.raising or _in_exit(frame):
            return False
        if self._deadline_passed():
            self.expired = True
        self.raising = False
        self.disarm_now()
        return True

    def on_cancel_signal(self, _signum, frame) -> None:
        self.cancelled = True  # first, and by assignment: see RunControl
        if self._stop(frame):
            raise Interrupted("run cancelled")

    def arm(self, seconds: float) -> None:
        raise NotImplementedError

    def disarm(self) -> None:
        raise NotImplementedError

    def disarm_now(self) -> None:
        raise NotImplementedError


def _in_exit(frame) -> bool:
    """True when `frame` or one of its callers is the guard's own exit: a region being left,
    `settle`, or the guard's `__exit__`."""
    while frame is not None:
        if frame.f_code in _EXIT_CODE:
            return True
        frame = frame.f_back
    return False


# A tuple: `in` compares by identity first, where a set would hash each caller's code object.
_EXIT_CODE = tuple(function.__code__ for function in (
    _Region.__exit__, _RunGuard.__exit__, _RunGuard.settle, _RunGuard._settle))
# The handler whose first statement records a cancel, and whose frame therefore shows a cancel
# that a raising handler the script left cut short at its entry (_RunGuard._count_cut_short).
_ON_CANCEL_SIGNAL = _RunGuard.on_cancel_signal.__code__


# The largest id each platform hands out: `pid_t` is a signed 32-bit integer on POSIX, and a Windows
# process id is a DWORD. A recorded value outside the range names no process.
POSIX_PID_MAX = 2**31 - 1
WINDOWS_PID_MAX = 2**32 - 1


def recorded_pid(pid: object, maximum: int) -> int | None:
    """The pid a record holds, or None when the value cannot be one: not an integer, a bool, zero,
    negative — `os.kill` reads 0 and -1 as process groups — or beyond `maximum`."""
    if isinstance(pid, bool):
        return None
    try:
        value = int(pid)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return value if 0 < value <= maximum else None


# --- POSIX ------------------------------------------------------------------------------------


class PosixFileLocks:
    @contextlib.contextmanager
    def exclusive(self, path: Path) -> Iterator[None]:
        import fcntl  # noqa: PLC0415 - absent on Windows, so it is imported with its user

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+b") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


class PosixAtomicFiles:
    def replace(self, src: Path | str, dst: Path | str) -> None:
        os.replace(src, dst)


def _absolute(path) -> str:
    """The path made absolute as it stands. os.path.abspath would also drop `..` by its text, but
    after a symbolic link `..` is the parent of the link's target: that would name another file."""
    path = os.fspath(path)
    return path if os.path.isabs(path) else os.path.join(os.getcwd(), path)


class PosixProcessControl:
    # The ask is a signal (see interrupt), so the kit is given no request file.
    asks_by_request = False

    def __init__(self, opener: str = "xdg-open") -> None:
        # The desktop's document opener: `open` on macOS, `xdg-open` elsewhere; detect() chooses.
        # Not whichever `open` is on PATH: on a Linux system that can be another program (Debian's
        # kbd once installed openvt under that name).
        self.opener = opener

    def alive(self, pid: int | str | None) -> bool:
        value = recorded_pid(pid, POSIX_PID_MAX)
        if value is None:
            return False  # also keeps 0 and -1 away from os.kill, which read them as groups
        try:
            os.kill(value, 0)
        except OSError:
            # ESRCH: no such process. EPERM: another user's process holds the id now.
            return False
        return True

    def spawn_kwargs(self) -> dict:
        return {"start_new_session": True}

    def base_env(self) -> dict[str, str]:
        return {}

    def interrupt(self, proc, request: Path | None = None) -> bool:
        # SIGINT to the child only; a `uv run` wrapper forwards it. The C-level handler that
        # receives it needs no GIL, so it also reaches a main thread inside C code that holds the
        # GIL and checks for signals (a catastrophic regular expression), which a Python thread
        # watching a request file could not (Issue #8 measurement: 0.08 s against 18.9 s).
        #
        # A child that has exited leaves nothing to ask, so the caller goes to its bounded wait
        # rather than to a forced end. One the event loop knows has exited is not signalled: its
        # pid may be free. One that exited and was reaped before the loop noticed gives ESRCH.
        if proc.returncode is not None:
            return True
        try:
            os.kill(proc.pid, signal.SIGINT)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return True

    def kill_tree(self, pid: int) -> None:
        # The group, by the child's own pid: `spawn_kwargs` starts it in a new session, so its
        # process group id is its pid. `os.getpgid` cannot be used, because this is also called
        # once the wrapper has been reaped while a descendant still holds the pipes — which is
        # exactly the case this exists to end — and by then the leader is gone.
        #
        # The caller sends this only while its wait is unfinished: the wrapper has not exited, or
        # something still holds its pipes. If that is the wrapper or a member of its group, the
        # group exists and the kernel will not give its id to anything else. The remaining case:
        # once the group has no members left, a pipe holder outside it — during a run, a process
        # forked without exec that called setsid — keeps the wait unfinished, this cannot reach it,
        # and the group id could in principle have been taken by an unrelated group in between. A
        # residual risk, not a promise.
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(pid, signal.SIGKILL)

    def open_document(self, path: Path, *, wait: bool) -> int | None:
        program = shutil.which(self.opener)
        if program is None:
            if wait:
                raise FileNotFoundError(errno.ENOENT, f"{self.opener} not found", self.opener)
            import webbrowser  # noqa: PLC0415 - only this fallback needs it

            webbrowser.open(Path(path).resolve().as_uri())
            return None
        argv = [program, os.fspath(path)]
        if wait:
            return subprocess.call(argv)
        # Its own session, and none of the caller's descriptors: neither the opener nor a text
        # browser it may start can read the caller's keyboard, draw on its screen, or get its
        # terminal's signals.
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return None

    def find_program(self, name: str) -> str | None:
        if "/" in name:
            path = Path(name).expanduser()
            return _absolute(path) if path.is_file() and os.access(path, os.X_OK) else None
        found = shutil.which(name)
        return _absolute(found) if found else None


class _PosixGuard(_RunGuard):
    def __init__(self) -> None:
        super().__init__()
        self.timer_set = False  # the region's arming set ITIMER_REAL, which disarming cancels
        self.handlers = [(signal.SIGTERM, self.on_cancel_signal), (signal.SIGALRM, self._on_alarm),
                         (signal.SIGINT, self.on_cancel_signal)]

    def _on_alarm(self, _signum, frame) -> None:
        # Any SIGALRM in a region with a deadline is taken for it (see RunControl).
        if self.armed and self.raising:
            self.expired = True  # first, and by assignment: see RunControl
        if not self.armed:
            return  # an alarm that arrives after the region disarmed has nothing left to stop
        self.armed = False
        if self._stop(frame):
            raise Interrupted("run deadline expired")

    def arm(self, seconds: float) -> None:
        # SIGALRM interrupts a blocking read or a long call inside DuckDB, which a timer thread
        # could not: the handler runs in the main thread as soon as the kernel returns. The clock's
        # deadline is taken before the timer starts, so it is never later than the kernel's: where
        # only the clock can count a deadline (settle, a cancel's _stop), the kernel's alarm has
        # passed it. `armed` and `timer_set` are set before the timer starts, so a signal handled
        # right after it starts disarms it.
        seconds = max(1.0, float(seconds))
        self.deadline_at = time.monotonic() + seconds
        self.armed = self.timer_set = True
        try:
            signal.setitimer(signal.ITIMER_REAL, seconds)
        except (OverflowError, OSError):
            # A deadline the timer cannot hold (`--timeout inf`): nothing was set, and a host's own
            # timer is left alone. The caller records the failure, as main did.
            self.armed = self.timer_set = False
            self.deadline_at = None
            raise

    def disarm(self) -> None:
        # The timer the region's arming set, whether or not its own SIGALRM came: the one the guard
        # took for the deadline may have been another process's, the script's, or a tick of a
        # periodic timer the script set in its place; and a script that caught the guard's
        # interruption may have set its own since. Main cancelled ITIMER_REAL after every script. A
        # host's own timer is left alone when arming set none. The mark goes only once the timer is
        # off: a raising handler run at the call leaves it for the retry that follows.
        self.armed = False
        if self.timer_set:
            signal.setitimer(signal.ITIMER_REAL, 0)
            self.timer_set = False

    def disarm_now(self) -> None:
        # Inside the handler that interrupts the region: the timer stops now, and the mark stays for
        # the region's exit (disarm). An assignment and a system call: safe inside a handler.
        self.armed = False
        if self.timer_set:
            signal.setitimer(signal.ITIMER_REAL, 0)


class PosixRunControl:
    def guard(self) -> _PosixGuard:
        return _PosixGuard()

    def watch_for_cancel(self, request: Path) -> Callable[[], None]:
        # Nothing to watch: the parent asks with SIGINT (PosixProcessControl.interrupt).
        return lambda: None


# --- Windows ------------------------------------------------------------------------------------

# Written from the documented behaviour of msvcrt, subprocess, CPython and the Win32 API, and
# corrected by the Issue #8 measurements on Windows Server 2025. What has run on Windows and what
# has not is in docs/CONTRACTS.md G, not here.

WINDOWS_ENV = ("SystemRoot", "SystemDrive", "windir", "COMSPEC", "PATHEXT", "TEMP", "TMP",
               "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
               "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
# `_locking` reports contention as EACCES, and an exhausted LK_LOCK retry as EDEADLOCK. Everything
# else it can report (EBADF, EINVAL) is a permanent failure that must not be retried. EDEADLOCK is
# a Windows spelling; macOS and Linux only have EDEADLK, so this module stays importable there.
LOCK_CONTENTION_ERRNOS = frozenset({errno.EACCES,
                                    getattr(errno, "EDEADLOCK", errno.EDEADLK)})
LOCK_POLL_SECONDS = 0.05
# The refusals a concurrent reader can cause. Replacing a file another process holds open fails with
# ERROR_ACCESS_DENIED (5) — measured on Windows Server 2025, where it was the only one seen — and the
# sharing and lock violations (32, 33) are what the documentation names for the same collision.
# ERROR_ACCESS_DENIED is also what a real permission problem gives, and the two cannot be told apart,
# so a permanent denial is raised only once the budget below is spent, about two seconds later.
REPLACE_RETRY_WINERRORS = frozenset({5, 32, 33})
REPLACE_ATTEMPTS = 40
REPLACE_POLL_SECONDS = 0.05
KILL_TREE_TIMEOUT_SECONDS = 10
# taskkill's exit status when no process has the pid. `kill_tree` runs while the caller's wait is
# unfinished, so this means the wrapper had exited by the time taskkill looked, and a descendant that
# outlived it may still hold the pipes — `/T` finds descendants through their parent, and one whose
# parent has exited is not found (Issue #8 measurement). It is reported, not taken as success; the
# caller's bounded wait shows whether anything still holds the pipes.
TASKKILL_NOT_FOUND = 128
# How long the processes a failed taskkill named are looked at again before the failure is
# reported (see WindowsProcessControl._named_have_ended). `taskkill /F` does not wait for what it
# ends, so a process it ended a moment ago may still be on its way out.
KILL_TREE_SETTLE_SECONDS = 2
KILL_TREE_POLL_SECONDS = 0.05


class WindowsFileLocks:
    """What has and has not been run on Windows is in docs/CONTRACTS.md G, not here."""

    @contextlib.contextmanager
    def exclusive(self, path: Path) -> Iterator[None]:
        import msvcrt  # noqa: PLC0415 - absent on POSIX, so it is imported with its user

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+b") as fh:
            # `_locking` locks a byte range from the current position, so every holder has to
            # agree on the same one; append mode starts at the end of the file, hence the seek.
            # LK_LOCK gives up after ten seconds, which is a timeout this lock does not have, so
            # the non-blocking mode is retried instead — but only for contention.
            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in LOCK_CONTENTION_ERRNOS:
                        raise
                    time.sleep(LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


class WindowsAtomicFiles:
    """What has and has not been run on Windows is in docs/CONTRACTS.md G, not here."""

    def replace(self, src: Path | str, dst: Path | str) -> None:
        # Windows refuses to replace a file another process still has open, and the report and
        # `status` read the records a write is replacing. Retrying turns that collision into a
        # short wait. It is a mitigation with a stated budget, not a guarantee: any other failure
        # is raised at once, and an exhausted budget raises the real one.
        for _ in range(REPLACE_ATTEMPTS - 1):
            try:
                os.replace(src, dst)
                return
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in REPLACE_RETRY_WINERRORS:
                    raise
                time.sleep(REPLACE_POLL_SECONDS)
        os.replace(src, dst)


class WindowsProcessControl:
    """What has and has not been run on Windows is in docs/CONTRACTS.md G, not here."""

    # The kit is asked through its request file (see interrupt).
    asks_by_request = True

    def _kernel32(self):
        import ctypes  # noqa: PLC0415 - only this implementation needs it
        from ctypes import wintypes  # noqa: PLC0415

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # ctypes defaults every result to a C int, which truncates a 64-bit HANDLE.
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        return ctypes, kernel32

    def alive(self, pid: int | str | None) -> bool:
        # `os.kill(pid, 0)` is not an existence check on Windows: signal 0 is CTRL_C_EVENT there.
        # `GetExitCodeProcess` is not one either — its STILL_ACTIVE is the ordinary value 259, so
        # a process whose exit code really was 259 reads as running. Waiting on the handle has no
        # such ambiguity.
        pid_value = recorded_pid(pid, WINDOWS_PID_MAX)
        if pid_value is None:
            return False
        # Not alive when no process has this id, or another user's does (see ProcessControl). No
        # answer is an answer this port does not have, and does not downgrade a running record.
        return self._look(pid_value) in ("running", "no answer")

    def _look(self, pid: int) -> str:
        """What Windows says of a pid: "running", "exited", "no process" (none has the id),
        "another user's" (it cannot be opened) or "no answer"."""
        ctypes, kernel32 = self._kernel32()
        synchronize, wait_timeout, wait_failed = 0x00100000, 0x00000102, 0xFFFFFFFF
        error_access_denied, error_invalid_parameter = 5, 87
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            return ("no process" if error == error_invalid_parameter else
                    "another user's" if error == error_access_denied else "no answer")
        try:
            result = kernel32.WaitForSingleObject(handle, 0)
            if result == wait_failed:
                return "no answer"  # the wait itself failed: that is not evidence the process exited
            return "running" if result == wait_timeout else "exited"
        finally:
            kernel32.CloseHandle(handle)

    def spawn_kwargs(self) -> dict:
        # Its own group, with Ctrl+C disabled in it: a Ctrl+C typed into aelix's console does not
        # reach the child, as `start_new_session` keeps a terminal's SIGINT away from it on POSIX.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    def base_env(self) -> dict[str, str]:
        # Python and uv start without these (measured on Windows Server 2025), but Windows programs
        # need some of them at run time — Winsock needs SystemRoot — which is reasoned, not
        # measured. The caller's allow-list is about what a run may see, not about what Windows
        # needs.
        return {name: os.environ[name] for name in WINDOWS_ENV if name in os.environ}

    def interrupt(self, proc, request: Path | None = None) -> bool:
        # No console event: CTRL_BREAK_EVENT is the one that can be aimed at a single group, and sent
        # to a `uv run` group it ends uv at once and leaves uv's child running, unrecorded and out
        # of `taskkill /T`'s reach (Issue #8 measurement). The kit is asked through its request
        # file instead (WindowsRunControl.watch_for_cancel), which reaches it through uv — also
        # once uv itself has exited. A child that is not the kit has no request, and the caller
        # ends its tree with `kill_tree`, unless it has exited and there is nothing left to ask.
        if request is not None:
            with contextlib.suppress(OSError):
                Path(request).touch()
                return True
        return proc.returncode is not None

    def open_document(self, path: Path, *, wait: bool) -> int | None:
        # The file goes to the program associated with it, and this returns once that program has
        # the request: there is nothing to wait for and no status.
        os.startfile(os.fspath(path))  # type: ignore[attr-defined]  # Windows only
        return 0 if wait else None

    def find_program(self, name: str) -> str | None:
        # PATH only, and only an .exe. shutil.which and CreateProcess (for a bare name) look in the
        # working directory first — 3.11's shutil.which always does — and a program there would
        # come from a project's files. A .bat or .cmd runs through cmd.exe, which parses its
        # arguments again, by rules list2cmdline does not quote for (`&` in a title).
        if "/" in name or "\\" in name:
            path = Path(name).expanduser()
            return (os.path.abspath(path) if path.is_file() and path.suffix.lower() == ".exe"
                    else None)
        suffix = os.path.splitext(name)[1].lower()
        if suffix not in ("", ".exe"):
            return None
        filename = name if suffix else name + ".exe"
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            entry = entry.strip().strip('"')
            # A relative entry is a directory relative to wherever the caller happens to be.
            if not entry or not os.path.isabs(entry):
                continue
            candidate = Path(entry) / filename
            if candidate.is_file():
                return os.path.abspath(candidate)
        return None

    def system_program(self, name: str) -> str:
        """A program of Windows itself, by its absolute path in the system directory. A bare name is
        looked up in the caller's working directory before System32 (CreateProcess), and the
        extension's working directory is often a project's (see find_program)."""
        try:
            import ctypes  # noqa: PLC0415 - only this implementation needs it

            buffer = ctypes.create_unicode_buffer(32768)
            length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
            if 0 < length < len(buffer):
                return os.path.join(buffer.value, name)
        except (AttributeError, OSError):
            pass
        return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", name)

    def kill_tree(self, pid: int) -> None:
        # Killing the process leaves its children running, and the child here is `uv`, which has
        # one. `taskkill /T` is the documented way to end the tree. This blocks, so an event loop
        # calls it off-thread (see ProcessControl). The pid cannot have been reused: the caller
        # sends this only while its wait is unfinished, so it still holds the process handle.
        #
        # A failure is reported rather than swallowed, because the caller cannot tell from a
        # silent return whether the descendants are gone: they may still be holding the pipes it
        # is about to wait on. The wrapper itself is then ended directly, so at least it does not
        # outlive the call; its descendants may, and docs/CONTRACTS.md says the run can stay
        # incomplete. A failure taskkill gives only for processes that had ended by themselves is
        # not one, so what it failed on is looked at again first (_named_have_ended): a bounded
        # part of this call's blocking, not a grace period — nothing is asked to stop.
        problem, gone = None, False
        try:
            done = subprocess.run([self.system_program("taskkill.exe"), "/F", "/T", "/PID", str(pid)],
                                  capture_output=True, timeout=KILL_TREE_TIMEOUT_SECONDS,
                                  check=False, **text_kwargs())
            if done.returncode == TASKKILL_NOT_FOUND:
                gone = True
                problem = ("the wrapper had already exited when taskkill looked; a descendant of "
                           "it may still be running")
            elif done.returncode != 0 and not self._named_have_ended(pid, done.stderr):
                problem = f"taskkill exited {done.returncode}: {(done.stderr or '').strip()[:200]}"
        except (OSError, subprocess.SubprocessError) as exc:
            problem = f"{type(exc).__name__}: {exc}"
        if problem:
            print(f"labkit: could not end the process tree of pid {pid}; its descendants may "
                  f"still be running ({problem})", file=sys.stderr)
        if problem and not gone:
            # At least the wrapper, by TerminateProcess. Not after 128: that process has exited.
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)

    def _named_have_ended(self, pid: int, errors: str | None) -> bool:
        """Whether a taskkill that failed left running none of the processes it failed on. `/T`
        lists the tree before it ends it, so a process that exits in between — a launcher whose
        own child had just been ended — is one taskkill says it could not end, though it is gone
        (Issue #38: exit 255, "There is no running instance of the task", on Windows Server 2025).

        Every number in the errors is taken for a pid, because their words are in the system's
        language; each process taskkill could not end is named there with its parent. Those and
        the wrapper are looked at again until all have ended or KILL_TREE_SETTLE_SECONDS pass. This
        process is left out: it is the wrapper's parent, which taskkill names as such. A number that
        is not a pid in the tree only adds a process to look at. The report stays when the errors
        hold no number that can be a pid, and when a process looked at is still there or cannot be
        seen — another user's is what taskkill's "Access is denied" leaves. The processes taskkill
        reported ended are not looked at, as after a taskkill that succeeded, and a descendant it
        never found is the limit docs/CONTRACTS.md G states."""
        named = {value for digits in re.findall(r"\d+", errors or "")
                 if (value := recorded_pid(digits, WINDOWS_PID_MAX)) is not None}
        named.discard(os.getpid())
        if not named:
            return False
        waiting = named | {pid}
        deadline = time.monotonic() + KILL_TREE_SETTLE_SECONDS
        while True:
            waiting = {each for each in waiting if self._look(each) not in ("exited", "no process")}
            if not waiting:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(KILL_TREE_POLL_SECONDS)


CANCEL_POLL_SECONDS = 0.1
CANCEL_RETRY_SECONDS = 0.5


def raise_sigint_in_main() -> bool:
    """Windows: interrupt the main thread from any thread the way Ctrl+C would, or return False.

    `raise()` runs CPython's C handler in the calling thread, and on Windows that handler also sets
    the event a sleeping main thread waits on, so the sleep returns; `_thread.interrupt_main` only
    sets the flag, and a sleep ran on to its end (Issue #8 measurement). It declines when SIGINT is
    ignored or at its default action, because raising it then would do nothing or end the process.
    Reasoned, not run: the check and the raise are two calls, so a script that sets the default
    action in between would have its process ended; and the CRT resets SIGINT to the default action
    while it runs a handler, until CPython's handler reinstalls itself."""
    if not callable(signal.getsignal(signal.SIGINT)):
        return False
    signal.raise_signal(signal.SIGINT)
    return True


class _WindowsGuard(_RunGuard):
    def __init__(self) -> None:
        super().__init__()
        # SIGINT is the request file's interrupt and a console's Ctrl+C; SIGBREAK a Ctrl+Break.
        # SIGTERM exists but is never delivered: `os.kill(pid, SIGTERM)` on Windows terminates the
        # process without running a handler, so a forced end leaves the run `incomplete`.
        breaks = [(signal.SIGBREAK, self.on_cancel_signal)] if hasattr(signal, "SIGBREAK") else []
        self.handlers = [*breaks, (signal.SIGINT, self._on_sigint)]
        self._lock = threading.RLock()  # see arm and disarm
        self._timer: threading.Timer | None = None

    def _on_sigint(self, _signum, frame) -> None:
        # The deadline arrives as a SIGINT too. Its timer counts it (`expired`) and marks the next
        # SIGINT as the deadline's (`deadline_due`) before raising one, so the count does not wait
        # for this handler. While the mark is set, a SIGINT that reaches this handler is the
        # deadline's: one the timer raised as the region ended must not be taken for a cancel, and
        # if a script's own handler took the timer's SIGINT, the next one here interrupts as the
        # deadline — which has passed. Inside the guard's own exit the mark is left alone; after the
        # region it is used up here; a later region resets it (_Region).
        if not self.deadline_due:
            self.cancelled = True  # first, and by assignment: see RunControl
            if self._stop(frame):
                raise Interrupted("run cancelled")
            return
        if self._stop(frame):
            self.deadline_due = False
            raise Interrupted("run deadline expired")
        if not self.raising and not _in_exit(frame):
            self.deadline_due = False

    def arm(self, seconds: float) -> None:
        seconds = max(1.0, float(seconds))
        if not seconds <= threading.TIMEOUT_MAX:
            # A Timer this long would fail in its own thread and leave the region without one.
            raise OverflowError("timeout value is too large")
        # A lock of the region's own: one an earlier region's exit left held (disarm) must not keep
        # this region's timer from firing.
        self._lock = threading.RLock()
        self.armed = True
        self.deadline_at = time.monotonic() + seconds
        timer = threading.Timer(seconds, self._fire)
        timer.args = (timer,)  # so it can tell whether it is still the region's (_fire)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _fire(self, timer: threading.Timer) -> None:
        """The deadline, in the timer's thread."""
        with self._lock:
            # Nothing between the test and the assignments lets the main thread run. Either the
            # region is still interruptible, and the deadline counts here, or it was left or
            # interrupted first; then whatever did that counted the deadline if it had already
            # passed (_settle, _stop) — this timer can fire late, held up by C code that keeps the
            # GIL. A timer the disarm dropped without cancelling it is no longer the region's: it
            # must not reach a region armed after it.
            if timer is not self._timer or not (self.armed and self.raising):
                return
            self.armed = False
            self.expired = True
            self.deadline_raised = True
            self.deadline_due = True
            # There is no SIGALRM. A script that has taken SIGINT over receives this instead of the
            # kit (the run is still `timeout`, see RunControl); one that ignores SIGINT is not
            # interrupted; and a call inside C code that holds the GIL keeps this timer from
            # running until it returns (docs/CONTRACTS.md G).
            raise_sigint_in_main()

    def disarm(self) -> None:
        # Dropped before it is cancelled. `Timer.cancel` is Python code: a raising handler the
        # script left, run inside it, can leave the timer's own lock held, and a retry that
        # cancelled the same timer again would wait on that lock for ever. A timer left running
        # blocks on it, or finds that it is no longer the region's (_fire) and does nothing.
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        # `Timer.cancel` does not stop a callback that has started, so disarming takes the same
        # lock: either the callback finished before this, or it finds the deadline disarmed. The
        # lock is re-entrant: a raising handler run between a `with`'s acquiring and its block —
        # CPython 3.14 checks for signals there, 3.12 and 3.13 under a profile or trace function
        # — leaves it held by this thread, and the retry (_settle) takes it again instead of
        # waiting for ever. Only a timer thread whose timer was dropped can be kept waiting on it:
        # this region's, or, when this region has no deadline and so no lock of its own (arm),
        # the last timed region's.
        with self._lock:
            self.armed = False

    def disarm_now(self) -> None:
        # Inside a handler: no lock (the main thread may hold it) and no Event.
        self.armed = False

    def _shows_a_cancel(self, code) -> bool:
        # `_on_sigint` records a cancel first too, but it also takes the deadline's SIGINT: once the
        # region's timer has raised it (`deadline_raised`), a frame of the handler may be the
        # deadline's, and the run is a timeout whatever it is.
        return super()._shows_a_cancel(code) or (code is _ON_SIGINT and not self.deadline_raised)


_ON_SIGINT = _WindowsGuard._on_sigint.__code__


class WindowsRunControl:
    """What has and has not been run on Windows is in docs/CONTRACTS.md G, not here."""

    def guard(self) -> _WindowsGuard:
        return _WindowsGuard()

    def watch_for_cancel(self, request: Path) -> Callable[[], None]:
        """A daemon thread that turns the request file into a SIGINT in the main thread: looked for
        at once and every CANCEL_POLL_SECONDS, delivered once, and retried while SIGINT is ignored
        or at its default action, in case the script hands it back. The returned function stops it,
        under the same lock as a delivery, so nothing is delivered after it returns."""
        stop = threading.Event()
        lock = threading.Lock()

        def watch() -> None:
            while not request.exists():
                if stop.wait(CANCEL_POLL_SECONDS):
                    return
            while True:
                with lock:
                    if stop.is_set() or raise_sigint_in_main():
                        return
                if stop.wait(CANCEL_RETRY_SECONDS):
                    return

        def stop_watching() -> None:
            with lock:
                stop.set()

        threading.Thread(target=watch, name="labkit-cancel-request", daemon=True).start()
        return stop_watching


# --- assembly -------------------------------------------------------------------------------------


class _Bundle:
    def __init__(self, name: str, locks, files, processes, runs) -> None:
        self.name = name
        self.locks = locks
        self.files = files
        self.processes = processes
        self.runs = runs

    def __repr__(self) -> str:
        return f"<Platform {self.name}>"


def detect(name: str | None = None) -> Platform:
    """The bundle for this platform, or for `name` ('posix' or 'windows') when it is given.

    `core.PLATFORM` calls this once, when core is imported, for this process; a test calls it with
    a name to build the other platform's bundle. The POSIX bundle's document opener follows this
    machine (`open` on macOS, `xdg-open` elsewhere); a test that wants the other one builds
    `PosixProcessControl(opener)` itself. Nothing chooses an implementation by reading the
    environment or by importing a module named in a record. Building a bundle does not
    import the operating-system module it will need, so the wiring can be asserted from either
    platform — constructing the Windows bundle on Linux proves nothing about Windows."""
    chosen = name or ("windows" if os.name == "nt" else "posix")
    if chosen == "windows":
        return _Bundle("windows", WindowsFileLocks(), WindowsAtomicFiles(),
                       WindowsProcessControl(), WindowsRunControl())
    if chosen == "posix":
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        return _Bundle("posix", PosixFileLocks(), PosixAtomicFiles(), PosixProcessControl(opener),
                       PosixRunControl())
    raise ValueError(f"unknown platform {name!r}; expected 'posix' or 'windows'")


# --- rules that are the same everywhere -----------------------------------------------------------

# The device names Windows reserves, as CPython 3.13's ntpath lists them: the four classic ones, the
# two console aliases, and the serial and parallel ports 1-9 together with the superscript digits
# Windows treats as the same ports. Which of them a given Windows build still reserves when an
# extension follows varies — Windows Server 2025 stores `con.txt` and `nul.parquet` as ordinary
# files (Issue #8 measurement) — so this keeps ntpath's cautious rule: a record has to open on every
# version. A new name is refused only where a person or agent chooses a new dataset or script name;
# see core.reject_unportable_name.
RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                            *(f"COM{c}" for c in "123456789\u00b9\u00b2\u00b3"),
                            *(f"LPT{c}" for c in "123456789\u00b9\u00b2\u00b3")})
# Characters Windows forbids in a path component, plus the control range.
UNPORTABLE_CHARS_RE = re.compile(r'[<>:"|?*\\\x00-\x1f]')
# Windows (CPython 3.12+ ntpath) reads any one character before a colon at the start of a path as a
# drive, `é:` and `1:` as well as `C:`.
_DRIVE_RE = re.compile(r"^[^\\/]:")
_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
# How the kit records an absolute Windows path: `str()` of a resolved path, with backslashes.
_RECORDED_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:\\")
_UNC_RE = re.compile(r"^\\\\[^\\/]+\\[^\\/]+")
TEXT_ENCODING = "utf-8"


def reserved_name(name: str) -> bool:
    """True for a file name Windows may treat as a device rather than a file.

    ntpath's rule: the part before the first dot, trailing spaces removed, compared without case.
    A leading space belongs to the name, so ` con.txt` is an ordinary file."""
    return str(name).partition(".")[0].rstrip(" ").upper() in RESERVED_NAMES


def portable_name(name: str) -> bool:
    """True for a file name that means the same thing on Linux, macOS and Windows."""
    text = str(name)
    if not text or text in (".", "..") or reserved_name(text):
        return False
    if UNPORTABLE_CHARS_RE.search(text) or "/" in text:
        return False
    # Windows drops a trailing dot or space from a name, so two records would collide.
    return text[-1] not in ". "


# A chart id is always a slug, so it holds only [a-z0-9-] (core.slugify). An underscore therefore
# cannot appear in one, which is what makes the mapping below injective over chart ids: no reserved
# id can be turned into a name another id already has. A suffix such as "-file" would not be, because
# `con-file` is a perfectly ordinary id and both would land on the same file.
UNPORTABLE_SUFFIX = "_"


def portable_stem(stem: str) -> str:
    """`stem`, or a variant of it that is a usable file name on Windows.

    Used where the name on disk may differ from the identity in the record: a chart keeps the id it
    was recorded with, and only the file it is written to has to be openable everywhere. Injective
    over chart ids, which cannot contain `_`; not in general (`con` and `con_` both give `con_`), so
    it must not be applied to names that may hold an underscore, such as dataset names."""
    text = str(stem)
    if reserved_name(text):
        return f"{text}{UNPORTABLE_SUFFIX}"
    return text


def _windows_absolute(text: str) -> str | None:
    """`text` as a Windows absolute path a link can name, or None when it is not one.

    The extended-length prefixes are dropped, so `\\\\?\\C:\\x` is `C:\\x` and
    `\\\\?\\UNC\\srv\\share\\x` is `\\\\srv\\share\\x`. What remains has to be a drive path or a UNC
    path with both a server and a share; the device namespace (`\\\\.\\`) names nothing to open."""
    if text[:8].upper() == "\\\\?\\UNC\\":
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    if _DRIVE_ABSOLUTE_RE.match(text):
        return text
    if _UNC_RE.match(text) and not text.startswith(("\\\\.\\", "\\\\?\\")):
        return text
    return None


def absolute_record_path(path: str) -> bool:
    """True for a recorded path that is absolute on the platform that recorded it.

    `os.path.isabs` answers for the platform running now, so a Windows path read on Linux would be
    treated as relative and linked as if it sat inside the report. The kit records a path outside
    the project as `str()` of the resolved path: `/…` on POSIX, `X:\\…` or `\\\\server\\share…` on
    Windows. Those are absolute here, whether or not a link can be made to them (see file_url).
    `X:/…` is not: the kit never records a Windows path that way, and on POSIX it is a relative
    path under a directory named `X:`."""
    text = str(path)
    return text.startswith(("/", "\\\\")) or bool(_RECORDED_DRIVE_ABSOLUTE_RE.match(text))


def file_url(path: Path | str) -> str:
    """A `file://` URL for an absolute path recorded on any platform.

    `C:\\data\\input.csv` becomes `file:///C:/data/input.csv` rather than the percent-encoded
    backslashes a plain quote() of the native path produces. Records outlive the machine that
    wrote them, so a Windows path is resolved as a Windows path even when the report is rebuilt on
    Linux, and the other way round.

    Built here rather than with `PurePath.as_uri`, which CPython 3.14 deprecates for removal in
    3.19 and whose replacement cannot build a Windows URL on another platform. The result is the
    string CPython 3.12's `as_uri` gives for the same path. Raises ValueError for a path no link can
    name: a relative one, the device namespace, a UNC path without a share."""
    text = os.fspath(path)
    windows = _windows_absolute(text)
    if windows is not None:
        pure = PureWindowsPath(windows)
        if len(pure.drive) == 2 and pure.drive[1] == ":":
            prefix, rest = f"file:///{pure.drive}", pure.as_posix()[2:]
        else:
            prefix, rest = "file:", pure.as_posix()
    elif text.startswith("/"):
        prefix, rest = "file://", str(PurePosixPath(text))
    else:
        raise ValueError(f"file_url needs an absolute path a link can name, got {path!r}")
    return prefix + quote_from_bytes(rest.encode("utf-8", "surrogateescape"))


def record_relative_path(path: object) -> bool:
    """True for a project-relative path a record may hold, read the same way on every platform.

    Not rooted (`/`, `\\`), no drive (any one character and a colon at the start: `C:`, `é:`, `1:`),
    no first segment starting with `~` (Project.path expands it where such a home exists, which
    differs from machine to machine), no NUL,
    and no segment — split on `/` and on `\\`, since Windows reads both — that is empty, `.` or `..`.
    A backslash inside a name recorded on POSIX is otherwise allowed: it cannot climb out of the
    project anywhere. So `/tmp/x.py`, which is not absolute on Windows, and `C:x.py`, which is not
    absolute on POSIX, are refused everywhere."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    if path.startswith(("/", "\\")) or _DRIVE_RE.match(path) or path.startswith("~"):
        return False
    return all(part not in ("", ".", "..") for part in re.split(r"[\\/]", path))


def text_kwargs() -> dict:
    """Keyword arguments that make a subprocess decode as UTF-8 instead of by locale.

    `text=True` alone uses the locale encoding, which is cp949 or cp1252 on a Windows machine and
    turns Korean output into an exception or into replacement characters. Reconfiguring this
    process's own streams does not change what a child's pipe is decoded with."""
    return {"text": True, "encoding": TEXT_ENCODING, "errors": "replace"}


def configure_stdio() -> None:
    """Make this process write text as UTF-8 even when its output is a pipe or a file.

    A modern Windows console already uses UTF-8, but a redirected stream still follows the locale,
    and the extension reads this process's stdout through a pipe."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(Exception):
            reconfigure(encoding=TEXT_ENCODING, errors="replace")
