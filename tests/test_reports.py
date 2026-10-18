"""Report PR1: preserved bytes and real failure/retry boundaries, not fixture-only writes."""
import base64
import contextlib
import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from conftest import analytics_project, kit_cli, kit_env
from jsonschema import Draft202012Validator, FormatChecker

import aelix_analytics as analytics
from aelix_analytics import _platform, core, relations, report_app, writer

HUMAN = core.Origin("작성자", "sdk", "declared")
PACK = Path(__file__).resolve().parents[1]
VALIDATOR = Draft202012Validator(json.loads((PACK / "docs/schemas/analytics-records.schema.json").read_bytes()),
                                format_checker=FormatChecker())


@pytest.fixture
def case(tmp_path):
    p = core.Project(analytics_project(tmp_path / "프로젝트", version="0.8.0"), platform=_platform.detect())
    source = tmp_path / "외부 보고서"
    source.mkdir()
    (source / "요약.md").write_bytes("# 결과\r\n한글 원문 e\u0301\r\n".encode())
    (source / "첨부.bin").write_bytes(bytes(range(256)))
    return p, source


def save(case, *, request="request-1", previous=None, **options):
    p, source = case
    return core.save_report(p, "weekly", files=source, entry="요약.md", title="요약 e\u0301",
                            previous_revision=previous, request_id=request, origin=HUMAN, **options)


def directory(case, answer):
    return case[0].root / "artifacts/reports/weekly" / answer["report"]["revision_id"]


def model(case):
    return report_app.Reports(core.writer_for(case[0], HUMAN))._model()


def error(code, callback):
    with pytest.raises(core.AnalyticsError) as caught:
        callback()
    assert caught.value.code == code
    return caught.value


def test_save_exact_bytes_basis_schema_and_immutable_versions(case):
    basis = {"as_of_seq": 1, "context_version": None, "refs": []}
    first = save(case, based_on=basis)
    assert first["outcome"] == "saved" and first["report"]["head"]
    path = directory(case, first)
    before = {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}
    manifest = json.loads(before["manifest.json"])
    assert manifest["based_on"] == basis
    VALIDATOR.validate(manifest)
    for event in model(case).journal.events:
        VALIDATOR.validate(event.value)
    for file in manifest["files"]:
        content = (path / "content" / file["path"]).read_bytes()
        assert content == (case[1] / file["path"]).read_bytes()
        assert file["sha256"] == hashlib.sha256(content).hexdigest()
    assert json.loads((case[0].root / "analytics.json").read_bytes())["min_writer_version"] == report_app.SINCE
    (case[1] / "요약.md").write_bytes(b"new version\n")
    second = save(case, request="request-2", previous=first["report"]["revision_id"])
    assert second["outcome"] == "saved"
    assert {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()} == before
    assert [row["head"] for row in core.reports(case[0])["reports"]] == [False, True]


def test_idempotency_case_insensitive_request_and_explicit_head_conflicts(case):
    answer = save(case)
    assert save(case, request="REQUEST-1")["outcome"] == "existing"
    error("revision_conflict", lambda: save(case, request="request-2"))
    (case[1] / "요약.md").write_bytes(b"changed intent")
    error("request_conflict", lambda: save(case))
    # Request identity is scoped to one logical Report.
    other = core.save_report(case[0], "daily", files=case[1], entry="요약.md", title="Daily",
                             previous_revision=None, request_id="request-1", origin=HUMAN)
    assert other["outcome"] == "saved"
    assert directory(case, answer).exists()


@pytest.mark.parametrize("where", ["copy", "manifest", "rename_before", "rename_after", "event_before", "event_after", "torn"])
def test_failed_attempts_are_retryable_without_rewriting_completed_bytes(case, monkeypatch, where):
    original_copy = report_app.shutil.copyfile
    original_write = writer.write_json
    original_rename = case[0].platform.files.rename_new
    original_append = writer.Locked.append
    fired = False

    def fault():
        nonlocal fired
        if not fired:
            fired = True
            raise OSError("injected interruption")

    def copy(*args, **kwargs):
        result = original_copy(*args, **kwargs)
        if where == "copy":
            fault()
        return result

    def write(path, *args, **kwargs):
        if Path(path).name == "manifest.json" and where == "manifest":
            fault()
        return original_write(path, *args, **kwargs)

    def rename(*args):
        if where == "rename_before":
            fault()
        original_rename(*args)
        if where == "rename_after":
            fault()

    def append(held, kind, *args, **kwargs):
        if kind == "report_revision":
            if where == "event_before":
                fault()
            if where == "torn" and not fired:
                with open(held.writer.journal, "ab") as fh:
                    fh.write(b'{"schema":"analytics.event/2","seq":3')
                fault()
        result = original_append(held, kind, *args, **kwargs)
        if kind == "report_revision" and where == "event_after":
            fault()
        return result

    monkeypatch.setattr(report_app.shutil, "copyfile", copy)
    monkeypatch.setattr(writer, "write_json", write)
    monkeypatch.setattr(case[0].platform.files, "rename_new", rename)
    monkeypatch.setattr(writer.Locked, "append", append)
    with pytest.raises(core.AnalyticsError, match="injected") as caught:
        save(case)
    assert caught.value.code == "report_io" and caught.value.details["request_id"] == "request-1"
    rows = model(case).report_revisions()
    after_rename = where in ("rename_after", "event_before", "event_after", "torn")
    assert len(rows) == int(after_rename)
    if after_rename:
        assert rows[0]["recording_status"] == ("complete" if where == "event_after" else "incomplete")
        old = (case[0].root / "artifacts/reports/weekly" / rows[0]["revision_id"] / "manifest.json").read_bytes()
    result = save(case)
    assert result["outcome"] == ("existing" if where == "event_after" else "resumed" if after_rename else "saved")
    if after_rename:
        assert (directory(case, result) / "manifest.json").read_bytes() == old
    assert sum(e.kind == "report_revision" for e in model(case).journal.events) == 1
    assert not list((case[0].root / "artifacts/reports/weekly/.staging").iterdir())


def test_incomplete_retry_refuses_a_new_head_and_retains_both(case, monkeypatch):
    original = writer.Locked.append

    def fail(held, kind, *args, **kwargs):
        if kind == "report_revision":
            raise OSError("interrupted completion")
        return original(held, kind, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(writer.Locked, "append", fail)
        with pytest.raises(core.AnalyticsError, match="interrupted"):
            save(case)
    incomplete = model(case).report_revisions()[0]
    second = save(case, request="new-request")
    error("revision_conflict", lambda: save(case))
    assert directory(case, second).exists()
    assert (case[0].root / "artifacts/reports/weekly" / incomplete["revision_id"]).exists()


@pytest.mark.parametrize("damage", ["missing_file", "changed_file", "manifest", "duplicate_event", "request", "digest"])
def test_damaged_completion_or_files_never_allow_a_new_head(case, damage):
    first = save(case)
    path = directory(case, first)
    if damage == "missing_file":
        (path / "content/요약.md").unlink()
    elif damage == "changed_file":
        (path / "content/요약.md").write_bytes(b"tampered")
    elif damage == "manifest":
        (path / "manifest.json").write_bytes(b"{}")
    else:
        lines = case[0].journal.read_bytes().splitlines()
        event = json.loads(lines[-1])
        if damage == "duplicate_event":
            lines.append(lines[-1])
        else:
            if damage == "request":
                event["request_id"] = "other"
            else:
                event["refs"][0]["digest"]["sha256"] = "0" * 64
            lines[-1] = json.dumps(event).encode()
        case[0].journal.write_bytes(b"\n".join(lines) + b"\n")
    error("report_record_invalid", lambda: save(case, request="new", previous=first["report"]["revision_id"]))
    if damage in ("request", "digest", "duplicate_event"):
        assert not any(row["head"] for row in model(case).report_revisions())


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a//b", "a/./b", "CON.txt", "com¹/x", "x.", "x ", "e\u0301.md", "a\\b", "C:x", "a:b", "_analytics/metadata.json"])
def test_invalid_paths_refused_before_staging_or_lock(case, path):
    error("report_path_invalid", lambda: core.save_report(case[0], "weekly", files={path: case[1] / "요약.md"},
          entry=path, title="x", previous_revision=None, origin=HUMAN))
    assert not (case[0].root / "artifacts").exists()
    assert not (case[0].state_dir / "report-attempts").exists()


def test_casefold_duplicates_and_source_changes_refuse_before_publication(case, monkeypatch):
    error("report_path_invalid", lambda: core.save_report(case[0], "weekly", files={"A.md": case[1] / "요약.md", "a.md": case[1] / "첨부.bin"},
          entry="A.md", title="x", previous_revision=None, origin=HUMAN))
    original = report_app.shutil.copyfile

    def edit(src, dst):
        original(src, dst)
        Path(src).write_bytes(b"changed during copy")
    monkeypatch.setattr(report_app.shutil, "copyfile", edit)
    error("report_source_changed", lambda: save(case))
    assert model(case).report_revisions() == []


def test_withdraw_restore_and_bounded_read_preserve_exact_revision(case):
    first = save(case)
    revision = first["report"]["revision_id"]
    before = (directory(case, first) / "manifest.json").read_bytes()
    for expected, new in [("current", "withdrawn"), ("withdrawn", "current")]:
        update = core.update_report(case[0], "weekly", revision, expected=expected, new=new, reason="작성자 판단", origin=HUMAN)
        VALIDATOR.validate(update["event"])
        assert model(case).journal.replay().state_of(relations.report_key("weekly", revision))["state"] == new
    error("state_conflict", lambda: core.update_report(case[0], "weekly", revision, expected="withdrawn", new="current", reason="stale", origin=HUMAN))
    out = core.reports(case[0], "weekly", revision, file="첨부.bin", file_offset=250, file_limit=4)
    assert base64.b64decode(out["file"]["data"]) == bytes(range(250, 254))
    assert out["file"]["next_offset"] == 254
    assert (directory(case, first) / "manifest.json").read_bytes() == before


def test_prepared_cleanup_waits_for_active_attempt_and_never_deletes_saved_revision(case, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = report_app.shutil.copyfile
    outcomes = []

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(report_app.shutil, "copyfile", blocked)
    thread = threading.Thread(target=lambda: outcomes.append(save(case)))
    thread.start()
    assert entered.wait(10)
    stage = next((case[0].root / "artifacts/reports/weekly/.staging").iterdir())
    errors = []

    def clean():
        try:
            core.clean_report_prepared(case[0], "weekly", stage.name, origin=HUMAN)
        except core.AnalyticsError as exc:
            errors.append(exc.code)
    cleaner = threading.Thread(target=clean)
    cleaner.start()
    release.set()
    thread.join(15)
    cleaner.join(15)
    assert not thread.is_alive() and not cleaner.is_alive()
    assert errors == ["report_not_prepared"] and outcomes[0]["outcome"] == "saved"
    assert directory(case, outcomes[0]).is_dir()
    abandoned = stage.parent / "abandoned"
    abandoned.mkdir()
    (abandoned / "partial.bin").write_bytes(b"unfinished")
    core.clean_report_prepared(case[0], "weekly", "abandoned", origin=HUMAN)
    assert not abandoned.exists()


def test_marker_floor_changed_during_preparation_is_refused(case, monkeypatch):
    original = writer.Writer.locked

    @contextlib.contextmanager
    def newer(w, op="write"):
        if op == "save_report":
            marker = json.loads(w.marker.read_bytes())
            marker["min_writer_version"] = "99.0.0"
            writer.write_json(w.marker, marker, platform=w.platform)
        with original(w, op) as held:
            yield held
    monkeypatch.setattr(writer.Writer, "locked", newer)
    error("writer_too_old", lambda: save(case))
    assert not model(case).report_revisions()


@pytest.mark.parametrize("same_request", [False, True])
def test_real_processes_with_same_previous_revision_only_one_saves(case, same_request):
    code = """import json,sys
from aelix_analytics import core
p=core.Project(sys.argv[1])
try:
 r=core.save_report(p,'weekly',files=sys.argv[2],entry='요약.md',title='concurrent',previous_revision=None,request_id=sys.argv[3],origin=core.Origin('human','cli'))
 print(json.dumps(r))
except core.AnalyticsError as e:
 print(json.dumps(e.to_json()))
"""
    children = [subprocess.Popen([sys.executable, "-c", code, str(case[0].root), str(case[1]), "req-" + str(0 if same_request else i)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=kit_env()) for i in range(2)]
    results = []
    for child in children:
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 0, stderr
        results.append(json.loads(stdout))
    assert sum(result.get("outcome") == "saved" for result in results) == 1
    if same_request:
        assert sum(result.get("outcome") == "existing" for result in results) == 1
    else:
        assert sum(result.get("error", {}).get("code") == "revision_conflict" for result in results) == 1
    assert len(model(case).report_revisions()) == 1


def test_process_exit_during_copy_leaves_prepared_for_explicit_cleanup_only(case):
    code = """import os,sys
from aelix_analytics import core,report_app
copy=report_app.shutil.copyfile
def crash(*args,**kw):
 copy(*args,**kw)
 os._exit(17)
report_app.shutil.copyfile=crash
core.save_report(core.Project(sys.argv[1]),'weekly',files=sys.argv[2],entry='요약.md',title='요약 e\\u0301',previous_revision=None,request_id='request-1',origin=core.Origin('human','cli'))
"""
    child = subprocess.run([sys.executable, "-c", code, str(case[0].root), str(case[1])], env=kit_env(), capture_output=True, timeout=30)
    assert child.returncode == 17, child.stderr
    parent = case[0].root / "artifacts/reports/weekly/.staging"
    abandoned = next(parent.iterdir())
    prior = {p.name: p.read_bytes() for p in abandoned.rglob("*") if p.is_file()}
    assert prior and not (abandoned / "manifest.json").exists()
    prepared = next(row for row in core.reports(case[0])["reports"] if row["revision_id"] == abandoned.name)
    assert prepared["recording_status"] == "prepared" and prepared["problem"] == "manifest_unavailable"
    saved = save(case)
    assert saved["outcome"] == "saved"
    assert {p.name: p.read_bytes() for p in abandoned.rglob("*") if p.is_file()} == prior
    core.clean_report_prepared(case[0], "weekly", abandoned.name, origin=HUMAN)
    assert not abandoned.exists() and directory(case, saved).exists()


def test_injected_filesystem_without_exclusive_directory_rename_has_no_fallback(case):
    from platform_doubles import recording_platform

    ports, calls = recording_platform()
    ports.files.renames = False
    project = core.Project(case[0].root, platform=ports)
    error("no_exclusive_rename", lambda: save((project, case[1])))
    assert model(case).report_revisions() == []
    assert any(call[0] == "files.rename_new" for call in calls)
    assert (case[1] / "요약.md").read_bytes()


def test_cli_sdk_equivalence_and_reports_callable_survives_imports(case, monkeypatch):
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(case[0].root))
    monkeypatch.setenv("AELIX_ANALYTICS_ACTOR", HUMAN.actor)
    assert callable(analytics.reports)
    result = analytics.save_report("weekly", files=case[1], entry="요약.md", title="요약 e\u0301", previous_revision=None, request_id="request-1")
    code, cli = kit_cli(case[0].root, "reports", "save", "weekly", "--files", str(case[1]), "--entry", "요약.md", "--title", "요약 e\u0301", "--previous", "none", "--request-id", "request-1")
    assert code == 0 and cli["outcome"] == "existing", cli
    assert cli["report"] == result["report"]
    for _ in range(2):
        assert analytics.reports()["reports"] == core.reports(case[0])["reports"]
    assert callable(analytics.reports)


def test_links_are_not_preserved_or_followed_on_read(case):
    linked = case[1] / "link.md"
    try:
        linked.symlink_to(case[1] / "요약.md")
    except OSError as exc:
        pytest.skip(f"This runner cannot create symlinks: {exc}")
    error("report_source_invalid", lambda: save(case))
    linked.unlink()
    first = save(case)
    content = directory(case, first) / "content/요약.md"
    content.unlink()
    content.symlink_to(case[1] / "요약.md")
    assert model(case).report_revisions()[0]["content"] == "missing"
    error("report_path_invalid", lambda: core.reports(case[0], "weekly", first["report"]["revision_id"], file="요약.md"))


def test_metadata_reads_do_not_hash_report_body_until_explicitly_requested(case, monkeypatch):
    first = save(case)
    original = report_app._sha
    calls = []

    def tracked(path):
        calls.append(Path(path))
        return original(path)
    monkeypatch.setattr(report_app, "_sha", tracked)
    listing = core.reports(case[0])
    assert listing["reports"][0]["content"] == "not_checked"
    assert not any("content" in p.parts for p in calls)
    verified = core.reports(case[0], "weekly", first["report"]["revision_id"], verify_files=True)
    assert verified["reports"][0]["content"] == "resolved"
    assert any("content" in p.parts for p in calls)


def test_all_parent_path_prefixes_keep_one_spelling_before_any_effect(case):
    source = case[1] / "요약.md"
    error("report_path_invalid", lambda: core.save_report(case[0], "weekly", files={"A/x.md": source, "a/y.md": source},
          entry="A/x.md", title="x", previous_revision=None, origin=HUMAN))
    assert not (case[0].root / "artifacts").exists()
    good = core.save_report(case[0], "weekly", files={"A/x.md": source, "A/y.md": source},
                            entry="A/x.md", title="x", previous_revision=None, origin=HUMAN)
    manifest = json.loads((directory(case, good) / "manifest.json").read_bytes())
    manifest["files"][1]["path"] = "a/y.md"
    manifest["request_fingerprint"] = relations.request_fingerprint(manifest)
    assert relations.report_manifest_problem(manifest) == "invalid_paths"


@pytest.mark.parametrize("ref", [
    {"type": "event", "relation": "cites", "basis": "declared", "version_binding": "author"},
    {"type": "event", "event_id": [], "relation": "cites", "basis": "declared", "version_binding": "author"},
    {"type": [], "relation": "cites", "basis": "declared", "version_binding": "author"},
    {"type": "report_revision", "report_id": [], "revision_id": "r1", "digest": {"sha256": "0" * 64},
     "relation": "cites", "basis": "declared", "version_binding": "author"},
])
def test_authored_basis_reference_shape_uses_common_validation_before_staging(case, ref):
    error("report_input_invalid", lambda: save(case, based_on={"as_of_seq": None, "context_version": None, "refs": [ref]}))
    assert not (case[0].root / "artifacts").exists()
    assert writer.reference_problem(ref) == relations.reference_problem(ref)


@pytest.mark.parametrize("field,value", [("report_id", []), ("revision_id", {}), ("revision_id", [])])
def test_malformed_completion_identity_is_read_conservatively_without_typeerror(case, field, value):
    first = save(case)
    ref = {"type": "report_revision", "report_id": "weekly", "revision_id": first["report"]["revision_id"],
           "relation": "about", "basis": "observed", "version_binding": "author"}
    ref[field] = value
    bad = {"schema": relations.EVENT_V2, "id": "e99", "seq": 99, "kind": "report_revision", "refs": [ref]}
    writer.append_line(case[0].journal, json.dumps(bad).encode())
    assert core.reports(case[0])["reports"][0]["head"]
    if field == "revision_id":
        error("report_record_invalid", lambda: save(case, request="new", previous=first["report"]["revision_id"]))


def test_update_validates_request_before_lock_and_generates_common_response_id(case):
    first = save(case)
    revision = first["report"]["revision_id"]
    before = case[0].journal.read_bytes()
    bad = core.Origin("human", "sdk", request_id="bad / id")
    error("name_not_portable", lambda: core.update_report(case[0], "weekly", revision, expected="current", new="withdrawn", reason="review", origin=bad))
    assert case[0].journal.read_bytes() == before
    result = core.update_report(case[0], "weekly", revision, expected="current", new="withdrawn", reason="review", origin=HUMAN)
    writer.check_request_id(result["request_id"])
    assert result["request_id"] and "request_id" not in result["event"]  # common generated-response-ID rule
    VALIDATOR.validate(result["event"])
