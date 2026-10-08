"""One view of a run's Visualizations and of its rerun comparison, whichever record holds them
(Issue #13 PR 1, design RECORD-WRITER-AND-MIGRATION 2.6 and 5.3, S13, T9).

A lab.run/1 run holds its Visualizations as charts[] (each named by `id`) and a rerun's comparison as
reproduction.charts; an analytics.run/2 run — the new writer's, PR 3 — as visualizations[] (by
`visualization_id`, with the sha256 of the stored file) and reproduction.visualizations. Every
consumer reads them through relations.run_visualizations and relations.run_reproduction, so a project
holding both forms shows the same Visualizations everywhere and a rerun compares the same ids across
the two forms; a static check keeps a new consumer from reading one form only. The extension's run
reply is checked in tests/test_extension.py.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest
from legacy_records import LegacyProject, at

from aelix_analytics import core, relations, runner, workspace

PACK = Path(__file__).resolve().parents[1]
FIGURE = {"data": [{"type": "bar", "x": ["a", "b"], "y": [1, 2]}], "layout": {}}


def write_v2_run(p: LegacyProject, run_id: str, *, script: str = "scripts/a.py", ids=("trend",),
                 reproduction=None, figure=FIGURE) -> dict:
    """An analytics.run/2 run as design 2.6 defines it, with its /2 completion event."""
    visualizations = []
    for vis_id in ids:
        rel = f"runs/{run_id}/visualizations/{vis_id}.plotly.json"
        digest = p.file(rel, json.dumps(figure).encode())
        visualizations.append({"visualization_id": vis_id, "kind": "plotly", "title": vis_id,
                               "caption": None, "via": "analytics.visualize",
                               "files": {"plotly": rel}, "sha256": digest, "line": 3})
    snapshot = f"runs/{run_id}/code/{script}"
    code = p.file(snapshot, b"# v2\n")
    record = {"schema": "analytics.run/2", "id": run_id, "title": run_id, "status": "ok",
              "started_at": at(p.minute % 60), "ended_at": at(p.minute % 60), "actor": "human",
              "actor_basis": "inferred", "means": "cli", "session": None, "rerun_of": None,
              "script": {"path": script, "sha256": code, "snapshot": snapshot}, "modules": [],
              "inputs": [], "outputs": [], "visualizations": visualizations,
              "env": {"kit_version": "0.8.0"}}
    if reproduction is not None:
        record["rerun_of"] = reproduction["of"]
        record["reproduction"] = reproduction
    p.write_json(f"runs/{run_id}/run.json", record)
    p.seq += 1
    p.minute += 1
    p.line({"schema": "analytics.event/2", "seq": p.seq, "id": f"e{p.seq}", "recorded_at": at(p.minute % 60),
            "kind": "run", "actor": "human", "text": f"{run_id} — ok",
            "refs": [{"type": "run", "run_id": run_id, "relation": "about", "basis": "observed",
                      "version_binding": "author"}]})
    return record


def mixed(tmp_path) -> tuple[LegacyProject, str, str]:
    """A lab.json project holding a /1 run and a /2 run of one script, each with Visualization
    `trend` (the /1 one's plotly file has the same data)."""
    p = LegacyProject(tmp_path / "p")
    old = p.run("20261001-090000-a", charts=["trend"])
    p.file(f"runs/{old}/charts/trend.plotly.json", json.dumps(FIGURE).encode())
    new = "20261001-100000-a"
    write_v2_run(p, new)
    return p, old, new


def test_the_view_reads_both_forms_alike(tmp_path):
    p, old, new = mixed(tmp_path)
    v1 = json.loads((p.root / "runs" / old / "run.json").read_text(encoding="utf-8"))
    v2 = json.loads((p.root / "runs" / new / "run.json").read_text(encoding="utf-8"))
    (one,), (two,) = relations.run_visualizations(v1), relations.run_visualizations(v2)
    assert (one["id"], one["key"], one["field"]) == ("trend", "scripts/a.py::trend", "charts")
    assert (two["id"], two["key"], two["field"]) == ("trend", "scripts/a.py::trend", "visualizations")
    assert "sha256" not in one and two["sha256"] == v2["visualizations"][0]["sha256"]
    assert relations.run_visualizations({"schema": "analytics.run/2", "charts": [{"id": "x"}]}) == []
    assert relations.run_visualizations({"schema": "lab.run/1", "visualizations": [{"id": "x"}]}) == []
    assert relations.run_reproduction(v1) is None
    rep1 = relations.run_reproduction({"schema": "lab.run/1", "reproduction": {
        "of": old, "verdict": "match", "inputs_same": True, "charts": {"trend": "same data"},
        "outputs": {"data/x.parquet": "same bytes"}}})
    rep2 = relations.run_reproduction({"schema": "analytics.run/2", "reproduction": {
        "of": old, "verdict": "match", "inputs_same": True, "visualizations": {"trend": "same data"},
        "outputs": {"data/x.parquet": "same bytes"}}})
    assert {k: v for k, v in rep1.items() if k != "field"} == {k: v for k, v in rep2.items() if k != "field"}
    assert (rep1["field"], rep2["field"]) == ("charts", "visualizations")
    assert rep1["visualizations"] == {"trend": "same data"}


def test_every_consumer_sees_both_forms(tmp_path):
    """T9: status, the Visualization index and the Workspace show the /1 run's and the /2 run's
    Visualizations as versions of one key, with the same counts."""
    p, old, new = mixed(tmp_path)
    project = core.Project(p.root)
    st = core.status(project)
    rows = {r["id"]: r for r in st["runs"]}
    assert rows[old]["charts"] == rows[new]["charts"] == 1
    (chart,) = st["charts"]
    assert (chart["key"], chart["run"], chart["versions"]) == ("scripts/a.py::trend", new, 2)
    model = core.read_model(project)
    assert model.visualization_versions("scripts/a.py::trend") == [f"{old}/trend", f"{new}/trend"]
    assert not model.unsupported
    workspace.build(project)
    page = (project.workspace_dir / "runs" / f"{new}.html").read_text(encoding="utf-8")
    assert "Visualizations (1)" in page and 'id="chart-trend"' in page
    assert "scripts/a.py::trend" in page and '"type": "bar"' in page
    index = (project.workspace_dir / "index.html").read_text(encoding="utf-8")
    assert f"runs/{new}.html#L-main-3" in index and ">v2</span>" in index


def test_a_rerun_compares_the_same_ids_across_the_two_forms(tmp_path):
    """S13: a lab.run/1 run re-executed by a kit that writes analytics.run/2 (or the other way
    round) is compared Visualization by Visualization — never every one `missing` and `new` because
    the two records name them differently."""
    p, old, new = mixed(tmp_path)
    project = core.Project(p.root)
    v1 = json.loads((p.root / "runs" / old / "run.json").read_text(encoding="utf-8"))
    v2 = json.loads((p.root / "runs" / new / "run.json").read_text(encoding="utf-8"))
    for before, after in ((v1, v2), (v2, v1)):
        result = runner.compare_runs(project, before, after)
        assert result["charts"] == {"trend": "same data"} and result["verdict"] == "match", result


def test_a_rerun_s_comparison_shows_on_its_page_in_both_forms(tmp_path):
    p, old, _ = mixed(tmp_path)
    write_v2_run(p, "20261001-110000-a", reproduction={
        "of": old, "verdict": "mismatch", "inputs_same": True,
        "visualizations": {"trend": "different data", "gone": "missing"}, "outputs": {}})
    project = core.Project(p.root)
    workspace.build(project)
    page = (project.workspace_dir / "runs" / "20261001-110000-a.html").read_text(encoding="utf-8")
    assert "Rerun comparison: Different" in page
    assert "<code lang=\"\">trend</code>: different data" in page
    assert "<code lang=\"\">gone</code>: missing" in page


def test_the_reader_reads_the_new_families_where_they_belong(tmp_path):
    """design 2.6–2.7 and 5.1: analytics.run/2 run records and analytics.dataset/1 import records
    under data/imported/ are read; a family found where it does not belong is a kind of record this
    reader does not know there (unknown_kind)."""
    p, _, new = mixed(tmp_path)
    p.write_json("data/imported/fresh.meta.json", {
        "schema": "analytics.dataset/1", "name": "fresh", "path": "data/imported/fresh.parquet",
        "sha256": p.file("data/imported/fresh.parquet", b"fresh"), "bytes": 5, "rows": 1,
        "columns": [], "description": None, "imported_at": at(50), "source": {"path": "/x.csv"}})
    p.write_json("data/imported/wrong.meta.json", {"schema": "lab.dataset/1", "name": "wrong"})
    p.write_json("data/raw/wrong.meta.json", {"schema": "analytics.dataset/1", "name": "wrong"})
    model = core.read_model(core.Project(p.root))
    assert model.run(new)["schema"] == "analytics.run/2"
    assert {u["location"]: u["reason"] for u in model.unsupported} == {
        "data/imported/wrong.meta.json": "unknown_kind", "data/raw/wrong.meta.json": "unknown_kind"}
    assert [i["location"] for i in model.imports()] == ["data/imported/fresh.meta.json"]
    datasets = core.list_datasets(core.Project(p.root))
    fresh = next(d for d in datasets if d["name"] == "fresh")
    assert (fresh["kind"], fresh["path"], fresh["sha256"]) == (
        "imported", "data/imported/fresh.parquet", hashlib.sha256(b"fresh").hexdigest())
    assert relations.READER_FORMAT == 2
    assert {"analytics.run/2", "analytics.dataset/1"} <= relations.KNOWN


def test_imported_data_takes_its_view_name_first(tmp_path):
    """design D24 (R13): a name in both data/imported and data/raw is data/imported's view; the
    other gets the next free name."""
    p = LegacyProject(tmp_path / "p")
    p.dataset("sales", imported_at=at(1))
    p.file("data/imported/sales.parquet", b"new")
    assert core.view_names(core.list_datasets(core.Project(p.root))) == {
        "sales": "data/imported/sales.parquet", "sales_2": "data/raw/sales.parquet"}
    text = core.status_text(core.status(core.Project(p.root)))
    assert ("dataset names in both data/imported and data/raw: sales (view sales: "
            "data/imported/sales.parquet; data/raw/sales.parquet is view sales_2)") in text


def test_a_reference_to_a_v2_visualization_is_judged_by_its_hash(tmp_path):
    """design 2.6: a visualization_version reference's digest is the Visualization's sha256, and
    an analytics.run/2 Visualization is a target that keeps a hash of its own."""
    p, _, new = mixed(tmp_path)
    digest = json.loads((p.root / "runs" / new / "run.json").read_text(encoding="utf-8"))[
        "visualizations"][0]["sha256"]
    model = core.read_model(core.Project(p.root))
    ref = {"type": "visualization_version", "run_id": new, "visualization_id": "trend"}
    assert model._resolve({**ref, "digest": digest})[:2] == ("resolved", None)
    assert model._resolve({**ref, "digest": "0" * 64})[:2] == ("missing", "digest_mismatch")
    (p.root / "runs" / new / "visualizations" / "trend.plotly.json").write_bytes(b"{}")
    assert core.read_model(core.Project(p.root))._resolve(ref)[0] == "content_changed"


# --- no consumer reads one form only (design 5.3, D20) -------------------------------------------

# Where `charts` is read as a key and it is not a run record's or a rerun comparison's: a /1 event's
# refs.charts (classify_refs, resolve_ref_aliases, the read model's /1 judgments), the status
# overview's own lists (st["charts"]), a run record this kit writes as lab.run/1 (the runner, the
# fixture generators that write the /1 records as the /1 writer does), and the two views themselves.
NOT_A_RUN_RECORD = {
    "src/aelix_analytics/relations.py": {"run_visualizations", "run_reproduction",
                                         "ReadModel._v1", "Replay._links"},
    "src/aelix_analytics/writer.py": {"classify_refs"},
    "src/aelix_analytics/core.py": {"resolve_ref_aliases", "_status_lines", "_status_list_len",
                                    "status_text"},
    "tools/usage_report.py": {"status_invariants"},
    # The tools' own summaries of runs, made through the views (V1Writer._remember, changes).
    "tools/eval_fixtures.py": {"ScaleFixture.finish"},
    "tools/model_eval.py": {"run_check"},
}
# Readers that take a field's name as an argument: a call of one with "charts" reads it too.
BY_NAME = {"run_items"}
CHECKED = sorted([*(PACK / "src" / "aelix_analytics").glob("*.py"), PACK / "extension" / "aelix_analytics.py",
                  *(PACK / "tools" / name for name in ("model_eval.py", "eval_fixtures.py",
                                                         "platform_smoke.py", "usage_report.py"))])


def charts_reads(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, enclosing function) of every read of a `charts` key: `x.get("charts" ...)`,
    `x["charts"]` read, and a BY_NAME reader called with "charts"."""
    found = []

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = f"{scope}.{child.name}" if scope else child.name
            if isinstance(child, ast.Call):
                func = child.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                args = [*child.args, *(k.value for k in child.keywords)]
                if name == "get" and child.args and _is_charts(child.args[0]):
                    found.append((child.lineno, inner))
                elif name in BY_NAME and any(_is_charts(a) for a in args):
                    found.append((child.lineno, inner))
            elif isinstance(child, ast.Subscript) and isinstance(child.ctx, ast.Load) \
                    and _is_charts(child.slice):
                found.append((child.lineno, inner))
            visit(child, inner)

    visit(tree, "")
    return found


def _is_charts(node) -> bool:
    return isinstance(node, ast.Constant) and node.value == "charts"


def test_no_consumer_reads_a_run_s_charts_outside_the_two_views():
    offenders = []
    for path in CHECKED:
        rel = path.relative_to(PACK).as_posix()
        allowed = NOT_A_RUN_RECORD.get(rel, set())
        for line, scope in charts_reads(ast.parse(path.read_text(encoding="utf-8"))):
            if scope not in allowed:
                offenders.append(f"{rel}:{line} in {scope or 'the module'}")
    assert not offenders, ("read relations.run_visualizations / run_reproduction instead:\n"
                           + "\n".join(offenders))


@pytest.mark.parametrize(("source", "found"), [
    ("def f(run):\n    return run.get('charts') or []", True),
    ("def f(run):\n    return run['charts']", True),
    ("def f(rep):\n    return rep.get('charts', {})", True),
    ("def f(run):\n    return run_items(run, 'charts')", True),
    ("def f(run):\n    run['charts'] = []", False),
    ("def f():\n    return {'charts': []}", False),
    ("def f(run):\n    return relations.run_visualizations(run)", False),
])
def test_the_check_finds_each_form(source, found):
    assert bool(charts_reads(ast.parse(source))) is found
