"""Issue #96: the Workspace's own words by message key, in English and Korean (design
WORKSPACE-READABILITY 3).

The catalogs: ko.json has only keys en.json has, each with the English message's placeholders,
nested messages and plural forms; every key the code names is in en.json, and every key en.json
has is one the code names. The pages: every switchable word names a known key and shows that key's
English, holds no element (switching replaces its text), and takes as arguments only numbers and
the kit's own values — never what people or scripts wrote, which stays in its own lang="" element.
The runtime in the browser is checked in Node: tests/workspace_i18n.cjs here, the controller's use
of it (URL state, return links, notices) in workspace_navigation_repairs.cjs.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest
from conftest import KIT, PACK
from legacy_records import LegacyProject, at

from aelix_analytics import core, i18n, workspace

ASSETS = KIT / "aelix_analytics" / "assets"
CODE = [KIT / "aelix_analytics" / "workspace.py", KIT / "aelix_analytics" / "workspace_relations.py",
        ASSETS / "workspace.js", ASSETS / "workspace-i18n.js"]
SHAPE = re.compile(r"\{@?[a-z][a-z0-9_.]*\}")
KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_/]+)+$")
FILE = re.compile(r"\.(html|json|jsonl|js|css|md|py|parquet|csv|txt|toml)$")
# A key the code builds from a value rather than names whole, found by the start it gives the key:
# words("kind", value), t("run.col." + column), t(f"time.date.{month}").
FAMILY = re.compile(r"""words\(\s*["']([a-z][a-z0-9_.]*)["']|["']([a-z][a-z0-9_.]*\.)["']\s*\+|f["']([a-z][a-z0-9_.]*\.)\{""")
LITERAL = re.compile(r"""["']([a-z][a-z0-9_]*(?:\.[a-z0-9_/]+)+)["']""")


def catalog(language: str) -> dict:
    return json.loads((ASSETS / "i18n" / f"{language}.json").read_text(encoding="utf-8"))


def shape(entry) -> dict | list:
    if isinstance(entry, dict):
        return {form: sorted(SHAPE.findall(text)) for form, text in entry.items()}
    return sorted(SHAPE.findall(entry))


# --- the catalogs -------------------------------------------------------------------------------

def test_each_translation_has_the_english_message_s_shape():
    en, ko = catalog("en"), catalog("ko")
    assert all(KEY.match(key) for key in en), [key for key in en if not KEY.match(key)]
    assert set(ko) <= set(en), sorted(set(ko) - set(en))
    for key, text in ko.items():
        english = en[key]
        if isinstance(english, dict):
            # Korean has no singular: `other` alone, with the English `other`'s placeholders.
            assert isinstance(text, dict) and set(text) <= set(english) and "other" in text, key
            assert all(shape(text)[form] == shape(english)["other"] for form in text), key
        else:
            assert isinstance(text, str) and shape(text) == shape(english), key
    # The files are kept sorted and in one layout, so a diff shows only what changed.
    for language, held in (("en", en), ("ko", ko)):
        raw = (ASSETS / "i18n" / f"{language}.json").read_text(encoding="utf-8")
        assert raw == json.dumps(dict(sorted(held.items())), ensure_ascii=False, indent=1) + "\n", language


def walk(language: str, held: dict, english: dict, key: str, seen: list[str]) -> None:
    """Follow `key`'s nested messages in `language` (English where it has none) down to the end."""
    assert key not in seen, f"{language}: {' → '.join([*seen, key])}"
    entry = held.get(key, english[key])
    for text in entry.values() if isinstance(entry, dict) else [entry]:
        for nested in i18n.NESTED.findall(text):
            assert nested in english, f"{language}: {key} names {nested}"
            walk(language, held, english, nested, [*seen, key])


def test_every_nested_message_exists_and_none_comes_back_to_itself():
    english = catalog("en")
    for language in i18n.LANGUAGES:
        held = catalog(language)
        for key in held:
            walk(language, held, english, key, [])


def test_every_key_the_code_names_is_in_the_catalog_and_every_key_is_named():
    en = catalog("en")
    code = "\n".join(path.read_text(encoding="utf-8") for path in CODE)
    families = {first + "." if first else second or third for first, second, third in FAMILY.findall(code)}
    starts = {key.split(".", 1)[0] for key in en}
    named = {literal for literal in LITERAL.findall(code) if literal.split(".", 1)[0] in starts}
    # A file name (run.json) is not a key, and a family's own name (data.kind) is where keys start.
    unknown = sorted(key for key in named if key not in en and key + "." not in families
                     and not FILE.search(key) and not any(key.startswith(family) for family in families))
    assert unknown == [], unknown
    assert all(any(key.startswith(family) for key in en) for family in families), families
    unused = sorted(key for key in en if key not in named and not any(key.startswith(f) for f in families))
    assert unused == [], unused


def test_a_message_falls_back_to_english_and_fills_its_values_once(monkeypatch):
    held = {"en": {"x.count": "{n} rows of {name}", "x.nest": "{@x.count}!", "x.only": "English"},
            "ko": {"x.count": "{name}의 {n}행", "x.nest": "{@x.count}?"}}
    monkeypatch.setattr(i18n, "catalog", lambda language: held[language])
    assert i18n.message("x.count", n=1234, name="t") == "1,234 rows of t"
    assert i18n.message("x.count", "ko", n=2, name="t") == "t의 2행"
    assert i18n.message("x.nest", "ko", n=2, name="t") == "t의 2행?"
    assert i18n.message("x.only", "ko") == "English"
    # A value is put in as given: never read again as a placeholder or a nested message.
    assert i18n.message("x.count", n=1, name="{n} {@x.only}") == "1 rows of {n} {@x.only}"
    assert i18n.message("x.nest", n="{name}", name="t") == "{name} rows of t!"
    with pytest.raises(KeyError):
        i18n.message("x.missing")
    with pytest.raises(KeyError):
        i18n.message("x.count", n=1)  # the generator never shows an unfilled placeholder


def test_the_markup_escapes_the_words_and_their_values():
    # A word without values whose English names one translation is bare; the page finds its key.
    assert i18n.by_english("tab.insights") and i18n.t("tab.insights") == "<k-w>Insights</k-w>"
    assert i18n.t("context.versions_count", n=3) == \
        '<k-w k="context.versions_count" a="{&quot;n&quot;:3}">3 version(s)</k-w>'
    assert i18n.attr("title", "badge.input_changed_title", n=2) == (
        f' title="{html.escape(i18n.message("badge.input_changed_title", n=2))}"'
        ' data-i18n-title="badge.input_changed_title" data-i18n-title-args="{&quot;n&quot;:2}"')
    with pytest.raises(ValueError):
        i18n.attr("onclick", "tab.insights")
    script = i18n.script()
    assert "</" not in script.split("AnalyticsI18n.register(", 1)[1]
    assert script.rstrip().endswith('AnalyticsI18n.start(typeof window === "undefined" ? null : window);')


def test_a_word_whose_english_two_translations_share_carries_its_key(monkeypatch):
    held = {"en": {"a.one": "Same", "a.two": "Same", "a.three": "Other", "a.four": "{n} x"},
            "ko": {"a.one": "하나", "a.two": "둘", "a.three": "다른"}}
    caches = (i18n._keys_saying, i18n.by_english, i18n._word)
    for cached in caches:
        cached.cache_clear()
    monkeypatch.setattr(i18n, "catalog", lambda language: held[language])
    try:
        assert not i18n.by_english("a.one") and i18n.t("a.one") == '<k-w k="a.one">Same</k-w>'
        assert i18n.by_english("a.three") and i18n.t("a.three") == "<k-w>Other</k-w>"
        assert not i18n.by_english("a.four")  # a message with values always carries its key
    finally:
        for cached in caches:
            cached.cache_clear()


def test_the_workspace_is_rebuilt_when_only_the_words_change(tmp_path, monkeypatch):
    copy = tmp_path / "assets"
    shutil.copytree(ASSETS, copy)
    monkeypatch.setattr(i18n, "_ASSETS", copy)
    before = i18n.digest()
    ko = copy / "i18n" / "ko.json"
    ko.write_text(ko.read_text(encoding="utf-8").replace("인사이트", "통찰"), encoding="utf-8")
    assert i18n.digest() != before


# --- the pages ------------------------------------------------------------------------------------

MARK = "ZQX"  # in everything people and scripts wrote below; never in the kit's words


def marked_project(tmp_path) -> core.Project:
    """Runs, a dataset, a stale input, producers with the same bytes, a finding citing a run, an
    unbound chart key, an earlier event and a missing one: every name, path and text holds MARK."""
    p = LegacyProject(tmp_path / "p", title=f"{MARK} title", migrated=True)
    base = p.dataset(f"{MARK}base", imported_at=at(1))
    p.event("import", {"data": [base]}, ts=at(2), text=f"{MARK} imported")
    base_sha = core.sha256_file(p.root / base)
    table = (f"data/derived/{MARK}t.parquet", p.file(f"data/derived/{MARK}t.parquet", b"t1"))
    a = p.run("20261001-091000-a", script=f"scripts/{MARK}a.py", started_at=at(10), ts=at(11),
              inputs=[(base, base_sha)], outputs=[table], charts=[f"{MARK}trend"])
    p.run("20261001-092000-b", script=f"scripts/{MARK}b.py", started_at=at(20), ts=at(21),
          inputs=[(base, base_sha)], outputs=[table], charts=[f"{MARK}mix"])
    p.run("20261001-093000-c", script=f"scripts/{MARK}c.py", started_at=at(30), ts=at(31), inputs=[table])
    p.file(base, b"changed since")
    p.event("finding", {"runs": [a], "charts": [f"scripts/{MARK}a.py::{MARK}trend"],
                        "events": ["e1", "e999"], "data": [table[0]]}, ts=at(40), text=f"{MARK} grew")
    p.event("question", {}, ts=at(41), text=f"why {MARK}?")
    return core.Project(p.root)


class Words(HTMLParser):
    """The switchable words of a page: (key, args, text, bare) of each <k-w>, (attribute, key, args,
    value) of each data-i18n-<attribute>, (datetime, text) of each <time datetime>, and any element
    inside a <k-w>."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
            "track", "wbr"}

    def __init__(self, page: str):
        super().__init__(convert_charrefs=True)
        self.words, self.attributes, self.times, self.nested, self.open = [], [], [], [], []
        self.feed(page)
        self.close()

    def handle_starttag(self, tag, attrs):
        held = dict(attrs)
        if any(t == i18n.WORD for t, *_ in self.open):
            self.nested.append(tag)
        for name in i18n.ATTRIBUTES:
            if f"data-i18n-{name}" in held:
                self.attributes.append((name, held[f"data-i18n-{name}"],
                                        json.loads(held.get(f"data-i18n-{name}-args") or "{}"), held.get(name)))
        if tag not in self.VOID:
            self.open.append((tag, held, []))

    def handle_endtag(self, tag):
        for i in range(len(self.open) - 1, -1, -1):
            if self.open[i][0] == tag:
                _, held, text = self.open[i]
                if tag == i18n.WORD:
                    self.words.append((held.get("k"), json.loads(held.get("a") or "{}"), "".join(text), "k" not in held))
                elif tag == "time" and "datetime" in held:
                    self.times.append((held["datetime"], "".join(text)))
                del self.open[i:]
                return

    def handle_data(self, data):
        for _, _, text in self.open:
            text.append(data)


def english_date(stamp: str) -> str:
    """What a page shows for a <time datetime>: the day in English, then the time when it has one."""
    y, m, d, hh, mm = workspace.STAMP.match(stamp).groups()
    return i18n.message(f"time.date.{int(m)}", y=y, d=int(d)) + (f" {hh}:{mm}" if hh else "")


def test_every_switchable_word_is_a_known_key_its_english_and_no_one_s_text(tmp_path):
    project = marked_project(tmp_path)
    workspace.build(project)
    pages = sorted(project.workspace_dir.rglob("*.html"))
    assert len(pages) > 5
    english = {}
    for key, text in catalog("en").items():
        if i18n.by_english(key):
            english.setdefault(text, key)
    seen, dates = set(), 0
    for path in pages:
        page = path.read_text(encoding="utf-8")
        found = Words(page)
        assert found.nested == [], (path.name, found.nested[:3])
        for key, args, text, bare in found.words:
            if bare:
                # Found by its English in the browser: a message without values, one translation.
                assert text in english, (path.name, text)
                key = english[text]
            seen.add(key)
            assert text == i18n.message(key, **args), (path.name, key)
            assert MARK not in json.dumps(args) and MARK not in text, (path.name, key, args)
            i18n.message(key, "ko", **args)  # every translation fills from the same values
        for name, key, args, value in found.attributes:
            seen.add(key)
            assert value == i18n.message(key, **args), (path.name, name, key)
            assert MARK not in json.dumps(args) and MARK not in (value or ""), (path.name, name, key, args)
            i18n.message(key, "ko", **args)
        for stamp, text in found.times:
            assert text == english_date(stamp), (path.name, stamp, text)
            dates += 1
        # The page is English without a script, and loads the catalogs before its body.
        assert page.startswith('<!doctype html>\n<html lang="en" ')
        head = page.split("<body", 1)[0]
        assert re.search(r"<script src=\"[^\"]*assets/i18n\.js\?v=[^\"]+\"></script>", head)
    # The build exercised a good part of the catalog, and dates.
    assert len(seen) > 150 and dates > 5
    script = (project.workspace_dir / "assets" / "i18n.js").read_text(encoding="utf-8")
    assert script == i18n.script()


def test_the_runtime_switches_formats_and_names_each_language_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    result = subprocess.run([node, str(PACK / "tests" / "workspace_i18n.cjs"), str(ASSETS / "workspace-i18n.js")],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
