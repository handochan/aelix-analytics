"""Byte-only format safety and actual preserved v1 -> renderer -> repeat immutability."""
import base64
import json

import pytest
from test_report_metadata import case, directory, save, snapshot  # noqa: F401

from aelix_analytics import core
from aelix_analytics.report_renderers import PREVIEW_PATH, StaticReportRenderer


def render(content, entry="report.md", **attachments):
    files = {entry: content, **attachments}
    return StaticReportRenderer().render(files, entry=entry, paths={name: "original/" + name for name in files})


def page(view):
    return view.files[PREVIEW_PATH].decode()


def test_markdown_html_is_escaped_and_only_explicit_local_resources_are_rewritten():
    view = render(b'# Heading\n<script>alert(1)</script>\n[local](sub/data.csv)\n', **{"sub/data.csv": b"x\n1\n"})
    assert view.supported
    assert "<h1>Heading</h1>" in page(view) and "<script>" not in page(view)
    assert "&lt;script&gt;" in page(view)
    assert 'href="../original/sub/data.csv" download' in page(view)
    assert view.originals["report.md"] == "../original/report.md"


@pytest.mark.parametrize("url", ["https://example.invalid/tracker", "//example.invalid/a", "data:text/html,evil", "javascript:alert(1)",
                                 "../../escape.csv", "/absolute.csv", "missing.csv", "%2e%2e/%2e%2e/escape.csv", "a.csv?remote=true"])
def test_external_and_unmapped_links_never_become_active_resources(url):
    view = render(("[link](" + url + ")").encode())
    text = page(view)
    assert ('href="' + url) not in text
    if url.startswith(("data:", "javascript:")):
        # markdown-it itself refuses these; literal text is safe and not a rendered link.
        assert "<a href=\"javascript:" not in text and "<a href=\"data:" not in text
    else:
        assert not view.supported and view.reasons == ("unmapped_or_external_resource",)


def test_links_resolve_from_nested_entry_and_unicode_mapping_without_current_folder_lookup():
    files = {"nested/report.md": "[한글](../첨부.csv#표)".encode(), "첨부.csv": b"x\n"}
    view = StaticReportRenderer().render(files, entry="nested/report.md", paths={"nested/report.md": "downloads/r.md", "첨부.csv": "downloads/첨부.csv"})
    assert view.supported
    assert "../downloads/%EC%B2%A8%EB%B6%80.csv#%ED%91%9C" in page(view)


@pytest.mark.parametrize("entry", ["unsafe.html", "unsafe.htm", "unsafe.svg", "slides.pptx", "report.pdf", "unknown.bin"])
def test_arbitrary_html_office_pdf_and_binary_are_download_only(entry):
    dangerous = b'<script>fetch("https://example.invalid")</script><iframe src="//example.invalid"></iframe>'
    view = render(dangerous, entry)
    assert not view.supported and len(view.reasons) == 1
    assert dangerous.decode() not in page(view) and "<script" not in page(view) and "<iframe" not in page(view)
    assert entry in view.originals and '<a download href="' in page(view)
    assert "default-src &#" not in page(view)  # static CSP is emitted as a proper meta value
    assert "default-src 'none'" in page(view)


def test_image_is_explicit_download_fallback_and_invalid_utf8_has_a_reason():
    view = render(b'![alt](image.svg)', **{"image.svg": b"<svg><script/></svg>"})
    assert not view.supported and view.reasons == ("image_download_only",)
    assert "<img" not in page(view) and "<span>alt</span>" in page(view)
    assert not render(b"\xff").supported and render(b"\xff").reasons == ("text_encoding_unsupported",)


@pytest.mark.parametrize("files,entry,paths", [
    ({"a.md": b"ok"}, "a.md", {"a.md": "../outside"}),
    ({"../escape": b"ok"}, "../escape", {"../escape": "original/a.md"}),
    ({"a.md": b"ok"}, "missing", {"a.md": "original/a.md"}),
    ({"a.md": b"ok"}, "a.md", {}),
    ({"a.md": b"ok"}, "a.md", {"a.md": PREVIEW_PATH}),
    ({"a.md": b"ok", "b.md": b"ok"}, "a.md", {"a.md": "original/A", "b.md": "original/a"}),
])
def test_invalid_renderer_paths_refuse_instead_of_guessing(files, entry, paths):
    with pytest.raises(ValueError):
        StaticReportRenderer().render(files, entry=entry, paths=paths)


def test_saved_v1_bytes_basis_metadata_and_collection_unchanged_after_v2_and_repeated_render(case):  # noqa: F811
    first = save(case, metadata={"summary": "v1", "content_status": "partial"})
    path = directory(case, first)
    frozen = snapshot(path)
    revision = first["report"]["revision_id"]
    chosen = core.reports(case[0], "weekly", revision, file="요약.md")["file"]
    files = {chosen["path"]: base64.b64decode(chosen["data"])}
    mapping = {chosen["path"]: "original/요약.md"}
    renderer = StaticReportRenderer()
    rendered = renderer.render(files, entry="요약.md", paths=mapping)
    (case[1] / "요약.md").write_bytes(b"new version")
    save(case, request="req-2", previous=revision, metadata={"summary": "v2", "content_status": "complete"})
    assert renderer.render(files, entry="요약.md", paths=mapping) == rendered
    assert snapshot(path) == frozen
    assert json.loads(frozen["manifest.json"])["content_status"] == "partial"
