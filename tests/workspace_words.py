"""Workspace pages as most tests read them: the kit's words without their switching markup.

Issue #96 puts each of the kit's words in a `<k-w>` element (bare, or with its key `k` and values
`a`) and adds `data-i18n-<attribute>` beside a switchable attribute, so a page can change language
in the browser. `plain` takes that markup away and gives the HTML a page was before, where a test
checks words and links rather than the switching itself (tests/test_workspace_i18n.py checks that).
"""

from __future__ import annotations

import re

WORD = re.compile(r'<k-w(?: k="[^"]*")?(?: a="[^"]*")?>([^<]*)</k-w>')
ATTRIBUTE = re.compile(r' data-i18n-[a-z-]+="[^"]*"')


def plain(page: str) -> str:
    return ATTRIBUTE.sub("", WORD.sub(r"\1", page))
