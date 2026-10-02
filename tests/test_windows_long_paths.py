"""A project past MAX_PATH on Windows, per route and per length (Issue #8; ADR-0002 check 6).

A probe on the hosted runner, with LongPathsEnabled=1, made, replaced, locked and walked a
321-character file and changed directory into a 312-character one, but a child started with that
directory as its working directory failed with WinError 267: CreateProcess keeps MAX_PATH for a
working directory. A project is used through several routes, and they meet that limit differently
(design review T2): the kit run by an interpreter outside the project, the extension's spawned
commands (working directory = the root), its in-process commands, its report builds, `/lab init`, and
the launcher, which starts uv without a working directory. Each test takes one route at a stated
length and keeps the lengths and what happened as an observation (AELIX_ANALYTICS_TEST_EVIDENCE_DIR),
from which CONTRACTS F and G are written.

The CI runs this module twice: in the suite, under the runner's LongPathsEnabled=1, and again in a new
process after setting LongPathsEnabled=0. What a test asserts follows the setting this process
actually has (RtlAreLongPathsEnabled): with long paths, what the code promises or the probe measured;
without, only that every route ends within its time limit, with what happened kept.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import KIT, PACK, kit_env, record_observation, write_sales_csv, write_script

pytestmark = pytest.mark.skipif(os.name != "nt", reason="MAX_PATH and LongPathsEnabled are Windows'")

# `<root>\.venv\Scripts\python.exe` is the root plus this many characters.
VENV_PYTHON = len(r"\.venv\Scripts\python.exe")
# A root whose environment's interpreter stays under MAX_PATH (260, with its terminating null).
SHORT_ROOT = 259 - VENV_PYTHON - 4
# Without long paths a root must leave room for the files a new project needs, the longest a
# context snapshot; 230 did not (the first LongPathsEnabled=0 run). This one does, and the run's
# outputs still do not. (A dataset's cached profile, `.lab/cache/profiles/<sha256>-20.json`, is
# longer, but the kit skips it when it cannot be written.)
CONTEXT_SNAPSHOT = len(r"\.lab\context\20261002-181930-910564_44593b156b5b.md")
TEMPLATE_ROOT = 259 - CONTEXT_SNAPSHOT - 4
# Past MAX_PATH for a working directory and for the root's own files (the probe's 312 failed).
DEEP_ROOT = 300
KIT_TIMEOUT = 180


def long_path_setting() -> dict:
    """The registry's LongPathsEnabled and this process's effective state."""
    import ctypes
    import winreg

    setting: dict = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            setting["registry"] = winreg.QueryValueEx(key, "LongPathsEnabled")[0]
    except OSError as exc:
        setting["registry"] = None
        setting["registry_error"] = repr(exc)
    try:
        enabled = ctypes.windll.ntdll.RtlAreLongPathsEnabled
        enabled.restype = ctypes.c_ubyte
        setting["effective"] = bool(enabled())
    except (OSError, AttributeError) as exc:
        setting["effective"] = None
        setting["effective_error"] = repr(exc)
    return setting


SETTING = long_path_setting() if os.name == "nt" else {}
# Unknown effective state (the export is undocumented): the registry decides.
ENABLED = bool(SETTING.get("effective") if SETTING.get("effective") is not None
               else SETTING.get("registry") == 1)


def observe(case: str) -> dict:
    return {"case": case, "setting": SETTING, "long_paths": ENABLED,
            "python": sys.version.split()[0]}


def keep(observation: dict) -> None:
    record_observation(f"long-paths-{'enabled' if ENABLED else 'disabled'}-{observation['case']}",
                       observation)


def path_of_length(base: Path, length: int) -> Path:
    """A path under `base` exactly `length` characters long, in components of at most 40."""
    remaining = length - len(str(base))
    assert remaining >= 2, (base, length)
    path = base
    while remaining:
        take = min(41, remaining)
        if remaining - take == 1:
            take -= 1  # never leave one character, which a component cannot take with its separator
        path = path / ("d" * (take - 1))
        remaining -= take
    assert len(str(path)) == length
    return path


def attempt(observation: dict, name: str, action):
    """`action()`, kept in the observation. With long paths an exception fails the test; without,
    it is what is measured."""
    try:
        value = action()
    except Exception as exc:
        failed = getattr(exc, "filename", None)
        observation[name] = {"raised": repr(exc), "winerror": getattr(exc, "winerror", None),
                             "filename": failed, "filename_length": failed and len(str(failed))}
        if ENABLED:
            raise
        return None
    observation[name] = value if isinstance(value, (dict, str, int, bool, type(None))) else repr(value)
    return value


def bounded(argv, *, timeout: float, cwd=None, env=None) -> dict:
    """Run `argv` with its whole tree ended at `timeout` and every wait bounded. On Windows,
    `subprocess.run` reads the output after killing only the direct child with no limit, which
    descendants holding the pipes can hold up for good (Codex F1); here the tree goes first."""
    proc = subprocess.Popen([str(a) for a in argv], cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                              "taskkill.exe")
        subprocess.run([system, "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = "", "(output not collected: the tree did not end)"
    return {"timed_out": timed_out, "returncode": proc.returncode, "stdout": out or "",
            "stderr": err or ""}


def kit(root: Path, *args) -> dict:
    """`python -m labkit` from this checkout, started from this directory (not the root), as an
    interpreter outside the project starts it. Fails the test only if it did not end in time."""
    done = bounded([sys.executable, "-m", "labkit", "--project", str(root), *args],
                   timeout=KIT_TIMEOUT, env=kit_env())
    assert not done["timed_out"], done
    result = None
    for line in reversed(done["stdout"].splitlines()):
        if line.startswith("{"):
            try:
                result = json.loads(line)
            except ValueError:
                continue
            break
    return {"returncode": done["returncode"], "result": result, "stderr": done["stderr"][-1500:]}


def deepest_file(root: Path) -> int:
    return max((len(os.path.join(d, n)) for d, _, files in os.walk(root) for n in files), default=0)


def make_project(root: Path, observation: dict, title: str = "긴 경로") -> Path | None:
    """A project at `root`, made in this process; None, kept in the observation, where it cannot be
    made (without long paths, past MAX_PATH)."""
    from labkit import core

    made = attempt(observation, "init_project", lambda: str(core.init_project(
        root, title=title, template_dir=KIT / "labkit" / "template", actor="test").root))
    if made:
        observation["project_root_length"] = len(made)
    return Path(made) if made else None


def test_a_root_under_max_path_records_and_reports_outputs_past_it(tmp_path):
    """Route 1: a root short enough for its environment's interpreter, and a run whose outputs and
    their records go past MAX_PATH (a 60-character script name, a 60-character dataset, a chart
    and its data). The kit runs by this checkout's interpreter, so no environment is involved.
    Without long paths the root is shorter still and the script's name short, so that the project
    and the script can be made and the run starts: only its outputs pass MAX_PATH, and what the run
    then records is the measurement (Codex R1: the long name kept the run from starting at all)."""
    observation = observe("root-under-max-path")
    try:
        root = path_of_length(tmp_path, SHORT_ROOT if ENABLED else TEMPLATE_ROOT)
        observation.update(root_length=len(str(root)),
                           venv_python_length=len(str(root)) + VENV_PYTHON)
        # Either setting: the measurement is the run, so a project that cannot be made fails it.
        assert make_project(root, observation) is not None, observation
        source = write_sales_csv(tmp_path / "sales.csv")
        imported = kit(root, "import", source, "--name", "sales")
        observation["import"] = imported
        body = ("import labkit as lab\nimport matplotlib\nmatplotlib.use('Agg')\n"
                "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1, 2])\n"
                "df = lab.read('sales')\nlab.save(df, 'a' * 60)\n"
                "lab.chart(fig, 'a-chart-id-that-is-forty-characters-long', data=df)\n")
        name = "a_script_name_of_sixty_characters_for_the_long_path_test.py" if ENABLED else "lp.py"
        script = attempt(observation, "script", lambda: write_script(root, name, body))
        ran = kit(root, "run", script, "--title", "long") if script else None
        observation["run"] = ran
        reported = kit(root, "report")
        observation["report"] = reported
        deepest = attempt(observation, "deepest_file", lambda: deepest_file(root))
        if not ENABLED:
            assert script is not None, observation  # the run was started, not skipped
            return
        assert imported["returncode"] == 0, imported
        assert ran["result"] is not None, ran
        run = ran["result"]["run"]
        assert ran["returncode"] == 0 and run["status"] == "ok", ran
        assert run["outputs"] and all(o["sha256"] for o in run["outputs"]), run["outputs"]
        assert any(len(str(root / o["path"])) > 260 for o in run["outputs"]), run["outputs"]
        assert reported["returncode"] == 0, reported
        index = root / "report" / "index.html"
        assert "a-chart-id-that-is-forty-characters-long" in index.read_text(encoding="utf-8")
        assert deepest > 260
    finally:
        keep(observation)


def _extension(monkeypatch):
    """The checkout's extension in this process with aelix stubbed, and a harmless `uv`: this
    interpreter, which ends at once on `run`/`sync` (only where it can start at all)."""
    from test_platform import load_extension

    monkeypatch.setenv("AELIX_LAB_UV", sys.executable)
    monkeypatch.delenv("AELIX_LAB_PROJECT", raising=False)
    return load_extension(monkeypatch, f"ext_long_{os.getpid()}")


def test_the_extension_cannot_start_a_kit_command_in_a_root_past_max_path(tmp_path, monkeypatch):
    """Route 2a: run_kit and sync_environment start uv with the root as the working directory, which
    CreateProcess refuses past MAX_PATH; both answer spawn_failed rather than hang or crash."""
    observation = observe("extension-spawn")
    try:
        root = path_of_length(tmp_path, DEEP_ROOT)
        observation["root_length"] = DEEP_ROOT
        if make_project(root, observation) is None:
            return
        module = _extension(monkeypatch)
        observation["use"] = asyncio.run(module.lab_command(f"use {root}"))
        project, _ = module.resolve_project()
        ok, value = asyncio.run(module.run_kit(project, ["status"], timeout=60))
        observation["run_kit"] = value
        try:
            observation["sync_environment"] = asyncio.run(module.sync_environment(project))
        except module.core.LabError as exc:
            observation["sync_environment"] = exc.to_json()
        if not ENABLED:
            return
        assert not ok and value["error"]["code"] == "spawn_failed", value
        assert "WinError 267" in value["error"]["message"], value
        synced = observation["sync_environment"]
        assert isinstance(synced, dict) and synced["error"]["code"] == "spawn_failed", synced
    finally:
        keep(observation)


def test_the_extension_s_in_process_commands_work_in_a_root_past_max_path(tmp_path, monkeypatch):
    """Route 2b: `/lab note` and the context block run in this process and work; the report build a
    note schedules is started with the root as its working directory, fails to start, and says
    nothing (schedule_report's `except Exception: return`)."""
    observation = observe("extension-in-process")
    try:
        root = path_of_length(tmp_path, DEEP_ROOT)
        observation["root_length"] = DEEP_ROOT
        if make_project(root, observation) is None:
            return
        module = _extension(monkeypatch)
        refused = []
        real_popen = module.subprocess.Popen

        def recording_popen(*args, **kwargs):
            try:
                return real_popen(*args, **kwargs)
            except OSError as exc:
                refused.append(exc)
                raise

        monkeypatch.setattr(module.subprocess, "Popen", recording_popen)
        observation["use"] = asyncio.run(module.lab_command(f"use {root}"))
        observation["note"] = asyncio.run(module.lab_command("note finding 깊은 경로의 발견"))
        observation["report_start_refused"] = [repr(e) for e in refused]
        observation["report_task"] = str(module.STATE["report_tasks"].get(str(root)))
        observation["context"] = asyncio.run(module.lab_command("context"))
        if not ENABLED:
            return
        assert observation["note"].endswith(" finding recorded"), observation["note"]
        events = module.core.read_events(module.core.Project(root))
        assert any(e["kind"] == "finding" and e["text"] == "깊은 경로의 발견" for e in events)
        assert "긴 경로" in observation["context"] and "깊은 경로의 발견" in observation["context"]
        assert [getattr(e, "winerror", None) for e in refused] == [267], refused
        assert str(root) not in module.STATE["report_tasks"]
    finally:
        keep(observation)


def test_lab_init_into_a_root_past_max_path_makes_the_project_then_fails_its_sync(tmp_path,
                                                                                 monkeypatch):
    """Route 2c: `/lab init` makes the project in this process, then its sync cannot start there.
    The project exists and is active, and the answer is the sync's error (design review T2)."""
    observation = observe("extension-init")
    try:
        target = path_of_length(tmp_path, DEEP_ROOT)
        observation["root_length"] = DEEP_ROOT
        module = _extension(monkeypatch)
        observation["answer"] = asyncio.run(module.lab_command(f"init {target} 긴경로"))
        observation["lab_json"] = attempt(observation, "lab_json_check",
                                          lambda: (target / "lab.json").is_file())
        observation["active"] = module.STATE["project"]
        if not ENABLED:
            return
        assert observation["lab_json"] is True
        assert os.path.samefile(observation["active"], target), observation["active"]
        assert "error [spawn_failed]" in observation["answer"], observation["answer"]
    finally:
        keep(observation)


def test_no_program_starts_with_a_working_directory_past_max_path(tmp_path):
    """Route 2d: what a shell inside such a root meets — any program it starts there — is
    CreateProcess's own error (the harness's, not the launcher's or the kit's)."""
    observation = observe("working-directory")
    try:
        root = path_of_length(tmp_path, DEEP_ROOT)
        observation["root_length"] = DEEP_ROOT
        if attempt(observation, "mkdir", lambda: root.mkdir(parents=True) or True) is None:
            return
        try:
            done = subprocess.run([sys.executable, "-c", "pass"], cwd=root, capture_output=True,
                                  timeout=60)
            observation["start"] = {"returncode": done.returncode}
        except OSError as exc:
            observation["start"] = {"raised": repr(exc), "winerror": getattr(exc, "winerror", None)}
        if ENABLED:
            assert observation["start"].get("winerror") == 267, observation["start"]
    finally:
        keep(observation)


def _installed_analytics(base: Path) -> str:
    """`analytics.exe` of the wheel built from this checkout, installed in a new environment as
    tests/test_packaging.py installs it (Codex R7: the route is the installed command's)."""
    uv = shutil.which("uv")
    venv = base / "venv"
    for argv, limit in (([uv, "build", "--wheel", "--quiet", "-o", base / "dist", PACK], 180),
                        ([uv, "venv", "--quiet", "--python", "3.12", venv], 60)):
        done = bounded(argv, timeout=limit)
        assert not done["timed_out"] and done["returncode"] == 0, (argv, done)
    [wheel] = (base / "dist").glob("*.whl")
    done = bounded([uv, "pip", "install", "--quiet", "--python", venv / "Scripts" / "python.exe",
                    "--no-deps", "--offline", wheel], timeout=60)
    assert not done["timed_out"] and done["returncode"] == 0, done
    return str(venv / "Scripts" / "analytics.exe")


def _launch(analytics: str, args, *, cwd: Path, env: dict, timeout: float) -> dict:
    """The installed `analytics` (its own kit, real uv), with every process it starts ended at its
    time limit."""
    done = bounded([analytics, *args], timeout=timeout, cwd=cwd, env=env)
    return {**done, "stdout": done["stdout"][-1500:], "stderr": done["stderr"][-1500:]}


# Longer than its own waits added up — build 180, environment 60, install 60, init 360, status
# 120, and 60 more for each to end a timed-out tree: 1080 — so a slow route ends by its own limit
# with its observation kept.
@pytest.mark.timeout(1100)
@pytest.mark.skipif(os.environ.get("AELIX_ANALYTICS_TEST_INSTALL") != "1",
                    reason="set AELIX_ANALYTICS_TEST_INSTALL=1 (network: uv syncs the project)")
def test_the_launcher_from_a_short_directory_with_lab_project_past_max_path(tmp_path):
    """Route 3: the installed `analytics init` and `analytics status` started from a short
    directory, the second with LAB_PROJECT naming the root. The launcher starts uv without a working
    directory; what uv
    then does with an environment past MAX_PATH (`<root>\\.venv\\Scripts\\python.exe`) is measured,
    not assumed: only the init's project files, made by the kit in a process with a short working
    directory, are asserted."""
    assert shutil.which("uv"), "AELIX_ANALYTICS_TEST_INSTALL=1 needs uv on PATH"
    observation = observe("launcher")
    try:
        root = path_of_length(tmp_path / "p", DEEP_ROOT)
        observation.update(root_length=DEEP_ROOT, venv_python_length=DEEP_ROOT + VENV_PYTHON)
        analytics = _installed_analytics(tmp_path / "install")
        env = {k: v for k, v in kit_env().items() if k not in ("PYTHONPATH", "LAB_PROJECT")}
        init = _launch(analytics, ["init", root, "--title", "긴 경로"], cwd=tmp_path, env=env,
                       timeout=360)
        observation["init"] = init
        observation["lab_json"] = attempt(observation, "lab_json_check",
                                          lambda: (root / "lab.json").is_file())
        observation["venv_python"] = attempt(
            observation, "venv_python_check",
            lambda: (root / ".venv" / "Scripts" / "python.exe").is_file())
        status = _launch(analytics, ["status"], cwd=tmp_path,
                         env={**env, "LAB_PROJECT": str(root)}, timeout=120)
        observation["status"] = status
        assert not init["timed_out"] and not status["timed_out"], observation
        if ENABLED:
            assert observation["lab_json"] is True, init
    finally:
        keep(observation)
