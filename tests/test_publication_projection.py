"""PUBLICATION-BUILD C1-C13, model-level evidence; no package/build/Report acceptance yet."""
import copy
import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from conftest import kit_env

from aelix_analytics import core, relations, runner, writer
from aelix_analytics.publishing.field_policy import CLASSIFIED_FIELDS, unclassified_fields
from aelix_analytics.publishing.model import (
    ContentBasis,
    CopyEntry,
    Field,
    ItemKey,
    PublicationError,
    Reference,
    SnapshotBasis,
    Text,
)
from aelix_analytics.publishing.projection import (
    ReadProjection,
    ShareRequest,
    ShareSnapshot,
    SnapshotFile,
    SnapshotItem,
    freeze,
    project,
    validate_projection,
)
from aelix_analytics.publishing.scope import PROFILES, scope_closure
from aelix_analytics.publishing.snapshot import capture_snapshot, content_basis

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = core.Origin("human", "sdk")
CANARY = "EXCLUDED_CANARY_6c42"
HASH = "a" * 64
P = ItemKey("project", "project")
R1, R2 = ItemKey("run", "r1"), ItemKey("run", "r2")
V1, V2 = ItemKey("visualization", "r1/v"), ItemKey("visualization", "r2/v")
C = ItemKey("code", "opaque-code")
E = ItemKey("event", "e4")
HIDDEN = ItemKey("event", CANARY)
MISSING_DATA = ItemKey("dataset", "opaque-missing")


def synthetic():
    f = SnapshotFile("runs/r2/visualizations/v.png", "figure", ".png", HASH, HASH, 8, "png")
    code = SnapshotFile("runs/r2/code/main.py", "code_snapshot", ".py", HASH, HASH, 9, "python")
    items = (
        SnapshotItem(P, "project", freeze({"title": "Public title", "project_id": CANARY}), root=True),
        SnapshotItem(R1, "run", freeze({"id": "r1", "title": "Old", "status": "ok", "recording_status": "complete"})),
        SnapshotItem(R2, "run", freeze({"id": "r2", "title": "New", "status": "ok", "recording_status": "complete",
            "env": {"python": "3.12", "packages": {"polars": "1"}, "executable": CANARY}, "stdout": CANARY,
            "script": {"path": "scripts/main.py"}, "warnings": [CANARY], "warning_count": 1,
            "inputs": [{"path": "/home/secret/file.csv", "external": True, "sha256": HASH, "private": CANARY}]}),
            attachments=(C,)),
        SnapshotItem(C, "code", freeze({"path": "scripts/main.py", "sha256": HASH}), files=(code,)),
        SnapshotItem(V1, "visualization", freeze({"title": "Historical", "visualization_id": "v", "kind": "matplotlib"}),
            attachments=(R1,), files=(replace(f, source="runs/r1/v.png"),)),
        SnapshotItem(V2, "visualization", freeze({"title": "Latest", "visualization_id": "v", "kind": "matplotlib"}),
            root=True, attachments=(R2,), files=(f,)),
        SnapshotItem(E, "finding", freeze({"id": "e4", "text": "Historical evidence", "state": "current", "actor_role": "human"}),
            seq=4, references=(Reference("cites", "author", "declared", "resolved", None, "visualization", V1, True),
                Reference("cites", "author", "declared", "missing", "file_absent", "dataset", MISSING_DATA),
                Reference("cites", "none", "unverified", "version_unbound", None, "visualization", None,
                          candidates=(V1, ItemKey("visualization", CANARY))))),
        SnapshotItem(HIDDEN, "note", freeze({"id": CANARY, "text": CANARY, "state": "current"})),
        SnapshotItem(MISSING_DATA, "dataset", freeze({"name": CANARY})),
        SnapshotItem(ItemKey("run", "running"), "run", freeze({"title": CANARY}), root=True, complete=False),
    )
    return ShareSnapshot(SnapshotBasis(4, ContentBasis(4, "2026-10-01T00:00:00+00:00", None)), items)


@pytest.mark.parametrize("profile", ["read", "evidence"])
def test_contract_sources_scope_history_states_and_model_canaries(profile):
    s, request = synthetic(), ShareRequest(exclude=frozenset({HIDDEN}))
    result = project(s, request, profile=profile)
    assert set(result.ledger.included) <= scope_closure(s, request, PROFILES[profile])
    assert CANARY not in json.dumps(asdict(result.model), ensure_ascii=False)
    finding = next(i for i in result.model.items if i.key == E)
    assert finding.references[0].target == V1 and finding.references[0].confirmed
    assert finding.references[1].state == "missing" and finding.references[1].reason == "file_absent"
    assert finding.references[1].target is None
    assert finding.references[2].state == "version_unbound"
    assert finding.references[2].candidates == (V1,) and finding.references[2].omitted_candidates == 1
    assert V2 in result.ledger.included and V1 in result.ledger.included
    assert result == project(s, request, profile=profile)


def test_evidence_superset_and_attachment_closure_does_not_follow_producers():
    s, req = synthetic(), ShareRequest(exclude=frozenset({HIDDEN}))
    read, evidence = project(s, req), project(s, req, profile="evidence")
    assert set(read.ledger.included) <= set(evidence.ledger.included)
    assert C not in read.ledger.included and C in evidence.ledger.included
    assert {f.role for f in evidence.copy_plan} == {"figure", "code_snapshot"}
    assert all(f.role == "figure" for f in read.copy_plan)
    assert "/home/secret" not in json.dumps(asdict(evidence.model))
    excluded = project(s, replace(req, exclude=frozenset({HIDDEN, V2})), profile="evidence")
    assert R2 not in excluded.ledger.included and C not in excluded.ledger.included


@pytest.mark.parametrize("state", ["content_changed", "missing", "unknown", "unsupported", "ambiguous"])
def test_outside_reference_identity_is_removed_for_every_original_state(state):
    s = synthetic()
    finding = next(i for i in s.items if i.key == E)
    ref = Reference("cites", "author", "declared", state, "reason", "event", HIDDEN)
    s = replace(s, items=tuple(replace(i, references=(ref,)) if i.key == E else i for i in s.items))
    out = project(s, ShareRequest(exclude=frozenset({HIDDEN})))
    actual = next(i for i in out.model.items if i.key == finding.key).references[0]
    assert actual.state == state and actual.target is None
    assert CANARY not in json.dumps(asdict(out.model))


@pytest.mark.parametrize("scope", ["reports", "collection", "unknown"])
def test_unsupported_scope_is_rejected_without_fallback(scope):
    with pytest.raises(PublicationError) as error:
        project(synthetic(), ShareRequest(scope=scope))
    assert error.value.code == ("scope_unknown" if scope == "unknown" else "scope_unsupported")


def test_unknown_profile_and_basis_context_exclusion_are_explicit():
    with pytest.raises(PublicationError, match="unknown publication profile"):
        project(synthetic(), profile="private")
    with pytest.raises(PublicationError, match="cannot be excluded"):
        project(synthetic(), ShareRequest(exclude=frozenset({P})))


def test_injected_strategy_is_used_and_adversarial_model_ledger_copy_are_rejected(monkeypatch):
    s, request = synthetic(), ShareRequest(exclude=frozenset({HIDDEN}))
    expected = project(s, request)
    calls = []

    class Recording:
        id, rules_version = "recording", 1

        def project(self, snapshot, passed):
            calls.append(passed)
            return expected

    monkeypatch.setattr(ReadProjection, "project", lambda *a: pytest.fail("default strategy used"))
    assert project(s, request, implementation=Recording()) == expected and calls == [request]
    bad_ledger = replace(expected.ledger, included=(*expected.ledger.included, HIDDEN))
    with pytest.raises(PublicationError, match="projection_out_of_scope"):
        validate_projection(s, request, PROFILES["read"], replace(expected, ledger=bad_ledger))
    bad_field = Field("text", Text(CANARY, "user", (E, "text")))
    bad_model = replace(expected.model, items=tuple(replace(i, fields=(bad_field,)) if i.key == E else i for i in expected.model.items))
    with pytest.raises(PublicationError, match="not from"):
        validate_projection(s, request, PROFILES["read"], replace(expected, model=bad_model))
    bad_copy = CopyEntry(E, ".env", "stolen.txt", "code_snapshot", None, HASH, 1)
    with pytest.raises(PublicationError, match="copy-plan"):
        validate_projection(s, request, PROFILES["read"], replace(expected, copy_plan=(bad_copy,)))


def test_projection_performs_no_io_after_snapshot_even_through_other_open_apis():
    script = """
import sys, builtins, io, os
from pathlib import Path
from aelix_analytics.publishing.projection import *
from aelix_analytics.publishing.model import *
s=ShareSnapshot(SnapshotBasis(0,ContentBasis(0,None,None)),
 (SnapshotItem(ItemKey('project','project'),'project',freeze({'title':'t'}),root=True),))
def audit(event,args):
 if event == 'open': raise AssertionError('projection opened a file')
sys.addaudithook(audit)
for op in (lambda: builtins.open('negative-control'),lambda: io.open('negative-control'),
           lambda: os.open('negative-control',os.O_RDONLY),lambda: Path('negative-control').open()):
 try: op()
 except AssertionError: pass
 else: raise AssertionError('open-audit negative control failed')
for profile in ('read','evidence'): project(s,profile=profile)
print('pure')
"""
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8", env=kit_env())
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "pure"


def event(seq, kind="finding", **kw):
    return {"schema": "analytics.event/2", "seq": seq, "id": f"e{seq}", "recorded_at": "2026-10-01T00:00:00+00:00",
            "kind": kind, "actor": "human", "text": "t", "refs": [], **kw}


def publication(seq):
    pid = "pub-" + "0" * 26
    return event(seq, "publication", details={"publication_id": pid, "format": "analytics.publication/1",
        "package_sha256": HASH, "content_sha256": HASH, "profile": {"id": "read", "rules_version": 1}, "scope": "project",
        "basis": {"as_of_seq": seq-1, "content_seq": 1, "context_version": None},
        "analysis_state": {"value": "in_progress", "basis": "declared"}, "exclusions": [],
        "location": f".analytics/publications/{pid}", "checks": {"format": 1, "result": "pass", "inspection": {"inspected": 0, "pinned_scanned": 0, "not_inspectable": 0}}})


def journal(values):
    return relations.Journal(b"\n".join(json.dumps(v).encode() for v in values)+b"\n")


def test_content_seq_closed_set_empty_unknown_unsupported_recovered_and_time():
    basis, _ = content_basis(journal([]), validate_reference=writer.reference_problem)
    assert basis == SnapshotBasis(0, ContentBasis(0, None, None))
    normal = [event(1), publication(2), event(3, "publish_receipt", details={"publication_id": "pub-"+"0"*26})]
    basis, excluded = content_basis(journal(normal), validate_reference=writer.reference_problem)
    assert basis.as_of_seq == 3 and basis.content.content_seq == 1 and excluded == {2, 3}
    for changed in (event(4, "unknown"), event(4, "publication", schema="analytics.event/99"), event(4, "publication")):
        b, _ = content_basis(journal([*normal, changed]), validate_reference=writer.reference_problem)
        assert b.content.content_seq == 4
    recovered = relations.Journal(journal(normal).data + b'broken'+json.dumps(publication(4)).encode()+b"\n")
    b, _ = content_basis(recovered, validate_reference=writer.reference_problem)
    assert b.content.content_seq == 4 and recovered.recovered == [4]
    b, _ = content_basis(journal([event(1, recorded_at=None)]), validate_reference=writer.reference_problem)
    assert b.content.content_recorded_at is None
    b, _ = content_basis(journal([event(1), event(1, "note")]), validate_reference=writer.reference_problem)
    assert b.content.content_recorded_at is None


def test_field_inventory_is_exhaustive_and_new_fields_require_classification():
    schema = json.loads((ROOT/"docs/schemas/analytics-records.schema.json").read_text(encoding="utf-8"))
    assert unclassified_fields(schema) == {}
    for family, classified in CLASSIFIED_FIELDS.items():
        assert set(classified) == set(schema["$defs"][family]["properties"])
    future = copy.deepcopy(schema)
    future["$defs"]["run_2"]["properties"][CANARY] = {"type": "string"}
    assert unclassified_fields(future) == {"run_2": [CANARY]}


def test_real_snapshot_freezes_bytes_and_repeat_publication_changes_identity_basis_only(tmp_path):
    p = core.init_project(tmp_path/"p", title="Public", origin=ORIGIN)
    core.record(p, "finding", "Fact", origin=ORIGIN)
    first = capture_snapshot(p)
    a = project(first)
    seq = core.read_model(p).as_of_seq + 1
    with p.journal.open("ab") as f:
        f.write(json.dumps(publication(seq)).encode()+b"\n")
    b = project(capture_snapshot(p))
    assert b.model == a.model and b.copy_plan == a.copy_plan
    assert b.basis.as_of_seq > a.basis.as_of_seq
    # Working CONTEXT changes after capture do not leak into the frozen model.
    p.context.write_text(CANARY, encoding="utf-8")
    assert project(first) == a
    assert CANARY not in json.dumps(asdict(project(first).model))


def test_real_run_snapshot_preserves_old_version_and_hash_checks(tmp_path):
    p = core.init_project(tmp_path/"p", origin=ORIGIN)
    script = p.scripts/"plot.py"
    script.write_text("import matplotlib.pyplot as plt\nplt.plot([1,2]);plt.show()\n", encoding="utf-8")
    result = runner.execute(p, script="scripts/plot.py", title="Figure", origin=ORIGIN)
    snapshot = capture_snapshot(p)
    out = project(snapshot, profile="evidence")
    assert any(i.key.category == "visualization" for i in out.model.items)
    assert {e.role for e in out.copy_plan} == {"figure", "code_snapshot"}
    original = result["run"]["script"]["snapshot"]
    (p.root/original).write_text(CANARY, encoding="utf-8")
    assert project(snapshot, profile="evidence") == out
    newer = project(capture_snapshot(p), profile="evidence")
    assert not any(e.role == "code_snapshot" for e in newer.copy_plan)
    assert any(o.reason == "content_changed" for o in newer.model.not_included)


def test_nested_field_coverage_requires_a_decision_and_legacy_fixture_fields_are_accounted_for():
    from aelix_analytics.publishing.field_policy import (
        LEGACY_FIELDS,
        PERSISTED_FIELD_PATHS,
        RECORD_FAMILIES,
        schema_field_paths,
    )
    schema = json.loads((ROOT/"docs/schemas/analytics-records.schema.json").read_text(encoding="utf-8"))
    assert set(schema["properties"]["schema"]["enum"]) == set(RECORD_FAMILIES)
    for family, paths in PERSISTED_FIELD_PATHS.items():
        assert schema_field_paths(schema, family) == paths, family
    future = copy.deepcopy(schema)
    future["$defs"]["run_2"]["properties"]["env"]["properties"][CANARY] = {"type": "string"}
    assert schema_field_paths(future, "run_2") - PERSISTED_FIELD_PATHS["run_2"] == {"env."+CANARY}
    for path in (ROOT/"tests/fixtures/records").glob("**/run.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") == "lab.run/1":
            assert set(value) <= LEGACY_FIELDS["run"], path


@pytest.mark.parametrize("profile", ["read", "evidence"])
def test_negative_controls_refuse_raw_display_text_rewritten_judgment_and_wrong_basis(profile):
    s, req = synthetic(), ShareRequest(exclude=frozenset({HIDDEN}))
    expected = project(s, req, profile=profile)
    finding = next(i for i in expected.model.items if i.key == E)
    changed = replace(finding, references=(replace(finding.references[0], confirmed=False), *finding.references[1:]))
    model = replace(expected.model, items=tuple(changed if i.key == E else i for i in expected.model.items))
    with pytest.raises(PublicationError, match="reference judgment changed"):
        validate_projection(s, req, PROFILES[profile], replace(expected, model=model))
    raw = replace(finding, fields=(Field("text", CANARY),))
    model = replace(expected.model, items=tuple(raw if i.key == E else i for i in expected.model.items))
    with pytest.raises(PublicationError):
        validate_projection(s, req, PROFILES[profile], replace(expected, model=model))
    with pytest.raises(PublicationError, match="basis changed"):
        validate_projection(s, req, PROFILES[profile], replace(expected, basis=SnapshotBasis(999, s.basis.content)))


def test_legacy_no_hash_figure_is_distinct_from_code_with_no_recorded_hash():
    s = synthetic()
    items = tuple(replace(i, files=tuple(replace(f, recorded_sha256=None) for f in i.files)) for i in s.items)
    out = project(replace(s, items=items), ShareRequest(exclude=frozenset({HIDDEN})), profile="evidence")
    assert out.copy_plan and all(e.role == "figure" for e in out.copy_plan)
    assert all(f.verified == "no_recorded_hash" for i in out.model.items for f in i.files)
    assert any(o.reason == "no_digest" for o in out.model.not_included)


def test_snapshot_uses_injected_reader_and_refuses_unstable_records(tmp_path):
    p = core.init_project(tmp_path/"p", origin=ORIGIN)
    calls = []

    def reader(project, *, cache):
        calls.append(cache)
        result = core.read_model(project, cache=cache)
        result.updating = True
        return result

    with pytest.raises(PublicationError, match="records changed"):
        capture_snapshot(p, reader=reader)
    assert len(calls) == 1 and callable(calls[0].sha)


def test_external_paths_are_never_exported_even_when_an_old_record_omits_external_flag():
    s = synthetic()
    absolute = r"C:\Users\private\EXCLUDED_CANARY_6c42.py"
    items = tuple(replace(i, values=freeze({"path": absolute, "sha256": HASH})) if i.key == C else i for i in s.items)
    out = project(replace(s, items=items), ShareRequest(exclude=frozenset({HIDDEN})), profile="evidence")
    assert absolute not in json.dumps(asdict(out.model)) and CANARY not in json.dumps(asdict(out.model))
    code = next(i for i in out.model.items if i.key == C)
    assert next(f.value for f in code.fields if f.name == "path") == Text("Path not included", "kit")


def test_snapshot_digest_provider_is_shared_by_reference_judgments_and_copy_plan(tmp_path, monkeypatch):
    p = core.init_project(tmp_path/"p", origin=ORIGIN)
    script = p.scripts/"p.py"
    script.write_text("import matplotlib.pyplot as plt\nplt.plot([1]);plt.show()\n", encoding="utf-8")
    run = runner.execute(p, script="scripts/p.py", title="f", origin=ORIGIN)["run"]
    vis = run["visualizations"][0]
    core.record(p, "finding", "cited", refs=[core.kit_reference("visualization_version", run_id=run["id"],
        visualization_id=vis["visualization_id"], digest={"sha256": vis["sha256"]})], origin=ORIGIN)
    original, calls = core.HashCache.sha, {}

    def sha(cache, path):
        key = p.canonical(path)
        calls[key] = calls.get(key, 0) + 1
        return original(cache, path)

    monkeypatch.setattr(core.HashCache, "sha", sha)
    snapshot = capture_snapshot(p)
    out = project(snapshot)
    assert out.copy_plan and max(calls.values()) == 1
    assert all(entry.current_sha256 == vis["sha256"] for entry in out.copy_plan)


def test_snapshot_does_not_write_project_files_or_use_working_context(tmp_path):
    p = core.init_project(tmp_path/"p", title="Public", origin=ORIGIN)
    p.context.write_text(CANARY, encoding="utf-8")
    before = {f.relative_to(p.root).as_posix(): f.read_bytes() for f in p.root.rglob("*") if f.is_file()}
    snapshot = capture_snapshot(p)
    for profile in ("read", "evidence"):
        assert CANARY not in json.dumps(asdict(project(snapshot, profile=profile).model))
    after = {f.relative_to(p.root).as_posix(): f.read_bytes() for f in p.root.rglob("*") if f.is_file()}
    assert after == before


def test_mutated_stored_context_is_refused_instead_of_using_new_bytes(tmp_path):
    p = core.init_project(tmp_path/"p", origin=ORIGIN)
    basis, _ = content_basis(core.read_model(p).journal, validate_reference=writer.reference_problem)
    path = relations.context_file(p.root, basis.content.context_version)
    path.write_text(CANARY, encoding="utf-8")
    with pytest.raises(PublicationError, match="CONTEXT bytes changed"):
        capture_snapshot(p)


def test_authored_text_keeps_its_source_for_the_later_independent_leak_checker():
    s = synthetic()
    text = "An included title mentioning " + CANARY
    s = replace(s, items=tuple(replace(i, values=freeze({"title": text})) if i.key == P else i for i in s.items))
    result = project(s, ShareRequest(exclude=frozenset({HIDDEN})))
    assert result.model.title == Text(text, "user", (P, "title"))
    # PR1 is an allow-list projection, not the PR2 content scanner or a publishable package.
    # User bytes are not silently masked; Text.source lets that checker reject the exact field.


def test_evidence_has_exact_historical_contexts_while_read_uses_the_basis_version(tmp_path):
    p = core.init_project(tmp_path/"p", origin=ORIGIN)
    p.context.write_text("Current context", encoding="utf-8")
    core.sync_context(p, origin=ORIGIN)
    snapshot = capture_snapshot(p)
    read, evidence = project(snapshot), project(snapshot, profile="evidence")
    versions = [i for i in evidence.model.items if i.key.category == "context"]
    assert len(versions) == 2
    current = [i for i in read.model.items if i.key.category == "context"]
    assert len(current) == 1 and current[0].key.identity == snapshot.basis.content.context_version
    assert any(f.name == "text" and f.value.value == "Current context" for f in current[0].fields)


@pytest.mark.parametrize("state", ["missing", "unsupported"])
def test_missing_and_unsupported_references_have_no_target_link_even_if_an_item_is_independently_included(state):
    s = synthetic()
    items = tuple(replace(i, references=(Reference("cites", "author", "declared", state, "digest_mismatch", "visualization", V2),))
                  if i.key == E else i for i in s.items)
    out = project(replace(s, items=items))
    assert V2 in out.ledger.included
    ref = next(i for i in out.model.items if i.key == E).references[0]
    assert ref.state == state and ref.target is None and not ref.confirmed


def test_latest_successful_script_run_uses_reader_instant_order_not_iso_lexical_order(tmp_path):
    p = core.init_project(tmp_path/"p", origin=ORIGIN)
    script = p.scripts/"p.py"
    script.write_text("import matplotlib.pyplot as plt\nplt.plot([1]);plt.show()\n", encoding="utf-8")
    runs = [runner.execute(p, script="scripts/p.py", title="f", origin=ORIGIN)["run"] for _ in range(2)]
    for run, stamp in zip(runs, ("2026-10-01T09:00:00+09:00", "2026-10-01T02:00:00+00:00"), strict=True):
        run["started_at"] = stamp
        core.writer_for(p, ORIGIN).write_run(p.runs/run["id"], run)
    out = project(capture_snapshot(p))
    vis = [i.key.identity for i in out.model.items if i.key.category == "visualization"]
    assert len(vis) == 1 and vis[0].startswith(runs[1]["id"]+'/')


def test_content_basis_uses_injected_common_reference_validation_and_retains_refusal():
    value = publication(2)
    value["refs"] = [{"type": "context_version", "version": "context.md", "digest": {"sha256": HASH},
                      "relation": "includes", "basis": "observed", "version_binding": "author"}]
    seen = []

    def refuse(ref):
        seen.append(ref)
        return "injected common reference problem"

    basis, excluded = content_basis(journal([event(1), value]), validate_reference=refuse)
    assert seen and basis.content.content_seq == 2 and not excluded
    basis, excluded = content_basis(journal([event(1), value]), validate_reference=writer.reference_problem)
    assert basis.content.content_seq == 1 and excluded == {2}
    value["refs"][0]["relation"] = "supersedes"
    basis, _ = content_basis(journal([event(1), value]), validate_reference=writer.reference_problem)
    assert basis.content.content_seq == 2


def test_partial_structures_do_not_copy_column_statistics_or_unknown_metric_parent_fields():
    s = synthetic()
    dataset = SnapshotItem(ItemKey("dataset", "d"), "dataset", freeze({"name": "Public", "columns": [
        {"name": "x", "type": "int", "min": CANARY, "preview": [CANARY]}]}), root=True)
    s = replace(s, items=(*s.items, dataset))
    out = project(s, ShareRequest(exclude=frozenset({HIDDEN})), profile="evidence")
    assert CANARY not in json.dumps(asdict(out.model))
    d = next(i for i in out.model.items if i.key == dataset.key)
    columns = next(f.value for f in d.fields if f.name == "columns")
    assert {k.value for k, _ in columns[0].entries} == {"name", "type"}


def test_observation_does_not_reveal_an_excluded_definition_identity_through_details(tmp_path):
    p = core.init_project(tmp_path/"p", origin=ORIGIN)
    definition = core.record(p, "metric_definition", "Definition", details={"contract": "metric_definition/1",
        "metric_id": CANARY, "name": CANARY, "status": "draft", "definition": {}, "unresolved": [],
        "derived_from": [], "change_reason": "Initial"}, origin=ORIGIN).event
    script = p.scripts/"p.py"
    script.write_text("print(1)\n", encoding="utf-8")
    run = runner.execute(p, script="scripts/p.py", title="Calculation", origin=ORIGIN)["run"]
    core.record(p, "metric_observation", "Measurement", details={"contract": "metric_observation/1",
        "metric_id": CANARY, "definition_event_id": definition["id"], "run_id": run["id"], "scope": {},
        "result": 1, "change_reason": "Measured"}, origin=ORIGIN)
    snapshot = capture_snapshot(p)
    request = ShareRequest(exclude=frozenset({ItemKey("event", definition["id"])}))
    for profile in ("read", "evidence"):
        out = project(snapshot, request, profile=profile)
        assert CANARY not in json.dumps(asdict(out.model))
        observation = next(i for i in out.model.items if i.kind == "metric_observation")
        assert all(f.name not in ("details.metric_id", "details.definition_event_id") for f in observation.fields)


def test_current_common_validator_rejects_a_delivery_ref_missing_required_version():
    value = publication(2)
    value["refs"] = [{"type": "context_version", "relation": "includes", "basis": "observed",
                      "version_binding": "author", "digest": {"sha256": HASH}}]
    assert relations.reference_problem(value["refs"][0]) is not None
    basis, excluded = content_basis(journal([event(1), value]), validate_reference=relations.reference_problem)
    assert basis.content.content_seq == 2 and not excluded


def test_adversarial_technical_metadata_cannot_carry_unapproved_text():
    from aelix_analytics.publishing.model import Omission

    s, req = synthetic(), ShareRequest(exclude=frozenset({HIDDEN}))
    out = project(s, req)
    bad = (Omission(CANARY, "profile", 1),)
    malicious = replace(out, model=replace(out.model, not_included=bad), ledger=replace(out.ledger, not_included=bad))
    with pytest.raises(PublicationError, match="unregistered omission"):
        validate_projection(s, req, PROFILES["read"], malicious)


def test_malformed_scalar_record_field_does_not_become_a_container_for_unapproved_content(tmp_path):
    p = core.init_project(tmp_path/"p", title="Public", origin=ORIGIN)
    raw = event(3, id={"unexpected": CANARY})
    with p.journal.open("ab") as file:
        file.write(json.dumps(raw).encode()+b"\n")
    out = project(capture_snapshot(p))
    assert CANARY not in json.dumps(asdict(out.model))
    assert any(i.kind == "finding" for i in out.model.items), "the valid original text remains readable"
