"""The kit's own words are English; what people and scripts wrote stays as written (Issue #10,
TERMINOLOGY-AND-LANGUAGE.md 7).

Two guards. A static one: the only Hangul in the files the kit ships are the exact lines that read
the goal heading and the pin mark of earlier CONTEXT.md templates. And checks of the output for a
Korean project: the status and the context block hold no Hangul once each thing written into the
project is taken out whole, and every Hangul the Workspace shows sits in an element marked
lang="" (a language the records do not name) while the kit's labels stay in the page's English —
so a Korean label of the kit's fails even where a user wrote the same word. Then the cases: a
Korean project's text, column names, dataset name and NFD paths come back as written; every
dataset is shown by its name as written, with its SQL view beside it where they differ; a new
project, its empty Workspace and the demo are English; every pin mark pins.
"""

import html
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest
from conftest import KIT, PACK, git_checkout, kit_cli, kit_env, write_script

from aelix_analytics import _platform, core

TEMPLATE = KIT / "aelix_analytics" / "template"
# Hangul in any form: conjoining jamo (what NFD makes of a syllable), compatibility and halfwidth
# jamo, the extended jamo blocks and the precomposed syllables.
HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-퟿ﾠ-ￜ]+")


def nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def hangul_outside(text: str, written: list[str]) -> list[str]:
    """The Hangul left in `text` once each thing people and scripts wrote (a title, a line of
    CONTEXT.md, a cell) is taken out whole: words of the kit's own. Only whole pieces go, so a
    Korean label of the kit's is found even when a user wrote the same word inside a longer text."""
    for piece in sorted({piece for piece in written if piece}, key=len, reverse=True):
        text = text.replace(piece, "")
    return sorted(set(HANGUL.findall(text)))


class LangScopes(HTMLParser):
    """A page's text nodes and attribute values, each with the language the page gives it: the
    `lang` of the nearest element that has one (`""`: what people and scripts wrote, in a language
    the records do not name; `"en"`: the kit's). Script and style content is not text."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
            "track", "wbr"}

    def __init__(self, page: str):
        super().__init__(convert_charrefs=True)
        self.open: list[tuple[str, str | None]] = []
        self.texts: list[tuple[str | None, str, str]] = []
        self.attrs: list[tuple[str | None, str, str, str]] = []
        self.feed(page)
        self.close()

    def lang(self) -> str | None:
        return next((lang for _, lang in reversed(self.open) if lang is not None), None)

    def handle_starttag(self, tag, attrs):
        own = dict(attrs).get("lang")
        lang = own if own is not None else self.lang()
        self.attrs += [(lang, tag, name, value) for name, value in attrs if value and name != "lang"]
        if tag not in self.VOID:
            self.open.append((tag, own))

    def handle_endtag(self, tag):
        for i in range(len(self.open) - 1, -1, -1):
            if self.open[i][0] == tag:
                del self.open[i:]
                return

    def handle_data(self, data):
        tags = [tag for tag, _ in self.open]
        if data.strip() and "script" not in tags and "style" not in tags:
            self.texts.append((self.lang(), tags[-1] if tags else "", data))


# --- the kit's shipped files ----------------------------------------------------------------------

# The only Hangul the kit's own files may hold, each a whole line (stripped) that appears once: the
# readers of the goal heading and the pin mark of the CONTEXT.md template before Issue #10
# (`## 목표`, `<!-- 항상 -->`), which projects keep, and the comments that name them.
LEGACY_READER_LINES = {
    "src/aelix_analytics/core.py": (
        '"""First content line under a Goal heading (or 목표, the heading of earlier templates, which',
        r'if re.match(r"^#{1,3}\s*(목표|goal|objective)", line.strip(), re.IGNORECASE):',
        "# template writes; `<!-- always -->` and `<!-- 항상 -->` are the marks of earlier templates, which",
        r'PIN_RE = re.compile(r"<!--\s*(pin|always|항상)\s*-->", re.IGNORECASE)',
    ),
}


def shipped_files() -> list[str]:
    """What the kit ships and shows from, as git tracks it (as conftest's tracked_kit_sha lists the
    package): the package (code, Workspace assets, templates, demo, manifest), the aelix extension
    and the checkout's launcher. A file git does not track (a .DS_Store) is not the kit's."""
    listed = git_checkout("ls-files", "-z", "--", "src/aelix_analytics", "extension", "bin")
    return sorted(name for name in listed.decode("utf-8").split("\0") if name)


def test_the_kit_s_own_files_hold_no_hangul_but_the_legacy_readers():
    found, used = [], Counter()
    for rel in shipped_files():
        allowed = LEGACY_READER_LINES.get(rel, ())
        for number, line in enumerate((PACK / rel).read_text(encoding="utf-8").split("\n"), 1):
            if not HANGUL.search(line):
                continue
            if line.strip() in allowed:
                used[(rel, line.strip())] += 1
            else:
                found.append(f"{rel}:{number}: {line.strip()[:120]}")
    assert not found, "system text that is not English:\n" + "\n".join(found)
    expected = {(rel, line) for rel, lines in LEGACY_READER_LINES.items() for line in lines}
    assert set(used) == expected, f"allowed but no longer there: {expected - set(used)}"
    assert all(n == 1 for n in used.values()), f"a reader line copied: {used}"


# --- a project written in Korean ------------------------------------------------------------------

TITLE = "지역 매출 분석 😀"
GOAL = "지역별 매출 차이를 설명한다"
QUESTION = "부산은 왜 낮은가?"
FINDING = "서울 매출이 가장 크다 — 근거는 첫 실행"
RUN_TITLE = "지역별 합계 — 첫 실행"
CHART_TITLE = "지역별 매출 (원) 😀"
CAPTION = "할인 뒤 금액의 합계. 결합 문자 é 포함"
DESCRIPTION = "합성 매출 — 한국어 설명"
# Names macOS and some tools hand over decomposed (NFD): they are kept as written, never composed.
SOURCE_PARTS = (nfd("원본 자료"), nfd("매출 자료.csv"))
DERIVED_NAME = nfd("지역별 매출")
DERIVED = f"data/derived/{DERIVED_NAME}.parquet"
CONTEXT = f"""# {TITLE}

> 옛 템플릿처럼 한국어로 쓴 안내 문구.

## 목표 <!-- 항상 -->
{GOAL}

## 핵심 질문
- {QUESTION}

## 메모 🧪
- NFD로 쓴 글: {nfd("한글 자모")}
"""
ROWS = [("서울", "온라인", 300), ("서울", "매장", 280), ("부산", "온라인", 90), ("부산", "매장", 120),
        ("대구", "온라인", 60), ("대구", "매장", 75)]
CSV = "지역,채널,매출\n" + "".join(f"{r},{c},{v}\n" for r, c, v in ROWS)
SCRIPT = f'''import matplotlib.pyplot as plt
import polars as pl

import aelix_analytics as analytics

sales = analytics.read("sales")
by_region = sales.group_by("지역").agg(pl.col("매출").sum()).sort("지역")
by_region.write_parquet({DERIVED!r})
fig, ax = plt.subplots()
ax.bar(by_region["지역"], by_region["매출"])
analytics.visualize(fig, "region-sales", title={CHART_TITLE!r}, caption={CAPTION!r}, data=by_region)
print("지역 수", by_region.height)
'''
# Everything written into the project, piece by piece as the outputs show it whole.
WRITTEN = [TITLE, GOAL, QUESTION, FINDING, RUN_TITLE, CHART_TITLE, CAPTION, DESCRIPTION, DERIVED,
           DERIVED_NAME, *SOURCE_PARTS, *CONTEXT.split("\n"), *SCRIPT.split("\n"),
           *CSV.replace(",", "\n").split("\n"), "지역 수 3"]


def korean_project(tmp_path: Path):
    """A project whose every text is Korean, with NFD names: returns (root, run id, the source
    file, the pieces of everything that was written into it)."""
    root = tmp_path / "proj"
    core.init_project(root, title=TITLE, goal=GOAL, template_dir=TEMPLATE, actor="test")
    (root / "CONTEXT.md").write_bytes(CONTEXT.encode("utf-8"))
    source = tmp_path.joinpath(*SOURCE_PARTS)
    source.parent.mkdir()
    source.write_bytes(CSV.encode("utf-8"))
    code, imported = kit_cli(root, "import", source, "--name", "sales", "--description", DESCRIPTION)
    assert code == 0 and imported["ok"], imported
    code, question = kit_cli(root, "record", "question", QUESTION)
    assert code == 0, question
    code, ran = kit_cli(root, "run", write_script(root, "01_region.py", SCRIPT), "--title", RUN_TITLE)
    assert code == 0 and ran["run"]["status"] == "ok", ran
    run_id = ran["run"]["id"]
    code, found = kit_cli(root, "record", "finding", FINDING, "--ref", run_id)
    assert code == 0, found
    return root, run_id, source, [*WRITTEN, str(source)]


def rendered_pages(root: Path) -> dict[str, str]:
    code, rendered = kit_cli(root, "render")
    assert code == 0, rendered
    return {p.relative_to(root / "report").as_posix(): p.read_text(encoding="utf-8")
            for p in (root / "report").rglob("*.html")}


def test_a_korean_project_keeps_its_text_and_gets_english_labels(tmp_path):
    root, run_id, source, written = korean_project(tmp_path)
    project = core.Project(root)

    # The run recorded the decomposed names as they are on disk.
    run = json.loads((root / "runs" / run_id / "run.json").read_text(encoding="utf-8"))
    assert DERIVED in [o["path"] for o in run["outputs"]], run["outputs"]
    assert run["charts"][0]["title"] == CHART_TITLE and run["charts"][0]["caption"] == CAPTION
    assert run["charts"][0]["data_columns"] == ["지역", "매출"]

    # status: the JSON the CLI gives (and analytics_status reads), then its text.
    code, out = kit_cli(root, "status")
    assert code == 0, out
    st = out["status"]
    assert st["project"]["title"] == TITLE and st["project"]["goal"] == GOAL
    datasets = {d["path"]: d for d in st["datasets"]}
    assert datasets[DERIVED]["name"] == DERIVED_NAME
    assert unicodedata.normalize("NFC", DERIVED) not in datasets
    raw = datasets["data/raw/sales.parquet"]
    assert raw["description"] == DESCRIPTION and Path(raw["source"]).parts[-2:] == SOURCE_PARTS
    assert [r["title"] for r in st["runs"]] == [RUN_TITLE]
    assert [(c["key"], c["title"]) for c in st["charts"]] == [("scripts/01_region.py::region-sales",
                                                               CHART_TITLE)]
    assert [e["text"] for e in st["open"]] == [QUESTION]
    assert [e["text"] for e in st["key"]] == [FINDING]
    assert st["project"]["context_omitted"] == []
    text = out["text"]
    assert text == core.status_text(core.status(project))
    for value in (TITLE, GOAL, QUESTION, FINDING, RUN_TITLE, CHART_TITLE, DERIVED, DERIVED_NAME):
        assert value in text, value
    done = subprocess.run([sys.executable, "-m", "aelix_analytics", "--project", str(root), "--text",
                           "status"], capture_output=True, env=kit_env(), timeout=300,
                          **_platform.text_kwargs())
    assert done.returncode == 0 and done.stdout.rstrip("\n") == text, done.stderr
    assert hangul_outside(text, written) == []

    # The context block the agent gets: the file's text as written, and the journal's.
    code, block = kit_cli(root, "context")
    assert code == 0, block
    block = block["context"]
    for line in CONTEXT.strip().split("\n")[2:]:
        assert line in block, line
    assert FINDING in block and QUESTION in block
    assert hangul_outside(block, written) == []

    # The Workspace: English labels around the text as written.
    pages = rendered_pages(root)
    view = core.view_names(core.list_datasets(project))
    derived_view = next(v for v, path in view.items() if path == DERIVED)
    assert set(pages) == {"index.html", "context.html", f"runs/{run_id}.html", "data/sales.html",
                          f"data/{derived_view}.html"}
    for name, page in pages.items():
        assert '<html lang="en" ' in page, name
        # Nothing composed the decomposed names (the goal holds the composed words on purpose).
        composed = unicodedata.normalize("NFC", DERIVED_NAME)
        assert unicodedata.normalize("NFC", DERIVED) not in page and f">{composed}<" not in page, name
    index, run_page = pages["index.html"], pages[f"runs/{run_id}.html"]
    for value in (TITLE, GOAL, QUESTION, FINDING, RUN_TITLE, CHART_TITLE, CAPTION):
        assert html.escape(value) in index, value
    assert f'alt="{html.escape(CHART_TITLE)}" lang=""' in index
    assert f'<code lang="">{DERIVED}</code>' in index
    assert (f'<a href="data/{derived_view}.html" lang="">{DERIVED_NAME}</a> '
            f'<small>view <code>{derived_view}</code></small>') in index
    assert ">Visualizations</h2>" in index and ">Key information</h2>" in index
    assert f'<h1 lang="">{RUN_TITLE}</h1>' in run_page and '<td lang="">지역, 매출</td>' in run_page
    assert f'title="{DERIVED}" lang="">{DERIVED_NAME}</a>' in run_page
    assert "Attached data · 3 rows" in run_page and '<pre lang="">지역 수 3' in run_page
    derived_page = pages[f"data/{derived_view}.html"]
    assert f'<h1 lang="">{DERIVED_NAME}</h1>' in derived_page
    assert f"SQL view <code>{derived_view}</code>" in derived_page
    assert '<td lang="">지역</td>' in derived_page and '<th lang="">매출' in derived_page
    raw_page = pages["data/sales.html"]
    assert f'<p class="lead" lang="">{html.escape(DESCRIPTION)}</p>' in raw_page
    assert '<td lang="">서울</td>' in raw_page
    assert f'<code lang="">{html.escape(raw["source"])}</code>' in raw_page
    assert '<h1 lang="">sales</h1>' in raw_page and "SQL view" not in raw_page
    assert html.escape(QUESTION) in pages["context.html"] and nfd("한글 자모") in pages["context.html"]


# The kit's labels the Korean project's pages show on every platform: each stays in the page's
# English. (`(empty)`, what an empty log says, depends on the platform: see the test.)
SYSTEM_LABELS = ("Key information", "Recent journal entries", "Visualizations", "Timeline", "Data",
                 "Runs", "Evaluation", "Record coverage", "Tool usage", "Attached data",
                 "Input data", "Code", "Question", "Finding", "Logs", "Environment",
                 "Journal entries for this run", "Snapshot file", "Same as the current file",
                 "Schema and statistics", "Preview", "Relations", "Runs that read this data",
                 "Source file", "Context history", "Changes", "Auto-reload", "Column", "Type")


def test_the_workspace_gives_what_people_wrote_no_language_and_its_own_words_english(tmp_path):
    """TERMINOLOGY 7: the pages are lang="en"; the elements that hold what people and scripts
    wrote say lang="" (a language the records do not name), so a screen reader reads them in the
    reader's own voice. Every Hangul a page shows — text or attribute — is in such an element, the
    page title (one string with the kit's words) aside, and the kit's labels are not."""
    root, run_id, _, _ = korean_project(tmp_path)
    pages = rendered_pages(root)
    langs: dict[str, set] = {}
    for name, page in pages.items():
        scopes = LangScopes(page)
        misplaced = [(lang, tag, text.strip()[:80]) for lang, tag, text in scopes.texts
                     if tag != "title" and HANGUL.search(text) and lang != ""]
        misplaced += [(lang, f"{tag}@{attr}", value[:80]) for lang, tag, attr, value in scopes.attrs
                      if HANGUL.search(value) and lang != ""]
        assert misplaced == [], name
        titles = [text for _, tag, text in scopes.texts if tag == "title"]
        assert len(titles) == 1 and titles[0].endswith(f" · {TITLE}"), titles
        assert titles[0].removesuffix(f" · {TITLE}") in ("Workspace", "Context history", RUN_TITLE,
                                                         DERIVED_NAME, "sales"), titles
        for lang, _, text in scopes.texts:
            langs.setdefault(text.strip(), set()).add(lang)
    for label in SYSTEM_LABELS:
        assert langs.get(label) == {"en"}, (label, langs.get(label))
    # The run's stderr is empty where matplotlib finds a Korean font (macOS, Windows) and holds its
    # warnings of the glyphs it lacks where none is installed (Linux CI). An empty log is said in
    # the kit's words, in English; a log is what the script printed, in lang="".
    run = json.loads((root / "runs" / run_id / "run.json").read_text(encoding="utf-8"))
    stderr = core.newlines_as_lf((root / run["stderr"]).read_bytes().decode("utf-8", "replace"))
    if stderr.strip():
        assert langs.get(stderr.strip()) == {""}, stderr[:200]
    else:
        assert langs.get("(empty)") == {"en"}, langs.get("(empty)")
    # What people and scripts wrote: the project's title and goal, CONTEXT.md, records, run and
    # Visualization titles and captions, a dataset's name, path and description, column names and
    # values, what a script printed.
    for value in (TITLE, GOAL, QUESTION, FINDING, RUN_TITLE, CHART_TITLE, CAPTION, DESCRIPTION,
                  DERIVED_NAME, DERIVED, "sales", "서울", "지역", "지역 수 3",
                  "옛 템플릿처럼 한국어로 쓴 안내 문구."):
        assert langs.get(value) == {""}, (value, langs.get(value))


# --- a dataset's name and its SQL view ------------------------------------------------------------

DATASET_NAMES = {"My Sales": "my_sales", "my-data": "my_data", "123sales": "t_123sales",
                 "sales": "sales"}


def test_a_dataset_is_shown_by_its_name_as_written_and_its_view_beside_it(tmp_path):
    """The view spells a name in [a-z0-9_]; the Workspace names the dataset as written wherever it
    shows it, with the view beside the name where the two differ (and not where they are equal)."""
    import polars as pl  # not in the aelix legs, which import this module for its Korean project

    root = tmp_path / "names"
    project = core.init_project(root, title="names", template_dir=TEMPLATE, actor="test")
    for name in DATASET_NAMES:
        pl.DataFrame({"a": [1, 2]}).write_parquet(root / "data" / "derived" / f"{name}.parquet")
    reads = "".join(f'pl.read_parquet("data/derived/{name}.parquet")\n' for name in DATASET_NAMES)
    code, ran = kit_cli(root, "run", write_script(root, "read.py", f"import polars as pl\n{reads}"),
                        "--title", "read")
    assert code == 0 and ran["run"]["status"] == "ok", ran
    run_id = ran["run"]["id"]
    views = core.view_names(core.list_datasets(project))
    assert {Path(path).stem: view for view, path in views.items()} == DATASET_NAMES
    pages = rendered_pages(root)
    index, run_page = pages["index.html"], pages[f"runs/{run_id}.html"]
    for name, view in DATASET_NAMES.items():
        page, path = pages[f"data/{view}.html"], f"data/derived/{name}.parquet"
        assert f"<title>{name} · names</title>" in page and f'<h1 lang="">{name}</h1>' in page
        assert f'<span>/</span> <span lang="">{name}</span></div>' in page
        assert f'title="{path}" lang="">{name}</a>' in run_page
        row = f'<a href="data/{view}.html" lang="">{name}</a>'
        if name == view:
            assert f"{row}</td>" in index and "SQL view" not in page
        else:
            assert f"{row} <small>view <code>{view}</code></small>" in index
            assert f"SQL view <code>{view}</code>" in page
        assert f"FROM {view} LIMIT 20" in page


# --- the defaults: a new project, its empty Workspace, the demo -----------------------------------

def test_a_new_project_and_its_empty_workspace_are_english(tmp_path):
    root = tmp_path / "q3-review"
    project = core.init_project(root, template_dir=TEMPLATE, actor="test")
    files = {name: (root / name).read_text(encoding="utf-8")
             for name in ("CONTEXT.md", "pyproject.toml", ".gitignore", ".gitattributes",
                          "journal.jsonl", "lab.json")}
    assert 'name = "analytics-q3-review"' in files["pyproject.toml"]
    _, included, omitted, _ = core.context_selection(core.context_file_text(project))
    assert [s["name"] for s in included if s["pinned"]] == ["Goal", "Analysis focus", "Next steps"]
    assert omitted == []
    # The placeholder of a project made without a goal is not taken for one.
    assert core.status(project)["project"]["goal"] == ""
    code, rendered = kit_cli(root, "render")
    assert code == 0, rendered
    pages = {p.name: p.read_text(encoding="utf-8") for p in (root / "report").rglob("*.html")}
    texts = {**files, "context block": core.context_block(project),
             "status": core.status_text(core.status(project)), **pages}
    assert {name: HANGUL.findall(text) for name, text in texts.items() if HANGUL.search(text)} == {}
    index = pages["index.html"]
    assert '<html lang="en" ' in index and "<title>Workspace · q3-review</title>" in index
    for empty in ("No visualizations yet.", "No datasets yet.", "No runs yet.",
                  "No workflow feedback yet.", "No tool calls recorded by the aelix extension.",
                  'aria-label="Filter by kind"'):
        assert empty in index, empty
    # A Korean title is the project's own text: the uv project name keeps its ASCII part only.
    titled = core.init_project(tmp_path / "titled", title="새 분석 Q3", template_dir=TEMPLATE,
                               actor="test")
    toml = (titled.root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "analytics-q3"' in toml and 'description = "새 분석 Q3"' in toml


def test_the_demo_is_english(tmp_path):
    root = tmp_path / "demo"
    core.init_project(root, title="Online shift demo", template_dir=TEMPLATE, actor="test")
    code, result = kit_cli(root, "demo", timeout=900)
    assert code == 0 and result["ok"], result
    seen = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if (not path.is_file() or rel.startswith((".venv/", "report/assets/plotly"))
                or path.suffix not in (".md", ".json", ".jsonl", ".py", ".html", ".csv", ".txt",
                                       ".toml", ".js", ".css")):
            continue
        if found := HANGUL.findall(path.read_text(encoding="utf-8", errors="replace")):
            seen[rel] = found[:5]
    assert seen == {}
    titles = {json.loads(p.read_text(encoding="utf-8"))["title"] for p in root.glob("runs/*/run.json")}
    assert titles == {"Monthly revenue trend", "Channel mix by region in 2025", "Growth by region",
                      "Rerun comparison of the monthly revenue"}
    assert core.status(core.Project(root))["project"]["goal"].startswith("Find out how fast")


# --- the pin marks and the goal heading -----------------------------------------------------------

@pytest.mark.parametrize("mark", ["<!-- pin -->", "<!--PIN-->", "<!-- always -->", "<!-- 항상 -->"])
def test_every_pin_mark_pins_and_leaves_the_section_name(mark):
    text = f"# t\n\n## Big\n{'x' * 5000}\n\n## Rules {mark}\n- keep\n"
    _, included, omitted, _ = core.context_selection(text)
    assert [s["name"] for s in included] == ["Rules"] and [s["name"] for s in omitted] == ["Big"]


def test_a_section_named_only_by_its_mark_gets_an_english_name():
    _, sections = core.context_sections("# t\n\n## <!-- pin -->\nkept\n")
    assert sections[0]["name"] == "Section 1" and sections[0]["pinned"]


@pytest.mark.parametrize("heading", ["## Goal <!-- pin -->", "## 목표 <!-- 항상 -->", "# Objective"])
def test_the_goal_is_read_under_every_template_s_heading(heading):
    assert core.context_goal(f"# t\n\n{heading}\n- (a placeholder)\n매출 증가 원인\n") == "매출 증가 원인"
