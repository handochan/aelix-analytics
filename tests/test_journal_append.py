"""The /1 journal writer after a line an interrupted write cut off (Issue #49, ADR-0006 6).

Every journal write of the kit (core._append_event_locked: notes, imports, runs, context versions,
init) takes its seq from relations.Journal.next_seq — one above the highest seq of every parsed and
recovered line, unsupported ones included, skipping a number whose id a hand-edited readable line
already holds — and writes an LF first when the journal's last byte is not one. The bytes already in the journal are never rewritten: a cut-off line stays as it is and the
reader reports it as damaged. A seq left in a cut-off line is not counted (that write never returned
an id), so the reader reads every id once. CRLF lines stay CRLF and new records are LF (ADR-0007).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import kit_env

from aelix_analytics import core, relations

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "records"

# Ends of a journal an interrupted write left: the record had begun, its line end was never written.
CUT_OFF = {
    # what the writer had numbered (e3) is left readable as text
    "seq_left_readable": b'{"schema": "lab.event/1", "seq": 3, "id": "e3", "ts": "2026-10-08T09:00:00.000+09:00", '
                         b'"kind": "finding", "actor": "agent", "text": "\xec\xa4\x91\xeb\x8b\xa8',
    # cut before the seq
    "seq_not_written": b'{"sche',
    # cut inside a UTF-8 character
    "inside_a_character": b'{"schema": "lab.event/1", "seq": 3, "id": "e3", "text": "\xec\xa4',
}


def _project(tmp_path) -> core.Project:
    """A new project: its journal holds e1 (project) and e2 (context) and ends in LF."""
    return core.init_project(tmp_path / "p", title="journal", goal="g", origin=core.Origin("test", "sdk"))


def _line(event: dict) -> bytes:
    """The bytes the writer gives a record: its JSON and an LF."""
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


def _append(project: core.Project, data: bytes) -> bytes:
    with open(project.journal, "ab") as fh:
        fh.write(data)
    return project.journal.read_bytes()


@pytest.mark.parametrize("cut", CUT_OFF.values(), ids=CUT_OFF)
def test_a_record_after_a_cut_off_line_is_a_line_of_its_own_and_takes_a_new_seq(tmp_path, cut):
    p = _project(tmp_path)
    before = _append(p, cut)
    first = core.add_note(p, "finding", "first after the cut", origin=core.Origin("test", "sdk"))
    second = core.add_note(p, "question", "second after the cut", origin=core.Origin("test", "sdk"))
    # The cut-off bytes are kept as they are and closed by one LF; each record is a line of its own.
    assert p.journal.read_bytes() == before + b"\n" + _line(first) + _line(second)
    # A seq left in the cut-off line is not counted: that write never returned an id, and the reader
    # reads none from that line (ADR-0006 6). The next record does not take the seq of the one before.
    assert (first["id"], second["id"]) == ("e3", "e4")
    journal = relations.read_journal(p.root)
    assert journal.summary() == {"lines": 5, "damaged": [3], "recovered": [], "next_seq": 5}
    assert {e.id: e.line for e in journal.events} == {"e1": 1, "e2": 2, "e3": 4, "e4": 5}
    assert [e["text"] for e in core.read_events(p)][-2:] == ["first after the cut", "second after the cut"]
    st = core.status(p)
    assert st["damaged"]["journal"] == ["journal:3"] and st["damaged"]["recovered"] == []
    assert st["shared_ids"] == {}
    assert "1 journal line(s) do not parse" in core.status_text(st)


def test_a_whole_record_left_without_its_line_end_stays_readable(tmp_path):
    p = _project(tmp_path)
    kept = {"schema": "lab.event/1", "seq": 3, "id": "e3", "ts": "2026-10-08T09:00:00.000+09:00",
            "kind": "note", "actor": "human", "text": "left without its line end", "refs": {}}
    before = _append(p, _line(kept)[:-1])
    event = core.add_note(p, "note", "after it", origin=core.Origin("test", "sdk"))
    assert event["id"] == "e4"
    assert p.journal.read_bytes() == before + b"\n" + _line(event)
    journal = relations.read_journal(p.root)
    assert journal.damaged == [] and [e.id for e in journal.events] == ["e1", "e2", "e3", "e4"]
    assert journal.events[2].value == kept


@pytest.mark.parametrize("end", ["crlf", "cr_without_its_lf", "cut_off"])
def test_crlf_lines_keep_their_bytes_and_a_new_record_is_lf(tmp_path, end):
    """ADR-0007 decision 6: CRLF records are read and never rewritten; what the kit writes is LF.
    An LF written after a lone CR completes that line's CRLF."""
    p = _project(tmp_path)
    crlf = p.journal.read_bytes().replace(b"\n", b"\r\n")
    if end == "cr_without_its_lf":
        crlf = crlf[:-1]
    elif end == "cut_off":
        crlf += b'{"schema": "lab.event/1", "seq": 3, "id": "e3", "te'
    p.journal.write_bytes(crlf)
    event = core.add_note(p, "note", "after the CRLF lines", origin=core.Origin("test", "sdk"))
    closing = b"" if end == "crlf" else b"\n"
    assert p.journal.read_bytes() == crlf + closing + _line(event)
    assert event["id"] == "e3"
    journal = relations.read_journal(p.root)
    assert journal.damaged == ([3] if end == "cut_off" else [])
    assert [(e.id, e.line) for e in journal.events] == [("e1", 1), ("e2", 2),
                                                         ("e3", 4 if end == "cut_off" else 3)]


@pytest.mark.parametrize("state", ["empty", "missing"])
def test_the_first_record_of_an_empty_or_missing_journal_is_e1_without_a_leading_lf(tmp_path, state):
    p = _project(tmp_path)
    if state == "empty":
        p.journal.write_bytes(b"")
    else:
        p.journal.unlink()
    event = core.add_note(p, "note", "first", origin=core.Origin("test", "sdk"))
    assert (event["seq"], event["id"]) == (1, "e1")
    assert p.journal.read_bytes() == _line(event)


def test_a_journal_that_ends_in_lf_gets_the_record_and_its_lf_and_nothing_else(tmp_path):
    """The bytes every write path appends to a journal that ends in LF: the same as before #49."""
    p = _project(tmp_path)
    before = p.journal.read_bytes()
    assert before.endswith(b"\n") and [e["id"] for e in core.read_events(p)] == ["e1", "e2"]
    note = core.add_note(p, "decision", "결정: 지역별로 비교한다", refs=["e1"], origin=core.Origin("human", "sdk", session="/s/session.jsonl"))
    imported = core.append_event(p, "import", "imported sales", refs={"data": ["data/raw/sales.parquet"]},
                                 origin=core.Origin("test", "sdk"))
    core.write_text(p.context, p.context.read_text(encoding="utf-8") + "\n- more\n")
    context = core.sync_context(p, origin=core.Origin("test", "sdk"))
    assert [e["id"] for e in (note, imported, context)] == ["e3", "e4", "e5"]
    assert p.journal.read_bytes() == before + _line(note) + _line(imported) + _line(context)


# Which line holds the highest seq: every kind of line a reader takes a seq from (ADR-0006 6).
HIGHEST = ["recovered", "analytics_event_2", "unsupported_family", "an_earlier_line"]


@pytest.mark.parametrize("highest", HIGHEST)
def test_the_next_seq_is_one_above_the_highest_seq_any_readable_line_holds(tmp_path, highest):
    p = _project(tmp_path)  # e1, e2

    def seq(kind: str, low: int) -> int:
        return 20 if kind == highest else low

    def event(n: int, **extra) -> dict:
        return {"schema": "lab.event/1", "seq": n, "id": f"e{n}", "ts": "2026-10-08T09:00:00.000+09:00",
                "kind": "note", "actor": "human", "text": f"line with seq {n}", "refs": {}, **extra}

    v2, unsupported = seq("analytics_event_2", 4), seq("unsupported_family", 5)
    _append(p, b"".join([
        # a record the /1 writer before #49 appended onto a cut-off line: recovered, the line damaged
        b'{"schema": "lab.event/1", "seq": 3, "te' + _line(event(seq("recovered", 3))),
        _line({"schema": "analytics.event/2", "seq": v2, "id": f"e{v2}",
               "recorded_at": "2026-10-08T09:00:00.000+09:00", "kind": "note", "actor": "human",
               "text": "a /2 line in the same seq space", "refs": []}),
        _line({"schema": "vendor.custom/1", "seq": unsupported, "id": f"e{unsupported}"}),
        _line(event(seq("an_earlier_line", 6))),
        _line(event(7)),
        _line({"schema": "vendor.custom/1"}),  # the last line holds no seq
    ]))
    journal = relations.read_journal(p.root)
    assert journal.damaged == [3] and journal.recovered == [3]
    event_ = core.add_note(p, "note", "next", origin=core.Origin("test", "sdk"))
    assert event_["id"] == "e21"
    assert relations.read_journal(p.root).lines_of["e21"] == [9]


# Hand-edited last lines the reader reads with their id but whose seq it does not count as that id's
# number: a seq that is not an integer, or an id ahead of its seq. (seq, id, the next record's id)
HAND_EDITED = {
    "seq_string": ("3", "e3", "e4"),
    "seq_float": (3.0, "e3", "e4"),
    "seq_bool": (True, "e3", "e4"),
    "id_ahead_of_seq": (3, "e4", "e5"),
}


@pytest.mark.parametrize("seq, id_, next_id", HAND_EDITED.values(), ids=HAND_EDITED)
def test_a_number_whose_id_a_readable_line_holds_is_skipped(tmp_path, seq, id_, next_id):
    """The kit before #49 numbered from the last line's seq through int() and gave "3" or 3.0 the
    next id e4. relations.Journal counts integer seqs only, so its next seq alone would give e3
    again and the reader would read e3 on two lines: the writer skips every id a readable line holds."""
    p = _project(tmp_path)  # e1, e2
    _append(p, _line({"schema": "lab.event/1", "seq": seq, "id": id_, "ts": "2026-10-08T09:00:00.000+09:00",
                      "kind": "note", "actor": "human", "text": "hand-edited", "refs": {}}))
    first = core.add_note(p, "note", "after the hand-edited line", origin=core.Origin("test", "sdk"))
    second = core.add_note(p, "note", "and the next", origin=core.Origin("test", "sdk"))
    assert first["id"] == next_id and second["seq"] == first["seq"] + 1
    journal = relations.read_journal(p.root)
    assert journal.damaged == [] and all(len(lines) == 1 for lines in journal.lines_of.values())
    assert journal.lines_of[first["id"]] == [4] and journal.lines_of[second["id"]] == [5]
    assert core.status(p)["shared_ids"] == {}


def test_a_line_nested_deeper_than_the_parser_goes_is_damaged_and_the_next_record_is_written(tmp_path):
    """Every append reads the journal (#49), so a line json cannot parse for its depth must not stop
    it: the reader reports it as damaged like a cut-off line and the record after it is written."""
    p = _project(tmp_path)  # e1, e2
    deep = b"[" * 200_000 + b"]" * 200_000 + b"\n"
    before = _append(p, deep)
    record = core.add_note(p, "note", "after the deep line", origin=core.Origin("test", "sdk"))
    assert record["id"] == "e3" and p.journal.read_bytes() == before + _line(record)
    journal = relations.read_journal(p.root)
    assert journal.damaged == [3] and journal.lines_of["e3"] == [4]
    assert core.status(p)["damaged"]["journal"] == ["journal:3"]


@pytest.mark.parametrize("case, seq, line", [("interrupted", 6, 7), ("mixed-version", 10, 11)])
def test_the_record_examples_journals_take_the_next_record_as_adr_0006_says(tmp_path, case, seq, line):
    """`interrupted`'s event_append: "the writer ends the cut-off line 6 with a newline first; the
    new event is e6 on line 7". `mixed-version`: CRLF /1 lines, a recovered line and a seq given
    twice by the /1 writer before #49, then /2 lines — one seq space, the next seq after the highest."""
    p = _project(tmp_path)
    data = (FIXTURES / case / "journal.jsonl").read_bytes()
    p.journal.write_bytes(data)
    event = core.add_note(p, "note", "appended by the /1 writer", origin=core.Origin("test", "sdk"))
    assert event["id"] == f"e{seq}"
    after = p.journal.read_bytes()
    assert after == data + (b"" if data.endswith(b"\n") else b"\n") + _line(event)
    journal = relations.read_journal(p.root)
    assert journal.lines_of[event["id"]] == [line]
    assert journal.damaged == relations.Journal(data).damaged


APPEND = """
import sys
from aelix_analytics import core
project = core.Project(sys.argv[1])
for i in range(int(sys.argv[3])):
    core.add_note(project, "note", f"{sys.argv[2]} {i}", origin=core.Origin("test", "sdk"))
"""


def test_concurrent_writers_after_a_cut_off_line_each_get_a_line_and_a_seq_of_their_own(tmp_path):
    """Four processes append ten records each under the journal lock, the journal ending in a
    cut-off line: one LF closes it, and every record is read once with its own seq."""
    p = _project(tmp_path)
    before = _append(p, b'{"schema": "lab.event/1", "seq": 3, "te')
    writers = [subprocess.Popen([sys.executable, "-c", APPEND, str(p.root), f"writer{w}", "10"],
                                env=kit_env()) for w in range(4)]
    assert [w.wait(timeout=120) for w in writers] == [0, 0, 0, 0]
    after = p.journal.read_bytes()
    assert after.startswith(before + b"\n{") and after.count(b"\n") == 43
    journal = relations.read_journal(p.root)
    assert journal.damaged == [3] and journal.recovered == []
    assert sorted(e.seq for e in journal.events) == list(range(1, 43))
    assert all(len(lines) == 1 for lines in journal.lines_of.values())
    texts = {e.value["text"] for e in journal.events}
    assert {f"writer{w} {i}" for w in range(4) for i in range(10)} <= texts
