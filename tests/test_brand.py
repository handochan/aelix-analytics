"""The Aelix mark (#204): the tab icon of every page the kit builds, and the small mark in its footer.

The owner chose the footer (2026-11-20) over the top bar and over the tab icon alone, for the Workspace
and for shared copies. The mark is the brand's own 48-pixel PNG, kept byte for byte.

Every kit page now holds the mark's base64 twice, and it spells short strings by chance (`v1`, `v3`,
`e2`, `e9`, ...). A test that looks for a short string in a whole page removes `brand.MARK_URI` first,
or looks only inside the part of the page it means.
"""

from __future__ import annotations

import base64
import hashlib
import re
import struct

from publication_reader_fixture import project as shared_project
from publication_reader_fixture import saved

from aelix_analytics import brand, core, workspace

ORIGIN = core.Origin("human", "cli", "declared")

# The brand's 48 x 48 entry of aelix-ai docs/assets/brand/favicon.ico (commit 651f71e).
BRAND_PNG_SHA256 = "05cc7992baef6b13bf01f01735bab2de94d20f25327a3c9a3bd82b9c24662262"


def chunks(png: bytes):
    """The PNG's chunk types in order, after its signature."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    at, found = 8, []
    while at < len(png):
        (size,) = struct.unpack(">I", png[at:at + 4])
        found.append((png[at + 4:at + 8].decode("ascii"), png[at + 8:at + 8 + size]))
        at += 12 + size
    assert at == len(png)
    return found


def test_the_mark_is_the_brand_s_png_with_nothing_but_its_image():
    png = base64.b64decode(brand.MARK_PNG_BASE64, validate=True)
    assert hashlib.sha256(png).hexdigest() == BRAND_PNG_SHA256
    found = chunks(png)
    # No text, time or colour-profile chunk: nothing of where or when it was made rides on every page.
    assert [kind for kind, _ in found] == ["IHDR", "IDAT", "IEND"]
    width, height, depth, colour = struct.unpack(">IIBB", found[0][1][:10])
    assert (width, height, depth, colour) == (48, 48, 8, 6)  # 8-bit RGBA: the transparent ground
    assert brand.MARK_URI == "data:image/png;base64," + brand.MARK_PNG_BASE64
    assert brand.ICON_LINK == f'<link rel="icon" type="image/png" sizes="48x48" href="{brand.MARK_URI}">'
    # Decorative: the words beside it already name the product.
    assert brand.mark() == f'<img class="mark" src="{brand.MARK_URI}" alt="" width="16" height="16">'


def icons(head: str) -> int:
    return len(re.findall(r"<link\b[^>]*\brel=\"[^\"]*\bicon\b", head))


def test_every_workspace_page_has_the_icon_and_the_mark_in_its_footer(tmp_path):
    # A project with a run, a script, figures, records and two Reports: the kinds of page it builds.
    p, _ids = shared_project(tmp_path / "project")
    workspace.build(p)
    # The kit's pages (C21: `data-build` on <html>); a Report's own files are the author's.
    pages = {path: text for path in sorted(p.workspace_dir.rglob("*.html"))
             if 'data-build="' in (text := path.read_text(encoding="utf-8")).split(">", 2)[1]}
    folders = {path.relative_to(p.workspace_dir).parts[0] for path in pages if path.parent != p.workspace_dir}
    assert {"runs", "scripts", "relations", "lists", "reports"} <= folders, folders
    for path, text in pages.items():
        head = text.split("</head>", 1)[0]
        assert head.count(brand.ICON_LINK) == 1 and icons(head) == 1, path
        assert text.count(brand.mark()) == 1, path
        footer = re.search(r"<footer>(.*?)</footer>", text, re.S).group(1)
        assert footer.startswith(brand.mark() + f"Aelix Analytics {core.KIT_VERSION} · "), path


def test_every_kit_page_of_a_shared_copy_has_the_icon_and_the_mark_in_its_footer(tmp_path):
    p, _ids = shared_project(tmp_path / "project")
    result = saved(p, reports=[("weekly", ""), ("page", "")])
    folder = p.root / result["path"]
    # The kit's pages; a Report's own documents and its renderer's view are authored content.
    pages = sorted(f for f in folder.rglob("*.html")
                   if "/content/" not in f.as_posix() and not f.as_posix().endswith("/preview/report.html"))
    assert len(pages) > 5
    for path in pages:
        text = path.read_text(encoding="utf-8")
        head = text.split("</head>", 1)[0]
        assert head.count(brand.ICON_LINK) == 1 and icons(head) == 1, path
        assert text.count(brand.mark()) == 1, path
        footer = re.search(r"<footer>(.*?)</footer>", text, re.S).group(1)
        assert footer == brand.mark() + "Aelix Analytics · This copy is a fixed snapshot. Its source may have changed.", path



def test_an_excluded_item_named_like_a_word_of_the_mark_does_not_stop_a_shared_copy(tmp_path):
    """The mark's URI spells short words (`png`, `base64`, and base64 pieces between `+` and `/`, such
    as `sw` and `v9`); an excluded Report with such an ID is a forbidden word in a page's references.
    The project has no other PNG, so the mark is the only place those words are."""
    core.init_project(tmp_path / "p", title="Words", origin=ORIGIN)
    p = core.Project(tmp_path / "p")
    core.record(p, "finding", "A finding", origin=ORIGIN)
    source = tmp_path / "words"
    source.mkdir()
    (source / "r.md").write_text("# r\n", encoding="utf-8")
    words = ("png", "base64", "sw", "v9")
    assert all(re.search(rf"(?<!\w){w}(?!\w)", brand.MARK_URI) for w in words)
    for report_id in words:
        core.save_report(p, report_id, files=source, entry="r.md", title=f"Report {report_id}",
                         previous_revision=None, origin=ORIGIN)
    # Before #204's narrow pass in check_strings, this build stopped with leak_found at link@href.
    result = saved(p, profile="read")
    about = (p.root / result["path"] / "about.html").read_text(encoding="utf-8")
    assert about.count(brand.MARK_URI) == 2  # the icon and the footer mark
