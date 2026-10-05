"""The platform measurement tool keeps working and keeps its record honest.

The repository CI (.github/workflows/ci.yml) runs the tool with `--gate required` on Linux and Windows
for every pull request and push to main, and on macOS on the `ci:macos` label, a manual run or the weekly
run. A broken tool fails that job; these tests also run in the suite, and locally before a push.
"""

import ast
import importlib.util
import json
import os
import signal
import subprocess
import sys
import types
from pathlib import Path

import pytest

from aelix_analytics import _platform

PACK = Path(__file__).resolve().parents[1]
TOOL = PACK / "tools" / "platform_smoke.py"
EXT = PACK / "extension" / "aelix_lab.py"


def tool_module():
    spec = importlib.util.spec_from_file_location("platform_smoke_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def smoke(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True,
                          timeout=300, cwd=PACK, **_platform.text_kwargs())


def test_the_record_says_where_it_ran_and_what_each_check_found(tmp_path):
    output = tmp_path / "smoke.json"
    done = smoke("--stdlib-only", "--only", "imports_without_analysis_libraries,utf8_survives_a_pipe",
                 "--output", output)
    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema"] == "aelix-analytics.platform-smoke/1"
    assert report["mode"] == "stdlib-only"
    assert report["environment"]["python"] and report["environment"]["platform"]
    assert "head" in report["source"]
    names = {c["name"]: c for c in report["checks"]}
    assert set(names) == {"imports_without_analysis_libraries", "utf8_survives_a_pipe"}
    assert all(c["result"] == "holds" for c in names.values()), names
    assert report["summary"]["holds"] == 2 and report["summary"]["violated"] == 0


def test_the_stdlib_mode_never_runs_the_cli_checks(tmp_path):
    output = tmp_path / "smoke.json"
    smoke("--stdlib-only", "--output", output)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["checks"], "stdlib mode ran nothing"
    assert not [c for c in report["checks"] if c["group"] == "cli"]
    # The self-test's check runs only when named, so a full run never fails because of it.
    assert "violated_by_construction" not in {c["name"] for c in report["checks"]}


def test_a_violated_promise_fails_the_run(tmp_path):
    # A check that does not hold, with nothing declaring it, has to turn the exit status red. The
    # check is violated by construction and runs only when named, so this cannot pass by accident
    # once every real promise holds (review 5, CH5-6e).
    output = tmp_path / "smoke.json"
    done = smoke("--stdlib-only", "--only", "violated_by_construction", "--output", output)
    report = json.loads(output.read_text(encoding="utf-8"))
    (check,) = report["checks"]
    assert check["result"] == "violated" and done.returncode == 1, (check, done.returncode)


def test_an_unknown_check_is_a_usage_error(tmp_path):
    done = smoke("--only", "no_such_check", "--output", tmp_path / "x.json")
    assert done.returncode == 2
    assert "unknown check" in done.stderr


# The tool's own judgement, each with the defect that would make a broken kit look fine (review 7,
# stop-1): none of these failed when the change it pins was reverted.

def test_the_smoke_tool_waits_as_long_as_the_extension_before_it_counts_a_stop_as_forced():
    """Review 6, AS-7: the tool's CLI cancel allowed 15 s where the extension ends the tree after
    GRACE_SECONDS, so a stop the extension would have forced still `held`. The two constants are
    equal, and the cancel waits the tool's."""
    def constant(path: Path, name: str):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name
                                                    for t in node.targets):
                return ast.literal_eval(node.value)
        raise AssertionError(f"{name} not found in {path}")

    assert constant(TOOL, "EXTENSION_GRACE_SECONDS") == constant(EXT, "GRACE_SECONDS")
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    cancel = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_cancel_a_cli_run")
    # `code, forced = finish(proc, ...), False`: the wait after the ask, before a forced end
    waits = [node.value.elts[0] for node in ast.walk(cancel)
             if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple)
             and [getattr(t, "id", None) for t in node.targets[0].elts] == ["code", "forced"]]
    assert [ast.unparse(w) for w in waits] == ["finish(proc, EXTENSION_GRACE_SECONDS)"], \
        [ast.unparse(w) for w in waits]


def test_the_smoke_gives_the_kit_the_extension_s_environment_but_for_the_stated_differences(
        monkeypatch):
    """kit_env stands in for the extension's child_env, so the CLI checks measure what the extension
    runs. Both are built here from the same inputs, and only the differences kit_env's docstring
    names are allowed: a variable either side adds, drops or sets to another value fails."""
    from test_platform import load_extension

    monkeypatch.delenv("AELIX_LAB_KIT", raising=False)
    tool, ext = tool_module(), load_extension(monkeypatch, "smoke_env_parity")
    monkeypatch.setitem(ext.STATE, "session", None)

    class Processes:
        # PATH is also allow-listed: the variable the run sees must win over the platform's base.
        def base_env(self):
            return {"BASE_FROM_THE_PLATFORM": "1", "PATH": "the platform's PATH"}

    class WindowsEnviron(dict):
        """os.environ as Windows has it: one variable per name, read without regard to case."""
        def __contains__(self, name):
            return any(key.upper() == name.upper() for key in self.keys())

        def __getitem__(self, name):
            return next(value for key, value in self.items() if key.upper() == name.upper())

    names = set(tool.ENV_ALLOW) | set(ext.ENV_ALLOW)
    environ = {name: f"value of {name}" for name in sorted(names)}
    if os.name == "nt":
        environ = WindowsEnviron({name.upper(): f"value of {name.upper()}" for name in names})
    environ.update({"UV_CACHE_DIR": "cache", "UV_INDEX_TOKEN": "secret", "UNRELATED": "no"})
    monkeypatch.setattr(os, "environ", environ)

    ctx = types.SimpleNamespace(bundle=types.SimpleNamespace(processes=Processes()))
    smoke, extension = tool.kit_env(ctx), ext.child_env(Processes(), "smoke")
    assert smoke.keys() - extension.keys() == {"AELIX_LAB_TRUST_FILE", "UV_INDEX_TOKEN"}
    assert extension.keys() - smoke.keys() == {"http_proxy", "https_proxy", "no_proxy", "all_proxy"}
    shared = (smoke.keys() & extension.keys()) - {"PYTHONPATH"}
    assert {k: smoke[k] for k in shared} == {k: extension[k] for k in shared}
    assert Path(smoke["PYTHONPATH"]).resolve() == Path(extension["PYTHONPATH"]).resolve()
    assert smoke["LAB_ACTOR"] == "smoke" and smoke["PATH"] == "value of PATH"


def test_every_declared_limitation_is_still_stated_in_the_tree():
    """The Windows checks report a limitation as declared only while CONTRACTS G states it in the
    words DECLARED quotes. Rewording that sentence without DECLARED would first show up as a red
    Windows smoke; this fails on every platform instead, in the same commit."""
    tool = tool_module()
    assert {key: tool.declared(key) for key in tool.DECLARED} == {
        key: f"{path} — {words}" for key, (path, words) in tool.DECLARED.items()}


def test_a_deadline_that_also_counted_a_cancel_does_not_hold():
    tool = tool_module()
    held = {"interrupted_by": "Interrupted", "flag_set": True, "cancel_flag": False, "seconds": 1.1}
    assert tool._deadline_held(held)
    assert not tool._deadline_held({**held, "cancel_flag": True})


class _OneShotBroken(_platform.PosixRunControl):
    """A guard that never spends its one interruption: every cancel in a region raises."""

    def guard(self):
        guard = super().guard()

        def stop(frame):
            return guard.raising and not _platform._in_exit(frame)

        guard._stop = stop
        return guard


@pytest.mark.skipif(os.name == "nt", reason="the broken guard stands in for the POSIX one")
def test_the_guard_check_sees_a_second_signal_that_raises_in_the_region():
    tool = tool_module()
    saved = {n: signal.getsignal(getattr(signal, n)) for n in ("SIGINT", "SIGTERM", "SIGALRM")}
    try:
        ctx = types.SimpleNamespace(bundle=types.SimpleNamespace(runs=_OneShotBroken()))
        outcome = tool.the_guard_records_outside_regions_and_interrupts_inside_once(ctx)
    finally:
        for name, handler in saved.items():
            signal.signal(getattr(signal, name), handler)
    assert outcome["holds"] is False, outcome
    assert outcome["observed"]["SIGINT"]["second_in_the_region"] != "only recorded", outcome


class _AlarmReplaced(_platform.PosixRunControl):
    """A POSIX guard whose SIGALRM handler is `alarm(guard, own, signum, frame)`."""

    def __init__(self, alarm):
        self.alarm = alarm

    def guard(self):
        guard = super().guard()
        own = guard._on_alarm

        def replaced(signum, frame):
            self.alarm(guard, own, signum, frame)

        guard.handlers = [(sig, replaced if handler == own else handler)
                          for sig, handler in guard.handlers]
        return guard


def _counts_a_cancel_too(guard, own, signum, frame):
    guard.cancelled = True
    own(signum, frame)


def _interrupts_without_counting(guard, _own, _signum, frame):
    guard.armed, guard.deadline_at = False, None  # nor can the clock count it
    if guard._stop(frame):
        raise _platform.Interrupted("run deadline expired")


@pytest.mark.skipif(os.name == "nt", reason="the stub guards stand in for the POSIX one")
@pytest.mark.parametrize("alarm", [_counts_a_cancel_too, _interrupts_without_counting])
def test_the_deadline_checks_read_both_flags_from_the_guard(alarm):
    """Review 8, rest-6: the self-test above pins _deadline_held's rule, not the observation the
    tool feeds it, which revision 3.2 rewired to the guard's flags. A kit whose deadline also counts
    as a cancel — the Windows defect CH-10's condition is there for — or is not counted at all has
    to fail the deadline checks."""
    tool = tool_module()
    saved = {n: signal.getsignal(getattr(signal, n)) for n in ("SIGINT", "SIGTERM", "SIGALRM")}
    try:
        ctx = types.SimpleNamespace(bundle=types.SimpleNamespace(runs=_AlarmReplaced(alarm)))
        outcome = tool.deadline_interrupts_a_sleep(ctx)
    finally:
        for name, handler in saved.items():
            signal.signal(getattr(signal, name), handler)
    observed = outcome["observed"]
    assert observed["interrupted_by"] == "Interrupted", outcome
    assert (observed["flag_set"], observed["cancel_flag"]) == \
        ((True, True) if alarm is _counts_a_cancel_too else (False, False)), outcome
    assert outcome["holds"] is False, outcome


@pytest.mark.parametrize("platform_name", ["posix", "windows"])
def test_the_required_verdict_counts_what_could_not_run_and_what_was_violated(monkeypatch,
                                                                              platform_name):
    """Nothing is exempt on either platform since the documents declare the two Windows
    limitations the first Windows run showed: a check showing one reports `declared_limitation`,
    which holds the verdict, and one violated fails it everywhere."""
    tool = tool_module()
    monkeypatch.setattr(tool, "WINDOWS", platform_name == "windows")
    regex = "cli_cancel_reaches_a_run_inside_a_regular_expression"
    ran = [{"name": regex, "observation": False, "result": "violated"},
           {"name": "deadline_interrupts_a_sleep", "observation": False, "result": "holds"}]
    verdict = tool.required_summary(ran)
    assert verdict["hold"] is False and verdict["violated"] == [regex], verdict
    declared = [{**ran[0], "result": "declared_limitation"}, ran[1]]
    verdict = tool.required_summary(declared)
    assert verdict["hold"] is True and verdict["declared_limitation"] == [regex], verdict
    not_run = [*ran[1:], {"name": "cli_cancel_the_way_the_extension_does", "observation": False,
                          "result": "not_run"}]
    verdict = tool.required_summary(not_run)
    assert verdict["hold"] is False and verdict["not_run"] == ["cli_cancel_the_way_the_extension_does"]


def test_the_required_gate_fails_when_a_required_check_could_not_run(tmp_path):
    uv = "uv.exe" if os.name == "nt" else "uv"
    path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if not (Path(p) / uv).exists()]
    env = {**os.environ, "PATH": os.pathsep.join(path)}
    args = [sys.executable, str(TOOL), "--only", "cli_cancel_the_way_the_extension_does",
            "--output", tmp_path / "gate.json"]
    every = subprocess.run([*map(str, args), "--gate", "all"], capture_output=True, timeout=300,
                           cwd=PACK, env=env, **_platform.text_kwargs())
    required = subprocess.run([*map(str, args), "--gate", "required"], capture_output=True,
                              timeout=300, cwd=PACK, env=env, **_platform.text_kwargs())
    report = json.loads((tmp_path / "gate.json").read_text(encoding="utf-8"))
    assert report["summary"]["not_run"] == 1, report["summary"]
    assert every.returncode == 0, every.stdout + every.stderr  # not_run is no violation
    assert required.returncode == 1, required.stdout + required.stderr


class _SettleCountsItsFailure:
    """A run control whose settle(failure) counts a deadline it is handed as a cancel, as revision
    3.3's Windows guard did (review 9, guard-2)."""

    def guard(self):
        guard = _platform.PosixRunControl().guard()
        settle = guard.settle

        def counting(failure=None):
            if isinstance(failure, _platform.Interrupted):
                guard.cancelled = True
            return settle(failure)

        guard.settle = counting
        return guard


@pytest.mark.skipif(os.name == "nt", reason="the stub guard stands in for the POSIX one")
def test_the_deadline_checks_settle_with_what_the_region_raised():
    """Review 10, rest-3 and honesty-7 (N10): the deadline checks see a deadline counted as a
    cancel at settle — where revision 3.3's Windows guard counted it — only because they settle
    with what the region raised, as the runner does. Nothing failed without that call."""
    tool = tool_module()
    saved = {n: signal.getsignal(getattr(signal, n)) for n in ("SIGINT", "SIGTERM", "SIGALRM")}
    try:
        ctx = types.SimpleNamespace(bundle=types.SimpleNamespace(runs=_SettleCountsItsFailure()))
        outcome = tool.deadline_interrupts_a_sleep(ctx)
    finally:
        for name, handler in saved.items():
            signal.signal(getattr(signal, name), handler)
    assert outcome["observed"]["cancel_flag"] is True and outcome["holds"] is False, outcome


# What `_cancel_a_cli_run` observed, by outcome: the form docs/CONTRACTS.md G states for a cancel
# that cannot land on Windows, and eight that are not it.
CANCEL_OUTCOMES = {
    "the_stated_form": {"recorded_status": "running", "listed_status": "incomplete",
                        "forced": True, "kit_process_gone": True},
    "the_kit_left_alive": {"recorded_status": "running", "listed_status": "running",
                           "forced": True, "kit_process_gone": False},
    "recorded_ok": {"recorded_status": "ok", "listed_status": "ok", "forced": False,
                    "kit_process_gone": True},
    "recorded_error": {"recorded_status": "error", "listed_status": "error", "forced": False,
                       "kit_process_gone": True},
    "never_started": {"run": "never started within 300 s", "recorded_status": None,
                      "forced": None},
    # Review 11, rest-5: each of these four differs from the stated form in one term only. The
    # tool ends a kit that outlived the forced end before it lists the runs, so a kit left alive is
    # seen as the first. The last cannot be observed — only a `running` record is listed
    # `incomplete` — and is here to hold the recorded status to the stated form on its own
    # (review 12, rest-11).
    "the_kit_left_alive_as_seen": {"recorded_status": "running", "listed_status": "incomplete",
                                   "forced": True, "kit_process_gone": False},
    "ended_unforced": {"recorded_status": "running", "listed_status": "incomplete",
                       "forced": False, "kit_process_gone": True},
    "still_listed_running": {"recorded_status": "running", "listed_status": "running",
                             "forced": True, "kit_process_gone": True},
    "recorded_cancelled_late": {"recorded_status": "cancelled", "listed_status": "incomplete",
                                "forced": True, "kit_process_gone": True},
}


@pytest.mark.parametrize("check", ["cli_cancel_reaches_a_run_inside_a_regular_expression",
                                   "cli_cancel_reaches_a_run_waiting_on_a_subprocess"])
@pytest.mark.parametrize("outcome", sorted(CANCEL_OUTCOMES))
def test_a_windows_cancel_check_declares_only_the_form_the_documents_state(monkeypatch, check,
                                                                           outcome):
    """Review 10, rest-4 and honesty-4: on Windows the two cancel checks named their declaration
    whenever they did not hold, so a run recorded ok, a kit left alive or a run that never started
    held the verdict as well. Only the stated form does now."""
    tool = tool_module()
    monkeypatch.setattr(tool, "WINDOWS", True)
    monkeypatch.setattr(tool, "declared", lambda key: f"stated: {key}")
    monkeypatch.setattr(tool, "_cancel_a_cli_run",
                        lambda ctx, name, source, *, wait: dict(CANCEL_OUTCOMES[outcome]))
    result = getattr(tool, check)(None)
    assert result["holds"] is False, result
    assert bool(result["declared"]) is (outcome == "the_stated_form"), result


def test_a_declaration_holds_only_while_the_measured_tree_states_it(monkeypatch, tmp_path):
    """Review 10, rest-4 and honesty-4: the Windows declarations cited docs/CONTRACTS.md G, which
    the measured tree did not yet contain; the report held the verdict on them regardless. A
    declaration is the document's own words now, looked up in the tree being measured."""
    tool = tool_module()
    path, words = tool.DECLARED["gil"]
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    assert tool.declared("gil") is None  # no document at all
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text("# G\n\nWindows에서 실행한 적이 없습니다.\n", encoding="utf-8")
    assert tool.declared("gil") is None
    cut = words.index(" ", len(words) // 2)  # the document wraps its lines at any space
    (tmp_path / path).write_text(f"# G\n\n- (측정) {words[:cut]}\n  {words[cut + 1:]}.\n",
                                 encoding="utf-8")
    assert tool.declared("gil") == f"{path} — {words}"


@pytest.mark.parametrize("diagnostic", ["", "aelix_analytics: could not end the process tree of pid 7",
                                        # review 12, rest-11: stderr, but not the kit's words
                                        "ERROR: The process with PID 8 could not be terminated."])
def test_the_windows_tree_check_declares_only_the_form_the_documents_state(monkeypatch,
                                                                           diagnostic):
    """Review 11, honesty-9: docs/CONTRACTS.md G states taskkill's limit with its form — the
    descendant survives and the kit says so on stderr, not counting it ended. The check named the
    declaration for any surviving descendant, said or not."""
    tool = tool_module()
    leader = types.SimpleNamespace(pid=7, returncode=0)
    monkeypatch.setattr(tool, "WINDOWS", True)
    monkeypatch.setattr(tool, "declared", lambda key: f"stated: {key}")
    monkeypatch.setattr(tool, "_tree", lambda ctx, name, mode: (leader, 8))
    monkeypatch.setattr(tool, "finish", lambda proc, seconds: 0)
    monkeypatch.setattr(tool, "captured_stderr", lambda fn, *args: (None, diagnostic))
    monkeypatch.setattr(tool, "wait_gone", lambda ctx, pid, seconds: False)
    monkeypatch.setattr(tool, "force_kill", lambda pid: None)
    ctx = types.SimpleNamespace(bundle=types.SimpleNamespace(
        processes=types.SimpleNamespace(kill_tree=lambda pid: None)))
    result = tool.kill_tree_after_the_leader_exited(ctx)
    stated = diagnostic.startswith("aelix_analytics:")
    assert result["holds"] is False and bool(result["declared"]) is stated, result


def test_an_observation_that_fails_does_not_cost_the_report(monkeypatch):
    """The runner settings are read after every check has run. If reading them fails, the report of
    those checks is still written, with the failure where the settings would be."""
    tool = tool_module()

    def broken():
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(tool, "runner_settings", broken)
    env = tool.environment(types.SimpleNamespace(uv=None, stdlib_only=True))
    assert env["runner"] == {"error": "PermissionError(5, 'Access is denied')"}
    assert env["uv"] == "not used (stdlib-only mode)"
