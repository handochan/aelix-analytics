"""The real-model evaluation harness of tools/model_eval.py and its data (Issue #40).

Without a model: the configuration (12 tasks, their draft answers, the pinned model configurations and
the conditions) is valid, and an invalid one is refused; every id an answer cites is in the seed-0 fixture
it is written for, and the answers' numbers and verdicts are those tools/eval_fixtures.py's expected
judgments and manifest hold; the scoring passes a right answer and fails a wrong one, a stale value stated
as current and a missing citation; aelix's event stream is read into turns, tool calls (with the class of
each failure) and tokens by each adapter's usage convention; the tool surfaces and the two generations of
names give the right command line; a model whose key is missing and the always_on condition are not run,
with the reason; no file of the repository holds a key.

With uv and aelix 0.1.0b2 (resolved by uv, as the CI's aelix tests are): the dry run end to end — the
harness's scripted fake model, the real aelix process, this checkout's extension and kit, the event
stream, the project's changes, the score — and no secret of the environment in anything it writes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "model_eval.py"
DATA = ROOT / "tools" / "model_eval_data"


def load_tool():
    spec = importlib.util.spec_from_file_location("model_eval_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


me = load_tool()


@pytest.fixture(scope="module")
def config():
    return me.load_config()


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory, config):
    """S, Q10 and Q13 with seed 0, made by tools/eval_fixtures.py through the harness."""
    root = tmp_path_factory.mktemp("model-eval-fixtures")
    made = {}
    for name, spec in config.fixtures.items():
        made[name] = me.ensure_fixture(spec, 0, root)
    return root, made


# --- configuration --------------------------------------------------------------------------------

def test_the_configuration_has_twelve_tasks_with_draft_answers_and_both_models(config):
    assert sorted(config.tasks) == [f"T{i:02d}" for i in range(1, 13)]
    assert sorted(config.answers) == sorted(config.tasks)
    # Agent-written answers stay drafts until the owner confirms them (Issue #40, 2026-10-07).
    assert {a["status"] for a in config.answers.values()} == {"draft"}
    assert all(a["confirmed_by"] is None for a in config.answers.values())
    gates = {g for t in config.tasks.values() for g in t["gates"]}
    assert gates == set(me.GATES)
    assert {t["q11_request"] for t in config.tasks.values()} >= {"single", "combination", "ambiguous"}
    assert {t for t, task in config.tasks.items() if task.get("proposal_conditions")} == {"T07", "T08", "T12"}
    # Requests name no tool: the same text serves every surface and both generations of names.
    for task in config.tasks.values():
        for request in task["requests"]:
            assert not re.search(r"\b(analytics|lab)_[a-z]+", request), task["id"]

    qwen = config.models["qwen3.8-27b-local"]
    serving = qwen["serving"]
    assert (qwen["provider"], qwen["model"], qwen["api"]) == (
        "aelix-eval-ollama", "qwen3.8:27b-mtp-q4_K_M", "openai-completions")
    assert serving["backend"] == "ollama" and serving["backend_version"] == "0.35.1"
    assert serving["quantization"] == "Q4_K_M" and len(serving["manifest_digest"]) == 64
    assert serving["context_window"] == 131072 == int(serving["server_env"]["OLLAMA_CONTEXT_LENGTH"])
    entry = qwen["aelix_provider"]["models"][0]
    assert (entry["contextWindow"], entry["maxTokens"]) == (131072, serving["output_reservation"])
    assert qwen["api_key_env"] is None

    frontier = config.models["gpt-6-astra"]
    assert (frontier["provider"], frontier["model"], frontier["api"]) == ("openai", "gpt-6-astra",
                                                                          "openai-responses")
    assert frontier["api_key_env"] == "OPENAI_API_KEY" and frontier["aelix_provider"] is None


def copy_data(tmp_path: Path) -> Path:
    target = tmp_path / "data"
    shutil.copytree(DATA, target)
    return target


def edit_json(path: Path, change) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_an_invalid_configuration_is_refused_with_every_problem(tmp_path):
    data = copy_data(tmp_path)
    edit_json(data / "answers" / "T06.json", lambda a: a["checks"].append(
        {"id": "x", "kind": "fact", "type": "guess", "values": []}))
    edit_json(data / "answers" / "T07.json", lambda a: a.update(status="final"))
    edit_json(data / "answers" / "T08.json", lambda a: a["checks"].append(
        {"id": "y", "kind": "critical", "type": "text_any", "values": ["22%"]}))
    edit_json(data / "answers" / "T09.json", lambda a: a["checks"].append(
        {"id": "z", "kind": "critical", "type": "stated_as_current", "patterns": ["(unclosed"],
         "unless": "@superseded"}))
    edit_json(data / "tasks.json", lambda t: t["tasks"][0]["requests"].append("--help"))
    edit_json(data / "models" / "dry-run.json",
              lambda m: m["aelix_provider"].update(apiKey="sk-" + "a" * 30))
    (data / "answers" / "T12.json").unlink()
    with pytest.raises(me.UsageError) as caught:
        me.load_config(data)
    message = str(caught.value)
    for expected in ("type 'guess' is unknown", "status must be one of", "a critical check is a "
                     "stated_as_current check", "does not compile", "may not start with - or @",
                     "looks like a real key", "answers/T12.json is missing"):
        assert expected in message


def test_every_cited_id_is_in_its_fixture(config, fixtures):
    root, _ = fixtures
    assert me.validate_against_fixtures(config, root) == []


def numbers_of(config, task: str) -> dict:
    return {c["id"]: c for c in config.answers[task]["checks"]}


def test_the_answers_agree_with_the_fixtures_own_judgments(config, fixtures):
    _, made = fixtures
    q10 = json.loads((made["Q10"] / "expected.json").read_text(encoding="utf-8"))
    examples = {e["id"]: e for e in q10["examples"]}
    assert numbers_of(config, "T06")["count"]["value"] == examples["denominator.sound"]["evidence"]["sessions"]
    assert numbers_of(config, "T07")["rate"]["value"] == examples["denominator.sound"]["evidence"]["sound_value"]
    assert numbers_of(config, "T08")["growth"]["value"] == examples["period.sound"]["evidence"]["sound_value"]
    assert numbers_of(config, "T09")["seoul"]["value"] == examples["aggregation_key.sound"]["evidence"]["sound_value"]
    assert numbers_of(config, "T09")["august"]["value"] == examples["unit.sound"]["evidence"]["sound_value"]
    assert set(numbers_of(config, "T07")["cites"]["values"]) == {
        examples["denominator.flawed"]["claim_event"], examples["denominator.sound"]["claim_event"]}
    assert numbers_of(config, "T08")["cites"]["values"] == [examples["period.flawed"]["claim_event"]]

    q13 = json.loads((made["Q13"] / "expected.json").read_text(encoding="utf-8"))
    assert numbers_of(config, "T10")["verdicts"]["claims"] == {c["event"]: c["verdict"] for c in q13["claims"]}
    assert numbers_of(config, "T10")["max-z"]["value"] == q13["facts"]["max_abs_z"]
    partial = q13["records"]["failed_run_partial_outputs"]
    assert numbers_of(config, "T11")["cites"]["values"] == [partial["run"], partial["rewritten_by"]]
    assert numbers_of(config, "T11")["partial-charts"]["values"] == partial["charts"]
    assert config.tasks["T12"]["requests"] == [
        s["request"].replace("(input/traces.csv)", "({input}/traces.csv)") for s in q13["session"]]

    cases = {c["id"]: c for c in json.loads((made["S"] / "manifest.json").read_text(encoding="utf-8"))["cases"]}
    t02 = numbers_of(config, "T02")
    assert t02["cites"]["values"] == [cases["correction"]["records"]["correction"],
                                      cases["withdrawal"]["records"]["withdrawal"]]
    assert t02["cites-originals"]["values"] == [cases["correction"]["records"]["original"],
                                                cases["withdrawal"]["records"]["decision"]]
    assert numbers_of(config, "T03")["cites"]["values"] == [cases["old_finding"]["records"]["finding"]]
    assert numbers_of(config, "T04")["cites"]["values"] == [cases["long_korean"]["records"]["note"]]
    unpreserved = cases["unpreserved_data"]["records"]
    assert numbers_of(config, "T05")["cites"]["values"] == [unpreserved["overwritten_run_1"],
                                                            unpreserved["overwritten_run_2"]]


def test_t01_s_open_and_closed_ids_are_what_the_kit_reads(config, fixtures):
    _, made = fixtures
    from aelix_analytics import core

    events = me.journal(made["S"] / "project")
    resolved = core.resolved_targets(events)
    open_kinds = {e["id"] for e in events if e["kind"] in ("question", "todo")}
    checks = numbers_of(config, "T01")
    assert set(checks["open-ids"]["values"]) == open_kinds - set(resolved)
    assert set(checks["closed-as-open"]["patterns"]) == open_kinds & set(resolved)


# --- scoring --------------------------------------------------------------------------------------

def evidence(text: str, *, state=None, calls=(), last_turn=None, existing=()) -> object:
    return me.Evidence(text=text, last_turn=text if last_turn is None else last_turn, calls=list(calls),
                       tools=me.GENERATIONS["analytics"]["tools"],
                       state=state or {"new_records": [], "new_runs": [], "context_changed": False},
                       existing_ids=set(existing))


def test_a_right_answer_succeeds_and_a_stale_value_stated_as_current_is_critical(config):
    answer = config.answers["T07"]
    record = {"new_records": [{"id": "e23", "kind": "finding", "text": "전환율 11.8%"}], "new_runs": [],
              "context_changed": False}
    right = ("맞는 값은 11.8%입니다(e18). e16의 46.8%는 분모를 고객 수로 나눈 오류라 틀렸습니다. 분모 정의의 정정입니다.")
    result = me.score(answer, evidence(right, state=record, existing={"e16", "e18", "e23"}), config.words)
    assert result["result"] == "success" and result["critical"] == []
    assert result["fact_recall"] == {"passed": 3, "total": 3}

    # The same value said to hold, on a line with nothing that marks it wrong or explains it.
    stale = "8월 세션당 전환율은 46.8%입니다(e16).\n참고로 e18에는 11.8%로도 적혀 있고 분모가 다릅니다."
    result = me.score(answer, evidence(stale, state=record), config.words)
    assert result["critical"] == ["wrong-rate"] and result["result"] == "failure"
    assert "wrong-rate" in result["needs_review"]

    # Nothing recorded: the state check fails the run.
    result = me.score(answer, evidence(right, existing={"e16", "e18"}), config.words)
    assert result["result"] == "failure"
    assert [c["id"] for c in result["checks"] if not c["passed"]] == ["recorded"]


def test_numbers_percentages_and_ids_are_read_with_their_boundaries(config):
    assert me.numbers_in("합계는 32,586,900원, 비율 0.118, e74와 C057, 2026-08-01, 4.36으로") == [
        32586900.0, 0.118, 2026.0, 8.0, 1.0, 4.36]
    check = {"id": "p", "kind": "fact", "type": "percent", "value": 0.117849, "abs_tol_pp": 0.1}
    assert me.run_check(check, evidence("전환율은 11.8 %"), {})["passed"]
    assert not me.run_check(check, evidence("전환율은 0.118"), {})["passed"]  # a ratio is not a percent
    assert not me.run_check(check, evidence("11.9%"), {})["passed"]
    ids = {"id": "i", "kind": "evidence", "type": "ids_all", "values": ["e41", "20260804-032539-case-overwritten"]}
    assert me.run_check(ids, evidence("e41의 값은 run 20260804-032539-case-overwritten가 바꿨다"), {})["passed"]
    assert not me.run_check(ids, evidence("e410과 20260804-032539-case-overwritten-2"), {})["passed"]
    assert me.fabricated_ids("e41과 e999, run 20260921-999999-made-up", {"e41"}) == [
        "20260921-999999-made-up", "e999"]


def test_claim_verdicts_are_read_per_claim(config):
    check = numbers_of(config, "T10")["verdicts"]
    right = ("- e8: 뒷받침된다. 3σ 밖은 pressure C057 하나.\n- e15: 틀렸다. 최대 |z|는 4.3612다.\n"
             "- e16: 판단할 수 없다. 기준을 같은 학습 데이터로 추정했다.\n- e20: 거짓이다. C017은 밴드 밖 샘플이 있다.")
    result = me.run_check(check, evidence(right), config.words)
    assert result["passed"] and not result.get("needs_review")
    wrong = right.replace("e16: 판단할 수 없다", "e16: 맞다")
    result = me.run_check(check, evidence(wrong), config.words)
    assert not result["passed"] and result["detail"]["claims"]["e16"]["detected"] == ["supported"]
    assert result["needs_review"]


def test_state_checks_read_new_runs_charts_and_records(config):
    checks = numbers_of(config, "T12")
    state = {"new_records": [{"id": "e2", "kind": "import", "text": "imported traces"},
                             {"id": "e9", "kind": "finding", "text": "x"}],
             "new_runs": [{"id": "20261007-100000-a", "status": "ok", "script": "scripts/01.py",
                           "charts": ["a", "b"]},
                          {"id": "20261007-100100-b", "status": "error", "script": "scripts/02.py",
                           "charts": []},
                          {"id": "20261007-100200-c", "status": "ok", "script": "scripts/03.py",
                           "charts": ["c"]},
                          {"id": "20261007-100300-d", "status": "ok", "script": "scripts/02.py",
                           "charts": ["all-sensor-bands"]}],
             "context_changed": False}
    ev = evidence("80개 컨텍스트, 스텝마다 15 시점. pressure rf_power flow temperature. 기준을 같은 학습 데이터로 추정했다.",
                  state=state)
    for name in ("contexts", "timepoints", "sensors", "same-data", "imported", "runs", "charts",
                 "last-figure", "context"):
        assert me.run_check(checks[name], ev, config.words)["passed"], name
    assert me.recovered(state) == {"failed_runs": 1, "fixed_by_a_later_run": 1}


# --- the event stream -----------------------------------------------------------------------------

def event_lines(api: str, usage_second: dict | None) -> list[str]:
    def assistant(content, stop, usage):
        return {"type": "message_end", "message": {"role": "assistant", "content": content,
                                                   "stop_reason": stop, "usage": usage, "api": api,
                                                   "model": "m-1", "provider": "p"}}
    events = [
        {"id": "session-1", "created_at": "2026-10-07T00:00:00Z"},
        {"type": "agent_start"},
        {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "q1"}]}},
        assistant([{"type": "thinking", "thinking": "…"}, {"type": "toolCall", "tool_call_id": "c1",
                                                            "tool_name": "analytics_query", "input": {}}],
                  "toolUse", {"input": 1000, "output": 10, "cache_read": 0, "reasoning": 4}),
        {"type": "tool_execution_start", "tool_call_id": "c1", "tool_name": "analytics_query", "args": {}},
        {"type": "tool_execution_end", "tool_call_id": "c1", "tool_name": "analytics_query", "is_error": True,
         "result": {"content": [{"type": "text", "text": 'Validation failed for tool "analytics_query":'}]}},
        {"type": "auto_retry_start", "attempt": 1},
        assistant([{"type": "text", "text": "answer one"}], "stop", usage_second),
        {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "q2"}]}},
        assistant([{"type": "text", "text": "answer two"}], "stop", {"input": 1500, "output": 30,
                                                                    "cache_read": 1200, "reasoning": 0}),
        {"type": "agent_end", "messages": []},
    ]
    return [json.dumps(e) for e in events] + ["not json"]


def test_the_event_stream_gives_turns_calls_and_tokens_by_the_adapters_convention():
    tr = me.parse_events(event_lines("openai-completions", {"input": 1200, "output": 20, "cache_read": 1000}))
    assert tr.session == "session-1" and tr.ended and tr.retries == 1 and tr.unparsed == 1
    assert [(t["request"], t["answer"], t["messages"]) for t in tr.turns] == [
        ("q1", "answer one", 2), ("q2", "answer two", 1)]
    summary = me.tool_summary(tr.calls, me.GENERATIONS["analytics"]["tools"])
    assert summary["errors"] == {"schema": 1} and summary["argument_errors"] == 1
    tokens = me.token_totals(tr.messages, "openai-completions")
    # Chat completions: input counts the cached prompt, cache_read is the cached part.
    assert tokens["input_uncached"] == {"status": "measured", "value": 3700 - 2200}
    assert tokens["input_cached"]["value"] == 2200 and tokens["output"]["value"] == 60
    assert tokens["reasoning"]["status"] == tokens["cache_write"]["status"] == "not_measured"
    assert tokens["complete"]

    tr = me.parse_events(event_lines("openai-responses", None))
    tokens = me.token_totals(tr.messages, "openai-responses")
    # Responses: aelix's input is already without the cached part; one request reported nothing.
    assert tokens["input_uncached"] == {"status": "measured", "value": 2500, "partial": True}
    assert tokens["reasoning"]["value"] == 4 and not tokens["complete"]
    assert tokens["requests"] == 3 and tokens["requests_with_usage"] == 2
    assert me.token_totals([], "openai-responses")["output"] == {
        "status": "not_measured", "reason": "no model request was made"}


@pytest.mark.parametrize("text, is_error, expected", [
    ("Unknown tool: analytics_qeury", True, "unknown_tool"),
    ('Validation failed for tool "analytics_run":\n  - $: \'title\' is a required property', True, "schema"),
    ("analytics_run: error [run_args]\nmessage: pass exactly one of script, code", True, "argument"),
    ("lab_query: error [query_parse]\nmessage: Parser Error", True, "argument"),
    ("analytics_query: error [query_failed]\nmessage: Binder Error", True, "query_error"),
    ("analytics_query: error [kit_failed]\nmessage: exited 1", True, "tool_error"),
    ("analytics_run: error — run 20261007-101010-x (1.2 s) — KeyError", True, "run_failed"),
    ("analytics_run: ok — run 20261007-101010-x (1.2 s)", False, None),
    ("bash: exit 1", True, "tool_error"),
])
def test_tool_failures_are_classified(text, is_error, expected):
    assert me.classify({"result": text, "is_error": is_error}) == expected


# --- conditions, generations, keys ----------------------------------------------------------------

def session_for(config, *, surface="default", proposal="unrequested", generation="analytics", model="dry-run"):
    return me.Session(config=config, model=config.models[model], out=Path("out"),
                      checkout={"path": str(ROOT), "generation": generation}, surface=surface,
                      proposal=proposal, seed=0, aelix_command=list(me.AELIX_COMMAND), aelix_version=None,
                      fixtures_root=Path("fixtures"), timeout=None, script=None, serving=None, uv=None,
                      secrets=[])


def test_surfaces_and_generations_give_the_command_line(config, tmp_path):
    argv, extension = me.surface_argv(session_for(config))
    assert (argv, extension) == ([], "extension/aelix_analytics.py")
    argv, _ = me.surface_argv(session_for(config, surface="analytics_read"))
    assert argv == ["--tools", "read,analytics_status,analytics_query,analytics_import,analytics_run,"
                               "analytics_record,analytics_init"]
    argv, extension = me.surface_argv(session_for(config, surface="coding", generation="lab"))
    assert argv[1].split(",")[:4] == ["read", "bash", "edit", "write"] and "lab_note" in argv[1]
    assert extension == "extension/aelix_lab.py"

    old = tmp_path / "old"
    (old / "extension").mkdir(parents=True)
    (old / "extension" / "aelix_lab.py").write_text("# before the rename\n", encoding="utf-8")
    assert me.checkout_info(old)["generation"] == "lab"
    with pytest.raises(me.UsageError):
        me.checkout_info(tmp_path / "nothing")
    session = session_for(config, generation="lab")
    env, set_vars = me.run_env(session, tmp_path / "run", tmp_path / "project")
    assert set(set_vars) >= {"AELIX_LAB_TRUST_FILE", "AELIX_LAB_PROJECT", "AELIX_CODING_AGENT_DIR"}
    assert not any(k.startswith("AELIX_ANALYTICS_") for k in env)


def test_a_missing_key_runs_nothing_and_says_how_to_run(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = tmp_path / "out"
    code = me.main(["run", "--model", "gpt-6-astra", "--task", "T06", "--out", str(out)])
    assert code == 3
    record = json.loads((out / "runs" / "T06-gpt-6-astra-default-unrequested-r1" / "run.json").read_text(
        encoding="utf-8"))
    assert record["outcome"] == "not_run" and record["reason"].startswith("api_key_missing: OPENAI_API_KEY")
    assert record["command_to_run"].startswith("OPENAI_API_KEY=<key> uv run --locked python tools/model_eval.py run")
    assert "--model gpt-6-astra --task T06" in record["command_to_run"]
    assert record["model"]["model"] == "gpt-6-astra"
    assert not (out / "fixtures").exists()  # nothing was made or started
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["aggregate"][0]["not_run"] == 1 and summary["aggregate"][0]["success"] == 0
    assert "to run: OPENAI_API_KEY=<key>" in capsys.readouterr().out


def test_always_on_is_not_run_until_the_kit_has_the_setting(tmp_path):
    out = tmp_path / "out"
    assert me.main(["run", "--model", "dry-run", "--task", "T07", "--proposal", "always_on",
                    "--out", str(out)]) == 3
    record = json.loads(next((out / "runs").glob("*/run.json")).read_text(encoding="utf-8"))
    assert record["outcome"] == "not_run" and "#17" in record["reason"]
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--proposal", "requested",
                    "--out", str(tmp_path / "out2")]) == 3  # T06 has no proposal conditions


def test_a_used_output_directory_is_refused(tmp_path):
    out = tmp_path / "used"
    out.mkdir()
    (out / "notes.txt").write_text("mine\n", encoding="utf-8")
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--out", str(out)]) == 2
    assert sorted(p.name for p in out.iterdir()) == ["notes.txt"]


KEY_LIKE = re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,}|OPENAI_API_KEY\s*[=:]\s*[\"']?sk-")


def test_no_file_of_the_repository_holds_a_key():
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others",
                             "--exclude-standard"], capture_output=True, check=True).stdout
    offenders = []
    for name in filter(None, listed.decode("utf-8").split("\0")):
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        if path == Path(__file__):
            text = text.replace(KEY_LIKE.pattern, "")
        if KEY_LIKE.search(text):
            offenders.append(name)
    assert offenders == []


# --- the dry run, end to end through aelix -------------------------------------------------------

@pytest.fixture(scope="module")
def aelix_command():
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH: the dry run starts aelix 0.1.0b2 through uv")
    try:
        version = me.aelix_version(list(me.AELIX_COMMAND))
    except me.HarnessError as exc:
        pytest.skip(f"aelix 0.1.0b2 could not be resolved by uv (offline?): {exc}")
    assert version == me.AELIX_VERSION
    return list(me.AELIX_COMMAND)


SECRET = "sk-test-" + "dryrunsecret0123456789abcdef"


def test_the_dry_run_goes_through_aelix_and_is_scored(aelix_command, fixtures, tmp_path, monkeypatch):
    root, _ = fixtures
    # A key in the environment must not reach anything the harness writes.
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    out = tmp_path / "dry"
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--task", "T07", "--out", str(out),
                    "--fixtures", str(root)]) == 0
    runs = out / "runs"
    t06 = json.loads((runs / "T06-dry-run-default-unrequested-r1" / "run.json").read_text(encoding="utf-8"))
    assert t06["outcome"] == "success" and t06["exit_code"] == 0 and not t06["timed_out"]
    assert t06["host"]["aelix"] == me.AELIX_VERSION and t06["kit"]["kit_version"] == "0.4.0"
    assert t06["answer"]["status"] == "draft" and t06["fixture"]["kind"] == "q10"
    assert [c["error_class"] for c in t06["tool_calls"]] == ["schema", None]
    assert t06["tool_summary"]["argument_errors"] == 1
    assert t06["tool_calls"][1]["result_head"].splitlines()[-1].strip() == "5838"
    tokens = t06["tokens"]
    assert tokens["source"].startswith("scripted fake server")
    assert (tokens["input_uncached"]["value"], tokens["input_cached"]["value"], tokens["output"]["value"]) == (
        2000, 2500, 120)
    offered = t06["offered_tools"]["tools"]
    assert set(me.GENERATIONS["analytics"]["tools"].values()) <= set(offered)

    t07 = json.loads((runs / "T07-dry-run-default-unrequested-r1" / "run.json").read_text(encoding="utf-8"))
    assert t07["outcome"] == "success"
    [record] = t07["changes"]["new_records"]
    assert record["kind"] == "finding" and "11.8%" in record["text"] and record["refs"]["events"] == ["e16", "e18"]

    # Isolation: the trust list, uv, aelix's agent directory and sessions are under OUT.
    command = json.loads((runs / "T06-dry-run-default-unrequested-r1" / "command.json").read_text(encoding="utf-8"))
    for name in ("AELIX_ANALYTICS_TRUST_FILE", "AELIX_ANALYTICS_PROJECT", "AELIX_CODING_AGENT_DIR",
                 "AELIX_CODING_AGENT_SESSION_DIR"):
        assert Path(command["set_env"][name]).is_relative_to(out)
    assert "--no-context-files" in command["argv"] and "--no-session" in command["argv"]
    # Only the work copy: the fixture's answers never reach the model's directory.
    work = runs / "T06-dry-run-default-unrequested-r1" / "work"
    assert sorted(p.name for p in work.iterdir()) == ["input", "project"]

    leaked = [p for p in out.rglob("*") if p.is_file() and SECRET.encode() in p.read_bytes()]
    assert leaked == []
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert [a["success"] for a in summary["aggregate"]] == [1, 1]

    # A wrong answer: the stale rate as current, no record — the same pipeline fails it.
    script = tmp_path / "wrong.json"
    script.write_text(json.dumps({"schema": me.SCRIPT_SCHEMA, "task": "T07", "steps": [
        {"text": "8월 세션당 전환율은 46.8%입니다(e16)."}]}, ensure_ascii=False), encoding="utf-8")
    assert me.main(["run", "--model", "dry-run", "--task", "T07", "--first-repeat", "2", "--out", str(out),
                    "--fixtures", str(root), "--script", str(script)]) == 0
    wrong = json.loads((runs / "T07-dry-run-default-unrequested-r2" / "run.json").read_text(encoding="utf-8"))
    assert wrong["outcome"] == "failure" and wrong["score"]["critical"] == ["wrong-rate"]
    assert wrong["model_requests"] == 1 and wrong["tool_calls"] == []

    # Scoring again from what the runs left gives the same outcomes.
    assert me.main(["score", str(out)]) == 0
    again = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert {r["run"]: r["outcome"] for r in again["runs"]} == {
        "T06-dry-run-default-unrequested-r1": "success", "T07-dry-run-default-unrequested-r1": "success",
        "T07-dry-run-default-unrequested-r2": "failure"}
    assert os.environ["OPENAI_API_KEY"] == SECRET
