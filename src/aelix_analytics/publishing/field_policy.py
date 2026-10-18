"""Explicit persisted field inventory. New schema fields require a sharing-policy decision."""
# Include/partial mean a named allow-list field or a derived typed view, never copying the object.
CLASSIFIED_FIELDS = {
    "project_2": {
        "title": "include", "schema": "internal", "project_id": "exclude", "created_at": "exclude",
        "min_writer_version": "internal", "kit_version": "internal", "asset_suggestions": "exclude", "migration": "exclude"},
    "event_2": {
        "schema": "internal", "seq": "internal", "id": "include", "recorded_at": "include", "kind": "label",
        "actor": "role", "actor_basis": "exclude", "means": "exclude", "session": "exclude", "request_id": "exclude",
        "text": "include", "refs": "references", "details": "partial"},
    "run_2": {
        "schema": "internal", "id": "include", "title": "include", "status": "label", "pid": "exclude",
        "started_at": "include", "ended_at": "include", "duration_s": "exclude", "actor": "role",
        "actor_basis": "exclude", "means": "exclude", "session": "exclude", "request_id": "exclude",
        "rerun_of": "references", "script": "partial", "modules": "copy", "inputs": "partial", "outputs": "partial",
        "visualizations": "view", "error": "partial", "finalize_errors": "count", "stdout": "exclude",
        "stderr": "exclude", "env": "partial", "reproduction": "partial", "warnings": "count"},
    "visualization_2": {
        "visualization_id": "include", "kind": "label", "title": "include", "caption": "include", "via": "exclude",
        "files": "copy", "sha256": "copy", "data": "copy", "data_rows": "evidence", "data_columns": "evidence",
        "data_sha256": "copy", "line": "evidence"},
    "dataset_1": {
        "schema": "internal", "name": "include", "path": "evidence", "sha256": "evidence", "bytes": "evidence",
        "rows": "evidence", "columns": "evidence", "description": "evidence", "imported_at": "exclude", "source": "exclude"},
    "report_revision_1": {k: "deferred_PR4" for k in (
        "schema", "report_id", "revision_id", "previous_revision", "request_id", "request_fingerprint", "created_at",
        "actor", "title", "entry", "files", "based_on", "summary", "scope", "purpose", "change_reason",
        "generation", "content_status", "metadata_file", "collection")},
    "metric_definition_details": {
        "contract": "internal", "metric_id": "include", "name": "include", "status": "include", "definition": "include",
        "unresolved": "include", "change_reason": "include", "previous_definition": "references", "derived_from": "references"},
    "analysis_focus_details": {
        "contract": "internal", "goal": "include", "question": "include", "activity": "include", "next_check": "include",
        "change_reason": "include", "metrics": "references", "unresolved": "include", "previous_focus": "references"},
    "metric_observation_details": {
        "contract": "internal", "metric_id": "include", "definition_event_id": "references", "run_id": "references",
        "scope": "include", "result": "include", "change_reason": "include", "recomputed_from": "references", "result_ref": "references"},
}
# The explicit /1 names in CONTRACTS C1-C4. New /1 fields do not get an automatic pass either.
LEGACY_FIELDS = {
    "event": frozenset({"schema", "seq", "id", "ts", "kind", "actor", "session", "text", "refs"}),
    "run": frozenset({"schema", "id", "title", "status", "pid", "started_at", "ended_at", "duration_s", "actor", "session",
                      "script", "modules", "inputs", "outputs", "charts", "error", "finalize_errors", "stdout", "stderr",
                      "env", "reproduction", "warnings", "rerun_of"}),
}


def unclassified_fields(schema):
    return {family: sorted(set(schema.get("$defs", {}).get(family, {}).get("properties", {})) - set(fields))
            for family, fields in CLASSIFIED_FIELDS.items()
            if set(schema.get("$defs", {}).get(family, {}).get("properties", {})) - set(fields)}

CLASSIFIED_FIELDS["reference"] = {k: "typed_reference_view" for k in ('type',
 'relation',
 'basis',
 'version_binding',
 'digest',
 'token',
 'key',
 'event_id',
 'run_id',
 'visualization_id',
 'role',
 'path',
 'version',
 'metric_id',
 'proposal_id',
 'asset_id',
 'report_id',
 'revision_id',
 'publication_id')}


def schema_field_paths(schema, family):
    """Known named properties, including conditional definitions and array members."""
    out = set()

    def visit(node, prefix, refs):
        if not isinstance(node, dict):
            return
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/") and reference not in refs:
            visit(schema["$defs"][reference.rsplit("/", 1)[1]], prefix, refs | {reference})
        for name, value in node.get("properties", {}).items():
            path = f"{prefix}.{name}" if prefix else name
            out.add(path)
            visit(value, path, refs)
        if isinstance(node.get("items"), dict):
            visit(node["items"], prefix + "[]", refs)
        for group in ("allOf", "anyOf", "oneOf"):
            for value in node.get(group, ()):
                visit(value, prefix, refs)
        for branch in ("if", "then", "else"):
            visit(node.get(branch), prefix, refs)

    visit(schema["$defs"][family], "", frozenset())
    return frozenset(out)

# Reviewed named property paths: additions (including nested/conditional ones) require classification.
PERSISTED_FIELD_PATHS = {
    'project_2': frozenset((
        'asset_suggestions asset_suggestions.cooldown_hours asset_suggestions.measurement_targets '
        'asset_suggestions.measurement_targets.input_tokens_per_review '
        'asset_suggestions.measurement_targets.output_tokens_per_review asset_suggestions.min_score '
        'asset_suggestions.mode created_at kit_version migration migration.id migration.report '
        'migration.report_sha256 min_writer_version project_id schema title '
    ).split()),
    'event_2': frozenset((
        'actor actor_basis details details.activity details.artifact_digest details.artifact_kind '
        'details.as_of_seq details.asset_id details.authoring_cost details.boundary '
        'details.candidate_event_id details.change_reason details.check_code details.check_code.asset_id '
        'details.check_code.basis details.check_code.digest details.check_code.digest.sha256 '
        'details.check_code.event_id details.check_code.key details.check_code.metric_id '
        'details.check_code.path details.check_code.proposal_id details.check_code.publication_id '
        'details.check_code.relation details.check_code.report_id details.check_code.revision_id '
        'details.check_code.role details.check_code.run_id details.check_code.token details.check_code.type '
        'details.check_code.version details.check_code.version_binding details.check_code.visualization_id '
        'details.check_run details.check_run.asset_id details.check_run.basis details.check_run.digest '
        'details.check_run.digest.sha256 details.check_run.event_id details.check_run.key '
        'details.check_run.metric_id details.check_run.path details.check_run.proposal_id '
        'details.check_run.publication_id details.check_run.relation details.check_run.report_id '
        'details.check_run.revision_id details.check_run.role details.check_run.run_id '
        'details.check_run.token details.check_run.type details.check_run.version '
        'details.check_run.version_binding details.check_run.visualization_id details.check_scope '
        'details.code details.code.asset_id details.code.basis details.code.digest details.code.digest.sha256 '
        'details.code.event_id details.code.key details.code.metric_id details.code.path '
        'details.code.proposal_id details.code.publication_id details.code.relation details.code.report_id '
        'details.code.revision_id details.code.role details.code.run_id details.code.token details.code.type '
        'details.code.version details.code.version_binding details.code.visualization_id details.conditions '
        'details.context_version details.contract details.definition details.definition_event_id '
        'details.derived_from details.derived_from.asset_id details.derived_from.basis '
        'details.derived_from.digest details.derived_from.digest.sha256 details.derived_from.event_id '
        'details.derived_from.key details.derived_from.metric_id details.derived_from.path '
        'details.derived_from.proposal_id details.derived_from.publication_id details.derived_from.relation '
        'details.derived_from.report_id details.derived_from.revision_id details.derived_from.role '
        'details.derived_from.run_id details.derived_from.token details.derived_from.type '
        'details.derived_from.version details.derived_from.version_binding '
        'details.derived_from.visualization_id details.derived_from[].event_id '
        'details.derived_from[].metric_id details.description details.effect_basis details.evidence '
        'details.evidence[].asset_id details.evidence[].basis details.evidence[].digest '
        'details.evidence[].digest.sha256 details.evidence[].event_id details.evidence[].key '
        'details.evidence[].metric_id details.evidence[].path details.evidence[].proposal_id '
        'details.evidence[].publication_id details.evidence[].relation details.evidence[].report_id '
        'details.evidence[].revision_id details.evidence[].role details.evidence[].run_id '
        'details.evidence[].token details.evidence[].type details.evidence[].version '
        'details.evidence[].version_binding details.evidence[].visualization_id details.exclusions '
        'details.expected_state details.files details.files[].path details.files[].ref '
        'details.files[].ref.asset_id details.files[].ref.basis details.files[].ref.digest '
        'details.files[].ref.digest.sha256 details.files[].ref.event_id details.files[].ref.key '
        'details.files[].ref.metric_id details.files[].ref.path details.files[].ref.proposal_id '
        'details.files[].ref.publication_id details.files[].ref.relation details.files[].ref.report_id '
        'details.files[].ref.revision_id details.files[].ref.role details.files[].ref.run_id '
        'details.files[].ref.token details.files[].ref.type details.files[].ref.version '
        'details.files[].ref.version_binding details.files[].ref.visualization_id details.from_seq '
        'details.goal details.input_contract details.journal_basis details.journal_basis.prefix_revision '
        'details.journal_basis.reader_format details.maintenance_cost details.measurement details.metric_id '
        'details.metrics details.metrics[].event_id details.metrics[].metric_id details.metrics[].role '
        'details.mode details.name details.new_state details.next_check details.omitted '
        'details.output_contract details.policy_version details.previous_definition details.previous_event_id '
        'details.previous_focus details.previous_revision details.problem details.question '
        'details.recomputed_from details.requirements details.result details.result_ref '
        'details.result_ref.asset_id details.result_ref.basis details.result_ref.digest '
        'details.result_ref.digest.sha256 details.result_ref.event_id details.result_ref.key '
        'details.result_ref.metric_id details.result_ref.path details.result_ref.proposal_id '
        'details.result_ref.publication_id details.result_ref.relation details.result_ref.report_id '
        'details.result_ref.revision_id details.result_ref.role details.result_ref.run_id '
        'details.result_ref.token details.result_ref.type details.result_ref.version '
        'details.result_ref.version_binding details.result_ref.visualization_id details.run_id details.scope '
        'details.scores details.session details.settings details.settings.cooldown_hours '
        'details.settings.measurement_targets details.settings.measurement_targets.input_tokens_per_review '
        'details.settings.measurement_targets.output_tokens_per_review details.settings.min_score '
        'details.settings.mode details.status details.suggestion_history details.summary '
        'details.summary.change_event_ids details.summary.finding_event_ids details.summary.focus_event_id '
        'details.summary.goal details.summary.metric_event_ids details.summary.next_actions '
        'details.summary.question_event_ids details.summary.todo_event_ids details.summary_source '
        'details.surface details.title details.trigger details.unresolved details.used_in '
        'details.used_in[].asset_id details.used_in[].basis details.used_in[].digest '
        'details.used_in[].digest.sha256 details.used_in[].event_id details.used_in[].key '
        'details.used_in[].metric_id details.used_in[].path details.used_in[].proposal_id '
        'details.used_in[].publication_id details.used_in[].relation details.used_in[].report_id '
        'details.used_in[].revision_id details.used_in[].role details.used_in[].run_id '
        'details.used_in[].token details.used_in[].type details.used_in[].version '
        'details.used_in[].version_binding details.used_in[].visualization_id id kind means recorded_at refs '
        'refs[].asset_id refs[].basis refs[].digest refs[].digest.sha256 refs[].event_id refs[].key '
        'refs[].metric_id refs[].path refs[].proposal_id refs[].publication_id refs[].relation '
        'refs[].report_id refs[].revision_id refs[].role refs[].run_id refs[].token refs[].type '
        'refs[].version refs[].version_binding refs[].visualization_id request_id schema seq session text '
    ).split()),
    'run_2': frozenset((
        'actor actor_basis duration_s ended_at env env.kit_version error finalize_errors id inputs '
        'inputs[].external inputs[].path inputs[].via means modules outputs outputs[].change outputs[].path '
        'outputs[].via pid reproduction reproduction.inputs_same reproduction.of reproduction.outputs '
        'reproduction.verdict reproduction.visualizations request_id rerun_of schema script script.path '
        'script.sha256 script.snapshot session started_at status stderr stdout title visualizations '
        'visualizations[].caption visualizations[].data visualizations[].data_columns '
        'visualizations[].data_rows visualizations[].data_sha256 visualizations[].files visualizations[].kind '
        'visualizations[].line visualizations[].sha256 visualizations[].title visualizations[].via '
        'visualizations[].visualization_id warnings '
    ).split()),
    'visualization_2': frozenset((
        'caption data data_columns data_rows data_sha256 files kind line sha256 title via visualization_id '
    ).split()),
    'dataset_1': frozenset((
        'bytes columns description imported_at name path rows schema sha256 source source.bytes source.format '
        'source.modified_at source.path source.sha256 '
    ).split()),
    'report_revision_1': frozenset((
        'actor based_on based_on.as_of_seq based_on.context_version based_on.refs based_on.refs[].asset_id '
        'based_on.refs[].basis based_on.refs[].digest based_on.refs[].digest.sha256 based_on.refs[].event_id '
        'based_on.refs[].key based_on.refs[].metric_id based_on.refs[].path based_on.refs[].proposal_id '
        'based_on.refs[].publication_id based_on.refs[].relation based_on.refs[].report_id '
        'based_on.refs[].revision_id based_on.refs[].role based_on.refs[].run_id based_on.refs[].token '
        'based_on.refs[].type based_on.refs[].version based_on.refs[].version_binding '
        'based_on.refs[].visualization_id created_at entry files files[].bytes files[].media_type '
        'files[].path files[].sha256 previous_revision report_id request_fingerprint request_id revision_id '
        'schema title '
            'summary scope purpose change_reason generation content_status metadata_file collection '
        'collection.contract collection.members collection.members[].member_key collection.members[].scope '
        'collection.members[].status collection.members[].report_id collection.members[].revision_id collection.members[].reason '
    ).split()),
    'metric_definition_details': frozenset((
        'change_reason contract definition derived_from derived_from[].event_id derived_from[].metric_id '
        'metric_id name previous_definition status unresolved '
    ).split()),
    'analysis_focus_details': frozenset((
        'activity change_reason contract goal metrics metrics[].event_id metrics[].metric_id metrics[].role '
        'next_check previous_focus question unresolved '
    ).split()),
    'metric_observation_details': frozenset((
        'change_reason contract definition_event_id metric_id recomputed_from result result_ref '
        'result_ref.asset_id result_ref.basis result_ref.digest result_ref.digest.sha256 result_ref.event_id '
        'result_ref.key result_ref.metric_id result_ref.path result_ref.proposal_id result_ref.publication_id '
        'result_ref.relation result_ref.report_id result_ref.revision_id result_ref.role result_ref.run_id '
        'result_ref.token result_ref.type result_ref.version result_ref.version_binding '
        'result_ref.visualization_id run_id scope '
    ).split()),
    'reference': frozenset((
        'asset_id basis digest digest.sha256 event_id key metric_id path proposal_id publication_id relation '
        'report_id revision_id role run_id token type version version_binding visualization_id '
    ).split()),
}

# These are record families, not arbitrary $defs helpers. New families must be classified too.
RECORD_FAMILIES = {
    "analytics.project/2": "partial",
    "analytics.event/2": "partial",
    "analytics.report_revision/1": "deferred_PR4",
    "analytics.run/2": "partial",
    "analytics.dataset/1": "partial",
    "analytics.trust/1": "excluded_internal",
    "analytics.migration/1": "excluded_internal",
}
