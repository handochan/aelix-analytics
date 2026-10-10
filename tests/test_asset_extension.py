"""Pinned host hooks/commands are model-free and never send or notify autonomously."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("aelix_coding_agent")

from aelix_agent_core.harness.hooks import BeforeAgentStartHookEvent  # noqa: E402
from asset_fixtures import ORIGIN, skill_project  # noqa: E402
from test_extension import fresh_module  # noqa: E402

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
