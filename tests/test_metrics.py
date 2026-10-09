"""ADR-0011: free exploration, exact metric versions and declared run-linked observations."""

import copy
import json
from pathlib import Path

import pytest
from conftest import analytics_project, kit_cli
from jsonschema import Draft202012Validator, FormatChecker

from aelix_analytics import core, relations, runner, writer

ORIGIN = core.Origin("human", "sdk", "inferred")
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "docs/schemas/analytics-records.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def definition(metric="품질", **changes):
    return {"contract": "metric_definition/1", "metric_id": metric, "name": "Quality",
            "status": "draft", "definition": {}, "unresolved": ["집계 단위 미정"],
            "change_reason": "미정 관심사를 먼저 보존", "derived_from": [], **changes}


def focus(selected=(), **changes):
    return {"contract": "analysis_focus/1", "goal": "추가 점검 대상을 정한다", "question": "품질이 나빠졌나?",
            "activity": "자유 탐색", "next_check": "분모를 확인", "change_reason": "현재 질문에 집중",
            "metrics": list(selected), "unresolved": ["재검사 집계"], **changes}


def observation(definition_id, run_id, **changes):
    return {"contract": "metric_observation/1", "metric_id": "품질", "definition_event_id": definition_id,
            "run_id": run_id, "scope": {"equipment": "A", "week": "2026-W40"}, "result": 0.2,
            "change_reason": "같은 정의로 측정", **changes}


def record(project, kind, details, *, refs=()):
    written = core.record(project, kind, details["change_reason"], details=details, refs=refs, origin=ORIGIN)
    VALIDATOR.validate(written.event)
    return written.event


def project_at(tmp_path, *, version=None):
    return core.Project(analytics_project(tmp_path / "project", version=version))


def execute(project, code="print('free exploration')", name="explore"):
    script = project.scripts / (name + ".py")
    script.parent.mkdir(exist_ok=True)
    script.write_text(code, encoding="utf-8")
    return runner.execute(project, script=f"scripts/{name}.py", title=name, origin=ORIGIN)


def bytes_tree(project):
    return {p.relative_to(project.root).as_posix(): p.read_bytes()
            for p in project.root.rglob("*") if p.is_file()}


def test_drafts_multiple_roles_focus_and_run_purpose_do_not_claim_calculation(tmp_path):
    p = project_at(tmp_path, version="0.8.0")
    # No metric or focus registration is necessary to execute arbitrary Python.
    free = execute(p)
    assert free["run"]["status"] == "ok" and free["run"]["analysis_focus"]["state"] == "absent"
    assert core.metric_records(p)["observations"] == []
    a = record(p, "metric_definition", definition())
    b = record(p, "metric_definition", definition("count", name="Quality", unresolved=[]))
    selected = [{"metric_id": "품질", "event_id": a["id"], "role": "주요 관심"},
                {"metric_id": "count", "event_id": b["id"], "role": "표본 크기"}]
    f = record(p, "analysis_focus", focus(selected))
    run = execute(p, "print('only inspect schema')", name="inspect")
    assert run["run"]["analysis_focus"]["refs"][0]["event_id"] == f["id"]
    assert core.metric_records(p)["observations"] == []
    context = core.context_block(p)
    assert "추가 점검 대상을 정한다" in context and a["id"] in context and "집계 단위 미정" not in context
    assert "분모를 확인" in context and "재검사 집계" in context
    assert "calculation check separate" in context
    assert writer.marker_state(p.root).value["min_writer_version"] == "0.9.0"
    with pytest.raises(core.AnalyticsError) as refused:
        writer.Writer(p.root, platform=p.platform, origin=ORIGIN, writer_version="0.8.0").record("note", "old")
    assert refused.value.code == "writer_too_old"


def test_definition_revision_derivation_and_old_results_keep_exact_parent_versions(tmp_path):
    p = project_at(tmp_path)
    d1 = record(p, "metric_definition", definition())
    run = execute(p)
    o1 = record(p, "metric_observation", observation(d1["id"], run["run"]["id"]))
    derived = record(p, "metric_definition", definition("delta", status="specified",
        definition={"formula": "this_week - previous_week", "unit": "percentage point"},
        derived_from=[{"metric_id": "품질", "event_id": d1["id"]}]))
    old_bytes = p.journal.read_bytes()
    d2 = record(p, "metric_definition", definition(previous_definition=d1["id"], status="specified",
        definition={"formula": "defective_unique_products / inspected_unique_products", "unit": "%"},
        change_reason="고유 제품을 분모로 구체화"))
    assert p.journal.read_bytes().startswith(old_bytes)
    assert core.read_model(p).journal.replay().state_of(d1["id"])["state"] == "superseded"
    assert derived["details"]["derived_from"][0]["event_id"] == d1["id"]
    assert o1["details"]["definition_event_id"] == d1["id"]
    revised = core.metric_records(p)
    assert [d["event_id"] for d in revised["definitions"] if d["state"] == "current"] == [derived["id"], d2["id"]]
    assert revised["observations"][0]["calculation_check"] == "unverified"


def test_same_definition_scope_query_and_code_fix_recomputation_are_separate_from_semantic_revision(tmp_path):
    p = project_at(tmp_path)
    d = record(p, "metric_definition", definition(status="specified", definition={"unit": "%"}))
    first = execute(p, "print('old buggy calculation')", name="calculate")
    o1 = record(p, "metric_observation", observation(d["id"], first["run"]["id"], result=[{"week": 1, "value": 2}]))
    second = execute(p, "print('corrected calculation')", name="calculate")
    o2 = record(p, "metric_observation", observation(d["id"], second["run"]["id"], result=1,
        recomputed_from=o1["id"], change_reason="계산 코드의 버그 정정"))
    record(p, "metric_observation", observation(d["id"], second["run"]["id"], scope={"equipment": "B"}))
    assert first["run"]["script"]["sha256"] != second["run"]["script"]["sha256"]
    view = core.metric_records(p)
    assert len(view["definitions"]) == 1 and len(view["observations"]) == 3
    assert o2["details"]["definition_event_id"] == d["id"]
    assert any(r.get("event_id") == o1["id"] and r["relation"] == "derived_from" for r in o2["refs"])
    d2 = record(p, "metric_definition", definition(previous_definition=d["id"]))
    before = bytes_tree(p)
    with pytest.raises(core.AnalyticsError) as error:
        record(p, "metric_observation", observation(d2["id"], second["run"]["id"], recomputed_from=o1["id"]))
    assert error.value.code == "metric_reference" and bytes_tree(p) == before


@pytest.mark.parametrize("relation", ["supersedes", "updates", "resolves"])
def test_caller_cannot_hide_an_unrelated_record_with_a_structured_revision(tmp_path, relation):
    p = project_at(tmp_path)
    a = record(p, "metric_definition", definition("a"))
    b = record(p, "metric_definition", definition("b"))
    before = bytes_tree(p)
    extra = {"type": "metric_definition", "metric_id": "b", "event_id": b["id"], "relation": relation}
    with pytest.raises(core.AnalyticsError) as error:
        record(p, "metric_definition", definition("a", previous_definition=a["id"]), refs=[extra])
    assert error.value.code == "bad_metric_record" and bytes_tree(p) == before
    assert core.read_model(p).journal.replay().state_of(b["id"])["state"] == "current"


@pytest.mark.parametrize("bad", [None, {}, {"contract": "metric_definition/2"},
                                definition(status="validated"), definition(definition={"x": float("nan")}),
                                definition(definition={1: "non-JSON key"})])
def test_bad_metric_payload_has_no_side_effect_not_even_marker_version(tmp_path, bad):
    p = project_at(tmp_path, version="0.8.0")
    before = bytes_tree(p)
    with pytest.raises(core.AnalyticsError) as error:
        core.record(p, "metric_definition", "invalid", details=bad, origin=ORIGIN)
    assert error.value.code == "bad_metric_record" and bytes_tree(p) == before


@pytest.mark.parametrize("target", ["e1", "e999", "e2"])
def test_wrong_kind_missing_or_future_definition_is_refused(tmp_path, target):
    p = project_at(tmp_path)
    before = bytes_tree(p)
    with pytest.raises(core.AnalyticsError) as error:
        record(p, "analysis_focus", focus([{"metric_id": "a", "event_id": target, "role": "main"}]))
    assert error.value.code == "metric_reference" and bytes_tree(p) == before


def test_revision_target_is_rechecked_under_lock_after_a_concurrent_change(tmp_path, monkeypatch):
    p = project_at(tmp_path)
    d = record(p, "metric_definition", definition())
    w = core.writer_for(p, ORIGIN)
    original = w._prepare
    other = []

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        other.append(record(p, "metric_definition", definition(previous_definition=d["id"],
                                                               change_reason="another worker")))
        return result

    monkeypatch.setattr(w, "_prepare", changed)
    with pytest.raises(core.AnalyticsError) as error:
        w.record("metric_definition", "late", details=definition(previous_definition=d["id"]))
    assert error.value.code == "state_conflict"
    assert relations.read_journal(p.root).events[-1].id == other[0]["id"]


def test_focus_change_preserves_reason_and_run_start_purpose_when_changed_during_script(tmp_path):
    p = project_at(tmp_path)
    f1 = record(p, "analysis_focus", focus())
    f2_details = focus(previous_focus=f1["id"], question="표본이 달랐나?", change_reason="분모가 다르다는 발견")
    code = f"import aelix_analytics as analytics\nanalytics.record('analysis_focus', 'direction changed', details={f2_details!r})"
    result = execute(p, code)
    view = core.metric_records(p)
    assert result["run"]["analysis_focus"]["refs"][0]["event_id"] == f1["id"]
    assert view["current_focus"]["details"]["change_reason"] == "분모가 다르다는 발견"
    assert view["focus_versions"][0]["state"] == "superseded"
    with pytest.raises(core.AnalyticsError) as error:
        record(p, "analysis_focus", f2_details)
    assert error.value.code == "state_conflict"


@pytest.mark.parametrize("ending,status", [("", "ok"), ("raise ValueError('failed after observation')", "error"),
                                          ("raise KeyboardInterrupt()", "cancelled")])
def test_sdk_queued_observation_retains_run_status_and_never_becomes_a_math_check(tmp_path, ending, status):
    p = project_at(tmp_path)
    d = record(p, "metric_definition", definition())
    details = observation(d["id"], "20261001-090000-placeholder")
    details.pop("run_id")
    code = (f"import aelix_analytics as analytics\npayload = {details!r}\n"
            "ack = analytics.record('metric_observation', 'measurement declared', details=payload)\n"
            "assert ack['pending'] and ack['run_id'] == analytics.run_id()\n"
            "payload['result'] = 999\n" + ending)
    result = execute(p, code)
    view = core.metric_records(p)
    assert result["run"]["status"] == status and len(view["observations"]) == 1
    item = view["observations"][0]
    assert item["details"]["result"] == 0.2 and item["execution_status"] == status
    assert item["recording_status"] == "complete" and item["calculation_check"] == "unverified"
    event = relations.read_journal(p.root).events[-1].value
    VALIDATOR.validate(event)
    assert event["means"] == "sdk" and event["actor"] == "human"


def test_cli_and_outside_run_sdk_use_same_exact_contract_and_return_event_id(tmp_path, monkeypatch):
    import aelix_analytics as analytics

    p = project_at(tmp_path)
    code, reply = kit_cli(p.root, "record", "metric_definition", "미정 후보", "--details-json", json.dumps(definition()))
    assert code == 0 and reply["ok"] and reply["event"]["details"]["metric_id"] == "품질"
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(p.root))
    f = analytics.record("analysis_focus", "focus", details=focus([
        {"metric_id": "품질", "event_id": reply["event"]["id"], "role": "주요 관심"}]))
    assert f["event"]["id"] and f["event"]["means"] == "sdk"
    before = bytes_tree(p)
    (p.context).write_text("changed context", encoding="utf-8")
    modified = bytes_tree(p)
    code, error = kit_cli(p.root, "record", "analysis_focus", "invalid", "--details-json", "{}")
    assert code == 1 and error["error"]["code"] == "bad_metric_record"
    assert bytes_tree(p) == modified and before != modified


def test_unstructured_legacy_and_minimal_v2_are_not_promoted_to_structured_candidates(tmp_path):
    p = project_at(tmp_path)
    old = {"schema": "analytics.event/2", "seq": 3, "id": "e3", "recorded_at": "2026-10-09T00:00:00Z",
           "kind": "metric_definition", "actor": "human", "text": "old loose definition", "refs": [],
           "details": {"metric_id": "old"}}
    with p.journal.open("ab") as stream:
        stream.write(json.dumps(old).encode() + b"\n")
    before = p.journal.read_bytes()
    view = core.metric_records(p)
    assert view["unstructured"] == ["e3"] and not view["definitions"]
    assert p.journal.read_bytes() == before
    new = record(p, "metric_definition", definition("old", previous_definition="e3"))
    assert new["details"]["previous_definition"] == "e3"


def test_result_ref_keeps_preserved_table_and_declared_input_basis(tmp_path):
    p = project_at(tmp_path)
    d = record(p, "metric_definition", definition())
    result = execute(p, "from pathlib import Path\nPath('data/derived').mkdir(parents=True, exist_ok=True)\n"
                     "Path('data/derived/result.csv').write_text('week,value\\n1,0.2\\n')")
    run = result["run"]
    data = next(o for o in run["outputs"] if o["path"] == "data/derived/result.csv")
    event = record(p, "metric_observation", observation(d["id"], run["id"], result=None,
        result_ref={"type": "data_observation", "run_id": run["id"], "role": "output",
                    "path": data["path"], "digest": {"sha256": data["sha256"]}}))
    assert event["details"]["result_ref"]["version_binding"] == "author"
    assert any(r["type"] == "code_snapshot" for r in event["refs"])
    view = core.metric_records(p)
    assert all(r["state"] == "resolved" for r in view["observations"][0]["references"])


def test_payload_validation_is_pure_and_does_not_read_project_or_host_files(monkeypatch):
    import builtins

    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: pytest.fail("payload validation performs no I/O"))
    assert relations.validate_metric("metric_definition", definition())["definition"] == {}


def test_partial_observation_write_failure_is_visible_as_incomplete_recording(tmp_path, monkeypatch):
    p = project_at(tmp_path)
    d = record(p, "metric_definition", definition())
    details = observation(d["id"], "20261001-090000-placeholder")
    details.pop("run_id")
    original = core.record

    def fail_observation(project, kind, *args, **kwargs):
        if kind == "metric_observation":
            raise OSError("injected disk failure")
        return original(project, kind, *args, **kwargs)

    monkeypatch.setattr(core, "record", fail_observation)
    result = execute(p, "import aelix_analytics as analytics\n"
                     f"analytics.record('metric_observation', 'result', details={details!r})")
    assert result["run"]["status"] == "ok"
    assert any("note metric_observation: OSError" in e for e in result["finalize_errors"])
    model = core.read_model(p)
    assert model.runs()[0]["recording_status"] == "incomplete"
    assert core.metric_records(p)["observations"] == []


def test_duplicate_definition_ids_and_parent_self_reference_do_not_create_false_versions(tmp_path):
    p = project_at(tmp_path)
    d = record(p, "metric_definition", definition())
    with p.journal.open("ab") as stream:
        stream.write(json.dumps(d, ensure_ascii=False).encode("utf-8") + b"\n")
    before = bytes_tree(p)
    with pytest.raises(core.AnalyticsError) as error:
        record(p, "analysis_focus", focus([{"metric_id": "품질", "event_id": d["id"], "role": "main"}]))
    assert error.value.code == "metric_reference" and bytes_tree(p) == before
    with pytest.raises(core.AnalyticsError) as error:
        record(p, "metric_definition", definition(derived_from=[{"metric_id": "품질", "event_id": d["id"]}]))
    assert error.value.code == "bad_metric_record"
    assert all(item["state"] == "ambiguous" for item in core.metric_records(p)["definitions"])


def test_malformed_historical_details_are_not_interpreted_or_crash_a_new_definition(tmp_path):
    p = project_at(tmp_path)
    value = {"schema": "analytics.event/2", "seq": 3, "id": "e3", "kind": "metric_definition",
             "recorded_at": "2026-10-09T00:00:00Z", "actor": "human", "text": "damaged old payload",
             "refs": [], "details": ["not an object"]}
    with p.journal.open("ab") as stream:
        stream.write(json.dumps(value).encode("utf-8") + b"\n")
    created = record(p, "metric_definition", definition())
    assert created["id"] == "e4" and core.metric_records(p)["unstructured"] == ["e3"]


@pytest.mark.parametrize("invalid", ["malformed", "unsupported", "extra_transition"])
@pytest.mark.parametrize("kind", ["metric_definition", "analysis_focus"])
def test_invalid_tagged_records_never_retire_valid_current_state(tmp_path, invalid, kind):
    p = project_at(tmp_path)
    details = definition() if kind == "metric_definition" else focus()
    original = record(p, kind, details)
    payload = copy.deepcopy(details)
    if invalid == "malformed":
        payload.pop("change_reason")
    elif invalid == "unsupported":
        payload["contract"] = f"{kind}/999"
    else:
        # A structurally valid *first* record still cannot carry a hidden transition.
        payload["change_reason"] = "unexpected extra transition"
    ref = {"type": kind, "event_id": original["id"], "relation": "supersedes",
           "basis": "declared", "version_binding": "author"}
    if kind == "metric_definition":
        ref["metric_id"] = "품질"
    value = {**original, "id": "e4", "seq": 4, "details": payload, "refs": [ref]}
    with p.journal.open("ab") as stream:
        stream.write(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n")
    journal = relations.read_journal(p.root)
    assert journal.replay().state_of(original["id"])["state"] == "current"
    view = core.metric_records(p)
    assert any(item["event_id"] == "e4" for item in view["invalid"])
    if kind == "analysis_focus":
        assert view["current_focus"]["event_id"] == original["id"]


@pytest.mark.parametrize("kind", ["data_observation", "visualization_version"])
def test_result_ref_digest_must_match_the_recorded_version_before_append(tmp_path, kind):
    p = project_at(tmp_path)
    d = record(p, "metric_definition", definition())
    result = execute(p, "from pathlib import Path\nimport aelix_analytics as analytics\n"
        "Path('data/derived').mkdir(parents=True, exist_ok=True)\n"
        "Path('data/derived/value.csv').write_text('value\\n0.2\\n')\n"
        "Path('data/derived/view.html').write_text('<p>0.2</p>')\n"
        "analytics.visualize(Path('data/derived/view.html'), 'value-view')")
    run = result["run"]
    if kind == "data_observation":
        version = next(o for o in run["outputs"] if o["path"] == "data/derived/value.csv")
        ref = {"type": kind, "run_id": run["id"], "role": "output", "path": version["path"]}
    else:
        version = relations.run_visualizations(run)[0]
        ref = {"type": kind, "run_id": run["id"], "visualization_id": version["id"]}
    before = bytes_tree(p)
    with pytest.raises(core.AnalyticsError) as error:
        record(p, "metric_observation", observation(d["id"], run["id"], result=None,
            result_ref={**ref, "digest": {"sha256": "0" * 64}}))
    assert error.value.code == "metric_reference" and bytes_tree(p) == before
    accepted = record(p, "metric_observation", observation(d["id"], run["id"], result=None,
        result_ref={**ref, "digest": {"sha256": version["sha256"]}}))
    assert accepted["details"]["result_ref"]["digest"]["sha256"] == version["sha256"]


def test_running_run_future_result_is_allowed_only_in_explicit_pending_precheck(tmp_path):
    p = project_at(tmp_path)
    d = record(p, "metric_definition", definition())
    rid = "20261009-100000-running"
    w = core.writer_for(p, ORIGIN)
    w.write_run(p.runs / rid, {"schema": relations.RUN_V2, "id": rid, "status": "running",
        "started_at": core.iso(), "actor": "human", "actor_basis": "inferred", "means": "sdk",
        "script": {"path": "scripts/x.py", "snapshot": f"runs/{rid}/code/scripts/x.py",
                   "sha256": "1" * 64}, "outputs": []})
    details = observation(d["id"], rid, result=None, result_ref={"type": "data_observation",
        "run_id": rid, "role": "output", "path": "data/derived/never-written.csv"})
    before = bytes_tree(p)
    w.check("metric_observation", "pending", details=details, seq=writer.SEQ_BOUND, pending_run=rid)
    assert bytes_tree(p) == before
    with pytest.raises(core.AnalyticsError) as error:
        w.record("metric_observation", "actual append", details=details)
    assert error.value.code == "metric_reference" and bytes_tree(p) == before


@pytest.mark.parametrize("correct_digest", [True, False])
def test_sdk_pending_result_version_is_revalidated_after_output_capture(tmp_path, correct_digest):
    p = project_at(tmp_path)
    d = record(p, "metric_definition", definition())
    details = observation(d["id"], "20261001-090000-placeholder", result=None)
    details.pop("run_id")
    code = ("import aelix_analytics as analytics\nimport hashlib\nfrom pathlib import Path\n"
        f"details = {details!r}\n"
        "data = b'value\\n0.2\\n'\n"
        f"digest = hashlib.sha256(data).hexdigest() if {correct_digest!r} else '0' * 64\n"
        "details['result_ref'] = {'type': 'data_observation', 'run_id': analytics.run_id(), 'role': 'output', "
        "'path': 'data/derived/value.csv', 'digest': {'sha256': digest}}\n"
        "ack = analytics.record('metric_observation', 'declare result', details=details)\n"
        "assert ack['pending']\n"
        "Path('data/derived').mkdir(parents=True, exist_ok=True)\n"
        "Path('data/derived/value.csv').write_bytes(data)\n")
    result = execute(p, code)
    assert result["run"]["status"] == "ok"
    view = core.metric_records(p)
    if correct_digest:
        assert len(view["observations"]) == 1 and not result.get("finalize_errors")
    else:
        assert not view["observations"]
        assert any("result_ref digest" in problem for problem in result["finalize_errors"])
        assert core.read_model(p).runs()[0]["recording_status"] == "incomplete"
