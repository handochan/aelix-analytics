"""tools/publish_access_check.py without a second account (#21 PR-3): the reader it runs as another
account in CI reads a target as this one, and a folder only this account can open is warned about.
CI runs the tool itself with --account (and --smb on Windows)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_reader_reads_both_targets_as_this_account(tmp_path):
    out = tmp_path / "access.json"
    env = {**os.environ}
    env.pop("GITHUB_ACTIONS", None)
    done = subprocess.run([sys.executable, str(ROOT / "tools/publish_access_check.py"), "--output", str(out)],
                          capture_output=True, text=True, encoding="utf-8", timeout=300, env=env)
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    if done.returncode != 0:  # pytest.fail, not an assert: an assertion message is cut short
        pytest.fail(f"stderr: {done.stderr[-2000:]}\nreport: {json.dumps(report, indent=1)[-6000:]}", pytrace=False)
    assert report["failed"] is False and report["account"]["name"] is None
    opened = report["cases"]["open"]
    assert opened["result"] == "pass" and opened["outcomes"] == ["published", "published"]
    assert opened["reader"]["files"] > 0 and opened["reader"]["errors"] == []
    assert opened["reader"]["account"] if os.name == "nt" else opened["reader"]["uid"] == os.getuid()
    if os.name != "nt":
        assert report["cases"]["shut"]["result"] == "pass" and "others_cannot_read" in report["cases"]["shut"]["warnings"]
    refused = subprocess.run([sys.executable, str(ROOT / "tools/publish_access_check.py"), "--output", str(out),
                              "--account"], capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)
    assert refused.returncode == 2 and "change this machine" in refused.stderr
