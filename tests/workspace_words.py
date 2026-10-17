"""Workspace pages as most tests read them: the kit's words without their switching markup.

Issue #96 puts each of the kit's words in a `<k-w>` element (bare, or with its key `k` and values
`a`) and adds `data-i18n-<attribute>` beside a switchable attribute, so a page can change language
in the browser. `plain` takes that markup away and gives the HTML a page was before, where a test
checks words and links rather than the switching itself (tests/test_workspace_i18n.py checks that).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

WORD = re.compile(r'<k-w(?: k="[^"]*")?(?: a=\'[^\']*\')?>([^<]*)</k-w>')
ATTRIBUTE = re.compile(r' data-i18n-[a-z-]+(?:="[^"]*"|=\'[^\']*\')?(?=[ >])')


def plain(page: str) -> str:
    return ATTRIBUTE.sub("", WORD.sub(r"\1", page))


class _Badges(HTMLParser):
    """Each reference badge (`<span class="ref …" data-state="…">`, workspace.Site.ref_badge) and
    its text, its parts joined by single spaces."""

    def __init__(self):
        super().__init__()
        self.found: list[tuple[str, str]] = []
        self.depth = 0
        self.parts: list[str] = []
        self.state = ""

    def handle_starttag(self, tag, attrs):
        held = dict(attrs)
        if self.depth:
            self.depth += tag == "span"
        elif tag == "span" and (held.get("class") or "").split()[:1] == ["ref"] and "data-state" in held:
            self.depth, self.parts, self.state = 1, [], held["data-state"]

    def handle_endtag(self, tag):
        if self.depth and tag == "span":
            self.depth -= 1
            if not self.depth:
                self.found.append((self.state, " ".join(" ".join(self.parts).split())))

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def badges(page: str) -> list[tuple[str, str]]:
    """(state, text) of every reference badge on a page, in page order: `("missing",
    "⊗ Not found when cited · e999")`."""
    parser = _Badges()
    parser.feed(plain(page))
    return parser.found
