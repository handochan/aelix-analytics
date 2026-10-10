"""Same-basis bounded reads, source-authoritative cache, historical judgment and Unicode recall."""
import json
import sqlite3

import pytest

from aelix_analytics import core, relations

ORIGIN = core.Origin("human", "cli", "declared")


def project_at(tmp_path):
    root = tmp_path / "project"
    core.init_project(root, title="Retrieval", goal="현황과 예전 근거 회수", origin=ORIGIN)
    return core.Project(root)


def record(project, kind="finding", text="매출 감소", **options):
    return core.record(project, kind, text, origin=ORIGIN, **options).event


def test_short_korean_full_fields_and_state_history(tmp_path):
    p = project_at(tmp_path)
    old = record(p, text="과거 매출 감소 " + "장기 분석 자료 " * 800)
    correction = record(p, text="매출 정정", refs=[{"type": "event", "event_id": old["id"],
                                                   "relation": "supersedes"}])
    found = core.status(p, part="records", query="매출", limit=2)
    rows = {r["id"]: r for r in found["items"]}
    assert rows[old["id"]]["record_state"]["state"] == "superseded"
    assert rows[old["id"]]["record_state"]["corrected_by"] == [correction["id"]]
    assert rows[old["id"]]["summary_omitted"]
    detail = core.status(p, part="records", id=old["id"], limit=2)
    fields = {}
    while True:
        fields.update((r["field"], r["value"]) for r in detail["items"])
        if not detail["has_more"]:
            break
        detail = core.status(p, part="records", id=old["id"], limit=2, cursor=detail["cursor"])
    assert fields["text"] == old["text"]
    historical = core.retrieve(p, id=old["id"], as_of_seq=old["seq"])
    assert historical["record_state"]["state"] == "current"
    assert historical["record_state"]["corrected_by"] == []
    assert historical["evidence_availability"].startswith("checked now")


def test_cursor_basis_query_fingerprint_and_journal_resume(tmp_path):
    p = project_at(tmp_path)
    for n in range(5):
        record(p, text=f"매출 {n}")
    page = core.retrieve(p, query="매출", limit=2)
    for opts in ({"part": "context"}, {"query": "다름"}, {"query": "매출", "limit": 3}):
        with pytest.raises(core.AnalyticsError) as error:
            core.retrieve(p, cursor=page["cursor"], **opts)
        assert error.value.code == "cursor_query_mismatch"
    record(p, text="새 매출")
    with pytest.raises(core.AnalyticsError) as error:
        core.retrieve(p, query="매출", limit=2, cursor=page["cursor"])
    assert error.value.code == "stale_cursor" and error.value.details["source_revision"]
    boundary = core.read_model(p).as_of_seq
    page = core.retrieve(p, limit=2, as_of_seq=boundary)
    record(p, text="후속 발견")
    next_page = core.retrieve(p, limit=2, as_of_seq=boundary, cursor=page["cursor"])
    assert page["items"][-1]["seq"] < next_page["items"][0]["seq"] <= boundary


def test_corrupt_and_unsupported_fts_index_cannot_replace_source(tmp_path):
    p = project_at(tmp_path)
    expected = record(p, text="짧은 한글 값 sensor_01")
    adapter = core.QueryIndex(p, fts=False)
    first = core.retrieve(p, query="값", index=adapter)
    assert first["index"]["state"] == "missing" and first["index"]["rebuilt"]
    warm = core.retrieve(p, query="값", index=adapter)
    assert warm["index"]["state"] == "ready" and not warm["index"]["fts"]
    with sqlite3.connect(adapter.path) as db:
        db.execute("UPDATE documents SET text='invented text' WHERE id=?", (expected["id"],))
        db.execute("INSERT INTO documents VALUES ('e9999', '값 ghost')")
    corrupt = core.retrieve(p, query="값", index=adapter)
    assert corrupt["index"]["state"] == "corrupt" and corrupt["index"]["rebuilt"]
    assert [r["id"] for r in corrupt["items"]] == [expected["id"]]
    adapter.path.write_bytes(b"truncated SQLite")
    failed = core.retrieve(p, query="값")
    assert failed["index"]["state"] == "corrupt"
    rebuilt = core.retrieve(p, query="값")
    assert rebuilt["index"]["rebuilt"] and rebuilt["items"][0]["id"] == expected["id"]


def test_read_only_legacy_index_has_no_side_effect(tmp_path):
    p = project_at(tmp_path)
    record(p, text="과거 매출")
    marker = json.loads(p.marker.read_text())
    p.marker.unlink()
    marker.update(schema="lab.project/1")
    (p.root / "lab.json").write_text(json.dumps(marker))
    p = core.Project(p.root)
    before = {f.relative_to(p.root): f.read_bytes() for f in p.root.rglob("*") if f.is_file()}
    result = core.retrieve(p, query="매출")
    assert result["index"]["write_skipped"]
    after = {f.relative_to(p.root): f.read_bytes() for f in p.root.rglob("*") if f.is_file()}
    assert before == after


def test_ambiguous_ids_and_missing_evidence_are_never_confirmed(tmp_path):
    p = project_at(tmp_path)
    old = record(p)
    # A damaged/hand-edited journal is still readable; duplicate IDs need line identities.
    with p.journal.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({**old, "seq": old["seq"] + 1}) + "\n")
    with pytest.raises(core.AnalyticsError) as error:
        core.retrieve(p, id=old["id"])
    assert error.value.code == "ambiguous_id"
    found = core.retrieve(p, query="매출")
    assert all(r["id"].startswith("journal:") for r in found["items"])
    assert core.retrieve(p, id=found["items"][0]["id"])["location"].startswith("journal:")


def test_context_pages_preserve_whole_sections_and_reference_lookup(tmp_path):
    p = project_at(tmp_path)
    p.context.write_text("# Context\n\n## 목적\n" + "원문 " * 1500 + "\n## 질문\n무엇인가?\n", encoding="utf-8")
    context = core.retrieve(p, part="context", limit=1)
    assert context["has_more"]
    second = core.retrieve(p, part="context", limit=1, cursor=context["cursor"])
    assert len(second["items"][0]["text"]) > 4000
    a = record(p)
    b = record(p, text="정정", refs=[{"type": "event", "event_id": a["id"], "relation": "supersedes"}])
    incoming = core.retrieve(p, part="relations", id=a["id"], direction="incoming")
    assert incoming["items"][0]["from"] == b["id"]
    assert incoming["items"][0]["confirmed"]
    outgoing = core.retrieve(p, part="relations", id=b["id"], direction="outgoing")
    assert outgoing["items"][0]["target"]["event_id"] == a["id"]


def test_repeated_source_changes_return_updating_without_facts(tmp_path, monkeypatch):
    p = project_at(tmp_path)
    record(p)
    original = relations.QueryPolicy.page
    calls = []
    def changing(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        calls.append(record(p, text="concurrent change"))
        return result
    monkeypatch.setattr(relations.QueryPolicy, "page", changing)
    result = core.retrieve(p)
    assert len(calls) == 3
    assert result["updating"] and not result["items"]
    assert result["omitted"]["reason"] == "updating" and result["total"] == "unknown"


def test_equal_relevance_preserves_old_and_new_judgments(tmp_path):
    p = project_at(tmp_path)
    old = record(p, text="야간 배송과 반품률")
    for n in range(30):
        newest = record(p, text=f"야간 배송 후속 관측 {n}")
    page = core.retrieve(p, query="야간 배송", limit=2)
    assert [row["id"] for row in page["items"]] == [old["id"], newest["id"]]
    assert page["total"] == 31 and page["has_more"]


def test_unreadable_record_and_bad_cursor_are_handled(tmp_path):
    p = project_at(tmp_path)
    for target in ("run:no-such-run", "report:no-such-report/revision"):
        with pytest.raises(core.AnalyticsError) as error:
            core.retrieve(p, id=target)
        assert error.value.code == "record_not_found"
    with pytest.raises(core.AnalyticsError) as error:
        core.retrieve(p, cursor="not base64/JSON")
    assert error.value.code == "bad_cursor"


def test_historical_read_excludes_later_run_completion_and_missing_links(tmp_path):
    p = project_at(tmp_path)
    run_id = "20261010-100000-historical"
    run_dir = p.runs / run_id
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"schema": relations.RUN_V2, "id": run_id,
                                                 "status": "ok", "title": "past analysis"}), encoding="utf-8")
    early = record(p, refs=[{"type": "run", "run_id": run_id, "relation": "cites"}])
    seq = early["seq"] + 1
    completion = {"schema": relations.EVENT_V2, "seq": seq, "id": f"e{seq}", "kind": "run",
                  "actor": "system", "recorded_at": early["recorded_at"], "text": "done",
                  "refs": [{"type": "run", "run_id": run_id, "relation": "about",
                            "basis": "observed", "version_binding": "author"}]}
    with p.journal.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(completion) + "\n")
    assert core.retrieve(p, id=f"run:{run_id}")["returned"]
    with pytest.raises(core.AnalyticsError) as error:
        core.retrieve(p, id=f"run:{run_id}", as_of_seq=early["seq"])
    assert error.value.code == "record_not_found"
    historical = core.retrieve(p, part="relations", id=early["id"], as_of_seq=early["seq"])
    assert historical["items"][0]["state"] == "missing" and not historical["items"][0]["confirmed"]
    unknown = record(p, refs=[{"type": "event", "event_id": "e999999", "relation": "supports"}])
    unavailable = core.retrieve(p, part="relations", id=unknown["id"])
    assert unavailable["items"][0]["state"] == "missing" and not unavailable["items"][0]["confirmed"]
