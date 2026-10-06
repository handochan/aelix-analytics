"""The kit's own words are English; what people and scripts wrote stays as written (Issue #10,
TERMINOLOGY-AND-LANGUAGE.md 7).

Two guards: the kit's shipped files hold no Hangul but the headings and pin marks of earlier
CONTEXT.md templates, which the kit still reads (a static check); and every Hangul the status,
the context block and the Workspace show for a Korean project is part of what was written into
that project (a check of the output). Then the cases: a Korean project's text, column names,
dataset name and NFD paths come back byte for byte; a new project, its empty Workspace and the
demo are English; every pin mark pins.
"""

import html
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest
from conftest import KIT, PACK, kit_cli, kit_env, write_script

from aelix_analytics import _platform, core

TEMPLATE = KIT / "aelix_analytics" / "template"
# Hangul in any form: conjoining jamo (what NFD makes of a syllable), compatibility and halfwidth
# jamo, the extended jamo blocks and the precomposed syllables.
HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-퟿ﾠ-ￜ]+")


def nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def hangul_outside(text: str, written: str) -> list[str]:
    """The runs of Hangul in `text` that are not part of `written` (what people and scripts wrote):
    words of the kit's own."""
    return sorted({run for run in HANGUL.findall(text) if run not in written})


# --- the kit's shipped files ----------------------------------------------------------------------

# Hangul the kit's own files may hold: the heading and the pin mark of the CONTEXT.md template
# before Issue #10 (`## 목표`, `<!-- 항상 -->`), which projects keep and the kit still reads. Each
# with the English word on the same line — the alternation it is read with, or its comment's.
LEGACY_READERS = {("src/aelix_analytics/core.py", "목표"): "goal",
                  ("src/aelix_analytics/core.py", "항상"): "always"}


def shipped_files() -> list[Path]:
    """What the kit ships and shows from: the package (code, Workspace assets, templates, demo,
    manifest), the aelix extension and the checkout's launcher."""
    package = PACK / "src" / "aelix_analytics"
    files = [p for p in package.rglob("*") if p.is_file() and "__pycache__" not in p.parts
             and p.suffix not in (".pyc", ".png", ".parquet")]
    return sorted(files) + [PACK / "extension" / "aelix_analytics.py", PACK / "bin" / "analytics"]


def test_the_kit_s_own_files_hold_no_hangul_but_the_legacy_readers():
    found, used = [], set()
    for path in shipped_files():
        rel = path.relative_to(PACK).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            for run in HANGUL.findall(line):
                word = LEGACY_READERS.get((rel, run))
                if word is not None and word in line.lower():
                    used.add((rel, run))
                else:
                    found.append(f"{rel}:{number}: {run!r} in {line.strip()[:120]}")
    assert not found, "system text that is not English:\n" + "\n".join(found)
    assert used == set(LEGACY_READERS), f"allowed but no longer there: {set(LEGACY_READERS) - used}"


# --- a project written in Korean ------------------------------------------------------------------

TITLE = "지역 매출 분석 😀"
GOAL = "지역별 매출 차이를 설명한다"
QUESTION = "부산은 왜 낮은가?"
FINDING = "서울 매출이 가장 크다 — 근거는 첫 실행"
RUN_TITLE = "지역별 합계 — 첫 실행"
CHART_TITLE = "지역별 매출 (원) 😀"
CAPTION = "할인 뒤 금액의 합계. 결합 문자 é 포함"
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


def korean_project(tmp_path: Path):
    """A project whose every text is Korean, with NFD names: returns (root, run id, the source
    file, everything that was written into it)."""
    root = tmp_path / "proj"
    core.init_project(root, title=TITLE, goal=GOAL, template_dir=TEMPLATE, actor="test")
    (root / "CONTEXT.md").write_bytes(CONTEXT.encode("utf-8"))
    source = tmp_path.joinpath(*SOURCE_PARTS)
    source.parent.mkdir()
    csv = "지역,채널,매출\n" + "".join(f"{r},{c},{v}\n" for r, c, v in ROWS)
    source.write_bytes(csv.encode("utf-8"))
    code, imported = kit_cli(root, "import", source, "--name", "sales", "--description", DESCRIPTION)
    assert code == 0 and imported["ok"], imported
    code, question = kit_cli(root, "record", "question", QUESTION)
    assert code == 0, question
    code, ran = kit_cli(root, "run", write_script(root, "01_region.py", SCRIPT), "--title", RUN_TITLE)
    assert code == 0 and ran["run"]["status"] == "ok", ran
    run_id = ran["run"]["id"]
    code, found = kit_cli(root, "record", "finding", FINDING, "--ref", run_id)
    assert code == 0, found
    written = "\n".join([TITLE, GOAL, CONTEXT, QUESTION, FINDING, RUN_TITLE, CHART_TITLE, CAPTION,
                         DESCRIPTION, SCRIPT, str(source), csv, "지역 수 3"])
    return root, run_id, source, written


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
    code, rendered = kit_cli(root, "render")
    assert code == 0, rendered
    pages = {p.relative_to(root / "report").as_posix(): p.read_text(encoding="utf-8")
             for p in (root / "report").rglob("*.html")}
    view = core.view_names(core.list_datasets(project))
    derived_view = next(v for v, path in view.items() if path == DERIVED)
    assert set(pages) == {"index.html", "context.html", f"runs/{run_id}.html", "data/sales.html",
                          f"data/{derived_view}.html"}
    for name, page in pages.items():
        assert '<html lang="en" ' in page, name
        assert hangul_outside(page, written) == [], name
        # Nothing composed the decomposed names (the goal holds the composed words on purpose).
        composed = unicodedata.normalize("NFC", DERIVED_NAME)
        assert unicodedata.normalize("NFC", DERIVED) not in page and f">{composed}<" not in page, name
    index, run_page = pages["index.html"], pages[f"runs/{run_id}.html"]
    for value in (TITLE, GOAL, QUESTION, FINDING, RUN_TITLE, CHART_TITLE, CAPTION):
        assert html.escape(value) in index, value
    assert f'alt="{html.escape(CHART_TITLE)}"' in index
    assert f"<code>{DERIVED}</code>" in index and f">{DERIVED_NAME}</a>" in index
    assert ">Visualizations</h2>" in index and ">Key information</h2>" in index
    assert f"<h1>{RUN_TITLE}</h1>" in run_page and "<td>지역, 매출</td>" in run_page
    assert f'title="{DERIVED}">{DERIVED_NAME}</a>' in run_page
    assert "Attached data · 3 rows" in run_page and "지역 수 3" in run_page
    derived_page = pages[f"data/{derived_view}.html"]
    assert f"<h1>{DERIVED_NAME}</h1>" in derived_page and f"SQL view <code>{derived_view}</code>" in derived_page
    assert "<td>지역</td>" in derived_page and "<td>매출</td>" in derived_page
    raw_page = pages["data/sales.html"]
    assert html.escape(DESCRIPTION) in raw_page and "<td>서울</td>" in raw_page
    assert f"<code>{html.escape(raw['source'])}</code>" in raw_page
    assert "<h1>sales</h1>" in raw_page and "SQL view" not in raw_page
    assert html.escape(QUESTION) in pages["context.html"] and nfd("한글 자모") in pages["context.html"]


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
