"""The Workspace's own words by message key (TERMINOLOGY 7; design WORKSPACE-READABILITY 3).

assets/i18n/en.json holds every key in English and is the source of the kit's words; ko.json holds
the Korean of the keys it translates, and a key it lacks is shown in English. The generator writes
English and puts each word a page can switch in a <k-w> element (`t`), or marks a switchable
attribute with its key (`attr`); assets/i18n.js carries both catalogs, so a page switches language
in the browser with no server and no fetch.

<k-w> is an element HTML does not know: inline, with no style of its own (a span would take the
styles of `.crumbs span` or `.badge span`), read the same without a script. A word without values
whose English names one translation is written bare, `<k-w>Evidence</k-w>`, and the page finds its
key by its English; any other carries its key, and its values as JSON: `<k-w k="…" a='{"n":3}'>`.
A switchable attribute is marked the same way: ` title="…" data-i18n-title` bare, else with its key
(and `data-i18n-title-args`). Each word costs a page a few bytes, which matters on pages that repeat
a label per item.

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
from collections import defaultdict
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
WORD = "k-w"
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
    """The text of `key` in `language` (English where it has none), its {names} filled. A message
    shown in English is English throughout, its nested messages too (workspace-i18n.js does the
    same). A key no catalog has is a KeyError: the generator never shows a key."""
    entry = catalog(language).get(key)
    if entry is None:
        entry, language = catalog(DEFAULT)[key], DEFAULT
    if isinstance(entry, dict):
        entry = entry["one"] if args.get("n") == 1 and "one" in entry else entry["other"]
    return TOKEN.sub(lambda m: message(m.group(1), language, **args) if m.group(1) else _arg(args[m.group(2)]),
                     entry)


def _json(args: dict) -> str:
    """Values as a single-quoted attribute: JSON's double quotes stay as they are."""
    data = json.dumps(args, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return "'" + html.escape(data, quote=False).replace("'", "&#x27;") + "'"


@functools.cache
def _keys_saying() -> dict[str, list[str]]:
    """English text -> the keys of the messages without values that say it."""
    said = defaultdict(list)
    for key, text in catalog(DEFAULT).items():
        if isinstance(text, str) and "{" not in text:
            said[text].append(key)
    return said


@functools.cache
def by_english(key: str) -> bool:
    """Whether a page may name `key` by its English alone: a message without values, and every
    other key that says the same English says the same in each translation (or none of them does).
    workspace-i18n.js finds such a key by the text the page was written in."""
    text = catalog(DEFAULT)[key]
    if not isinstance(text, str) or "{" in text:
        return False
    keys = _keys_saying()[text]
    return all(len({catalog(language).get(k) for k in keys}) == 1
               for language in LANGUAGES if language != DEFAULT)


@functools.cache
def _word(key: str) -> str:
    named = "" if by_english(key) else f' k="{key}"'
    return f"<{WORD}{named}>{html.escape(message(key), quote=False)}</{WORD}>"


def t(key: str, **args) -> str:
    """The kit's words for `key`, in an element a page switches (it holds no other element)."""
    if not args:
        return _word(key)
    return _valued(key, tuple(sorted(args.items())))


@functools.lru_cache(maxsize=65536)
def _valued(key: str, items: tuple) -> str:
    # The same words with the same values recur on many pages (counts, version numbers): made once.
    args = dict(items)
    return f'<{WORD} k="{key}" a={_json(args)}>{html.escape(message(key, **args), quote=False)}</{WORD}>'


@functools.cache
def _attr(name: str, key: str) -> str:
    marked = f"data-i18n-{name}" if by_english(key) else f'data-i18n-{name}="{key}"'
    return f' {name}="{html.escape(message(key), quote=True)}" {marked}'


def attr(name: str, key: str, **args) -> str:
    """A switchable attribute of the kit's words: ` name="…" data-i18n-name` (bare as t's words are),
    or ` name="…" data-i18n-name="key"` with its values."""
    if name not in ATTRIBUTES:
        raise ValueError(name)
    if not args:
        return _attr(name, key)
    return (f' {name}="{html.escape(message(key, **args), quote=True)}" data-i18n-{name}="{key}"'
            f" data-i18n-{name}-args={_json(args)}")


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
