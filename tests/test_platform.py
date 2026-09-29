"""The portable boundaries: the rules that are the same everywhere, the ports' contract, the
injection seam and the loader the aelix process uses.

Running the Windows implementations is out of scope here and stays `not_run` until Issue #8's
platform verification: constructing the Windows bundle on POSIX proves wiring, never behaviour.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import errno
import gc
import glob
import importlib.util
import io
import json
import multiprocessing
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import types
import warnings
from pathlib import Path, PureWindowsPath

import pytest
from conftest import kit_env, labkit, write_sales_csv, write_script

from labkit import _platform, core

PACK = Path(__file__).resolve().parents[1]
KIT = PACK / "kit"
EXT = PACK / "extension" / "aelix_lab.py"


# --- rules that are the same on every platform ----------------------------------------------------

# The expected values are CPython 3.13's ntpath (`_isreservedname`'s device-name rule), which this
# follows exactly rather than a list of its own.
@pytest.mark.parametrize("name, reserved", [
    ("con", True), ("CON", True), ("con.png", True), ("nul", True), ("aux.parquet", True),
    ("com1", True), ("lpt9", True), ("console", False), ("contract", False), ("com10", False),
    ("sales", False), ("nul_data", False), ("com0", False), ("lpt0", False),
    ("conin$", True), ("CONOUT$.txt", True), ("com\u00b9", True), ("COM\u00b2.log", True),
    ("lpt\u00b3", True), ("aux.tar.gz", True), ("con .txt", True), ("con  ", True),
    (" con.txt", False), ("conin", False),
])
def test_windows_device_names_are_recognised_everywhere(name, reserved):
    assert _platform.reserved_name(name) is reserved


def test_portable_name_rejects_what_windows_cannot_store():
    assert _platform.portable_name("sales_2025")
    for bad in ("con", "a<b", 'a"b', "a|b", "a?b", "a*b", "a:b", "a\\b", "a/b", "a\x01b",
                "trailing.", "trailing ", "", ".", ".."):
        assert not _platform.portable_name(bad), bad


def test_portable_stem_keeps_a_usable_name_and_leaves_ordinary_ones_alone():
    assert _platform.portable_stem("con") == "con_"
    assert not _platform.reserved_name(_platform.portable_stem("con"))
    assert _platform.portable_stem("monthly-revenue") == "monthly-revenue"


def test_portable_stem_never_maps_two_chart_ids_onto_one_file():
    """A chart id is a slug and holds no underscore, which is what makes the mapping injective.
    `con` and the ordinary id `con-file` must not land on the same file, or two charts would
    overwrite each other's image and a rerun comparison would report them as the same."""
    ids = [core.slugify(text, 40) for text in
           ("con", "con file", "con-file", "con 1", "nul", "nul data", "aux", "revenue")]
    stems = [_platform.portable_stem(i) for i in ids]
    assert len(set(stems)) == len(set(ids)), dict(zip(ids, stems, strict=True))
    assert all("_" not in i for i in ids), "a slug with an underscore would break the mapping"


# The expected URLs are what CPython 3.12's PurePath.as_uri gives, which file_url reproduces without
# calling it (deprecated in 3.14), plus the extended-length forms as_uri turns into a dead link.
@pytest.mark.parametrize("recorded, url", [
    (r"C:\data\input.csv", "file:///C:/data/input.csv"),
    ("C:/data/input.csv", "file:///C:/data/input.csv"),
    ("C:\\", "file:///C:/"),
    (r"c:\a b\#1%.csv", "file:///c:/a%20b/%231%25.csv"),
    (r"\\server\share\x.csv", "file://server/share/x.csv"),
    (r"\\srv\share\a b\#%.csv", "file://srv/share/a%20b/%23%25.csv"),
    (r"\\?\C:\long\x.csv", "file:///C:/long/x.csv"),
    (r"\\?\UNC\srv\share\x.csv", "file://srv/share/x.csv"),
    (r"\\?\unc\srv\share\x.csv", "file://srv/share/x.csv"),  # the prefix is read without case
    ("/data/sales.csv", "file:///data/sales.csv"),
    ("/a//b/./c", "file:///a/b/c"),
    ("/", "file:///"),
])
def test_file_url_reads_a_path_the_way_the_platform_that_recorded_it_meant_it(recorded, url):
    # A record outlives the machine that wrote it, so a Windows path has to resolve on Linux too.
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no deprecated pathlib call on any supported Python
        assert _platform.file_url(recorded) == url


def test_file_url_does_not_depend_on_the_deprecated_as_uri(monkeypatch):
    # PurePath.as_uri is deprecated in 3.14 and gone in 3.19; a warning test cannot fail on the
    # Pythons this runs on, so the method itself is made to refuse.
    def refuse(self):
        raise AssertionError("file_url called PurePath.as_uri")

    from pathlib import PurePath  # noqa: PLC0415

    monkeypatch.setattr(PurePath, "as_uri", refuse)
    assert _platform.file_url(r"C:\data\x.csv") == "file:///C:/data/x.csv"
    assert _platform.file_url("/data/x.csv") == "file:///data/x.csv"


def test_file_url_percent_encodes_instead_of_mangling_non_ascii():
    assert _platform.file_url("/data/한글.csv").startswith("file:///data/%ED")


@pytest.mark.parametrize("recorded", ["data/raw/sales.parquet", "C:rel.csv", r"\\.\C:\x",
                                      "\\\\server\\", r"\\?\Volume{1}\x"])
def test_file_url_refuses_what_no_link_can_name(recorded):
    with pytest.raises(ValueError, match="absolute"):
        _platform.file_url(recorded)


def test_a_record_no_link_can_name_does_not_stop_the_report():
    from labkit import report  # noqa: PLC0415 - the report module, not only the rule

    site = object.__new__(report.Site)
    for recorded in (r"\\.\C:\x", "\\\\server\\"):
        assert site.file_link(recorded, 1) == "#"


@pytest.mark.parametrize("recorded, absolute", [
    (r"C:\data\x.csv", True), (r"\\srv\share\x", True),
    ("/data/x.csv", True), ("data/raw/x.parquet", False), ("runs/1/code/x.py", False),
    (r"\\?\C:\x", True), (r"\\.\C:\x", True), ("C:rel.csv", False),
    # The kit records a Windows path natively, with backslashes; `c:/x.csv` is a POSIX output under
    # a directory named `c:`, linked relatively as main did (review 6, ONC-3).
    ("C:/data/x.csv", False), ("c:/x.csv", False),
])
def test_absolute_record_path_does_not_ask_the_running_platform(recorded, absolute):
    assert _platform.absolute_record_path(recorded) is absolute
    if absolute and os.name != "nt" and recorded.startswith("C:"):
        # This is the bug it exists to prevent: the report would have linked it as a relative path.
        assert not os.path.isabs(recorded)


def test_a_project_directory_named_like_a_drive_is_linked_inside_the_project():
    from labkit import report  # noqa: PLC0415

    site = object.__new__(report.Site)
    assert site.file_link("c:/x.csv", 1) == "../../c:/x.csv"  # what main linked


@pytest.mark.parametrize("recorded, ok", [
    ("scripts/a.py", True), ("scripts/sub/a.py", True), ("a.py", True), ("scripts/con.py", True),
    # A backslash inside a name the kit recorded on POSIX cannot climb out of the project anywhere,
    # so the kit accepts back what it records (review 5, NLL-2).
    (r"scripts/we\ird.py", True), (r"scripts\a.py", True),
    ("/tmp/evil.py", False), (r"\tmp\evil.py", False), ("C:/x.py", False), ("c:x.py", False),
    # Windows (CPython 3.12+) reads any one character before a colon as a drive (review 6, ONC-4).
    ("\u00e9:/x.py", False), ("1:/x.py", False), ("_:x.py", False), ("\uff23:/x.py", False),
    ("ab:/x.py", True),
    ("scripts/../../x.py", False), (r"scripts\..\..\x.py", False), (r"a\\b.py", False),
    ("./a.py", False), ("scripts//a.py", False), ("scripts/", False),
    ("~/a.py", False), ("~root/a.py", False),  # Project.path would expand them (review 5, NLL-3)
    ("", False), (None, False), (3, False), ("a\x00.py", False),
])
def test_a_recorded_relative_path_means_the_same_on_every_platform(recorded, ok):
    # `/tmp/evil.py` is not absolute on Windows and `C:x.py` is not on POSIX; asking the running
    # platform let the first through on Windows (Issue #8 measurement).
    assert _platform.record_relative_path(recorded) is ok


def test_text_kwargs_pin_utf8_rather_than_the_locale():
    assert _platform.text_kwargs()["encoding"] == "utf-8"


# --- assembling a bundle ---------------------------------------------------------------------------

def test_detect_picks_this_platform_and_can_be_asked_for_either():
    here = _platform.detect()
    assert here.name == ("windows" if os.name == "nt" else "posix")
    assert _platform.detect("posix").name == "posix"
    with pytest.raises(ValueError, match="unknown platform"):
        _platform.detect("bsd")


def test_the_posix_document_opener_is_chosen_where_the_bundle_is_built(monkeypatch):
    # Not whichever `open` is on PATH: on Linux that can be another program (ADR-0004).
    monkeypatch.setattr(_platform.sys, "platform", "darwin")
    assert _platform.detect("posix").processes.opener == "open"
    monkeypatch.setattr(_platform.sys, "platform", "linux")
    assert _platform.detect("posix").processes.opener == "xdg-open"


def test_building_the_windows_bundle_does_not_import_its_operating_system_module():
    # So the wiring can be asserted from any platform. It proves nothing about Windows behaviour.
    sys.modules.pop("msvcrt", None)
    bundle = _platform.detect("windows")
    assert type(bundle.locks).__name__ == "WindowsFileLocks"
    assert type(bundle.runs).__name__ == "WindowsRunControl"
    assert "msvcrt" not in sys.modules


# --- the file lock contract -------------------------------------------------------------------------

def _hold_lock(lock_path, started, seconds):
    with _platform.detect().locks.exclusive(Path(lock_path)):
        started.set()
        time.sleep(seconds)


def test_the_lock_excludes_another_process(tmp_path):
    lock = tmp_path / "lock"
    started = multiprocessing.Event()
    holder = multiprocessing.Process(target=_hold_lock, args=(str(lock), started, 1.5))
    holder.start()
    try:
        assert started.wait(10), "the holder never acquired the lock"
        t0 = time.perf_counter()
        with _platform.detect().locks.exclusive(lock):
            waited = time.perf_counter() - t0
        assert waited > 0.5, f"the lock did not exclude the other process (waited {waited:.2f}s)"
    finally:
        holder.join(20)


def test_the_lock_creates_its_directory_and_does_not_truncate(tmp_path):
    lock = tmp_path / "missing" / "lock"
    with _platform.detect().locks.exclusive(lock):
        pass
    assert lock.is_file()
    # Written between two holds, not inside one: on Windows a second handle cannot write the byte
    # the lock holds (measured), and the product's lock files hold nothing else anyway.
    lock.write_bytes(b"content")
    with _platform.detect().locks.exclusive(lock):
        pass
    assert lock.read_bytes() == b"content"


# --- liveness and replacement, with the operating system mocked ----------------------------------

@pytest.mark.parametrize("pid", [0, -1, 2**40, "x", None, True, 1.5e300, float("nan")])
def test_a_value_that_cannot_be_a_pid_is_never_alive(pid):
    # os.kill reads 0 and -1 as process groups, so alive(0) used to report the caller itself.
    assert _platform.PosixProcessControl().alive(pid) is False
    assert _platform.WindowsProcessControl().alive(pid) is False  # rejected before any Win32 call


def test_a_pid_another_user_owns_is_not_this_run(monkeypatch):
    # The kit records its own pid as the project's user, so EPERM means the id was reused. main read
    # it that way; reading it as alive kept a dead run listed as running.
    def eperm(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(_platform.os, "kill", eperm)
    assert _platform.PosixProcessControl().alive(4242) is False


class _FakeKernel32:
    def __init__(self, handle, last_error=0, wait=0x102):
        self.handle, self.last_error, self.wait, self.closed = handle, last_error, wait, []

    def OpenProcess(self, access, inherit, pid):  # noqa: N802 - the Win32 name
        return self.handle

    def WaitForSingleObject(self, handle, ms):  # noqa: N802
        return self.wait

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return 1


@pytest.mark.parametrize("fake, alive", [
    (_FakeKernel32(handle=0, last_error=87), False),   # no such process
    (_FakeKernel32(handle=0, last_error=5), False),    # another user's process: a reused id
    (_FakeKernel32(handle=0, last_error=1), True),     # no answer: not downgraded on it
    (_FakeKernel32(handle=7, wait=0x102), True),       # still running
    (_FakeKernel32(handle=7, wait=0), False),          # exited
    (_FakeKernel32(handle=7, wait=0xFFFFFFFF), True),  # the wait failed: not evidence of an exit
])
def test_windows_liveness_reads_each_answer_the_way_the_port_says(monkeypatch, fake, alive):
    ctypes_stub = types.SimpleNamespace(get_last_error=lambda: fake.last_error)
    control = _platform.WindowsProcessControl()
    monkeypatch.setattr(control, "_kernel32", lambda: (ctypes_stub, fake))
    assert control.alive(4242) is alive
    assert fake.closed == ([fake.handle] if fake.handle else [])


@pytest.mark.parametrize("winerror, retried", [(5, True), (32, True), (33, True), (2, False)])
def test_windows_replace_waits_out_a_reader_and_nothing_else(monkeypatch, winerror, retried):
    calls = []

    def replace(src, dst):
        calls.append(1)
        if len(calls) < 3:
            error = PermissionError(13, "refused")
            error.winerror = winerror
            raise error

    monkeypatch.setattr(_platform.os, "replace", replace)
    monkeypatch.setattr(_platform.time, "sleep", lambda seconds: None)
    if retried:
        _platform.WindowsAtomicFiles().replace("a", "b")
        assert len(calls) == 3
    else:
        with pytest.raises(PermissionError):
            _platform.WindowsAtomicFiles().replace("a", "b")
        assert len(calls) == 1


@pytest.mark.parametrize("exit_code, reported, fallback", [(0, False, False), (1, True, True),
                                                              (128, True, False)])
def test_windows_kill_tree_reports_what_taskkill_could_not_do(monkeypatch, capsys, exit_code,
                                                               reported, fallback):
    # 128 is "no such process": the wrapper had exited when taskkill looked, and a descendant may
    # still hold the pipes (Issue #8 measurement). There is nothing left to end by pid then; after
    # any other failure the wrapper itself is ended. os.kill is mocked: 4242 is nobody's here.
    ran, killed = [], []
    monkeypatch.setattr(_platform.subprocess, "run", lambda argv, **kw: (
        ran.append(argv), subprocess.CompletedProcess(argv, exit_code, "", "ERROR: fake"))[1])
    monkeypatch.setattr(_platform.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    _platform.WindowsProcessControl().kill_tree(4242)
    assert ran == [["taskkill", "/F", "/T", "/PID", "4242"]]
    err = capsys.readouterr().err
    assert ("could not end the process tree of pid 4242" in err) is reported, err
    assert killed == ([(4242, signal.SIGTERM)] if fallback else [])


def test_windows_asks_the_kit_through_its_request_file_and_nothing_else(tmp_path):
    # Its only console event that reaches one group ended `uv run` at once and orphaned the kit
    # (Issue #8 measurement): no signal is sent, a child without a request is left to kill_tree.
    proc = types.SimpleNamespace(pid=4242, returncode=None,
                                 send_signal=lambda sig: pytest.fail("a signal was sent"))
    control = _platform.WindowsProcessControl()
    assert control.interrupt(proc) is False
    request = tmp_path / "cancel"
    assert control.interrupt(proc, request) is True and request.is_file()
    # A child that has exited leaves nothing to ask (review 7, stop-4): the caller waits, and does
    # not end the tree at once.
    assert control.interrupt(types.SimpleNamespace(pid=4242, returncode=0)) is True


# --- opening a document, finding a program (ADR-0004) --------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="a POSIX shell script stands in for the opener")
def test_posix_open_document_runs_the_opener_attached_or_detached(tmp_path, monkeypatch):
    opened = tmp_path / "opened.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    opener = bin_dir / "fake-open"
    opener.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$1\" >> '{opened}'\nexit 7\n",
                      encoding="utf-8")
    opener.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    control = _platform.PosixProcessControl("fake-open")
    document = tmp_path / "report" / "index.html"
    assert control.open_document(document, wait=True) == 7
    assert control.open_document(document, wait=False) is None
    deadline = time.monotonic() + 30
    while (not opened.exists() or len(opened.read_text(encoding="utf-8").splitlines()) < 2) \
            and time.monotonic() < deadline:
        time.sleep(0.02)
    assert opened.read_text(encoding="utf-8").splitlines() == [str(document), str(document)]


def test_posix_open_document_without_an_opener(tmp_path, monkeypatch):
    """The human CLI reports it; the extension keeps main's webbrowser route (review of design
    revision 1, Codex F7)."""
    import webbrowser

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    control = _platform.PosixProcessControl("no-such-opener")
    document = tmp_path / "index.html"
    with pytest.raises(FileNotFoundError):
        control.open_document(document, wait=True)
    handed = []
    monkeypatch.setattr(webbrowser, "open", lambda url: handed.append(url) or True)
    assert control.open_document(document, wait=False) is None
    assert handed == [document.resolve().as_uri()]


def test_windows_open_document_hands_the_file_to_its_program(monkeypatch, tmp_path):
    # Written for Windows; os.startfile is stood in for, so this proves the wiring, not Windows.
    started = []
    monkeypatch.setattr(_platform.os, "startfile", started.append, raising=False)
    control = _platform.WindowsProcessControl()
    document = tmp_path / "index.html"
    assert control.open_document(document, wait=True) == 0
    assert control.open_document(document, wait=False) is None
    assert started == [str(document), str(document)]


@pytest.mark.skipif(os.name == "nt", reason="POSIX executables and HOME")
def test_posix_find_program_keeps_the_extension_s_old_uv_rule(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    program = bin_dir / "uv"
    program.write_text("#!/bin/sh\n", encoding="utf-8")
    program.chmod(0o755)
    plain = bin_dir / "plain"
    plain.write_text("x", encoding="utf-8")
    plain.chmod(0o644)
    (bin_dir / "a-directory").mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    control = _platform.PosixProcessControl()
    assert control.find_program("uv") == os.path.abspath(program)
    assert control.find_program(str(program)) == os.path.abspath(program)
    assert control.find_program("~/bin/uv") == os.path.abspath(program)
    # A shim is a link whose target decides what to be by the name it was started by (critic-1 of
    # the design review: a mise shim resolved to mise itself).
    shim = bin_dir / "shimmed"
    shim.symlink_to(program)
    assert control.find_program("shimmed") == os.path.abspath(shim)
    assert control.find_program(str(shim)) == os.path.abspath(shim)
    for missing in (str(plain), str(bin_dir / "a-directory"), "no-such-program",
                    str(tmp_path / "nowhere" / "uv")):
        assert control.find_program(missing) is None
    monkeypatch.chdir(bin_dir)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert control.find_program("uv") is None, "the working directory is not searched"


def test_windows_find_program_takes_an_exe_from_path_and_never_the_working_directory(tmp_path,
                                                                                    monkeypatch):
    """Written for Windows, simulated with this platform's paths: it exercises the rule, not
    Windows' file system (review of design revision 1, Codex F9: an explicit path is held to it
    too)."""
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("uv.exe", "uv.cmd", "uv.bat"):
        (tools / name).write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "uv.exe").write_text("", encoding="utf-8")
    monkeypatch.chdir(project)
    control = _platform.WindowsProcessControl()
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "relative", f'"{tools}"']))
    assert control.find_program("uv") == os.path.abspath(tools / "uv.exe")
    assert control.find_program("uv.exe") == os.path.abspath(tools / "uv.exe")
    assert control.find_program("uv.cmd") is None and control.find_program("uv.bat") is None
    assert control.find_program(str(tools / "uv.exe")) == os.path.abspath(tools / "uv.exe")
    assert control.find_program(str(tools / "uv.cmd")) is None
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "relative"]))
    assert control.find_program("uv") is None, "not the uv.exe in the working directory"


def test_posix_asks_with_sigint_and_never_signals_a_reaped_child(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(_platform.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    control = _platform.PosixProcessControl()
    assert control.asks_by_request is False and _platform.WindowsProcessControl.asks_by_request
    request = tmp_path / "cancel"
    assert control.interrupt(types.SimpleNamespace(pid=4242, returncode=None), request) is True
    assert sent == [(4242, signal.SIGINT)] and not request.exists()
    # An exited child leaves nothing to ask, and is not signalled: its pid may be free.
    assert control.interrupt(types.SimpleNamespace(pid=4242, returncode=0), request) is True
    assert sent == [(4242, signal.SIGINT)]


def test_posix_takes_a_child_reaped_before_the_loop_noticed_as_nothing_left_to_ask(monkeypatch):
    """Review 6, AS-8: in the window where the child watcher has reaped the child and the event loop
    has not yet set its returncode, os.kill fails with ESRCH; reading that as 'could not ask' sent an
    immediate group SIGKILL main never sent. Nothing is left to ask, so the caller waits instead."""
    def gone(pid, sig):
        raise ProcessLookupError(pid)

    monkeypatch.setattr(_platform.os, "kill", gone)
    proc = types.SimpleNamespace(pid=4242, returncode=None)
    assert _platform.PosixProcessControl().interrupt(proc) is True


# --- the run-control contract -------------------------------------------------------------------

# These tests deliver signals to this process with signal.raise_signal, never os.kill(os.getpid(), …):
# on Windows os.kill with anything but a console event calls TerminateProcess, which would end the
# test runner instead of exercising the handler under test.

def _handlers():
    names = ("SIGINT", "SIGTERM", "SIGALRM", "SIGBREAK")
    return {n: signal.getsignal(getattr(signal, n)) for n in names if hasattr(signal, n)}


def _guard():
    return _platform.detect().runs.guard()


def _outcome(guard) -> dict:
    return {"timeout": guard.expired, "cancel": guard.cancelled}


def test_the_deadline_interrupts_a_sleeping_main_thread_and_is_recorded():
    # A sleep is the hard case: the Windows port used to set only the flag, and the sleep ran on to
    # its end (Issue #8 measurement). Without the elapsed-time check this passed after ten seconds.
    t0 = time.perf_counter()
    with _guard() as guard:
        with pytest.raises(_platform.Interrupted):
            with guard.interruptible(deadline=1.0):
                time.sleep(10)
    assert time.perf_counter() - t0 < 5, "the sleep was not interrupted"
    assert _outcome(guard) == {"timeout": True, "cancel": False}


def test_the_deadline_disarms_when_the_region_ends_first():
    # Waiting past the deadline is what makes a missing disarm visible. Recording handlers stand in
    # for the default actions, which would otherwise end the test runner later.
    late = []
    saved = {sig: signal.signal(sig, lambda signum, _frame: late.append(signum))
             for sig in (getattr(signal, n, None) for n in ("SIGALRM", "SIGINT")) if sig}
    try:
        with _guard() as guard:
            with guard.interruptible(deadline=1.0):
                pass
            assert not guard.armed
            if hasattr(signal, "setitimer"):
                assert signal.getitimer(signal.ITIMER_REAL)[0] == 0
            time.sleep(1.5)
        time.sleep(0.2)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
    assert _outcome(guard) == {"timeout": False, "cancel": False} and not late


def test_outside_a_region_a_cancel_only_records_and_the_next_region_raises_at_once():
    before = _handlers()
    with _guard() as guard:
        signal.raise_signal(signal.SIGINT)
        time.sleep(0.05)
        assert guard.cancelled, "the cancel was not recorded"
        ran = []
        with pytest.raises(_platform.Interrupted):
            with guard.interruptible():
                ran.append(1)
        assert not ran, "the region ran after a recorded cancel"
    assert _handlers() == before


def test_a_region_is_interrupted_once_and_a_second_signal_only_records():
    """The one-shot rule is what keeps a second signal off the path to the record: the handler
    stops raising before it raises, whatever the region is doing when the next one comes."""
    seen = []
    before = _handlers()
    with _guard() as guard:
        with guard.interruptible(deadline=30.0):
            try:
                signal.raise_signal(signal.SIGINT)
                time.sleep(0.2)
            except _platform.Interrupted:
                seen.append("first")
                assert not guard.armed, "raising also disarms the deadline"
            signal.raise_signal(signal.SIGINT)  # swallowed by the script above: not raised again
            time.sleep(0.2)
            seen.append("carried on")
    assert seen == ["first", "carried on"] and guard.cancelled
    assert _handlers() == before


def test_a_handler_the_script_replaced_is_taken_back_when_the_region_ends():
    before = _handlers()
    with _guard() as guard:
        with guard.interruptible():
            signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.raise_signal(signal.SIGINT)  # reaches the guard, which only records here
        time.sleep(0.05)
        assert guard.cancelled
    assert _handlers() == before


# --- the guard under signals landing at its own check points (review 6) ---------------------------

# Where CPython 3.12-3.14 handles a pending signal while it runs Python code: a function's entry
# (RESUME, seen here as a PY_START event), a backward jump, and the CALL family (a C function called
# there can check too). Injecting before each of those instructions, one run per point, approximates
# the states a real signal can find a frame in: a call's own check comes once the function it called
# has returned, a state the injection before the call does not reach (review 9, honesty-4), and the
# injection before it reaches one a real signal reaches only when a trace or profile function runs
# there. An injection at an instruction CPython never checks at (a NOP left by a with-block's
# SETUP/POP_BLOCK) would test a state no signal can cause.
CHECK_POINT_OPS = ("JUMP_BACKWARD", "CALL", "CALL_KW", "CALL_FUNCTION_EX")


def _check_points(code) -> set[int]:
    import dis  # noqa: PLC0415

    return {i.offset for i in dis.get_instructions(code)
            if i.opname in CHECK_POINT_OPS or i.opname.startswith("CALL_")}


def _code_objects(*functions) -> set:
    found, stack = set(), [getattr(getattr(f, "__func__", f), "__code__", None) for f in functions]
    while stack:
        code = stack.pop()
        if code is not None and code not in found:
            found.add(code)
            stack += [c for c in code.co_consts if isinstance(c, types.CodeType)]
    return found


class _Injector:
    """Call `deliver` at the n-th check point of the watched code, once, while `on` is set: every
    check point, or only the entries of the watched functions (`entries_only`)."""

    def __init__(self, codes, deliver, *, entries_only=False, no_entry=()):
        self.monitoring = sys.monitoring
        self.tool = next(i for i in range(2, 6) if self.monitoring.get_tool(i) is None)
        self.codes = {code: set() if entries_only else _check_points(code) for code in codes}
        self.no_entry = set(no_entry)  # functions whose entry is not a point here
        self.deliver, self.on, self.count, self.target, self.fired = deliver, False, 0, None, False

    def _point(self):
        if not self.on:
            return
        self.count += 1
        if self.target is None:
            self.deliver()  # a deliverer that decides by self.count itself
        elif self.count == self.target and not self.fired:
            self.fired = True
            self.deliver()

    def _start(self, code, _offset):
        if code in self.codes and code not in self.no_entry:
            self._point()

    def _instruction(self, code, offset):
        if offset in self.codes.get(code, ()):
            self._point()

    def __enter__(self):
        events = self.monitoring.events
        self.monitoring.use_tool_id(self.tool, "labkit-guard-injection")
        self.monitoring.register_callback(self.tool, events.PY_START, self._start)
        self.monitoring.register_callback(self.tool, events.INSTRUCTION, self._instruction)
        for code in self.codes:
            self.monitoring.set_local_events(self.tool, code, events.PY_START | events.INSTRUCTION)
        return self

    def __exit__(self, *exc_info):
        events = self.monitoring.events
        for code in self.codes:
            self.monitoring.set_local_events(self.tool, code, 0)
        self.monitoring.register_callback(self.tool, events.PY_START, None)
        self.monitoring.register_callback(self.tool, events.INSTRUCTION, None)
        self.monitoring.free_tool_id(self.tool)
        return False

    def arm(self, target):
        self.count, self.target, self.fired = 0, target, False


NEEDS_MONITORING = pytest.mark.skipif(not hasattr(sys, "monitoring"),
                                      reason="sys.monitoring is Python 3.12+")
POSIX_GUARD = pytest.mark.skipif(os.name == "nt", reason="the POSIX guard's signals")


def _deliverable(sig) -> bool:
    current = signal.getsignal(sig)
    return current is signal.default_int_handler or \
        isinstance(getattr(current, "__self__", None), _platform._RunGuard)


def _handler_path(guard_cls) -> set:
    """The code a guard's signal handler runs: its entry and everything it calls."""
    return _code_objects(guard_cls.on_cancel_signal, guard_cls._stop, _platform._in_exit,
                         guard_cls._deadline_passed, getattr(guard_cls, "_on_alarm", None),
                         getattr(guard_cls, "_on_sigint", None), guard_cls.disarm,
                         guard_cls.disarm_now)


GUARD_SIGNALS = ("SIGINT", "SIGTERM", "SIGALRM")


@NEEDS_MONITORING
@POSIX_GUARD
@pytest.mark.parametrize("second", GUARD_SIGNALS)
@pytest.mark.parametrize("first", GUARD_SIGNALS)
def test_a_signal_handled_inside_the_first_handler_never_spends_the_interrupt(first, second):
    """Review 6, SG-3: a second signal handled while the first handler notified its callback took
    the one-shot and was swallowed with it, and the script ran to its end with a `cancelled` or
    `timeout` record. The second signal is delivered at each check point of the first handler in
    turn — its entry and every call and backward jump in it and in what it calls — and the region
    is interrupted every time."""
    first_sig, second_sig = getattr(signal, first), getattr(signal, second)
    before = _handlers()
    reached = 0
    with _Injector(_handler_path(type(_guard())),
                   lambda: _deliverable(second_sig) and signal.raise_signal(second_sig)) \
            as injector:
        for target in range(1, 200):
            ran_on = False
            injector.arm(target)
            with _guard() as guard:
                try:
                    with guard.interruptible(deadline=30.0):
                        injector.on = True
                        signal.raise_signal(first_sig)
                        injector.on = False
                        ran_on = True
                except _platform.Interrupted:
                    pass
                finally:
                    injector.on = False
                assert not guard.raising and not guard.armed, (target, first, second)
            assert not ran_on, f"the region ran on past the first signal (point {target})"
            assert guard.cancelled or guard.expired, (target, _outcome(guard))
            if not injector.fired:
                break
            reached += 1
    assert reached >= 5, reached
    assert _handlers() == before


def _script_s_own(sig) -> bool:
    handler = signal.getsignal(sig)
    return callable(handler) and not isinstance(getattr(handler, "__self__", None),
                                                _platform._RunGuard)


def _inside_a_guard_handler() -> bool:
    """Whether a guard's signal handler is running: the points of the handler path that the
    region's exit also passes (disarming, the deadline's time check) are not the handler's."""
    handlers = {f.__code__ for f in (_platform._RunGuard.on_cancel_signal,
                                     _platform._PosixGuard._on_alarm,
                                     _platform._WindowsGuard._on_sigint)}
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code in handlers:
            return True
        frame = frame.f_back
    return False


@NEEDS_MONITORING
@POSIX_GUARD
@pytest.mark.parametrize("script", ["ends", "catches"])
@pytest.mark.parametrize("first", ["SIGINT", "SIGTERM", "SIGINT_past_its_deadline", "SIGALRM",
                                   "SIGALRM_before_its_time"])
def test_a_script_s_raising_handler_run_inside_the_guard_s_handler_cannot_take_its_flag(first,
                                                                                      script):
    """Review 7, guard-1 and critic-1: the guard's handler told the runner what happened only after
    deciding, through a callback, and a second signal taken inside it by a handler the script left
    installed — the graceful-shutdown idiom, `SIGTERM -> sys.exit(0)` — either left the handler
    before that or was swallowed by the callback, so a run a cancel had cut short was recorded `ok`
    and one past its deadline `cancelled` or `error`. The handler now sets its flag by its first
    statement, and settle finds a cancel cut short at the handler's entry, before that statement, in
    the traceback of what cut it short (review 8, guard-4). The script's signal is delivered at each
    check point of the handler in turn, and the first signal is recorded every time. A cancel that
    comes after its deadline counts the deadline at every point too (review 8, honesty-5: counted
    once the one-shot was spent, it was lost where the script's handler ran in between). The one
    loss left: a SIGALRM that is not the guard's timer — raised here before the clock's deadline —
    cut short at the alarm handler's entry. A script that catches what its own handler raised and
    carries on (`catches`) leaves settle nothing to find, so there a cancel cut short at the entry is
    lost as well: the handler's first-statement assignment is what counts it everywhere else."""
    first_sig = getattr(signal, first.split("_")[0])
    script_sig = signal.SIGTERM if first != "SIGTERM" else signal.SIGUSR1
    before = _handlers()
    saved = signal.getsignal(script_sig)
    reached, lost = 0, []
    with _Injector(_handler_path(type(_guard())),
                   lambda: _inside_a_guard_handler() and _script_s_own(script_sig)
                   and signal.raise_signal(script_sig)) as injector:
        for target in range(1, 200):
            ran_on, ended, caught = False, None, None
            injector.arm(target)
            try:
                with _guard() as guard:
                    try:
                        with guard.interruptible(deadline=30.0):
                            signal.signal(script_sig, lambda *a: sys.exit(0))  # the script's
                            if first != "SIGINT" and first != "SIGTERM":
                                # The alarm is raised by hand below, or the script took SIGALRM over.
                                signal.setitimer(signal.ITIMER_REAL, 0)
                            if first in ("SIGALRM", "SIGINT_past_its_deadline"):
                                guard.deadline_at = time.monotonic() - 0.001  # the deadline came
                            injector.on = True
                            try:
                                signal.raise_signal(first_sig)
                            except SystemExit:
                                if script == "ends":
                                    raise
                            injector.on = False
                            ran_on = True
                    except (_platform.Interrupted, SystemExit) as exc:
                        ended, caught = type(exc).__name__, exc
                    finally:
                        injector.on = False
                    assert guard.settle(caught) is None, target
            finally:
                signal.signal(script_sig, saved)
            if script == "ends":
                assert not ran_on, f"the region ran on past the first signal (point {target})"
                assert ended in ("Interrupted", "SystemExit"), (target, ended)
            else:  # interrupted, or cut short and carried on
                assert (ended == "Interrupted") != ran_on, (target, ended, ran_on)
            recorded = {"SIGINT": guard.cancelled, "SIGTERM": guard.cancelled,
                        "SIGINT_past_its_deadline": guard.cancelled and guard.expired}.get(
                            first, guard.expired)
            if not recorded:
                lost.append(target)
            if not injector.fired:
                break
            reached += 1
    assert reached >= 5, reached
    lost_at_the_entry = first == "SIGALRM_before_its_time" or (script == "catches"
                                                              and first != "SIGALRM")
    assert lost == ([1] if lost_at_the_entry else []), (first, script, lost)
    assert _handlers() == before


@NEEDS_MONITORING
@POSIX_GUARD
@pytest.mark.parametrize("second", ["guard", "script", "script_caught"])
@pytest.mark.parametrize("first", ["cancel", "deadline"])
def test_the_windows_guard_s_handler_under_a_second_signal(monkeypatch, first, second):
    """The same two properties for the Windows guard's SIGINT handler, with its delivery replaced
    (review 7, guard-7 and A04): a second SIGINT inside it only records and the region is
    interrupted once; and a script's raising handler run inside it (SIGTERM, standing in for a
    Ctrl+Break the script took over) cannot take the flag — a deadline is counted by the timer
    itself before its SIGINT is raised, a cancel by the handler's first statement, or at the
    handler's entry by settle, from the traceback of what cut it short (review 8, guard-4). Where
    the script catches what its handler raised (`script_caught`), settle has nothing to find: the
    first statement is all that counts the cancel, and only the entry is lost."""
    guard_cls = _platform._WindowsGuard
    before = _handlers()
    saved = signal.getsignal(signal.SIGTERM)
    reached, lost = 0, []

    def deliver():
        if second == "guard":
            return _deliverable(signal.SIGINT) and signal.raise_signal(signal.SIGINT)
        return _inside_a_guard_handler() and _script_s_own(signal.SIGTERM) \
            and signal.raise_signal(signal.SIGTERM)

    def the_first_sigint():
        try:
            signal.raise_signal(signal.SIGINT)
        except SystemExit:
            if second != "script_caught":
                raise

    with _Injector(_handler_path(guard_cls), deliver) as injector:
        for target in range(1, 200):
            ran_on, ended = False, None
            injector.arm(target)
            try:
                with _windows_guard(monkeypatch) as guard:
                    caught = None
                    try:
                        with guard.interruptible(deadline=30.0):
                            if second != "guard":
                                signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
                            if first == "deadline":
                                guard._timer.cancel()
                                _fired_without_its_sigint(monkeypatch, guard)
                            injector.on = True
                            the_first_sigint()
                            injector.on = False
                            ran_on = True
                    except (_platform.Interrupted, SystemExit) as exc:
                        ended, caught = type(exc).__name__, exc
                    finally:
                        injector.on = False
                    guard.settle(caught)
                    assert not guard.raising and not guard.armed, target
            finally:
                signal.signal(signal.SIGTERM, saved)
            if second == "script_caught":  # interrupted, or cut short and carried on
                assert (ended == "Interrupted") != ran_on, (target, ended, ran_on)
            else:
                assert not ran_on, f"the region ran on past the first signal (point {target})"
                assert ended in ("Interrupted", "SystemExit"), (target, ended)
            recorded = guard.expired if first == "deadline" else guard.cancelled
            if not recorded:
                lost.append(target)
            if not injector.fired:
                break
            reached += 1
    assert reached >= 5, reached
    assert lost == ([1] if (first, second) == ("cancel", "script_caught") else []), \
        (first, second, lost)
    assert _handlers() == before


@NEEDS_MONITORING
@pytest.mark.parametrize("where", ["region_exit", "settle", "_settle"])
def test_a_signal_at_the_start_of_the_guard_s_own_exit_only_records(where):
    """The first instruction of a region's exit, or of settle, runs before either has stopped the
    raising. A signal handled there used to raise into the guard's own exit and, from settle's
    entry, past the runner (review 6, SG-1); now it records and stops the raising."""
    function = {"region_exit": _platform._Region.__exit__, "settle": _platform._RunGuard.settle,
                "_settle": _platform._RunGuard._settle}[where]
    before = _handlers()
    injector = _Injector({function.__code__}, lambda: signal.raise_signal(signal.SIGINT),
                         entries_only=True)
    with injector, _guard() as guard:
        region = guard.interruptible(deadline=30.0)
        region.__enter__()
        injector.arm(1)
        injector.on = True
        try:
            if where == "region_exit":
                region.__exit__(None, None, None)
            else:
                getattr(guard, where)()
        finally:
            injector.on = False
        assert injector.fired
        assert _outcome(guard) == {"timeout": False, "cancel": True}
        assert not guard.raising and not guard.armed
        assert guard.settle() is None
    assert _handlers() == before


@NEEDS_MONITORING
@pytest.mark.parametrize("platform_guard", ["posix", "windows"])
def test_a_deadline_handled_at_the_start_of_the_region_s_exit_still_counts(monkeypatch,
                                                                          platform_guard):
    """The deadline's signal can be pending until the first instruction of the region's exit, as
    the script ends. The guard does not raise there, and must not decide there either: had it
    stopped the raising, settle would no longer count a deadline that had passed."""
    if platform_guard == "posix" and not hasattr(signal, "setitimer"):
        pytest.skip("SIGALRM is POSIX")
    if platform_guard == "windows":
        guard = _windows_guard(monkeypatch)
        sig = signal.SIGINT
    else:
        guard = _guard()
        sig = signal.SIGALRM
    before = _handlers()
    injector = _Injector({_platform._Region.__exit__.__code__}, lambda: signal.raise_signal(sig),
                         entries_only=True)
    with injector, guard:
        with guard.interruptible(deadline=1.0):
            # The kernel's alarm, or the Windows timer's SIGINT, not handled before the script ends:
            # the timer is stopped by hand and what it would have done is done at the exit.
            if platform_guard == "windows":
                guard._timer.cancel()
                _fired_without_its_sigint(monkeypatch, guard)
            else:
                signal.setitimer(signal.ITIMER_REAL, 0)
                time.sleep(1.1)
            injector.arm(1)
            injector.on = True
        injector.on = False
        assert injector.fired
        guard.settle()
    assert _outcome(guard) == {"timeout": True, "cancel": False}, _outcome(guard)
    assert _handlers() == before


LEAVES_RAISING_SIGTERM = ("import signal, sys\n"
                          "signal.signal(signal.SIGTERM, lambda *a: sys.exit(1))\n"
                          "print('left a raising SIGTERM handler')\n")


@NEEDS_MONITORING
@POSIX_GUARD
@pytest.mark.parametrize("then", ["cancel", "deadline", "again_in_settle"])
def test_a_script_s_raising_handler_at_the_region_s_end_cannot_lose_the_record(project,
                                                                             monkeypatch, then):
    """Review 6, SG-1: a signal taken at the region's exit by a handler the script left behind cut
    the exit short with the region still raising and its deadline armed, and the settle after it
    was skipped, so a cancel — or the deadline alone — during _finalize left the record `running`.
    Now the settle runs whatever the region raised."""
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    delivered = []

    def at_the_script_region_s_exit():
        # The second region's exit; the handler SIGTERM has there is the one the script left.
        assert not isinstance(getattr(signal.getsignal(signal.SIGTERM), "__self__", None),
                              _platform._RunGuard)
        delivered.append(1)
        signal.raise_signal(signal.SIGTERM)

    real_environment = runner.environment

    def slow_environment(project_):
        if then == "cancel":
            signal.raise_signal(signal.SIGINT)
        elif then == "deadline":
            time.sleep(1.5)  # past the region's one-second deadline
        return real_environment(project_)

    monkeypatch.setattr(runner, "environment", slow_environment)
    if then == "again_in_settle":
        # The runner's settle, with the region's exit cut short, finds the script's handler still
        # in place; a second SIGTERM taken by it there must not escape settle.
        real_getsignal = signal.getsignal
        again = []

        def getsignal(sig):
            if sys._getframe(1).f_code is _platform._RunGuard._reassert.__code__ and not again \
                    and delivered and callable(real_getsignal(signal.SIGTERM)) \
                    and not isinstance(getattr(real_getsignal(signal.SIGTERM), "__self__", None),
                                       _platform._RunGuard):
                again.append(1)
                signal.raise_signal(signal.SIGTERM)
            return real_getsignal(sig)

        monkeypatch.setattr(signal, "getsignal", getsignal)
    injector = _Injector({_platform._Region.__exit__.__code__}, at_the_script_region_s_exit,
                         entries_only=True)
    before = _handlers()
    with injector:
        injector.arm(2)
        injector.on = True
        try:
            result = runner.execute(core.Project(project),
                                    script=write_script(project, "40_left.py",
                                                        LEAVES_RAISING_SIGTERM),
                                    title="남긴 핸들러", actor="test", timeout=1.0)
        finally:
            injector.on = False
    assert delivered == [1]
    if then == "again_in_settle":
        assert again == [1], "the second signal never reached the runner's settle"
    assert result["run"]["status"] == "error", result["run"]  # SystemExit(1), the script's handler
    statuses = [r["status"] for r in core.list_runs(core.Project(project))]
    assert "running" not in statuses and "incomplete" not in statuses, statuses
    assert _handlers() == before


DEFAULT_INT_HANDLER_SCRIPT = ("import signal\n"
                              "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
                              "print('handed SIGINT back to KeyboardInterrupt')\n")


def test_what_a_script_s_handler_raised_during_the_reassertion_is_classified(project, monkeypatch):
    """Review 6, CH-12: the region's exit reasserted the guard's handlers and dropped what a handler
    the script left raised meanwhile, so a cancel taken by the script's default_int_handler at that
    moment was recorded `ok`. It is now handed to the runner, which records `cancelled`."""
    from labkit import runner  # noqa: PLC0415

    real_getsignal = signal.getsignal
    fired = []

    def getsignal(sig):
        caller = sys._getframe(1).f_code
        if caller is _platform._RunGuard._reassert.__code__ and not fired and \
                real_getsignal(signal.SIGINT) is signal.default_int_handler:
            fired.append(1)
            signal.raise_signal(signal.SIGINT)  # taken by the script's handler: KeyboardInterrupt
        return real_getsignal(sig)

    monkeypatch.setattr(signal, "getsignal", getsignal)
    result = runner.execute(core.Project(project),
                            script=write_script(project, "41_kbint.py", DEFAULT_INT_HANDLER_SCRIPT),
                            title="재지정 중 취소", actor="test")
    assert fired, "the script's handler was never found installed during the reassertion"
    assert result["run"]["status"] == "cancelled", result["run"]


class HostShutdown(Exception):  # noqa: N818 - named as the idiom it stands for
    """What a host's graceful-shutdown handler raises."""


@POSIX_GUARD
@pytest.mark.parametrize("raising", ["SIGTERM", "SIGALRM", "both"])
def test_a_host_handler_that_raises_during_the_restore_still_gets_every_handler_back(
        project, monkeypatch, raising):
    """Review 6, SG-2 and CH-1: the restore stopped swapping after the first interrupted swap, so a
    host whose SIGTERM handler raises — the graceful-shutdown idiom — kept the guard's handler for
    SIGINT after the run and its Ctrl+C was swallowed. The signal is delivered before each swap a
    run makes, in turn, into whichever handler is installed at that moment, the host's included."""
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    # With two raising handlers, one put back before the other can interrupt the second's swap,
    # which is what a restore that stops after its first interruption got wrong.
    sig = signal.SIGTERM if raising == "both" else getattr(signal, raising)
    hosts = (signal.SIGTERM, signal.SIGALRM) if raising == "both" else (sig,)

    def host_handler(signum, frame):
        raise HostShutdown(signum)

    real_signal = signal.signal
    script = write_script(project, "42_host.py", "x = 1\n")
    counted = []
    saved = {s: real_signal(s, host_handler) for s in hosts}
    try:
        monkeypatch.setattr(signal, "signal",
                            lambda s, h: (counted.append(s), real_signal(s, h))[1])
        runner.execute(core.Project(project), script=script, title="셈", actor="test")
        monkeypatch.setattr(signal, "signal", real_signal)
        total = len(counted)
        for nth in range(1, total + 1):
            seen = []

            def injecting(signum, handler, nth=nth, seen=seen):
                seen.append(signum)
                current = signal.getsignal(sig)
                if len(seen) == nth and (current is host_handler or _deliverable(sig)):
                    signal.raise_signal(sig)
                return real_signal(signum, handler)

            before = _handlers()
            monkeypatch.setattr(signal, "signal", injecting)
            try:
                runner.execute(core.Project(project), script=script, title=f"{raising}-{nth}",
                               actor="test")
            except HostShutdown:
                pass  # the host's own handler, after the run: the host's choice
            finally:
                monkeypatch.setattr(signal, "signal", real_signal)
            assert _handlers() == before, (raising, nth, _handlers(), before)
    finally:
        for s, handler in saved.items():
            real_signal(s, handler)
    statuses = [r["status"] for r in core.list_runs(core.Project(project))]
    assert "running" not in statuses and "incomplete" not in statuses, statuses


@NEEDS_MONITORING
@POSIX_GUARD
def test_a_raising_host_handler_at_any_check_point_of_the_restore_leaves_nothing_behind():
    """CPython leaves a loop's backward jump inside a `try` out of the exception table, so a raising
    handler run there skipped the restore's retry and left the guard's handlers installed after the
    run (found by the rev3.1 sweep, raising host variant). A signal for the host's raising handler
    at every check point of the restore, in turn: every handler comes back."""
    codes = _code_objects(_platform._RunGuard._restore, _platform._RunGuard._put_back,
                          _platform._swap)

    def host(signum, frame):
        raise HostShutdown(signum)

    injector = _Injector(codes, lambda: callable(signal.getsignal(signal.SIGTERM))
                         and signal.raise_signal(signal.SIGTERM))
    saved = {s: signal.signal(s, host) for s in (signal.SIGTERM, signal.SIGALRM)}
    reached = 0
    try:
        with injector:
            for target in range(1, 200):
                before = _handlers()
                injector.arm(target)
                try:
                    with _guard():
                        injector.on = True  # from here to the end of the guard's exit
                except HostShutdown:
                    pass  # raised once everything is back, as the host's handler wants
                finally:
                    injector.on = False
                assert _handlers() == before, (target, _handlers())
                if not injector.fired:
                    break
                reached += 1
    finally:
        for s, handler in saved.items():
            signal.signal(s, handler)
    assert reached >= 10, reached


@POSIX_GUARD
def test_the_restore_puts_back_the_handlers_that_cannot_raise_first(monkeypatch):
    """Until the first Python callable is back, only the guard's record-only handlers and handlers
    that raise nothing are installed, so nothing can interrupt the restore (review 7, A01)."""
    host = signal.signal(signal.SIGTERM, lambda *a: None)
    ignored = signal.signal(signal.SIGALRM, signal.SIG_IGN)
    order = []
    real_signal = signal.signal
    try:
        with _guard():
            monkeypatch.setattr(signal, "signal",
                                lambda sig, handler: (order.append(sig), real_signal(sig, handler))[1])
        monkeypatch.setattr(signal, "signal", real_signal)
        assert order == [signal.SIGALRM, signal.SIGTERM, signal.SIGINT], order
    finally:
        signal.signal(signal.SIGTERM, host)
        signal.signal(signal.SIGALRM, ignored)


@POSIX_GUARD
def test_a_host_handler_raising_oserror_at_a_swap_does_not_stop_the_restore(monkeypatch):
    """`signal.signal` runs a pending handler before it swaps, and a host handler can raise any
    exception there — an OSError too, which revision 3 took for a swap that cannot be done, and
    left the guard's handler in place (review 7, A06)."""
    def host(signum, frame):
        raise OSError("the host's own handler")

    saved = signal.signal(signal.SIGTERM, host)
    real_signal = signal.signal
    pending = []

    def swap(sig, handler):
        if sig == signal.SIGINT and not pending and signal.getsignal(signal.SIGTERM) is host:
            pending.append(1)
            # Held back and let through here: the host's handler runs inside this call, as it
            # would when `signal.signal` handles a pending signal before it swaps.
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
            signal.raise_signal(signal.SIGTERM)
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
        return real_signal(sig, handler)

    try:
        before = _handlers()
        with pytest.raises(OSError, match="the host's own handler"):
            with _guard():
                monkeypatch.setattr(signal, "signal", swap)
        monkeypatch.setattr(signal, "signal", real_signal)
        assert pending == [1]
        assert _handlers() == before
    finally:
        monkeypatch.setattr(signal, "signal", real_signal)
        signal.signal(signal.SIGTERM, saved)


@POSIX_GUARD
def test_a_handler_object_that_claims_equality_is_still_replaced():
    """The reassertion asks `is not`, never `!=`: a script's handler object decides what `==`
    means, and one that claims to equal everything kept the guard's handler out (review 7, A07)."""
    class Sneaky:
        def __eq__(self, other):
            return True

        __hash__ = object.__hash__

        def __call__(self, *args):
            pass

    with _guard() as guard:
        with guard.interruptible():
            signal.signal(signal.SIGINT, Sneaky())
        assert isinstance(getattr(signal.getsignal(signal.SIGINT), "__self__", None),
                          _platform._RunGuard)
        signal.raise_signal(signal.SIGINT)
        assert guard.cancelled


def _settle_codes():
    return _code_objects(_platform._RunGuard._settle, _platform._RunGuard._reassert,
                         _platform._RunGuard._deadline_passed, _platform._PosixGuard.disarm)


def _script_takes_sigint():
    if signal.getsignal(signal.SIGINT) is signal.default_int_handler:
        signal.raise_signal(signal.SIGINT)


@NEEDS_MONITORING
@POSIX_GUARD
@pytest.mark.parametrize("deadline", ["ahead", "passed"])
def test_a_raising_script_handler_inside_settling_is_kept_not_raised(deadline):
    """Settling — what a region's exit runs — lets out nothing a handler the script left installed
    raises at any of its check points but its first instruction, the backward jump of its loop
    included (see _ATTEMPTS); it is kept for settle() to return, and the guard's handlers are back.
    A deadline that had passed is counted wherever the interruption lands: settling cleared its
    note of it before the count was made, and a retry then skipped the count (review 7, guard-4 and
    honesty-1)."""
    injector = _Injector(_settle_codes(), _script_takes_sigint,
                         no_entry={_platform._RunGuard._settle.__code__})
    reached = 0
    with injector:
        for target in range(1, 200):
            before = _handlers()
            with _guard() as guard:
                guard.raising = True
                guard.deadline_at = time.monotonic() + (30 if deadline == "ahead" else -0.001)
                signal.signal(signal.SIGINT, signal.default_int_handler)  # left by a script
                injector.arm(target)
                injector.on = True
                try:
                    guard._settle()
                except KeyboardInterrupt:
                    pytest.fail(f"settling let the script's KeyboardInterrupt out (point {target})")
                finally:
                    injector.on = False
                assert isinstance(guard.settle(), (KeyboardInterrupt, type(None))), target
                assert isinstance(getattr(signal.getsignal(signal.SIGINT), "__self__", None),
                                  _platform._RunGuard), target
                assert guard.expired == (deadline == "passed"), (target, _outcome(guard))
            assert _handlers() == before, target
            if not injector.fired:
                break
            reached += 1
    assert reached >= 5, reached


@NEEDS_MONITORING
@POSIX_GUARD
def test_the_runner_s_settle_survives_a_second_signal_after_the_region_s_exit_was_cut_short():
    """The first signal, taken by the script's handler at the first instruction of the region's
    settling, cuts the region's exit short (the runner catches what it raised); a second one at any
    check point of the runner's own settle — _settle's entry included — is kept, not raised. Only
    settle()'s own first instruction is left: the stated residual."""
    injector = _Injector(_settle_codes(), _script_takes_sigint)
    reached = 0
    with injector:
        for target in range(2, 200):
            before = _handlers()
            with _guard() as guard:
                delivered = []

                def twice(target=target, delivered=delivered):
                    # the first point is the region's settling entry; the second one is the target
                    if injector.count in (1, target):
                        delivered.append(injector.count)
                        _script_takes_sigint()

                injector.deliver, injector.target = twice, None
                injector.count, injector.fired = 0, False
                failure = None
                try:
                    with guard.interruptible(deadline=30.0):
                        signal.signal(signal.SIGINT, signal.default_int_handler)  # a script's
                        injector.on = True
                except BaseException as exc:  # noqa: BLE001 - as _run_script does
                    failure = exc
                try:
                    caught = guard.settle()
                except KeyboardInterrupt:
                    pytest.fail(f"the runner's settle raised (second signal at point {target})")
                finally:
                    injector.on = False
                assert isinstance(failure, KeyboardInterrupt), (target, failure)
                assert isinstance(caught, (KeyboardInterrupt, type(None))), (target, caught)
                assert isinstance(getattr(signal.getsignal(signal.SIGINT), "__self__", None),
                                  _platform._RunGuard), target
            assert _handlers() == before, target
            if len(delivered) < 2 and injector.count < target:
                break
            reached += 1
    assert reached >= 5, reached


def test_a_deadline_that_passes_after_a_cancel_interrupted_the_region_is_no_timeout():
    """A cancel spends the region's one interruption; its deadline no longer counts, even when the
    script swallowed the cancel and ran past it (review 6, CH-7)."""
    with _guard() as guard:
        with guard.interruptible(deadline=1.0):
            try:
                signal.raise_signal(signal.SIGINT)
                time.sleep(0.5)
            except _platform.Interrupted:
                pass
            time.sleep(1.2)
        assert guard.settle() is None
    assert _outcome(guard) == {"timeout": False, "cancel": True}


@POSIX_GUARD
def test_a_script_that_takes_sigalrm_over_is_still_timed_out():
    """`timeout` means the deadline's signal or its time came while the region could still be
    interrupted, whoever received the signal (RunControl; review 6, SG-6/CH-5): a script that took
    SIGALRM over is not stopped by it, but its run is `timeout`, not `ok`."""
    with _guard() as guard:
        with guard.interruptible(deadline=1.0):
            signal.signal(signal.SIGALRM, lambda *a: None)
            time.sleep(1.5)
        guard.settle()
    assert _outcome(guard) == {"timeout": True, "cancel": False}


@POSIX_GUARD
@pytest.mark.parametrize("script", ["takes_sigalrm", "lends_sigalrm", "cancels_the_timer"])
def test_a_deadline_that_passed_before_a_cancel_is_still_the_run_s_deadline(script):
    """Review 7, guard-2 and honesty-2: a deadline the script kept from the guard, followed by a
    cancel — the extension's own backstop ends such a run that way — was `cancelled` on POSIX and
    `timeout` on the Windows guard. The cancel now counts a deadline that had passed before it."""
    with _guard() as guard:
        with pytest.raises(_platform.Interrupted, match="cancelled"):
            with guard.interruptible(deadline=1.0):
                if script == "takes_sigalrm":
                    signal.signal(signal.SIGALRM, lambda *a: None)
                    time.sleep(1.5)
                elif script == "lends_sigalrm":
                    own = signal.signal(signal.SIGALRM, lambda *a: None)
                    time.sleep(1.5)
                    signal.signal(signal.SIGALRM, own)
                else:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    time.sleep(1.2)
                signal.raise_signal(signal.SIGINT)
                time.sleep(5)
        guard.settle()
    assert _outcome(guard) == {"timeout": True, "cancel": True}


@POSIX_GUARD
def test_a_run_cancelled_after_the_deadline_it_kept_from_the_guard_is_timed_out(project,
                                                                                monkeypatch):
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    body = ("import signal, time\n"
            "signal.signal(signal.SIGALRM, lambda *a: print('the alarm, taken by the script'))\n"
            "time.sleep(1.5)\n"
            "signal.raise_signal(signal.SIGINT)  # the extension's backstop, later on\n"
            "time.sleep(5)\n"
            "print('ran to its end')\n")
    result = runner.execute(core.Project(project), script=write_script(project, "44_late.py", body),
                            title="늦은 취소", actor="test", timeout=1.0)
    assert result["run"]["status"] == "timeout", result["run"]
    log = (Path(project) / "runs" / result["run"]["id"] / "stdout.txt").read_text(encoding="utf-8")
    assert "the alarm" in log and "ran to its end" not in log


def test_a_deadline_the_platform_cannot_hold_is_an_error_not_a_stuck_guard(project, monkeypatch):
    """Review 6, SG-1: `--timeout inf` made the region's entry raise with the region already
    raising, and a Ctrl+C during _finalize then lost the record. It is recorded `error`, as main
    recorded it, and the guard is back to recording only."""
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    real_environment = runner.environment

    def cancel_then_environment(project_):
        signal.raise_signal(signal.SIGINT)
        return real_environment(project_)

    monkeypatch.setattr(runner, "environment", cancel_then_environment)
    host_timer = hasattr(signal, "setitimer")
    if host_timer:  # a host's own timer, which a deadline that was never set must leave alone
        saved = signal.signal(signal.SIGALRM, lambda *a: None)
        signal.setitimer(signal.ITIMER_REAL, 100)
    try:
        before = _handlers()
        result = runner.execute(core.Project(project),
                                script=write_script(project, "43_inf.py", RAN), title="무한",
                                actor="test", timeout=float("inf"))
        assert _handlers() == before
        if host_timer:
            assert signal.getitimer(signal.ITIMER_REAL)[0] > 50, "the host's timer was cancelled"
    finally:
        if host_timer:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, saved)
    assert result["run"]["status"] == "error", result["run"]
    assert result["run"]["error"]["type"] == "OverflowError", result["run"]["error"]
    assert not (Path(project) / "data/derived/ran.txt").exists()


def test_the_windows_guard_refuses_a_deadline_its_timer_cannot_hold():
    # A Timer beyond threading.TIMEOUT_MAX dies in its own thread and leaves the region without a
    # deadline; the guard refuses it before arming, as setitimer does on POSIX.
    guard = _platform._WindowsGuard()
    with pytest.raises(OverflowError):
        guard.arm(float("inf"))
    assert not guard.armed and guard._timer is None


# The Windows guard delivers its deadline as a SIGINT through raise_signal, which only wakes a
# sleeping main thread on Windows. Its logic runs anywhere once the delivery is replaced by what a
# POSIX system has for the same job; the delivery itself is measured on Windows (Issue #8).
def _windows_guard(monkeypatch):
    def deliver():
        if not callable(signal.getsignal(signal.SIGINT)):
            return False
        signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
        return True

    monkeypatch.setattr(_platform, "raise_sigint_in_main", deliver)
    return _platform._WindowsGuard()


def _windows_behaviour(monkeypatch):
    """The Windows guard: itself on Windows, and elsewhere with its delivery simulated."""
    return _guard() if os.name == "nt" else _windows_guard(monkeypatch)


def _fired_without_its_sigint(monkeypatch, guard):
    """What the Windows guard's timer does when the deadline comes, except that the SIGINT it raises
    is left for the test to deliver where it wants."""
    with monkeypatch.context() as patch:
        patch.setattr(_platform, "raise_sigint_in_main", lambda: True)
        guard._fire(guard._timer)
    assert guard.expired and guard.deadline_due


@POSIX_GUARD
@pytest.mark.parametrize("script", ["default_int_handler", "lent_and_given_back", "ignored"])
def test_the_windows_deadline_counts_whoever_took_its_sigint(monkeypatch, script):
    """Review 6, CH-5 (simulated): a script that reset SIGINT to default_int_handler turned the
    deadline into `cancelled`; one that lent SIGINT to a handler and gave it back left a flag that
    turned a later cancel into a deadline the region never had; one that ignored SIGINT ran past
    its deadline as `ok`. The deadline passed in all three: each is `timeout`."""
    before = _handlers()
    t0 = time.perf_counter()
    outcome = None
    with _windows_guard(monkeypatch) as guard:
        try:
            with guard.interruptible(deadline=1.0):
                if script == "default_int_handler":
                    signal.signal(signal.SIGINT, signal.default_int_handler)
                    time.sleep(5)
                elif script == "lent_and_given_back":
                    lent = []
                    own = signal.signal(signal.SIGINT, lambda *a: lent.append(1))
                    time.sleep(1.5)  # the deadline's SIGINT goes to the lent handler
                    signal.signal(signal.SIGINT, own)
                    assert lent == [1]
                    signal.raise_signal(signal.SIGINT)  # a cancel, after the deadline passed
                    time.sleep(5)
                else:
                    signal.signal(signal.SIGINT, signal.SIG_IGN)
                    time.sleep(1.5)
            outcome = "ran to its end"
        except (KeyboardInterrupt, _platform.Interrupted) as exc:
            outcome = type(exc).__name__
        guard.settle()
    assert guard.expired, (script, outcome, _outcome(guard))
    assert time.perf_counter() - t0 < 4, "the region ran on"
    assert outcome == {"default_int_handler": "KeyboardInterrupt",
                       "lent_and_given_back": "Interrupted",
                       "ignored": "ran to its end"}[script]
    assert _handlers() == before


@NEEDS_MONITORING
@POSIX_GUARD
def test_a_windows_deadline_sigint_handled_while_settling_is_not_a_cancel(monkeypatch):
    """The timer can count the deadline and raise its SIGINT as the region ends; the SIGINT is then
    handled inside settle, after settle has stopped the raising. It is the deadline's, counted by the
    timer already — a Windows timer may wake a little before the region's own clock says the
    deadline has passed, so the time alone is not relied on — and it is not a cancel."""
    guard = _windows_guard(monkeypatch)
    injector = _Injector({_platform._RunGuard._deadline_passed.__code__},
                         lambda: signal.raise_signal(signal.SIGINT), entries_only=True)
    with injector, guard:
        with guard.interruptible(deadline=30.0):  # far off: only the timer can count it
            guard._timer.cancel()
            _fired_without_its_sigint(monkeypatch, guard)  # its SIGINT lands inside settle
            injector.arm(1)
            injector.on = True
        injector.on = False
        assert injector.fired
        assert guard.deadline_due, "the mark was used up inside the guard's own exit"
        guard.settle()
    assert _outcome(guard) == {"timeout": True, "cancel": False}, _outcome(guard)


@POSIX_GUARD
def test_a_windows_deadline_sigint_pending_past_settle_is_not_a_cancel(monkeypatch):
    # Settle leaves the mark: a SIGINT the timer raised that is handled only after settle is still
    # the deadline's.
    with _windows_guard(monkeypatch) as guard:
        with guard.interruptible(deadline=30.0):
            guard._timer.cancel()
            _fired_without_its_sigint(monkeypatch, guard)
        guard.settle()
        signal.raise_signal(signal.SIGINT)  # the timer's, handled late
        assert not guard.deadline_due
    assert _outcome(guard) == {"timeout": True, "cancel": False}, _outcome(guard)


@POSIX_GUARD
def test_a_windows_deadline_sigint_after_the_region_is_not_a_cancel(monkeypatch):
    # The timer marks its SIGINT before raising it; one handled once the region has ended — the
    # timer fired as the region left — must not be recorded as a cancel nobody asked for.
    with _windows_guard(monkeypatch) as guard:
        guard.deadline_due = True
        signal.raise_signal(signal.SIGINT)
        assert not guard.deadline_due
        signal.raise_signal(signal.SIGINT)  # a real one this time
    assert _outcome(guard) == {"timeout": False, "cancel": True}


@POSIX_GUARD
def test_a_later_region_s_deadline_does_not_inherit_an_earlier_mark(monkeypatch):
    """A mark an earlier region's timer left — its SIGINT taken by a script's handler — is not the
    next region's: that region's entry resets it, so a cancel there is a cancel (review 7, A14)."""
    with _windows_guard(monkeypatch) as guard:
        with guard.interruptible(deadline=30.0):
            guard._timer.cancel()
            _fired_without_its_sigint(monkeypatch, guard)
        guard.settle()
        assert guard.deadline_due  # nobody's SIGINT used it up
        with pytest.raises(_platform.Interrupted, match="cancelled"):
            with guard.interruptible(deadline=30.0):
                assert not guard.deadline_due
                signal.raise_signal(signal.SIGINT)
                time.sleep(1)
        guard.settle()
    assert guard.cancelled


def _calls_until(calls, n, timeout=5.0):
    end = time.monotonic() + timeout
    while len(calls) < n and time.monotonic() < end:
        time.sleep(0.01)
    return len(calls)


def test_the_windows_watcher_retries_a_decline_delivers_once_and_ends_with_its_caller(
        monkeypatch, tmp_path):
    # Its delivery is raise_signal, which only wakes a sleeping main thread on Windows; the logic
    # around it runs anywhere, with the delivery replaced.
    answers, calls = [False, False, True], []

    def deliver():
        calls.append(time.monotonic())
        return answers.pop(0) if answers else True

    monkeypatch.setattr(_platform, "raise_sigint_in_main", deliver)
    monkeypatch.setattr(_platform, "CANCEL_RETRY_SECONDS", 0.05)
    request = tmp_path / "cancel"
    stop = _platform.WindowsRunControl().watch_for_cancel(request)
    try:
        time.sleep(0.3)
        assert calls == [], "delivered before anything asked"
        request.touch()
        assert _calls_until(calls, 3) == 3, "a declined delivery was not retried"
        time.sleep(0.3)
        assert len(calls) == 3, "delivered more than once"
    finally:
        stop()


def test_the_windows_watcher_sees_an_early_request_and_nothing_after_it_stopped(monkeypatch,
                                                                              tmp_path):
    calls = []
    monkeypatch.setattr(_platform, "raise_sigint_in_main", lambda: calls.append(1) or True)
    early = tmp_path / "early"
    early.touch()
    stop = _platform.WindowsRunControl().watch_for_cancel(early)
    assert _calls_until(calls, 1, timeout=1.0) == 1, "a request made before the start was missed"
    stop()
    late = tmp_path / "late"
    stop = _platform.WindowsRunControl().watch_for_cancel(late)
    stop()
    late.touch()
    time.sleep(0.4)
    assert calls == [1], "delivered after the caller had stopped watching"


def test_posix_has_nothing_to_watch(tmp_path):
    # Its ask is a signal (PosixProcessControl.interrupt), so no thread is started for a file.
    before = threading.active_count()
    stop = _platform.PosixRunControl().watch_for_cancel(tmp_path / "cancel")
    assert threading.active_count() == before and stop() is None


# --- the injection seam -----------------------------------------------------------------------------

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


@pytest.fixture
def real_ports_refuse(monkeypatch):
    """Make every real implementation fail, so a path that quietly falls back to the process
    default cannot pass by producing the same record.

    Every public method of every implementation is poisoned rather than a chosen few, so a port
    that grows a method does not silently fall outside this."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("the real platform implementation was used despite an injected one")

    poisoned = 0
    for cls in IMPLEMENTATIONS:
        for name in port_methods(cls):
            monkeypatch.setattr(cls, name, refuse, raising=True)
            poisoned += 1
    assert poisoned >= 18, f"only {poisoned} implementation methods were poisoned"


CONTEXT_VERSION_RE = re.compile(r"^\d{8}-\d{6}-\d{6}_")


def _stable(value):
    """Drop the wall-clock part of a context version so two runs of the same flow compare.

    The content digest that follows it is kept: that is what has to be identical."""
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items()}
    if isinstance(value, str):
        return CONTEXT_VERSION_RE.sub("<when>_", value)
    return value


def _representative_flow(root: Path, source: Path, platform) -> dict:
    """Create a project, import data, write a journal entry and read the project back."""
    project = core.init_project(root, title="이식 경계", goal="목표", template_dir=KIT / "labkit" / "template",
                                actor="test", platform=platform)
    from labkit import data  # noqa: PLC0415 - needs the analysis libraries, unlike core

    data.import_file(project, source, name="sales", description="test data", actor="test")
    core.add_note(project, "finding", "경계 뒤에서도 같은 기록", refs=["sales"], actor="test")
    status = core.status(project)
    events = [{k: _stable(e[k]) for k in ("id", "kind", "actor", "text", "refs")}
              for e in core.read_events(project)]
    return {"events": events, "datasets": [{k: d.get(k) for k in ("name", "path", "kind", "rows",
                                                                  "sha256")}
                                           for d in status["datasets"]],
            "views": status["views"], "counts": status["counts"]}


def test_the_injected_platform_is_the_one_actually_used(tmp_path, real_ports_refuse):
    # Every real implementation is armed to fail, so a path that quietly falls back to the process
    # default cannot pass by producing the same record.
    source = write_sales_csv(tmp_path / "sales.csv")
    fake, calls = recording_platform()
    _representative_flow(tmp_path / "injected", source, fake)

    assert {name for name, _ in calls} >= {"locks.exclusive", "files.replace"}, calls
    assert any(arg == "sales.parquet" for name, arg in calls if name == "files.replace"), calls
    assert any(arg == "lock" for name, arg in calls if name == "locks.exclusive"), calls
    assert any(arg == "lab.json" for name, arg in calls if name == "files.replace"), calls


def test_the_real_and_the_injected_platform_record_the_same_thing(tmp_path):
    source = write_sales_csv(tmp_path / "sales.csv")
    fake, _calls = recording_platform()
    with_real = _representative_flow(tmp_path / "real", source, _platform.detect())
    with_fake = _representative_flow(tmp_path / "fake", source, fake)
    assert with_real == with_fake


def test_a_run_goes_through_the_injected_platform_too(tmp_path, real_ports_refuse):
    from labkit import runner  # noqa: PLC0415 - needs the analysis libraries

    fake, calls = recording_platform()
    project = core.init_project(tmp_path / "p", title="실행 경계", template_dir=KIT / "labkit" / "template",
                                actor="test", platform=fake)
    project.scripts.mkdir(parents=True, exist_ok=True)
    (project.scripts / "01_min.py").write_text("print('ok')\n", encoding="utf-8")
    result = runner.execute(project, script="scripts/01_min.py", title="최소 실행", actor="test")

    assert result["run"]["status"] == "ok", result["run"].get("error")
    # A run swallows a failure in the parts that come after the script — the report build and the
    # bookkeeping — so a poisoned real port would otherwise surface only as a recorded error and
    # leave this test green.
    assert "report_error" not in result, result.get("report_error")
    assert not result.get("finalize_errors"), result.get("finalize_errors")
    assert not result["run"].get("finalize_errors"), result["run"].get("finalize_errors")
    names = {name for name, _ in calls}
    assert {"runs.guard", "runs.interruptible", "runs.settle", "locks.exclusive",
            "files.replace"} <= names, calls
    assert ("runs.interruptible", 600.0) in calls, "the script's region carries the deadline"
    assert ("files.replace", "run.json") in calls, calls


def test_the_hash_cache_writes_through_the_project_platform(tmp_path, real_ports_refuse):
    fake, calls = recording_platform()
    project = core.init_project(tmp_path / "p", title="캐시", template_dir=KIT / "labkit" / "template",
                                actor="test", platform=fake)
    cache = core.HashCache(project)
    assert cache.sha(project.marker)  # marks the cache dirty
    cache.save()
    assert ("files.replace", "hashes.json") in calls, calls


def test_a_run_is_listed_through_the_project_platform(tmp_path, real_ports_refuse):
    fake, calls = recording_platform()
    project = core.init_project(tmp_path / "p", title="실행 목록", template_dir=KIT / "labkit" / "template",
                                actor="test", platform=fake)
    run_dir = project.runs / "20260922-101010-x"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"schema": core.SCHEMA_RUN, "status": "running",
                                                  "pid": 999_999}), encoding="utf-8")
    runs = core.list_runs(project)
    assert [r["status"] for r in runs] == ["incomplete"]
    assert ("processes.alive", 999_999) in calls, calls


# --- new names stay portable, existing records keep working ------------------------------------

def test_a_new_dataset_name_that_windows_cannot_store_is_refused(project, tmp_path):
    source = write_sales_csv(tmp_path / "con.csv")
    from labkit import data  # noqa: PLC0415

    with pytest.raises(core.LabError) as exc:
        data.import_file(core.Project(project), source, name="con", actor="test")
    assert exc.value.code == "name_not_portable"
    assert "con" in exc.value.message


def test_a_script_that_writes_a_reserved_name_still_runs_and_is_warned_about(project):
    # Refusing inside a run would stop a preserved script from re-executing, including one whose
    # output file has since been deleted, so the run is recorded with the limit stated instead.
    body = ("import polars as pl\nimport labkit as lab\n"
            "lab.save(pl.DataFrame({'a': [1]}), 'con')\n")
    code, result = labkit(project, "run", write_script(project, "21_reserved_save.py", body),
                          "--title", "예약 이름 저장")
    assert code == 0 and result["run"]["status"] == "ok", result
    # On Windows this depends on the version: Server 2025 stores `con.parquet` as an ordinary file
    # (Issue #8 measurement), older versions may open the console device instead (review 4, TV-13).
    # A failure here on Windows says which kind of version it ran on, not that the kit broke.
    assert (Path(project) / "data/derived/con.parquet").is_file()
    assert any("reserved device name on Windows" in w for w in result["run"]["warnings"]), \
        result["run"]["warnings"]


def test_reserved_names_are_grouped_and_never_bury_the_data_warnings(project):
    # A partitioned write under a reserved directory used to produce one warning per file, ahead of
    # the others; the agent's reply shows ten and the run page twelve, so the warning that data/raw
    # had been rewritten disappeared from both.
    body = ("import polars as pl\nfrom pathlib import Path\n"
            "d = Path('data/derived/con')\nd.mkdir(parents=True, exist_ok=True)\n"
            "for i in range(12):\n"
            "    pl.DataFrame({'a': [i]}).write_parquet(d / f'part-{i:02d}.parquet')\n"
            "pl.read_parquet('data/raw/sales.parquet').write_parquet('data/raw/sales.parquet')\n"
            "import os\nfrom pathlib import Path as P\n"
            "P(os.environ['LAB_RUN_DIR'], 'con.html').write_text('<p>x</p>', encoding='utf-8')\n")
    code, result = labkit(project, "run", write_script(project, "23_partitions.py", body),
                          "--title", "예약 디렉터리")
    assert code == 0, result
    warnings = result["run"]["warnings"]
    reserved = [w for w in warnings if "reserved device name" in w]
    assert len(reserved) == 2, warnings
    assert any(w.startswith("data/derived/con/ uses a reserved device name") and "12 file(s)" in w
               for w in reserved), reserved
    assert any("/con.html uses a reserved device name" in w for w in reserved), reserved
    first = warnings[:10]
    assert any(w.startswith("run changed imported data data/raw/sales.parquet") for w in first), first
    assert warnings.index(reserved[0]) > max(i for i, w in enumerate(warnings)
                                             if "reserved device name" not in w
                                             and "pandas" not in w), warnings


def test_every_warning_main_gave_still_comes_first(monkeypatch):
    """Review 5, NLL-4: placed before the pandas line, a reserved-name warning pushed main's tenth
    warning out of what the agent sees."""
    from labkit import runner  # noqa: PLC0415

    record = {"status": "ok", "inputs": [{"path": "data/raw/sales.parquet", "sha256": "x"}],
              "charts": [], "outputs": [{"path": f"data/derived/out{i}.csv", "change": "created"}
                                        for i in range(9)]
              + [{"path": "data/derived/con/x.parquet", "change": "created"}]}
    monkeypatch.setitem(sys.modules, "pandas", types.ModuleType("pandas"))
    warnings = runner.run_warnings(record)
    reserved = [w for w in warnings if "reserved device name" in w]
    assert reserved and warnings[-len(reserved):] == reserved, warnings
    assert warnings[9].startswith("pandas was imported"), warnings


def test_many_reserved_paths_become_one_warning():
    from labkit import runner  # noqa: PLC0415

    paths = [f"data/derived/{name}.parquet" for name in ("con", "nul", "aux", "prn", "com1")]
    (only,) = runner.reserved_name_warnings(paths + ["data/derived/sales.parquet"])
    assert only.startswith("5 paths use a reserved device name on Windows, e.g. "), only


CHART_CASES = {
    # review 4 critic-2: the script saved the figure where the chart lives and handed the file over
    "file_in_charts": ("fig.savefig(charts / 'aux.png')\nplt.close(fig)\n"
                       "lab.chart(charts / 'aux.png', 'aux')\n"),
    # review 5 NLL-1: the same with the figure object, an automatic capture, another source file
    "figure_after_savefig": "fig.savefig(charts / 'aux.png')\nlab.chart(fig, 'aux')\n",
    "auto_captured": "ax.set_title('aux')\nfig.savefig(charts / 'aux.png')\n",
    "other_source": ("Path('figures').mkdir(exist_ok=True)\nfig.savefig('figures/aux.png')\n"
                     "fig.savefig(charts / 'aux.png')\nplt.close(fig)\n"
                     "lab.chart('figures/aux.png', 'aux')\n"),
}


@pytest.mark.parametrize("case", sorted(CHART_CASES))
def test_a_file_the_script_wrote_at_a_reserved_chart_id_is_not_a_second_chart(project, case):
    """The kit writes chart `aux` as charts/aux_.png. A file the script wrote at charts/aux.png is
    the file main wrote or overwrote for that same chart, not a second one — recording it again gave
    a duplicate chart and a rerun of an older run a false mismatch."""
    body = ("import os\nfrom pathlib import Path\nimport matplotlib.pyplot as plt\n"
            "import labkit as lab\n"
            "charts = Path(os.environ['LAB_RUN_DIR'], 'charts')\ncharts.mkdir(exist_ok=True)\n"
            "fig, ax = plt.subplots()\nax.plot([1, 2], [1, 2])\n" + CHART_CASES[case])
    code, result = labkit(project, "run", write_script(project, f"24_{case}.py", body),
                          "--title", case)
    assert code == 0, result
    charts = result["run"]["charts"]
    assert [c["id"] for c in charts] == ["aux"], charts
    assert Path(next(iter(charts[0]["files"].values()))).name == "aux_.png", charts
    code, rerun = labkit(project, "run", "--rerun-of", result["run"]["id"], "--title", "again")
    assert code == 0 and rerun["run"]["reproduction"]["verdict"] == "match", rerun["run"]


CHART_BODY = ("import os\nfrom pathlib import Path\nimport matplotlib.pyplot as plt\n"
              "import labkit as lab\n"
              "charts = Path(os.environ['LAB_RUN_DIR'], 'charts')\ncharts.mkdir(exist_ok=True)\n"
              "fig, ax = plt.subplots()\nax.plot([1, 2], [1, 2])\n")


def test_a_chart_captured_under_a_reserved_title_and_named_later_leaves_one_chart(project):
    """Review 6, ONC-2: the automatic capture of a figure titled `aux` is discarded when the script
    names the figure; main unlinked charts/aux.png with it, but the kit's file is charts/aux_.png, so
    the script's own charts/aux.png became a second chart and a reserved-name warning."""
    body = CHART_BODY + ("ax.set_title('aux')\nfig.savefig(charts / 'aux.png')\nfig.show()\n"
                         "lab.chart(fig, 'named')\n")
    code, result = labkit(project, "run", write_script(project, "50_discard.py", body),
                          "--title", "discard")
    assert code == 0, result
    assert [c["id"] for c in result["run"]["charts"]] == ["named"], result["run"]["charts"]
    assert not any("reserved device name" in w for w in result["run"]["warnings"]), \
        result["run"]["warnings"]
    # Main unlinked its file there with the chart, and so does this kit (review 8, rest-2).
    assert not (Path(project) / "runs" / result["run"]["id"] / "charts" / "aux.png").exists()


WRITTEN_AFTER_THE_DISCARD = {
    "shown": "ax.set_title('aux')\nfig.show()\nlab.chart(fig, 'named')\n"
             "fig.savefig(charts / 'aux.png')\n",
    "plt_shown": "ax.set_title('aux')\nplt.show()\nlab.chart(fig, 'named')\n"
                 "fig.savefig(charts / 'aux.png')\n",
    "replaced_by_another_kind": "import plotly.graph_objects as go\nlab.chart(fig, 'aux')\n"
                                "lab.chart(go.Figure(go.Bar(y=[1, 2])), 'aux')\n"
                                "fig.savefig(charts / 'aux.png')\n",
    # the same bytes before and after, with the first file's size and time: what a file system with
    # one-second times gives a rewrite within the second (review 8, rest-2 and honesty-6)
    "rewritten_as_it_was": "ax.set_title('aux')\nfig.savefig(charts / 'aux.png')\n"
                           "first = os.stat(charts / 'aux.png')\nfig.show()\n"
                           "lab.chart(fig, 'named')\nfig.savefig(charts / 'aux.png')\n"
                           "os.utime(charts / 'aux.png', ns=(first.st_atime_ns, first.st_mtime_ns))\n",
}


@pytest.mark.parametrize("case", sorted(WRITTEN_AFTER_THE_DISCARD))
def test_a_file_written_at_a_reserved_id_s_own_name_after_its_chart_went_is_a_chart(project,
                                                                                   case):
    """Review 7, output-1: once a reserved id's chart is discarded or replaced by another kind, main
    had unlinked charts/aux.png itself, so a file the script wrote there afterwards was a chart of its
    own. Revision 3.1 kept the name known for good and dropped that file: neither a chart nor a
    warning, and main's records of it reran as `missing`."""
    code, result = labkit(project, "run", write_script(project, f"52_{case}.py",
                                                       CHART_BODY + WRITTEN_AFTER_THE_DISCARD[case]),
                          "--title", case)
    assert code == 0, result
    charts = {c["id"]: c for c in result["run"]["charts"]}
    file_chart = charts.get("aux-2" if case == "replaced_by_another_kind" else "aux")
    assert file_chart is not None and file_chart["kind"] == "file", result["run"]["charts"]
    assert file_chart["files"] == {"png": f"runs/{result['run']['id']}/charts/aux.png"}
    code, rerun = labkit(project, "run", "--rerun-of", result["run"]["id"], "--title", "again")
    assert code == 0 and rerun["run"]["reproduction"]["verdict"] == "match", rerun["run"]


def _as_main_recorded(project: Path, run_id: str, *, image: str, extra_chart: bool = False) -> None:
    """Rewrite a run into what main (48b79cc) recorded for it: chart `aux` at charts/aux.png, holding
    the kit's render (`image="kit"`) or the script's later file (`image="script"`)."""
    run_dir = Path(project) / "runs" / run_id
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    charts_dir = run_dir / "charts"
    for chart in record["charts"]:
        if chart["id"] != "aux":
            continue
        kit_file = charts_dir / "aux_.png"
        own = charts_dir / "aux.png"
        data = kit_file.read_bytes() if image == "kit" else own.read_bytes()
        own.write_bytes(data)
        kit_file.unlink()
        chart["files"] = {"png": f"runs/{run_id}/charts/aux.png"}
    if extra_chart:
        (charts_dir / "aux_.png").write_bytes(b"\x89PNG the script's own file")
        record["charts"].append({"id": "aux-2", "kind": "file", "title": "aux_", "caption": None,
                                 "via": "file", "line": None,
                                 "files": {"png": f"runs/{run_id}/charts/aux_.png"}})
    (run_dir / "run.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


MAIN_RECORDED = {
    # the kit's render replaced the script's earlier file at the chart's own name: match
    "savefig_then_chart": (CHART_BODY + "fig.savefig(charts / 'aux.png')\nlab.chart(fig, 'aux')\n",
                           {"image": "kit"}, "match"),
    # main's chart was the script's later overwrite; this kit records its own render: D9 states it
    "chart_then_savefig": (CHART_BODY + "lab.chart(fig, 'aux')\nfig.savefig(charts / 'aux.png')\n",
                           {"image": "script"}, "mismatch"),
    # an automatic capture under a reserved title, named later: main unlinked its file, and so does
    # this kit now, so the run's directory is main's already
    "captured_then_named": (CHART_BODY + "ax.set_title('aux')\nfig.savefig(charts / 'aux.png')\n"
                                         "fig.show()\nlab.chart(fig, 'named')\n",
                            {"image": "as_recorded"}, "match"),
    # a script that writes the kit's portable name itself: main recorded two charts, this one
    "script_writes_the_portable_name": (CHART_BODY + "fig.savefig(charts / 'aux_.png')\n"
                                                     "lab.chart(fig, 'aux')\n",
                                        {"image": "kit", "extra_chart": True}, "mismatch"),
    # the script's file written after the automatic capture was named: a chart of its own on main,
    # recorded the same way here, so nothing to rewrite (review 7, output-1)
    "captured_named_then_file": (CHART_BODY + WRITTEN_AFTER_THE_DISCARD["shown"],
                                 {"image": "as_recorded"}, "match"),
    # an automatic capture, then the script's savefig over its own name: main's chart was the
    # script's file; this kit records its render (D9's exception, automatic-capture form)
    "captured_then_savefig": (CHART_BODY + "ax.set_title('aux')\nfig.show()\n"
                                           "fig.savefig(charts / 'aux.png')\n",
                              {"image": "script"}, "mismatch"),
}


@pytest.mark.parametrize("case", sorted(MAIN_RECORDED))
def test_a_run_recorded_under_main_s_chart_names_reruns_as_d9_says(project, case):
    """Review 6, ONC-9: the D9 test only compared this kit with itself. Here the run is rewritten
    into main's layout first, and each rerun verdict is the one D9 states, exceptions included."""
    body, layout, verdict = MAIN_RECORDED[case]
    code, result = labkit(project, "run", write_script(project, f"51_{case}.py", body),
                          "--title", case)
    assert code == 0, result
    run_id = result["run"]["id"]
    if layout["image"] != "as_recorded":
        _as_main_recorded(project, run_id, image=layout["image"],
                          extra_chart=layout.get("extra_chart", False))
    code, rerun = labkit(project, "run", "--rerun-of", run_id, "--title", "again")
    assert code == 0, rerun
    assert rerun["run"]["reproduction"]["verdict"] == verdict, rerun["run"]["reproduction"]


@pytest.mark.parametrize("recorded", ["/tmp/evil.py", r"\\tmp\\evil.py", "C:/evil.py", "c:evil.py",
                                      r"scripts\\..\\..\\evil.py", "scripts/../../evil.py",
                                      "~/evil.py", "runs/x.py", r"runs\x.py",
                                      "report/x.py", "\u00e9:/evil.py", "1:/evil.py", "RUNS/x.py",
                                      ".Venv/x.py"])
def test_a_rerun_refuses_a_recorded_script_path_on_every_platform(project, recorded):
    code, one = labkit(project, "run", write_script(project, "25_ok.py", "x = 1\n"), "--title", "x")
    assert code == 0, one
    run_json = Path(project) / "runs" / one["run"]["id"] / "run.json"
    record = json.loads(run_json.read_text(encoding="utf-8"))
    record["script"]["path"] = recorded
    run_json.write_text(json.dumps(record), encoding="utf-8")
    code, result = labkit(project, "run", "--rerun-of", one["run"]["id"], "--title", "x")
    assert code == 1 and result["error"]["code"] == "rerun_snapshot", result


@pytest.mark.skipif(os.name == "nt", reason="names POSIX can hold and Windows reads otherwise")
def test_a_new_run_of_a_path_no_record_could_rerun_is_refused(project):
    drive_like = Path(project) / "c:"
    drive_like.mkdir()
    (drive_like / "a.py").write_text("x = 1\n", encoding="utf-8")
    code, result = labkit(project, "run", "c:/a.py", "--title", "drive")
    assert code == 1 and result["error"]["code"] == "script_location", result


@pytest.mark.skipif(os.name == "nt", reason="names POSIX can hold and Windows reads otherwise")
@pytest.mark.parametrize("rel", ["\u00e9:/a.py", "1:/a.py", "runs\\x.py", "RUNS/x.py",
                                 "~no-such-user-here/a.py"])
def test_a_new_run_of_a_name_another_platform_reads_differently_is_refused_truthfully(project,
                                                                                     rel):
    """Review 6, ONC-4 and ONC-8: Windows reads any one character before a colon as a drive and a
    backslash as a separator, and macOS and Windows ignore case. The refusal says why — not that a
    root file named `runs\\x.py` sits in a managed directory, which on POSIX it does not, but that
    `RUNS/x.py` does where this file system ignores case (review 7, output-9). A first segment
    starting with `~` that names no home is refused too, where it used to end the CLI with a
    traceback."""
    path = Path(project) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")
    code, result = labkit(project, "run", rel, "--title", "name")
    assert code == 1 and result["error"]["code"] == "script_location", result
    folds_case = rel == "RUNS/x.py" and (Path(project) / "runs").is_dir() and \
        os.path.samefile(Path(project) / "RUNS", Path(project) / "runs")
    expected = "inside a managed directory" if folds_case else "every platform"
    assert expected in result["error"]["message"], result


@pytest.mark.skipif(os.name == "nt", reason="a directory symlink needs privileges on Windows")
def test_a_rerun_through_a_link_into_a_managed_directory_is_refused(project):
    """Review 7, output-7: the managed-directory test on a rerun's path was lexical, so a directory
    linked to runs/ put runs/ on sys.path, where a new run of the same path is refused."""
    (Path(project) / "lnk").symlink_to(Path(project) / "runs", target_is_directory=True)
    run_id = _recorded_as(project, "lnk/x.py")
    code, result = labkit(project, "run", "--rerun-of", run_id, "--title", "link")
    assert code == 1 and result["error"]["code"] == "rerun_snapshot", result


@pytest.mark.skipif(os.name == "nt", reason="a directory symlink needs privileges on Windows")
def test_a_rerun_through_a_link_loop_runs_as_on_main(project):
    """Review 7, output-3: resolving a path through a link loop raises RuntimeError on CPython 3.12
    (not on 3.13+), and the new containment check turned a record main reran into a traceback."""
    (Path(project) / "loop").symlink_to("loop")
    run_id = _recorded_as(project, "loop/x.py")
    code, result = labkit(project, "run", "--rerun-of", run_id, "--title", "loop")
    assert code == 0 and result["run"]["status"] == "ok", result


def test_a_script_inside_a_managed_directory_is_refused_as_such(project):
    path = Path(project) / "report" / "x.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")
    code, result = labkit(project, "run", "report/x.py", "--title", "managed")
    assert code == 1 and "inside a managed directory" in result["error"]["message"], result


def _recorded_as(project, script_path: str) -> str:
    code, one = labkit(project, "run", write_script(project, "44_ok.py", "x = 1\n"), "--title", "x")
    assert code == 0, one
    run_json = Path(project) / "runs" / one["run"]["id"] / "run.json"
    record = json.loads(run_json.read_text(encoding="utf-8"))
    record["script"]["path"] = script_path
    run_json.write_text(json.dumps(record), encoding="utf-8")
    return one["run"]["id"]


@pytest.mark.skipif(os.name == "nt", reason="a directory symlink needs privileges on Windows")
def test_a_rerun_does_not_follow_a_link_out_of_the_project(project, tmp_path):
    """Review 6, ONC-5: the recorded path was checked only as text, so a directory inside the
    project that links outside it put outside code at sys.path[0]."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evilmod.py").write_text("print('IMPORTED outside module')\n", encoding="utf-8")
    (Path(project) / "lnk").symlink_to(outside, target_is_directory=True)
    run_id = _recorded_as(project, "lnk/x.py")
    code, result = labkit(project, "run", "--rerun-of", run_id, "--title", "link")
    assert code == 1 and result["error"]["code"] == "rerun_snapshot", result


def test_a_snapshot_that_cannot_be_written_leaves_no_run_behind(project):
    """Review 6, ONC-5: a segment longer than the file system allows made the snapshot write fail
    after the run directory existed, leaving it without run.json and the CLI without a JSON line."""
    run_id = _recorded_as(project, "scripts/" + "a" * 300 + ".py")
    before = sorted(p.name for p in (Path(project) / "runs").iterdir())
    code, result = labkit(project, "run", "--rerun-of", run_id, "--title", "long")
    assert code == 1 and result["error"]["code"] == "snapshot_failed", result
    assert result["error"]["hint"] == "shorten the script's path", result
    assert sorted(p.name for p in (Path(project) / "runs").iterdir()) == before


def test_a_name_too_long_is_told_where_the_error_does_not_say_so():
    """Measured on Windows: open() there reports a name longer than NTFS holds with a C runtime
    errno and no Windows error, and the hint said to check that runs/ could be written."""
    from labkit import runner  # noqa: PLC0415

    run = Path("runs", "20260924-000000-a", "code")
    long_name = run / "scripts" / ("a" * 300 + ".py")
    generic = "check that the project's runs/ directory can be written"
    for errno_only in (OSError(errno.EINVAL, "Invalid argument"),
                       OSError(errno.ENOENT, "No such file or directory")):
        assert runner._write_failure_hint(errno_only, long_name) == "shorten the script's path"
    denied = OSError(errno.EACCES, "Permission denied")
    assert runner._write_failure_hint(denied, long_name.with_name("a.py")) == generic
    # Review 10, honesty-8 (N8, N9) and rest-7: any part, not only the name; over 255, not at it;
    # in UTF-16 units, as NTFS and APFS count (an emoji takes two); and not the anchor.
    einval = OSError(errno.EINVAL, "Invalid argument")
    hint = runner._write_failure_hint
    assert hint(einval, run / ("d" * 300) / "a.py") == "shorten the script's path"
    assert hint(einval, run / ("a" * 252 + ".py")) == generic
    assert hint(einval, run / ("a" * 253 + ".py")) == "shorten the script's path"
    assert hint(einval, run / ("\U0001F600" * 127 + ".py")) == "shorten the script's path"
    assert hint(einval, run / ("\U0001F600" * 126 + ".py")) == generic
    unc = PureWindowsPath("//" + "h" * 200 + ".example.com/" + "s" * 60 + "/proj/runs/a.py")
    assert len(unc.anchor) > 255 and hint(einval, unc) == generic
    # A name POSIX could not decode carries a lone surrogate, which counts as one unit (X10).
    assert hint(einval, run / "caf\udce9.py") == generic


def test_a_run_record_that_cannot_be_written_leaves_no_run_behind(project, monkeypatch):
    """Review 7, output-2: a disk that filled one block after the snapshot failed the running
    record's write outside the snapshot's `try`, with a traceback, no JSON line, and a run directory
    holding only code/."""
    from labkit import runner  # noqa: PLC0415

    real_write_json = core.write_json

    def full_disk(path, obj, **kwargs):
        if Path(path).name == "run.json":
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        return real_write_json(path, obj, **kwargs)

    monkeypatch.setattr(core, "write_json", full_disk)
    with pytest.raises(core.LabError) as exc:
        runner.execute(core.Project(project), script=write_script(project, "53_full.py", RAN),
                       title="가득 참", actor="test")
    assert exc.value.code == "record_failed", exc.value
    assert "space" in exc.value.hint, exc.value.hint
    assert not list((Path(project) / "runs").iterdir())
    assert not (Path(project) / "data/derived/ran.txt").exists()


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="a directory the process cannot list: POSIX permissions, not root")
def test_a_run_directory_the_kit_cannot_list_is_still_removed(project, monkeypatch):
    """Review 7, output-2: with a umask that leaves the new run directory unreadable to the kit
    itself, rmtree could not list it and the refused run left it behind."""
    from labkit import runner  # noqa: PLC0415

    real_new_run_dir = core.new_run_dir

    def unreadable(project_, label):
        run_id = real_new_run_dir(project_, label)
        (project_.runs / run_id).chmod(0)
        return run_id

    monkeypatch.setattr(core, "new_run_dir", unreadable)
    try:
        with pytest.raises(core.LabError) as exc:
            runner.execute(core.Project(project), script=write_script(project, "54_umask.py", RAN),
                           title="권한", actor="test")
        assert exc.value.code == "snapshot_failed", exc.value
        assert "runs/" in exc.value.hint, exc.value.hint
        assert not list((Path(project) / "runs").iterdir())
    finally:  # what a failure leaves must not outlive the test as a tree nothing can remove
        for left in (Path(project) / "runs").iterdir():
            left.chmod(0o700)


def test_reject_unportable_name_never_refuses_a_name_the_project_already_has():
    core.reject_unportable_name("con", kind="dataset", exists=True)
    core.reject_unportable_name("sales", kind="dataset")
    with pytest.raises(core.LabError) as exc:
        core.reject_unportable_name("con", kind="script")
    assert exc.value.code == "name_not_portable" and "script" in exc.value.message


def test_two_chart_ids_that_differ_keep_two_files(project):
    # `con-file` is an ordinary id and has to keep its own file. If the reserved one were mapped
    # onto it the two charts would overwrite each other's image and plotted data, and a rerun
    # comparison would then report two different charts as the same.
    body = ("import matplotlib.pyplot as plt\nimport polars as pl\nimport labkit as lab\n"
            "for cid, y in (('con', 1), ('con-file', 2)):\n"
            "    fig, ax = plt.subplots()\n    ax.plot([1, 2], [y, y])\n"
            "    lab.chart(fig, cid, title=cid, data=pl.DataFrame({'y': [y]}))\n")
    code, result = labkit(project, "run", write_script(project, "20_reserved.py", body),
                          "--title", "예약 이름 차트")
    assert code == 0, result
    charts = {c["id"]: c for c in result["run"]["charts"]}
    assert set(charts) == {"con", "con-file"}, "the chart identity is what records point at"
    assert Path(charts["con"]["files"]["png"]).name == "con_.png"
    assert Path(charts["con-file"]["files"]["png"]).name == "con-file.png"
    files = [c["files"]["png"] for c in charts.values()] + [c["data"] for c in charts.values()]
    assert len(set(files)) == len(files), files
    for rel in files:
        assert (Path(project) / rel).is_file(), rel


def test_a_title_that_slugifies_to_a_device_name_keeps_the_id_it_always_had(project):
    # A generated id is still `script::id` identity in the records, so it is not rewritten either:
    # an unchanged historical script must not start a second chart history.
    body = ("import matplotlib.pyplot as plt\nimport labkit as lab\n"
            "fig, ax = plt.subplots()\nax.plot([1, 2], [1, 2])\n"
            "lab.chart(fig, title='CON')\n")
    code, result = labkit(project, "run", write_script(project, "22_generated.py", body),
                          "--title", "생성된 id")
    assert code == 0, result
    (chart,) = result["run"]["charts"]
    assert chart["id"] == "con"
    assert Path(chart["files"]["png"]).name == "con_.png"


# --- the loader the aelix process uses ---------------------------------------------------------

def _aelix_stubs() -> dict:
    """Just enough of the aelix API to import the extension without aelix installed.

    The loader is what is under test; the tools and hooks only have to construct."""
    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        return mod

    class Any:
        def __init__(self, *args, **kwargs):
            self.__dict__.update(kwargs)

    core_pkg = module("aelix_agent_core")
    core_pkg.harness = module("aelix_agent_core.harness")
    ai_pkg = module("aelix_ai")
    return {
        "aelix_agent_core": core_pkg,
        "aelix_agent_core.harness": core_pkg.harness,
        "aelix_agent_core.harness.hooks": module("aelix_agent_core.harness.hooks",
                                                 BeforeAgentStartResult=Any),
        "aelix_agent_core.types": module("aelix_agent_core.types", AgentTool=Any),
        "aelix_ai": ai_pkg,
        "aelix_ai.messages": module("aelix_ai.messages", ImageContent=Any, TextContent=Any),
        "aelix_ai.tools": module("aelix_ai.tools", ToolExecutionContext=Any, ToolResult=Any),
    }


def load_extension(monkeypatch, name: str):
    for key, value in _aelix_stubs().items():
        monkeypatch.setitem(sys.modules, key, value)
    spec = importlib.util.spec_from_file_location(name, EXT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYSIS_MODULES = ("polars", "duckdb", "matplotlib", "plotly", "pyarrow", "numpy")


def test_a_checkout_file_finds_the_kit_next_to_it(monkeypatch):
    monkeypatch.delenv("AELIX_LAB_KIT", raising=False)
    module = load_extension(monkeypatch, "ext_route")
    assert (module.KIT, module.KIT_ROUTE) == (KIT.resolve(), "checkout")


def test_a_file_name_with_dots_is_still_a_checkout_file(monkeypatch):
    # Review of design revision 1, dist-6 and kit-5: deciding the route by __package__ sent a
    # dotted name (`-e extension.aelix_lab`, a symlink named aelix_lab.v2.py) to the installed one.
    monkeypatch.delenv("AELIX_LAB_KIT", raising=False)
    module = load_extension(monkeypatch, "extension.aelix_lab")
    assert (module.KIT, module.KIT_ROUTE) == (KIT.resolve(), "checkout")


def test_the_installed_module_runs_the_kit_installed_with_it(monkeypatch, tmp_path):
    import aelix_analytics._install

    monkeypatch.setattr(aelix_analytics._install, "kit_dir", lambda: KIT.resolve())
    monkeypatch.setenv("AELIX_LAB_KIT", str(tmp_path / "another-kit"))  # not followed here
    module = load_extension(monkeypatch, "aelix_analytics.extension")
    assert (module.KIT, module.KIT_ROUTE) == (KIT.resolve(), "installed")


@pytest.mark.skipif(os.name == "nt", reason="a symbolic link to the kit")
def test_the_installed_module_loaded_as_a_file_finds_the_kit_beside_it(monkeypatch, tmp_path):
    package = tmp_path / "aelix_analytics"
    package.mkdir()
    (package / "extension.py").write_bytes(EXT.read_bytes())
    (package / "kit").symlink_to(KIT, target_is_directory=True)
    monkeypatch.delenv("AELIX_LAB_KIT", raising=False)
    for key, value in _aelix_stubs().items():
        monkeypatch.setitem(sys.modules, key, value)
    spec = importlib.util.spec_from_file_location("_aelix_ext_extension", package / "extension.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.KIT, module.KIT_ROUTE) == ((package / "kit").resolve(), "installed file")


def test_each_runtime_starts_the_extension_s_state_over(monkeypatch):
    """Review of design revision 1, dist-1: aelix sets extensions up again for every session it
    builds, and the installed module keeps its globals across them. A /lab note before a new
    session's first prompt recorded the previous session's file."""
    module = load_extension(monkeypatch, "ext_rebuilt")
    module.setup(_Api(_EventBus()))
    module.STATE.update({"session": "/sessions/A.jsonl", "project": "/somewhere"})
    module.setup(_Api(_EventBus()))
    assert module.STATE["session"] is None and module.STATE["project"] is None


class _EventBus:
    """aelix's ExtensionAPI.events: synchronous, one per runtime, shared by its extensions."""

    def __init__(self) -> None:
        self.handlers: dict = {}

    def emit(self, channel, data=None) -> None:
        for handler in list(self.handlers.get(channel, ())):
            handler(data)

    def on(self, channel, handler):
        self.handlers.setdefault(channel, []).append(handler)
        return lambda: self.handlers[channel].remove(handler)


class _Api:
    def __init__(self, events=None, fail_on_tool: int | None = None) -> None:
        if events is not None:
            self.events = events
        self.registered: list = []
        self._fail_on_tool = fail_on_tool

    def register_flag(self, name, **kwargs):
        self.registered.append(("flag", name))

    def register_tool(self, tool):
        if self._fail_on_tool is not None and len(self.tools()) == self._fail_on_tool:
            raise RuntimeError("setup failed part way")
        self.registered.append(("tool", tool.name))

    def register_command(self, name, **kwargs):
        self.registered.append(("command", name))

    def on(self, event, handler):
        self.registered.append(("on", event))

    def tools(self) -> list:
        return [name for kind, name in self.registered if kind == "tool"]


def test_a_second_copy_in_one_runtime_is_not_set_up(monkeypatch, capsys):
    """A checkout's symlink or -e beside the installed package is two copies to aelix, which would
    fire both copies' hooks (ADR-0004)."""
    first = load_extension(monkeypatch, "ext_copy_one")
    second = load_extension(monkeypatch, "ext_copy_two")
    bus = _EventBus()
    active, extra = _Api(bus), _Api(bus)
    first.setup(active)
    second.setup(extra)
    assert len(active.tools()) == 6 and ("command", "lab") in active.registered
    assert extra.registered == []
    assert "is not set up: this aelix already loaded" in capsys.readouterr().err
    # Another runtime has another bus: a copy is set up there.
    elsewhere = _Api(_EventBus())
    second.setup(elsewhere)
    assert len(elsewhere.tools()) == 6


def test_a_copy_whose_setup_failed_does_not_keep_the_next_one_out(monkeypatch):
    # Review of design revision 1, Codex F3: answering before registering could suppress the only
    # working copy.
    first = load_extension(monkeypatch, "ext_fails")
    second = load_extension(monkeypatch, "ext_works")
    bus = _EventBus()
    with pytest.raises(RuntimeError, match="part way"):
        first.setup(_Api(bus, fail_on_tool=3))
    works = _Api(bus)
    second.setup(works)
    assert len(works.tools()) == 6


def test_a_host_without_an_event_bus_sets_the_extension_up_as_before(monkeypatch):
    module = load_extension(monkeypatch, "ext_no_bus")
    api = _Api()
    module.setup(api)
    assert len(api.tools()) == 6


def _open_through_lab_command(module, project_root):
    import asyncio

    (project_root / "report").mkdir(exist_ok=True)
    (project_root / "report" / "index.html").write_text("<html>", encoding="utf-8")
    return asyncio.run(module.lab_command("open"))


def test_lab_open_hands_the_report_to_the_platform_detached(monkeypatch, project):
    module = load_extension(monkeypatch, "ext_open")
    monkeypatch.setenv("AELIX_LAB_PROJECT", str(project))
    handed = []
    monkeypatch.setattr(module.core.PLATFORM.processes, "open_document",
                        lambda path, *, wait: handed.append((Path(path), wait)))
    answer = _open_through_lab_command(module, project)
    assert handed == [(project / "report" / "index.html", False)]
    assert answer.startswith("opened ")


def test_lab_open_says_so_when_nothing_can_open_the_report(monkeypatch, project):
    module = load_extension(monkeypatch, "ext_cannot_open")
    monkeypatch.setenv("AELIX_LAB_PROJECT", str(project))

    def cannot(path, *, wait):
        raise OSError("no application is associated with this file")

    monkeypatch.setattr(module.core.PLATFORM.processes, "open_document", cannot)
    answer = _open_through_lab_command(module, project)
    assert answer.startswith("could not open ") and "yourself" in answer


def test_the_extension_loads_the_kit_without_leaving_module_names_behind(monkeypatch):
    before = {k for k in sys.modules if k.startswith("aelix_lab_kit")}
    first = load_extension(monkeypatch, "ext_one")
    second = load_extension(monkeypatch, "ext_two")

    assert first.core is not second.core, "each load has to get its own kit"
    assert first.core._platform is not second.core._platform, "a cached sibling could mix packs"
    assert first.core.SCHEMA_RUN == core.SCHEMA_RUN
    assert {k for k in sys.modules if k.startswith("aelix_lab_kit")} == before
    assert first.core.PLATFORM.name == _platform.detect().name


def test_loading_the_kit_pulls_no_analysis_library_into_the_host_process(monkeypatch):
    # The extension runs inside aelix. Polars or matplotlib arriving there is the failure this
    # whole boundary exists to prevent, whichever loader is used.
    code = (
        "import importlib.util, sys, types\n"
        f"sys.path.insert(0, {str(PACK / 'tests')!r})\n"
        "stub = types.ModuleType\n"
        "class Any:\n"
        "    def __init__(self, *a, **k): self.__dict__.update(k)\n"
        "for name, attrs in (('aelix_agent_core', {}), ('aelix_agent_core.harness', {}),\n"
        "                    ('aelix_agent_core.harness.hooks', {'BeforeAgentStartResult': Any}),\n"
        "                    ('aelix_agent_core.types', {'AgentTool': Any}),\n"
        "                    ('aelix_ai', {}), ('aelix_ai.messages', {'ImageContent': Any, 'TextContent': Any}),\n"
        "                    ('aelix_ai.tools', {'ToolExecutionContext': Any, 'ToolResult': Any})):\n"
        "    m = stub(name)\n"
        "    [setattr(m, k, v) for k, v in attrs.items()]\n"
        "    sys.modules[name] = m\n"
        f"spec = importlib.util.spec_from_file_location('ext', {str(EXT)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        f"loaded = [n for n in {ANALYSIS_MODULES!r} if n in sys.modules]\n"
        "print('LOADED:' + ','.join(loaded))\n"
        "print('KIT_OK:' + mod.core.SCHEMA_RUN)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=120,
                          env=kit_env(), **_platform.text_kwargs())
    assert "KIT_OK:lab.run/1" in proc.stdout, proc.stdout + proc.stderr
    loaded = next(line for line in proc.stdout.splitlines() if line.startswith("LOADED:"))
    assert loaded == "LOADED:", f"the aelix process would have imported {loaded[len('LOADED:'):]}"


def test_a_kit_that_is_not_there_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("AELIX_LAB_KIT", str(tmp_path / "nowhere"))
    with pytest.raises(RuntimeError, match="kit not found"):
        load_extension(monkeypatch, "ext_missing")
    assert not [k for k in sys.modules if k.startswith("aelix_lab_kit")]


def test_a_failed_kit_load_leaves_no_module_behind(monkeypatch, tmp_path):
    # core imports its sibling before it fails, which is the case the cleanup exists for.
    kit = tmp_path / "kit"
    (kit / "labkit").mkdir(parents=True)
    (kit / "labkit" / "_platform.py").write_text("X = 1\n", encoding="utf-8")
    (kit / "labkit" / "core.py").write_text("from . import _platform\nraise RuntimeError('broken')\n",
                                            encoding="utf-8")
    monkeypatch.setenv("AELIX_LAB_KIT", str(kit))
    with pytest.raises(RuntimeError, match="broken"):
        load_extension(monkeypatch, "ext_broken")
    assert not [k for k in sys.modules if k.startswith("aelix_lab_kit")]


def test_the_cli_writes_utf8_whatever_the_locale_says(project):
    # Review 4, TV-12: nothing noticed the stream pinning being removed. A usage error that quotes
    # the argument has to survive too, which is why it happens before argument parsing.
    env = kit_env(PYTHONIOENCODING="ascii")  # the project's title is Korean (see the fixture)
    done = subprocess.run([sys.executable, "-m", "labkit", "--project", str(project), "status"],
                          capture_output=True, env=env, timeout=120, **_platform.text_kwargs())
    assert done.returncode == 0, done.stderr
    assert "테스트 분석" in done.stdout
    bad = subprocess.run([sys.executable, "-m", "labkit", "--project", str(project), "note", "발견", "x"],
                         capture_output=True, env=env, timeout=120, **_platform.text_kwargs())
    assert bad.returncode == 2 and "발견" in bad.stderr, bad.stderr


# --- how the extension stops a child -------------------------------------------------------------

class _ProcessesSeen:
    """The extension's view of ProcessControl, recorded, with the real implementation behind it
    unless a step is switched off. `ask_by_file` asks the way Windows does, on any platform."""

    def __init__(self, real, *, kill=True, ask_by_file=False):
        self.real, self.kill, self.ask_by_file, self.calls = real, kill, ask_by_file, []
        self.asks_by_request = ask_by_file or real.asks_by_request
        self.times: dict[str, float] = {}
        self.requests: list = []

    def alive(self, pid):
        return self.real.alive(pid)

    def spawn_kwargs(self):
        self.calls.append("spawn_kwargs")
        return self.real.spawn_kwargs()

    def base_env(self):
        self.calls.append("base_env")
        return self.real.base_env()

    def interrupt(self, proc, request=None):
        self.calls.append("interrupt")
        self.times.setdefault("interrupt", time.monotonic())
        self.requests.append(request)
        if self.ask_by_file:
            return _platform.WindowsProcessControl().interrupt(proc, request)
        return self.real.interrupt(proc, request)

    def kill_tree(self, pid):
        self.calls.append("kill_tree")
        self.times.setdefault("kill_tree", time.monotonic())
        assert isinstance(pid, int), "kill_tree runs off the loop and gets a pid, never the object"
        if self.kill:
            self.real.kill_tree(pid)


def _spawned(ext, argv, cwd, env, timeout, processes, request=None, cancels=(), wait_first=None,
             leftovers=None):
    """Run the extension's _spawn in a fresh loop; returns (its result, seconds it took).

    `cancels` are the delays between the cancellations of the call, after `wait_first` returns;
    `leftovers` receives the processes the extension started, so a test that leaves one running on
    purpose can end it before the loop closes."""
    async def scenario():
        started = []
        original = ext.asyncio.create_subprocess_exec

        async def remembering(*args, **kwargs):
            proc = await original(*args, **kwargs)
            started.append(proc)
            return proc

        ext.asyncio.create_subprocess_exec = remembering
        try:
            task = asyncio.ensure_future(ext._spawn(argv, cwd, env, timeout, processes, request))
            t0 = time.perf_counter()
            if not cancels:
                result = await task
            else:
                if wait_first is not None:
                    await asyncio.to_thread(wait_first)
                t0 = time.perf_counter()
                for delay in cancels:
                    await asyncio.sleep(delay)
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                result = None
                # What the second cancellation left running still has to end on its own.
                await asyncio.gather(*ext.STOPPING)
            elapsed = time.perf_counter() - t0
        finally:
            ext.asyncio.create_subprocess_exec = original
            for proc in started:
                if leftovers is not None:
                    leftovers(proc)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(ext._finished(proc), 10)
        return result, elapsed

    return asyncio.run(scenario())


def test_an_aborted_kit_command_is_asked_the_platform_s_way_and_not_killed(monkeypatch, project):
    ext = load_extension(monkeypatch, "ext_abort")
    seen = _ProcessesSeen(ext.core.PLATFORM.processes)
    request = ext._CancelRequest()
    argv = [sys.executable, "-m", "labkit", "--project", str(project), "run",
            write_script(project, "34_abort.py", SLEEPER), "--title", "중단"]
    _, elapsed = _spawned(ext, argv, project, kit_env(LAB_CANCEL_FILE=str(request.path)), 120,
                          seen, request, cancels=(0,), wait_first=lambda: _wait_for_running_run(project))
    assert seen.calls.count("interrupt") == 1 and "kill_tree" not in seen.calls, seen.calls
    assert elapsed < 30
    assert not request.directory.exists(), "the request outlived the stop"
    (run,) = core.list_runs(core.Project(project))[-1:]
    assert run["status"] == "cancelled", run


# A stand-in for `uv run ... python -m labkit`: it polls the request the way the kit's watcher does,
# every CANCEL_POLL_SECONDS, after a start-up delay like uv's own, and says what it saw in a marker.
STAND_IN = """\
import json, os, sys, time
from pathlib import Path
marker = Path(sys.argv[1])
delay, poll = float(sys.argv[2]), float(sys.argv[3])
time.sleep(delay)
marker.with_suffix(".started").write_text("started", encoding="utf-8")
request = Path(os.environ["LAB_CANCEL_FILE"]) if "LAB_CANCEL_FILE" in os.environ else None
for _ in range(int(60 / poll) if request else 0):
    if request.exists():
        marker.write_text("saw the request", encoding="utf-8")
        print(json.dumps({"ok": False, "error": {"code": "interrupted"}}), flush=True)
        sys.exit(3)
    time.sleep(poll)
print(json.dumps({"ok": True}), flush=True)
"""


def _stand_in_kit(ext, monkeypatch, tmp_path, *, delay):
    """run_kit, with the process it starts replaced by STAND_IN; returns (marker, the processes)."""
    script = tmp_path / "stand_in.py"
    script.write_text(STAND_IN, encoding="utf-8")
    marker = tmp_path / "marker"
    started = []
    original = ext.asyncio.create_subprocess_exec

    async def stand_in(*argv, **kwargs):
        proc = await original(sys.executable, str(script), str(marker), str(delay),
                              str(_platform.CANCEL_POLL_SECONDS), **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(ext.asyncio, "create_subprocess_exec", stand_in)
    monkeypatch.setattr(ext, "resolve_uv", lambda processes=None: "uv")
    return marker, started


async def _until(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, "timed out waiting"
        await asyncio.sleep(0.005)


def _windows_asking_project(ext, project):
    seen = _ProcessesSeen(ext.core.PLATFORM.processes, ask_by_file=True)
    platform = ext.core.PLATFORM
    bundle = types.SimpleNamespace(name="asks-by-request", processes=seen, locks=platform.locks,
                                   files=platform.files, runs=platform.runs)
    return seen, ext.core.Project(project, platform=bundle)


@pytest.mark.parametrize("when", ["kit_running", "before_the_kit_starts"])
def test_a_second_cancellation_does_not_take_the_request_away(monkeypatch, project, tmp_path,
                                                              when):
    """aelix cancels the turn again on every Esc (review 5, CC-1): the second cancellation ends
    _spawn at once, and the request used to go with it before the kit had seen it. Review 6
    (AS-2, CH-2): through run_kit, whose clean-up was the bug, with a child that polls as slowly as
    the kit does, the second cancellation only once the termination has started, and also before
    the child has started looking."""
    ext = load_extension(monkeypatch, "ext_twice")
    monkeypatch.setattr(ext, "GRACE_SECONDS", 5)
    seen, lab_project = _windows_asking_project(ext, project)
    marker, started = _stand_in_kit(ext, monkeypatch, tmp_path,
                                    delay=0 if when == "kit_running" else 1.0)

    async def scenario():
        task = asyncio.ensure_future(ext.run_kit(lab_project, ["status"], timeout=60))
        await _until(lambda: started)
        if when == "kit_running":
            await _until(lambda: marker.with_suffix(".started").exists())
        task.cancel()
        await _until(lambda: ext.STOPPING)  # the termination has started and owns the request
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.gather(*ext.STOPPING)

    asyncio.run(scenario())
    assert marker.is_file() and marker.read_text(encoding="utf-8") == "saw the request", \
        ("the child never saw the request", seen.calls)
    assert "kill_tree" not in seen.calls, seen.calls
    (request,) = seen.requests
    assert not request.parent.exists(), "the request outlived a kit that had finished"


def test_aelix_exiting_while_a_command_stops_leaves_the_request_for_the_kit(monkeypatch, project,
                                                                          tmp_path):
    """Review 6, AS-1: aelix exits through asyncio.run, which cancels the termination still waiting
    in STOPPING; its clean-up then deleted the request before the kit had looked, and the run went
    on with nobody left to stop it. The request now stays until the kit has finished."""
    ext = load_extension(monkeypatch, "ext_exit")
    seen, lab_project = _windows_asking_project(ext, project)
    marker, started = _stand_in_kit(ext, monkeypatch, tmp_path, delay=0.5)

    async def scenario():
        task = asyncio.ensure_future(ext.run_kit(lab_project, ["status"], timeout=60))
        await _until(lambda: started)
        task.cancel()
        await _until(lambda: ext.STOPPING)
        # aelix exits here: asyncio.run cancels what is still pending, the termination included

    try:
        asyncio.run(scenario())
        end = time.monotonic() + 10
        while not marker.is_file() and time.monotonic() < end:
            time.sleep(0.05)
        assert marker.is_file() and marker.read_text(encoding="utf-8") == "saw the request", \
            "the kit never saw the request after aelix exited"
    finally:
        for proc in started:  # held until here: a transport collected earlier may kill its child
            with contextlib.suppress(Exception):
                proc._transport.get_extra_info("subprocess").kill()
            # Marked closed now, so it does not report its closed loop when it is collected.
            with contextlib.suppress(RuntimeError):
                proc._transport.close()
        for request in seen.requests:
            shutil.rmtree(request.parent, ignore_errors=True)  # the leftover, by design
    assert "kill_tree" not in seen.calls, seen.calls


STUCK = "import time\ntime.sleep(30)\n"


def test_a_child_that_ignores_the_request_is_ended_by_pid_and_the_wait_is_bounded(
        monkeypatch, tmp_path):
    ext = load_extension(monkeypatch, "ext_stuck")
    monkeypatch.setattr(ext, "GRACE_SECONDS", 0.5)
    seen = _ProcessesSeen(ext.core.PLATFORM.processes, ask_by_file=True)
    (tmp_path / "stuck.py").write_text(STUCK, encoding="utf-8")
    request = ext._CancelRequest()
    (timed_out, *_), elapsed = _spawned(ext, [sys.executable, str(tmp_path / "stuck.py")],
                                        tmp_path, kit_env(), 0.5, seen, request)
    assert timed_out and seen.calls.count("kill_tree") == 1, seen.calls
    assert elapsed < 10
    assert not request.directory.exists()


def test_the_final_wait_returns_even_when_the_tree_outlives_the_forced_end(monkeypatch, tmp_path):
    # Review 4, TV-4: with the final wait unbounded, a tree that survived kill_tree held the
    # session's event loop until it exited by itself.
    ext = load_extension(monkeypatch, "ext_outlives")
    monkeypatch.setattr(ext, "GRACE_SECONDS", 0.5)
    real = ext.core.PLATFORM.processes
    seen = _ProcessesSeen(real, kill=False, ask_by_file=True)
    (tmp_path / "stuck.py").write_text(STUCK, encoding="utf-8")
    request = ext._CancelRequest()
    try:
        (timed_out, *_), elapsed = _spawned(ext, [sys.executable, str(tmp_path / "stuck.py")],
                                            tmp_path, kit_env(), 0.5, seen, request,
                                            leftovers=lambda proc: real.kill_tree(proc.pid))
        assert timed_out and "kill_tree" in seen.calls
        assert elapsed < 10, elapsed
        # The tree survived the forced end, so a kit in it may still look for its request
        # (review 7, stop-3): it stays.
        assert request.path.is_file(), "the request went although the tree survived the forced end"
    finally:
        request.remove()


FLOODS_ON_EXIT = """\
import os, sys, time
from pathlib import Path
request = Path(os.environ["LAB_CANCEL_FILE"])
while not request.exists():
    time.sleep(0.01)
sys.stderr.write("e" * 4_000_000)
sys.stdout.write("o" * 4_000_000)
sys.exit(3)
"""


def test_a_child_that_writes_a_lot_while_it_stops_is_not_killed(monkeypatch, tmp_path):
    # Review 5, CC-4: draining one pipe to its end before the other stalls on a child blocked
    # writing the second, and the wait then kills a child that was stopping by itself.
    ext = load_extension(monkeypatch, "ext_flood")
    monkeypatch.setattr(ext, "GRACE_SECONDS", 3)
    seen = _ProcessesSeen(ext.core.PLATFORM.processes, ask_by_file=True)
    (tmp_path / "floods.py").write_text(FLOODS_ON_EXIT, encoding="utf-8")
    request = ext._CancelRequest()
    (timed_out, *_), _ = _spawned(ext, [sys.executable, str(tmp_path / "floods.py")], tmp_path,
                                  kit_env(LAB_CANCEL_FILE=str(request.path)), 0.5, seen, request)
    assert timed_out and "kill_tree" not in seen.calls, seen.calls


GRANDPARENT = """\
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
open(sys.argv[1], "w", encoding="utf-8").write(str(child.pid))
"""


def test_a_descendant_still_holding_the_pipes_is_ended_after_its_parent_exited(
        monkeypatch, tmp_path):
    """`Process.wait` returns at once once the parent has exited, even while a descendant holds
    the pipes; the extension waits for the pipes to close instead, and ends what holds them. The
    parent exits well before the timeout, so a wait()-only implementation would not kill."""
    ext = load_extension(monkeypatch, "ext_grandchild")
    monkeypatch.setattr(ext, "GRACE_SECONDS", 0.5)
    real = ext.core.PLATFORM.processes
    seen = _ProcessesSeen(real)
    (tmp_path / "parent.py").write_text(GRANDPARENT, encoding="utf-8")
    pid_file = tmp_path / "child.pid"

    def end_the_orphan(_proc):
        # POSIX reached it through the group already; on Windows `taskkill /T` cannot find a
        # descendant whose parent has exited (a declared limitation), so the test ends it itself.
        # SIGTERM is TerminateProcess on Windows, and the pid is the orphan's own, not a group.
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)

    (timed_out, *_), _ = _spawned(
        ext, [sys.executable, str(tmp_path / "parent.py"), str(pid_file)], tmp_path, kit_env(),
        1.5, seen, leftovers=end_the_orphan)
    assert timed_out and "kill_tree" in seen.calls, seen.calls


def test_a_platform_that_asks_with_a_signal_needs_no_request_file(monkeypatch, project,
                                                                  tmp_path):
    """Review 6, AS-6: every kit command used to create a temporary directory for the request, and
    failed when it could not, although POSIX asks with a signal and never reads it."""
    ext = load_extension(monkeypatch, "ext_no_request")
    seen = _ProcessesSeen(_platform.PosixProcessControl())
    platform = ext.core.PLATFORM
    bundle = types.SimpleNamespace(name="posix", processes=seen, locks=platform.locks,
                                   files=platform.files, runs=platform.runs)
    _stand_in_kit(ext, monkeypatch, tmp_path, delay=0)

    def no_temporary_directory(**_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ext.tempfile, "mkdtemp", no_temporary_directory)
    real_child_env = ext.child_env
    envs = []
    monkeypatch.setattr(ext, "child_env", lambda *a: envs.append(real_child_env(*a)) or envs[-1])
    ok, result = asyncio.run(ext.run_kit(ext.core.Project(project, platform=bundle), ["status"],
                                         timeout=60))
    assert ok, result
    assert envs and "LAB_CANCEL_FILE" not in envs[0], envs


def test_a_wrapper_that_already_exited_is_given_the_grace_period_before_its_group_is_ended(
        monkeypatch, tmp_path):
    """Review 6, CH-7: a wrapper that has exited while something it started holds the pipes is
    not asked (its pid may be free), but its group is ended only after the grace period, so a kit
    stopping by itself is not cut short."""
    ext = load_extension(monkeypatch, "ext_grace")
    monkeypatch.setattr(ext, "GRACE_SECONDS", 1.0)
    real = ext.core.PLATFORM.processes
    seen = _ProcessesSeen(real)
    (tmp_path / "parent.py").write_text(GRANDPARENT, encoding="utf-8")
    pid_file = tmp_path / "child.pid"

    def end_the_orphan(_proc):
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)

    (timed_out, *_), _ = _spawned(
        ext, [sys.executable, str(tmp_path / "parent.py"), str(pid_file)], tmp_path, kit_env(),
        1.5, seen, leftovers=end_the_orphan)
    assert timed_out and "kill_tree" in seen.calls, seen.calls
    assert seen.times["kill_tree"] - seen.times["interrupt"] >= 0.9, seen.times


def test_lab_use_writes_the_trust_off_the_event_loop(monkeypatch, project):
    """Review 5, NLL-12 and review 6, CH-7: on Windows a record being read elsewhere makes the replace
    wait up to about two seconds, so /lab use must not write the trust file on aelix's loop."""
    ext = load_extension(monkeypatch, "ext_use")
    threads = []
    monkeypatch.setattr(ext.core, "trust_project",
                        lambda root: threads.append(threading.current_thread()))
    reply = asyncio.run(ext.lab_command(f"use {project}"))
    assert "active project" in reply, reply
    assert threads and threads[0] is not threading.main_thread(), threads


# main() with the Windows watcher, its delivery replaced by what a POSIX system has for the same job:
# the wiring — the variable taken out of the environment, the watcher started inside the try, and
# stopped when main() returns — is the same code on every platform (review 6, AS-5, CH-8).
MAIN_WITH_THE_WATCHER = """\
import os, signal, sys, threading, time
from pathlib import Path
from labkit import __main__ as cli, _platform, core

def deliver():
    if not callable(signal.getsignal(signal.SIGINT)):
        return False
    signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
    return True

_platform.raise_sigint_in_main = deliver
watch = _platform.WindowsRunControl().watch_for_cancel

def slow_to_start(request):
    stop = watch(request)
    time.sleep(0.5)  # an interrupt landing here must still give the interrupted JSON
    return stop

core.PLATFORM.runs.watch_for_cancel = slow_to_start
mode = sys.argv[1]

def command(argv):
    print("VARIABLE:" + str("LAB_CANCEL_FILE" in os.environ), flush=True)
    if mode == "wait":
        time.sleep(10)
    return 0

cli._command = command
code = cli.main([])
if mode == "after":
    got = []
    signal.signal(signal.SIGINT, lambda *a: got.append(1))
    Path(os.environ.get("REQUEST_PATH", "")).touch()
    time.sleep(1.0)
    print("DELIVERED_AFTER_MAIN:" + str(bool(got)), flush=True)
sys.exit(code)
"""


@pytest.mark.skipif(os.name == "nt", reason="the delivery is replaced with pthread_kill")
def test_main_honours_a_request_made_before_it_started_even_while_its_watcher_starts(tmp_path):
    request = tmp_path / "cancel"
    request.touch()
    driver = tmp_path / "driver.py"
    driver.write_text(MAIN_WITH_THE_WATCHER, encoding="utf-8")
    done = subprocess.run([sys.executable, str(driver), "wait"], capture_output=True, timeout=60,
                          env=kit_env(LAB_CANCEL_FILE=str(request)), **_platform.text_kwargs())
    assert done.returncode == 130, (done.stdout, done.stderr)
    assert '"code": "interrupted"' in done.stdout.splitlines()[-1], done.stdout


@pytest.mark.skipif(os.name == "nt", reason="the delivery is replaced with pthread_kill")
def test_main_hides_the_request_from_the_command_and_stops_watching_when_it_returns(tmp_path):
    request = tmp_path / "cancel"
    driver = tmp_path / "driver.py"
    driver.write_text(MAIN_WITH_THE_WATCHER, encoding="utf-8")
    done = subprocess.run([sys.executable, str(driver), "after"], capture_output=True, timeout=60,
                          env=kit_env(LAB_CANCEL_FILE=str(request), REQUEST_PATH=str(request)),
                          **_platform.text_kwargs())
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert "VARIABLE:False" in done.stdout, done.stdout
    assert "DELIVERED_AFTER_MAIN:False" in done.stdout, done.stdout


RAISE_SIGINT = """\
import signal, sys, time
from labkit import _platform
got = []
mode = sys.argv[1]
if mode == "ignored":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
elif mode == "default":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
else:
    signal.signal(signal.SIGINT, lambda *a: got.append(1))
answer = _platform.raise_sigint_in_main()
time.sleep(0.1)
print(answer, got, flush=True)
"""


@pytest.mark.parametrize("mode, printed", [("ignored", "False []"), ("default", "False []"),
                                           ("handled", "True [1]")])
def test_raise_sigint_in_main_declines_where_raising_would_do_nothing_or_end_the_process(
        tmp_path, mode, printed):
    """Review 6, CH-8: every test replaced it. It declines for an ignored SIGINT and for the default
    action — which would end the process, so that case runs in a process of its own — and delivers
    to a handler."""
    driver = tmp_path / "raise.py"
    driver.write_text(RAISE_SIGINT, encoding="utf-8")
    done = subprocess.run([sys.executable, str(driver), mode], capture_output=True, timeout=60,
                          env=kit_env(), **_platform.text_kwargs())
    assert done.returncode == 0 and done.stdout.strip() == printed, (done.stdout, done.stderr)


def test_the_extension_uses_the_process_control_of_the_project_it_was_given(monkeypatch, project,
                                                                           tmp_path):
    """Review 4, TV-1: nothing checked that run_kit used the project's bundle, and the extension's
    own copy of _platform was never armed. Both are here: its real classes refuse, and the injected
    bundle has to see the calls."""
    ext = load_extension(monkeypatch, "ext_injected")

    def refuse(*_args, **_kwargs):
        raise AssertionError("the extension used its own platform instead of the project's")

    for name in ("PosixProcessControl", "WindowsProcessControl"):
        cls = getattr(ext.core._platform, name)
        for method in port_methods(cls):
            monkeypatch.setattr(cls, method, refuse)
    seen = _ProcessesSeen(_platform.detect().processes)
    bundle = types.SimpleNamespace(name="recording", processes=seen,
                                   locks=_platform.detect().locks, files=_platform.detect().files,
                                   runs=_platform.detect().runs)
    monkeypatch.setattr(ext, "resolve_uv", lambda processes=None: sys.executable)  # `python run ...` fails at once
    ok, _ = asyncio.run(ext.run_kit(ext.core.Project(project, platform=bundle), ["status"],
                                    timeout=60))
    assert not ok
    assert seen.calls[:2] == ["base_env", "spawn_kwargs"], seen.calls


# --- behaviour the review reproduced as broken ---------------------------------------------------

# A leader that starts a long-lived child and exits at once, the shape `uv run` has when it hands
# off to python and dies. Two plain files rather than nested -c strings, so the quoting is legible.
LEADER = """
import subprocess
import sys

child = subprocess.Popen([sys.executable, sys.argv[1]], stdout=sys.stdout)
sys.stderr.write(str(child.pid) + chr(10))
sys.stderr.flush()
"""
DESCENDANT = """
import sys
import time

sys.stdout.write("CHILD-ALIVE" + chr(10))
sys.stdout.flush()
time.sleep(120)
"""


@pytest.mark.skipif(os.name == "nt", reason="process groups are a POSIX concept")
def test_the_group_is_ended_even_after_its_leader_has_been_reaped(tmp_path):
    """The case kill_tree exists for: uv exits, its python child keeps the pipes open.

    `os.getpgid(pid)` cannot be used to find that group, because the leader is gone. Addressing it
    by the leader's pid works and stays safe, because a process group id is not reused while the
    group still has members."""
    leader, descendant_script = tmp_path / "leader.py", tmp_path / "descendant.py"
    leader.write_text(LEADER, encoding="utf-8")
    descendant_script.write_text(DESCENDANT, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(leader), str(descendant_script)],
                            start_new_session=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    descendant = int(proc.stderr.readline().decode().strip())
    assert proc.stdout.readline().decode().strip() == "CHILD-ALIVE"
    proc.wait(30)
    try:
        assert proc.returncode == 0
        with pytest.raises(ProcessLookupError):
            os.getpgid(proc.pid)  # this is what the earlier implementation asked for
        assert os.getpgid(descendant) == proc.pid

        _platform.detect().processes.kill_tree(proc.pid)
        for _ in range(100):
            try:
                os.kill(descendant, 0)
            except OSError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("the surviving descendant was not reached")
    finally:
        with contextlib.suppress(OSError):
            os.kill(descendant, signal.SIGKILL)



def test_a_cancel_while_the_capture_hooks_install_stops_the_script(project, monkeypatch):
    """The cancel has to reach the script's own phase, not only the record's.

    Installing the capture hooks used to sit outside the interrupting scope, so a cancel arriving
    there set the flag and the script ran anyway — the record said `cancelled` while its side
    effects were on disk."""
    from labkit import capture, runner  # noqa: PLC0415 - needs the analysis libraries

    real_install = capture.install

    def install_then_cancel(state):
        signal.raise_signal(signal.SIGINT)
        return real_install(state)

    monkeypatch.setattr(capture, "install", install_then_cancel)
    body = ("from pathlib import Path\n"
            "Path('data/derived/ran.txt').write_text('x', encoding='utf-8')\n")
    script = write_script(project, "23_cancel_setup.py", body)
    result = runner.execute(core.Project(project), script=script, title="설치 중 취소", actor="test")

    assert result["run"]["status"] == "cancelled", result["run"]
    assert not (Path(project) / "data/derived/ran.txt").exists(), "the script ran after the cancel"


RAN = "from pathlib import Path\nPath('data/derived/ran.txt').write_text('x', encoding='utf-8')\n"


def test_a_cancel_before_the_script_starts_stops_the_run_before_it(project, monkeypatch):
    """A cancel handled while only the flag-only scope is in place — here at the redirection of
    the run's output — used to set the flag and let the script run to the end under a record that
    said `cancelled` (review 4, RIO-1)."""
    from labkit import runner  # noqa: PLC0415 - needs the analysis libraries

    real = runner._redirected

    @contextlib.contextmanager
    def redirected_then_cancel(*args):
        signal.raise_signal(signal.SIGINT)
        with real(*args):
            yield

    monkeypatch.setattr(runner, "_redirected", redirected_then_cancel)
    before = _handlers()
    result = runner.execute(core.Project(project), script=write_script(project, "26_early.py", RAN),
                            title="시작 전 취소", actor="test")
    assert result["run"]["status"] == "cancelled", result["run"]
    assert not (Path(project) / "data/derived/ran.txt").exists(), "the script ran after the cancel"
    assert _handlers() == before


def test_a_cancel_while_the_run_is_being_set_up_is_recorded(project, monkeypatch):
    # Before the flag-only scope covered the run's setup, this raised KeyboardInterrupt out of
    # execute and left a record that said `running`.
    from labkit import runner  # noqa: PLC0415

    real = runner.tree_stat
    calls = []

    def tree_stat_then_cancel(project_):
        calls.append(1)
        if len(calls) == 1:
            signal.raise_signal(signal.SIGINT)
        return real(project_)

    monkeypatch.setattr(runner, "tree_stat", tree_stat_then_cancel)
    try:
        result = runner.execute(core.Project(project),
                                script=write_script(project, "27_setup.py", RAN),
                                title="준비 중 취소", actor="test")
    except KeyboardInterrupt:
        pytest.fail("the cancel escaped execute and left the run's record behind as running")
    run = result["run"]
    assert run["status"] == "cancelled", run
    assert not (Path(project) / "data/derived/ran.txt").exists()
    # What makes it the setup's own cancel, rather than one only recorded and raised when the
    # script's region is entered (review 6, CH-7): its message, the paths its record names, and a
    # finalize with nothing to diff.
    assert run["error"]["message"] == "cancelled before the script started", run["error"]
    assert (Path(project) / run["stdout"]).is_file() and (Path(project) / run["stderr"]).is_file()
    assert "finalize_errors" not in run and run["outputs"] == [], run


def test_a_cancel_while_an_input_is_hashed_records_no_half_made_input(project, monkeypatch):
    """Review 6, SG-7: the input entry used to be created before its hash was taken, so a cancel
    during the hash recorded an input with no hash and no source."""
    from labkit import runner  # noqa: PLC0415

    real_sha = core.HashCache.sha

    def sha_then_cancel(self, path):
        signal.raise_signal(signal.SIGINT)
        return real_sha(self, path)

    monkeypatch.setattr(core.HashCache, "sha", sha_then_cancel)
    body = "import polars as pl\npl.read_parquet('data/raw/sales.parquet')\n"
    result = runner.execute(core.Project(project), script=write_script(project, "45_hash.py", body),
                            title="해시 중 취소", actor="test")
    run = result["run"]
    assert run["status"] == "cancelled", run
    assert all(entry.get("sha256") and entry.get("via") for entry in run["inputs"]), run["inputs"]


def test_a_cancel_just_after_the_import_hook_went_in_leaves_no_hook_behind(project, monkeypatch):
    from labkit import capture, runner  # noqa: PLC0415

    real = capture.install

    def install_then_cancel(state):
        finder = real(state)
        signal.raise_signal(signal.SIGINT)
        return finder

    monkeypatch.setattr(capture, "install", install_then_cancel)
    before = list(sys.meta_path)
    result = runner.execute(core.Project(project), script=write_script(project, "28_hook.py", RAN),
                            title="훅 뒤 취소", actor="test")
    assert result["run"]["status"] == "cancelled", result["run"]
    assert sys.meta_path == before


def test_a_signal_at_any_handler_swap_leaves_the_handlers_and_the_record_intact(project,
                                                                              monkeypatch):
    """Deliver an interrupt just before each `signal.signal` call a run makes, in turn.

    A signal handled between two swaps used to leave a handler behind, and one handled during a
    restore left a raising handler in place through the writing of the record (review 4, RIO-4
    and RIO-5). A signal is delivered only into the kit's own handler or the CLI's default SIGINT
    handler: the default action would end the test runner, and a harness's handler — pytest-timeout
    uses SIGALRM (review 5, critic-2) — is not what is under test."""
    from labkit import runner  # noqa: PLC0415

    real_signal = signal.signal
    script = write_script(project, "29_swaps.py", "x = 1\n")
    counted = []
    monkeypatch.setattr(signal, "signal", lambda sig, h: (counted.append(sig), real_signal(sig, h))[1])
    runner.execute(core.Project(project), script=script, title="셈", actor="test")
    total = len(counted)
    assert total >= 4, counted
    kinds = [s for s in (getattr(signal, n, None) for n in ("SIGINT", "SIGTERM", "SIGALRM",
                                                             "SIGBREAK")) if s is not None]
    for sig in kinds:
        for nth in range(1, total + 1):
            seen = []

            def injecting(signum, handler, sig=sig, nth=nth, seen=seen):
                seen.append(signum)
                current = signal.getsignal(sig)
                if len(seen) == nth and (current is signal.default_int_handler or isinstance(
                        getattr(current, "__self__", None), _platform._RunGuard)):
                    signal.raise_signal(sig)
                return real_signal(signum, handler)

            monkeypatch.setattr(signal, "signal", injecting)
            before = _handlers()
            try:
                result = runner.execute(core.Project(project), script=script, title=f"{sig}-{nth}",
                                        actor="test")
            except KeyboardInterrupt:
                result = None  # before the run's first scope: nothing was recorded yet
            finally:
                monkeypatch.setattr(signal, "signal", real_signal)
            assert _handlers() == before, (sig, nth)
            if result is not None:
                assert result["run"]["status"] in ("ok", "cancelled", "timeout"), (sig, nth)
    leftover = [r["status"] for r in core.list_runs(core.Project(project))
                if r["status"] in ("running", "incomplete")]
    assert not leftover, leftover


def _kit_code_objects():
    """Every function of runner.py and _platform.py, with the bodies of the generator-based
    context managers a run goes through (runner._redirected, the file lock, capture.paused), which
    live behind contextlib's wrapper."""
    from labkit import capture, runner  # noqa: PLC0415

    found, stack = set(), []
    for module in (runner, _platform, capture, core):
        for obj in vars(module).values():
            members = vars(obj).values() if isinstance(obj, type) and \
                obj.__module__ == module.__name__ else [obj]
            for member in members:
                function = getattr(member, "__func__", member)
                wrapped = getattr(function, "__wrapped__", None)
                for candidate in (function, wrapped):
                    code = getattr(candidate, "__code__", None)
                    if code is not None and code.co_filename == module.__file__ and \
                            (module in (runner, _platform) or candidate is wrapped):
                        stack.append(code)
    while stack:
        code = stack.pop()
        if code not in found:
            found.add(code)
            stack += [c for c in code.co_consts if isinstance(c, types.CodeType)]
    return found


SWEPT = [name for name in (("SIGINT", "SIGBREAK") if os.name == "nt"
                           else ("SIGINT", "SIGTERM", "SIGALRM")) if hasattr(signal, name)]
SWEEP_SAMPLES = 90


@NEEDS_MONITORING
@pytest.mark.parametrize("signame", SWEPT)
def test_a_signal_at_sampled_check_points_of_a_run_never_loses_its_record(tmp_path, monkeypatch,
                                                                         signame):
    """A signal at a check point of the kit's own code — a function's entry, a backward jump, a
    call — sampled evenly over a whole run, and a second one while the record is written. The record
    is never left `running` or missing, the guard's own exception never escapes `execute`, and the
    handlers are the ones it found. A KeyboardInterrupt may escape once the record is final: that is
    the host's own handler, put back (main behaved the same). A cancel delivered before the run is
    classified makes it `cancelled`, and no timer is left running after it (review 8, honesty-4
    and guard-1).

    Review 6 (SG-5, CR-1, CH-4): the sample used to inject at every instruction, NOPs outside the
    exception table included, and stopped short of the record write and the guard's exit. Each run
    counts its own points here, and the sample reaches past the longest run counted. The full
    sweep over every point is a probe (Issue #8 evidence); this keeps a sample in the suite."""
    from labkit import report, runner  # noqa: PLC0415

    sig = getattr(signal, signame)
    monkeypatch.setattr(report, "build", lambda project_, **kw: {})  # not under test, and slow
    real_environment = runner.environment

    def second_signal(project_):
        if _deliverable(sig):
            signal.raise_signal(sig)
        return real_environment(project_)

    monkeypatch.setattr(runner, "environment", second_signal)
    delivered_at, classified_at = [], []
    real_classify = runner._classify

    def classify(failure, guard, timeout, filename):
        classified_at.append(injector.count)
        return real_classify(failure, guard, timeout, filename)

    monkeypatch.setattr(runner, "_classify", classify)

    def deliver():
        if _deliverable(sig):
            delivered_at.append(injector.count)
            signal.raise_signal(sig)

    def one_run(n, injector, target):
        # A project per run: in a shared one each run goes through more points than the last,
        # and a sample fixed in advance would drift off the end of the shorter runs.
        project_ = core.init_project(tmp_path / f"p{n}", title="t", actor="test",
                                     template_dir=KIT / "labkit" / "template")
        (project_.scripts / "a.py").write_text("x = 1\n", encoding="utf-8")
        injector.arm(target)
        delivered_at.clear()
        classified_at.clear()
        before = _handlers()
        existing = {p.name for p in project_.runs.iterdir()} if project_.runs.is_dir() else set()
        injector.on = True
        try:
            runner.execute(project_, script="scripts/a.py", title="s", actor="test")
            escaped = None
        except BaseException as exc:  # noqa: BLE001 - what escaped is the finding
            escaped = type(exc).__name__
        finally:
            injector.on = False
        new = {p.name for p in project_.runs.iterdir()} - existing
        records = [core.read_json(project_.runs / name / "run.json").get("status", "missing")
                   if (project_.runs / name / "run.json").is_file() else "missing" for name in new]
        timer_left = hasattr(signal, "setitimer") and signal.getitimer(signal.ITIMER_REAL)[0] > 0
        if timer_left:
            signal.setitimer(signal.ITIMER_REAL, 0)  # it would end this process later
        if signame == "SIGALRM":  # the deadline's signal: counted in the script's region only
            wrong_status = bool(set(records) - {"ok", "timeout"})
        else:
            wrong_status = bool(delivered_at and classified_at
                                and delivered_at[0] <= classified_at[0] and records != ["cancelled"])
        problem = _handlers() != before or {"running", "missing"} & set(records) \
            or escaped == "Interrupted" or (escaped and escaped != "KeyboardInterrupt" and new) \
            or timer_left or wrong_status
        return injector.count, injector.fired, (target, escaped, records) if problem else None

    injector = _Injector(_kit_code_objects(), deliver)
    problems, delivered, counts = [], 0, []
    with injector:
        for n in range(3):  # counted, not injected: the longest of three runs
            # Target 0 is never reached. No target (None) delivers at every point, which ended the
            # SIGINT runs at their first point, so the sample was that one point (found in review
            # 8's follow-up: counts [1, 1, 1], one target).
            counts.append(one_run(n, injector, 0)[0])
        longest = max(counts)
        step = max(1, longest * 105 // 100 // SWEEP_SAMPLES)
        targets = list(range(1, longest * 105 // 100 + 1, step))
        for n, target in enumerate(targets, start=3):
            count, fired, problem = one_run(n, injector, target)
            counts.append(count)
            delivered += fired
            if problem:
                problems.append(problem)
    assert not problems, problems[:5]
    assert len(targets) >= SWEEP_SAMPLES * 3 // 4, (len(targets), counts[:3])
    assert delivered >= len(targets) * 3 // 4, (delivered, len(targets), counts[:4])
    assert max(t for t in targets) >= min(counts), "the sample stopped short of a run's end"


def test_the_script_s_own_streams_are_taken_back_when_it_ends(tmp_path):
    """_exec_script puts back all four names a script may replace, the interpreter's originals as
    well (review 6, CH-7: a mutant restoring two went unnoticed)."""
    from labkit import runner  # noqa: PLC0415

    before = tuple(getattr(sys, name) for name in runner.STREAM_NAMES)
    body = b"import io, sys\nfor name in ('stdout', 'stderr', '__stdout__', '__stderr__'):\n" \
           b"    setattr(sys, name, io.StringIO())\n"
    runner._exec_script(body, tmp_path / "replaces.py", tmp_path)
    assert tuple(getattr(sys, name) for name in runner.STREAM_NAMES) == before


class _RefusesToFlush(io.StringIO):
    def flush(self):
        raise KeyboardInterrupt


def test_the_output_redirection_is_undone_even_when_a_stream_raises_on_flush(tmp_path):
    """Review 6, ONC-12: the end of a run flushes whatever the four names hold and then restores the
    names and the descriptors. A stream installed after the script — by a thread of it, say —
    that raises a BaseException on flush must stop neither the restore nor the caller."""
    from labkit import runner  # noqa: PLC0415

    names = runner.STREAM_NAMES
    before = tuple(getattr(sys, name) for name in names)
    fds = [os.fstat(1).st_ino, os.fstat(2).st_ino]
    try:
        with runner._redirected(tmp_path / "out.txt", tmp_path / "err.txt"):
            print("RUN-OUTPUT")
            sys.stdout = _RefusesToFlush()
            sys.__stderr__ = _RefusesToFlush()
    except KeyboardInterrupt:
        for name, stream in zip(names, before, strict=True):
            setattr(sys, name, stream)
        pytest.fail("a stream's KeyboardInterrupt on flush escaped the end of the run")
    assert tuple(getattr(sys, name) for name in names) == before
    assert [os.fstat(1).st_ino, os.fstat(2).st_ino] == fds
    assert "RUN-OUTPUT" in (tmp_path / "out.txt").read_text(encoding="utf-8")


STREAM_LAYERS = ("import sys\n"
                 "for name in ('stdout', 'stderr'):\n"
                 "    s = getattr(sys, name)\n"
                 "    print(name, type(s.buffer).__name__, s.line_buffering, s.write_through,\n"
                 "          s.mode, s.name)\n")


@pytest.mark.parametrize("unbuffered", [False, True])
def test_the_run_streams_are_layered_as_the_interpreter_s_own(project, unbuffered):
    """Review 6, ONC-1 and CH-3: only line_buffering was copied, so under -u or PYTHONUNBUFFERED —
    where CPython writes both streams straight through to the raw file — the run's streams were
    block-buffered and a forced end lost what main kept. And they carry CPython's `mode` and names
    (ONC-14)."""
    env = kit_env(**({"PYTHONUNBUFFERED": "1"} if unbuffered else {}))
    done = subprocess.run([sys.executable, "-m", "labkit", "--project", str(project), "run",
                           write_script(project, f"46_layers_{unbuffered}.py", STREAM_LAYERS),
                           "--title", "layers"], capture_output=True, env=env, timeout=120,
                          **_platform.text_kwargs())
    result = json.loads([line for line in done.stdout.splitlines() if line.startswith("{")][-1])
    lines = result["stdout_tail"].splitlines()
    if unbuffered:
        assert lines == ["stdout FileIO False True w <stdout>",
                         "stderr FileIO False True w <stderr>"], lines
    else:
        assert lines == ["stdout BufferedWriter False False w <stdout>",
                         "stderr BufferedWriter True False w <stderr>"], lines


KILLED_MID_RUN = ("import sys, time\nprint('OUT-LINE-1')\nprint('ERR-LINE-1', file=sys.stderr)\n"
                  "time.sleep(60)\n")


@pytest.mark.parametrize("unbuffered", [False, True])
def test_a_forced_end_loses_no_more_of_the_logs_than_main_did(project, unbuffered):
    """What main kept when a run was killed: stderr always (line-buffered), stdout only when the
    interpreter wrote through (review 6, ONC-1: under PYTHONUNBUFFERED both were lost)."""
    env = kit_env(**({"PYTHONUNBUFFERED": "1"} if unbuffered else {}))
    proc = subprocess.Popen([sys.executable, "-m", "labkit", "--project", str(project), "run",
                             write_script(project, f"47_killed_{unbuffered}.py", KILLED_MID_RUN),
                             "--title", "killed"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, **_platform.detect().processes.spawn_kwargs())
    try:
        run_id = _wait_for_running_run(project)
        err = Path(project) / "runs" / run_id / "stderr.txt"
        end = time.monotonic() + 30
        # The record says `running` before the run's setup has opened its logs (a slow Windows
        # runner read the file before it existed, measurement run 36044596511).
        while time.monotonic() < end and not (err.is_file()
                                              and "ERR-LINE-1" in err.read_text(encoding="utf-8")):
            time.sleep(0.05)
    finally:
        _platform.detect().processes.kill_tree(proc.pid)
        proc.communicate(timeout=30)
    out = (Path(project) / "runs" / run_id / "stdout.txt").read_text(encoding="utf-8")
    assert "ERR-LINE-1" in err.read_text(encoding="utf-8")
    assert ("OUT-LINE-1" in out) is unbuffered, out


REFERENCES_HOST = """\
import sys, threading, time, types
from labkit import core, runner
project = core.Project(sys.argv[1])
hostlib = types.ModuleType("hostlib")
hostlib.out = sys.stdout  # a host library keeps the stream it found
sys.modules["hostlib"] = hostlib
logs = []
for n, script in enumerate(sys.argv[2:]):
    if n:  # the host, between two runs, through the stream the helper kept from the first
        sys.modules["helper"].say("HOST-BETWEEN-RUNS")
    result = runner.execute(project, script=script, title="host", actor="test")
    logs.append((project.root / result["run"]["stdout"]).read_text(encoding="utf-8"))


def left_running(out=sys.modules["helper"].OUT):
    # A thread a script left running, holding the run's stream: nothing it holds is destroyed at
    # exit, so nothing flushes the stream then unless the kit does.
    out.write("HOST-AFTER-THE-LAST-RUN\\n")
    threading.Event().wait()


threading.Thread(target=left_running, daemon=True).start()
time.sleep(0.2)
sys.stderr.write("LOGS=" + repr(logs) + "\\n")
"""
HELPER = "import sys\nOUT = sys.stdout\n\ndef say(text):\n    OUT.write(text + '\\n')\n"


def test_output_through_streams_kept_from_before_the_run_reaches_the_log(project, tmp_path):
    """Review 6, ONC-6 and ONC-7, in-process callers: a stream a host library took before the run,
    and one a module kept from an earlier run in the same process, used to write past the log —
    into the host's output — where main logged it."""
    host = tmp_path / "host.py"
    host.write_text(REFERENCES_HOST, encoding="utf-8")
    (Path(project) / "scripts" / "helper.py").write_text(HELPER, encoding="utf-8")
    one = write_script(project, "48_one.py", "import hostlib, helper\n"
                                             "hostlib.out.write('VIA-HOST-REFERENCE\\n')\n"
                                             "helper.say('RUN1-VIA-HELPER')\nprint('RUN1-PRINT')\n")
    two = write_script(project, "49_two.py", "import helper\nhelper.say('RUN2-VIA-HELPER')\n"
                                             "print('RUN2-PRINT')\n")
    done = subprocess.run([sys.executable, str(host), str(project), one, two], capture_output=True,
                          env=kit_env(), timeout=120, **_platform.text_kwargs())
    assert done.returncode == 0, done.stderr
    first, second = ast.literal_eval(done.stderr.split("LOGS=")[-1].strip())
    for expected in ("VIA-HOST-REFERENCE", "RUN1-VIA-HELPER", "RUN1-PRINT"):
        assert expected in first, first
    for expected in ("RUN2-VIA-HELPER", "RUN2-PRINT"):
        assert expected in second, second
    assert "RUN2" not in done.stdout, done.stdout
    # What the host wrote through a run's stream outside any run is the host's output: flushed to it
    # as the next run starts, and at exit after the last run (review 7, output-5 and output-6).
    assert "HOST-BETWEEN-RUNS" in done.stdout and "HOST-BETWEEN-RUNS" not in second, done.stdout
    assert "HOST-AFTER-THE-LAST-RUN" in done.stdout, done.stdout


def test_a_run_stream_over_a_stream_that_is_not_a_text_wrapper_takes_cpython_s_defaults():
    """A host's StringIO, or None under pythonw: CPython's defaults for a file, stdout
    block-buffered and stderr line-buffered (review 7, output-6)."""
    from labkit import runner  # noqa: PLC0415

    streams = [runner._run_stream(1, io.StringIO()), runner._run_stream(2, None)]
    try:
        assert [(type(s.buffer).__name__, s.line_buffering, s.write_through) for s in streams] == \
            [("BufferedWriter", False, False), ("BufferedWriter", True, False)]
    finally:
        for stream in streams:
            stream.close()


def test_a_script_whose_stream_raises_on_flush_cannot_take_the_result_line(project):
    # The flush at the end of a run used to skip the restore of the descriptors, so the CLI's own
    # JSON line went into the run's log and the record stayed `running` (review 4, RIO-6).
    body = ("import sys\n"
            "class Tee:\n"
            "    def __init__(self, inner): self.inner = inner\n"
            "    def write(self, s): return self.inner.write(s)\n"
            "    def flush(self): raise KeyboardInterrupt\n"
            "sys.__stdout__ = Tee(sys.__stdout__)\nprint('SCRIPT')\n")
    code, result = labkit(project, "run", write_script(project, "30_tee.py", body),
                          "--title", "흘리는 스트림")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert "SCRIPT" in result["stdout_tail"]


HOST = """\
import contextlib, io, sys
from labkit import core, runner
project, mode = core.Project(sys.argv[1]), sys.argv[2]
if mode == "refusing":
    # Refuses while fd 1 is still the host's (a full non-blocking pipe, a reader that has gone),
    # and would succeed once fd 1 had become the run's log.
    class Refusing(io.TextIOWrapper):
        refused = False

        def flush(self):
            if not Refusing.refused:
                Refusing.refused = True
                raise BlockingIOError("the host's pipe is full")
            return super().flush()
    sys.__stdout__ = Refusing(io.BufferedWriter(io.FileIO(1, "w", closefd=False)), encoding="utf-8")
sys.__stdout__.write("HOST-BEFORE-RUN")
with contextlib.redirect_stdout(io.StringIO()):
    result = runner.execute(project, script=sys.argv[3], title="host", actor="test")
log = (project.root / result["run"]["stdout"]).read_text(encoding="utf-8")
sys.stderr.write("LOG=" + repr(log) + "\\n")
"""


@pytest.mark.parametrize("mode", ["plain", "refusing"])
def test_an_in_process_caller_keeps_its_own_output_out_of_the_run(project, tmp_path, mode):
    """Bytes a host buffered before a run are the host's; bytes the script writes through the
    interpreter's original stdout are the run's (review 4, RIO-2, TV-2 and TV-8). A host stream
    that refused to flush before the run keeps its bytes rather than handing them to the log."""
    host = tmp_path / "host.py"
    host.write_text(HOST, encoding="utf-8")
    body = "import sys\nprint('SCRIPT')\nsys.__stdout__.write('VIA-DUNDER')\n"
    script = write_script(project, f"31_host_{mode}.py", body)
    done = subprocess.run([sys.executable, str(host), str(project), mode, script],
                          capture_output=True, env=kit_env(), timeout=120, **_platform.text_kwargs())
    assert done.returncode == 0, done.stderr
    log = done.stderr.split("LOG=")[-1].strip()
    assert "HOST-BEFORE-RUN" not in log, log
    assert "SCRIPT" in log, log
    if mode == "plain":
        assert "VIA-DUNDER" in log, log
        assert "HOST-BEFORE-RUN" in done.stdout, done.stdout


SLEEPER = "import time\nprint('started', flush=True)\ntime.sleep(60)\n"


def _wait_for_running_run(project: Path, timeout: float = 60) -> str:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for record in (Path(project) / "runs").glob("*/run.json"):
            with contextlib.suppress(OSError, ValueError):
                if json.loads(record.read_text(encoding="utf-8")).get("status") == "running":
                    return record.parent.name
        time.sleep(0.1)
    pytest.fail("the run never started")


def _ask_a_run_to_stop(project, tmp_path, name, body, *, wait=0.5):
    """Start the CLI on a script, wait until its run exists, ask it to stop the way the extension
    does on this platform — ProcessControl.interrupt with the request file — and return
    (exit code, the JSON result, seconds from the ask to the exit)."""
    request = tmp_path / f"{name}.cancel"
    proc = subprocess.Popen(
        [sys.executable, "-m", "labkit", "--project", str(project), "run",
         write_script(project, f"{name}.py", body), "--title", name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=kit_env(LAB_CANCEL_FILE=str(request)),
        **_platform.detect().processes.spawn_kwargs(), **_platform.text_kwargs())
    try:
        _wait_for_running_run(project)
        time.sleep(wait)
        t0 = time.perf_counter()
        assert _platform.detect().processes.interrupt(proc, request)
        out, err = proc.communicate(timeout=60)
        elapsed = time.perf_counter() - t0
    finally:
        if proc.poll() is None:
            _platform.detect().processes.kill_tree(proc.pid)
            proc.wait(30)
    lines = [line for line in out.splitlines() if line.startswith("{")]
    return proc.returncode, (json.loads(lines[-1]) if lines else None), elapsed, err


def test_the_way_the_extension_asks_stops_a_sleeping_run_and_records_it(project, tmp_path):
    """POSIX asks with SIGINT, Windows through the request file the kit watches; a single long sleep
    is the case the old Windows path could not wake (Issue #8 measurement)."""
    code, result, elapsed, err = _ask_a_run_to_stop(project, tmp_path, "32_sleeper", SLEEPER)
    assert elapsed < 5, (elapsed, err)
    assert code == 3 and result["run"]["status"] == "cancelled", (code, result, err)


CATASTROPHIC = ("import re\nprint('started', flush=True)\n"
                "re.search(r'(a+)+$', 'a' * 32 + 'b')\nprint('regex done', flush=True)\n")


@pytest.mark.skipif(os.name == "nt", reason="declared: on Windows the ask is a Python thread, which "
                                            "a regular expression holding the GIL starves")
def test_a_run_stuck_in_a_regular_expression_is_still_stopped(project, tmp_path):
    # Review 5, critic-1: a thread watching the request file never gets the GIL while _sre holds
    # it; the C-level SIGINT handler needs none, and _sre checks for signals (0.08 s measured).
    code, result, elapsed, err = _ask_a_run_to_stop(project, tmp_path, "33_regex", CATASTROPHIC,
                                                    wait=1.0)
    assert elapsed < 5, (elapsed, err)
    assert result["run"]["status"] == "cancelled", result
    assert "regex done" not in result["stdout_tail"]


def test_a_cancel_requested_before_the_command_started_is_not_missed(project, tmp_path):
    # Windows only has a request to find; POSIX has nothing to watch (its ask is a signal), and
    # this checks that the watcher costs nothing there either.
    request = tmp_path / "cancel"
    request.touch()
    done = subprocess.run(
        [sys.executable, "-m", "labkit", "--project", str(project), "run",
         write_script(project, "35_early.py", "print('ran')\n"), "--title", "미리 요청"],
        capture_output=True, env=kit_env(LAB_CANCEL_FILE=str(request)), timeout=60,
        **_platform.text_kwargs())
    statuses = [r["status"] for r in core.list_runs(core.Project(project))]
    assert "running" not in statuses and "incomplete" not in statuses, statuses
    if os.name == "nt":
        assert done.returncode in (3, 130), done.stdout
    else:
        assert done.returncode == 0, done.stdout


def test_output_written_through_the_original_stdout_still_reaches_the_log(project):
    """Anything still holding the stream the run replaced has to be flushed while fd 1 is the log.

    Otherwise the bytes surface after the descriptors are restored and land in front of the CLI's
    own JSON line, which is the line the extension parses."""
    body = "import sys\nsys.__stdout__.write('BUFFERED-OUTPUT')\n"
    code, result = labkit(project, "run", write_script(project, "24_buffered.py", body),
                          "--title", "원래 스트림")
    assert code == 0, result
    assert "BUFFERED-OUTPUT" in result["stdout_tail"], result["stdout_tail"]


# --- text encoding is explicit everywhere -----------------------------------------------------

# Read with the parser rather than with a pattern: the regex version of this test exempted exactly
# the `Path(...).read_text()` calls it existed to catch, treated a payload that happened to contain
# "rb" as a binary mode, and rejected a spaced `encoding = "utf-8"`.
#
# What it checks, per call form (review 4, TV-7, listed the forms an earlier version missed):
#   read_text / write_text on anything                   encoding, by keyword or position
#   open(...), io.open(...), codecs.open(...)            unless the mode has "b"
#   <object>.open(mode) — Path.open's signature          unless the mode has "b"
#   gzip / bz2 / lzma .open                              only when the mode has "t"
#   os.fdopen, fileinput.input                           unless the mode has "b"
#   tempfile.*TemporaryFile                              only when the mode lacks "b"
#   io.TextIOWrapper                                     always
#   subprocess run / Popen / check_output                when text, universal_newlines, encoding
#                                                        or errors makes it decode
#   subprocess getoutput / getstatusoutput               always
#   os.popen                                             always (it has no encoding parameter)
# `subprocess` is recognised under an alias and through `from subprocess import ...`. An explicit
# `encoding=None` is the locale default and counts as missing.
# Known gaps: a call reached through getattr, functools.partial or a variable holding the function;
# and `.open` on an object that is not a path (ZipFile.open, Image.open) is read as a file open.
NON_FILE_OPENERS = frozenset({"webbrowser", "request", "socket", "zipfile", "tarfile", "shelve",
                              "dbm", "sqlite3", "os"})
COMPRESSED_OPENERS = frozenset({"gzip", "bz2", "lzma"})
TEMPFILES = frozenset({"TemporaryFile", "NamedTemporaryFile", "SpooledTemporaryFile"})
SUBPROCESS_DECODING = frozenset({"run", "Popen", "check_output"})
SUBPROCESS_ALWAYS_TEXT = frozenset({"getoutput", "getstatusoutput"})
TEXT_MODE_KWARGS = ("text", "universal_newlines")
SOURCE_DIRS = ("kit", "extension", "tools", "src")


def _receiver(func: ast.AST) -> str:
    """`subprocess` for subprocess.run(...), `gzip` for gzip.open(...), '' for a bare call."""
    value = getattr(func, "value", None)
    return getattr(value, "id", "") if value is not None else ""


def _keyword(call: ast.Call, name: str):
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _argument(call: ast.Call, name: str, position: int | None):
    value = _keyword(call, name)
    if value is None and position is not None and len(call.args) > position:
        value = call.args[position]
    return value


def _has_encoding(call: ast.Call, position: int | None = None) -> bool:
    value = _argument(call, "encoding", position)
    if value is not None:
        return not (isinstance(value, ast.Constant) and value.value is None)
    # **_platform.text_kwargs() carries it.
    return any(k.arg is None and isinstance(k.value, ast.Call)
               and getattr(k.value.func, "attr", "") == "text_kwargs" for k in call.keywords)


def _mode(call: ast.Call, position: int, default: str) -> str | None:
    """The mode as written, the default when it is left out, None when it is not a literal."""
    value = _argument(call, "mode", position)
    if value is None:
        return default
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def _decodes_text(call: ast.Call) -> bool:
    for name in TEXT_MODE_KWARGS:
        value = _keyword(call, name)
        if isinstance(value, ast.Constant) and value.value is True:
            return True
    return _keyword(call, "errors") is not None or _has_encoding(call)


def _subprocess_names(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """Names this module gives the subprocess module, and the functions it imported from it."""
    modules, functions = {"subprocess"}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "subprocess"}
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            functions |= {a.asname or a.name: a.name for a in node.names}
    return modules, functions


def _text_call_without_encoding(node: ast.Call, subprocess_modules, subprocess_functions) -> bool:
    func = node.func
    name = getattr(func, "attr", None) or getattr(func, "id", "")
    receiver = _receiver(func)
    bare = isinstance(func, ast.Name)
    if bare and name in subprocess_functions:
        name, receiver = subprocess_functions[name], "subprocess"
    elif receiver in subprocess_modules:
        receiver = "subprocess"
    if name == "read_text":
        return not _has_encoding(node, 0)
    if name == "write_text":
        return not _has_encoding(node, 1)
    if receiver == "subprocess":
        if name in SUBPROCESS_ALWAYS_TEXT:
            return not _has_encoding(node)
        return name in SUBPROCESS_DECODING and _decodes_text(node) and not _has_encoding(node)
    if name == "popen" and receiver == "os":
        return True
    if name == "fdopen" and receiver == "os":
        mode = _mode(node, 1, "r")
        return mode is not None and "b" not in mode and not _has_encoding(node, 3)
    if name == "input" and receiver == "fileinput":
        mode = _mode(node, None, "r")
        return mode is not None and "b" not in mode and not _has_encoding(node)
    if name in TEMPFILES:
        mode = _mode(node, 0, "w+b")
        return mode is not None and "b" not in mode and not _has_encoding(node, 3)
    if name == "TextIOWrapper":
        return not _has_encoding(node, 1)
    if name != "open":
        return False
    if receiver in COMPRESSED_OPENERS:
        mode = _mode(node, 1, "rb")
        return mode is not None and "t" in mode and not _has_encoding(node, 3)
    if bare or receiver in ("io", "codecs"):
        mode = _mode(node, 1, "r")
        return mode is not None and "b" not in mode and not _has_encoding(node, 3)
    if receiver in NON_FILE_OPENERS:
        return False
    mode = _mode(node, 0, "r")  # Path.open(mode, buffering, encoding)
    return mode is not None and "b" not in mode and not _has_encoding(node, 2)


def unencoded_text_calls(paths) -> list[str]:
    """Calls of the forms listed above that read or write text without naming an encoding."""
    offenders = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules, functions = _subprocess_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _text_call_without_encoding(node, modules, functions):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", "")
                offenders.append(f"{path}:{node.lineno} {name}(...)")
    return offenders


def test_the_shipped_code_names_the_encoding_of_every_text_call_the_scan_knows():
    """A locale-dependent default turns Korean records into an exception on a Windows machine, and
    reconfiguring this process's streams does not reach a child's pipe or a file.

    Scope, deliberately: the Python of `kit`, `extension`, `tools` and `src` (the distribution's
    `lab` launcher). `tests/` is PR-3's cleanup, `bin/lab` is shell, and the template files are not
    Python. A directory that is not there would make the scan pass on nothing, so that fails."""
    missing = [d for d in SOURCE_DIRS if not (PACK / d).is_dir()]
    assert not missing, f"the scan's directories moved: {missing}"
    paths = [p for directory in SOURCE_DIRS
             for p in sorted((PACK / directory).rglob("*.py")) if "__pycache__" not in p.parts]
    offenders = unencoded_text_calls(paths)
    assert not offenders, "text without an explicit encoding:\n" + "\n".join(offenders)


CAUGHT = """
import bz2
import codecs
import fileinput
import gzip
import io
import os
import subprocess
import subprocess as sp
import tempfile
from pathlib import Path
from subprocess import check_output as co, run

Path("a").write_text("한글")
Path("b").read_text()
io.open("c").read()
gzip.open("x.gz", "rt").read()
subprocess.run(["x"], text=True)
subprocess.run(["y"], universal_newlines = True)
Path("h").write_text("rb")
open("i", encoding=None)
Path("j").open()
Path("k").open("w")
codecs.open("l")
bz2.open("m.bz2", mode="wt")
os.fdopen(3, "w")
os.popen("ls")
fileinput.input("n")
tempfile.NamedTemporaryFile("w")
tempfile.TemporaryFile(mode="w+")
io.TextIOWrapper(io.BytesIO())
subprocess.check_output(["x"], text=True)
subprocess.getoutput("x")
subprocess.getstatusoutput("x")
subprocess.run(["x"], capture_output=True, errors="replace")
sp.run(["x"], text=True)
run(["x"], text=True)
co(["x"], universal_newlines=True)
"""
ALLOWED = """
import gzip
import io
import lzma
import os
import subprocess
import tempfile
import webbrowser
from pathlib import Path

from labkit import _platform

Path("d").write_text("ok", encoding="utf-8")
Path("e").read_text(encoding = "utf-8")
Path("e2").read_text("utf-8")
Path("e3").write_text("ok", "utf-8")
open("f", "rb").read()
open("g", mode="wb").write(b"")
open("g2", "r", -1, "utf-8")
Path("p").open("rb")
Path("p2").open("w", encoding="utf-8")
gzip.open("x.gz")
lzma.open("x.xz", "rb")
os.open("q", os.O_RDONLY)
os.fdopen(3, "rb")
tempfile.NamedTemporaryFile()
tempfile.NamedTemporaryFile(mode="w", encoding="utf-8")
io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
subprocess.run(["z"], capture_output=True)
subprocess.run(["w"], capture_output=True, **_platform.text_kwargs())
subprocess.check_output(["v"])
subprocess.getoutput("u", encoding="utf-8")
webbrowser.open("https://example.invalid")
"""


def test_the_encoding_scan_flags_the_listed_forms_and_passes_their_encoded_counterparts(tmp_path):
    # `write_text("rb")` belongs in the caught half: the payload is text and the mode argument the
    # regex version mistook it for does not exist on write_text.
    caught, allowed = tmp_path / "caught.py", tmp_path / "allowed.py"
    caught.write_text(CAUGHT, encoding="utf-8")
    allowed.write_text(ALLOWED, encoding="utf-8")
    flagged = unencoded_text_calls([caught])
    expected = sum(1 for line in CAUGHT.splitlines() if "(" in line and not line.startswith(
        ("import", "from")))
    assert len(flagged) == expected == 25, flagged
    assert unencoded_text_calls([allowed]) == []


# --- review 8 -------------------------------------------------------------------------------------

@POSIX_GUARD
@pytest.mark.parametrize("sigalrm", ["raised_by_the_script", "a_periodic_timer_of_the_script"])
def test_the_timer_a_region_set_is_cancelled_whoever_s_sigalrm_ended_it(project, monkeypatch,
                                                                       sigalrm):
    """Review 8, guard-1: the guard took a SIGALRM that was not its own timer's last expiry for the
    deadline, cleared `armed` first, and disarming then skipped the timer. The kit's own deadline,
    or the script's periodic timer, ran on after the run and reached the host: an in-process host
    that kept SIGALRM's default action was killed, the CLI too after its JSON line. Main cancelled
    ITIMER_REAL after every script. Any SIGALRM in the region is the deadline, as on main."""
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    body = {"raised_by_the_script": "signal.raise_signal(signal.SIGALRM)\n",
            "a_periodic_timer_of_the_script": "signal.setitimer(signal.ITIMER_REAL, 0.2, 0.2)\n"}
    write_script(Path(project), "80_alarm.py",
                 "import signal, time\n" + body[sigalrm] + "time.sleep(3)\n")
    late = []
    saved = signal.signal(signal.SIGALRM, lambda *_a: late.append(time.monotonic()))
    try:
        result = runner.execute(core.Project(project), script="scripts/80_alarm.py",
                                title="alarm", timeout=30, actor="test")
        left = signal.getitimer(signal.ITIMER_REAL)
        time.sleep(0.6)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, saved)
    assert result["run"]["status"] == "timeout", result["run"]
    assert left == (0.0, 0.0) and not late, (left, late)


@NEEDS_MONITORING
@POSIX_GUARD
def test_a_disarm_cut_short_at_its_call_still_cancels_the_timer():
    """Found by revision 3.3's sweep: disarming cleared its mark before it called setitimer, so a
    raising handler the script left, run at that call, left the region's timer running after the
    run although settle retried the disarm. The script's handler is delivered at each check point
    of disarm as the region is left."""
    before = _handlers()
    saved = signal.getsignal(signal.SIGTERM)
    reached = 0
    with _Injector({_platform._PosixGuard.disarm.__code__},
                   lambda: _script_s_own(signal.SIGTERM) and signal.raise_signal(signal.SIGTERM)) \
            as injector:
        for target in range(1, 20):
            injector.arm(target)
            caught = None
            try:
                with _guard() as guard:
                    try:
                        with guard.interruptible(deadline=30.0):
                            signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))  # the script's
                            injector.on = True
                    except SystemExit as exc:
                        caught = exc
                    finally:
                        injector.on = False
                    guard.settle(caught)
                left = signal.getitimer(signal.ITIMER_REAL)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGTERM, saved)
            assert left[0] == 0, (target, left)
            if not injector.fired:
                break
            reached += 1
    assert reached >= 2, reached
    assert _handlers() == before


@POSIX_GUARD
def test_the_clock_s_deadline_is_taken_before_the_kernel_s_timer_starts(monkeypatch):
    """Review 8, guard-2: arm took the clock's deadline after setitimer returned, so a process
    descheduled in between had a clock deadline later than the kernel's alarm, and where only the
    clock counts a deadline — an alarm handler cut short at its entry, a cancel coming with it — the
    run was recorded `ok` or `cancelled`."""
    seen = []
    real = signal.setitimer
    guard = _guard()

    def setitimer(which, seconds, *interval):
        if seconds:
            seen.append((guard.deadline_at, time.monotonic() + seconds))
        return real(which, seconds, *interval)

    monkeypatch.setattr(signal, "setitimer", setitimer)
    with guard, guard.interruptible(deadline=30.0):
        pass
    ((clock, kernel),) = seen
    assert clock is not None and clock <= kernel, seen


def test_the_windows_timer_counts_its_deadline_before_it_raises_the_signal(monkeypatch):
    """Review 8, honesty-5 (H2): the count does not wait for the delivery, so a delivery that fails,
    or a SIGINT the script's handler takes at once, finds the deadline counted already."""
    guard = _windows_guard(monkeypatch)
    counted = []

    def deliver():
        counted.append(guard.expired)
        raise OSError("the delivery failed")

    monkeypatch.setattr(_platform, "raise_sigint_in_main", deliver)
    with guard:
        with guard.interruptible(deadline=30.0):
            guard._timer.cancel()
            with pytest.raises(OSError):
                guard._fire(guard._timer)
        guard.settle()
    assert counted == [True] and guard.expired


@NEEDS_MONITORING
@pytest.mark.parametrize("platform_guard", ["posix", "windows"])
def test_a_cancel_at_the_start_of_the_region_s_exit_leaves_the_deadline_to_the_settle(
        monkeypatch, platform_guard):
    """Review 8, honesty-3 (M24): a cancel handled at the first instruction of the region's exit
    must change nothing there — spending the one-shot there stops the raising — so a deadline that
    passes before the exit's settle is still counted: by the clock on POSIX, by the timer on
    Windows. Both came before the script ended; the run is a timeout."""
    guard = _windows_guard(monkeypatch) if platform_guard == "windows" else _guard()

    def cancel_then_the_deadline():
        signal.raise_signal(signal.SIGINT)
        if platform_guard == "windows":
            guard._timer.cancel()
            with monkeypatch.context() as patch:
                patch.setattr(_platform, "raise_sigint_in_main", lambda: True)
                guard._fire(guard._timer)
        else:
            guard.deadline_at = time.monotonic() - 0.001

    before = _handlers()
    injector = _Injector({_platform._Region.__exit__.__code__}, cancel_then_the_deadline,
                         entries_only=True)
    with injector, guard:
        with guard.interruptible(deadline=30.0):
            injector.arm(1)
            injector.on = True
        injector.on = False
        assert injector.fired
        guard.settle()
    assert _outcome(guard) == {"timeout": True, "cancel": True}, _outcome(guard)
    assert _handlers() == before


def test_a_cancel_the_script_swallowed_is_still_recorded(project):
    """Review 8, honesty-4 (H15): the runner classifies by the guard's cancel flag. A script that
    catches the interruption and runs on to its end was recorded `ok` once the flag went unread."""
    body = ("import signal, time\ntry:\n    signal.raise_signal(signal.SIGINT)\n    time.sleep(1)\n"
            "except BaseException:\n    pass\nprint('ran on')\n")
    code, result = labkit(project, "run", write_script(project, "81_swallow.py", body),
                          "--title", "swallow")
    assert code == 3 and result["run"]["status"] == "cancelled", result["run"]


CUT_SHORT_SCRIPTS = {
    "exits": "signal.raise_signal(signal.SIGINT)\ntime.sleep(1)\n",
    # what cut the handler short is chained to the error the script raises instead
    "turns_it_into_an_error": "try:\n    signal.raise_signal(signal.SIGINT)\n    time.sleep(1)\n"
                              "except SystemExit as exc:\n    raise RuntimeError('stopped') from exc\n",
    # review 9, honesty-2: through __context__ alone, through __cause__ alone (raised after the
    # except block), and three deep
    "replaces_it_inside_its_except": "try:\n    signal.raise_signal(signal.SIGINT)\n"
                                     "    time.sleep(1)\nexcept SystemExit:\n"
                                     "    raise RuntimeError('stopped')\n",
    "chains_it_after_its_except": "try:\n    signal.raise_signal(signal.SIGINT)\n"
                                  "    time.sleep(1)\nexcept SystemExit as exc:\n    kept = exc\n"
                                  "raise RuntimeError('stopped') from kept\n",
    "wraps_it_twice": "try:\n    try:\n        signal.raise_signal(signal.SIGINT)\n"
                      "        time.sleep(1)\n    except SystemExit as exc:\n"
                      "        raise ValueError('inner') from exc\n"
                      "except ValueError as exc:\n    raise RuntimeError('outer') from exc\n",
    # review 9, guard-4: asyncio's TaskGroup raises what a task raised as a group's member
    "in_a_task_group": "import asyncio\n"
                       "def fail(*_a):\n    raise RuntimeError('shut down')\n"
                       "signal.signal(signal.SIGTERM, fail)\n"
                       "async def task():\n    signal.raise_signal(signal.SIGINT)\n"
                       "    await asyncio.sleep(1)\n"
                       "async def main():\n    async with asyncio.TaskGroup() as group:\n"
                       "        group.create_task(task())\n"
                       "asyncio.run(main())\n",
    # review 10, honesty-6: a SystemExit member makes a BaseExceptionGroup, which review 9's
    # honesty-9 raised this way
    "in_a_base_group_after_its_except": "try:\n    signal.raise_signal(signal.SIGINT)\n"
                                        "    time.sleep(1)\nexcept SystemExit as exc:\n"
                                        "    kept = exc\n"
                                        "raise BaseExceptionGroup('steps', [kept])\n",
}


@NEEDS_MONITORING
@POSIX_GUARD
@pytest.mark.parametrize("script", sorted(CUT_SHORT_SCRIPTS))
def test_a_cancel_cut_short_by_the_script_s_handler_is_recorded_at_every_point(project,
                                                                             monkeypatch,
                                                                             script):
    """Review 8, guard-4 and honesty-4: review 7's guard-1 scenario through the runner. The script
    leaves `SIGTERM -> sys.exit(0)`; a cancel comes, and the script's SIGTERM is handled at each
    check point of the guard's handler in turn — at its entry too, where the handler has not run a
    single statement and settle finds the cancel in the traceback of the SystemExit, or of what the
    script chained it to. The run is `cancelled` every time: not `ok` or `error`, as it was at the
    entry, and as it was at every point once the runner stopped reading the flag."""
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    write_script(Path(project), "82_shutdown.py",
                 "import signal, sys, time\nsignal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\n"
                 + CUT_SHORT_SCRIPTS[script] + "print('ran on')\n")
    before = _handlers()
    outcomes = []
    with _Injector(_handler_path(_platform._PosixGuard),
                   lambda: _inside_a_guard_handler() and _script_s_own(signal.SIGTERM)
                   and signal.raise_signal(signal.SIGTERM)) as injector:
        for target in range(1, 100):
            injector.arm(target)
            injector.on = True
            try:
                result = runner.execute(core.Project(project), script="scripts/82_shutdown.py",
                                        title="t", actor="test", timeout=30)
            finally:
                injector.on = False
            outcomes.append((target, result["run"]["status"]))
            if not injector.fired:
                break
    assert all(status == "cancelled" for _, status in outcomes), outcomes
    assert len(outcomes) >= 6, outcomes
    assert _handlers() == before


def test_what_a_leftover_thread_wrote_after_its_run_comes_before_the_result(project):
    """Review 8, rest-1 and honesty-1: a thread a script left running wrote through the run's stream
    after the run, and revision 3.2 flushed that only at exit — after the CLI's JSON line, so the
    extension, which takes the last line that parses as JSON, returned the thread's line as the
    result. Main had such lines ahead of the result: its scripts wrote through the CLI's stream."""
    body = ("import os, sys, threading, time\nout = sys.stdout\nlog = os.fstat(1)\n"
            "def later():\n"
            "    while os.path.samestat(os.fstat(1), log):\n"
            "        time.sleep(0.001)\n"
            "    out.write('{\"from\": \"a leftover thread\"}\\n')\n"
            "    threading.Event().wait()\n"
            "threading.Thread(target=later, daemon=True).start()\n")
    cmd = [sys.executable, "-m", "labkit", "--project", str(project), "run",
           write_script(project, "83_leftover.py", body), "--title", "leftover"]
    proc = subprocess.run(cmd, capture_output=True, env=kit_env(), timeout=120,
                          **_platform.text_kwargs())
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    assert lines and json.loads(lines[-1]).get("ok") is True, proc.stdout
    assert '{"from": "a leftover thread"}' in lines[:-1], proc.stdout


def test_what_a_thread_wrote_after_its_run_survives_the_collection_of_its_module(
        project, monkeypatch, capfd, tmp_path):
    """Review 8, rest-4: the earlier runs' streams were only weakly held. Once the thread that wrote
    through one had ended, the stream was held by the script's module alone — a reference cycle —
    and the collector, finalizing its layers in either order, dropped the line before anything
    flushed it. Main delivered it. The latest run's streams are held now. Which layer the collector
    finalizes first varies with the process, so the line alone does not show it; the stream's
    survival does."""
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    done = tmp_path / "written"
    monkeypatch.setenv("WRITTEN_FLAG", str(done))
    probe = types.SimpleNamespace(ref=None)
    monkeypatch.setitem(sys.modules, "_leftover_probe", probe)
    write_script(Path(project), "84_ends.py",
                 "import os, sys, threading, time, weakref\nimport _leftover_probe\n"
                 "_leftover_probe.ref = weakref.ref(sys.stdout)\n"
                 "out = sys.stdout\nlog = os.fstat(1)\n"
                 "def later():\n"
                 "    while os.path.samestat(os.fstat(1), log):\n"
                 "        time.sleep(0.001)\n"
                 "    out.write('LEFTOVER-LINE\\n')\n"
                 "    open(os.environ['WRITTEN_FLAG'], 'w').close()\n"
                 "threading.Thread(target=later, daemon=True).start()\n")
    runner.execute(core.Project(project), script="scripts/84_ends.py", title="ends", actor="test")
    deadline = time.monotonic() + 30
    while not done.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert done.exists(), "the script's thread never wrote"
    time.sleep(0.1)  # the thread ends after its flag
    gc.collect()
    gc.collect()
    assert probe.ref() is not None, "the run's stream was collected before anything flushed it"
    runner.flush_run_streams()
    assert "LEFTOVER-LINE" in capfd.readouterr().out


def _ignores_case(directory: Path) -> bool:
    probe = directory / "Case-Probe"
    probe.write_bytes(b"")
    try:
        return (directory / "case-probe").exists()
    finally:
        probe.unlink()


CASE_VARIANTS = {
    # the script names its file after the title as written; the chart's id is its lower-case slug
    "chart_then_file": "lab.chart(fig, 'AUX')\nfig.savefig(charts / 'AUX.png')\n",
    "file_then_captured_and_named": "ax.set_title('AUX')\nfig.savefig(charts / 'AUX.png')\n"
                                    "fig.show()\nlab.chart(fig, 'named')\n",
    # review 9, rest-3: main wrote its chart into the script's file and recorded that file twice
    "file_then_chart": "fig.savefig(charts / 'AUX.png')\nlab.chart(fig, 'AUX')\n",
}
CASE_VARIANT_CHARTS = {
    ("chart_then_file", True): [("aux", "matplotlib")],
    ("chart_then_file", False): [("aux", "matplotlib"), ("aux-2", "file")],
    ("file_then_captured_and_named", True): [("named", "matplotlib")],
    ("file_then_captured_and_named", False): [("named", "matplotlib"), ("aux", "file")],
    ("file_then_chart", True): [("aux", "matplotlib")],
    ("file_then_chart", False): [("aux", "matplotlib"), ("aux-2", "file")],
}


@pytest.mark.parametrize("case", sorted(CASE_VARIANTS))
def test_a_reserved_id_s_own_name_in_another_case_is_that_file_where_case_is_ignored(project,
                                                                                   case):
    """Review 8, rest-3: own names were compared as strings, so where the file system ignores case
    (macOS by default) the script's charts/AUX.png — the file main wrote, overwrote or unlinked for
    chart `aux` — became a second chart with a reserved-name warning. Where case counts, it is a
    file of its own, as it was on main. Where case is ignored and the file came first, main recorded
    the one file twice, as `aux` and `aux-2`; it is one chart now (review 9, rest-3)."""
    code, result = labkit(project, "run", write_script(project, f"85_{case}.py",
                                                       CHART_BODY + CASE_VARIANTS[case]),
                          "--title", case)
    assert code == 0, result
    charts = [(c["id"], c["kind"]) for c in result["run"]["charts"]]
    ignores = _ignores_case(Path(project))
    assert charts == CASE_VARIANT_CHARTS[case, ignores], (ignores, charts)


def test_a_case_variant_is_an_own_name_only_when_it_is_the_same_file(project, monkeypatch):
    """Review 9, rest-7: whether charts/AUX.png is chart `aux`'s own name is decided by the file
    system, which on a case-sensitive one says it is another file; nothing but a case-sensitive
    volume pinned that half. Told it is another file, the kit records it as a chart of its own
    wherever it runs."""
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    monkeypatch.setattr(os.path, "samefile", lambda _a, _b: False)
    write_script(Path(project), "88_case.py", CHART_BODY + CASE_VARIANTS["chart_then_file"])
    result = runner.execute(core.Project(project), script="scripts/88_case.py", title="case",
                            actor="test")
    charts = [(c["id"], c["kind"]) for c in result["run"]["charts"]]
    assert charts == CASE_VARIANT_CHARTS["chart_then_file", False], charts


def test_a_record_that_cannot_be_written_as_utf8_leaves_no_run_directory(project):
    """Review 8, rest-5: the first files' `try` caught OSError only, and a title holding a lone
    surrogate — what POSIX hands over for bytes that are not UTF-8 — failed the record's encoding
    with a ValueError: a traceback, no JSON line, and runs/<id> left with the code and a partial
    record."""
    from labkit import runner  # noqa: PLC0415

    write_script(Path(project), "86_x.py", "x = 1\n")
    with pytest.raises(core.LabError) as exc:
        runner.execute(core.Project(project), script="scripts/86_x.py", title="caf\udce9",
                       actor="test")
    assert exc.value.code == "record_failed" and "UTF-8" in (exc.value.hint or ""), \
        exc.value.to_json()
    assert not list((Path(project) / "runs").glob("*")), list((Path(project) / "runs").glob("*"))


@pytest.mark.skipif(os.name == "nt", reason="a directory symlink needs privileges on Windows")
def test_a_rerun_through_an_outward_link_is_refused_behind_a_loop_too(project, tmp_path):
    """Review 8, rest-7: Path.resolve raises for a link loop on CPython 3.12 and resolves up to it on
    3.13, so a record reaching outside through a link and then a loop was refused on 3.13 and 3.14
    and rerun on 3.12. Links are followed with os.path.realpath now, the same on every version."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "loop").symlink_to("loop")
    (Path(project) / "out").symlink_to(outside)
    run_id = _recorded_as(project, "out/loop/x.py")
    code, result = labkit(project, "run", "--rerun-of", run_id, "--title", "outward")
    assert code == 1 and result["error"]["code"] == "rerun_snapshot", result


def test_a_literal_naming_a_home_nobody_has_does_not_drop_the_other_scanned_inputs(project):
    """Review 8, critic-1: the static scan expanded every literal with pathlib, which raises for a
    `~user` nobody has — an Office lock file's `~$budget.xlsx` — and that one literal lost every
    input the scan had found. Each literal now fails on its own. Only the scan sees a file read with
    open(). Review 9, rest-1: a SQL text naming a quoted data file did the same on every version —
    the scan took the names in it as a regular expression's match tuples. Review 9, rest-5 and
    honesty-1: a `~` name that exists in the project, read as written, is an input like any
    other. The second Windows run: Windows guessed a home for it (C:\\Users\\$budget.xlsx), so
    Project.path now expands a leading `~name` only when that home exists, there too."""
    raw = Path(project) / "data" / "raw"
    (raw / "a.csv").write_text("x\n1\n", encoding="utf-8")
    (raw / "b.csv").write_text("x\n2\n", encoding="utf-8")
    (Path(project) / "~$budget.xlsx").write_bytes(b"lock")
    # A name longer than the file system takes fails its own lookup too (on CPython 3.12).
    body = ("LOCK = '~$budget.xlsx'\n"
            f"LONG = 'data/{'x' * 300}.csv'\n"
            "QUERY = \"SELECT * FROM 'data/raw/b.csv'\"\n"
            "with open('data/raw/a.csv', encoding='utf-8') as fh:\n    fh.read()\n")
    code, result = labkit(project, "run", write_script(project, "87_scan.py", body),
                          "--title", "scan")
    assert code == 0, result
    vias = {i["path"]: i["via"] for i in result["run"]["inputs"]}
    assert vias == {"data/raw/a.csv": ["scan"], "data/raw/b.csv": ["scan"],
                    "~$budget.xlsx": ["scan"]}, result["run"]["inputs"]


# --- review 9 ----------------------------------------------------------------------------------

WINDOWS_DISARM_INTERRUPTED = """\
import signal, sys, threading
from labkit import _platform

monitoring = sys.monitoring
tool = next(i for i in range(2, 6) if monitoring.get_tool(i) is None)
disarm = _platform._WindowsGuard.disarm.__code__
fired = []


def at_the_entry(code, _offset):
    frame = sys._getframe(1)
    while frame is not None and frame.f_code is not disarm:
        frame = frame.f_back
    if frame is not None and not fired:
        fired.append(1)
        signal.raise_signal(signal.SIGINT)  # the script's handler raises it here


monitoring.use_tool_id(tool, "labkit-test")
monitoring.register_callback(tool, monitoring.events.PY_START, at_the_entry)
monitoring.set_local_events(tool, threading.Condition.__exit__.__code__,
                            monitoring.events.PY_START)
guard = _platform._WindowsGuard()
with guard:
    with guard.interruptible(deadline=30.0):
        signal.signal(signal.SIGINT, signal.default_int_handler)  # the script's
    caught = guard.settle()
print("returned", bool(fired), type(caught).__name__, flush=True)
"""


@NEEDS_MONITORING
def test_a_windows_disarm_interrupted_inside_the_timer_s_cancel_does_not_hang():
    """Review 9, guard-1: `Timer.cancel` is Python code. A raising handler the script left, run at
    the entry of the timer's Condition.__exit__, left the timer's lock held; revision 3.3 kept the
    timer until it was cancelled, so settle's retry cancelled it again and waited on that lock for
    ever, and the run never reached its record. Run in a child process, which a hang cannot keep."""
    try:
        done = subprocess.run([sys.executable, "-c", WINDOWS_DISARM_INTERRUPTED],
                              capture_output=True, env=kit_env(), timeout=30,
                              **_platform.text_kwargs())
    except subprocess.TimeoutExpired:
        pytest.fail("the region's exit hung in the timer's cancel")
    assert done.stdout.split() == ["returned", "True", "KeyboardInterrupt"], \
        (done.stdout, done.stderr)


def test_a_windows_deadline_is_not_also_counted_as_a_cancel(monkeypatch):
    """Review 9, guard-2 and honesty-3: the Windows deadline is raised from the SIGINT handler that
    also records cancels, so that handler's frame is in the traceback of every deadline it
    interrupts, and settle(failure) — the runner's call — counted a cancel nobody sent. The record
    stayed `timeout`, but the flag said otherwise and a later region was refused, unlike POSIX."""
    guard = _guard() if os.name == "nt" else _windows_guard(monkeypatch)
    before = _handlers()
    failure, ran = None, []
    with guard:
        try:
            with guard.interruptible(deadline=1.0):
                time.sleep(5)
        except _platform.Interrupted as exc:
            failure = exc
        guard.settle(failure)
        with guard.interruptible():
            ran.append(1)
    assert isinstance(failure, _platform.Interrupted), failure
    assert _outcome(guard) == {"timeout": True, "cancel": False} and ran == [1], _outcome(guard)
    assert _handlers() == before


@POSIX_GUARD
@pytest.mark.parametrize("interruption", ["cancel", "deadline"])
def test_a_timer_the_script_sets_after_catching_its_interruption_ends_with_the_run(
        project, monkeypatch, interruption):
    """Review 9, critic-1: the guard stopped its timer as it interrupted the region and dropped its
    mark there, so the region's end left ITIMER_REAL alone. A script that caught the interruption
    and then set a timer of its own (a `signal.alarm` watchdog) had it fire after the run into the
    host: an in-process host that kept SIGALRM's default action was killed, and the CLI too,
    sometimes before its JSON line. Main cancelled ITIMER_REAL after every script."""
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    stop = {"cancel": "signal.raise_signal(signal.SIGINT)\n    time.sleep(2)\n",
            "deadline": "time.sleep(3)\n"}[interruption]
    write_script(Path(project), "89_timer.py",
                 "import signal, time\ntry:\n    " + stop + "except BaseException:\n    pass\n"
                 "signal.setitimer(signal.ITIMER_REAL, 30)\n")
    saved = signal.signal(signal.SIGALRM, signal.SIG_IGN)
    try:
        result = runner.execute(core.Project(project), script="scripts/89_timer.py",
                                title="timer", timeout=1, actor="test")
        left = signal.getitimer(signal.ITIMER_REAL)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, saved)
    assert result["run"]["status"] == {"cancel": "cancelled", "deadline": "timeout"}[interruption]
    assert left == (0.0, 0.0), left


def test_a_cancel_kept_from_an_earlier_run_is_not_counted_for_a_later_one(project, monkeypatch):
    """Review 9, guard-3 and honesty-1: a frame of a guard's cancel handler counts only for that
    guard. A module a script imported stays in sys.modules across the runs of an in-process host,
    and a later run's error chained to a cancel kept from an earlier run made the later run
    `cancelled`, though no cancel reached it, once the check was gone."""
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    monkeypatch.setitem(sys.modules, "_kept_across_runs", types.SimpleNamespace(last=None))
    write_script(Path(project), "90_first.py",
                 "import signal, time\nimport _kept_across_runs as keep\ntry:\n"
                 "    signal.raise_signal(signal.SIGINT)\n    time.sleep(1)\n"
                 "except BaseException as exc:\n    keep.last = exc\n    raise\n")
    write_script(Path(project), "91_second.py",
                 "import _kept_across_runs as keep\n"
                 "raise RuntimeError('the previous attempt failed') from keep.last\n")
    first = runner.execute(core.Project(project), script="scripts/90_first.py", title="first",
                           actor="test")
    second = runner.execute(core.Project(project), script="scripts/91_second.py",
                            title="second", actor="test")
    assert first["run"]["status"] == "cancelled", first["run"]
    assert second["run"]["status"] == "error", second["run"]


@NEEDS_MONITORING
@pytest.mark.parametrize("platform_guard", [pytest.param("posix", marks=POSIX_GUARD), "windows"])
def test_a_cancel_cut_short_as_its_region_is_left_is_found_in_what_the_exit_caught(
        monkeypatch, platform_guard):
    """Review 9, critic-2: settle looks for a cut-short cancel in the region's failure and in what a
    handler raised while the region was left, which the region never raises. Only the first was
    tested. Here the region ends normally, a cancel arrives as it disarms, and the script's SIGTERM
    handler raises at the cancel handler's entry. The Windows guard runs on Windows itself too."""
    windows = platform_guard == "windows"
    guard = _windows_behaviour(monkeypatch) if windows else _guard()
    handler = (_platform._WindowsGuard._on_sigint if windows
               else _platform._RunGuard.on_cancel_signal)
    state = {"on": False}

    def the_cancel_arrives(call):
        def wrapped(*args):
            if state.pop("on", False):
                signal.raise_signal(signal.SIGINT)
            return call(*args)
        return wrapped

    if not windows:
        monkeypatch.setattr(signal, "setitimer", the_cancel_arrives(signal.setitimer))
    injector = _Injector({handler.__code__}, lambda: _script_s_own(signal.SIGTERM)
                         and signal.raise_signal(signal.SIGTERM), entries_only=True)
    before = _handlers()
    saved = signal.getsignal(signal.SIGTERM)
    timers = []
    try:
        with injector, guard:
            with guard.interruptible(deadline=30.0):
                signal.signal(signal.SIGTERM, lambda *_a: sys.exit(1))  # the script's
                if windows:
                    timers.append(guard._timer)
                    guard._timer.cancel = the_cancel_arrives(guard._timer.cancel)
                injector.arm(1)
                injector.on = state["on"] = True
            injector.on = False
            caught = guard.settle()
    finally:
        signal.signal(signal.SIGTERM, saved)
        for timer in timers:  # the one the interrupted disarm dropped uncancelled
            timer.cancel()
    assert injector.fired and isinstance(caught, SystemExit), (injector.fired, caught)
    assert _outcome(guard) == {"timeout": False, "cancel": True}, _outcome(guard)
    assert _handlers() == before


@pytest.mark.skipif(os.name == "nt", reason="a directory symlink needs privileges on Windows")
@pytest.mark.parametrize("where", ["snapshot", "code_root"])
def test_a_rerun_whose_recorded_snapshot_loops_is_refused(project, where):
    """Review 9, rest-8: the recorded snapshot was still resolved with Path.resolve, which raises
    for a link loop on CPython 3.12: a traceback and no JSON line there, a refusal on 3.13 and
    3.14. Review 10, honesty-8 (N7): the run's code directory, which it is checked against, is
    resolved the same way."""
    code, one = labkit(project, "run", write_script(project, "92_ok.py", "x = 1\n"), "--title", "x")
    assert code == 0, one
    run_id = one["run"]["id"]
    code_dir = Path(project) / "runs" / run_id / "code"
    if where == "code_root":
        shutil.rmtree(code_dir)
        code_dir.symlink_to("code")
    else:
        (code_dir / "loop").symlink_to("loop")
        run_json = Path(project) / "runs" / run_id / "run.json"
        record = json.loads(run_json.read_text(encoding="utf-8"))
        record["script"]["snapshot"] = f"runs/{run_id}/code/loop/x.py"
        run_json.write_text(json.dumps(record), encoding="utf-8")
    code, result = labkit(project, "run", "--rerun-of", run_id, "--title", "loop")
    assert code == 1 and result["error"]["code"] == "rerun_snapshot", result


@pytest.mark.parametrize("flushed", [False, True])
def test_a_leftover_thread_s_unfinished_line_does_not_join_the_result(project, flushed):
    """Review 9, rest-2: the result followed on the same line whatever such a thread had written
    without a newline — a progress message written with end='' — and the extension found no result
    for a run recorded `ok`. Revision 3.2 put the text after the result; a thread that flushed it
    itself broke every version, main included. After a run the result starts a line of its own."""
    body = ("import os, sys, threading, time\nout = sys.stdout\nlog = os.fstat(1)\n"
            "def later():\n"
            "    while os.path.samestat(os.fstat(1), log):\n"
            "        time.sleep(0.001)\n"
            "    out.write('saving...')\n" + ("    out.flush()\n" if flushed else "") +
            "    threading.Event().wait()\n"
            "threading.Thread(target=later, daemon=True).start()\n")
    cmd = [sys.executable, "-m", "labkit", "--project", str(project), "run",
           write_script(project, "93_partial.py", body), "--title", "partial"]
    proc = subprocess.run(cmd, capture_output=True, env=kit_env(), timeout=120,
                          **_platform.text_kwargs())
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    assert lines and json.loads(lines[-1]).get("ok") is True, proc.stdout
    assert "saving..." in proc.stdout, proc.stdout


# --- review 10 ---------------------------------------------------------------------------------

WINDOWS_LOCK_INTERRUPTED = """\
import signal, sys, threading, time
from labkit import _platform

if hasattr(signal, "pthread_kill"):  # POSIX: the timer's SIGINT goes to the main thread, as there
    def deliver():
        if not callable(signal.getsignal(signal.SIGINT)):
            return False
        signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
        return True
    _platform.raise_sigint_in_main = deliver
where = sys.argv[1]
guard = _platform._WindowsGuard()
disarm = _platform._WindowsGuard.disarm.__code__
fired = []


def the_script_s_sigint():
    if not fired:
        fired.append(1)
        signal.raise_signal(signal.SIGINT)  # the script's handler raises it here


def the_guard_s_lock(called, first, name):
    # A bound method, or (3.14's LOAD_SPECIAL) the class's method with the lock as first argument.
    lock = guard._lock
    return getattr(called, "__name__", "") == name and \\
        (getattr(called, "__self__", None) is lock or first is lock)


def after_the_acquiring(code, _offset, called, first):
    # CPython 3.14 calls a `with`'s __enter__ through a CALL and checks for signals after it.
    if code is disarm and the_guard_s_lock(called, first, "__enter__"):
        the_script_s_sigint()


def before_the_release(frame, event, called):
    # A profile function runs before the call that releases the lock.
    if event == "c_call" and frame.f_code is disarm and the_guard_s_lock(called, None, "__exit__"):
        the_script_s_sigint()


if where == "enter":
    monitoring = sys.monitoring
    tool = next(i for i in range(2, 6) if monitoring.get_tool(i) is None)
    monitoring.use_tool_id(tool, "labkit-test")
    monitoring.register_callback(tool, monitoring.events.C_RETURN, after_the_acquiring)
    monitoring.set_local_events(tool, disarm, monitoring.events.CALL)
with guard:
    with guard.interruptible(deadline=30.0):
        signal.signal(signal.SIGINT, signal.default_int_handler)  # the script's
        if where == "exit":
            sys.setprofile(before_the_release)
    sys.setprofile(None)
    caught = guard.settle()
    # A later region's timer fires on a lock of its own, whatever the first region's exit left held.
    t0 = time.monotonic()
    try:
        with guard.interruptible(deadline=1.0):
            time.sleep(3)
        later = "ran-to-its-end"
    except _platform.Interrupted:
        later = "timed-out" if time.monotonic() - t0 < 2.5 else "timed-out-late"
    guard.settle()
print("returned", bool(fired), type(caught).__name__, later, flush=True)
"""


@NEEDS_MONITORING
@pytest.mark.parametrize("where", [
    pytest.param("enter", marks=pytest.mark.skipif(
        sys.version_info < (3, 14), reason="before 3.14 a `with` calls __enter__ without a CALL, "
        "and no signal is handled between its acquiring and the block")),
    "exit"])
def test_a_windows_disarm_interrupted_holding_the_guard_s_lock_does_not_hang(where):
    """Review 10, guard-1: the disarm takes the guard's own lock with a `with`, and a raising
    handler the script left, run between the acquiring and the block — CPython 3.14 checks for
    signals at the end of __enter__'s call; a profile function runs before the release's call on
    every version — left it held; settle's retry then waited on it for ever (revisions 3 to 3.4;
    17e6e76 had no retry). The lock is re-entrant now, and a later region's timer fires on a lock
    of its own. Run in a child process, which a hang cannot keep."""
    try:
        done = subprocess.run([sys.executable, "-c", WINDOWS_LOCK_INTERRUPTED, where],
                              capture_output=True, env=kit_env(), timeout=30,
                              **_platform.text_kwargs())
    except subprocess.TimeoutExpired:
        pytest.fail("the region's exit hung on the guard's lock")
    assert done.stdout.split() == ["returned", "True", "KeyboardInterrupt", "timed-out"], \
        (done.stdout, done.stderr)


def _raises_the_script_s(sig):
    return lambda: _script_s_own(sig) and signal.raise_signal(sig)


@NEEDS_MONITORING
def test_a_timer_its_disarm_dropped_uncancelled_does_not_time_out_a_later_region(monkeypatch):
    """Review 10, guard-2 and honesty-3: a raising handler the script left, run at the entry of the
    timer's cancel, cuts the disarm short after it dropped the timer (revision 3.4's order, against
    guard-1). The timer fired at the first region's deadline into whatever region of the guard was
    armed then, and timed a later one out. It now finds that it is no longer the region's timer."""
    guard = _windows_behaviour(monkeypatch)
    injector = _Injector({threading.Timer.cancel.__code__}, _raises_the_script_s(signal.SIGINT),
                         entries_only=True)
    failure = None
    with injector, guard:
        with guard.interruptible(deadline=1.0):
            signal.signal(signal.SIGINT, signal.default_int_handler)  # the script's
            injector.arm(1)
            injector.on = True
        injector.on = False
        caught = guard.settle()
        try:
            with guard.interruptible(deadline=30.0):
                time.sleep(1.5)
        except _platform.Interrupted as exc:
            failure = exc
        guard.settle(failure)
    assert injector.fired and isinstance(caught, KeyboardInterrupt), (injector.fired, caught)
    assert failure is None and _outcome(guard) == {"timeout": False, "cancel": False}, \
        (failure, _outcome(guard))


@NEEDS_MONITORING
def test_a_later_region_s_cut_short_cancel_counts_after_an_earlier_region_timed_out(
        monkeypatch):
    """Review 10, guard-6: the Windows guard left its SIGINT handler's frame uncounted once the
    run's deadline flag was set, and that flag is never reset, so after one region timed out a
    later region's cancel cut short at the handler's entry was not counted on Windows, where POSIX
    counts it. The mark it reads now is the region's own."""
    guard = _windows_behaviour(monkeypatch)
    injector = _Injector({_platform._WindowsGuard._on_sigint.__code__},
                         _raises_the_script_s(signal.SIGTERM), entries_only=True)
    saved = signal.getsignal(signal.SIGTERM)
    failures = []
    try:
        with injector, guard:
            try:
                with guard.interruptible(deadline=1.0):
                    time.sleep(5)
            except _platform.Interrupted as exc:
                failures.append(exc)
            guard.settle(failures[-1] if failures else None)
            try:
                with guard.interruptible():
                    signal.signal(signal.SIGTERM, lambda *_a: sys.exit(1))  # the script's
                    injector.arm(1)
                    injector.on = True
                    signal.raise_signal(signal.SIGINT)  # a cancel
                    time.sleep(1)
            except SystemExit as exc:
                failures.append(exc)
            injector.on = False
            guard.settle(failures[-1])
    finally:
        signal.signal(signal.SIGTERM, saved)
    assert [type(f).__name__ for f in failures] == ["Interrupted", "SystemExit"], failures
    assert _outcome(guard) == {"timeout": True, "cancel": True}, _outcome(guard)


def test_a_later_region_without_a_deadline_takes_a_cancel_for_a_cancel(monkeypatch):
    """Review 10, found beside guard-6: the mark that makes the next SIGINT the deadline's was
    reset only when a later region armed a deadline. After a region whose timer's SIGINT the script
    took, a region without a deadline took a cancel for that deadline and raised it as one."""
    guard = _windows_behaviour(monkeypatch)
    got = []
    with guard:
        with guard.interruptible(deadline=1.0):
            signal.signal(signal.SIGINT, lambda *_a: got.append("the script's"))  # takes it
            time.sleep(1.5)
        guard.settle()
        try:
            with guard.interruptible():
                signal.raise_signal(signal.SIGINT)
                time.sleep(1)
        except _platform.Interrupted as exc:
            got.append(str(exc))
        guard.settle()
    assert got == ["the script's", "run cancelled"], got
    assert _outcome(guard) == {"timeout": True, "cancel": True}, _outcome(guard)


@NEEDS_MONITORING
@POSIX_GUARD
def test_a_cancel_cut_short_by_a_second_handler_while_the_region_is_left_is_counted(
        monkeypatch):
    """Review 10, honesty-10 (review 9's guard-4 sub-point): settle kept only the first thing a
    handler raised while the region was left. When the script's raising handler had already run
    once there, a cancel it cut short next was lost. Everything caught there is looked at now, and
    settle still returns the first of it (review 11, guard-4: the two exits differ)."""
    guard = _guard()
    codes = iter((1, 2))
    injector = _Injector({_platform._RunGuard.on_cancel_signal.__code__},
                         _raises_the_script_s(signal.SIGTERM), entries_only=True)
    real, calls = signal.setitimer, {"on": False, "n": 0}

    def stopping(which, seconds, *rest):
        if seconds == 0 and calls["on"]:
            calls["n"] += 1
            if calls["n"] == 1:
                signal.raise_signal(signal.SIGTERM)  # the script's handler raises here
            elif calls["n"] == 2:
                injector.arm(1)
                injector.on = True
                signal.raise_signal(signal.SIGINT)  # a cancel, cut short at its handler's entry
        return real(which, seconds, *rest)

    monkeypatch.setattr(signal, "setitimer", stopping)
    before = _handlers()
    saved = signal.getsignal(signal.SIGTERM)
    try:
        with injector, guard:
            with guard.interruptible(deadline=30.0):
                signal.signal(signal.SIGTERM, lambda *_a: sys.exit(next(codes)))  # the script's
                calls["on"] = True
            calls["on"] = injector.on = False
            caught = guard.settle()
    finally:
        signal.signal(signal.SIGTERM, saved)
    assert calls["n"] >= 3 and injector.fired and isinstance(caught, SystemExit), \
        (calls, injector.fired, caught)
    assert caught.code == 1, caught.code
    assert _outcome(guard) == {"timeout": False, "cancel": True}, _outcome(guard)
    assert _handlers() == before and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


@NEEDS_MONITORING
def test_a_windows_break_cut_short_at_its_handler_s_entry_is_counted(monkeypatch):
    """Review 10, honesty-8 (N1): the Windows guard counts a cancel cut short at the entry of its
    SIGINT handler and, through the base class, of its SIGBREAK handler (a console's Ctrl+Break).
    Only the first was tested. Where there is no SIGBREAK its handler is given another signal."""
    if not hasattr(signal, "SIGBREAK"):
        monkeypatch.setattr(signal, "SIGBREAK", signal.SIGUSR1, raising=False)
    guard = _windows_behaviour(monkeypatch)
    injector = _Injector({_platform._RunGuard.on_cancel_signal.__code__},
                         _raises_the_script_s(signal.SIGTERM), entries_only=True)
    saved = signal.getsignal(signal.SIGTERM)
    failure = None
    try:
        with injector, guard:
            try:
                with guard.interruptible():
                    signal.signal(signal.SIGTERM, lambda *_a: sys.exit(1))  # the script's
                    injector.arm(1)
                    injector.on = True
                    signal.raise_signal(signal.SIGBREAK)
                    time.sleep(1)
            except SystemExit as exc:
                failure = exc
            injector.on = False
            guard.settle(failure)
    finally:
        signal.signal(signal.SIGTERM, saved)
    assert injector.fired and isinstance(failure, SystemExit), (injector.fired, failure)
    assert _outcome(guard) == {"timeout": False, "cancel": True}, _outcome(guard)


@NEEDS_MONITORING
@POSIX_GUARD
@pytest.mark.parametrize("position", ["last", "first"])
def test_the_cut_short_walk_looks_at_chain_limit_exceptions_newest_first(project, monkeypatch,
                                                                         position):
    """Review 10, guard-5 and honesty-8 (N3): the walk that finds a cut-short cancel looks at up to
    CHAIN_LIMIT exceptions, newest first — the bound that keeps a pathological structure cheap. So a
    group of a hundred finds it among its last members and loses it among its first, as
    docs/CONTRACTS.md G states."""
    from labkit import report, runner  # noqa: PLC0415

    monkeypatch.setattr(report, "build", lambda project_, **kw: {})
    keep = {"first": "errors.insert(0, exc)", "last": "errors.append(exc)"}[position]
    write_script(Path(project), "96_steps.py",
                 "import signal, time\nclass Stop(Exception):\n    pass\n"
                 "def stop(*_a):\n    raise Stop('shut down')\n"
                 "signal.signal(signal.SIGTERM, stop)\n"
                 "errors = [ValueError(f'step {i}') for i in range(99)]\n"
                 "try:\n    signal.raise_signal(signal.SIGINT)\n    time.sleep(1)\n"
                 f"except Stop as exc:\n    {keep}\n"
                 "raise ExceptionGroup('steps failed', errors)\n")
    with _Injector({_platform._RunGuard.on_cancel_signal.__code__},
                   _raises_the_script_s(signal.SIGTERM), entries_only=True) as injector:
        injector.arm(1)
        injector.on = True
        try:
            result = runner.execute(core.Project(project), script="scripts/96_steps.py",
                                    title="t", actor="test", timeout=30)
        finally:
            injector.on = False
    assert injector.fired, injector.count
    assert result["run"]["status"] == {"last": "cancelled", "first": "error"}[position], \
        result["run"]


@POSIX_GUARD
def test_a_cancel_s_interruption_stops_the_deadline_s_timer_at_once():
    """Review 10, honesty-8 (N4): the guard stops ITIMER_REAL as it interrupts the region, not only
    when the region is left. Without that, a script that caught the cancel and installed its own
    SIGALRM handler got the old deadline's alarm; nothing tested it."""
    got = []
    with _guard() as guard:
        with guard.interruptible(deadline=1.0):
            try:
                signal.raise_signal(signal.SIGINT)
                time.sleep(1)
            except _platform.Interrupted:
                pass
            signal.signal(signal.SIGALRM, lambda *_a: got.append("alarm"))  # the script's
            time.sleep(1.5)
        guard.settle()
    assert got == [] and _outcome(guard) == {"timeout": False, "cancel": True}, \
        (got, _outcome(guard))


def test_only_a_command_that_ran_a_script_starts_its_result_with_an_empty_line(project):
    """Review 10, rest-8 and honesty-8 (N5): the empty line that keeps a leftover thread's
    unfinished line out of the result comes after every run that got past its setup, whose output
    was redirected (review 11, rest-8 and honesty-7: whether or not its script then ran) — and only
    then: a command that ran no script prints its one JSON line alone, as main did, whether or not
    it loaded the runner (`report` does, for the kit's digest; `demo` runs its scripts in child
    processes)."""
    def stdout_of(*args):
        return subprocess.run([sys.executable, "-m", "labkit", "--project", str(project), *args],
                              capture_output=True, env=kit_env(), timeout=120,
                              **_platform.text_kwargs()).stdout

    for command in ("status", "report"):
        alone = stdout_of(command)
        assert alone.startswith("{") and alone.count("\n") == 1, (command, alone)
    ran = stdout_of("run", write_script(project, "95_quiet.py", "x = 1\n"), "--title", "quiet")
    assert ran.startswith("\n{") and ran.count("\n") == 2, ran
    # Review 12, rest-9: past its setup, a run whose script never started (a timeout no clock can
    # hold) gives the empty line too; `demo`, whose runs are child processes, gives none.
    never = stdout_of("run", write_script(project, "95_never.py", "x = 1\n"), "--timeout", "inf")
    assert never.startswith("\n{") and never.count("\n") == 2, never
    assert json.loads(never)["run"]["status"] == "error", never
    fresh = Path(project).parent / "demo-project"
    subprocess.run([sys.executable, "-m", "labkit", "init", str(fresh)], capture_output=True,
                   env=kit_env(), timeout=120, check=True)
    demo = subprocess.run([sys.executable, "-m", "labkit", "--project", str(fresh), "demo"],
                          capture_output=True, env=kit_env(), timeout=600,
                          **_platform.text_kwargs()).stdout
    assert demo.startswith("{") and demo.count("\n") == 1 and json.loads(demo)["ok"], demo[-500:]


def test_the_length_hint_reads_the_snapshot_s_path(project, monkeypatch):
    """Review 10, honesty-8 (N21): where the error names no cause — Windows' open() answers a name
    too long with an errno only — the hint reads the length of the path that was refused, the
    snapshot's, not the run directory's."""
    from labkit import runner  # noqa: PLC0415

    run_id = _recorded_as(project, "scripts/" + "a" * 300 + ".py")
    real = Path.write_bytes

    def as_windows_open_answers(self, data):
        if len(self.name) > 255:
            raise OSError(errno.EINVAL, "Invalid argument")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", as_windows_open_answers)
    with pytest.raises(core.LabError) as refused:
        runner.execute(core.Project(project), script=None, rerun_of=run_id, title="long",
                       actor="test")
    assert (refused.value.code, refused.value.hint) == \
        ("snapshot_failed", "shorten the script's path"), refused.value.to_json()


def test_a_tilde_name_whose_home_does_not_exist_is_taken_as_written(tmp_path, monkeypatch):
    """The second Windows run (measurement run 35993026796; review 10, rest-1 and honesty-1):
    Windows guesses a home for any `~name` beside the current user's profile, so the Office lock
    file `~$budget.xlsx` in a project was read as C:\\Users\\$budget.xlsx there — not the project's
    file, as on POSIX, where no home is found. Here the guess is made on any platform; a home that
    exists is still expanded."""
    project = core.Project(tmp_path)
    real = os.path.expanduser

    def guesses_like_windows(path):
        text = os.fspath(path)
        if text.startswith("~") and not text.startswith(("~/", "~\\")) and text != "~":
            return str(tmp_path.parent / "Users" / text[1:])  # a profile directory nobody has
        return real(path)

    monkeypatch.setattr(os.path, "expanduser", guesses_like_windows)
    assert project.path("~$budget.xlsx") == tmp_path / "~$budget.xlsx"
    assert project.path("~nosuchuser/x.csv") == tmp_path / "~nosuchuser" / "x.csv"
    assert project.path("~/x.csv") == Path(real("~")) / "x.csv"


def test_a_tilde_name_pathlib_cannot_expand_is_taken_as_written(tmp_path, monkeypatch):
    """Where no home is found the expansion leaves the name as it was, and a directory of that name
    in the working directory makes it look like a home that exists: pathlib then refuses to
    expand it (RuntimeError), and the path is still taken as written."""
    monkeypatch.setattr(os.path, "expanduser", os.fspath)  # finds no home
    monkeypatch.chdir(tmp_path)
    (tmp_path / "~nosuchuser").mkdir()
    project = core.Project(tmp_path / "project")
    assert project.path("~nosuchuser/x.csv") == project.root / "~nosuchuser" / "x.csv"


def test_a_scanned_name_a_record_cannot_hold_is_left_out(project):
    """Review 10, rest-2: a record is UTF-8, and POSIX hands over a name that is not UTF-8 with
    surrogates in it, so the record could not be written: no JSON line, and the run left
    `running`. Revision 3.4's scan reached such a name from any quoted glob — a docstring's — where
    main, whose scan lost every SQL-quoted name, recorded the run. The scan leaves it out, and keeps
    the other files of the same glob."""
    raw = Path(project) / "data" / "raw"
    (raw / "ok.csv").write_text("x\n1\n", encoding="utf-8")
    try:
        with open(os.path.join(os.fsencode(raw), b"caf\xe9.csv"), "wb") as fh:
            fh.write(b"x\n2\n")
    except (OSError, ValueError) as exc:
        pytest.skip(f"this file system refuses a name that is not UTF-8: {exc!r}")
    body = "\"\"\"Reads 'data/raw/*.csv' with DuckDB later.\"\"\"\nprint('hi')\n"
    code, result = labkit(project, "run", write_script(project, "94_glob.py", body),
                          "--title", "glob")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert [i["path"] for i in result["run"]["inputs"]] == ["data/raw/ok.csv"], result["run"]


def test_the_scan_leaves_a_name_a_record_cannot_hold_out_of_the_same_glob(project, monkeypatch):
    """Review 10, rest-2, where the file system at hand refuses such a name (APFS, NTFS): the glob
    answers with one, as Linux's does for a file whose name is not UTF-8."""
    from labkit import runner  # noqa: PLC0415

    raw = Path(project) / "data" / "raw"
    (raw / "ok.csv").write_text("x\n1\n", encoding="utf-8")
    undecodable = raw / "caf\udce9.csv"  # how POSIX hands over the byte 0xE9
    in_such_a_directory = raw / "caf\udce9" / "x.csv"  # review 11, honesty-5 (X9): any part
    real_is_file = Path.is_file
    monkeypatch.setattr(glob, "glob", lambda pattern, **kw: [
        str(raw / "ok.csv"), str(undecodable), str(in_such_a_directory)])
    monkeypatch.setattr(Path, "is_file", lambda self: self in (undecodable, in_such_a_directory)
                        or real_is_file(self))
    found = runner.static_input_files(core.Project(project), {"'data/raw/*.csv'"}, set())
    assert found == [raw / "ok.csv"], found


LEFTOVER_WRITER = """\
import os, sys, threading, time
out = sys.stdout
log = os.fstat(1)


def after_the_run():
    while os.path.samestat(os.fstat(1), log):
        time.sleep(0.001)
    line = "leftover " * 20 + "\\n"
    while True:
        {write}


threading.Thread(target=after_the_run, daemon=True).start()
os.makedirs(os.path.join("data", "derived", "parts"), exist_ok=True)
for i in range({count}):
    with open(os.path.join("data", "derived", "parts", f"part-{{i:04d}}.csv"), "w",
              encoding="utf-8") as fh:
        fh.write("x\\n")
"""


LEFTOVER_WRITES = {
    "through_the_stream_it_took": "out.write(line)\n        out.flush()",
    "through_print": "print(line, end='', flush=True)",
}


@pytest.mark.parametrize("writes", sorted(LEFTOVER_WRITES))
def test_a_leftover_thread_s_writes_do_not_split_a_result_longer_than_a_pipe_holds(project,
                                                                                   writes):
    """Review 10, critic-1: a thread the script left running wrote through its run's stdout stream
    — a buffer of its own over fd 1 — after the run, while the CLI printed a result longer than a
    pipe holds, which goes out in pieces: the thread's line landed inside the result, and the
    extension found none for a run recorded `ok`. Main's scripts wrote through the CLI's own
    stream, whose buffer kept the two apart. The result is written through the run stdout's buffer
    the kit kept now (revision 3.7; 3.6 wrote it through the run's text stream, and 3.5 used a lock
    of its own, review 11 rest-1), with sys.stdout the run's text stream until then, so a thread's
    print() is kept out too. The reader takes 4 KiB at a time."""
    body = LEFTOVER_WRITER.format(count=1200, write=LEFTOVER_WRITES[writes])
    cmd = [sys.executable, "-m", "labkit", "--project", str(project), "run",
           write_script(project, f"97_parts_{writes}.py", body), "--title", "parts"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=kit_env())
    chunks = []

    def read():
        while chunk := os.read(proc.stdout.fileno(), 4096):
            chunks.append(chunk)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=240)
    finally:
        if proc.poll() is None:
            proc.kill()
        reader.join(60)
        proc.stdout.close()
    out = b"".join(chunks).decode("utf-8", errors="replace")
    result = None
    for line in reversed(out.splitlines()):  # as the extension reads it
        with contextlib.suppress(ValueError):
            result = json.loads(line)
            break
    assert isinstance(result, dict) and result.get("ok") is True, out[-3000:]
    assert len(result["run"]["outputs"]) == 1200, len(result["run"]["outputs"])


# --- review 11 ---------------------------------------------------------------------------------

def test_the_cli_gives_its_own_stdout_back_when_it_returns(project, monkeypatch, tmp_path):
    """Review 11 (U6): the run's stdout stream stands in for the CLI's own until the result is out;
    main() puts the CLI's own back when it returns — before the interpreter's shutdown flushes
    sys.stdout, and for a caller that runs the CLI in its own process."""
    from labkit import __main__ as cli  # noqa: PLC0415

    fd1 = tmp_path / "fd1.txt"
    saved_fd1 = os.dup(1)
    with open(fd1, "wb") as sink:
        os.dup2(sink.fileno(), 1)
    try:
        own = io.TextIOWrapper(io.BufferedWriter(io.FileIO(1, "w", closefd=False)),
                               encoding="utf-8")
        monkeypatch.setattr(sys, "stdout", own)
        monkeypatch.setattr(sys, "__stdout__", own)
        code = cli.main(["--project", str(project), "run",
                         write_script(project, "96_back.py", "x = 1\n"), "--title", "back"])
        assert code == 0 and sys.stdout is own and sys.__stdout__ is own
        own.flush()
        results = [line for line in fd1.read_text(encoding="utf-8").splitlines()
                   if line.startswith("{")]
        assert results and json.loads(results[-1])["ok"] is True, results
    finally:
        os.dup2(saved_fd1, 1)
        os.close(saved_fd1)


NUMBERED_LINES = "import itertools\nfor i in itertools.count():\n    print(f'L{i:08d}', flush=True)\n"


def _numbers_logged(project, result) -> list[int]:
    text = (Path(project) / "runs" / result["run"]["id"] / "stdout.txt").read_text(encoding="utf-8")
    lines = text.split("\n")[:-1]  # what follows the last newline is a line cut short, if any
    return [int(line[1:]) for line in lines]


def test_a_stopped_run_s_log_holds_each_line_once(project, tmp_path):
    """Review 11, rest-1: revision 3.5 put Python code under the run's stdout buffer, and a cancel
    or a deadline handled right after a write reached the file raised before the buffer counted it
    written: the closing flush wrote it again, and the log repeated the line (35 of 40 runs). A
    script printing numbered lines with flush=True is stopped at varied instants, both ways; its
    log must count up by one from 0."""
    runs = []
    for wait in (0.3, 0.4, 0.5, 0.6):
        code, result, _elapsed, err = _ask_a_run_to_stop(project, tmp_path, f"98_count_{wait}",
                                                         NUMBERED_LINES, wait=wait)
        assert result and result["run"]["status"] == "cancelled", (code, result, err)
        runs.append(result)
    for n in range(2):
        code, result = labkit(project, "run", write_script(project, f"99_count_{n}.py",
                                                          NUMBERED_LINES),
                              "--title", "count", "--timeout", "1")
        assert result["run"]["status"] == "timeout", result
        runs.append(result)
    for result in runs:
        numbers = _numbers_logged(project, result)
        assert numbers and numbers == list(range(len(numbers))), \
            [n for i, n in enumerate(numbers) if n != i][:5]


@NEEDS_MONITORING
@POSIX_GUARD
def test_settle_looks_at_the_region_s_failure_before_what_the_exit_caught():
    """Review 11, guard-1: revision 3.5 walked what handlers raised while the region was left
    before the region's own failure, so CHAIN_LIMIT or more of them hid a cancel cut short in the
    region, which 3.4 counted. The failure comes first now."""
    guard = _guard()
    injector = _Injector({_platform._RunGuard.on_cancel_signal.__code__},
                         _raises_the_script_s(signal.SIGTERM), entries_only=True)
    saved = signal.getsignal(signal.SIGTERM)
    failure = None
    try:
        with injector, guard:
            try:
                with guard.interruptible(deadline=30.0):
                    signal.signal(signal.SIGTERM, lambda *_a: sys.exit(1))  # the script's
                    injector.arm(1)
                    injector.on = True
                    signal.raise_signal(signal.SIGINT)  # a cancel, cut short at its handler's entry
                    time.sleep(1)
            except SystemExit as exc:
                failure = exc
            injector.on = False
            # As if a handler the script left had raised that often while the region was left.
            guard._caught = tuple(RuntimeError(f"burst {i}")
                                  for i in range(_platform.CHAIN_LIMIT + 6))
            guard.settle(failure)
    finally:
        signal.signal(signal.SIGTERM, saved)
    assert injector.fired and isinstance(failure, SystemExit), (injector.fired, failure)
    assert _outcome(guard) == {"timeout": False, "cancel": True}, _outcome(guard)


def test_a_runtime_read_of_a_tilde_name_is_recorded_as_its_reader_resolves_it(project,
                                                                              monkeypatch, tmp_path):
    """Review 11, critic-2, and review 12, rest-3/honesty-3: 3.5 expanded a read name with pathlib,
    which raised for a `~name` with no such user on POSIX, and the read was left out of the record;
    3.6 read every one by Project.path's rule, which neither reader follows. Polars expands a
    leading `~` with os.path.expanduser: the file it names is recorded, whether that is the
    project's file as written or a home Windows guessed. DuckDB puts its home in place of the `~`:
    `~/x.csv` is recorded as the home's file, and a `~name` (DuckDB's home with `name` appended) is
    not recorded at all."""
    from labkit import capture  # noqa: PLC0415

    monkeypatch.chdir(project)  # a script's reads are relative to the project, where it runs
    (Path(project) / "~$budget.csv").write_text("x\n1\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    (home / "x.csv").write_text("x\n2\n", encoding="utf-8")
    guessed = tmp_path / "guessed" / "$budget.csv"  # where Windows' Python would look
    real_expanduser = os.path.expanduser

    def recorded(name, via, *, windows_guess=False):
        run_dir = Path(project) / "runs" / f"r-{len(list((Path(project) / 'runs').glob('r-*')))}"
        run_dir.mkdir(parents=True)
        state = capture.RunState(core.Project(project), run_dir.name, run_dir,
                                 Path(project) / "scripts" / "a.py")
        with monkeypatch.context() as m:
            m.setenv("HOME", str(home))
            m.setenv("USERPROFILE", str(home))
            if windows_guess:
                m.setattr(os.path, "expanduser", lambda path: str(guessed) if str(path).startswith(
                    "~$") else real_expanduser(path))
            state.record_read(name, via)
        return sorted(state.inputs)

    assert recorded("~$budget.csv", "polars.read_csv") == ["~$budget.csv"]
    assert recorded("~$budget.csv", "polars.read_csv", windows_guess=True) == []
    guessed.parent.mkdir()
    guessed.write_text("x\n3\n", encoding="utf-8")
    [only] = recorded("~$budget.csv", "polars.read_csv", windows_guess=True)
    assert os.path.samefile(only, guessed), only
    assert recorded("~$budget.csv", "duckdb.read_csv") == []
    assert recorded("~$budget.csv", "duckdb.sql") == []
    [only] = recorded("~/x.csv", "duckdb.read_csv")
    assert os.path.samefile(only, home / "x.csv"), only
    assert recorded("~$budget.csv", "lab.read") == ["~$budget.csv"]  # the kit's own rule


@pytest.mark.skipif(os.name == "nt", reason="names POSIX can hold and Windows reads otherwise")
def test_the_readers_record_what_they_read_of_a_tilde_name(project, tmp_path):
    """Review 12, rest-3: through the libraries themselves. The script reads the project's
    `~tmp.csv` through Polars, which opens it as written, and through DuckDB, which opens its home
    directory's path with `tmp.csv` appended instead: the record lists the project's file once,
    as Polars read it."""
    (Path(project) / "~tmp.csv").write_text("a\n1\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    Path(str(home) + "tmp.csv").write_text("a\n7\n8\n", encoding="utf-8")  # what DuckDB opens
    body = ("import duckdb\nimport polars as pl\n"
            "name = '~' + 'tmp.csv'\n"
            "print(pl.read_csv(name).height, len(duckdb.read_csv(name).fetchall()))\n")
    done = subprocess.run([sys.executable, "-m", "labkit", "--project", str(project), "run",
                           write_script(project, "92_tilde_readers.py", body)],
                          capture_output=True, env=kit_env(HOME=str(home)), timeout=180,
                          **_platform.text_kwargs())
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    result = json.loads(lines[-1])
    assert result["run"]["status"] == "ok" and "1 2" in result["stdout_tail"], result
    inputs = {entry["path"]: sorted(entry["via"]) for entry in result["run"]["inputs"]}
    assert inputs == {"~tmp.csv": ["polars.read_csv"]}, inputs


def test_lab_import_takes_a_tilde_name_that_names_no_home_as_written(project):
    """Review 11, critic-2: `lab import ~nosuchuser/x.csv` ended in a traceback with no JSON line
    on POSIX, where pathlib refuses the expansion. It follows Project.path's rule now."""
    source = Path(project) / "~no-such-labkit-user"
    source.mkdir()
    (source / "x.csv").write_text("a\n1\n", encoding="utf-8")
    done = subprocess.run([sys.executable, "-m", "labkit", "--project", str(project), "import",
                           "~no-such-labkit-user/x.csv", "--name", "x"], cwd=project,
                          capture_output=True, env=kit_env(), timeout=120,
                          **_platform.text_kwargs())
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    assert lines and json.loads(lines[-1]).get("ok") is True, (done.stdout, done.stderr)


def test_a_scanned_name_in_a_directory_a_record_cannot_hold_is_left_out(project):
    """Review 11, honesty-5 (X9): the filter reads the whole path, a directory's name too."""
    raw = Path(project) / "data" / "raw"
    (raw / "ok").mkdir()
    (raw / "ok" / "x.csv").write_text("x\n1\n", encoding="utf-8")
    try:
        os.mkdir(os.path.join(os.fsencode(raw), b"caf\xe9"))
        with open(os.path.join(os.fsencode(raw), b"caf\xe9", b"x.csv"), "wb") as fh:
            fh.write(b"x\n2\n")
    except (OSError, ValueError) as exc:
        pytest.skip(f"this file system refuses a name that is not UTF-8: {exc!r}")
    body = "\"\"\"Reads 'data/raw/*/x.csv' with DuckDB later.\"\"\"\nprint('hi')\n"
    code, result = labkit(project, "run", write_script(project, "94_dir_glob.py", body),
                          "--title", "glob")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert [i["path"] for i in result["run"]["inputs"]] == ["data/raw/ok/x.csv"], result["run"]


@pytest.mark.skipif(os.name == "nt", reason="bin/lab is a POSIX shell script")
def test_lab_help_prints_the_whole_header():
    """Review 11, honesty-1: usage() printed lines 2-15 of bin/lab, and revision 3.5's header ran
    to line 16: `lab --help` stopped mid-sentence and dropped 'Nothing here is a sandbox.' It
    prints the header's comment block to its end now."""
    lab = Path(__file__).resolve().parents[1] / "bin" / "lab"
    done = subprocess.run(["sh", str(lab), "--help"], capture_output=True, timeout=30,
                          **_platform.text_kwargs())
    assert done.returncode == 0, done.stderr
    assert done.stdout.rstrip().endswith("Nothing here is a sandbox."), done.stdout
    # Review 12, honesty-8 (Y12, Y13): from the header's first line, without its comment marks.
    header = lab.read_text(encoding="utf-8").splitlines()[1]
    assert done.stdout.splitlines()[0] == header.removeprefix("# "), done.stdout.splitlines()[:2]


# --- review 12 ---------------------------------------------------------------------------------

# What a script can do to its stdout during a run (review 12), each the way a script writes it,
# in the script's globals, which outlive the run as a module's do.
SCRIPT_STDOUT = {
    "left_alone": "pass",
    "closed": "sys.stdout.close()",
    # The wrapper is dropped with the script's own sys.stdout, and closes the buffer as it goes.
    "detached": "sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8')",
    "detached_and_kept": "sys.stdout = out = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8')",
    "buffer_detached": "sys.stdout.buffer.detach()",
    "reconfigured": "sys.stdout.reconfigure(encoding='ascii', errors='strict')",
    "methods_replaced": ("sys.stdout.write = lambda text: len(text)\n"
                         "sys.stdout.buffer.write = lambda data: len(data)"),
}
# What can happen to the buffer, or to fd 1, after the run and before the line.
AFTER_THE_RUN = {
    "nothing": lambda run_buf: None,
    "buffer_closed": lambda run_buf: io.BufferedWriter.close(run_buf),  # a wrapper collected
    "fd_1_closed": lambda run_buf: os.close(1),  # a stream the script opened on fd 1, collected
}


@pytest.mark.parametrize(("host_kind", "script", "after", "expected"), [
    # expected: the route keeps the buffer, the run's stream stands in, what carries the line
    ("a_pipe_or_a_file", "left_alone", "nothing", (True, True, "buffer")),
    ("a_console", "left_alone", "nothing", (False, False, "host")),
    ("on_another_fd", "left_alone", "nothing", (False, False, "host")),
    ("a_pipe_or_a_file", "closed", "nothing", (False, False, "host")),
    ("a_pipe_or_a_file", "detached", "nothing", (True, False, "host")),
    ("a_pipe_or_a_file", "detached_and_kept", "nothing", (True, False, "buffer")),
    ("a_pipe_or_a_file", "buffer_detached", "nothing", (False, False, "host")),
    ("a_pipe_or_a_file", "reconfigured", "nothing", (True, True, "buffer")),
    ("a_pipe_or_a_file", "methods_replaced", "nothing", (True, True, "buffer")),
    ("a_pipe_or_a_file", "left_alone", "buffer_closed", (True, True, "host")),
    ("a_pipe_or_a_file", "left_alone", "fd_1_closed", (True, True, "descriptor")),
])
def test_the_result_line_goes_through_the_buffer_the_kit_kept(monkeypatch, tmp_path, host_kind,
                                                              script, after, expected):
    """Review 12, result-1/rest-1, result-2/rest-2, honesty-1, critic-1 (review 10's critic-1
    before them): after a run the CLI started, the result line is the kit's own — encoded here and
    written as one write of the run stdout's buffer the kit kept, where the host writes to fd 1
    through a plain file and the buffer is open — so whatever the script did to its text stream does
    not reach it; the run's text stream stands in as sys.stdout where it is still attached. Where
    the buffer cannot carry the line, the host's stream does; where fd 1 no longer leads where it
    did, the descriptor the run kept. 3.6 wrote the line through the script's own text stream: a
    detached one lost the run's record, a reconfigured one the line. A leftover thread's text and
    the host's come before the line; give_back_stdout ends it all."""
    from labkit import runner  # noqa: PLC0415

    fd1 = tmp_path / "fd1.txt"
    saved_fd1 = os.dup(1)
    with open(fd1, "wb") as sink:
        os.dup2(sink.fileno(), 1)
    other = open(tmp_path / "other.txt", "wb")  # noqa: SIM115 - closed below
    try:
        raw = {"a_pipe_or_a_file": io.FileIO(1, "w", closefd=False), "a_console": object(),
               "on_another_fd": io.FileIO(other.fileno(), "w", closefd=False)}[host_kind]
        writes, armed = [], []

        def host_flush():
            writes.append(("host flushed", None))
            if armed:  # text a thread wrote through the host's own stream reaches fd 1 now
                os.write(1, b"H")

        host = types.SimpleNamespace(buffer=types.SimpleNamespace(raw=raw),
                                     write=lambda text: writes.append(("host", text)),
                                     flush=host_flush)
        original = types.SimpleNamespace(buffer=None, flush=lambda: None)
        monkeypatch.setattr(sys, "stdout", host)
        monkeypatch.setattr(sys, "__stdout__", original)
        script_globals = {"io": io, "sys": sys}
        with runner._redirected(tmp_path / "out.txt", tmp_path / "err.txt", result_follows=True):
            run_out = sys.stdout
            run_buf = run_out.buffer
            exec(SCRIPT_STDOUT[script], script_globals)  # noqa: S102 - the script's line
        route = runner._ROUTE
        keeps, stands_in, carrier = expected
        assert (route.buffer is run_buf, sys.stdout is run_out, sys.__stdout__ is run_out) == \
            (keeps, stands_in, stands_in), (route, script)
        leftover = stands_in and script != "methods_replaced"  # that one writes nothing
        if leftover:
            run_out.write("late")  # a leftover thread's text, still in the run's text stream
        AFTER_THE_RUN[after](run_buf)
        writes.clear()
        armed.append(True)
        runner.write_result('{"title": "지역별 요약"}')
        if after == "fd_1_closed":
            os.dup2(os.open(fd1, os.O_WRONLY | os.O_APPEND), 1)  # fd 1 again, for the restore below
        written = fd1.read_text(encoding="utf-8")
        line = '{"title": "지역별 요약"}\n'
        through_the_host = ("host", "\n" + line) in writes
        assert (carrier == "host") == through_the_host, (carrier, written, writes)
        if carrier == "buffer":  # after the thread's text and the host's
            assert written.endswith("\n" + line) and written.count(line) == 1, written
            assert written.index("H") < written.index(line), written
            if leftover:
                assert written.index("late") < written.index(line), written
        elif carrier == "descriptor":
            assert written.endswith("\n" + line) and written.count(line) == 1, written
        else:
            assert line not in written and sys.stdout is host and sys.__stdout__ is original
        kept = route.fd
        runner.give_back_stdout()
        assert sys.stdout is host and sys.__stdout__ is original and runner._ROUTE is None
        with pytest.raises(OSError):
            os.fstat(kept)
    finally:
        os.dup2(saved_fd1, 1)
        os.close(saved_fd1)
        other.close()


def test_the_run_s_stream_stands_in_from_the_restore_on(monkeypatch, tmp_path):
    """Review 12, result-3: 3.6 made the host's stream sys.stdout again for the length of the
    restore and only then the run's, so a thread that looked sys.stdout up in between wrote through
    another buffer than the line's, and a print() in pieces could land inside it. The run's stream
    is sys.stdout from the restore on: at each descriptor the restore puts back, it already is."""
    from labkit import runner  # noqa: PLC0415

    fd1 = tmp_path / "fd1.txt"
    saved_fd1 = os.dup(1)
    with open(fd1, "wb") as sink:
        os.dup2(sink.fileno(), 1)
    try:
        host = types.SimpleNamespace(
            buffer=types.SimpleNamespace(raw=io.FileIO(1, "w", closefd=False)),
            write=lambda text: None, flush=lambda: None)
        monkeypatch.setattr(sys, "stdout", host)
        monkeypatch.setattr(sys, "__stdout__", host)
        seen, real_dup2 = [], os.dup2

        def watching(fd, fd2, *args, **kwargs):
            seen.append(sys.stdout)
            return real_dup2(fd, fd2, *args, **kwargs)

        with runner._redirected(tmp_path / "out.txt", tmp_path / "err.txt", result_follows=True):
            run_out = sys.stdout
            monkeypatch.setattr(os, "dup2", watching)
        monkeypatch.setattr(os, "dup2", real_dup2)
        assert len(seen) == 2 and all(stream is run_out for stream in seen), seen
        runner.give_back_stdout()
    finally:
        os.dup2(saved_fd1, 1)
        os.close(saved_fd1)


def test_a_run_without_a_result_to_follow_leaves_stdout_alone(project, monkeypatch):
    """Review 12, honesty-8 (Y18): only a caller that prints a result line next (the CLI) gets the
    stand-in and the kept descriptor; an in-process caller's sys.stdout is its own after a run."""
    from labkit import runner  # noqa: PLC0415

    own = io.TextIOWrapper(io.BufferedWriter(io.FileIO(os.dup(1), "w")), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", own)
    monkeypatch.setattr(sys, "__stdout__", own)
    try:
        result = runner.execute(core.Project(project), script=write_script(
            project, "96_inproc.py", "print('hi')\n"), title="inproc")
        assert result["run"]["status"] == "ok"
        assert sys.stdout is own and sys.__stdout__ is own and runner._ROUTE is None
    finally:
        own.close()


# Review 12's scripts: what a script does to its stdout that reached 3.6's result line.
STDOUT_IDIOMS = {
    "detached_text": ("import io, sys\n"
                      "sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8')\n"
                      "print('hello')\n"),
    "detached_codecs": ("import codecs, sys\n"
                        "sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())\n"
                        "print('hello')\n"),
    "dunder_detached": "import sys\nraw = sys.__stdout__.detach()\n",
    "buffer_detached": "import sys\nsys.stdout.buffer.detach()\n",
    "reconfigured_ascii": ("import sys\nsys.stdout.reconfigure(encoding='ascii', errors='strict')\n"
                           "print('hello')\n"),
    "reconfigured_cp1252": ("import sys\nsys.stdout.reconfigure(encoding='cp1252')\n"
                            "print('hello')\n"),
    # A wrapper over the run's buffer in the script's globals, which a function puts in a reference
    # cycle: the collector finalizes it after the run, and it closes the buffer.
    "rewrapped_and_kept": ("import io, sys\n"
                           "out = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')\n"
                           "sys.stdout = out\n"
                           "def main():\n    print('안녕하세요')\n"
                           "main()\n"),
    "tee_written": ("import os, sys\n"
                    "os.makedirs(os.path.join('data', 'derived'), exist_ok=True)\n"
                    "tee = open(os.path.join('data', 'derived', 'printed.txt'), 'w', encoding='utf-8')\n"
                    "write = sys.stdout.write\n"
                    "def both(text):\n    tee.write(text)\n    tee.flush()\n    return write(text)\n"
                    "sys.stdout.write = both\n"
                    "print('hello')\n"),
    "silenced": "import sys\nsys.stdout.write = lambda text: len(text)\nprint('hidden')\n",
    # A thread that closes the stdout it took once the run is over (fd 1 no longer the log).
    "closed_by_a_thread_after_the_run": (
        "import os, sys, threading, time\n"
        "out, log = sys.stdout, os.fstat(1)\n"
        "def later():\n"
        "    while os.path.samestat(os.fstat(1), log):\n        time.sleep(0.001)\n"
        "    time.sleep(0.005)\n    out.close()\n"
        "threading.Thread(target=later).start()\n"),
    # A stream the script opened on fd 1 itself, which owns the descriptor: collected after the run
    # (a function makes the globals a cycle), it closes the CLI's own fd 1 (review 12, critic-1).
    "own_stream_on_fd_1": ("import sys\n"
                           "out = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)\n"
                           "sys.stdout = out\n"
                           "def helper():\n    return 1\n"
                           "print('hello through my own stream')\n"),
}


@pytest.mark.parametrize("idiom", sorted(STDOUT_IDIOMS))
def test_the_result_line_outlives_what_the_script_did_to_its_stdout(project, idiom):
    """Review 12 (result-1, rest-1, result-2, rest-2, honesty-1, critic-1): each of these lost the
    CLI's result line under 3.6, and the detaching ones the run's record too: the line went through
    the script's own text stream. The line is the kit's own now, and the record is written first:
    the run is `ok`, its title arrives as it was given, and a file the script wrote through a
    replaced write() keeps the hash its record gives it."""
    code, result = labkit(project, "run", write_script(project, f"91_{idiom}.py",
                                                      STDOUT_IDIOMS[idiom]),
                          "--title", "지역별 요약")
    assert code == 0 and result.get("ok") is True and result["run"]["status"] == "ok", \
        (idiom, code, result)
    assert result["run"]["title"] == "지역별 요약", result["run"]["title"]
    record = json.loads((Path(project) / "runs" / result["run"]["id"] / "run.json")
                        .read_text(encoding="utf-8"))
    assert record["status"] == "ok"
    for output in record["outputs"]:
        assert core.sha256_file(Path(project) / output["path"]) == output["sha256"], output


@NEEDS_MONITORING
@POSIX_GUARD
@pytest.mark.parametrize("where", ["with_a_large_failure", "first_of_many_caught",
                                   "last_of_many_caught"])
def test_settle_walks_the_failure_and_each_caught_exception_with_a_budget_of_its_own(where):
    """Review 12, rest-4: 3.6 walked the region's failure first and everything handlers raised
    while the region was left after it, CHAIN_LIMIT exceptions in all, so a failure with that many
    chained or grouped exceptions (a TaskGroup over many failing tasks) hid a cancel cut short while
    the region was left, which 3.4 and 3.5 counted. Each is walked with a budget of its own now."""
    guard = _guard()
    injector = _Injector({_platform._RunGuard.on_cancel_signal.__code__},
                         _raises_the_script_s(signal.SIGTERM), entries_only=True)
    saved = signal.getsignal(signal.SIGTERM)
    evidence = None
    many = [RuntimeError(f"burst {i}") for i in range(_platform.CHAIN_LIMIT + 6)]
    large = ExceptionGroup("downloads", [ConnectionError(i)
                                         for i in range(_platform.CHAIN_LIMIT + 6)])
    try:
        with injector, guard:
            try:
                with guard.interruptible(deadline=30.0):
                    signal.signal(signal.SIGTERM, lambda *_a: sys.exit(1))  # the script's
                    injector.arm(1)
                    injector.on = True
                    signal.raise_signal(signal.SIGINT)  # a cancel, cut short at its handler's entry
                    time.sleep(1)
            except SystemExit as exc:
                evidence = exc  # what cut the cancel short, raised while the region was left
            injector.on = False
            guard._caught = {"with_a_large_failure": (evidence,),
                             "first_of_many_caught": (evidence, *many),
                             "last_of_many_caught": (*many, evidence)}[where]
            guard.settle(large)
    finally:
        signal.signal(signal.SIGTERM, saved)
    assert injector.fired and isinstance(evidence, SystemExit), (injector.fired, evidence)
    assert _outcome(guard) == {"timeout": False, "cancel": True}, _outcome(guard)


def test_a_home_is_a_directory_and_a_failed_expansion_leaves_the_name(monkeypatch, tmp_path):
    """Review 12, honesty-8 (Y9, Y11): expand_home expands a leading `~name` only to a directory —
    a file where Windows' guess lands is not a home — and a name whose expansion fails is taken as
    written."""
    guessed_file = tmp_path / "$budget.xlsx"
    guessed_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(os.path, "expanduser", lambda path: str(guessed_file))
    assert core.expand_home(Path("~$budget.xlsx")) == Path("~$budget.xlsx")

    def refuses(path):
        raise OSError(errno.EIO, "no home to be had")

    monkeypatch.setattr(os.path, "expanduser", refuses)
    assert core.expand_home(Path("~someone/x.csv")) == Path("~someone/x.csv")


@NEEDS_MONITORING
def test_a_second_ctrl_c_as_the_cli_gives_its_stdout_back_still_gives_it_back(project, monkeypatch,
                                                                              tmp_path):
    """Review 12, rest-6: a SIGINT handled as main() gave the CLI's own stdout back (a second
    Ctrl+C) raised before it was back, and left the run's stream as sys.stdout for a leftover thread
    to hold at shutdown, which then aborted as main did. At every check point of the give-back, the
    CLI's own stdout is back when main() lets the interruption go on (3.7's sweep found the points
    after the first: the route was dropped before the streams were back)."""
    from labkit import __main__ as cli  # noqa: PLC0415
    from labkit import runner  # noqa: PLC0415

    fd1 = tmp_path / "fd1.txt"
    saved_fd1 = os.dup(1)
    with open(fd1, "wb") as sink:
        os.dup2(sink.fileno(), 1)
    try:
        own = io.TextIOWrapper(io.BufferedWriter(io.FileIO(1, "w", closefd=False)),
                               encoding="utf-8")
        script = write_script(project, "93_second.py", "x = 1\n")
        reached = 0
        for target in range(1, 40):
            monkeypatch.setattr(sys, "stdout", own)
            monkeypatch.setattr(sys, "__stdout__", own)
            injector = _Injector({runner.give_back_stdout.__code__, runner._restore_host.__code__},
                                 lambda: signal.raise_signal(signal.SIGINT))
            with injector:
                injector.arm(target)
                injector.on = True
                with contextlib.suppress(KeyboardInterrupt):
                    cli.main(["--project", str(project), "run", script, "--title", "second"])
            if not injector.fired:
                break
            reached += 1
            assert sys.stdout is own and sys.__stdout__ is own and runner._ROUTE is None, target
        assert reached >= 3, reached
    finally:
        os.dup2(saved_fd1, 1)
        os.close(saved_fd1)


# --- review 13 ---------------------------------------------------------------------------------

EXIT_WITH_A_BLOCKED_WRITER = """\
import atexit, sys, threading, time
out = sys.stdout
go = threading.Event()


def after_the_run():
    go.wait()
    line = "leftover " * 100000 + "\\n"
    while True:
        out.write(line)
        out.flush()


def as_the_cli_exits():  # registered after the kit's exit flush, so it runs first
    go.set()
    time.sleep(0.5)  # the writer fills the pipe nobody reads and blocks, holding the buffer's lock


threading.Thread(target=after_the_run, daemon=True).start()
atexit.register(as_the_cli_exits)
"""


def test_the_cli_ends_after_its_result_line_though_a_leftover_thread_blocks_writing(project):
    """Review 13, result-1: a caller read the CLI's stdout up to the result line and then only
    waited for it, while a daemon thread the script left kept writing through the stdout it took:
    the thread filled the pipe and blocked in a write, holding the run stdout buffer's lock. The
    kit's exit flush of earlier runs' streams waited for that lock without end — atexit runs before
    CPython's one-second grace for such locks — and the CLI never ended; main ended with SIGABRT.
    The exit flush waits a bounded time now. A thread writing through the stream it took keeps
    that stream alive, so nothing flushes it at the interpreter's shutdown, which exits 0."""
    cmd = [sys.executable, "-m", "labkit", "--project", str(project), "run",
           write_script(project, "98_blocked.py", EXIT_WITH_A_BLOCKED_WRITER), "--title", "blocked"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=kit_env())
    got = {"out": b"", "result": None}

    def read_to_the_result_line():
        while chunk := os.read(proc.stdout.fileno(), 4096):
            got["out"] += chunk
            for line in got["out"].split(b"\n")[:-1]:
                with contextlib.suppress(ValueError):
                    if isinstance(parsed := json.loads(line), dict):
                        got["result"] = parsed
                        return

    reader = threading.Thread(target=read_to_the_result_line, daemon=True)
    reader.start()
    try:
        reader.join(240)
        # From here nobody reads the pipe: a caller that took its result and only waits.
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=60)
        ended = proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=60)
        reader.join(60)
        proc.stdout.close()
    result = got["result"]
    assert isinstance(result, dict) and result.get("ok") is True, got["out"][-3000:]
    assert ended, "the CLI did not end after its result line"
    assert proc.returncode == 0, proc.returncode


def test_the_extension_creates_projects_from_the_kit_s_own_template(monkeypatch, tmp_path):
    """Review of design revision 1, kit-1: the adapter's lab_init and /lab init computed the
    template's place themselves, and no check called them."""
    import asyncio

    module = load_extension(monkeypatch, "ext_init")

    async def synced(project):
        return True, "a stand-in for uv sync"

    async def kit(project, args, **kwargs):
        return True, {"ok": True}

    monkeypatch.setattr(module, "sync_environment", synced)
    monkeypatch.setattr(module, "run_kit", kit)
    text, value = asyncio.run(module.tool_init({"path": str(tmp_path / "by-agent"), "title": "t"},
                                               None))
    assert value["synced"] is True, text
    answer = asyncio.run(module.lab_command(f"init {tmp_path / 'by-human'} 제목"))
    assert answer.startswith("created "), answer
    for root in (tmp_path / "by-agent", tmp_path / "by-human"):
        for name in ("CONTEXT.md", "pyproject.toml", ".gitignore", "lab.json", "journal.jsonl"):
            assert (root / name).is_file(), (root, name)
