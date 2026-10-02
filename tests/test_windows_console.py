"""Ctrl+C and Ctrl+Break at a Windows console, through the installed `analytics` (Issue #8 PR-3b,
design §B.6; ADR-0003 check 6; docs/CONTRACTS.md F and G).

The installed command drives a real project — real uv, the project's environment, the kit — in a
console this module owns (tests/windows_console.py): a pseudo console, where Ctrl+C is the byte
\\x03, and a classic console; Ctrl+Break is a helper's GenerateConsoleCtrlEvent. None of it is a key
a person pressed at a visible window, which stays `not_run`. One trial per case.

The gates are what must hold every time. What depends on timing, or on an input Windows does not
document, is kept as an observation (AELIX_ANALYTICS_TEST_EVIDENCE_DIR, `console/<case>.json`) and
never asserted; every case, gate or observation, is written there before its asserts. The CI runs
this module in a step of its own (it needs AELIX_ANALYTICS_TEST_INSTALL=1: PyPI and an analysis
environment); the suite's step leaves it out.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import windows_console as wc
from conftest import record_observation

PACK = Path(__file__).resolve().parents[1]
E2E = os.environ.get("AELIX_ANALYTICS_TEST_INSTALL") == "1"
TITLE = "콘솔 분석"

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="the Windows console"),
    pytest.mark.skipif(not E2E, reason="set AELIX_ANALYTICS_TEST_INSTALL=1 (network: PyPI, an "
                                       "analysis environment)"),
]


def ok(argv, **kwargs) -> subprocess.CompletedProcess:
    done = subprocess.run([str(a) for a in argv], capture_output=True, encoding="utf-8",
                          errors="replace", timeout=600, **kwargs)
    assert done.returncode == 0, f"{argv}\nstdout={done.stdout}\nstderr={done.stderr}"
    return done


@pytest.fixture(scope="module")
def console(tmp_path_factory):
    """The wheel built from this checkout, installed on 3.12 and 3.11 (test_packaging's way), and
    one project the 3.12 install created with real uv. Every case runs its own script in it."""
    uv = shutil.which("uv")
    assert uv is not None, "the console tests need uv on PATH"
    base = tmp_path_factory.mktemp("console")
    ok([uv, "build", "--wheel", "--quiet", "-o", base / "dist", PACK])
    [wheel] = (base / "dist").glob("*.whl")
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("PYTHONPATH", "VIRTUAL_ENV", "LAB_PROJECT", "LAB_ACTOR",
                                "AELIX_LAB_KIT", "AELIX_LAB_UV", "AELIX_LAB_PROJECT")}
    env.update({"AELIX_LAB_TRUST_FILE": str(base / "trust.json"), "PYTHONDONTWRITEBYTECODE": "1"})
    installs = {}
    for python in ("3.12", "3.11"):
        venv = base / f"venv-{python}"
        ok([uv, "venv", "--quiet", "--python", python, venv])
        ok([uv, "pip", "install", "--quiet", "--python", venv / "Scripts" / "python.exe",
            "--no-deps", "--offline", wheel])
        installs[python] = str(venv / "Scripts" / "analytics.exe")
    work = base / "work"
    work.mkdir()
    ok([installs["3.12"], "init", "proj", "--title", TITLE], cwd=work, env=env)
    return SimpleNamespace(installs=installs, project=work / "proj", scratch=base / "scratch",
                           env=env)


def case(console, name, script, keys, python="3.12", **kwargs) -> dict:
    """One trial of `name` through the `python` install; written as an observation first."""
    tag = f"{name}_py{python.replace('.', '')}"
    try:
        result = wc.run_case(tag, script, keys, analytics=console.installs[python],
                             project=console.project, scratch=console.scratch, env=console.env,
                             **kwargs)
    except wc.CaseError as exc:
        record_observation(f"console/{tag}", {"python": python, "case_error": str(exc)[:300],
                                              **exc.result})
        raise
    except Exception as exc:  # the harness's own failure is kept too
        record_observation(f"console/{tag}", {"python": python, "harness_error": repr(exc)[:300]})
        raise
    result["python"] = python
    record_observation(f"console/{tag}", result)
    return result


def observe(console, name, script, keys, python="3.12", **kwargs) -> dict:
    """A case kept as an observation only: whatever it shows is written, nothing about it is
    asserted but that the harness ended every process it started."""
    kwargs.setdefault("prompt_timeout", 20)
    kwargs.setdefault("settle_timeout", 20)
    try:
        result = case(console, name, script, keys, python, **kwargs)
    except wc.CaseError as exc:
        result = exc.result
    assert result.get("left_after_close") == 0, result
    return result


def assert_cancelled_before_the_prompt(result):
    """CONTRACTS F's promise: Ctrl+C reaches the launcher, uv and the kit together, and the
    launcher returns the kit's status after its record — so the shell finds `cancelled`, the
    result line comes before the prompt, and nothing of the run is left. The run's stdout log
    holds the script's Korean print (F's stream capture)."""
    prompt = result["prompt"]
    assert prompt and prompt["rc"] == 3, result
    assert prompt["status_at_prompt"] == "cancelled", result
    assert result["record_status"] == "cancelled", result
    assert "시작 started" in (result["stdout_log"] or ""), result
    if result["conpty"]:  # a classic console has no output pipe here: its text is not measured
        assert result["result_line_before_prompt"], result
        assert result["korean_title_on_console"], result
    assert result["left_over_after_kit"] == [], result
    # At the prompt already, not only after the settle: the shell's own list of the processes on
    # its console, taken when it got control back, holds the shell alone (Codex F3); the job's
    # sample just after it holds nothing else of the run either.
    assert prompt["attached_at_prompt"] == [prompt["shell"]], result
    stray = {pid: n for pid, n in result["names_at_prompt"].items()
             if pid != prompt["shell"] and n is not None and n.lower() not in wc.HOSTS}
    assert not stray, result
    assert result["left_after_close"] == 0, result


def assert_helper_sent(result):
    assert result["helper"] and all(h.get("sent") is True for h in result["helper"]), result


# --- Ctrl+C: gates ----------------------------------------------------------------------------


@pytest.mark.parametrize(("script", "python"), [
    ("busy", "3.12"), ("busy", "3.11"), ("sleep", "3.12"), ("sleep", "3.11"),
    ("child_wait", "3.12"), ("regex", "3.12")])
def test_ctrl_c_cancels_the_run_before_the_prompt(console, script, python):
    """Busy and sleeping scripts on both installs. `child_wait`: the script waits on a child of its
    own on the same console, which ends on the Ctrl+C, and so does the wait (CC-1: a child that
    does not end keeps a Windows wait going; not run). `regex`: a console Ctrl+C reaches C code
    that checks for signals (_sre), unlike the extension's cancel and the Windows timeout."""
    result = case(console, f"c_{script}", script, [b"\x03"], python)
    assert_cancelled_before_the_prompt(result)
    # The Ctrl+C came while the run was where the row says: the traceback names that frame.
    frame = {"child_wait": ", in _wait", "regex": ", in match"}.get(script)
    assert frame is None or frame in result["text_tail"], result


def test_ctrl_c_a_script_swallowed_is_delivered_again(console):
    """The script catches the first interrupt; the second Ctrl+C reaches every process on the
    console again — the shell counts two (CC-11) — and the run ends `cancelled`."""
    result = case(console, "c_swallow_second", "swallow",
                  [b"\x03", ("after", ".swallowed", b"\x03")])
    assert result["markers"] == ["c_swallow_second_py312.ready.swallowed"], result
    assert result["deliveries"]["CTRL_C"] == 2, result
    assert_cancelled_before_the_prompt(result)


def test_ctrl_c_at_console_code_page_949(console):
    """The console at code page 949, as `chcp 949` leaves it: the Korean title still shows on the
    console, as at 437 (crit-11: output through Python's console path; raw bytes written to a
    console handle are not measured)."""
    result = case(console, "c_busy_cp949", "busy", [b"\x03"], codepage=949)
    assert result["prompt"]["cp"] == [949, 949], result
    assert_cancelled_before_the_prompt(result)


def test_ctrl_c_in_a_classic_console(console):
    """A classic console (conhost, hidden) and a helper's CTRL_C_EVENT instead of a pseudo
    console's \\x03 (CC-7)."""
    result = case(console, "c_busy_classic", "busy", [("helper", wc.CTRL_C_EVENT)], conpty=False)
    assert_helper_sent(result)
    assert_cancelled_before_the_prompt(result)


# --- Ctrl+Break: the measured, declared form ----------------------------------------------------


def second_run(console):
    """After the brk_sleep prompt the orphaned kit still holds the project's run lock: start a
    second run then and see how long it waits (CC-12). An observation."""
    analytics = console.installs["3.12"]
    rel = wc.write_script(console.project, "second_after_break", "quick")

    def started_after_the_prompt(session, seen):
        alive = [n for p, n in session.names(session.job_pids()).items() if p != session.pid]
        t = time.time()
        # In a job of its own, in a hidden console of its own: whatever it starts ends with the
        # session, and its wait is bounded (Codex R2).
        cmdline = subprocess.list2cmdline([analytics, "run", rel, "--title", "콘솔 second"])
        with wc.Session(cmdline, str(console.project), console.env, conpty=False) as second:
            finished = wc.wait_until(second.root_ended, 120)
            rc = second.exit_code()
        record = wc.record_of(console.project, rel)
        return {"processes_of_the_first_run_alive": alive, "rc": rc, "finished": finished,
                "seconds": round(time.time() - t, 3),
                "left_after_close": second.left_after_close,
                "record": record and {k: record.get(k) for k in ("status", "started_at",
                                                                  "ended_at")}}

    return started_after_the_prompt


@pytest.mark.parametrize(("name", "script", "python", "conpty"), [
    ("brk_sleep", "sleep", "3.12", True), ("brk_sleep", "sleep", "3.11", True),
    ("brk_busy", "busy", "3.12", True), ("brk_busy_classic", "busy", "3.12", False)])
def test_ctrl_break_ends_uv_at_once_and_the_kit_records_cancelled_later(console, name, script,
                                                                        python, conpty):
    """CONTRACTS G's Ctrl+Break, measured: uv ends at once with 0xC000013A and the launcher returns
    that before the kit's record; the kit records `cancelled` later (a sleeping kit when its sleep
    ends) and nothing is left once it has. `running` at the prompt is a gate only in the sleep row:
    a busy kit can finish first (Codex 5), so there it is an observation, as is the order of the
    result line and the prompt."""
    lock = name == "brk_sleep" and python == "3.12"
    result = case(console, name, script, [("helper", wc.CTRL_BREAK_EVENT)], python, conpty=conpty,
                  after_prompt=second_run(console) if lock else None)
    if lock:
        second = (result.get("after_prompt") or {}).get("record") or {}
        first_ended = (result.get("record_times") or [None, None])[1]
        result["run_lock"] = {
            "second_started_after_the_first_ended": bool(
                second.get("started_at") and first_ended
                and datetime.fromisoformat(second["started_at"])
                >= datetime.fromisoformat(first_ended)),
            "first_ended_at": first_ended, "second": second}
        record_observation(f"console/{name}_py312", result)
        assert (result.get("after_prompt") or {}).get("left_after_close") == 0, result
    assert_helper_sent(result)
    prompt = result["prompt"]
    assert prompt and prompt["rc"] == wc.STATUS_CONTROL_C_EXIT, result
    if script == "sleep":
        assert prompt["status_at_prompt"] == "running", result
    assert result["record_status"] == "cancelled", result
    assert result["left_over_after_kit"] == [], result
    assert result["left_after_close"] == 0, result


# --- observations -------------------------------------------------------------------------------


def test_observe_two_ctrl_c_in_one_write(console):
    """\\x03\\x03 in one write: how many the shell received, and how the run ended (CC-3, CC-11)."""
    observe(console, "c_double_same_write", "busy", [b"\x03\x03"])


def test_observe_the_win32_input_mode_ctrl_break(console):
    """The win32-input-mode key records of Ctrl+Break typed into the pseudo console, an input
    Windows does not document."""
    observe(console, "brk_w32im", "busy", [wc.CTRL_BREAK_W32IM])


@pytest.mark.parametrize("shell", ["cmd", "pwsh"])
def test_observe_a_command_typed_into_a_shell(console, shell):
    """`analytics run` typed into `cmd /d /k` and `pwsh -NoProfile` in a pseudo console, then
    \\x03: the shell's own handling of Ctrl+C around a native command (CC-2)."""
    analytics = console.installs["3.12"]
    name = f"typed_{shell}"
    rel = f"scripts/console_{name}.py"
    if shell == "cmd":
        argv = [os.environ.get("ComSpec") or r"C:\Windows\System32\cmd.exe", "/d", "/k"]
        command = f'"{analytics}" run {rel} --title typed-cmd'
        rc_line, prompt_text = "echo __RC__%ERRORLEVEL%__", ">"
    else:
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            pytest.skip("pwsh is not on PATH")
        argv = [pwsh, "-NoProfile", "-NoLogo"]
        command = f"& '{analytics}' run {rel} --title typed-pwsh"
        rc_line, prompt_text = 'Write-Output "__RC__${LASTEXITCODE}__"', "PS "
    result = wc.typed_case(name, argv, command, rc_line, prompt_text=prompt_text,
                           project=console.project, scratch=console.scratch, env=console.env)
    record_observation(f"console/{name}", result)
    assert result.get("left_after_close") == 0, result


@pytest.mark.parametrize("codepage", [437, 949])
def test_observe_pwsh_capturing_status(console, codepage):
    """pwsh capturing `analytics status` into a variable decodes it with [Console]::OutputEncoding,
    which follows the console's code page: is the Korean title intact at 437 and at 949 (CC-4)?"""
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is not on PATH")
    captured = Path(console.scratch) / f"pwsh-status-{codepage}.txt"
    captured.parent.mkdir(parents=True, exist_ok=True)
    captured.unlink(missing_ok=True)
    command = (f"$o = & '{console.installs['3.12']}' status; $c = $LASTEXITCODE; "
               "$e = [Console]::OutputEncoding.CodePage; $v = $PSVersionTable.PSVersion; "
               f"[IO.File]::WriteAllLines('{captured}', "
               "[string[]](@('cp=' + $e, 'rc=' + $c, 'pwsh=' + $v) + $o))")
    name = f"pwsh_status_{codepage}"
    try:
        result = wc.run_case(name, None, [], analytics=console.installs["3.12"],
                             project=console.project, scratch=console.scratch, env=console.env,
                             codepage=codepage,
                             argv=[pwsh, "-NoProfile", "-NonInteractive", "-Command", command])
    except wc.CaseError as exc:
        result = exc.result
    lines = (captured.read_text(encoding="utf-8", errors="replace").splitlines()
             if captured.is_file() else None)
    result.update({"captured": lines and lines[:40],
                   "title_intact": bool(lines and any(TITLE in line for line in lines))})
    record_observation(f"console/{name}", result)
    assert result.get("left_after_close") == 0, result


# --- the console closed, and the harness's own failure path ------------------------------------


def test_closing_the_console_ends_the_run_cancelled_or_incomplete(console):
    """ClosePseudoConsole, as closing the window does: Windows sends CTRL_CLOSE_EVENT to every
    process attached and ends each once its handler returns, so the kit may or may not get to write
    its record. Which of the two the record ends as is measured (the observation says), not promised
    (CC-2); whether the kit's handler ran is not observed. A person closing a window is not run."""
    result = case(console, "close_busy", "busy", [("close",)])
    assert result["record_status"] in ("cancelled", "incomplete"), result
    assert result["left_over_after_kit"] == [], result
    assert result["left_after_close"] == 0, result


def test_a_case_that_fails_half_way_leaves_no_process_behind(console):
    """Codex 1: a case that fails while the run is going ends every process of its job, the kit
    included, so a later case never meets a leftover. Last in the module: the killed run's record
    stays `incomplete`."""
    rel = wc.write_script(console.project, "fails_half_way", "long")
    cmdline, paths = wc.shell_command(console.scratch, "fails_half_way",
                                      [console.installs["3.12"], "run", rel, "--title", "콘솔 x"],
                                      script_rel=rel)
    env = {**console.env, "PROBE_READY": str(paths["ready"])}
    handles, names = [], {}
    try:
        with pytest.raises(RuntimeError, match="on purpose"):
            with wc.Session(cmdline, str(console.project), env) as session:
                assert wc.wait_until(paths["ready"].exists, 60), session.text()[-2000:]
                pids = session.job_pids()
                names = session.names(pids)
                handles = wc.open_processes(pids)
                raise RuntimeError("failing on purpose, with the run going")
        record_observation("console/fails_half_way", {"names": names,
                                                       "left_after_close": session.left_after_close})
        assert {"analytics.exe", "uv.exe"} <= {(n or "").lower() for n in names.values()}, names
        assert session.left_after_close == 0
        assert len(handles) >= 4 and all(wc.ended(h, 5) for h in handles), names
    finally:
        wc.close_handles(handles)
    assert wc.record_of(console.project, rel)["status"] == "incomplete"

