"""#172: one folding rule for every list of the Workspace that can grow (design WORKSPACE-READABILITY
8.4). The first FOLD_SHOWN items show; the next ones up to the list's cap fold into a native <details>
that says how many it holds and how many the list has, and opens without a script; past the cap the
page says how many more there are and where all of them are. Folding changes neither the order nor the
number of the items, and an address naming a folded item opens its folds (workspace.js)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from legacy_records import LegacyProject, at, sha
from workspace_words import plain

from aelix_analytics import core, i18n, workspace, workspace_relations, workspace_reports

HUMAN = core.Origin("human", "cli", "declared")
SHOWN, CAP, LIMIT = workspace.FOLD_SHOWN, workspace.FOLD_CAP, workspace.INDEX_LIMIT
BIG = "20261001-080000-big"
T = "data/derived/t.parquet"
FOLD = re.compile(r'<details class="more"><summary>(.*?)</summary>(.*?)</details>', re.S)
BEYOND = re.compile(r'<p class="more-beyond">(.*?)</p>', re.S)


@pytest.fixture(scope="module")
def long(tmp_path_factory):
    """A project whose lists are long: a run with CAP + 50 inputs (SHOWN + 5 changed since), CAP + 30
    outputs, SHOWN + 10 warnings, 30 packages and SHOWN + 5 records naming it; a data file and a
    Visualization with 30 versions; a dataset of SHOWN + 25 columns; a Report with SHOWN + 5 versions,
    the latest holding SHOWN + 10 files, SHOWN + 10 members and SHOWN + 5 evidence references; and
    INDEX_LIMIT + 10 tools in the tool log, each flagged."""
    base = tmp_path_factory.mktemp("folding")
    with pytest.MonkeyPatch.context() as env:
        env.setenv("AELIX_ANALYTICS_TRUST_FILE", str(base / "trust" / "analytics-projects.json"))
        q = LegacyProject(base / "p", migrated=True)
        inputs = [(f"data/raw/in-{i:03d}.parquet", q.file(f"data/raw/in-{i:03d}.parquet", f"in-{i}".encode()))
                  for i in range(CAP + 50)]
        outputs = [(f"data/derived/out-{i:03d}.parquet", q.file(f"data/derived/out-{i:03d}.parquet", f"o-{i}".encode()))
                   for i in range(CAP + 30)]
        q.run(BIG, script="scripts/big.py", started_at=at(0, hour=8), ts=at(1, hour=8), inputs=inputs, outputs=outputs)
        # The last inputs change after the run: their rows come first.
        for i in range(CAP + 45 - SHOWN, CAP + 50):
            q.file(f"data/raw/in-{i:03d}.parquet", b"changed")
        record = json.loads((q.root / "runs" / BIG / "run.json").read_text(encoding="utf-8"))
        record["warnings"] = [f"warning {i}" for i in range(SHOWN + 10)]
        record["env"] = {"python": "3.12", "kit_version": "0.32.1", "packages": {f"pkg-{i:02d}": "1.0" for i in range(30)}}
        q.write_json(f"runs/{BIG}/run.json", record)
        import polars as pl
        wide = q.root / "data" / "derived" / "wide.parquet"
        pl.DataFrame({f"column_{i:02d}": [i] for i in range(SHOWN + 25)}).write_parquet(wide)
        q.run("20261001-081000-wide", script="scripts/wide.py", started_at=at(10, hour=8), ts=at(11, hour=8),
              outputs=[("data/derived/wide.parquet", sha(wide.read_bytes()))])
        # 30 versions of a Visualization and of the data file it writes: each run reads other bytes.
        for n in range(30):
            q.run(f"20261001-10{n:02d}00-v", script="scripts/v.py", started_at=at(n, hour=10), ts=at(n, hour=10),
                  inputs=[("data/raw/in-000.parquet", sha(str(n).encode()))],
                  outputs=[(T, q.file(T, f"t-{n}".encode()))], charts=["c"])
        p = core.Project(q.root)
        records = [core.record(p, "note", f"note {i}", origin=HUMAN,
                               refs=[{"type": "run", "run_id": BIG, "relation": "cites"}]).event for i in range(SHOWN + 5)]
        folder = base / "report"
        folder.mkdir()
        previous = None
        for v in range(SHOWN + 5):
            (folder / "index.md").write_text(f"# v{v}\n", encoding="utf-8")
            options = {}
            if v == SHOWN + 4:
                for i in range(SHOWN + 9):
                    (folder / f"f-{i:02d}.csv").write_text(f"a\n{i}\n", encoding="utf-8")
                options = {"metadata": {"collection": {"contract": "report_collection/1", "members": [
                    {"member_key": f"m{i}", "status": "not_generated", "reason": "later"} for i in range(SHOWN + 10)]}},
                    "based_on": {"as_of_seq": core.read_model(p).as_of_seq, "context_version": None, "refs": [
                        {"type": "event", "event_id": e["id"], "relation": "cites", "basis": "declared",
                         "version_binding": "author"} for e in records]}}
            previous = core.save_report(p, "long", files=folder, entry="index.md", title="Long", previous_revision=previous,
                                        request_id=f"long-{v}", origin=HUMAN, **options)["report"]["revision_id"]
        # The tool log: INDEX_LIMIT + 10 tools, each called once and flagged.
        p.telemetry.parent.mkdir(parents=True, exist_ok=True)
        p.telemetry.write_text("".join(json.dumps({"ts": at(n % 60, hour=11), "tool": f"tool_{n:02d}", "ms": 1, "flags": ["python_via_bash"],
                                                   "summary": "python -c"}) + "\n" for n in range(LIMIT + 10)), encoding="utf-8")
        workspace.build(p)
    return p


def page(p: core.Project, name: str) -> str:
    return plain((p.workspace_dir / name).read_text(encoding="utf-8"))


def section(html: str, ident: str) -> str:
    return html.split(f'<section id="{ident}"', 1)[1].split("</section>", 1)[0]


def rows(html: str) -> int:
    """The items of a list or the body rows of a table in `html` (a head row is not one)."""
    return len(re.findall(r"<tr><td|<tr id=|<li class=\"ev rec\"|<li class=\"vh-row|<li lang=|<li><time", html))


def visible(html: str) -> str:
    """What a reader sees without opening a fold: the html with every fold taken out."""
    return FOLD.sub("", html)


# --- the rule -------------------------------------------------------------------------------------------

def wrap(part) -> str:
    return "<ol>" + "".join(f"<li>{x}</li>" for x in part) + "</ol>"


def test_the_rule_shows_the_first_items_folds_the_next_and_names_where_all_are():
    assert workspace.fold(range(SHOWN), wrap, cap=CAP) == wrap(range(SHOWN))  # nothing to fold
    html = workspace.fold(range(CAP + 7), wrap, cap=CAP, beyond='<a href="r.json">r.json</a>')
    (summary, folded), = FOLD.findall(html)
    assert html.startswith(wrap(range(SHOWN)))
    assert folded == wrap(range(SHOWN, CAP))
    assert plain(summary) == i18n.message("fold.more", n=CAP - SHOWN, total=CAP + 7) == f"Show {CAP - SHOWN} more ({CAP + 7} in all)"
    assert 'k="fold.more"' in summary
    beyond, = BEYOND.findall(html)
    assert "7 more are not on this page. All of them:" in beyond and beyond.endswith('<a href="r.json">r.json</a>')
    # No cap: all of them on the page. A row kept in sight splits the fold in two, in order.
    html = workspace.fold(range(SHOWN + 10), wrap, cap=None, keep=[SHOWN + 4])
    assert [plain(s) for s, _ in FOLD.findall(html)] == [f"Show 4 more ({SHOWN + 10} in all)", f"Show 5 more ({SHOWN + 10} in all)"]
    assert re.findall(r"<li>(\d+)</li>", html) == [str(i) for i in range(SHOWN + 10)]
    assert not BEYOND.search(html)
    assert workspace.fold(range(SHOWN + 1), wrap, cap=None, keep=[SHOWN]) == wrap(range(SHOWN + 1))
    # Only the items the page holds are made.
    made = []
    workspace.fold(range(10_000), lambda part: made.extend(part) or "", cap=CAP)
    assert made == list(range(CAP))


def test_the_words_of_a_fold_switch_language():
    assert i18n.message("fold.more", "ko", n=3, total=23) == "3개 더 보기 (모두 23개)"
    assert i18n.message("fold.beyond", "ko", n=3) == "3개는 이 페이지에 없습니다. 모두 보기:"
    assert i18n.message("fold.beyond", n=1) == "1 more is not on this page. All of them:"


# --- the lists -------------------------------------------------------------------------------------------

def test_a_run_s_lists_fold_after_the_first_rows_and_its_record_holds_every_one(long):
    html = page(long, f"runs/{BIG}.html")
    record = f'<a href="../../runs/{BIG}/run.json">run.json</a>'
    inputs = section(html, "inputs")
    assert f"Input data ({CAP + 50})" in inputs
    # The changed inputs come first; CAP rows on the page, the rest named by the record.
    shown = visible(inputs)
    assert rows(shown) == SHOWN and shown.count(" Changed</span>") == SHOWN
    (summary, folded), = FOLD.findall(inputs)
    assert rows(folded) == CAP - SHOWN and plain(summary) == f"Show {CAP - SHOWN} more ({CAP + 50} in all)"
    assert folded.count(" Changed</span>") == 5 and folded.index(" Changed</span>") < folded.index(" Same</span>")
    # The sentence above names the first changed inputs and how many more (the table lists them all).
    said = html.split("This run's inputs differ from the files now.</strong>", 1)[1].split("</div>", 1)[0]
    assert said.count("in-") == workspace.SHOWN_PLACES and said.endswith(f"and {SHOWN + 5 - workspace.SHOWN_PLACES} more")
    assert BEYOND.search(inputs).group(1).endswith(record) and "50 more are not on this page" in inputs
    outputs = section(html, "outputs")
    assert f"Outputs ({CAP + 30})" in outputs and rows(visible(outputs)) == SHOWN
    assert rows(FOLD.search(outputs).group(2)) == CAP - SHOWN and "30 more are not on this page" in outputs
    # Warnings, the environment and the journal: the same rule; the journal's rest is on the relation page.
    warnings = html.split('<div class="callout tone-warning">', 1)[1].split("</div>", 1)[0]
    assert rows(visible(warnings)) == SHOWN and rows(FOLD.search(warnings).group(2)) == 10
    assert not BEYOND.search(warnings)
    env = section(html, "env")
    assert env.count("<tr>") == 33 and env.count('<details class="more">') == 1
    # The run's own completion record and the notes naming it; a fold starts with its day.
    journal = section(html, "journal")
    assert rows(visible(journal)) == SHOWN and rows(FOLD.search(journal).group(2)) == 1 + 5
    assert re.match(r'<ol class="timeline records"><li class="day">', FOLD.search(journal).group(2))


def test_a_dataset_s_columns_and_runs_fold(long):
    html = page(long, "data/wide.html")
    schema = html.split("Schema and statistics", 1)[1].split("</section>", 1)[0]
    assert rows(visible(schema)) == SHOWN and rows(FOLD.search(schema).group(2)) == 25
    readers = page(long, "data/in_000.html").split("Runs that read this data", 1)[1].split("<h3>", 1)[0]
    assert len(re.findall(r"<li><a href", visible(readers))) == SHOWN
    assert len(re.findall(r"<li><a href", FOLD.search(readers).group(2))) == 31 - SHOWN


def test_a_long_history_folds_with_the_version_shown_in_sight_and_its_rows_anchors(long):
    data = page(long, "data/t.html")
    history = data.split('id="version-history"', 1)[1].split("</section>", 1)[0]
    shown_rows = re.findall(r'id="data-v(\d+)"', visible(history))
    assert shown_rows == [str(n) for n in range(30, 10, -1)]
    (summary, folded), = FOLD.findall(history)
    # Each folded version keeps its anchor, where references to it lead.
    assert re.findall(r'id="data-v(\d+)"', folded) == [str(n) for n in range(10, 0, -1)]
    assert plain(summary) == "Show 10 more (30 in all)"
    # On the page of version 3 of 30, its row stays in sight between two folds.
    run = page(long, "runs/20261001-100200-v.html")
    viz = run.split('id="chart-c-versions-list"', 1)[1].split("</section>", 1)[0]
    assert [plain(s) for s, _ in FOLD.findall(viz)] == ["Show 7 more (30 in all)", "Show 2 more (30 in all)"]
    assert "is-shown" in visible(viz) and "is-shown" not in "".join(f for _, f in FOLD.findall(viz))
    assert len(re.findall(r'<li class="vh-row', viz)) == 30


def test_a_report_s_history_files_members_and_evidence_fold(long):
    name = workspace_reports.report_page("long")
    html = page(long, f"reports/{name}")
    history = html.split('id="report-history"', 1)[1].split("</section>", 1)[0]
    # The latest SHOWN versions and the first in sight; the ones between folded, each linking its section.
    assert re.findall(r'<span class="vh-n">v(\d+)</span>', visible(history)) == [str(n) for n in range(SHOWN + 5, 5, -1)] + ["1"]
    assert plain(FOLD.search(history).group(1)) == f"Show 4 more ({SHOWN + 5} in all)"
    latest = html.split('<section class="report-version">', 2)[1]
    files = latest.split("<h3>Files</h3>", 1)[1].split("</section>", 1)[0]
    assert rows(visible(files)) == SHOWN and rows(FOLD.search(files).group(2)) == 10
    members = latest.split('<details class="report-members">', 1)[1]
    assert len(re.findall(r'<tr id="m-', members)) == SHOWN + 10
    assert len(re.findall(r'<tr id="m-', FOLD.search(members).group(2))) == 10
    evidence = latest.split("<h3>Evidence</h3>", 1)[1].split("</section>", 1)[0]
    assert len(re.findall(r'class="ref ', visible(evidence))) == SHOWN
    assert len(re.findall(r'class="ref ', FOLD.search(evidence).group(2))) == 5


def test_the_index_s_lists_have_the_index_s_cap(long):
    """The first page a reader opens holds INDEX_LIMIT items of a list, as its list pages do."""
    evaluation = page(long, "index.html").split('<section id="evaluation"', 1)[1].split("</section>", 1)[0]
    tools = evaluation.split("<h3>Tool usage</h3>", 1)[1].split("<h3>Possible workarounds", 1)[0]
    flagged = evaluation.split("<h3>Possible workarounds", 1)[1]
    for listed in (tools, flagged):
        assert rows(visible(listed)) == SHOWN
        assert plain(FOLD.search(listed).group(1)) == f"Show {LIMIT - SHOWN} more ({LIMIT + 10} in all)"
        assert "10 more are not on this page. All of them:" in listed
        assert re.search(r'<p class="more-beyond">[^<]*<a href="\.\./\.analytics/telemetry\.jsonl">', listed)


def test_nothing_folded_needs_a_script_and_every_link_past_a_cap_resolves(long):
    for path in long.workspace_dir.rglob("*.html"):
        html = path.read_text(encoding="utf-8")
        for _, folded in FOLD.findall(html):
            # A native disclosure: its items are in the page, not made or shown by a script.
            assert folded.strip() and "hidden" not in folded.split(">", 1)[0], path.name
        for beyond in BEYOND.findall(html):
            target = re.search(r'href="([^"]+)"', beyond)
            if target:
                assert (path.parent / target.group(1).split("#", 1)[0]).resolve().is_file(), (path.name, beyond)
    # The run journal's rest is on the run's relation page, which lists every record naming it.
    journal = section(page(long, f"runs/{BIG}.html"), "journal")
    related = page(long, "relations/" + workspace_relations.name("run:" + BIG))
    assert int(re.search(r"Relations 1–\d+ of (\d+)", related).group(1)) >= rows(journal)


def test_an_address_naming_a_folded_item_opens_its_folds_in_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: controller checks are not browser QA")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / "tests/workspace_navigation_repairs.cjs"),
                             str(root / "src/aelix_analytics/assets/workspace-state.js"),
                             str(root / "src/aelix_analytics/assets/workspace.js")],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
