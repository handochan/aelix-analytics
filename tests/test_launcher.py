"""The `analytics` command line of the distribution (src/aelix_analytics/cli.py) against bin/analytics's
contract.

Both run a stand-in for uv that records how it was called, so what reaches the kit — argv,
environment, working directory — is compared without running the kit. On POSIX every case also runs
through bin/analytics, which is the other implementation of the same contract (ADR-0002 decision 6); its
text is the same bytes (before kit 0.3.0 the script was bin/lab and its text differed by that name).
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
KIT = PACK / "src"
BIN = PACK / "bin" / "analytics"
POSIX = os.name != "nt"

# The stand-in for uv. It appends {argv, cwd, env} to $FAKE_UV_LOG and then does what $FAKE_UV_DO says
# for the call it recognises: "init" (`run --no-project ... aelix_analytics init DIR`, which it makes the
# directory for, as the kit would), "sync", "sync-offline", or the kit command, after `--project P`
# when there is one (`--text status` counts as "status", `migrate` is "migrate"); `run ... python -c CODE`
# is run by this interpreter (bin/analytics reads a migration's answer so). A behaviour may set:
# exit, stdout/stderr (hex bytes), kill_self (a signal number), wait (seconds to wait for a signal,
# writing $FAKE_UV_LOG.ready first and counting signals into $FAKE_UV_LOG.signals), hold (the same, but
# it stays the whole time), or exec (run the kit itself, `python -m aelix_analytics ...` with this
# interpreter and the environment uv was given, and exit with its status: the kit's answer, without
# an environment).
FAKE_UV = r'''
import json, os, signal, sys, time
from pathlib import Path

argv = sys.argv[1:]
log = Path(os.environ["FAKE_UV_LOG"])
keys = ("PYTHONPATH", "MPLBACKEND", "PYTHONDONTWRITEBYTECODE", "AELIX_ANALYTICS_ACTOR", "VIRTUAL_ENV")
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": argv, "argv0": os.path.basename(sys.argv[0]), "cwd": os.getcwd(),
                         "sigint_ignored": signal.getsignal(signal.SIGINT) is signal.SIG_IGN,
                         "env": {k: os.environ.get(k) for k in keys}}) + "\n")
if argv[:1] == ["sync"]:
    key = "sync-offline" if "--offline" in argv else "sync"
elif "aelix_analytics" not in argv and argv[argv.index("python") + 1:][:1] == ["-c"]:
    # `uv run ... python -c CODE` (bin/analytics reads the kit's answer so): Python itself runs it.
    import subprocess
    sys.exit(subprocess.call([sys.executable, *argv[argv.index("python") + 1:]]))
else:
    rest = argv[argv.index("aelix_analytics") + 1:]
    if rest[:1] == ["--project"]:
        rest = rest[2:]
    if rest[:1] == ["--text"]:
        rest = rest[1:]
    key = rest[0]
do = json.loads(os.environ.get("FAKE_UV_DO") or "{}").get(key, {})
if do.get("exec"):
    import subprocess
    sys.exit(subprocess.call([sys.executable, *argv[argv.index("python") + 1:]]))
if key == "init":
    Path(rest[1]).mkdir(parents=True, exist_ok=True)
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


# What a caller's environment may hold that a case must not inherit: the variables the command reads,
# and their names before kit 0.3.0, which it refuses (a developer's shell may still have them).
INHERITED = ("AELIX_ANALYTICS_PROJECT", "AELIX_ANALYTICS_ACTOR", "AELIX_ANALYTICS_UV", "PYTHONPATH",
             *cli.OLD_ENV)


def environment(fake, do=None, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in INHERITED}
    env.update({"FAKE_UV_LOG": str(fake["log"]), "FAKE_UV_DO": json.dumps(do or {}),
                "VIRTUAL_ENV": str(fake["tmp"] / "some-venv")})
    if POSIX:
        env["AELIX_ANALYTICS_UV"] = str(fake["uv"])
    env.update(extra)
    return env


def launcher_argv(*args) -> list[str]:
    return [sys.executable, "-c", RUN_LAUNCHER, *args]


def launch(fake, *args, cwd, do=None, which="launcher", **extra) -> subprocess.CompletedProcess:
    """Run ARGS through the launcher (`analytics`) or bin/analytics, with bytes for stdout and stderr."""
    env = environment(fake, do, **extra)
    if which == "bin/analytics":
        argv = ["sh", str(BIN), *args]
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


def project_dir(tmp_path, name="proj", marker="analytics.json") -> Path:
    """A directory with a project marker (`marker`): the launcher looks at its presence only and the
    kit judges what it holds. The new format's by default, whose page is workspace/index.html."""
    root = tmp_path / name
    root.mkdir()
    (root / marker).write_text("{}", encoding="utf-8")
    return root.resolve()


WHICH = ["launcher", pytest.param("bin/analytics", marks=pytest.mark.skipif(
    not POSIX, reason="bin/analytics is a POSIX shell script"))]

# The command's own name in each: one name since kit 0.3.0 (Issue #9), when bin/lab became
# bin/analytics.
NAME = {"launcher": "analytics", "bin/analytics": "analytics"}


# --- usage ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("args", [["--help"], ["-h"], ["help"]])
def test_help_is_the_usage_on_stdout(fake, tmp_path, which, args):
    done = launch(fake, *args, cwd=tmp_path, which=which)
    assert done.returncode == 0, done.stderr
    assert done.stdout.decode("utf-8") == cli.USAGE
    assert not calls(fake)


@pytest.mark.skipif(not POSIX, reason="bin/analytics is a POSIX shell script")
def test_the_usage_is_bin_analytics_byte_for_byte(fake, tmp_path):
    ours = launch(fake, "--help", cwd=tmp_path)
    theirs = launch(fake, "--help", cwd=tmp_path, which="bin/analytics")
    assert ours.stdout == theirs.stdout and b"analytics record KIND" in ours.stdout


@pytest.mark.parametrize("which", WHICH)
def test_an_unknown_command_prints_the_usage_on_stderr_and_exits_2(fake, tmp_path, which):
    done = launch(fake, "frobnicate", cwd=tmp_path, which=which)
    assert done.returncode == 2
    assert done.stdout == b""
    assert done.stderr.decode("utf-8") == cli.USAGE


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("old", ["note", "report"])
def test_the_old_subcommands_are_usage_errors(fake, tmp_path, which, old):
    """`note` and `report` became `record` and `render` in kit 0.3.0 (Issue #9): no alias."""
    project = project_dir(tmp_path)
    done = launch(fake, old, "finding", "x", cwd=project, which=which)
    assert (done.returncode, done.stdout, done.stderr.decode("utf-8")) == (2, b"", cli.USAGE)
    assert not calls(fake)


# The renamed variables and the words both commands say for them (ADR-0005 decision 7).
RENAMED = {
    "LAB_PROJECT": "analytics: LAB_PROJECT was renamed AELIX_ANALYTICS_PROJECT in kit 0.3.0 (the "
                   "analytics command and the aelix extension both read AELIX_ANALYTICS_PROJECT, "
                   "ahead of the working directory); set AELIX_ANALYTICS_PROJECT instead and unset "
                   "LAB_PROJECT\n",
    "LAB_ACTOR": "analytics: LAB_ACTOR was renamed AELIX_ANALYTICS_ACTOR in kit 0.3.0; set "
                 "AELIX_ANALYTICS_ACTOR instead and unset LAB_ACTOR\n",
    "AELIX_LAB_UV": "analytics: AELIX_LAB_UV was renamed AELIX_ANALYTICS_UV in kit 0.3.0; set "
                    "AELIX_ANALYTICS_UV instead and unset AELIX_LAB_UV\n",
}


def test_the_commands_refuse_the_old_names_the_kit_lists():
    from aelix_analytics import core

    assert cli.OLD_ENV == {old: core.RENAMED_ENV[old] for old in RENAMED}
    for old in RENAMED:
        assert RENAMED[old] == f"analytics: {core.renamed_env_message(old)}\n"
    shell = BIN.read_text(encoding="utf-8")
    assert "LAB_PROJECT=AELIX_ANALYTICS_PROJECT LAB_ACTOR=AELIX_ANALYTICS_ACTOR " \
           "AELIX_LAB_UV=AELIX_ANALYTICS_UV" in shell


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("old", list(RENAMED))
@pytest.mark.parametrize("command", [["status"], ["run", "scripts/a.py", "--title", "t"], ["open"],
                                     ["init", "new-project"]])
def test_an_old_variable_stops_the_command_before_uv(fake, tmp_path, which, old, command):
    """Set, even beside its new name, an old variable is an error that names the new one, before
    anything is started or written: no alias (ADR-0005 decision 7)."""
    project = project_dir(tmp_path)
    before = sorted(p.name for p in project.iterdir())
    new = cli.OLD_ENV[old]
    done = launch(fake, *command, cwd=project, which=which, **{old: "x", new: str(project)})
    assert (done.returncode, done.stdout, done.stderr.decode("utf-8")) == (2, b"", RENAMED[old])
    assert not calls(fake)
    assert sorted(p.name for p in project.iterdir()) == before


@pytest.mark.parametrize("which", WHICH)
def test_an_empty_old_variable_is_not_set(fake, tmp_path, which):
    """The old readers took an empty value as unset; so does the check."""
    project = project_dir(tmp_path)
    done = launch(fake, "status", cwd=project, which=which, LAB_PROJECT="", LAB_ACTOR="",
                  AELIX_LAB_UV="")
    assert done.returncode == 0, done.stderr
    [call] = calls(fake)
    assert uv_args(call) == kit_call(project, "--text", "status")


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("command", ["init", "demo"])
def test_init_and_demo_need_a_directory(fake, tmp_path, which, command):
    done = launch(fake, command, cwd=tmp_path, which=which)
    assert done.returncode == 2
    assert f"usage: {NAME[which]} {command} DIR" in done.stderr.decode("utf-8")
    assert not calls(fake)


# --- what reaches uv and the kit -----------------------------------------------------------------------


def kit_call(project: Path, *args) -> list[str]:
    return ["run", "--project", str(project), "--quiet", "python", "-m", "aelix_analytics",
            "--project", str(project), *args]


CASES = [
    (["status"], ["--text", "status"]),
    ([], ["--text", "status"]),
    (["context"], ["--text", "context"]),
    (["run", "scripts/a.py", "--title", "한글 제목 & \"따옴표\""],
     ["run", "scripts/a.py", "--title", "한글 제목 & \"따옴표\""]),
    (["import", "data.csv", "--name", "sales"], ["import", "data.csv", "--name", "sales"]),
    (["query", "SELECT 1 AS x"], ["query", "SELECT 1 AS x"]),
    (["record", "finding", "text"], ["record", "finding", "text"]),
    (["render"], ["render"]),
    (["render", "--if-changed"], ["render", "--if-changed"]),
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
    # No actor of their own (design 4.2, OQ1): without AELIX_ANALYTICS_ACTOR the kit records `human`
    # as inferred, not declared.
    assert call["env"] == {"PYTHONPATH": str(KIT), "MPLBACKEND": "Agg",
                           "PYTHONDONTWRITEBYTECODE": "1", "AELIX_ANALYTICS_ACTOR": None,
                           "VIRTUAL_ENV": None}


@pytest.mark.parametrize("which", WHICH)
def test_the_actor_and_project_variables_are_passed_as_they_are(fake, tmp_path, which):
    elsewhere = str(tmp_path / "not-a-project")  # not checked: the kit reports it
    done = launch(fake, "status", cwd=tmp_path, which=which, AELIX_ANALYTICS_ACTOR="someone",
                  AELIX_ANALYTICS_PROJECT=elsewhere)
    assert done.returncode == 0, done.stderr
    [call] = calls(fake)
    assert uv_args(call) == kit_call(Path(elsewhere), "--text", "status")
    assert call["env"]["AELIX_ANALYTICS_ACTOR"] == "someone"


@pytest.mark.parametrize("which", WHICH)
def test_no_project_is_a_message_and_exit_1(fake, tmp_path, which):
    outside = tmp_path / "nowhere"
    outside.mkdir()
    done = launch(fake, "status", cwd=outside, which=which)
    assert done.returncode == 1
    assert "no analytics.json or lab.json in" in done.stderr.decode("utf-8")
    assert not calls(fake)


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize(("above", "below"), [("lab.json", "analytics.json"),
                                              ("analytics.json", "lab.json")])
def test_the_nearest_marker_of_either_name_is_the_project(fake, tmp_path, which, above, below):
    """Issue #13 PR 1 (design 4.3): the launcher and bin/analytics stop at the nearest directory with
    an analytics.json or a lab.json and hand it to the kit, which judges it; neither reads what the
    marker holds. A directory with an analytics.json below a lab.json project is that directory,
    never the project above (a kit before #13 wrote to the project above)."""
    outer = tmp_path / "outer"
    inner = outer / "inner"
    (inner / "scripts").mkdir(parents=True)
    (outer / above).write_text("{}", encoding="utf-8")
    (inner / below).write_text("not even JSON", encoding="utf-8")
    done = launch(fake, "record", "note", "x", cwd=inner / "scripts", which=which)
    assert done.returncode == 0, done.stderr
    assert launch(fake, "status", cwd=outer, which=which).returncode == 0
    first, second = calls(fake)
    assert uv_args(first) == kit_call(inner.resolve(), "record", "note", "x")
    assert uv_args(second) == kit_call(outer.resolve(), "--text", "status")


def odd_marker(path: Path, kind: str) -> None:
    """An entry of a marker's name that is no regular file: a FIFO, a link to itself, a link to
    nothing."""
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "loop":
        path.symlink_to(path.name)
    else:
        path.symlink_to("nowhere.json")


@pytest.mark.skipif(not POSIX, reason="a FIFO and the links are POSIX's here")
@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("kind", ["fifo", "loop", "dangling"])
def test_a_marker_that_is_no_regular_file_still_stops_the_search(fake, tmp_path, which, kind):
    """Review of PR 1: the launcher and bin/analytics stop at least where the kit's own search does.
    The kit counts an entry of a marker's name that is not a directory — a FIFO, a link that loops —
    and refuses it (marker_invalid), so neither command may pass it over for the project above. A
    link that leads nowhere stops them too; the kit, searching upward from there (`--project` names
    a start), passes it over as no marker (design 4.3)."""
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    (outer / "lab.json").write_text("{}", encoding="utf-8")
    odd_marker(inner / "analytics.json", kind)
    done = launch(fake, "record", "note", "x", cwd=inner, which=which)
    assert done.returncode == 0, done.stderr
    (call,) = calls(fake)
    assert uv_args(call) == kit_call(inner.resolve(), "record", "note", "x")


@pytest.mark.skipif(not POSIX, reason="os.startfile would open a real program on Windows")
@pytest.mark.parametrize("which", WHICH)
def test_open_refuses_a_directory_with_two_markers(fake, tmp_path, which):
    """Review of PR 1: with both markers the kit reads neither (marker_conflict, design 4.3), so
    `open` does not open a page already built there either. It looks at the markers' presence only,
    as the search does, and starts neither uv nor the opener."""
    project = project_dir(tmp_path)
    (project / "lab.json").write_text("{}", encoding="utf-8")
    (project / "workspace").mkdir()
    (project / "workspace" / "index.html").write_text("<html>", encoding="utf-8")
    opened = tmp_path / "opened.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("open", "xdg-open"):
        opener = bin_dir / name
        opener.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{opened}'\n", encoding="utf-8")
        opener.chmod(0o755)
    done = launch(fake, "open", cwd=project, which=which,
                  PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert done.returncode == 1
    assert done.stderr.decode("utf-8") == (
        f"analytics: {project} holds two project markers, analytics.json and lab.json, and the kit "
        "reads neither; analytics status says more\n")
    assert not opened.exists() and not calls(fake)


@pytest.mark.skipif(not POSIX, reason="the links are POSIX's here, and os.startfile would open a real "
                    "program on Windows")
@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize(("kind", "where"), [("dangling", "beside"), ("dangling", "below"),
                                             ("loop", "beside")])
def test_open_counts_a_marker_link_as_the_kit_does(fake, tmp_path, which, kind, where):
    """Fix check of PR 1: `open` opens a page without the kit, so it judges by the kit's own presence
    rule (writer._look), where a link that leads nowhere is no marker and one that loops is one. A
    lab.json project that also holds a dangling analytics.json is a lab.json project to the kit, and
    its page opens (both commands had refused it as two markers). A directory below the project whose
    only marker entry is such a link is passed over: `open` is about the project above (both
    commands had opened the directory's page, which does not exist) — a lab.json project without
    its page, which this kit does not build (PR 3: it says so and how to migrate, exit 1, no uv). A
    looping analytics.json beside the lab.json is a second marker (marker_conflict): refused,
    neither uv nor the opener."""
    project = project_dir(tmp_path, marker="lab.json")
    here = project / "inner" if where == "below" else project
    here.mkdir(exist_ok=True)
    odd_marker(here / "analytics.json", kind)
    if where == "beside":
        (project / "report").mkdir()
        (project / "report" / "index.html").write_text("<html>", encoding="utf-8")
    opened = tmp_path / "opened.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("open", "xdg-open"):
        opener = bin_dir / name
        opener.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{opened}'\n", encoding="utf-8")
        opener.chmod(0o755)
    done = launch(fake, "open", cwd=here, which=which,
                  PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    if kind == "loop":
        assert done.returncode == 1
        assert done.stderr.decode("utf-8") == (
            f"analytics: {project} holds two project markers, analytics.json and lab.json, and the kit "
            "reads neither; analytics status says more\n")
        assert not opened.exists() and not calls(fake)
        return
    if where == "below":
        assert done.returncode == 1
        assert done.stderr.decode("utf-8").startswith(
            f"analytics: {project} uses the older record format (lab.json) and has no page to open")
        assert not opened.exists() and not calls(fake)
        return
    assert done.returncode == 0, done.stderr
    assert opened.read_text(encoding="utf-8") == f"{project / 'report' / 'index.html'}\n"
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
    done = launch(fake, "render", cwd=project, which=which, do={"render": {"exit": code}})
    assert done.returncode == code


@pytest.mark.skipif(not POSIX, reason="a POSIX signal")
@pytest.mark.parametrize("which", WHICH)
def test_a_kit_ended_by_a_signal_is_128_plus_its_number(fake, tmp_path, which):
    project = project_dir(tmp_path)
    done = launch(fake, "render", cwd=project, which=which,
                  do={"render": {"kill_self": int(signal.SIGKILL)}})
    assert done.returncode == 128 + signal.SIGKILL


def test_a_missing_uv_is_a_message_and_exit_127(fake, tmp_path):
    project = project_dir(tmp_path)
    env = environment(fake, AELIX_ANALYTICS_UV=str(tmp_path / "no" / "uv"))
    env["PYTHONPATH"] = str(PACK / "src")
    done = subprocess.run(launcher_argv("status"), cwd=project, env=env, capture_output=True,
                          timeout=60)
    assert done.returncode == 127
    assert "uv not found" in done.stderr.decode("utf-8")


@pytest.mark.skipif(not POSIX, reason="bytes that are not UTF-8 in the environment are POSIX's")
def test_a_value_that_is_not_utf8_is_printed_as_its_bytes(fake, tmp_path):
    """Review of 7cfe851, launcher-6: such a value ended the launcher's own message in a
    UnicodeEncodeError traceback; bin/analytics prints the bytes. The rule holds where the filesystem
    encoding is UTF-8, which PYTHONUTF8 makes it under any locale."""
    project = project_dir(tmp_path)
    env = {os.fsencode(k): os.fsencode(v) for k, v in environment(fake).items()}
    env[b"PYTHONPATH"] = os.fsencode(PACK / "src")
    env[b"PYTHONUTF8"] = b"1"
    env[b"AELIX_ANALYTICS_UV"] = b"/nowhere/caf\xe9"
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
                  do={"render": {"stdout": b"REPORT-JSON\n".hex()},
                      "demo": {"stdout": b"DEMO-JSON\n".hex()}})
    assert done.returncode == 0, done.stderr
    project = (work / "new").resolve()
    made = calls(fake)
    assert [uv_args(c) for c in made] == [
        ["run", "--no-project", "--python", "3.12", "--quiet", "python", "-m", "aelix_analytics", "init",
         "new", "--title", "제목"],
        ["sync", "--project", str(project), "--quiet"],
        kit_call(project, "demo" if command == "demo" else "render"),
    ]
    assert made[0]["cwd"] == str(work.resolve())
    # init shows the kit's JSON line and the report's is discarded; demo shows demo's.
    assert done.stdout == (b"DEMO-JSON\n" if command == "demo" else b"")
    stderr = done.stderr.decode("utf-8")
    assert f"{NAME[which]}: syncing the project environment" in stderr
    assert f"{NAME[which]}: Workspace at {project / 'workspace' / 'index.html'}" in stderr
    assert f"({NAME[which]} open, from inside the project)" in stderr


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
    assert f"{NAME[which]}: Workspace at" not in done.stderr.decode("utf-8")


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("command", ["init", "demo"])
@pytest.mark.parametrize("marker", ["analytics.json", "lab.json"])
def test_init_and_demo_inside_a_project_are_refused_before_anything(fake, tmp_path, which, command,
                                                                     marker):
    """OQ9 through the launchers: the kit's own init (run here as uv would, the stand-in running
    it) refuses a directory inside a project — destination_invalid, inside_project — before it
    makes anything; the launcher stops there with the kit's answer and status: no sync, no render or
    demo, no directory."""
    project = project_dir(tmp_path, marker=marker)
    done = launch(fake, command, "inner/new", cwd=project, which=which, do={"init": {"exec": True}})
    assert done.returncode == 1, done.stderr
    answer = json.loads(done.stdout.decode("utf-8").strip().split("\n")[-1])
    assert answer["error"]["code"] == "destination_invalid", answer
    assert answer["error"]["details"] == {"reason": "inside_project", "project": str(project)}
    assert [uv_args(c)[:2] for c in calls(fake)] == [["run", "--no-project"]]
    assert sorted(p.name for p in project.iterdir()) == [marker]


# --- open -----------------------------------------------------------------------------------------


@pytest.mark.skipif(not POSIX, reason="os.startfile would open a real program on Windows")
@pytest.mark.parametrize("built", [True, False])
def test_open_builds_a_missing_report_then_hands_it_to_the_opener(fake, tmp_path, built):
    project = project_dir(tmp_path)
    if built:
        (project / "workspace").mkdir()
        (project / "workspace" / "index.html").write_text("<html>", encoding="utf-8")
    opened = tmp_path / "opened.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("open", "xdg-open"):
        opener = bin_dir / name
        opener.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{opened}'\nexit 5\n",
                          encoding="utf-8")
        opener.chmod(0o755)
    done = launch(fake, "open", cwd=project, do={"render": {"stdout": b"REPORT\n".hex()}},
                  PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert done.returncode == 5
    assert opened.read_text(encoding="utf-8") == f"{project / 'workspace' / 'index.html'}\n"
    assert [uv_args(c) for c in calls(fake)] == ([] if built else [kit_call(project, "render")])
    assert done.stdout == b""


@pytest.mark.skipif(not POSIX, reason="os.startfile would open a real program on Windows")
@pytest.mark.parametrize("which", WHICH)
def test_open_shows_a_lab_json_project_s_page_and_says_it_is_not_updated(fake, tmp_path, which):
    """design 7 (Issue #13 PR 3): a lab.json project is read only to this kit, so its page is
    report/index.html as an older kit built it — opened when it is there, with a line that says the
    kit no longer updates it and where to read how to migrate; uv is not started. An analytics.json
    project's page is workspace/index.html, and nothing is said about it."""
    project = project_dir(tmp_path, marker="lab.json")
    (project / "report").mkdir()
    (project / "report" / "index.html").write_text("<html>", encoding="utf-8")
    opened = tmp_path / "opened.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("open", "xdg-open"):
        opener = bin_dir / name
        opener.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{opened}'\n", encoding="utf-8")
        opener.chmod(0o755)
    done = launch(fake, "open", cwd=project, which=which, AELIX_ANALYTICS_UV=str(tmp_path / "no-uv"),
                  PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert done.returncode == 0, done.stderr
    assert opened.read_text(encoding="utf-8") == f"{project / 'report' / 'index.html'}\n"
    assert done.stderr.decode("utf-8") == (
        f"analytics: {project} uses the older record format (lab.json): the kit reads it and no longer "
        "updates this page; analytics status says how to migrate it\n")
    assert not calls(fake)


@pytest.mark.parametrize("which", WHICH)
def test_open_of_a_lab_json_project_without_its_page_says_how_to_migrate(fake, tmp_path, which):
    """design 7: this kit builds no page for a lab.json project (its report/ is in that template's
    .gitignore, so a clone has none). `open` says so and how to migrate it, exits 1, and starts
    neither uv nor an opener: no environment is made for a page that will not be built."""
    project = project_dir(tmp_path, marker="lab.json")
    done = launch(fake, "open", cwd=project, which=which, AELIX_ANALYTICS_UV=str(tmp_path / "no-uv"))
    assert done.returncode == 1 and done.stdout == b""
    assert done.stderr.decode("utf-8") == (
        f"analytics: {project} uses the older record format (lab.json) and has no page to open: the "
        "kit reads it and builds no page for it. Copy it into the current format with `analytics "
        "migrate --to <new empty folder>` run inside it (preview with --dry-run)\n")
    assert not calls(fake) and not (project / ".venv").exists()


@pytest.mark.parametrize("which", WHICH)
def test_open_shows_the_kit_s_answer_when_it_builds_no_page(fake, tmp_path, which):
    """A project the kit does not write to (writer_too_old, say) and that has no page: the render
    that would build one is refused, and its answer — the JSON line with the code and the hint — is
    shown as it came, with its exit status; no opener starts."""
    project = project_dir(tmp_path)
    refusal = b'{"ok": false, "error": {"code": "writer_too_old", "hint": "update the kit"}}\n'
    done = launch(fake, "open", cwd=project, which=which,
                  do={"render": {"exit": 1, "stdout": refusal.hex()}})
    assert (done.returncode, done.stdout) == (1, refusal), done.stderr
    assert [uv_args(c) for c in calls(fake)] == [kit_call(project, "render")]


@pytest.mark.skipif(not POSIX, reason="os.startfile would open a real program on Windows")
@pytest.mark.parametrize("which", WHICH)
def test_open_needs_no_uv_for_a_built_report(fake, tmp_path, which):
    """Review of 7cfe851, launcher-1: bin/analytics runs uv only to build a missing report, and the
    launcher looked it up first and exited 127."""
    project = project_dir(tmp_path)
    (project / "workspace").mkdir()
    (project / "workspace" / "index.html").write_text("<html>", encoding="utf-8")
    opened = tmp_path / "opened.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("open", "xdg-open"):
        opener = bin_dir / name
        opener.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{opened}'\n", encoding="utf-8")
        opener.chmod(0o755)
    done = launch(fake, "open", cwd=project, which=which, AELIX_ANALYTICS_UV=str(tmp_path / "no-uv"),
                  PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert done.returncode == 0, done.stderr
    assert opened.read_text(encoding="utf-8") == f"{project / 'workspace' / 'index.html'}\n"


@pytest.mark.parametrize(("name", "status"), [("SIGINT", 130), ("SIGTERM", 143)])
def test_open_starts_no_opener_once_a_signal_has_arrived(tmp_path, monkeypatch, name, status):
    """Review of 7cfe851, R1: the opener started after a signal that arrived once the report was
    there, which no other step does."""
    project = project_dir(tmp_path)
    (project / "workspace").mkdir()
    (project / "workspace" / "index.html").write_text("<html>", encoding="utf-8")
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
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(project))
    monkeypatch.delenv("AELIX_ANALYTICS_UV", raising=False)
    assert cli.run(["open"], kit=KIT) == status
    assert opened == []


@pytest.mark.parametrize("winerror", [None, 1155])
def test_open_through_the_windows_port_builds_a_missing_report_then_starts_it(
        fake, tmp_path, monkeypatch, capsys, winerror):
    """ADR-0004 check 4 on Windows (design review, Codex 6): `analytics open` through the Windows
    port with os.startfile replaced, so no program opens. A missing report is built first and the
    file then goes to os.startfile: exit 0. A file no program is associated with (WinError 1155) is
    exit 1 and the message. Off Windows the Windows port is built by name; a real opener is not run."""
    project = project_dir(tmp_path)
    index = project / "workspace" / "index.html"
    started = []

    def startfile(path, *args, **kwargs):
        started.append(path)
        if winerror:
            raise OSError(22, "No application is associated with the specified file for this "
                              "operation", path, winerror)

    monkeypatch.setattr(os, "startfile", startfile, raising=False)
    if POSIX:
        port = cli._load_platform(KIT)
        monkeypatch.setattr(cli, "_load_platform",
                            lambda kit: SimpleNamespace(detect=lambda: port.detect("windows")))
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(project))
    monkeypatch.setenv("FAKE_UV_LOG", str(fake["log"]))
    monkeypatch.delenv("FAKE_UV_DO", raising=False)
    status = cli.run(["open"], kit=KIT, uv=[sys.executable, str(fake["uv"])])
    assert [uv_args(c) for c in calls(fake)] == [kit_call(project, "render")]
    assert started == [os.fspath(index)]
    err = capsys.readouterr().err
    if winerror:
        assert status == 1
        assert f"analytics: could not open {index} (" in err
        assert "); open it in a browser yourself" in err
    else:
        assert status == 0, err
        assert err == ""


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


@pytest.mark.skipif(not POSIX, reason="a terminal's process group; a Windows console's Ctrl+C is "
                                       "tests/test_windows_console.py's")
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


@pytest.mark.skipif(POSIX, reason="what Windows does; POSIX sends it again (the test above)")
def test_a_ctrl_c_while_a_child_starts_is_not_sent_again_on_windows(fake, monkeypatch):
    """Investigation con-13: Windows has no SIGINT to send to one process (Popen.send_signal takes
    SIGTERM, CTRL_C_EVENT and CTRL_BREAK_EVENT), so a Ctrl+C noted while the child is being started
    is not sent to it again: the child runs to its end, its status is the launcher's, and no later
    step starts. A console's Ctrl+C reaches every process attached to it, the child as well, which
    tests/test_windows_console.py measures; this pins the part only the launcher decides."""
    real_popen = subprocess.Popen

    def popen_hit_by_ctrl_c(*args, **kwargs):
        signal.raise_signal(signal.SIGINT)  # the launcher's handler notes it, as a console's
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(cli.subprocess, "Popen", popen_hit_by_ctrl_c)
    monkeypatch.setenv("FAKE_UV_LOG", str(fake["log"]))
    # With a Ctrl+C sent to it the stand-in would stop waiting at once and record the signal.
    monkeypatch.setenv("FAKE_UV_DO", json.dumps({"run": {"wait": 2, "exit": 3}}))
    argv = [sys.executable, str(fake["uv"]), *kit_call(fake["tmp"], "run", "a.py")]
    with cli._Children() as children:
        status = children.run(argv, env=dict(os.environ), cancellable=True)
        assert children.interrupted
        assert children.run(argv, env=dict(os.environ)) == 130
    assert status == 3, "the child ran to its end"
    assert signals_the_kit_got(fake) == []
    assert len(calls(fake)) == 1


@pytest.mark.skipif(not POSIX, reason="a shell's background job")
@pytest.mark.parametrize("which", WHICH)
def test_a_background_job_keeps_sigint_ignored_for_the_kit(fake, tmp_path, which):
    """A non-interactive shell starts a background job with SIGINT ignored; the kit must inherit
    that, as it does under bin/analytics (review of design revision 1, launcher-5)."""
    project = project_dir(tmp_path)
    env = environment(fake)
    if which == "bin/analytics":
        command = f"sh '{BIN}' render"
    else:
        env["PYTHONPATH"] = str(PACK / "src")
        command = " ".join("'" + a.replace("'", "'\\''") + "'" for a in launcher_argv("render"))
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
        env = environment(fake, AELIX_ANALYTICS_UV=configured,
                          PATH=f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
        env["PYTHONPATH"] = str(PACK / "src")
        done = subprocess.run(launcher_argv("status"), cwd=project, env=env, capture_output=True,
                              timeout=60)
        assert done.returncode == 0, done.stderr
        assert calls(fake)[0]["argv0"] == "uv"


# --- migrate (Issue #13 PR 3, design 6.1) ----------------------------------------------------------

def migrate_call(*args) -> list[str]:
    return ["run", "--no-project", "--python", "3.12", "--quiet", "python", "-m", "aelix_analytics",
            "migrate", *args]


def sync_call(project: Path, *offline) -> list[str]:
    return ["sync", "--project", str(project), "--locked", "--quiet", *offline]


def answer_read(call) -> bool:
    """bin/analytics reading the kit's answer with Python (uv run --no-project ... python -c)."""
    return call["argv"][:7] == ["run", "--no-project", "--python", "3.12", "--quiet", "python", "-c"]


def migrated(tmp_path: Path, name: str = "new", **answer) -> tuple[Path, dict]:
    """A destination as the kit's migrate leaves it once committed (its analytics.json), and the
    stand-in's behaviour that answers so: `ok` and the destination, as the kit names it."""
    dest = tmp_path / name
    dest.mkdir(exist_ok=True)
    (dest / "analytics.json").write_text("{}", encoding="utf-8")
    value = {"ok": True, "already_migrated": False, "destination": str(dest.resolve()), **answer}
    return dest.resolve(), {"migrate": {"stdout": (json.dumps(value) + "\n").encode("utf-8").hex()}}


def kit_calls(fake) -> list[list[str]]:
    """The uv calls but bin/analytics' reading of the kit's answer."""
    return [uv_args(c) for c in calls(fake) if not answer_read(c)]


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("where", ["in the source", "nowhere"])
def test_migrate_runs_the_kit_outside_any_environment_then_prepares_the_destination(fake, tmp_path,
                                                                                   which, where):
    """design 6.1: the kit's migrate under `uv run --no-project` — no environment of the source's is
    made or synced, and the kit finds the source itself (no --project: AELIX_ANALYTICS_PROJECT, else
    the working directory, a call without one where none is) — and once it committed (exit 0), the
    environment of the destination its answer names from its lock as it is (`uv sync --locked`)."""
    cwd = project_dir(tmp_path, marker="lab.json") if where == "in the source" else tmp_path / "work"
    cwd.mkdir(exist_ok=True)
    project, do = migrated(tmp_path)
    done = launch(fake, "migrate", "--to", "../new", cwd=cwd, which=which, do=do)
    assert done.returncode == 0, done.stderr
    made = calls(fake)
    assert kit_calls(fake) == [migrate_call("--to", "../new"), sync_call(project)]
    assert [answer_read(c) for c in made] == ([False, True, False] if which == "bin/analytics"
                                              else [False, False])
    assert made[0]["cwd"] == str(cwd.resolve())
    assert made[0]["env"] == {"PYTHONPATH": str(KIT), "MPLBACKEND": "Agg", "PYTHONDONTWRITEBYTECODE": "1",
                              "AELIX_ANALYTICS_ACTOR": None, "VIRTUAL_ENV": None}
    assert done.stdout == bytes.fromhex(do["migrate"]["stdout"])
    assert done.stderr.decode("utf-8") == (
        f"{NAME[which]}: syncing the project environment (first time can take a minute) ...\n"
        f"{NAME[which]}: the project is {project} now, and the source is as it was "
        f"(in aelix: /analytics use {project})\n")


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("answer", ["committed", "already", "dry run", "dry run committed", "not ok",
                                    "no answer", "not json", "no marker"])
def test_migrate_prepares_the_destination_the_kit_s_answer_names(fake, tmp_path, which, answer):
    """The destination is the kit's, never the launcher's reading of --to (a `~` the shell left as
    it is would be another folder, below which uv would find the source's pyproject): the folder
    the answer names, when the answer says the migration went on (`ok`, not a dry run: committed now
    or before), and only when that folder holds analytics.json. A dry run prepares nothing, as it
    writes nothing; an answer that names no folder, or one without its marker, prepares nothing."""
    project, do = migrated(tmp_path)
    value = json.loads(bytes.fromhex(do["migrate"]["stdout"]))
    if answer == "already":
        value["already_migrated"] = True
    elif answer.startswith("dry run"):
        value = {"ok": True, "dry_run": True, "destination": value["destination"],
                 **({"state": "committed", "already_migrated": True} if answer.endswith("committed")
                    else {"state": "new"})}
    elif answer == "not ok":
        value = {"ok": False, "error": {"code": "x"}}
    elif answer == "no marker":
        (project / "analytics.json").unlink()
    stdout = {"no answer": b"", "not json": b"{oops\n"}.get(answer, (json.dumps(value) + "\n").encode())
    done = launch(fake, "migrate", "--to", "~/elsewhere", cwd=tmp_path, which=which,
                  do={"migrate": {"stdout": stdout.hex()}})
    assert done.returncode == 0, done.stderr
    assert done.stdout == stdout
    prepared = answer in ("committed", "already")
    assert kit_calls(fake) == [migrate_call("--to", "~/elsewhere"), *([sync_call(project)] if prepared else [])]
    stderr = done.stderr.decode("utf-8")
    if answer == "no marker":
        assert stderr == (f"{NAME[which]}: the kit's answer names {project}, which holds no analytics.json: "
                          "its environment is not prepared; analytics status there says what it is\n")
    elif not prepared:
        assert stderr == ""
    assert not (tmp_path / "~").exists()


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("code", [1, 2, 130])
def test_a_migration_the_kit_refused_prepares_nothing(fake, tmp_path, which, code):
    (tmp_path / "new").mkdir()
    done = launch(fake, "migrate", "--to", "new", cwd=tmp_path, which=which,
                  do={"migrate": {"exit": code, "stdout": b'{"ok": false}\n'.hex()}})
    assert done.returncode == code and done.stdout == b'{"ok": false}\n'
    assert [uv_args(c) for c in calls(fake)] == [migrate_call("--to", "new")]


@pytest.mark.parametrize("which", WHICH)
def test_a_destination_whose_environment_cannot_be_prepared_is_still_migrated(fake, tmp_path, which):
    """A lock that does not fit (or no network for it): `--locked` leaves the lock as it is and fails;
    an offline try fails too. The migration is done, the command says what failed, and exits 0: the
    project's next command prepares its environment as every command does."""
    project, do = migrated(tmp_path)
    done = launch(fake, "migrate", "--to", "new", cwd=tmp_path, which=which,
                  do={**do, "sync": {"exit": 1}, "sync-offline": {"exit": 4}})
    assert done.returncode == 0, done.stderr
    assert kit_calls(fake) == [migrate_call("--to", "new"), sync_call(project),
                               sync_call(project, "--offline")]
    assert done.stderr.decode("utf-8").endswith(
        f"{NAME[which]}: the migration is done, but preparing the environment of {project} failed "
        "(uv sync --locked, exit 4); the project's next command prepares it\n")


# --- a migration's destination that has not committed (design 4.3, T3) ----------------------------

def unfinished(tmp_path: Path, kind: str = "ledger") -> Path:
    """A folder with a migration's record and no marker, as a migration leaves its destination before
    its commit point: its lock only (F1), or a ledger with copies and a temporary file (F5)."""
    dest = tmp_path / "dest"
    (dest / ".analytics").mkdir(parents=True)
    (dest / ".analytics" / "migration.lock").write_bytes(b"")
    if kind == "ledger":
        mid = dest / ".analytics" / "migration" / "20261009T000000Z-0a1b2c3d"
        mid.mkdir(parents=True)
        (mid / "progress.jsonl").write_text(
            '{"step": "start", "at": "2026-10-09T00:00:00+00:00", "source": {"path": "/src"}}\n'
            '{"step": "plan", "at": "2026-10-09T00:00:01+00:00"}\n', encoding="utf-8")
        (dest / "scripts").mkdir()
        (dest / "scripts" / "a.py").write_text("print(1)\n", encoding="utf-8")
        (dest / "scripts" / ".mig-0a1b2c3d-0.part").write_bytes(b"print(")
        (dest / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return dest.resolve()


def outside_call(project: Path, *args) -> list[str]:
    return ["run", "--no-project", "--python", "3.12", "--quiet", "python", "-m", "aelix_analytics",
            "--project", str(project), *args]


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("kind", ["lock", "ledger"])
def test_the_search_stops_at_a_destination_not_committed_and_asks_the_kit_outside_any_environment(
        fake, tmp_path, which, kind):
    """design 4.3 (PR 2's hand-over): the search stops at a folder with a migration's record and no
    marker — never at a project above it — and the kit is run there without an environment, since uv
    would make `.venv/` there before the kit refuses (migration_incomplete): from below it, by
    AELIX_ANALYTICS_PROJECT given as it or a folder in it, every command."""
    outer = project_dir(tmp_path, "outer", marker="lab.json")
    dest = unfinished(outer, kind)
    (dest / "deeper").mkdir()
    cases = [((), dest / "deeper", ("status",), ("--text", "status")),
             ((), dest, ("record", "note", "x"), ("record", "note", "x")),
             ((("AELIX_ANALYTICS_PROJECT", str(dest)),), tmp_path, ("render",), ("render",)),
             ((("AELIX_ANALYTICS_PROJECT", str(dest / "deeper")),), tmp_path, ("query", "SELECT 1"),
              ("query", "SELECT 1"))]
    for env, cwd, args, expected in cases:
        fake["log"].unlink(missing_ok=True)
        done = launch(fake, *args, cwd=cwd, which=which, **dict(env))
        assert done.returncode == 0, done.stderr
        [call] = calls(fake)
        given = dict(env).get("AELIX_ANALYTICS_PROJECT") or str(dest)
        assert uv_args(call) == outside_call(Path(given), *expected), (args, uv_args(call))


@pytest.mark.skipif(not POSIX, reason="os.startfile would open a real program on Windows")
@pytest.mark.parametrize("which", WHICH)
def test_open_in_a_destination_not_committed_shows_the_kit_s_refusal_and_opens_nothing(fake, tmp_path,
                                                                                       which):
    dest = unfinished(tmp_path)
    opened = tmp_path / "opened.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("open", "xdg-open"):
        opener = bin_dir / name
        opener.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{opened}'\n", encoding="utf-8")
        opener.chmod(0o755)
    done = launch(fake, "open", cwd=dest / "scripts", which=which,
                  do={"render": {"exec": True}}, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert done.returncode == 1, done.stderr
    assert json.loads(done.stdout)["error"]["code"] == "migration_incomplete"
    assert [uv_args(c) for c in calls(fake)] == [outside_call(dest, "render")]
    assert not opened.exists() and not (dest / ".venv").exists()


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("args", [("status",), ("record", "finding", "x"), ("render",)])
def test_the_kit_refuses_every_command_in_a_destination_not_committed_and_nothing_changes(
        fake, tmp_path, which, args):
    """T3 through the launchers, the kit's own answer (run by the stand-in without an environment):
    migration_incomplete and 1, the folder as it was, no `.venv/`."""
    dest = unfinished(tmp_path)

    def listing() -> dict:
        return {str(p.relative_to(dest)): p.read_bytes() if p.is_file() else None
                for p in sorted(dest.rglob("*"))}

    before = listing()
    key = "status" if args == ("status",) else args[0]
    done = launch(fake, *args, cwd=dest / "scripts", which=which, do={key: {"exec": True}})
    assert done.returncode == 1, (done.stdout, done.stderr)
    assert b'"code": "migration_incomplete"' in done.stdout
    assert listing() == before and not (dest / ".venv").exists()
