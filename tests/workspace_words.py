"""Workspace pages as most tests read them: the kit's words without their message-key markup.

Issue #96 wraps each of the kit's words in `<span data-i18n="key">English</span>` and adds
`data-i18n-<attribute>` beside a switchable attribute, so a page can change language in the browser.
`plain` takes that markup away and gives the HTML a page was before, where a test checks words and
links rather than the switching itself (tests/test_workspace_i18n.py checks that)."""

from __future__ import annotations

import re

SPAN = re.compile(r'<span data-i18n="[^"]*"(?: data-i18n-args="[^"]*")?>([^<]*)</span>')
ATTRIBUTE = re.compile(r' data-i18n-[a-z-]+="[^"]*"')


def plain(page: str) -> str:
    return ATTRIBUTE.sub("", SPAN.sub(r"\1", page))
