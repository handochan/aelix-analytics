"""Issue46: detected inputs can be capped, but the loss must be inspectable."""

import json

import polars as pl
import pytest
from conftest import kit_cli

from aelix_analytics import core, runner, writer

ORIGIN = core.Origin("test", "sdk")


def parts_at(tmp_path, suffix="csv"):
    project = core.init_project(tmp_path / "project", origin=ORIGIN)
    parts = project.imported / "parts"
    parts.mkdir()
    for i in reversed(range(503)):
        path = parts / f"part_{i:03d}.{suffix}"
        if suffix == "parquet":
            pl.DataFrame({"x": [i]}).write_parquet(path)
        else:
            path.write_text(f"x\n{i}\n", encoding="utf-8")
    return project


def test_static_scan_sorts_before_cap_and_is_independent_of_glob_order(tmp_path, monkeypatch):
    project = parts_at(tmp_path)
    import glob

    real_glob = glob.glob
    warnings = []
    monkeypatch.setattr(glob, "glob", lambda *a, **kw: list(reversed(real_glob(*a, **kw))))
    found = runner.static_input_files(project, {"data/imported/parts/*.csv"}, set(), warnings=warnings)
    assert [p.name for p in found] == [f"part_{i:03d}.csv" for i in range(200)]
    assert len(warnings) == 1
    assert "capped at 200" in warnings[0] and "303 file candidate(s) omitted" in warnings[0]
    monkeypatch.setattr(glob, "glob", real_glob)
    assert runner.static_input_files(project, {"data/imported/parts/*.csv"}, set()) == found


@pytest.mark.parametrize("kind", ["glob", "directory"])
def test_503_real_reader_inputs_report_cap_in_record_reply_and_run_page(tmp_path, kind):
    project = parts_at(tmp_path, "parquet" if kind == "directory" else "csv")
    source, reader = (("data/imported/parts", "read_parquet") if kind == "directory"
                      else ("data/imported/parts/*.csv", "read_csv"))
    script = project.scripts / "read.py"
    script.write_text(f"import polars as pl\ndf = pl.{reader}({source!r})\nprint(df.height)\n",
                      encoding="utf-8")
    replies = [kit_cli(project.root, "run", "scripts/read.py", "--title", kind) for _ in range(2)]
    first = replies[0][1]["run"]
    for code, result in replies:
        assert code == 0 and result["run"]["status"] == "ok", result
        assert result["stdout_tail"].strip() == "503"
        run = result["run"]
        assert [i["path"] for i in run["inputs"]] == [i["path"] for i in first["inputs"]]
        assert len(run["inputs"]) == 500
        assert run["warnings"] == first["warnings"]
        cap = next(w for w in run["warnings"] if "capped at 500" in w)
        assert "3 file candidate(s) omitted" in cap
        assert "this detection pass" in cap and "freshness and lineage cover recorded inputs only" in cap
        stored = json.loads((project.runs / run["id"] / "run.json").read_text(encoding="utf-8"))
        assert stored["warnings"] == run["warnings"]
        page = (project.root / result["page"]).read_text(encoding="utf-8")
        assert "3 file candidate(s) omitted" in page
        assert "freshness and lineage cover recorded inputs only" in page
        assert not result.get("finalize_errors")


def test_static_only_503_candidate_cap_is_visible_when_unhooked_python_reads(tmp_path):
    project = parts_at(tmp_path)
    script = project.scripts / "read.py"
    # Builtin open is deliberately not a capture hook; the static pass is the only detector here.
    script.write_text("from pathlib import Path\npattern = 'data/imported/parts/*.csv'\n"
                      "print(sum(len(p.read_bytes()) > 0 for p in Path('.').glob(pattern)))\n",
                      encoding="utf-8")
    result = runner.execute(project, script="scripts/read.py", title="static", origin=ORIGIN)
    assert result["run"]["status"] == "ok" and result["stdout_tail"].strip() == "503"
    assert len(result["run"]["inputs"]) == 200
    assert "303 file candidate(s) omitted" in result["run"]["warnings"][0]
    run = result["run"]
    omitted = project.imported / "parts" / "part_502.csv"
    omitted.write_text("x\n-1\n", encoding="utf-8")
    # Stale/lineage do not infer the unseen remainder from a warning.
    assert not core.Freshness(project, [run]).of(run["id"])["stale"]
    recorded = project.imported / "parts" / "part_000.csv"
    recorded.write_text("x\n-1\n", encoding="utf-8")
    assert core.Freshness(project, [run]).of(run["id"])["changed_inputs"] == [
        "data/imported/parts/part_000.csv"]


def test_large_input_observation_refuses_without_truncating_or_appending(tmp_path):
    project = parts_at(tmp_path)
    script = project.scripts / "read.py"
    script.write_text("import polars as pl\npl.read_csv('data/imported/parts/*.csv')\n", encoding="utf-8")
    result = runner.execute(project, script="scripts/read.py", title="503", origin=ORIGIN)
    definition = {"contract": "metric_definition/1", "metric_id": "n", "name": "Count",
                  "status": "specified", "definition": {"unit": "rows"}, "unresolved": [],
                  "derived_from": [], "change_reason": "count"}
    d = core.record(project, "metric_definition", "count", details=definition, origin=ORIGIN)
    details = {"contract": "metric_observation/1", "metric_id": "n",
               "definition_event_id": d.event["id"], "run_id": result["run"]["id"],
               "scope": {}, "result": 503, "change_reason": "count"}
    before = project.journal.read_bytes()
    with pytest.raises(core.AnalyticsError) as refused:
        core.record(project, "metric_observation", "503", details=details, origin=ORIGIN)
    error = refused.value
    assert error.code == "text_too_long"
    assert error.details["text_bytes"] == 3
    assert error.details["line_bytes"] >= writer.LINE_LIMIT
    assert project.journal.read_bytes() == before
    assert len(result["run"]["inputs"]) == 500
    assert not core.metric_records(project)["observations"]


def test_pending_observation_final_refusal_preserves_run_and_reports_failure(tmp_path):
    project = parts_at(tmp_path)
    definition = {"contract": "metric_definition/1", "metric_id": "n", "name": "Count",
                  "status": "specified", "definition": {"unit": "rows"}, "unresolved": [],
                  "derived_from": [], "change_reason": "count"}
    d = core.record(project, "metric_definition", "count", details=definition, origin=ORIGIN)
    details = {"contract": "metric_observation/1", "metric_id": "n",
               "definition_event_id": d.event["id"], "scope": {}, "result": 503,
               "change_reason": "count"}
    script = project.scripts / "read.py"
    # No literal names the complete glob: the pending precheck sees no inputs yet.
    script.write_text("import polars as pl\nimport aelix_analytics as analytics\n"
                      f"ack = analytics.record('metric_observation', '503', details={details!r})\n"
                      "assert ack['pending']\nfolder = 'data/imported/' + 'parts'\n"
                      "df = pl.read_csv(folder + '/' + '*.csv')\nprint(df.height)\n",
                      encoding="utf-8")
    result = runner.execute(project, script="scripts/read.py", title="pending", origin=ORIGIN)
    assert result["run"]["status"] == "ok" and result["stdout_tail"].strip() == "503"
    assert len(result["run"]["inputs"]) == 500
    assert any("note metric_observation:" in p and "51,200" in p for p in result["finalize_errors"])
    assert not core.metric_records(project)["observations"]
    assert core.read_model(project).runs()[0]["recording_status"] == "incomplete"
