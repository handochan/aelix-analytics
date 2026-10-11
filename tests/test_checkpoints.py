"""ADR0012 T1–T10: exact journal basis, mechanical review and immutable pending handoff."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from conftest import analytics_project, kit_cli
from jsonschema import Draft202012Validator, FormatChecker

from aelix_analytics import checkpoints, core, relations, runner, writer

HUMAN = core.Origin("human", "sdk", "inferred")
VALIDATOR = Draft202012Validator(json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/analytics-records.schema.json").read_text()),
                                format_checker=FormatChecker())


def project(tmp_path):
    return core.Project(analytics_project(tmp_path / "프로젝트 space", version="0.8.0"))


def record(p, kind="finding", text="원문 그대로", **options):
    return core.record(p, kind, text, origin=HUMAN, **options).event


def checkpoint(p, text=None, **options):
    return core.checkpoint(p, text, origin=HUMAN, **options)["event"]


def view(p, event=None):
    return core.checkpoint_view(core.read_model(p), core.read_model(p).journal.event_of(event["id"]) if event else None)


def test_t1_exact_shape_origin_raw_bytes_and_unknown_fields(tmp_path):
    p = project(tmp_path)
    finding = record(p)
    prior = p.journal.read_bytes()
    cp = checkpoint(p, "사람 판단 원문", refs=[finding["id"]])
    assert p.journal.read_bytes().startswith(prior)
    d = cp["details"]
    VALIDATOR.validate(cp)
    assert d["contract"] == checkpoints.CONTRACT and d["summary_source"] == "authored"
    assert d["summary"]["finding_event_ids"] == [finding["id"]]
    assert cp["actor"] == "human" and cp["means"] == "sdk" and cp["actor_basis"] == "inferred"
    d["future_optional"] = {"한국어": "preserved"}
    tagged = core.record(p, "checkpoint", "추가", details=d, refs=cp["refs"], origin=HUMAN).event
    assert tagged["details"]["future_optional"] == d["future_optional"]
    assert tagged["details"]["as_of_seq"] == d["as_of_seq"]


@pytest.mark.parametrize("options", [{"as_of_seq": True, "context_version": None}, {"from_seq": True},
                                     {"limit": True}, {"limit": 0}, {"as_of_seq": 999, "context_version": None},
                                     {"as_of_seq": 0}])
def test_t1_invalid_input_before_context_sync_or_lock(tmp_path, options):
    p = project(tmp_path)
    p.context.write_text("# Goal\n아직 저장 안 된 맥락\n", encoding="utf-8")
    before = {str(f.relative_to(p.root)): f.read_bytes() for f in p.root.rglob("*") if f.is_file()}
    with pytest.raises(core.AnalyticsError):
        checkpoint(p, **options)
    assert {str(f.relative_to(p.root)): f.read_bytes() for f in p.root.rglob("*") if f.is_file()} == before


@pytest.mark.parametrize("relation", ["updates", "supersedes", "resolves"])
def test_t1_no_state_change_refs(tmp_path, relation):
    p = project(tmp_path)
    target = record(p, "question")
    before = p.journal.read_bytes()
    with pytest.raises(core.AnalyticsError):
        checkpoint(p, "invalid", refs=[{"type": "event", "event_id": target["id"], "relation": relation}])
    assert p.journal.read_bytes() == before


def append_raw(p, value):
    with p.journal.open("ab") as stream:
        stream.write(json.dumps(value, ensure_ascii=False).encode()+b"\n")


@pytest.mark.parametrize("contract,expected", [(None, "generic_checkpoint"), ("analysis_checkpoint/9", "unsupported_checkpoint_contract"),
                                              ("analysis_checkpoint/1", "invalid_checkpoint")])
def test_t2_latest_problem_not_silently_replaced(tmp_path, contract, expected):
    p = project(tmp_path)
    good = checkpoint(p)
    latest = copy.deepcopy(good)
    latest.update(seq=good["seq"]+1, id=f"e{good['seq']+1}", text="문제 있는 최신 원문")
    latest["details"] = {"as_of_seq": 0, "context_version": None}
    if contract:
        latest["details"]["contract"] = contract
    append_raw(p, latest)
    before = p.journal.read_bytes()
    result = core.status(p, part="context")["checkpoint"]
    assert result["id"] == latest["id"] and result["needs_review"] is None
    assert expected in result["qualification"] and p.journal.read_bytes() == before
    assert core.retrieve(p, id=latest["id"], limit=100)["items"][-1]["value"] == latest["details"]


def test_t3_exact_context_current_historical_unknown_and_no_read_sync(tmp_path):
    p = project(tmp_path)
    p.context.write_text("# Goal\n과거 목적\n", encoding="utf-8")
    first = checkpoint(p)
    version = first["details"]["context_version"]
    assert first["details"]["summary"]["goal"] == "과거 목적"
    p.context.write_text("# Goal\n현재 미저장 목적\n", encoding="utf-8")
    journal = p.journal.read_bytes()
    result = core.status(p, part="context")
    assert result["context_pending"] and p.journal.read_bytes() == journal
    old = checkpoint(p, as_of_seq=first["details"]["as_of_seq"], context_version=version)
    assert old["details"]["summary"]["goal"] == "과거 목적" and old["details"]["context_version"] == version
    unknown = checkpoint(p, as_of_seq=0, context_version=None)
    assert unknown["details"]["summary"]["goal"] is None and unknown["details"]["context_version"] is None
    assert "context_unknown" in view(p, unknown)["qualification"]


def test_t4_first_floor_and_lock_prefix_recheck_append_only(tmp_path):
    p = project(tmp_path)
    first = record(p)
    prepared = core.prepare_checkpoint(p, "held", refs=[first["id"]], origin=HUMAN)
    record(p, "note", "later unrelated")
    held = core.record(p, "checkpoint", prepared["text"], refs=prepared["refs"], details=prepared["details"], origin=HUMAN).event
    assert held["details"]["as_of_seq"] == prepared["details"]["as_of_seq"]
    assert writer.marker_state(p.root).value["min_writer_version"] == "0.14.0"
    with pytest.raises(core.AnalyticsError) as error:
        writer.Writer(p.root, platform=p.platform, origin=HUMAN, writer_version="0.13.0").record("note", "old writer")
    assert error.value.code == "writer_too_old"
    p.journal.write_bytes(p.journal.read_bytes().replace("원문 그대로".encode(), "원문 바뀜".encode()))
    with pytest.raises(core.AnalyticsError) as error:
        core.record(p, "checkpoint", prepared["text"], refs=prepared["refs"], details=prepared["details"], origin=HUMAN)
    assert error.value.code == "checkpoint_basis_changed"
    assert relations.METRIC_SINCE == "0.9.0" and relations.ASSET_WRITER_SINCE == "0.10.0"
    assert relations.ASSET_AUTOMATIC_SINCE == "0.12.0" and relations.ASSET_HOST_SINCE == "0.13.0"


@pytest.mark.parametrize("kind", ["question", "todo"])
def test_t5_intermediate_selected_close_reopen_and_unrelated_update(tmp_path, kind):
    p = project(tmp_path)
    chosen = record(p, kind, "선택된 질문")
    other = record(p, kind, "무관한 질문")
    cp = checkpoint(p, "요약", refs=[chosen["id"]])
    record(p, "resolve", "unrelated", refs=[{"type": "event", "event_id": other["id"], "relation": "resolves"}])
    assert (view(p, cp)["needs_review"], view(p, cp)["updates_available"]) == (False, True)
    closed = record(p, "resolve", "선택 닫음", refs=[{"type": "event", "event_id": chosen["id"], "relation": "resolves"}])
    reopened = core.writer_for(p, HUMAN).update(chosen["id"], expected="resolved", new="open", text="재개 이유").event
    result = view(p, cp)
    assert result["needs_review"] and result["updates_available"]
    assert {r.get("change_event_id") for r in result["review_reasons"]} == {closed["id"], reopened["id"]}
    assert core.read_model(p).journal.replay().state_of(chosen["id"])["state"] == "open"


def test_t5_finding_withdraw_restore_corrections_and_conflict(tmp_path):
    p = project(tmp_path)
    finding = record(p)
    cp = checkpoint(p)
    w = core.writer_for(p, HUMAN)
    w.update(finding["id"], expected="current", new="withdrawn", text="철회")
    w.update(finding["id"], expected="withdrawn", new="current", text="복원")
    for text in ("첫 정정", "서로 다른 정정"):
        record(p, text=text, refs=[{"type": "event", "event_id": finding["id"], "relation": "supersedes"}])
    result = view(p, cp)
    assert result["needs_review"] and len(result["review_reasons"]) == 4
    assert result["review_reasons"][-1]["after"]["state"] == "conflict"


def test_t6_flags_checkpoint_itself_and_missing_context_without_append(tmp_path):
    p = project(tmp_path)
    cp = checkpoint(p)
    assert (view(p, cp)["needs_review"], view(p, cp)["updates_available"]) == (False, False)
    before = p.journal.read_bytes()
    relations.context_file(p.root, cp["details"]["context_version"]).unlink()
    assert (view(p, cp)["needs_review"], view(p, cp)["updates_available"]) == (True, False)
    assert p.journal.read_bytes() == before
    with pytest.raises(core.AnalyticsError) as error:
        core.record(p, "checkpoint", "missing preserved context", details=cp["details"], origin=HUMAN)
    assert error.value.code == "checkpoint_reference" and p.journal.read_bytes() == before


def execute(p, code):
    script = p.scripts / "handoff.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(code, encoding="utf-8")
    return runner.execute(p, script="scripts/handoff.py", title="Handoff", origin=HUMAN)


def test_t7_exact_future_run_ref_and_pending_default_success(tmp_path):
    p = project(tmp_path)
    result = execute(p, "import aelix_analytics as a\nreply=a.checkpoint('실행 중 요약')\nprint(reply)\n")
    assert result["run"]["status"] == "ok" and not result.get("finalize_errors")
    cp = core.read_model(p).journal.events[-1].value
    assert cp["kind"] == "checkpoint" and cp["actor"] == "human" and cp["means"] == "sdk"
    assert not any(r.get("run_id") == result["run"]["id"] for r in cp["refs"])
    assert cp["details"]["as_of_seq"] < core.read_model(p).completion_seq(result["run"]["id"])
    with pytest.raises(core.AnalyticsError) as error:
        checkpoint(p, "historical", as_of_seq=cp["details"]["as_of_seq"],
                   context_version=cp["details"]["context_version"], refs=[result["run"]["id"]])
    assert error.value.code == "checkpoint_reference"


def test_t10_explicit_pending_run_ref_fails_finalization_not_retroactively_accepted(tmp_path):
    p = project(tmp_path)
    result = execute(p, "import aelix_analytics as a\na.checkpoint('explicit pending evidence', refs=[a.run_id()])\n")
    assert result["run"]["status"] == "ok"
    assert any("checkpoint_reference" in value for value in result["finalize_errors"])
    assert all(e.kind != "checkpoint" for e in core.read_model(p).journal.events)


def test_t8_followups_cursor_bound_prefix_append_and_complete_json(tmp_path):
    p = project(tmp_path)
    long = "완전한 원문 " * 1000
    cp = checkpoint(p, long)
    for index in range(4):
        record(p, "note", f"후속 {index}")
    first = core.status(p, part="context", id=cp["id"], limit=2)
    assert first["has_more"] and first["omitted"]["count"] == 2
    held = first["as_of_seq"]
    record(p, "note", "append after page")
    following = core.status(p, part="context", id=cp["id"], limit=2, as_of_seq=held, cursor=first["cursor"])
    assert following["returned"] == 2
    detail = core.status(p, part="records", id=cp["id"], limit=100)
    assert dict((row["field"], row["value"]) for row in detail["items"])["text"] == long.strip()
    p.journal.write_bytes(p.journal.read_bytes().replace("후속 0".encode(), "후속 X".encode()))
    with pytest.raises(core.AnalyticsError) as error:
        core.status(p, part="context", id=cp["id"], limit=2, as_of_seq=held, cursor=first["cursor"])
    assert error.value.code == "stale_cursor"


def test_t9_t10_cli_and_sdk_authored_deterministic_unicode_handoff(tmp_path):
    p = project(tmp_path)
    code, answer = kit_cli(p.root, "checkpoint", "사람의 요약")
    assert code == 0 and answer["event"]["text"] == "사람의 요약"
    agent = core.record(p, "checkpoint", "에이전트 인계", origin=core.Origin("agent", "tool", "declared")).event
    final = checkpoint(p)
    assert agent["actor"] == "agent" and agent["means"] == "tool"
    assert len({answer["event"]["id"], agent["id"], final["id"]}) == 3
    assert answer["event"]["details"]["summary_source"] == "authored"
    assert final["details"]["summary_source"] == "deterministic"


def test_t1_t2_wrong_selected_kind_shared_id_and_damaged_knowledge(tmp_path):
    p = project(tmp_path)
    q = record(p, "question")
    cp = checkpoint(p)
    wrong = copy.deepcopy(cp["details"])
    wrong["summary"]["finding_event_ids"] = [q["id"]]
    with pytest.raises(core.AnalyticsError) as error:
        core.record(p, "checkpoint", "wrong kind", details=wrong, origin=HUMAN)
    assert error.value.code == "checkpoint_reference"
    append_raw(p, {**q, "seq": cp["seq"]+1})
    with pytest.raises(core.AnalyticsError) as error:
        checkpoint(p, "shared identity", refs=[q["id"]])
    assert error.value.code == "checkpoint_reference"
    with p.journal.open("ab") as out:
        out.write(b'cut and damaged\n')
    result = view(p, cp)
    assert "journal_knowledge_incomplete" in result["qualification"]


def test_t3_context_sync_partial_effect_is_explicit(tmp_path, monkeypatch):
    p = project(tmp_path)
    p.context.write_text("# Goal\nChanged purpose\n", encoding="utf-8")
    original = writer.Writer.record

    def refuse(self, kind, *args, **kwargs):
        if kind == "checkpoint":
            raise core.AnalyticsError("checkpoint_basis_changed", "forced failure after context sync")
        return original(self, kind, *args, **kwargs)

    monkeypatch.setattr(writer.Writer, "record", refuse)
    with pytest.raises(core.AnalyticsError) as error:
        checkpoint(p)
    assert error.value.details["context_synced"]["kind"] == "context"
    assert core.read_model(p).journal.events[-1].kind == "context"


def test_t5_metric_and_focus_replacement_keep_exact_selected_versions(tmp_path):
    from test_metrics import definition, focus

    p = project(tmp_path)
    old = core.record(p, "metric_definition", "old", details=definition(), origin=HUMAN).event
    chosen = core.record(p, "analysis_focus", "focus", details=focus([
        {"metric_id": "품질", "event_id": old["id"], "role": "primary"}]), origin=HUMAN).event
    cp = checkpoint(p)
    assert cp["details"]["summary"]["metric_event_ids"] == [old["id"]]
    assert cp["details"]["summary"]["focus_event_id"] == chosen["id"]
    core.record(p, "metric_definition", "changed", details=definition(previous_definition=old["id"]), origin=HUMAN)
    assert view(p, cp)["needs_review"]
    assert cp["details"]["summary"]["metric_event_ids"] == [old["id"]]


@pytest.mark.parametrize("named_ref", [False, True])
def test_review_f1_tagged_pending_keeps_call_time_context_digest(tmp_path, named_ref):
    p = project(tmp_path)
    original = checkpoint(p, "old exact basis")
    version = original["details"]["context_version"]
    path = relations.context_file(p.root, version)
    old_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    refs = [version] if named_ref else []
    result = execute(p, "import aelix_analytics as a\n" +
                     f"a.record('checkpoint', 'tagged pending', details={original['details']!r}, refs={refs!r})\n" +
                     f"(a.project().context_history / {version!r}).write_bytes(b'# Goal\\nchanged preserved bytes\\n')\n")
    assert result["run"]["status"] == "ok" and not result.get("finalize_errors")
    latest = core.read_model(p).journal.events[-1].value
    digest = next(ref["digest"]["sha256"] for ref in latest["refs"] if ref["type"] == "context_version")
    assert latest["kind"] == "checkpoint" and digest == old_digest
    assert digest != hashlib.sha256(path.read_bytes()).hexdigest()
    assert latest["details"]["as_of_seq"] == original["details"]["as_of_seq"]
    assert view(p, latest)["needs_review"] is True


@pytest.mark.parametrize("case", ["wrong_focus", "plain_change", "closed_question", "closed_todo",
                                  "resolves", "malformed_digest", "non_object", "non_string_type"])
def test_review_f2_specialist_reader_invalid_payload_is_qualified_and_raw_latest_preserved(tmp_path, case):
    p = project(tmp_path)
    q = record(p, "question")
    todo = record(p, "todo")
    note = record(p, "note")
    if case in ("closed_question", "closed_todo"):
        target = q if case == "closed_question" else todo
        record(p, "resolve", "closed before basis", refs=[{"type": "event", "event_id": target["id"], "relation": "resolves"}])
    original = checkpoint(p, "valid source")
    bad = copy.deepcopy(original)
    bad.update(seq=original["seq"]+1, id=f"e{original['seq']+1}")
    summary = bad["details"]["summary"]
    if case == "wrong_focus":
        summary["focus_event_id"] = note["id"]
    if case == "plain_change":
        summary["change_event_ids"] = [note["id"]]
    if case in ("closed_question", "closed_todo"):
        summary["question_event_ids" if case == "closed_question" else "todo_event_ids"] = [target["id"]]
    digest = next(ref["digest"]["sha256"] for ref in original["refs"] if ref["type"] == "context_version")
    bad["refs"] = checkpoints.generated_refs(bad["details"], core.read_model(p).journal, digest)
    if case == "resolves":
        bad["refs"].append(writer.typed_reference({"type": "event", "event_id": q["id"], "relation": "resolves"}))
    if case == "malformed_digest":
        next(ref for ref in bad["refs"] if ref["type"] == "context_version")["digest"] = "invalid digest"
    if case == "non_object":
        bad["refs"].append(17)
    if case == "non_string_type":
        bad["refs"].append({"type": [], "relation": "cites", "basis": "declared", "version_binding": "author"})
    append_raw(p, bad)
    before = p.journal.read_bytes()
    result = core.status(p, part="context")["checkpoint"]
    assert result["id"] == bad["id"] and result["needs_review"] is None
    assert "invalid_checkpoint" in result["qualification"] and p.journal.read_bytes() == before
    raw = core.retrieve(p, id=bad["id"], limit=100)
    assert dict((row["field"], row["value"]) for row in raw["items"])["refs"] == bad["refs"]
    if case == "resolves":
        assert core.read_model(p).journal.replay().state_of(q["id"])["state"] == "open"


def test_review_f2_prefix_change_remains_positive_review_and_generic_relations_keep_meaning(tmp_path):
    p = project(tmp_path)
    q = record(p, "question", "selected")
    cp = checkpoint(p, "valid", refs=[q["id"]])
    p.journal.write_bytes(p.journal.read_bytes().replace(b'"kind": "question"', b'"kind": "note"'))
    result = view(p, cp)
    assert result["needs_review"] is True
    assert any(reason["reason"] == "checkpoint_basis_changed" for reason in result["review_reasons"])
    q2 = record(p, "question", "generic compatibility")
    generic = copy.deepcopy(cp)
    seq = core.read_model(p).journal.next_seq
    generic.update(seq=seq, id=f"e{seq}")
    generic["details"] = {"as_of_seq": seq-1, "context_version": None}
    generic["refs"] = [writer.typed_reference({"type": "event", "event_id": q2["id"], "relation": "resolves"})]
    append_raw(p, generic)
    assert core.read_model(p).journal.replay().state_of(q2["id"])["state"] == "resolved"
    assert "generic_checkpoint" in view(p, generic)["qualification"]


@pytest.mark.parametrize("start", [True, 999])
def test_review_f3_supplied_authored_range_is_validated_before_effects(tmp_path, start):
    p = project(tmp_path)
    record(p)
    p.context.write_text("# Goal\nunsaved purpose\n", encoding="utf-8")
    before = {str(f.relative_to(p.root)): f.read_bytes() for f in p.root.rglob("*") if f.is_file()}
    with pytest.raises(core.AnalyticsError):
        core.record(p, "checkpoint", "bad authored range", details={"from_seq": start}, origin=HUMAN)
    assert {str(f.relative_to(p.root)): f.read_bytes() for f in p.root.rglob("*") if f.is_file()} == before


def test_review_f3_supplied_authored_range_retained_and_default_only_if_absent(tmp_path):
    p = project(tmp_path)
    record(p)
    record(p)
    written = core.record(p, "checkpoint", "declared range", details={"from_seq": 2}, origin=HUMAN).event
    assert written["details"]["from_seq"] == 2
    assert core.record(p, "checkpoint", "default", origin=HUMAN).event["details"]["from_seq"] == 0
