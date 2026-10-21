"""The Workspace's Reports tab and pages (#19 PR3; ADR-0013 7, design REPORT-ARTIFACTS 5).

A project with Reports as the records hold them — two versions of one, the first withdrawn, cited by
a finding and based on another; a collection of five members, three complete, one failed, one not
generated; an HTML Report — built into the Workspace, and the pages read as a person reads them:

- the tab beside Insights, the latest version of each Report, its states apart, without its body;
- a Report's page: 'Version n of N', the versions (#99), and for each, when and on what it was made
  before its body, its evidence judged, its members as the snapshot holds them, its files where the
  records keep them, the records citing it (a completion or a withdrawal cites nothing);
- the body a document of its own by the injected renderer, never in a Workspace page; no document
  for HTML, nor for files that are not the bytes saved;
- building again keeps the preserved bytes, reuses each version's view and removes stale ones;
- relation pages, reference badges and the URL codec reach the pages; every link resolves.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from conftest import analytics_project
from report_renderer_doubles import RecordingReportRenderer

from aelix_analytics import (
    _platform,
    core,
    relations,
    workspace,
    workspace_relations,
    workspace_reports,
)

HUMAN = core.Origin("작성자", "sdk", "declared")


def cite(p, report_id, revision_id, relation="cites"):
    """A reference to a Report version its author binds: by the digest of its manifest."""
    manifest = p.root / "artifacts" / "reports" / report_id / revision_id / "manifest.json"
    return {"type": "report_revision", "report_id": report_id, "revision_id": revision_id,
            "digest": {"sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()},
            "relation": relation, "basis": "declared", "version_binding": "author"}


def save(p, report_id, folder, entry, title, previous=None, request=None, **options):
    return core.save_report(p, report_id, files=folder, entry=entry, title=title, previous_revision=previous,
                            request_id=request or f"{report_id}-{previous or 'first'}", origin=HUMAN, **options)["report"]


@pytest.fixture
def reports(tmp_path):
    p = core.Project(analytics_project(tmp_path / "보고서 프로젝트", version="0.8.0"), platform=_platform.detect())
    finding = core.record(p, "finding", "서울 매출이 12% 늘었다", origin=HUMAN).event
    source = tmp_path / "주간"
    source.mkdir()
    (source / "요약.md").write_text("# 주간 결과\n\n<b>raw</b> 서울 매출이 늘었습니다. [첨부](첨부.csv)\n", encoding="utf-8")
    (source / "첨부.csv").write_text("region,sales\n서울,10\n", encoding="utf-8")
    first = save(p, "weekly", source, "요약.md", "주간 매출 보고서", metadata={"summary": "서울 중심", "purpose": "주간 공유"})
    (source / "요약.md").write_text("# 주간 결과\n\n서울 매출이 12% 늘었습니다.\n", encoding="utf-8")
    seq = core.read_model(p, core.HashCache(p)).as_of_seq
    second = save(p, "weekly", source, "요약.md", "주간 매출 보고서", previous=first["revision_id"],
                  metadata={"change_reason": "숫자 고침", "content_status": "complete"},
                  based_on={"as_of_seq": seq, "context_version": None,
                            "refs": [{"type": "event", "event_id": finding["id"], "relation": "cites", "basis": "declared",
                                      "version_binding": "author"}]})
    core.update_report(p, "weekly", first["revision_id"], expected="current", new="withdrawn", reason="틀린 숫자", origin=HUMAN)
    core.record(p, "decision", "주간 보고서대로 진행", refs=[cite(p, "weekly", second["revision_id"])], origin=HUMAN)
    members = []
    for n in range(3):
        folder = tmp_path / f"지역{n}"
        folder.mkdir()
        (folder / "r.md").write_text(f"# 지역 {n}\n", encoding="utf-8")
        member = save(p, f"region-{n}", folder, "r.md", f"지역 {n}")
        members.append({"member_key": f"지역 {n}", "status": "complete", "report_id": member["report_id"],
                        "revision_id": member["revision_id"]})
    members += [{"member_key": "부산", "status": "failed", "reason": "데이터 없음", "scope": {"열": "b"}},
                {"member_key": "대구", "status": "not_generated", "reason": "아직 요청하지 않음"}]
    folder = tmp_path / "묶음"
    folder.mkdir()
    (folder / "묶음.md").write_text("# 지역 묶음\n", encoding="utf-8")
    collection = save(p, "regions", folder, "묶음.md", "지역별 묶음",
                      metadata={"collection": {"contract": "report_collection/1", "members": members},
                                "content_status": "partial"})
    folder = tmp_path / "html"
    folder.mkdir()
    (folder / "index.html").write_text("<script>alert(1)</script><h1>HTML</h1>", encoding="utf-8")
    html = save(p, "html-one", folder, "index.html", "HTML 보고서 <b>")
    return {"p": p, "first": first, "second": second, "collection": collection, "html": html, "finding": finding}


def page_of(p, report_id) -> str:
    return (p.workspace_dir / "reports" / workspace_reports.report_page(report_id)).read_text(encoding="utf-8")


def text(html: str) -> str:
    html = re.sub(r"<(script|svg)[\s\S]*?</\1>", " ", html)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def main(page: str) -> str:
    return page[page.index("<main>"):page.index("</main>")]


def section(page: str, ident: str) -> str:
    """The element whose id is `ident`: a version's section up to the next version's (it holds parts
    of its own), any other section up to its end."""
    start = page.index(f'id="{ident}"')
    if ident.startswith("v-"):
        after = page.find('<section class="report-version"', start)
        return page[start:after if after > 0 else page.index("</main>", start)]
    return page[start:page.index("</section>", start)]


def test_the_tab_lists_the_latest_version_of_each_report_beside_insights(reports):
    p = reports["p"]
    workspace.build(p)
    index = (p.workspace_dir / "index.html").read_text(encoding="utf-8")
    order = re.findall(r'<section id="(\w+)" class="tab"', index)
    assert order[:3] == ["overview", "insights", "reports"]
    tab = section(index, "reports")
    assert 'data-count="6"' in index[index.index('<section id="reports"'):][:120]
    rows = re.findall(r'<tr id="report-([^"]+)">(.*?)</tr>', tab)
    assert [r for r, _ in rows][:2] == ["html-one", "regions"]  # newest first
    weekly = dict(rows)["weekly"]
    assert "Version 2 of 2" in text(weekly) and "Complete (the author's word)" in text(weekly)
    assert 'lang="">주간 매출 보고서</a>' in weekly
    regions = text(dict(rows)["regions"])
    assert "3 of 5 complete" in regions and "Partial (the author's word)" in regions
    # The list reads no body: neither version's text is in the tab.
    assert "서울 매출" not in tab


def test_a_reports_page_shows_its_versions_then_each_versions_facts_before_its_body(reports):
    p, first, second = reports["p"], reports["first"], reports["second"]
    workspace.build(p)
    page = page_of(p, "weekly")
    shown = text(main(page))
    assert "Version 2 of 2" in shown and 'class="vh-strip kind-report"' in page
    assert f'id="v-{second["revision_id"]}"' in page and f'id="v-{first["revision_id"]}"' in page
    latest = section(page, f'v-{second["revision_id"]}')
    assert "<details open>" in latest
    words = text(latest)
    # When and on what before the body (REPORT-ARTIFACTS 5): the facts come before the files and the body.
    assert words.index("Created at") < words.index("Based on") < words.index("Files ✓") < words.index("Open the report")
    assert "as of record" in words and "1 reference" in words and "숫자 고침" in words
    assert "A review of its conclusion is a record of its own" in words
    # The evidence, judged: the finding the version names, found (a found record's badge names its kind).
    evidence = latest[latest.index("Evidence"):]
    assert 'data-state="resolved"' in evidence and f'Finding {reports["finding"]["id"]}' in text(evidence)
    # The older version: withdrawn, folded, its own facts.
    older = section(page, f'v-{first["revision_id"]}')
    assert "<details>" in older and "Withdrawn" in text(older) and "서울 중심" in text(older)
    assert "This version names no evidence (not linked)." in text(older)
    # Compared with the one before: the text's changed lines.
    assert "Compared with v1" in words and "Changed lines: 1 added" in words


def test_citations_count_records_not_the_kits_own_completion_or_withdrawal(reports):
    p, first, second = reports["p"], reports["first"], reports["second"]
    model = core.read_model(p, core.HashCache(p))
    assert len(model.report_citations("weekly", second["revision_id"])) == 1  # the decision
    assert model.report_citations("weekly", first["revision_id"]) == []  # completion and withdrawal only
    # A collection's members are no citation of them.
    member = model.report_histories()["region-0"]["versions"][0]["revision_id"]
    assert model.report_citations("region-0", member) == []
    workspace.build(p)
    page = page_of(p, "weekly")
    assert "Cited by 1 record" in text(section(page, f'v-{second["revision_id"]}'))
    assert "No record cites this version." in text(section(page, f'v-{first["revision_id"]}'))


def test_a_collection_shows_every_member_as_its_snapshot_holds_them_without_a_script(reports):
    p, collection = reports["p"], reports["collection"]
    workspace.build(p)
    page = page_of(p, "regions")
    part = section(page, f'v-{collection["revision_id"]}')
    words = text(part)
    assert "3 of 5 complete · 1 failed · 1 not generated" in words
    rows = re.findall(r'<tr id="m-[^"]+-(\d)">(.*?)</tr>', part)
    assert [int(n) for n, _ in rows] == [0, 1, 2, 3, 4]
    assert all("Complete" in text(r) and "Exact version" in text(r) for _, r in rows[:3])
    assert "Failed" in text(rows[3][1]) and "데이터 없음" in text(rows[3][1]) and '"열": "b"' in unescape(rows[3][1])
    assert "Not generated" in text(rows[4][1]) and "아직 요청하지 않음" in text(rows[4][1])
    # A complete member links its exact version on its own Report's page.
    member = core.read_model(p, core.HashCache(p)).report_histories()["region-0"]["versions"][0]["revision_id"]
    assert f'href="../reports/{workspace_reports.report_page("region-0")}#v-{member}"' in rows[0][1]
    # The member relation is no evidence of the collection document.
    assert "This version names no evidence (not linked)." in words
    assert "<script" not in part


def test_the_body_is_a_document_of_its_own_and_never_in_a_workspace_page(reports):
    p, second = reports["p"], reports["second"]
    workspace.build(p)
    page = page_of(p, "weekly")
    folder = p.workspace_dir / "reports" / workspace_reports.view_folder("weekly", second["revision_id"])
    body = (folder / "preview" / "report.html").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in body and "서울 매출이 12% 늘었습니다." in body
    assert f'href="{workspace_reports.view_folder("weekly", second["revision_id"])}/preview/report.html"' in page
    # The body is not markup of a Workspace page (the comparison shows its lines as text), and the
    # authored HTML of the first version is escaped in its own document, never markup.
    assert "<h1>주간 결과</h1>" not in page and "<b>raw</b>" not in page
    first_body = (p.workspace_dir / "reports" / workspace_reports.view_folder("weekly", reports["first"]["revision_id"])
                  / "preview" / "report.html").read_text(encoding="utf-8")
    assert "<b>raw</b>" not in first_body and "&lt;b&gt;raw&lt;/b&gt;" in first_body
    # The copies its links take sit beside it; the originals stay where the records keep them.
    assert (folder / "files" / "첨부.csv").read_bytes() == b"region,sales\n\xec\x84\x9c\xec\x9a\xb8,10\n"
    assert f'href="../../artifacts/reports/weekly/{second["revision_id"]}/content/첨부.csv"' in unquote(page)


def test_html_and_changed_files_get_no_body_document_and_say_why(reports):
    p, html, second = reports["p"], reports["html"], reports["second"]
    workspace.build(p)
    assert not (p.workspace_dir / "reports" / workspace_reports.view_folder("html-one", html["revision_id"])).exists()
    words = text(page_of(p, "html-one"))
    assert "HTML and SVG are not shown inside the Workspace: open or download the original." in words
    assert "Open the report" not in words
    # A preserved file changed after it was saved: no body from bytes that are not the ones saved.
    target = p.root / "artifacts/reports/weekly" / second["revision_id"] / "content" / "요약.md"
    target.write_bytes(b"# tampered\n")
    workspace.build(p)
    part = text(section(page_of(p, "weekly"), f'v-{second["revision_id"]}'))
    assert "Files changed since saved" in part and "Its files are not the bytes it was saved with" in part
    assert not (p.workspace_dir / "reports" / workspace_reports.view_folder("weekly", second["revision_id"])).exists()


def test_building_again_keeps_the_preserved_bytes_reuses_views_and_prunes_stale_ones(reports):
    p, first = reports["p"], reports["first"]
    artifacts = p.root / "artifacts" / "reports"
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts.rglob("*") if path.is_file()}
    workspace.build(p)
    stamp = p.workspace_dir / "reports" / workspace_reports.view_folder("weekly", first["revision_id"]) / "view.json"
    made = stamp.stat().st_mtime_ns
    stale = p.workspace_dir / "reports" / "gone-0123456789" / "01OLD"
    stale.mkdir(parents=True)
    (stale / "view.json").write_text("{}", encoding="utf-8")
    (p.workspace_dir / "reports" / "gone-0123456789.html").write_text("old", encoding="utf-8")
    workspace.build(p)
    assert stamp.stat().st_mtime_ns == made  # the same bytes by the same renderer: kept as it is
    assert not stale.parent.exists() and not (p.workspace_dir / "reports" / "gone-0123456789.html").exists()
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts.rglob("*") if path.is_file()}
    assert after == before  # completion condition 2: v1's bytes and evidence after v2 and a rebuild


def test_the_injected_renderer_receives_only_bytes_an_entry_and_paths(reports):
    p = reports["p"]
    renderer = RecordingReportRenderer()
    workspace.build(p, report_renderer=renderer)
    calls = {entry: (files, paths) for files, entry, paths in renderer.calls}
    files, paths = calls["요약.md"]
    assert set(files) == {"요약.md", "첨부.csv"} and relations.REPORT_METADATA_FILE not in files
    assert all(isinstance(v, bytes) for v in files.values())
    assert paths == {"요약.md": "files/요약.md", "첨부.csv": "files/첨부.csv"}
    assert "index.html" in calls  # the HTML Report is offered too; the renderer says it has no body
    assert len(renderer.calls) == 7  # every version: weekly's two, three members, the collection, the HTML


def test_relation_pages_badges_and_the_timeline_reach_the_report_pages(reports):
    p, second = reports["p"], reports["second"]
    workspace.build(p)
    key = relations.report_key("weekly", second["revision_id"])
    relation = (p.workspace_dir / "relations" / workspace_relations.name(key)).read_text(encoding="utf-8")
    link = f'../reports/{workspace_reports.report_page("weekly")}#v-{second["revision_id"]}'
    assert link in relation and "Report version" in text(relation) and "주간 매출 보고서" in text(relation)
    index = (p.workspace_dir / "index.html").read_text(encoding="utf-8")
    assert f'reports/{workspace_reports.report_page("weekly")}#v-{second["revision_id"]}' in index  # the decision's badge


def test_every_link_of_the_report_pages_resolves(reports):
    p = reports["p"]
    workspace.build(p)
    for file in p.workspace_dir.rglob("*.html"):
        for raw in re.findall(r'href="([^"]+)"', file.read_text(encoding="utf-8")):
            link = urlsplit(unescape(raw))
            if link.scheme or not link.path:
                continue
            target = (file.parent / unquote(link.path)).resolve()
            assert target.exists(), (str(file.relative_to(p.root)), raw)
            if link.fragment and target.suffix == ".html":
                assert f'id="{unquote(link.fragment)}"' in target.read_text(encoding="utf-8"), (file.name, raw)


def test_the_kits_words_switch_and_what_people_wrote_stays_as_written(reports):
    p = reports["p"]
    workspace.build(p)
    page = page_of(p, "regions")
    assert 'lang="">지역별 묶음</h1>' in page and 'lang="">부산</span>' in page and 'lang="">데이터 없음</span>' in page
    # The kit's words are marked for the page's language switch (workspace-i18n.js): a message with
    # arguments by its key, one without by its English text, which the catalogs pair with Korean.
    assert "<k-w>Collection</k-w>" in page and "<k-w>Failed</k-w>" in page
    assert 'k="reports.collection.counts"' in page and 'k="versions.n_of"' in page
    en = json.loads((Path(core.__file__).parent / "assets/i18n/en.json").read_text(encoding="utf-8"))
    ko = json.loads((Path(core.__file__).parent / "assets/i18n/ko.json").read_text(encoding="utf-8"))
    assert {k for k in en if k.startswith("reports.")} == {k for k in ko if k.startswith("reports.")}


def test_the_url_codec_takes_the_reports_tab_and_pages():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: pure URL codec checks are not browser QA")
    codec = Path(__file__).resolve().parents[1] / "src/aelix_analytics/assets/workspace-state.js"
    script = r'''
const assert = require("node:assert/strict"), codec = require(process.argv[1]);
const state = { v:1, page:"reports/weekly-0123456789.html", anchor:"v-01ABC", tab:"reports", filter:"", retired:false,
  selection:"weekly", source:"1234567890abcdef", card:"", offset:12,
  origin: { v:1, page:"index.html", anchor:"report-weekly", tab:"reports", filter:"", retired:false, selection:"",
            source:"1234567890abcdef", card:"", offset:0 } };
assert.deepEqual(codec.decode(codec.search(state)).state, state);
// A body document is no page the state may name (it is not a Workspace page: no script, no state).
assert.throws(() => codec.decode(codec.search({...state, page:"reports/x/01ABC/preview/report.html"})));
'''
    subprocess.run([node, "-e", script, str(codec)], check=True, encoding="utf-8")
