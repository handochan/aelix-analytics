"""ADR-0013 PR2: actual writes, partial collections, sidecar binding and shared CLI/SDK."""
import hashlib
import json
import math
from pathlib import Path

import pytest
from conftest import analytics_project, kit_cli
from test_reports import VALIDATOR

import aelix_analytics as analytics
from aelix_analytics import _platform, core, relations, report_app

AUTHOR = core.Origin("한국어 작성자", "sdk", "declared")


@pytest.fixture
def case(tmp_path):
    p = core.Project(analytics_project(tmp_path / "프로젝트", version="0.8.0"), platform=_platform.detect())
    source = tmp_path / "한글 원본"
    source.mkdir()
    (source / "요약.md").write_bytes("# 요약\r\n원문 e\u0301\r\n".encode())
    return p, source


def save(case, report_id="weekly", *, request="req-1", previous=None, metadata=None):
    return core.save_report(case[0], report_id, files=case[1], entry="요약.md", title="보고서",
                            previous_revision=previous, request_id=request, metadata=metadata, origin=AUTHOR)


def model(case):
    return report_app.Reports(core.writer_for(case[0], AUTHOR))._model()


def directory(case, answer):
    row = answer["report"]
    return case[0].root / "artifacts/reports" / row["report_id"] / row["revision_id"]


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def error(code, callback):
    with pytest.raises(core.AnalyticsError) as caught:
        callback()
    assert caught.value.code == code


def test_canonical_metadata_unicode_vector_and_unknown_fields(case):
    metadata = {"summary": "요약 e\u0301\r\n", "scope": {"열": [1.25, None, True]},
                "generation": {"environment": "외부", "version": 2}, "content_status": "partial",
                "purpose": "검토", "change_reason": "수정", "작성자 확장": {"b": 2, "a": "그대로"}}
    data = relations.report_metadata_bytes(metadata)
    assert relations.report_metadata_bytes({"한글": [1.25, True, None], "a": "e\u0301\r\n"}) == '{"a":"e\u0301\\r\\n","한글":[1.25,true,null]}'.encode()
    first = save(case, metadata=metadata)
    preserved = snapshot(directory(case, first))
    manifest = json.loads(preserved["manifest.json"])
    VALIDATOR.validate(manifest)
    assert preserved["content/_analytics/metadata.json"] == data
    assert all(manifest[k] == v for k, v in metadata.items())
    sidecar = next(f for f in manifest["files"] if f["path"] == relations.REPORT_METADATA_FILE)
    assert sidecar == {"path": relations.REPORT_METADATA_FILE, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    assert first["files"][-1]["source"] is None and first["files"][-1]["generated_by"] == "kit"
    assert save(case, metadata=dict(reversed(list(metadata.items()))))["outcome"] == "existing"
    metadata["scope"]["열"].append("caller changed")
    assert snapshot(directory(case, first)) == preserved


@pytest.mark.parametrize("field,value", [("summary", "new"), ("scope", {"열": 2.1}), ("purpose", "reason"),
                                         ("change_reason", "reason"), ("generation", [1, 2]),
                                         ("content_status", "complete"), ("future_optional", {"x": False})])
def test_every_optional_change_changes_intent_and_same_request_conflicts(case, field, value):
    first = save(case)
    old = json.loads((directory(case, first) / "manifest.json").read_bytes())
    error("request_conflict", lambda: save(case, metadata={field: value}))
    second = save(case, request="req-2", previous=first["report"]["revision_id"], metadata={field: value})
    new = json.loads((directory(case, second) / "manifest.json").read_bytes())
    assert old["request_fingerprint"] != new["request_fingerprint"]
    assert relations.request_fingerprint(old) == old["request_fingerprint"]


@pytest.mark.parametrize("metadata", [{"title": "hijack"}, {"metadata_file": "elsewhere"}, {"summary": 1},
                                       {"content_status": "approved"}, {"x": math.nan}, {"x": math.inf},
                                       {"x": {1: "coerced key"}}, {"x": (1, 2)}, {"x": "\ud800"}, [],
                                       {"collection": {"contract": "other", "members": []}}])
def test_invalid_metadata_refused_before_staging(case, metadata):
    error("report_input_invalid", lambda: save(case, metadata=metadata))
    assert not (case[0].root / "artifacts/reports/weekly").exists()


@pytest.mark.parametrize("tamper", ["manifest", "sidecar", "missing_sidecar", "noncanonical_sidecar"])
def test_sidecar_and_manifest_disagreement_refuses_reads_and_retry(case, tamper):
    first = save(case, metadata={"summary": "v1"})
    path = directory(case, first)
    manifest = json.loads((path / "manifest.json").read_bytes())
    sidecar = path / "content/_analytics/metadata.json"
    if tamper == "manifest":
        manifest["summary"] = "unbound change"
        assert relations.report_manifest_problem(manifest) == "metadata_binding_mismatch"
        (path / "manifest.json").write_text(json.dumps(manifest))
    elif tamper == "missing_sidecar":
        sidecar.unlink()
    elif tamper == "noncanonical_sidecar":
        sidecar.write_text('{ "summary": "v1" }')
    else:
        sidecar.write_text('{"summary":"different"}')
    read = core.reports(case[0], "weekly", first["report"]["revision_id"])
    assert read["reports"][0]["problem"]
    error("report_record_invalid", lambda: save(case, metadata={"summary": "v1"}))


def test_metadata_list_does_not_hash_authored_body(case, monkeypatch):
    first = save(case, metadata={"scope": {"x": 1.2}})
    original, hashed = report_app._sha, []

    def sha(path):
        hashed.append(Path(path).name)
        return original(path)

    monkeypatch.setattr(report_app, "_sha", sha)
    result = core.reports(case[0])
    assert result["reports"][0]["content"] == "not_checked" and "요약.md" not in hashed
    selected = core.reports(case[0], "weekly", first["report"]["revision_id"], verify_files=True)
    assert selected["reports"][0]["content"] == "resolved" and "요약.md" in hashed


def collection(members):
    return {"collection": {"contract": "report_collection/1", "members": members}}


def complete(key, result):
    return {"member_key": key, "status": "complete", "report_id": result["report"]["report_id"],
            "revision_id": result["report"]["revision_id"]}


def test_twenty_members_partial_failure_then_new_snapshot_keeps_old_bytes_and_members(case):
    saved = [save(case, f"column-{n}") for n in range(18)]
    members = [complete(f"컬럼 {n}", answer) for n, answer in enumerate(saved)]
    members += [{"member_key": "실패", "status": "failed", "reason": "생성 실패", "scope": {"열": "a"}},
                {"member_key": "미생성", "status": "not_generated", "reason": "아직 요청하지 않음"}]
    metadata = collection(members)
    metadata["content_status"] = "partial"
    first = save(case, "collection", metadata=metadata)
    assert first["report"]["collection_counts"] == {"requested": 20, "complete": 18, "failed": 1, "not_generated": 1}
    frozen = snapshot(directory(case, first))
    m = model(case)
    source = relations.report_key("collection", first["report"]["revision_id"])
    refs = m.references(source)
    assert len(refs) == 18 and all(r["relation"] == "includes" and r["structural"] and r["state"] == "resolved" for r in refs)
    assert all(r["basis"] == "declared" and r["version_binding"] == "author" for r in refs)
    assert core.references_summary(refs)["confirmed"] == 0
    assert source in m.incoming_sources(relations.report_key(saved[0]["report"]["report_id"], saved[0]["report"]["revision_id"]))
    assert [r for r in m.references() if r["from"] == source] == refs
    late = save(case, "failed-column")
    members[18] = complete("실패", late)
    # A member gets a newer version, but the existing collection keeps exactly its old address.
    newer = save(case, "column-0", request="req-2", previous=saved[0]["report"]["revision_id"])
    second = save(case, "collection", request="collection-2", previous=first["report"]["revision_id"], metadata=collection(members))
    assert second["report"]["collection_counts"] == {"requested": 20, "complete": 19, "failed": 0, "not_generated": 1}
    assert snapshot(directory(case, first)) == frozen
    assert json.loads(frozen["manifest.json"])["collection"]["members"][0]["revision_id"] != newer["report"]["revision_id"]
    error("request_conflict", lambda: save(case, "collection", metadata=collection(members)))


def test_collection_retry_after_rename_rechecks_member_integrity(case, monkeypatch):
    member = save(case, "member")
    metadata = collection([complete("column", member)])
    original = report_app.Reports._complete

    def stop(*args, **kwargs):
        raise OSError("interrupt after rename")

    monkeypatch.setattr(report_app.Reports, "_complete", stop)
    error("report_io", lambda: save(case, "collection", metadata=metadata))
    monkeypatch.setattr(report_app.Reports, "_complete", original)
    (directory(case, member) / "content/요약.md").unlink()
    error("report_member_invalid", lambda: save(case, "collection", metadata=metadata))
    row = next(row for row in model(case).report_revisions() if row["report_id"] == "collection")
    assert row["recording_status"] == "incomplete"


@pytest.mark.parametrize("kind", ["missing", "prepared", "incomplete", "damaged"])
def test_complete_members_require_prior_completion_and_intact_bytes(case, monkeypatch, kind):
    if kind in ("prepared", "incomplete"):
        def stop(*args, **kwargs):
            raise OSError("stop before completion")
        monkeypatch.setattr(report_app.Reports, "_complete", stop)
        error("report_io", lambda: save(case, "member"))
        monkeypatch.undo()
        row = model(case).report_revisions()[0]
        revision = row["revision_id"]
        if kind == "prepared":
            path = case[0].root / "artifacts/reports/member"
            (path / ".staging").mkdir(exist_ok=True)
            (path / revision).rename(path / ".staging" / revision)
    elif kind == "damaged":
        answer = save(case, "member")
        revision = answer["report"]["revision_id"]
        (directory(case, answer) / "content/요약.md").write_bytes(b"damage")
    else:
        revision = "future"
    error("report_member_invalid", lambda: save(case, "collection", metadata=collection([
        {"member_key": "x", "status": "complete", "report_id": "member", "revision_id": revision}])))
    assert not list((case[0].root / "artifacts/reports/collection").glob("*/manifest.json"))
    assert not any(event.kind == "report_revision" and any(ref.get("report_id") == "collection" for ref in event.refs)
                   for event in model(case).journal.events)


@pytest.mark.parametrize("members", [
    [{"member_key": "x", "status": "failed"}],
    [{"member_key": "x", "status": "failed", "reason": "why", "report_id": "forbidden"}],
    [{"member_key": "x", "status": "complete", "report_id": "member"}],
    [{"member_key": "x", "status": "unknown", "reason": "why"}],
    [{"member_key": "same", "status": "failed", "reason": "why"}] * 2,
])
def test_invalid_collection_shapes_refuse_before_staging(case, members):
    error("report_input_invalid", lambda: save(case, metadata=collection(members)))
    assert not (case[0].root / "artifacts/reports/weekly").exists()


def test_sdk_and_cli_metadata_use_same_application(case, monkeypatch, tmp_path):
    monkeypatch.setattr(analytics, "project", lambda: case[0])
    monkeypatch.setattr(analytics, "_asset_origin", lambda: AUTHOR)
    metadata = {"summary": "한글", "scope": {"alpha": 0.5}}
    first = analytics.save_report("weekly", files=case[1], entry="요약.md", title="보고서", previous_revision=None,
                                  request_id="shared-request", metadata=metadata)
    args = tmp_path / "메타데이터.json"
    args.write_text(json.dumps(metadata, ensure_ascii=False))
    result = kit_cli(case[0].root, "reports", "save", "weekly", "--files", str(case[1]), "--entry", "요약.md", "--title", "보고서",
                  "--previous", "none", "--request-id", "shared-request", "--metadata", str(args))
    assert result[0] == 0, result
    assert result[1]["outcome"] == "existing"
    assert analytics.reports("weekly", first["report"]["revision_id"])["manifest"]["scope"] == metadata["scope"]
    assert callable(analytics.reports)
