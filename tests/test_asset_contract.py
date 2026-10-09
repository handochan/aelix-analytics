"""Public value and deterministic policy boundaries, separate from actual project I/O tests."""

from __future__ import annotations

import copy

import pytest

from aelix_analytics import asset_contract as contract
from aelix_analytics import asset_policy

SOURCE = {"type": "code_snapshot", "run_id": "20261009-100000-source", "path": "runs/20261009-100000-source/code/source.py",
          "digest": {"sha256": "a" * 64}}


def proposed(**changes):
    return {"asset_id": "growth-rate", "title": "성장률 계산", "problem": "Zero denominators",
            "artifact_kind": "module", "conditions": "Sorted monthly data", "exclusions": "Unmatched periods",
            "next_check": "Use another period and a zero denominator", "input_contract": "month, numerator, denominator",
            "output_contract": "finite growth or explicit missing", "code": SOURCE, **changes}


def test_version_identity_and_authored_unicode_are_preserved_without_mutating_input():
    source = proposed()
    before = copy.deepcopy(source)
    result = contract.validate("asset_candidate", source)
    assert source == before
    assert result["title"] == "성장률 계산"
    assert result["code"]["digest"]["sha256"] == "a" * 64
    assert contract.bundle_digest(result) == contract.bundle_digest(source)
    assert contract.semantic_refs("asset_candidate", result) == [{**result["code"], "relation": "uses"}]


@pytest.mark.parametrize("relation", ["supersedes", "updates", "resolves", "about", "uses"])
def test_public_source_refs_cannot_change_an_unrelated_records_state_or_forge_a_semantic_target(relation):
    with pytest.raises(contract.InvalidAsset):
        contract.input_refs([{"type": "event", "event_id": "e99", "relation": relation}])


def test_missing_or_extra_generated_edges_are_not_valid_reader_evidence():
    value = contract.validate("asset_candidate", proposed(previous_event_id="e3"))
    refs = contract.semantic_refs("asset_candidate", value)
    assert contract.record_problem("asset_candidate", value, refs) is None
    assert contract.record_problem("asset_candidate", value, refs + [{"type": "event", "event_id": "e99",
                                                                      "relation": "supersedes"}])
    assert contract.record_problem("asset_candidate", value, refs[:-1])
    assert contract.record_problem("asset_candidate", value, refs + refs[:1])


def test_skill_only_bundle_does_not_need_a_python_implementation_or_translate_the_skill():
    value = proposed(artifact_kind="skill", files=[{"path": "SKILL.md", "ref": {"type": "data_observation",
                     "run_id": "20261009-100000-source", "role": "input", "path": "draft/SKILL.md",
                     "digest": {"sha256": "b" * 64}}}])
    del value["code"]
    result = contract.validate("asset_candidate", value)
    assert result["files"][0]["path"] == "SKILL.md"
    assert "code" not in result
    assert contract.bundle_digest(result)


@pytest.mark.parametrize("path", ["../SKILL.md", "/SKILL.md", "folder/../SKILL.md", "folder\\SKILL.md", "con.txt", "a."])
def test_artifact_bundle_is_a_relative_portable_tree(path):
    with pytest.raises(contract.InvalidAsset):
        contract.files(proposed(files=[{"path": path, "ref": SOURCE}]))


def test_unicode_normalization_or_case_cannot_create_duplicate_bundle_files():
    for left, right in [("Résumé.py", "Re\u0301sume\u0301.py"), ("A.py", "a.py")]:
        with pytest.raises(contract.InvalidAsset):
            contract.files(proposed(artifact_kind="package", files=[{"path": left, "ref": SOURCE},
                                                                    {"path": right, "ref": SOURCE}]))


def test_past_unicode_proposal_identity_is_accepted_as_an_exact_legacy_target():
    legacy = {"type": "reuse_proposal", "proposal_id": "기존 후보", "event_id": "e4"}
    result = contract.validate("asset_candidate", proposed(derived_from=legacy))
    assert result["derived_from"]["proposal_id"] == "기존 후보"


@pytest.mark.parametrize("kind", contract.SYSTEM_KINDS)
def test_generic_input_cannot_forge_mode_or_notification_bookkeeping(kind):
    with pytest.raises(contract.InvalidAsset, match="not a public"):
        contract.validate(kind, {})


def test_notification_read_basis_keeps_unknown_context_explicitly_null():
    d = {"asset_id": "growth-rate", "candidate_event_id": "e4", "trigger": "requested", "surface": "cli",
         "as_of_seq": 5, "context_version": None, "evidence": [], "scores": {}, "requirements": {}, "policy_version": "v1"}
    assert contract.validate("asset_notification", d, system=True)["context_version"] is None
    del d["context_version"]
    with pytest.raises(contract.InvalidAsset, match="context_version"):
        contract.validate("asset_notification", d, system=True)


@pytest.mark.parametrize("facts,expected", [
    ({}, {"demand": 0, "checks": 0, "scope": 0, "benefit": 0}),
    ({"independent_uses": 1, "source_confirmed": True}, {"demand": 1, "checks": 1, "scope": 0, "benefit": 0}),
    ({"human_repeated_need": True, "passed_check": True, "conditions": "x", "exclusions": "y", "estimated_effect": True},
     {"demand": 2, "checks": 2, "scope": 2, "benefit": 1}),
    ({"independent_uses": 2, "passed_distinct_or_boundary": True, "conditions": "x", "exclusions": "y", "observed_effect": True},
     {"demand": 3, "checks": 3, "scope": 2, "benefit": 2}),
])
def test_recorded_facts_get_adopted_scores_and_estimates_never_become_observed_effect(facts, expected):
    result = asset_policy.assessed(asset_policy.DeterministicPolicy(), facts)
    assert result["scores"] == expected
    assert result["total"] == sum(expected.values())


def test_a_replacement_policy_cannot_change_scores_or_claim_a_low_evidence_candidate_is_qualified():
    class Broken:
        def assess(self, facts, *, min_score=8):
            return {"scores": {"demand": 3, "checks": 3, "scope": 2, "benefit": 2}, "total": 10,
                    "requirements": {}, "qualified": True}

    with pytest.raises(ValueError, match="shared deterministic"):
        asset_policy.assessed(Broken(), {})
