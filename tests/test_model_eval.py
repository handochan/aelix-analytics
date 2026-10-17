"""The real-model evaluation harness of tools/model_eval.py and its data (Issue #40).

Without a model: the configuration (12 tasks, their confirmed answers, the pinned model configurations and
the conditions) is valid, and an invalid one is refused; every id an answer cites is in the seed-0 fixture
it is written for, and the answers' numbers and verdicts are those tools/eval_fixtures.py's expected
judgments and manifest hold; the scoring passes a right answer and fails a wrong one, a stale value stated
as current and a missing citation; aelix's event stream is read into turns, tool calls (with the class of
each failure) and tokens by each adapter's usage convention; the tool surfaces and the two generations of
names give the right command line; a model whose login or key is missing and the always_on condition are
not run, with the reason; the secret scan overwrites credential-shaped text (a JWT, a bearer token, a
refresh/access field, an sk- key, a random long token, a name with words before the keyword) without
knowing it and removes a link that leaves the run (its target recorded as it was made, without Windows'
extended-length prefix), and passes what runs hold that is not a credential; a key variable's value is
masked in each of the launcher's forms (UTF-16LE after a byte-order mark and hex among them); it
never writes through a hard link (a name of a file that has another outside the run, or of the login, is
removed without being read; two names inside the run are each replaced), fails when a directory cannot be
read, and leaves no credential shape in a file name, a link's target or the record; the harness reads no
project file that links outside; what still works in the work root after aelix exited (named on its command
line, working there, or started by aelix) is waited for, then stopped; no file of the repository holds a
key or a credential shape.

With uv and aelix 0.1.0b2 (resolved by uv, as the CI's aelix tests are): the dry run end to end — the
harness's scripted fake model, the real aelix process, this checkout's extension and kit in a work root
outside the output, the event stream, the project's changes, the score — and no secret of the
environment in anything it writes; a model configuration that names a key variable, with a script
that puts the key into a bash call (in the shell's syntax: PowerShell on Windows), project files (as the
shell writes it, as UTF-16LE with a byte-order mark and as hex), a record and the answer, leaves no form
of it under the output, also when the harness fails after aelix exited. A model that uses an aelix login (as the frontier
gpt-5.6-sol does through aelix's openai-codex login): with a fake login file holding dummy tokens,
aelix (through tools/model_eval_aelix.py) sends the login's key read in place, the run's directory
gets no store, no link and none of the dummy values even when the model prints the login, the secret
scan fails and says where; a login without the model's provider, or no login file, is not run, nor is
one the API refuses. The child gets no credential variable and the run's own HOME; a hard link the model
makes to the login is removed and the login never written; a job the model leaves in the background is
waited for before the scan; a timeout stops aelix and what the model left running; a work root that cannot
be moved is searched where it is; a harness stopped by SIGTERM stops aelix and still searches the run, and
one killed with SIGKILL leaves nothing running (the launcher's lifeline) and `scan` secures the run; a
login that is a link, has two names or is not an agent directory's auth.json is refused; the launcher
refuses a missing login and defers a stop until a refreshed login is written. The launcher's masking by
the login's exact values (POSIX): it compares the credential strings of the entries only (not a field
that is not a credential, a value under 16 characters or a key aelix resolves), finds each form of a
value (raw, JSON-escaped once and twice, URL-encoded, base64 at each alignment, UTF-16) and keeps a
file's layout, never follows or writes through a link (the login's own name and a file with a name
outside are not read), renames a name and rewrites a link target that holds a value, and reports counts
and paths only; end to end, a model that echoes the fake login's values in every form into its answer,
a bash call's output, project files, a nested file, a name and a background job's command line leaves
none of them in any file, name or record of the run, nor on the console, and the record says so.
tests/test_model_eval_scoring.py holds the reviewers' adversarial answers.
"""

from __future__ import annotations

import base64
import codecs
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

from aelix_analytics import core

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

def test_the_configuration_has_twelve_tasks_with_confirmed_answers_and_both_models(config):
    assert sorted(config.tasks) == [f"T{i:02d}" for i in range(1, 13)]
    assert sorted(config.answers) == sorted(config.tasks)
    # The agent wrote the answers as drafts and the owner confirmed all twelve (Issue #40, 2026-10-17).
    assert {a["status"] for a in config.answers.values()} == {"confirmed"}
    assert all(a["confirmed_by"] == "handochan (owner), 2026-10-17, Issue #40" for a in config.answers.values())
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

    # The frontier model through aelix's own openai-codex login (owner decision 2026-10-07, updated).
    frontier = config.models["gpt-5.6-sol"]
    assert (frontier["provider"], frontier["model"], frontier["api"], frontier["thinking"]) == (
        "openai-codex", "gpt-5.6-sol", "openai-codex-responses", "medium")
    assert frontier["auth"]["source"] == "aelix_login" and frontier["auth"]["provider"] == "openai-codex"
    assert frontier["api_key_env"] is None and frontier["aelix_provider"] is None
    assert (frontier["serving"]["context_window"], frontier["serving"]["output_reservation"]) == (1050000, 128000)
    assert "1.1M" in frontier["serving"]["listed"] and "128K" in frontier["serving"]["listed"]
    assert sorted(config.models) == ["dry-run", "gpt-5.6-sol", "qwen3.8-27b-local"]


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
    edit_json(data / "models" / "gpt-5.6-sol.json", lambda m: m.update(
        auth={"source": "copied_file", "provider": "openai-codex"}, api_key_env="OPENAI_API_KEY",
        api="openai-chat"))
    edit_json(data / "models" / "qwen3.8-27b-local.json", lambda m: m["serving"].update(
        note="refresh: " + "\"" + "Ab1" * 8 + "\""))
    (data / "answers" / "T12.json").unlink()
    with pytest.raises(me.UsageError) as caught:
        me.load_config(data)
    message = str(caught.value)
    for expected in ("type 'guess' is unknown", "status must be one of", "a critical check is a "
                     "stated_as_current check", "does not compile", "may not start with - or @",
                     "looks like a real key", "answers/T12.json is missing", "auth must say its source",
                     "an aelix login or a key variable, not both", "api must be one of",
                     "qwen3.8-27b-local.json: the configuration holds credential-shaped text (credential_field)"):
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
    # The record that closed each one: naming it next to the closed id says it is closed.
    assert checks["closed-as-open"]["resolved_by"] == {
        target: resolved[target]["id"] for target in checks["closed-as-open"]["patterns"]}


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
    assert me.run_check(check, evidence("전환율은 0.118"), {})["passed"]  # a written fraction counts
    assert not me.run_check({**check, "fractions": False}, evidence("전환율은 0.118"), {})["passed"]
    assert not me.run_check(check, evidence("11.9%"), {})["passed"]
    assert not me.run_check(check, evidence("-11.8%"), {})["passed"]  # the sign is kept
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
    # Per request (with its turn) and per turn: the second request of turn 1 and turn 2 apart.
    assert [r["turn"] for r in tokens["per_request"]] == [1, 1, 2]
    assert [(t["turn"], t["requests"], t["output"]["value"]) for t in tokens["per_turn"]] == [
        (1, 2, 30), (2, 1, 30)]
    assert tokens["full_input"]["status"] == "not_measured"

    tr = me.parse_events(event_lines("openai-responses", None))
    tokens = me.token_totals(tr.messages, "openai-responses")
    # Responses: aelix's input is already without the cached part; one request reported nothing.
    assert tokens["input_uncached"] == {"status": "measured", "value": 2500, "partial": True}
    assert tokens["reasoning"]["value"] == 4 and not tokens["complete"]
    assert tokens["requests"] == 3 and tokens["requests_with_usage"] == 2
    assert me.token_totals([], "openai-responses")["output"] == {
        "status": "not_measured", "reason": "no model request was made"}

    # The codex backend (the frontier model through aelix's openai-codex login): the same Responses
    # engine, so the same convention; a reasoning 0 may be aelix's default, which the note says.
    tr = me.parse_events(event_lines("openai-codex-responses", {"input": 300, "output": 20, "cache_read": 900,
                                                                "reasoning": 7}))
    tokens = me.token_totals(tr.messages, "openai-codex-responses")
    assert (tokens["input_uncached"]["value"], tokens["input_cached"]["value"], tokens["output"]["value"]) == (
        2800, 2100, 60)
    assert tokens["reasoning"]["value"] == 11 and "not told apart" in tokens["reasoning"]["note"]
    assert tokens["cache_write"]["status"] == "not_measured" and tokens["complete"]


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


def test_a_missing_login_runs_nothing_and_says_how_to_run(tmp_path, capsys):
    out = tmp_path / "out"
    login = tmp_path / "home" / ".aelix" / "agent" / "auth.json"
    code = me.main(["run", "--model", "gpt-5.6-sol", "--task", "T06", "--out", str(out), "--aelix-auth", str(login)])
    assert code == 3
    record = json.loads((out / "runs" / "T06-gpt-5.6-sol-default-unrequested-r1" / "run.json").read_text(
        encoding="utf-8"))
    assert record["outcome"] == "not_run" and record["reason"].startswith("aelix_login_missing: ")
    assert "does not exist" in record["reason"] and "/login" in record["login_to_run"]
    assert record["command_to_run"].startswith(
        "uv run --locked python tools/model_eval.py run --model gpt-5.6-sol --task T06 --surface default")
    assert shlex.join(["--aelix-auth", str(login)]) in record["command_to_run"]
    assert record["model"]["auth"]["file"] == str(login)
    assert record["model"]["auth"]["login_check"]["ok"] is False
    assert record["model"]["configuration"]["provider"] == "openai-codex"
    assert record["secret_scan"]["status"] == "passed"  # a run not run is searched too
    assert not login.parent.exists()  # nothing was made where the login would be
    assert not (out / "fixtures").exists()  # nothing was made or started
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["aggregate"][0]["not_run"] == 1 and summary["aggregate"][0]["success"] == 0
    printed = capsys.readouterr().out
    assert "login: log in with aelix" in printed and "to run: uv run --locked" in printed


def test_a_missing_key_runs_nothing(tmp_path, monkeypatch):
    original = me.load_config

    def with_key(*args, **kwargs):
        config = original(*args, **kwargs)
        config.models["dry-run"] = {**config.models["dry-run"], "api_key_env": "EVAL_PROBE_KEY"}
        return config

    monkeypatch.setattr(me, "load_config", with_key)
    monkeypatch.delenv("EVAL_PROBE_KEY", raising=False)
    out = tmp_path / "out"
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--out", str(out)]) == 3
    record = json.loads(next((out / "runs").glob("*/run.json")).read_text(encoding="utf-8"))
    assert record["reason"] == "api_key_missing: EVAL_PROBE_KEY is not set"
    assert record["command_to_run"].startswith("EVAL_PROBE_KEY=<key> uv run --locked python tools/model_eval.py")
    assert not (out / "fixtures").exists()


def test_a_login_model_refuses_another_command_and_shared_directories(tmp_path):
    login = tmp_path / "agent" / "auth.json"
    base = ["run", "--model", "gpt-5.6-sol", "--task", "T06", "--aelix-auth", str(login)]
    assert me.main([*base, "--out", str(tmp_path / "a"), "--aelix-command", "aelix"]) == 2
    assert me.main([*base, "--out", str(tmp_path / "agent" / "out")]) == 2
    assert me.main([*base, "--out", str(tmp_path / "b"), "--work-root", str(tmp_path / "agent" / "w")]) == 2
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--out", str(tmp_path / "c"),
                    "--aelix-auth", str(login)]) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == []  # refused before anything was made


def test_the_login_is_where_the_callers_aelix_keeps_it_and_aelix_starts_through_the_launcher(
        config, tmp_path, monkeypatch):
    monkeypatch.delenv("AELIX_CODING_AGENT_DIR", raising=False)
    assert me.default_aelix_auth() == Path(os.path.abspath(Path.home() / ".aelix" / "agent" / "auth.json"))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    assert me.default_aelix_auth() == Path(os.path.abspath(tmp_path / "agent" / "auth.json"))
    login = tmp_path / "login.json"
    assert me.launch_command(config.models["gpt-5.6-sol"], login, ["aelix"]) == [
        *me.AELIX_COMMAND[:-1], "python", str(me.LAUNCHER), str(login)]
    assert me.launch_command(config.models["qwen3.8-27b-local"], None, ["aelix"]) == ["aelix"]
    # The launcher changes the credential store's path only, and only with an isolated agent directory.
    source = me.LAUNCHER.read_text(encoding="utf-8")
    assert "AuthStorage.__init__" in source and "entry.main_sync()" in source
    assert "AELIX_CODING_AGENT_DIR is not set" in source


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
        data = path.read_bytes()
        text = data.decode("utf-8", errors="ignore")
        if path == Path(__file__):
            text = text.replace(KEY_LIKE.pattern, "")
        if KEY_LIKE.search(text):
            offenders.append(name)
        # The harness's credential shapes too (the dummies of these tests are built at run time).
        offenders += [f"{name}:{f['line']} {f['kind']}" for f in me.credential_findings(data)]
    assert offenders == []


def sleeper(**kwargs) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", *kwargs.pop("args", [])],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            **me.processes().spawn_kwargs(), **kwargs)


def test_what_still_works_in_the_work_root_is_waited_for_then_stopped(tmp_path):
    work = tmp_path / "aelix-eval-T00-settle"
    work.mkdir()
    named = sleeper(args=[str(work)])  # names the work root (the kit's report build)
    inside = sleeper(cwd=work)  # works there without naming it (a job the model left behind)
    started = sleeper(cwd=tmp_path)  # elsewhere, but started by aelix
    procs = (named, inside, started)
    try:
        table = me.process_table()
        if table is None:
            pytest.skip("this machine gives no process list (ps or PowerShell)")
        assert named.pid in table and started.pid in table
        result = me.settle(work, timeout=1, tracked={started.pid: table[started.pid][1][:300]})
        assert result["checked"] and result["left"] == []
        why = {p["pid"]: p["why"] for p in result["processes"]}
        assert "command line" in why[named.pid] and "started by aelix" in why[started.pid]
        assert {named.pid, started.pid} <= set(result["stopped"])
        if result["cwd_checked"]:  # /proc or lsof: not on Windows, whose process list has no directory
            assert "working directory" in why[inside.pid] and inside.pid in result["stopped"]
        for proc in (named, started) + ((inside,) if result["cwd_checked"] else ()):
            assert proc.wait(timeout=60) is not None
    finally:
        for proc in procs:
            with contextlib.suppress(OSError):
                proc.kill()
    assert me.settle(work, timeout=1)["processes"] == []


@pytest.fixture
def settle_clock(monkeypatch):
    """Advance time instead of waiting minutes for a stuck or continually replaced writer."""
    clock = [0.0]
    monkeypatch.setattr(me.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(me.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setitem(me.STOP, "signals", 0)
    return clock


def test_settle_bounds_a_stream_of_new_writers_and_reports_the_last_one(tmp_path, monkeypatch,
                                                                    settle_clock):
    calls = [0]
    stopped = []

    def table(**kwargs):
        calls[0] += 1
        settle_clock[0] += min(1, kwargs.get("timeout", 1))
        return {10_000 + calls[0]: (0, f"late-builder {tmp_path}")}

    monkeypatch.setattr(me, "process_table", table)
    monkeypatch.setattr(me, "holders", lambda *args, **kwargs: None)
    monkeypatch.setattr(me, "stop_processes", lambda pids, **kwargs: stopped.extend(pids))
    result = me.settle(tmp_path, timeout=2)
    assert result["waited_s"] <= 32
    assert result["max_wait_s"] == 32 and result["deadline_reached"]
    assert result["left"] and stopped
    last = next(p for p in result["processes"] if p["pid"] == result["left"][0])
    assert last["args"].startswith("late-builder ") and last["why"] == ["command line"]


def test_settle_bounds_probes_and_preserves_names_if_the_next_probe_times_out(tmp_path, monkeypatch,
                                                                            settle_clock):
    budgets = []

    def table(*, timeout):
        budgets.append(timeout)
        if len(budgets) == 1:
            return {10_001: (0, f"stuck-builder {tmp_path}")}
        settle_clock[0] += timeout
        return None

    monkeypatch.setattr(me, "process_table", table)
    monkeypatch.setattr(me, "holders", lambda *args, **kwargs: None)
    result = me.settle(tmp_path, timeout=2)
    assert not result["checked"] and result["deadline_reached"]
    assert result["waited_s"] == 32 and budgets[1] < budgets[0] == 32
    assert result["left"] == [10_001]
    assert result["processes"][0]["args"].startswith("stuck-builder ")


def test_settle_reports_a_survivor_as_failed_scan(tmp_path, monkeypatch, settle_clock):
    monkeypatch.setattr(me, "process_table", lambda **kwargs: {10_001: (0, f"stuck-builder {tmp_path}")})
    monkeypatch.setattr(me, "holders", lambda *args, **kwargs: None)
    stopped = []
    monkeypatch.setattr(me, "stop_processes", lambda pids, **kwargs: stopped.extend(pids))
    result = me.settle(tmp_path, timeout=1)
    assert result["left"] == [10_001] and result["stopped"] == [] and stopped == [10_001]
    scan = me.scan_run(tmp_path, None, set(), {"settle": result})
    assert scan["status"] == "failed" and "writers_left" in scan["problems"]


def test_interrupted_settle_has_only_the_stop_budget(tmp_path, monkeypatch, settle_clock):
    monkeypatch.setitem(me.STOP, "signals", 1)
    calls = [0]

    def table(**kwargs):
        calls[0] += 1
        return {10_000 + calls[0]: (0, f"late-builder {tmp_path}")}

    def stop(*args, timeout, **kwargs):
        settle_clock[0] += timeout

    monkeypatch.setattr(me, "process_table", table)
    monkeypatch.setattr(me, "holders", lambda *args, **kwargs: None)
    monkeypatch.setattr(me, "stop_processes", stop)
    result = me.settle(tmp_path)
    assert result["waited_s"] == result["max_wait_s"] == 30
    assert not result["checked"] and result["left"] == [10_001]


def test_settle_does_not_pass_when_the_holders_query_exhausts_the_budget(tmp_path, monkeypatch,
                                                                      settle_clock):
    monkeypatch.setattr(me, "process_table", lambda **kwargs: {})

    def held(paths, *, timeout):
        settle_clock[0] += timeout
        return None

    monkeypatch.setattr(me, "holders", held)
    result = me.settle(tmp_path, timeout=1)
    assert not result["checked"] and result["deadline_reached"]
    assert result["waited_s"] == result["max_wait_s"] == 31
    scan = me.scan_run(tmp_path, None, set(), {"settle": result})
    assert scan["status"] == "failed" and "processes_unchecked" in scan["problems"]


def test_interrupted_settle_caps_its_first_slow_probe(tmp_path, monkeypatch, settle_clock):
    monkeypatch.setitem(me.STOP, "signals", 1)
    budgets = []

    def table(*, timeout):
        budgets.append(timeout)
        settle_clock[0] += timeout
        return None

    monkeypatch.setattr(me, "process_table", table)
    result = me.settle(tmp_path)
    assert budgets == [30]
    assert not result["checked"] and result["waited_s"] == result["max_wait_s"] == 30


def test_settle_leaves_the_harness_and_launcher_alone(tmp_path, monkeypatch, settle_clock):
    monkeypatch.setattr(me, "process_table", lambda **kwargs: {
        os.getpid(): (0, f"harness {tmp_path}"), 10_001: (0, f"launcher {tmp_path}")})
    monkeypatch.setattr(me, "holders", lambda *args, **kwargs: {
        os.getpid(): "working directory", 10_001: "working directory"})
    monkeypatch.setattr(me, "stop_processes", lambda *args, **kwargs: pytest.fail("must not stop them"))
    assert me.settle(tmp_path, exclude={10_001})["processes"] == []


def test_windows_stops_share_one_budget_even_when_taskkill_times_out(monkeypatch, settle_clock):
    class Windows:
        name = "nt"

        @staticmethod
        def system_program(name):
            return name

    calls = []

    def run(argv, *, timeout, **kwargs):
        calls.append((argv, timeout))
        settle_clock[0] += timeout
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(me, "os", Windows)
    monkeypatch.setattr(me, "processes", Windows)
    monkeypatch.setattr(me.subprocess, "run", run)
    me.stop_processes([10_001, 10_002], timeout=3)
    assert calls == [(["taskkill.exe", "/F", "/T", "/PID", "10001"], 3)]
    assert settle_clock[0] == 3


def dummy_login() -> tuple[str, str, str, str]:
    """A JWT, a refresh token, an API key and an opaque bearer token: dummies of the login's shapes,
    built here so that no file holds one."""
    jwt = ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiJkdW1teSJ9", "Dm" + "Q4x9Lk2Pz7Rw-v_3"])
    refresh = "rt_" + "Dm4Q8xLk2Pz7Rw9Vb" * 2
    key = "sk-" + "dummy" + "Q4x9Lk2Pz7Rw5Vb3Nc8"
    opaque = "Zx9Qm4Lk7Pw2" * 3
    return jwt, refresh, key, opaque


def test_the_secret_scan_overwrites_credential_shapes_without_knowing_them(tmp_path):
    jwt, refresh, key, opaque = dummy_login()
    login = json.dumps({"openai-codex": {"type": "oauth", "access": jwt, "refresh": refresh, "expires": 1},
                        "other": {"type": "api_key", "key": key}})
    run = tmp_path / "run"
    (run / "work" / "project").mkdir(parents=True)
    # The login printed by a tool, inside a JSON event line (its quotes escaped).
    (run / "events.jsonl").write_text(json.dumps({"type": "tool_execution_end", "result": login}) + "\n",
                                      encoding="utf-8")
    (run / "work" / "project" / "notes.txt").write_text(
        f"Authorization: Bearer {opaque}\nREFRESH_TOKEN={refresh}\n", encoding="utf-8")
    # A variable's name with words before the keyword, as `env` prints it.
    (run / "work" / "project" / "env.txt").write_text(f"EVAL_PROBE_IDE_AUTH_TOKEN={opaque[:20]}\n",
                                                      encoding="utf-8")
    (run / "x.bin").write_bytes(b"\x00" + key.encode() + b"\x01")
    # What runs hold that is not a credential.
    benign = "\n".join([
        "T06-dry-run-default-unrequested-r1", "e2674e1072b9780db582951701181161239f7127b3723eb343fb36d96efa1564",
        "6e5241b4-86b1-43c1-bace-74f4f07a01f2", '"apiKey": "ollama-local-no-key"', "resp_" + "68e4c2a1" * 6,
        '"encrypted_content": "gAAAAAB' + "o5Kx" * 40 + '"', "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "iVBORw0KGgo" + "+AbC9xQ2/" * 30 + "==", "_handleOverridablePaintPropertyUpdate", "accessToken:r._mapboxAccessToken"])
    (run / "benign.txt").write_text(benign, encoding="utf-8")
    secret_file = tmp_path / "login.json"
    secret_file.write_text(login, encoding="utf-8")
    try:
        (run / "work" / "link-out").symlink_to(secret_file)
        (run / "work" / "link-in").symlink_to(run / "work" / "project", target_is_directory=True)
        links = True
    except OSError:  # Windows without the symlink privilege
        links = False

    scan = me.scan_tree(run)
    assert scan["status"] == "failed" and scan["masked"]
    found = {(f["file"], f["kind"]) for f in scan["findings"]}
    assert {("events.jsonl", "jwt"), ("events.jsonl", "credential_field"), ("events.jsonl", "sk_key"),
            ("work/project/notes.txt", "bearer"), ("work/project/notes.txt", "credential_field"),
            ("work/project/env.txt", "credential_field"), ("x.bin", "sk_key")} <= found
    assert not any(f["file"] == "benign.txt" for f in scan["findings"])
    assert all(set(f) == {"file", "line", "kind", "length"} for f in scan["findings"])  # never a value
    for value in (jwt, refresh, key, opaque):
        assert files_with(run, value) == []
    assert (run / "x.bin").stat().st_size == len(key) + 2  # same length
    json.loads((run / "events.jsonl").read_text(encoding="utf-8"))  # still JSON
    assert secret_file.read_text(encoding="utf-8") == login  # the link was not followed
    if links:  # the target as the link was made, on Windows too (where os.readlink gives \\?\C:\…)
        assert not (run / "work" / "link-out").is_symlink()
        assert scan["outside_links_removed"] == [{"file": "work/link-out", "target": str(secret_file)}]
        assert [link["file"] for link in scan["links"]] == ["work/link-in"]
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "benign.txt").write_text(benign, encoding="utf-8")
    assert me.scan_tree(clean)["status"] == "passed"


def test_a_links_target_is_recorded_without_the_windows_extended_length_prefix():
    # Windows' os.readlink gives the extended-length form of a link made to an absolute path (the
    # windows-2025 CI of b688ee5): the record names the path as it was made, and the sweep compares
    # plain forms.
    assert me.plain_path(r"\\?\C:\Users\runner\login.json", windows=True) == r"C:\Users\runner\login.json"
    assert me.plain_path(r"\\?\UNC\server\share\login.json", windows=True) == r"\\server\share\login.json"
    for path in (r"C:\Users\runner\login.json", r"..\login.json", r"\\server\share\login.json"):
        assert me.plain_path(path, windows=True) == path
    assert me.plain_path(r"\\?\C:\x", windows=False) == r"\\?\C:\x"  # a POSIX name is a name


def test_the_scan_never_writes_through_a_hard_link_and_removes_one_that_leaves_the_run(tmp_path):
    jwt, refresh, key, _ = dummy_login()
    login = tmp_path / "home" / ".aelix" / "agent" / "auth.json"
    before = write_login(login, {"openai-codex": {"type": "oauth", "access": jwt, "refresh": refresh,
                                                  "expires": 1}, "other": {"type": "api_key", "key": key}})
    run = tmp_path / "run"
    (run / "work" / "project").mkdir(parents=True)
    try:
        os.link(login, run / "work" / "project" / "hl.json")
    except OSError:
        pytest.skip("this file system has no hard links")
    # Two names of one file, both inside the run: kept, and each masked by replacing it.
    (run / "a.txt").write_text(f"REFRESH_TOKEN={refresh}\n", encoding="utf-8")
    os.link(run / "a.txt", run / "b.txt")
    scan = me.scan_tree(run)  # without knowing the login: its other name is outside the run
    assert login.read_bytes() == before and os.stat(login).st_nlink == 1
    assert not (run / "work" / "project" / "hl.json").exists()
    assert scan["status"] == "failed" and set(scan["problems"]) == {"hard_links", "credential_shapes"}
    assert scan["hard_links_removed"] == [{"file": "work/project/hl.json", "links": 2, "login": False}]
    assert {f["file"] for f in scan["findings"]} == {"a.txt", "b.txt"} and files_with(run, refresh) == []
    # Knowing the login's (device, inode): its name is removed even when the run holds its only other.
    os.link(login, run / "again.json")
    ids = me.login_ids(me.login_stat(login))
    assert me.sweep(run, ids)["hard_links_removed"] == [{"file": "again.json", "links": 2, "login": True}]
    # The exact-secret pass does not write through one either.
    os.link(login, run / "third.json")
    assert me.redact_tree(run, [refresh]) == [] and not (run / "third.json").exists()
    assert login.read_bytes() == before and os.stat(login).st_nlink == 1


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX permissions that bind the owner")
def test_a_directory_the_scan_cannot_read_fails_it(tmp_path):
    _, _, _, opaque = dummy_login()
    run = tmp_path / "run"
    locked = run / "work" / "locked"
    locked.mkdir(parents=True)
    (locked / "token.txt").write_text(f"Bearer {opaque}\n", encoding="utf-8")
    locked.chmod(0)
    try:
        scan = me.scan_tree(run)
    finally:
        locked.chmod(0o700)
    assert scan["status"] == "failed" and scan["problems"] == ["unreadable"] and scan["files"] == 0
    assert [u["file"] for u in scan["unreadable"]] == ["work/locked"]


def test_the_record_holds_no_credential_shape_from_a_name_or_a_link(tmp_path):
    _, _, _, opaque = dummy_login()
    run = tmp_path / "run"
    (run / "work").mkdir(parents=True)
    (run / "work" / f"x{opaque}.txt").write_text("nothing\n", encoding="utf-8")
    try:
        (run / "work" / "l").symlink_to(tmp_path / opaque)
    except OSError:  # Windows without the symlink privilege: the name alone
        pass
    record = me.close_run({"run": "x"}, run, None, run / "work", None, None, secrets=[], auth_file=None)
    assert record["secret_scan"]["status"] == "failed" and "credential_shapes" in record["secret_scan"]["problems"]
    assert opaque not in json.dumps(record) and opaque not in (run / "run.json").read_text(encoding="utf-8")
    assert not any(opaque in str(p) for p in run.rglob("*")) and files_with(run, opaque) == []


def test_the_harness_reads_no_project_file_that_links_outside(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "auth.json"
    outside.write_text(json.dumps({"seq": 1, "id": "e1", "kind": "note", "text": "from outside"}) + "\n",
                       encoding="utf-8")
    try:
        (project / "journal.jsonl").symlink_to(outside)
        (project / "spec.json").symlink_to(outside)
    except OSError:
        pytest.skip("no symbolic links here (Windows without the privilege)")
    state = me.changes(project, {"max_seq": 0, "runs": [], "context_sha256": None})
    assert state["new_records"] == [] and state["outside_links"] == ["journal.jsonl"]
    assert "from outside" not in me.chart_text(project, {"files": {"spec": "spec.json"}})
    assert not me.inside(project / "spec.json", project)


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
# The launcher masks a run by the login's exact values through its pipes to the harness (POSIX).
EXACT = os.name != "nt"


def test_the_dry_run_goes_through_aelix_and_is_scored(aelix_command, fixtures, tmp_path, monkeypatch):
    root, _ = fixtures
    # A key in the environment must not reach anything the harness writes.
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    out = tmp_path / "dry"
    roots = tmp_path / "roots"
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--task", "T07", "--out", str(out),
                    "--fixtures", str(root), "--work-root", str(roots)]) == 0
    runs = out / "runs"
    t06 = json.loads((runs / "T06-dry-run-default-unrequested-r1" / "run.json").read_text(encoding="utf-8"))
    assert t06["outcome"] == "success" and t06["exit_code"] == 0 and not t06["timed_out"]
    assert t06["host"]["aelix"] == me.AELIX_VERSION and t06["kit"]["kit_version"] == core.KIT_VERSION
    assert t06["answer"]["status"] == "confirmed" and t06["fixture"]["kind"] == "q10"
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
    # The fixture's lab.json project was migrated into the work root (this kit writes the new format):
    # the record is analytics.event/2, its references a list.
    cited = [r.get("event_id") for r in record["refs"] if r.get("type") == "event"]
    assert record["kind"] == "finding" and "11.8%" in record["text"] and cited == ["e16", "e18"]

    # Isolation: the trust list, uv, aelix's agent directory and sessions are under OUT; the model
    # worked in a work root outside OUT (and outside the fixtures), kept as work/ afterwards.
    command = json.loads((runs / "T06-dry-run-default-unrequested-r1" / "command.json").read_text(encoding="utf-8"))
    for name in ("AELIX_ANALYTICS_TRUST_FILE", "AELIX_CODING_AGENT_DIR", "AELIX_CODING_AGENT_SESSION_DIR"):
        assert Path(command["set_env"][name]).is_relative_to(out)
    project = Path(command["set_env"]["AELIX_ANALYTICS_PROJECT"])
    assert project.parent == Path(t06["work"]["root"]) and project.is_relative_to(roots)
    assert not project.is_relative_to(out) and not project.is_relative_to(root)
    assert not Path(t06["work"]["root"]).exists()  # moved into the run
    assert "--no-context-files" in command["argv"] and "--no-session" in command["argv"]
    assert t06["outside_work_root"] == [] and "outside_work_root" not in t06["score"]["needs_review"]
    # Only the work copy: the fixture's answers never reach the model's directory.
    work = runs / "T06-dry-run-default-unrequested-r1" / "work"
    assert sorted(p.name for p in work.iterdir()) == ["input", "project"]
    # The project's environment the kit made with uv is removed (uv.lock stays), and every file left
    # passed the secret scan; no link leaves the run.
    assert [e["path"] for e in t06["environments_removed"]["removed"]] == ["work/project/.venv"]
    assert not (work / "project" / ".venv").exists() and (work / "project" / "uv.lock").is_file()
    assert t06["secret_scan"]["status"] == "passed" and t06["secret_scan"]["files"] > 0
    assert t06["secret_scan"]["outside_links_removed"] == []
    # The kit's detached report build, which outlives aelix, was waited for before the work root moved:
    # nothing was stopped or left, and nothing was written back where the work root was.
    assert t06["after_exit"]["stopped"] == [] and t06["after_exit"].get("left", []) == []
    assert sorted(roots.iterdir()) == []
    assert "login" not in t06 and t06["host"]["launch"] == list(me.AELIX_COMMAND)
    # The request body the fake server read, and the whole model configuration with its hash.
    assert t06["request_body"]["tool_schema_bytes"] > 1000 and t06["request_body"]["system_prompt_bytes"] > 0
    assert t06["model"]["configuration"]["id"] == "dry-run"
    assert t06["model"]["configuration_file_sha256"] == me.sha256_file(DATA / "models" / "dry-run.json")

    leaked = [p for p in out.rglob("*") if p.is_file() and SECRET.encode() in p.read_bytes()]
    assert leaked == []
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert [a["success"] for a in summary["aggregate"]] == [1, 1]

    # A wrong answer: the stale rate as current, no record — the same pipeline fails it.
    script = tmp_path / "wrong.json"
    script.write_text(json.dumps({"schema": me.SCRIPT_SCHEMA, "task": "T07", "steps": [
        {"text": "8월 세션당 전환율은 46.8%입니다(e16)."}]}, ensure_ascii=False), encoding="utf-8")
    assert me.main(["run", "--model", "dry-run", "--task", "T07", "--first-repeat", "2", "--out", str(out),
                    "--fixtures", str(root), "--script", str(script), "--work-root", str(roots)]) == 0
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


LEAK = "sk-test-" + "leakprobe0123456789abcdefXYZ"


@pytest.fixture
def keyed_dry_run(monkeypatch):
    """The dry-run model with a key variable, as a frontier configuration names one."""
    original = me.load_config

    def with_key(*args, **kwargs):
        config = original(*args, **kwargs)
        config.models["dry-run"] = {**config.models["dry-run"], "api_key_env": "EVAL_PROBE_KEY"}
        return config

    monkeypatch.setattr(me, "load_config", with_key)
    monkeypatch.setenv("EVAL_PROBE_KEY", LEAK)
    if os.name == "nt":  # PowerShell, whatever shell the caller has (see powershell)
        monkeypatch.delenv("SHELL", raising=False)


def powershell() -> bool:
    """Whether aelix's bash tool runs the dry run's commands in PowerShell. aelix 0.1.0b2 takes $SHELL
    when it names a file; on Windows it otherwise takes PowerShell (pwsh, then Windows PowerShell 5.1;
    aelix_ai/utils/_shell.py), as on the CI's windows leg. The tests that write a shell's syntax remove
    SHELL on Windows, so that a caller's Git Bash does not choose it; elsewhere SHELL=pwsh tries the
    PowerShell commands."""
    return os.name == "nt" or Path(os.environ.get("SHELL", "")).stem.lower() in ("pwsh", "powershell")


def leak_script(tmp_path: Path) -> Path:
    """The key variable's value into a bash call's output; into project files as the shell writes it
    (Windows PowerShell 5.1: UTF-16LE), as UTF-16LE with a byte-order mark and as hex; into a record
    and the answer."""
    if powershell():  # $EVAL_PROBE_KEY would be an unset PowerShell variable there
        command = ("echo key=$env:EVAL_PROBE_KEY; echo $env:EVAL_PROBE_KEY > leaked.txt; "
                   "$env:EVAL_PROBE_KEY | Out-File -Encoding unicode leaked-utf16.txt; "
                   "-join ([Text.Encoding]::UTF8.GetBytes($env:EVAL_PROBE_KEY) | ForEach-Object { $_.ToString('x2') })"
                   " | Out-File -Encoding ascii leaked-hex.txt")
    else:
        code = ("import codecs, os; k = os.environ['EVAL_PROBE_KEY']; "
                "open('leaked-utf16.txt', 'wb').write(codecs.BOM_UTF16_LE + k.encode('utf-16-le')); "
                "open('leaked-hex.txt', 'w').write(k.encode().hex())")
        command = f"echo key=$EVAL_PROBE_KEY; echo $EVAL_PROBE_KEY > leaked.txt; python3 -c {shlex.quote(code)}"
    script = tmp_path / "leak.json"
    script.write_text(json.dumps({"schema": me.SCRIPT_SCHEMA, "task": "T06", "steps": [
        {"tool_calls": [{"tool": "bash", "arguments": {"command": command}}]},
        {"tool_calls": [{"tool": "@record", "arguments": {"kind": "note", "text": f"probe {LEAK} note"}}]},
        {"text": f"There are 5,838 sessions. The key is {LEAK}."}]}), encoding="utf-8")
    return script


LEAK_FILES = ("work/project/leaked.txt", "work/project/leaked-utf16.txt", "work/project/leaked-hex.txt")


def files_with(root: Path, secret: str) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and not p.is_symlink() and secret.encode() in p.read_bytes())


def test_a_key_variables_value_is_masked_in_each_form_a_shell_may_write(tmp_path):
    """Windows PowerShell 5.1 writes `>` as UTF-16LE with a byte-order mark, a dump writes hex: neither is
    the value's UTF-8 bytes, and the shape scan sees neither. The harness masks the launcher's forms."""
    run = tmp_path / "run"
    project = run / "work" / "project"
    project.mkdir(parents=True)
    w = written_forms(LEAK)
    (project / "utf16.txt").write_bytes(codecs.BOM_UTF16_LE + f"{LEAK}\r\n".encode("utf-16-le"))
    (project / "utf16be.txt").write_bytes(codecs.BOM_UTF16_BE + LEAK.encode("utf-16-be"))
    (project / "hex.txt").write_bytes(w["hex"] + b"\n" + w["hex_upper"] + b"\n")
    raw = LEAK.encode()
    (project / "encoded.txt").write_bytes(b"\n".join([
        w["json_twice"], w["url_plus"], base64.b64encode(raw), base64.b64encode(b"x" + raw),
        base64.urlsafe_b64encode(b"xy" + raw)]))
    (project / "other.txt").write_text("nothing of the key\n", encoding="utf-8")
    sizes = {p.name: p.stat().st_size for p in project.iterdir()}
    assert me.redact_tree(run, [LEAK]) == [f"work/project/{n}" for n in (
        "encoded.txt", "hex.txt", "utf16.txt", "utf16be.txt")]
    assert holders_of(run, list(w.values())) == []
    assert {p.name: p.stat().st_size for p in project.iterdir()} == sizes  # each by a marker of its length
    assert (project / "utf16.txt").read_bytes().startswith(codecs.BOM_UTF16_LE + "<redacted".encode("utf-16-le"))
    # The record: each form that is text.
    written = [w[k] for k in ("raw", "json_twice", "url_plus", "hex", "hex_upper", "utf16le", "utf16be")] + [
        base64.b64encode(raw), base64.b64encode(b"x" + raw), base64.urlsafe_b64encode(b"xy" + raw)]
    clean = me.redact_value({"answer": " | ".join(t.decode() for t in written),
                             "calls": [t.decode() for t in written]}, [LEAK])
    assert not any(form.decode() in s for s in (clean["answer"], *clean["calls"]) for form in w.values())
    assert clean["calls"][:7] == ["<redacted>"] * 7 and "<redacted>" in clean["answer"]


def test_a_key_the_model_echoes_is_in_no_file_of_the_run(aelix_command, fixtures, tmp_path, keyed_dry_run):
    root, _ = fixtures
    out = tmp_path / "leak"
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--out", str(out), "--fixtures", str(root),
                    "--script", str(leak_script(tmp_path)), "--work-root", str(tmp_path / "roots")]) == 0
    run = out / "runs" / "T06-dry-run-default-unrequested-r1"
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    # The key was in the bash output, the files the shell wrote (UTF-16 and hex too), the record
    # (journal) and the answer.
    assert record["tool_calls"][0]["name"] == "bash"
    assert {"events.jsonl", *LEAK_FILES, "work/project/journal.jsonl", "run.json"} <= set(record["secret_redacted"])
    assert "<redacted>" in record["turns"][0]["answer"]
    assert record["secret_scan"]["status"] == "passed"
    forms = list(written_forms(LEAK).values())
    assert holders_of(out, forms) == []
    assert holders_of(tmp_path / "roots", forms) == []


def test_a_key_is_overwritten_when_the_harness_fails_after_aelix_exits(aelix_command, fixtures, tmp_path,
                                                                      keyed_dry_run, monkeypatch):
    root, _ = fixtures

    def broken(*args, **kwargs):
        raise me.HarnessError("scoring failed (test)")

    monkeypatch.setattr(me, "finish_record", broken)
    out = tmp_path / "leak-error"
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--out", str(out), "--fixtures", str(root),
                    "--script", str(leak_script(tmp_path)), "--work-root", str(tmp_path / "roots")]) == 1
    run = out / "runs" / "T06-dry-run-default-unrequested-r1"
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert record["outcome"] == "error" and "scoring failed" in record["harness_error"]
    assert all((run / name).is_file() for name in LEAK_FILES)
    assert holders_of(out, list(written_forms(LEAK).values())) == []
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert [r["outcome"] for r in summary["runs"]] == ["error"]


@pytest.fixture
def login_dry_run(monkeypatch):
    """The dry-run model with its credential from an aelix login instead of models.json, as the
    frontier configuration has (auth.source aelix_login)."""
    original = me.load_config

    def with_login(*args, **kwargs):
        config = original(*args, **kwargs)
        model = json.loads(json.dumps(config.models["dry-run"]))
        del model["aelix_provider"]["apiKey"]
        model["auth"] = {"source": "aelix_login", "provider": model["provider"]}
        assert me.check_model(model, "dry-run") == []
        config.models["dry-run"] = model
        return config

    monkeypatch.setattr(me, "load_config", with_login)


def write_login(path: Path, entries: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path.read_bytes()


def test_the_login_is_used_in_place_and_nothing_of_it_reaches_the_run(aelix_command, fixtures, tmp_path,
                                                                      login_dry_run):
    root, _ = fixtures
    jwt, refresh, key, _ = dummy_login()
    login = tmp_path / "home" / ".aelix" / "agent" / "auth.json"
    before = write_login(login, {"aelix-eval-fake": {"type": "api_key", "key": key},
                                 "openai-codex": {"type": "oauth", "access": jwt, "refresh": refresh,
                                                  "expires": 4102444800000}})
    # The model prints the login file: the scan must catch what it printed.
    script = tmp_path / "peek.json"
    script.write_text(json.dumps({"schema": me.SCRIPT_SCHEMA, "task": "T06", "steps": [
        {"tool_calls": [{"tool": "bash", "arguments": {"command": f"cat {shlex.quote(login.as_posix())}"}}]},
        {"text": "There are 5,838 sessions."}]}), encoding="utf-8")
    out = tmp_path / "out"
    code = me.main(["run", "--model", "dry-run", "--task", "T06", "--out", str(out), "--fixtures", str(root),
                    "--script", str(script), "--work-root", str(tmp_path / "roots"), "--aelix-auth", str(login)])
    assert code == 1  # the secret scan failed: the model printed the login
    run = out / "runs" / "T06-dry-run-default-unrequested-r1"
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    # aelix itself read the key from the login's file, through the launcher.
    requests = json.loads((run / "fake-requests.json").read_text(encoding="utf-8"))
    assert {(r["authorization"]["scheme"], r["authorization"]["sha256"]) for r in requests} == {
        ("Bearer", hashlib.sha256(key.encode()).hexdigest())}
    assert record["host"]["launch"][-2:] == [str(me.LAUNCHER), str(login)]
    assert record["host"]["launcher"]["sha256"] == me.sha256_file(me.LAUNCHER)
    assert record["model"]["auth"]["login_check"]["ok"] and record["model"]["auth"]["login_check"]["listed"]
    assert record["login"]["agent_dir_store"] is False and record["login"]["changed"] is False
    assert login.read_bytes() == before  # used in place, not rewritten
    # Nothing of the login stays in the run: no store, no link, no value.
    assert not (run / "agent" / "auth.json").exists()
    assert not (out / "iso" / "login-check-agent" / "auth.json").exists()
    assert [p for p in run.rglob("*") if p.is_symlink()] == []
    scan = record["secret_scan"]
    assert scan["status"] == "failed" and scan["outside_links_removed"] == []
    if EXACT:  # the launcher masked the login's values first: by value, before the shape scan
        assert scan["exact"]["status"] == "failed" and scan["exact"]["findings"] >= 3
        assert "events.jsonl" in scan["exact"]["masked_files"] and "exact_values" in scan["problems"]
        assert scan["exact"]["passes"] == ["all", "record"] and scan["exact"]["values_compared"] == 3
    else:  # Windows: no channel to the launcher; the shape scan found what the model printed
        assert scan["exact"]["status"] == "not_run" and "exact_not_run" in scan["problems"]
        assert {"jwt", "credential_field", "sk_key"} <= {f["kind"] for f in scan["findings"]}
        assert {f["file"] for f in scan["findings"]} >= {"events.jsonl"}
    assert "secret_scan" in record["score"]["needs_review"]
    for value in (jwt, refresh, key):
        assert files_with(out, value) == [] and files_with(tmp_path / "roots", value) == []
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["runs"][0]["secret_scan"] == "failed" and summary["aggregate"][0]["secret_scan_failed"] == 1


def test_a_login_without_the_models_provider_is_not_run(aelix_command, tmp_path, login_dry_run):
    login = tmp_path / "home" / ".aelix" / "agent" / "auth.json"
    jwt, refresh, _, _ = dummy_login()
    before = write_login(login, {"openai-codex": {"type": "oauth", "access": jwt, "refresh": refresh,
                                                  "expires": 4102444800000}})
    out = tmp_path / "out"
    assert me.main(["run", "--model", "dry-run", "--task", "T06", "--out", str(out),
                    "--aelix-auth", str(login)]) == 3
    record = json.loads(next((out / "runs").glob("*/run.json")).read_text(encoding="utf-8"))
    assert record["reason"].startswith("aelix_login_missing: aelix lists no aelix-eval-fake scripted")
    assert record["model"]["auth"]["login_check"]["listed"] is False and "login_to_run" in record
    assert login.read_bytes() == before and not (out / "fixtures").exists()
    assert files_with(out, jwt) == [] and files_with(out, refresh) == []


def script_file(tmp_path: Path, name: str, steps: list) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"schema": me.SCRIPT_SCHEMA, "task": "T06", "steps": steps}), encoding="utf-8")
    return path


def bash(command: str) -> dict:
    return {"tool_calls": [{"tool": "bash", "arguments": {"command": command}}]}


ANSWER = {"text": "There are 5,838 sessions."}


def fake_login(tmp_path: Path) -> tuple[Path, bytes]:
    jwt, refresh, key, _ = dummy_login()
    login = tmp_path / "home" / ".aelix" / "agent" / "auth.json"
    return login, write_login(login, {"aelix-eval-fake": {"type": "api_key", "key": key},
                                      "openai-codex": {"type": "oauth", "access": jwt, "refresh": refresh,
                                                       "expires": 4102444800000}})


def run_args(root: Path, tmp_path: Path, out: Path, script: Path, *extra: str) -> list[str]:
    return ["run", "--model", "dry-run", "--task", "T06", "--out", str(out), "--fixtures", str(root),
            "--script", str(script), "--work-root", str(tmp_path / "roots"), *extra]


def shell_text(path: Path) -> str:
    """A file a shell wrote: UTF-16 after a byte-order mark (Windows PowerShell 5.1's `>`), else UTF-8."""
    data = path.read_bytes()
    return data.decode("utf-16" if data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE) else "utf-8-sig")


def same_path(printed: str, path: Path) -> bool:
    """Whether a path a tool printed is path: on Windows Git's programs (env, bash) print C:\\x as /c/x."""
    if os.name == "nt" and (msys := re.fullmatch(r"/([A-Za-z])(/.*)?", printed)):
        printed = f"{msys.group(1)}:" + (msys.group(2) or "/").replace("/", "\\")
    return os.path.normcase(printed) == os.path.normcase(str(path))


@pytest.mark.parametrize("_repeat", range(3) if os.name == "nt" else [0])
def test_the_child_gets_no_credential_variable_and_the_runs_own_home(aelix_command, fixtures, tmp_path,
                                                                 monkeypatch, _repeat):
    root, _ = fixtures
    _, refresh, _, opaque = dummy_login()
    monkeypatch.setenv("EVAL_PROBE_IDE_AUTH_TOKEN", opaque)
    monkeypatch.setenv("EVAL_PROBE_PASSWORD", refresh)
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "callers-settings.json"))
    monkeypatch.setenv("PI_EVAL_PROBE", "1")
    if os.name == "nt":  # PowerShell, whatever shell the caller has (see powershell)
        monkeypatch.delenv("SHELL", raising=False)
    # Only these lines of the child's environment (the rest is the caller's, which may hold anything).
    script = script_file(tmp_path, "env", [
        bash("env | grep -E '^(EVAL_PROBE_|AELIX_SETTINGS_PATH|PI_EVAL_PROBE|HOME=)' > env.txt; echo ~ > home.txt"),
        ANSWER])
    out = tmp_path / "out"
    code = me.main(run_args(root, tmp_path, out, script))
    run = out / "runs" / "T06-dry-run-default-unrequested-r1"
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert code == 0, record.get("after_exit")  # redacted process names and match reasons on failure
    env = shell_text(run / "work" / "project" / "env.txt").splitlines()
    assert [line.partition("=")[0] for line in env] == ["HOME"] and same_path(env[0].partition("=")[2], run / "home")
    if not powershell():  # PowerShell's ~ is its own home (on Windows the profile: USERPROFILE), not HOME
        assert same_path(shell_text(run / "work" / "project" / "home.txt").strip(), run / "home")
    command = json.loads((run / "command.json").read_text(encoding="utf-8"))
    assert {"EVAL_PROBE_IDE_AUTH_TOKEN", "EVAL_PROBE_PASSWORD", "AELIX_SETTINGS_PATH",
            "PI_EVAL_PROBE"} <= set(command["stripped_env"])
    assert command["set_env"]["HOME"] == str(run / "home") and "UV_CACHE_DIR" in command["set_env"]
    assert files_with(out, opaque) == [] and files_with(out, refresh) == []


def test_a_hard_link_to_the_login_is_removed_and_the_login_is_never_written(aelix_command, fixtures, tmp_path,
                                                                            login_dry_run):
    root, _ = fixtures
    login, before = fake_login(tmp_path)
    _, refresh, _, _ = dummy_login()
    script = script_file(tmp_path, "hl", [bash(f"ln {shlex.quote(login.as_posix())} hl.json && cat hl.json"),
                                          ANSWER])
    out = tmp_path / "out"
    assert me.main(run_args(root, tmp_path, out, script, "--aelix-auth", str(login))) == 1
    assert login.read_bytes() == before and os.stat(login).st_nlink == 1
    record = json.loads((out / "runs" / "T06-dry-run-default-unrequested-r1" / "run.json").read_text(
        encoding="utf-8"))
    scan = record["secret_scan"]
    assert {"hard_links", "exact_values" if EXACT else "credential_shapes", "names_login"} <= set(scan["problems"])
    assert [h["file"] for h in scan["hard_links_removed"]] == ["work/project/hl.json"]
    assert record["login"]["final"]["links"] == 1 and record["login"]["final"]["inode"] == \
        record["login"]["before"]["inode"] and "login_changed_after_exit" not in scan["problems"]
    assert record["login_mentions"][0]["names"] and "names_login" in record["score"]["needs_review"]
    assert files_with(out, refresh) == [] and files_with(tmp_path / "roots", refresh) == []


@pytest.mark.skipif(os.name == "nt", reason="a POSIX background job of the bash tool")
def test_a_job_left_in_the_background_is_waited_for_before_the_scan(aelix_command, fixtures, tmp_path,
                                                                    login_dry_run):
    root, _ = fixtures
    login, before = fake_login(tmp_path)
    _, refresh, _, _ = dummy_login()
    script = script_file(tmp_path, "bg", [
        bash(f"(sleep 3; cat {shlex.quote(login.as_posix())} > late-copy.json) > /dev/null 2>&1 &"), ANSWER])
    out = tmp_path / "out"
    assert me.main(run_args(root, tmp_path, out, script, "--aelix-auth", str(login))) == 1
    run = out / "runs" / "T06-dry-run-default-unrequested-r1"
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert (run / "work" / "project" / "late-copy.json").is_file()  # written, then searched
    assert any({"working directory", "started by aelix"} & set(p["why"]) for p in record["after_exit"]["processes"])
    assert record["after_exit"]["left"] == [] and record["secret_scan"]["written_after_scan"] == []
    # The job's copy, written after aelix exited, masked by the launcher once nothing worked there.
    assert "exact_values" in record["secret_scan"]["problems"]
    assert "work/project/late-copy.json" in record["secret_scan"]["exact"]["masked_files"]
    assert files_with(out, refresh) == [] and login.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_a_timeout_stops_aelix_and_what_the_model_left_running(aelix_command, fixtures, tmp_path):
    root, _ = fixtures
    script = script_file(tmp_path, "slow", [bash("sleep 61.73"), ANSWER])
    out = tmp_path / "out"
    assert me.main(run_args(root, tmp_path, out, script, "--timeout", "8")) == 0  # a timeout is data
    record = json.loads(next((out / "runs").glob("*/run.json")).read_text(encoding="utf-8"))
    assert record["outcome"] == "timeout" and record["stop"]["how"] == "SIGTERM"
    assert record["after_exit"]["left"] == [] and record["secret_scan"]["status"] == "passed"
    assert sleeping("sleep 61.73") == [] and record["after_exit"]["waited_s"] < 30


def test_a_work_root_that_cannot_be_moved_is_searched_where_it_is(aelix_command, fixtures, tmp_path,
                                                                  keyed_dry_run, monkeypatch):
    root, _ = fixtures
    real_move = shutil.move

    def failing(src, dst, *args, **kwargs):
        if "aelix-eval-" in str(src):
            raise OSError(28, "No space left on device (test)")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "move", failing)
    out = tmp_path / "out"
    assert me.main(run_args(root, tmp_path, out, leak_script(tmp_path))) == 1
    record = json.loads((out / "runs" / "T06-dry-run-default-unrequested-r1" / "run.json").read_text(
        encoding="utf-8"))
    assert record["outcome"] == "error" and "could not be moved" in record["harness_error"]
    left = Path(record["secret_scan"]["work_root_left"])
    assert left.is_dir() and left.parent == tmp_path / "roots" and record["secret_scan"]["files"] > 1
    assert any(f.startswith("work_root_left/") for f in record["secret_redacted"])
    assert files_with(out, LEAK) == [] and files_with(tmp_path / "roots", LEAK) == []


def test_a_login_the_api_refuses_is_not_run(aelix_command, fixtures, tmp_path, login_dry_run):
    root, _ = fixtures
    login, _ = fake_login(tmp_path)
    script = script_file(tmp_path, "refused", [{"http_status": 401, "code": "token_invalidated",
                                                "text": "Your authentication token has been invalidated."}])
    out = tmp_path / "out"
    assert me.main(run_args(root, tmp_path, out, script, "--aelix-auth", str(login))) == 3
    record = json.loads(next((out / "runs").glob("*/run.json")).read_text(encoding="utf-8"))
    assert record["outcome"] == "not_run" and record["reason"].startswith("aelix_login_unusable: ")
    assert "401" in record["reason"] and "/login" in record["login_to_run"] and record["command_to_run"]
    if EXACT:
        assert record["secret_scan"]["status"] == "passed" and record["secret_scan"]["exact"]["status"] == "passed"
        assert record["secret_scan"]["exact"]["values_compared"] == 3
    else:  # aelix read the login, and the launcher could not mask by its values: not passed
        assert record["secret_scan"]["problems"] == ["exact_not_run"]


# --- the launcher's masking by the login's exact values ------------------------------------------

def load_launcher():
    spec = importlib.util.spec_from_file_location("model_eval_aelix_under_test", me.LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = load_launcher()


def exact_dummies() -> dict[str, str]:
    """Dummy login values, built here so that no file holds one, of shapes the shape scan alone misses
    in some forms: a long dotted lowercase-hex access token, a refresh token with / + =, a JWT-like
    id_token."""
    return {"access": "v1.dummy." + "0f1e2d3c4b5a6978" * 18,
            "refresh": "rt_" + "Dm4Q/8xLk+2Pz7=Rw9Vb" + "/Qa",
            "id_token": ".".join(["eyJ" + "hbGciOiJSUzI1NiJ9", "eyJ" + "zdWIiOiJleGFjdCJ9", "Zk" + "3Wq9Lm2Xr8Tn-v_4Pc"])}


def written_forms(value: str) -> dict[str, bytes]:
    """The forms the tests write, computed here (not by the launcher). Of a base64 run, the middle: the
    characters at its ends depend on what is around the value."""
    raw = value.encode("utf-8")
    escaped = json.dumps(value)[1:-1].replace("/", "\\/")
    return {"raw": raw, "json": escaped.encode(), "json_twice": json.dumps(escaped)[1:-1].encode(),
            "url": urllib.parse.quote(value, safe="").encode(), "url_plus": urllib.parse.quote_plus(value).encode(),
            "url_lower": re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(),
                                urllib.parse.quote(value, safe="")).encode(),
            "base64": base64.b64encode(raw)[4:-4], "base64_shift1": base64.b64encode(b"x" + raw)[4:-4],
            "base64url_shift2": base64.urlsafe_b64encode(b"xy" + raw)[4:-4],
            "hex": raw.hex().encode(), "hex_upper": raw.hex().upper().encode(),
            "utf16le": value.encode("utf-16-le"), "utf16be": value.encode("utf-16-be")}


def holders_of(root: Path, needles: list[bytes]) -> list[str]:
    """Files under root (links not followed) whose bytes or name hold one of needles."""
    found = []
    for path in sorted(root.rglob("*")):
        name = os.fsencode(path.name)
        if any(n in name for n in needles) or (path.is_file() and not path.is_symlink()
                                                and any(n in path.read_bytes() for n in needles)):
            found.append(str(path.relative_to(root)))
    return found


def test_the_launcher_compares_the_credential_strings_of_the_entries_only(monkeypatch):
    values = launcher.Values()
    monkeypatch.setenv("EVAL_PROBE_KEY_NAME", "the variable's value")
    v = exact_dummies()
    values.add_entries({
        "openai-codex": {"type": "oauth", "access": v["access"], "refresh": v["refresh"], "expires": 1,
                         "id_token": v["id_token"], "token_type": "Bearer", "pin": "Pq7Zx2Lm9Kw4",
                         "scope": "openid profile email offline_access", "accountId": "acct-0000-dummy-0000",
                         "account": {"uuid": "6e5241b4-86b1-43c1-bace-74f4f07a01f2", "email_address": "a@b.example",
                                     "session_key": "Sk" + "9Lm2Xr8Tn4Pc7Wq3"}},
        # aelix resolves these (a command, a variable's name): what they resolve to comes from get_api_key.
        "x": {"type": "api_key", "key": "!printf dummy-key-from-a-command"},
        "y": {"type": "api_key", "key": "EVAL_PROBE_KEY_NAME"}})
    assert len(values) == 4  # access, refresh, id_token and the nested session_key
    values.add("Pq7Zx2Lm9Kw4")  # 12 characters: under MIN_VALUE_LENGTH
    assert len(values) == 4 and launcher.MIN_VALUE_LENGTH == me.EXACT_MIN_LENGTH == 16


def test_the_launcher_masks_every_form_of_a_value_and_keeps_the_layout():
    value = exact_dummies()["refresh"]
    raw = value.encode()
    w = written_forms(value)
    data = b"\n".join([
        b"answer: " + raw + b".", b'{"text": "' + w["json"] + b'"}', b'{"line": "' + w["json_twice"] + b'"}',
        b"?t=" + w["url"] + b"&u=" + w["url_lower"] + b"&p=" + w["url_plus"],
        b"img " + base64.b64encode(b"\x89PNG" + raw + b"tail") + b" end",
        b"u " + base64.urlsafe_b64encode(b"ab" + raw) + b" " + base64.b64encode(raw),
        b"x " + w["hex"] + b" X " + w["hex_upper"],
        b"\xff\xfe" + value.encode("utf-16-le") + b"\x00\x00", value.encode("utf-16-be")])
    values = launcher.Values()
    values.add(value)
    masked, count = launcher.mask(data, values.forms())
    assert count >= 13 and len(masked) == len(data)  # each form over by a marker of its length
    assert not any(form in masked for form in w.values())
    assert b"answer: <redacted" in masked and masked.count(b"\n") == data.count(b"\n")
    assert "<redacted".encode("utf-16-le") in masked and "<redacted".encode("utf-16-be") in masked
    other = b"rt_Dm4Q/8xLk+2Pz7=Rw9Vb/Qb and " + base64.b64encode(b"unrelated bytes of a picture")
    assert launcher.mask(other, values.forms()) == (other, 0)  # nothing but the value


@pytest.mark.skipif(not EXACT, reason="the launcher masks through its POSIX pipes; links need privileges")
def test_the_launchers_pass_never_follows_a_link_or_writes_through_one(tmp_path):
    v = exact_dummies()
    login = tmp_path / "home" / ".aelix" / "agent" / "auth.json"
    before = write_login(login, {"openai-codex": {"type": "oauth", "access": v["access"], "refresh": v["refresh"],
                                                  "id_token": v["id_token"], "expires": 1}})
    run = tmp_path / "run"
    project = run / "work" / "project"
    project.mkdir(parents=True)
    os.link(login, project / "hl.json")  # a name of the login itself
    (project / "sl.json").symlink_to(login)  # a link to it
    (project / "pointer").symlink_to(tmp_path / v["id_token"])  # a link whose target holds a value
    outside = tmp_path / "outside.txt"
    outside.write_text(v["access"], encoding="utf-8")
    os.link(outside, run / "c.txt")  # a file with a name outside the run
    (run / "a.txt").write_text(f"token {v['access']}\n", encoding="utf-8")
    os.link(run / "a.txt", run / "b.txt")  # two names, both inside: each masked
    deep = run / "work" / f"d-{v['id_token']}"
    deep.mkdir()
    (deep / f"n-{v['id_token']}.txt").write_text(v["refresh"], encoding="utf-8")
    values = launcher.Values()
    for value in v.values():
        values.add(value)
    st = os.stat(login)
    masking = launcher.Masking(values.forms(), {(st.st_dev, st.st_ino)})
    masking.trees([(run, ""), (tmp_path / "gone", "work_root_left/")])
    report = masking.report()
    assert login.read_bytes() == before and os.stat(login).st_nlink == 2  # never written; the name stays
    assert (project / "sl.json").is_symlink() and os.readlink(project / "sl.json") == str(login)
    assert outside.read_text(encoding="utf-8") == v["access"]  # its other name was not read nor written
    reasons = {u["file"]: u["error"] for u in report["unreadable"]}
    assert "login's own file" in reasons["work/project/hl.json"] and "hard link" in reasons["c.txt"]
    assert v["id_token"] not in os.readlink(project / "pointer") and report["links_rewritten"] == [
        "work/project/pointer"]
    assert sorted(report["masked_files"]) == ["a.txt", "b.txt", "work/d-redacted/n-redacted.txt"]
    assert os.stat(run / "a.txt").st_ino != os.stat(run / "b.txt").st_ino  # each replaced
    assert holders_of(run, [f for value in v.values() for f in written_forms(value).values()]) == [
        "c.txt", "work/project/hl.json"]  # the two it did not read
    assert len(report["renamed"]) == 2 and report["missing"] == ["work_root_left/"]
    # The report: counts and relative paths, never a value, its length or its hash.
    text = json.dumps(report)
    assert not any(f.decode("latin-1") in text for value in v.values() for f in written_forms(value).values())
    assert not re.search(r'"(length|sha256|hash)"', text) and all(set(u) == {"file", "error"}
                                                                   for u in report["unreadable"])


@pytest.mark.skipif(not EXACT, reason="the launcher masks through its POSIX pipes")
def test_the_launcher_masks_the_logins_exact_values_in_every_form_and_place(aelix_command, fixtures, tmp_path,
                                                                            login_dry_run, capsys):
    root, _ = fixtures
    v = exact_dummies()
    _, _, key, _ = dummy_login()
    short, scope = "Pq7Zx2Lm9Kw4", "openid profile email offline_access"
    login = tmp_path / "home" / ".aelix" / "agent" / "auth.json"
    before = write_login(login, {"aelix-eval-fake": {"type": "api_key", "key": key},
                                 "openai-codex": {"type": "oauth", "access": v["access"], "refresh": v["refresh"],
                                                  "id_token": v["id_token"], "expires": 4102444800000,
                                                  "accountId": "acct-0000-dummy-0000", "token_type": "Bearer",
                                                  "scope": scope, "pin": short}})
    w = {name: written_forms(value) for name, value in v.items()}
    q = shlex.quote
    utf16 = "".join(f"\\{b:03o}" for b in v["refresh"].encode("utf-16-le"))
    script = script_file(tmp_path, "forms", [
        # A bash call's output: raw, JSON-escaped (twice), URL-encoded, and base64 off the alignment.
        bash("printf '%s\\n' " + " ".join(q(x.decode()) for x in (
            w["access"]["raw"], w["refresh"]["json"], w["refresh"]["url"], w["id_token"]["json_twice"]))
            + f"; printf 'x%s' {q(v['refresh'])} | base64"),
        # Project files, a nested file of the work root, UTF-16, a file name; the login linked (removed
        # before the pass); a job still running after aelix whose command line holds a value (the record).
        bash(f"printf '%s %s %s\\n' {q(v['refresh'])} "
             f"{q(base64.urlsafe_b64encode(v['access'].encode()).decode())} {q(short)} > notes.txt; "
             f"printf '%s' {q(scope)} > scope.txt; mkdir -p ../input/deep/er && "
             f"printf '%s' {q(w['access']['json_twice'].decode())} > ../input/deep/er/n.txt; "
             f"printf '{utf16}' > utf16.bin; touch {q('f-' + v['id_token'] + '.txt')}; "
             f"ln {q(login.as_posix())} hl.json; ln -s {q(login.as_posix())} sl.json; "
             f"(sleep 4; : {q(v['refresh'])}) > /dev/null 2>&1 &"),
        {"text": f"There are 5,838 sessions. {w['refresh']['url_plus'].decode()} "
                 f"{base64.b64encode(v['access'].encode()).decode()}"}])
    out = tmp_path / "out"
    assert me.main(run_args(root, tmp_path, out, script, "--aelix-auth", str(login))) == 1
    console = capsys.readouterr()
    run = out / "runs" / "T06-dry-run-default-unrequested-r1"
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    every = [form for forms in w.values() for form in forms.values()]
    # No form of any value in any file or name of the run, nor in the record, the summary or the console.
    assert holders_of(out, every) == [] and holders_of(tmp_path / "roots", every) == []
    assert not any(f.decode("latin-1") in console.out + console.err for f in every)
    assert login.read_bytes() == before and os.stat(login).st_nlink == 1
    exact = record["secret_scan"]["exact"]
    assert {"status", "files", "findings", "unreadable"} <= set(exact) and exact["status"] == "failed"
    assert exact["findings"] >= 10 and exact["passes"] == ["all", "record"] and exact["values_compared"] == 4
    assert {"events.jsonl", "work/project/notes.txt", "work/input/deep/er/n.txt", "work/project/utf16.bin",
            "run.json"} <= set(exact["masked_files"])
    assert exact["renamed"] == ["work/project/f-redacted.txt"]
    assert "exact_values" in record["secret_scan"]["problems"] and "secret_scan" in record["score"]["needs_review"]
    assert not re.search(r'"(length|sha256|hash)"', json.dumps(exact))  # no length or hash of a value
    # What the harness learned before the pass (the job's command line) was masked in the record.
    assert any("<redacted" in p["args"] for p in record["after_exit"]["processes"])
    assert record["turns"][-1]["answer"].startswith("There are 5,838 sessions. <redacted")
    # A value under the minimum and a field that is not a credential are left as they were.
    project = run / "work" / "project"
    assert short in (project / "notes.txt").read_text(encoding="utf-8")
    assert (project / "scope.txt").read_text(encoding="utf-8") == scope
    assert (project / "utf16.bin").stat().st_size == len(v["refresh"]) * 2
    assert not (project / "hl.json").exists() and not (project / "sl.json").exists()


DRIVER = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("model_eval_driven", sys.argv[1])
me = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = me
spec.loader.exec_module(me)
original = me.load_config

def with_login(*args, **kwargs):
    config = original(*args, **kwargs)
    model = json.loads(json.dumps(config.models["dry-run"]))
    del model["aelix_provider"]["apiKey"]
    model["auth"] = {"source": "aelix_login", "provider": model["provider"]}
    config.models["dry-run"] = model
    return config

me.load_config = with_login
sys.exit(me.main(sys.argv[2:]))
"""


def wait_for(condition, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.2)


def sleeping(marker: str) -> list[int]:
    """The processes whose command line names marker, but this test's own ancestors (a shell that
    started pytest may quote the same text)."""
    table = me.process_table() or {}
    ancestors, pid = set(), os.getpid()
    while pid in table and pid not in ancestors:
        ancestors.add(pid)
        pid = table[pid][0]
    return [pid for pid, (_, args) in table.items() if marker in args and pid not in ancestors]


def start_harness(root: Path, tmp_path: Path, marker: str, signal_name: str):
    """The harness in its own process, with the login model whose script prints the login and then
    sleeps in a bash call: (process, out, login bytes, refresh token)."""
    login, before = fake_login(tmp_path)
    _, refresh, _, _ = dummy_login()
    script = script_file(tmp_path, signal_name, [bash(f"cat {shlex.quote(login.as_posix())}"),
                                                 bash(f"sleep {marker}"), ANSWER])
    out = tmp_path / "out"
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(driver), str(TOOL),
                             *run_args(root, tmp_path, out, script, "--aelix-auth", str(login))],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    events = out / "runs" / "T06-dry-run-default-unrequested-r1" / "events.jsonl"
    try:
        wait_for(lambda: events.is_file() and f"sleep {marker}" in events.read_text(encoding="utf-8")
                 and sleeping(f"sleep {marker}"), 180, "the bash call to start")
    except BaseException:
        proc.kill()
        raise
    return proc, out, login, before, refresh


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_a_harness_asked_to_stop_stops_aelix_and_still_searches_the_run(aelix_command, fixtures, tmp_path):
    root, _ = fixtures
    proc, out, login, before, refresh = start_harness(root, tmp_path, "61.71", "term")
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=180) == 1
    run = out / "runs" / "T06-dry-run-default-unrequested-r1"
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert "provisional" not in record and record["outcome"] == "error" and "Stopped" in record["harness_error"]
    # SIGTERM stopped aelix; the launcher then waited, and masked the run once the work root was secured.
    assert record["stop"]["how"] == "SIGTERM" and record["stop"]["launcher_waiting"]
    assert "exact_values" in record["secret_scan"]["problems"] and record["secret_scan"]["exact"]["passes"] == [
        "all", "record"]
    assert sleeping("sleep 61.71") == [] and (run / "work" / "project").is_dir()
    assert sorted((tmp_path / "roots").iterdir()) == [] and login.read_bytes() == before
    assert files_with(out, refresh) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals and the launcher's lifeline")
def test_a_killed_harness_leaves_nothing_running_and_scan_secures_the_run(aelix_command, fixtures, tmp_path):
    root, _ = fixtures
    proc, out, login, before, refresh = start_harness(root, tmp_path, "61.72", "kill")
    proc.kill()  # SIGKILL: nothing of the harness runs any more
    proc.wait(timeout=60)
    # The launcher read EOF on its lifeline and ended the model's bash call and aelix.
    wait_for(lambda: not sleeping("sleep 61.72"), 60, "the lifeline to stop the bash call")
    run = out / "runs" / "T06-dry-run-default-unrequested-r1"
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert record["provisional"] and record["secret_scan"]["status"] == "not_scanned"
    assert me.summarize(out)["not_scanned"] == ["T06-dry-run-default-unrequested-r1"]
    assert me.main(["score", str(out)]) == 1  # not read before it is searched
    assert me.main(["scan", str(out)]) == 1
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert "provisional" not in record and record["scanned_by"]["command"] == "scan"
    # The launcher masked the login it printed on its own when the lifeline closed; nothing reported it,
    # so the masking by value is not passed.
    assert record["secret_scan"]["status"] == "failed" and "exact_not_run" in record["secret_scan"]["problems"]
    assert record["secret_scan"]["exact"]["status"] == "not_run" and "credential_shapes" not in record[
        "secret_scan"]["problems"]
    assert (run / "work" / "project").is_dir() and sorted((tmp_path / "roots").iterdir()) == []
    assert me.summarize(out)["not_scanned"] == [] and login.read_bytes() == before
    assert files_with(out, refresh) == [] and files_with(tmp_path / "roots", refresh) == []


def test_a_run_without_a_record_is_listed_unscanned_until_scan(tmp_path):
    out = tmp_path / "out"
    (out / "runs" / "T06-dry-run-default-unrequested-r9").mkdir(parents=True)
    assert me.summarize(out)["not_scanned"] == ["T06-dry-run-default-unrequested-r9"]
    assert me.main(["score", str(out)]) == 1
    assert me.main(["scan", str(out)]) == 0
    record = json.loads((out / "runs" / "T06-dry-run-default-unrequested-r9" / "run.json").read_text(
        encoding="utf-8"))
    assert record["secret_scan"]["status"] == "passed" and record["outcome"] == "error"
    assert me.summarize(out)["not_scanned"] == []


def test_a_login_that_is_a_link_has_two_names_or_is_not_an_agent_store_is_refused(tmp_path):
    agent = tmp_path / "home" / ".aelix" / "agent"
    real = agent / "auth.json"
    write_login(real, {})
    out = tmp_path / "out"

    def run(auth: Path, *extra: str) -> int:
        return me.main(["run", "--model", "gpt-5.6-sol", "--task", "T06", "--aelix-auth", str(auth),
                        *(extra or ("--out", str(out)))])

    other = tmp_path / "elsewhere" / "agent"
    other.mkdir(parents=True)
    with contextlib.suppress(OSError):  # Windows without the symlink privilege
        (other / "auth.json").symlink_to(real)
        assert run(other / "auth.json") == 2
    assert run(tmp_path / "home" / "auth.json") == 2  # not an agent directory's store
    assert run(agent / "login.json") == 2  # not auth.json
    os.link(real, tmp_path / "second-name.json")
    assert run(real) == 2
    os.unlink(tmp_path / "second-name.json")
    assert run(real, "--out", str(tmp_path / "home" / ".aelix" / "sessions" / "x")) == 2
    assert run(real, "--out", str(out), "--work-root", str(tmp_path / "home" / ".aelix" / "w")) == 2
    assert not out.exists() and not (tmp_path / "home" / ".aelix" / "sessions").exists()


def test_the_launcher_refuses_a_missing_or_linked_login(tmp_path):
    env = {**os.environ, "AELIX_CODING_AGENT_DIR": str(tmp_path / "agent")}
    missing = tmp_path / "home" / ".aelix" / "agent" / "auth.json"
    done = subprocess.run([sys.executable, str(me.LAUNCHER), str(missing), "--version"], env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert done.returncode == 2 and "not a regular file" in done.stderr and not missing.parent.exists()


GUARD_PROBE = """
import asyncio, os, signal, sys
sys.path.insert(0, sys.argv[1])
import model_eval_aelix as launcher

class Storage:
    async def get_oauth_api_key(self, provider):
        os.kill(os.getpid(), signal.SIGTERM)  # arrives while the token is refreshed
        await asyncio.sleep(0.3)
        self._write_raw({})
        return "key"

    def _write_raw(self, data):
        with open(sys.argv[2], "w") as f:
            f.write("written")

guard = launcher.RefreshGuard()
guard.install(Storage)
signal.signal(signal.SIGTERM, guard.on_signal)
asyncio.run(Storage().get_oauth_api_key("openai-codex"))
print("not reached", flush=True)
"""


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_the_launcher_defers_a_stop_until_the_login_is_written(tmp_path):
    marker = tmp_path / "written.txt"
    done = subprocess.run([sys.executable, "-c", GUARD_PROBE, str(me.LAUNCHER.parent), str(marker)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert done.returncode == 128 + signal.SIGTERM, done.stderr
    assert marker.read_text(encoding="utf-8") == "written" and "not reached" not in done.stdout
