"""The call ledger's kit side (experiment: Issue #196, branch exp/session-substrate, not adopted):
core.calls — what a ledger line keeps of a call, and how the ledger is read. The extension's side
(which calls get a line, in aelix's hooks) is in tests/test_extension.py."""

import dataclasses
import json
import sys
from types import SimpleNamespace

import pytest
from conftest import KIT, kit_cli

sys.path.insert(0, str(KIT))
from aelix_analytics import core  # noqa: E402

calls = core.calls
MARKER = "row-value-7c1f"


@pytest.fixture
def project(tmp_path):
    return core.init_project(tmp_path / "proj", title="원장", template_dir=KIT / "aelix_analytics" / "template",
                             origin=core.Origin("test", "sdk"))


def line(n, *, tool="warehouse__query", session="s1", **more):
    return calls.entry(call=f"c{n}", session=session, tool=tool, started=f"2026-11-17T00:00:{n:02d}.000+00:00",
                       ended=f"2026-11-17T00:00:{n:02d}.500+00:00", ms=500, error=False, args={"n": n},
                       result=calls.digest_result([{"type": "text", "text": f"r{n}"}])[0], **more)


def test_whose_tool_a_call_is_is_told_from_its_name():
    assert calls.source_of("analytics_run") == "kit"
    assert [calls.source_of(name) for name in sorted(calls.HOST_TOOLS)] == ["host"] * 7
    # Another extension's tool and an MCP server's (with or without a server prefix) are not told apart.
    assert calls.source_of("warehouse__query") == calls.source_of("fetch") == "other"


def test_arguments_are_kept_as_a_hash_their_size_and_their_names():
    one = calls.digest_args({"sql": f"select '{MARKER}'", "limit": 10})
    same = calls.digest_args({"limit": 10, "sql": f"select '{MARKER}'"})
    other = calls.digest_args({"sql": f"select '{MARKER}'", "limit": 11})
    assert one == same and one["sha256"] != other["sha256"]
    assert one["keys"] == ["limit", "sql"] and one["bytes"] > 0
    assert MARKER not in json.dumps(one)
    assert calls.digest_args(None) == calls.digest_args({})
    many = calls.digest_args({f"k{n:03d}": n for n in range(100)})
    assert len(many["keys"]) == calls.KEYS


def test_a_result_is_kept_as_the_hash_of_its_blocks_in_order_and_its_sizes():
    text = SimpleNamespace(text=f"a,b\n1,{MARKER}\n")
    image = SimpleNamespace(data="aGVsbG8=", mime_type="image/png")
    kept, raw = calls.digest_result([text, image], {"rows": 1})
    assert kept["blocks"] == 2 and kept["images"] == 1 and kept["image_bytes"] == 8
    assert kept["text_bytes"] == len(text.text.encode())
    assert kept["sha256"] == calls.sha256(raw) and kept["details"]["sha256"] == calls.sha256(b'{"rows":1}')
    assert MARKER not in json.dumps(kept) and MARKER.encode() in raw
    # The same blocks as the host's objects or as plain dicts, and nothing else, give the same hash.
    as_dicts = [{"type": "text", "text": text.text}, {"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"}]
    assert calls.digest_result(as_dicts, {"rows": 1})[0] == kept
    assert calls.digest_result([image, text])[0]["sha256"] != kept["sha256"]
    assert calls.digest_result([text])[0]["sha256"] != kept["sha256"]
    assert "details" not in calls.digest_result([text])[0]
    assert calls.digest_result(None)[0]["blocks"] == 0


def test_details_json_has_no_form_for_are_hashed_by_their_fields_and_never_by_an_address():
    @dataclasses.dataclass
    class Details:
        exit_code: int
        seen: frozenset

    class Opaque:
        pass

    one = calls.digest_result([], Details(0, frozenset({"b", "a"})))[0]["details"]
    assert one == calls.digest_result([], Details(0, frozenset({"a", "b"})))[0]["details"]
    assert one["sha256"] == calls.sha256(b'{"exit_code":0,"seen":["a","b"]}')
    assert calls.digest_result([], {"x": Opaque()})[0]["details"] == calls.digest_result([], {"x": Opaque()})[0]["details"]
    cycle = {}
    cycle["self"] = cycle
    assert calls.digest_result([], cycle)[0]["details"] == {"unreadable": True}
    assert calls.digest_args({1: "a", "b": 2})["unreadable"] is True


def test_a_result_past_the_bound_is_counted_and_not_hashed(monkeypatch):
    monkeypatch.setattr(calls, "HASHED_BYTES", 8)
    kept, raw = calls.digest_result([{"type": "text", "text": "123456789"}], {"long": "x" * 20})
    assert raw is None and kept["too_large"] is True and "sha256" not in kept and kept["text_bytes"] == 9
    assert kept["details"]["too_large"] is True and "sha256" not in kept["details"]
    assert "sha256" in calls.digest_result([{"type": "text", "text": "12345678"}])[0]


def test_a_host_file_tool_names_the_project_file_and_no_path_outside_it(project, tmp_path):
    root = project.root
    assert calls.file_of(project, "write", {"path": str(root / "scripts" / "a.py")}) == {"wrote": "scripts/a.py"}
    assert calls.file_of(project, "edit", {"path": str(root / "CONTEXT.md")}) == {"wrote": "CONTEXT.md"}
    assert calls.file_of(project, "read", {"path": str(root / "data" / "raw" / "x.csv")}) == {"read": "data/raw/x.csv"}
    # A relative path is the host's: below its working directory, which need not be the project.
    assert calls.file_of(project, "write", {"path": "proj/scripts/a.py"}, tmp_path) == {"wrote": "scripts/a.py"}
    assert calls.file_of(project, "write", {"path": "scripts/a.py"}, root) == {"wrote": "scripts/a.py"}
    assert calls.file_of(project, "write", {"path": "scripts/a.py"}, tmp_path) is None
    assert calls.file_of(project, "write", {"path": str(tmp_path / "elsewhere.py")}) is None
    assert calls.file_of(project, "bash", {"path": str(root / "scripts" / "a.py")}) is None
    assert calls.file_of(project, "write", {}) is None and calls.file_of(project, "write", None) is None


def test_a_line_names_the_calls_that_ran_with_it_the_file_and_the_observation():
    plain = line(1)
    assert plain["schema"] == calls.SCHEMA and plain["source"] == "other"
    assert not {"overlaps", "file", "observed"} & set(plain)
    full = line(2, tool="bash", overlaps={"c9", "c3"}, observed="e7")
    assert full["overlaps"] == ["c3", "c9"] and full["observed"] == "e7" and full["source"] == "host"
    assert line(3, tool="write", file={"wrote": "scripts/a.py"})["file"] == {"wrote": "scripts/a.py"}
    crowd = line(4, overlaps={f"x{n:02d}" for n in range(calls.OVERLAPS + 5)})
    assert len(crowd["overlaps"]) == calls.OVERLAPS and crowd["overlaps_more"] == 5
    # A call no result was seen of (blocked before it ran, cancelled).
    assert calls.entry(call="c5", session=None, tool="bash", started=None, ended="t", ms=None, error=True,
                       args={})["result"] is None


def test_the_ledger_is_read_from_its_end_with_filters_and_counts(project, monkeypatch):
    assert calls.read(project) == {"calls": [], "more": False, "summary": calls.summary([])}
    calls.append(project, line(1))
    calls.append(project, line(2, tool="bash", session="s2", overlaps={"c3"}, observed="e4"))
    calls.append(project, line(3, tool="write", file={"wrote": "scripts/a.py"}))
    calls.append(project, line(4, tool="read", file={"read": "data/raw/x.csv"}))
    calls.append(project, {**line(5, tool="analytics_record"), "error": True})
    with open(calls.path_of(project), "a", encoding="utf-8") as fh:
        fh.write("not json\n" + json.dumps({"schema": "another/1", "call": "zz"}) + "\n")
    found = calls.read(project)
    assert [c["call"] for c in found["calls"]] == ["c1", "c2", "c3", "c4", "c5"] and not found["more"]
    assert found["summary"] == {
        "calls": 5, "by_source": {"kit": 1, "host": 3, "other": 1},
        "by_tool": {"analytics_record": 1, "bash": 1, "read": 1, "warehouse__query": 1, "write": 1},
        "errors": 1, "overlapping": 1, "files_read": 1, "files_written": 1, "observed": 1}
    assert [c["call"] for c in calls.read(project, session="s2")["calls"]] == ["c2"]
    assert [c["call"] for c in calls.read(project, tool="write")["calls"]] == ["c3"]
    assert [c["call"] for c in calls.read(project, source="host")["calls"]] == ["c2", "c3", "c4"]
    assert [c["call"] for c in calls.read(project, since="2026-11-17T00:00:04")["calls"]] == ["c4", "c5"]
    last = calls.read(project, limit=2)
    assert [c["call"] for c in last["calls"]] == ["c4", "c5"] and last["more"]
    text = calls.text_of(found)
    assert "wrote scripts/a.py" in text and "observed e4" in text and "with 1 other(s)" in text
    assert text.splitlines()[-1].startswith("5 call(s): kit 1, host 3, other 1; 1 error(s)")
    # Only the file's end is read: the line the cut falls in is left out, and the answer says so.
    monkeypatch.setattr(calls, "TAIL_BYTES", len(json.dumps(line(5), ensure_ascii=False)) + 150)
    tail = calls.read(project)
    assert tail["more"] and tail["calls"] and tail["calls"][-1]["call"] == "c5" and len(tail["calls"]) < 5


def test_the_command_line_reads_the_ledger(project):
    calls.append(project, line(1))
    calls.append(project, line(2, tool="bash"))
    code, out = kit_cli(project.root, "calls", "--source", "host")
    assert code == 0 and out["ok"] and [c["call"] for c in out["calls"]] == ["c2"]
    assert out["summary"]["by_source"] == {"host": 1} and out["more"] is False
    # Reading it writes nothing: the ledger keeps its bytes.
    before = calls.path_of(project).read_bytes()
    kit_cli(project.root, "calls")
    assert calls.path_of(project).read_bytes() == before
