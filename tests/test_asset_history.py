"""Identity namespaces, conservative old choices and exact new reason evidence."""

from __future__ import annotations

import copy
import json
from datetime import timedelta

import pytest
from test_asset_suggestions import T0, application, effect, qualified
from test_assets import ORIGIN, candidate, checked, record, run
from test_assets import prepared as prepared

from aelix_analytics import asset_suggestions as suggestions
from aelix_analytics import core, relations, runner


def old_record(project, kind, details):
    """An existing canonical /2 legacy line; preserve its old keys and Unicode verbatim."""
    seq = relations.read_journal(project.root).as_of_seq + 1
    value = {"schema": "analytics.event/2", "seq": seq, "id": f"e{seq}",
             "recorded_at": T0.isoformat(), "kind": kind, "actor": "human", "means": "cli",
             "text": "과거 후보 이력", "refs": [], "details": details}
    with project.journal.open("ab") as stream:
        stream.write(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n")
    return value


def old_notice(project, proposal_id, assessed, *, automatic=False):
    return old_record(project, "reuse_notification", {"proposal_id": proposal_id,
                      "trigger": "automatic" if automatic else "requested",
                      "evidence": assessed["evidence"], "scores": assessed["scores"]})


@pytest.mark.parametrize("hours", [0, 48])
@pytest.mark.parametrize("trigger", ["automatic", "requested", "legacy"])
def test_ambiguous_notification_cannot_reset_window_or_same_evidence_history(prepared, hours, trigger):
    project, _, proposed = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    if trigger == "automatic":
        notice = app.publish_suggestion()["published"]["notification"]
    elif trigger == "requested":
        notice = app.review("growth-rate")["assets"][0]["notification"]
    else:
        old_record(project, "reuse_proposal", {"proposal_id": "기존 후보"})
        notice = old_notice(project, "기존 후보", app.assessment(proposed, app.model()), automatic=True)
    row = next(line for line in project.journal.read_bytes().splitlines() if json.loads(line)["id"] == notice["id"])
    with project.journal.open("ab") as stream:
        stream.write(row + b"\n")  # Duplicate the exact original bytes; do not select either occurrence.
    before = project.journal.read_bytes()
    restarted = application(project, T0 + timedelta(hours=hours))
    status = restarted.suggestion_status()
    assert not status["allowed"] and status["wait_reason"] == "ambiguous_asset_history"
    assert status["ambiguous_event_ids"] == [notice["id"]]
    assert restarted.publish_suggestion()["published"] is None
    evaluated = suggestions.evaluate(restarted, restarted.model(), suggestions.DEFAULTS)
    assert evaluated["eligible"] == [] and evaluated["history_errors"][0]["code"] == "ambiguous_asset_history"
    assert project.journal.read_bytes() == before


@pytest.mark.parametrize("kind", ["mode", "choice", "identity"])
def test_ambiguous_mode_and_user_choices_are_conservative_without_arbitrary_replay(prepared, kind):
    project, source, proposed = qualified(prepared)
    app = application(project)
    event = app.set_mode("always_on")["event"]
    if kind == "choice":
        event = app.update(proposed["id"], expected="proposed", new="rejected", text="Stronger checks",
                           details={"reason_items": ["checks"]})["event"]
    elif kind == "identity":
        other = candidate(project, source, asset_id="other-rate")
        event = app.update(proposed["id"], expected="proposed", new="proposed", text="Same asset",
                           details={"identity": {"relation": "duplicate_of", "target": {
                               "type": "asset_candidate", "asset_id": "other-rate", "event_id": other["id"]}}})["event"]
    row = next(line for line in project.journal.read_bytes().splitlines() if json.loads(line)["id"] == event["id"])
    with project.journal.open("ab") as stream:
        stream.write(row + b"\n")
    before = project.journal.read_bytes()
    assert application(project, T0 + timedelta(days=2)).publish_suggestion()["published"] is None
    assert app.suggestion_status()["ambiguous_event_ids"] == [event["id"]]
    assert project.journal.read_bytes() == before


def test_unrelated_generic_ambiguity_does_not_erase_asset_history_or_add_a_new_gate(prepared):
    project, _, _ = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    note = core.record(project, "note", "An unrelated observation", origin=ORIGIN).event
    row = next(line for line in project.journal.read_bytes().splitlines() if json.loads(line)["id"] == note["id"])
    with project.journal.open("ab") as stream:
        stream.write(row + b"\n")
    assert app.publish_suggestion()["published"] is not None


@pytest.mark.parametrize("field", ["id", "seq"])
def test_unbound_known_notification_is_diagnosed_instead_of_becoming_absent_history(prepared, field):
    project, _, _ = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    notice = app.publish_suggestion()["published"]["notification"]
    lines = project.journal.read_bytes().splitlines()
    changed = json.loads(lines[-1])
    assert changed["id"] == notice["id"]
    changed.pop(field)
    project.journal.write_bytes(b"\n".join([*lines[:-1], json.dumps(changed).encode("utf-8")]) + b"\n")
    before = project.journal.read_bytes()
    restarted = application(project, T0 + timedelta(days=2))
    assert restarted.suggestion_status()["wait_reason"] == "ambiguous_asset_history"
    assert restarted.publish_suggestion()["published"] is None
    assert project.journal.read_bytes() == before


def test_legacy_namespaces_do_not_collide_but_explicit_derivation_unions_history(prepared):
    project, source, proposed = qualified(prepared)
    old = old_record(project, "reuse_proposal", {"proposal_id": "growth-rate"})
    app = application(project)
    assessed = app.assessment(proposed, app.model())
    old_notice(project, "growth-rate", assessed)
    preserved = project.journal.read_bytes()
    groups = suggestions.groups(app.model())
    assert groups["reuse_proposal:growth-rate"] != groups["asset_candidate:growth-rate"]
    derived = candidate(project, source, asset_id="derived-rate", derived_from={
        "type": "reuse_proposal", "proposal_id": "growth-rate", "event_id": old["id"]})
    groups = suggestions.groups(app.model())
    assert groups["reuse_proposal:growth-rate"] == groups["asset_candidate:derived-rate"]
    assert project.journal.read_bytes().startswith(preserved)
    assert derived["details"]["derived_from"]["proposal_id"] == "growth-rate"


def test_evidence_subset_of_other_legacy_history_is_suspected_and_requested_review_stays_available(prepared):
    project, _, proposed = qualified(prepared)
    app = application(project)
    old_record(project, "reuse_proposal", {"proposal_id": "기존 후보"})
    old_notice(project, "기존 후보", app.assessment(proposed, app.model()))
    app.set_mode("always_on")
    result = app.publish_suggestion()
    assert result["published"] is None
    assert result["suppressed"] == [{"asset_id": "growth-rate", "reason": "duplicate_suspicion"}]
    # Explicit review can inspect the candidate without erasing this independent history rule.
    requested = app.review("growth-rate")
    assert requested["assets"][0]["assessment"]["requirements"]["identity"] is False
    assert requested["assets"][0]["notification"]["details"]["trigger"] == "requested"


def test_identity_reason_snapshot_can_improve_with_confirmed_derivation(prepared):
    project, source, proposed = qualified(prepared)
    app = application(project)
    old = old_record(project, "reuse_proposal", {"proposal_id": "기존 후보"})
    old_notice(project, "기존 후보", app.assessment(proposed, app.model()))
    choice = core.update_asset(project, proposed["id"], expected="proposed", new="rejected", text="Clarify identity",
                               details={"reason_items": ["identity"]}, origin=ORIGIN)["event"]
    assert choice["details"]["requirements"]["identity"] is False
    assert choice["details"]["suggestion_history"] == "analysis_assets.suggestions/1"
    corrected = candidate(project, source, previous_event_id=proposed["id"], derived_from={
        "type": "reuse_proposal", "proposal_id": "기존 후보", "event_id": old["id"]})
    checked(project, corrected, source)
    effect(project, corrected)
    app.set_mode("always_on")
    assert app.publish_suggestion()["published"]["candidate"]["id"] == corrected["id"]


@pytest.mark.parametrize("known_snapshot", [True, False])
def test_source_reason_snapshot_only_improves_after_exact_source_is_confirmed(prepared, known_snapshot):
    project, source, proposed = prepared
    missing = copy.deepcopy(proposed["details"])
    missing.update(asset_id="missing-source")
    missing["code"].update(run_id="20260101-000000-unknown", path="runs/20260101-000000-unknown/code/growth.py")
    early = record(project, "asset_candidate", missing)
    choice = core.update_asset(project, early["id"], expected="proposed", new="deferred", text="Need a source",
                               details={"reason_items": ["source"]}, origin=ORIGIN)["event"]
    assert choice["details"]["requirements"]["source"] is False
    if not known_snapshot:
        # Old B3-a choices did not preserve source/identity requirements. Do not invent a past false value.
        lines = project.journal.read_bytes().splitlines()
        older = json.loads(lines[-1])
        older["details"].pop("suggestion_history")
        older["details"].pop("requirements")
        project.journal.write_bytes(b"\n".join([*lines[:-1], json.dumps(older).encode("utf-8")]) + b"\n")
    corrected = candidate(project, source, asset_id="missing-source", previous_event_id=early["id"])
    checked(project, corrected, source)
    effect(project, corrected)
    run(project, "different", "other")
    app = application(project)
    app.set_mode("always_on")
    published = app.publish_suggestion()["published"]
    if known_snapshot:
        assert published["candidate"]["id"] == corrected["id"]
    else:
        assert published is None


@pytest.mark.parametrize("damage", ["foreign", "missing", "ambiguous"])
def test_external_bad_identity_target_never_unions_unrelated_groups(prepared, damage):
    project, source, proposed = prepared
    target = {"type": "asset_candidate", "asset_id": "growth-rate", "event_id": proposed["id"]}
    derived = candidate(project, source, asset_id="other-rate", derived_from=target)
    lines = [json.loads(line) for line in project.journal.read_bytes().splitlines()]
    if damage == "ambiguous":
        lines.append(copy.deepcopy(proposed))
    else:
        changed = next(value for value in lines if value["id"] == derived["id"])
        altered = changed["details"]["derived_from"]
        if damage == "foreign":
            altered["project_id"] = "prj_ffffffffffffffff"
        else:
            altered["event_id"] = "e999"
        for ref in changed["refs"]:
            if ref.get("relation") == "derived_from":
                ref.update(altered)
                ref["relation"] = "derived_from"
    project.journal.write_bytes(b"".join(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n" for value in lines))
    groups = suggestions.groups(application(project).model())
    assert "asset_candidate:other-rate" not in groups or groups["asset_candidate:other-rate"] != groups.get("asset_candidate:growth-rate")


@pytest.mark.parametrize("kind", ["text", "same_evidence", "rerun", "other_version"])
def test_no_new_scored_evidence_does_not_reissue_a_candidate(prepared, kind):
    project, source, proposed = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    app.publish_suggestion()
    if kind == "text":
        candidate(project, source, previous_event_id=proposed["id"], title="More precise prose")
    elif kind == "same_evidence":
        core.record(project, "note", "Cite the same source again", refs=[{"type": "run", "run_id": source["id"]}], origin=ORIGIN)
    elif kind == "rerun":
        result = runner.execute(project, script=None, title="Rerun", rerun_of=source["id"], origin=ORIGIN)
        assert result["run"]["status"] == "ok"
    else:
        corrected = candidate(project, source, previous_event_id=proposed["id"])
        checked(project, proposed, run(project, "boundary", "boundary", boundary=True), boundary=True)
        effect(project, corrected)
    assert application(project, T0 + timedelta(days=2)).publish_suggestion()["published"] is None


def test_malformed_old_snapshot_is_conservative_and_legacy_automatic_consumes_window(prepared):
    project, _, proposed = qualified(prepared)
    app = application(project)
    old = old_record(project, "reuse_proposal", {"proposal_id": "old"})
    notice = old_notice(project, "old", app.assessment(proposed, app.model()), automatic=True)
    assert not app.suggestion_status()["allowed"]  # default mode remains off as well.
    app.set_mode("always_on")
    assert app.suggestion_status()["wait_reason"] == "window"
    groups = suggestions.groups(app.model())
    assert old["id"] != notice["id"] and suggestions.history(app.model(), groups)[0]["automatic"]
    # A choice from before the known requirements snapshot contract never invents source/identity improvements.
    assert not suggestions._fresh({"evidence": [{"type": "event", "event_id": "e999"}],
                                   "scores": {"demand": 3, "checks": 3, "scope": 2, "benefit": 2},
                                   "requirements": {"source": True}},
                                  {"valid_snapshot": False, "evidence": None, "scores": None}, reasons=["source"])

