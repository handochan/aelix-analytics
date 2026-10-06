"""The evaluation fixtures of tools/eval_fixtures.py (Issue #41).

The generator runs as a command, as a person runs it: it sets POLARS_MAX_THREADS=1 before Polars loads, which
this test process cannot promise for itself (another test may have imported Polars on every core). The
fixtures it writes are then read here: the same seed gives the same bytes but for the declared volatile
fields, another seed another fixture; S has the sizes of docs/BETA-QUALITY-GATES.md §4; every case the
manifest names reads as it says through the kit's own reader (relations.read, core.status); the run
records and journal lines the generator writes itself are those the /1 writer writes; and the expected
judgments of Q10 and Q13 are true to their data, recomputed here by other means than the generator's.

M is generated only with AELIX_ANALYTICS_TEST_SCALE_M=1 (two generations of about 220 MB each).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from conftest import OLD_ENV, record_observation

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "eval_fixtures.py"
SCHEMAS = ROOT / "tools" / "eval_fixtures_data" / "schemas"
BETA = ROOT / "docs" / "BETA-QUALITY-GATES.md"
REQUIRED_CASES = {"correction", "withdrawal", "unpreserved_data", "same_chart_id", "reference_cycle", "long_korean"}
EVENT_KEYS = ("schema", "seq", "id", "ts", "kind", "actor", "text", "refs")


def load_tool():
    spec = importlib.util.spec_from_file_location("eval_fixtures_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


tool = load_tool()


def tool_env(base: Path) -> dict:
    """The kit's variables isolated as the rest of the suite has them: a trust list inside `base`, no
    variable of the old names, no Polars thread count inherited."""
    env = {k: v for k, v in os.environ.items()
           if k not in (*OLD_ENV, *tool.ISOLATED_KEYS, "POLARS_MAX_THREADS", "VIRTUAL_ENV")}
    env.update({"AELIX_ANALYTICS_TRUST_FILE": str(base / "trust" / "lab-projects.json"),
                "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def run_tool(*args, env: dict, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, env=env,
                          timeout=timeout, encoding="utf-8", errors="replace")


GENERATED = {
    "s0a": ("scale", "--scale", "S", "--seed", "0"),
    "s0b": ("scale", "--scale", "S", "--seed", "0"),
    "s1": ("scale", "--scale", "S", "--seed", "1"),
    "q10a": ("q10", "--seed", "0"),
    "q10b": ("q10", "--seed", "0"),
    "q13a": ("q13", "--seed", "0"),
    "q13b": ("q13", "--seed", "0"),
}


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("eval-fixtures")
    env = tool_env(base)
    out, seconds = {}, {}
    for name, args in GENERATED.items():
        target = base / name
        started = time.perf_counter()
        done = run_tool(*args, "--out", target, env=env)
        seconds[name] = round(time.perf_counter() - started, 3)
        assert done.returncode == 0, f"{name}: {done.stdout}\n{done.stderr}"
        out[name] = target
    record_observation("eval-fixtures-generation", {
        "command_seconds": seconds,
        "generator_seconds": {name: json.loads((path / "environment.json").read_text(encoding="utf-8"))
                              ["generation"]["seconds"] for name, path in out.items()}})
    return out


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def schema_errors(value, name: str) -> list[str]:
    import jsonschema

    validator = jsonschema.Draft202012Validator(read_json(SCHEMAS / name))
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(value)]


def files_of(root: Path) -> dict[str, Path]:
    return {rel: path for rel, path in tool._tree_files(root)}


def volatile(rel: str) -> bool:
    return any(re.fullmatch(pattern.replace("*", "[^/]+"), rel) for pattern in [r["files"] for r in tool.VOLATILE])


# --- determinism ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("pair", [("s0a", "s0b"), ("q10a", "q10b"), ("q13a", "q13b")])
def test_the_same_seed_gives_the_same_bytes(fixtures, pair):
    first, second = (fixtures[name] for name in pair)
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    digest = read_json(first / "manifest.json")["digest"]
    assert tool.tree_digest(first) == tool.tree_digest(second) == digest
    a, b = files_of(first), files_of(second)
    assert sorted(a) == sorted(b)
    differ = sorted(rel for rel in a if a[rel].read_bytes() != b[rel].read_bytes())
    # Only the generation's own record and the fields VOLATILE declares differ; masked, they are equal.
    assert "environment.json" in differ
    for rel in differ:
        if rel == "environment.json":
            continue
        assert volatile(rel), rel
        assert tool.masked_bytes(rel, a[rel].read_bytes(), str((first / "project").resolve())) == \
            tool.masked_bytes(rel, b[rel].read_bytes(), str((second / "project").resolve())), rel


def test_another_seed_gives_another_fixture(fixtures):
    zero, one = read_json(fixtures["s0a"] / "manifest.json"), read_json(fixtures["s1"] / "manifest.json")
    assert zero["digest"] != one["digest"]
    assert [zero["counts"][k] for k in ("runs", "events", "datasets")] == \
        [one["counts"][k] for k in ("runs", "events", "datasets")]
    journal = (fixtures["s0a"] / "project" / "journal.jsonl").read_bytes()
    assert journal != (fixtures["s1"] / "project" / "journal.jsonl").read_bytes()


# --- sizes ----------------------------------------------------------------------------------------------

def test_the_scale_table_is_the_quality_gates_one():
    text = BETA.read_text(encoding="utf-8")
    for scale in ("S", "M"):
        match = re.search(rf"^\| {scale} \| ([\d,]+) runs, ([\d,]+) events, ([\d,]+) datasets", text, re.MULTILINE)
        assert match, scale
        runs, events, datasets = (int(g.replace(",", "")) for g in match.groups())
        spec = tool.SCALES[scale]
        assert (spec.runs, spec.events, spec.datasets) == (runs, events, datasets)
        assert len(spec.bulk_rows) == spec.datasets - 1  # sales_kr is the other one
    assert "1 GB" in text and tool.M_DATA_LIMIT == 1 << 30


def assert_sizes(out: Path, scale: str) -> dict:
    spec = tool.SCALES[scale]
    project = out / "project"
    runs = [d for d in (project / "runs").iterdir() if (d / "run.json").is_file()]
    lines = (project / "journal.jsonl").read_bytes().split(b"\n")
    assert lines[-1] == b"" and all(lines[:-1])
    raw = sorted((project / "data" / "raw").glob("*.parquet"))
    metas = sorted((project / "data" / "raw").glob("*.meta.json"))
    assert (len(runs), len(lines) - 1, len(raw), len(metas)) == (spec.runs, spec.events, spec.datasets, spec.datasets)
    manifest = read_json(out / "manifest.json")
    assert manifest["counts"]["runs"] == spec.runs and manifest["counts"]["events"] == spec.events
    assert manifest["counts"]["datasets"] == spec.datasets
    assert manifest["counts"]["runs_executed"] == 3 and len(manifest["runs_executed"]) == 3
    assert manifest["sizes"]["raw_parquet_bytes"] == sum(p.stat().st_size for p in raw)
    assert sum(p.stat().st_size for p in (project / "data").rglob("*") if p.is_file()) <= tool.M_DATA_LIMIT
    return manifest


def test_s_has_the_sizes_of_its_scale(fixtures):
    manifest = assert_sizes(fixtures["s0a"], "S")
    assert manifest["scale"] == "S" and manifest["format"] == "v1"
    assert schema_errors(manifest, "manifest.schema.json") == []


# --- the cases ------------------------------------------------------------------------------------------

def read_model(root: Path):
    from aelix_analytics import core, relations

    project = core.Project(root)
    return project, relations.read(root, alive=project.platform.processes.alive,
                                   sha=lambda p: tool.sha256_file(Path(p)) if Path(p).is_file() else None)


def test_every_case_is_there_and_reads_as_the_manifest_says(fixtures):
    from aelix_analytics import core

    out = fixtures["s0a"]
    manifest = read_json(out / "manifest.json")
    cases = {case["id"]: case for case in manifest["cases"]}
    assert REQUIRED_CASES <= {name for name, case in cases.items() if case["required"]}
    assert tool.check(out) == []
    project, model = read_model(out / "project")
    assert (model.journal.damaged, model.journal.recovered, model.damaged_files, model.unsupported) == ([], [], [], [])
    status = core.status(project)
    assert status["counts"]["events"] == 300 and status["counts"]["runs"] == 30
    assert core.status_text(status)
    # A few of the judgments, from the reader directly rather than through the tool's check.
    correction = cases["correction"]["records"]
    assert model.record_states()[correction["original"]]["state"] == "current"
    resolved = core.resolved_targets(core.read_events(project))
    assert resolved[correction["original"]]["id"] == correction["correction"]
    assert int(correction["correction"][1:]) - int(correction["original"][1:]) > 12  # far outside the recent list
    withdrawal = cases["withdrawal"]["records"]
    [event] = [e for e in model.journal.events if e.id == withdrawal["withdrawal"]]
    assert event.kind == "resolve" and event.value["refs"] == {"resolves": withdrawal["decision"]}
    first, second = cases["reference_cycle"]["records"]["cycle_runs"]
    assert model.run(first)["started_at"] == model.run(second)["started_at"]
    assert model.upstream(first)["cycle"] and model.upstream(second)["cycle"]
    long_note = cases["long_korean"]["records"]
    [event] = [e for e in model.journal.events if e.id == long_note["note"]]
    assert long_note["original_chars"] > core.MAX_EVENT_TEXT == len(event.value["text"])
    assert re.search(r"[가-힣]{2}", event.value["text"])
    unpreserved = cases["unpreserved_data"]["records"]
    assert not (out / "project" / unpreserved["deleted_path"]).exists()
    assert model.observation(unpreserved["overwritten_run_1"], "output", unpreserved["overwritten_path"]) == \
        {"state": "content_changed"}
    same = cases["same_chart_id"]["records"]
    keys = {f"{model.run(r)['script']['path']}::{c['id']}" for r in model._runs for c in model.run(r)["charts"]}
    assert {"scripts/case_trend.py::trend", "scripts/k02_sensor_profile.py::trend"} <= keys
    assert len(model.visualization_versions("scripts/case_trend.py::trend")) >= 2
    [event] = [e for e in model.journal.events if e.id == same["bare_note"]]
    assert event.value["refs"] == {"charts": ["scripts/case_region_total.py::region-total"]}


def test_check_finds_a_changed_fixture(fixtures, tmp_path):
    copy = tmp_path / "q10"
    shutil.copytree(fixtures["q10a"], copy)
    assert tool.check(copy) == []
    with open(copy / "project" / "journal.jsonl", "ab") as fh:
        fh.write(b'{"schema": "lab.event/1", "seq": 99, "id": "e99", "ts": "x", "kind": "note", "actor": "t", '
                 b'"text": "added", "refs": {}}\n')
    problems = tool.check(copy)
    assert any("digest" in p for p in problems) and any("events" in p for p in problems)


# --- the records are the /1 writer's ----------------------------------------------------------------------

def shapes(record: dict) -> set[tuple]:
    """(where, keys in order) of a run record and its nested objects."""
    out = {("$", tuple(record))}
    for name in ("script", "error", "env"):
        if isinstance(record.get(name), dict):
            out.add((name, tuple(record[name])))
    for name in ("inputs", "outputs", "charts"):
        for entry in record.get(name) or []:
            out.add((f"{name}[]", tuple(entry)))
            if name == "charts":
                out.add(("charts[].files", tuple(entry["files"])))
    return out


def test_written_records_are_the_v1_writer_s(fixtures):
    executed: set[tuple] = set()
    for name in ("s0a", "q13a"):
        manifest = read_json(fixtures[name] / "manifest.json")
        for run_id in manifest["runs_executed"]:
            executed |= shapes(read_json(fixtures[name] / "project" / "runs" / run_id / "run.json"))
    out = fixtures["s0a"]
    manifest = read_json(out / "manifest.json")
    written = [d for d in (out / "project" / "runs").iterdir() if d.name not in manifest["runs_executed"]]
    assert len(written) == 27
    for directory in written:
        data = (directory / "run.json").read_bytes()
        record = json.loads(data)
        # core.write_json: indent 2, not ASCII-escaped, LF, a final newline.
        assert data == (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), directory.name
        assert shapes(record) <= executed, (directory.name, shapes(record) - executed)
        assert (directory / "code" / record["script"]["path"]).is_file()
    previous = None
    for number, line in enumerate((out / "project" / "journal.jsonl").read_bytes().split(b"\n")[:-1], 1):
        assert not line.endswith(b"\r")
        event = json.loads(line)
        assert tuple(event) == EVENT_KEYS and event["seq"] == number and event["id"] == f"e{number}"
        at = datetime.fromisoformat(event["ts"])
        assert previous is None or at > previous
        previous = at


# --- Q10 ------------------------------------------------------------------------------------------------

def test_q10_examples_have_their_verdicts_and_their_numbers_are_the_data_s(fixtures):
    import duckdb

    out = fixtures["q10a"]
    expected = read_json(out / "expected.json")
    assert schema_errors(expected, "expected.schema.json") == []
    assert tool.check(out) == []
    examples = expected["examples"]
    assert sorted((e["check"], e["verdict"]) for e in examples) == sorted(
        (check, verdict) for check in ("aggregation_key", "unit", "denominator", "period") for verdict in ("fail", "pass"))
    _, model = read_model(out / "project")
    texts = {e.id: e.value["text"] for e in model.journal.events}
    assert {r["run_id"]: r["execution_status"] for r in model.runs()} == {e["run"]: "ok" for e in examples}
    raw = out / "project" / "data" / "raw"
    con = duckdb.connect()
    for name in ("orders", "order_items", "sessions", "store_sales"):
        path = str(raw / f"{name}.parquet").replace("'", "''")
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')")

    def one(sql: str):
        return con.execute(sql).fetchone()[0]

    by_check = {(e["check"], e["verdict"]): e for e in examples}
    for example in examples:
        assert texts[example["claim_event"]] == example["claim"]
    agg = by_check[("aggregation_key", "fail")]["evidence"]
    assert agg["joined_rows"] == one("SELECT count(*) FROM order_items") > agg["orders_rows"] == one(
        "SELECT count(*) FROM orders")
    assert agg["sound_value"] == one("SELECT sum(금액_원) FROM orders WHERE 지역 = '서울'")
    assert agg["flawed_value"] == one("SELECT sum(o.금액_원) FROM orders o, order_items i "
                                      "WHERE o.order_id = i.order_id AND o.지역 = '서울'") > agg["sound_value"]
    assert f"{agg['flawed_value']:,}원" in by_check[("aggregation_key", "fail")]["claim"]
    assert f"{agg['sound_value']:,}원" in by_check[("aggregation_key", "pass")]["claim"]
    unit = by_check[("unit", "fail")]["evidence"]
    online = one("SELECT sum(금액_원) FROM orders WHERE 주문일 < DATE '2026-09-01'")
    store = one("SELECT sum(매출_천원) FROM store_sales WHERE 일자 < DATE '2026-09-01'")
    assert (unit["flawed_value"], unit["sound_value"]) == (online + store, online + 1000 * store)
    rate = by_check[("denominator", "fail")]["evidence"]
    sessions = one("SELECT count(*) FROM sessions WHERE 세션일 < DATE '2026-09-01'")
    converted = one("SELECT count(*) FROM sessions WHERE 세션일 < DATE '2026-09-01' AND 주문여부")
    customers = one("SELECT count(DISTINCT customer_id) FROM sessions WHERE 세션일 < DATE '2026-09-01'")
    assert rate["sound_value"] == round(converted / sessions, 6) < rate["flawed_value"] == round(
        converted / customers, 6)  # every converted session is one order
    period = by_check[("period", "fail")]["evidence"]
    assert period["last_day"] == "2026-09-20" == str(one("SELECT max(주문일) FROM orders"))
    assert period["flawed_value"] < 0 < period["sound_value"]
    assert "감소" in by_check[("period", "fail")]["claim"] and "증가" in by_check[("period", "pass")]["claim"]


# --- Q13 ------------------------------------------------------------------------------------------------

def test_q13_reproduction_has_its_judgments_and_they_are_the_data_s(fixtures):
    import polars as pl

    out = fixtures["q13a"]
    expected = read_json(out / "expected.json")
    assert schema_errors(expected, "expected.schema.json") == []
    assert tool.check(out) == []
    project = out / "project"
    _, model = read_model(project)
    statuses = sorted(r["execution_status"] for r in model.runs())
    assert statuses == ["error", "error", "ok", "ok", "ok", "ok"]
    assert expected["records"]["run_counts"] == {"ok": 4, "error": 2}
    assert expected["records"]["context_versions"] == len(list((project / ".lab" / "context").glob("*.md"))) == 3
    facts = expected["facts"]
    # From the scripts' own outputs, not the generator's queries.
    table = pl.read_parquet(project / "data" / "derived" / "score_table.parquet")
    scores = pl.read_parquet(project / "data" / "derived" / "sample_scores.parquet")
    assert facts["score_table_rows"] == table.height == 320
    assert facts["exceed_total"] == table["exceed_count"].sum()
    assert facts["contexts_sensors_with_exceed"] == table.filter(pl.col("exceed_count") > 0).height
    assert facts["max_abs_z"] == round(scores["abs_z"].max(), 4) and 4.3 < facts["max_abs_z"] < 4.4
    shown = scores.filter((pl.col("context_id") == facts["displayed_context"]) & (pl.col("abs_z") > 3))
    assert dict(shown.group_by("sensor").len().iter_rows()) == facts["displayed_out_of_band"] == {
        "flow": 1, "pressure": 1, "rf_power": 1, "temperature": 1}
    means = pl.read_parquet(project / "data" / "derived" / "trace_means.parquet").filter(pl.col("z").abs() > 3)
    assert means.select("sensor", "context_id").to_dicts() == facts["trace_mean_outliers"]
    verdicts = {claim["verdict"] for claim in expected["claims"]}
    assert verdicts == {"supported", "false", "unverified"}
    texts = {e.id: e.value["text"] for e in model.journal.events}
    for claim in expected["claims"]:
        assert texts[claim["event"]] == claim["text"]
    partial = expected["records"]["failed_run_partial_outputs"]
    failed = model.run(partial["run"])
    assert failed["status"] == "error" and len(failed["outputs"]) == 2 and len(failed["charts"]) == 2
    assert sorted(o["path"] for o in failed["outputs"]) == partial["outputs"]
    assert all(found == [partial["rewritten_by"]] for found in partial["producers_of_current_bytes"].values())
    for attached in expected["records"]["attached_data"]:
        script, _, chart_id = attached["chart"].partition("::")
        [chart] = [c for c in model.run(attached["run"])["charts"] if c["id"] == chart_id]
        assert (chart["data_rows"], chart["data_columns"]) == (attached["data_rows"], attached["data_columns"])
    assert (out / "input" / "traces.csv").read_bytes().count(b"\n") == 19_201
    assert len(expected["metrics"]) == 4 and [s["step"] for s in expected["session"]] == [1, 2, 3]


# --- the command and the environment record --------------------------------------------------------------

def test_usage_errors_write_nothing(tmp_path):
    env = tool_env(tmp_path)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("mine", encoding="utf-8")
    done = run_tool("q10", "--out", busy, env=env)
    assert done.returncode == 2 and "not empty" in done.stderr
    assert sorted(p.name for p in busy.iterdir()) == ["keep.txt"]
    for fmt, says in (("v2", "#13"), ("v9", "unknown format")):
        target = tmp_path / f"format-{fmt}"
        done = run_tool("scale", "--scale", "S", "--format", fmt, "--out", target, env=env)
        assert done.returncode == 2 and says in done.stderr and not target.exists()
    assert run_tool("scale", "--scale", "L", "--out", tmp_path / "l", env=env).returncode == 2


def test_the_python_api_refuses_a_process_whose_polars_has_threads():
    if tool.polars_threads() == 1:
        pytest.skip("Polars runs on one thread in this process")
    with pytest.raises(tool.UsageError, match="POLARS_MAX_THREADS"):
        tool.require_deterministic_process()


def test_the_environment_record(fixtures, tmp_path):
    env = tool_env(tmp_path)
    generated = read_json(fixtures["s0a"] / "environment.json")
    assert schema_errors(generated, "environment.schema.json") == []
    assert generated["cache_state"]["kit_caches"] == "cold" and generated["threads"]["polars"] == 1
    assert generated["browser"]["status"] == "not_measured"
    assert {"total", "kit_phase", "written_phase", "self_check"} <= set(generated["generation"]["seconds"])
    target = tmp_path / "env.json"
    done = run_tool("environment", "--out", target, "--cache-state", "warm", "--browser", "chrome=141.0", env=env)
    assert done.returncode == 0, done.stderr
    record = read_json(target)
    assert schema_errors(record, "environment.schema.json") == []
    assert record["browser"] == {"name": "chrome", "version": "141.0"}
    assert record["cache_state"]["measurement"] == "warm" and "generation" not in record
    assert isinstance(record["sqlite"]["fts5"], bool)


# --- M ----------------------------------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("AELIX_ANALYTICS_TEST_SCALE_M") != "1",
                    reason="M writes about 220 MB twice; set AELIX_ANALYTICS_TEST_SCALE_M=1")
def test_m_has_its_sizes_and_cases_and_is_the_seed_s(tmp_path):
    env = tool_env(tmp_path)
    seconds = {}
    for name in ("a", "b"):
        started = time.perf_counter()
        done = run_tool("scale", "--scale", "M", "--seed", "0", "--out", tmp_path / name, env=env, timeout=1800)
        seconds[name] = round(time.perf_counter() - started, 3)
        assert done.returncode == 0, done.stderr
    manifest = assert_sizes(tmp_path / "a", "M")
    assert REQUIRED_CASES <= {case["id"] for case in manifest["cases"] if case["required"]}
    assert tool.check(tmp_path / "a") == []
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (tmp_path / "b" / "manifest.json").read_bytes()
    assert tool.tree_digest(tmp_path / "b") == manifest["digest"]
    record_observation("eval-fixtures-m", {
        "command_seconds": seconds, "sizes": manifest["sizes"], "counts": manifest["counts"],
        "generator_seconds": read_json(tmp_path / "a" / "environment.json")["generation"]["seconds"]})
