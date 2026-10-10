"""Actual project records and stdlib runs: lifecycle, history and owned registration recovery."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from conftest import analytics_project, kit_cli
from jsonschema import Draft202012Validator, FormatChecker

from aelix_analytics import asset_contract as contract
from aelix_analytics import core, relations, runner, writer

ORIGIN = core.Origin("human", "sdk", "inferred")
SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/analytics-records.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def record(project, kind, details, refs=()):
    event = core.record(project, kind, "자산 기록", details=details, refs=refs, origin=ORIGIN).event
    VALIDATOR.validate(event)
    return event


def run(project, name, input_name, *, boundary=False):
    script = project.scripts / f"{name}.py"
    script.parent.mkdir(exist_ok=True)
    code = ("import json\nimport sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path.cwd()))\nfrom lib.growth import rate\n"
            f"rows = json.loads(Path('data/{input_name}.json').read_text(encoding='utf-8'))\n"
            "actual = [rate(r['n'], r['d']) for r in rows]\n"
            "assert all(v is None or isinstance(v, float) for v in actual)\n"
            + ("assert actual == [None]\n" if boundary else "assert actual == [1.0]\n")
            + "print(actual)\n")
    script.write_text(code, encoding="utf-8")
    result = runner.execute(project, script=f"scripts/{name}.py", title=name,
                            declared_inputs=[f"data/{input_name}.json"], origin=ORIGIN)
    assert result["run"]["status"] == "ok", result
    return result["run"]


def code_ref(run, item=None):
    item = item or run["script"]
    return {"type": "code_snapshot", "run_id": run["id"], "path": item["snapshot"],
            "digest": {"sha256": item["sha256"]}}


def candidate(project, source, **changes):
    library = next(m for m in source["modules"] if m["path"] == "lib/growth.py")
    d = {"asset_id": "growth-rate", "title": "성장률 계산", "problem": "Zero denominators must be explicit",
         "artifact_kind": "module", "conditions": "Matched periods", "exclusions": "Unmatched periods",
         "next_check": "Different inputs and zero denominator", "input_contract": "numerator and denominator",
         "output_contract": "float or explicit missing", "code": code_ref(source, library), **changes}
    return record(project, "asset_candidate", d, refs=[{"type": "run", "run_id": source["id"]}])


def checked(project, proposed, executed, *, boundary=False, result="passed"):
    d = {"asset_id": proposed["details"]["asset_id"], "candidate_event_id": proposed["id"],
         "check_run": {"type": "run", "run_id": executed["id"]}, "result": result, "boundary": boundary,
         "check_scope": "Growth rate assertions", "check_code": code_ref(executed),
         "code": proposed["details"]["code"], "artifact_digest": contract.bundle_digest(proposed["details"])}
    return record(project, "asset_check", d)


@pytest.fixture
def prepared(tmp_path):
    # runner.execute is an in-process SDK path; fixtures are independent projects, so none
    # inherits the preceding fixture's Python module object or namespace package path.
    sys.modules.pop("lib.growth", None)
    sys.modules.pop("lib", None)
    project = core.Project(analytics_project(tmp_path / "project", version="0.9.0"))
    (project.root / "lib").mkdir()
    (project.root / "lib/growth.py").write_text("def rate(n, d):\n    return None if d == 0 else float(n / d - 1)\n", encoding="utf-8")
    (project.root / "data").mkdir()
    for name, value in [("original", [{"n": 10, "d": 5}]), ("other", [{"n": 20, "d": 10}]),
                        ("boundary", [{"n": 2, "d": 0}])]:
        (project.root / f"data/{name}.json").write_text(json.dumps(value), encoding="utf-8")
    source = run(project, "source", "original")
    proposed = candidate(project, source)
    return project, source, proposed


def validated(prepared):
    project, source, proposed = prepared
    core.update_asset(project, proposed["id"], expected="proposed", new="trial", text="Try separate inputs", origin=ORIGIN)
    checked(project, proposed, source)
    checked(project, proposed, run(project, "different", "other"))
    checked(project, proposed, run(project, "boundary", "boundary", boundary=True), boundary=True)
    core.update_asset(project, proposed["id"], expected="trial", new="validated", text="Declared scope passed", origin=ORIGIN)
    return project, source, proposed


def test_success_is_separate_from_checks_and_registration_is_explicit(prepared):
    project, source, proposed = prepared
    assert writer.marker_state(project.root).value["min_writer_version"] == "0.10.0"
    assert core.assets(project)["assets"][0]["state"] == "proposed"
    with pytest.raises(core.AnalyticsError):
        core.update_asset(project, proposed["id"], expected="proposed", new="validated", text="Do not skip trial", origin=ORIGIN)
    core.update_asset(project, proposed["id"], expected="proposed", new="trial", text="Try", origin=ORIGIN)
    checked(project, proposed, source)
    with pytest.raises(core.AnalyticsError, match="different-input"):
        core.update_asset(project, proposed["id"], expected="trial", new="validated", text="Not enough", origin=ORIGIN)
    assert not (project.root / "lib/registered_growth.py").exists()


def test_real_independent_and_boundary_checks_register_the_preserved_version(prepared):
    project, _, proposed = validated(prepared)
    before = project.journal.read_bytes()
    result = core.register_asset(project, proposed["id"], name="registered_growth", origin=ORIGIN)
    assert result["registered"] is True and result["model_calls"] == 0
    assert (project.root / "lib/registered_growth.py").read_bytes() == (project.root / "lib/growth.py").read_bytes()
    registration = result["registration"]
    assert registration["loader_discovery"]["result"] == "not_run"
    assert (project.root / registration["source"] / "growth.py").read_bytes() == (project.root / "lib/growth.py").read_bytes()
    assert project.journal.read_bytes().startswith(before)
    assert core.assets(project)["assets"][0]["state"] == "registered"
    assert core.read_model(project).journal.replay().state_of(proposed["id"])["state"] == "registered"
    VALIDATOR.validate(result["event"])
    with pytest.raises(core.AnalyticsError):
        core.writer_for(project, ORIGIN).update(proposed["id"], expected="registered", new="deprecated", text="Use asset API")


def test_identical_preexisting_destination_without_an_owned_published_receipt_is_refused(prepared):
    project, _, proposed = validated(prepared)
    destination = project.root / "lib/registered_growth.py"
    destination.write_bytes((project.root / "lib/growth.py").read_bytes())
    before = project.journal.read_bytes()
    with pytest.raises(core.AnalyticsError) as error:
        core.register_asset(project, proposed["id"], name="registered_growth", origin=ORIGIN)
    assert error.value.code == "registration_exists"
    assert project.journal.read_bytes() == before
    assert core.assets(project)["assets"][0]["state"] == "validated"


def test_published_files_with_journal_failure_remain_unregistered_and_owned_retry_completes(prepared, monkeypatch):
    project, _, proposed = validated(prepared)
    append = writer.Locked.append

    def fail(held, kind, text, **kwargs):
        if kind == "record_update" and (kwargs.get("details") or {}).get("new_state") == "registered":
            raise OSError("injected journal failure")
        return append(held, kind, text, **kwargs)

    monkeypatch.setattr(writer.Locked, "append", fail)
    with pytest.raises(core.AnalyticsError) as error:
        core.register_asset(project, proposed["id"], name="registered_growth", origin=ORIGIN)
    assert error.value.code == "registration_incomplete" and error.value.details["recorded"] is False
    assert (project.root / "lib/registered_growth.py").exists()
    assert core.assets(project)["assets"][0]["state"] == "validated"
    monkeypatch.setattr(writer.Locked, "append", append)
    result = core.register_asset(project, proposed["id"], name="registered_growth", origin=ORIGIN)
    assert result["registered"]
    receipt = json.loads((project.root / result["registration"]["receipt"]).read_bytes())
    assert receipt["state"] == "recorded" and receipt["registration_event_id"] == result["event"]["id"]


@pytest.mark.parametrize("relation", ["supersedes", "updates", "resolves"])
def test_asset_input_cannot_change_an_unrelated_finding_or_metric(prepared, relation):
    project, source, proposed = prepared
    unrelated = core.record(project, "finding", "Unrelated original result", origin=ORIGIN).event
    before = project.journal.read_bytes()
    with pytest.raises(core.AnalyticsError) as error:
        core.record(
            project, "asset_candidate", "Revised", details={**proposed["details"], "previous_event_id": proposed["id"]},
            refs=[{"type": "event", "event_id": unrelated["id"], "relation": relation}], origin=ORIGIN)
    assert error.value.code == "bad_asset" and project.journal.read_bytes() == before
    assert core.read_model(project).journal.replay().state_of(unrelated["id"])["state"] == "current"


def test_bad_external_asset_record_keeps_raw_bytes_and_cannot_supersede_a_finding(prepared):
    project, _, proposed = prepared
    finding = core.record(project, "finding", "Keep me current", origin=ORIGIN).event
    raw = copy.deepcopy(proposed)
    raw.update(seq=999, id="e999")
    raw["refs"].append({"type": "event", "event_id": finding["id"], "relation": "supersedes",
                       "basis": "declared", "version_binding": "author"})
    with project.journal.open("ab") as stream:
        stream.write(json.dumps(raw).encode("utf-8") + b"\n")
    before = project.journal.read_bytes()
    model = core.read_model(project)
    assert model.journal.replay().state_of(finding["id"])["state"] == "current"
    assert model.journal.replay().state_of("e999") is None
    assert core.assets(project)["warnings"]
    assert project.journal.read_bytes() == before


def test_explicit_review_is_recorded_but_list_details_and_cli_reads_have_no_event(prepared):
    project, _, proposed = prepared
    before = project.journal.read_bytes()
    code, reply = kit_cli(project.root, "assets", "show", "growth-rate")
    assert code == 0 and reply["assets"][0]["title"] == "성장률 계산"
    assert project.journal.read_bytes() == before
    review = core.review_assets(project, "growth-rate", origin=ORIGIN)
    assert len(review["assets"]) == 1 and review["model_calls"] == 0
    notification = review["assets"][0]["notification"]
    assert notification["details"]["trigger"] == "requested"
    assert notification["details"]["candidate_event_id"] == proposed["id"]
    assert notification["details"]["context_version"]
    VALIDATOR.validate(notification)


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_skill_only_candidate_registers_without_a_python_implementation_or_host_dependency(tmp_path, newline):
    from asset_fixtures import skill_project

    project, proposed = skill_project(tmp_path / "skill-project", newline=newline)
    source_bytes = (project.root / "draft/SKILL.md").read_bytes()
    assert "code" not in proposed["details"]
    result = core.register_asset(project, proposed["id"], name="denominator-rule", origin=ORIGIN)
    target = project.root / ".aelix/skills/denominator-rule/SKILL.md"
    assert target.read_bytes() == source_bytes
    assert (project.root / "draft/SKILL.md").read_bytes() == source_bytes
    assert result["registered"] and result["registration"]["loader_discovery"]["result"] == "not_run"
    assert core.assets(project)["assets"][0]["state"] == "registered"


def test_a_private_receipt_finish_failure_does_not_hide_canonical_registered_success(prepared, monkeypatch):
    project, _, proposed = validated(prepared)
    write = writer.write_json

    def fail_finish(path, value, **kwargs):
        if str(path).endswith("receipt.json") and value.get("state") == "recorded":
            raise OSError("injected receipt finish failure")
        return write(path, value, **kwargs)

    monkeypatch.setattr(writer, "write_json", fail_finish)
    result = core.register_asset(project, proposed["id"], name="registered_growth", origin=ORIGIN)
    assert result["registered"] is True
    assert result["warnings"][0]["code"] == "registration_receipt_not_updated"
    assert core.assets(project)["assets"][0]["state"] == "registered"


def test_external_registered_update_without_a_complete_owned_receipt_is_not_applied(prepared):
    project, _, proposed = validated(prepared)
    d = {"contract": contract.CONTRACT, "asset_id": proposed["details"]["asset_id"],
         "candidate_event_id": proposed["id"], "expected_state": "validated", "new_state": "registered",
         "registration": {}}
    seq = relations.read_journal(project.root).next_seq
    raw = {"schema": "analytics.event/2", "seq": seq, "id": f"e{seq}", "kind": "record_update",
           "recorded_at": core.iso(), "actor": "human", "text": "Incomplete external registration claim",
           "refs": [contract.candidate_ref(d, "updates")], "details": d}
    with project.journal.open("ab") as stream:
        stream.write(json.dumps(raw).encode("utf-8") + b"\n")
    assert core.assets(project)["assets"][0]["state"] == "validated"
    assert core.read_model(project).journal.replay().state_of(proposed["id"])["state"] == "validated"


def test_a_check_with_no_sequence_is_preserved_but_never_strengthens_the_assessment(prepared):
    project, source, proposed = prepared
    d = contract.validate("asset_check", {"asset_id": "growth-rate", "candidate_event_id": proposed["id"],
                         "check_run": {"type": "run", "run_id": source["id"]}, "result": "passed", "boundary": True,
                         "check_scope": "Malformed envelope must not count", "check_code": code_ref(source),
                         "code": proposed["details"]["code"], "artifact_digest": contract.bundle_digest(proposed["details"])})
    raw = core.writer_for(project, ORIGIN)._event(999, "asset_check", "Malformed external check",
                                                 contract.semantic_refs("asset_check", d), d)
    raw["seq"] = None
    with project.journal.open("ab") as stream:
        stream.write(json.dumps(raw).encode("utf-8") + b"\n")
    application = core.asset_application(project, ORIGIN)
    assessed = application.assessment(proposed, application.model())
    assert assessed["scores"]["checks"] == 1 and not assessed["facts"]["passed_check"]
    assert core.assets(project)["warnings"]


def test_a_mismatched_human_state_update_does_not_claim_repeated_need(prepared):
    project, _, proposed = prepared
    d = {"contract": contract.CONTRACT, "asset_id": "growth-rate", "candidate_event_id": proposed["id"],
         "expected_state": "trial", "new_state": "trial", "demand": {"repeated_need": True}}
    raw = core.writer_for(project, ORIGIN)._event(999, "record_update", "Mismatched human update",
                                                 [contract.candidate_ref(d, "updates")], d)
    with project.journal.open("ab") as stream:
        stream.write(json.dumps(raw).encode("utf-8") + b"\n")
    application = core.asset_application(project, ORIGIN)
    assessed = application.assessment(proposed, application.model())
    assert assessed["scores"]["demand"] == 1 and not assessed["facts"]["human_repeated_need"]


def test_sdk_check_inside_a_running_script_is_validated_against_final_run_observations(prepared):
    project, _, proposed = prepared
    details = {'asset_id': 'growth-rate', 'candidate_event_id': proposed['id'], 'result': 'passed',
               'boundary': False, 'check_scope': 'Actual in-run assertions', 'code': proposed['details']['code'],
               'artifact_digest': contract.bundle_digest(proposed['details'])}
    source = ("import json, sys\nfrom pathlib import Path\nimport aelix_analytics as analytics\n"
              "sys.path.insert(0, str(Path.cwd()))\nfrom lib.growth import rate\n"
              "rows=json.loads(Path('data/other.json').read_text(encoding='utf-8'))\n"
              "assert [rate(r['n'],r['d']) for r in rows] == [1.0]\n"
              "run_id=analytics.run_id()\n"
              "current=json.loads(analytics.path(f'runs/{run_id}/run.json').read_text(encoding='utf-8'))\n"
              f"details={details!r}\n"
              "details['check_run']={'type':'run','run_id':run_id}\n"
              "details['check_code']={'type':'code_snapshot','run_id':run_id,'path':current['script']['snapshot'],"
              "'digest':{'sha256':current['script']['sha256']}}\n"
              "analytics.record('asset_check','In-run declaration',details=details)\n")
    (project.scripts / "sdk_check.py").write_text(source, encoding="utf-8")
    executed = runner.execute(project, script="scripts/sdk_check.py", title="In-run check",
                              declared_inputs=["data/other.json"], origin=ORIGIN)["run"]
    assert executed["status"] == "ok" and not executed.get("finalize_errors"), executed.get("error")
    application = core.asset_application(project, ORIGIN)
    assert contract.passed_checks(application.model().journal, proposed, application.model())


def test_asset_surfaces_share_prior_target_chronology_and_warn_without_rewriting(prepared):
    project, _, proposed = prepared
    d = {"contract": contract.CONTRACT, "asset_id": "growth-rate", "candidate_event_id": proposed["id"],
         "expected_state": "proposed", "new_state": "trial", "promotion": {"artifact_digest": contract.bundle_digest(proposed["details"]),
             "source_run_id": proposed["details"]["code"]["run_id"], "check_events": []}}
    raw = core.writer_for(project, ORIGIN)._event(1, "record_update", "Earlier than the target",
                                                 [contract.candidate_ref(d, "updates")], d)
    raw["id"] = "e999"
    with project.journal.open("ab") as stream:
        stream.write(json.dumps(raw).encode("utf-8") + b"\n")
    before = project.journal.read_bytes()
    assert core.assets(project)["assets"][0]["state"] == "proposed"
    assert core.assets(project)["warnings"]
    assert core.read_model(project).journal.replay().state_of(proposed["id"])["state"] == "proposed"
    assert project.journal.read_bytes() == before


def test_an_invented_promotion_proof_cannot_advance_a_source_less_draft(tmp_path):
    project = core.Project(analytics_project(tmp_path / "draft"))
    proposed = record(project, "asset_candidate", {"asset_id": "draft", "artifact_kind": "skill",
                      "title": "Early idea", "problem": "No artifact yet", "conditions": "", "exclusions": "",
                      "next_check": "", "input_contract": "", "output_contract": ""})
    d = {"contract": contract.CONTRACT, "asset_id": "draft", "candidate_event_id": proposed["id"],
         "expected_state": "proposed", "new_state": "trial",
         "promotion": {"artifact_digest": "a" * 64, "source_run_id": "absent", "check_events": []}}
    raw = core.writer_for(project, ORIGIN)._event(999, "record_update", "Invented proof",
                                                 [contract.candidate_ref(d, "updates")], d)
    with project.journal.open("ab") as stream:
        stream.write(json.dumps(raw).encode("utf-8") + b"\n")
    assert core.assets(project)["assets"][0]["state"] == "proposed"
    assert core.assets(project)["warnings"]
    assert core.read_model(project).journal.replay().state_of(proposed["id"])["state"] == "proposed"
