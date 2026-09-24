"""Reusable human CLI/SDK baseline; no Aelix process or model is needed.

Each test retains its synthetic project and exact subprocess commands under pytest's
temporary directory. Use --basetemp inside an ignored evidence directory to keep a run.
This is automated coverage of the human entrypoint, not a full Q17 usability/model audit.
"""

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
from conftest import kit_env

from labkit import _platform, core

FIXTURE = Path(__file__).parent / "fixtures" / "human_baseline"
EXPECTED = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))


def file_hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


@pytest.fixture
def human_cli(tmp_path):
    root = tmp_path / "project"
    env = kit_env()
    # Exercise the CLI's human default; do not inherit an agent's session or actor.
    for key in ("LAB_ACTOR", "LAB_SESSION", "LAB_PROJECT", "AELIX_LAB_PROJECT"):
        env.pop(key, None)

    def call(*args, expected_exit=0):
        argv = [sys.executable, "-m", "labkit", "--project", str(root), *map(str, args)]
        started_at = datetime.now(UTC).isoformat()
        result = subprocess.run(argv, env=env, cwd=tmp_path, capture_output=True, timeout=120,
                                **_platform.text_kwargs())
        with (tmp_path / "commands.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"argv": argv, "cwd": str(tmp_path),
                "started_at": started_at, "exit_code": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}, ensure_ascii=False) + "\n")
        assert result.returncode == expected_exit, result.stdout + result.stderr
        records = [line for line in result.stdout.splitlines() if line.startswith("{")]
        assert records, result.stdout + result.stderr
        value = json.loads(records[-1])
        # A recorded failed run has ok=true but exit 3 and run.status=error.
        assert value["ok"] is (expected_exit != 1), value
        # import and note embed a report build that degrades to {"error": ...} while still
        # reporting ok=true and exit 0; only this payload shows that regression.
        assert "error" not in value.get("report", {}), value
        return value

    call("init", root, "--title", "Synthetic human baseline", "--goal", "Compare regional revenue")
    call("import", FIXTURE / "sales.csv", "--name", "sales", "--description", "Six synthetic rows")
    return root, call


def test_human_cli_analysis_failure_repair_and_snapshot_history(human_cli):
    root, call = human_cli
    project = core.Project(root)
    raw = root / "data/raw/sales.parquet"
    raw_hash = core.sha256_file(raw)
    meta = json.loads((root / "data/raw/sales.meta.json").read_text(encoding="utf-8"))
    assert meta["rows"] == EXPECTED["rows"]
    assert meta["source"]["sha256"] == core.sha256_file(FIXTURE / "sales.csv")
    refused = call("import", FIXTURE / "sales.csv", "--name", "sales", expected_exit=1)
    assert refused["error"]["code"] == "dataset_exists"
    query = call("query", (FIXTURE / "by_region.sql").read_text(encoding="utf-8"))
    assert query["tables"] == ["sales"]
    assert [column["name"] for column in query["columns"]] == ["region", "orders", "units", "revenue"]
    assert query["rows"] == [list(row.values()) for row in EXPECTED["by_region"]]
    assert sum(row[-1] for row in query["rows"]) == EXPECTED["total_revenue"]

    script = root / "scripts/region_summary.py"
    original = (FIXTURE / script.name).read_bytes()
    script.write_bytes(original)
    first = call("run", "scripts/region_summary.py", "--title", "All channels")["run"]
    assert first["schema"] == "lab.run/1" and first["status"] == "ok"
    assert first["actor"] == "human" and not first.get("session")
    assert first["inputs"][0]["path"] == "data/raw/sales.parquet"
    assert first["inputs"][0]["sha256"] == raw_hash
    assert "lab.read" in first["inputs"][0]["via"]
    assert (root / first["script"]["snapshot"]).read_bytes() == original
    chart = first["charts"][0]
    assert chart["id"] == "region-revenue" and chart["data_rows"] == 3
    assert pl.read_parquet(root / chart["data"]).to_dicts() == EXPECTED["by_region"]
    assert pl.read_parquet(root / "data/derived/region_summary.parquet").to_dicts() == EXPECTED["by_region"]
    first_hashes = file_hashes(root / "runs" / first["id"])

    question = call("note", "question", "어느 지역의 매출이 가장 높은가?")["event"]
    finding = call("note", "finding", "서울 매출은 580으로 가장 높다.",
                   "--ref", first["id"], "--ref", "region-revenue", "--ref", "sales",
                   "--resolves", question["id"])["event"]
    assert finding["actor"] == "human" and not finding.get("session")
    assert finding["refs"] == {"runs": [first["id"]], "charts": ["scripts/region_summary.py::region-revenue"],
                               "data": ["sales"], "resolves": question["id"]}
    before_context = (root / "CONTEXT.md").read_bytes()
    with (root / "CONTEXT.md").open("a", encoding="utf-8") as stream:
        stream.write("\n## Next question\nCompare online orders only.\n")
    context = call("context")["context"]
    assert "서울 매출은 580" in context and "Compare online orders only." in context

    failing = root / "scripts/failure.py"
    failing.write_bytes((FIXTURE / failing.name).read_bytes())
    failed = call("run", "scripts/failure.py", "--title", "Deliberate failure", expected_exit=3)["run"]
    assert failed["status"] == "error" and failed["error"]["type"] == "ValueError"
    assert failed["error"]["line"] == 6
    assert "Fixture: revenue requires source correction" in (root / failed["stderr"]).read_text(encoding="utf-8")
    failed_hashes = file_hashes(root / "runs" / failed["id"])
    failing.write_text("import labkit as lab\nprint(lab.read('sales').height)\n", encoding="utf-8")
    repaired = call("run", "scripts/failure.py", "--title", "Repaired analysis")["run"]
    assert repaired["status"] == "ok" and repaired["id"] != failed["id"]

    # Change the live code, then remove it: replay must use the preserved first version.
    edited = original.decode().replace('sales = lab.read("sales")',
                                       'sales = lab.read("sales").filter(pl.col("channel") == "online")')
    assert edited != original.decode(), "the fixture read line changed; the edit did nothing"
    script.write_text(edited, encoding="utf-8")
    second = call("run", "scripts/region_summary.py", "--title", "Online only")["run"]
    assert pl.read_parquet(root / second["charts"][0]["data"]).to_dicts() == EXPECTED["online_by_region"]
    script.unlink()
    rerun = call("run", "--rerun-of", first["id"], "--title", "Replay original snapshot")["run"]
    assert rerun["status"] == "ok" and rerun["id"] != first["id"]
    assert rerun["rerun_of"] == first["id"]
    assert rerun["reproduction"]["verdict"] == "match"
    assert rerun["reproduction"]["inputs_same"] is True
    assert rerun["reproduction"]["charts"] == {"region-revenue": "same data"}
    assert (root / rerun["script"]["snapshot"]).read_bytes() == original

    status = call("status")["status"]
    current = next(c for c in status["charts"] if c["key"] == "scripts/region_summary.py::region-revenue")
    assert current["run"] == second["id"] and current["versions"] == 2
    preserved_runs = file_hashes(root / "runs")
    journal_before_report = (root / "journal.jsonl").read_bytes()
    rendered = call("report")
    assert rendered["report"]["pages"] and not rendered["report"].get("skipped")
    assert (root / "report/index.html").is_file()
    for record in (first, failed, repaired, second, rerun):
        assert (root / "report/runs" / f"{record['id']}.html").is_file()
    assert file_hashes(root / "runs") == preserved_runs
    assert (root / "journal.jsonl").read_bytes().startswith(journal_before_report)
    assert file_hashes(root / "runs" / first["id"]) == first_hashes
    assert file_hashes(root / "runs" / failed["id"]) == failed_hashes
    assert core.sha256_file(raw) == raw_hash
    assert any(p.read_bytes() == before_context for p in (root / ".lab/context").glob("*.md"))
    records = {r["id"]: r for r in core.list_runs(project)}
    assert records[first["id"]]["script"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert records[failed["id"]]["status"] == "error"
    assert all(r["actor"] == "human" and not r.get("session") for r in records.values())
    assert any(e["id"] == finding["id"] and e["schema"] == "lab.event/1"
               for e in core.read_events(project))


def test_human_cli_captures_ordinary_libraries_without_sdk(human_cli):
    root, call = human_cli
    script = root / "scripts/direct_libraries.py"
    script.write_bytes((FIXTURE / script.name).read_bytes())
    run = call("run", "scripts/direct_libraries.py", "--title", "Ordinary library analysis")["run"]
    assert run["status"] == "ok" and run["actor"] == "human"
    assert run["inputs"][0]["path"] == "data/raw/sales.parquet"
    assert "duckdb.sql" in run["inputs"][0]["via"]
    assert pl.read_parquet(root / "data/derived/channel_summary.parquet").to_dicts() == EXPECTED["by_channel"]
    chart = run["charts"][0]
    assert chart["kind"] == "matplotlib" and chart["via"] == "plt.show"
    assert (root / chart["files"]["png"]).is_file()
    # Automatic image capture is useful but does not imply a plotted-data record.
    assert not chart.get("data")
    assert any("plotted data not recorded" in warning for warning in run["warnings"])
