"""The Workspace's own words by message key (TERMINOLOGY 7; design WORKSPACE-READABILITY 3).

assets/i18n/en.json holds every key in English and is the source of the kit's words; ko.json holds
the Korean of the keys it translates, and a key it lacks is shown in English. The generator writes
English and marks each word a page can switch with its key (`t`, `attr`); assets/i18n.js carries
both catalogs, so a page switches language in the browser with no server and no fetch.

A message never takes what people or scripts wrote as an argument: that text stays its own
lang="" element beside the kit's words, so it keeps its unknown language in every UI language.
Arguments are numbers and the kit's own short values.
"""

from __future__ import annotations

import functools
import hashlib
import html
import json
import re
from importlib import resources

LANGUAGES = ("en", "ko")
DEFAULT = "en"
# The attributes whose words a page can switch (data-i18n-<name>, data-i18n-<name>-args).
ATTRIBUTES = ("title", "aria-label", "placeholder")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
# {@key}: another message of the same language and arguments, so a long explanation is written once.
NESTED = re.compile(r"\{@([a-z][a-z0-9_.]*)\}")
# Both, read in one pass: a value put in is never read again as a placeholder or a nested message.
TOKEN = re.compile(NESTED.pattern + "|" + PLACEHOLDER.pattern)
_ASSETS = resources.files(__package__) / "assets"


@functools.cache
def catalog(language: str) -> dict:
    """One language's messages: key -> text, or {"one", "other"} for a count `n`."""
    return json.loads((_ASSETS / "i18n" / f"{language}.json").read_text(encoding="utf-8"))


def has(key: str) -> bool:
    return key in catalog(DEFAULT)


def _arg(value) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def message(key: str, language: str = DEFAULT, **args) -> str:
    """The text of `key` in `language` (English where it has none), its {names} filled. A key no
    catalog has is a KeyError: the generator never shows a key."""
    entry = catalog(language).get(key)
    if entry is None:
        entry = catalog(DEFAULT)[key]
    if isinstance(entry, dict):
        entry = entry["one"] if args.get("n") == 1 and "one" in entry else entry["other"]
    return TOKEN.sub(lambda m: message(m.group(1), language, **args) if m.group(1) else _arg(args[m.group(2)]),
                     entry)


def _args(args: dict, attribute: str | None = None) -> str:
    if not args:
        return ""
    name = f"data-i18n-{attribute}-args" if attribute else "data-i18n-args"
    data = json.dumps(args, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f' {name}="{html.escape(data, quote=True)}"'


def t(key: str, **args) -> str:
    """The kit's words for `key`, in an element a page switches (it holds no other element)."""
    return f'<span data-i18n="{key}"{_args(args)}>{html.escape(message(key, **args), quote=False)}</span>'


def attr(name: str, key: str, **args) -> str:
    """A switchable attribute of the kit's words: ` name="…" data-i18n-name="key"`."""
    if name not in ATTRIBUTES:
        raise ValueError(name)
    return (f' {name}="{html.escape(message(key, **args), quote=True)}" data-i18n-{name}="{key}"'
            + _args(args, name))


def digest() -> str:
    """The catalogs' digest: a Workspace is rebuilt when only the words change."""
    h = hashlib.sha256()
    for language in LANGUAGES:
        h.update((_ASSETS / "i18n" / f"{language}.json").read_bytes())
    h.update((_ASSETS / "workspace-i18n.js").read_bytes())
    return h.hexdigest()


def script() -> str:
    """assets/i18n.js of a Workspace: the runtime, then both catalogs registered."""
    runtime = (_ASSETS / "workspace-i18n.js").read_text(encoding="utf-8")
    data = json.dumps({language: catalog(language) for language in LANGUAGES},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (runtime + "\nAnalyticsI18n.register(" + data.replace("</", "<\\/") + ");\n"
            # In the head: the page's language is set before its body is read.
            + "AnalyticsI18n.start(typeof window === \"undefined\" ? null : window);\n")
