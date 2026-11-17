"""The held results and the claim check (experiment: Issue #196, branch exp/session-substrate, not
adopted): core.calls.hold/held/keep/prune and core.claims — where the numbers a record states were
seen among the results of the session's tool calls. The extension's side is in tests/test_extension.py."""

import json
import os
import subprocess
import sys
import time

import pytest
from conftest import KIT, kit_env

sys.path.insert(0, str(KIT))
from aelix_analytics import _platform, core  # noqa: E402

calls, claims = core.calls, core.claims
SESSION = "sessions/this.jsonl"


@pytest.fixture(autouse=True)
def nothing_parsed_before():
    """The claim check keeps the numbers of the results it read, by their hash, for the process: a
    test starts with none, so what it reads from the files is what it sees."""
    claims._PARSED.clear()
    yield
    claims._PARSED.clear()


@pytest.fixture
def project(tmp_path):
    return core.init_project(tmp_path / "proj", title="대조", template_dir=KIT / "aelix_analytics" / "template",
                             origin=core.Origin("test", "sdk"))


def saw(project, n, tool, text, details=None, *, session=SESSION, hold=True, **more):
    """A call of the session whose result the ledger holds: its line and its held bytes."""
    kept, raw = calls.digest_result([{"type": "text", "text": text}], details)
    if hold:
        kept["held"] = calls.hold(project, kept["sha256"], raw)
        if details is not None:
            kept["details"]["held"] = calls.hold(project, kept["details"]["sha256"], calls.canonical(details))
    line = calls.entry(call=f"c{n}", session=session, tool=tool, started=f"2026-11-17T00:00:{n:02d}.000+00:00",
                       ended=f"2026-11-17T00:00:{n:02d}.500+00:00", ms=500, error=False, args={"n": n},
                       result=kept, **more)
    calls.append(project, line)
    return line


def event(n, kind, text):
    return {"id": f"e{n}", "kind": kind, "text": text}


def test_the_numbers_a_text_states():
    text = ("KR 매출은 41이고 전체의 38.5%다(총41건). 2026년 3월, e3과 run 20261117-010101-x, v0.32.1, "
            "1,234,567원, 3개 지역, 0.4, 5%, 2024, 2026-11-17 09:30, sales_2023, Q4, 41")
    assert [v["text"] for v in claims.values_of(text)] == ["41", "38.5%", "1,234,567", "0.4", "5%"]
    grouped = claims.values_of("1,234,567원")[0]
    assert (grouped["number"], grouped["decimals"], grouped["percent"]) == ("1234567", 0, False)
    share = claims.values_of("38.50%")[0]
    assert (share["number"], share["decimals"], share["percent"]) == ("38.50", 2, True)
    # Numbers a comma only separates are each one; a single digit alone is none.
    assert [v["text"] for v in claims.values_of("41,38 그리고 7, 8")] == ["41", "38"]
    assert claims.values_of("숫자가 없다") == [] and claims.values_of("") == []
    many = claims.values_of(" ".join(str(100 + n) for n in range(60)))
    assert len(many) == claims.VALUES


def test_the_numbers_a_result_holds():
    numbers, more = claims.numbers_of("region,total\nKR,41\nUS,12,345\nshare 38.49% v1.2")
    assert set(numbers) == {41.0, 12345.0, 12.0, 345.0, 38.49} and not more
    assert claims.numbers_of("1 2 3 4 5", limit=3) == ([1.0, 2.0, 3.0], True)
    assert claims.result_text(calls.digest_result([{"type": "text", "text": "a 1"}, {"type": "text", "text": "b 2"}])[1]) \
        == "a 1\nb 2"
    assert claims.result_text(b"not json") == "" and claims.result_text(b'{"a": 1}') == ""


def test_a_record_s_numbers_are_looked_for_in_the_session_s_results(project):
    """Seen as stated, as a rounded longer number, as a share, in a result's details — in any tool's
    result — or not seen; newest call first; another session's calls are not looked at."""
    saw(project, 1, "warehouse__query", "region,total\nKR,41\nUS,0.3849\n")
    saw(project, 2, "bash", "mean 41.69\n")
    saw(project, 3, "fetch", "ok", {"rows": 1204})
    saw(project, 4, "warehouse__query", "JP,777\n", session="sessions/other.jsonl")
    found = claims.check(project, "KR은 41, US 비중 38.5%, 평균 41.7, 합계 1,204, JP 777, 없는 값 9,999", session=SESSION)
    by_text = {value["text"]: [(s["call"], s["how"]) for s in value["seen"]] for value in found["values"]}
    assert by_text == {"41": [("c1", "exact")], "38.5%": [("c1", "share")], "41.7": [("c2", "rounded")],
                       "1,204": [("c3", "exact")], "777": [], "9,999": []}
    assert found["values"][3]["seen"][0]["in"] == "details" and "in" not in found["values"][0]["seen"][0]
    assert found["values"][0]["seen"][0]["tool"] == "warehouse__query"
    assert found["grade"] == "some_seen"
    assert found["looked"] == {"calls": 3, "results": 4, "bytes": found["looked"]["bytes"], "partial": False}
    assert claims.check(project, "KR은 41", session=SESSION)["grade"] == "all_seen"
    assert claims.check(project, "JP 777", session=SESSION)["grade"] == "none_seen"
    assert claims.check(project, "JP 777", session="sessions/other.jsonl")["grade"] == "all_seen"
    assert claims.check(project, "수치가 없는 글", session=SESSION) is None


def test_what_repeats_records_is_not_a_source_and_what_is_not_held_makes_the_look_partial(project):
    saw(project, 1, "analytics_status", "finding e3: KR 41")
    saw(project, 2, "bash", "e3 finding: KR 41", kit_command=True)
    assert claims.check(project, "KR 41", session=SESSION) == {
        "values": [{"text": "41", "seen": []}], "grade": "none_seen",
        "looked": {"calls": 2, "results": 0, "bytes": 0, "partial": False}}
    saw(project, 3, "analytics_query", "KR 41")  # the kit's query is a source
    assert claims.check(project, "KR 41", session=SESSION)["values"][0]["seen"][0]["call"] == "c3"
    saw(project, 4, "fetch", "KR 52", hold=False)
    partial = claims.check(project, "KR 52", session=SESSION)
    assert partial["grade"] == "none_seen" and partial["looked"]["partial"] is True
    assert "not every result was looked at" in claims.said({**partial, "values": partial["values"]})


def test_a_record_gets_a_line_and_the_results_its_values_were_seen_in_are_kept(project):
    first = saw(project, 1, "warehouse__query", "region,total\nKR,41\n")
    saw(project, 2, "fetch", "nothing of it")
    assert claims.link(project, event(5, "question", "41인가?"), session=SESSION) is None
    assert claims.link(project, event(6, "finding", "수치 없음"), session=SESSION) is None
    assert not (project.state_dir / claims.FILE).exists() and not (project.state_dir / calls.KEPT).exists()
    line = claims.link(project, event(7, "finding", "KR은 41이고 전체는 1,204"), session=SESSION, at="2026-11-17T00:01:00+00:00")
    assert (line["schema"], line["event"], line["kind"], line["grade"]) == (claims.SCHEMA, "e7", "finding", "some_seen")
    assert line["values"][0]["seen"][0]["kept"] is True and line["values"][1]["seen"] == []
    assert claims.said(line) == "values seen in this session's tool results: 1 of 2 (not seen: 1,204)"
    digest = first["result"]["sha256"]
    assert calls.kept_path(project, digest).read_bytes() == calls.held_path(project, digest).read_bytes()
    # Both places ignore themselves: a project made before them has no line for them.
    for place in (project.cache / calls.HELD, project.state_dir / calls.KEPT):
        assert (place / ".gitignore").read_text(encoding="utf-8").splitlines()[-1] == "*"
    # Only the result a value was seen in is kept, and it outlives the held ones.
    assert [p.name for p in (project.state_dir / calls.KEPT).rglob("*.json")] == [f"{digest}.json"]
    for path in (project.cache / calls.HELD).rglob("*.json"):
        path.unlink()
    claims._PARSED.clear()  # as a later process, which has read none of them
    again = claims.check(project, "KR은 41", session=SESSION)
    assert again["values"][0]["seen"][0]["call"] == "c1" and again["looked"]["partial"] is True
    found = claims.read(project)
    assert [c["event"] for c in found["claims"]] == ["e7"] and found["grades"] == {
        "all_seen": 0, "some_seen": 1, "none_seen": 0}
    assert claims.read(project, event="e1")["claims"] == []
    text = claims.text_of(found)
    assert "e7 finding: some_seen" in text and "  41: warehouse__query c1 (exact)" in text and "  1,204: not seen" in text


def test_results_are_held_by_their_hash_up_to_a_size_and_never_given_changed(project, monkeypatch):
    kept, raw = calls.digest_result([{"type": "text", "text": "KR,41"}])
    assert calls.held(project, kept["sha256"]) is None and calls.held_size(project, kept["sha256"]) is None
    assert calls.hold(project, kept["sha256"], raw) == "held"
    path = calls.held_path(project, kept["sha256"])
    assert path.read_bytes() == raw and calls.held(project, kept["sha256"]) == raw
    assert calls.held_size(project, kept["sha256"]) == len(raw)
    # The same bytes are not written again.
    os.utime(path, (1_000_000, 1_000_000))
    assert calls.hold(project, kept["sha256"], raw) == "held" and path.stat().st_mtime == 1_000_000
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".part")]
    # Bytes that are no longer that hash's are not a result.
    path.write_bytes(b"[]")
    assert calls.held(project, kept["sha256"]) is None and calls.keep(project, kept["sha256"]) is False
    monkeypatch.setattr(calls, "HELD_RESULT_BYTES", 4)
    big, big_raw = calls.digest_result([{"type": "text", "text": "0123456789"}])
    assert calls.hold(project, big["sha256"], big_raw) == "too_large"
    assert not calls.held_path(project, big["sha256"]).exists()


def test_held_results_are_pruned_by_age_then_by_size_and_kept_ones_stay(project, monkeypatch):
    now = time.time()
    digests = []
    for n in range(4):
        kept, raw = calls.digest_result([{"type": "text", "text": f"result {n} " + "x" * 100}])
        calls.hold(project, kept["sha256"], raw)
        digests.append(kept["sha256"])
    assert calls.keep(project, digests[0])
    old = now - calls.HELD_SECONDS - 60
    os.utime(calls.held_path(project, digests[0]), (old, old))
    for n in (1, 2, 3):
        os.utime(calls.held_path(project, digests[n]), (now - 300 + n, now - 300 + n))
    assert calls.prune(project, now=now) == {"looked": 4, "removed": 1, "partial": False,
                                             "bytes": sum(calls.held_size(project, d) for d in digests[1:])}
    assert not calls.held_path(project, digests[0]).exists() and calls.held(project, digests[0]) is not None
    # Past the total, the oldest go first.
    size = calls.held_size(project, digests[1])
    monkeypatch.setattr(calls, "HELD_TOTAL_BYTES", size + 10)
    pruned = calls.prune(project, now=now)
    assert pruned["removed"] == 2 and pruned["bytes"] <= size + 10
    assert [calls.held_path(project, d).exists() for d in digests] == [False, False, False, True]
    assert calls.kept_path(project, digests[0]).exists()
    assert calls.prune(project, now=now)["removed"] == 0


def test_a_record_from_the_command_line_in_a_session_gets_its_claim_check(project):
    """The extension gives the shells its session; a record made there (`analytics record` in bash, with
    none of the extension's tools) is checked as the tool's is. Outside a session nothing is."""
    saw(project, 1, "warehouse__query", "region,total\nKR,41\n")

    def record(text, **extra):
        cmd = [sys.executable, "-m", "aelix_analytics", "--project", str(project.root), "record", "finding", text]
        proc = subprocess.run(cmd, capture_output=True, env=kit_env(**extra), timeout=300, **_platform.text_kwargs())
        return json.loads([line for line in proc.stdout.split("\n") if line.startswith("{")][-1])

    plain = record("KR은 41")
    assert plain["ok"] and "claim" not in plain and not (project.state_dir / claims.FILE).exists()
    checked = record("KR은 41이고 JP는 777", AELIX_ANALYTICS_SESSION=SESSION)
    assert checked["ok"] and checked["claim"]["grade"] == "some_seen"
    assert checked["claim"]["event"] == checked["event"]["id"]
    off = record("KR은 41", AELIX_ANALYTICS_SESSION=SESSION, AELIX_ANALYTICS_CLAIMS="off")
    assert off["ok"] and "claim" not in off
    code_lines = claims.read(project)["claims"]
    assert [c["event"] for c in code_lines] == [checked["event"]["id"]]


def test_a_result_read_before_is_not_read_again_in_the_same_process(project):
    line = saw(project, 1, "warehouse__query", "region,total\nKR,41\n")
    first = claims.check(project, "KR은 41", session=SESSION)
    assert first["looked"]["results"] == 1 and first["looked"]["bytes"] > 0
    calls.held_path(project, line["result"]["sha256"]).unlink()
    again = claims.check(project, "KR은 41", session=SESSION)
    assert again["values"] == first["values"] and again["looked"] == {"calls": 1, "results": 1, "bytes": 0, "partial": False}
    # What it holds is bounded by the numbers held, the oldest going first.
    claims._parse("a" * 64, " ".join(map(str, range(100, 400))))
    assert len(claims._PARSED) == 2
    saved = claims.PARSED_NUMBERS
    try:
        claims.PARSED_NUMBERS = 300
        claims._parse("b" * 64, "500 600")
    finally:
        claims.PARSED_NUMBERS = saved
    assert list(claims._PARSED) == ["b" * 64]
