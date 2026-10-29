"""The evaluation fixtures of tools/eval_fixtures.py (Issue #41).

The generator runs as a command, as a person runs it: it sets POLARS_MAX_THREADS=1 before Polars loads, which
this test process cannot promise for itself (another test may have imported Polars on every core). The
fixtures it writes are then read here, in both formats: the same seed gives the same bytes but for the
declared volatile fields (at the same --out, but for the executed runs' measured time), another seed another
fixture; S has the sizes of docs/BETA-QUALITY-GATES.md §4; every case the manifest names reads as it says
through the kit's own reader (relations.read, core.status); the run records and journal lines the generator
writes itself are those the format's writer writes, and their code reads, saves and visualizes what the
records say; and the expected judgments of Q10 and Q13 are true to their data, recomputed here by other means
than the generator's.

M is generated only with AELIX_ANALYTICS_TEST_SCALE_M=1 (two generations of about 440 MB each, per format:
the project's 217 MB of imported data and the sources under input/).

The v1 fixtures are written by a kit before the new record format (design OQ7): the runtime root of
tool.V1_KIT_COMMIT, made from the checkout's git history (git archive) and given as --kit-root. This
checkout's kit writes analytics.event/2 only: it writes the v2 fixtures, and refuses v1 without --kit-root.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import types
from datetime import datetime
from pathlib import Path

import pytest
from conftest import OLD_ENV, git_checkout, record_observation

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "eval_fixtures.py"
SCHEMAS = ROOT / "tools" / "eval_fixtures_data" / "schemas"
BETA = ROOT / "docs" / "BETA-QUALITY-GATES.md"
REQUIRED_CASES = {"correction", "withdrawal", "unpreserved_data", "same_chart_id", "reference_cycle", "long_korean"}
# The keys of a journal line, in order, by format (v2: and `details` after them where a record has it).
EVENT_KEYS = {"v1": ("schema", "seq", "id", "ts", "kind", "actor", "text", "refs"),
              "v2": ("schema", "seq", "id", "recorded_at", "kind", "actor", "actor_basis", "means", "text", "refs")}
TIME_KEY = {"v1": "ts", "v2": "recorded_at"}
# Where a run record holds its Visualizations, and the field naming each, by format.
VISUALIZATIONS = {"v1": ("charts", "id"), "v2": ("visualizations", "visualization_id")}


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
# Generated at one --out and moved away after: the two S fixtures are compared byte for byte unmasked, and
# every test reads a fixture that is no longer where it was generated.
SAME_OUT = {"s0a": "s0", "s0b": "s0"}


@pytest.fixture(scope="session")
def v1_kit(tmp_path_factory) -> Path:
    """The runtime root of tool.V1_KIT_COMMIT's kit, the v1 writer (design OQ7): its src/ out of the
    checkout's git history. Outside a git checkout this skips, and on CI it fails (conftest.git_checkout)."""
    data = git_checkout("archive", "--format=tar", tool.V1_KIT_COMMIT, "src")
    base = tmp_path_factory.mktemp("v1-kit")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(base, filter="data")
    root = base / "src"
    assert (root / "aelix_analytics" / "core.py").is_file()
    return root


def generate_all(base: Path, fmt: str, extra: tuple) -> dict[str, Path]:
    env = tool_env(base)
    out, seconds = {}, {}
    for name, args in GENERATED.items():
        target = base / SAME_OUT.get(name, name)
        started = time.perf_counter()
        done = run_tool(*args, "--format", fmt, *extra, "--out", target, env=env)
        seconds[name] = round(time.perf_counter() - started, 3)
        assert done.returncode == 0, f"{fmt} {name}: {done.stdout}\n{done.stderr}"
        if name in SAME_OUT:
            target = target.rename(base / name)
        out[name] = target
    record_observation(f"eval-fixtures-generation-{fmt}", {
        "command_seconds": seconds,
        "generator_seconds": {name: json.loads((path / "environment.json").read_text(encoding="utf-8"))
                              ["generation"]["seconds"] for name, path in out.items()}})
    return out


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory, v1_kit) -> dict[str, Path]:
    """The v1 fixtures, written by the kit of tool.V1_KIT_COMMIT."""
    return generate_all(tmp_path_factory.mktemp("eval-fixtures"), "v1", ("--kit-root", v1_kit))


@pytest.fixture(scope="module")
def fixtures_v2(tmp_path_factory) -> dict[str, Path]:
    """The v2 fixtures, written by this checkout's kit."""
    return generate_all(tmp_path_factory.mktemp("eval-fixtures-v2"), "v2", ())


@pytest.fixture(params=tool.FORMATS)
def generated(request) -> tuple[str, dict[str, Path]]:
    """(format, its fixtures), for each format."""
    return request.param, request.getfixturevalue("fixtures" if request.param == "v1" else "fixtures_v2")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def schema_errors(value, name: str) -> list[str]:
    import jsonschema

    validator = jsonschema.Draft202012Validator(read_json(SCHEMAS / name),
                                                format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(value)]


def files_of(root: Path) -> dict[str, Path]:
    return {rel: path for rel, path in tool._tree_files(root)}


def digested_files(root: Path, fmt: str) -> dict[str, Path]:
    """The files the digest covers: not manifest.json, environment.json or the kit's derived files, which a
    read of the project in another test may rebuild."""
    return {rel: path for rel, path in files_of(root).items() if tool._digested(rel, fmt)}


def volatile(rel: str, fmt: str) -> bool:
    return any(re.fullmatch(rule["files"].replace("*", "[^/]+"), rel) for rule in tool.VOLATILE[fmt])


# --- determinism ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("pair", [("s0a", "s0b"), ("q10a", "q10b"), ("q13a", "q13b")])
def test_the_same_seed_gives_the_same_bytes(generated, pair):
    fmt, fixtures = generated
    first, second = (fixtures[name] for name in pair)
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    digest = read_json(first / "manifest.json")["digest"]
    assert tool.tree_digest(first) == tool.tree_digest(second, fmt) == digest
    assert (first / "environment.json").is_file() and (second / "environment.json").is_file()
    a, b = digested_files(first, fmt), digested_files(second, fmt)
    assert sorted(a) == sorted(b)
    differ = sorted(rel for rel in a if a[rel].read_bytes() != b[rel].read_bytes())
    # Only the fields VOLATILE declares differ; masked, they are equal.
    for rel in differ:
        assert volatile(rel, fmt), rel
        assert tool.masked_bytes(rel, a[rel].read_bytes(), fmt) == tool.masked_bytes(rel, b[rel].read_bytes(), fmt), rel
    if pair[0] in SAME_OUT:
        # The same --out: every byte is the same but the executed runs' measured time (which two
        # generations may also happen to measure alike).
        manifest = read_json(first / "manifest.json")
        assert set(differ) <= {f"project/runs/{run}/run.json" for run in manifest["runs_executed"]}
        for rel in differ:
            one, other = read_json(a[rel]), read_json(b[rel])
            del one["duration_s"], other["duration_s"]
            assert one == other, rel


def test_another_seed_gives_another_fixture(generated):
    _, fixtures = generated
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


def test_the_layouts_are_the_kit_s():
    """The stored names the generator reads and clears are the kit's own (writer.LEGACY_LAYOUT for lab.json,
    writer.LAYOUT for analytics.json), and so are the formats' event schemas."""
    from aelix_analytics import relations, writer

    for fmt, kit_layout, event in (("v1", writer.LEGACY_LAYOUT, relations.EVENT_V1),
                                   ("v2", writer.LAYOUT, relations.EVENT_V2)):
        layout = tool.LAYOUTS[fmt]
        assert layout.event == event
        for name in ("marker", "state_dir", "workspace_dir", "imported_dir", "visualizations_dir"):
            assert getattr(layout, name) == getattr(kit_layout, name), (fmt, name)
    assert writer.FAMILIES["event"] == relations.EVENT_V2


def assert_sizes(out: Path, scale: str, fmt: str) -> dict:
    spec = tool.SCALES[scale]
    project = out / "project"
    runs = [d for d in (project / "runs").iterdir() if (d / "run.json").is_file()]
    lines = (project / "journal.jsonl").read_bytes().split(b"\n")
    assert lines[-1] == b"" and all(lines[:-1])
    imported = project / tool.LAYOUTS[fmt].imported_dir
    raw = sorted(imported.glob("*.parquet"))
    metas = sorted(imported.glob("*.meta.json"))
    assert (len(runs), len(lines) - 1, len(raw), len(metas)) == (spec.runs, spec.events, spec.datasets, spec.datasets)
    manifest = read_json(out / "manifest.json")
    assert manifest["counts"]["runs"] == spec.runs and manifest["counts"]["events"] == spec.events
    assert manifest["counts"]["datasets"] == spec.datasets
    assert manifest["counts"]["runs_executed"] == 3 and len(manifest["runs_executed"]) == 3
    assert manifest["sizes"]["raw_parquet_bytes"] == sum(p.stat().st_size for p in raw)
    assert sum(p.stat().st_size for p in (project / "data").rglob("*") if p.is_file()) <= tool.M_DATA_LIMIT
    return manifest


def test_s_has_the_sizes_of_its_scale(generated):
    fmt, fixtures = generated
    manifest = assert_sizes(fixtures["s0a"], "S", fmt)
    assert manifest["scale"] == "S" and manifest["format"] == fmt
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
    # The /1 writer of a kit before 0.8.0 kept a text's first 4,000 characters (its MAX_EVENT_TEXT).
    assert long_note["original_chars"] > 4000 == len(event.value["text"])
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


def test_every_v2_case_reads_as_the_common_writer_wrote_it(fixtures_v2):
    """The v2 cases are the v1 cases' records written by the common writer (design §8): the correction
    supersedes, the withdrawal is a record_update, the long record is kept whole, a version-less key is bound
    as it is written and a bare name is not."""
    from aelix_analytics import core, relations

    out = fixtures_v2["s0a"]
    manifest = read_json(out / "manifest.json")
    cases = {case["id"]: case for case in manifest["cases"]}
    assert REQUIRED_CASES <= {name for name, case in cases.items() if case["required"]}
    assert all("v2" in case and "v1" not in case for case in manifest["cases"])
    assert tool.check(out) == []
    project, model = read_model(out / "project")
    assert (model.journal.damaged, model.journal.recovered, model.damaged_files, model.unsupported) == ([], [], [], [])
    status = core.status(project)
    assert status["counts"]["events"] == 300 and status["counts"]["runs"] == 30
    assert core.status_text(status)
    events = {e.id: e.value for e in model.journal.events}
    states = model.record_states()
    correction = cases["correction"]["records"]
    assert states[correction["original"]]["state"] == "superseded"
    assert states[correction["correction"]]["state"] == "current"
    assert events[correction["correction"]]["refs"] == [{"type": "event", "event_id": correction["original"],
                                                         "relation": "supersedes", "basis": "declared",
                                                         "version_binding": "author"}]
    assert int(correction["correction"][1:]) - int(correction["original"][1:]) > 12  # far outside the recent list
    withdrawal = cases["withdrawal"]["records"]
    event = events[withdrawal["withdrawal"]]
    assert (event["kind"], event["details"]) == ("record_update", {"expected_state": "current", "new_state": "withdrawn"})
    assert [(r["event_id"], r["relation"]) for r in event["refs"]] == [(withdrawal["decision"], "updates")]
    assert states[withdrawal["decision"]]["state"] == "withdrawn"
    first, second = cases["reference_cycle"]["records"]["cycle_runs"]
    assert model.run(first)["started_at"] == model.run(second)["started_at"]
    assert model.upstream(first)["cycle"] and model.upstream(second)["cycle"]
    long_note = cases["long_korean"]["records"]
    # The common writer keeps a text whole up to its limit (32 KiB of UTF-8); 6,000 Korean characters are 18,000 bytes.
    assert len(events[long_note["note"]]["text"]) == long_note["original_chars"] == 6000
    assert re.search(r"[가-힣]{2}", events[long_note["note"]]["text"])
    unpreserved = cases["unpreserved_data"]["records"]
    assert not (out / "project" / unpreserved["deleted_path"]).exists()
    assert model.observation(unpreserved["overwritten_run_1"], "output", unpreserved["overwritten_path"]) == \
        {"state": "content_changed"}
    gone = model.run(unpreserved["chart_gone_run_1"])
    assert [v["id"] for v in relations.run_visualizations(gone)] == ["chart-gone"]
    assert not (out / "project" / "runs" / unpreserved["chart_gone_run_1"] / "visualizations").exists() or \
        not any((out / "project" / "runs" / unpreserved["chart_gone_run_1"] / "visualizations").iterdir())
    same = cases["same_chart_id"]["records"]
    keys = {v["key"] for r in model._runs for v in relations.run_visualizations(model.run(r))}
    assert {"scripts/case_trend.py::trend", "scripts/k02_sensor_profile.py::trend"} <= keys
    assert len(model.visualization_versions("scripts/case_trend.py::trend")) >= 2
    [key_ref] = events[same["key_note"]]["refs"]
    assert (key_ref["run_id"], key_ref["visualization_id"], key_ref["version_binding"]) == \
        (same["trend_run_2"], "trend", "inferred")
    assert events[same["bare_note"]]["refs"] == [{"type": "visualization_version", "token": "region-total",
                                                  "relation": "cites", "basis": "declared", "version_binding": "none"}]
    assert "bare_bound_to" not in same
    # The gap kind of the plan is written by its new name.
    kinds = {e.kind for e in model.journal.events}
    assert "workflow_feedback" in kinds and "gap" not in kinds


def test_check_finds_a_changed_fixture(generated, tmp_path):
    fmt, fixtures = generated
    # A copy elsewhere checks clean: the failed runs' tracebacks name the project where it was generated.
    for name in ("s0a", "q13a"):
        copy = tmp_path / name
        shutil.copytree(fixtures[name], copy)
        assert b"Traceback" in b"".join(p.read_bytes() for p in (copy / "project" / "runs").glob("*/stderr.txt"))
        assert tool.check(copy) == [], name
    copy = tmp_path / "q10"
    shutil.copytree(fixtures["q10a"], copy)
    assert tool.check(copy) == []
    added = {"v1": {"schema": "lab.event/1", "seq": 99, "id": "e99", "ts": "x", "kind": "note", "actor": "t",
                    "text": "added", "refs": {}},
             "v2": {"schema": "analytics.event/2", "seq": 99, "id": "e99", "recorded_at": "2026-09-22T10:00:00+09:00",
                    "kind": "note", "actor": "human", "actor_basis": "declared", "means": "sdk", "text": "added",
                    "refs": []}}[fmt]
    with open(copy / "project" / "journal.jsonl", "ab") as fh:
        fh.write(json.dumps(added).encode() + b"\n")
    problems = tool.check(copy)
    assert any("digest" in p for p in problems) and any("events" in p for p in problems)


def test_check_finds_records_of_the_other_format(fixtures_v2, tmp_path):
    copy = tmp_path / "q10"
    shutil.copytree(fixtures_v2["q10a"], copy)
    manifest = read_json(copy / "manifest.json")
    manifest["format"] = "v1"
    for case in manifest["cases"]:
        case["v1"] = case.pop("v2")
    problems = tool.check(copy, manifest=manifest, verify_digest=False)
    assert problems == ["the project is not a v1 fixture's: it has no lab.json"]
    # A journal line of the other format in a project of this one.
    with open(copy / "project" / "journal.jsonl", "ab") as fh:
        fh.write(b'{"schema": "lab.event/1", "seq": 23, "id": "e23", "ts": "x", "kind": "note", "actor": "t", '
                 b'"text": "added", "refs": {}}\n')
    problems = tool.check(copy)
    assert any("analytics.event/2" in p and "lab.event/1" in p for p in problems), problems


def test_check_leaves_the_fixture_cold(generated, tmp_path):
    fmt, fixtures = generated
    layout = tool.LAYOUTS[fmt]
    derived = (f"project/{layout.state_dir}/cache/", f"project/{layout.workspace_dir}/")
    copy = tmp_path / "s0"
    shutil.copytree(fixtures["s0a"], copy)  # another test's read may have left the kit's hash cache in it
    kept = sorted(rel for rel in files_of(copy) if not rel.startswith(derived))
    assert tool.check(copy) == []
    assert sorted(files_of(copy)) == kept
    for name in (f"{layout.state_dir}/cache", layout.workspace_dir):
        assert not any((copy / "project" / name).rglob("*")), name


def test_the_manifest_s_formats_are_checked(fixtures):
    manifest = read_json(fixtures["q10a"] / "manifest.json")
    manifest["clock"]["start"] = "the Monday after"
    problems = tool.check(fixtures["q10a"], manifest=manifest, verify_digest=False)
    assert len(problems) == 1 and "clock/start" in problems[0] and "date-time" in problems[0]


def test_a_case_describes_its_records_in_the_fixture_s_format(fixtures_v2):
    manifest = read_json(fixtures_v2["s0a"] / "manifest.json")
    assert schema_errors(manifest, "manifest.schema.json") == []
    case = manifest["cases"][0]
    case["v1"] = case.pop("v2")
    assert schema_errors(manifest, "manifest.schema.json")
    manifest = read_json(fixtures_v2["s0a"] / "manifest.json")
    manifest["datasets"][0]["path"] = manifest["datasets"][0]["path"].replace("data/imported/", "data/raw/")
    assert schema_errors(manifest, "manifest.schema.json")


# --- the records are the format's writer's ----------------------------------------------------------------

def shapes(record: dict, fmt: str) -> set[tuple]:
    """(where, keys in order) of a run record and its nested objects."""
    field, _ = VISUALIZATIONS[fmt]
    out = {("$", tuple(record))}
    for name in ("script", "error", "env", "analysis_focus"):
        if isinstance(record.get(name), dict):
            out.add((name, tuple(record[name])))
    for name in ("inputs", "outputs", field):
        for entry in record.get(name) or []:
            out.add((f"{name}[]", tuple(entry)))
            if name == field:
                out.add((f"{field}[].files", tuple(entry["files"])))
    return out


def test_written_records_are_the_writer_s(generated):
    fmt, fixtures = generated
    executed: set[tuple] = set()
    for name in ("s0a", "q13a"):
        manifest = read_json(fixtures[name] / "manifest.json")
        for run_id in manifest["runs_executed"]:
            executed |= shapes(read_json(fixtures[name] / "project" / "runs" / run_id / "run.json"), fmt)
    out = fixtures["s0a"]
    manifest = read_json(out / "manifest.json")
    written = [d for d in (out / "project" / "runs").iterdir() if d.name not in manifest["runs_executed"]]
    assert len(written) == 27
    for directory in written:
        data = (directory / "run.json").read_bytes()
        record = json.loads(data)
        # The kit's write_json: indent 2, not ASCII-escaped, LF, a final newline.
        assert data == (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), directory.name
        assert shapes(record, fmt) <= executed, (directory.name, shapes(record, fmt) - executed)
        assert (directory / "code" / record["script"]["path"]).is_file()
    previous = None
    keys = EVENT_KEYS[fmt]
    for number, line in enumerate((out / "project" / "journal.jsonl").read_bytes().split(b"\n")[:-1], 1):
        assert not line.endswith(b"\r")
        event = json.loads(line)
        assert tuple(event)[:len(keys)] == keys and set(event) - set(keys) <= ({"details"} if fmt == "v2" else set())
        assert event["schema"] == tool.LAYOUTS[fmt].event and event["seq"] == number and event["id"] == f"e{number}"
        at = datetime.fromisoformat(event[TIME_KEY[fmt]])
        assert previous is None or at > previous
        previous = at


READS = re.compile(r'analytics\.read\("([^"]+)"\)')
SAVES = re.compile(r'analytics\.save\(\w+, "([^"]+)"\)')
CHARTS = re.compile(r'analytics\.visualize\(fig, "([^"]+)", title="([^"]*)"(, data=table)?\)')
PRINTS = re.compile(r'^print\(f"([^{]*)\{')


def test_written_runs_code_says_what_their_records_say(generated):
    """A run the generator wrote and did not execute has code that reads the record's inputs (a derived table
    by a path literal, which the runner's static scan also records), saves its outputs, visualizes its charts
    on the lines the record names and prints what its stdout holds; a failed one fails on the line its error
    names, for a column its dataset lacks. (Its output bytes are the seed's, not what running the code gives.)"""
    fmt, fixtures = generated
    field, id_field = VISUALIZATIONS[fmt]
    out = fixtures["s0a"]
    project = out / "project"
    manifest = read_json(out / "manifest.json")
    datasets = {d["name"]: d["path"] for d in manifest["datasets"]}
    written = sorted(d for d in (project / "runs").iterdir() if d.name not in manifest["runs_executed"])
    statuses = set()
    for directory in written:
        record = read_json(directory / "run.json")
        statuses.add(record["status"])
        code = (project / record["script"]["snapshot"]).read_text(encoding="utf-8")
        lines = code.splitlines()
        reads = READS.findall(code)
        assert sorted(i["path"] for i in record["inputs"]) == sorted(datasets.get(r, r) for r in reads), directory.name
        for entry in record["inputs"]:
            assert ("scan" in entry["via"]) == (entry["path"] in reads), (directory.name, entry)
        stdout = (project / record["stdout"]).read_text(encoding="utf-8").splitlines()
        if record["status"] == "ok":
            assert [f"data/derived/{s}.parquet" for s in SAVES.findall(code)] == [o["path"] for o in record["outputs"]]
            visualized = [(n, m.group(1), m.group(2), bool(m.group(3)))
                          for n, line in enumerate(lines, 1) if (m := CHARTS.search(line))]
            assert visualized == [(c["line"], c[id_field], c["title"], "data" in c) for c in record[field]], directory.name
            printed = [m.group(1) for line in lines if (m := PRINTS.match(line))]
        else:
            assert record["outputs"] == [] and record[field] == []
            failing = record["error"]["line"]
            [missing] = re.findall(r'unable to find column "([^"]+)"', record["error"]["message"])
            assert "group_by" in lines[failing - 1] and f'"{missing}"' in lines[failing - 1]
            [source] = [r for r in reads if r in datasets]
            meta = project / tool.LAYOUTS[fmt].imported_dir / f"{source}.meta.json"
            columns = [c["name"] for c in read_json(meta)["columns"]]
            assert missing not in columns and json.dumps(columns, ensure_ascii=False) in record["error"]["message"]
            printed = [m.group(1) for line in lines[:failing - 1] if (m := PRINTS.match(line))]
        assert [re.sub(r"\d+$", "", line) for line in stdout] == printed, directory.name
    assert statuses == {"ok", "error"}


def test_written_visualizations_keep_the_hash_of_their_files(fixtures_v2):
    """An analytics.run/2 Visualization keeps the sha256 of the bytes written for it and of its attached data
    (design 2.6), as the runner's capture records them."""
    out = fixtures_v2["s0a"]
    project = out / "project"
    manifest = read_json(out / "manifest.json")
    gone = {c["records"]["chart_gone_run_1"] for c in manifest["cases"] if c["id"] == "unpreserved_data"}
    checked = 0
    for directory in (project / "runs").iterdir():
        if directory.name in manifest["runs_executed"] or directory.name in gone:
            continue
        for visualization in read_json(directory / "run.json")["visualizations"]:
            [figure] = visualization["files"].values()
            assert tool.sha256_file(project / figure) == visualization["sha256"]
            if "data" in visualization:
                assert tool.sha256_file(project / visualization["data"]) == visualization["data_sha256"]
            checked += 1
    assert checked >= 20


# --- Q10 ------------------------------------------------------------------------------------------------

def test_q10_examples_have_their_verdicts_and_their_numbers_are_the_data_s(generated):
    import duckdb

    fmt, fixtures = generated
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
    raw = out / "project" / tool.LAYOUTS[fmt].imported_dir
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


def test_the_formats_hold_the_same_q10_examples(fixtures, fixtures_v2):
    """One plan in two formats: the examples' checks, verdicts, claims and evidence are the same."""
    def plain(out: Path) -> list:
        return [{k: v for k, v in e.items() if k not in ("run", "claim_event")}
                for e in read_json(out / "expected.json")["examples"]]

    assert plain(fixtures["q10a"]) == plain(fixtures_v2["q10a"])


def run_q10_script(script: Path, tables: dict, monkeypatch) -> list[str]:
    """The texts a Q10 script records, run here with the kit's read and record standing in."""
    recorded: list[str] = []
    stand_in = types.ModuleType("aelix_analytics")
    stand_in.read = lambda name: tables[name]
    stand_in.save = lambda frame, name: None
    stand_in.record = lambda kind, text, **_: recorded.append(text)
    with monkeypatch.context() as patched:
        patched.setitem(sys.modules, "aelix_analytics", stand_in)
        exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), {"__name__": "__main__"})
    return recorded


def test_q10_holds_its_design_for_every_seed(tmp_path, monkeypatch, capsys):
    """For seeds 0-19: the partial month is a decrease and the same days an increase, and every script's
    claim is the text the generator expects from the exact numbers (no rounding before the format)."""
    import polars as pl

    numbers = None
    for seed in range(20):
        directory = tmp_path / str(seed)
        paths = tool.write_q10_sources(directory, seed)
        numbers = tool.q10_numbers(tool.q10_source_tables(directory))
        assert numbers["period"]["flawed_value"] < 0 < numbers["period"]["sound_value"], seed
        expected = tool.q10_claims(numbers)
        tables = {name: pl.read_csv(path, try_parse_dates=True) for name, path in paths.items()}
        for example_id, _, _, script, *_ in tool.Q10_EXAMPLES:
            claims = run_q10_script(tool.SCRIPTS / "q10" / script, tables, monkeypatch)
            assert claims == [expected[example_id]], (seed, script)
    capsys.readouterr()  # the scripts' prints
    # A ratio on a rounding boundary (seed 18 of the first generator): formatted as the script does.
    numbers["period"]["sound_value"] = 0.05049964761428982
    assert f"{round(0.05049964761428982, 6):.1%}" == "5.1%"
    assert tool.q10_claims(numbers)["period.sound"] == "9월 1–20일 주문 금액은 8월 1–20일보다 5.0% 증가했다."
    # Data that would not hold the design is drawn again: even with no September growth it holds.
    monkeypatch.setattr(tool, "Q10_SEPTEMBER_GROWTH", 1.0)
    for seed in range(5):
        directory = tmp_path / f"flat-{seed}"
        tool.write_q10_sources(directory, seed)
        numbers = tool.q10_numbers(tool.q10_source_tables(directory))
        assert numbers["period"]["flawed_value"] < 0 < numbers["period"]["sound_value"], seed


# --- Q13 ------------------------------------------------------------------------------------------------

# The runs' statuses by format: the first EDA's record API error (refs passed to the script API) happens
# with the /1 kit only; the script API of the new writer takes refs.
Q13_STATUSES = {"v1": ["error", "error", "ok", "ok", "ok", "ok"], "v2": ["error", "ok", "ok", "ok", "ok", "ok"]}


def test_q13_reproduction_has_its_judgments_and_they_are_the_data_s(generated):
    import polars as pl

    from aelix_analytics import relations

    fmt, fixtures = generated
    out = fixtures["q13a"]
    expected = read_json(out / "expected.json")
    assert schema_errors(expected, "expected.schema.json") == []
    assert tool.check(out) == []
    project = out / "project"
    _, model = read_model(project)
    statuses = sorted(r["execution_status"] for r in model.runs())
    assert statuses == Q13_STATUSES[fmt]
    assert expected["records"]["run_counts"] == {s: Q13_STATUSES[fmt].count(s) for s in ("ok", "error")}
    first = "eda_failed" if fmt == "v1" else "eda_first"
    assert expected["records"]["runs"][first]["status"] == ("error" if fmt == "v1" else "ok")
    layout = tool.LAYOUTS[fmt]
    assert expected["records"]["context_versions"] == len(list((project / layout.state_dir / "context").glob("*.md"))) == 3
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
    assert failed["status"] == "error" and len(failed["outputs"]) == 2 and len(relations.run_visualizations(failed)) == 2
    assert sorted(o["path"] for o in failed["outputs"]) == partial["outputs"]
    assert all(found == [partial["rewritten_by"]] for found in partial["producers_of_current_bytes"].values())
    for attached in expected["records"]["attached_data"]:
        script, _, chart_id = attached["chart"].partition("::")
        [chart] = [c for c in relations.run_visualizations(model.run(attached["run"])) if c["id"] == chart_id]
        assert (chart["data_rows"], chart["data_columns"]) == (attached["data_rows"], attached["data_columns"])
    assert (out / "input" / "traces.csv").read_bytes().count(b"\n") == 19_201
    assert len(expected["metrics"]) == 4 and [s["step"] for s in expected["session"]] == [1, 2, 3]


def test_the_formats_hold_the_same_q13_judgments(fixtures, fixtures_v2):
    """One plan in two formats: the facts, the claims' texts and verdicts, the metrics and the session are
    the same; the records differ only by the first EDA's status (Q13_STATUSES) and the ids."""
    one, other = read_json(fixtures["q13a"] / "expected.json"), read_json(fixtures_v2["q13a"] / "expected.json")
    for key in ("facts", "metrics", "session"):
        assert one[key] == other[key], key
    assert [{k: v for k, v in c.items() if k != "event"} for c in one["claims"]] == \
        [{k: v for k, v in c.items() if k != "event"} for c in other["claims"]]
    assert [r["status"] for r in one["records"]["runs"].values()][1:] == \
        [r["status"] for r in other["records"]["runs"].values()][1:]


def test_q13_context_defines_the_metrics_as_expected_json_and_the_script_do(generated):
    import polars as pl

    fmt, fixtures = generated
    out = fixtures["q13a"]
    expected = read_json(out / "expected.json")
    context = (out / "project" / "CONTEXT.md").read_text(encoding="utf-8")
    section = context.split("\n## 정의\n", 1)[1].split("\n## ", 1)[0]
    defined = dict(line[2:].split(": ", 1) for line in section.splitlines() if line.startswith("- "))
    assert list(defined) == [metric["name"] for metric in expected["metrics"]]
    for metric in expected["metrics"]:
        assert defined[metric["name"]].startswith(metric["computation"] + "."), metric["id"]
    # The formula stated is the one 02_step_profiles.py computed: the step mean less the sensor×step mean,
    # over the sensor×step standard deviation of the samples.
    [step] = [metric for metric in expected["metrics"] if metric["id"] == "context_step_score"]
    assert step["computation"] == "(트레이스의 스텝 평균 − sensor×step 전체 샘플의 평균) ÷ sensor×step 전체 샘플의 표준편차"
    traces = pl.read_parquet(out / "project" / tool.LAYOUTS[fmt].imported_dir / "traces.parquet")
    profile = traces.group_by("sensor", "step").agg(pl.col("value").mean().alias("m"), pl.col("value").std().alias("s"))
    means = traces.group_by("context_id", "sensor", "step").agg(pl.col("value").mean().alias("step_mean"))
    formula = means.join(profile, on=["sensor", "step"]).select(
        "context_id", "sensor", "step", ((pl.col("step_mean") - pl.col("m")) / pl.col("s")).alias("formula"))
    saved = pl.read_parquet(out / "project" / "data" / "derived" / "step_scores.parquet")
    joined = saved.join(formula, on=["context_id", "sensor", "step"])
    assert joined.height == saved.height == 80 * 4 * 4
    assert (joined["score"] - joined["formula"]).abs().max() < 1e-9


# --- the command and the environment record --------------------------------------------------------------

def test_usage_errors_write_nothing(tmp_path, v1_kit):
    env = tool_env(tmp_path)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("mine", encoding="utf-8")
    done = run_tool("q10", "--kit-root", v1_kit, "--out", busy, env=env)
    assert done.returncode == 2 and "not empty" in done.stderr
    assert sorted(p.name for p in busy.iterdir()) == ["keep.txt"]
    # v2 with a kit before the new writer, and a format there is none of.
    for fmt, says in (("v2", "writes lab.*/1"), ("v9", "unknown format")):
        target = tmp_path / f"format-{fmt}"
        done = run_tool("scale", "--scale", "S", "--format", fmt, "--kit-root", v1_kit, "--out", target,
                        env=env)
        assert done.returncode == 2 and says in done.stderr and not target.exists(), done.stderr
    assert run_tool("scale", "--scale", "L", "--out", tmp_path / "l", env=env).returncode == 2
    # This checkout's kit writes the new record format (kit 0.8.0): v1 without --kit-root says how to
    # give one, and --kit-root that is no runtime root is refused, both before anything is written.
    for extra, says in (((), "--kit-root"), (("--kit-root", tmp_path), "not a runtime root")):
        target = tmp_path / f"kit-{len(extra)}"
        done = run_tool("q10", *extra, "--out", target, env=env)
        assert done.returncode == 2 and says in done.stderr and not target.exists(), done.stderr


def test_the_manifest_names_the_kit_that_wrote_the_fixture(generated):
    """The kit that wrote the records: for v1 that of --kit-root (design OQ7), before the new record format,
    whose commit git cannot tell from an archive (null); for v2 this checkout's, named by its commit when its
    src/ is unchanged. A v2 project's id is the fixture's, not a random one."""
    from aelix_analytics import core

    fmt, fixtures = generated
    manifest = read_json(fixtures["s0a"] / "manifest.json")
    journal = (fixtures["s0a"] / "project" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert {json.loads(line)["schema"] for line in journal} == {tool.LAYOUTS[fmt].event}
    if fmt == "v1":
        assert manifest["kit_version"] == "0.7.0" and manifest["kit_commit"] is None
        return
    assert manifest["kit_version"] == core.KIT_VERSION
    assert manifest["kit_commit"] in (None, git_checkout("rev-parse", "HEAD").decode().strip())
    marker = read_json(fixtures["s0a"] / "project" / "analytics.json")
    assert marker["project_id"] == tool.fixture_project_id("scale/S/0") == json.loads(journal[0])["details"]["project_id"]
    ids = {read_json(fixtures[name] / "project" / "analytics.json")["project_id"] for name in ("s0a", "s1", "q10a", "q13a")}
    assert len(ids) == 4


def test_a_failed_generation_leaves_nothing_behind(tmp_path, monkeypatch):
    """What a failed generation wrote is removed, so the same --out can be given again."""
    def fail(*args, **kwargs):
        raise tool.FixtureError("a planned failure")

    monkeypatch.setattr(tool, "require_deterministic_process", lambda: None)
    # This process's kit is the checkout's, which writes no v1 (--kit-root is for a command's process).
    monkeypatch.setattr(tool, "check_format", lambda fmt: None)
    monkeypatch.setattr(tool, "kit_session", fail)  # after the sources are written under input/
    new = tmp_path / "new"
    with pytest.raises(tool.FixtureError, match="planned"):
        tool.generate_q10(new)
    assert not new.exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(tool.FixtureError, match="planned"):
        tool.generate_q13(empty)
    assert empty.is_dir() and not any(empty.iterdir())
    assert tool.prepare_out(empty) == empty


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
    assert record["disk"]["path"] == str(tmp_path.resolve())
    # The disk of the fixture measured, when the record is written elsewhere.
    done = run_tool("environment", "--out", tmp_path / "env-disk.json", "--disk", fixtures["s0a"], env=env)
    assert done.returncode == 0, done.stderr
    assert read_json(tmp_path / "env-disk.json")["disk"]["path"] == str(fixtures["s0a"].resolve())


# --- M ----------------------------------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("AELIX_ANALYTICS_TEST_SCALE_M") != "1",
                    reason="M writes about 440 MB twice per format; set AELIX_ANALYTICS_TEST_SCALE_M=1")
@pytest.mark.parametrize("fmt", tool.FORMATS)
def test_m_has_its_sizes_and_cases_and_is_the_seed_s(tmp_path, request, fmt):
    env = tool_env(tmp_path)
    extra = ("--kit-root", request.getfixturevalue("v1_kit")) if fmt == "v1" else ()
    seconds = {}
    for name in ("a", "b"):
        started = time.perf_counter()
        # v2 writes its 10,000 records through the common writer, each judged against the journal so far.
        done = run_tool("scale", "--scale", "M", "--seed", "0", "--format", fmt, *extra, "--out", tmp_path / name,
                        env=env, timeout=3600)
        seconds[name] = round(time.perf_counter() - started, 3)
        assert done.returncode == 0, done.stderr
    manifest = assert_sizes(tmp_path / "a", "M", fmt)
    assert REQUIRED_CASES <= {case["id"] for case in manifest["cases"] if case["required"]}
    assert tool.check(tmp_path / "a") == []
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (tmp_path / "b" / "manifest.json").read_bytes()
    assert tool.tree_digest(tmp_path / "b") == manifest["digest"]
    record_observation(f"eval-fixtures-m-{fmt}", {
        "command_seconds": seconds, "sizes": manifest["sizes"], "counts": manifest["counts"],
        "generator_seconds": read_json(tmp_path / "a" / "environment.json")["generation"]["seconds"]})
