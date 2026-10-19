"""Publication manifest and completion schema match actual writing and negative examples."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from aelix_analytics import core, relations
from aelix_analytics.publishing import build, checks

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "docs/schemas/analytics-records.schema.json").read_bytes())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
ORIGIN = core.Origin("human", "cli")


def test_actual_completion_and_manifest_validate_against_registered_record_family(tmp_path):
    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    answer = build.build_publication(p, origin=ORIGIN)
    event = next(e.value for e in core.read_model(p).journal.events if e.kind == "publication")
    manifest = checks.verify_package(p.root / answer["path"])["manifest"]
    VALIDATOR.validate(event)
    VALIDATOR.validate(manifest)
    assert relations.publication_event_problem(event) is None
    for name in SCHEMA["$defs"]["publication_details"]["required"]:
        invalid = copy.deepcopy(event)
        del invalid["details"][name]
        assert not VALIDATOR.is_valid(invalid) and relations.publication_event_problem(invalid)
    for name in SCHEMA["$defs"]["publication_1"]["required"]:
        invalid = copy.deepcopy(manifest)
        del invalid[name]
        assert not VALIDATOR.is_valid(invalid)


def test_committed_publication_examples_and_negative_shapes():
    examples = json.loads((ROOT / "tests/fixtures/publication-binding.json").read_bytes())
    for name in ("completion", "manifest"):
        VALIDATOR.validate(examples[name])
    assert relations.publication_event_problem(examples["completion"]) is None
    for case in examples["negative_shapes"]:
        changed = copy.deepcopy(examples[case["target"]])
        parent = changed
        for key in case["path"][:-1]:
            parent = parent[key]
        parent[case["path"][-1]] = case["value"]
        assert not VALIDATOR.is_valid(changed), case["name"]
        if case["target"] == "completion":
            assert relations.publication_event_problem(changed), case["name"]


@pytest.mark.parametrize("profile", ["read", "evidence"])
def test_schema_preserves_existing_typed_exclusions_with_opaque_public_keys(tmp_path, profile):
    p = core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)
    hidden = core.record(
        p, "finding", "Private finding chosen for explicit exclusion.", origin=ORIGIN
    ).event
    result = build.build_publication(p, profile=profile, exclude=[hidden["id"]], origin=ORIGIN)
    event = next(e.value for e in core.read_model(p).journal.events if e.kind == "publication")
    assert event["details"]["exclusions"][0]["event_id"] == hidden["id"]
    VALIDATOR.validate(event)
    manifest = checks.verify_package(p.root / result["path"])["manifest"]
    VALIDATOR.validate(manifest)
    assert all(
        len(k["identity"]) == 16 and all(c in "0123456789abcdef" for c in k["identity"])
        for k in manifest["included"]
    )
