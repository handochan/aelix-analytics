"""Behavioral tests for portable documentation checks and the development-state contract."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "check_docs.py"
spec = importlib.util.spec_from_file_location("analytics_doc_checks", SCRIPT)
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text(".devstate/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"],
                   check=True)
    return root


def write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def cli(root, *args, env=None):
    # The checker writes UTF-8 whatever the console or the pipe's locale.
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *map(str, args)],
                          cwd=root.parent, capture_output=True, text=True, encoding="utf-8",
                          env=env)


def test_cli_includes_templates_and_new_docs_with_replayable_evidence(repository):
    template = write(repository, "kit/template/CONTEXT.md", "# 목표\n")
    subprocess.run(["git", "-C", str(repository), "add", "kit"], check=True)
    write(repository, "README.md", "[맥락](kit/template/CONTEXT.md#목표)\n")
    write(repository, ".devstate/ignored.md", "[missing](missing.md)\n")
    output = repository / ".devstate/evidence/docs.json"
    proc = cli(repository, "--output", output)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    files = {item["path"]: item for item in report["files"]}
    assert report["counts"]["markdown_files"] == 2
    assert set(files) == {"README.md", "kit/template/CONTEXT.md"}
    assert files["kit/template/CONTEXT.md"]["tracked"] is True
    assert files["README.md"]["tracked"] is False
    assert files["kit/template/CONTEXT.md"]["sha256"] == hashlib.sha256(template.read_bytes()).hexdigest()
    assert report["invocation"]["argv"] == [sys.executable, str(SCRIPT), "--root",
                                             str(repository), "--output", str(output)]
    assert report["checker"]["sha256"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    assert report["source"]["dirty"] is True


def test_markdown_links_tables_reference_links_and_duplicate_headings(repository):
    write(repository, "docs/한 글(1).md", "# `한글` 정의\n# 같은 이름\n# 같은 이름\n# 같은 이름-1\n")
    write(repository, "README.md", """# Links

| Reference |
|---|
| [table](<docs/한 글(1).md#한글-정의>) |

[second][target]

[target]: <docs/한 글(1).md#같은-이름-1>

[collision](<docs/한 글(1).md#같은-이름-1-1>)

`[example only](missing.md)`

```md
[example only](also-missing.md)
```
""")
    report = checks.Check(repository).run()
    assert report["ok"], report["errors"]
    assert report["counts"]["local_links"] == 3


def test_the_report_on_stdout_is_utf8_whatever_the_locale(repository):
    """A pipe's text follows the locale (cp1252 on the Windows runner), where a Korean path in the
    report was an exception instead of a report."""
    write(repository, "docs/한 글.md", "[없음](missing.md)\n")
    proc = cli(repository, env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert proc.returncode == 1, proc.stderr
    report = json.loads(proc.stdout)
    assert [e["path"] for e in report["errors"]] == ["docs/한 글.md"], report["errors"]


def test_broken_anchor_and_missing_link_fail_with_file_and_line(repository):
    write(repository, "docs/target.md", "# Present\n")
    write(repository, "README.md", "[bad](docs/target.md#absent)\n\n[missing](lost.md)\n")
    proc = cli(repository)
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert len(report["errors"]) == 2
    assert all(e["path"] == "README.md" and e["line"] for e in report["errors"])


LINK = {"schema": "aelix-analytics.project-link/1", "repository": "owner/repo",
        "project_url": "https://github.com/users/owner/projects/7",
        "default_view_url": "https://github.com/users/owner/projects/7/views/1",
        "project_node_id": "PVT_kwHOA-ATMs4Bj707"}


def test_the_project_link_is_checked_against_itself_and_the_origin(repository):
    subprocess.run(["git", "-C", str(repository), "remote", "add", "origin", "https://github.com/owner/repo.git"], check=True)
    write(repository, ".github/project.json", json.dumps(LINK))
    assert cli(repository).returncode == 0
    wrong = {**LINK, "repository": "someone/else", "default_view_url": "https://github.com/users/owner/projects/8/views/1",
             "project_node_id": "7"}
    write(repository, ".github/project.json", json.dumps(wrong))
    proc = cli(repository)
    assert proc.returncode == 1
    messages = [e["message"] for e in json.loads(proc.stdout)["errors"] if e["path"] == ".github/project.json"]
    assert len(messages) == 3, messages
    assert any("origin 'owner/repo'" in m for m in messages)
    assert any("default_view_url" in m for m in messages) and any("project_node_id" in m for m in messages)


def test_an_unlinked_project_link_passes_and_another_family_fails(repository):
    write(repository, ".github/project.json", json.dumps({**LINK, "project_url": None}))
    assert cli(repository).returncode == 0
    write(repository, ".github/project.json", json.dumps({**LINK, "schema": "other/1"}))
    proc = cli(repository)
    assert proc.returncode == 1 and "unsupported schema" in proc.stdout


def test_this_repositorys_project_link_is_valid():
    check = checks.Check(ROOT)
    check.check_json(".github/project.json")
    assert check.errors == []


@pytest.mark.parametrize(("document", "target"), [
    ("README.md", "."),
    ("docs/guide.md", "../"),
])
def test_repository_root_links_are_valid(repository, document, target):
    write(repository, document, f"[Repository root]({target})\n")
    report = checks.Check(repository).run()
    assert report["ok"], report["errors"]
    assert report["counts"]["local_links"] == 1


def test_neighbor_repository_and_ignored_target_do_not_pass_locally(repository):
    (repository.parent / "neighbor.md").write_text("# Exists on this machine\n", encoding="utf-8")
    write(repository, ".devstate/private.md", "private\n")
    write(repository, "README.md", "[neighbor](../neighbor.md)\n[local only](.devstate/private.md)\n")
    report = checks.Check(repository).run()
    assert not report["ok"]
    assert len(report["errors"]) == 2


def test_absolute_link_inside_current_checkout_is_still_not_portable(repository):
    target = write(repository, "docs/target.md", "# Target\n")
    write(repository, "README.md", f"[local absolute](<{target}>)\n")
    report = checks.Check(repository).run()
    assert not report["ok"]
    assert any("absolute filesystem link" in e["message"] for e in report["errors"])


@pytest.mark.parametrize("target", [r"C:\Users\me\docs\target.md", "C:/Users/me/target.md",
                                    r"\\server\share\target.md",
                                    # review 12, rest-10: relative to that drive's current directory
                                    "C:target.md", "d:docs/target.md"])
def test_a_windows_absolute_link_is_not_portable_on_any_platform(repository, target):
    # urlsplit reads `C:` as a URL scheme, which let a Windows checkout's absolute link through as
    # an external one (Issue #8 measurement on Windows Server 2025).
    write(repository, "README.md", f"[local absolute](<{target}>)\n")
    report = checks.Check(repository).run()
    assert not report["ok"]
    assert any("absolute filesystem link" in e["message"] for e in report["errors"])


def test_fence_validation_is_static_and_rejects_bad_and_unclosed_examples(repository):
    sentinel = repository / "should-not-exist"
    write(repository, "README.md", f"""```python
from pathlib import Path
Path({str(sentinel)!r}).touch()
```

```json
{{"duplicate": 1, "duplicate": 2}}
```

```python
def broken(:
```

```text
unclosed
""")
    report = checks.Check(repository).run()
    assert not report["ok"]
    assert not sentinel.exists()
    assert any("duplicate JSON key" in e["message"] for e in report["errors"])
    assert any("invalid python" in e["message"] for e in report["errors"])
    assert any("unclosed" in e["message"] for e in report["errors"])


def test_output_cannot_overwrite_a_document(repository):
    doc = write(repository, "README.md", "# Keep me\n")
    proc = cli(repository, "--output", doc)
    assert proc.returncode == 2
    assert doc.read_text(encoding="utf-8") == "# Keep me\n"


def test_output_cannot_overwrite_existing_development_state(repository):
    write(repository, "README.md", "# Valid\n")
    state = write(repository, ".devstate/state.json", '{"schema": "aelix-analytics.dev-state/1"}\n')
    original = state.read_bytes()
    proc = cli(repository, "--output", state)
    assert proc.returncode == 2
    assert state.read_bytes() == original


def test_source_change_is_not_reported_as_a_clean_pass(repository, monkeypatch):
    doc = write(repository, "README.md", "# Before\n")
    checker = checks.Check(repository)
    original = checker.check_markdown

    def modify_after_read(rel):
        original(rel)
        doc.write_text("# After\n", encoding="utf-8")

    monkeypatch.setattr(checker, "check_markdown", modify_after_read)
    report = checker.run()
    assert not report["ok"]
    assert any("changed during validation" in e["message"] for e in report["errors"])


@pytest.fixture
def state_contract():
    schema = json.loads((ROOT / "docs/schemas/dev-state.schema.json").read_text(encoding="utf-8"))
    example = json.loads((ROOT / "docs/templates/WORK-STATE.example.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker()), example


def test_state_example_and_legacy_extensions_remain_readable_without_rewrite(state_contract):
    validator, state = state_contract
    validator.validate(state)
    state["reviewed"] = {"commit": "a" * 40}
    state["merge"] = {"sha": "b" * 40}
    state["extensions"]["another-tool"] = {"custom": [1, 2]}
    before = copy.deepcopy(state)
    validator.validate(state)
    assert state == before
    assert json.loads(json.dumps(state)) == before


@pytest.mark.parametrize("sync", [
    {"state": "done", "last_synced_at": None, "pending_actions": [], "last_error": None},
    {"state": "synced", "last_synced_at": None, "pending_actions": [], "last_error": None},
    {"state": "synced", "last_synced_at": "2026-09-19T00:00:00Z", "pending_actions": ["upload"], "last_error": None},
    {"state": "synced", "last_synced_at": "2026-09-19T00:00:00Z", "pending_actions": [], "last_error": "failed"},
    {"state": "pending", "last_synced_at": None, "pending_actions": [], "last_error": None},
])
def test_sync_values_do_not_claim_unconfirmed_completion(state_contract, sync):
    validator, state = state_contract
    state["sync"] = sync
    assert list(validator.iter_errors(state))


def test_pending_and_confirmed_sync_transitions(state_contract):
    validator, state = state_contract
    state["sync"] = {"state": "pending", "last_synced_at": None,
                     "pending_actions": ["publish handoff"], "last_error": "offline"}
    validator.validate(state)
    state["sync"] = {"state": "synced", "last_synced_at": "2026-09-19T00:00:00Z",
                     "pending_actions": [], "last_error": None,
                     "handoff_url": "https://github.com/example/repo/issues/1#issuecomment-1"}
    validator.validate(state)


def test_unexecuted_verification_cannot_be_promoted_without_evidence(state_contract):
    validator, state = state_contract
    state["verification"][0]["result"] = "pass"
    assert list(validator.iter_errors(state))


def test_cli_validates_state_example_against_checked_in_schema(repository, state_contract):
    _, state = state_contract
    write(repository, checks.STATE_SCHEMA, (ROOT / checks.STATE_SCHEMA).read_text(encoding="utf-8"))
    state["sync"]["state"] = "done"
    write(repository, "docs/templates/WORK-STATE.example.json", json.dumps(state))
    proc = cli(repository)
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert any("state sync/state" in e["message"] for e in report["errors"])


@pytest.mark.parametrize(("field", "invalid_value"), [
    (("checkpoint_at",), "not-a-date"),
    (("work_item", "issue_url"), "not a uri"),
    (("sync", "last_synced_at"), ""),
    (("verification", 0, "executed_at"), "yesterday"),
])
def test_cli_rejects_invalid_state_formats(repository, state_contract, field, invalid_value):
    _, state = state_contract
    state["work_item"].update(local_id=None, issue_url="https://github.com/example/repo/issues/1")
    state["sync"].update(state="synced", last_synced_at="2026-09-19T00:00:00Z")
    state["verification"][0].update(result="pass", exit_code=0,
        executed_at="2026-09-19T00:00:00Z", source_snapshot="a" * 40,
        evidence_path="evidence/test.log")
    target = state
    for key in field[:-1]:
        target = target[key]
    target[field[-1]] = invalid_value
    write(repository, checks.STATE_SCHEMA, (ROOT / checks.STATE_SCHEMA).read_text(encoding="utf-8"))
    write(repository, checks.STATE_EXAMPLE, json.dumps(state))
    output = repository / ".devstate/evidence/docs.json"
    proc = cli(repository, "--output", output)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert not report["ok"]
    field_path = "/".join(map(str, field))
    assert any(f"state {field_path}:" in e["message"] for e in report["errors"])


def test_unresolved_schema_reference_preserves_failure_evidence(repository, state_contract):
    _, state = state_contract
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
              "$ref": "#/$defs/absent"}
    write(repository, checks.STATE_SCHEMA, json.dumps(schema))
    example = write(repository, checks.STATE_EXAMPLE, json.dumps(state))
    write(repository, "later.md", "# Still checked\n")
    output = repository / ".devstate/evidence/docs.json"
    proc = cli(repository, "--output", output)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert not report["ok"]
    assert any(e["path"] == checks.STATE_EXAMPLE and "/$defs/absent" in e["message"]
               for e in report["errors"])
    files = {item["path"]: item for item in report["files"]}
    assert files[checks.STATE_EXAMPLE]["sha256"] == hashlib.sha256(example.read_bytes()).hexdigest()
    assert checks.STATE_SCHEMA in files and "later.md" in files
    assert report["source"]["head"] == checks.git(repository, "rev-parse", "HEAD").decode().strip()
    assert report["invocation"]["argv"][-2:] == ["--output", str(output)]


def test_state_schema_cannot_trigger_network_retrieval(repository, state_contract):
    _, state = state_contract
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
              "$ref": "https://example.invalid/unavailable-schema.json"}
    write(repository, checks.STATE_SCHEMA, json.dumps(schema))
    write(repository, checks.STATE_EXAMPLE, json.dumps(state))
    proc = cli(repository)
    assert proc.returncode == 1
    assert any("nonlocal schema reference" in e["message"] for e in json.loads(proc.stdout)["errors"])


def test_future_state_schema_is_not_silently_checked_as_plain_json(repository, state_contract):
    _, state = state_contract
    state["schema"] = "aelix-analytics.dev-state/2"
    write(repository, checks.STATE_EXAMPLE, json.dumps(state))
    proc = cli(repository)
    assert proc.returncode == 1
    assert any("unsupported schema" in e["message"] for e in json.loads(proc.stdout)["errors"])
