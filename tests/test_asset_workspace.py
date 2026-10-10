"""Workspace reads canonical stored asset evidence, never scoring or publishing it."""
from __future__ import annotations

import re
from html import unescape

from test_asset_history import old_record
from test_asset_suggestions import application, qualified
from test_assets import ORIGIN
from test_assets import prepared as prepared

from aelix_analytics import asset_app, core, workspace


def test_asset_workspace_versions_scores_pending_and_canonical_links_are_read_only(prepared, monkeypatch):
    project, _, proposed = qualified(prepared)
    app = application(project)
    app.set_mode("always_on")
    first = app.publish_suggestion()["published"]["notification"]
    details = {**proposed["details"], "previous_event_id": proposed["id"],
               "title": "<script>second version</script> 사용자가 쓴 제목"}
    corrected = core.record(project, "asset_candidate", "Corrected fields", details=details,
                            refs=[r for r in proposed["refs"] if r.get("relation") == "cites"], origin=ORIGIN).event
    before = project.journal.read_bytes()
    monkeypatch.setattr(asset_app.Assets, "assessment", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Workspace scored")))
    workspace.build(project)
    page = (project.workspace_dir / "index.html").read_text(encoding="utf-8")
    assert '<section id="assets" class="tab">' in page
    assert "Analysis assets" in page and "Pending asset suggestions" in page
    assert "Recorded score: 8/10" in page and "No recorded score" in page
    for event_id in (proposed["id"], corrected["id"], first["id"]):
        assert f'href="#{event_id}"' in page and f'id="{event_id}"' in page
    assert "&lt;script&gt;second version&lt;/script&gt; 사용자가 쓴 제목" in page
    assert "<script>second version</script>" not in page
    assert project.journal.read_bytes() == before


def test_legacy_asset_workspace_preserves_raw_fields_and_ambiguous_occurrence_links(prepared):
    project, _, _ = prepared
    old = old_record(project, "reuse_proposal", {"proposal_id": "사용자 원본", "skill": "기존 설명"})
    with project.journal.open("ab") as out:
        out.write(project.journal.read_bytes().splitlines(keepends=True)[-1])
    before = project.journal.read_bytes()
    workspace.build(project)
    page = (project.workspace_dir / "index.html").read_text(encoding="utf-8")
    assert "Asset candidate (legacy)" in page and "Raw kind: reuse_proposal" in page
    assert "proposal_id" in page and "사용자 원본" in page and "기존 설명" in page
    section = page.split('<section id="assets"', 1)[1].split('</section>', 1)[0]
    ids = set(re.findall(r'id="([^"]+)"', page))
    for fragment in re.findall(r'href="#([^"]+)"', section):
        assert unescape(fragment) in ids
    assert old["id"] in section and project.journal.read_bytes() == before
