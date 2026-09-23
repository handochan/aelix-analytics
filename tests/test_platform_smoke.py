"""The platform measurement tool keeps working and keeps its record honest.

It is run on Linux and Windows by the Issue #8 measurement workflow, so a broken tool would show up
there as missing evidence rather than as a failure; these tests catch that here first.
"""

import json
import subprocess
import sys
from pathlib import Path

from labkit import _platform

PACK = Path(__file__).resolve().parents[1]
TOOL = PACK / "tools" / "platform_smoke.py"


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


def test_a_violated_promise_fails_the_run(tmp_path):
    # A check that does not hold, with nothing declaring it, has to turn the exit status red.
    output = tmp_path / "smoke.json"
    done = smoke("--stdlib-only", "--only", "liveness_of_a_live_an_exited_and_a_bogus_pid",
                 "--output", output)
    report = json.loads(output.read_text(encoding="utf-8"))
    (check,) = report["checks"]
    assert done.returncode == (0 if check["result"] == "holds" else 1), check


def test_an_unknown_check_is_a_usage_error(tmp_path):
    done = smoke("--only", "no_such_check", "--output", tmp_path / "x.json")
    assert done.returncode == 2
    assert "unknown check" in done.stderr
