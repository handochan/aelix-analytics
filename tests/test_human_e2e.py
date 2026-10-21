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
    assert profile.count('(with message "q17a-deny-x")') == 4
    assert '(remote ip)' in profile and f'(literal "{he.RESOLVER}")' in profile
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


def fake_out(tmp_path, *, canary=1, denials=(), env_names=("HOME", "PATH"), home=(), kit_source="import json\n"):
    out = tmp_path / "q17"
    paths = he.layout(out)
    site = paths["tools"] / "aelix-analytics" / "lib" / "python3.12" / "site-packages" / "aelix_analytics"
    site.mkdir(parents=True)
    (site / "build.py").write_text("import socket\nhost = socket.gethostname()\n", encoding="utf-8")
    (site / "other.py").write_text(kit_source, encoding="utf-8")
    for rel in home:
        (paths["home"] / rel).parent.mkdir(parents=True, exist_ok=True)
        (paths["home"] / rel).write_text("{}", encoding="utf-8")
    he.write_json(out / "prepare.json", {"wheel": {"file": "w.whl"}, "checkout": {"commit": "c"}, "session_env": {},
                                         "sandbox": {"denied_network": "all", "denied_exec": [], "denied_read": []}})
    he.write_json(paths["sessions"] / "1.json", {"number": 1, "started_at": "a", "ended_at": "b", "exit_code": 0,
                                                  "canary": {"curl_returncode": 7, "logged": canary},
                                                  "env_names": list(env_names), "denials": list(denials)})
    return out


def test_check_passes_a_clean_environment_and_fails_each_problem(tmp_path):
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


def test_check_fails_when_no_session_was_recorded(tmp_path):
    out = fake_out(tmp_path)
    (he.layout(out)["sessions"] / "1.json").unlink()
    assert "no session was recorded" in he.check(out, None)["problems"]


def test_files_are_classed_and_regenerable_ones_left_out():
    assert [he.file_class(r) for r in ("journal.jsonl", ".analytics/queries.jsonl", ".analytics/context/x.md",
                                        "analytics.json", "runs/r/run.json", "artifacts/reports/a/b/manifest.json",
                                        "data/imported/o.parquet", "CONTEXT.md", "scripts/a.py", "data/derived/d.parquet",
                                        ".analytics/report-attempts/x.lock", ".analytics/sub/x.jsonl")] == [
        "append", "append", "immutable", "immutable", "immutable", "immutable", "immutable", "editable", "editable",
        "editable", "kit_other", "kit_other"]
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
    assert snap["schema"] == he.SNAPSHOT_SCHEMA and snap["project_id"] == json.loads((p.root / "analytics.json").read_text())["project_id"]
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
    wrong = he.compare(before, he.snapshot(p.root), "human")
    assert any("has actor agent, not human" in x for x in wrong["problems"])


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
    assert files["data"] == {"class": "editable", "link": str(tmp_path)}


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
            "except OSError: print('read denied')\n")
    done = subprocess.run(["sandbox-exec", "-f", str(profile), sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=60)
    lines = done.stdout.splitlines()
    assert lines[0] != "connect 0" and lines[1:] == ["lookup denied", "exec denied", "read denied"], (lines, done.stderr)
