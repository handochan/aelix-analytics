"""Shared host/session contract without a model or host dependency."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from test_asset_publication import candidate, checked, effect
from test_asset_suggestions import T0, application, qualified
from test_assets import prepared as prepared

from aelix_analytics import asset_suggestions, core, writer


def host_app(project, session="session-A", *, actor="human", means="command"):
    app = core.asset_application(project, core.Origin(actor, means, "declared", session))
    app.writer.clock = lambda: T0
    return app


@pytest.mark.parametrize("project_mode,session_mode", [("on_demand", "always_on"), ("always_on", "on_demand")])
def test_same_session_reload_retains_override_while_new_session_uses_project(prepared, project_mode, session_mode):
    project, _, _ = qualified(prepared)
    application(project).set_mode(project_mode)
    app = host_app(project)
    selected = app.set_mode(session_mode, scope="session")
    assert selected["event"]["details"]["session"] == selected["event"]["session"] == "session-A"
    assert writer.marker_state(project.root).value["asset_suggestions"]["mode"] == project_mode
    assert writer.marker_state(project.root).value["min_writer_version"] == "0.13.0"
    assert host_app(project).suggestion_status(session="session-A")["mode"] == session_mode
    assert host_app(project, "session-B").suggestion_status(session="session-B")["mode"] == project_mode
    assert not host_app(project).set_mode(session_mode, scope="session")["changed"]
    assert core.status(project, asset_session="session-A")["asset_suggestions"]["scope"] == "session"


@pytest.mark.parametrize("actor,means,session", [("agent", "command", "s"), ("human", "sdk", "s"),
                                               ("human", "command", None)])
def test_session_choice_requires_human_command_and_current_session_before_append(prepared, actor, means, session):
    project, _, _ = prepared
    before = project.journal.read_bytes(), (project.root / "analytics.json").read_bytes()
    with pytest.raises(core.AnalyticsError):
        host_app(project, session, actor=actor, means=means).set_mode("always_on", scope="session")
    assert (project.journal.read_bytes(), (project.root / "analytics.json").read_bytes()) == before


def test_context_and_human_status_share_project_window_and_context_is_relay_only(prepared):
    project, _, _ = qualified(prepared)
    host_app(project).set_mode("always_on", scope="session")
    first = host_app(project, actor="system", means="hook").publish_suggestion(surface="aelix_context")["published"]
    assert first["notification"]["details"]["surface"] == "aelix_context"
    assert writer.marker_state(project.root).value["min_writer_version"] == "0.13.0"
    text = core.asset_context_notice(first)
    assert "Relay this suggestion; review only when the user requests it." in text
    assert first["notification"]["id"] in text and "성장률 계산" in text
    assert host_app(project).publish_suggestion(surface="aelix_command_status")["published"] is None
    assert len(host_app(project).suggestion_status()["pending"]) == 1


def test_agent_tool_cannot_claim_a_host_publication_surface(prepared, monkeypatch):
    project, _, _ = qualified(prepared)
    application(project).set_mode("always_on")
    app = host_app(project, actor="agent", means="tool")
    monkeypatch.setattr(app, "assessment", lambda *a, **k: pytest.fail("read-only tool scored"))
    before = project.journal.read_bytes()
    assert app.publish_suggestion(surface="aelix_context")["reason"] == "read_only_surface"
    assert project.journal.read_bytes() == before


def test_preassessment_default_is_off_and_progress_cache_contains_no_candidate_body(prepared, monkeypatch):
    project, _, _ = qualified(prepared)
    app = host_app(project, actor="system", means="hook")
    before = project.journal.read_bytes()
    monkeypatch.setattr(app, "assessment", lambda *a, **k: pytest.fail("default preassessment scored"))
    assert not asset_suggestions.preassess(app)["assessed"]
    assert not (project.root / ".analytics/cache/asset-progress.json").exists()
    assert project.journal.read_bytes() == before
    monkeypatch.undo()
    application(project).set_mode("always_on")
    before = project.journal.read_bytes()
    first = asset_suggestions.preassess(app)
    assert first["assessed"] and first["counts"]["eligible"] == 1
    cache = project.root / ".analytics/cache/asset-progress.json"
    value = json.loads(cache.read_text(encoding="utf-8"))
    assert set(value) == {"contract", "basis", "counts", "window_allowed"}
    assert "성장률 계산" not in cache.read_text(encoding="utf-8")
    monkeypatch.setattr(app, "assessment", lambda *a, **k: pytest.fail("unchanged preassessment rescored"))
    assert asset_suggestions.preassess(app)["cached"]
    monkeypatch.undo()
    cache.unlink()
    assert asset_suggestions.preassess(host_app(project, actor="system", means="hook"))["counts"] == first["counts"]
    assert project.journal.read_bytes() == before  # Preassessment is never publication.


def test_progress_cache_reassesses_at_window_expiry_without_new_records(prepared, monkeypatch):
    project, source, _ = qualified(prepared)
    application(project).set_mode("always_on")
    application(project).publish_suggestion()
    other = candidate(project, source, asset_id="pending-rate")
    checked(project, other, source)
    effect(project, other)
    app = host_app(project, actor="system", means="hook")
    first = asset_suggestions.preassess(app)
    assert first["assessed"] and first["counts"]["eligible"] == 1
    before = project.journal.read_bytes()
    monkeypatch.setattr(app, "assessment", lambda *a, **k: pytest.fail("unchanged preassessment rescored"))
    assert asset_suggestions.preassess(app)["cached"]
    monkeypatch.undo()
    app.writer.clock = lambda: T0 + timedelta(hours=24)
    expired = asset_suggestions.preassess(app)
    assert expired["assessed"] and expired["counts"] == first["counts"]
    assert project.journal.read_bytes() == before
    # Publication reads authoritative facts again, independently of regenerable cache contents.
    cache = project.root / ".analytics/cache/asset-progress.json"
    value = json.loads(cache.read_text(encoding="utf-8"))
    value["counts"] = {"eligible": 0, "suppressed": 0}
    cache.write_text(json.dumps(value), encoding="utf-8")
    result = app.publish_suggestion(surface="aelix_context")
    assert result["published"]["candidate"]["id"] == other["id"]


@pytest.mark.parametrize("feature", ["session", "context", "human_status"])
def test_host_effect_introduces_floor13_while_old_project_mode_stays12(prepared, feature):
    project, _, _ = qualified(prepared)
    application(project).set_mode("always_on")
    assert writer.marker_state(project.root).value["min_writer_version"] == "0.12.0"
    if feature == "session":
        event = host_app(project).set_mode("always_on", scope="session")["event"]
    else:
        app = host_app(project, actor="system" if feature == "context" else "human",
                       means="hook" if feature == "context" else "command")
        event = app.publish_suggestion(surface="aelix_context" if feature == "context" else "aelix_command_status")["published"]["notification"]
    assert event["kind"] in ("asset_mode", "asset_notification")
    marker = writer.marker_state(project.root).value
    assert marker["min_writer_version"] == marker["kit_version"] == "0.13.0"
