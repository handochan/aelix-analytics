import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from conftest import LINK_KINDS, dir_link, labkit, write_script

from labkit import _platform, core, runner

LAB_API = '''import matplotlib.pyplot as plt
import polars as pl

import labkit as lab

sales = lab.read("sales")
by_region = sales.group_by("region").agg(pl.col("revenue").sum()).sort("region")
lab.save(by_region, "region_revenue")
fig, ax = plt.subplots()
ax.bar(by_region["region"], by_region["revenue"])
ax.set_title("지역별 매출")
lab.chart(fig, "region-revenue", caption="합계", data=by_region)
print("rows", by_region.height)
'''

DUCKDB_PLOTLY = '''import duckdb
import plotly.express as px

mix = duckdb.sql("SELECT channel, sum(revenue) AS revenue FROM 'data/raw/sales.parquet' GROUP BY ALL ORDER BY channel").pl()
fig = px.bar(mix, x="channel", y="revenue", title="Channel mix")
fig.show()
'''

POLARS_PLT = '''import matplotlib.pyplot as plt
import polars as pl

df = pl.read_parquet("data/raw/sales.parquet")
monthly = df.group_by(pl.col("order_date").dt.month().alias("m")).agg(pl.col("revenue").sum()).sort("m")
monthly.write_parquet("data/derived/monthly.parquet")
plt.plot(monthly["m"], monthly["revenue"])
plt.title("Monthly")
plt.show()
'''


def _run(project, script, title="t", *extra):
    return labkit(project, "run", script, "--title", title, *extra)


def test_init_layout_and_journal(tmp_path):
    root = tmp_path / "p"
    from conftest import KIT

    project = core.init_project(root, title="분석", template_dir=KIT / "labkit" / "template", actor="test")
    for rel in ("lab.json", "CONTEXT.md", "journal.jsonl", "pyproject.toml", ".python-version",
                ".gitignore", "data/raw", "data/derived", "scripts", "runs", "report"):
        assert (root / rel).exists(), rel
    kinds = [e["kind"] for e in core.read_events(project)]
    assert kinds == ["project", "context"]
    assert "{{" not in (root / "pyproject.toml").read_text(encoding="utf-8")
    try:
        core.init_project(root, template_dir=KIT / "labkit" / "template")
    except core.LabError as exc:
        assert exc.code == "init_not_empty"
    else:
        raise AssertionError("init adopted a non-empty directory")


def test_lab_api_run_records_inputs_outputs_chart_and_code(project):
    code, result = _run(project, write_script(project, "01_region.py", LAB_API), "지역별")
    assert code == 0, result
    run = result["run"]
    assert run["status"] == "ok"
    assert [i["path"] for i in run["inputs"]] == ["data/raw/sales.parquet"]
    assert "lab.read" in run["inputs"][0]["via"]
    assert run["inputs"][0]["sha256"] == core.sha256_file(project / "data/raw/sales.parquet")
    out = {o["path"]: o for o in run["outputs"]}
    assert out["data/derived/region_revenue.parquet"]["change"] == "created"
    assert out["data/derived/region_revenue.parquet"]["via"] == "lab.save"
    assert out["data/derived/region_revenue.parquet"]["rows"] == 3
    (chart,) = run["charts"]
    assert chart["id"] == "region-revenue" and chart["title"] == "지역별 매출"
    assert chart["line"] == 12 and chart["data_rows"] == 3
    assert (project / chart["files"]["png"]).is_file() and (project / chart["data"]).is_file()
    snapshot = project / run["script"]["snapshot"]
    assert snapshot.read_text(encoding="utf-8") == LAB_API
    assert run["script"]["sha256"] == core.sha256_file(snapshot)
    assert "rows 3" in result["stdout_tail"]
    assert run["env"]["packages"]["polars"] and run["warnings"] == []
    events = core.read_events(core.Project(project))
    assert events[-1]["kind"] == "run" and events[-1]["refs"]["runs"] == [run["id"]]


def test_idiomatic_duckdb_plotly_and_polars_matplotlib_are_captured(project):
    _, r1 = _run(project, write_script(project, "02_mix.py", DUCKDB_PLOTLY))
    run = r1["run"]
    assert run["status"] == "ok"
    assert run["inputs"][0]["path"] == "data/raw/sales.parquet"
    assert "duckdb.sql" in run["inputs"][0]["via"]
    (chart,) = run["charts"]
    assert chart["kind"] == "plotly" and chart["via"] == "fig.show" and chart["title"] == "Channel mix"

    _, r2 = _run(project, write_script(project, "03_monthly.py", POLARS_PLT))
    run = r2["run"]
    assert "polars.read_parquet" in run["inputs"][0]["via"]
    assert [o["path"] for o in run["outputs"]] == ["data/derived/monthly.parquet"]
    (chart,) = run["charts"]
    assert chart["via"] == "plt.show" and chart["title"] == "Monthly" and not chart.get("data")
    assert any("plotted data not recorded" in w for w in run["warnings"])


def test_lab_sql_records_the_views_it_reads(project):
    _run(project, write_script(project, "00_part.py",
                               "import labkit as lab\nlab.save(lab.read('sales').head(5), 'part')\n"))
    body = ("import labkit as lab\n"
            "rel = lab.sql(\"WITH p AS (SELECT * FROM part) SELECT s.region, count(*) AS n "
            "FROM sales s JOIN p USING (region) WHERE s.channel <> 'part' GROUP BY 1\")\n"
            "print(rel.pl().height)\n")
    _, result = _run(project, write_script(project, "04_sql.py", body))
    inputs = {i["path"]: i["via"] for i in result["run"]["inputs"]}
    assert inputs == {"data/derived/part.parquet": ["lab.sql"],
                      "data/raw/sales.parquet": ["lab.sql"]}


def test_failed_run_keeps_error_line_and_traceback(project):
    body = "import polars as pl\n\nx = 1\nraise ValueError('boom')\n"
    code, result = _run(project, write_script(project, "bad.py", body))
    assert code == 3
    run = result["run"]
    assert run["status"] == "error" and run["error"]["line"] == 4
    assert "ValueError: boom" in result["stderr_tail"]
    assert core.chart_index(core.list_runs(core.Project(project))) == []


def test_timeout_is_recorded(project):
    code, result = _run(project, write_script(project, "slow.py", "import time\ntime.sleep(20)\n"),
                        "slow", "--timeout", "1")
    assert code == 3 and result["run"]["status"] == "timeout"


def test_rerun_match_and_mismatch(project):
    _, first = _run(project, write_script(project, "01_region.py", LAB_API))
    _, again = labkit(project, "run", "--rerun-of", first["run"]["id"], "--title", "rerun")
    repro = again["run"]["reproduction"]
    assert repro["verdict"] == "match" and repro["inputs_same"]
    assert repro["charts"] == {"region-revenue": "same data"}

    noisy = ("import time\nimport matplotlib.pyplot as plt\nimport polars as pl\nimport labkit as lab\n"
             "fig, ax = plt.subplots()\n"
             "lab.chart(fig, 'n', data=pl.DataFrame({'t': [time.time_ns()]}))\n")
    _, one = _run(project, write_script(project, "noise.py", noisy))
    _, two = labkit(project, "run", "--rerun-of", one["run"]["id"], "--title", "rerun")
    assert two["run"]["reproduction"]["verdict"] == "mismatch"


def test_charts_turn_stale_when_an_upstream_output_changes(project):
    upstream = ("import polars as pl\nimport labkit as lab\n"
                "lab.save(lab.read('sales').head(10), 'part')\n")
    downstream = ("import matplotlib.pyplot as plt\nimport labkit as lab\n"
                  "part = lab.read('part')\nfig, ax = plt.subplots()\nax.plot(part['revenue'])\n"
                  "lab.chart(fig, 'part-line', data=part)\n")
    _run(project, write_script(project, "10_up.py", upstream))
    _run(project, write_script(project, "11_down.py", downstream))
    st = core.status(core.Project(project))
    assert [c["stale"] for c in st["charts"]] == [False]

    time.sleep(0.01)
    write_script(project, "10_up.py", upstream.replace("head(10)", "head(20)"))
    _run(project, "scripts/10_up.py")
    st = core.status(core.Project(project))
    (chart,) = st["charts"]
    assert chart["stale"] and chart["changed_inputs"] == ["data/derived/part.parquet"]
    assert "STALE" in core.status_text(st)


def test_query_is_read_only(project):
    code, result = labkit(project, "query", "SELECT region, count(*) AS n FROM sales GROUP BY 1 ORDER BY 1")
    assert code == 0 and [r[1] for r in result["rows"]] == [40, 40, 40]
    assert result["tables"] == ["sales"]
    code, result = labkit(project, "query", "SUMMARIZE sales")
    assert code == 0 and result["rows"]
    for sql, expected in (("COPY (SELECT 1) TO 'x.parquet'", "query_read_only"),
                          ("CREATE TABLE t AS SELECT 1", "query_read_only"),
                          ("SELECT 1; SELECT 2", "query_single_statement")):
        code, result = labkit(project, "query", sql)
        assert code == 1 and result["error"]["code"] == expected
    assert not (Path(project) / "x.parquet").exists()


def test_import_is_immutable_and_records_source(project, tmp_path):
    meta = json.loads((project / "data/raw/sales.meta.json").read_text(encoding="utf-8"))
    assert meta["rows"] == 120 and meta["source"]["format"] == "csv" and meta["source"]["sha256"]
    code, result = labkit(project, "import", tmp_path / "sales.csv", "--name", "sales")
    assert code == 1 and result["error"]["code"] == "dataset_exists"
    ndjson = tmp_path / "events.ndjson"
    ndjson.write_text('{"a": 1, "b": "x"}\n{"a": 2, "b": "y"}\n', encoding="utf-8")
    code, result = labkit(project, "import", ndjson)
    assert code == 0 and result["dataset"]["name"] == "events" and result["dataset"]["rows"] == 2


def test_notes_resolution_and_context_history(project):
    p = core.Project(project)
    q = core.add_note(p, "question", "언제 넘었나?", actor="test")
    f = core.add_note(p, "finding", "3월에 넘었다", refs=["sales"], resolves=q["id"], actor="test")
    assert f["refs"] == {"data": ["sales"], "resolves": q["id"]}
    block = core.context_block(p)
    assert "3월에 넘었다" in block and "언제 넘었나?" not in block
    before = len(core.context_versions(p))
    p.context.write_text(p.context.read_text(encoding="utf-8") + "\n- 새 정의\n", encoding="utf-8")
    event = core.sync_context(p, actor="test")
    assert event and event["text"] == "CONTEXT.md updated (+2 -0 lines)"
    assert core.sync_context(p) is None
    assert len(core.context_versions(p)) == before + 1
    try:
        core.add_note(p, "resolve", "x")
    except core.LabError as exc:
        assert exc.code == "resolve_needs_target"


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.ids = [], set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        for key in ("href", "src"):
            if a.get(key):
                self.links.append(a[key])


def test_report_links_point_at_existing_files_and_anchors(project):
    _run(project, write_script(project, "01_region.py", LAB_API))
    _run(project, write_script(project, "02_mix.py", DUCKDB_PLOTLY))
    code, result = labkit(project, "report")
    assert code == 0
    report = project / "report"
    pages = sorted(report.rglob("*.html"))
    assert len(pages) >= 5
    parsed = {}
    for page in pages:
        parser = _Links()
        parser.feed(page.read_text(encoding="utf-8"))
        parsed[page.resolve()] = parser
    checked = 0
    for page, parser in parsed.items():
        for link in parser.links:
            url = urlparse(link)
            if url.scheme or link.startswith("#") and not link[1:]:
                continue
            target = page if not url.path else (page.parent / unquote(url.path)).resolve()
            assert target.exists(), f"{page.name}: broken link {link}"
            if url.fragment and target.suffix == ".html" and target in parsed:
                frag = unquote(url.fragment)
                tabs = {"overview", "charts", "timeline", "data", "runs", "context", "evaluation"}
                assert frag in parsed[target].ids or frag in tabs, f"{page.name}: missing #{frag}"
            checked += 1
    assert checked > 50
    index = (report / "index.html").read_text(encoding="utf-8")
    assert re.search(r'id="chart-scripts-01-region-py-region-revenue-[0-9a-f]{8}"', index)
    assert re.search(r'report\.js\?v=\d{8}-\d{6}', index)


def test_scan_literals_separates_reads_from_writes():
    reads, writes = runner.scan_literals(
        "import polars as pl\n"
        "df = pl.read_parquet('data/raw/a.parquet')\n"
        "df.write_parquet('data/derived/b.parquet')\n"
        "q = \"COPY (SELECT * FROM 'data/raw/c.parquet') TO 'data/derived/d.parquet'\"\n")
    assert "data/raw/a.parquet" in reads and "data/derived/b.parquet" in writes
    assert "data/derived/d.parquet" in writes


# --- regressions from the independent review (2026-09-16) ---

def _write_csv(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def test_one_figure_reused_for_several_named_charts_keeps_each_chart(project):
    body = ("import matplotlib.pyplot as plt\nimport polars as pl\nimport labkit as lab\n"
            "sales = lab.read('sales')\nfig, ax = plt.subplots()\n"
            "for n, region in [(5, '서울'), (6, '부산'), (7, '대구')]:\n"
            "    part = sales.filter(pl.col('region') == region).head(n)\n"
            "    ax.clear()\n    ax.plot(part['revenue'])\n    ax.set_title(f'Revenue {region}')\n"
            "    lab.chart(fig, f'rev-{len(part)}', data=part)\n"
            "fig2, ax2 = plt.subplots()\nax2.plot([1, 2])\nplt.show()\n"
            "lab.chart(fig2, 'named-after-show', data=pl.DataFrame({'y': [1, 2]}))\n")
    _, result = _run(project, write_script(project, "05_loop.py", body))
    charts = {c["id"]: c for c in result["run"]["charts"]}
    assert set(charts) == {"rev-5", "rev-6", "rev-7", "named-after-show"}, charts
    assert [charts[f"rev-{n}"]["data_rows"] for n in (5, 6, 7)] == [5, 6, 7]
    pngs = {core.sha256_file(project / charts[f"rev-{n}"]["files"]["png"]) for n in (5, 6, 7)}
    assert len(pngs) == 3
    assert charts["named-after-show"]["via"] == "lab.chart"
    leftovers = sorted(p.name for p in (project / "runs" / result["run"]["id"] / "charts").iterdir())
    assert not any(name.startswith("chart-") for name in leftovers), leftovers


def test_failed_or_incomplete_rerun_is_never_a_match(project):
    first = ("import polars as pl\nimport labkit as lab\nfrom pathlib import Path\n"
             "df = lab.read('sales').head(4)\nlab.save(df, 'base')\n"
             "if Path('data/derived/switch.txt').exists():\n    lab.save(df, 'maybe')\n")
    (project / "data/derived/switch.txt").write_text("on", encoding="utf-8")
    _, one = _run(project, write_script(project, "06_cond.py", first))
    (project / "data/derived/switch.txt").unlink()
    _, two = labkit(project, "run", "--rerun-of", one["run"]["id"], "--title", "rerun")
    repro = two["run"]["reproduction"]
    assert repro["verdict"] == "mismatch" and repro["outputs"]["data/derived/maybe.parquet"] == "not written"

    reader = ("import polars as pl\nimport matplotlib.pyplot as plt\nimport labkit as lab\n"
              "df = pl.read_parquet('data/derived/base.parquet')\nfig, ax = plt.subplots()\n"
              "ax.plot(df['revenue'])\nlab.chart(fig, 'rev', data=df)\n")
    _, three = _run(project, write_script(project, "07_reader.py", reader))
    (project / "data/derived/base.parquet").unlink()
    code, four = labkit(project, "run", "--rerun-of", three["run"]["id"], "--title", "rerun")
    assert code == 3 and four["run"]["status"] == "error"
    assert four["run"]["reproduction"]["verdict"] == "failed"


def test_rerun_of_an_old_snapshot_does_not_become_the_current_chart(project):
    v1 = ("import matplotlib.pyplot as plt\nimport labkit as lab\ndf = lab.read('sales')\n"
          "fig, ax = plt.subplots()\nax.plot(df['revenue'])\nlab.chart(fig, 'rev', title='v1', data=df)\n")
    _, one = _run(project, write_script(project, "08_versions.py", v1))
    v2 = v1.replace("title='v1'", "title='v2'") + (
        "fig2, ax2 = plt.subplots()\nax2.plot(df['revenue'])\nlab.chart(fig2, 'orders', data=df)\n")
    time.sleep(0.01)
    _, two = _run(project, write_script(project, "08_versions.py", v2))
    labkit(project, "run", "--rerun-of", one["run"]["id"], "--title", "check v1")
    charts = {c["key"].split("::")[1]: c for c in core.chart_index(core.list_runs(core.Project(project)))}
    assert charts["rev"]["title"] == "v2" and charts["rev"]["run"] == two["run"]["id"]
    assert not charts["orders"]["retired"]
    assert len(charts["rev"]["versions"]) == 2


def test_reading_back_an_own_output_is_not_an_input_and_not_stale(project):
    body = ("import polars as pl\nimport matplotlib.pyplot as plt\nimport labkit as lab\n"
            "m = lab.read('sales').group_by('region').agg(pl.col('revenue').sum()).sort('region')\n"
            "lab.save(m, 'monthly')\nback = pl.read_parquet('data/derived/monthly.parquet')\n"
            "fig, ax = plt.subplots()\nax.bar(back['region'], back['revenue'])\n"
            "lab.chart(fig, 'back', data=back)\n")
    script = write_script(project, "09_readback.py", body)
    _run(project, script)
    _, second = _run(project, script)
    assert [i["path"] for i in second["run"]["inputs"]] == ["data/raw/sales.parquet"]
    incremental = ("import polars as pl\nimport labkit as lab\nfrom pathlib import Path\n"
                   "p = Path('data/derived/acc.parquet')\n"
                   "old = pl.read_parquet(p) if p.exists() else pl.DataFrame({'n': []}, schema={'n': pl.Int64})\n"
                   "pl.concat([old, pl.DataFrame({'n': [old.height]})]).write_parquet(p)\n")
    inc = write_script(project, "10_incremental.py", incremental)
    _run(project, inc)
    _, again = _run(project, inc)
    assert "data/derived/acc.parquet" in [i["path"] for i in again["run"]["inputs"]]
    st = core.status(core.Project(project))
    assert not any(c["stale"] for c in st["charts"]), st["charts"]
    fresh = core.Freshness(core.Project(project), core.list_runs(core.Project(project)))
    assert not fresh.of(again["run"]["id"])["stale"]


# An output the kit cannot read when it hashes it. POSIX takes read access away; Windows cannot
# with chmod, so the script locks the byte through a second handle it keeps open, and a read of the
# file through any other handle — the kit's included — is refused (a byte-range lock).
UNREADABLE = ("if os.name == 'nt':\n"
              "    import msvcrt\n"
              "    sys._kept_locked = open('data/derived/secret.bin', 'r+b')\n"
              "    msvcrt.locking(sys._kept_locked.fileno(), msvcrt.LK_NBLCK, 1)\n"
              "else:\n"
              "    os.chmod('data/derived/secret.bin', 0)\n")


def test_finalization_survives_bad_notes_replaced_stdout_and_unreadable_outputs(project):
    body = ("import os, sys\nimport labkit as lab\nlab.note('finding', 42)\n"
            "open('data/derived/secret.bin', 'wb').write(b'x')\n" + UNREADABLE +
            "sys.stdout = open('data/derived/log.txt', 'w')\nprint('into the log')\n")
    code, result = _run(project, write_script(project, "11_hostile.py", body))
    os.chmod(project / "data/derived/secret.bin", 0o600)
    assert code == 0 and result["run"]["status"] == "ok"
    events = core.read_events(core.Project(project))
    assert any(e["kind"] == "finding" and e["text"] == "42" for e in events)
    if os.name != "nt" and os.geteuid() == 0:
        # Only this property needs the output to be unreadable (design review T11).
        pytest.skip("root reads a file whatever its mode, so the output was not unreadable")
    outs = {o["path"]: o for o in result["run"]["outputs"]}
    assert outs["data/derived/secret.bin"]["sha256"] is None


def test_timeout_inside_duckdb_is_recorded_as_timeout(project):
    body = "import duckdb\nduckdb.sql('SELECT sum(i) FROM range(200000000000) t(i)').fetchall()\n"
    code, result = _run(project, write_script(project, "12_slow_sql.py", body), "slow", "--timeout", "2")
    assert code == 3 and result["run"]["status"] == "timeout", result["run"]["error"]


# Both separators where Windows takes both.
SEPARATORS = ["/", "\\"] if os.name == "nt" else ["/"]


def _read_text(project, path: Path, separator: str):
    """`lab_query` reading one file. `content` only: read_text's last_modified column is a
    TIMESTAMPTZ, which needs pytz, which no environment here has (design review T9)."""
    text = str(path).replace(os.sep, separator)
    return labkit(project, "query", f"SELECT content FROM read_text('{text}')")


def _refused(result) -> bool:
    """DuckDB's own refusal, not some other failure of the same query."""
    error = result["error"]
    return error["code"] == "query_failed" and "Permission Error" in error["message"]


def test_query_cannot_write_or_read_outside_data(project):
    """A file that exists outside data/ — the project's own lab.json, directly and through
    data/.. — is refused by DuckDB's sandbox, while a file inside data/ is read: the refusal is the
    sandbox's, not a path that does not exist (criterion 9: this read /etc/hosts, which Windows
    does not have)."""
    code, result = labkit(project, "query", f"EXPLAIN ANALYZE COPY (SELECT * FROM sales) TO '{project}/data/derived/sneaky.parquet'")
    assert code == 1 and result["error"]["code"] == "query_read_only"
    assert not (project / "data/derived/sneaky.parquet").exists()
    (project / "data" / "raw" / "note.txt").write_text("inside", encoding="utf-8")
    for separator in SEPARATORS:
        code, result = _read_text(project, project / "data" / "raw" / "note.txt", separator)
        assert code == 0 and result["rows"] == [["inside"]], (separator, result)
        for outside in (project / "lab.json", project / "data" / ".." / "lab.json"):
            assert outside.is_file()
            code, result = _read_text(project, outside, separator)
            assert code == 1 and _refused(result), (separator, outside, result)
    code, result = labkit(project, "query", "EXPLAIN SELECT count(*) FROM sales")
    assert code == 0


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_query_cannot_read_through_a_link_out_of_data(project, tmp_path, kind):
    """A directory inside data/ that links outside it (design review T9): DuckDB checks the path it
    reaches, not the text, for a symbolic link everywhere and for a junction on Windows."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    dir_link(kind, project / "data" / "raw" / "out", outside)
    assert (project / "data" / "raw" / "out" / "secret.txt").is_file()
    for separator in SEPARATORS:
        code, result = _read_text(project, project / "data" / "raw" / "out" / "secret.txt", separator)
        assert code == 1 and _refused(result), (separator, result)


def test_sql_lineage_ignores_columns_and_ctes_named_like_datasets(project, tmp_path):
    csv = _write_csv(tmp_path / "region.csv", "region,manager", ["서울,a", "부산,b"])
    assert labkit(project, "import", csv, "--name", "region")[0] == 0
    body = ("import labkit as lab\n"
            "rel = lab.sql(\"WITH sales AS (SELECT * FROM sales) SELECT s.region FROM sales s "
            "JOIN (SELECT 1 AS k) x ON true WHERE s.region IN (SELECT region FROM sales)\")\n"
            "print(rel.pl().height)\n")
    _, result = _run(project, write_script(project, "13_cte.py", body))
    assert [i["path"] for i in result["run"]["inputs"]] == ["data/raw/sales.parquet"]
    from labkit import data
    views = {"sales": "data/raw/sales.parquet", "region": "data/raw/region.parquet"}
    assert data.referenced_views("SELECT * FROM sales s JOIN sales t USING (region)", views) == {"sales"}


def test_import_refuses_a_name_used_by_a_derived_dataset(project, tmp_path):
    _run(project, write_script(project, "14_derive.py",
                               "import labkit as lab\nlab.save(lab.read('sales').head(2), 'monthly')\n"))
    csv = _write_csv(tmp_path / "monthly.csv", "a", ["1"])
    code, result = labkit(project, "import", csv, "--name", "monthly")
    assert code == 1 and result["error"]["code"] == "dataset_name_taken"


def test_concurrent_runs_do_not_claim_each_others_outputs(project):
    import subprocess
    import sys

    from conftest import kit_env

    a = write_script(project, "15_a.py", "import time\nimport labkit as lab\ntime.sleep(1.5)\n"
                                         "lab.save(lab.read('sales').head(1), 'a_out')\n")
    b = write_script(project, "16_b.py", "import labkit as lab\nlab.save(lab.read('sales').head(1), 'b_out')\n")
    procs = [subprocess.Popen([sys.executable, "-m", "labkit", "--project", str(project), "run", s,
                               "--title", s], stdout=subprocess.PIPE, env=kit_env(),
                              **_platform.text_kwargs())
             for s in (a, b)]
    results = [json.loads([ln for ln in pr.communicate(timeout=120)[0].splitlines() if ln.startswith("{")][-1])
               for pr in procs]
    outputs = [[o["path"] for o in r["run"]["outputs"]] for r in results]
    assert outputs == [["data/derived/a_out.parquet"], ["data/derived/b_out.parquet"]], outputs


def test_rerun_ids_and_recorded_paths_are_validated(project):
    code, result = labkit(project, "run", "--rerun-of", "../../evil", "--title", "x")
    assert code == 1 and result["error"]["code"] == "rerun_id"
    _, one = _run(project, write_script(project, "17_ok.py", "x = 1\n"))
    run_json = project / "runs" / one["run"]["id"] / "run.json"
    record = json.loads(run_json.read_text(encoding="utf-8"))
    record["script"]["path"] = "/tmp/evil.py"
    run_json.write_text(json.dumps(record), encoding="utf-8")
    code, result = labkit(project, "run", "--rerun-of", one["run"]["id"], "--title", "x")
    assert code == 1 and result["error"]["code"] == "rerun_snapshot"


def test_non_utf8_context_does_not_break_status_or_report(project):
    (project / "CONTEXT.md").write_bytes("# 목표\n매출 분석\n".encode("cp949"))
    assert labkit(project, "status")[0] == 0
    assert labkit(project, "report")[0] == 0


def test_chart_anchors_are_unique_for_similar_keys():
    from labkit import report
    keys = [f"scripts/{'x' * 70}.py::growth-rate-by-region-{year}" for year in (2024, 2025)]
    keys += ["scripts/a.py::rev_total", "scripts/a.py::rev-total"]
    assert len({report.slug_key(k) for k in keys}) == len(keys)


def test_dynamic_duckdb_params_quoted_paths_and_polars_io_are_detected(project):
    body = ("import duckdb\nfrom polars.io import read_parquet\n"
            "path = 'data/raw/' + 'sales' + '.parquet'\n"
            "con = duckdb.connect()\ncon.execute('SELECT count(*) FROM read_parquet(?)', [path]).fetchall()\n")
    _, r1 = _run(project, write_script(project, "18_params.py", body))
    assert r1["run"]["inputs"][0]["via"] == ["duckdb.execute"]
    _, r2 = _run(project, write_script(project, "19_io.py", "from polars.io import read_parquet\n"
                                                           "read_parquet('data/raw/' + 'sales.parquet')\n"))
    assert "polars.read_parquet" in r2["run"]["inputs"][0]["via"]
    _, r3 = _run(project, write_script(project, "20_quoted.py", "import duckdb\n"
                                                               "duckdb.sql('SELECT count(*) FROM \"data/raw/sales.parquet\"').fetchall()\n"))
    assert "duckdb.sql" in r3["run"]["inputs"][0]["via"]


def test_replacing_a_chart_id_leaves_no_phantom_chart(project):
    body = ("import matplotlib.pyplot as plt\nimport polars as pl\nimport labkit as lab\n"
            "df = lab.read('sales')\n"
            "fig1, ax1 = plt.subplots()\nax1.plot(df['revenue'].head(3))\nax1.set_title('DRAFT chart')\n"
            "lab.chart(fig1, 'rev', title='draft', data=df.head(3))\n"
            "fig2, ax2 = plt.subplots()\nax2.plot(df['revenue'].head(9))\nax2.set_title('FINAL chart')\n"
            "lab.chart(fig2, 'rev', title='final', data=df.head(9))\n")
    _, result = _run(project, write_script(project, "21_replace.py", body))
    charts = result["run"]["charts"]
    assert [(c["id"], c["title"], c["via"], c["data_rows"]) for c in charts] == [
        ("rev", "final", "lab.chart", 9)]
    assert result["run"]["warnings"] == []
    keys = [c["key"] for c in core.chart_index(core.list_runs(core.Project(project)))]
    assert keys == ["scripts/21_replace.py::rev"]


def test_a_journal_failure_after_the_record_is_still_reported(project):
    (project / "journal.jsonl").unlink()
    (project / "journal.jsonl").mkdir()
    code, result = _run(project, write_script(project, "22_note.py",
                                              "import labkit as lab\nlab.note('finding', 'x')\n"))
    assert code == 0 and result["run"]["status"] == "ok"
    problems = result["finalize_errors"]
    assert any(p.startswith("journal:") for p in problems) and any(p.startswith("note ") for p in problems)
    recorded = json.loads((project / "runs" / result["run"]["id"] / "run.json").read_text(encoding="utf-8"))
    assert recorded["finalize_errors"] == problems


MANY_INPUTS = '''from pathlib import Path

import polars as pl

import labkit as lab

parts = sorted(Path("sources").glob("*.parquet"))
frames = [pl.read_parquet(p) for p in parts]
lab.save(pl.concat(frames), "combined")
print("parts", len(frames))
'''

READS_EXTERNAL = '''import polars as pl

import labkit as lab

lab.save(pl.read_csv(r"{source}").head(3), "peek")
'''


def test_a_bare_chart_id_in_a_note_links_to_the_chart(project):
    _run(project, write_script(project, "01_region.py", LAB_API), "지역별")
    event = core.add_note(core.Project(project), "finding", "서울 편중",
                          refs=["region-revenue", "sales", "made-up"])
    assert event["refs"]["charts"] == ["scripts/01_region.py::region-revenue"]
    assert event["refs"]["data"] == ["sales"]
    assert event["refs"]["other"] == ["made-up"]


def test_a_run_over_many_files_stays_readable_in_the_journal_and_report(project):
    import polars as pl

    sources = project / "sources"
    sources.mkdir()
    for i in range(70):
        pl.DataFrame({"v": [i]}).write_parquet(sources / f"part_{i:02d}.parquet")
    code, result = _run(project, write_script(project, "01_many.py", MANY_INPUTS), "많은 입력")
    assert code == 0, result
    assert len(result["run"]["inputs"]) >= 70

    event = core.read_events(core.Project(project))[-1]
    assert event["kind"] == "run"
    assert len(event["refs"]["data"]) <= core.MAX_EVENT_REFS
    assert "data/derived/combined.parquet" in event["refs"]["data"]  # outputs survive the cap
    assert "input file(s)" in event["text"]
    assert len(json.dumps(event, ensure_ascii=False)) < 4000

    assert labkit(project, "report")[0] == 0
    index = (project / "report" / "index.html").read_text(encoding="utf-8")
    assert "그 외 12개" in index, "the timeline does not say how many refs it hid"
    assert index.count("part_") <= 2 * core.MAX_EVENT_REFS, "the timeline dumped every input path"
    page = next((project / "report" / "runs").glob("*01-many.html")).read_text(encoding="utf-8")
    assert "입력 데이터 (70)" in page, "the run page no longer states the real input count"
    assert "그 외 10개" in page and "run.json" in page


def test_import_reads_uuid_as_text_and_warns_about_a_nested_json_object(project, tmp_path):
    src = tmp_path / "events.jsonl"
    src.write_text("\n".join(json.dumps({"session_id": sid, "n": i}) for i, sid in enumerate(
        ("3cdcaca2-c8b0-4a79-9fd7-4d21a1a2f03d", "b81995e3-4776-4175-bc82-eab0d664d109"))),
        encoding="utf-8")
    code, result = labkit(project, "import", src, "--name", "events")
    assert code == 0, result
    dtypes = {c["name"]: c["dtype"] for c in result["dataset"]["columns"]}
    assert dtypes["session_id"] == "String", dtypes
    assert any("read as text" in w for w in result["warnings"]), result["warnings"]

    obj = tmp_path / "settings.json"
    obj.write_text(json.dumps({"providers": {"ollama": {"models": [{"id": "q"}]}}}),
                   encoding="utf-8")
    code, result = labkit(project, "import", obj, "--name", "settings")
    assert code == 0 and result["dataset"]["rows"] == 1
    assert any("one JSON object" in w for w in result["warnings"]), result["warnings"]


def test_status_calls_out_a_dataset_no_run_produced(project):
    import polars as pl

    pl.DataFrame({"a": [1]}).write_parquet(project / "data" / "derived" / "sneaked.parquet")
    text = core.status_text(core.status(core.Project(project)))
    assert "sneaked" in text and "NO PRODUCING RUN" in text


def test_an_external_input_names_the_dataset_it_was_imported_as(project, tmp_path):
    source = tmp_path / "sales.csv"
    code, result = _run(project, write_script(project, "01_ext.py",
                                              READS_EXTERNAL.format(source=source)), "외부 입력")
    assert code == 0, result
    warnings = result["run"]["warnings"]
    assert any("already imported as dataset 'sales'" in w for w in warnings), warnings
    assert any("lab.read('sales')" in w for w in warnings), warnings


def test_a_context_with_no_sections_is_cut_and_says_to_split_it(project):
    p = core.Project(project)
    p.context.write_text("# 목표\n" + "긴 배경 설명. " * 900, encoding="utf-8")
    block = core.context_block(p)
    assert len(block) < 2 * core.CONTEXT_PROMPT_CHARS
    assert "`## 제목`으로 섹션을 나누면" in block
    text = core.status_text(core.status(p))
    assert f"only {core.CONTEXT_PROMPT_CHARS:,} reach the system prompt" in text


POLARS_PANDAS_HABIT = '''import polars as pl

df = pl.DataFrame({"a": [1, 2]})
print(df.to_string())
'''


def test_records_from_a_newer_kit_are_skipped_and_said_so(project):
    p = core.Project(project)
    core.append_jsonl(p.journal, {"schema": "lab.event/2", "seq": 99, "id": "e99",
                                  "kind": "finding", "text": "미래 kit이 쓴 기록"})
    future = p.runs / "20990101-000000-future"
    future.mkdir(parents=True)
    core.write_json(future / "run.json", {"schema": "lab.run/2", "id": future.name, "status": "ok"})

    assert all(e.get("id") != "e99" for e in core.read_events(p))
    assert future.name not in [r["id"] for r in core.list_runs(p)]
    assert core.unreadable_records(p) == {"lab.event/2": 1, "lab.run/2": 1}
    text = core.status_text(core.status(p))
    assert "lab.event/2" in text and "update the lab kit" in text
    assert labkit(project, "report")[0] == 0
    assert "이 리포트에서 빠진 기록이 있습니다" in (p.report / "index.html").read_text(encoding="utf-8")

    marker = json.loads(p.marker.read_text(encoding="utf-8"))
    p.marker.write_text(json.dumps({**marker, "schema": "lab.project/2"}), encoding="utf-8")
    try:
        core.Project(project).title()
    except core.LabError as exc:
        assert exc.code == "project_too_new"
    else:
        raise AssertionError("a project from a newer kit was read anyway")


def test_a_failing_run_says_what_to_write_instead(project):
    code, result = _run(project, write_script(project, "01_habit.py", POLARS_PANDAS_HABIT), "습관")
    assert code != 0
    assert "print(df)" in result["run"]["error"]["hint"]
    assert labkit(project, "report")[0] == 0
    page = next((project / "report" / "runs").glob("*01-habit.html")).read_text(encoding="utf-8")
    assert "고치는 법" in page and "df.glimpse()" in page

    for summary, expected in (
            ("ModuleNotFoundError: No module named 'pandas'", "no pandas"),
            ("AttributeError: 'GroupBy' object has no attribute 'pivot'", "pivot on the DataFrame"),
            ("AttributeError: 'Expr' object has no attribute 'mask'", "pl.when"),
            ("AttributeError: 'Series' object has no attribute 'map'", "map_elements"),
            ("AttributeError: 'float' object has no attribute 'alias'", "operator precedence"),
            ("AttributeError: 'DataFrame' object has no attribute 'iterrows'", "iter_rows")):
        assert expected in (runner.api_hint(summary) or ""), summary
    assert runner.api_hint("ZeroDivisionError: division by zero") is None


def test_context_is_disclosed_by_section_not_truncated_mid_sentence(project):
    p = core.Project(project)
    p.context.write_text(
        "# 제목\n\n> 안내 문구\n\n"
        "## 목표 <!-- 항상 -->\n세션 성능 비교.\n\n"
        "## 데이터 출처\n" + "| a | b |\n" * 400 + "\n"
        "## 다음 할 일\n- 모델 단위 비교\n", encoding="utf-8")

    block = core.context_block(p)
    assert "세션 성능 비교." in block and "모델 단위 비교" in block
    assert "| a | b |" not in block, "the oversized section was injected anyway"
    assert "### CONTEXT.md sections not repeated here" in block
    assert "- 데이터 출처 (402 lines): read offset 8 limit 402" in block
    assert "안내 문구" in block, "the preamble is always part of the block"
    assert not block.endswith("…"), "sections are dropped whole, never cut mid-sentence"

    st = core.status(p)
    assert st["project"]["context_omitted"] == ["데이터 출처"]
    text = core.status_text(st)
    assert "데이터 출처" in text and "<!-- 항상 -->" in text

    assert labkit(project, "report")[0] == 0
    index = (p.report / "index.html").read_text(encoding="utf-8")
    assert "프롬프트에 매번 들어가는 섹션: 목표, 다음 할 일" in index
    assert "| a | b |" in index, "the report still shows the whole file"


def test_a_pinned_section_survives_the_budget(project):
    p = core.Project(project)
    p.context.write_text("# 제목\n\n## 채우기\n" + "x" * 5000 + "\n\n"
                         "## 규칙 <!-- always -->\n- 매출은 세금 제외\n", encoding="utf-8")
    block = core.context_block(p)
    assert "매출은 세금 제외" in block
    assert core.status(p)["project"]["context_omitted"] == ["채우기"]


def test_init_reads_its_template_before_it_creates_anything(tmp_path):
    """A kit installed without its template answered with a traceback and left a half-made project
    that a second init refused as not empty (ADR-0004)."""
    from importlib import resources

    import pytest

    from labkit import core

    target = tmp_path / "proj"
    with pytest.raises(core.LabError) as caught:
        core.init_project(target, template_dir=tmp_path / "no-template", actor="test")
    assert caught.value.code == "template_missing"
    assert not target.exists()
    # The CLI hands it the template as package data, through the resource API.
    project = core.init_project(target, template_dir=resources.files("labkit") / "template",
                                actor="test")
    assert (project.root / "CONTEXT.md").is_file() and (project.root / ".gitignore").is_file()


def _kit_copy(tmp_path):
    import shutil

    from conftest import KIT

    copy = tmp_path / "kit"
    shutil.copytree(KIT, copy, ignore=shutil.ignore_patterns("__pycache__"))
    return copy


def test_the_kit_fingerprint_covers_its_template(tmp_path):
    """ADR-0004 moved the template into labkit, so env.kit_sha256 covers it (CONTRACTS F1)."""
    import subprocess
    import sys

    from conftest import kit_env

    copy = _kit_copy(tmp_path)

    def fingerprint():
        done = subprocess.run([sys.executable, "-c", "from labkit import runner; print(runner.kit_sha())"],
                              env=kit_env(PYTHONPATH=str(copy)), capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    before = fingerprint()
    (copy / "labkit" / "template" / "CONTEXT.md").write_text("# changed\n", encoding="utf-8")
    assert fingerprint() != before


def test_cli_init_without_a_template_answers_in_json_and_creates_nothing(tmp_path):
    import shutil
    import subprocess
    import sys

    from conftest import kit_env

    copy = _kit_copy(tmp_path)
    shutil.rmtree(copy / "labkit" / "template")
    target = tmp_path / "proj"
    done = subprocess.run([sys.executable, "-m", "labkit", "init", str(target)],
                          env=kit_env(PYTHONPATH=str(copy)), capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    assert done.returncode == 1, done.stderr
    assert json.loads(done.stdout.splitlines()[-1])["error"]["code"] == "template_missing"
    assert not target.exists()
