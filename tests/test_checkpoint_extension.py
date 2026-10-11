"""ADR0012 T9/T11: the pinned host uses existing tools with no provider/model attempts."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("aelix_coding_agent")

from conftest import analytics_project  # noqa: E402
from test_extension import fresh_module  # noqa: E402


def test_existing_record_tool_and_context_read_never_invoke_model_or_publish(tmp_path, monkeypatch):
    module = fresh_module()
    root = analytics_project(tmp_path / "프로젝트 space")
    p = module.core.Project(root)
    monkeypatch.setattr(module, "resolve_project", lambda: (p, "test"))
    monkeypatch.setattr(module, "schedule_workspace_render", lambda *a: None)
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append("called")
        pytest.fail("checkpoint read or deterministic generation invoked a model/publication")

    ctx = SimpleNamespace(provider=SimpleNamespace(complete=forbidden), send_message=forbidden,
                          ui=SimpleNamespace(notify=forbidden))
    monkeypatch.setattr(module.core, "publish_asset_suggestion", forbidden)
    monkeypatch.setattr(module.core, "preassess_asset_suggestions", forbidden)
    generated = module.core.checkpoint(p, origin=module.core.Origin("agent", "sdk", "declared"))
    assert generated["event"]["details"]["summary_source"] == "deterministic" and attempts == []
    before = p.journal.read_bytes()
    module.STATE["session"] = "session-A"
    text, written = asyncio.run(module.tool_record({"kind": "checkpoint", "text": "에이전트 작성 원문"}, ctx))
    assert written["event"]["actor"] == "agent" and written["event"]["means"] == "tool"
    assert written["event"]["session"] == "session-A" and "recorded" in text
    assert p.journal.read_bytes().startswith(before)
    current = p.journal.read_bytes()
    monkeypatch.setattr(module.core, "sync_context", forbidden)
    _, result = asyncio.run(module.tool_status({"part": "context"}, ctx))
    assert result["checkpoint"]["id"] == written["event"]["id"]
    assert p.journal.read_bytes() == current and attempts == []
    assert len(module.TOOLS) == 6


def test_tool_tagged_historical_checkpoint_does_not_sync_current_context(tmp_path, monkeypatch):
    module = fresh_module()
    p = module.core.Project(analytics_project(tmp_path / "project"))
    old = module.core.checkpoint(p, "old", origin=module.core.Origin("human", "cli", "inferred"))["event"]
    p.context.write_text("# Goal\nCurrent unsaved direction\n", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_project", lambda: (p, "test"))
    monkeypatch.setattr(module, "schedule_workspace_render", lambda *a: None)
    monkeypatch.setattr(module.core, "sync_context", lambda *a, **k: pytest.fail("historical checkpoint synced current context"))
    _, answer = asyncio.run(module.tool_record({"kind": "checkpoint", "text": "historical handoff",
                                               "details": old["details"], "refs": old["refs"]}, SimpleNamespace()))
    assert answer["event"]["details"]["context_version"] == old["details"]["context_version"]
    assert module.core.context_pending(p)
