"""#184 (owner decision 2026-11-10): a chart card's input data starts folded, and the Overview shortens a
long record text.

- The card (the Overview's recent visualizations, the Visualizations tab and its list pages) says in one
  line how many inputs there are and how many of them changed, went missing or are unknown now, and
  lists them all, in the run's order, only when it is opened. The first CARD_WARNINGS changed or missing
  ones stay in sight under the line while it is folded. The run page's own input table is #172's: the
  first FOLD_SHOWN rows in sight, not folded from the start.
- The Overview's Current insights and Open questions show a long text's start (about three lines) and
  "Show more" in a native <details>, which opens without a script and holds the text whole; the kind,
  the state badges, the meta line and the relation link stay outside it. Every other list shows the text
  whole, as before.
"""
from __future__ import annotations

import html
import re

import pytest
from conftest import KIT
from legacy_records import LegacyProject, at
from workspace_words import plain

from aelix_analytics import core, i18n, workspace

HUMAN = core.Origin("human", "cli", "declared")
RUN, CALM, BARE = "20261001-090000-many", "20261001-091000-calm", "20261001-092000-bare"
# The inputs of RUN in its record's order, and what becomes of each after the run: two change, two go
# missing (one of them past the warnings shown), one has no recorded hash, the rest stay.
INPUTS = [f"data/raw/part-{i:02d}.parquet" for i in range(10)]
CHANGED, MISSING, UNKNOWN = {INPUTS[1], INPUTS[6]}, {INPUTS[3], INPUTS[8]}, {INPUTS[4]}
LONG_EN = ("Online revenue grew faster than store revenue in every region during 2025, but the gap is not uniform: "
           "the northern regions show the largest shift, while the southern regions moved by less than 5 points. "
           "Part of the difference comes from the store closures in March.")
LONG_KO = ("2025년 3월 이후 온라인 매출이 매장 매출을 넘어선 것이 실제 고객 선호의 변화인지, 아니면 같은 시기에 있었던 "
           "매장 임시 휴업과 판촉 일정 때문인지 확인해야 한다. 매장 일정 자료를 받으면 휴업 기간을 빼고 다시 비교한다.")
LINES = "Compare channels by month.\nReasons:\n- quarters hide the closures\n- months match the calendar\n- appendix"
FOLD = re.compile(r'<details class="inputs-fold"><summary><span class="inputs-line">(.*?)</span></summary>'
                  r'<ul class="inputs">(.*?)</ul></details>(<ul class="input-warnings">.*?</ul>)?</dd>', re.S)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    base = tmp_path_factory.mktemp("card-inputs")
    with pytest.MonkeyPatch.context() as env:
        env.setenv("AELIX_ANALYTICS_TRUST_FILE", str(base / "trust" / "analytics-projects.json"))
        q = LegacyProject(base / "p", migrated=True)
        recorded = [(path, None if path in UNKNOWN else q.file(path, path.encode())) for path in INPUTS]
        q.run(RUN, script="scripts/many.py", started_at=at(0), ts=at(1), inputs=recorded, charts=["c"])
        for path in CHANGED:
            q.file(path, b"changed")
        for path in MISSING:
            (q.root / path).unlink()
        calm = [(f"data/raw/calm-{i}.parquet", q.file(f"data/raw/calm-{i}.parquet", str(i).encode())) for i in range(3)]
        q.run(CALM, script="scripts/calm.py", started_at=at(10), ts=at(11), inputs=calm, charts=["c"])
        q.run(BARE, script="scripts/bare.py", started_at=at(20), ts=at(21), charts=["c"])
        p = core.Project(q.root)
        ids = {name: core.record(p, kind, text, origin=HUMAN).event["id"]
               for name, kind, text in (("en", "finding", LONG_EN), ("lines", "decision", LINES),
                                        ("short", "finding", "Revenue fell in Q3."), ("ko", "question", LONG_KO))}
        workspace.build(p)
    return p, ids


def read(p: core.Project, name: str) -> str:
    return plain((p.workspace_dir / name).read_text(encoding="utf-8"))


def card(page: str, run_id: str, prefix: str) -> str:
    """The card of `run_id`'s visualization whose id starts with `prefix` (recent-chart- or chart-)."""
    found = [part for part in page.split("<article ")[1:]
             if part.startswith('class="chart-card') and f'data-selection="{run_id}%2Fc"' in part.split(">", 1)[0]
             and f'id="{prefix}' in part.split(">", 1)[0]]
    assert len(found) == 1, (run_id, prefix, len(found))
    return found[0].split("</article>", 1)[0]


def text_of(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


# --- the card's input data ------------------------------------------------------------------------------

@pytest.mark.parametrize("page,prefix", [("index.html", "recent-chart-"), ("index.html", "chart-"),
                                         ("lists/charts-0.html", "chart-")])
def test_a_card_s_input_data_starts_folded_under_one_line_of_counts(built, page, prefix):
    p, _ = built
    found = FOLD.search(card(read(p, page), RUN, prefix))
    assert found, "the card's input data is not one folded <details> (no `open`)"
    line, listed, warnings = found.groups()
    # The line: how many, then each state but the unchanged one, a mark and words each.
    assert text_of(line) == "10 inputs · ⚠ 2 changed · ⚠ 2 missing · ? 1 unknown"
    assert line.count('aria-hidden="true"') == 3
    # Opened: every input, in the run record's order, each with its state now (nothing lost, nothing moved).
    items = re.findall(r'<li><span class="input">(.*?)</span></li>', listed, re.S)
    assert [re.search(r"data/raw/part-\d\d\.parquet", item).group(0) for item in items] == INPUTS
    states = [re.search(r'title="File now: (\w+)"', item).group(1) for item in items]
    assert states == ["Missing" if path in MISSING else "Changed" if path in CHANGED else
                      "Unknown" if path in UNKNOWN else "Same" for path in INPUTS]
    # Folded: the first three changed or missing inputs, in the list's order, in words; then how many more.
    # (A file that is a dataset is named by its dataset, its path in the link's title.)
    shown = [(state, re.search(r"data/raw/part-\d\d\.parquet", link).group(0)) for state, link in re.findall(
        r'<li><span class="badge tone-critical"><span aria-hidden="true">⚠</span> (\w+)</span> (<a [^>]*>[^<]*</a>)</li>', warnings)]
    assert shown == [("Changed", INPUTS[1]), ("Missing", INPUTS[3]), ("Changed", INPUTS[6])]
    assert warnings.endswith('<li class="muted">and 1 more</li></ul>')


def test_a_card_says_all_unchanged_or_that_no_input_was_detected(built):
    p, _ = built
    index = read(p, "index.html")
    found = FOLD.search(card(index, CALM, "chart-"))
    assert found and text_of(found.group(1)) == "3 inputs · ✓ all unchanged" and found.group(3) is None
    assert len(re.findall(r'<li><span class="input">', found.group(2))) == 3
    bare = card(index, BARE, "chart-")
    assert "inputs-fold" not in bare and '<span class="muted">No inputs detected</span>' in bare
    # One input: "unchanged", its count in the singular.
    assert i18n.message("card.inputs.count", n=1) == "1 input" and i18n.message("card.inputs.unchanged", n=1) == "unchanged"


def test_the_line_is_the_owner_s_example_in_korean():
    ko = [i18n.message("card.inputs.count", "ko", n=24), i18n.message("card.inputs.changed", "ko", n=2),
          i18n.message("card.inputs.missing", "ko", n=1)]
    assert " · ".join(ko) == "입력 24개 · 2개 바뀜 · 1개 없음"
    assert f'{i18n.message("card.inputs.count", "ko", n=24)} · {i18n.message("card.inputs.unchanged", "ko", n=24)}' \
        == "입력 24개 · 모두 그대로"


def test_the_run_page_s_input_table_does_not_start_folded(built):
    """The owner's correction of the same day: the run page's input table stays as #172 left it."""
    p, _ = built
    inputs = read(p, f"runs/{RUN}.html").split('<section id="inputs">', 1)[1].split("</section>", 1)[0]
    assert "<details" not in inputs and "inputs-fold" not in inputs
    assert inputs.count("<tr><td>") == len(INPUTS)


def test_the_folded_line_hides_its_warnings_once_open():
    css = (KIT / "aelix_analytics" / "assets" / "workspace.css").read_text(encoding="utf-8")
    assert "details.inputs-fold[open] + ul.input-warnings { display: none; }" in css


# --- the Overview's long texts --------------------------------------------------------------------------

def rows(p: core.Project) -> dict[str, str]:
    overview = read(p, "index.html").split('<section id="overview"', 1)[1].split("</section>", 1)[0]
    panels = overview.split('id="overview-key"', 1)[1]
    return {m.group(1): m.group(2) for m in re.finditer(r'<li class="ev rec" id="key-(e\d+)"(.*?)</li>(?=<li class="ev rec"|</ol>)',
                                                         panels, re.S)}


@pytest.mark.parametrize("name,text", [("en", LONG_EN), ("ko", LONG_KO), ("lines", LINES)],
                         ids=["en", "ko", "lines"])
def test_the_overview_shows_a_long_text_s_start_and_opens_it_whole_without_a_script(built, name, text):
    p, ids = built
    row = rows(p)[ids[name]]
    found = re.search(r'<div class="rec-line"><details class="rec-fold"><summary>'
                      r'<span class="rec-text rec-head" lang="">(.*?)</span><span class="rec-cut" aria-hidden="true">…</span> '
                      r'<span class="rec-toggle">Show more</span><span class="rec-toggle" hidden>Show less</span></summary>'
                      r'<div class="rec-text rec-full" lang="">(.*?)</div></details>(.*?)</div>', row, re.S)
    assert found, row
    head, whole, after = (html.unescape(g) for g in found.groups())
    # The whole text is in the fold, as written; the start is the text's own start, cut at a space or line end.
    assert whole == text and text.startswith(head) and len(head) < len(text) and text[len(head)] in " \n"
    assert head == workspace.text_head(text)
    # The kind, the state badge, the meta line and the relation link stay out of the fold.
    assert '<span class="rec-kind' in row.split("<details", 1)[0]
    assert "</details>" in row and '<div class="rec-meta">' in row.split("</details>", 1)[1]
    assert 'class="rec-action"' in row.split("</details>", 1)[1]
    if name == "ko":
        assert "Open" in after  # the question's state badge, beside the fold
    # The Insights tab (findings, decisions, assumptions) and the open items' list page show it whole, as
    # every list did.
    other = (read(p, "lists/open-0.html") if name == "ko" else
             read(p, "index.html").split('<section id="insights"', 1)[1].split("</section>", 1)[0])
    assert f'<span class="rec-text" lang="">{html.escape(text, quote=True)}</span>' in other and "rec-fold" not in other


def test_a_short_text_shows_whole_in_the_overview(built):
    p, ids = built
    assert '<span class="rec-text" lang="">Revenue fell in Q3.</span>' in rows(p)[ids["short"]]
    assert "rec-fold" not in rows(p)[ids["short"]]


def test_the_open_text_says_show_less_with_the_css_and_show_more_alone_without():
    css = (KIT / "aelix_analytics" / "assets" / "workspace.css").read_text(encoding="utf-8")
    assert "details.rec-fold[open] > summary > :is(.rec-head, .rec-cut, .rec-toggle:not([hidden])) { display: none; }" in css
    # `hidden` is display: none !important on every page: the words override it while the text is open.
    assert "[hidden] { display: none !important; }" in css
    assert "details.rec-fold[open] > summary > .rec-toggle[hidden] { display: inline !important; }" in css
    assert i18n.message("overview.text_more", "ko") == "더 보기" and i18n.message("overview.text_less", "ko") == "접기"


def test_how_much_of_a_text_the_overview_shows():
    budget = workspace.OVERVIEW_TEXT_LINES * workspace.OVERVIEW_LINE_COLUMNS
    head = workspace.text_head
    assert head("a" * budget) is None and head("a" * (budget + 1) + " b") == "a" * budget
    # A wide character takes two columns: half as many Korean letters fit.
    assert head("가" * (budget // 2)) is None and head("가" * (budget // 2 + 5)) == "가" * (budget // 2)
    # A cut goes back to a space in the last third, not before it.
    words = "word " * 60
    assert head(words) == words[:workspace.OVERVIEW_TEXT_LINES * workspace.OVERVIEW_LINE_COLUMNS].rsplit(" ", 1)[0].rstrip()
    assert head("x" * (budget - 10) + " " + "y" * 40) == "x" * (budget - 10)
    # A line break ends its line: three short lines show, a fourth is cut; blank lines at the end are no rest.
    assert head("1\n2\n3") is None and head("1\n2\n3\n4") == "1\n2\n3" and head("1\n2\n3\n\n\n") is None
    assert head("1\n\n\n4") == "1" and head("x" * 50 + "\n2\n3") == "x" * 50 + "\n2"  # a blank line, a long line: lines too
    # Never between a character and the marks drawn with it (a combining accent, a jamo vowel).
    accented = "é" * (budget + 10)
    assert len(head(accented)) % 2 == 0 and head(accented).endswith("́")
    jamo = "가" * (budget // 2 + 10)
    assert head(jamo).endswith("ᅡ")
    assert head("") is None and workspace.text_html(None, True) == '<span class="rec-text" lang=""></span>'
