"""tools/model_eval_compare.py: the comparison with aelix alone and its rules (docs/design/PRODUCT-EVALUATION.md §8).

Rows stand in for runs here, so each rule is tried on both sides of its line; one test reads stored runs
to see that a re-run replaces a run that did not execute and that a person's verdict on a critical check
counts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import model_eval as me  # noqa: E402
import model_eval_compare as mc  # noqa: E402


@pytest.fixture(scope="module")
def config():
    return me.load_config()


def row(task, cond, repeat=1, *, model="gpt-6.1-sol", ok=True, fact=(1, 1), evidence=(1, 1), limits=(0, 0),
        critical=(), unconfirmed=(), fabricated=(), tokens=(100, 0, 10)):
    """A run's row. `critical` fired and counts as real; those also in `unconfirmed` await a person, and
    the run would be an answer success were they misread when nothing else failed."""
    misread_ok = ok or (bool(critical) and set(critical) <= set(unconfirmed))
    return {"run": f"{task}-{model}-{cond}-r{repeat}", "out": "out", "model": model, "task": task,
            "kit": "analytics:0.32.0:26c28d0", "with_kit": cond == "A",
            "condition": cond, "surface": "default" if cond == "A" else "vanilla", "proposal": "unrequested",
            "repeat": repeat, "outcome": "success" if ok else "failure", "executed": True,
            "answer_success": ok, "answer_success_if_misread": misread_ok, "stale_confirmation": False,
            "session": None, "recall_only": False, "resume": list(fact), "recall": [0, 0], "recall_checks": {},
            "analysis_calls": 0, "fact": list(fact), "evidence": list(evidence), "limits": list(limits),
            "critical_fired": list(critical), "critical_real": list(critical), "critical_unconfirmed": list(unconfirmed),
            "fabricated_ids": list(fabricated), "tokens": list(tokens), "tool_calls": 3, "tool_errors": 0,
            "argument_errors": 0, "tools": {}, "raw_reads": 0, "elapsed_s": 10, "script": None,
            "context_block_bytes": None, "availability": [], "finished_at": "2026-10-31T00:00:00"}


def test_the_rules_hold_when_the_extension_does_better_and_harms_nothing(config):
    rows = []
    for r in (1, 2, 3):
        rows += [row("T01", "A", r, fact=(4, 4)), row("T01", "V", r, fact=(2, 4)),
                 row("T04", "A", r, evidence=(1, 1), limits=(1, 1)), row("T04", "V", r, evidence=(0, 1), limits=(0, 1)),
                 row("T07", "A", r), row("T07", "V", r)]
    verdict = mc.judge(config, rows)["gpt-6.1-sol"]
    rules = {k: v["verdict"] for k, v in verdict["rules"].items()}
    assert verdict["tier"] == "upper"
    assert rules == {"R1": "pass", "R2": "pass", "R3": "pass", "R4": "not_run", "R5": "pass", "R6": "report",
                     "R7": "not_run"}
    # R4 (Part II) has not run, so the gate waits.
    assert verdict["gate"] == "pending"
    assert verdict["rules"]["R1"]["A"] == 1.0 and verdict["rules"]["R1"]["V"] == 0.5


def test_each_rule_fails_on_its_own_line(config):
    # R1: pooled better, but one resume task worse.
    rows = [row("T01", "A", fact=(4, 4)), row("T01", "V", fact=(1, 4)),
            row("T03", "A", fact=(1, 3)), row("T03", "V", fact=(2, 3))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R1"]["verdict"] == "fail"
    # Both full is no difference, and passes (O6).
    rows = [row("T01", "A", fact=(4, 4)), row("T01", "V", fact=(4, 4))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R1"]["verdict"] == "pass"
    # R2: more ids that do not exist with the extension, in its evidence tasks.
    rows = [row("T04", "A", evidence=(1, 1), fabricated=("e999",)), row("T04", "V", evidence=(0, 1))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R2"]["verdict"] == "fail"
    rows = [row("T04", "A"), row("T04", "V"), row("T07", "A", fabricated=("e999",)), row("T07", "V")]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R2"]["verdict"] == "pass"
    # R2: pooled better, but one evidence task's limits worse.
    rows = [row("T04", "A", evidence=(3, 3), limits=(1, 1)), row("T04", "V", evidence=(0, 3), limits=(0, 1)),
            row("T05", "A", evidence=(1, 1), limits=(0, 1)), row("T05", "V", evidence=(1, 1), limits=(1, 1))]
    r2 = mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R2"]
    assert r2["verdict"] == "fail" and r2["A_below_V"] == {"evidence": [], "limits": ["T05"]}
    # R3: the upper model's bar is zero, the lower model's no more than aelix alone.
    rows = [row("T02", "A", critical=("old-rate",)), row("T02", "V", critical=("old-rate", "old-decision"))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R3"]["verdict"] == "fail"
    lower = [{**r, "model": "qwen3.8-27b-openrouter"} for r in rows]
    assert mc.judge(config, lower)["qwen3.8-27b-openrouter"]["rules"]["R3"]["verdict"] == "pass"
    # R3 whose verdict a person's confirmation could turn: provisional. One it could not turn is not.
    rows = [row("T02", "A", ok=False, critical=("old-rate",), unconfirmed=("old-rate",)), row("T02", "V")]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R3"]["verdict"] == "provisional"
    rows = [row("T02", "A"), row("T02", "V", ok=False, critical=("old-rate",), unconfirmed=("old-rate",))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R3"]["verdict"] == "pass"
    # R5: more than one run short on an analysis task is harm; one run is allowed.
    rows = [row("T07", "A", 1, ok=False), row("T07", "A", 2, ok=False), row("T07", "A", 3),
            row("T07", "V", 1), row("T07", "V", 2), row("T07", "V", 3)]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R5"]["verdict"] == "fail"
    rows[1] = row("T07", "A", 2)
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R5"]["verdict"] == "pass"
    # Short only by critical checks no person has confirmed: provisional, and the gate waits.
    rows[0] = row("T07", "A", 1, ok=False, critical=("wrong-rate",), unconfirmed=("wrong-rate",))
    rows[1] = row("T07", "A", 2, ok=False, critical=("wrong-rate",), unconfirmed=("wrong-rate",))
    verdict = mc.judge(config, rows)["gpt-6.1-sol"]
    assert verdict["rules"]["R5"]["verdict"] == "provisional" and verdict["gate"] == "pending"
    # R6 reports a task that costs more than 1.2x with the extension.
    rows = [row("T01", "A", tokens=(130, 0, 0)), row("T01", "V", tokens=(100, 0, 0))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R6"]["over_1_2x"] == [{"task": "T01", "A": 130, "V": 100}]
    # R7: the upper model's resume accuracy under 2/3 fails it.
    rows = [row("T01", "A", fact=(1, 2)), row("T01", "V", fact=(0, 2))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R7"]["verdict"] == "fail"
    assert mc.judge(config, [{**r, "model": "qwen3.8-27b-openrouter"} for r in rows])[
        "qwen3.8-27b-openrouter"]["rules"]["R7"]["verdict"] == "report"


def test_a_task_without_the_same_repeats_in_both_conditions_is_not_judged(config):
    """A task whose V runs did not all run (402) is not left out quietly: the rules over it wait."""
    rows = [row("T04", "A", r) for r in (1, 2, 3)] + [row("T04", "V", 1)]
    rows += [row(t, c, r) for t in ("T01", "T07") for c in "AV" for r in (1, 2, 3)]
    verdict = mc.judge(config, rows)["gpt-6.1-sol"]
    assert verdict["incomplete"] == ["T04"]
    assert {k: v["verdict"] for k, v in verdict["rules"].items() if k in ("R1", "R2", "R3", "R5")} == {
        "R1": "pass", "R2": "not_run", "R3": "not_run", "R5": "pass"}
    assert verdict["rules"]["R2"]["incomplete"] == ["T04"] and verdict["gate"] == "pending"
    # A confirmed context error with the extension fails the upper model's bar, whatever V ran.
    rows[0] = row("T04", "A", 1, ok=False, critical=("complete-claim",))
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R3"]["verdict"] == "fail"
    # With no V at all, nothing is compared.
    only_a = [r for r in rows if r["condition"] == "A"]
    assert {v["verdict"] for k, v in mc.judge(config, only_a)["gpt-6.1-sol"]["rules"].items()
            if k in ("R1", "R2", "R5")} == {"not_run"}


def chain_row(task, cond, repeat, session, recall=None, *, ok=True, calls=0, tokens=(100, 0, 10), recall_only=None):
    """A chain session's row: `recall` maps its recall checks to passed (None: not applicable)."""
    recall = recall or {}
    recalled = [sum(v is True for v in recall.values()), sum(v is not None for v in recall.values())]
    only = session == 3 if recall_only is None else recall_only
    return {**row(task, cond, repeat, ok=ok, tokens=tokens), "run": f"{task}-{cond}-r{repeat}-s{session}",
            "session": session, "recall_only": only, "resume": [0, 0] if only else recalled, "recall": recalled,
            "recall_checks": recall, "analysis_calls": calls, "fact": [1, 1] if ok else [0, 1]}


C2_RECALL = ("recall-value", "recall-direction", "recall-earlier")


def chain(cond, repeat, recalled, *, calls=0, first_ok=True):
    """C2's three sessions; session 3 recalls the value, its direction and the earlier number."""
    return [chain_row("C2", cond, repeat, 1, ok=first_ok), chain_row("C2", cond, repeat, 2),
            chain_row("C2", cond, repeat, 3, dict.fromkeys(C2_RECALL, recalled), calls=calls)]


def test_the_chains_judge_recall_and_redundant_work(config):
    """R4: A recalls more of what the earlier sessions found and runs no more analyses to answer a
    question about them; R7: A's recall is at least 2/3 for the upper model."""
    part1 = [row(t, c, r) for t in ("T01", "T04", "T07") for c in "AV" for r in (1, 2, 3)]
    rows = part1 + [x for r in (1, 2, 3) for x in chain("A", r, True) + chain("V", r, False, calls=4)]
    verdict = mc.judge(config, rows)["gpt-6.1-sol"]
    r4 = verdict["rules"]["R4"]
    assert r4["verdict"] == "pass" and r4["recall"] == {"A": 1.0, "V": 0.0} and r4["pairs"] == 9
    assert r4["redundant_analysis_calls"] == {"A": 0, "V": 12}
    assert verdict["rules"]["R7"] == {"resume_A": 1.0, "recall_A": 1.0, "floor": mc.FLOOR, "verdict": "pass"}
    assert verdict["rules"]["R5"]["tasks"] == ["T07", "C2/s1"] and verdict["gate"] == "pass"
    # Recomputing what an earlier session found, more than aelix alone did, fails R4.
    rows = part1 + [x for r in (1, 2, 3) for x in chain("A", r, True, calls=5) + chain("V", r, False, calls=4)]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R4"]["verdict"] == "fail"
    # A first session that fails with the extension where aelix alone's did not: R5.
    rows = part1 + [x for r in (1, 2, 3) for x in chain("A", r, True, first_ok=False) + chain("V", r, False)]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R5"]["A_short"] == ["C2/s1"]


def test_a_recall_pair_with_nothing_to_recall_leaves_both_and_too_many_withhold_r4(config):
    """O7: a pair where either side had nothing to recall is left out of both; past half of them, R4 waits."""
    rows = [x for r in (1, 2, 3) for x in chain("A", r, True) + chain("V", r, None)]
    r4 = mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R4"]
    assert r4["verdict"] == "withheld" and r4["pairs"] == 0 and r4["not_applicable_pairs"] == 9
    rows = [x for r in (1, 2, 3) for x in chain("A", r, True) + chain("V", r, None if r == 1 else False)]
    r4 = mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R4"]
    assert r4["verdict"] == "pass" and r4["pairs"] == 6 and r4["not_applicable_pairs"] == 3
    # A session that did not run had nothing to recall: its checks are pairs left out, not pairs dropped.
    rows = [x for r in (1, 2, 3) for x in chain("A", r, True) + chain("V", r, False)]
    rows = [x for x in rows if not (x["session"] == 3 and x["repeat"] in (1, 2))]
    r4 = mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R4"]
    assert r4["verdict"] == "withheld" and r4["pairs"] == 3 and r4["not_applicable_pairs"] == 6


def test_a_chain_where_one_side_had_nothing_to_resume_is_no_difference_for_r1(config):
    """C1's working session 2 recalls what session 1 named: where A's session 1 named nothing, the pair is
    left out of R1 too, not counted as A's failure to resume."""
    rows = [row("T01", c, r) for c in "AV" for r in (1, 2, 3)]
    for r in (1, 2, 3):
        for cond, value in (("A", None), ("V", True)):
            rows += [chain_row("C1", cond, r, 1), chain_row("C1", cond, r, 2, {"resume-c057": value, "resume-c035": None}),
                     chain_row("C1", cond, r, 3, {"recall-c057": value, "recall-max-z": None})]
    r1 = mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R1"]
    assert r1["verdict"] == "pass" and r1["A_below_V"] == []


def test_a_chain_costs_the_sum_of_its_sessions(config):
    rows = [x for r in (1, 2, 3) for x in chain("A", r, True) + chain("V", r, False)]
    rows = [{**x, "tokens": [300, 0, 0]} if x["condition"] == "A" else x for x in rows]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R6"]["over_1_2x"] == [{"task": "C2", "A": 900, "V": 330}]
    # A repeat whose last session did not run is no finished chain: it has no cost to compare.
    assert mc.successful_costs([x for x in rows if x["condition"] == "A" and x["session"] != 3], 3) == []


def test_a_call_that_runs_an_analysis_is_counted():
    bash = {"name": "bash"}
    assert mc.analysis_call({**bash, "args": {"command": "cd project && uv run python scripts/compare.py"}})
    assert mc.analysis_call({**bash, "args": {"command": "python3 -c 'import duckdb'"}})
    assert mc.analysis_call({"name": "analytics_query", "args": {"sql": "select 1"}})
    assert not mc.analysis_call({**bash, "args": {"command": "cat runs/x/run.json && ls scripts/"}})
    assert not mc.analysis_call({**bash, "args": {"command": "cat my_python_notes.txt"}})
    # The program by any path counts; a word that only names it does not; reading the kit's records does not.
    assert mc.analysis_call({**bash, "args": {"command": ".venv/bin/python scripts/recompute.py"}})
    assert mc.analysis_call({**bash, "args": {"command": "/usr/bin/python3 scripts/recompute.py"}})
    assert not mc.analysis_call({**bash, "args": {"command": "echo python"}})
    assert not mc.analysis_call({**bash, "args": {"command": "uv run --project /w/project analytics status"}})


def test_a_value_is_looked_for_as_a_text_holds_it():
    assert mc.check_tokens({"type": "ids_all", "values": ["e74", "20260803-191107-case-overwritten"]}) == [
        "e74", "20260803-191107-case-overwritten"]
    assert mc.check_tokens({"type": "number", "value": 5838}) == ["5838", "5,838"]
    assert mc.check_tokens({"type": "percent", "value": 0.20588}) == ["20.6"]
    assert mc.check_tokens({"type": "number_near", "percent": True, "value": 0.117849}) == ["11.8"]
    assert mc.check_tokens({"type": "text_any", "values": ["품목"]}) == []
    assert mc.held("… e74 …", "e74") and not mc.held("… e740 …", "e74")
    assert mc.held("합계 5,838건", "5,838")
    # A number is not a piece of another.
    assert not mc.held("세종 성장률은 12.4%", "2.4") and not mc.held("2.45배", "2.4") and mc.held("2.4배", "2.4")
    assert not mc.held("15,838건", "5,838") and not mc.held("5,838,000", "5,838") and mc.held("(5,838).", "5,838")


def stored(out: Path, name: str, record: dict, events: list | None = None) -> None:
    run = out / "runs" / name
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps(record), encoding="utf-8")
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events or []) + "\n", encoding="utf-8")


def record(task, surface, repeat, outcome, *, checks=(), finished="2026-10-31T01:00:00", reason=None):
    return {"run": f"{task}-qwen3.8-27b-openrouter-{surface}-unrequested-r{repeat}", "task": task, "repeat": repeat,
            "model": {"config": "qwen3.8-27b-openrouter"}, "outcome": outcome, "reason": reason,
            "conditions": {"surface": surface, "proposal": "unrequested", "kit": surface != "vanilla"},
            "score": {"result": outcome, "answer_result": outcome, "checks": list(checks),
                      "fact_recall": {"passed": 1, "total": 1}, "evidence": {"passed": 1, "total": 1},
                      "limits": {"passed": 0, "total": 0}, "fabricated_ids": []},
            "finished_at": finished, "tool_calls": [], "turns": [], "requests": []}


def test_a_rerun_replaces_a_run_that_did_not_execute_and_a_person_s_verdict_counts(config, tmp_path):
    first, rerun = tmp_path / "out", tmp_path / "rerun"
    critical = {"id": "old-rate", "kind": "critical", "required": True, "passed": False, "detail": {"fired": ["x"]}}
    stored(first, "a", record("T02", "default", 1, "error", reason="provider refused", finished="2026-10-31T01:00:00"))
    stored(rerun, "a", record("T02", "default", 1, "failure", checks=[critical], finished="2026-10-31T02:00:00"))
    stored(first, "v", record("T02", "vanilla", 1, "success"))
    rows, other, replaced = mc.collect(config, [first, rerun], {}, None)
    kept = {(r["condition"], r["repeat"]): r for r in rows}
    assert kept[("A", 1)]["out"] == str(rerun) and replaced == [f"{kept[('A', 1)]['run']} in {first}"]
    assert kept[("A", 1)]["critical_real"] == ["old-rate"] and not kept[("A", 1)]["answer_success"]
    # A person found the fired check a misreading: the answer is a success, and nothing is unconfirmed.
    name = kept[("A", 1)]["run"]
    rows, _, _ = mc.collect(config, [first, rerun], {name: {"finished_at": "2026-10-31T02:00:00",
                                                           "checks": {"old-rate": "misread"}}}, None)
    kept = {(r["condition"], r["repeat"]): r for r in rows}
    assert kept[("A", 1)]["answer_success"] and kept[("A", 1)]["critical_real"] == []
    assert kept[("A", 1)]["critical_unconfirmed"] == []
    # A confirmation of the run that finished earlier does not hold for a re-run under its name.
    rows, _, _ = mc.collect(config, [first, rerun], {name: {"finished_at": "2026-10-30T09:00:00",
                                                           "checks": {"old-rate": "misread"}}}, None)
    kept = {(r["condition"], r["repeat"]): r for r in rows}
    assert kept[("A", 1)]["stale_confirmation"] and not kept[("A", 1)]["answer_success"]
    assert kept[("A", 1)]["critical_unconfirmed"] == ["old-rate"]


def test_a_chain_s_sessions_are_runs_of_their_own(config, tmp_path):
    """The sessions of one chain repeat share task, condition and repeat: none of them replaces another."""
    out = tmp_path / "out"
    recall = {"id": "recall-value", "kind": "recall", "required": True, "passed": None, "not_applicable": "…"}
    for surface in ("default", "vanilla"):
        for step in (1, 2, 3):
            rec = record("C2", surface, 1, "success", checks=[recall] if step == 3 else [])
            rec["run"] += f"-s{step}"
            rec["chain"] = {"session": step, "sessions": 3, "recall_only": step == 3, "previous": None}
            stored(out, rec["run"], rec)
    rows, _, replaced = mc.collect(config, [out], {}, None)
    assert replaced == [] and sorted((r["condition"], r["session"]) for r in rows) == [
        (c, s) for c in "AV" for s in (1, 2, 3)]
    last = next(r for r in rows if r["condition"] == "A" and r["session"] == 3)
    assert last["recall_only"] and last["recall_checks"] == {"recall-value": None} and last["recall"] == [0, 0]


def test_a_rerun_chain_is_kept_whole(config, tmp_path):
    """A re-run of a chain's repeat in another OUT: the attempt that ran more is kept whole, never
    session 1 of one attempt and sessions 2-3 of the other."""
    first, rerun = tmp_path / "out", tmp_path / "rerun"
    for out, outcomes, at in ((first, ("success", "success", "success"), "01"), (rerun, ("success", "error", "not_run"), "05")):
        for step, outcome in enumerate(outcomes, 1):
            rec = record("C2", "default", 1, outcome, finished=f"2026-10-31T{at}:0{step}:00")
            rec["run"] += f"-s{step}"
            rec["chain"] = {"session": step, "sessions": 3, "recall_only": step == 3, "previous": None}
            stored(out, rec["run"], rec)
    rows, _, replaced = mc.collect(config, [first, rerun], {}, None)
    assert {r["out"] for r in rows} == {str(first)} and len(rows) == 3 and len(replaced) == 3


def test_another_kit_s_runs_are_listed_apart(config, tmp_path):
    """An older checkout's runs (#9's comparison) do not stand in for the candidate's."""
    new, old = tmp_path / "out", tmp_path / "old"
    stored(new, "a", {**record("T02", "default", 1, "failure"), "kit": {"generation": "analytics", "kit_version": "0.32.0"}})
    stored(new, "v", {**record("T02", "vanilla", 1, "success"), "kit": {"generation": "analytics", "kit_version": "0.32.0"}})
    stored(old, "a", {**record("T02", "default", 1, "success", finished="2026-10-31T09:00:00"),
                      "kit": {"generation": "lab", "kit_version": "0.2.0"}})
    rows, other, replaced = mc.collect(config, [new, old], {}, None)
    assert replaced == [] and {r["kit"] for r in rows} == {"analytics:0.32.0:"}
    assert [(r["surface"], r["condition"]) for r in other] == [("default (lab:0.2.0:)", None)]


def test_an_ids_any_check_is_held_by_its_minimum(config, tmp_path, monkeypatch):
    """T01's open questions ask for 3 of 28 ids: a run that received 3 had what it needed."""
    rec = record("T01", "default", 1, "failure", checks=[{"id": "open-ids", "kind": "evidence", "required": True,
                                                         "passed": False}])
    monkeypatch.setattr(mc, "received_texts", lambda run_dir: "e51 e70 e71")
    found = mc.availability(config, rec, tmp_path, None, {})
    assert [(a["where"], a["needed"]) for a in found] == [("received", 3)]
    monkeypatch.setattr(mc, "received_texts", lambda run_dir: "e51 e70")
    monkeypatch.setattr(mc, "kit_reads", lambda *a: {"context_block": "e85", "status": "", "ids": {}})
    assert [a["where"] for a in mc.availability(config, rec, tmp_path, tmp_path, {})] == ["available_not_received"]


def test_a_failed_check_is_told_received_offered_or_missing(config, tmp_path, monkeypatch):
    check = {"id": "cites", "kind": "evidence", "required": True, "passed": False}
    rec = record("T05", "default", 1, "failure", checks=[check])
    run = tmp_path / "run"
    run.mkdir()
    ids = config.answers["T05"]["checks"]
    wanted = next(c for c in ids if c["id"] == "cites")["values"]
    # Received: both run ids were in a tool result.
    (run / "events.jsonl").write_text(json.dumps({"type": "tool_execution_end", "tool_call_id": "c1",
                                                  "result": {"content": [{"type": "text", "text": " ".join(wanted)}]}})
                                      + "\n", encoding="utf-8")
    monkeypatch.setattr(mc, "received_texts", lambda run_dir: " ".join(wanted))
    assert [a["where"] for a in mc.availability(config, rec, run, None, {})] == ["received"]
    # Offered by a read of the kit, not received.
    monkeypatch.setattr(mc, "received_texts", lambda run_dir: "")
    monkeypatch.setattr(mc, "kit_reads", lambda *a: {"context_block": "", "status": "", "ids": {w: w for w in wanted}})
    assert [a["where"] for a in mc.availability(config, rec, run, tmp_path, {})] == ["available_not_received"]
    # In no read of the kit: a product gap.
    monkeypatch.setattr(mc, "kit_reads", lambda *a: {"context_block": "", "status": "", "ids": {}})
    assert [a["where"] for a in mc.availability(config, rec, run, tmp_path, {})] == ["not_available"]
    # A value the task asks the model to compute (an analysis task, E6) is not diagnosed.
    fact = {"id": "count", "kind": "fact", "required": True, "passed": False}
    assert mc.availability(config, record("T06", "default", 1, "failure", checks=[fact]), run, tmp_path, {}) == []
