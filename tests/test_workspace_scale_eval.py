"""tools/workspace_scale_eval.py: Q9's Workspace measurement on a scale fixture (#24, B6).

The tool runs as a command on a copy of a v2 S fixture's project, as the measurement record runs it on S
and M: it builds the copy's Workspace, never the fixture's, leaves its journal as it was, and finds the
display case's markup only as text. Its check of that case is tried on pages that hold the markup as
markup. The browser half runs where AELIX_ANALYTICS_TEST_BROWSER names chromium.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import OLD_ENV

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "workspace_scale_eval.py"
GENERATOR = ROOT / "tools" / "eval_fixtures.py"
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("workspace_scale_eval_under_test", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
BROWSER = "chromium" in os.environ.get("AELIX_ANALYTICS_TEST_BROWSER", "").split(",")


def env_in(base: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in (*OLD_ENV, "POLARS_MAX_THREADS", "VIRTUAL_ENV")}
    env.update({"AELIX_ANALYTICS_TRUST_FILE": str(base / "trust" / "lab-projects.json"), "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def run(script: Path, *args, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, env=env,
                          encoding="utf-8", errors="replace", timeout=900)


@pytest.fixture(scope="module")
def fixture(tmp_path_factory) -> tuple[Path, dict]:
    base = tmp_path_factory.mktemp("workspace-scale")
    env = env_in(base)
    done = run(GENERATOR, "scale", "--scale", "S", "--seed", "0", "--format", "v2", "--out", base / "S", env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    return base / "S", env


def copy_of(fixture: Path, where: Path) -> Path:
    shutil.copytree(fixture / "project", where)
    return where


def test_a_copy_is_built_and_its_journal_kept(fixture, tmp_path):
    path, env = fixture
    project = copy_of(path, tmp_path / "copy")
    args = ["--fixture", path, "--project", project, "--repeats", "2", "--cold-repeats", "1"]
    done = run(TOOL, *args, "--out", tmp_path / "out", "--browser", "chromium" if BROWSER else "none", env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads((tmp_path / "out" / "workspace-scale-evaluation.json").read_text(encoding="utf-8"))
    assert report["ok"] is True and report["journal"]["unchanged"] is True
    assert report["unchanged"]["skipped"] == [True, True] and report["warm"]["skipped"] == [False, False]
    assert report["fixture"]["scale"] == "S" and report["pages"]["pages"] > 1
    assert report["display_case"]["pages_with_markup"] == [] and report["display_case"]["pages_with_escaped_text"]
    assert report["display_case"]["browser"] == (True if BROWSER else "not_run")
    # The fixture's own project was never built.
    assert not any((path / "project" / "workspace").iterdir())


def test_the_fixture_s_own_project_and_another_project_are_refused(fixture, tmp_path):
    path, env = fixture
    done = run(TOOL, "--fixture", path, "--project", path / "project", "--out", tmp_path / "a", "--browser", "none", env=env)
    assert done.returncode == 2 and "copy" in done.stderr
    other = copy_of(path, tmp_path / "other")
    with (other / "journal.jsonl").open("ab") as journal:
        journal.write(b"\n")
    done = run(TOOL, "--fixture", path, "--project", other, "--out", tmp_path / "b", "--browser", "none", env=env)
    assert done.returncode == 2 and "journal differs" in done.stderr


def test_the_display_check_finds_markup_left_as_markup(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "lists").mkdir(parents=True)
    text = "case: " + " ".join(tool.ACTIVE)
    (workspace / "index.html").write_text(f"<p>{text}</p>", encoding="utf-8")
    (workspace / "lists" / "notes.html").write_text(f"<p>{tool.html.escape(text)}</p>", encoding="utf-8")
    manifest = {"cases": [{"id": "display_safety", "records": {"note": "e1"},
                           "checks": [{"type": "event_text", "id": "e1", "text": text}]}]}
    found = tool.display_case(manifest, workspace)
    assert found["pages_with_markup"] == ["index.html"]
    assert found["pages_with_escaped_text"] == ["lists/notes.html"]


@pytest.mark.skipif(not BROWSER, reason="AELIX_ANALYTICS_TEST_BROWSER does not name chromium")
def test_the_browser_check_tells_text_from_elements(tmp_path):
    from playwright.sync_api import sync_playwright

    text = " ".join(tool.ACTIVE)
    as_text, as_markup = tmp_path / "text.html", tmp_path / "markup.html"
    as_text.write_text(f"<p>{tool.html.escape(text)}</p>", encoding="utf-8")
    as_markup.write_text(f"<p>{text.replace('alert(', 'console.log(')}</p><script>// alert(\"fixture\")</script>",
                         encoding="utf-8")
    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page()
        page.goto(as_text.as_uri())
        assert page.evaluate(tool.DISPLAY_DOM, list(tool.ACTIVE)) == {"text_shown": True, "elements_made": 0}
        page.goto(as_markup.as_uri())
        seen = page.evaluate(tool.DISPLAY_DOM, list(tool.ACTIVE))
        browser.close()
    assert seen["text_shown"] is False and seen["elements_made"] >= 3
