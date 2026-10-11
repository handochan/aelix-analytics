"""Real project history/window fixtures; publication is model-free and append-before-return."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from test_assets import ORIGIN, VALIDATOR, candidate, checked, record, run
from test_assets import prepared as prepared

from aelix_analytics import asset_policy, core, relations, writer
from aelix_analytics import asset_suggestions as suggestions

CLI = core.Origin("human", "cli", "declared")
T0 = datetime(2026, 10, 10, 12, tzinfo=UTC)


def effect(project, proposed, *, observed=False, source=None):
    details = {"asset_id": proposed["details"]["asset_id"], "candidate_event_id": proposed["id"],
               "effect_basis": "observed" if observed else "estimated", "description": "Avoid repeated ratio work"}
    if observed:
        details.update(authoring_cost="10 minutes", maintenance_cost="1 minute", measurement="20 minutes saved")
    return record(project, "asset_effect", details, refs=[{"type": "run", "run_id": source["id"]}] if observed else [])


def qualified(prepared):
    project, source, proposed = prepared
    checked(project, proposed, source)
    effect(project, proposed)
    run(project, "different", "other")
    return project, source, proposed


def application(project, when=T0):
    app = core.asset_application(project, CLI)
    app.writer.clock = lambda: when
    return app


def automatic(project):
    return [event.value for event in relations.read_journal(project.root).in_order()
            if event.kind == "asset_notification" and event.value["details"]["trigger"] == "automatic"]


def test_default_on_demand_does_not_score_or_append(prepared, monkeypatch):
    project, _, _ = qualified(prepared)
    app = application(project)
    before = project.journal.read_bytes()
    monkeypatch.setattr(app, "assessment", lambda *a, **k: pytest.fail("on_demand scored automatically"))
    assert app.publish_suggestion()["published"] is None
    assert project.journal.read_bytes() == before
    assert app.suggestion_status()["mode"] == "on_demand"


@pytest.mark.parametrize("config", [
    {"mode": "unknown"}, {"min_score": True}, {"min_score": 11}, {"min_score": 7.5},
    {"cooldown_hours": 23}, {"cooldown_hours": True}, {"cooldown_hours": float("inf")},
    {"measurement_targets": {"input_tokens_per_review": False}},
])
def test_bad_settings_are_explicit_and_stop_publication(prepared, config):
    project, _, _ = qualified(prepared)
    marker = writer.marker_state(project.root).value
    writer.write_json(project.root / "analytics.json", {**marker, "asset_suggestions": {"mode": "always_on", **config}},
                      platform=project.platform)
    app = application(project)
    before = project.journal.read_bytes()
    assert app.suggestion_status()["settings_errors"]
    assert app.publish_suggestion()["published"] is None
    assert project.journal.read_bytes() == before


@pytest.mark.parametrize("key", ["reuse_proposals", "skill_proposals"])
def test_legacy_settings_never_silently_activate(prepared, key):
    project, _, _ = prepared
    marker = writer.marker_state(project.root).value
    writer.write_json(project.root / "analytics.json", {**marker, key: {"mode": "always_on"}}, platform=project.platform)
    app = application(project)
    assert app.suggestion_status()["settings_errors"][0]["code"] == "legacy_asset_settings"
    assert app.publish_suggestion()["published"] is None


def test_mode_is_human_only_and_has_its_own_floor(prepared):
    project, _, _ = prepared
    before = project.journal.read_bytes()
    agent = core.asset_application(project, core.Origin("agent", "cli"))
    with pytest.raises(core.AnalyticsError, match="human"):
        agent.set_mode("always_on")
    assert project.journal.read_bytes() == before
    app = application(project)
    changed = app.set_mode("always_on")
    assert changed["event"]["kind"] == "asset_mode"
    assert writer.marker_state(project.root).value["min_writer_version"] == "0.12.0"
    assert relations.ASSET_WRITER_SINCE == "0.10.0" and relations.METRIC_SINCE == "0.9.0"
    assert not app.set_mode("always_on")["changed"]
    old = writer.Writer(project.root, platform=project.platform, origin=ORIGIN, writer_version="0.10.0")
    with pytest.raises(core.AnalyticsError) as error:
        old.admit("record")
    assert error.value.code == "writer_too_old"


def test_mode_partial_failure_is_visible_and_same_command_repairs_without_duplicate(prepared, monkeypatch):
    project, _, _ = prepared
    app = application(project)
    real = writer.write_json

    def fail_settings(path, value, **kwargs):
        if path == project.root / "analytics.json" and "asset_suggestions" in value:
            raise OSError("injected marker failure")
        return real(path, value, **kwargs)

    monkeypatch.setattr(writer, "write_json", fail_settings)
    with pytest.raises(core.AnalyticsError) as error:
        app.set_mode("always_on")
    assert error.value.code == "asset_mode_incomplete"
    assert app.suggestion_status()["settings_errors"]
    overview = core.status(project)
    assert overview["asset_suggestions"]["settings_errors"][0]["code"] == "asset_mode_incomplete"
    assert "repeat the human mode command" in core.status_text(overview)
    count = len(relations.read_journal(project.root).events)
    monkeypatch.setattr(writer, "write_json", real)
    assert app.set_mode("always_on")["repaired"]
    assert len(relations.read_journal(project.root).events) == count
    assert not app.suggestion_status()["settings_errors"]


def test_application_bookkeeping_does_not_mutate_a_conforming_memo_policy(prepared):
    class MemoPolicy:
        def __init__(self):
            self.cache = {}

        def assess(self, facts, *, min_score=8):
            key = (json.dumps(facts, sort_keys=True), min_score)
            if key not in self.cache:
                self.cache[key] = asset_policy.DeterministicPolicy().assess(facts, min_score=min_score)
            return self.cache[key]

    project, _, proposed = qualified(prepared)
    app = application(project)
    policy = MemoPolicy()
    app.policy = policy
    app.review("growth-rate")
    app.review("growth-rate")  # Same facts must remain a conforming cached result.
    app.update(proposed["id"], expected="proposed", new="deferred", text="Later", details={"reason_items": ["checks"]})
    app.set_mode("always_on")
    assert app.publish_suggestion()["published"] is None
    for value in policy.cache.values():
        assert "identity" not in value["requirements"]
        assert set(value["scores"]) == {"demand", "checks", "scope", "benefit"}


def test_append_precedes_return_and_requested_history_does_not_consume_window(prepared):
    project, source, proposed = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    result = app.publish_suggestion()
    notice = result["published"]["notification"]
    assert automatic(project)[0] == notice
    assert notice["recorded_at"] == T0.isoformat(timespec="milliseconds")
    assert app.publish_suggestion()["published"] is None
    # Reading/resetting the application/cache cannot issue the same candidate again.
    assert application(project, T0 + timedelta(days=2)).publish_suggestion()["published"] is None
    assert app.suggestion_status()["pending"][0]["event_id"] == notice["id"]


def test_request_suppresses_same_candidate_without_using_automatic_window(prepared):
    project, _, proposed = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    requested = core.review_assets(project, proposed["details"]["asset_id"], origin=ORIGIN)
    assert requested["assets"][0]["notification"]["details"]["trigger"] == "requested"
    assert app.suggestion_status()["allowed"]
    assert app.publish_suggestion()["published"] is None
    assert not automatic(project)


def test_window_expiry_can_publish_pending_without_new_records(prepared):
    project, source, proposed = qualified(prepared)
    second = candidate(project, source, asset_id="second-rate")
    checked(project, second, source)
    effect(project, second)
    app = application(project)
    app.set_mode("always_on")
    assert app.publish_suggestion()["published"]["candidate"]["id"] == proposed["id"]
    before = project.journal.read_bytes()
    assert application(project, T0 + timedelta(hours=23, minutes=59)).publish_suggestion()["published"] is None
    assert project.journal.read_bytes() == before
    result = application(project, T0 + timedelta(hours=24)).publish_suggestion()
    assert result["published"]["candidate"]["id"] == second["id"]
    assert len(automatic(project)) == 2


def test_backwards_clock_and_aware_offsets_are_not_new_windows(prepared):
    project, _, _ = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    app.publish_suggestion()
    earlier = application(project, T0 - timedelta(seconds=1)).suggestion_status()
    assert earlier["wait_reason"] == "clock_backwards" and not earlier["allowed"]
    equivalent = T0.astimezone(timezone(timedelta(hours=9)))
    assert not application(project, equivalent).suggestion_status()["allowed"]
    assert suggestions.instant("2026-10-10T12:00:00") is None


def test_rejection_reason_survives_requested_review_and_unrelated_new_evidence(prepared):
    project, source, proposed = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    core.update_asset(project, proposed["id"], expected="proposed", new="rejected", text="Need stronger checks",
                      details={"reason_items": ["checks"]}, origin=ORIGIN)
    core.review_assets(project, proposed["details"]["asset_id"], origin=ORIGIN)
    effect(project, proposed, observed=True, source=source)
    assert app.publish_suggestion()["published"] is None
    checked(project, proposed, run(project, "boundary", "boundary", boundary=True), boundary=True)
    assert app.publish_suggestion()["published"] is not None


def test_no_reason_rejection_stays_requested_only(prepared):
    project, source, proposed = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    core.update_asset(project, proposed["id"], expected="proposed", new="rejected", text="Not now", origin=ORIGIN)
    effect(project, proposed, observed=True, source=source)
    checked(project, proposed, run(project, "boundary", "boundary", boundary=True), boundary=True)
    assert app.publish_suggestion()["published"] is None


def test_historical_qualification_uses_completion_cutoff_not_current_final_manifest(prepared):
    project, source, proposed = prepared
    checked(project, proposed, source)
    effect(project, proposed)
    cutoff = relations.read_journal(project.root).as_of_seq
    run(project, "later", "other")
    app = application(project)
    model = app.model()
    assert app.assessment(proposed, model)["qualified"]
    old = model.through(cutoff)
    assert not app.assessment(proposed, old)["qualified"]
    assert old.completion_seq(source["id"]) is not None
    evaluated = suggestions.evaluate(app, model, suggestions.DEFAULTS)
    assert evaluated["eligible"][0]["first_threshold_seq"] > cutoff


def test_generic_notification_and_mode_inputs_remain_unavailable(prepared):
    project, _, _ = prepared
    for kind in ("asset_mode", "asset_notification"):
        with pytest.raises(core.AnalyticsError):
            core.record(project, kind, "No bookkeeping bypass", details={}, origin=ORIGIN)


def test_new_mode_notification_and_choice_records_validate_and_keep_known_boolean_snapshots(prepared):
    project, _, proposed = qualified(prepared)
    app = application(project)
    mode = app.set_mode("always_on")["event"]
    notice = app.publish_suggestion()["published"]["notification"]
    choice = core.update_asset(project, proposed["id"], expected="proposed", new="deferred", text="Later",
                               details={"reason_items": ["source", "identity"]}, origin=ORIGIN)["event"]
    for event in (mode, notice, choice):
        VALIDATOR.validate(event)
    marker = writer.marker_state(project.root).value
    VALIDATOR.validate(marker)
    assert mode["details"]["settings"] == marker["asset_suggestions"]
    assert choice["details"]["suggestion_history"] == "analysis_assets.suggestions/1"
    assert {"source", "identity"} <= choice["details"]["requirements"].keys()
    assert all(type(value) is bool for value in choice["details"]["requirements"].values())
    for bad in ({"min_score": True}, {"cooldown_hours": 23}, {"measurement_targets": {"input_tokens_per_review": False}}):
        assert not VALIDATOR.is_valid({**marker, "asset_suggestions": bad})


def test_malformed_automatic_time_blocks_instead_of_resetting_history(prepared):
    project, _, _ = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    notice = app.publish_suggestion()["published"]["notification"]
    lines = project.journal.read_bytes().splitlines()
    value = json.loads(lines[-1])
    assert value["id"] == notice["id"]
    value["recorded_at"] = "undated"
    project.journal.write_bytes(b"\n".join([*lines[:-1], json.dumps(value).encode()]) + b"\n")
    state = application(project, T0 + timedelta(days=3)).suggestion_status()
    assert not state["allowed"] and state["wait_reason"] == "malformed_automatic_history"
