"""#20 PR4: Report and collection scopes of a private publication (PUBLICATION-BUILD 5.1, 6.2, 7.5, 12.4)."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import zlib

import pytest
from conftest import kit_env

from aelix_analytics import core
from aelix_analytics.publishing import build, checks
from aelix_analytics.publishing.model import ItemKey, PublicationError, page_key
from aelix_analytics.publishing.projection import ShareRequest
from aelix_analytics.report_renderer import ReportView
from aelix_analytics.report_renderers import StaticReportRenderer

ORIGIN = core.Origin("human", "cli")
RENDERER = StaticReportRenderer()
CANARY = "EXCLUDED_REPORT_CANARY_7f31ab"
UNCITED = "An uncited finding that the Report copy must not carry"


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


PNG = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
       + _chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + _chunk(b"IEND", b""))


def project(tmp_path):
    p = core.init_project(tmp_path / "project", title="Public title", origin=ORIGIN)
    core.sync_context(p, origin=core.system_origin(ORIGIN))
    return p


def folder(tmp_path, name, files):
    root = tmp_path / name
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return root


def save(p, report_id, source, entry, *, previous=None, based_on=None, metadata=None, title="Weekly report"):
    return core.save_report(p, report_id, files=source, entry=entry, title=title, previous_revision=previous,
                            based_on=based_on, metadata=metadata, origin=ORIGIN)["report"]


def cites(*event_ids):
    return {"as_of_seq": None, "context_version": None,
            "refs": [{"type": "event", "event_id": e, "relation": "cites", "basis": "declared",
                      "version_binding": "author"} for e in event_ids]}


def preview(p, *reports, scope="reports", **options):
    request = ShareRequest(scope=scope, reports=tuple(reports), **options)
    return build.preview_package(p, request, report_renderer=RENDERER)


def refused(call):
    with pytest.raises(PublicationError) as caught:
        call()
    return caught.value


def report_page(files, report_id, revision_id):
    return files[f"reports/{page_key(ItemKey('report', f'{report_id}/{revision_id}'))}/index.html"].decode("utf-8")


@pytest.mark.parametrize("profile", ["read", "evidence"])
def test_report_copy_holds_the_selected_version_and_only_its_cited_evidence(tmp_path, profile):
    p = project(tmp_path)
    cited = core.record(p, "finding", "Sales rose in the north region", origin=ORIGIN).event
    core.record(p, "finding", UNCITED, origin=ORIGIN)
    source = folder(tmp_path, "src", {"r.md": "# Weekly\n\nNorth grew.\n"})
    v1 = save(p, "weekly", source, "r.md", based_on=cites(cited["id"]),
              metadata={"summary": "Short summary", "purpose": "Share weekly", "content_status": "partial",
                        "scope": {"private": CANARY}, "generation": {"model": CANARY}, "future_field": CANARY})
    summary, files = build.preview_package(p, ShareRequest(scope="reports", reports=(("weekly", ""),)),
                                           profile=profile, report_renderer=RENDERER)
    data = b"".join(files.values())
    assert summary["scope"] == {"kind": "reports", "reports": [f"weekly@{v1['revision_id']}"]}
    assert summary["included"] == {"event": 1, "project": 1, "report": 1}
    assert b"Sales rose in the north region" in data and UNCITED.encode() not in data
    # OD1: the four descriptions; never scope, generation or an unknown optional field.
    page = report_page(files, "weekly", v1["revision_id"])
    for shown in ("Short summary", "Share weekly", "partial", "Version 1 of 1"):
        assert shown in page
    assert CANARY.encode() not in data
    # CONTEXT is only the copy's basis here: named, its text not included (5.1, 6.4).
    assert b"(not included in this publication)" in files["index.html"]
    assert not any(k.startswith("pages/context/") for k in files)


def test_head_selection_later_version_and_repeated_builds_keep_the_old_bytes(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "first text\n"}), "r.md")
    built = build.build_publication(p, ShareRequest(scope="reports", reports=(("weekly", v1["revision_id"]),)),
                                    origin=ORIGIN, report_renderer=RENDERER)
    save(p, "weekly", folder(tmp_path, "v2", {"r.md": "second text\n"}), "r.md", previous=v1["revision_id"])
    again = build.build_publication(p, ShareRequest(scope="reports", reports=(("weekly", v1["revision_id"]),)),
                                    origin=ORIGIN, report_renderer=RENDERER)
    head = build.build_publication(p, ShareRequest(scope="reports", reports=(("weekly", ""),)),
                                   origin=ORIGIN, report_renderer=RENDERER)
    assert head["scope"]["reports"][0] != f"weekly@{v1['revision_id']}"
    first, second = (p.root / built["path"], p.root / again["path"])
    copied = f"reports/{page_key(ItemKey('report', 'weekly/' + v1['revision_id']))}/content/r.md"
    assert (first / copied).read_bytes() == (second / copied).read_bytes() == b"first text\n"
    assert built["publication_id"] != again["publication_id"]
    assert b"second text" not in b"".join(f.read_bytes() for f in second.rglob("*") if f.is_file())
    page = (second / copied.replace("content/r.md", "index.html")).read_text(encoding="utf-8")
    assert "A later version existed when this copy was built — not included." in page
    manifest = json.loads((second / "publication.json").read_text(encoding="utf-8"))
    selected = manifest["scope"]["reports"]
    assert [(r["report_id"], r["revision_id"], r["version"], r["state"]) for r in selected] == [
        ("weekly", v1["revision_id"], 1, "current")]
    assert selected[0]["files"] == [{"path": "r.md", "sha256": manifest_file(second, copied), "package_path": copied}]
    assert checks.verify_package(second)["package_sha256"]
    event = next(e for e in core.read_model(p).journal.in_order() if e.kind == "publication"
                 and e.value["details"]["publication_id"] == again["publication_id"])
    report_refs = [r for r in event.refs if r["type"] == "report_revision"]
    assert report_refs == [{"type": "report_revision", "report_id": "weekly", "revision_id": v1["revision_id"],
                            "digest": {"sha256": selected[0]["manifest_sha256"]}, "relation": "includes",
                            "basis": "declared", "version_binding": "author"}]


def manifest_file(folder_path, package_path):
    import hashlib

    return hashlib.sha256((folder_path / package_path).read_bytes()).hexdigest()


def test_same_basis_report_builds_have_the_same_content_hash(tmp_path):
    p = project(tmp_path)
    save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n"}), "r.md")
    a, _ = preview(p, ("weekly", ""))
    b, _ = preview(p, ("weekly", ""))
    assert a["content_sha256"] == b["content_sha256"]


def test_project_copy_lists_selected_reports_after_its_analysis(tmp_path):
    p = project(tmp_path)
    core.record(p, "finding", "Project finding stays", origin=ORIGIN)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n"}), "r.md")
    plain, plain_files = build.preview_package(p)
    assert plain["included"].get("report", 0) == 0
    summary, files = preview(p, ("weekly", v1["revision_id"]), scope="project")
    assert summary["included"]["report"] == 1 and summary["included"]["context"] == 1
    index = files["index.html"].decode("utf-8")
    assert index.index("Finding:") < index.index("Report version:")


def test_withdrawn_version_needs_permission_and_says_so_everywhere(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n"}), "r.md")
    core.update_report(p, "weekly", v1["revision_id"], expected="current", new="withdrawn", reason="wrong", origin=ORIGIN)
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert (error.code, error.details["reason"]) == ("report_not_shareable", "withdrawn")
    _summary, files = preview(p, ("weekly", v1["revision_id"]), allow_withdrawn=True)
    assert "Withdrawn" in report_page(files, "weekly", v1["revision_id"])
    assert "· Withdrawn" in files["index.html"].decode("utf-8")
    assert "Withdrawn</p>" in files["about.html"].decode("utf-8")  # the band of every page


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("<!doctype html><p>Hi</p><script>alert(1)</script>", "scripts"),
        ('<!doctype html><p onclick="x()">Hi</p>', "scripts"),
        ('<!doctype html><img src="https://example.com/a.png">', "remote_resources"),
    ],
)
def test_unsafe_report_html_stops_the_build(tmp_path, body, reason):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.html": body}), "r.html")
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert error.code == "report_not_shareable" and error.details["reason"] == reason
    assert error.details["file"] == "r.html"


def test_safe_report_html_opens_in_a_frame_without_scripts(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.html": '<!doctype html><p>Body</p><img src="img/a.png" alt="a">',
                                                    "img/a.png": PNG}), "r.html")
    _summary, files = preview(p, ("weekly", v1["revision_id"]))
    page = report_page(files, "weekly", v1["revision_id"])
    assert '<iframe src="content/r.html" sandbox="allow-same-origin" title="Report body">' in page
    assert "allow-scripts" not in page


def test_report_html_link_outside_its_files_stops_the_build(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.html": '<!doctype html><img src="missing.png" alt="a">'}), "r.html")
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert (error.code, error.details["reason"]) == ("report_not_shareable", "link")


@pytest.mark.parametrize("data", [b"%PDF-1.7\n%...", b"PK\x03\x04office"])
def test_uninspectable_report_file_stops_the_build(tmp_path, data):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n", "attachment.bin": data}), "r.md")
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert error.code == "report_not_shareable"
    assert (error.details["reason"], error.details["file"]) == ("format", "attachment.bin")


def test_free_text_naming_content_outside_the_copy_stops_the_build(tmp_path):
    p = project(tmp_path)
    core.record(p, "finding", UNCITED, origin=ORIGIN)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": f"# Weekly\n\n{UNCITED}\n"}), "r.md")
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert error.code == "leak_found"


def test_subfolder_entry_links_and_unicode_paths_stay_whole(tmp_path):
    p = project(tmp_path)
    source = folder(tmp_path, "v1", {"sub/보고서.md": "# 주간\n\n![그림](img/a.png) [표](../data.csv)\n",
                                     "sub/img/a.png": PNG, "data.csv": "a,b\n1,2\n"})
    v1 = save(p, "weekly", source, "sub/보고서.md")
    _summary, files = preview(p, ("weekly", v1["revision_id"]))
    token = page_key(ItemKey("report", f"weekly/{v1['revision_id']}"))
    for name in ("sub/보고서.md", "sub/img/a.png", "data.csv"):
        assert f"reports/{token}/content/{name}" in files
    view = files[f"reports/{token}/preview/report.html"].decode("utf-8")
    assert 'href="../content/data.csv"' in view  # resolved from the entry's folder (7.5)
    built = build.build_publication(p, ShareRequest(scope="reports", reports=(("weekly", ""),)), origin=ORIGIN,
                                    report_renderer=RENDERER)
    assert (p.root / built["path"] / f"reports/{token}/content/sub/보고서.md").is_file()


def test_changed_or_missing_report_file_is_refused(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n", "a.csv": "a\n1\n"}), "r.md")
    stored = p.root / f"artifacts/reports/weekly/{v1['revision_id']}/content/a.csv"
    stored.write_text("a\n2\n", encoding="utf-8")
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert (error.code, error.details["reason"]) == ("report_not_shareable", "content_changed")
    stored.unlink()
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert (error.code, error.details["reason"]) == ("report_not_shareable", "missing")


def test_unknown_report_or_selection_problems_are_scope_errors(tmp_path):
    p = project(tmp_path)
    save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n"}), "r.md")
    assert refused(lambda: preview(p, ("nothing", ""))).code == "scope_invalid"
    assert refused(lambda: preview(p, ("weekly", "01NOTAVERSION"))).code == "scope_invalid"
    assert refused(lambda: build.preview_package(p, ShareRequest(scope="reports"))).code == "scope_invalid"
    error = refused(lambda: build.preview_package(p, ShareRequest(scope="reports", reports=(("weekly", ""),))))
    assert error.code == "renderer_unavailable"
    exclude = refused(lambda: build.preview_package(
        p, ShareRequest(scope="reports", reports=(("weekly", ""),)), exclude=[f"weekly@{_head(p)}"],
        report_renderer=RENDERER))
    assert exclude.code == "scope_invalid"


def _head(p):
    return core.read_model(p).report_histories()["weekly"]["versions"][-1]["revision_id"]


def test_collection_copy_holds_its_complete_members_and_shows_partial_state(tmp_path):
    p = project(tmp_path)
    member = save(p, "region-north", folder(tmp_path, "m", {"r.md": "north body\n"}), "r.md", title="North")
    collection = save(
        p, "regions", folder(tmp_path, "c", {"index.md": "# Regions\n"}), "index.md", title="Regions",
        metadata={"content_status": "partial", "collection": {"contract": "report_collection/1", "members": [
            {"member_key": "North", "status": "complete", "report_id": "region-north",
             "revision_id": member["revision_id"], "scope": {"private": CANARY}},
            {"member_key": "South", "status": "failed", "reason": "no data for the south"},
        ]}})
    request = ShareRequest(scope="collection", reports=(("regions", collection["revision_id"]),))
    summary, files = build.preview_package(p, request, report_renderer=RENDERER)
    assert summary["scope"]["reports"] == [f"regions@{collection['revision_id']}",
                                           f"region-north@{member['revision_id']}"]
    page = report_page(files, "regions", collection["revision_id"])
    assert "1 of 2 complete · 1 failed · 0 not generated" in page
    assert "no data for the south" in page and "Open member version" in page
    assert CANARY.encode() not in b"".join(files.values())
    assert b"north body" in b"".join(files.values())
    built = build.build_publication(p, request, origin=ORIGIN, report_renderer=RENDERER)
    manifest = json.loads((p.root / built["path"] / "publication.json").read_text(encoding="utf-8"))
    assert manifest["scope"]["kind"] == "reports"
    assert [r["report_id"] for r in manifest["scope"]["reports"]] == ["regions", "region-north"]
    not_collection = refused(lambda: build.preview_package(
        p, ShareRequest(scope="collection", reports=(("region-north", member["revision_id"]),)),
        report_renderer=RENDERER))
    assert not_collection.code == "scope_invalid"


def test_saving_attempt_is_not_a_version(tmp_path, monkeypatch):
    p = project(tmp_path)
    source = folder(tmp_path, "v1", {"r.md": "text\n"})

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    from aelix_analytics import writer

    monkeypatch.setattr(writer.Locked, "append", interrupt)
    with pytest.raises(KeyboardInterrupt):
        save(p, "draft", source, "r.md")
    monkeypatch.undo()
    attempts = core.read_model(p).report_histories().get("draft", {}).get("attempts", [])
    if not attempts:
        pytest.skip("this kit leaves no attempt for an interrupted completion")
    error = refused(lambda: preview(p, ("draft", "")))
    assert (error.code, error.details["reason"]) == ("report_not_shareable", "not_saved")


class ScriptRenderer:
    def render(self, files, *, entry, paths):
        return ReportView({"preview/report.html": b"<!doctype html><script>fetch('x')</script>"}, {}, True)


class RefusingRenderer:
    def render(self, files, *, entry, paths):
        raise ValueError("no")


def test_injected_renderer_output_is_checked_like_authored_content(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n"}), "r.md")
    request = ShareRequest(scope="reports", reports=(("weekly", v1["revision_id"]),))
    error = refused(lambda: build.preview_package(p, request, report_renderer=ScriptRenderer()))
    assert error.code == "package_check_failed"
    _summary, files = build.preview_package(p, request, report_renderer=RefusingRenderer())
    assert "This body is not displayed in this copy." in report_page(files, "weekly", v1["revision_id"])


def test_verify_checks_each_selected_file_mapping(tmp_path):
    p = project(tmp_path)
    save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n"}), "r.md")
    built = build.build_publication(p, ShareRequest(scope="reports", reports=(("weekly", ""),)), origin=ORIGIN,
                                    report_renderer=RENDERER)
    root = p.root / built["path"]
    files = {f.relative_to(root).as_posix(): f.read_bytes() for f in root.rglob("*") if f.is_file()}
    manifest = json.loads(files["publication.json"])
    manifest["scope"]["reports"][0]["files"][0]["sha256"] = "0" * 64
    files["publication.json"] = json.dumps(manifest).encode()
    assert refused(lambda: checks.verify_package(files)).code == "package_check_failed"


def kit(p, *args):
    done = subprocess.run([sys.executable, "-m", "aelix_analytics", "--project", str(p.root), "build", *args],
                          capture_output=True, text=True, encoding="utf-8", env=kit_env(), check=False)
    return done.returncode, json.loads(done.stdout)


def test_cli_report_selection(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n"}), "r.md")
    code, out = kit(p, "--preview", "--report", "weekly")
    assert code == 0 and out["scope"] == {"kind": "reports", "reports": [f"weekly@{v1['revision_id']}"]}
    code, out = kit(p, "--preview", "--scope", "project", "--report", f"weekly@{v1['revision_id']}")
    assert code == 0 and out["scope"]["kind"] == "project"
    for args in (("--scope", "reports"), ("--collection", "weekly"), ("--collection", "weekly@x", "--report", "weekly"),
                 ("--report", "weekly@")):
        code, out = kit(p, "--preview", *args)
        assert code == 1 and out["error"]["code"] == "scope_invalid", args
    code, out = kit(p, "--report", "weekly")
    assert code == 0 and out["recording_status"] == "complete" and out["scope"]["kind"] == "reports"


def test_report_file_names_that_need_url_escaping_still_link(tmp_path):
    p = project(tmp_path)
    source = folder(tmp_path, "v1", {"r.md": "[note](a b%23#1.txt)\n", "a b#1%.txt": "kept\n",
                                     "page.html": '<!doctype html><a href="a%20b%231%25.txt">x</a>'})
    v1 = save(p, "weekly", source, "r.md")
    built = build.build_publication(p, ShareRequest(scope="reports", reports=(("weekly", ""),)), origin=ORIGIN,
                                    report_renderer=RENDERER)
    root = p.root / built["path"]
    token = page_key(ItemKey("report", f"weekly/{v1['revision_id']}"))
    assert (root / f"reports/{token}/content/a b#1%.txt").read_text(encoding="utf-8") == "kept\n"
    assert 'href="content/a%20b%231%25.txt"' in (root / f"reports/{token}/index.html").read_text(encoding="utf-8")
    assert checks.verify_package(root)["package_sha256"] == built["package_sha256"]


def test_report_path_too_long_for_a_portable_copy_is_refused(tmp_path):
    p = project(tmp_path)
    deep = "/".join(["d" * 40] * 6) + "/r.md"
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "x\n", deep: "y\n"}), "r.md")
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert (error.code, error.details["reason"], error.details["file"]) == ("report_not_shareable", "path", deep)


def test_an_unselected_report_named_like_a_page_word_is_not_a_leak(tmp_path):
    p = project(tmp_path)
    save(p, "evidence", folder(tmp_path, "other", {"r.md": "other\n"}), "r.md", title="Other")
    save(p, "collection", folder(tmp_path, "other2", {"r.md": "other\n"}), "r.md", title="Other")
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n"}), "r.md")
    summary, _files = preview(p, ("weekly", v1["revision_id"]))
    assert summary["included"]["report"] == 1


def test_html_entry_needs_no_derived_view_document(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.html": "<!doctype html><p>Body</p>"}), "r.html")
    _summary, files = preview(p, ("weekly", v1["revision_id"]))
    assert not any(k.endswith("/preview/report.html") for k in files)


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ({"r.html": '<!doctype html><link rel="stylesheet" href="style.css"><p>Body</p>',
          "style.css": '@import url("https://example.com/private.css");'}, "remote_resources"),
        ({"r.html": '<?xml version="1.0"?><html><body><script>alert(1)</script></body></html>'}, "scripts"),
        ({"r.md": "text\n", "notes.htm": "plain words <img src=https://example.com/a.png>"}, "remote_resources"),
    ],
)
def test_report_files_are_inspected_as_what_their_names_say(tmp_path, files, reason):
    p = project(tmp_path)
    entry = "r.md" if "r.md" in files else "r.html"
    v1 = save(p, "weekly", folder(tmp_path, "v1", files), entry)
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert (error.code, error.details["reason"]) == ("report_not_shareable", reason)


def test_report_xml_file_is_not_shareable(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n",
                                                    "data.xml": '<?xml-stylesheet href="x.xsl"?><root/>'}), "r.md")
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert (error.code, error.details["reason"], error.details["file"]) == ("report_not_shareable", "format", "data.xml")


def test_project_copy_with_a_selected_report_builds_and_verifies(tmp_path):
    p = project(tmp_path)
    core.record(p, "finding", "Project finding stays", origin=ORIGIN)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n"}), "r.md")
    built = build.build_publication(p, ShareRequest(scope="project", reports=(("weekly", v1["revision_id"]),)),
                                    origin=ORIGIN, report_renderer=RENDERER)
    manifest = json.loads((p.root / built["path"] / "publication.json").read_text(encoding="utf-8"))
    assert manifest["scope"]["kind"] == "project"
    assert [r["revision_id"] for r in manifest["scope"]["reports"]] == [v1["revision_id"]]
    assert checks.verify_package(p.root / built["path"])["package_sha256"] == built["package_sha256"]


@pytest.mark.parametrize(
    ("files", "code", "reason"),
    [
        ({"r.md": "x\n", "notes.html": "Plain start <script>alert(1)</script>"}, "report_not_shareable", "scripts"),
        ({"r.md": "x\n", "a.svg": '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg" onload="x()"/>'},
         "inspection_failed", None),
        ({"r.html": '<!doctype html><a href="#missing">x</a>'}, "report_not_shareable", "link"),
    ],
)
def test_more_report_file_shapes_are_checked(tmp_path, files, code, reason):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", files), "r.md" if "r.md" in files else "r.html")
    error = refused(lambda: preview(p, ("weekly", v1["revision_id"])))
    assert error.code == code and error.details.get("reason") == reason


def test_a_null_basis_is_not_printed(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "x\n"}), "r.md",
              based_on={"as_of_seq": None, "context_version": None, "refs": []})
    _summary, files = preview(p, ("weekly", v1["revision_id"]))
    data = b"".join(files.values())
    assert b"eNone" not in data and b">null" not in data


def test_a_gap_between_selected_versions_is_named(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "one\n"}), "r.md")
    v2 = save(p, "weekly", folder(tmp_path, "v2", {"r.md": "two\n"}), "r.md", previous=v1["revision_id"])
    v3 = save(p, "weekly", folder(tmp_path, "v3", {"r.md": "three\n"}), "r.md", previous=v2["revision_id"])
    _summary, files = preview(p, ("weekly", v1["revision_id"]), ("weekly", v3["revision_id"]))
    assert "A later version existed" in report_page(files, "weekly", v1["revision_id"])
    assert "A later version existed" not in report_page(files, "weekly", v3["revision_id"])


def test_read_only_modes_still_accept_the_default_scope(tmp_path):
    p = project(tmp_path)
    code, out = kit(p, "--list", "--scope", "project")
    assert code == 0 and out["ok"]


def test_verify_checks_a_report_view_as_authored_content(tmp_path):
    p = project(tmp_path)
    v1 = save(p, "weekly", folder(tmp_path, "v1", {"r.md": "text\n"}), "r.md")
    built = build.build_publication(p, ShareRequest(scope="reports", reports=(("weekly", ""),)), origin=ORIGIN,
                                    report_renderer=RENDERER)
    root = p.root / built["path"]
    files = {f.relative_to(root).as_posix(): f.read_bytes() for f in root.rglob("*") if f.is_file()}
    token = page_key(ItemKey("report", f"weekly/{v1['revision_id']}"))
    view = f"reports/{token}/preview/report.html"
    tampered = files[view].replace(b"</body>", b"<script>alert(1)</script></body>")
    files[view] = tampered
    manifest = json.loads(files["publication.json"])
    import hashlib

    row = next(r for r in manifest["files"] if r["path"] == view)
    row.update(sha256=hashlib.sha256(tampered).hexdigest(), bytes=len(tampered))
    manifest["content_sha256"] = checks.content_hash({k: v for k, v in files.items() if k != "publication.json"})
    files["publication.json"] = json.dumps(manifest).encode()
    assert refused(lambda: checks.verify_package(files)).code == "package_check_failed"
