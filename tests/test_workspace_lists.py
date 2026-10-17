"""#97: a row per record, judgments as words, each kind's own list, the relation page as people read
a record, and the way back named (design WORKSPACE-READABILITY 8)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from legacy_records import LegacyProject, at
from workspace_words import badges, plain

from aelix_analytics import core, i18n, workspace, workspace_relations

ORIGIN = core.Origin("human", "cli", "declared")


def project(tmp_path):
    core.init_project(tmp_path / "p", title="Lists", goal="목록 확인", origin=ORIGIN)
    return core.Project(tmp_path / "p")


def page(p: core.Project, name: str) -> str:
    return plain((p.workspace_dir / name).read_text(encoding="utf-8"))


def section(index: str, name: str) -> str:
    return index.split(f'<section id="{name}"', 1)[1].split("</section>", 1)[0]


def rows(html: str) -> dict[str, str]:
    """Each record row by its id: `<li class="ev rec" id="…">…</li>`."""
    return dict(re.findall(r'<li class="ev rec" id="([^"]+)"[^>]*>(.*?)</li>', html))


def relation_page(p: core.Project, identity: str, n: int = 0) -> str:
    return page(p, "relations/" + workspace_relations.name(identity, n))


# --- rows -------------------------------------------------------------------------------------------

def test_an_insight_is_one_row_with_its_words_its_badges_and_one_way_to_its_relations(tmp_path):
    p = project(tmp_path)
    question = core.record(p, "question", "왜 줄었나?", origin=ORIGIN).event
    finding = core.record(p, "finding", "서울 매출이 줄었다", origin=ORIGIN,
                          refs=[{"type": "event", "event_id": question["id"], "relation": "cites"}]).event
    alone = core.record(p, "assumption", "반품은 없다", origin=ORIGIN).event
    workspace.build(p)
    insights = section(page(p, "index.html"), "insights")
    found = rows(insights)
    row = found["insight-" + finding["id"]]
    assert '<span class="rec-kind kind-finding">' in row and ">Finding</span>" in row
    assert '<span class="rec-text" lang="">서울 매출이 줄었다</span>' in row
    assert f'<span class="rec-id">{finding["id"]}</span> · <time datetime=' in row and ">human</span>" in row
    assert ("resolved", f"✓ Question {question['id']}") in badges(row)
    # One link to its relations, counted, and none of the three links it had (#95's density problem).
    assert re.search(r'<a class="rec-action" href="relations/[0-9a-f]{64}-0\.html">Relations 1</a>', row)
    for word in ("Evidence</a>", "Related items</a>", "History</a>", "State:"):
        assert word not in insights, word
    # A record that cites nothing says so, and its link opens the record itself.
    lone = found["insight-" + alone["id"]]
    assert ("muted", "No references recorded") in [(s, t) for s, t in [(m.group(1), m.group(2)) for m in
                                                    re.finditer(r'<span class="ref tone-(\w+)">([^<]+)</span>', lone)]]
    assert re.search(r'<a class="rec-action" href="relations/[0-9a-f]{64}-0\.html#record">Open</a>', lone)


def test_a_row_shows_its_state_only_when_it_is_not_as_it_was_made(tmp_path):
    p = project(tmp_path)
    corrected = core.record(p, "finding", "원래 발견", origin=ORIGIN).event
    correction = core.record(p, "finding", "정정한 발견", origin=ORIGIN,
                             refs=[{"type": "event", "event_id": corrected["id"], "relation": "supersedes"}]).event
    disputed = core.record(p, "finding", "갈린 발견", origin=ORIGIN).event
    for text in ("한쪽 정정", "다른 정정"):
        core.record(p, "finding", text, origin=ORIGIN,
                    refs=[{"type": "event", "event_id": disputed["id"], "relation": "supersedes"}])
    withdrawn = core.record(p, "decision", "철회할 결정", origin=ORIGIN).event
    core.writer_for(p, ORIGIN).update(withdrawn["id"], expected="current", new="withdrawn", text="철회")
    asked = core.record(p, "question", "열린 질문", origin=ORIGIN).event
    current = core.record(p, "finding", "그대로인 발견", origin=ORIGIN).event
    workspace.build(p)
    found = rows(section(page(p, "index.html"), "timeline"))
    row = found[corrected["id"]]
    assert re.search(rf'<a class="badge tone-muted" href="[^"]+"><span aria-hidden="true">↺</span> '
                     rf'Superseded by {correction["id"]}</a>', row)
    assert '<span aria-hidden="true">⚠</span> Corrected by 2 records</span>' in found[disputed["id"]]
    assert '<span aria-hidden="true">⊘</span> Withdrawn</span>' in found[withdrawn["id"]]
    assert '<span aria-hidden="true">●</span> Open</span>' in found[asked["id"]]
    assert 'class="badge' not in found[current["id"]].split('<div class="rec-meta">', 1)[0]
    # The correction's own row says what it corrects.
    assert ("resolved", f"→ Corrects {corrected['id']}") in badges(found[correction["id"]])


def test_a_row_shows_a_few_references_and_its_relation_page_the_rest(tmp_path):
    p = project(tmp_path)
    cited = [core.record(p, "note", f"근거 {n}", origin=ORIGIN).event for n in range(7)]
    finding = core.record(p, "finding", "많이 인용한 발견", origin=ORIGIN,
                          refs=[{"type": "event", "event_id": e["id"], "relation": "cites"} for e in cited]).event
    workspace.build(p)
    row = rows(section(page(p, "index.html"), "insights"))["insight-" + finding["id"]]
    assert len(badges(row)) == workspace.ROW_REFS
    assert '<span class="ref-more" title="The relation page lists them all">3 more</span>' in row
    assert f">Relations {len(cited)}</a>" in row
    assert all(e["id"] in relation_page(p, finding["id"]) for e in cited)


# --- judgments in words -----------------------------------------------------------------------------

def test_every_judgment_has_words_of_its_own_and_its_reason_when_the_catalog_has_them():
    for state in workspace.REF_STATE:
        assert i18n.has(f"ref.{state}"), state
    assert plain(workspace.ref_words("missing", "record_absent")) == "Not found when cited"
    assert plain(workspace.ref_words("missing", "file_absent")) == "File missing now"
    assert plain(workspace.ref_words("missing", "digest_mismatch")) == "Cited bytes differ"
    assert plain(workspace.ref_words("missing", "a_reason_no_kit_wrote")) == "Missing"
    assert plain(workspace.ref_words("unknown", "unresolved_name")) == "Unknown name"
    assert plain(workspace.ref_words("version_unbound")) == "Version not named"
    assert plain(workspace.ref_words("content_changed")) == "Changed since cited"
    # Each tone carries a mark as well as a colour.
    assert {mark for _, mark in workspace.REF_STATE.values()} >= {"✓", "⚠", "⊗", "–"}


def test_a_record_cited_before_it_was_written_says_it_was_recorded_later(tmp_path):
    p = project(tmp_path)
    early = core.record(p, "finding", "앞선 발견", origin=ORIGIN,
                        refs=[{"type": "event", "event_id": "e4", "relation": "cites", "basis": "declared",
                               "version_binding": "author"}]).event
    later = core.record(p, "note", "나중 기록", origin=ORIGIN).event
    assert later["id"] == "e4", later
    workspace.build(p)
    row = rows(section(page(p, "index.html"), "insights"))["insight-" + early["id"]]
    assert ("missing", "⊗ Not found when cited · e4 recorded later: e4") in badges(row)
    # A mistyped id that no record holds says only that it was not found.
    q = project(tmp_path / "q")
    gone = core.record(q, "finding", "없는 기록 인용", origin=ORIGIN,
                       refs=[{"type": "event", "event_id": "e99", "relation": "cites", "basis": "declared",
                              "version_binding": "author"}]).event
    workspace.build(q)
    row = rows(section(page(q, "index.html"), "insights"))["insight-" + gone["id"]]
    assert ("missing", "⊗ Not found when cited · e99") in badges(row)


# --- each kind's own list (owner decision A, 2026-10-14) ---------------------------------------------

def test_the_kind_filter_links_each_kind_s_own_list_of_every_record(tmp_path):
    p = project(tmp_path)
    findings = [core.record(p, "finding", f"발견 {n}", origin=ORIGIN).event for n in range(120)]
    decisions = [core.record(p, "decision", f"결정 {n}", origin=ORIGIN).event for n in range(3)]
    notes = [core.record(p, "note", f"메모 {n}", origin=ORIGIN).event for n in range(2)]
    workspace.build(p)
    index = page(p, "index.html")
    timeline, insights = section(index, "timeline"), section(index, "insights")
    # The counts are the whole journal's, not the 50 rows shown, and every link is a page: no script.
    kinds = re.search(r'<nav class="kinds"[^>]*>(.*?)</nav>', insights).group(1)
    assert re.findall(r'href="([^"]+)"', kinds) == ["index.html#insights", "lists/kind-finding-0.html",
                                                    "lists/kind-decision-0.html"]
    assert 'aria-current="page">All <span class="n">123</span>' in kinds
    assert 'Finding <span class="n">120</span>' in kinds and "Note" not in kinds
    assert 'Note <span class="n">2</span>' in timeline and 'href="lists/kind-note-0.html"' in timeline
    for words in ("Filters apply", "filters apply"):
        assert words not in index, words
    assert "<button" not in timeline and "<button" not in insights
    # Each kind's list: 50 a page, newest first, every record once, its own kind marked.
    pages = sorted((p.workspace_dir / "lists").glob("kind-finding-*.html"))
    assert [f.name for f in pages] == ["kind-finding-0.html", "kind-finding-1.html", "kind-finding-2.html"]
    listed = [re.findall(r'<li class="ev rec" id="([^"]+)"', page(p, "lists/" + f.name)) for f in pages]
    assert [len(ids) for ids in listed] == [50, 50, 20]
    assert sum(listed, []) == [e["id"] for e in reversed(findings)]
    first = page(p, "lists/kind-finding-0.html")
    assert '<h1>Finding</h1>' in first and "Items 1–50 of 120; 70 outside this page." in first
    assert 'href="kind-finding-0.html" class="on" aria-current="page"' not in first  # links are to the root
    assert '<a href="../lists/kind-finding-0.html" class="on" aria-current="page">Finding' in first
    assert '<a href="kind-finding-1.html">Next page</a>' in first
    assert "<title>Finding · Lists</title>" in first
    assert {f.name for f in (p.workspace_dir / "lists").glob("kind-*.html")} == {
        "kind-finding-0.html", "kind-finding-1.html", "kind-finding-2.html", "kind-decision-0.html",
        "kind-note-0.html", "kind-project-0.html", "kind-context-0.html"}
    assert all(e["id"] in page(p, "lists/kind-decision-0.html") for e in decisions)
    assert all(e["id"] in page(p, "lists/kind-note-0.html") for e in notes)


def test_a_kind_no_name_can_hold_still_gets_a_list_that_names_no_path():
    assert workspace.kind_file("workflow_feedback") == "workflow_feedback"
    for odd in ("../x", "Finding", "a b", "x" * 60, "결정"):
        name = workspace.kind_file(odd)
        assert re.fullmatch(r"x-[0-9a-f]{12}", name), (odd, name)
    assert workspace.kind_file("../x") != workspace.kind_file("../y")


# --- the relation page -------------------------------------------------------------------------------

def test_the_relation_page_reads_as_the_record_with_its_correction_first(tmp_path):
    p = project(tmp_path)
    original = core.record(p, "finding", "원래 발견 <b>그대로</b>", origin=ORIGIN).event
    correction = core.record(p, "finding", "정정한 발견", origin=ORIGIN,
                             refs=[{"type": "event", "event_id": original["id"], "relation": "supersedes"}]).event
    long = core.record(p, "note", "긴 메모 " * 60, origin=ORIGIN).event
    workspace.build(p)
    html = relation_page(p, original["id"])
    head = html.split('<header class="page-head record-head" id="record">', 1)[1].split("</header>", 1)[0]
    assert '<h1 lang="">원래 발견 &lt;b&gt;그대로&lt;/b&gt;</h1>' in head
    assert f'<p class="sub">{original["id"]} · <time datetime=' in head and "journal line" in head
    assert "Superseded by " + correction["id"] in head
    note = html.split('role="note">', 1)[1].split("</div>", 1)[0]
    assert "This record was corrected." in note and f'{correction["id"]} “<span lang="">정정한 발견</span>”' in note
    assert "The original text and its evidence stay as recorded." in note
    # The stored values are under each relation's Details, as stored.
    details = html.split('<details class="relation-details">', 1)[1].split("</details>", 1)[0]
    assert "<code>declared</code>" in details and "<code>author</code>" in details and "<code>resolved</code>" in details
    assert "Confirmed link" in details and "&quot;relation&quot;: &quot;supersedes&quot;" in details
    # The record's stored state and fields come last, folded.
    technical = html.split('<section id="technical">', 1)[1]
    assert technical.startswith('<details class="technical">') and "&quot;corrected_by&quot;" in technical
    # A long text follows a heading that names the record.
    long_html = relation_page(p, long["id"])
    assert f'<h1>Note {long["id"]}</h1><p class="record-text" lang="">' in long_html


def test_every_page_but_the_index_names_where_its_way_back_goes(tmp_path):
    p = project(tmp_path)
    finding = core.record(p, "finding", "발견", origin=ORIGIN).event
    workspace.build(p)
    back = re.compile(r'<a class="back" data-return href="([^"]+)"><span aria-hidden="true">←</span> ([^<]+)</a>')
    assert back.search(relation_page(p, finding["id"])).groups() == ("../index.html#insights", "Back to Insights")
    assert back.search(page(p, "context.html")).groups() == ("index.html#context", "Back to Context")
    assert back.search(page(p, "lists/timeline-0.html")).groups() == ("../index.html#timeline", "Back to Timeline")
    assert back.search(page(p, "lists/insights-0.html")).groups() == ("../index.html#insights", "Back to Insights")
    assert back.search(page(p, "lists/kind-finding-0.html")).groups() == ("../index.html#timeline", "Back to Timeline")
    assert "data-return" not in page(p, "index.html")
    for name in ("relations/" + workspace_relations.name(finding["id"]), "lists/kind-finding-0.html"):
        assert page(p, name).count("data-return") == 1, name


def test_data_and_run_rows_have_ids_a_way_back_can_return_to(tmp_path):
    q = LegacyProject(tmp_path / "q", migrated=True)
    path = "data/derived/t.parquet"
    run = q.run("20261001-090000-a", started_at=at(0), ts=at(1), outputs=[(path, q.file(path, b"t"))])
    p = core.Project(q.root)
    workspace.build(p)
    index = page(p, "index.html")
    view = core.view_names(core.list_datasets(p))
    [name] = [v for v, held in view.items() if held == path]
    assert f'<tr id="run-{run}">' in section(index, "runs")
    assert f'<tr id="data-{name}">' in section(index, "data")


def test_plotly_is_read_when_a_figure_shows_not_before_the_page_paints(tmp_path):
    q = LegacyProject(tmp_path / "q", migrated=True)
    run = q.run("20261001-090000-a", started_at=at(0), ts=at(1), charts=["trend"])
    p = core.Project(q.root)
    workspace.build(p)
    for name in ("index.html", f"runs/{run}.html"):
        html = page(p, name)
        assert 'data-plotly="' in html.split(">", 2)[1], name
        assert "plotly.min.js" not in html.split("</head>", 1)[1].split('class="plotly-chart"', 1)[0], name
        assert not re.search(r'<script src="[^"]*plotly\.min\.js', html), name
    assert (p.workspace_dir / "assets" / "plotly.min.js").is_file()
    assert 'data-plotly="' not in page(p, "context.html").split(">", 2)[1]
    css = (Path(workspace.__file__).parent / "assets" / "workspace.css").read_text(encoding="utf-8")
    # Text sizes follow the reader's setting: no font size in px.
    assert not re.search(r"font(-size)?:\s*[0-9.]+px", css)


def test_the_script_names_the_way_back_reads_plotly_late_and_folds_the_menu_without_a_browser():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: controller checks are not browser QA")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / "tests/workspace_lists.cjs"),
                             str(root / "src/aelix_analytics/assets/workspace-state.js"),
                             str(root / "src/aelix_analytics/assets/workspace.js")],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    assert "workspace lists: ok" in result.stdout
