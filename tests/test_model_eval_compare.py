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
    return {"run": f"{task}-{model}-{cond}-r{repeat}", "out": "out", "model": model, "task": task,
            "condition": cond, "surface": "default" if cond == "A" else "vanilla", "proposal": "unrequested",
            "repeat": repeat, "outcome": "success" if ok else "failure", "executed": True,
            "answer_success": ok, "fact": list(fact), "evidence": list(evidence), "limits": list(limits),
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
    # R2: more ids that do not exist with the extension.
    rows = [row("T04", "A", evidence=(1, 1), fabricated=("e999",)), row("T04", "V", evidence=(0, 1))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R2"]["verdict"] == "fail"
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
    # R3 rests on a critical check no person has confirmed: provisional.
    rows = [row("T02", "A"), row("T02", "V", critical=("old-rate",), unconfirmed=("old-rate",))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R3"]["verdict"] == "provisional"
    # R5: more than one run short on an analysis task is harm; one run is allowed.
    rows = [row("T07", "A", 1, ok=False), row("T07", "A", 2, ok=False), row("T07", "A", 3),
            row("T07", "V", 1), row("T07", "V", 2), row("T07", "V", 3)]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R5"]["verdict"] == "fail"
    rows[1] = row("T07", "A", 2)
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R5"]["verdict"] == "pass"
    # R6 reports a task that costs more than 1.2x with the extension.
    rows = [row("T01", "A", tokens=(130, 0, 0)), row("T01", "V", tokens=(100, 0, 0))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R6"]["over_1_2x"] == [{"task": "T01", "A": 130, "V": 100}]
    # R7: the upper model's resume accuracy under 2/3 fails it.
    rows = [row("T01", "A", fact=(1, 2)), row("T01", "V", fact=(0, 2))]
    assert mc.judge(config, rows)["gpt-6.1-sol"]["rules"]["R7"]["verdict"] == "fail"
    assert mc.judge(config, [{**r, "model": "qwen3.8-27b-openrouter"} for r in rows])[
        "qwen3.8-27b-openrouter"]["rules"]["R7"]["verdict"] == "report"


def test_a_value_is_looked_for_as_a_text_holds_it():
    assert mc.check_tokens({"type": "ids_all", "values": ["e74", "20260803-191107-case-overwritten"]}) == [
        "e74", "20260803-191107-case-overwritten"]
    assert mc.check_tokens({"type": "number", "value": 5838}) == ["5838", "5,838"]
    assert mc.check_tokens({"type": "percent", "value": 0.20588}) == ["20.6"]
    assert mc.check_tokens({"type": "number_near", "percent": True, "value": 0.117849}) == ["11.8"]
    assert mc.check_tokens({"type": "text_any", "values": ["품목"]}) == []
    assert mc.held("… e74 …", "e74") and not mc.held("… e740 …", "e74")
    assert mc.held("합계 5,838건", "5,838")


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
    rows, _, _ = mc.collect(config, [first, rerun], {kept[("A", 1)]["run"]: {"old-rate": "misread"}}, None)
    kept = {(r["condition"], r["repeat"]): r for r in rows}
    assert kept[("A", 1)]["answer_success"] and kept[("A", 1)]["critical_real"] == []
    assert kept[("A", 1)]["critical_unconfirmed"] == []


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
