"""Source-backed neighborhoods, exact versions, bounded HTML and nonbrowser state checks."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path

import pytest
from legacy_records import LegacyProject

from aelix_analytics import core, workspace, workspace_relations

ORIGIN = core.Origin("human", "cli", "declared")


def project(tmp_path):
    core.init_project(tmp_path / "p", title="Workspace", goal="선택 버전 확인", origin=ORIGIN)
    return core.Project(tmp_path / "p")


def test_neighborhood_rows_are_the_shared_bounded_read_before_and_after_index(tmp_path):
    p = project(tmp_path)
    a = core.record(p, "finding", "매출", origin=ORIGIN).event
    b = core.record(p, "finding", "정정", refs=[{"type": "event", "event_id": a["id"], "relation": "supersedes"}], origin=ORIGIN).event
    model = core.read_model(p)
    expected = core.retrieve(p, part="relations", id=a["id"], limit=100)["items"]
    assert model.reference_neighborhood(a["id"]) == expected
    model.prepare_reference_neighborhoods()
    assert model.reference_neighborhood(a["id"]) == expected
    assert expected[0]["from"] == b["id"] and expected[0]["confirmed"]
    historical = model.through(a["seq"])
    assert historical.reference_neighborhood(a["id"]) == []


def test_outgoing_neighborhood_does_not_judge_unrelated_files(tmp_path, monkeypatch):
    p = project(tmp_path)
    event = core.record(p, "finding", "No references", origin=ORIGIN).event
    model = core.read_model(p)
    monkeypatch.setattr(model, "incoming_sources", lambda *_: pytest.fail("outgoing lookup scanned incoming"))
    assert model.reference_neighborhood(event["id"], "outgoing") == []
    assert model._references is None


def test_exact_versions_and_unbound_names_have_separate_links_and_source_qualifications(tmp_path):
    legacy = LegacyProject(tmp_path / "legacy", migrated=True)
    first = legacy.run("20261011-160000-first", charts=["trend"], script="scripts/a.py")
    second = legacy.run("20261011-160100-second", charts=["trend"], script="scripts/a.py")
    # This fixture helper is itself the legacy record writer, under the current marker.
    cited = legacy.event("finding", text="<script>原文</script>", refs={"charts": "scripts/a.py::trend"})
    p = core.Project(legacy.root)
    before = p.journal.read_bytes()
    workspace.build(p)
    site = workspace.Site(p)
    page = (p.workspace_dir / "relations" / workspace_relations.name(cited)).read_text(encoding="utf-8")
    assert "version_unbound" in page and "not a confirmed link" in page
    assert "&lt;script&gt;原文&lt;/script&gt;" in page and "<script>原文</script>" not in page
    old = (p.workspace_dir / "relations" / workspace_relations.name(first + "/trend")).read_text(encoding="utf-8")
    assert f'../runs/{first}.html#chart-trend' in old
    assert "View latest version" in old and "Small directed neighborhood" in old
    assert first != second
    assert site.model.source_revision in page
    assert p.journal.read_bytes() == before


def test_relation_text_follows_edge_direction_and_one_order_serves_every_width(tmp_path):
    p = project(tmp_path)
    original = core.record(p, "finding", "원래 발견", origin=ORIGIN).event
    correction = core.record(p, "finding", "정정", origin=ORIGIN,
                             refs=[{"type": "event", "event_id": original["id"], "relation": "supersedes"}]).event
    workspace.build(p)
    page = (p.workspace_dir / "relations" / workspace_relations.name(original["id"])).read_text(encoding="utf-8")
    incoming = page.split('<li class="relation-item" data-direction="incoming"><p>', 1)[1].split("</p>", 1)[0]
    assert incoming.index(correction["id"]) < incoming.index("→ Selected version")  # source → target, like the diagram
    # Focus follows what is shown: the DOM order is the visual order, with no CSS reordering.
    assert page.index('id="related"') < page.index('class="relation-diagram"') < page.index('id="evidence"') < page.index('id="record"')
    assert '<details class="relation-diagram">' in page  # collapsed until the script finds room for its columns
    css = (p.workspace_dir / "assets" / "workspace.css").read_text(encoding="utf-8")
    assert not re.search(r"(^|[^-a-z])order\s*:", css)


def test_bounded_index_full_continuation_and_paginated_relations_preserve_all_records(tmp_path):
    p = project(tmp_path)
    target = core.record(p, "finding", "重要な古い記録", origin=ORIGIN).event
    expected = []
    for n in range(125):
        expected.append(core.record(p, "note", f"note {n}", refs=[{"type": "event", "event_id": target["id"]}], origin=ORIGIN).event["id"])
    before = p.journal.read_bytes()
    workspace.build(p)
    index = (p.workspace_dir / "index.html").read_text(encoding="utf-8")
    timeline = index.split('<section id="timeline"', 1)[1].split('</section>', 1)[0]
    assert len(re.findall(r'<li class="ev"', timeline)) == workspace.INDEX_LIMIT
    assert "outside this page" in timeline and 'lists/timeline-0.html' in timeline
    listing = ''.join(p.read_text(encoding="utf-8") for p in sorted((p.workspace_dir / "lists").glob('timeline-*.html')))
    assert all(f'id="{id_}"' in listing for id_ in expected)
    pages = sorted((p.workspace_dir / "relations").glob(workspace_relations.name(target["id"]).split('-0.html')[0] + '-*.html'))
    assert len(pages) == 7
    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert html.count('class="relation-item"') <= workspace_relations.PAGE_SIZE
        assert html.count('class="relation-node ') <= workspace_relations.PAGE_SIZE
    assert all(id_ in ''.join(page.read_text(encoding="utf-8") for page in pages) for id_ in expected)
    assert p.journal.read_bytes() == before


def test_unsupported_target_values_stay_text_and_cannot_break_static_generation(tmp_path):
    p = project(tmp_path)
    event = core.record(p, "finding", "Malformed reference", origin=ORIGIN).event
    value = {**event, "id": "e999", "seq": event["seq"] + 1,
             "refs": [{"type": "event", "event_id": ["not", "an", "identity"],
                       "relation": "cites", "basis": "declared", "version_binding": "author"}]}
    with p.journal.open("a", encoding="utf-8") as out:
        out.write(json.dumps(value) + "\n")
    workspace.build(p)
    page = (p.workspace_dir / "relations" / workspace_relations.name("e999")).read_text(encoding="utf-8")
    judged = core.read_model(p).references("e999")[0]
    assert judged["state"] in page and "Unknown target" in page
    assert "not a confirmed link" in page


def test_all_generated_internal_links_point_to_existing_pages_and_fragments(tmp_path):
    p = project(tmp_path)
    first = core.record(p, "finding", "古い根拠", origin=ORIGIN).event
    for n in range(60):
        core.record(p, "finding", f"新しい根拠 {n}", refs=[{"type": "event", "event_id": first["id"]}], origin=ORIGIN)
    workspace.build(p)
    from urllib.parse import unquote, urlsplit
    for file in p.workspace_dir.rglob('*.html'):
        for raw in re.findall(r'href="([^"]+)"', file.read_text(encoding="utf-8")):
            link = urlsplit(unescape(raw))
            if link.scheme or not link.path:
                continue
            target = (file.parent / unquote(link.path)).resolve()
            if not target.is_relative_to(p.workspace_dir) or target.suffix != '.html':
                continue
            assert target.is_file(), (file.name, raw)
            if link.fragment:
                assert f'id="{unquote(link.fragment)}"' in target.read_text(encoding="utf-8"), (file.name, raw)


def test_url_state_codec_roundtrip_without_browser_or_storage_and_invalid_origins():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: pure URL codec checks are not browser QA")
    codec = Path(__file__).resolve().parents[1] / 'src/aelix_analytics/assets/workspace-state.js'
    script = r'''
const assert = require("node:assert/strict"), codec = require(process.argv[1]);
const origin = { v:1, page:"index.html", anchor:"insight-e2", tab:"insights", filter:"finding", retired:true,
 selection:"run-old/図 #1", source:"1234567890abcdef", card:"insight-e2", offset:117.25 };
const state = {...origin, page:"relations/abc-0.html", origin};
assert.deepEqual(codec.decode(codec.search(state)).state, state);
assert.equal(codec.decode(codec.search(state, "?other=yes")).invalid, false);
assert.equal(new URLSearchParams(codec.search(state, "?other=yes")).get("other"), "yes");
for (const page of ["../index.html", "https://evil.test/x.html", "runs/%2e%2e.html", "runs/%2fother.html", "runs/%5cother.html", "index.html?x=y", "//evil/index.html"]) assert.equal(codec.safePage(page), false, page);
for (const raw of ["?ws=%7b", "?ws=null", "?ws=[]", "?ws="+encodeURIComponent(JSON.stringify({...state, origin:{v:1,page:"../index.html"}}))]) assert.equal(codec.decode(raw).invalid, true);
assert.equal(codec.decode("").invalid, false);
const detail = codec.next(origin, "relations/abc-0.html", "evidence");
const chart = codec.next(detail, "runs/old.html", "chart-図 #1");
const code = codec.next(chart, "runs/old.html", "code");
assert.deepEqual(code.origin, origin);
assert.deepEqual(codec.decode(codec.search(code.origin)).state, origin);
assert.equal(codec.scrollTarget(origin, 600, 250), 732.75);
assert.equal(codec.scrollTarget(origin, 600, null), null); // vanished card never substitutes another
assert.equal(codec.scrollTarget({...origin,card:"",offset:-700}, 0, null), 700);
assert.equal(codec.next(origin, "../outside.html"), null);
assert.equal(origin.card, "insight-e2"); // traversing descendants does not mutate the origin
assert.equal(codec.normalize({...origin, offset:Infinity}).offset, 0);
assert.equal(codec.normalize({...state, origin:{...origin,origin:state}}).origin.origin, undefined);
'''
    result = subprocess.run([node, '-e', script, str(codec)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    for file in ('workspace-state.js', 'workspace.js'):
        checked = subprocess.run([node, '--check', str(codec.with_name(file))], capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert checked.returncode == 0, checked.stderr


def test_navigation_controller_origin_history_geometry_and_escape_without_browser():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: controller checks are not browser QA")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests/workspace_navigation.cjs'),
                             str(root / 'src/aelix_analytics/assets/workspace-state.js'),
                             str(root / 'src/aelix_analytics/assets/workspace.js')],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr


def test_all_data_details_regenerate_beyond_the_initial_index_limit(tmp_path):
    from legacy_records import at

    legacy = LegacyProject(tmp_path / "many-data", migrated=True)
    for n in range(51):
        path = legacy.dataset(f"d{n:03}", imported_at=at(0))
        legacy.event("import", {"data": [path]})
    p = core.Project(legacy.root)
    journal = p.journal.read_bytes()
    workspace.build(p)
    target = p.workspace_dir / "data/d050.html"
    assert len(list((p.workspace_dir / "data").glob("*.html"))) == 51
    assert target.is_file()
    continuation = (p.workspace_dir / "lists/data-1.html").read_text(encoding="utf-8")
    assert '../data/d050.html' in continuation
    meta_path = p.root / 'data/raw/d050.meta.json'
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta['description'] = 'Updated outside the initial slice · 한국어'
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    workspace.build(p)
    assert meta['description'] in target.read_text(encoding="utf-8")
    assert p.journal.read_bytes() == journal


def test_overview_links_old_key_and_open_items_to_exact_existing_records(tmp_path):
    p = project(tmp_path)
    finding = core.record(p, "finding", "Retained old finding", origin=ORIGIN).event
    question = core.record(p, "question", "Retained old question", origin=ORIGIN).event
    for n in range(60):
        core.record(p, "note", f"Later note {n}", origin=ORIGIN)
    workspace.build(p)
    index = (p.workspace_dir / 'index.html').read_text(encoding="utf-8")
    overview = index.split('<section id="overview"', 1)[1].split('</section>', 1)[0]
    for event in (finding, question):
        assert f'id="{event["id"]}"' not in index
        link = f'relations/{workspace_relations.name(event["id"])}#record'
        assert link in overview
        assert 'id="record"' in (p.workspace_dir / 'relations' / workspace_relations.name(event['id'])).read_text(encoding="utf-8")


def test_retired_chart_continuations_offer_visibility_controls_and_exact_targets(tmp_path):
    legacy = LegacyProject(tmp_path / 'retired', migrated=True)
    charts = [f'c{n:02}' for n in range(21)]
    legacy.run('20261011-160000-old', charts=charts)
    legacy.run('20261011-160100-new')
    p = core.Project(legacy.root)
    workspace.build(p)
    page = (p.workspace_dir / 'lists/charts-1.html').read_text(encoding="utf-8")
    assert 'is-retired' in page and 'id="show-retired"' in page
    assert 'data-selection=' in page
    site = workspace.Site(p)
    last = site.charts[20]
    assert site.chart_anchor(last['key'], 0).startswith('lists/charts-1.html#chart-')


def test_controller_list_origin_invalid_page_and_retired_repairs_without_browser():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: controller checks are not browser QA")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests/workspace_navigation_repairs.cjs'),
                             str(root / 'src/aelix_analytics/assets/workspace-state.js'),
                             str(root / 'src/aelix_analytics/assets/workspace.js')],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
