"""#98: the Overview's analysis status, each cell from its own judgment and never one score, where
the analysis is going, and the lists six records each (design WORKSPACE-READABILITY 7.1)."""
from __future__ import annotations

import json
import re
from collections import Counter

from legacy_records import LegacyProject, at, sha
from workspace_words import plain

from aelix_analytics import core, relations, workspace, workspace_map

ORIGIN = core.Origin("human", "cli", "declared")
A, B, C, A2 = "20261001-090000-a", "20261001-091000-b", "20261001-092000-c", "20261001-093000-a"
RAW, T, U = "data/raw/in.parquet", "data/derived/t.parquet", "data/derived/u.parquet"


def status_project(tmp_path) -> tuple[core.Project, dict]:
    """Scripts a → b → c by the files their runs wrote and read; c's run failed; the input a's first
    run read changed afterwards, and a rerun of it compared. Six current insights, one of each
    evidence class (and a correction), two open items and one closed."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    q.run(A, script="scripts/a.py", started_at=at(0), ts=at(1), inputs=[(RAW, sha(b"in-1"))],
          outputs=[(T, q.file(T, b"t-1"))], charts=["c1"])
    q.run(B, script="scripts/b.py", started_at=at(10), ts=at(11), inputs=[(T, sha(b"t-1"))],
          outputs=[(U, q.file(U, b"u-1"))], charts=["c2"])
    q.run(C, script="scripts/c.py", status="error", started_at=at(20), ts=at(21), inputs=[(U, sha(b"u-1"))])
    q.file(RAW, b"in-2")
    q.run(A2, script="scripts/a.py", started_at=at(30), ts=at(31), inputs=[(RAW, sha(b"in-2"))])
    record = json.loads((q.root / "runs" / A2 / "run.json").read_text(encoding="utf-8"))
    record.update(rerun_of=A, reproduction={"of": A, "verdict": "match", "inputs_same": False,
                                            "charts": {}, "outputs": {}})
    q.write_json(f"runs/{A2}/run.json", record)
    p = core.Project(q.root)

    def rec(kind: str, text: str, *refs) -> str:
        return core.record(p, kind, text, origin=ORIGIN, refs=list(refs)).event["id"]

    def run(run_id: str, **more) -> dict:
        return {"type": "run", "run_id": run_id, "relation": "cites", **more}

    ids = {"confirmed": rec("finding", "a의 실행이 근거", run(A)),
           "found": rec("finding", "추정한 근거", run(B, basis="inferred")),
           "unbound": rec("decision", "버전을 정하지 않은 차트",
                          {"type": "visualization_version", "key": "scripts/a.py::c1", "relation": "cites",
                           "version_binding": "none"}),
           "problem": rec("assumption", "없는 실행", run("20261001-095900-gone")),
           "none": rec("finding", "근거 없음")}
    old = rec("finding", "정정 전", run(A))
    ids["correction"] = rec("finding", "실패한 실행을 인용", run(C),
                            {"type": "event", "event_id": old, "relation": "supersedes"})
    ids["question"] = rec("question", "왜 실패했나?")
    ids["todo"] = rec("todo", "다시 실행한다")
    closed = rec("question", "닫힌 질문")
    rec("resolve", "답", {"type": "event", "event_id": closed, "relation": "resolves"})
    return p, ids


def strongest(judgments) -> str:
    """The test's own reading of design 7.1's order, from the stored fields (ADR-0006 3)."""
    order = ["confirmed", "found", "unbound", "changed", "problem", "none"]
    best = len(order) - 1
    for j in judgments:
        if j.get("relation") in ("supersedes", "updates", "resolves"):
            continue
        state = j["state"]
        if j.get("version_binding") == "author" and j.get("basis") in ("observed", "declared") and state == "resolved":
            best = min(best, 0)
        elif state == "resolved":
            best = min(best, 1)
        elif state == "version_unbound":
            best = min(best, 2)
        elif state == "content_changed":
            best = min(best, 3)
        else:
            best = min(best, 4)
    return order[best]


def overview(p: core.Project) -> str:
    index = (p.workspace_dir / "index.html").read_text(encoding="utf-8")
    return plain(index.split('<section id="overview"', 1)[1].split("</section>", 1)[0])


def test_each_status_cell_counts_what_the_read_model_judges(tmp_path):
    """Acceptance 2 of #98: each cell's numbers are the read model's (reference states, run status,
    Freshness, record state, rerun verdicts), checked here against the read model itself."""
    p, ids = status_project(tmp_path)
    site = workspace.Site(p)
    counts = site.status.counts()
    assert counts == {"insights": 6, "evidence": {"confirmed": 2, "found": 1, "unbound": 1, "changed": 0,
                                                  "problem": 1, "none": 1},
                      "runs": {"ok": 3, "error": 1}, "changed": 1, "upstream": 1,
                      "open": {"question": 1, "todo": 1}, "reruns": 1, "verdicts": {"match": 1},
                      "checks": None, "reviews": None}
    # The same numbers, read from the read model by the test.
    model = core.read_model(p)
    lists = core.JournalLists(model.journal)
    by = {e["id"]: strongest(model.references(e["id"])) for e in lists.key}
    assert Counter(by.values()) == Counter({k: v for k, v in counts["evidence"].items() if v})
    assert by[ids["confirmed"]] == "confirmed" and by[ids["found"]] == "found" and by[ids["none"]] == "none"
    assert by[ids["unbound"]] == "unbound" and by[ids["problem"]] == "problem" and by[ids["correction"]] == "confirmed"
    runs = core.model_runs(p, model)
    assert Counter(r["status"] for r in runs) == Counter(counts["runs"])
    fresh = core.Freshness(p, runs, core.HashCache(p), model)
    charts = [c for c in core.chart_index(runs, model.read_as) if not c.get("retired")]
    assert sum(1 for c in charts if fresh.of(c["run"])["changed_inputs"]) == counts["changed"]
    assert sum(1 for c in charts if not fresh.of(c["run"])["changed_inputs"] and fresh.of(c["run"])["stale_upstream"]) == counts["upstream"]
    assert Counter(e["kind"] for e in lists.open) == Counter(counts["open"])
    assert Counter(relations.run_reproduction(r)["verdict"] for r in runs if relations.run_reproduction(r)) == Counter(counts["verdicts"])


def test_the_overview_shows_the_six_cells_apart_and_never_one_score(tmp_path):
    """Acceptance 1 of #98: the status is six cells, each saying its own state; nothing adds them up."""
    p, _ = status_project(tmp_path)
    workspace.build(p)
    page = overview(p)
    cells = re.findall(r'<article class="status-cell cell-(\w+)">', page)
    assert cells == ["evidence", "runs", "inputs", "open", "rerun", "review"]
    for words in ("2 of 6 current insights have a confirmed link", "2 confirmed · 1 found, not confirmed · "
                  "1 version not named · 1 not found or unclear · 1 cites nothing", "3 succeeded",
                  "2 visualizations rest on changed inputs", "1 was made from inputs that changed afterwards.",
                  "1 builds on a run whose input changed.", "2 open", "1 question · 1 next step.",
                  "1 run compared with its rerun", "Matched 1"):
        assert words in page, words
    review = page.split('cell-review">', 1)[1].split("</article>", 1)[0]
    assert review.count("None recorded") == 2
    assert not re.search(r"score|\d+ ?%", page, re.IGNORECASE)


def test_a_new_project_says_what_is_not_recorded_yet(tmp_path):
    core.init_project(tmp_path / "p", title="New", goal="Find out", origin=ORIGIN)
    p = core.Project(tmp_path / "p")
    workspace.build(p)
    page = overview(p)
    for cell, words in (("evidence", "None recorded"), ("runs", "None recorded"), ("inputs", "None recorded"),
                        ("open", "Nothing open"), ("rerun", "Not run"), ("review", "None recorded")):
        assert words in page.split(f'cell-{cell}">', 1)[1].split("</article>", 1)[0], cell
    # The new template pins Goal, Analysis focus and Next steps: the goal is the hero's, the others the panel's.
    direction = page.split('<aside class="direction panel">', 1)[1].split("</aside>", 1)[0]
    assert '<h3><span lang="">Analysis focus</span></h3>' in direction and '<h3><span lang="">Next steps</span></h3>' in direction
    assert "Goal</span></h3>" not in direction and '<p class="goal" lang="">Find out</p>' in page
    assert "Metrics: none defined yet." in direction


def test_the_lists_show_six_records_and_say_where_the_rest_are(tmp_path):
    core.init_project(tmp_path / "p", title="Lists", goal="많이 기록", origin=ORIGIN)
    p = core.Project(tmp_path / "p")
    context = p.context.read_text(encoding="utf-8").replace("## Analysis focus <!-- pin -->",
                                                            "## 핵심 질문 <!-- pin -->\n- 왜 줄었나?\n\n## Analysis focus")
    p.context.write_text(context, encoding="utf-8")
    findings = [core.record(p, "finding", f"발견 {n}", origin=ORIGIN).event["id"] for n in range(8)]
    asked = [core.record(p, "question", f"질문 {n}", origin=ORIGIN).event["id"] for n in range(8)]
    workspace.build(p)
    page = overview(p)
    key = page.split('id="overview-key"', 1)[1].split('id="overview-open"', 1)[0]
    assert re.findall(r'<li class="ev rec" id="key-(e\d+)"', key) == findings[:1:-1]
    assert "6 of 8 current" in key and '<a href="#insights">All insights (8)' in key
    open_panel = page.split('id="overview-open"', 1)[1]
    assert re.findall(r'<li class="ev rec" id="open-(e\d+)"', open_panel) == asked[:1:-1]
    assert "6 of 8 open" in open_panel and '<a href="lists/open-0.html">All 8 open' in open_panel
    rest = plain((p.workspace_dir / "lists" / "open-0.html").read_text(encoding="utf-8"))
    assert re.findall(r'<li class="ev rec" id="open-(e\d+)"', rest) == asked[::-1]
    assert 'data-back="back.tab.overview"' in rest
    # CONTEXT.md's pinned section, as written, beside the hero's goal.
    direction = page.split('<aside class="direction panel">', 1)[1].split("</aside>", 1)[0]
    assert '<h3><span lang="">핵심 질문</span></h3>' in direction and "왜 줄었나?" in direction


def test_tabs_say_how_many_items_their_sections_list(tmp_path):
    p, _ = status_project(tmp_path)
    workspace.build(p)
    index = (p.workspace_dir / "index.html").read_text(encoding="utf-8")
    counted = dict(re.findall(r'<section id="(\w+)" class="tab" data-count="(\d+)"', index))
    site = workspace.Site(p)
    assert counted == {"insights": str(sum(1 for e in site.events if e.get("kind") in core.KEY_KINDS)),
                       "charts": "2", "timeline": str(len(site.events)), "data": str(len(site.datasets)),
                       "runs": "4", "evaluation": str(len(site.lists.feedback))}
    # The map's tab follows the Visualizations', and the Overview folds what it does not lead with.
    order = re.findall(r'<section id="(\w+)" class="tab"', index)
    assert order[:4] == ["overview", "insights", "charts", "map"]
    folds = re.findall(r'<details class="fold"( open)?><summary><h2>(.*?)</h2>', index)
    assert [(bool(opened), re.sub("<[^>]+>", "", words)) for opened, words in folds] == [
        (True, "Recent visualizations"), (False, "Recent activity"), (False, "Record coverage and workflow feedback")]


def test_evidence_classes_are_ranked_strongest_first():
    judged = [{"relation": "cites", "state": "version_unbound"}, {"relation": "cites", "state": "resolved"},
              {"relation": "supersedes", "state": "resolved", "confirmed": True}]
    assert workspace_map.evidence_class(judged) == "found"
    assert workspace_map.evidence_class(judged + [{"relation": "cites", "state": "resolved", "confirmed": True}]) == "confirmed"
    assert workspace_map.evidence_class([{"relation": "supersedes", "state": "resolved", "confirmed": True}]) == "none"
    assert workspace_map.evidence_class([{"relation": "cites", "state": "missing"}]) == "problem"
