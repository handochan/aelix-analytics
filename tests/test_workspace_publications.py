"""ADR-0014: 기록된 publication 참조와 현재 전달 폴더를 따로 읽는 화면."""
from __future__ import annotations

import json
import os
import shutil

import pytest
from workspace_words import plain

from aelix_analytics import core, i18n, workspace, workspace_relations
from aelix_analytics.publishing import build, state
from aelix_analytics.publishing.model import PublicationError

ORIGIN = core.Origin("human", "cli", "declared")


def publication(tmp_path):
    p = core.init_project(tmp_path / "p", title="Publication", origin=ORIGIN)
    result = build.build_publication(p, origin=ORIGIN)
    ref = {"type": "publication", "publication_id": result["publication_id"],
           "digest": {"sha256": result["package_sha256"]}, "relation": "cites",
           "basis": "declared", "version_binding": "author"}
    citing = core.record(p, "finding", "원문 <그대로>", refs=[ref], origin=ORIGIN).event
    completion = next(e.value for e in core.read_model(p).journal.events if e.kind == "publication")
    return p, result, citing, completion


def pages(p, citing, completion):
    paths = [p.workspace_dir / "index.html",
             p.workspace_dir / "lists/kind-publication-0.html",
             p.workspace_dir / "relations" / workspace_relations.name(citing["id"]),
             p.workspace_dir / "relations" / workspace_relations.name(completion["id"])]
    return [plain(path.read_text(encoding="utf-8")) for path in paths]


@pytest.mark.parametrize("folder_status", ["same", "changed", "missing"])
def test_workspace_keeps_reference_resolved_after_delivery_change(tmp_path, folder_status):
    p, result, citing, completion = publication(tmp_path)
    folder = p.root / result["path"]
    if folder_status == "changed":
        (folder / "index.html").write_bytes(b"changed")
    elif folder_status == "missing":
        shutil.rmtree(folder)
    journal = p.journal.read_bytes()
    workspace.build(p)
    observed = state.observe(core.read_model(p), result["publication_id"])
    assert observed["recording_status"] == "complete" and observed["folder_status"] == folder_status
    index, listing, cited_page, completion_page = pages(p, citing, completion)
    for page in (index, listing, cited_page, completion_page):
        assert 'data-recording="complete"' in page and f'data-folder="{folder_status}"' in page
        assert "Recording: Complete" in page and "Folder:" in page
        assert "does not prove current integrity or publishing success" in page
        assert result["publication_id"] in page
    for page in (index, cited_page):
        assert "Recorded package found" in page
    assert "원문 &lt;그대로&gt;" in cited_page
    assert f'#{completion["id"]}' in cited_page
    assert core.read_model(p).references(citing["id"])[0]["state"] == "resolved"
    assert p.journal.read_bytes() == journal


def test_if_changed_rechecks_publication_bytes_even_with_preserved_stat(tmp_path):
    p, result, citing, completion = publication(tmp_path)
    assert not workspace.build(p, if_changed=True).get("skipped")
    assert workspace.build(p, if_changed=True)["skipped"]
    path = p.root / result["path"] / "index.html"
    before = path.stat()
    raw = path.read_bytes()
    path.write_bytes(b"X" + raw[1:])
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not workspace.build(p, if_changed=True).get("skipped")
    assert 'data-folder="changed"' in pages(p, citing, completion)[0]
    shutil.rmtree(p.root / result["path"])
    assert not workspace.build(p, if_changed=True).get("skipped")
    assert 'data-folder="missing"' in pages(p, citing, completion)[0]


@pytest.mark.parametrize("failure", ["absent", "unsupported", "ambiguous", "digest"])
def test_reference_failure_and_current_record_observation_are_separate(tmp_path, failure):
    p, result, citing, completion = publication(tmp_path)
    rows = [json.loads(line) for line in p.journal.read_bytes().splitlines()]
    if failure == "absent":
        rows = [row for row in rows if row["id"] != completion["id"]]
    elif failure == "unsupported":
        next(row for row in rows if row["id"] == completion["id"])["details"]["format"] = "analytics.publication/99"
    elif failure == "ambiguous":
        duplicate = {**completion, "id": "e999", "seq": 999}
        rows.append(duplicate)
    else:
        next(row for row in rows if row["id"] == citing["id"])["refs"][0]["digest"]["sha256"] = "0" * 64
    p.journal.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n" for row in rows))
    workspace.build(p)
    page = plain((p.workspace_dir / "relations" / workspace_relations.name(citing["id"])).read_text(encoding="utf-8"))
    expected = "unsupported" if failure == "unsupported" else "missing"
    # The duplicate follows the citation, so its historical reference stays resolved; current completion is ambiguous.
    if failure == "ambiguous":
        expected = "resolved"
    assert core.read_model(p).references(citing["id"])[0]["state"] == expected
    assert f'data-recording="{"complete" if failure == "digest" else "incomplete"}"' in page
    assert 'data-folder="same"' in page
    if failure == "digest":
        assert "Cited bytes differ" in page and "Recorded package found" not in page
    if failure == "ambiguous":
        assert "Multiple completion records" in page and "Recorded package found" in page


@pytest.mark.parametrize("failure", ["io", "updating"])
def test_unchecked_folder_is_never_shown_as_matching(tmp_path, monkeypatch, failure):
    p, result, citing, _completion = publication(tmp_path)
    site = workspace.Site(p)
    if failure == "io":
        def unreadable(*args, **kwargs):
            raise PermissionError("synthetic unreadable directory")
        monkeypatch.setattr(state.checks, "verify_package", unreadable)
    else:
        def unreadable(*args, **kwargs):
            raise PublicationError("records_updating", "synthetic concurrent write")
        monkeypatch.setattr(state, "observe", unreadable)
    judged = site.model.references(citing["id"])[0]
    html = plain(site.ref_html(judged, 0))
    assert "Recorded package found" in html and 'data-folder="not_checked"' in html
    assert "Package checks passed" not in html
    assert result["publication_id"] in html


def test_publication_words_have_both_catalogs_and_keep_original_identity(tmp_path):
    p, result, citing, _completion = publication(tmp_path)
    site = workspace.Site(p)
    html = site.ref_html(site.model.references(citing["id"])[0], 0)
    for key in i18n.catalog("en"):
        if key.startswith("publication."):
            assert key in i18n.catalog("ko")
    assert 'lang=""' in html and result["publication_id"] in html
    assert i18n.message("publication.reference_found", "ko") == "기록된 패키지를 찾음"


@pytest.mark.parametrize("details", [None, [], ["invalid"], "invalid", {"publication_id": ["invalid"]}])
def test_malformed_completion_stays_visible_without_stopping_workspace(tmp_path, details):
    p, _result, citing, completion = publication(tmp_path)
    rows = [json.loads(line) for line in p.journal.read_bytes().splitlines()]
    next(row for row in rows if row["id"] == completion["id"])["details"] = details
    p.journal.write_bytes(b"".join(json.dumps(row).encode("utf-8") + b"\n" for row in rows))
    journal = p.journal.read_bytes()
    workspace.build(p)
    index, listing, _cited_page, completion_page = pages(p, citing, completion)
    for page in (index, listing, completion_page):
        assert 'data-recording="not_checked"' in page and 'data-folder="not_checked"' in page
        assert "Not checked" in page
    assert p.journal.read_bytes() == journal
