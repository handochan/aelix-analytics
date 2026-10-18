"""#99: version histories — a Visualization key, a data file, a script's code and CONTEXT.md counted
from the records the same way every time and shown the same way on every page (design
WORKSPACE-READABILITY 5). A number is for people; links and citations keep the records' ids."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from legacy_records import LegacyProject, at, sha
from workspace_words import plain

from aelix_analytics import core, workspace, workspace_versions

ORIGIN = core.Origin("human", "cli", "declared")
KEY = "scripts/s.py::c"
T = "data/derived/t.parquet"
RAW = "data/raw/in.parquet"
CODE_1 = b"import analytics\nframe = analytics.read('in')\nanalytics.save(frame, 't')\n"
CODE_2 = b"import analytics\nframe = analytics.read('in')\nframe = frame.bad()\nanalytics.save(frame, 't')\n"
CODE_3 = b"import analytics\nframe = analytics.read('other')\nanalytics.save(frame, 't')\n"


def run_id(minute: int, name: str = "s") -> str:
    return f"20261001-09{minute:02d}00-{name}"


def rerun(q: LegacyProject, rid: str, of: str, verdict: str) -> None:
    record = json.loads((q.root / "runs" / rid / "run.json").read_text(encoding="utf-8"))
    record.update(rerun_of=of, reproduction={"of": of, "verdict": verdict, "inputs_same": True,
                                             "charts": {}, "outputs": {}})
    q.write_json(f"runs/{rid}/run.json", record)


def versions_project(tmp_path) -> tuple[core.Project, dict]:
    """One script s.py writing t.parquet and drawing chart c, run seven times: code 1 twice (the
    second writing the same bytes again), code 2 failing, code 1 again (with a rerun of it), code 3
    reading another input, and a run that never ended. A reader script r.py reads t.parquet's first
    bytes, and a failed reader run reads them too."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    raw_1 = q.file(RAW, b"in-1")
    ids = {}
    ids["v1"] = q.run(run_id(0), script="scripts/s.py", started_at=at(0), ts=at(1), code=CODE_1,
                      inputs=[(RAW, raw_1)], outputs=[(T, q.file(T, b"t-1"))], charts=["c"])
    ids["read_1"] = q.run(run_id(2, "r"), script="scripts/r.py", started_at=at(2), ts=at(3),
                          inputs=[(T, sha(b"t-1"))])
    ids["again"] = q.run(run_id(4), script="scripts/s.py", started_at=at(4), ts=at(5), code=CODE_1,
                         inputs=[(RAW, raw_1)], outputs=[(T, sha(b"t-1"))], charts=["c"])
    ids["read_failed"] = q.run(run_id(6, "r"), script="scripts/r.py", status="error", started_at=at(6), ts=at(7),
                               inputs=[(T, sha(b"t-1"))])
    ids["failed"] = q.run(run_id(8), script="scripts/s.py", status="error", started_at=at(8), ts=at(9),
                          code=CODE_2, inputs=[(RAW, raw_1)])
    ids["v3"] = q.run(run_id(10), script="scripts/s.py", started_at=at(10), ts=at(11), code=CODE_1,
                      inputs=[(RAW, raw_1)], outputs=[(T, q.file(T, b"t-2"))], charts=["c"])
    ids["rerun"] = q.run(run_id(12), script="scripts/s.py", started_at=at(12), ts=at(13), code=CODE_1,
                         inputs=[(RAW, raw_1)], outputs=[(T, sha(b"t-2"))], charts=["c"])
    rerun(q, ids["rerun"], ids["v3"], "match")
    other = q.file("data/raw/other.parquet", b"other")
    ids["v4"] = q.run(run_id(14), script="scripts/s.py", started_at=at(14), ts=at(15), code=CODE_3,
                      inputs=[("data/raw/other.parquet", other)], outputs=[(T, q.file(T, b"t-3"))], charts=["c"])
    ids["running"] = q.run(run_id(16), script="scripts/s.py", status="running", started_at=at(16), code=CODE_3,
                           pid=999999999, completion=False)
    p = core.Project(q.root)

    def rec(kind: str, text: str, *refs) -> str:
        return core.record(p, kind, text, origin=ORIGIN, refs=list(refs)).event["id"]

    def chart(run: str) -> dict:
        return {"type": "visualization_version", "run_id": run, "visualization_id": "c", "relation": "cites"}

    ids["cites_1"] = rec("finding", "첫 버전의 차트", chart(ids["v1"]))
    ids["cites_1b"] = rec("finding", "첫 버전을 다시 인용", chart(ids["v1"]))
    ids["cites_rerun"] = rec("finding", "재실행의 차트", chart(ids["rerun"]))
    ids["unbound"] = rec("decision", "버전 없이 이름만",
                         {"type": "visualization_version", "key": KEY, "relation": "cites", "version_binding": "none"})
    return p, ids


def test_a_visualization_version_is_a_successful_run_that_is_not_a_rerun(tmp_path):
    p, ids = versions_project(tmp_path)
    model = core.read_model(p)
    history = model.visualization_history(KEY)
    assert [(v["number"], v["run"]) for v in history["versions"]] == [
        (1, ids["v1"]), (2, ids["again"]), (3, ids["v3"]), (4, ids["v4"])]
    # The failed run is no version, shown after the two versions before it; the run that never ended
    # makes nothing; the rerun is beside the version it re-executed, with its comparison.
    assert [(f["run"], f["after"], f["status"]) for f in history["failed"]] == [(ids["failed"], 2, "error")]
    assert [(r["run"], r["verdict"], r["cited_by"]) for r in history["versions"][2]["reruns"]] == [
        (ids["rerun"], "match", [ids["cites_rerun"]])]
    # The uses are the records citing each version with a version; a name without one is counted apart.
    assert [v["cited_by"] for v in history["versions"]] == [[ids["cites_1"], ids["cites_1b"]], [], [], []]
    assert history["named_by"] == [ids["unbound"]]
    assert history["latest"] == {"number": 4, "cited": False, "last_cited": 1}
    # Same code and inputs join a group; a failed run between and a code change break it.
    assert history["groups"] == [[1, 2], [3], [4]]
    assert [r["kind"] for r in history["versions"][3]["reasons"]] == ["code", "inputs"]
    assert history["versions"][3]["reasons"][1] == {"kind": "inputs", "changed": ["data/raw/other.parquet"],
                                                    "removed": [RAW]}
    # The gallery counts the same versions.
    chart = next(c for c in core.chart_index(core.model_runs(p, model), model.read_as) if c["key"] == KEY)
    assert [v["run"] for v in chart["versions"]] == [v["run"] for v in history["versions"]]


def test_a_data_version_is_new_bytes_and_the_same_bytes_again_keep_its_number(tmp_path):
    p, ids = versions_project(tmp_path)
    model = core.read_model(p)
    history = model.data_history(T)
    assert [(v["number"], v["sha256"], v["writes"]) for v in history["versions"]] == [
        (1, sha(b"t-1"), [ids["v1"], ids["again"]]), (2, sha(b"t-2"), [ids["v3"]]), (3, sha(b"t-3"), [ids["v4"]])]
    # Readers per version, a failed one among them; the rerun that wrote v2's bytes makes no version.
    assert [(r["run"], r["status"]) for r in history["versions"][0]["readers"]] == [
        (ids["read_1"], "ok"), (ids["read_failed"], "error")]
    assert [r["run"] for r in history["versions"][1]["reruns"]] == [ids["rerun"]]
    assert [(f["run"], f["after"], f["wrote"]) for f in history["failed"]] == [(ids["failed"], 1, None)]
    assert history["latest"] == {"number": 3, "read": False, "last_read": 1}
    assert history["now"] == {"state": "version", "number": 3, "sha256": sha(b"t-3")}
    assert history["scripts"] == {"writers": ["scripts/s.py"], "readers": ["scripts/r.py"]}
    assert history["groups"] == [[1], [2], [3]]
    # What the file is now is an observation of the read: other bytes, then none.
    (p.root / T).write_bytes(b"by hand")
    assert core.read_model(p).data_history(T)["now"] == {"state": "other", "sha256": sha(b"by hand")}
    (p.root / T).unlink()
    assert core.read_model(p).data_history(T)["now"]["state"] == "missing"


def test_bytes_written_again_later_are_the_earlier_version_and_a_failed_run_s_bytes_are_none(tmp_path):
    q = LegacyProject(tmp_path / "p", migrated=True)
    q.run(run_id(0), started_at=at(0), ts=at(1), outputs=[(T, q.file(T, b"a"))])
    q.run(run_id(2), started_at=at(2), ts=at(3), outputs=[(T, q.file(T, b"b"))])
    q.run(run_id(4), started_at=at(4), ts=at(5), outputs=[(T, q.file(T, b"a"))])
    q.run(run_id(6), status="error", started_at=at(6), ts=at(7), outputs=[(T, q.file(T, b"partial"))])
    history = core.read_model(core.Project(q.root)).data_history(T)
    assert [(v["number"], v["writes"]) for v in history["versions"]] == [
        (1, [run_id(0), run_id(4)]), (2, [run_id(2)])]
    assert history["failed"][0]["wrote"] == sha(b"partial")
    assert history["now"] == {"state": "failed_output", "run": run_id(6), "sha256": sha(b"partial")}


def test_imported_data_has_a_version_per_import_and_per_bytes_read_outside_one(tmp_path):
    q = LegacyProject(tmp_path / "p", migrated=True)
    path = q.dataset("in", imported_at=at(0))
    q.run(run_id(2), started_at=at(2), ts=at(3), inputs=[(path, sha(b"in"))])
    q.run(run_id(4), started_at=at(4), ts=at(5), inputs=[(path, sha(b"edited"))])
    q.run(run_id(6), started_at=at(6), ts=at(7), inputs=[(path, None)])
    history = core.read_model(core.Project(q.root)).data_history(path)
    assert [(v["number"], v["source"], [r["run"] for r in v["readers"]]) for v in history["versions"]] == [
        (1, "import", [run_id(2)]), (2, "read", [run_id(4)])]
    assert [r["kind"] for r in history["versions"][1]["reasons"]] == ["outside"]
    assert [(r["run"], r["sha256"]) for r in history["other_reads"]] == [(run_id(6), "")]


def test_a_code_version_is_a_snapshot_hash_and_earlier_code_keeps_its_number(tmp_path):
    p, ids = versions_project(tmp_path)
    history = core.read_model(p).code_history("scripts/s.py")
    assert [(v["number"], v["failed"], len(v["runs"])) for v in history["versions"]] == [
        (1, False, 4), (2, True, 1), (3, False, 1)]
    # Lines changed against the code run before each version first ran (v2 and v3 against v1).
    assert [(v["added"], v["removed"], v["against"]) for v in history["versions"]] == [
        (None, None, None), (1, 0, 1), (1, 1, 1)]
    assert [(s["number"], s["again"], [r["run"] for r in s["runs"]]) for s in history["segments"]] == [
        (1, False, [ids["v1"], ids["again"]]), (2, False, [ids["failed"]]),
        (1, True, [ids["v3"], ids["rerun"]]), (3, False, [ids["v4"]])]
    # What each segment made, by the numbers the other histories give; the rerun makes none.
    assert [s["visualizations"] for s in history["segments"]] == [{KEY: [1, 2]}, {}, {KEY: [3]}, {KEY: [4]}]
    assert [s["data"] for s in history["segments"]] == [{T: [1, 1]}, {}, {T: [2]}, {T: [3]}]
    assert history["segments"][-1]["inputs"] == {"changed": ["data/raw/other.parquet"], "removed": [RAW]}
    assert history["latest"] == {"number": 3}


def context_project(tmp_path) -> core.Project:
    """Five CONTEXT.md snapshots: the pinned goal set in the second, a note added in each after;
    insights written while each was the latest."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    texts = ["# P\n\n## Goal <!-- pin -->\n(not yet)\n",
             "# P\n\n## Goal <!-- pin -->\n목표를 정했다\n",
             "# P\n\n## Goal <!-- pin -->\n목표를 정했다\n\n## Note 1\n메모\n",
             "# P\n\n## Goal <!-- pin -->\n목표를 정했다\n\n## Note 1\n메모\n\n## Note 2\n메모 둘\n",
             "# P\n\n## Goal <!-- pin -->\n목표를 정했다\n\n## Note 1\n메모\n\n## Note 2\n메모 둘\n\n## Note 3\n셋\n"]
    for n, text in enumerate(texts):
        name = f"20261001-09{n:02d}00-000000_{n:012x}.md"
        q.file(f".analytics/context/{name}", text.encode())
        q.event("context", {"context": name}, ts=at(n * 10))
        for _ in range(n):
            q.event("finding", ts=at(n * 10 + 1))
        q.event("question", ts=at(n * 10 + 2))
    return core.Project(q.root)


def test_context_versions_say_when_the_pinned_sections_changed_and_the_insights_meanwhile(tmp_path):
    p = context_project(tmp_path)
    history = core.context_history(core.read_model(p))
    assert [(v["number"], v["pinned_changed"], v["insights"], v["added"], v["removed"])
            for v in history["versions"]] == [(1, None, 0, 4, 0), (2, True, 1, 1, 1), (3, False, 2, 3, 0),
                                              (4, False, 3, 3, 0), (5, False, 4, 3, 0)]
    assert history["groups"] == [[1], [2], [3, 4, 5]]
    assert history["latest"] == {"number": 5, "pinned_since": 2}


def test_a_correction_history_joins_records_in_order_without_numbers(tmp_path):
    q = LegacyProject(tmp_path / "p", migrated=True)
    p = core.Project(q.root)
    first = core.record(p, "finding", "처음", origin=ORIGIN).event["id"]
    second = core.record(p, "finding", "정정", origin=ORIGIN,
                         refs=[{"type": "event", "event_id": first, "relation": "supersedes"}]).event["id"]
    third = core.record(p, "finding", "다시 정정", origin=ORIGIN,
                        refs=[{"type": "event", "event_id": second, "relation": "supersedes"}]).event["id"]
    alone = core.record(p, "finding", "따로", origin=ORIGIN).event["id"]
    model = core.read_model(p)
    chain = model.correction_history(second)
    assert [(c["source"], c["state"], c["corrects"], c["corrected_by"]) for c in chain] == [
        (first, "superseded", [], [second]), (second, "superseded", [first], [third]),
        (third, "current", [second], [])]
    assert model.correction_history(first) == chain
    assert model.correction_history(alone) == []
    workspace.build(p)
    page = plain((p.workspace_dir / "relations" / workspace.Site(p).relations.filename(second)).read_text(encoding="utf-8"))
    history = page.split('id="corrections"', 1)[1]
    assert history.index(f">{first}</a>") < history.index(f">{second}</a>") < history.index(f">{third}</a>")
    assert "(this record)" in history and "v1" not in history


def rows(html: str) -> list[str]:
    """The text of each version row of a history (`<li class="vh-row…">`), tags removed."""
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", part.split(">", 1)[1].split("</li>", 1)[0])).strip()
            for part in html.split('<li class="vh-row')[1:]]


def test_every_page_shows_the_same_numbers_and_reads_without_a_script(tmp_path):
    p, ids = versions_project(tmp_path)
    workspace.build(p)
    out = p.workspace_dir
    index = plain((out / "index.html").read_text(encoding="utf-8"))
    card = index.split('id="chart-', 1)[1].split("</article>", 1)[0]
    assert ">v4</span>" in card and 'title="Version 4 of 4"' in card and "4 versions" in card
    # The run page of v3 is that version's page: its number, the history with it marked, the rerun
    # beside it, the failed run where it came. The strip is a picture; the list says the same.
    page = plain((out / "runs" / f"{ids['v3']}.html").read_text(encoding="utf-8"))
    chart = page.split('id="chart-c-versions"', 1)[0].rsplit("vh-showing", 1)[1] + page.split('id="chart-c-versions"', 1)[1]
    assert "Version 3 of 4" in chart
    assert re.search(r'<svg class="vh-strip kind-viz"[^>]*aria-hidden="true"', chart)
    listed = rows(chart)
    assert [r.split(" ", 1)[0] for r in listed] == ["v4", "v3", "×", "v2", "v1"]
    # The rerun's own citation is beside it (a rerun's version is cited apart), not the version's.
    assert "Rerun Matched · Cited by 1" in listed[1] and listed[1].endswith("Cited by 0")
    assert "Failed run" in listed[2] and "no visualization" in listed[2]
    assert listed[4].endswith("Cited by 2 Open")
    assert "1 record names this visualization without a version." in chart
    # A rerun's page says it is no version and which one it re-executed.
    rerun_page = plain((out / "runs" / f"{ids['rerun']}.html").read_text(encoding="utf-8"))
    assert "This run re-executed version 3: a rerun makes no version." in rerun_page
    # The dataset page: the same versions, the readers of each, the file now.
    data = plain((out / "data" / "t.html").read_text(encoding="utf-8"))
    assert "Version 3 of 3" in data and "the file now is version 3" in data
    assert "No run has read version 3 yet." in data and "The last version a run read is version 1:" in data
    assert [r.split(" ", 1)[0] for r in rows(data)] == ["v3", "v2", "×", "v1"]
    # The script page: code versions, earlier code again, the failed one; the run page links to it.
    name = workspace_versions.script_page("scripts/s.py")
    script = plain((out / "scripts" / name).read_text(encoding="utf-8"))
    assert [r.split(" ", 2)[:2] for r in rows(script)] == [["v3", "Oct"], ["v1", "again"], ["v2", "Oct"], ["v1", "Oct"]]
    assert "viz v3" in rows(script)[1] and "data v2" in rows(script)[1]
    assert "Changes from code version 1 to 3" in script
    assert f'href="../scripts/{name}"' in page and "code version 1 of 3" in page
    assert sorted(f.name for f in (out / "scripts").iterdir()) == sorted(
        workspace_versions.script_page(s) for s in ("scripts/r.py", "scripts/s.py"))


def test_a_script_page_that_no_longer_has_a_script_is_removed(tmp_path):
    p, _ = versions_project(tmp_path)
    workspace.build(p)
    stale = p.workspace_dir / "scripts" / "gone.py-0000000000.html"
    stale.write_text("old", encoding="utf-8")
    workspace.build(p)
    assert not stale.exists()


def test_the_context_page_lists_its_versions_and_keeps_their_anchors(tmp_path):
    p = context_project(tmp_path)
    workspace.build(p)
    page = plain((p.workspace_dir / "context.html").read_text(encoding="utf-8"))
    assert "Version 5 of 5" in page and "The pinned sections last changed in version 2" in page
    listed = rows(page)
    assert [r.split(" ", 1)[0] for r in listed] == ["v5", "v4", "v3", "v2", "v1"]
    assert "Pinned sections changed" in listed[3] and "1 insight" in listed[3]
    for n in range(5):
        assert f'id="v-20261001-09{n:02d}00-000000_{n:012x}"' in page
    # Versions with the pinned sections unchanged are folded into one row; the latest is a row of
    # its own.
    group = page.split('<li class="vh-group">', 1)[1].split("</details>", 1)[0]
    assert "v3–v4 · 2 versions" in group and "v-20261001-090300" in group and "v-20261001-090400" not in group


def test_script_pages_are_pages_the_url_state_may_name():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: pure URL codec checks are not browser QA")
    codec = Path(__file__).resolve().parents[1] / "src/aelix_analytics/assets/workspace-state.js"
    script = r'''
const assert = require("node:assert/strict"), codec = require(process.argv[1]);
assert.equal(codec.safePage("scripts/s.py-0123456789.html"), true);
for (const page of ["scripts/%2e%2e.html", "scripts/a/b.html", "scripts/../index.html", "script/s.html"]) assert.equal(codec.safePage(page), false, page);
'''
    subprocess.run([node, "-e", script, str(codec)], check=True)


@pytest.mark.parametrize("path", ["scripts/s.py", "분석/단계 1.py", "a/b/../c.py", ""])
def test_a_script_page_name_is_safe_and_one_per_path(path):
    name = workspace_versions.script_page(path)
    assert re.fullmatch(r"[A-Za-z0-9._-]+-[0-9a-f]{10}\.html", name)
    assert name != workspace_versions.script_page(path + "x")


def test_a_long_history_shows_the_rows_near_a_version_and_all_of_them_on_the_latest_page(tmp_path):
    q = LegacyProject(tmp_path / "p", migrated=True)
    count = workspace_versions.WINDOW_ROWS + 5
    for n in range(count):
        # Each run reads other bytes, so no two versions share a group.
        q.run(f"20261001-{n // 60:02d}{n % 60:02d}00-s", script="scripts/s.py", started_at=at(n % 60, hour=n // 60),
              ts=at(n % 60, hour=n // 60), code=CODE_1, inputs=[(RAW, sha(str(n).encode()))], charts=["c"])
    p = core.Project(q.root)
    workspace.build(p)
    first = plain((p.workspace_dir / "runs" / "20261001-000000-s.html").read_text(encoding="utf-8"))
    latest = plain((p.workspace_dir / "runs" / f"20261001-{(count - 1) // 60:02d}{(count - 1) % 60:02d}00-s.html").read_text(
        encoding="utf-8"))
    assert len(rows(first)) == workspace_versions.WINDOW_ROWS and "vh-strip" not in first.split("chart-c-versions", 1)[1]
    assert f"All {count} versions on the latest version's page" in first
    assert len(rows(latest)) == count and '<svg class="vh-strip' in latest
