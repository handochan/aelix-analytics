"""Reconciliation (experiment: Issue #196, branch exp/session-substrate, not adopted): core.reconcile —
the changes in a project's files since the last look that no record says, with the tool call each may
be from. The extension's side (when it looks, what it tells the model) is in tests/test_extension.py."""

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest
from conftest import KIT, kit_cli

sys.path.insert(0, str(KIT))
from aelix_analytics import core  # noqa: E402

calls, reconcile = core.calls, core.reconcile


@pytest.fixture
def project(tmp_path):
    made = core.init_project(tmp_path / "proj", title="대조", template_dir=KIT / "aelix_analytics" / "template",
                             origin=core.Origin("test", "sdk"))
    (made.root / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (made.root / "scripts").mkdir(exist_ok=True)
    return made


def stamp(seconds: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def called(project, call, tool, *, started=-1.0, ended=1.0, **more):
    line = calls.entry(call=call, session="s1", tool=tool, started=stamp(started), ended=stamp(ended), ms=1,
                       error=False, args={}, result=None, **more)
    calls.append(project, line)
    return line


def look(project):
    return reconcile.look(project, session="s1", at=stamp())


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_the_first_look_tells_nothing_and_a_later_one_tells_what_changed(project):
    root = project.root
    write(root / "data" / "raw" / "old.csv", "a\n1\n")
    assert look(project) is None and (project.cache / reconcile.SNAPSHOT).is_file()
    assert look(project) is None and not (project.state_dir / reconcile.FILE).exists()
    write(root / "data" / "raw" / "new.csv", "a\n2\n")
    os.remove(root / "data" / "raw" / "old.csv")
    line = look(project)
    assert line["schema"] == reconcile.SCHEMA and line["total"] == 2 and line["records_read"] is True
    created, deleted = line["changes"]
    assert created == {"path": "data/raw/new.csv", "change": "created", "bytes": 4,
                       "sha256": calls.sha256(b"a\n2\n")}
    assert deleted == {"path": "data/raw/old.csv", "change": "deleted"}
    assert reconcile.said(line) == (
        "aelix-analytics: files changed since the last look that no run, import or observation recorded: "
        "data/raw/new.csv (created, outside any tool call seen); data/raw/old.csv (deleted, outside any tool "
        "call seen). Record data with analytics_import or analytics_run.")
    # Told once: the next look starts from this one.
    assert look(project) is None
    assert [entry["total"] for entry in reconcile.read(project)["looks"]] == [2]
    # The project's own files are not changes: the journal, CONTEXT.md.
    write(root / "CONTEXT.md", "# changed\n")
    assert look(project) is None


def test_a_change_is_told_with_the_call_it_may_be_from(project):
    root = project.root
    assert look(project) is None
    called(project, "w1", "write", file={"wrote": "data/raw/b.csv"})
    called(project, "x1", "warehouse__export")
    called(project, "r1", "read", file={"read": "data/raw/in.csv"})
    called(project, "old", "warehouse__export", started=-7200, ended=-7100)
    write(root / "data" / "raw" / "b.csv", "x\n1\n")
    write(root / "data" / "raw" / "export.csv", "KR,41\n")
    write(root / "scripts" / "c.py", "print(1)\n")  # scripts/ is not looked at, as in an observation
    line = look(project)
    by_path = {item["path"]: item.get("from") for item in line["changes"]}
    assert set(by_path) == {"data/raw/b.csv", "data/raw/export.csv"}
    assert by_path["data/raw/b.csv"] == {"call": "w1", "tool": "write", "how": "named"}
    # The calls whose window holds the file's time, newest first; a tool that writes nothing is none,
    # nor is a call that ended before the last look.
    assert by_path["data/raw/export.csv"] == {"how": "window", "candidates": [
        {"call": "x1", "tool": "warehouse__export", "source": "other"},
        {"call": "w1", "tool": "write", "source": "host"}]}
    said = reconcile.said(line)
    assert "data/raw/export.csv (created, while warehouse__export, write ran)" in said
    assert "b.csv" not in said  # the agent's own write is logged and not told
    only_named = {**line, "changes": [line["changes"][0]], "total": 1}
    assert only_named["changes"][0]["path"] == "data/raw/b.csv" and reconcile.said(only_named) == ""
    many = {**line, "changes": [{"path": f"data/raw/f{n}.csv", "change": "created"} for n in range(7)], "total": 9}
    assert reconcile.said(many).count("outside any tool call seen") == 5 and "; and 4 more (`analytics changes`)" in reconcile.said(many)
    text = reconcile.text_of(reconcile.read(project))
    assert "  data/raw/b.csv: created, by write w1" in text and "while warehouse__export x1, write w1 ran" in text


def test_what_a_record_says_is_not_told(project, tmp_path):
    """A file an import made (its parquet and its meta file) is in the journal; so is a change an
    observed activity lists."""
    assert look(project) is None
    source = write(tmp_path / "sales.csv", "region,total\nKR,41\n")
    code, out = kit_cli(project.root, "import", str(source), "--name", "sales")
    assert code == 0 and out["ok"], out
    assert look(project) is None
    outputs, deleted, seen = reconcile._recorded(project, [
        {"kind": "observed_activity", "details": {"changes": {"items": [{"path": "data/x.csv"}, {"nope": 1}]}}},
        {"kind": "note", "details": None}])
    assert seen == {"data/x.csv"} and outputs == {} and deleted == {}


def test_a_look_its_bounds_stop_tells_nothing_and_keeps_the_last_state(project, monkeypatch):
    root = project.root
    assert look(project) is None
    before = (project.cache / reconcile.SNAPSHOT).read_bytes()
    write(root / "data" / "raw" / "new.csv", "a\n")
    monkeypatch.setattr(reconcile, "ENTRIES", 0)
    assert look(project) is None and (project.cache / reconcile.SNAPSHOT).read_bytes() == before
    monkeypatch.undo()
    assert look(project)["changes"][0]["path"] == "data/raw/new.csv"
    # A file past the bytes one look hashes is told without a hash.
    monkeypatch.setattr(reconcile, "HASH_BYTES", 1)
    write(root / "data" / "raw" / "big.csv", "a,b\n1,2\n")
    assert "sha256" not in look(project)["changes"][0]


def test_the_command_line_looks_and_reads(project):
    root = project.root
    code, out = kit_cli(root, "changes", "--look")
    assert code == 0 and out["looks"] == [] and out["looked"] is None
    write(root / "data" / "raw" / "new.csv", "a\n")
    time.sleep(0.01)
    code, out = kit_cli(root, "changes", "--look")
    assert code == 0 and out["looked"]["total"] == 1 and len(out["looks"]) == 1
    before = (project.state_dir / reconcile.FILE).read_bytes()
    code, out = kit_cli(root, "changes")
    assert code == 0 and "looked" not in out and len(out["looks"]) == 1
    assert (project.state_dir / reconcile.FILE).read_bytes() == before
    assert json.loads(before.decode().splitlines()[0])["changes"][0]["path"] == "data/raw/new.csv"
