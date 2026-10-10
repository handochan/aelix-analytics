"""Model-free score predicates of ADR-0008; no I/O, history, host or model calls.

The application supplies confirmed version-specific facts. A policy decides scores, never
whether it may override a user choice, a rejection, identity history or a publication window.
"""

from __future__ import annotations

from typing import Protocol

VERSION = "analysis_assets.policy/1"


class ProposalPolicy(Protocol):
    def assess(self, facts: dict, *, min_score: int = 8) -> dict: ...


class DeterministicPolicy:
    def assess(self, facts: dict, *, min_score: int = 8) -> dict:
        independent = facts.get("independent_uses", 0)
        demand = (3 if independent >= 2 else 2 if facts.get("human_repeated_need") else
                  1 if independent == 1 else 0)
        checks = (3 if facts.get("passed_distinct_or_boundary") else 2 if facts.get("passed_check") else
                  1 if facts.get("source_confirmed") else 0)
        scope = int(bool(facts.get("conditions"))) + int(bool(facts.get("exclusions")))
        benefit = 2 if facts.get("observed_effect") else 1 if facts.get("estimated_effect") else 0
        scores = {"demand": demand, "checks": checks, "scope": scope, "benefit": benefit}
        requirements = {"source": bool(facts.get("source_confirmed") and facts.get("problem")
                                        and facts.get("artifact_kind")),
                        "demand": demand >= 2, "checks": checks >= 2,
                        "scope": scope == 2, "benefit": benefit >= 1}
        total = sum(scores.values())
        return {"scores": scores, "total": total, "requirements": requirements,
                "qualified": all(requirements.values()) and total >= min_score,
                "policy_version": VERSION}


def assessed(policy: ProposalPolicy, facts: dict, *, min_score: int = 8) -> dict:
    """Replacement policies retain the shared deterministic contract for each supplied fact set."""
    if type(min_score) is not int or not 0 <= min_score <= 10:
        raise ValueError("min_score is an integer from 0 to 10")
    result = policy.assess(facts, min_score=min_score)
    standard = DeterministicPolicy().assess(facts, min_score=min_score)
    if not isinstance(result, dict) or any(result.get(k) != standard[k] for k in
                                          ("scores", "total", "requirements", "qualified")):
        raise ValueError("ProposalPolicy does not implement the shared deterministic score contract")
    # Application bookkeeping must not mutate a conforming policy's memoized result.
    return {**result, "scores": dict(result["scores"]), "requirements": dict(result["requirements"]),
            "policy_version": VERSION}
