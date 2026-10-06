import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from conftest import LINK_KINDS, dir_link, kit_cli, write_script

from aelix_analytics import _platform, core, runner

LAB_API = '''import matplotlib.pyplot as plt
import polars as pl

import aelix_analytics as analytics

sales = analytics.read("sales")
by_region = sales.group_by("region").agg(pl.col("revenue").sum()).sort("region")
analytics.save(by_region, "region_revenue")
fig, ax = plt.subplots()
ax.bar(by_region["region"], by_region["revenue"])
ax.set_title("지역별 매출")
analytics.visualize(fig, "region-revenue", caption="합계", data=by_region)
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
    return kit_cli(project, "run", script, "--title", title, *extra)


def test_init_layout_and_journal(tmp_path):
    root = tmp_path / "p"
    from conftest import KIT

    project = core.init_project(root, title="분석", template_dir=KIT / "aelix_analytics" / "template", actor="test")
    for rel in ("lab.json", "CONTEXT.md", "journal.jsonl", "pyproject.toml", ".python-version",
                ".gitignore", "data/raw", "data/derived", "scripts", "runs", "report"):
        assert (root / rel).exists(), rel
    kinds = [e["kind"] for e in core.read_events(project)]
    assert kinds == ["project", "context"]
    assert "{{" not in (root / "pyproject.toml").read_text(encoding="utf-8")
    try:
        core.init_project(root, template_dir=KIT / "aelix_analytics" / "template")
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
                               "import aelix_analytics as analytics\nanalytics.save(analytics.read('sales').head(5), 'part')\n"))
    body = ("import aelix_analytics as analytics\n"
            "rel = analytics.sql(\"WITH p AS (SELECT * FROM part) SELECT s.region, count(*) AS n "
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
    _, again = kit_cli(project, "run", "--rerun-of", first["run"]["id"], "--title", "rerun")
    repro = again["run"]["reproduction"]
    assert repro["verdict"] == "match" and repro["inputs_same"]
    assert repro["charts"] == {"region-revenue": "same data"}

    noisy = ("import time\nimport matplotlib.pyplot as plt\nimport polars as pl\nimport aelix_analytics as analytics\n"
             "fig, ax = plt.subplots()\n"
             "analytics.visualize(fig, 'n', data=pl.DataFrame({'t': [time.time_ns()]}))\n")
    _, one = _run(project, write_script(project, "noise.py", noisy))
    _, two = kit_cli(project, "run", "--rerun-of", one["run"]["id"], "--title", "rerun")
    assert two["run"]["reproduction"]["verdict"] == "mismatch"


def test_charts_turn_stale_when_an_upstream_output_changes(project):
    upstream = ("import polars as pl\nimport aelix_analytics as analytics\n"
                "analytics.save(analytics.read('sales').head(10), 'part')\n")
    downstream = ("import matplotlib.pyplot as plt\nimport aelix_analytics as analytics\n"
                  "part = analytics.read('part')\nfig, ax = plt.subplots()\nax.plot(part['revenue'])\n"
                  "analytics.visualize(fig, 'part-line', data=part)\n")
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
    code, result = kit_cli(project, "query", "SELECT region, count(*) AS n FROM sales GROUP BY 1 ORDER BY 1")
    assert code == 0 and [r[1] for r in result["rows"]] == [40, 40, 40]
    assert result["tables"] == ["sales"]
    code, result = kit_cli(project, "query", "SUMMARIZE sales")
    assert code == 0 and result["rows"]
    for sql, expected in (("COPY (SELECT 1) TO 'x.parquet'", "query_read_only"),
                          ("CREATE TABLE t AS SELECT 1", "query_read_only"),
                          ("SELECT 1; SELECT 2", "query_single_statement")):
        code, result = kit_cli(project, "query", sql)
        assert code == 1 and result["error"]["code"] == expected
    assert not (Path(project) / "x.parquet").exists()


def test_import_is_immutable_and_records_source(project, tmp_path):
    meta = json.loads((project / "data/raw/sales.meta.json").read_text(encoding="utf-8"))
    assert meta["rows"] == 120 and meta["source"]["format"] == "csv" and meta["source"]["sha256"]
    code, result = kit_cli(project, "import", tmp_path / "sales.csv", "--name", "sales")
    assert code == 1 and result["error"]["code"] == "dataset_exists"
    ndjson = tmp_path / "events.ndjson"
    ndjson.write_text('{"a": 1, "b": "x"}\n{"a": 2, "b": "y"}\n', encoding="utf-8")
    code, result = kit_cli(project, "import", ndjson)
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
    code, result = kit_cli(project, "render")
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
    body = ("import matplotlib.pyplot as plt\nimport polars as pl\nimport aelix_analytics as analytics\n"
            "sales = analytics.read('sales')\nfig, ax = plt.subplots()\n"
            "for n, region in [(5, '서울'), (6, '부산'), (7, '대구')]:\n"
            "    part = sales.filter(pl.col('region') == region).head(n)\n"
            "    ax.clear()\n    ax.plot(part['revenue'])\n    ax.set_title(f'Revenue {region}')\n"
            "    analytics.visualize(fig, f'rev-{len(part)}', data=part)\n"
            "fig2, ax2 = plt.subplots()\nax2.plot([1, 2])\nplt.show()\n"
            "analytics.visualize(fig2, 'named-after-show', data=pl.DataFrame({'y': [1, 2]}))\n")
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
    first = ("import polars as pl\nimport aelix_analytics as analytics\nfrom pathlib import Path\n"
             "df = analytics.read('sales').head(4)\nanalytics.save(df, 'base')\n"
             "if Path('data/derived/switch.txt').exists():\n    analytics.save(df, 'maybe')\n")
    (project / "data/derived/switch.txt").write_text("on", encoding="utf-8")
    _, one = _run(project, write_script(project, "06_cond.py", first))
    (project / "data/derived/switch.txt").unlink()
    _, two = kit_cli(project, "run", "--rerun-of", one["run"]["id"], "--title", "rerun")
    repro = two["run"]["reproduction"]
    assert repro["verdict"] == "mismatch" and repro["outputs"]["data/derived/maybe.parquet"] == "not written"

    reader = ("import polars as pl\nimport matplotlib.pyplot as plt\nimport aelix_analytics as analytics\n"
              "df = pl.read_parquet('data/derived/base.parquet')\nfig, ax = plt.subplots()\n"
              "ax.plot(df['revenue'])\nanalytics.visualize(fig, 'rev', data=df)\n")
    _, three = _run(project, write_script(project, "07_reader.py", reader))
    (project / "data/derived/base.parquet").unlink()
    code, four = kit_cli(project, "run", "--rerun-of", three["run"]["id"], "--title", "rerun")
    assert code == 3 and four["run"]["status"] == "error"
    assert four["run"]["reproduction"]["verdict"] == "failed"


def test_rerun_of_an_old_snapshot_does_not_become_the_current_chart(project):
    v1 = ("import matplotlib.pyplot as plt\nimport aelix_analytics as analytics\ndf = analytics.read('sales')\n"
          "fig, ax = plt.subplots()\nax.plot(df['revenue'])\nanalytics.visualize(fig, 'rev', title='v1', data=df)\n")
    _, one = _run(project, write_script(project, "08_versions.py", v1))
    v2 = v1.replace("title='v1'", "title='v2'") + (
        "fig2, ax2 = plt.subplots()\nax2.plot(df['revenue'])\nanalytics.visualize(fig2, 'orders', data=df)\n")
    time.sleep(0.01)
    _, two = _run(project, write_script(project, "08_versions.py", v2))
    kit_cli(project, "run", "--rerun-of", one["run"]["id"], "--title", "check v1")
    charts = {c["key"].split("::")[1]: c for c in core.chart_index(core.list_runs(core.Project(project)))}
    assert charts["rev"]["title"] == "v2" and charts["rev"]["run"] == two["run"]["id"]
    assert not charts["orders"]["retired"]
    assert len(charts["rev"]["versions"]) == 2


def test_reading_back_an_own_output_is_not_an_input_and_not_stale(project):
    body = ("import polars as pl\nimport matplotlib.pyplot as plt\nimport aelix_analytics as analytics\n"
            "m = analytics.read('sales').group_by('region').agg(pl.col('revenue').sum()).sort('region')\n"
            "analytics.save(m, 'monthly')\nback = pl.read_parquet('data/derived/monthly.parquet')\n"
            "fig, ax = plt.subplots()\nax.bar(back['region'], back['revenue'])\n"
            "analytics.visualize(fig, 'back', data=back)\n")
    script = write_script(project, "09_readback.py", body)
    _run(project, script)
    _, second = _run(project, script)
    assert [i["path"] for i in second["run"]["inputs"]] == ["data/raw/sales.parquet"]
    incremental = ("import polars as pl\nimport aelix_analytics as analytics\nfrom pathlib import Path\n"
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
    body = ("import os, sys\nimport aelix_analytics as analytics\nanalytics.record('finding', 42)\n"
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
    """`analytics_query` reading one file. `content` only: read_text's last_modified column is a
    TIMESTAMPTZ, which needs pytz, which no environment here has (design review T9)."""
    text = str(path).replace(os.sep, separator)
    return kit_cli(project, "query", f"SELECT content FROM read_text('{text}')")


def _refused(result) -> bool:
    """DuckDB's own refusal, not some other failure of the same query."""
    error = result["error"]
    return error["code"] == "query_failed" and "Permission Error" in error["message"]


def test_query_cannot_write_or_read_outside_data(project):
    """A file that exists outside data/ — the project's own lab.json, directly and through
    data/.. — is refused by DuckDB's sandbox, while a file inside data/ is read: the refusal is the
    sandbox's, not a path that does not exist (criterion 9: this read /etc/hosts, which Windows
    does not have)."""
    code, result = kit_cli(project, "query", f"EXPLAIN ANALYZE COPY (SELECT * FROM sales) TO '{project}/data/derived/sneaky.parquet'")
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
    code, result = kit_cli(project, "query", "EXPLAIN SELECT count(*) FROM sales")
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
    assert kit_cli(project, "import", csv, "--name", "region")[0] == 0
    body = ("import aelix_analytics as analytics\n"
            "rel = analytics.sql(\"WITH sales AS (SELECT * FROM sales) SELECT s.region FROM sales s "
            "JOIN (SELECT 1 AS k) x ON true WHERE s.region IN (SELECT region FROM sales)\")\n"
            "print(rel.pl().height)\n")
    _, result = _run(project, write_script(project, "13_cte.py", body))
    assert [i["path"] for i in result["run"]["inputs"]] == ["data/raw/sales.parquet"]
    from aelix_analytics import data
    views = {"sales": "data/raw/sales.parquet", "region": "data/raw/region.parquet"}
    assert data.referenced_views("SELECT * FROM sales s JOIN sales t USING (region)", views) == {"sales"}


def test_import_refuses_a_name_used_by_a_derived_dataset(project, tmp_path):
    _run(project, write_script(project, "14_derive.py",
                               "import aelix_analytics as analytics\nanalytics.save(analytics.read('sales').head(2), 'monthly')\n"))
    csv = _write_csv(tmp_path / "monthly.csv", "a", ["1"])
    code, result = kit_cli(project, "import", csv, "--name", "monthly")
    assert code == 1 and result["error"]["code"] == "dataset_name_taken"


def test_concurrent_runs_do_not_claim_each_others_outputs(project):
    import subprocess
    import sys

    from conftest import kit_env

    a = write_script(project, "15_a.py", "import time\nimport aelix_analytics as analytics\ntime.sleep(1.5)\n"
                                         "analytics.save(analytics.read('sales').head(1), 'a_out')\n")
    b = write_script(project, "16_b.py", "import aelix_analytics as analytics\nanalytics.save(analytics.read('sales').head(1), 'b_out')\n")
    procs = [subprocess.Popen([sys.executable, "-m", "aelix_analytics", "--project", str(project), "run", s,
                               "--title", s], stdout=subprocess.PIPE, env=kit_env(),
                              **_platform.text_kwargs())
             for s in (a, b)]
    results = [json.loads([ln for ln in pr.communicate(timeout=120)[0].splitlines() if ln.startswith("{")][-1])
               for pr in procs]
    outputs = [[o["path"] for o in r["run"]["outputs"]] for r in results]
    assert outputs == [["data/derived/a_out.parquet"], ["data/derived/b_out.parquet"]], outputs


def test_rerun_ids_and_recorded_paths_are_validated(project):
    code, result = kit_cli(project, "run", "--rerun-of", "../../evil", "--title", "x")
    assert code == 1 and result["error"]["code"] == "rerun_id"
    _, one = _run(project, write_script(project, "17_ok.py", "x = 1\n"))
    run_json = project / "runs" / one["run"]["id"] / "run.json"
    record = json.loads(run_json.read_text(encoding="utf-8"))
    record["script"]["path"] = "/tmp/evil.py"
    run_json.write_text(json.dumps(record), encoding="utf-8")
    code, result = kit_cli(project, "run", "--rerun-of", one["run"]["id"], "--title", "x")
    assert code == 1 and result["error"]["code"] == "rerun_snapshot"


def test_non_utf8_context_does_not_break_status_or_report(project):
    (project / "CONTEXT.md").write_bytes("# 목표\n매출 분석\n".encode("cp949"))
    assert kit_cli(project, "status")[0] == 0
    assert kit_cli(project, "render")[0] == 0


def test_chart_anchors_are_unique_for_similar_keys():
    from aelix_analytics import report
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
    body = ("import matplotlib.pyplot as plt\nimport polars as pl\nimport aelix_analytics as analytics\n"
            "df = analytics.read('sales')\n"
            "fig1, ax1 = plt.subplots()\nax1.plot(df['revenue'].head(3))\nax1.set_title('DRAFT chart')\n"
            "analytics.visualize(fig1, 'rev', title='draft', data=df.head(3))\n"
            "fig2, ax2 = plt.subplots()\nax2.plot(df['revenue'].head(9))\nax2.set_title('FINAL chart')\n"
            "analytics.visualize(fig2, 'rev', title='final', data=df.head(9))\n")
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
                                              "import aelix_analytics as analytics\nanalytics.record('finding', 'x')\n"))
    assert code == 0 and result["run"]["status"] == "ok"
    problems = result["finalize_errors"]
    assert any(p.startswith("journal:") for p in problems) and any(p.startswith("note ") for p in problems)
    recorded = json.loads((project / "runs" / result["run"]["id"] / "run.json").read_text(encoding="utf-8"))
    assert recorded["finalize_errors"] == problems


MANY_INPUTS = '''from pathlib import Path

import polars as pl

import aelix_analytics as analytics

parts = sorted(Path("sources").glob("*.parquet"))
frames = [pl.read_parquet(p) for p in parts]
analytics.save(pl.concat(frames), "combined")
print("parts", len(frames))
'''

READS_EXTERNAL = '''import polars as pl

import aelix_analytics as analytics

analytics.save(pl.read_csv(r"{source}").head(3), "peek")
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

    assert kit_cli(project, "render")[0] == 0
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
    code, result = kit_cli(project, "import", src, "--name", "events")
    assert code == 0, result
    dtypes = {c["name"]: c["dtype"] for c in result["dataset"]["columns"]}
    assert dtypes["session_id"] == "String", dtypes
    assert any("read as text" in w for w in result["warnings"]), result["warnings"]

    obj = tmp_path / "settings.json"
    obj.write_text(json.dumps({"providers": {"ollama": {"models": [{"id": "q"}]}}}),
                   encoding="utf-8")
    code, result = kit_cli(project, "import", obj, "--name", "settings")
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
    assert any("analytics.read('sales')" in w for w in warnings), warnings


def test_a_context_with_no_sections_is_cut_and_says_to_split_it(project):
    p = core.Project(project)
    p.context.write_text("# 목표\n" + "긴 배경 설명. " * 900, encoding="utf-8")
    block = core.context_block(p)
    assert len(block) < 2 * core.CONTEXT_PROMPT_CHARS
    preamble = "# 목표\n" + ("긴 배경 설명. " * 900).strip()
    kept = preamble[:core.CONTEXT_PROMPT_CHARS].rstrip()
    assert block.split("## CONTEXT.md\n", 1)[1].startswith(kept + "\n")
    # Two lines, as the read tool numbers them: the pointer reads all of the text that was cut.
    assert f"\n… (Omitted from current context: {len(preamble) - len(kept):,} more characters " \
           f"above the first `## ` heading — all of it: read {p.context} offset 1 limit 2)" in block
    text = core.status_text(core.status(p))
    assert f"only the first {core.CONTEXT_PROMPT_CHARS:,} reach the system prompt" in text
    assert "split it into `## ` sections" in text
    # The file's length is not said to be the length above the first heading (D4).
    assert not re.search(r"CONTEXT\.md has [\d,]+ characters above", text)


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
    line = len(p.journal.read_bytes().splitlines())
    st = core.status(p)
    # Issue #12 PR-2: counted where they are and why (ADR-0006 1.4), by the read the overview is.
    assert st["unsupported"] == [{"location": f"journal:{line}", "reason": "unknown_version"},
                                 {"location": f"runs/{future.name}/run.json",
                                  "reason": "unknown_version"}]
    assert future.name not in [r["id"] for r in st["runs"]]
    text = core.status_text(st)
    assert (f"unsupported: 2 record(s) this kit cannot read are in no list here and not in the "
            f"report — journal:{line} (unknown_version), runs/{future.name}/run.json "
            "(unknown_version); a newer kit may read them") in text
    assert kit_cli(project, "render")[0] == 0
    index = (p.report / "index.html").read_text(encoding="utf-8")
    assert "Unsupported: 2 record(s) this kit cannot read" in index
    assert f"journal:{line} (unknown_version)" in index

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
    assert kit_cli(project, "render")[0] == 0
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

    assert kit_cli(project, "render")[0] == 0
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

    from aelix_analytics import core

    target = tmp_path / "proj"
    with pytest.raises(core.LabError) as caught:
        core.init_project(target, template_dir=tmp_path / "no-template", actor="test")
    assert caught.value.code == "template_missing"
    assert not target.exists()
    # The CLI hands it the template as package data, through the resource API.
    project = core.init_project(target, template_dir=resources.files("aelix_analytics") / "template",
                                actor="test")
    assert (project.root / "CONTEXT.md").is_file() and (project.root / ".gitignore").is_file()


def _kit_copy(tmp_path):
    import shutil

    from conftest import KIT

    copy = tmp_path / "kit"
    shutil.copytree(KIT, copy, ignore=shutil.ignore_patterns("__pycache__"))
    return copy


def test_the_kit_fingerprint_covers_its_template(tmp_path):
    """ADR-0004 moved the template into aelix_analytics, so env.kit_sha256 covers it (CONTRACTS F1)."""
    import subprocess
    import sys

    from conftest import kit_env

    copy = _kit_copy(tmp_path)

    def fingerprint():
        done = subprocess.run([sys.executable, "-c", "from aelix_analytics import runner; print(runner.kit_sha())"],
                              env=kit_env(PYTHONPATH=str(copy)), capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    before = fingerprint()
    (copy / "aelix_analytics" / "template" / "CONTEXT.md").write_text("# changed\n", encoding="utf-8")
    assert fingerprint() != before


def test_cli_init_without_a_template_answers_in_json_and_creates_nothing(tmp_path):
    import shutil
    import subprocess
    import sys

    from conftest import kit_env

    copy = _kit_copy(tmp_path)
    shutil.rmtree(copy / "aelix_analytics" / "template")
    target = tmp_path / "proj"
    done = subprocess.run([sys.executable, "-m", "aelix_analytics", "init", str(target)],
                          env=kit_env(PYTHONPATH=str(copy)), capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    assert done.returncode == 1, done.stderr
    assert json.loads(done.stdout.splitlines()[-1])["error"]["code"] == "template_missing"
    assert not target.exists()


# --- Issue #11 PR-2: what the context block and analytics_status leave out, and where it is ---

LINE_ENDS = {"lf": None, "crlf": None, "cr": "\r", "u2028": "\u2028", "u2029": "\u2029",
              "ff": "\x0c", "vt": "\x0b", "nel": "\x85", "lead1": None, "lead3": None,
              "trail": None}


def _sections_file(variant: str) -> bytes:
    """A CONTEXT.md of a title, a pinned goal, a background that takes most of the budget and 20
    sections that do not fit, its lines written as `variant` says."""
    rules = [f"## Rule {i:02d}\n" + "".join(f"- rule {i:02d} line {j}: {'x' * 60}\n"
                                            for j in range(6 + i % 3)) for i in range(20)]
    text = ("# Title\n\n> a note\n\n## Goal <!-- always -->\n- measure\n\n## Background\n"
            + "b" * 3500 + "\n\n" + "\n".join(rules))
    if variant == "crlf":
        return text.replace("\n", "\r\n").encode()
    if variant in ("lead1", "lead3"):
        return ("\n" * int(variant[-1]) + text).encode()
    if variant == "trail":
        return (text + "\n \n\n").encode()
    if LINE_ENDS[variant]:
        # Inside a line of rule 03, which str.splitlines would split in two.
        at = text.index("- rule 03 line 2: ") + 10
        text = text[:at] + LINE_ENDS[variant] + text[at:]
    return text.encode()


@pytest.mark.parametrize("variant", list(LINE_ENDS))
def test_section_pointers_are_the_lines_the_read_tool_numbers(tmp_path, variant):
    """Issue #11 G1 (D7): aelix's read tool splits a file at b"\\n" alone and numbers its lines
    from 1. Every pointer's range is one whole section of the file's bytes split that way: its
    heading first, no other heading inside, and a heading or the end of the file after it."""
    p = core.init_project(tmp_path / "p", actor="test")
    data = _sections_file(variant)
    p.context.write_bytes(data)
    lines = data.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    block = core.context_block(p)
    pointers = re.findall(r"^- (.+) \((\d+) lines\): read offset (\d+) limit (\d+)$", block, re.M)
    assert [name for name, *_ in pointers] == [f"Rule {i:02d}" for i in range(20)], block[-1500:]
    for name, count, offset, limit in pointers:
        first, n = int(offset), int(limit)
        assert int(count) == n
        got = lines[first - 1:first - 1 + n]
        assert got[0].rstrip(b"\r") == f"## {name}".encode(), (name, got[0])
        assert not [line for line in got[1:] if line.startswith(b"## ")], name
        # A heading after it, or only blank lines: the last section ends at its last line that is
        # not blank, as main counted it.
        rest = lines[first - 1 + n:]
        assert (rest and rest[0].startswith(b"## ")) or (got[-1].strip() and not
                                                        b"".join(rest).strip()), name
    assert f"read the one you need from {p.context}:" in block and "- measure" in block
    # The sections chosen are the same whatever the line ends: their length is counted on the text
    # with its line ends as "\n" (design section 18).
    assert core.status(p)["project"]["context_omitted"] == [f"Rule {i:02d}" for i in range(20)]


def test_a_context_whose_lines_end_in_a_lone_cr_is_one_line(tmp_path):
    """A lone CR is no line end to aelix's read tool: a CONTEXT.md written with lone-CR line ends
    is one line. The kit reads it as that one line, all of it preamble: the block shows its first
    4,000 characters, each CR as a line end, and points at line 1 for the rest (Issue #11 PR-2)."""
    p = core.init_project(tmp_path / "p", actor="test")
    p.context.write_bytes(("# Title\r\r## Goal <!-- always -->\r- measure\r\r## Rules\r"
                           + "- a rule\r" * 600).encode())
    block = core.context_block(p)
    assert "### CONTEXT.md sections not repeated here" not in block
    shown = block.split("## CONTEXT.md\n", 1)[1]
    assert shown.startswith("# Title\n\n## Goal <!-- always -->\n- measure\n\n## Rules\n- a rule\n")
    assert f"— all of it: read {p.context} offset 1 limit 1)" in block
    st = core.status(p)
    assert st["project"]["context_truncated"] and st["project"]["context_omitted"] == []
    # analytics_status names the cause: no `## ` heading starts a line, as the read tool counts lines.
    text = core.status_text(st)
    assert f"CONTEXT.md ({st['project']['context_chars']:,} characters) ends its lines in a lone " \
           "CR, which is no line end to the read tool, so it is one line" in text
    assert "save it with LF or CRLF line ends" in text and "split it into" not in text


def test_the_block_counts_the_entries_it_leaves_out_and_says_where_they_are(tmp_path):
    """Issue #11 G3 (D5): past its latest 12 entries, and past 300 characters of one, the block
    says how many entries it leaves out and where the journal is, once a list."""
    p = core.init_project(tmp_path / "p", actor="test")
    for i in range(14):
        core.add_note(p, "finding", f"finding {i:02d}", actor="test")
    core.add_note(p, "finding", "long " * 100, actor="test")
    for i in range(3):
        core.add_note(p, "question", f"question {i}", actor="test")
    block = core.context_block(p)
    key = block.split("## Key information", 1)[1].split("\n## ", 1)[0]
    assert key.rstrip("\n").split("\n")[-1] == (
        "- … Omitted from current context: 3 older entries (the latest 12 of 15 are above) and "
        f"the rest of each entry cut at 300 characters (…) — read {p.journal} and find them by id")
    assert len(re.findall(r"^- e\d+ finding:", key, re.M)) == 12
    assert "Omitted" not in block.split("## Open questions and todos", 1)[1]
    assert block.count(str(p.journal)) == 1


def _main_status_text(st: dict, max_chars: int = 9000) -> str:
    """analytics_status's overview as main (bc77176) made it before Issue #11 PR-2, to compare with, with
    the lines Issue #12 PR-2 changed or added: the read the overview is (`read:`), the records the
    lists cannot show, a dataset's origin as the read judges it, a run's recording status, a chart's
    producer flags, the line of an id several lines share and a key entry's references. Those lines
    come from the kit's own helpers (their content is tested on its own); what this compares is
    that an overview within its length is these lines with only the omission notes added."""
    _one_line, versions_line, _fmt_int = core._one_line, core.versions_line, core._fmt_int
    CONTEXT_PROMPT_CHARS = core.CONTEXT_PROMPT_CHARS  # noqa: N806 - as main spelled it
    p, counts = st["project"], st["counts"]
    lines = [f"project: {p['title']} ({p['root']})", f"report: {st['report']}"]
    if st.get("source_revision"):
        lines.append(core._read_line(st))
    if p.get("goal"):
        lines.append(f"goal: {_one_line(p['goal'])}")
    if st.get("versions"):
        lines.append(f"environment: {versions_line(st['versions'])} — use APIs of these versions")
    lines += core._records_lines(st, Path(p["root"]) / "journal.jsonl")
    if p.get("context_truncated"):
        lines.append(f"CONTEXT.md has {p['context_chars']:,} characters above its first `## ` "
                     f"heading, and only {CONTEXT_PROMPT_CHARS:,} reach the system prompt — split "
                     "it into `## ` sections so the rest can be read on demand")
    if p.get("context_omitted"):
        lines.append("CONTEXT.md sections not in the system prompt (read them from the file when "
                     f"they matter): {', '.join(p['context_omitted'])} — move a section earlier or "
                     "mark its heading <!-- 항상 --> to pin it")
    lines.append(f"datasets ({counts['datasets']}) — query views: "
                 + (", ".join(st["views"]) or "none"))
    for ds in st["datasets"]:
        lines.append(f"  {ds['kind']:<7} {ds['name']}: {_fmt_int(ds.get('rows'))} rows × "
                     f"{ds.get('columns') or '?'} cols · {ds['path']} · {core.dataset_origin(ds)} · "
                     f"used by {len(ds.get('used_by') or [])} run(s)")
    lines.append(f"runs ({counts['runs']} total, {counts['runs_failed']} not ok; latest first):")
    for r in st["runs"]:
        extra = f" — {_one_line(r['error'], 100)}" if r.get("error") else ""
        if r.get("rerun_of"):
            extra += f" [rerun of {r['rerun_of']}: {r.get('verdict')}]"
        if r.get("recording_status") == "incomplete":
            extra += f" · recording incomplete ({', '.join(r.get('reasons') or [])})"
        dur = f"{r['duration_s']:.1f}s" if isinstance(r.get("duration_s"), (int, float)) else "-"
        lines.append(f"  {r['id']} {r.get('status')} {dur} charts={r['charts']} "
                     f"\"{_one_line(r.get('title'), 80)}\"{extra}")
    lines.append(f"charts (latest version per script::chart, {counts['charts']}):")
    for c in st["charts"]:
        flags = []
        if c["stale"]:
            flags.append("STALE(" + core._stale_reasons(c["changed_inputs"] + c["stale_upstream"],
                                                        len(c["changed_inputs"])) + ")")
        if c.get("ambiguous_inputs"):
            flags.append(f"producer ambiguous for {len(c['ambiguous_inputs'])} input(s)")
        if c.get("cycle"):
            flags.append("producer cycle")
        if c["code"] == "changed":
            flags.append("script edited since")
        elif c["code"] == "missing":
            flags.append("script file no longer exists")
        if not c["has_data"] and c.get("kind") == "matplotlib":
            flags.append("plotted data not recorded")
        lines.append(f"  {c['key']} \"{_one_line(c.get('title'), 70)}\" run {c['run']} "
                     f"v{c['versions']}" + (" · " + "; ".join(flags) if flags else ""))
    name = core._entry_name
    if st["open"]:
        lines.append(f"open questions/todos ({counts['open']}):")
        lines += [f"  {name(e)} {e['kind']}: {_one_line(e['text'])}" for e in st["open"]]
    if st["key"]:
        lines.append("key information (findings, decisions, assumptions):")
        lines += [f"  {name(e)} {e['kind']}: {_one_line(e['text'])}"
                  f"{core._references_text(e.get('references'))}" for e in st["key"]]
    lines.append("recent journal:")
    lines += [f"  {name(e)} {str(e.get('ts', ''))[:16].replace('T', ' ')} {e['kind']}: "
              f"{_one_line(e['text'], 120)}" for e in st["recent"]]
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars - 40] + "\n… (truncated)"


def _datasets(p, n: int, prefix: str = "part") -> list[dict]:
    """`n` imported datasets as `analytics import` leaves them: a parquet file (a marker: nothing here
    reads its content) and its meta.json."""
    p.raw.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        name = f"{prefix}_{i:03d}"
        (p.raw / f"{name}.parquet").write_bytes(b"PAR1 marker")
        meta = {"schema": "lab.dataset/1", "name": name, "path": f"data/raw/{name}.parquet",
                "sha256": core.sha256_file(p.raw / f"{name}.parquet"), "rows": 100,
                "columns": [{"name": "v", "dtype": "Int64"}], "description": f"table {i}",
                "source": {"path": f"/sources/{name}.csv"}}
        core.write_json(p.raw / f"{name}.meta.json", meta)
        out.append(meta)
    return out


def _scale_project(root: Path, events: int, datasets: int, runs: int):
    """The scale fixtures of docs/BETA-QUALITY-GATES.md (tools/usage_report.py's SC-S and SC-M):
    journal lines and run records written in the kit's formats."""
    p = core.init_project(root, title=f"scale {events}", goal="compare regional growth",
                          actor="test")
    tables = _datasets(p, datasets, "t")
    kinds = ("finding", "note", "question", "decision", "todo")
    start = core._last_seq(p.journal)
    with open(p.journal, "ab") as fh:
        fh.write("".join(json.dumps({
            "schema": "lab.event/1", "seq": start + i + 1, "id": f"e{start + i + 1}",
            "ts": f"2026-09-01T09:{i // 60 % 60:02d}:{i % 60:02d}.000", "kind": kinds[i % 5],
            "actor": "test", "text": f"entry {i}: region {i % 17} grew {i % 9}% apart",
            "refs": {}}, ensure_ascii=False) + "\n" for i in range(events)).encode())
    for i in range(runs):
        run_id = f"20260901-{9 + i // 3600:02d}{(i // 60) % 60:02d}{i % 60:02d}-run-{i:03d}"
        ds = tables[i % datasets]
        (p.runs / run_id).mkdir(parents=True)
        core.write_json(p.runs / run_id / "run.json", {
            "schema": "lab.run/1", "id": run_id, "title": f"run {i}", "status": "ok",
            "started_at": f"2026-09-01T09:{i // 60 % 60:02d}:{i % 60:02d}", "duration_s": 1.0,
            "script": {"path": f"scripts/s{i % 10}.py", "sha256": "0" * 64},
            "inputs": [{"path": ds["path"], "sha256": ds["sha256"], "via": ["lab.read"]}],
            "outputs": [], "charts": [{"id": f"c{i % 10}", "title": f"chart {i % 10}",
                                       "kind": "matplotlib", "via": "lab.chart", "data": None,
                                       "files": {}}], "warnings": []})
    return p


@pytest.mark.parametrize("shape", ["B", "S", "M"])
def test_an_overview_within_its_length_is_mains_with_counts_and_places_added(tmp_path, shape):
    """Issue #11 G4 and design section 18: an overview of at most 9,000 characters shortens only
    the lists main shortened, and is main's text but for the lines that say how many entries a
    list leaves out and where they are (the project as analytics init leaves it; scale S and M)."""
    if shape == "B":
        p = core.init_project(tmp_path / "b", title="새 분석", goal="측정", actor="test")
    else:
        p = _scale_project(tmp_path / shape, *{"S": (300, 5, 30), "M": (10_000, 20, 300)}[shape])
    st = core.status(p)
    main = _main_status_text(st)
    assert len(main) <= core.STATUS_CHARS and not main.endswith("… (truncated)"), len(main)
    text = core.status_text(st)
    added = [line for line in text.split("\n") if line.startswith(("  … ", "journal: "))]
    assert [line for line in text.split("\n") if line not in added] == main.split("\n")
    assert len(text) == len(main) + sum(len(line) + 1 for line in added)
    if shape == "B":
        assert not added
        return
    assert added[0] == f"journal: {p.journal}"
    counts = st["counts"]
    assert f"  … {counts['runs'] - 8:,} older run(s) not listed: in the journal, and each in " \
           "runs/<id>/run.json" in added
    assert f"  … {counts['open'] - 16:,} older open item(s) not listed: in the journal" in added
    assert "  … only the latest 16 are listed; older unresolved ones, if any, are in the journal" \
        in added
    assert added[-1] == f"  … {counts['events'] - 10:,} earlier entries not listed: in the journal"


# The lines of an overview STATUS_CHARS does not count: the journal's path, the count of what a list
# status() limits leaves out, and what CONTEXT.md's text above its first heading leaves out.
STATUS_NOTE = re.compile(r"^(?:journal: |  … (?![\d,]+ more dataset)|CONTEXT\.md \([\d,]+ "
                         r"characters\) )")


def _budgeted(text: str) -> int:
    return len("\n".join(line for line in text.split("\n") if not STATUS_NOTE.match(line)))


def _overviews(tmp_path):
    """Overviews of one scale-S project (300 entries, 30 runs) as it would be with its first n of
    60 datasets, n = 0, 1, …: (n, the status, main's overview, this overview)."""
    p = _scale_project(tmp_path / "p", 300, 60, 30)
    full = core.status(p)
    names = list(full["views"])
    for n in range(len(full["datasets"]) + 1):
        st = {**full, "datasets": full["datasets"][:n], "views": names[:n],
              "counts": {**full["counts"], "datasets": n}}
        yield n, st, _main_status_text(st, max_chars=10 ** 9), core.status_text(st)


def test_an_overview_main_showed_whole_is_not_shortened_for_the_lines_added(tmp_path):
    """Issue #11 PR-2 review (Codex): the lines that say what a list leaves out and where are
    budgeted apart from the 9,000 characters. With 30 runs and the datasets that bring main's
    overview within those lines of 9,000 characters, every dataset is still listed, and the text
    is main's with those lines added."""
    def added(text: str) -> list[str]:
        return [line for line in text.split("\n") if line.startswith(("  … ", "journal: "))]

    found = next(((n, main, text) for n, _st, main, text in _overviews(tmp_path)
                  if len(main) <= core.STATUS_CHARS
                  < len(main) + sum(len(line) + 1 for line in added(text))), None)
    assert found, "no overview within the added lines of 9,000 characters"
    n, main, text = found
    assert [line for line in text.split("\n") if line not in added(text)] == main.split("\n")
    assert not [line for line in added(text) if "dataset" in line]
    assert sum(1 for line in text.split("\n") if line.startswith("  raw ")) == n


def test_an_overview_just_over_its_length_keeps_as_many_entries_as_fit(tmp_path):
    """Issue #11 PR-2 review: an overview a little longer than 9,000 characters loses the datasets
    it has to, not all but 20 of them: the list keeps the most that fit."""
    found = next(((n, st, text) for n, st, main, text in _overviews(tmp_path)
                  if _budgeted(main) > core.STATUS_CHARS), None)
    assert found, "no overview over 9,000 characters"
    n, st, text = found
    assert _budgeted(text) <= core.STATUS_CHARS
    more = re.search(r"^  … ([\d,]+) more dataset\(s\) not listed: call analytics_query with ", text,
                     re.M)
    shown = sum(1 for line in text.split("\n") if line.startswith("  raw "))
    assert more and shown + int(more.group(1)) == n, text[-2000:]
    assert core.STATUS_FLOORS["datasets"] < shown and n - shown <= 4, (n, shown)
    # One more would not fit.
    assert core._counted(core._status_lines(st, {"datasets": shown + 1})) > core.STATUS_CHARS


def test_an_overview_too_long_shortens_its_unbounded_lists_and_says_how_many_and_where(tmp_path):
    """Issue #11 G4 (D2), the shape of B1-a: 200 sections with descriptive headings left out of
    the prompt and 503 datasets. Every part keeps its heading, each list it shortens says how many
    it leaves out and where they are, and no line is cut."""
    p = core.init_project(tmp_path / "p", actor="test")
    heading = "Section {i:03d}: definition of the monthly active user metric and its denominator"
    p.context.write_text("# T\n\n## Goal <!-- always -->\n- g\n\n## Background\n" + "b" * 3900
                         + "\n\n" + "\n\n".join(f"## {heading.format(i=i)}\n" + "- x\n" * 20
                                                for i in range(200)) + "\n", encoding="utf-8")
    _datasets(p, 503)
    core.add_note(p, "question", "an open question", actor="test")
    core.add_note(p, "finding", "a key finding", actor="test")
    st = core.status(p)
    full = core.status_text(st, max_chars=10 ** 9)
    assert len(full) > core.STATUS_CHARS
    text = core.status_text(st)
    assert _budgeted(text) <= core.STATUS_CHARS
    lines = text.split("\n")
    for head in ("datasets (503)", "runs (", "charts (", "open questions/todos (1):",
                 "key information", "recent journal:"):
        assert any(line.startswith(head) for line in lines), head
    full_lines = set(full.split("\n"))
    changed = [line for line in lines if line not in full_lines]
    assert len(changed) == 3, changed  # the sections left out, the views, the datasets
    for line in changed:
        assert re.search(r"\b[\d,]+ more\b", line), line
        assert re.search(r"\bread \S*[/\\]|\bcall analytics_query with\b", line), line
    # The datasets save the most, then the sections: both are at their floors and it is still too
    # long. The views come last and keep the most names that fit.
    assert f"and 180 more (the system prompt lists every one with its line range in read " \
           f"{p.context})" in text
    paging = (f"call analytics_query with \"{core.VIEWS_SQL}\" and again with OFFSET 50, 100, … until "
              "it returns fewer than 50 rows")
    assert f"  … 483 more dataset(s) not listed: {paging} to list every view; an imported one's " \
           "record is data/raw/<name>.meta.json" in lines
    views = re.search(r"^datasets \(503\) — query views: (.+) and ([\d,]+) more \((.+)\)$", text,
                      re.M)
    shown = views.group(1).split(", ")
    assert shown == [f"part_{i:03d}" for i in range(len(shown))]
    assert len(shown) + int(views.group(2)) == 503 and len(shown) > core.STATUS_FLOORS["views"]
    assert views.group(3) == f"{paging} to list every view"
    assert core.VIEWS_SQL == ("SELECT table_name FROM information_schema.tables ORDER BY "
                              "table_name LIMIT 50 OFFSET 0")


def test_an_overview_that_still_does_not_fit_ends_at_a_line_and_names_what_it_leaves_out(
        tmp_path):
    """Issue #11 G4 (D2), an extreme shape: 503 datasets, 16 charts of a run whose 503 inputs are
    gone (each chart STALE with all of them), 42 key entries and 23 open items with long texts.
    Shortened, it is still too long: it ends at a line end, and its last line names every part it
    leaves out, with how many entries, and where to read them."""
    p = core.init_project(tmp_path / "p", actor="test")
    _datasets(p, 503)
    run_id = "20260901-090000-many-inputs"
    (p.runs / run_id).mkdir(parents=True)
    core.write_json(p.runs / run_id / "run.json", {
        "schema": "lab.run/1", "id": run_id, "title": "many inputs", "status": "ok",
        "started_at": "2026-09-01T09:00:00", "duration_s": 1.0,
        "script": {"path": "scripts/many.py", "sha256": "0" * 64},
        "inputs": [{"path": f"data/raw/gone/input_{i:03d}.jsonl", "sha256": "1" * 64,
                    "via": ["lab.read"]} for i in range(503)],
        "outputs": [], "warnings": [],
        "charts": [{"id": f"chart-{i:02d}", "title": f"chart {i}", "kind": "matplotlib",
                    "via": "lab.chart", "data": None, "files": {}} for i in range(16)]})
    for i in range(42):
        core.add_note(p, "finding", f"finding {i:02d} " + "evidence " * 50, actor="test")
    for i in range(23):
        core.add_note(p, "question", f"question {i:02d} " + "detail " * 50, actor="test")
    st = core.status(p)
    assert all(len(c["changed_inputs"]) == 503 for c in st["charts"])
    text = core.status_text(st)
    assert _budgeted(text) <= core.STATUS_CHARS
    full_lines = set(core.status_text(st, max_chars=10 ** 9).split("\n"))
    *kept, last = text.split("\n")
    assert last.startswith(f"… (truncated at {core.STATUS_CHARS:,} characters — not shown: "), last
    assert last.endswith(f"read {p.journal})"), last
    for line in kept:
        assert line in full_lines or re.search(r"\b[\d,]+ more\b", line), line
    assert any(f"and 498 more, among the run's inputs in runs/{run_id}/run.json)" in line
               for line in kept)
    for head, label in (("charts (", "charts"), ("open questions/todos", "open questions/todos"),
                        ("key information", "key information"), ("recent journal:",
                                                                 "recent journal")):
        if not any(line.startswith(head) for line in kept):
            assert re.search(rf"\bthe {re.escape(label)} \([\d,]+ listed\)", last), (label, last)
    assert not any(line.startswith("recent journal:") for line in kept)


@pytest.mark.parametrize("extra", [0, 1, 2])
def test_a_log_tail_starts_at_a_whole_character(tmp_path, extra):
    """Issue #11 G6 (D8): the kit keeps a log's last 4,000 bytes; where they begin inside a
    character (here each of the three bytes of a Korean one), the tail starts at the next whole
    character, after the "…" that says the start is left out, never with U+FFFD."""
    log = tmp_path / "stdout.txt"
    data = "진행 로그 — 합계\n".encode() * 600 + b"x" * extra
    log.write_bytes(data)
    tail = runner._tail(log)
    assert tail.startswith("…") and "�" not in tail
    start = len(data) - runner.OUTPUT_TAIL
    while 0x80 <= data[start] <= 0xBF:
        start += 1
    assert tail == "…" + data[start:].decode("utf-8")
    short = tmp_path / "short.txt"
    short.write_bytes("짧은 로그\n".encode())
    assert runner._tail(short) == "짧은 로그\n"


# --- Issue #9 PR-1: the kit as the package aelix_analytics -----------------------------------------


def _tree(root: Path) -> dict:
    import hashlib

    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _module(pythonpath: Path, *args, cwd=None):
    """`python -m <args>` with exactly `pythonpath` as the PYTHONPATH (not this checkout's src)."""
    import subprocess
    import sys

    from conftest import kit_env

    done = subprocess.run([sys.executable, "-m", *map(str, args)], capture_output=True,
                          env=kit_env(PYTHONPATH=str(pythonpath)), cwd=cwd, timeout=300,
                          **_platform.text_kwargs())
    lines = [line for line in done.stdout.split("\n") if line.startswith("{")]
    return done.returncode, (json.loads(lines[-1]) if lines else None), done


def test_python_m_runs_only_from_a_runtime_root(tmp_path):
    """Owner's decision of 2026-10-06 (Issue #9, ADR-0005 confirmation 1): the installed
    distribution's own package — the one with the runtime root's copy as kit/ beside it — refuses
    `python -m aelix_analytics`, and so does that copy run under another name; the runtime root on
    PYTHONPATH runs. Laid out here as the wheel installs it, without a build: site/aelix_analytics is
    the package, site/aelix_analytics/kit the runtime root. Nothing is written by a refused call: not
    the project, not the trust list, not a directory `init` was asked to make."""
    import shutil

    from conftest import KIT

    site = tmp_path / "site"
    ignore = shutil.ignore_patterns("__pycache__")
    shutil.copytree(KIT / "aelix_analytics", site / "aelix_analytics", ignore=ignore)
    shutil.copytree(KIT / "aelix_analytics", site / "aelix_analytics" / "kit" / "aelix_analytics",
                    ignore=ignore)
    root = site / "aelix_analytics" / "kit"
    project = tmp_path / "proj"
    code, made, done = _module(root, "aelix_analytics", "init", project, "--title", "실행 루트")
    assert code == 0 and made["ok"], done.stderr
    trust = Path(os.environ["AELIX_ANALYTICS_TRUST_FILE"])
    before = (_tree(project), trust.read_bytes())
    fresh = tmp_path / "fresh"
    for path, args in ((site, ["aelix_analytics", "--project", project, "status"]),
                       (site, ["aelix_analytics", "--project", project, "record", "finding", "x"]),
                       (site, ["aelix_analytics", "init", fresh]),
                       (site, ["aelix_analytics.kit.aelix_analytics", "--project", project, "status"])):
        code, answer, done = _module(path, *args, cwd=tmp_path)
        assert code == 1, (args, done.stdout, done.stderr)
        assert answer["ok"] is False and answer["error"]["code"] == "not_runtime_root", answer
        hint = answer["error"]["hint"]
        assert "analytics --help" in hint and "PYTHONPATH" in hint and "kit_dir()" in hint, hint
        assert "uv run --project <project> python -m aelix_analytics" in hint, hint
    assert (_tree(project), trust.read_bytes()) == before
    assert not fresh.exists()
    code, status, done = _module(root, "aelix_analytics", "--project", project, "status")
    assert code == 0 and status["ok"], done.stderr
    code, status, done = _module(KIT, "aelix_analytics", "--project", project, "status")
    assert code == 0 and status["ok"], done.stderr


def test_a_script_that_imports_labkit_is_told_the_new_name(project):
    """ADR-0005 decision 9: no `labkit` alias. A script or a run snapshot from before the rename
    fails with a hint that names the new import and the kit version before which the old name
    worked; a rerun of such a run records its failure as a rerun and leaves the snapshot's bytes
    as they were. Only that failed import gets the hint."""
    body = "import labkit as lab\nprint(lab.read('sales').height)\n"
    code, result = _run(project, write_script(project, "01_old_name.py", body), "옛 이름")
    assert code == 3 and result["run"]["status"] == "error", result
    error = result["run"]["error"]
    assert error["summary"] == "ModuleNotFoundError: No module named 'labkit'", error
    assert "import aelix_analytics as analytics" in error["hint"], error
    assert f"before kit {runner.SDK_RENAMED_IN}" in error["hint"], error
    assert runner.SDK_RENAMED_IN == "0.2.0"
    run_id = result["run"]["id"]
    before = _tree(project / "runs" / run_id)
    code, again = kit_cli(project, "run", "--rerun-of", run_id, "--title", "다시")
    assert code == 3 and again["run"]["rerun_of"] == run_id, again
    assert again["run"]["error"]["hint"] == error["hint"], again["run"]["error"]
    assert _tree(project / "runs" / run_id) == before
    with pytest.raises(ModuleNotFoundError) as caught:
        __import__("labkit.core")
    assert runner._error_info(caught.value, "x")["hint"] == error["hint"]
    # Only the old module's failed import gets it: not an error that quotes the words, not another
    # module (Issue #9 PR-1 review).
    for summary in ("ValueError: No module named 'labkit' in the supplied log",
                    "ModuleNotFoundError: No module named 'labkitx'",
                    "ModuleNotFoundError: No module named 'labkit_tools'"):
        assert runner.api_hint(summary) is None, summary
    body = "raise ValueError(\"No module named 'labkit' in the supplied log\")\n"
    code, quoted = _run(project, write_script(project, "02_quotes_it.py", body), "인용")
    assert code == 3 and quoted["run"]["error"]["type"] == "ValueError", quoted
    assert "hint" not in quoted["run"]["error"], quoted["run"]["error"]


# What a project's records are called until the new writer (ADR-0005 decision 2, #13). #9 and #10
# change public names only: this list stays as it is through them.
STORED = {"marker": "lab.json", "managed": ".lab", "report": "report", "imported": "data/raw",
          "families": {"lab.project/1", "lab.run/1", "lab.event/1", "lab.dataset/1"},
          "trust": "lab.trust/1",
          "via": {"lab.read", "lab.scan", "lab.sql", "lab.save", "lab.chart"}}

# Every SDK call that records something, so a run of it records each `via` of STORED.
SDK_CALLS = '''import matplotlib.pyplot as plt
import polars as pl

import aelix_analytics as analytics

rows = analytics.read("sales").height
counted = analytics.scan("sales").select(pl.len()).collect().item()
by_region = analytics.sql("SELECT region, sum(revenue) AS revenue FROM sales GROUP BY 1 ORDER BY 1").pl()
analytics.save(by_region, "region_revenue")
fig, ax = plt.subplots()
ax.bar(by_region["region"], by_region["revenue"])
analytics.visualize(fig, "region-revenue", data=by_region)
print(rows, counted)
'''


def test_storage_names_stay_until_the_new_writer(project, tmp_path):
    """ADR-0005 confirmation 1: the stored names do not change with the public ones. A project as an
    earlier kit left it (its marker says kit 0.1.0, as every kit before the rename wrote) is read and
    written by this kit — status, a note, a run that makes every SDK call that records (read, scan,
    sql, save, chart), the report — and what was there keeps its bytes: the marker, the data, the
    runs, the scripts and CONTEXT.md as they were, the journal only appended to. Everything this kit
    writes uses the names and families of STORED, and the run records exactly STORED's `via` values."""
    marker = json.loads((project / STORED["marker"]).read_text(encoding="utf-8"))
    assert marker["schema"] == "lab.project/1"
    marker["kit_version"] = "0.1.0"
    (project / STORED["marker"]).write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    kept = {rel: digest for rel, digest in _tree(project).items()
            if rel.split("/")[0] in ("lab.json", "data", "runs", "scripts", "CONTEXT.md")}
    journal = (project / "journal.jsonl").read_bytes()

    assert kit_cli(project, "status")[0] == 0
    assert kit_cli(project, "record", "finding", "새 kit의 기록")[0] == 0
    code, result = _run(project, write_script(project, "01_new_kit.py", SDK_CALLS), "새 kit")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert kit_cli(project, "render")[0] == 0

    after = _tree(project)
    assert {rel: after.get(rel) for rel in kept} == kept
    assert (project / "journal.jsonl").read_bytes().startswith(journal)
    assert json.loads((project / STORED["marker"]).read_text(encoding="utf-8"))["kit_version"] == "0.1.0"
    events = [json.loads(line) for line in
              (project / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {e["schema"] for e in events} == {"lab.event/1"}
    run = json.loads((project / "runs" / result["run"]["id"] / "run.json").read_text(encoding="utf-8"))
    assert run["schema"] == "lab.run/1" and run["env"]["kit_version"] == core.KIT_VERSION
    vias = ({v for i in run["inputs"] for v in i["via"]} | {o.get("via") for o in run["outputs"]}
            | {c["via"] for c in run["charts"]})
    assert {v for v in vias if v and v.startswith(("lab.", "analytics."))} == STORED["via"], vias
    chart_files = [f for c in run["charts"] for f in c["files"].values()]
    assert chart_files and all(f.startswith(f"runs/{run['id']}/charts/") for f in chart_files), \
        chart_files
    datasets = list((project / STORED["imported"]).glob("*.json"))
    assert {json.loads(p.read_text(encoding="utf-8"))["schema"] for p in datasets} == {"lab.dataset/1"}
    families = {marker["schema"], run["schema"], *(e["schema"] for e in events),
                *(json.loads(p.read_text(encoding="utf-8"))["schema"] for p in datasets)}
    assert families == STORED["families"]
    assert (project / STORED["managed"]).is_dir() and (project / STORED["report"] / "index.html").is_file()
    trust = json.loads(Path(os.environ["AELIX_ANALYTICS_TRUST_FILE"]).read_text(encoding="utf-8"))
    assert trust["schema"] == STORED["trust"]
    assert (core.MARKER, core.SCHEMA_PROJECT, core.SCHEMA_RUN, core.SCHEMA_EVENT,
            core.SCHEMA_DATASET) == ("lab.json", "lab.project/1", "lab.run/1", "lab.event/1",
                                     "lab.dataset/1")


# --- Issue #9 PR-2: the public names are analytics, and the old ones stop with the new name -------

# ADR-0005 decision 7's list: every variable the kit, its SDK, the command lines and the extension
# read under an old name, with the name TERMINOLOGY section 3 gives it.
ADR_RENAMED = {"LAB_PROJECT": "AELIX_ANALYTICS_PROJECT", "LAB_ACTOR": "AELIX_ANALYTICS_ACTOR",
               "LAB_SESSION": "AELIX_ANALYTICS_SESSION", "LAB_STYLE": "AELIX_ANALYTICS_STYLE",
               "AELIX_LAB_PROJECT": "AELIX_ANALYTICS_PROJECT",
               "AELIX_LAB_TRUST_FILE": "AELIX_ANALYTICS_TRUST_FILE",
               "AELIX_LAB_KIT": "AELIX_ANALYTICS_RUNTIME_PATH", "AELIX_LAB_UV": "AELIX_ANALYTICS_UV"}


def _kit_in(cwd, env_extra, *args):
    """`python -m aelix_analytics ARGS` from `cwd`, with this checkout's runtime root and `env_extra`
    on top of the tests' environment; (exit code, last JSON object or None, the process)."""
    import subprocess
    import sys

    from conftest import kit_env

    done = subprocess.run([sys.executable, "-m", "aelix_analytics", *map(str, args)],
                          capture_output=True, env=kit_env(**env_extra), cwd=cwd, timeout=300,
                          **_platform.text_kwargs())
    lines = [line for line in done.stdout.split("\n") if line.startswith("{")]
    return done.returncode, (json.loads(lines[-1]) if lines else None), done


def test_the_renamed_variables_are_the_adrs_list():
    from aelix_analytics import __main__ as kit_main

    assert core.RENAMED_ENV == ADR_RENAMED
    assert set(kit_main.OLD_ENV) <= set(ADR_RENAMED) and core.NAMES_RENAMED_IN == "0.3.0"
    assert core.KIT_VERSION == "0.4.0"


@pytest.mark.parametrize("old", ["LAB_PROJECT", "LAB_ACTOR", "LAB_SESSION", "LAB_STYLE",
                                 "AELIX_LAB_TRUST_FILE"])
def test_an_old_variable_stops_the_kit_before_it_writes(project, tmp_path, old):
    """ADR-0005 decision 7: the kit command line refuses each old name it, a run in it or init read,
    with env_renamed and the new name, also beside the new one, before anything is written: not the
    journal, not the trust list, not a new project."""
    new = ADR_RENAMED[old]
    trust = Path(os.environ["AELIX_ANALYTICS_TRUST_FILE"])
    before, trusted = _tree(project), (trust.read_bytes() if trust.exists() else None)
    extra = {old: "x", new: os.environ[new] if new in os.environ else "y"}
    for args in (["--project", project, "record", "finding", "x"], ["--project", project, "status"],
                 ["init", tmp_path / "new-project"]):
        code, result, done = _kit_in(tmp_path, extra, *args)
        assert code == 1 and result["error"]["code"] == "env_renamed", (args, done.stderr)
        assert result["error"]["details"] == {"renamed": {old: new}}
        assert result["error"]["message"] == core.renamed_env_message(old, remedy=False)
        assert new in result["error"]["message"] and f"unset {old}" in result["error"]["hint"]
    assert _tree(project) == before
    assert (trust.read_bytes() if trust.exists() else None) == trusted
    assert not (tmp_path / "new-project").exists()


def test_an_empty_old_variable_is_not_set(project, tmp_path):
    # The old readers took an empty value as unset; so does the check.
    code, result, done = _kit_in(tmp_path, {"LAB_ACTOR": "", "LAB_PROJECT": ""},
                                 "--project", project, "status")
    assert code == 0 and result["ok"], done.stderr


def test_the_trust_list_refuses_its_old_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("AELIX_LAB_TRUST_FILE", str(tmp_path / "old.json"))
    with pytest.raises(core.LabError) as raised:
        core.trust_file()
    assert raised.value.code == "env_renamed" and "AELIX_ANALYTICS_TRUST_FILE" in raised.value.message
    assert not (tmp_path / "old.json").exists()


def test_the_test_helpers_do_not_pass_on_an_old_variable(project, monkeypatch):
    """Design revision 3 (critic 16): a shell that still sets an old name must not fail the suite."""
    from conftest import OLD_ENV, kit_env

    for name in OLD_ENV:
        monkeypatch.setenv(name, "left over")
    assert not set(OLD_ENV) & set(kit_env())
    assert kit_cli(project, "status")[0] == 0


def test_one_project_variable_is_read_ahead_of_the_working_directory(project, tmp_path):
    """TERMINOLOGY section 3 makes LAB_PROJECT and AELIX_LAB_PROJECT one AELIX_ANALYTICS_PROJECT
    (design section 9, R6): set for aelix, it also picks the project of a command run inside another
    project. The record lands in the variable's project, and the other is untouched."""
    other = tmp_path / "other"
    core.init_project(other, title="다른 프로젝트", actor="test",
                      template_dir=Path(core.__file__).parent / "template")
    before = _tree(other)
    code, result, done = _kit_in(other, {"AELIX_ANALYTICS_PROJECT": str(project)},
                                 "record", "finding", "변수의 프로젝트에")
    assert code == 0 and result["ok"], done.stderr
    assert _tree(other) == before
    assert core.read_events(core.Project(project))[-1]["text"] == "변수의 프로젝트에"


@pytest.mark.parametrize("old", ["note", "report"])
def test_the_old_subcommands_are_usage_errors(project, tmp_path, old):
    before = _tree(project)
    code, result, done = _kit_in(tmp_path, {}, "--project", project, old, "finding", "x")
    assert code == 2 and result is None and "invalid choice" in done.stderr, done.stderr
    assert _tree(project) == before


def test_the_sdk_names_record_and_visualize_and_tells_the_old_ones(project):
    """note became record and chart became visualize (kit 0.3.0): the old names are not aliases.
    The attribute names the new one without importing anything, a run that uses it fails with that
    message and a hint (also for `from aelix_analytics import note`, whose ImportError cannot carry
    it), and what the new names record is what the old ones did (via lab.chart, kind finding)."""
    import subprocess
    import sys

    from conftest import kit_env

    probe = ("import json, sys\nimport aelix_analytics as a\nbefore = set(sys.modules)\nsaid = {}\n"
             "for name in ('note', 'chart', 'nothing'):\n"
             "    try:\n        getattr(a, name)\n    except AttributeError as exc:\n"
             "        said[name] = str(exc)\n"
             "print(json.dumps({'said': said, 'new': sorted(set(sys.modules) - before),\n"
             "                  'all': a.__all__, 'has': hasattr(a, 'note'), 'in': a._RENAMED_IN}))\n")
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, env=kit_env(),
                          timeout=120, **_platform.text_kwargs())
    seen = json.loads(done.stdout)
    assert seen["new"] == [] and seen["has"] is False and seen["in"] == core.NAMES_RENAMED_IN
    assert "aelix_analytics.note was renamed aelix_analytics.record in kit 0.3.0" in seen["said"]["note"]
    assert "aelix_analytics.chart was renamed aelix_analytics.visualize" in seen["said"]["chart"]
    assert seen["said"]["nothing"] == "module 'aelix_analytics' has no attribute 'nothing'"
    assert {"record", "visualize"} <= set(seen["all"]) and not {"note", "chart"} & set(seen["all"])

    for name, body in (("01_attr.py", "import aelix_analytics as analytics\nanalytics.note('finding', 'x')\n"),
                       ("02_from.py", "from aelix_analytics import chart\n")):
        code, result = _run(project, write_script(project, name, body), "옛 SDK 이름")
        assert code == 3 and result["run"]["status"] == "error", result
        error = result["run"]["error"]
        assert "renamed note to record and chart to visualize in kit 0.3.0" in error["hint"], error
    assert result["run"]["error"]["summary"].startswith(
        "ImportError: cannot import name 'chart' from 'aelix_analytics'")

    body = ("import matplotlib.pyplot as plt\nimport aelix_analytics as analytics\n"
            "fig, ax = plt.subplots()\nax.plot([1, 2])\nanalytics.visualize(fig, 'new-name')\n"
            "analytics.record('finding', '새 이름으로 기록')\n")
    code, result = _run(project, write_script(project, "03_new.py", body), "새 SDK 이름")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert [c["via"] for c in result["run"]["charts"]] == ["lab.chart"]
    last = core.read_events(core.Project(project))[-1]
    assert (last["kind"], last["text"]) == ("finding", "새 이름으로 기록")


def test_an_sdk_project_lookup_refuses_the_old_variable(project, monkeypatch):
    import aelix_analytics

    monkeypatch.setenv("LAB_PROJECT", str(project))
    with pytest.raises(RuntimeError, match="LAB_PROJECT was renamed AELIX_ANALYTICS_PROJECT"):
        aelix_analytics.project()
    monkeypatch.delenv("LAB_PROJECT")
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(project))
    assert aelix_analytics.project().root == core.Project(project).root


def test_the_kit_names_the_analytics_commands(project, tmp_path):
    """The kit cannot tell which entry point called it, and names the commands as `analytics`
    (Issue #9 comment, 2026-09-30): the hint for no project, the run page's rerun line, the
    template's .gitignore and the demo's refusal."""
    empty = tmp_path / "empty"
    empty.mkdir()
    code, result, _ = _kit_in(empty, {}, "status")
    assert code == 1 and result["error"]["hint"] == "create one with: analytics init DIR", result
    assert "no analytics project at or above" in result["error"]["message"]
    assert "rebuild with `analytics render`." in (project / ".gitignore").read_text(encoding="utf-8")
    code, ran = _run(project, write_script(project, "01_x.py", "print(1)\n"))
    assert code == 0
    kit_cli(project, "render")
    page = (project / "report" / "runs" / f"{ran['run']['id']}.html").read_text(encoding="utf-8")
    assert f"CLI:   analytics run --rerun-of {ran['run']['id']}" in page
    assert f'aelix: analytics_run(title="재실행 검증", rerun_of="{ran["run"]["id"]}")' in page
    code, refused = kit_cli(project, "demo")
    assert code == 1 and refused["error"]["hint"].startswith("analytics demo NEW_DIR"), refused
