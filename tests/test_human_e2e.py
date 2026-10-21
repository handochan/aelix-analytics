"""tools/human_e2e.py, the Q17 tools (Issue #23).

The synthetic data is the same for the same seed and holds what the guide's steps look for (a partial
September, Busan's web orders falling from 2026-07-20, Daegu's app refunds in August). The sandbox
profile tags every denial, quotes its paths and denies the resolver; the unified log's denials are read
by the tag, and the canary's apart. check fails a session whose canary was not logged (a zero that was
not measured), a denied attempt, a credential variable, a HOME holding more of aelix than the kit's
trust list, aelix importable in an environment and a network client in the installed kit, and passes a
clean one. snapshot classes a kit project's files (the kit's own init, records and Report versions)
and leaves out what the kit regenerates; compare passes an append and an edit of CONTEXT.md, and fails
a changed earlier journal line, a changed run, a removed Report version, another project and, with an
expected actor, an event or run of another one (the kit's `system` records allowed). agent-usage counts
an aelix session's requests, tokens by tools/model_eval.py's convention and tool calls, never its text.
The project copy of agent-env leaves out .venv, workspace/ and the cache.

On macOS: the profile really denies an outbound connection, the resolver and a model client's execution.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import analytics_project

from aelix_analytics import _platform, core

TOOL = Path(__file__).resolve().parents[1] / "tools" / "human_e2e.py"


def load():
    spec = importlib.util.spec_from_file_location("human_e2e_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


he = load()
HUMAN = core.Origin("사람", "cli", "inferred")
AGENT = core.Origin("agent", "tool", "declared")


def test_the_synthetic_orders_are_seeded_and_hold_what_the_guide_looks_for():
    text = he.orders_csv()
    assert text == he.orders_csv() and text != he.orders_csv(seed=24)
    rows = list(csv.DictReader(io.StringIO(text)))
    days = (date(2026, 9, 15) - date(2026, 6, 1)).days + 1
    assert len(rows) == days * 8 and rows[-1]["date"] == "2026-09-15"

    def mean(region, channel, test):
        values = [int(r["orders"]) for r in rows if r["region"] == region and r["channel"] == channel and test(r["date"])]
        return sum(values) / len(values)
    before = mean("부산", "웹", lambda d: d < "2026-07-20")
    after = mean("부산", "웹", lambda d: d >= "2026-07-20")
    assert 0.55 < after / before < 0.75
    assert 0.9 < mean("부산", "앱", lambda d: d >= "2026-07-20") / mean("부산", "앱", lambda d: d < "2026-07-20") < 1.1

    def rate(month):
        part = [r for r in rows if r["region"] == "대구" and r["channel"] == "앱" and r["date"][5:7] == month]
        return sum(int(r["refunds"]) for r in part) / sum(int(r["orders"]) for r in part)
    assert rate("08") > 2 * rate("07")


def test_the_profile_tags_every_denial_and_quotes_its_paths():
    profile = he.sandbox_profile("q17a-deny-x", ['/opt/a "b"/aelix'], ["/Users/me/.aelix"])
    assert profile.startswith("(version 1)\n(allow default)\n")
    assert profile.count('(with message "q17a-deny-x")') == 5
    assert '(remote ip)' in profile and '(remote unix-socket)' in profile and f'(literal "{he.RESOLVER}")' in profile
    assert '(literal "/opt/a \\"b\\"/aelix")' in profile and '(subpath "/Users/me/.aelix")' in profile
    assert "process-exec" not in he.sandbox_profile("t", [], []) and "file-read" not in he.sandbox_profile("t", [], [])


def test_denials_are_read_by_their_tag(monkeypatch):
    lines = [{"timestamp": "2026-10-21 13:26:31.778+0900",
              "eventMessage": "Sandbox: curl(85563) deny(1) network-outbound remote:*:11436\nq17a-deny-1"},
             {"timestamp": "2026-10-21 13:26:32.000+0900",
              "eventMessage": "Sandbox: curl(85564) deny(1) network-outbound remote:*:9\nq17a-canary-1"},
             {"timestamp": "2026-10-21 13:26:33.000+0900",
              "eventMessage": "Sandbox: bash(1) deny(1) process-exec* /opt/homebrew/bin/codex\nq17a-deny-1"},
             {"timestamp": "x", "eventMessage": "Sandbox: other(2) deny(1) mach-lookup com.apple.x"}]
    seen = {}

    def fake(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "\n".join(json.dumps(x) for x in lines) + "\nnot json\n", "")
    monkeypatch.setattr(he, "run", fake)
    start = he.datetime(2026, 10, 21, 13, 26, 0)
    found = he.read_denials(start, start, ["q17a-deny-1", "q17a-canary-1"])["found"]
    assert [d["operation"] for d in found["q17a-deny-1"]] == ["network-outbound", "process-exec*"]
    assert found["q17a-deny-1"][0] == {"time": "2026-10-21 13:26:31.778+0900", "process": "curl", "pid": 85563,
                                       "operation": "network-outbound", "target": "remote:*:11436"}
    assert [d["pid"] for d in found["q17a-canary-1"]] == [85564]
    assert 'eventMessage CONTAINS "q17a-deny-1" OR eventMessage CONTAINS "q17a-canary-1"' in seen["argv"]


def fake_out(tmp_path, *, canary=1, denials=(), env_names=("HOME", "PATH"), home=(), kit_source="import json\n",
             log_returncode=0):
    out = tmp_path / "q17"
    paths = he.layout(out)
    site = paths["tools"] / "aelix-analytics" / "lib" / "python3.12" / "site-packages" / "aelix_analytics"
    site.mkdir(parents=True)
    (site / "build.py").write_text("import socket\nhost = socket.gethostname()\n", encoding="utf-8")
    (site / "other.py").write_text(kit_source, encoding="utf-8")
    for rel in home:
        (paths["home"] / rel).parent.mkdir(parents=True, exist_ok=True)
        (paths["home"] / rel).write_text("{}", encoding="utf-8")
    (out / "sandbox.sb").write_text(he.sandbox_profile("q17a-deny-t", [], []), encoding="utf-8")
    (out / "canary.sb").write_text(he.sandbox_profile("q17a-canary-t", [], []), encoding="utf-8")
    he.write_json(out / "prepare.json", {"prepared_at": "2026-10-21T12:50:00+09:00", "wheel": {"file": "w.whl"},
                                         "checkout": {"commit": "c"}, "session_env": {},
                                         "sandbox": {"tag": "q17a-deny-t", "denied_network": "all", "denied_exec": [],
                                                     "denied_read": [], "profile_sha256": he.sha256_file(out / "sandbox.sb"),
                                                     "canary_sha256": he.sha256_file(out / "canary.sb")}})
    he.write_json(paths["sessions"] / "1.json", {"number": 1, "started_at": "2026-10-21T13:00:00+09:00",
                                                  "ended_at": "2026-10-21T13:30:00+09:00", "exit_code": 0,
                                                  "canary": {"curl_returncode": 7, "logged": canary},
                                                  "env_names": list(env_names), "denials": list(denials),
                                                  "log": {"command": "log show", "returncode": log_returncode}})
    return out


@pytest.fixture
def log_again(monkeypatch):
    """What check reads from the unified log again: nothing, unless a test puts denials here."""
    state = {"found": [], "returncode": 0, "calls": []}

    def fake(start, end, tags):
        state["calls"].append((start, tags))
        return {"command": "log show", "returncode": state["returncode"], "found": {tags[0]: list(state["found"])}}
    monkeypatch.setattr(he, "read_denials", fake)
    return state


def test_check_passes_a_clean_environment_and_fails_each_problem(tmp_path, log_again):
    clean = he.check(fake_out(tmp_path / "a", home=[".aelix/agent/analytics-projects.json"]), None)
    assert clean["passed"] and clean["model_calls"] == 0 and clean["attempts"] == 0, clean["problems"]
    assert clean["kit_network_imports"] == [{"file": "aelix_analytics/build.py", "module": "socket", "uses": ["gethostname"]}]
    assert clean["aelix_importable"]["tool_env"] == {"aelix_coding_agent": None, "aelix_ai": None, "aelix": None}

    def problems(**kw):
        return " | ".join(he.check(fake_out(tmp_path / str(len(list(tmp_path.iterdir()))), **kw), None)["problems"])
    assert "canary's denial was not logged" in problems(canary=0)
    denial = {"operation": "network-outbound", "target": "remote:*:443", "process": "Python", "pid": 1, "time": "t"}
    assert "1 denied attempt(s): {'network-outbound': 1}" in problems(denials=[denial])
    assert "credential variables ['OPENAI_API_KEY']" in problems(env_names=["HOME", "OPENAI_API_KEY"])
    assert "more than the kit's trust list: ['.aelix/agent/auth.json']" in problems(home=[".aelix/agent/auth.json"])
    assert "imports a network client" in problems(kit_source="import urllib.request\n")
    assert "imports a network client" in problems(kit_source="import asyncio\nasyncio.open_connection('h', 1)\n")
    assert "imports a network client" not in problems(kit_source="import asyncio\nasyncio.create_subprocess_exec('x')\n")
    assert "session 1: the unified log was not read (1)" in problems(log_returncode=1)
    assert log_again["calls"][0][1] == ["q17a-deny-t"]
    assert log_again["calls"][0][0].isoformat().startswith("2026-10-21T12:49:55")  # from prepare on


def test_check_fails_an_open_session_and_a_changed_profile(tmp_path, log_again):
    out = fake_out(tmp_path)
    he.write_json(he.layout(out)["sessions"] / "2.json", {"number": 2, "started_at": "x", "ended_at": None})
    (he.layout(out)["sessions"] / "3.json").write_text("", encoding="utf-8")
    problems = he.check(out, None)["problems"]
    assert "session(s) [2, 3] have no end record (still open, or their launcher was killed)" in problems
    out = fake_out(tmp_path / "b")
    (out / "sandbox.sb").write_text("(version 1)(allow default)\n", encoding="utf-8")
    assert "sandbox.sb is not the profile prepare wrote" in he.check(out, None)["problems"]


def test_two_shells_never_share_a_session_number(tmp_path):
    assert [he.reserve_session(tmp_path) for _ in range(3)] == [1, 2, 3]
    (tmp_path / "2.json").unlink()
    assert he.reserve_session(tmp_path) == 4


def test_a_record_written_outside_every_session_fails_check(tmp_path, log_again):
    out = fake_out(tmp_path)
    root = tmp_path / "p"
    root.mkdir()
    (root / "analytics.json").write_text('{"project_id": "p"}', encoding="utf-8")
    lines = [{"id": "e1", "seq": 1, "kind": "project", "actor": "human", "recorded_at": "2026-10-21T13:00:02.000+09:00"},
             {"id": "e2", "seq": 2, "kind": "finding", "actor": "agent", "recorded_at": "2026-10-21T15:00:00.000+09:00"},
             {"id": "e3", "seq": 3, "kind": "context", "actor": "system", "recorded_at": "2026-10-21T15:00:01.000+09:00"}]
    (root / "journal.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    assert he.check(out, root)["records_outside_sessions"] == []
    (root / "journal.jsonl").write_text("".join(json.dumps(x) + "\n" for x in [*lines, {
        "id": "e4", "seq": 4, "kind": "decision", "actor": "human", "recorded_at": "2026-10-21T16:00:00.000+09:00"}]),
        encoding="utf-8")
    (root / "runs" / "20261021-160000-x").mkdir(parents=True)
    he.write_json(root / "runs" / "20261021-160000-x" / "run.json", {"id": "x", "actor": "human",
                                                                   "started_at": "2026-10-21T16:00:00.000+09:00"})
    result = he.check(out, root)
    assert result["records_outside_sessions"] == ["e4 at 2026-10-21T16:00:00.000+09:00",
                                                  "run 20261021-160000-x at 2026-10-21T16:00:00.000+09:00"]
    assert not result["passed"]


def test_the_kit_scan_reads_imports_in_every_form(tmp_path):
    site = tmp_path / "site"
    (site / "aelix_analytics").mkdir(parents=True)
    forms = {"a.py": "from urllib import request\n", "b.py": "from http import client\n",
             "c.py": "import importlib\nimportlib.import_module('http.client')\n", "d.py": "import json, os\n",
             "e.py": "import socket\nsocket.gethostname()\n", "f.py": "import urllib.parse\n"}
    for name, text in forms.items():
        (site / "aelix_analytics" / name).write_text(text, encoding="utf-8")
    found = {(f["file"].split("/")[-1], f["module"], tuple(f["uses"])) for f in he.kit_network_imports(site)}
    assert found == {("a.py", "urllib.request", ()), ("b.py", "http.client", ()), ("c.py", "http.client", ()),
                     ("e.py", "socket", ("gethostname",))}


def test_check_counts_what_a_process_tried_after_its_session_once(tmp_path, log_again):
    denial = {"operation": "network-outbound", "target": "remote:*:11435", "process": "curl", "pid": 7, "time": "t1"}
    late = {**denial, "pid": 8, "time": "t2"}
    log_again["found"] = [denial, late]
    result = he.check(fake_out(tmp_path, denials=[denial]), None)
    assert result["attempts"] == 2 and not result["passed"]
    assert result["log_read_again"]["found"] == 2
    log_again.update(found=[], returncode=1)
    assert "the unified log was not read again (1)" in he.check(fake_out(tmp_path / "b"), None)["problems"]


def test_check_fails_when_no_session_was_recorded(tmp_path, log_again):
    out = fake_out(tmp_path)
    (he.layout(out)["sessions"] / "1.json").unlink()
    assert "no session was recorded" in he.check(out, None)["problems"]


def test_files_are_classed_and_regenerable_ones_left_out():
    assert [he.file_class(r) for r in ("journal.jsonl", ".analytics/queries.jsonl", ".analytics/context/x.md",
                                        "analytics.json", "runs/r/run.json", "artifacts/reports/a/b/manifest.json",
                                        "data/imported/o.parquet", "CONTEXT.md", "scripts/a.py", "data/derived/d.parquet",
                                        ".analytics/report-attempts/x.lock", ".analytics/sub/x.jsonl",
                                        "assets/candidates/a/e5/lib.py", ".analytics/adhoc/20261021/x.parquet",
                                        "assets/registry.json")] == [
        "append", "append", "immutable", "immutable", "immutable", "immutable", "immutable", "editable", "editable",
        "editable", "kit_other", "kit_other", "immutable", "immutable", "editable"]
    assert all(he.excluded(r) for r in ("workspace/index.html", ".venv/bin/python", ".analytics/cache/x",
                                        ".analytics/lock", "scripts/__pycache__/a.pyc", ".DS_Store"))
    assert not any(he.excluded(r) for r in ("workspace.md", ".analytics/context/x.md", "scripts/a.py"))


@pytest.fixture
def project(tmp_path):
    p = core.Project(analytics_project(tmp_path / "분석", origin=HUMAN), platform=_platform.detect())
    core.record(p, "finding", "부산 웹 주문이 줄었다", origin=HUMAN)
    source = tmp_path / "초안"
    source.mkdir()
    (source / "요약.md").write_text("# 보고\n", encoding="utf-8")
    first = core.save_report(p, "q3-review", files=source, entry="요약.md", title="중간 보고", previous_revision=None,
                             request_id="r1", origin=HUMAN)["report"]
    (p.root / "workspace").mkdir()
    (p.root / "workspace" / "index.html").write_text("x", encoding="utf-8")
    (p.root / ".venv").mkdir()
    (p.root / ".venv" / "pyvenv.cfg").write_text("x", encoding="utf-8")
    return p, source, first


def test_snapshot_reads_a_kit_project(project):
    p, _, first = project
    snap = he.snapshot(p.root)
    assert snap["schema"] == he.SNAPSHOT_SCHEMA and snap["project_id"] == json.loads((p.root / "analytics.json").read_text(encoding="utf-8"))["project_id"]
    assert not any(k.startswith(("workspace/", ".venv/")) for k in snap["files"])
    classes = {k: f["class"] for k, f in snap["files"].items()}
    assert classes["journal.jsonl"] == "append" and classes["analytics.json"] == "immutable"
    assert classes[f"artifacts/reports/q3-review/{first['revision_id']}/manifest.json"] == "immutable"
    assert any(k.startswith(".analytics/context/") and c == "immutable" for k, c in classes.items())
    assert [(e["id"], e["kind"], e["actor"]) for e in snap["events"]][2:] == [("e3", "finding", "사람"), ("e4", "report_revision", "사람")]
    assert list(snap["reports"]) == [f"q3-review@{first['revision_id']}"]
    assert len(snap["files"]["journal.jsonl"]["lines"]) == 4


def test_compare_passes_appends_and_edits_and_names_what_is_new(project):
    p, source, first = project
    before = he.snapshot(p.root)
    core.record(p, "decision", "에이전트의 판단", origin=AGENT)
    (source / "요약.md").write_text("# 보고 v2\n", encoding="utf-8")
    core.save_report(p, "q3-review", files=source, entry="요약.md", title="중간 보고", previous_revision=first["revision_id"],
                     request_id="r2", origin=AGENT)
    (p.root / "CONTEXT.md").write_text("## Goal\n바뀐 목적\n", encoding="utf-8")
    (p.root / "workspace" / "index.html").write_text("rebuilt", encoding="utf-8")
    result = he.compare(before, he.snapshot(p.root), "agent")
    assert result["passed"], result["problems"]
    assert [e["kind"] for e in result["new_events"]] == ["decision", "report_revision"]
    assert result["new_events_by_origin"] == {"agent/declared/tool": 2}
    assert result["edited"] == ["CONTEXT.md"] and len(result["new_reports"]) == 1
    after = he.snapshot(p.root)
    assert [v["actor"] for v in after["reports"].values()] == ["사람", "agent"]
    wrong = he.compare(before, after, "human")
    assert any(x.startswith("event e5 (decision) has actor agent") for x in wrong["problems"])
    assert any(x.startswith("report q3-review@") and x.endswith("has actor agent, not human") for x in wrong["problems"])


def test_an_open_last_line_ended_by_the_next_append_is_kept(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "analytics.json").write_text('{"project_id": "p"}', encoding="utf-8")
    (root / "journal.jsonl").write_bytes(b'{"id": "e1"}\n{"id": "e2"}')
    before = he.snapshot(root)
    (root / "journal.jsonl").write_bytes(b'{"id": "e1"}\n{"id": "e2"}\n{"id": "e3"}\n')
    assert he.compare(before, he.snapshot(root))["passed"]
    (root / "journal.jsonl").write_bytes(b'{"id": "e1"}\n{"id": "e2x"}\n')
    assert not he.compare(before, he.snapshot(root))["passed"]


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("lsof") is None, reason="lsof")
def test_what_a_session_left_running_under_out_is_stopped(tmp_path):
    out = tmp_path / "q17"
    (out / "work").mkdir(parents=True)
    child = subprocess.Popen(["sleep", "60"], cwd=out / "work")
    short = subprocess.Popen(["sleep", "2"], cwd=out / "work")
    try:
        left = he.end_left_running(out, wait=4)
        assert sorted((p["pid"], p["action"]) for p in left) == sorted([(child.pid, "SIGTERM"), (short.pid, "waited for")])
        assert child.wait(timeout=10) != 0 and short.wait(timeout=10) == 0
    finally:
        child.kill()
    assert he.end_left_running(out, wait=0) == []


def test_compare_allows_the_kits_system_records():
    before = {"project_id": "p", "files": {}, "events": [], "runs": {}, "reports": {}, "taken_at": "t"}
    after = {**before, "events": [{"id": "e1", "kind": "context", "actor": "system"}]}
    assert he.compare(before, after, "agent")["passed"]


def test_compare_fails_what_a_handoff_must_keep(project):
    p, _, first = project
    before = he.snapshot(p.root)
    journal = p.root / "journal.jsonl"
    lines = journal.read_bytes().split(b"\n")
    journal.write_bytes(b"\n".join([lines[0], lines[1], lines[2].replace("줄었다".encode(), "늘었다".encode()), *lines[3:]]))
    assert "earlier lines changed: journal.jsonl" in he.compare(before, he.snapshot(p.root))["problems"]
    journal.write_bytes(b"\n".join(lines))
    assert he.compare(before, he.snapshot(p.root))["passed"]
    journal.write_bytes(journal.read_bytes()[:-1])  # the last line's LF taken away
    assert "earlier lines changed: journal.jsonl" in he.compare(before, he.snapshot(p.root))["problems"]
    journal.write_bytes(b"\n".join(lines))
    manifest = p.root / "artifacts" / "reports" / "q3-review" / first["revision_id"] / "manifest.json"
    shutil.rmtree(manifest.parent)
    problems = he.compare(before, he.snapshot(p.root))["problems"]
    assert f"removed: artifacts/reports/q3-review/{first['revision_id']}/manifest.json" in problems
    other = {**before, "project_id": "prj_other"}
    assert any(x.startswith("another project") for x in he.compare(before, other)["problems"])


def test_compare_fails_a_changed_run_and_a_new_version_whose_previous_is_gone():
    base = {"project_id": "p", "taken_at": "t", "events": [], "reports": {},
            "files": {"runs/r/run.json": {"class": "immutable", "sha256": "a"}},
            "runs": {"r": {"id": "r", "status": "error", "actor": "사람"}}}
    after = {**base, "files": {"runs/r/run.json": {"class": "immutable", "sha256": "b"}},
             "runs": {"r": {"id": "r", "status": "ok", "actor": "사람"}},
             "reports": {"q@2": {"report_id": "q", "revision_id": "2", "previous_revision": "1"}}}
    problems = he.compare(base, after)["problems"]
    assert "changed: runs/r/run.json" in problems
    assert any(x.startswith("run r reads differently") for x in problems)
    assert "report q@2: previous revision 1 is not kept" in problems


def test_a_link_is_recorded_as_a_link(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "analytics.json").write_text('{"project_id": "p"}', encoding="utf-8")
    try:
        (root / "data").symlink_to(tmp_path)
    except OSError:
        pytest.skip("no symlinks here")
    files = he.snapshot(root)["files"]
    assert files["data"] == {"class": "editable", "link": os.readlink(root / "data")}


def test_agent_usage_counts_requests_tokens_and_tools_without_text(tmp_path):
    sessions = tmp_path / "sessions" / "--x--"
    sessions.mkdir(parents=True)
    entries = [{"type": "session", "id": "s"},
               {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "비밀 요청"}],
                                               "timestamp": 1}},
               {"type": "message", "message": {"role": "assistant", "api": "openai-codex-responses",
                                               "provider": "openai-codex", "model": "gpt-6.1-sol", "stop_reason": "toolUse",
                                               "usage": {"input": 100, "output": 20, "cache_read": 50, "reasoning": 5},
                                               "content": [{"type": "toolCall", "tool_name": "bash", "input": {}},
                                                           {"type": "toolCall", "tool_name": "analytics_record", "input": {}}],
                                               "timestamp": 2}},
               {"type": "message", "message": {"role": "assistant", "api": "openai-codex-responses",
                                               "provider": "openai-codex", "model": "gpt-6.1-sol", "stop_reason": "stop",
                                               "usage": {"input": 10, "output": 5, "cache_read": 150, "reasoning": 0},
                                               "content": [{"type": "text", "text": "비밀 답"}], "timestamp": 3}}]
    (sessions / "s.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\nbroken\n",
                                      encoding="utf-8")
    usage = he.agent_usage(tmp_path / "sessions")
    assert usage["requests"] == 2 and usage["user_messages"] == 1 and usage["models"] == ["openai-codex/gpt-6.1-sol"]
    assert usage["tool_calls"] == {"analytics_record": 1, "bash": 1}
    tokens = usage["tokens"]
    assert tokens["complete"] and tokens["requests"] == 2
    assert "비밀" not in json.dumps(usage, ensure_ascii=False)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs")
def test_a_fifo_is_named_never_opened(project, tmp_path):
    p, _, _ = project
    os.mkfifo(p.root / "scripts-pipe")
    snap = he.snapshot(p.root)
    assert snap["files"]["scripts-pipe"]["special"].startswith("p")
    he.copy_project(p.root, tmp_path / "copy")
    assert not (tmp_path / "copy" / "scripts-pipe").exists()


def test_the_copy_leaves_out_what_the_kit_regenerates(project, tmp_path):
    p, _, _ = project
    (p.root / ".analytics" / "cache").mkdir(exist_ok=True)
    (p.root / ".analytics" / "cache" / "hashes.json").write_text("{}", encoding="utf-8")
    he.copy_project(p.root, tmp_path / "copy")
    assert not (tmp_path / "copy" / ".venv").exists() and not (tmp_path / "copy" / "workspace").exists()
    assert not (tmp_path / "copy" / ".analytics" / "cache").exists()
    assert he.compare(he.snapshot(p.root), he.snapshot(tmp_path / "copy"))["passed"]


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("sandbox-exec") is None, reason="macOS's sandbox-exec")
def test_the_profile_denies_a_connection_the_resolver_and_a_model_client(tmp_path):
    client = tmp_path / "aelix"
    client.write_text("#!/bin/sh\necho ran\n", encoding="utf-8")
    client.chmod(0o755)
    profile = tmp_path / "p.sb"
    profile.write_text(he.sandbox_profile("q17-test", [str(client.resolve())], [str(tmp_path / "secret")]),
                       encoding="utf-8")
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "auth.json").write_text("{}", encoding="utf-8")
    code = ("import socket, subprocess, sys\n"
            "s = socket.socket(); s.settimeout(2)\n"
            "print('connect', s.connect_ex(('127.0.0.1', 9)))\n"
            "try:\n    socket.getaddrinfo('example.com', 443); print('lookup ok')\n"
            "except OSError: print('lookup denied')\n"
            f"try:\n    print('exec', subprocess.run([{str(client.resolve())!r}], capture_output=True).returncode)\n"
            "except OSError: print('exec denied')\n"
            f"try:\n    open({str(tmp_path / 'secret' / 'auth.json')!r}).read(); print('read ok')\n"
            "except OSError: print('read denied')\n"
            "u = socket.socket(socket.AF_UNIX)\n"
            "try:\n    u.connect('/var/run/syslog'); print('unix ok')\n"
            "except OSError: print('unix denied')\n")
    done = subprocess.run(["sandbox-exec", "-f", str(profile), sys.executable, "-c", code],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    lines = done.stdout.splitlines()
    assert lines[0] != "connect 0" and lines[1:] == ["lookup denied", "exec denied", "read denied", "unix denied"], (
        lines, done.stderr)
