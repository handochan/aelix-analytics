"""Windows native-console probe for `analytics run` (Issue #8 PR-3 design input, run on a throwaway branch).

Issue #8 PR-3 design input. Every Windows statement here is reasoned from Microsoft/CPython/uv
sources, not observed; the first CI run decides. Shape:

  harness (pytest, attached to the CI step's own console — it NEVER generates a console event)
    └─ job object (KILL_ON_JOB_CLOSE; no breakaway, so every descendant stays countable)
        └─ ConPTY (CreatePseudoConsole) — or, variant B, a classic console (CREATE_NEW_CONSOLE)
            └─ SHELL: python shell.py  (no creationflags beyond the console; ignores ^C/^Break with a
               *handler*, never SetConsoleCtrlHandler(NULL, TRUE), whose "ignore" bit is inherited)
                └─ analytics.exe (uv trampoline) → python cli.py → uv.exe → venv python.exe shim
                   → python -m labkit (the kit)            all attached to the same console

Keys: ConPTY input "\x03" is conhost's own VT-input path to CTRL_C_EVENT (needs ENABLE_PROCESSED_INPUT,
on by default). Ctrl+Break has no VT byte: a helper (DETACHED_PROCESS) AttachConsole()s to the SHELL,
checks GetConsoleProcessList() holds none of the harness's pids, and calls
GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, 0) — group 0 = every process on that console, as a keyboard
does. The win32-input-mode Ctrl+Break sequence is tried as an observation only.
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

# ---------------------------------------------------------------------------- Win32 bindings
k32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None

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
CTRL_C_EVENT, CTRL_BREAK_EVENT = 0, 1
INFINITE = 0xFFFFFFFF


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
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", w.DWORD), ("TotalProcesses", w.DWORD),
                ("ActiveProcesses", w.DWORD), ("TotalTerminatedProcesses", w.DWORD)]


def _bind():
    sig = {
        "CreatePipe": ([ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), ctypes.c_void_p, w.DWORD], w.BOOL),
        "CreatePseudoConsole": ([COORD, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], ctypes.HRESULT),
        "ClosePseudoConsole": ([w.HANDLE], None),
        "InitializeProcThreadAttributeList": ([ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
        "UpdateProcThreadAttribute": ([ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], w.BOOL),
        "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
        "CreateProcessW": ([w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(PROCESS_INFORMATION)], w.BOOL),
        "ResumeThread": ([w.HANDLE], w.DWORD),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "ReadFile": ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
        "WriteFile": ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
        "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
    }
    for name, (argtypes, restype) in sig.items():
        fn = getattr(k32, name)
        fn.argtypes, fn.restype = argtypes, restype


if k32 is not None:
    _bind()


def _check(ok, what):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)


# ---------------------------------------------------------------------------- the console session
class Session:
    """One root process in a private console, inside a job; output drained on a thread."""

    def __init__(self, cmdline: str, cwd: str, env: dict[str, str], *, conpty: bool = True,
                 cols: int = 400, rows: int = 60):
        self.chunks: list[tuple[float, bytes]] = []
        self.hpc = self.in_w = self.out_r = None
        self.job = k32.CreateJobObjectW(None, None)
        _check(self.job, "CreateJobObjectW")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE  # a safety net
        _check(k32.SetInformationJobObject(self.job, JobObjectExtendedLimitInformation,
                                           ctypes.byref(info), ctypes.sizeof(info)), "SetInformationJobObject")
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW) if conpty else ctypes.sizeof(STARTUPINFOW)
        flags = CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED
        if conpty:
            flags |= EXTENDED_STARTUPINFO_PRESENT
        attr_buf = None
        if conpty:
            in_r, in_w, out_r, out_w = w.HANDLE(), w.HANDLE(), w.HANDLE(), w.HANDLE()
            _check(k32.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w), None, 0), "CreatePipe in")
            _check(k32.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w), None, 0), "CreatePipe out")
            hpc = w.HANDLE()
            k32.CreatePseudoConsole(COORD(cols, rows), in_r, out_w, 0, ctypes.byref(hpc))  # raises on failure
            self.hpc, self.in_w, self.out_r = hpc, in_w, out_r
            size = ctypes.c_size_t()
            k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attr_buf = (ctypes.c_byte * size.value)()
            _check(k32.InitializeProcThreadAttributeList(attr_buf, 1, 0, ctypes.byref(size)), "InitAttr")
            # The HPCON value itself is lpValue (Microsoft's sample passes `hpc`, not `&hpc`).
            _check(k32.UpdateProcThreadAttribute(attr_buf, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                                                 hpc, ctypes.sizeof(w.HANDLE), None, None), "UpdateAttr")
            si.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)
            # The harness's own std handles are CI pipes: without this the child inherits them instead of
            # opening the pseudoconsole (microsoft/terminal discussion #15814).
            si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        else:  # variant B: a classic conhost console, never shown
            flags |= CREATE_NEW_CONSOLE
            si.StartupInfo.dwFlags = STARTF_USESHOWWINDOW
            si.StartupInfo.wShowWindow = 0  # SW_HIDE
        block = "".join(f"{k}={v}\0" for k, v in env.items()) + "\0"
        env_buf = ctypes.create_unicode_buffer(block, len(block))
        cmd_buf = ctypes.create_unicode_buffer(cmdline)
        pi = PROCESS_INFORMATION()
        _check(k32.CreateProcessW(None, cmd_buf, None, None, False, flags, env_buf, cwd,
                                  ctypes.byref(si), ctypes.byref(pi)), "CreateProcessW")
        # Into the job before it runs, so nothing it starts escapes the count.
        _check(k32.AssignProcessToJobObject(self.job, pi.hProcess), "AssignProcessToJobObject")
        k32.ResumeThread(pi.hThread)
        k32.CloseHandle(pi.hThread)
        self.process, self.pid = pi.hProcess, pi.dwProcessId
        if conpty:
            k32.CloseHandle(in_r)   # the pseudoconsole holds its own copies now
            k32.CloseHandle(out_w)
            k32.DeleteProcThreadAttributeList(attr_buf)
            threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        buf = (ctypes.c_char * 65536)()
        n = w.DWORD()
        while k32.ReadFile(self.out_r, buf, len(buf), ctypes.byref(n), None) and n.value:
            self.chunks.append((time.time(), bytes(buf[:n.value])))

    def type(self, data: bytes):
        n = w.DWORD()
        _check(k32.WriteFile(self.in_w, data, len(data), ctypes.byref(n), None), "WriteFile")

    def text(self) -> str:
        raw = b"".join(c for _, c in self.chunks).decode("utf-8", "replace")
        return VT.sub("", raw).replace("\r", "")

    def job_pids(self) -> list[int]:
        class LIST(ctypes.Structure):
            _fields_ = [("Assigned", w.DWORD), ("InList", w.DWORD), ("Ids", ctypes.c_size_t * 256)]
        lst = LIST()
        _check(k32.QueryInformationJobObject(self.job, JobObjectBasicProcessIdList, ctypes.byref(lst),
                                             ctypes.sizeof(lst), None), "QueryJob pids")
        return list(lst.Ids[:lst.InList])

    def active(self) -> int:
        acc = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        _check(k32.QueryInformationJobObject(self.job, JobObjectBasicAccountingInformation,
                                             ctypes.byref(acc), ctypes.sizeof(acc), None), "QueryJob acct")
        return acc.ActiveProcesses

    def close(self, drain_seconds: float = 2.0):
        if self.hpc:
            # Drained concurrently by _drain; closing ends every process still attached (documented).
            k32.ClosePseudoConsole(self.hpc)
            time.sleep(drain_seconds)
        for h in (self.in_w, self.out_r, self.process, self.job):  # closing the job kills leftovers
            if h:
                k32.CloseHandle(h)


VT = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")

# ---------------------------------------------------------------------------- the two helpers
SHELL = r'''
import ctypes, json, os, signal, subprocess, sys, time
from pathlib import Path
done, result, cp, argv = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4:]
# A shell survives ^C/^Break with a handler. Not SetConsoleCtrlHandler(NULL, TRUE): that bit is inherited.
for name in ("SIGINT", "SIGBREAK"):
    signal.signal(getattr(signal, name), lambda *a: None)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
if cp:  # e.g. 949: what `chcp 949` does to this console
    k32.SetConsoleOutputCP(cp); k32.SetConsoleCP(cp)
rc = subprocess.call(argv)  # same console, same console process group
t = time.time()
pids = (ctypes.c_uint32 * 64)(); n = k32.GetConsoleProcessList(pids, 64)
runs = sorted(Path("runs").glob("*/run.json"), key=os.path.getmtime)
status = json.loads(runs[-1].read_text(encoding="utf-8")).get("status") if runs else None
result.write_text(json.dumps({"rc": rc & 0xFFFFFFFF, "t": t, "attached_at_prompt": list(pids[:n]),
                              "status_at_prompt": status, "shell": os.getpid(),
                              "cp": [k32.GetConsoleCP(), k32.GetConsoleOutputCP()]}), encoding="utf-8")
sys.stdout.write(f"\n__PROMPT__ rc={rc & 0xFFFFFFFF:#x}\n"); sys.stdout.flush()
end = time.time() + 180
while not done.exists() and time.time() < end:
    time.sleep(0.05)
'''

HELPER = r'''
import ctypes, json, sys
from ctypes import wintypes as w
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
target, event, forbidden = int(sys.argv[1]), int(sys.argv[2]), {int(p) for p in sys.argv[3].split(",") if p}
HANDLER = ctypes.WINFUNCTYPE(w.BOOL, w.DWORD)
survive = HANDLER(lambda ev: True)   # Ctrl+Break cannot be ignored with NULL: a handler must return TRUE
k32.FreeConsole()
if not k32.AttachConsole(target):
    print(json.dumps({"attached": False, "error": ctypes.get_last_error()})); sys.exit(1)
k32.SetConsoleCtrlHandler(survive, True)   # after AttachConsole, which resets the handler table
k32.SetConsoleCtrlHandler(None, True)
pids = (ctypes.c_uint32 * 64)(); n = k32.GetConsoleProcessList(pids, 64); attached = list(pids[:n])
if forbidden & set(attached):   # the CI step's own console: never send there
    print(json.dumps({"refused": attached})); sys.exit(2)
ok = k32.GenerateConsoleCtrlEvent(event, 0)   # group 0: every process on this console, as a keyboard
err = ctypes.get_last_error()
print(json.dumps({"attached": attached, "sent": bool(ok), "error": err}))
'''

# win32-input-mode key records for Ctrl+Break (VK_CANCEL=3, scan 0x46, LEFT_CTRL_PRESSED=8), down then
# up: ESC [ Vk ; Sc ; Uc ; Kd ; Cs ; Rc _  — observation only: whether conhost honours it is unverified.
CTRL_BREAK_W32IM = b"\x1b[3;70;0;1;8;1_\x1b[3;70;0;0;8;1_"

SCRIPTS = {
    # Bare Python, no kit: does the key reach a process on this console at all, and which handler runs?
    "raw": "import os, signal, sys, time\nfrom pathlib import Path\nr = os.environ['PROBE_READY']\n"
           "def on(sig, frame):\n    Path(r + '.' + signal.Signals(sig).name).touch()\n    sys.exit(7)\n"
           "signal.signal(signal.SIGINT, on)\nsignal.signal(signal.SIGBREAK, on)\n"
           "Path(r).touch()\ntime.sleep(30)\nsys.exit(9)\n",
    "busy": "import os, time\nfrom pathlib import Path\nprint('시작 started', flush=True)\n"
            "Path(os.environ['PROBE_READY']).touch()\nend = time.monotonic() + 60\n"
            "while time.monotonic() < end:\n    pass\n",
    "sleep": "import os, time\nfrom pathlib import Path\nPath(os.environ['PROBE_READY']).touch()\ntime.sleep(15)\n",
    # In a console the script's own child gets the Ctrl+C too, unlike the extension's request file.
    "child_wait": "import os, subprocess, sys\nfrom pathlib import Path\nPath(os.environ['PROBE_READY']).touch()\n"
                  "subprocess.run([sys.executable, '-c', 'import time; time.sleep(120)'])\n",
    # Catastrophic regex: a console Ctrl+C trips CPython's C-level flag without the GIL; _sre checks it.
    "regex": "import os, re\nfrom pathlib import Path\nPath(os.environ['PROBE_READY']).touch()\n"
             "re.match(r'(a+)+$', 'a' * 40 + 'b')\n",
    "swallow": "import os, time\nfrom pathlib import Path\nPath(os.environ['PROBE_READY']).touch()\n"
               "try:\n    time.sleep(60)\nexcept BaseException:\n"
               "    Path(os.environ['PROBE_READY'] + '.swallowed').touch()\n    time.sleep(2)\n",
}


def run_case(name, script, keys, *, analytics, project, scratch, conpty=True, codepage=0, gap=0.0, raw=False):
    """keys: list of b"..." (typed into the ConPTY) or ("helper", CTRL_C_EVENT|CTRL_BREAK_EVENT)."""
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    script_path = Path(project) / "scripts" / f"probe_{name}.py"
    script_path.write_text(SCRIPTS[script], encoding="utf-8")
    ready, done, result = (scratch / f"{name}.{x}" for x in ("ready", "done", "result.json"))
    for p in (ready, done, result):
        p.unlink(missing_ok=True)
    shell = scratch / "shell.py"
    shell.write_text(SHELL, encoding="utf-8")
    helper = scratch / "helper.py"
    helper.write_text(HELPER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("VIRTUAL_ENV", "PYTHONPATH", "LAB_PROJECT")}
    env.update({"PROBE_READY": str(ready), "AELIX_LAB_TRUST_FILE": str(scratch / "trust.json")})
    if raw:
        argv = [sys.executable, str(shell), str(done), str(result), str(codepage), sys.executable, str(script_path)]
    else:
        argv = [sys.executable, str(shell), str(done), str(result), str(codepage), analytics, "run",
                f"scripts/probe_{name}.py", "--title", f"콘솔 {name}"]
    s = Session(subprocess.list2cmdline(argv), str(project), env, conpty=conpty)
    t0 = time.time()
    while not ready.exists():
        if time.time() - t0 > 120:
            s.close()
            raise RuntimeError(f"{name}: never ready\n{s.text()[-2000:]}")
        time.sleep(0.02)
    tree = s.job_pids()
    sent, helper_out = [], []
    for i, key in enumerate(keys):
        if i and gap:
            time.sleep(gap)
        if isinstance(key, tuple):  # ("helper", event) or ("after", suffix, key)
            if key[0] == "after":
                while not Path(str(ready) + key[1]).exists():
                    time.sleep(0.02)
                key = key[2]
            if isinstance(key, tuple) and key[0] == "helper":
                forbidden = f"{os.getpid()},{os.getppid()}"
                out = subprocess.run([sys.executable, str(helper), str(s.pid), str(key[1]), forbidden],
                                     capture_output=True, text=True, creationflags=DETACHED_PROCESS, timeout=30)
                helper_out.append(out.stdout.strip())
                sent.append(time.time())
                continue
        s.type(key)
        sent.append(time.time())
    while not result.exists() and time.time() - t0 < 180:
        time.sleep(0.02)
    prompt = json.loads(result.read_text(encoding="utf-8")) if result.exists() else None
    active_at_prompt = s.active()
    pids_at_prompt = s.job_pids()
    # Wait for whatever outlived the prompt (the Ctrl+Break case): the kit should finish on its own.
    deadline = time.time() + 60
    while s.active() > 1 and time.time() < deadline:   # 1 = the shell, still lingering
        time.sleep(0.1)
    leftover = s.job_pids()
    runs = sorted((p for p in (Path(project) / "runs").glob("*/run.json") if p.stat().st_mtime >= t0 - 1),
                  key=os.path.getmtime)
    record = json.loads(runs[-1].read_text(encoding="utf-8")) if runs and not raw else None
    markers = sorted(p.name for p in scratch.glob(f"{name}.ready.*"))
    done.touch()
    time.sleep(0.5)
    text = s.text()
    s.close()
    head, _, tail = text.partition("__PROMPT__")
    return {
        "case": name, "conpty": conpty, "codepage": codepage, "keys": repr(keys),
        "helper": helper_out, "processes_in_job_during": tree,
        "prompt": prompt, "active_in_job_at_prompt": active_at_prompt, "pids_at_prompt": pids_at_prompt,
        "leftover_after_wait": leftover, "markers": markers,
        "record_status": record and record.get("status"),
        "record_error": record and record.get("error"),
        "result_line_before_prompt": any(l.startswith("{") and '"run"' in l for l in head.splitlines()),
        "result_line_after_prompt": any(l.startswith("{") and '"run"' in l for l in tail.splitlines()),
        "korean_title_on_console": f"콘솔 {name}" in text,
        "seconds_key_to_prompt": round(prompt["t"] - sent[0], 3) if prompt and sent else None,
        "tail": text[-1500:],
    }


CASES = {
    # bare Python first: whether each delivery reaches a console process at all
    "raw_conpty_ctrl_c": dict(script="raw", keys=[b"\x03"], raw=True),
    "raw_conpty_helper_break": dict(script="raw", keys=[("helper", CTRL_BREAK_EVENT)], raw=True),
    "raw_classic_helper_c": dict(script="raw", keys=[("helper", CTRL_C_EVENT)], conpty=False, raw=True),
    # deterministic: gate on these
    "c_busy": dict(script="busy", keys=[b"\x03"]),
    "c_sleep": dict(script="sleep", keys=[b"\x03"]),
    "c_swallow_second": dict(script="swallow", keys=[b"\x03", ("after", ".swallowed", b"\x03")]),
    "c_busy_cp949": dict(script="busy", keys=[b"\x03"], codepage=949),
    "c_busy_classic": dict(script="busy", keys=[("helper", CTRL_C_EVENT)], conpty=False),
    # measure the G inferences (observations until the first run)
    "brk_busy": dict(script="busy", keys=[("helper", CTRL_BREAK_EVENT)]),
    "brk_sleep": dict(script="sleep", keys=[("helper", CTRL_BREAK_EVENT)]),
    "brk_w32im": dict(script="busy", keys=[CTRL_BREAK_W32IM]),
    "c_child_wait": dict(script="child_wait", keys=[b"\x03"]),
    "c_regex": dict(script="regex", keys=[b"\x03"]),
    "c_double_same_write": dict(script="busy", keys=[b"\x03\x03"]),
}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--analytics", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    out = []
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    for n in a.cases.split(","):
        try:
            out.append(run_case(n, analytics=a.analytics, project=a.project, scratch=a.scratch, **CASES[n]))
        except BaseException as exc:  # noqa: BLE001 — one case's failure is that case's observation
            import traceback

            out.append({"case": n, "probe_error": repr(exc), "trace": traceback.format_exc()[-2000:]})
        print(json.dumps(out[-1], ensure_ascii=False)[:1500], flush=True)
        Path(a.output).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
