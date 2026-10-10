"""Publication boundaries use real child processes, preserved history and public surfaces."""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta

import pytest
from conftest import kit_env, record_observation
from test_asset_suggestions import T0, application, automatic, effect, qualified
from test_assets import ORIGIN, candidate, checked, run
from test_assets import prepared as prepared

from aelix_analytics import __main__ as entry
from aelix_analytics import asset_contract as contract
from aelix_analytics import asset_suggestions as suggestions
from aelix_analytics import core, relations, writer


def child_env(tmp_path, name, *, actor="human"):
    home = tmp_path / name
    home.mkdir()
    return kit_env(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / "config"),
                   AELIX_ANALYTICS_ACTOR=actor, AELIX_ANALYTICS_SESSION="",
                   AELIX_ANALYTICS_TRUST_FILE=str(home / "trust.json"))


def test_two_child_processes_publish_at_most_one_at_the_same_boundary(prepared, tmp_path):
    project, _, _ = qualified(prepared)
    application(project).set_mode("always_on")
    # Both children have imported the actual CLI before the parent releases their input gates.
    # Pipes work on all three supported OSes; no fork, signal or advisory-lock substitute.
    code = ("import sys\nfrom aelix_analytics.__main__ import main\n"
            "print('READY', flush=True)\nsys.stdin.readline()\n"
            "sys.exit(main(['--project', sys.argv[1], '--text', 'status']))\n")
    children = [subprocess.Popen([sys.executable, "-c", code, str(project.root)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                env=child_env(tmp_path, f"publication-child-{i}")) for i in range(2)]
    output = []
    try:
        for child in children:
            assert child.stdout.readline().strip() == "READY"
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        for child in children:
            stdout, stderr = child.communicate(timeout=60)
            assert child.returncode == 0, stderr
            output.append(stdout)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
    notices = automatic(project)
    assert len(notices) == 1
    assert sum("Asset suggestion (new," in value for value in output) == 1
    assert notices[0]["details"]["surface"] == "cli_text_status"
    record_observation("asset-two-process-publication", {"children": 2, "exit_codes": [c.returncode for c in children],
                       "automatic_events": len(notices), "publication_outputs": 1, "event_id": notices[0]["id"]})


@pytest.mark.parametrize("actor,args", [
    ("human", ["status"]), ("agent", ["--text", "status"]), ("test", ["--text", "status"]),
    *((actor, [*text, "status", "--part", part]) for actor in ("human", "agent")
      for part in ("records", "relations", "context") for text in ([], ["--text"])),
    ("human", ["context"]), ("human", ["assets", "list"]), ("human", ["assets", "show", "growth-rate"]),
])
def test_json_agent_and_every_detail_page_are_read_only(prepared, tmp_path, actor, args):
    project, _, proposed = qualified(prepared)
    application(project).set_mode("always_on")
    before = project.journal.read_bytes()
    if "relations" in args:
        args = [*args, "--id", proposed["id"]]
    result = subprocess.run([sys.executable, "-m", "aelix_analytics", "--project", str(project.root), *args],
                            capture_output=True, text=True, encoding="utf-8", timeout=60,
                            env=child_env(tmp_path, "read-only-child", actor=actor))
    assert result.returncode == 0, result.stderr + result.stdout
    assert project.journal.read_bytes() == before
    assert not automatic(project)


def test_output_failure_consumes_window_and_the_record_is_pending(prepared, monkeypatch):
    project, _, _ = qualified(prepared)
    application(project).set_mode("always_on")
    monkeypatch.setenv("AELIX_ANALYTICS_ACTOR", "human")

    def failed_output(*args, **kwargs):
        assert len(automatic(project)) == 1  # The journal must be durable before any output.
        raise OSError("injected output failure")

    monkeypatch.setattr(entry, "print", failed_output, raising=False)
    with pytest.raises(OSError, match="injected output"):
        entry.main(["--project", str(project.root), "--text", "status"])
    app = core.asset_application(project, core.Origin("human", "cli"))
    assert app.publish_suggestion()["published"] is None
    assert len(app.suggestion_status()["pending"]) == 1
    assert len(automatic(project)) == 1


def test_sdk_mode_and_status_use_the_shared_contract_without_automatic_publication(prepared, monkeypatch):
    import aelix_analytics as analytics

    project, _, proposed = qualified(prepared)
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(project.root))
    monkeypatch.setenv("AELIX_ANALYTICS_ACTOR", "human")
    assert analytics.set_asset_mode("always_on")["event"]["actor"] == "human"
    before = project.journal.read_bytes()
    for part in (None, "records", "relations", "context"):
        analytics.status(part=part, **({"id": proposed["id"]} if part == "relations" else {}))
    assert project.journal.read_bytes() == before and not automatic(project)
    monkeypatch.setenv("AELIX_ANALYTICS_ACTOR", "agent")
    with pytest.raises(core.AnalyticsError) as caught:
        analytics.set_asset_mode("on_demand")
    assert caught.value.code == "human_mode_required"
    assert project.journal.read_bytes() == before


def test_turning_mode_off_preserves_pending_and_never_scores(prepared, monkeypatch):
    project, _, proposed = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    app.publish_suggestion()
    app.set_mode("on_demand")
    monkeypatch.setattr(app, "assessment", lambda *a, **k: pytest.fail("off mode scored automatically"))
    before = project.journal.read_bytes()
    assert app.publish_suggestion()["published"] is None
    assert app.suggestion_status()["pending"][0]["candidate_event_id"] == proposed["id"]
    assert project.journal.read_bytes() == before
    core.update_asset(project, proposed["id"], expected="proposed", new="deferred", text="Later", origin=ORIGIN)
    assert app.suggestion_status()["pending"] == []


def test_min_score_and_longer_cooldown_are_applied_without_relaxing_requirements(prepared):
    project, source, proposed = qualified(prepared)
    marker = writer.marker_state(project.root).value
    writer.write_json(project.root / "analytics.json", {**marker, "asset_suggestions": {"min_score": 9, "cooldown_hours": 48}},
                      platform=project.platform)
    app = application(project)
    app.set_mode("always_on")
    assert app.publish_suggestion()["published"] is None  # 8 is below the configured threshold.
    effect(project, proposed, observed=True, source=source)
    assert app.publish_suggestion()["published"]["assessment"]["total"] == 9
    second = candidate(project, source, asset_id="second-rate")
    checked(project, second, source)
    effect(project, second, observed=True, source=source)
    assert application(project, T0 + timedelta(hours=24)).publish_suggestion()["published"] is None
    assert application(project, T0 + timedelta(hours=48)).publish_suggestion()["published"]["candidate"]["id"] == second["id"]
    config, errors = suggestions.settings({"asset_suggestions": {"min_score": 0, "cooldown_hours": 1e300}})
    assert not errors
    state = suggestions.window(relations.read_journal(project.root), config, T0 + timedelta(days=10))
    assert not state["allowed"] and state["next_automatic_at"] is None


def test_order_is_score_then_first_actual_qualification_then_name(prepared):
    project, source, proposed = prepared
    earlier = candidate(project, source, asset_id="z-earlier")
    checked(project, earlier, source)
    effect(project, earlier)
    run(project, "different", "other")
    checked(project, proposed, source)
    effect(project, proposed)
    app = application(project)
    config = suggestions.settings({})[0]
    evaluated = suggestions.evaluate(app, app.model(), config)["eligible"]
    assert [v["asset_id"] for v in evaluated] == ["z-earlier", "growth-rate"]
    assert evaluated[0]["first_threshold_seq"] < evaluated[1]["first_threshold_seq"]
    effect(project, proposed, observed=True, source=source)
    assert suggestions.evaluate(app, app.model(), config)["eligible"][0]["asset_id"] == "growth-rate"


def test_same_threshold_sequence_uses_asset_id_order(prepared):
    project, source, proposed = prepared
    other = candidate(project, source, asset_id="a-rate")
    for value in (proposed, other):
        checked(project, value, source)
        effect(project, value)
    run(project, "different", "other")
    app = application(project)
    values = suggestions.evaluate(app, app.model(), suggestions.DEFAULTS)["eligible"]
    assert [v["asset_id"] for v in values] == ["a-rate", "growth-rate"]
    assert values[0]["first_threshold_seq"] == values[1]["first_threshold_seq"]


@pytest.mark.parametrize("relation", ["duplicate_of", "merged_into"])
def test_explicit_identity_combines_choice_history_without_removing_records(prepared, relation):
    project, source, proposed = qualified(prepared)
    core.update_asset(project, proposed["id"], expected="proposed", new="rejected", text="Not now", origin=ORIGIN)
    other = candidate(project, source, asset_id="other-rate")
    checked(project, other, source)
    effect(project, other, observed=True, source=source)
    before = project.journal.read_bytes()
    target = contract.candidate_ref({"asset_id": "growth-rate", "candidate_event_id": proposed["id"]})
    core.update_asset(project, other["id"], expected="proposed", new="proposed", text="Same work",
                      details={"identity": {"relation": relation, "target": target}}, origin=ORIGIN)
    app = application(project)
    app.set_mode("always_on")
    assert app.publish_suggestion()["published"] is None
    assert project.journal.read_bytes().startswith(before)
    groups = suggestions.groups(app.model())
    assert groups["asset_candidate:other-rate"] == groups["asset_candidate:growth-rate"]


@pytest.mark.parametrize("change", ["foreign", "missing", "self", "malformed"])
def test_bad_identity_links_do_not_merge_history_or_write(prepared, change):
    project, source, proposed = prepared
    target = contract.candidate_ref({"asset_id": "growth-rate", "candidate_event_id": proposed["id"]})
    if change == "foreign":
        other = candidate(project, source, asset_id="other-rate")
        target = contract.candidate_ref({"asset_id": "other-rate", "candidate_event_id": other["id"]})
        target["project_id"] = "prj_ffffffffffffffff"
    elif change == "missing":
        target.update(asset_id="other-rate", event_id="e999")
    elif change == "malformed":
        target.pop("event_id")
    before = project.journal.read_bytes()
    with pytest.raises(core.AnalyticsError):
        core.update_asset(project, proposed["id"], expected="proposed", new="proposed", text="Bad link",
                          details={"identity": {"relation": "duplicate_of", "target": target}}, origin=ORIGIN)
    assert project.journal.read_bytes() == before

