"""The screens show relations as the read model judges them (Issue #12 PR-2).

analytics_status's overview (core.status and core.status_text, which `/analytics status` and the
kit's `status` give too) and the Workspace (workspace.build) read the records once through
relations.read, so they name the same targets by the same identifiers and say which read they were
made from (as_of_seq, source_revision). The projects hold records an older kit wrote, written record by
record (tests/legacy_records.py) under the new format's marker — what a migration leaves, which this kit
renders (it builds no Workspace for a lab.json project) — so each case states exactly which records exist.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from legacy_records import LegacyProject, at
from workspace_words import plain

from aelix_analytics import core, i18n, relations, workspace

# The English title of a relation found only through an older kit's path (workspace.inferred_title).
INFERRED_TITLE = i18n.message("inferred.title", since=core.PATH_IDENTITY_SINCE)

READ_TEXT = re.compile(r"^read: as_of_seq (\d+) · source_revision ([0-9a-f]{16})$", re.M)
READ_HTML = re.compile(r"Read at as_of_seq (\d+) · source_revision ([0-9a-f]{16})")


def project_of(p: LegacyProject) -> core.Project:
    return core.Project(p.root)


def overview(p: LegacyProject) -> tuple[dict, str]:
    st = core.status(project_of(p))
    return st, core.status_text(st)


def built(p: LegacyProject) -> dict[str, str]:
    """The Workspace's pages, by path under workspace/."""
    project = project_of(p)
    workspace.build(project)
    return {path.relative_to(project.workspace_dir).as_posix(): plain(path.read_text(encoding="utf-8"))
            for path in project.workspace_dir.rglob("*.html")}


def evidence_project(tmp_path) -> SimpleNamespace:
    """Runs that write one table with the same bytes, a run that consumes it, a failed run, a table
    overwritten since its run, a table no run wrote, and a finding citing a run, a chart key left
    unbound, an earlier event, a later one and a table."""
    p = LegacyProject(tmp_path / "p", migrated=True)
    base = p.dataset("base", imported_at=at(1))
    p.event("import", {"data": [base]}, ts=at(2))
    base_sha = core.sha256_file(p.root / base)
    table = ("data/derived/t.parquet", p.file("data/derived/t.parquet", b"t1"))
    a = p.run("20261001-091000-a", started_at=at(10), ts=at(11), inputs=[(base, base_sha)],
              outputs=[table], charts=["trend"])
    b = p.run("20261001-092000-b", script="scripts/b.py", started_at=at(20), ts=at(21),
              inputs=[(base, base_sha)], outputs=[table], charts=["mix"])
    c = p.run("20261001-093000-c", script="scripts/c.py", started_at=at(30), ts=at(31),
              inputs=[table], outputs=[("data/derived/u.parquet", p.file("data/derived/u.parquet", b"u"))],
              charts=["summary"])
    failed = p.run("20261001-094000-d", script="scripts/d.py", status="error", started_at=at(40),
                   ts=at(41), outputs=[("data/derived/v.parquet", p.file("data/derived/v.parquet", b"v"))])
    late = p.run("20261001-095000-late", script="scripts/b.py", started_at=at(50), ts=at(51),
                 outputs=[table])
    overwritten = p.run("20261001-095500-e", script="scripts/e.py", started_at=at(55), ts=at(56),
                        outputs=[("data/derived/x.parquet", p.file("data/derived/x.parquet", b"x1"))])
    p.file("data/derived/x.parquet", b"x2")  # changed after the run that wrote it
    p.file("data/derived/w.parquet", b"w")  # written outside any run
    finding = p.event("finding", {"runs": [a], "charts": ["scripts/a.py::trend"],
                                  "events": ["e1", "e999"], "data": ["data/derived/t.parquet"]},
                      ts=at(58), text="t grew")
    return SimpleNamespace(p=p, base=base, a=a, b=b, c=c, failed=failed, late=late,
                           overwritten=overwritten, finding=finding)


# --- one read, one set of identifiers -------------------------------------------------------------

def test_the_overview_and_the_workspace_name_one_read_and_one_identifier(tmp_path):
    """Issue #12 completion condition 4 (critique must-change 5): the text the agent gets and the
    pages a person reads say the same as_of_seq and source_revision, and name one reference's
    target by the same string."""
    case = evidence_project(tmp_path)
    st, text = overview(case.p)
    model = relations.read(case.p.root, alive=lambda pid: False, sha=lambda path: None)
    [(seq, revision)] = READ_TEXT.findall(text)
    assert (int(seq), revision) == (st["as_of_seq"], st["source_revision"])
    assert (int(seq), revision) == (model.as_of_seq, model.source_revision)
    pages = built(case.p)
    for page in ("index.html", f"runs/{case.c}.html", "context.html"):
        assert READ_HTML.findall(pages[page]) == [(seq, revision)], page
    # One reference, one identifier: the later event the finding names is e999 on both screens.
    key_line = next(line for line in text.split("\n") if line.startswith(f"  {case.finding} finding:"))
    assert "1 missing (e999)" in key_line and "2 version_unbound" in key_line
    assert "e999 <small class=\"ref-state tone-critical\">Missing: record_absent</small>" in pages["index.html"]
    assert "chart scripts/a.py::trend" in pages["index.html"]  # the unbound key, by its logical key


def test_a_key_entry_counts_its_references_by_state(tmp_path):
    case = evidence_project(tmp_path)
    st, text = overview(case.p)
    [entry] = [e for e in st["key"] if e["id"] == case.finding]
    # run a, e1 resolved and confirmed; the chart key and the table are unbound; e999 is later.
    assert entry["references"] == {
        "states": {"resolved": 2, "missing": 1, "version_unbound": 2}, "confirmed": 2,
        "not_resolved": [{"state": "missing", "target": "e999"},
                         {"state": "version_unbound", "target": "scripts/a.py::trend"},
                         {"state": "version_unbound", "target": "data/derived/t.parquet"}]}
    assert entry["source"] == case.finding
    # Unbound and unresolved targets are counted; those that need a look are named.
    assert (f"  {case.finding} finding: t grew · refs: 2 resolved (2 confirmed), 1 missing (e999), "
            "2 version_unbound") in text.split("\n")


def test_a_list_of_targets_names_three_and_says_where_the_rest_are(tmp_path):
    p = LegacyProject(tmp_path / "p", migrated=True)
    p.event("finding", {"events": [f"e{90 + i}" for i in range(5)],
                        "charts": [f"scripts/s.py::c{i}" for i in range(5)]}, text="five later")
    _, text = overview(p)
    assert ("· refs: 5 missing (e90, e91, e92 and 2 more (in the entry's refs in the journal)), "
            "5 version_unbound") in text


def test_a_key_entry_whose_references_need_no_look_has_no_refs_summary(tmp_path):
    """The overview names an entry's references only when one of them needs a look (missing,
    ambiguous, changed, unsupported); the counts of one whose references resolve or are left unbound
    are in the JSON. At M scale those summaries cut the newest entries (review of 8d2ecc7)."""
    p = LegacyProject(tmp_path / "p", migrated=True)
    run = p.run("20261001-090000-a", started_at=at(0), ts=at(1))
    finding = p.event("finding", {"runs": [run], "charts": ["scripts/a.py::c"]}, ts=at(5), text="fine")
    st, text = overview(p)
    [entry] = [e for e in st["key"] if e["id"] == finding]
    assert entry["references"]["states"] == {"resolved": 1, "version_unbound": 1}
    assert f"  {finding} finding: fine" in text.split("\n")
    assert "refs:" not in text


def test_a_target_is_one_short_line_and_an_unreadable_field_is_named(tmp_path):
    p = LegacyProject(tmp_path / "p", migrated=True)
    long_id = "e" + "9" * 200
    finding = p.event("finding", {"events": [long_id + "\nx"], "weird": ["x"]}, text="odd")
    st, text = overview(p)
    [entry] = st["key"]
    assert entry["references"]["not_resolved"] == [{"state": "missing", "target": long_id + "\nx"},
                                                   {"state": "unsupported", "target": "refs.weird"}]
    assert (f"  {finding} finding: odd · refs: 1 missing ({long_id[:79]}…), 1 unsupported "
            "(refs.weird)") in text.split("\n")
    assert 'refs.weird <small class="ref-state tone-muted">Unsupported</small>' in built(p)["index.html"]


# --- producers --------------------------------------------------------------------------------------

def test_a_dataset_names_every_run_that_output_its_bytes_and_never_the_last_one(tmp_path):
    case = evidence_project(tmp_path)
    st, text = overview(case.p)
    by_name = {ds["name"]: ds for ds in st["datasets"]}
    t = by_name["t"]
    assert t["producer_state"] == "ambiguous" and "producer" not in t
    assert t["producer_candidates"] == sorted([case.a, case.b, case.late])
    assert t["producers"] == [case.a, case.b, case.late]  # every successful run that wrote the path
    assert by_name["u"]["producer_state"] == "resolved" and by_name["u"]["producer"] == case.c
    assert by_name["v"]["producer_state"] == "none" and by_name["v"]["written_by"] == [case.failed]
    assert by_name["x"]["producer_state"] == "none" and by_name["x"]["written_by"] == [case.overwritten]
    assert by_name["w"]["producer_state"] == "none" and by_name["w"]["written_by"] == []
    lines = {ds: next(line for line in text.split("\n") if line.startswith(f"  derived {ds}:"))
             for ds in "tuvwx"}
    assert f"same bytes as 3 runs: {case.a}, {case.b}, {case.late}" in lines["t"]
    assert f"same bytes as run {case.c}'s output" in lines["u"]
    assert ("no successful run output these bytes (changed since, or written by a run that did not "
            f"succeed); runs that wrote this path, latest first: {case.failed}") in lines["v"]
    assert "NO PRODUCING RUN (written outside analytics_run" in lines["w"]
    assert "← run" not in text


def test_more_than_three_candidates_are_counted_with_where_to_read_them(tmp_path):
    p = LegacyProject(tmp_path / "p", migrated=True)
    table = ("data/derived/t.parquet", p.file("data/derived/t.parquet", b"same"))
    runs = [p.run(f"20261001-09{i:02d}00-r{i}", started_at=at(i), ts=at(i), outputs=[table])
            for i in range(5)]
    _, text = overview(p)
    assert (f"same bytes as 5 runs: {runs[0]}, {runs[1]}, {runs[2]} and 2 more (the kit's `status` "
            "command lists every one in its JSON `producer_candidates`)") in text


def test_a_dataset_that_cannot_be_hashed_has_an_unknown_producer(tmp_path):
    case = evidence_project(tmp_path)
    project = project_of(case.p)
    model = core.read_model(project)
    datasets = core.list_datasets(project, core.model_runs(project, model), model=model,
                                  sha=lambda path: None)
    assert {ds["producer_state"] for ds in datasets if ds["kind"] == "derived"} == {"unknown"}
    assert core.dataset_origin(next(ds for ds in datasets if ds["name"] == "t")) == \
        "producer unknown: the file could not be hashed"
    # Without a read model nothing is hashed and no producer is judged (the views' cost).
    assert all("producer_state" not in ds for ds in core.list_datasets(project))


def test_an_import_record_this_kit_cannot_read_gives_only_its_place(tmp_path):
    """A lab.dataset/2 record was counted unsupported while its rows, hash, description and source
    filled the dataset's line and page (review of 8d2ecc7)."""
    p = LegacyProject(tmp_path / "p", migrated=True)
    p.dataset("future", imported_at=at(1), schema="lab.dataset/2")
    place = {"location": "data/raw/future.meta.json", "reason": "unknown_version"}
    st, text = overview(p)
    assert st["unsupported"] == [place]
    [ds] = st["datasets"]
    assert ds["unsupported_record"] == place
    assert not {"rows", "sha256", "description", "source", "imported_at"} & set(ds)
    assert ("  raw     future: ? rows × ? cols · data/raw/future.parquet · import record not read "
            "(data/raw/future.meta.json: unknown_version) · used by 0 run(s)") in text.split("\n")
    [plain] = core.list_datasets(project_of(p))  # without a read model too
    assert plain["unsupported_record"] == place and "rows" not in plain
    pages = built(p)
    assert "/elsewhere/future.csv" not in pages["data/future.html"]
    # The record's place is a path people wrote (lang=""), beside the kit's words (#96).
    assert 'import record not read (<span lang="">data/raw/future.meta.json</span>: unknown_version)' in pages["data/future.html"]
    assert "Import record not read" in pages["index.html"]


def test_stale_follows_every_candidate_and_not_one_that_started_later(tmp_path):
    case = evidence_project(tmp_path)
    project = project_of(case.p)
    fresh = core.Freshness(project, core.list_runs(project))
    assert fresh.of(case.c)["stale"] is False
    assert fresh.of(case.c)["ambiguous_inputs"] == ["data/derived/t.parquet"]
    (case.p.root / case.base).write_bytes(b"changed")  # what a and b read
    fresh = core.Freshness(project, core.list_runs(project))
    result = fresh.of(case.c)
    assert result["stale"] and result["changed_inputs"] == []
    assert result["stale_upstream"] == [case.a, case.b]  # both candidates; the late run is none
    assert result["cycle"] is False
    st, text = overview(case.p)
    [row] = [c for c in st["charts"] if c["run"] == case.c]
    assert row["stale_upstream"] == [case.a, case.b] and row["ambiguous_inputs"] == [
        "data/derived/t.parquet"]
    line = next(line for line in text.split("\n") if line.startswith("  scripts/c.py::summary"))
    assert f"STALE(upstream candidates {case.a}, {case.b}); producer ambiguous for 1 input(s)" in line


def _card(page: str, key: str) -> str:
    """A chart's card in the gallery."""
    start = page.index(f'id="chart-{workspace.slug_key(key)}"')
    return page[start:page.index("</article>", start)]


def test_a_stale_candidate_upstream_is_not_said_to_be_a_changed_input(tmp_path):
    """A run none of whose inputs changed, whose input has the same bytes as a stale run's output,
    was said to read inputs that differ from the files, with the stale run as its upstream (review of
    8d2ecc7). That run is a producer candidate; the critical notice is for a changed input."""
    case = evidence_project(tmp_path)
    (case.p.root / case.base).write_bytes(b"changed")  # what a and b read; c reads their table
    pages = built(case.p)
    consumer = pages[f"runs/{case.c}.html"]
    assert "This run's inputs differ from the files now." not in consumer
    assert "<strong>Upstream candidate stale.</strong> An input has the same bytes as the output of " \
           f'run(s) <a href="../runs/{case.a}.html">{case.a}</a>' in consumer
    assert "producer candidates, not confirmed links." in consumer
    assert (f'This run\'s inputs differ from the files now.</strong> <span lang="">{case.base}</span></div>'
            in pages[f"runs/{case.a}.html"])
    card = _card(pages["index.html"], "scripts/c.py::summary")
    assert "Input changed" not in card and "Upstream candidate stale" in card
    assert f"output of run(s) {case.a}, {case.b}, whose inputs" in card
    assert "Input changed" in _card(pages["index.html"], "scripts/a.py::trend")


def test_a_diamond_is_no_cycle_and_a_true_cycle_ends(tmp_path):
    """Critique must-change 2: a run reached twice through two producers is not a cycle; two runs
    that started at the same instant and read each other's output are, and the walk ends."""
    p = LegacyProject(tmp_path / "p", migrated=True)
    a = ("data/derived/a.parquet", p.file("data/derived/a.parquet", b"a"))
    b = ("data/derived/b.parquet", p.file("data/derived/b.parquet", b"b"))
    c = ("data/derived/c.parquet", p.file("data/derived/c.parquet", b"c"))
    top = p.run("20261001-090100-top", started_at=at(1), ts=at(1), outputs=[a])
    left = p.run("20261001-090200-left", started_at=at(2), ts=at(2), inputs=[a], outputs=[b])
    right = p.run("20261001-090300-right", started_at=at(3), ts=at(3), inputs=[a], outputs=[c])
    bottom = p.run("20261001-090400-bottom", started_at=at(4), ts=at(4), inputs=[b, c], charts=["d"])
    project = project_of(p)
    fresh = core.Freshness(project, core.list_runs(project))
    assert fresh.of(bottom)["cycle"] is False and not fresh.of(bottom)["stale"]
    (p.root / a[0]).write_bytes(b"A")  # top's output changed: left and right read it
    fresh = core.Freshness(project, core.list_runs(project))
    assert fresh.of(bottom)["stale_upstream"] == [left, right] and fresh.of(bottom)["cycle"] is False
    assert top not in fresh.of(bottom)["stale_upstream"]

    q = LegacyProject(tmp_path / "q", migrated=True)
    x = ("data/derived/x.parquet", q.file("data/derived/x.parquet", b"x"))
    y = ("data/derived/y.parquet", q.file("data/derived/y.parquet", b"y"))
    one = q.run("20261001-090000-one", started_at=at(0), ts=at(1), inputs=[y], outputs=[x], charts=["p"])
    two = q.run("20261001-090000-two", script="scripts/b.py", started_at=at(0), ts=at(1),
                inputs=[x], outputs=[y])
    st, text = overview(q)
    [row] = [r for r in st["charts"] if r["run"] == one]
    assert row["cycle"] is True and row["stale"] is False
    assert "producer cycle" in text
    (q.root / y[0]).write_bytes(b"Y")
    fresh = core.Freshness(project_of(q), core.list_runs(project_of(q)))
    assert fresh.of(two)["stale"] and fresh.of(one)["stale"]  # one's input changed; two reads one


# --- runs, records and lines --------------------------------------------------------------------

def test_a_run_whose_recording_did_not_finish_says_why(tmp_path):
    p = LegacyProject(tmp_path / "p", migrated=True)
    lost = p.run("20261001-090000-lost", completion=False)
    partial = p.run("20261001-091000-partial", finalize_errors=["journal: disk full"])
    stopped = p.run("20261001-092000-stopped", status="running", pid=4294967295, completion=False)
    st, text = overview(p)
    rows = {r["id"]: r for r in st["runs"]}
    assert (rows[lost]["recording_status"], rows[lost]["reasons"]) == ("incomplete", ["no_completion_event"])
    assert (rows[partial]["recording_status"], rows[partial]["reasons"]) == ("incomplete", ["finalize_errors"])
    assert rows[stopped]["status"] == "incomplete"
    assert rows[stopped]["reasons"] == ["not_final", "no_completion_event"]
    line = next(line for line in text.split("\n") if line.startswith(f"  {stopped} "))
    assert line.endswith("· recording incomplete (not_final, no_completion_event)")
    pages = built(p)
    assert "Recording incomplete" in pages[f"runs/{partial}.html"]
    assert "some recording steps failed" in pages[f"runs/{partial}.html"]


def test_records_the_lists_cannot_show_are_counted_where_they_are(tmp_path):
    p = LegacyProject(tmp_path / "p", migrated=True)
    p.event("finding", text="first")
    for _ in range(7):
        p.event("verdict")  # a kind the /1 writer never wrote
    p.line({"schema": "analytics.event/2", "seq": 20, "id": "e20", "recorded_at": at(30), "kind": "note",
            "actor": "agent", "text": "next format", "refs": []})
    st, text = overview(p)
    assert len(st["unsupported"]) == 7 and st["unsupported"][0] == {"location": "journal:2",
                                                                    "reason": "unknown_kind"}
    assert ("unsupported: 7 record(s) this kit cannot read are in no list here and not in the report — "
            "journal:2 (unknown_kind), journal:3 (unknown_kind), journal:4 (unknown_kind), journal:5 "
            "(unknown_kind), journal:6 (unknown_kind) and 2 more (the kit's `status` command lists "
            "every one in its JSON)") in text
    assert "a newer kit" not in text  # a kind the /1 writer never wrote is no newer version
    # An analytics.event/2 record is listed with the /1 records, in seq order (Issue #13 PR 1, design
    # 5.2: the state map's P6); before it, it was counted as `not_listed` only.
    assert "not_listed" not in st and "not listed: 1 analytics.event/2" not in text
    assert [(e["id"], e["kind"], e["text"]) for e in st["recent"]] == [("e1", "finding", "first"),
                                                                     ("e20", "note", "next format")]
    assert "  e20 2026-10-01 09:30 note: next format" in text
    index = built(p)["index.html"]
    assert "Unsupported: 7 record(s) this kit cannot read" in index
    assert "Not listed" not in index and "next format" in index


def test_only_records_of_a_newer_version_are_said_to_be_for_a_newer_kit(tmp_path):
    p = LegacyProject(tmp_path / "p", migrated=True)
    p.event("note", schema="lab.event/9")
    p.event("note", schema="other.event/1")
    st, text = overview(p)
    assert [u["reason"] for u in st["unsupported"]] == ["unknown_version", "unknown_family"]
    assert text.count("a newer kit may read the 1 of an unknown version") == 1
    assert "A newer kit may read the 1 of an unknown version." in built(p)["index.html"]


def test_an_id_on_many_lines_names_two_lines_an_id_and_five_in_all(tmp_path):
    """One id on 1,200 lines named every line: a 15,800-character line that cut the overview to
    nothing (review of 8d2ecc7). The rest of the lines are in the status JSON's shared_ids."""
    p = LegacyProject(tmp_path / "p", migrated=True)
    for n in range(1200):
        p.event("note", seq=1, text=f"note {n}")
    for seq in (2, 3, 4):
        for _ in range(3):
            p.event("note", seq=seq)
    st, text = overview(p)
    assert len(st["shared_ids"]["e1"]) == 1200 and len(st["shared_ids"]) == 4
    [line] = [line for line in text.split("\n") if line.startswith("ids on several lines:")]
    assert line == ("ids on several lines: e1 (journal:1, journal:2 and 1,198 more line(s)), e2 "
                    "(journal:1201, journal:1202 and 1 more line(s)), e3 (journal:1204 and 2 more "
                    "line(s)) and 1 more id(s) (the kit's `status` command lists every line in its JSON "
                    "`shared_ids`) — each line is its own record, and a reference to the id is ambiguous")
    assert "recent journal:" in text and "truncated" not in text
    index = built(p)["index.html"]
    assert "e1 (journal:1, journal:2 and 1,198 more line(s)), e2 (journal:1201" in index
    assert "lists every line in its JSON shared_ids" in index


def test_a_cut_line_and_the_record_appended_onto_it(tmp_path):
    """F3: the record a /1 writer appended onto a cut-off line was lost with it. Now it is read, the
    line is counted as damaged, and an id two lines have is two records, each named by its line."""
    p = LegacyProject(tmp_path / "p", migrated=True)
    p.event("question", text="why?")
    p.raw(b'{"schema": "lab.event/1", "seq": 2, "id": "e2", "kind": "fin')  # cut off
    p.event("finding", text="appended", seq=2)
    p.event("finding", text="twin", seq=2)
    events = core.read_events(project_of(p))
    assert [e["text"] for e in events] == ["why?", "appended", "twin"]
    st, text = overview(p)
    assert st["damaged"] == {"journal": ["journal:2"], "recovered": ["journal:2"], "files": []}
    assert st["shared_ids"] == {"e2": ["journal:2", "journal:3"]}
    assert ("damaged: 1 journal line(s) do not parse (journal:2); the record a later write appended "
            "onto 1 of them is read (journal:2)") in text
    assert "ids on several lines: e2 (journal:2, journal:3)" in text
    assert "  e2 (journal:2) finding: appended" in text and "  e2 (journal:3) finding: twin" in text
    index = built(p)["index.html"]
    assert 'id="journal-2"' in index and 'id="journal-3"' in index
    assert "Damaged: 1 journal line(s) do not parse" in index


def test_a_read_that_keeps_changing_is_said_on_both_screens(tmp_path):
    p = LegacyProject(tmp_path / "p", migrated=True)
    p.run("20261001-090000-live", status="running", pid=4242, completion=False)

    class Appending:
        def alive(self, pid):
            p.event("note")  # a writer appends while the overview reads
            return True

    platform = SimpleNamespace(name=core.PLATFORM.name, locks=core.PLATFORM.locks,
                               files=core.PLATFORM.files, runs=core.PLATFORM.runs, processes=Appending())
    project = core.Project(p.root, platform=platform)
    st = core.status(project)
    assert st["updating"] is True
    assert "· updating: the records changed while they were read (read three times)" in \
        core.status_text(st)
    workspace.build(project)
    index = plain((project.workspace_dir / "index.html").read_text(encoding="utf-8"))
    assert "· updating: the records changed while they were read</span></footer>" in index
    assert "<strong>Updating:</strong> the records changed while they were read" in index


# --- the Workspace -----------------------------------------------------------------------------------

def test_the_workspace_links_a_run_events_chart_to_that_run_and_marks_unbound_ones(tmp_path):
    """F10: a chart reference went to the latest gallery card whatever run it named."""
    case = evidence_project(tmp_path)
    pages = built(case.p)
    index = pages["index.html"]
    # The run's completion event binds its chart to that run (ADR-0006 4).
    assert f'href="runs/{case.a}.html#chart-trend" title="{case.a}/trend">chart trend</a>' in index
    # The finding left its chart unbound: the gallery card is the latest, not the cited one.
    unbound = re.search(r'<a href="index\.html#chart-[^"]+" title="latest, not the cited version">'
                        r"chart scripts/a\.py::trend</a>", index)
    assert unbound and "Version unbound</span>" in index
    assert "1 candidate version(s)" in index


def test_the_workspace_shows_producers_and_outputs_not_preserved(tmp_path):
    """F11: the dataset page called the last producer the current version."""
    case = evidence_project(tmp_path)
    pages = built(case.p)
    assert "current version" not in pages["data/t.html"].lower()
    assert "same bytes as 3 runs:" in pages["data/t.html"]
    assert "same bytes as now" in pages["data/t.html"]
    assert "Producer ambiguous" in pages["index.html"]
    assert "No matching run output" in pages["index.html"]
    run_page = pages[f"runs/{case.overwritten}.html"]
    assert "Overwritten since this run — this run's bytes are not preserved" in run_page
    consumer = pages[f"runs/{case.c}.html"]
    assert "<th>Producer</th>" in consumer and "Producer ambiguous" in consumer
    assert f'runs/{case.a}.html' in consumer and f'runs/{case.b}.html' in consumer
    assert f'runs/{case.late}.html' not in consumer.split('id="inputs"', 1)[1].split("</section>", 1)[0]


def test_a_dataset_page_says_a_run_holds_its_bytes_only_when_one_does(tmp_path):
    """The dataset page said "A run named here output this path with these bytes" when the runs it
    named wrote other bytes or failed, and called every run that wrote the path one that made the
    file, a deletion included (review of 8d2ecc7)."""
    case = evidence_project(tmp_path)
    pages = built(case.p)
    claim = "A run named here output this path with these bytes"
    assert claim in pages["data/t.html"] and claim in pages["data/u.html"]
    for name in ("v", "w", "x"):  # failed run, written outside, overwritten: producer none
        assert claim not in pages[f"data/{name}.html"], name
    assert not any("the run that made this file" in page.lower() for page in pages.values())
    x = pages["data/x.html"]
    assert "<h3>Successful runs that recorded an output at this path</h3>" in x
    assert case.overwritten in x and "same bytes as now" not in x

    q = LegacyProject(tmp_path / "q", migrated=True)
    path = "data/derived/t.parquet"
    made = q.run("20261001-090000-made", started_at=at(0), ts=at(1), outputs=[(path, q.file(path, b"one"))])
    gone = q.run("20261001-091000-gone", script="scripts/g.py", started_at=at(10), ts=at(11),
                 outputs=[(path, core.sha256_file(q.root / path))])
    record = json.loads((q.root / f"runs/{gone}/run.json").read_text(encoding="utf-8"))
    record["outputs"][0]["change"] = "deleted"
    q.write_json(f"runs/{gone}/run.json", record)
    page = built(q)["data/t.html"]
    assert f'{made}</a> <span lang="">{made}</span> · same bytes as now</li>' in page
    assert f'{gone}</a> <span lang="">{gone}</span> · deleted this path</li>' in page


def test_the_timeline_says_the_state_of_a_closing_reference(tmp_path):
    """A record closed by an earlier record's refs.resolves (a forward reference the read judges
    missing, D1) showed "✓ 해결됨" (now "✓ Resolved by") while analytics_status said missing (review of 8d2ecc7). The
    lists still select as C1 says; the badge says the judgment."""
    p = LegacyProject(tmp_path / "p", migrated=True)
    asked = p.event("question", text="asked")
    closes = p.event("finding", {"resolves": asked}, text="answered")
    early = p.event("finding", {"resolves": "e4"}, text="early")
    later = p.event("question", text="later")
    st, text = overview(p)
    assert later not in {e["id"] for e in st["open"]}
    assert f"  {early} finding: early · refs: 1 missing (e4)" in text.split("\n")
    index = built(p)["index.html"]
    assert f'<a class="badge tone-good" href="#{closes}">✓ Resolved by {closes}</a>' in index
    assert (f'<a class="badge tone-critical" href="#{early}" title="the lists take this record as '
            f'closed by {early} (refs.resolves), and the read judges that reference missing">closed by '
            f"{early} · Missing: record_absent</a>") in index
    assert f"✓ Resolved by {early}" not in index


def test_a_chart_link_from_a_record_is_escaped(tmp_path):
    p = LegacyProject(tmp_path / "p", migrated=True)
    run = p.run("20261001-090000-a", started_at=at(0), ts=at(1), charts=["a&copy"])
    index = built(p)["index.html"]
    assert f'href="runs/{run}.html#chart-a&amp;copy"' in index
    assert "#chart-a&copy" not in index


def test_an_event_reference_to_an_id_on_several_lines_has_candidate_records(tmp_path):
    p = LegacyProject(tmp_path / "p", migrated=True)
    p.event("finding", text="one", seq=1)
    p.event("finding", text="two", seq=1)
    p.event("decision", {"events": ["e1"]}, seq=2, text="cites e1")
    index = built(p)["index.html"]
    assert 'title="journal:1, journal:2">2 candidate record(s)</small>' in index
    assert "candidate version(s)" not in index


def test_labels_and_paths_from_records_are_escaped(tmp_path):
    p = LegacyProject(tmp_path / "p", migrated=True)
    p.event("finding", {"charts": ['scripts/a.py::<img src=x onerror="alert(1)">'],
                        "other": ["<script>alert(2)</script>"], "events": ['e9"><b>']}, text="x")
    _, text = overview(p)
    assert '1 missing (e9"><b>)' in text  # the text is text
    index = built(p)["index.html"]
    assert "<img src=x" not in index and "<script>alert(2)" not in index and '"><b>' not in index
    assert "chart scripts/a.py::&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in index
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in index


def test_the_same_files_give_the_same_overview(tmp_path):
    """An overview made twice from unchanged files is the same text (the read's source_revision
    included), which is what an analytics_status reply is compared with (tools/usage_report.py)."""
    case = evidence_project(tmp_path)
    st, text = overview(case.p)
    assert json.loads(json.dumps(st, default=str))["source_revision"] == st["source_revision"]
    assert core.status_text(core.status(project_of(case.p))) == text


@pytest.mark.parametrize("name", sorted(p.name for p in (Path(__file__).parent / "fixtures" / "records").iterdir()
                                        if p.is_dir()))
def test_one_records_references_are_the_same_judged_alone_or_with_all(name):
    """relations.ReadModel.references(source) judges only that record before every judgment is made
    (Issue #12 PR-2, for a screen that shows a few records); the judgments are the same."""
    root = Path(__file__).parent / "fixtures" / "records" / name

    def sha(path):
        path = Path(path)
        return core.sha256_file(path) if path.is_file() else None

    whole = relations.read(root, alive=lambda pid: False, sha=sha)
    sources = {j["from"] for j in whole.references()}
    assert any(not s.startswith("report:") for s in sources)
    for source in sorted(sources):
        alone = relations.read(root, alive=lambda pid: False, sha=sha)
        assert alone.references(source) == whole.references(source), source
        if not source.startswith("report:"):  # a Report revision's are judged with all of them
            assert alone._references is None, source  # nothing else was judged


# --- paths an older kit recorded (ADR-0006 9, Issue #12 PR-3) -------------------------------------

def older_kit_screens(tmp_path, monkeypatch, *, folder: str = "", script: str = "plot") -> SimpleNamespace:
    """An older kit's runs that recorded a table and a script in another spelling and an absolute
    path, today's run with the script and a copy of the table outside the root (which the mapping
    also maps: a run of today's kit is read as recorded), and a stale input upstream.
    Project.legacy_identity is replaced by a mapping so every platform reads the same (the reader's
    tests read a real file system). `folder` puts the table in a folder of that name, `script`
    names the script."""
    p = LegacyProject(tmp_path / "p", migrated=True)
    p.file(f"scripts/{script}.py", b"# plot\n")
    derived = f"data/derived/{folder}/" if folder else "data/derived/"
    base = ("data/raw/base.parquet", p.file("data/raw/base.parquet", b"base"))
    agg = (f"{derived}agg.parquet", p.file(f"{derived}agg.parquet", b"agg"))
    # The real path an older kit recorded for a table it read (a root reached through a link, say).
    absolute = os.path.join(os.path.realpath(p.root), *agg[0].split("/"))
    # A copy outside the root that today's run read (the same bytes, so its input is not changed).
    outside = Path(os.path.realpath(tmp_path)) / "outside" / "agg.parquet"
    outside.parent.mkdir()
    outside.write_bytes(b"agg")
    maker = p.run("20261001-091000-maker", script=f"Scripts/{script.title()}.py", started_at=at(10),
                  ts=at(11), inputs=[base], outputs=[(f"{derived}AGG.parquet", agg[1])], charts=["trend"])
    consumer = p.run("20261001-092000-consumer", script="scripts/use.py", started_at=at(20), ts=at(21),
                     inputs=[(absolute, agg[1])], charts=["usage"], kit_version="0.4.0")
    today = p.run("20261001-093000-today", script=f"scripts/{script}.py", started_at=at(30), ts=at(31),
                  inputs=[(str(outside), agg[1])], charts=["trend"], kit_version=core.PATH_IDENTITY_SINCE)
    p.file(base[0], b"base changed")  # the maker's input changed since: the maker is stale
    mapping = {f"Scripts/{script.title()}.py": f"scripts/{script}.py", f"{derived}AGG.parquet": agg[0],
               absolute: agg[0], str(outside): agg[0]}
    monkeypatch.setattr(core.Project, "legacy_identity", lambda self, recorded: mapping.get(recorded))
    return SimpleNamespace(p=p, maker=maker, consumer=consumer, today=today, absolute=absolute)


def records_of(root: Path) -> dict[str, bytes]:
    """The project's files but what the screens write (the Workspace, the kit's cache and locks)."""
    def written(parts: tuple) -> bool:
        return parts[0] in ("report", "workspace") or parts[0] in (".lab", ".analytics") and (
            parts[1] == "cache" or parts[-1] == "lock" or parts[-1].endswith(".lock"))

    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and not written(path.relative_to(root).parts)}


def test_the_overview_says_which_relations_are_inferred_from_an_older_kit_s_path(tmp_path, monkeypatch):
    case = older_kit_screens(tmp_path, monkeypatch)
    before = records_of(case.p.root)
    st, text = overview(case.p)
    [agg] = [d for d in st["datasets"] if d["path"] == "data/derived/agg.parquet"]
    assert (agg["producer_state"], agg["producer_candidates"], agg["used_by"]) == \
        ("resolved", [case.maker], [case.consumer])
    assert agg["inferred"] == {"producer_candidates": [case.maker], "producers": [case.maker],
                               "used_by": [case.consumer], "written_by": [case.maker]}
    charts = {c["key"]: c for c in st["charts"]}
    assert set(charts) == {"scripts/plot.py::trend", "scripts/use.py::usage"}
    assert (charts["scripts/plot.py::trend"]["versions"], charts["scripts/plot.py::trend"]["inferred_versions"],
            charts["scripts/plot.py::trend"]["run"]) == (2, 1, case.today)
    usage = charts["scripts/use.py::usage"]
    assert (usage["stale"], usage["stale_upstream"], usage["inferred_upstream"], usage["inferred_inputs"]) == \
        (True, [case.maker], [case.maker], [case.absolute])
    assert f"same bytes as run {case.maker}'s output (inferred from an older kit's path)" in text
    assert "used by 1 run(s) (1 inferred from an older kit's path)" in text
    assert "scripts/plot.py::trend \"trend\" run " + case.today + " v2 (1 inferred from an older kit's path)" in text
    assert f"STALE(upstream candidate {case.maker} (inferred from an older kit's path))" in text
    assert "STALE(" not in text.split("scripts/plot.py::trend", 1)[1].split("\n", 1)[0]
    assert "producer inferred from an older kit's path for 1 input(s)" in text
    assert records_of(case.p.root) == before, "the overview rewrites no record"


def test_the_workspace_marks_what_it_reads_through_an_older_kit_s_path(tmp_path, monkeypatch):
    case = older_kit_screens(tmp_path, monkeypatch)
    case.p.event("finding", {"charts": ["scripts/plot.py::trend"]})
    before = records_of(case.p.root)
    pages = built(case.p)
    # The cited key's versions found through the older kit's script spelling: their count beside the
    # kit's English title, not the versions (Visualization ids people wrote) in it (#10).
    assert (f'<small class="ref-note" title="{workspace.esc(INFERRED_TITLE)}">1 inferred from an '
            "older kit's path</small>") in pages["index.html"]
    consumer = pages[f"runs/{case.consumer}.html"]
    inputs = consumer.split('id="inputs"', 1)[1].split("</section>", 1)[0]
    assert (f'<span lang="">{case.absolute}</span>' in inputs or f'>{case.absolute}</a>' in inputs)
    assert 'read as <a href="../data/agg.html"><code lang="">data/derived/agg.parquet</code></a> — ' \
           "inferred from an older kit's path" in inputs
    assert f'runs/{case.maker}.html' in inputs and "Inferred from an older kit's path" in inputs
    assert f"({case.maker}: inferred from an older kit's path)" in consumer  # the stale callout
    today = pages[f"runs/{case.today}.html"]
    assert "read as" not in today and "older kit" not in today
    dataset = pages["data/agg.html"]
    assert f'{case.consumer}</a> <span lang="">{case.consumer}</span> · <span class="ref-note"' in dataset
    assert dataset.count("inferred from an older kit's path</span>") >= 2  # producers and readers
    assert "Inferred from an older kit's path" in dataset  # the current bytes' candidate
    card = _card(pages["index.html"], "scripts/plot.py::trend")
    assert "v2" in card and "1 of the 2 version(s) of this key were recorded under another spelling" in card
    # The gallery's card lists the earlier version, recorded by the older kit, as inferred.
    gallery = pages["index.html"].split('id="charts"', 1)[1]
    earlier = _card(gallery, "scripts/plot.py::trend").split('class="versions"', 1)[1]
    assert f'runs/{case.maker}.html' in earlier and "(inferred)</small>" in earlier
    assert "Inferred from an older kit's path" in _card(pages["index.html"], "scripts/use.py::usage")
    maker = pages[f"runs/{case.maker}.html"]
    assert ('id <code lang="">scripts/plot.py::trend</code> <small class="ref-note"' in maker
            and 'recorded as <code lang="">Scripts/Plot.py::trend</code> — inferred from an older kit\'s path'
            in maker)
    assert f'index.html#chart-{workspace.slug_key("scripts/plot.py::trend")}' in maker
    assert records_of(case.p.root) == before, "the Workspace rewrites no record"


def test_records_of_today_s_kit_and_paths_already_stored_get_no_inferred_mark(tmp_path):
    """Nothing is marked inferred where no older kit's path is read as another one: the evidence
    project's runs have no kit version (an older kit's) but their paths are the stored ones."""
    case = evidence_project(tmp_path)
    st, text = overview(case.p)
    assert "older kit" not in text
    assert not any("inferred" in d for d in st["datasets"])
    assert all(not (c["inferred_versions"] or c["inferred_inputs"] or c["inferred_upstream"])
               for c in st["charts"])
    pages = built(case.p)
    assert not any("older kit" in page for page in pages.values())


def test_the_inferred_marks_keep_what_people_wrote_in_lang_empty(tmp_path, monkeypatch):
    """TERMINOLOGY 7: a path or key an older kit recorded, and the one it is read as, are what
    people and scripts wrote; every Hangul the Workspace shows for them is in an element marked
    lang="", text or attribute, and the kit's mark stays in its English."""
    from test_language import HANGUL, LangScopes

    case = older_kit_screens(tmp_path, monkeypatch, folder="요약", script="그림")
    pages = built(case.p)
    assert "Inferred from an older kit's path" in pages[f"runs/{case.consumer}.html"]
    assert 'recorded as <code lang="">Scripts/그림.py::trend</code>' in pages[f"runs/{case.maker}.html"]
    for name, page in pages.items():
        scopes = LangScopes(page)
        misplaced = [(lang, tag, text.strip()[:80]) for lang, tag, text in scopes.texts
                     if tag != "title" and HANGUL.search(text) and lang != ""]
        misplaced += [(lang, f"{tag}@{attr}", value[:80]) for lang, tag, attr, value in scopes.attrs
                      if HANGUL.search(value) and lang != ""]
        assert misplaced == [], name


def v2_event(p: LegacyProject, kind: str, text: str, refs=(), details=None) -> str:
    """Append an analytics.event/2 record with the next seq, as the new writer will (ADR-0006 1.2)."""
    p.seq += 1
    p.minute += 1
    value = {"schema": "analytics.event/2", "seq": p.seq, "id": f"e{p.seq}", "recorded_at": at(p.minute % 60),
             "kind": kind, "actor": "human", "actor_basis": "declared", "means": "cli", "text": text,
             "refs": list(refs)}
    if details is not None:
        value["details"] = details
    p.line(value)
    return f"e{p.seq}"


def event_ref(event_id: str, relation: str = "cites") -> dict:
    return {"type": "event", "event_id": event_id, "relation": relation, "basis": "declared",
            "version_binding": "author"}


def test_v2_records_are_listed_with_the_v1_ones_by_their_replayed_state(tmp_path):
    """design 5.2 (the state map's P6): analytics.event/2 records are in the lists, the context block
    and the Workspace with the /1 records, in seq order. Their key information and open items follow
    the state the read model replays (ADR-0006 6): a /2 finding withdrawn by a record_update is not
    key information, a /2 question a /2 resolve closed is not open, and a /1 question a /2 record
    resolves is closed too. workflow_feedback is Workflow feedback, as `gap` is."""
    p = LegacyProject(tmp_path / "p", migrated=True)
    old_question = p.event("question", text="old question")
    kept = v2_event(p, "finding", "kept finding")
    withdrawn = v2_event(p, "finding", "withdrawn finding")
    v2_event(p, "record_update", "withdraw it", [{**event_ref(withdrawn, "updates")}],
             {"expected_state": "current", "new_state": "withdrawn"})
    open_question = v2_event(p, "question", "open v2 question")
    closed = v2_event(p, "question", "closed v2 question")
    v2_event(p, "resolve", "closing both", [event_ref(closed, "resolves")])
    v2_event(p, "resolve", "closing the old one", [event_ref(old_question, "resolves")])
    v2_event(p, "workflow_feedback", "the tools got in the way")
    v2_event(p, "metric_definition", "revenue per order", details={"metric_id": "rpo"})
    st, text = overview(p)
    assert [e["id"] for e in st["key"]] == [kept]
    assert [e["id"] for e in st["open"]] == [open_question]
    assert st["counts"]["gaps"] == 1 and st["counts"]["events"] == 10
    assert [e["kind"] for e in st["recent"]][-2:] == ["workflow_feedback", "metric_definition"]
    assert f"  {kept} finding: kept finding" in text and "withdrawn finding" not in text.split("recent journal:")[0]
    block = core.context_block(project_of(p))
    assert f"- {kept} finding: kept finding" in block and "withdrawn finding" not in block
    assert f"- {open_question} question: open v2 question" in block
    assert "closed v2 question" not in block and "old question" not in block
    index = built(p)["index.html"]
    assert "the tools got in the way" in index and "Workflow feedback" in index
    assert "Metric definition" in index and "revenue per order" in index


def test_the_context_history_of_both_state_directories_is_shown(tmp_path):
    """design 5.3 (review of PR 1): the CONTEXT.md versions a reader shows are those of
    .analytics/context/ (the new format's) and of .lab/context/, as a context reference resolves
    them: status says when the context last changed, and the Workspace's history lists every version
    with the anchor the Timeline links to. A name in both directories is the .analytics one."""
    p = LegacyProject(tmp_path / "p", migrated=True)
    old = "20261001-090000-000000_aaaaaaaaaaaa.md"
    new = "20261008-090000-000000_0123456789ab.md"
    p.file(f".lab/context/{old}", b"# Goal\nthe old context\n")
    p.file(f".analytics/context/{new}", b"# Goal\nRecorded context\n")
    p.file(f".lab/context/{new}", b"# Goal\nnot the one a reference resolves to\n")
    v2_event(p, "context", "Context saved", [{"type": "context_version", "version": new,
                                              "relation": "about", "basis": "declared",
                                              "version_binding": "author"}])
    project = project_of(p)
    assert [v.relative_to(p.root).as_posix() for v in core.context_versions(project)] == [
        f".lab/context/{old}", f".analytics/context/{new}"]
    st, _ = overview(p)
    assert st["project"]["context_updated"] == "2026-10-08T09:00:00"
    model = core.read_model(project)
    assert {r["state"] for r in model.references()} == {"resolved"}
    pages = built(p)
    context = pages["context.html"]
    assert "2 version(s)" in context and f'id="v-{new[:-3]}"' in context
    assert "Recorded context" in context and "not the one a reference resolves to" not in context
    assert f"context.html#v-{new[:-3]}" in pages["index.html"]
    assert "2 recorded version(s)" in pages["index.html"]
    # The kit writes its own versions to .analytics/context/ only (design 4.8).
    p.file("CONTEXT.md", b"# Goal\nnow\n")
    core.sync_context(project, origin=core.Origin("test", "cli"))
    assert len(list((p.root / ".lab" / "context").glob("*.md"))) == 2
    written = sorted(x.name for x in (p.root / ".analytics" / "context").iterdir())
    assert len(written) == 2 and written[0] == new and written[1].endswith(
        f"_{core.sha256_bytes(b'# Goal' + bytes([10]) + b'now' + bytes([10]))[:12]}.md")
