"""Internal value-rule facade for analysis assets; all validators live in the standalone reader.

No I/O. Writer and Replay share relations.asset_* rather than duplicate record semantics.
"""

from . import relations

CONTRACT = relations.ASSET_CONTRACT
SINCE = relations.ASSET_WRITER_SINCE
PUBLIC_KINDS = relations.ASSET_PUBLIC_KINDS
SYSTEM_KINDS = relations.ASSET_SYSTEM_KINDS
KINDS = relations.ASSET_KINDS
ARTIFACT_KINDS = relations.ASSET_ARTIFACT_KINDS
REASONS = relations.ASSET_REASONS
STATES = relations.ASSET_STATES
STATE_RELATIONS = relations.ASSET_STATE_RELATIONS
SHA = relations.ASSET_SHA
EVENT = relations.ASSET_EVENT
RUN = relations.ASSET_RUN
NAME = relations.ASSET_NAME
DEVICES = relations.ASSET_DEVICES
InvalidAsset = relations.AssetRecordError
_fail = relations._asset_fail
name = relations.asset_name
_text = relations._asset_text
_event = relations._asset_event
exact = relations.asset_exact
bundle_path = relations.asset_bundle_path
files = relations.asset_files
bundle_digest = relations.asset_bundle_digest
validate = relations.asset_validate
candidate_ref = relations.asset_candidate_ref
semantic_refs = relations.asset_semantic_refs
input_refs = relations.asset_input_refs
record_problem = relations.asset_record_problem
target = relations.asset_target
input_set = relations.asset_input_set
candidate = relations.asset_candidate
candidate_versions = relations.asset_candidate_versions
original_run = relations.asset_original_run
_source_file = relations._asset_source_file
check_matches = relations.asset_check_matches
validate_context = relations.asset_validate_context
passed_checks = relations.asset_passed_checks
promotion = relations.asset_promotion
promotion_proof = relations.asset_promotion_proof
update_problem = relations.asset_update_problem
event_problem = relations.asset_event_problem
