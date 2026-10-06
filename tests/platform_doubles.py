"""The test adapter of the four platform ports, and the real implementations' classes.

`recording_platform()` is the stand-in a test injects with `Project(root, platform=…)`: every port
records what it was asked and does it in this process, without the operating system's help. It keeps
the ports' contract where a stand-in can (tests/test_contracts.py runs it through the same cases as the
real implementations) and declares what it does not do: its lock excludes nothing outside this
process, `alive` says no recorded process is running, and its run guard never interrupts.

Shared by tests/test_platform.py (Issue #8's injection tests) and tests/test_contracts.py (Issue #43).
"""

from __future__ import annotations

import contextlib
import os
import types
from pathlib import Path

from aelix_analytics import _platform


class _RecordingLocks:
    def __init__(self, calls):
        self.calls = calls
        self._held = set()

    @contextlib.contextmanager
    def exclusive(self, path):
        path = Path(path)
        self.calls.append(("locks.exclusive", path.name))
        assert path not in self._held, f"{path.name} was taken re-entrantly"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+b"):  # the port creates its lock file and never truncates it
            pass
        self._held.add(path)
        try:
            yield
        finally:
            self._held.discard(path)


class _RecordingFiles:
    def __init__(self, calls):
        self.calls = calls

    def replace(self, src, dst):
        self.calls.append(("files.replace", Path(dst).name))
        os.replace(src, dst)


class _RecordingProcesses:
    asks_by_request = False

    def __init__(self, calls):
        self.calls = calls

    def alive(self, pid):
        self.calls.append(("processes.alive", pid))
        return False

    def spawn_kwargs(self):
        return {}

    def base_env(self):
        return {}

    def interrupt(self, proc, request=None):
        self.calls.append(("processes.interrupt", proc.pid))
        return True

    def kill_tree(self, pid):
        self.calls.append(("processes.kill_tree", pid))

    def open_document(self, path, *, wait):
        # Opens nothing: a test must not start a desktop program.
        self.calls.append(("processes.open_document", Path(path).name))
        return 0 if wait else None

    def find_program(self, name):
        self.calls.append(("processes.find_program", name))
        return None


class _RecordingGuard:
    def __init__(self, calls):
        self.calls = calls
        self.cancelled = self.expired = False

    def __enter__(self):
        self.calls.append(("runs.guard", "enter"))
        return self

    def __exit__(self, *exc_info):
        self.calls.append(("runs.guard", "exit"))
        return False

    @contextlib.contextmanager
    def interruptible(self, deadline=None):
        self.calls.append(("runs.interruptible", deadline))
        yield

    def settle(self, failure=None):
        self.calls.append(("runs.settle", None))


class _RecordingRuns:
    def __init__(self, calls):
        self.calls = calls

    def guard(self):
        return _RecordingGuard(self.calls)

    def watch_for_cancel(self, request):
        self.calls.append(("runs.watch_for_cancel", Path(request).name))
        return lambda: None


def recording_platform():
    calls: list[tuple] = []
    bundle = types.SimpleNamespace(
        name="recording", locks=_RecordingLocks(calls), files=_RecordingFiles(calls),
        processes=_RecordingProcesses(calls), runs=_RecordingRuns(calls))
    return bundle, calls


IMPLEMENTATIONS = (_platform.PosixFileLocks, _platform.WindowsFileLocks,
                   _platform.PosixAtomicFiles, _platform.WindowsAtomicFiles,
                   _platform.PosixProcessControl, _platform.WindowsProcessControl,
                   _platform.PosixRunControl, _platform.WindowsRunControl)


def port_methods(cls) -> set[str]:
    """Every public callable the class offers, not only the ones defined on it.

    `vars(cls)` misses an inherited method and a classmethod, and a port that grew one of those
    would then run for real behind a passing injection test."""
    return {name for name in dir(cls)
            if not name.startswith("_") and callable(getattr(cls, name, None))}
