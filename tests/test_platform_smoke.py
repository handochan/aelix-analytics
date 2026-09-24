"""The platform measurement tool keeps working and keeps its record honest.

It is run on Linux and Windows by the Issue #8 measurement workflow, so a broken tool would show up
there as missing evidence rather than as a failure; these tests catch that here first.
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

from labkit import _platform

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
