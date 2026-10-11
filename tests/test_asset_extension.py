"""Pinned host hooks/commands are model-free and never send or notify autonomously."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("aelix_coding_agent")

from aelix_agent_core.harness.hooks import BeforeAgentStartHookEvent  # noqa: E402
from asset_fixtures import ORIGIN, skill_project  # noqa: E402
from test_asset_publication import child_env  # noqa: E402
from test_extension import EXT, fresh_module  # noqa: E402

from aelix_analytics import core, relations  # noqa: E402


@pytest.fixture
def host_project(tmp_path, monkeypatch):
    project, proposed = skill_project(tmp_path / "project")
    core.update_asset(project, proposed["id"], expected="validated", new="validated", text="Repeated need",
                      details={"demand": {"repeated_need": True}}, origin=ORIGIN)
    core.record(project, "asset_effect", "Estimated benefit", details={"asset_id": "ratio-procedure",
                "candidate_event_id": proposed["id"], "effect_basis": "estimated", "description": "Avoid repeated interpretation"}, origin=ORIGIN)
    # Build SDK fixtures before loading the host's synthetic kit namespace: in-process SDK
    # run isolation restores imported project namespaces, unlike the host's child-process runs.
    module = fresh_module()
    host_project = module.core.Project(project.root)
    monkeypatch.setattr(module, "resolve_project", lambda: (host_project, "synthetic fixture"))
    monkeypatch.setattr(module, "schedule_workspace_render", lambda *a, **k: None)
    return module, project, proposed


def context(file):
    def forbidden(*args, **kwargs):
        pytest.fail("host invoked a provider, send_message or UI notify")

    return SimpleNamespace(session_manager=SimpleNamespace(get_session=lambda: SimpleNamespace(session_file=file)),
                           provider=SimpleNamespace(complete=forbidden), send_message=forbidden,
                           ui=SimpleNamespace(notify=forbidden))


def notices(project):
    return [e.value for e in relations.read_journal(project.root).events if e.kind == "asset_notification"
            and e.value["details"]["trigger"] == "automatic"]


def before(module, ctx):
    return asyncio.run(module.on_before_agent_start(BeforeAgentStartHookEvent(prompt="hello", system_prompt="BASE"), ctx))


def test_mode_before_first_prompt_restores_same_file_and_new_fork_uses_project(host_project, tmp_path):
    module, project, _ = host_project
    file = str(tmp_path / "sessions/A.jsonl")
    ctx = context(file)
    assert module.STATE["session"] is None
    selected = json.loads(asyncio.run(module.analytics_command("assets mode always_on --scope session", ctx)))
    assert selected["scope"] == "session" and selected["event"]["session"] == file
    assert module.STATE["session"] == file
    assert core.asset_suggestion_status(project)["mode"] == "on_demand"
    module.on_agent_end(SimpleNamespace(), ctx)
    assert notices(project) == []  # Assistant end can assess/cache, never publish.
    block = before(module, ctx)
    assert "Relay this suggestion; review only when the user requests it." in block.system_prompt
    assert len(notices(project)) == 1 and notices(project)[0]["details"]["surface"] == "aelix_context"
    # A reloaded module has no in-memory mode; the same file still selects its stored choice.
    module.STATE["session"] = None
    assert "Asset suggestion (" not in before(module, ctx).system_prompt
    assert core.asset_suggestion_status(project, session=file)["mode"] == "always_on"
    for name in ("new", "fork"):
        assert "Asset suggestion (" not in before(module, context(str(tmp_path / f"sessions/{name}.jsonl"))).system_prompt
        assert len(notices(project)) == 1


def test_current_session_failure_never_reuses_old_scope(host_project, tmp_path):
    module, project, _ = host_project
    ctx = context(str(tmp_path / "sessions/A.jsonl"))
    asyncio.run(module.analytics_command("assets mode always_on --scope session", ctx))
    unavailable = SimpleNamespace(session_manager=SimpleNamespace(get_session=lambda: (_ for _ in ()).throw(ValueError("unavailable"))))
    result = before(module, unavailable)
    assert result is not None and "Asset suggestion (" not in result.system_prompt
    assert module.STATE["session"] is None and not notices(project)
    answer = asyncio.run(module.analytics_command("assets mode always_on --scope session", unavailable))
    assert "asset_session_required" in answer


@pytest.mark.parametrize("project_mode,session_mode", [("always_on", "on_demand"), ("on_demand", "always_on")])
def test_getter_failure_never_authorizes_host_automatic_effects(host_project, tmp_path, monkeypatch, project_mode, session_mode):
    module, project, _ = host_project
    ctx = context(str(tmp_path / "sessions/A.jsonl"))
    asyncio.run(module.analytics_command(f"assets mode {project_mode}", ctx))
    asyncio.run(module.analytics_command(f"assets mode {session_mode} --scope session", ctx))
    unavailable = SimpleNamespace(session_manager=SimpleNamespace(
        get_session=lambda: (_ for _ in ()).throw(ValueError("unavailable"))))
    assert "Asset suggestion (" not in before(module, unavailable).system_prompt
    assert "error [" not in asyncio.run(module.analytics_command("status", unavailable))
    assert module.STATE["session"] is None and not notices(project)
    monkeypatch.setattr(module.core, "preassess_asset_suggestions", lambda *a, **k: pytest.fail("unknown session preassessed"))
    module.on_agent_end(SimpleNamespace(), unavailable)
    assert not (project.root / ".analytics/cache/asset-progress.json").exists()


def test_hook_origin_is_held_when_another_context_changes_state_during_await(host_project, tmp_path, monkeypatch):
    module, project, _ = host_project
    ctx_a, ctx_b = [context(str(tmp_path / f"sessions/{name}.jsonl")) for name in ("A", "B")]
    asyncio.run(module.analytics_command("assets mode on_demand --scope session", ctx_a))
    entered, release = threading.Event(), threading.Event()
    original = module.core.context_block

    def held_read(p):
        entered.set()
        assert release.wait(timeout=10), "controlled context read was not released"
        return original(p)

    monkeypatch.setattr(module.core, "context_block", held_read)

    async def controlled_callbacks():
        task = asyncio.create_task(module.on_before_agent_start(
            BeforeAgentStartHookEvent(prompt="hello", system_prompt="BASE"), ctx_a))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            selected = await module.analytics_command("assets mode always_on --scope session", ctx_b)
            assert json.loads(selected)["scope"] == "session"
        finally:
            release.set()
        return await task

    value = asyncio.run(controlled_callbacks())
    assert "Asset suggestion (" not in value.system_prompt and not notices(project)
    assert module.STATE["session"] == ctx_b.session_manager.get_session().session_file


def test_default_context_stays_unchanged_and_read_only_surfaces_do_not_publish(host_project, tmp_path):
    module, project, _ = host_project
    ctx = context(str(tmp_path / "sessions/A.jsonl"))
    baseline = before(module, ctx).system_prompt
    assert "Asset suggestion" not in baseline
    asyncio.run(module.analytics_command("assets mode always_on --scope session", ctx))
    journal = project.journal.read_bytes()
    for part in ("records", "context"):
        text = asyncio.run(module.analytics_command(f"status --part {part}", ctx))
        assert "error [" not in text
    text = asyncio.run(module.analytics_command("status --query missing-part", ctx))
    assert "bad_query" in text
    asyncio.run(module.tool_status({}, SimpleNamespace()))
    assert project.journal.read_bytes().startswith(journal) and not notices(project)
    asyncio.run(module.analytics_command("assets mode on_demand --scope session", ctx))
    expected = "BASE\n\n" + module.core.context_block(module.core.Project(project.root))
    assert before(module, ctx).system_prompt == expected


def test_context_output_failure_consumes_record_and_leaves_pending(host_project, tmp_path, monkeypatch):
    module, project, _ = host_project
    ctx = context(str(tmp_path / "sessions/A.jsonl"))
    asyncio.run(module.analytics_command("assets mode always_on --scope session", ctx))
    monkeypatch.setattr(module.core, "asset_context_notice", lambda *a: (_ for _ in ()).throw(RuntimeError("output failure")))
    assert "Asset suggestion (" not in before(module, ctx).system_prompt
    assert len(notices(project)) == 1
    assert len(core.asset_suggestion_status(project)["pending"]) == 1
    before(module, ctx)
    assert len(notices(project)) == 1


def test_human_asset_command_registers_only_project_skill_with_real_loader(host_project, tmp_path):
    module, project, proposed = host_project
    ctx = context(str(tmp_path / "sessions/A.jsonl"))
    original = (project.root / "draft/SKILL.md").read_bytes()
    result = json.loads(asyncio.run(module.analytics_command(f"assets register {proposed['id']} --name denominator-rule", ctx)))
    assert result["registered"] and result["registration"]["loader_discovery"]["result"] == "pass"
    assert (project.root / ".aelix/skills/denominator-rule/SKILL.md").read_bytes() == original


def test_default_hook_and_agent_end_never_score_or_publish(host_project, tmp_path, monkeypatch):
    module, project, _ = host_project
    ctx = context(str(tmp_path / "sessions/A.jsonl"))
    monkeypatch.setattr(module.core.asset_app.Assets, "assessment", lambda *a, **k: pytest.fail("on_demand scored"))
    value = before(module, ctx)
    assert value is not None and "Asset suggestion" not in value.system_prompt
    assert "Use when reporting a ratio" not in value.system_prompt
    module.on_agent_end(SimpleNamespace(), ctx)
    assert not notices(project)
    assert not (project.root / ".analytics/cache/asset-progress.json").exists()


def test_host_overview_and_context_read_failures_precede_publication(host_project, tmp_path, monkeypatch):
    module, project, _ = host_project
    ctx = context(str(tmp_path / "sessions/A.jsonl"))
    asyncio.run(module.analytics_command("assets mode always_on", ctx))
    monkeypatch.setattr(module.core, "publish_asset_suggestion", lambda *a, **k: pytest.fail("published before read validation"))

    def failed_read(*args, **kwargs):
        raise module.core.AnalyticsError("injected_read_failure", "Reader unavailable")

    monkeypatch.setattr(module.core, "status", failed_read)
    assert "injected_read_failure" in asyncio.run(module.analytics_command("status", ctx))
    monkeypatch.setattr(module.core, "context_block", failed_read)
    assert before(module, ctx) is None
    assert not notices(project)


def test_human_asset_commands_use_requested_shared_records_and_preserve_text(host_project, tmp_path):
    module, project, proposed = host_project
    ctx = context(str(tmp_path / "sessions/A.jsonl"))
    for command in ("assets list", "assets show ratio-procedure"):
        assert json.loads(asyncio.run(module.analytics_command(command, ctx)))["assets"][0]["asset_id"] == "ratio-procedure"
    review = json.loads(asyncio.run(module.analytics_command("assets review ratio-procedure", ctx)))
    assert review["requested"] and review["model_calls"] == 0
    notice = review["assets"][0]["notification"]
    assert notice["actor"] == "human" and notice["session"] == ctx.session_manager.get_session().session_file
    assert notice["details"]["trigger"] == "requested" and notice["details"]["surface"] == "aelix_command"
    command = f'assets update {proposed["id"]} --expected validated --to deferred --reason "나중에 확인" --details-json \'{{"reason_items":["benefit"]}}\''
    changed = json.loads(asyncio.run(module.analytics_command(command, ctx)))
    assert changed["event"]["text"] == "나중에 확인"
    assert changed["event"]["details"]["reason_items"] == ["benefit"]
    assert not notices(project)


def test_real_cli_and_loaded_host_command_share_one_window(host_project, tmp_path):
    module, project, _ = host_project
    ctx = context(str(tmp_path / "sessions/A.jsonl"))
    asyncio.run(module.analytics_command("assets mode always_on", ctx))
    cli = ("import sys\nfrom aelix_analytics.__main__ import main\n"
           "print('READY',flush=True)\nsys.stdin.readline()\n"
           "sys.exit(main(['--project',sys.argv[1],'--text','status']))\n")
    host = ("import asyncio,importlib.util,sys\nfrom types import SimpleNamespace\n"
            "spec=importlib.util.spec_from_file_location('isolated_host',sys.argv[2])\n"
            "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n"
            "p=m.core.Project(sys.argv[1]);m.resolve_project=lambda:(p,'synthetic')\n"
            "m.schedule_workspace_render=lambda *a,**k:None\n"
            "ctx=SimpleNamespace(session_manager=SimpleNamespace(get_session=lambda:SimpleNamespace(session_file=sys.argv[3])))\n"
            "print('READY',flush=True)\nsys.stdin.readline()\n"
            "print(asyncio.run(m.analytics_command('status',ctx)))\n")
    children = [subprocess.Popen([sys.executable, "-c", code, str(project.root), str(EXT), str(tmp_path / "sessions/A.jsonl")],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8",
                                env={**child_env(tmp_path, f"host-publication-child-{i}"),
                                     "PYTHONIOENCODING": "utf-8"})
                for i, code in enumerate((cli, host))]
    output = []
    try:
        for child in children:
            assert child.stdout.readline().strip() == "READY"
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        for child in children:
            stdout, stderr = child.communicate(timeout=60)
            assert isinstance(stdout, str) and isinstance(stderr, str), (child.returncode, stdout, stderr)
            assert child.returncode == 0, stdout + stderr
            assert "error [" not in stdout
            output.append(stdout)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
    assert len(notices(project)) == 1
    assert sum("Asset suggestion (new," in text for text in output) == 1
