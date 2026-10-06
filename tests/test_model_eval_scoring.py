"""The scoring of tools/model_eval.py against adversarial answers (Issue #40 reviews of 29c7c4b).

Each wrong answer a reviewer wrote must fail, and each right answer written another way (another
order, another notation, a table, English) must pass: closed questions said to be open with a
negated resolved word, a cut record called intact, a sign or a direction flipped, swapped metrics,
contradictory or substring verdicts, a failed run said to have left nothing, an overwritten table
said to be the same or lost. The other rules the reviews asked for are here too: numbers with their
sign and in other notations, verdicts read per claim, an id that does not exist and the free-analysis
task set needs_review, T12's results (not activity counts) read from a project, tokens that a request
did not report stay not measured, the summary keeps partial usage out of the medians, paths that
leave the work root, a checkout inside another repository, a seed the answers are not for and run
directories that exist are refused before anything runs, and a secret is overwritten in files and
records.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "model_eval.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("model_eval_scoring_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


me = load_tool()
CONFIG = me.load_config()
EMPTY = {"new_records": [], "new_runs": [], "context_changed": False}
QUERIED = [{"name": "analytics_query"}]
T01_HEAD = "목표: 다음 분기 계획의 근거. 핵심 질문: 온라인 비중, 센서 이상. 다음 할 일: 같은 기간끼리 비교하는 기준.\n"
T04_HEAD = "진행 메모 3의 할 일: 모바일 세션 — 같은 기간끼리 다시 비교한다.\n"
T10_TAIL = "\nMax |z| = 4.3612; same training data. Run 20260916-214600-03-sample-zscores."


def recorded(text: str) -> dict:
    return {"new_records": [{"id": "e99", "kind": "finding", "text": text}], "new_runs": [],
            "context_changed": False}


def scored(task: str, text: str, state=None, calls=None, existing=None) -> dict:
    ev = me.Evidence(text=text, last_turn=text, calls=list(calls or []),
                     tools=me.GENERATIONS["analytics"]["tools"], state=state or EMPTY,
                     existing_ids=set(existing or ()))
    return me.score(CONFIG.answers[task], ev, CONFIG.words)


def failed_checks(result: dict) -> list[str]:
    return [c["id"] for c in result["checks"] if c["required"] and not c["passed"]]


# (task, answer, project state, tool calls, a check that must catch it)
WRONG = [
    ("T01", T01_HEAD + "아직 답이 나오지 않은 질문: e20, e21, e33, e51, e70 / 할 일: e11, e43, e52",
     None, None, "closed-as-open"),
    ("T01", T01_HEAD + "- e20 센서 07의 압력 — 계절 효과? (미해결)\n- e56 부산 매장 매출 (미해결)\n- e51 (미해결)\n"
     "- e70 (미해결)\n- e71 (미해결)\n- e11 할 일 (미완료)", None, None, "closed-as-open"),
    ("T01", T01_HEAD + "열린 질문: e51, e70, e71, e20(해결되지 않음).", None, None, "closed-as-open"),
    ("T04", T04_HEAD + "e74의 글은 4,000자이며 끝까지 온전합니다. 잘린 곳은 없습니다.", None, None, "complete-claim"),
    ("T04", T04_HEAD + "e74 is intact; nothing is cut.", None, None, "complete-claim"),
    ("T04", T04_HEAD + "e74는 4,000자입니다. 기록은 온전히 남아 있습니다.", None, None, "complete-claim"),
    ("T08", "쓰면 안 됩니다. 9월은 20일까지만 있는 부분 월입니다(e20). 같은 일수로 비교하면 9월 1–20일은 8월 1–20일보다 "
     "20.6% 감소했습니다(e22).", recorded("같은 일수 비교 20.6% 감소"), None, "growth-as-decline"),
    ("T08", "9월은 부분 월이므로 e20은 잘못된 비교입니다. 같은 일수의 올바른 비교(e22)는 -20.6%, 즉 감소입니다.", None, None,
     "growth"),
    ("T10", "e8: not supported.\ne15: false, max |z| is 4.3612.\ne16: undetermined, the profile was estimated "
     "on the same data.\ne20: false. Runs 20260916-214600-03-sample-zscores.", None, None, "verdicts"),
    ("T10", "e8: incorrect.\ne15: false.\ne16: unverified.\ne20: false." + T10_TAIL, None, None, "verdicts"),
    ("T10", "e8: supported? No — e8 is false.\ne15: false.\ne16: unverified.\ne20: false." + T10_TAIL, None, None,
     "verdicts"),
    ("T11", "실패한 run 20260916-214200-03-sample-zscores는 아무 파일도 차트도 남기지 않았다.\ntimepoint_profile과 "
     "z-distribution, pressure-band는 성공한 run이 만들었다.\n지금 sample_scores는 20260916-214600-03-sample-zscores가 "
     "만들었다.", None, None, "left-nothing"),
    ("T05", "같습니다. 20260803-191107-case-overwritten이 쓴 표는 지금도 그대로입니다. 20260804-032539-case-overwritten은 "
     "다른 표를 바꿨고 원래 내용을 확인할 수 없는 문제는 없습니다.", None, None, "unchanged-claim"),
    ("T05", "같지 않습니다. 지금 표는 20260804-032539-case-overwritten이 덮어썼습니다. 20260803-191107-case-overwritten이 쓴 "
     "내용은 sha256만 남아 있어 볼 수 없습니다.", None, None, "lost-claim"),
    ("T07", "기록대로 46.8%가 맞습니다(e16). e18의 11.8%는 분모를 잘못 잡은 값이 아닙니다.", recorded("11.8% 아님"), None,
     "wrong-rate"),
    ("T07", "맞는 값은 11.8%(e18). e16은 고객 수를 분모로 쓴 오류. 정의 정정.\n현재 세션당 전환율은 46.8%입니다.",
     recorded("전환율 11.8%"), None, "wrong-rate"),
    ("T02", "세종 3분기 성장률은 12.4%로 쓰세요(e41). e287의 9.8%는 이전 계산입니다. 비교는 같은 기간으로(e274).", None,
     None, "old-rate"),
    ("T02", "9.8%(e287), 같은 기간 비교(e274).\n현재 성장률은 12.4%입니다.", None, None, "old-rate"),
    ("T06", "There are -5,838 sessions.", None, QUERIED, "count"),
    ("T06", "5,838 is the old wrong count. The actual answer is 999 sessions.", None, QUERIED, "count"),
    ("T06", "세션 수는 5,838개가 아니라 5,804개입니다.", None, QUERIED, "count"),
    ("T09", "서울은 445,969,600원(e10), 8월 전체 매출은 32,586,900원(e14). 품목 join과 단위 오류를 정정했습니다.", None, None,
     "seoul"),
]


@pytest.mark.parametrize("task, text, state, calls, catches", WRONG,
                         ids=[f"{w[0]}-{i}" for i, w in enumerate(WRONG)])
def test_a_wrong_answer_fails(task, text, state, calls, catches):
    result = scored(task, text, state, calls)
    assert result["result"] == "failure"
    assert catches in failed_checks(result) + result["critical"]
    critical = {c["id"] for c in CONFIG.answers[task]["checks"] if c["kind"] == "critical"}
    if catches in critical:
        assert catches in result["needs_review"]


T08_RECORD = recorded("같은 일수 비교 20.6% 증가")
RIGHT = [
    ("T05", "같지 않습니다. 지금 data/derived/case_overwritten.parquet는 뒤의 run 20260804-032539-case-overwritten이 덮어쓴 "
     "바이트입니다.\n20260803-191107-case-overwritten이 쓴 원래 표는 그 run의 차트 데이터(runs/20260803-191107-case-"
     "overwritten/charts/overwritten.data.parquet)에 sha256 dd52dc…까지 같은 바이트로 남아 있어 지금도 볼 수 있습니다.",
     None, None),
    ("T05", "No. The current bytes of data/derived/case_overwritten.parquet are the ones run 20260804-032539-case-"
     "overwritten wrote (it overwrote the file). The table run 20260803-191107-case-overwritten wrote is not in that "
     "path any more, but its chart data runs/20260803-191107-case-overwritten/charts/overwritten.data.parquet holds "
     "the same bytes (sha256 dd52dc15…), so you can still open it.", None, None),
    ("T06", "There are 5 838 sessions in August 2026.", None, QUERIED),
    ("T06", "There are 5.838e3 sessions in August 2026.", None, QUERIED),
    ("T06", "August 2026 has 5838 sessions.", None, QUERIED),
    ("T06", "The sessions dataset has **5,838 sessions** in August 2026 (session dates 2026-08-01 to 2026-08-31).",
     None, QUERIED),
    ("T09", "서울 지역 주문 금액 합계는 3,258만 6,900원입니다(e10). 8월 전체 매출은 4억 4,596만 9,600원입니다(e14). e8은 품목 "
     "결합으로 중복, e12는 천원 단위를 원으로 바꾸지 않음.", None, None),
    ("T09", "서울은 3,258.69만원(e10), 8월 전체 매출은 44,596.96만원(e14). 품목 join과 단위 오류를 정정했습니다.", None, None),
    ("T09", "### 서울 지역 주문 금액 합계\n- 값: 32,586,900원 (e10)\n- e8의 68,551,800원은 품목 결합으로 중복된 값\n"
     "### 8월 전체 매출\n- 값: 445,969,600원 (e14)\n- e12는 천원 단위를 바꾸지 않은 값", None, None),
    ("T07", "맞는 값은 0.118(688/5,838, e18)입니다. e16은 분모를 고객 수로 나눠 틀렸습니다.", recorded("0.118"), None),
    ("T07", "맞는 값은 11.8%(e18). e16은 고객 수를 분모로 쓴 오류. 정의 정정.",
     recorded("전환율 688/5838 = 0.117849"), None),
    ("T07", "맞는 값은 11.8%(e18)이고 e16의 46.8%는 고객 수를 분모로 써서 틀렸습니다.", recorded("전환율 11.8%"), None),
    ("T10", "e8은 뒷받침된다. e15와 e20은 거짓이다(최대 |z| 4.3612, 20260916-214600-03-sample-zscores). e16은 같은 학습 "
     "데이터로 기준을 추정해 판단할 수 없다.", None, None),
    ("T10", "| 주장 | 판정 |\n| e8 | 뒷받침 |\n| e15 | 거짓 |\n| e16 | 판단 불가 |\n| e20 | 거짓 |\n최대 |z| 4.3612"
     "(20260916-214600-03-sample-zscores), 같은 학습 데이터로 추정.", None, None),
    ("T10", "Supported: e8\nFalse: e15\nUnverified: e16\nFalse: e20" + T10_TAIL, None, None),
    ("T10", "e8: supported.\ne15: false — the correct maximum |z| is 4.3612 (run 20260916-214600-03-sample-zscores)."
     "\ne16: cannot be verified; the profile was estimated on the same training data.\ne20: false.", None, None),
    ("T10", "### e8\n판정: 뒷받침된다. 3σ 밖은 pressure C057 하나.\n### e15\n판정: 거짓. 최대 |z| 4.3612(20260916-214600-"
     "03-sample-zscores), 이를 뒷받침하는 근거는 점수 표.\n### e16\n판정: 판단할 수 없다. 같은 학습 데이터로 추정.\n### e20\n"
     "판정: 거짓.", None, None),
    ("T03", "e24: 반품률이 높은 주문은 야간 배송(밤 10시–새벽 2시)에 몰려 있고 낮 배송의 2.4배입니다.", None, None),
    ("T01", "목표: 다음 분기 계획의 근거를 남긴다. 핵심 질문: 온라인 비중, 센서 이상. 다음 할 일: 같은 기간끼리 비교하는 기준을 "
     "정한다.\n열린 질문: e51, e70, e71.\n- e56(부산 매장 매출)은 e139가 답해 목록에서 뺐다.\n- e20은 e103이 해결했다.",
     None, None),
    ("T01", "목표: 다음 분기 계획. 핵심 질문: 온라인 비중, 센서 이상. 다음 할 일: 같은 기간끼리 비교.\n열린 질문: e51, e70, "
     "e71.\n(e56 → e139)", None, None),
    ("T08", "결론: e20의 22.0% 감소 수치는 다음 분기 계획에 쓰면 안 됩니다.\n9월 데이터는 9월 20일까지만 있습니다. 같은 "
     "1–20일끼리 비교하면 20.6% 증가입니다(e22).", T08_RECORD, None),
    ("T08", "e20의 22.0% 감소가 아니라, 같은 일수(9월 20일까지)로 비교하면 20.6% 증가입니다(e22).", T08_RECORD, None),
    ("T04", T04_HEAD + "e74 is exactly 4,000 characters and ends mid-word, so it was truncated at 4,000 characters; it "
     "is not intact.", None, None),
    ("T04", "진행 메모 3의 할 일은 '모바일 세션 — 같은 기간끼리 다시 비교한다'입니다.\ne74의 글이 끝까지 온전히 남아 있는지 "
     "확인했습니다: 정확히 4,000자에서 단어 중간에 끊겨 잘린 것으로 보입니다.", None, None),
    ("T11", "The failed run 20260916-214200-03-sample-zscores left data/derived/sample_scores.parquet, "
     "data/derived/timepoint_profile.parquet and the charts z-distribution and pressure-band.\n"
     "sample_scores.parquet: both runs wrote identical bytes; the last writer is 20260916-214600-03-sample-zscores.",
     None, None),
    ("T11", "실패한 run 20260916-214200-03-sample-zscores는 timepoint_profile.parquet와 sample_scores.parquet, 차트 "
     "z-distribution·pressure-band를 남겼다.\n\n20260916-214600-03-sample-zscores가 두 파일을 다시 썼고,\n"
     "지금 sample_scores.parquet는 그 run의 출력이다.", None, None),
]


@pytest.mark.parametrize("task, text, state, calls", RIGHT, ids=[f"{r[0]}-{i}" for i, r in enumerate(RIGHT)])
def test_a_right_answer_written_another_way_passes(task, text, state, calls):
    if state is None and task == "T07":
        state = recorded("전환율 11.8%")
    if state is None and task == "T08":
        state = T08_RECORD
    result = scored(task, text, state, calls)
    assert result["result"] == "success", (failed_checks(result), result["critical"])


@pytest.mark.parametrize("task", sorted(CONFIG.answers))
def test_each_correct_completion_passes_its_own_text_checks(task):
    """The answer's own text, scored without the project: every text check it can pass, it passes."""
    answer = CONFIG.answers[task]
    result = scored(task, answer["correct_completion"],
                    state=recorded("11.8% 20.6% 증가") if task in ("T07", "T08") else None,
                    calls=QUERIED)
    text_types = {"text_any", "text_all", "number", "percent", "number_near", "cooccur", "claim_verdicts",
                  "ids_all", "ids_any", "stated_as_current"}
    failing = [c["id"] for c in result["checks"] if c["type"] in text_types and not c["passed"]]
    assert failing == []


def test_numbers_keep_their_sign_and_other_notations():
    assert me.numbers_in("-20.6%, −3, +4, 2026-08-01") == [-20.6, -3.0, 4.0, 2026.0, 8.0, 1.0]
    assert me.numbers_in("5 838 and 5 838 and 5.838e3") == [5838.0, 5838.0, 5838.0]
    amounts = [v for v, *_ in me.ko_amounts("서울 3,258만 6,900원, 8월 4억 4,596만 9,600원, 3,258.69만원")]
    assert [round(v) for v in amounts] == [32586900, 445969600, 32586900]
    percent = {"id": "p", "kind": "fact", "type": "percent", "value": 0.117849, "abs_tol_pp": 0.1}
    assert me.run_check(percent, scored_evidence("전환율은 0.118"), {})["passed"]
    assert not me.run_check({**percent, "fractions": False}, scored_evidence("전환율은 0.118"), {})["passed"]
    assert not me.run_check({**percent, "value": 0.20588}, scored_evidence("-20.6%"), {})["passed"]


def scored_evidence(text: str):
    return me.Evidence(text=text, last_turn=text, calls=[], tools=me.GENERATIONS["analytics"]["tools"],
                       state=EMPTY, existing_ids=set())


def test_verdict_words_are_whole_longest_and_negation_aware():
    words = CONFIG.words
    assert [v for *_, v, _ in me.verdict_marks("e8: incorrect", words)] == ["false"]
    assert [v for *_, v, _ in me.verdict_marks("e8: not supported", words)] == ["false"]
    assert [v for *_, v, _ in me.verdict_marks("e8은 뒷받침되지 않는다", words)] == ["false"]
    assert [v for *_, v, _ in me.verdict_marks("e8은 거짓이 아니다", words)] == ["supported"]
    assert [v for *_, v, _ in me.verdict_marks("| e8 | 뒷받침 |", words)] == ["supported"]
    assert me.verdict_marks("이를 뒷받침하는 근거는 점수 표다", words) == []
    assert me.verdict_marks("e8이 뒷받침되는지 판정해 줘", words) == []
    detected, _ = me.claim_verdicts("e8: supported. e15: false, e8: false.", ["e8", "e15"], words)
    assert detected == {"e8": {"supported", "false"}, "e15": {"false"}}


def test_an_id_that_does_not_exist_and_the_free_analysis_task_need_review():
    result = scored("T06", "There are 5,838 sessions, confirmed by e999.", calls=QUERIED, existing={"e1"})
    assert result["result"] == "success" and result["fabricated_ids"] == ["e999"]
    assert "fabricated_ids" in result["needs_review"]
    result = scored("T12", "80 contexts, 15 timepoints; pressure rf_power flow temperature; same training data.")
    assert "always_review" in result["needs_review"] and result["always_review"]


@pytest.fixture(scope="module")
def q13(tmp_path_factory):
    root = tmp_path_factory.mktemp("q13")
    return me.ensure_fixture(CONFIG.fixtures["Q13"], 0, root) / "project"


def test_t12_reads_results_not_activity_counts(q13):
    checks = {c["id"]: c for c in CONFIG.answers["T12"]["checks"]}
    # Activity only: three ok runs with unrelated charts, an import and a note.
    counts_only = {"new_records": [{"id": "e1", "kind": "import", "text": "x"}, {"id": "e2", "kind": "note",
                                                                                 "text": "done"}],
                   "new_runs": [{"id": f"20261007-10000{i}-x", "status": "ok", "charts": ["unrelated"],
                                 "chart_files": [{"id": "unrelated", "files": {}, "data": None,
                                                  "data_columns": ["a"]}],
                                 "outputs": [], "script": "scripts/x.py"} for i in range(3)],
                   "context_changed": False}
    result = scored("T12", "80 contexts, 15 timepoints; pressure rf_power flow temperature; same training data.",
                    state=counts_only)
    assert result["result"] == "failure"
    assert {"all-sensor-figure", "timepoint-scores"} <= set(failed_checks(result))
    # The fixture's own session: its last ok run draws all four sensors, a run left per-timepoint scores.
    state = me.changes(q13, {"max_seq": 0, "runs": [], "context_sha256": None})
    ev = me.Evidence(text="", last_turn="", calls=[], tools={}, state=state, existing_ids=set(), project=q13)
    figure = me.run_check(checks["all-sensor-figure"], ev, CONFIG.words)
    assert figure["passed"] and figure["detail"]["covering"] == ["20260916-220200-04-all-sensor-bands::all-sensor-bands"]
    table = me.run_check(checks["timepoint-scores"], ev, CONFIG.words)
    assert table["passed"]
    assert {t["path"] for t in table["detail"]["tables"]} == {"data/derived/sample_scores.parquet"}


def test_tokens_a_request_did_not_report_stay_not_measured():
    tokens = me.token_totals([{"turn": 1, "usage": {"input": 123}}], "openai-responses")
    assert tokens["input_uncached"] == {"status": "measured", "value": 123}
    assert {tokens[k]["status"] for k in ("input_cached", "output", "reasoning")} == {"not_measured"}
    failed = [{"turn": 1, "stop_reason": "error",
               "usage": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "reasoning": 0}}]
    tokens = me.token_totals(failed, "openai-responses")
    assert tokens["requests_with_usage"] == 0 and tokens["output"]["status"] == "not_measured"
    assert tokens["full_input"]["status"] == "not_measured"
    two_turns = [{"turn": 1, "usage": {"input": 1000, "output": 10, "cache_read": 0}},
                 {"turn": 2, "usage": {"input": 1500, "output": 30, "cache_read": 1200}}]
    tokens = me.token_totals(two_turns, "openai-completions", proposal_turn=2)
    assert [t["output"]["value"] for t in tokens["per_turn"]] == [10, 30]
    assert tokens["proposal_request"]["input_uncached"] == {"status": "measured", "value": 300}
    assert [r["turn"] for r in tokens["per_request"]] == [1, 2]


def write_record(out: Path, name: str, **fields) -> None:
    record = {"run": name, "task": "T06", "model": {"config": "qwen3.8-27b-local"},
              "conditions": {"surface": "default", "proposal": "unrequested"}, "repeat": 1,
              "answer": {"status": "draft"}, **fields}
    me.write_json(out / "runs" / name / "run.json", record)


def test_the_summary_keeps_partial_usage_out_of_the_medians(tmp_path):
    def tokens(complete, output, partial=False):
        value = {"status": "measured", "value": output, **({"partial": True} if partial else {})}
        return {"complete": complete, "input_uncached": {"status": "measured", "value": 1000},
                "input_cached": {"status": "measured", "value": 0}, "output": value}

    write_record(tmp_path, "T06-a-r1", outcome="success", tokens=tokens(True, 40),
                 score={"critical": [], "needs_review": [], "fabricated_ids": []})
    write_record(tmp_path, "T06-a-r2", outcome="success", tokens=tokens(False, 10, partial=True),
                 score={"critical": [], "needs_review": ["fabricated_ids"], "fabricated_ids": ["e999"]})
    write_record(tmp_path, "T06-a-r3", outcome="not_run", reason="api_key_missing: X is not set")
    summary = me.summarize(tmp_path)
    [group] = summary["aggregate"]
    assert group["median_total_tokens"] == 1040 and group["token_runs"] == 1 and group["partial_usage_runs"] == 1
    assert group["needs_review"] == 1 and group["fabricated_ids"] == 1 and group["success_without_review"] == 1
    md = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "10 (partial)" in md and "| e999 |" in md
    not_run = next(line for line in md.splitlines() if line.startswith("| T06-a-r3"))
    assert not_run.count("—") == 11 and "| 0 |" not in not_run


def test_paths_that_leave_the_work_root_are_listed(tmp_path):
    root = str(tmp_path / "work")
    cwd = str(tmp_path / "work" / "project")
    assert me.outside_paths({"command": "cat ../input/traces.csv && ls ../../.. && cat ~/.ssh/x"}, [root], cwd) == [
        "../../..", "~/.ssh/x"]
    assert me.outside_paths({"path": str(tmp_path / "out" / "fixtures" / "expected.json")}, [root], cwd) == [
        str(tmp_path / "out" / "fixtures" / "expected.json")]
    assert me.outside_paths({"command": "/usr/bin/env python3 x.py > /dev/null", "sql": "SELECT 1 / 2",
                             "text": "see /etc/hosts"}, [root], cwd) == []
    assert me.outside_paths({"path": f"{root}/project/data/raw/x.parquet"}, [root], cwd) == []


def test_a_checkout_inside_another_repository_has_no_commit(tmp_path):
    repo = tmp_path / "repo"
    (repo / "snapshot" / "extension").mkdir(parents=True)
    (repo / "snapshot" / "extension" / "aelix_lab.py").write_text("# old\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    info = me.checkout_info(repo / "snapshot")
    assert info["commit"] is None and info["dirty"] is None
    assert "not the top level" in info["commit_reason"]


def test_another_seed_and_existing_runs_are_refused_before_anything_runs(tmp_path, capsys):
    out = tmp_path / "seed"
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--seed", "1", "--out", str(out)]) == 2
    assert not out.exists()
    assert "seed 0" in capsys.readouterr().err
    out = tmp_path / "used"
    me.write_json(out / "summary.json", {})
    (out / "runs" / "T07-dry-run-default-unrequested-r2").mkdir(parents=True)
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--task", "T07", "--repeats", "2",
                    "--out", str(out)]) == 2
    assert sorted(p.name for p in (out / "runs").iterdir()) == ["T07-dry-run-default-unrequested-r2"]
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--out", str(tmp_path / "w"),
                    "--work-root", str(tmp_path / "w" / "roots")]) == 2


def test_a_secret_is_overwritten_in_files_and_records(tmp_path):
    secret = "sk-test-" + "redactionunit0123456789"
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.bin").write_bytes(b"\x00\x01" + secret.encode() + b"\x02")
    (tmp_path / "b.txt").write_text(f"key={secret}\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("nothing\n", encoding="utf-8")
    assert me.redact_tree(tmp_path, [secret]) == ["a/x.bin", "b.txt"]
    data = (tmp_path / "a" / "x.bin").read_bytes()
    assert secret.encode() not in data and len(data) == len(secret) + 3
    assert me.redact_value({"k": [f"x {secret}"], secret: 1}, [secret]) == {"k": ["x <redacted>"], "<redacted>": 1}


def test_the_answers_name_their_seed_and_t12_is_always_reviewed():
    assert {a["fixture"]["seed"] for a in CONFIG.answers.values()} == {0}
    assert CONFIG.answers["T12"]["always_review"]
    assert json.loads((ROOT / "tools" / "model_eval_data" / "answers" / "T05.json").read_text(encoding="utf-8"))[
        "evidence_ids"][-1] == "runs/20260803-191107-case-overwritten/charts/overwritten.data.parquet"
