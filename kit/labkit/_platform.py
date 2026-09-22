"""OS-dependent operations behind small ports, so the rest of the kit has no platform branches.

This module is loaded inside the aelix process together with `core.py` (see
`extension/aelix_lab.py`), so it uses the standard library only and imports nothing from labkit.

Four operations behave differently on POSIX and Windows and are therefore ports: exclusive file
locking, atomic file replacement, child-process control, and the deadline and cancellation of a
run. Each has one implementation per platform, chosen once by `detect()` and carried in a bundle
that entry points pass to `core.Project`. Call sites use the bundle they were given; they do not
test `sys.platform` themselves.

Reserved file names, file URLs and text encoding are not ports: one rule that is correct
everywhere is better than a per-platform one, because a project directory is a record that has to
stay readable on another machine.

SUPPORT STATUS (ADR-0002). The POSIX implementations are the behaviour this kit has always had.
Every Windows implementation here is written from documentation and **has never been executed**:
each one carries a `not_run` note naming what a real Windows run has to establish. Nothing in this
module may be cited as evidence that Windows works.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol

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
    the new ones, never a partial write. It is not a transaction — a crash between writing the
    temporary file and replacing leaves the temporary file behind. An implementation may retry a
    documented, transient refusal, but it raises the last failure rather than losing the write."""

    def replace(self, src: Path | str, dst: Path | str) -> None: ...


class ProcessControl(Protocol):
    """Starting, observing and stopping child processes.

    These are primitives, not a lifecycle: they do not check whether a process has already exited
    and they do not wait. The caller keeps its own grace period and its own ordering, because the
    policy differs between the extension's abort path and the CLI's.

    `alive` answers about a recorded pid and cannot tell a reused pid from the original process —
    a limitation of pid-based liveness on every platform, not of one implementation. It reports
    `True` when the answer is not knowable, so a running record is never downgraded on a guess.

    `spawn_kwargs` go to `subprocess`/`asyncio` so a child starts in its own group. `base_env` is
    what a child cannot start without on this platform; callers add their own on top. `interrupt`
    asks a child to stop and lets it record what it was doing. `kill_tree` ends it and its
    descendants without warning and **may block**, so an event loop must call it off-thread."""

    def alive(self, pid: int | str | None) -> bool: ...

    def spawn_kwargs(self) -> dict: ...

    def base_env(self) -> dict[str, str]: ...

    def interrupt(self, proc) -> None: ...

    def kill_tree(self, proc) -> None: ...


class RunControl(Protocol):
    """The deadline and the cancellation of a script running in this process.

    Both callbacks are **notifications**: they record that the event happened and must not raise.
    Interrupting the main thread is the port's job, and it always does it by raising `Interrupted`
    or `KeyboardInterrupt` there, so the caller classifies by the flags its callback set rather
    than by the exception type.

    `deadline` arms a timer for `seconds`. Cancellation of an alarm that has already started
    firing is not possible on either platform, so an interruption may still be delivered while the
    context is unwinding; the body must therefore sit inside a region that catches
    `BaseException`, and the flag remains authoritative.

    `cancel_scope` installs `on_cancel` for the interrupt signals this platform can deliver and
    restores the previous handlers afterwards. With `interrupt=False` it records the event without
    raising, which is what a caller uses while it is writing a record that must not be lost.

    Neither guarantees that a blocking call inside a C extension stops at once."""

    def deadline(self, seconds: float,
                 on_expire: Callable[[], None]) -> contextlib.AbstractContextManager[None]: ...

    def cancel_scope(self, on_cancel: Callable[[], None], *,
                     interrupt: bool) -> contextlib.AbstractContextManager[None]: ...


class Platform(Protocol):
    """The bundle of ports one process uses. `name` is for records and messages only."""

    name: str
    locks: FileLocks
    files: AtomicFiles
    processes: ProcessControl
    runs: RunControl


def _notify(callback: Callable[[], None]) -> None:
    """Run a notification callback. A callback that raises is a bug in the caller, and it must not
    stop the port from interrupting the main thread, so its failure is reported and dropped."""
    try:
        callback()
    except BaseException as exc:  # noqa: BLE001 - reported, never allowed to skip the interrupt
        print(f"labkit: run-control callback failed: {type(exc).__name__}: {exc}", file=sys.stderr)


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


class PosixProcessControl:
    def alive(self, pid: int | str | None) -> bool:
        try:
            os.kill(int(pid), 0)  # type: ignore[arg-type]
        except PermissionError:
            return True  # it exists; this user may not signal it
        except (OSError, ValueError, TypeError):
            return False
        return True

    def spawn_kwargs(self) -> dict:
        return {"start_new_session": True}

    def base_env(self) -> dict[str, str]:
        return {}

    def interrupt(self, proc) -> None:
        # SIGINT to the child only: uv forwards it, and the kit records the run as cancelled.
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(proc.pid, signal.SIGINT)

    def kill_tree(self, proc) -> None:
        # The group, by the child's own pid: `spawn_kwargs` starts it in a new session, so its
        # process group id is its pid. `os.getpgid` would fail here, because this is also used
        # once the wrapper has been reaped while a descendant still holds the pipes — and that is
        # exactly the case this has to end. Addressing the group by that id stays safe then: the
        # kernel does not reuse a pid while a process group is still using it as its id, so this
        # either reaches the surviving descendants or fails with ESRCH.
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(proc.pid, signal.SIGKILL)


class PosixRunControl:
    @contextlib.contextmanager
    def deadline(self, seconds: float, on_expire: Callable[[], None]) -> Iterator[None]:
        previous = signal.getsignal(signal.SIGALRM)

        def fire(_signum, _frame):
            _notify(on_expire)
            raise Interrupted("run deadline expired")

        signal.signal(signal.SIGALRM, fire)
        try:
            # SIGALRM interrupts a blocking read or a long call inside DuckDB, which a timer
            # thread could not: the handler runs in the main thread as soon as the kernel returns.
            signal.setitimer(signal.ITIMER_REAL, max(1.0, float(seconds)))
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            with contextlib.suppress(Exception):
                signal.signal(signal.SIGALRM, previous)

    @contextlib.contextmanager
    def cancel_scope(self, on_cancel: Callable[[], None], *, interrupt: bool) -> Iterator[None]:
        handled = (signal.SIGTERM, signal.SIGINT)
        previous = {sig: signal.getsignal(sig) for sig in handled}

        def fire(_signum, _frame):
            _notify(on_cancel)
            if interrupt:
                raise Interrupted("run cancelled")

        for sig in handled:
            signal.signal(sig, fire)
        try:
            yield
        finally:
            for sig, handler in previous.items():
                with contextlib.suppress(Exception):
                    signal.signal(sig, handler)


# --- Windows ------------------------------------------------------------------------------------

# Written from the documented behaviour of msvcrt, subprocess, CPython and the Win32 API. Nothing
# below has run on Windows. Each class names what its `not_run` verification has to establish.

WINDOWS_ENV = ("SystemRoot", "SystemDrive", "windir", "COMSPEC", "PATHEXT", "TEMP", "TMP",
               "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
               "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
# `_locking` reports contention as EACCES, and an exhausted LK_LOCK retry as EDEADLOCK. Everything
# else it can report (EBADF, EINVAL) is a permanent failure that must not be retried. EDEADLOCK is
# a Windows spelling; macOS and Linux only have EDEADLK, so this module stays importable there.
LOCK_CONTENTION_ERRNOS = frozenset({errno.EACCES,
                                    getattr(errno, "EDEADLOCK", errno.EDEADLK)})
LOCK_POLL_SECONDS = 0.05
# ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION are the two refusals a concurrent reader causes.
SHARING_WINERRORS = frozenset({32, 33})
REPLACE_ATTEMPTS = 40
REPLACE_POLL_SECONDS = 0.05
KILL_TREE_TIMEOUT_SECONDS = 10
# taskkill reports "process not found" as 128, which is success for this purpose.
TASKKILL_NOT_FOUND = 128


class WindowsFileLocks:
    """not_run: that two processes actually exclude each other, and that the lock is released when
    the holder is killed rather than leaking until the file handle is closed."""

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
    """not_run: that the retry budget is enough for a report build that runs while `status` reads
    the same records, and that no reader holds a handle for longer than it."""

    def replace(self, src: Path | str, dst: Path | str) -> None:
        # Windows refuses to replace a file another process still has open, and the report and
        # `status` read the records a write is replacing. Retrying turns that collision into a
        # short wait. It is a mitigation with a stated budget, not a guarantee: a permanent
        # refusal is raised at once, and an exhausted budget raises the real failure.
        for _ in range(REPLACE_ATTEMPTS - 1):
            try:
                os.replace(src, dst)
                return
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in SHARING_WINERRORS:
                    raise
                time.sleep(REPLACE_POLL_SECONDS)
        os.replace(src, dst)


class WindowsProcessControl:
    """not_run: that CTRL_BREAK reaches a `uv run` child from a console-less host and that uv
    forwards it the way it forwards SIGINT on POSIX; that `taskkill /F /T` ends the tree within
    its timeout; and that `base_env` is sufficient for `uv` to start."""

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
        try:
            pid_value = int(pid)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        ctypes, kernel32 = self._kernel32()
        synchronize, wait_timeout, wait_failed = 0x00100000, 0x00000102, 0xFFFFFFFF
        error_invalid_parameter = 87
        handle = kernel32.OpenProcess(synchronize, False, pid_value)
        if not handle:
            if ctypes.get_last_error() == error_invalid_parameter:
                return False  # no process has this id
            # Access denied means it exists and this user may not wait on it; any other code is an
            # answer this port does not have. Neither may downgrade a running record on a guess.
            return True
        try:
            result = kernel32.WaitForSingleObject(handle, 0)
            if result == wait_failed:
                return True  # the wait itself failed: that is not evidence the process exited
            return result == wait_timeout
        finally:
            kernel32.CloseHandle(handle)

    def spawn_kwargs(self) -> dict:
        # Its own group, so a console event can be aimed at the child without stopping aelix.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    def base_env(self) -> dict[str, str]:
        # A child started with an explicit environment fails to initialise without these. The
        # caller's allow-list is about what a run may see, not about what Windows needs to start.
        return {name: os.environ[name] for name in WINDOWS_ENV if name in os.environ}

    def interrupt(self, proc) -> None:
        # CTRL_BREAK_EVENT is the one console event that can be aimed at a single process group,
        # and it needs CREATE_NEW_PROCESS_GROUP, which `spawn_kwargs` supplies.
        with contextlib.suppress(OSError, ValueError):
            proc.send_signal(signal.CTRL_BREAK_EVENT)

    def kill_tree(self, proc) -> None:
        # Killing the process leaves its children running, and the child here is `uv`, which has
        # one. `taskkill /T` is the documented way to end the tree. This blocks, so an event loop
        # calls it off-thread (see ProcessControl).
        #
        # A failure here is reported rather than swallowed, because the caller cannot tell from a
        # silent return whether the descendants are gone: they may still be holding the pipes it
        # is about to wait on. There is no second way to end the tree, so this is a diagnostic,
        # not a recovery, and docs/CONTRACTS.md says the run can stay incomplete.
        problem = None
        try:
            done = subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                  capture_output=True, timeout=KILL_TREE_TIMEOUT_SECONDS,
                                  check=False, **text_kwargs())
            if done.returncode not in (0, TASKKILL_NOT_FOUND):
                problem = f"taskkill exited {done.returncode}: {(done.stderr or '').strip()[:200]}"
        except (OSError, subprocess.SubprocessError) as exc:
            problem = f"{type(exc).__name__}: {exc}"
        if problem:
            print(f"labkit: could not end the process tree of pid {proc.pid}; its descendants may "
                  f"still be running ({problem})", file=sys.stderr)
        with contextlib.suppress(OSError, ValueError):
            proc.kill()


class WindowsRunControl:
    """not_run: that a timeout in a blocking DuckDB call is still recorded as `timeout`, and that
    a `time.sleep` script cancels promptly — CPython's Windows sleep wakes for SIGINT only, not
    for the SIGBREAK this receives, so timely cancellation cannot be assumed."""

    @contextlib.contextmanager
    def deadline(self, seconds: float, on_expire: Callable[[], None]) -> Iterator[None]:
        import _thread  # noqa: PLC0415 - interrupt_main is the portable half of this port

        state = {"armed": True}
        lock = threading.Lock()

        def fire() -> None:
            with lock:
                if not state["armed"]:
                    return
                state["armed"] = False
                _notify(on_expire)
                # There is no SIGALRM: raise in the main thread the way Ctrl+C would. A call
                # blocked inside a C extension keeps running until it returns.
                with contextlib.suppress(Exception):
                    _thread.interrupt_main()

        timer = threading.Timer(max(1.0, float(seconds)), fire)
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()
            # `Timer.cancel` does not stop a callback that already started, so disarming takes the
            # same lock: either the alarm completed before this point, or it never fires. An
            # interrupt raised by an alarm that won the race lands while this block unwinds, which
            # is why the contract requires the body to sit inside a BaseException handler.
            with lock:
                state["armed"] = False

    @contextlib.contextmanager
    def cancel_scope(self, on_cancel: Callable[[], None], *, interrupt: bool) -> Iterator[None]:
        # SIGBREAK carries CTRL_BREAK_EVENT, which is what the parent sends. SIGTERM exists but is
        # never delivered: `os.kill(pid, SIGTERM)` on Windows terminates the process without
        # running a handler, so forced termination leaves the run `incomplete` rather than
        # `cancelled`. That difference is stated in docs/CONTRACTS.md.
        handled = [signal.SIGINT]
        if hasattr(signal, "SIGBREAK"):
            handled.append(signal.SIGBREAK)
        previous = {sig: signal.getsignal(sig) for sig in handled}

        def fire(_signum, _frame):
            _notify(on_cancel)
            if interrupt:
                raise Interrupted("run cancelled")

        for sig in handled:
            signal.signal(sig, fire)
        try:
            yield
        finally:
            for sig, handler in previous.items():
                with contextlib.suppress(Exception):
                    signal.signal(sig, handler)


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

    Entry points call this once and pass the result down. Nothing chooses an implementation by
    reading the environment or by importing a module named in a record. Building a bundle does not
    import the operating-system module it will need, so the wiring can be asserted from either
    platform — constructing the Windows bundle on Linux proves nothing about Windows."""
    chosen = name or ("windows" if os.name == "nt" else "posix")
    if chosen == "windows":
        return _Bundle("windows", WindowsFileLocks(), WindowsAtomicFiles(),
                       WindowsProcessControl(), WindowsRunControl())
    if chosen == "posix":
        return _Bundle("posix", PosixFileLocks(), PosixAtomicFiles(), PosixProcessControl(),
                       PosixRunControl())
    raise ValueError(f"unknown platform {name!r}; expected 'posix' or 'windows'")


# --- rules that are the same everywhere -----------------------------------------------------------

# Windows keeps these names reserved whatever the extension is, so `con.png` and `aux.parquet` are
# device names there. A new name is refused on every platform: a project made on Linux has to stay
# usable on Windows, and a name becomes part of a record that outlives the machine that made it.
# Names already in a project are never refused — see core.reject_unportable_name.
RESERVED_STEMS = frozenset({"con", "prn", "aux", "nul",
                            *(f"com{n}" for n in "123456789"),
                            *(f"lpt{n}" for n in "123456789")})
# Characters Windows forbids in a path component, plus the control range.
UNPORTABLE_CHARS_RE = re.compile(r'[<>:"|?*\\\x00-\x1f]')
_WINDOWS_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/])")
TEXT_ENCODING = "utf-8"


def reserved_name(name: str) -> bool:
    """True for a file name that Windows treats as a device rather than a file."""
    return str(name).split(".")[0].strip().lower() in RESERVED_STEMS


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
# cannot appear in one, which makes the mapping below injective: no reserved id can be turned into
# a name another id already has. A suffix such as "-file" would not be, because `con-file` is a
# perfectly ordinary id and both would land on the same file.
UNPORTABLE_SUFFIX = "_"


def portable_stem(stem: str) -> str:
    """`stem`, or a distinct variant of it that is a usable file name on Windows.

    Used where the name on disk may differ from the identity in the record: a chart keeps the id it
    was recorded with, and only the file it is written to has to be openable everywhere. Two
    different stems never map to the same result."""
    text = str(stem)
    if reserved_name(text):
        return f"{text}{UNPORTABLE_SUFFIX}"
    return text


def file_url(path: Path | str) -> str:
    """A `file://` URL for an absolute path recorded on any platform.

    `C:\\data\\input.csv` becomes `file:///C:/data/input.csv` rather than the percent-encoded
    backslashes a plain quote() of the native path produces. Records outlive the machine that
    wrote them, so a Windows path is resolved as a Windows path even when the report is rebuilt on
    Linux, and the other way round."""
    text = os.fspath(path)
    pure = PureWindowsPath(text) if _WINDOWS_ABS_RE.match(text) else PurePosixPath(text)
    if not pure.is_absolute():
        raise ValueError(f"file_url needs an absolute path, got {path!r}")
    return pure.as_uri()


def absolute_record_path(path: str) -> bool:
    """True for a recorded path that is absolute on the platform that recorded it.

    `os.path.isabs` answers for the platform running now, so a Windows path read on Linux would be
    treated as relative and linked as if it sat inside the report."""
    text = str(path)
    return bool(_WINDOWS_ABS_RE.match(text)) or text.startswith("/")


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
