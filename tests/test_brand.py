"""The Aelix mark (#204): the tab icon of every page the kit builds, and the small mark in its footer.

The owner chose the footer (2026-11-20) over the top bar and over the tab icon alone, for the Workspace
and for shared copies. The mark is the brand's own 48-pixel PNG, kept byte for byte.
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


def test_every_workspace_page_has_the_icon_and_the_mark_in_its_footer(tmp_path):
    core.init_project(tmp_path / "p", title="Mark", goal="마크 확인", origin=ORIGIN)
    p = core.Project(tmp_path / "p")
    core.record(p, "finding", "A finding", origin=ORIGIN)
    workspace.build(p)
    pages = sorted(p.workspace_dir.rglob("*.html"))
    assert len(pages) > 5
    for path in pages:
        text = path.read_text(encoding="utf-8")
        head = text.split("</head>", 1)[0]
        assert head.count(brand.ICON_LINK) == 1, path
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
        assert head.count(brand.ICON_LINK) == 1 and 'href="data:,"' not in head, path
        assert text.count(brand.mark()) == 1, path
        footer = re.search(r"<footer>(.*?)</footer>", text, re.S).group(1)
        assert footer == brand.mark() + "Aelix Analytics · This copy is a fixed snapshot. Its source may have changed.", path
