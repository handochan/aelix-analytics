"""A console the test owns, for Ctrl+C and Ctrl+Break through the installed `analytics` on Windows
(Issue #8 PR-3b, design §B.6). Ported from the probe that measured it first (CI 36952098887, on a
throwaway branch). Imported on every platform; it calls Windows only from inside its functions.

  test process (attached to the CI step's console: it never generates a console event)
    └─ job object (KILL_ON_JOB_CLOSE, no breakaway: every descendant stays countable)
        └─ a pseudo console (CreatePseudoConsole), or a classic console (CREATE_NEW_CONSOLE, hidden)
            └─ SHELL: python shell.py — survives ^C/^Break with a handler and counts them
                └─ analytics.exe → python (the launcher) → uv.exe → the venv's python.exe → the kit

Ctrl+C is the byte \\x03 typed into the pseudo console: conhost turns it into CTRL_C_EVENT for every
process on the console, as the key does. Ctrl+Break has no byte: HELPER, started detached, attaches
to the shell's console, refuses when one of the test's own processes shares it, and calls
GenerateConsoleCtrlEvent(event, 0) — group 0 is every process on that console, as a keyboard's.
Neither is a key a person pressed.

Every wait is bounded (Codex 1). A Session closes its console and its job in `close`, which a `with`
block calls whatever happened; the job ends what is still running then, and `left_after_close` is
how many processes it still held after a bounded wait.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes as w
from pathlib import Path

from labkit import core

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_SUSPENDED = 0x00000004
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
STARTF_USESTDHANDLES = 0x00000100
STARTF_USESHOWWINDOW = 0x00000001
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
JobObjectBasicAccountingInformation = 1
JobObjectBasicProcessIdList = 3
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_OBJECT_0 = 0
CTRL_C_EVENT, CTRL_BREAK_EVENT = 0, 1
STATUS_CONTROL_C_EXIT = 0xC000013A
HOSTS = ("conhost.exe", "openconsole.exe")
# The interpreter itself, not a virtual environment's python.exe, which on Windows is a launcher
# that starts it as a child: the shell is then one process, as on the probe.
PYTHON = getattr(sys, "_base_executable", None) or sys.executable

# win32-input-mode key records for Ctrl+Break (VK_CANCEL=3, scan 0x46, LEFT_CTRL_PRESSED=8), down
# then up: ESC [ Vk ; Sc ; Uc ; Kd ; Cs ; Rc _ . An observation only: undocumented as an input.
CTRL_BREAK_W32IM = b"\x1b[3;70;0;1;8;1_\x1b[3;70;0;0;8;1_"

VT = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


class COORD(ctypes.Structure):
    _fields_ = [("X", w.SHORT), ("Y", w.SHORT)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD),
                ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD),
                ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD), ("wShowWindow", w.WORD),
                ("cbReserved2", w.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD),
                ("dwThreadId", w.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount",
        "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", w.DWORD), ("TotalProcesses", w.DWORD),
                ("ActiveProcesses", w.DWORD), ("TotalTerminatedProcesses", w.DWORD)]


class _PidList(ctypes.Structure):
    _fields_ = [("Assigned", w.DWORD), ("InList", w.DWORD), ("Ids", ctypes.c_size_t * 256)]


_k32 = None


def k32():
    """kernel32 with the signatures this module uses, bound on first use (Windows only)."""
    global _k32
    if _k32 is None:
        lib = ctypes.WinDLL("kernel32", use_last_error=True)
        handle_p = ctypes.POINTER(w.HANDLE)
        for name, argtypes, restype in [
            ("CreatePipe", [handle_p, handle_p, ctypes.c_void_p, w.DWORD], w.BOOL),
            ("CreatePseudoConsole", [COORD, w.HANDLE, w.HANDLE, w.DWORD, handle_p],
             ctypes.HRESULT),
            ("ClosePseudoConsole", [w.HANDLE], None),
            ("InitializeProcThreadAttributeList",
             [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
            ("UpdateProcThreadAttribute", [ctypes.c_void_p, w.DWORD, ctypes.c_size_t,
                                           ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                           ctypes.c_void_p], w.BOOL),
            ("DeleteProcThreadAttributeList", [ctypes.c_void_p], None),
            ("CreateProcessW", [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL,
                                w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p,
                                ctypes.POINTER(PROCESS_INFORMATION)], w.BOOL),
            ("ResumeThread", [w.HANDLE], w.DWORD),
            ("CloseHandle", [w.HANDLE], w.BOOL),
            ("ReadFile", [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD),
                          ctypes.c_void_p], w.BOOL),
            ("WriteFile", [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD),
                           ctypes.c_void_p], w.BOOL),
            ("CreateJobObjectW", [ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            ("SetInformationJobObject", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD],
             w.BOOL),
            ("AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL),
            ("TerminateJobObject", [w.HANDLE, w.UINT], w.BOOL),
            ("TerminateProcess", [w.HANDLE, w.UINT], w.BOOL),
            ("QueryInformationJobObject", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD,
                                           ctypes.POINTER(w.DWORD)], w.BOOL),
            ("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD),
            ("GetExitCodeProcess", [w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            ("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            ("QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR,
                                            ctypes.POINTER(w.DWORD)], w.BOOL),
            ("GetConsoleProcessList", [ctypes.POINTER(w.DWORD), w.DWORD], w.DWORD),
        ]:
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = argtypes, restype
        _k32 = lib
    return _k32


def _check(ok, what):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)


def wait_until(predicate, seconds: float, interval: float = 0.02) -> bool:
    """Poll `predicate` until it is true or `seconds` have passed; returns its last answer."""
    end = time.monotonic() + seconds
    while True:
        if predicate():
            return True
        if time.monotonic() >= end:
            return bool(predicate())
        time.sleep(interval)


def own_console_pids() -> set[int]:
    """This process, its parent, and every process on the console it is attached to (the CI step's
    shell and uv among them): the processes HELPER must never send an event to."""
    pids = {os.getpid(), os.getppid()}
    buf = (w.DWORD * 256)()
    n = k32().GetConsoleProcessList(buf, 256)
    pids.update(buf[:min(n, 256)])
    return pids


def open_processes(pids) -> list:
    """Handles that keep each pid's process object (not its id) to ask about later."""
    handles = []
    for pid in pids:
        h = k32().OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            handles.append(h)
    return handles


def ended(handle, seconds: float = 0.0) -> bool:
    return k32().WaitForSingleObject(handle, int(seconds * 1000)) == WAIT_OBJECT_0


def close_handles(handles) -> None:
    for h in handles:
        k32().CloseHandle(h)


class Session:
    """One root process in a console of its own, inside a job; a pseudo console's output is drained
    on a thread. Use it in a `with` block: `close` runs whatever happens inside."""

    def __init__(self, cmdline: str, cwd: str, env: dict[str, str], *, conpty: bool = True,
                 cols: int = 400, rows: int = 60):
        lib = k32()
        self.conpty = conpty
        self.chunks: list[tuple[float, bytes]] = []
        self.hpc = self.in_w = self.out_r = self.process = self.job = None
        self.pid = None
        self.left_after_close: int | None = None
        self.close_console_returned: bool | None = None
        self._drainer: threading.Thread | None = None
        self._closed = False
        try:
            self._start(lib, cmdline, cwd, env, cols, rows)
        except BaseException:
            self.close()
            raise

    def _start(self, lib, cmdline, cwd, env, cols, rows):
        self.job = lib.CreateJobObjectW(None, None)
        _check(self.job, "CreateJobObjectW")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _check(lib.SetInformationJobObject(self.job, JobObjectExtendedLimitInformation,
                                           ctypes.byref(info), ctypes.sizeof(info)),
               "SetInformationJobObject")
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW if self.conpty else STARTUPINFOW)
        flags = CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED
        block = "".join(f"{k}={v}\0" for k, v in env.items()) + "\0"
        env_buf = ctypes.create_unicode_buffer(block, len(block))
        cmd_buf = ctypes.create_unicode_buffer(cmdline)
        pi = PROCESS_INFORMATION()
        in_r, in_w, out_r, out_w = w.HANDLE(), w.HANDLE(), w.HANDLE(), w.HANDLE()
        attr_buf = None
        try:
            if self.conpty:
                flags |= EXTENDED_STARTUPINFO_PRESENT
                _check(lib.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w), None, 0),
                       "CreatePipe")
                self.in_w = in_w
                _check(lib.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w), None, 0),
                       "CreatePipe")
                self.out_r = out_r
                hpc = w.HANDLE()
                lib.CreatePseudoConsole(COORD(cols, rows), in_r, out_w, 0, ctypes.byref(hpc))
                self.hpc = hpc
                size = ctypes.c_size_t()
                lib.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
                attr_buf = (ctypes.c_byte * size.value)()
                _check(lib.InitializeProcThreadAttributeList(attr_buf, 1, 0, ctypes.byref(size)),
                       "InitializeProcThreadAttributeList")
                # The HPCON value itself is lpValue (Microsoft's sample passes `hpc`, not `&hpc`).
                _check(lib.UpdateProcThreadAttribute(attr_buf, 0,
                                                     PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, hpc,
                                                     ctypes.sizeof(w.HANDLE), None, None),
                       "UpdateProcThreadAttribute")
                si.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)
                # This process's std handles are the CI's pipes (and pytest's files): without this
                # the child inherits them instead of opening the pseudo console
                # (microsoft/terminal discussion #15814).
                si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            else:  # a classic conhost console, never shown
                flags |= CREATE_NEW_CONSOLE
                si.StartupInfo.dwFlags = STARTF_USESHOWWINDOW
                si.StartupInfo.wShowWindow = 0  # SW_HIDE
            _check(lib.CreateProcessW(None, cmd_buf, None, None, False, flags, env_buf, cwd,
                                      ctypes.byref(si), ctypes.byref(pi)), "CreateProcessW")
        finally:
            for h in (in_r, out_w):  # the pseudo console holds its own copies now
                if h:
                    lib.CloseHandle(h)
            if attr_buf is not None:
                lib.DeleteProcThreadAttributeList(attr_buf)
        self.process, self.pid = pi.hProcess, pi.dwProcessId
        try:
            # Into the job before it runs, so nothing it starts escapes the count.
            _check(lib.AssignProcessToJobObject(self.job, pi.hProcess), "AssignProcessToJobObject")
        except BaseException:
            lib.TerminateProcess(pi.hProcess, 1)
            raise
        finally:
            lib.ResumeThread(pi.hThread)
            lib.CloseHandle(pi.hThread)
        if self.conpty:
            self._drainer = threading.Thread(target=self._drain, daemon=True)
            self._drainer.start()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _drain(self):
        buf = (ctypes.c_char * 65536)()
        n = w.DWORD()
        while k32().ReadFile(self.out_r, buf, len(buf), ctypes.byref(n), None) and n.value:
            self.chunks.append((time.time(), bytes(buf[:n.value])))

    def type(self, data: bytes) -> None:
        n = w.DWORD()
        _check(k32().WriteFile(self.in_w, data, len(data), ctypes.byref(n), None), "WriteFile")

    def text(self) -> str:
        raw = b"".join(c for _, c in list(self.chunks)).decode("utf-8", "replace")
        return VT.sub("", raw).replace("\r", "")

    def job_pids(self) -> list[int]:
        lst = _PidList()
        _check(k32().QueryInformationJobObject(self.job, JobObjectBasicProcessIdList,
                                               ctypes.byref(lst), ctypes.sizeof(lst), None),
               "QueryInformationJobObject")
        return list(lst.Ids[:lst.InList])

    def active(self) -> int:
        acc = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        _check(k32().QueryInformationJobObject(self.job, JobObjectBasicAccountingInformation,
                                               ctypes.byref(acc), ctypes.sizeof(acc), None),
               "QueryInformationJobObject")
        return acc.ActiveProcesses

    def root_ended(self) -> bool:
        return ended(self.process)

    def exit_code(self) -> int | None:
        """The root's exit status once it has ended, else None."""
        if not self.root_ended():
            return None
        code = w.DWORD()
        _check(k32().GetExitCodeProcess(self.process, ctypes.byref(code)), "GetExitCodeProcess")
        return code.value

    @staticmethod
    def names(pids) -> dict[int, str | None]:
        out = {}
        for pid in pids:
            h = k32().OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                out[pid] = None
                continue
            buf = ctypes.create_unicode_buffer(1024)
            size = w.DWORD(1024)
            ok = k32().QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            k32().CloseHandle(h)
            out[pid] = os.path.basename(buf.value) if ok else None
        return out

    def close_console(self, seconds: float = 30.0) -> bool:
        """ClosePseudoConsole, as closing the window does: CTRL_CLOSE_EVENT to every process still
        attached. On a thread, so a call that does not return does not hang the test."""
        if not self.hpc:
            return True
        hpc, self.hpc = self.hpc, None
        caller = threading.Thread(target=k32().ClosePseudoConsole, args=(hpc,), daemon=True)
        caller.start()
        caller.join(seconds)
        self.close_console_returned = not caller.is_alive()
        if self._drainer is not None:
            self._drainer.join(5)
        return self.close_console_returned

    def close(self, seconds: float = 10.0) -> None:
        """Close the console, end whatever the job still holds, and count what it holds after a
        bounded wait (`left_after_close`); then release every handle. Safe to call again."""
        if self._closed:
            return
        self._closed = True
        lib = k32()
        try:
            self.close_console()
            if self.job:
                lib.TerminateJobObject(self.job, 1)
                wait_until(lambda: self.active() == 0, seconds, 0.05)
                self.left_after_close = self.active()
        finally:
            for h in (self.in_w, self.out_r, self.process, self.job):
                if h:
                    lib.CloseHandle(h)


SHELL = r'''
import ctypes, json, os, signal, subprocess, sys, time
from ctypes import wintypes as w
from pathlib import Path

done, result, final = (Path(p) for p in sys.argv[1:4])
cp, script, argv = int(sys.argv[4]), sys.argv[5], sys.argv[6:]
# A shell survives ^C/^Break with a handler, never SetConsoleCtrlHandler(NULL, TRUE), whose
# "ignore" bit its children would inherit. The ctypes handler runs on the thread the console
# starts for each event, so it counts every delivery even while this thread waits on the child
# (a Python signal handler would run once for two, CC-11). It is the last registered, so the
# CRT's (Python's) runs only for the events it passes on: a close, which ends this process.
for name in ("SIGINT", "SIGBREAK"):
    signal.signal(getattr(signal, name), lambda *a: None)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
counts = {"CTRL_C": 0, "CTRL_BREAK": 0, "CTRL_CLOSE": 0, "other": 0}
NAMES = {0: "CTRL_C", 1: "CTRL_BREAK", 2: "CTRL_CLOSE"}

def on_event(event):
    counts[NAMES.get(event, "other")] += 1
    return event in (0, 1)

HANDLER = ctypes.WINFUNCTYPE(w.BOOL, w.DWORD)(on_event)
k32.SetConsoleCtrlHandler(HANDLER, True)
if cp:  # what `chcp` does to this console
    k32.SetConsoleOutputCP(cp)
    k32.SetConsoleCP(cp)

def status():
    for path in Path("runs").glob("*/run.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (record.get("script") or {}).get("path") == script:
            return record.get("status")
    return None

rc = subprocess.call(argv) & 0xFFFFFFFF  # same console, same console process group
t = time.time()
pids = (ctypes.c_uint32 * 64)()
n = k32.GetConsoleProcessList(pids, 64)
def write(path, data):  # whole or not at all: the test polls for it
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)

write(result, {"rc": rc, "t": t, "attached_at_prompt": list(pids[:min(n, 64)]),
               "status_at_prompt": status(), "shell": os.getpid(),
               "cp": [k32.GetConsoleCP(), k32.GetConsoleOutputCP()], "deliveries": dict(counts)})
sys.stdout.write(f"\n__PROMPT__ rc={rc:#x}\n")
sys.stdout.flush()
end = time.time() + 300
while not done.exists() and time.time() < end:
    time.sleep(0.05)
write(final, {"deliveries": dict(counts), "t": time.time()})
'''

HELPER = r'''
import ctypes, json, sys, time
from ctypes import wintypes as w

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
target, event = int(sys.argv[1]), int(sys.argv[2])
forbidden = {int(p) for p in sys.argv[3].split(",") if p}
HANDLER = ctypes.WINFUNCTYPE(w.BOOL, w.DWORD)
survive = HANDLER(lambda ev: True)  # Ctrl+Break cannot be ignored with NULL: a handler returns TRUE
k32.FreeConsole()
if not k32.AttachConsole(target):
    print(json.dumps({"attached": False, "error": ctypes.get_last_error()}))
    sys.exit(1)
k32.SetConsoleCtrlHandler(survive, True)  # after AttachConsole, which resets the handler table
k32.SetConsoleCtrlHandler(None, True)
pids = (ctypes.c_uint32 * 64)()
n = k32.GetConsoleProcessList(pids, 64)
attached = list(pids[:min(n, 64)])
if forbidden & set(attached):  # the test's own console: never send there
    print(json.dumps({"refused": attached}))
    sys.exit(2)
ok = k32.GenerateConsoleCtrlEvent(event, 0)  # group 0: every process on this console
err = ctypes.get_last_error()
print(json.dumps({"attached": attached, "sent": bool(ok), "error": err, "t_sent": time.time()}))
'''

STARTED = "print('시작 started', flush=True)\n"
SCRIPTS = {
    "busy": "import os, time\nfrom pathlib import Path\n" + STARTED +
            "Path(os.environ['PROBE_READY']).touch()\nend = time.monotonic() + 60\n"
            "while time.monotonic() < end:\n    pass\n",
    "sleep": "import os, time\nfrom pathlib import Path\n" + STARTED +
             "Path(os.environ['PROBE_READY']).touch()\ntime.sleep(15)\n",
    "long": "import os, time\nfrom pathlib import Path\n" + STARTED +
            "Path(os.environ['PROBE_READY']).touch()\ntime.sleep(120)\n",
    # In a console the script's own child gets the Ctrl+C too, unlike the extension's request file:
    # the wait ends because the child does (CC-1).
    "child_wait": "import os, subprocess, sys\nfrom pathlib import Path\n" + STARTED +
                  "Path(os.environ['PROBE_READY']).touch()\n"
                  "subprocess.run([sys.executable, '-c', 'import time; time.sleep(120)'])\n",
    # A console Ctrl+C trips CPython's C-level flag without the GIL, and _sre checks it.
    "regex": "import os, re\nfrom pathlib import Path\n" + STARTED +
             "Path(os.environ['PROBE_READY']).touch()\nre.match(r'(a+)+$', 'a' * 40 + 'b')\n",
    "swallow": "import os, time\nfrom pathlib import Path\n" + STARTED +
               "Path(os.environ['PROBE_READY']).touch()\n"
               "try:\n    time.sleep(60)\nexcept BaseException:\n"
               "    Path(os.environ['PROBE_READY'] + '.swallowed').touch()\n    time.sleep(2)\n",
    "quick": STARTED,
}


class CaseError(AssertionError):
    """A case that could not reach the point its gates look at; `result` holds what it saw."""

    def __init__(self, message: str, result: dict):
        super().__init__(f"{message}\n{json.dumps(result, ensure_ascii=False, default=str)[-3000:]}")
        self.result = result


def write_script(project: Path, name: str, script: str) -> str:
    """scripts/console_<name>.py holding SCRIPTS[script]; its project-relative path."""
    rel = f"scripts/console_{name}.py"
    (Path(project) / rel).write_text(SCRIPTS[script], encoding="utf-8")
    return rel


def record_of(project: Path, script_rel: str) -> dict | None:
    """The run of `script_rel`, with the status `analytics status` would show (a `running` record
    whose process is gone is `incomplete`)."""
    found = [r for r in core.list_runs(core.Project(project))
             if (r.get("script") or {}).get("path") == script_rel]
    return found[-1] if found else None


def send_event(shell_pid: int, event: int, scratch: Path) -> dict:
    """GenerateConsoleCtrlEvent from HELPER attached to the shell's console. `t_spawn` is taken
    before the helper is started, the anchor the case's timings are measured from (CC-10)."""
    helper = Path(scratch) / "helper.py"
    helper.write_text(HELPER, encoding="utf-8")
    forbidden = ",".join(str(p) for p in sorted(own_console_pids()))
    t_spawn = time.time()
    out = subprocess.run([PYTHON, str(helper), str(shell_pid), str(event), forbidden],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         creationflags=DETACHED_PROCESS, timeout=30)
    try:
        said = json.loads(out.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        said = {"stdout": out.stdout, "stderr": out.stderr[-1000:]}
    return {"t_spawn": t_spawn, "rc": out.returncode, **said}


def shell_command(scratch: Path, name: str, argv: list[str], *, codepage: int = 0,
                  script_rel: str = "-") -> tuple[str, dict[str, Path]]:
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    paths = {x: scratch / f"{name}.{x}" for x in ("ready", "done", "result", "final")}
    for p in paths.values():
        p.unlink(missing_ok=True)
    shell = scratch / "shell.py"
    shell.write_text(SHELL, encoding="utf-8")
    cmd = [PYTHON, str(shell), str(paths["done"]), str(paths["result"]),
           str(paths["final"]), str(codepage), script_rel, *argv]
    return subprocess.list2cmdline(cmd), paths


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_case(name: str, script: str | None, keys, *, analytics: str, project, scratch, env: dict,
             conpty: bool = True, codepage: int = 0, argv: list[str] | None = None,
             ready_timeout: float = 60.0, prompt_timeout: float = 45.0,
             settle_timeout: float = 45.0, after_prompt=None) -> dict:
    """Run `analytics run scripts/console_<name>.py` (or `argv`) under SHELL in a console of its
    own, deliver `keys` once the script says it is ready, and return what was seen.

    keys: b"..." typed into the pseudo console; ("helper", CTRL_C_EVENT | CTRL_BREAK_EVENT);
    ("after", suffix, key) — `key` once the marker `<ready>suffix` exists; ("close",) —
    ClosePseudoConsole. `after_prompt(session, result)` runs once the shell has its prompt back,
    while a kit the launcher did not wait for may still be running; its answer is kept.
    Raises CaseError when the script never gets ready or a marker never appears; the session is
    closed either way."""
    project, scratch = Path(project), Path(scratch)
    script_rel = write_script(project, name, script) if script else "-"
    if argv is None:
        argv = [analytics, "run", script_rel, "--title", f"콘솔 {name}"]
    cmdline, paths = shell_command(scratch, name, argv, codepage=codepage, script_rel=script_rel)
    env = {**env, "PROBE_READY": str(paths["ready"])}
    out: dict = {"case": name, "conpty": conpty, "codepage": codepage, "keys": repr(keys),
                 "script": script_rel}
    session = None
    try:
        with Session(cmdline, str(project), env, conpty=conpty) as session:
            out["shell"] = session.pid
            t0 = time.time()

            def finished():
                return paths["result"].exists() or session.root_ended()

            if keys and not wait_until(lambda: paths["ready"].exists() or finished(),
                                       ready_timeout):
                raise CaseError(f"{name}: the script never said it was ready", out)
            if keys and not paths["ready"].exists():
                out["text"] = session.text()[-3000:]
                out["prompt"] = _read_json(paths["result"])
                raise CaseError(f"{name}: the command ended before the script was ready", out)
            out["seconds_to_ready"] = round(time.time() - t0, 3)
            tree = session.job_pids()
            out["tree_names"] = session.names(tree)
            sent, helper = [], []
            for key in keys:
                if isinstance(key, tuple) and key[0] == "after":
                    marker = Path(str(paths["ready"]) + key[1])
                    if not wait_until(marker.exists, 15):
                        raise CaseError(f"{name}: {marker.name} never appeared", out)
                    key = key[2]
                if isinstance(key, tuple) and key[0] == "helper":
                    said = send_event(session.pid, key[1], scratch)
                    helper.append(said)
                    sent.append(said["t_spawn"])
                elif isinstance(key, tuple) and key[0] == "close":
                    sent.append(time.time())
                    out["close_console_returned"] = session.close_console()
                else:
                    sent.append(time.time())
                    session.type(key)
            out["helper"] = helper
            wait_until(finished, prompt_timeout)
            prompt = _read_json(paths["result"])
            out["prompt"] = prompt
            out["seconds_key_to_prompt"] = (round(prompt["t"] - sent[0], 3)
                                            if prompt and sent else None)
            out["active_at_prompt"] = session.active()
            out["names_at_prompt"] = session.names(session.job_pids())
            if after_prompt is not None:
                out["after_prompt"] = after_prompt(session, out)
            # What outlived the prompt (Ctrl+Break) should end on its own: wait for it, bounded.
            hosts = {p for p, n in out["tree_names"].items() if n and n.lower() in HOSTS}
            shell = {session.pid, (prompt or {}).get("shell")} - {None}
            keep = set() if session.root_ended() else shell | hosts
            wait_until(lambda: set(session.job_pids()) <= keep, settle_timeout, 0.1)
            leftover = session.job_pids()
            out["leftover_after_wait"] = session.names(leftover)
            out["left_over_after_kit"] = sorted(set(leftover) - keep)
            out["seconds_to_settle"] = round(time.time() - t0, 3)
            paths["done"].touch()
            wait_until(session.root_ended, 10, 0.05)
            out["final"] = _read_json(paths["final"])
            out["deliveries"] = ((out["final"] or {}).get("deliveries")
                                 or (prompt or {}).get("deliveries"))
            out["markers"] = sorted(p.name for p in scratch.glob(f"{name}.ready.*"))
            if script:
                record = record_of(project, script_rel)
                out["record_status"] = record and record.get("status")
                out["record_error"] = record and record.get("error")
                out["record_times"] = record and [record.get("started_at"),
                                                  record.get("ended_at")]
                log = record and record.get("stdout")
                out["stdout_log"] = ((project / log).read_text(encoding="utf-8",
                                                               errors="replace")[:2000]
                                     if log and (project / log).is_file() else None)
            time.sleep(0.3)
            text = session.text()
            head, mark, tail = text.partition("__PROMPT__")
            out["prompt_on_console"] = bool(mark)
            out["result_line_before_prompt"] = _result_line(head)
            out["result_line_after_prompt"] = _result_line(tail)
            out["korean_title_on_console"] = f"콘솔 {name}" in text
            out["text_tail"] = text[-1500:]
            if not session.conpty:  # no output pipe from a classic console: not read, not false
                for key in ("prompt_on_console", "result_line_before_prompt",
                            "result_line_after_prompt", "korean_title_on_console", "text_tail"):
                    out[key] = None
    finally:
        if session is not None:
            out["left_after_close"] = session.left_after_close
            out["close_console_returned"] = out.get("close_console_returned",
                                                    session.close_console_returned)
    return out


def _result_line(text: str) -> bool:
    return any(line.startswith("{") and '"run"' in line for line in text.splitlines())


def typed_case(name: str, shell_argv: list[str], command: str, rc_line: str, *, prompt_text: str,
               project, scratch, env: dict, script: str = "busy") -> dict:
    """Start a real shell (cmd, pwsh) in a pseudo console, type `command` (which runs
    scripts/console_<name>.py), Ctrl+C once the script is ready, then type `rc_line`, which prints
    `__RC__<status>__`. An observation: every wait is bounded and nothing here asserts."""
    project, scratch = Path(project), Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    script_rel = write_script(project, name, script)
    ready = scratch / f"{name}.ready"
    ready.unlink(missing_ok=True)
    out: dict = {"case": name, "shell": shell_argv, "command": command}
    session = None
    try:
        with Session(subprocess.list2cmdline(shell_argv), str(project),
                     {**env, "PROBE_READY": str(ready)}) as session:
            out["shell_prompt_seen"] = wait_until(lambda: prompt_text in session.text(), 30, 0.1)
            session.type(command.encode("utf-8") + b"\r")
            out["ready"] = wait_until(lambda: ready.exists() or session.root_ended(), 60)
            if out["ready"] and ready.exists():
                session.type(b"\x03")
                wait_until(lambda: set(session.job_pids()) <= {session.pid}, 45, 0.1)
                out["shell_alive_after_the_run"] = not session.root_ended()
                out["leftover_after_wait"] = session.names(session.job_pids())
                record = record_of(project, script_rel)
                out["record_status"] = record and record.get("status")
                session.type(rc_line.encode("utf-8") + b"\r")
                pattern = re.compile(r"__RC__(-?\d+)__")
                wait_until(lambda: pattern.search(session.text()), 15, 0.1)
                found = pattern.search(session.text())
                out["rc"] = int(found.group(1)) if found else None
                out["result_line_on_console"] = _result_line(session.text())
            session.type(b"exit\r")
            out["shell_exited"] = wait_until(session.root_ended, 15, 0.1)
            out["text_tail"] = session.text()[-2000:]
    finally:
        if session is not None:
            out["left_after_close"] = session.left_after_close
    return out
