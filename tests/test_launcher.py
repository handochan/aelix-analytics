"""The `lab` command line of the distribution (src/aelix_analytics/cli.py) against bin/lab's contract.

Both run a stand-in for uv that records how it was called, so what reaches the kit — argv,
environment, working directory — is compared without running the kit. On POSIX every case also runs
through bin/lab, which is the other implementation of the same contract (ADR-0002 decision 6).
tests/test_packaging.py runs the launcher as the installed console script.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from aelix_analytics import cli

PACK = Path(__file__).resolve().parents[1]
KIT = PACK / "kit"
BIN_LAB = PACK / "bin" / "lab"
POSIX = os.name != "nt"

# The stand-in for uv. It appends {argv, cwd, env} to $FAKE_UV_LOG and then does what $FAKE_UV_DO says
# for the call it recognises: "init" (`run --no-project ... labkit init DIR`, which it makes the
# directory for, as the kit would), "sync", "sync-offline", or the kit command after `--project P`
# (`--text status` counts as "status"). A behaviour may set: exit, stdout/stderr (hex bytes),
# kill_self (a signal number), wait (seconds to wait for a signal, writing $FAKE_UV_LOG.ready first
# and counting signals into $FAKE_UV_LOG.signals), or hold (the same, but it stays the whole time).
FAKE_UV = r'''
import json, os, signal, sys, time
from pathlib import Path

argv = sys.argv[1:]
log = Path(os.environ["FAKE_UV_LOG"])
keys = ("PYTHONPATH", "MPLBACKEND", "PYTHONDONTWRITEBYTECODE", "LAB_ACTOR", "VIRTUAL_ENV")
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": argv, "argv0": os.path.basename(sys.argv[0]), "cwd": os.getcwd(),
                         "sigint_ignored": signal.getsignal(signal.SIGINT) is signal.SIG_IGN,
                         "env": {k: os.environ.get(k) for k in keys}}) + "\n")
if argv[:1] == ["sync"]:
    key = "sync-offline" if "--offline" in argv else "sync"
elif "--no-project" in argv:
    key = "init"
    Path(argv[argv.index("init") + 1]).mkdir(parents=True, exist_ok=True)
else:
    rest = argv[argv.index("labkit") + 3:]
    key = rest[1] if rest[:1] == ["--text"] else rest[0]
do = json.loads(os.environ.get("FAKE_UV_DO") or "{}").get(key, {})
got = []
def note(signum, frame):
    got.append(signum)
if do.get("wait") or do.get("hold"):
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), note)
    Path(str(log) + ".ready").write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + (do.get("wait") or do.get("hold"))
    while (do.get("hold") or not got) and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.3)  # a second signal, if one comes, arrives now
    Path(str(log) + ".signals").write_text(json.dumps(got), encoding="utf-8")
if do.get("stdout"):
    sys.stdout.buffer.write(bytes.fromhex(do["stdout"]))
    sys.stdout.flush()
if do.get("stderr"):
    sys.stderr.buffer.write(bytes.fromhex(do["stderr"]))
    sys.stderr.flush()
if do.get("kill_self"):
    os.kill(os.getpid(), do["kill_self"])
    time.sleep(5)
sys.exit(do.get("exit", 0))
'''

# The launcher as a checkout test runs it: this checkout's kit, and uv as the stand-in.
RUN_LAUNCHER = (
    "import json, os, sys; from pathlib import Path; from aelix_analytics import cli; "
    "uv = json.loads(os.environ['LAUNCHER_UV']) if os.environ.get('LAUNCHER_UV') else None; "
    f"sys.exit(cli.run(sys.argv[1:], kit=Path({str(KIT)!r}), uv=uv))"
)


@pytest.fixture
def fake(tmp_path):
    uv = tmp_path / "fake-uv"
    uv.write_text(f"#!{sys.executable}\n{FAKE_UV}", encoding="utf-8")
    uv.chmod(0o755)
    return {"uv": uv, "log": tmp_path / "uv-calls.jsonl", "tmp": tmp_path}


def environment(fake, do=None, **extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("LAB_PROJECT", "LAB_ACTOR", "AELIX_LAB_UV", "PYTHONPATH")}
    env.update({"FAKE_UV_LOG": str(fake["log"]), "FAKE_UV_DO": json.dumps(do or {}),
                "VIRTUAL_ENV": str(fake["tmp"] / "some-venv")})
    if POSIX:
        env["AELIX_LAB_UV"] = str(fake["uv"])
    env.update(extra)
    return env


def launcher_argv(*args) -> list[str]:
    return [sys.executable, "-c", RUN_LAUNCHER, *args]


def launch(fake, *args, cwd, do=None, which="launcher", **extra) -> subprocess.CompletedProcess:
    """Run `lab ARGS` through the launcher or bin/lab, with bytes for stdout and stderr."""
    env = environment(fake, do, **extra)
    if which == "bin/lab":
        argv = ["sh", str(BIN_LAB), *args]
    else:
        argv = launcher_argv(*args)
        env["PYTHONPATH"] = str(PACK / "src")
        if not POSIX:  # the stand-in is a script, and Windows starts only an .exe by name
            env["LAUNCHER_UV"] = json.dumps([sys.executable, str(fake["uv"])])
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=120)


def calls(fake) -> list[dict]:
    if not fake["log"].exists():
        return []
    return [json.loads(line) for line in fake["log"].read_text(encoding="utf-8").splitlines()]


def uv_args(call) -> list[str]:
    """A call's argv as uv received it, without the stand-in's own path."""
    return call["argv"]


def project_dir(tmp_path, name="proj") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "lab.json").write_text("{}", encoding="utf-8")
    return root.resolve()


WHICH = ["launcher", pytest.param("bin/lab", marks=pytest.mark.skipif(
    not POSIX, reason="bin/lab is a POSIX shell script"))]


# --- usage ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("args", [["--help"], ["-h"], ["help"]])
def test_help_is_the_usage_on_stdout(fake, tmp_path, which, args):
    done = launch(fake, *args, cwd=tmp_path, which=which)
    assert done.returncode == 0, done.stderr
    assert done.stdout.decode("utf-8") == cli.USAGE
    assert not calls(fake)


@pytest.mark.skipif(not POSIX, reason="bin/lab is a POSIX shell script")
def test_the_usage_is_bin_labs_byte_for_byte(fake, tmp_path):
    ours = launch(fake, "--help", cwd=tmp_path)
    theirs = launch(fake, "--help", cwd=tmp_path, which="bin/lab")
    assert ours.stdout == theirs.stdout


@pytest.mark.parametrize("which", WHICH)
def test_an_unknown_command_prints_the_usage_on_stderr_and_exits_2(fake, tmp_path, which):
    done = launch(fake, "frobnicate", cwd=tmp_path, which=which)
    assert done.returncode == 2
    assert done.stdout == b""
    assert done.stderr.decode("utf-8") == cli.USAGE


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("command", ["init", "demo"])
def test_init_and_demo_need_a_directory(fake, tmp_path, which, command):
    done = launch(fake, command, cwd=tmp_path, which=which)
    assert done.returncode == 2
    assert f"usage: lab {command} DIR" in done.stderr.decode("utf-8")
    assert not calls(fake)


# --- what reaches uv and the kit -----------------------------------------------------------------------


def kit_call(project: Path, *args) -> list[str]:
    return ["run", "--project", str(project), "--quiet", "python", "-m", "labkit",
            "--project", str(project), *args]


CASES = [
    (["status"], ["--text", "status"]),
    ([], ["--text", "status"]),
    (["context"], ["--text", "context"]),
    (["run", "scripts/a.py", "--title", "한글 제목 & \"따옴표\""],
     ["run", "scripts/a.py", "--title", "한글 제목 & \"따옴표\""]),
    (["import", "data.csv", "--name", "sales"], ["import", "data.csv", "--name", "sales"]),
    (["query", "SELECT 1 AS x"], ["query", "SELECT 1 AS x"]),
    (["note", "finding", "text"], ["note", "finding", "text"]),
    (["report"], ["report"]),
    (["report", "--if-changed"], ["report", "--if-changed"]),
]


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize(("args", "expected"), CASES)
def test_each_command_reaches_the_kit_in_the_project_environment(fake, tmp_path, which, args,
                                                                 expected):
    project = project_dir(tmp_path)
    (project / "scripts").mkdir()
    done = launch(fake, *args, cwd=project / "scripts", which=which)
    assert done.returncode == 0, done.stderr
    [call] = calls(fake)
    assert uv_args(call) == kit_call(project, *expected)
    assert call["env"] == {"PYTHONPATH": str(KIT), "MPLBACKEND": "Agg",
                           "PYTHONDONTWRITEBYTECODE": "1", "LAB_ACTOR": "human",
                           "VIRTUAL_ENV": None}


@pytest.mark.parametrize("which", WHICH)
def test_lab_actor_and_lab_project_are_passed_as_they_are(fake, tmp_path, which):
    elsewhere = str(tmp_path / "not-a-project")  # not checked: the kit reports it
    done = launch(fake, "status", cwd=tmp_path, which=which, LAB_ACTOR="someone",
                  LAB_PROJECT=elsewhere)
    assert done.returncode == 0, done.stderr
    [call] = calls(fake)
    assert uv_args(call) == kit_call(Path(elsewhere), "--text", "status")
    assert call["env"]["LAB_ACTOR"] == "someone"


@pytest.mark.parametrize("which", WHICH)
def test_no_project_is_a_message_and_exit_1(fake, tmp_path, which):
    outside = tmp_path / "nowhere"
    outside.mkdir()
    done = launch(fake, "status", cwd=outside, which=which)
    assert done.returncode == 1
    assert "no lab.json in" in done.stderr.decode("utf-8")
    assert not calls(fake)


@pytest.mark.parametrize("which", WHICH)
def test_output_passes_through_byte_for_byte(fake, tmp_path, which):
    project = project_dir(tmp_path)
    out = b"\xff\xfe\xc3(\r\n\xea\xb0\x80\x00 last line without a newline"
    err = b"err \xed\x95\x9c \xff"
    done = launch(fake, "query", "SELECT 1", cwd=project, which=which,
                  do={"query": {"stdout": out.hex(), "stderr": err.hex()}})
    assert done.stdout == out
    assert done.stderr == err


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("code", [0, 1, 2, 3, 130])
def test_the_exit_status_is_the_kits(fake, tmp_path, which, code):
    project = project_dir(tmp_path)
    done = launch(fake, "report", cwd=project, which=which, do={"report": {"exit": code}})
    assert done.returncode == code


@pytest.mark.skipif(not POSIX, reason="a POSIX signal")
@pytest.mark.parametrize("which", WHICH)
def test_a_kit_ended_by_a_signal_is_128_plus_its_number(fake, tmp_path, which):
    project = project_dir(tmp_path)
    done = launch(fake, "report", cwd=project, which=which,
                  do={"report": {"kill_self": int(signal.SIGKILL)}})
    assert done.returncode == 128 + signal.SIGKILL


def test_a_missing_uv_is_a_message_and_exit_127(fake, tmp_path):
    project = project_dir(tmp_path)
    env = environment(fake, AELIX_LAB_UV=str(tmp_path / "no" / "uv"))
    env["PYTHONPATH"] = str(PACK / "src")
    done = subprocess.run(launcher_argv("status"), cwd=project, env=env, capture_output=True,
                          timeout=60)
    assert done.returncode == 127
    assert "uv not found" in done.stderr.decode("utf-8")


@pytest.mark.skipif(not POSIX, reason="bytes that are not UTF-8 in the environment are POSIX's")
def test_a_value_that_is_not_utf8_is_printed_as_its_bytes(fake, tmp_path):
    """Review of 7cfe851, launcher-6: such a value ended the launcher's own message in a
    UnicodeEncodeError traceback; bin/lab prints the bytes."""
    project = project_dir(tmp_path)
    env = {os.fsencode(k): os.fsencode(v) for k, v in environment(fake).items()}
    env[b"PYTHONPATH"] = os.fsencode(PACK / "src")
    env[b"AELIX_LAB_UV"] = b"/nowhere/caf\xe9"
    done = subprocess.run(launcher_argv("status"), cwd=project, env=env, capture_output=True,
                          timeout=60)
    assert done.returncode == 127, done.stderr
    assert b"uv not found (/nowhere/caf\xe9)" in done.stderr
    assert b"Traceback" not in done.stderr


# --- init and demo --------------------------------------------------------------------------------


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("command", ["init", "demo"])
def test_init_and_demo_create_sync_then_build_or_fill(fake, tmp_path, which, command):
    work = tmp_path / "work"
    work.mkdir()
    done = launch(fake, command, "new", "--title", "제목", cwd=work, which=which,
                  do={"report": {"stdout": b"REPORT-JSON\n".hex()},
                      "demo": {"stdout": b"DEMO-JSON\n".hex()}})
    assert done.returncode == 0, done.stderr
    project = (work / "new").resolve()
    made = calls(fake)
    assert [uv_args(c) for c in made] == [
        ["run", "--no-project", "--python", "3.12", "--quiet", "python", "-m", "labkit", "init",
         "new", "--title", "제목"],
        ["sync", "--project", str(project), "--quiet"],
        kit_call(project, "demo" if command == "demo" else "report"),
    ]
    assert made[0]["cwd"] == str(work.resolve())
    # init shows the kit's JSON line and the report's is discarded; demo shows demo's.
    assert done.stdout == (b"DEMO-JSON\n" if command == "demo" else b"")
    stderr = done.stderr.decode("utf-8")
    assert "lab: syncing the project environment" in stderr
    assert f"lab: report at {project / 'report' / 'index.html'}" in stderr


@pytest.mark.parametrize("which", WHICH)
def test_init_falls_back_to_an_offline_sync(fake, tmp_path, which):
    done = launch(fake, "init", "new", cwd=tmp_path, which=which, do={"sync": {"exit": 1}})
    assert done.returncode == 0, done.stderr
    assert [uv_args(c)[0] for c in calls(fake)] == ["run", "sync", "sync", "run"]
    assert "--offline" in uv_args(calls(fake)[2])


@pytest.mark.parametrize("which", WHICH)
def test_init_stops_at_the_first_failing_step(fake, tmp_path, which):
    done = launch(fake, "init", "new", cwd=tmp_path, which=which,
                  do={"sync": {"exit": 1}, "sync-offline": {"exit": 4}})
    assert done.returncode == 4
    assert len(calls(fake)) == 3
    assert "lab: report at" not in done.stderr.decode("utf-8")


# --- open -----------------------------------------------------------------------------------------


@pytest.mark.skipif(not POSIX, reason="os.startfile would open a real program on Windows")
@pytest.mark.parametrize("built", [True, False])
def test_open_builds_a_missing_report_then_hands_it_to_the_opener(fake, tmp_path, built):
    project = project_dir(tmp_path)
    if built:
        (project / "report").mkdir()
        (project / "report" / "index.html").write_text("<html>", encoding="utf-8")
    opened = tmp_path / "opened.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("open", "xdg-open"):
        opener = bin_dir / name
        opener.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{opened}'\nexit 5\n",
                          encoding="utf-8")
        opener.chmod(0o755)
    done = launch(fake, "open", cwd=project, do={"report": {"stdout": b"REPORT\n".hex()}},
                  PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert done.returncode == 5
    assert opened.read_text(encoding="utf-8") == f"{project / 'report' / 'index.html'}\n"
    assert [uv_args(c) for c in calls(fake)] == ([] if built else [kit_call(project, "report")])
    assert done.stdout == b""


@pytest.mark.skipif(not POSIX, reason="os.startfile would open a real program on Windows")
@pytest.mark.parametrize("which", WHICH)
def test_open_needs_no_uv_for_a_built_report(fake, tmp_path, which):
    """Review of 7cfe851, launcher-1: bin/lab runs uv only to build a missing report, and the
    launcher looked it up first and exited 127."""
    project = project_dir(tmp_path)
    (project / "report").mkdir()
    (project / "report" / "index.html").write_text("<html>", encoding="utf-8")
    opened = tmp_path / "opened.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("open", "xdg-open"):
        opener = bin_dir / name
        opener.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{opened}'\n", encoding="utf-8")
        opener.chmod(0o755)
    done = launch(fake, "open", cwd=project, which=which, AELIX_LAB_UV=str(tmp_path / "no-uv"),
                  PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert done.returncode == 0, done.stderr
    assert opened.read_text(encoding="utf-8") == f"{project / 'report' / 'index.html'}\n"


@pytest.mark.parametrize(("name", "status"), [("SIGINT", 130), ("SIGTERM", 143)])
def test_open_starts_no_opener_once_a_signal_has_arrived(tmp_path, monkeypatch, name, status):
    """Review of 7cfe851, R1: the opener started after a signal that arrived once the report was
    there, which no other step does."""
    project = project_dir(tmp_path)
    (project / "report").mkdir()
    (project / "report" / "index.html").write_text("<html>", encoding="utf-8")
    opened = []

    class Processes:
        def find_program(self, program):
            return program

        def open_document(self, path, *, wait):
            opened.append(path)
            return 0

    def detect():
        signal.raise_signal(getattr(signal, name))  # arrives before the opener starts
        return SimpleNamespace(processes=Processes())

    monkeypatch.setattr(cli, "_load_platform", lambda kit: SimpleNamespace(detect=detect))
    monkeypatch.setenv("LAB_PROJECT", str(project))
    monkeypatch.delenv("AELIX_LAB_UV", raising=False)
    assert cli.run(["open"], kit=KIT) == status
    assert opened == []


# --- signals --------------------------------------------------------------------------------------


def start_in_its_own_group(fake, project, do):
    env = environment(fake, do)
    env["PYTHONPATH"] = str(PACK / "src")
    proc = subprocess.Popen(launcher_argv("run", "scripts/a.py", "--title", "t"), cwd=project,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    ready = Path(str(fake["log"]) + ".ready")
    deadline = time.monotonic() + 60
    while not ready.exists():
        assert proc.poll() is None, proc.communicate()
        assert time.monotonic() < deadline, "the stand-in never started"
        time.sleep(0.02)
    return proc


def signals_the_kit_got(fake) -> list[int]:
    return json.loads(Path(str(fake["log"]) + ".signals").read_text(encoding="utf-8"))


@pytest.mark.skipif(not POSIX, reason="a terminal's process group; Windows consoles are PR-3's")
def test_ctrl_c_reaches_the_kit_once_and_the_launcher_waits_for_its_record(fake, tmp_path):
    """A terminal sends SIGINT to the whole foreground group. The kit records the run as cancelled
    and exits 3 (CONTRACTS D1); the launcher must neither die first nor pass the signal on again."""
    project = project_dir(tmp_path)
    do = {"run": {"wait": 30, "exit": 3,
                  "stdout": b'\n{"ok": true, "run": {"status": "cancelled"}}\n'.hex()}}
    proc = start_in_its_own_group(fake, project, do)
    os.killpg(proc.pid, signal.SIGINT)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 3, err
    assert out.splitlines()[-1] == b'{"ok": true, "run": {"status": "cancelled"}}'
    assert b"Traceback" not in err
    assert signals_the_kit_got(fake) == [signal.SIGINT]


@pytest.mark.skipif(not POSIX, reason="SIGTERM from outside a process is POSIX")
def test_sigterm_to_the_launcher_alone_is_passed_on_to_the_kit(fake, tmp_path):
    project = project_dir(tmp_path)
    do = {"run": {"wait": 30, "exit": 3}}
    proc = start_in_its_own_group(fake, project, do)
    os.kill(proc.pid, signal.SIGTERM)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 3, err
    assert signals_the_kit_got(fake) == [signal.SIGTERM]


def test_no_child_starts_once_ctrl_c_has_arrived(monkeypatch):
    children = cli._Children()
    children.stopped = 130

    def refuse(*args, **kwargs):
        raise AssertionError("a child was started after Ctrl+C")

    monkeypatch.setattr(cli.subprocess, "Popen", refuse)
    assert children.run(["anything"], env={}) == 130


@pytest.mark.skipif(not POSIX, reason="signal dispositions as POSIX inherits them")
def test_an_ignored_sigint_stays_ignored_and_a_handled_one_is_given_back():
    before = signal.getsignal(signal.SIGINT)
    with cli._Children():
        assert signal.getsignal(signal.SIGINT) != before
    assert signal.getsignal(signal.SIGINT) == before
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        with cli._Children():
            assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGINT, before)


@pytest.mark.parametrize(("returncode", "status"), [
    (0, 0), (1, 1), (3, 3), (130, 130), (255, 255),
    (-2, 130), (-9, 137), (-15, 143),
    (0xC000013A, 0xC000013A - 2**32), (0xFFFFFFFF, -1), (2**31, -(2**31)), (2**31 - 1, 2**31 - 1),
])
def test_exit_status(returncode, status):
    assert cli.exit_status(returncode) == status


@pytest.mark.skipif(not POSIX, reason="SIGTERM from outside a process is POSIX")
def test_sigterm_lets_a_step_finish_and_starts_no_other(fake, tmp_path):
    """Reviews of design revision 1: SIGTERM between init's steps was swallowed and the next step
    started (Codex F4); passed on, it killed init or an import part way and lost its record
    (launcher-1, launcher-2). Only a kit `run` records SIGTERM as a cancel."""
    env = environment(fake, {"init": {"hold": 1.0, "exit": 0}})
    env["PYTHONPATH"] = str(PACK / "src")
    proc = subprocess.Popen(launcher_argv("init", "new"), cwd=tmp_path, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    ready = Path(str(fake["log"]) + ".ready")
    deadline = time.monotonic() + 60
    while not ready.exists():
        assert proc.poll() is None and time.monotonic() < deadline, proc.communicate()
        time.sleep(0.02)
    os.kill(proc.pid, signal.SIGTERM)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 128 + signal.SIGTERM, err
    assert [uv_args(c)[0] for c in calls(fake)] == ["run"]  # no sync, no report
    assert signals_the_kit_got(fake) == [], "init was left to finish"


@pytest.mark.skipif(not POSIX, reason="SIGTERM from outside a process is POSIX")
def test_sigterm_during_an_import_leaves_the_import_to_finish(fake, tmp_path):
    project = project_dir(tmp_path)
    env = environment(fake, {"import": {"hold": 1.0, "exit": 0,
                                        "stdout": b'{"ok": true}\n'.hex()}})
    env["PYTHONPATH"] = str(PACK / "src")
    proc = subprocess.Popen(launcher_argv("import", "data.csv"), cwd=project, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    ready = Path(str(fake["log"]) + ".ready")
    deadline = time.monotonic() + 60
    while not ready.exists():
        assert proc.poll() is None and time.monotonic() < deadline, proc.communicate()
        time.sleep(0.02)
    os.kill(proc.pid, signal.SIGTERM)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 128 + signal.SIGTERM, err
    assert out.splitlines()[-1] == b'{"ok": true}', "the import finished and answered"
    assert signals_the_kit_got(fake) == []


@pytest.mark.skipif(not POSIX, reason="a POSIX signal sent to one process")
def test_a_ctrl_c_while_a_child_starts_still_reaches_it(fake, tmp_path, monkeypatch):
    """Review of design revision 1, launcher-3: a Ctrl+C that lands after the check and before the
    child execs reaches neither the child nor the kit, and the step ran in full."""
    real_popen = subprocess.Popen

    def popen_hit_by_ctrl_c(*args, **kwargs):
        os.kill(os.getpid(), signal.SIGINT)  # the terminal's Ctrl+C, as the child is being made
        time.sleep(0.05)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(cli.subprocess, "Popen", popen_hit_by_ctrl_c)
    monkeypatch.setenv("FAKE_UV_LOG", str(fake["log"]))
    # Without the Ctrl+C the stand-in would run its whole 2 s and exit 3.
    monkeypatch.setenv("FAKE_UV_DO", json.dumps({"run": {"wait": 2, "exit": 3}}))
    with cli._Children() as children:
        argv = [sys.executable, str(fake["uv"]), *kit_call(tmp_path, "run", "a.py")]
        status = children.run(argv, env=dict(os.environ), cancellable=True)
    # It reached the child: here one that had not set a handler yet, as uv has none that early.
    assert status == 130


@pytest.mark.skipif(not POSIX, reason="a shell's background job")
@pytest.mark.parametrize("which", WHICH)
def test_a_background_job_keeps_sigint_ignored_for_the_kit(fake, tmp_path, which):
    """A non-interactive shell starts a background job with SIGINT ignored; the kit must inherit
    that, as it does under bin/lab (review of design revision 1, launcher-5)."""
    project = project_dir(tmp_path)
    env = environment(fake)
    if which == "bin/lab":
        command = f"sh '{BIN_LAB}' report"
    else:
        env["PYTHONPATH"] = str(PACK / "src")
        command = " ".join("'" + a.replace("'", "'\\''") + "'" for a in launcher_argv("report"))
    done = subprocess.run(["sh", "-c", f"{command} & wait"], cwd=project, env=env,
                          capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    [call] = calls(fake)
    assert call["sigint_ignored"] is True


@pytest.mark.skipif(not POSIX, reason="a symbolic link to a program")
def test_uv_is_started_by_the_name_it_was_found_by(fake, tmp_path):
    """Review of design revision 1, critic-1: a mise shim is a link to mise, which reads argv[0];
    resolving the link ran mise itself."""
    project = project_dir(tmp_path)
    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    (shim_dir / "uv").symlink_to(fake["uv"])
    for configured in (str(shim_dir / "uv"), "uv"):
        fake["log"].unlink(missing_ok=True)
        env = environment(fake, AELIX_LAB_UV=configured,
                          PATH=f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
        env["PYTHONPATH"] = str(PACK / "src")
        done = subprocess.run(launcher_argv("status"), cwd=project, env=env, capture_output=True,
                              timeout=60)
        assert done.returncode == 0, done.stderr
        assert calls(fake)[0]["argv0"] == "uv"
