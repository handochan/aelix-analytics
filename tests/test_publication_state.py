"""Record identity, folder integrity and standalone delivery inspection stay separate."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aelix_analytics import core
from aelix_analytics.publishing import build, checks, state
from aelix_analytics.publishing.model import PublicationError

ORIGIN = core.Origin("human", "cli")
ROOT = Path(__file__).resolve().parents[1]


def _project(tmp_path):
    return core.init_project(tmp_path / "project", title="Public", origin=ORIGIN)


def _env():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LAB_", "AELIX_LAB_"))}
    for key in ("AELIX_ANALYTICS_PROJECT", "AELIX_ANALYTICS_CANCEL_FILE"):
        env.pop(key, None)
    return {**env, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}


def _cli(tmp_path, *args, wrapper="module", project=None):
    if wrapper == "shell":
        command = [str(ROOT / "bin/analytics"), "build", *args]
    elif wrapper == "human":
        command = [
            sys.executable,
            "-c",
            f"from aelix_analytics.cli import run; import sys; from pathlib import Path; raise SystemExit(run(sys.argv[1:],kit=Path({str(ROOT / 'src')!r})))",
            "build",
            *args,
        ]
    else:
        command = [sys.executable, "-m", "aelix_analytics", "build", *args]
    env = _env()
    if project:
        env["AELIX_ANALYTICS_PROJECT"] = str(project.root)
    done = subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8"
    )
    assert done.stdout.strip(), done.stderr
    return done.returncode, json.loads(done.stdout.splitlines()[-1])


def test_list_separates_bound_identity_from_current_folder_and_compares_known_renderer(tmp_path):
    p = _project(tmp_path)
    one = build.build_publication(p, origin=ORIGIN)
    two = build.build_publication(p, origin=ORIGIN)
    listed = state.list_publications(core.read_model(p))["publications"]
    assert [r["publication_id"] for r in listed] == [one["publication_id"], two["publication_id"]]
    assert listed[1]["same_content_as"] == one["publication_id"]
    path = p.root / one["path"]
    (path / "index.html").write_bytes(b"changed")
    changed = state.observe(core.read_model(p), one["publication_id"])
    assert changed["recording_status"] == "complete" and changed["folder_status"] == "changed"
    shutil.rmtree(path)
    missing = state.observe(core.read_model(p), one["publication_id"])
    assert missing["recording_status"] == "complete" and missing["folder_status"] == "missing"
    assert state.list_publications(core.read_model(p))["publications"][1]["same_content_as"] is None
    ref = {
        "type": "publication",
        "publication_id": one["publication_id"],
        "digest": {"sha256": one["package_sha256"]},
        "relation": "cites",
        "basis": "declared",
        "version_binding": "author",
    }
    assert core.read_model(p).reference(ref)["state"] == "resolved"


def test_list_keeps_unbound_partial_unknown_and_ambiguous_without_fallback(tmp_path):
    p = _project(tmp_path)
    answer = build.build_publication(p, origin=ORIGIN)
    event = next(
        e.value for e in core.read_model(p).journal.events if e.value.get("kind") == "publication"
    )
    # A valid folder without its record is not completed; an empty folder is not final.
    raw = p.journal.read_bytes()
    p.journal.write_bytes(
        b"\n".join(
            line for line in raw.splitlines() if json.loads(line).get("kind") != "publication"
        )
        + b"\n"
    )
    row = state.observe(core.read_model(p), answer["publication_id"])
    assert (
        row["recording_status"] == "incomplete"
        and row["recording_reason"] == "no_completion_event"
        and row["folder_status"] == "same"
    )
    partial = "pub-" + "0" * 26
    (p.root / ".analytics/publications" / partial).mkdir()
    assert state.observe(core.read_model(p), partial)["recording_reason"] == "not_final"
    unknown = {**event, "details": {**event["details"], "format": "analytics.publication/99"}}
    p.journal.write_bytes(raw + json.dumps(unknown).encode() + b"\n")
    assert (
        state.observe(core.read_model(p), answer["publication_id"])["record_state"] == "ambiguous"
    )
    p.journal.write_bytes(json.dumps(unknown).encode() + b"\n")
    assert (
        state.observe(core.read_model(p), answer["publication_id"])["record_state"] == "unsupported"
    )


def test_readonly_state_uses_common_updating_and_does_not_write(tmp_path, monkeypatch):
    p = _project(tmp_path)
    model = core.read_model(p)
    before = {q.relative_to(p.root): q.read_bytes() for q in p.root.rglob("*") if q.is_file()}
    assert state.list_publications(model)["publications"] == []
    assert before == {
        q.relative_to(p.root): q.read_bytes() for q in p.root.rglob("*") if q.is_file()
    }
    model.updating = True
    with pytest.raises(PublicationError, match="changed while"):
        state.list_publications(model)
    model.updating = False
    pid = "pub-" + "0" * 26
    monkeypatch.setattr(
        state, "_folder", lambda *_: (_ for _ in ()).throw(PermissionError("cannot read"))
    )
    row = state.observe(model, pid)
    assert row["folder_status"] is None and not row["folder_checked"]


@pytest.mark.parametrize(
    "wrapper",
    [
        "module",
        "human",
        pytest.param("shell", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")),
    ],
)
def test_standalone_verify_needs_no_project_or_analysis_environment(tmp_path, wrapper):
    p = _project(tmp_path)
    answer = build.build_publication(p, origin=ORIGIN)
    destination = tmp_path / "한국어 공백 사본"
    shutil.copytree(p.root / answer["path"], destination)
    shutil.rmtree(p.root)
    code, result = _cli(tmp_path, "--verify", str(destination), wrapper=wrapper)
    assert (
        code == 0
        and result["ok"]
        and not result["project_binding_checked"]
        and result["recording_status"] is None
    )
    assert not (tmp_path / ".venv").exists()
    (destination / "extra.txt").write_bytes(b"extra")
    code, result = _cli(tmp_path, "--verify", str(destination), wrapper=wrapper)
    assert code == 1 and result["error"]["code"] == "package_check_failed"


def test_cli_list_verify_and_zip_of_use_binding_without_new_events_or_environment(tmp_path):
    p = _project(tmp_path)
    answer = build.build_publication(p, origin=ORIGIN)
    before = p.journal.read_bytes()
    code, result = _cli(tmp_path, "--list", wrapper="human", project=p)
    assert code == 0 and result["publications"][0]["recording_status"] == "complete"
    code, result = _cli(
        tmp_path, "--verify", str(p.root / answer["path"]), wrapper="human", project=p
    )
    assert code == 0 and result["project_binding_checked"]
    destination = tmp_path / "copy.zip"
    code, result = _cli(
        tmp_path,
        "--zip-of",
        answer["publication_id"],
        "--zip",
        str(destination),
        wrapper="human",
        project=p,
    )
    assert (
        code == 0
        and result["recording_status"] == "complete"
        and checks.verify_package(destination)["ok"]
    )
    assert p.journal.read_bytes() == before and not (p.root / ".venv").exists()
    code, result = _cli(
        tmp_path, "--zip-of", answer["publication_id"], "--zip", str(destination), project=p
    )
    assert code == 1 and result["error"]["code"] == "zip_path_invalid"


def test_module_actual_build_default_and_error_modes_preserve_recorded_result(tmp_path):
    p = _project(tmp_path)
    code, result = _cli(tmp_path, "--zip", str(p.root / "invalid.zip"), project=p)
    assert code == 1 and not result["ok"] and result["recording_status"] == "complete"
    assert result["error"]["code"] == "zip_path_invalid"
    assert len(core.read_model(p).journal._publications) == 1
    for args in (("--list", "--preview"), ("--verify", "a", "--zip-of", "b")):
        done = subprocess.run(
            [sys.executable, "-m", "aelix_analytics", "build", *args],
            cwd=tmp_path,
            env=_env(),
            capture_output=True,
        )
        assert done.returncode == 2


def test_status_overview_records_relations_keep_resolved_identity_beside_missing_folder(tmp_path):
    from aelix_analytics import writer

    p = _project(tmp_path)
    answer = build.build_publication(p, origin=ORIGIN)
    finding = core.record(
        p,
        "finding",
        "Public evidence cites a delivered copy.",
        refs=[
            writer.typed_reference(
                {
                    "type": "publication",
                    "publication_id": answer["publication_id"],
                    "digest": {"sha256": answer["package_sha256"]},
                    "relation": "cites",
                    "basis": "declared",
                }
            )
        ],
        origin=ORIGIN,
    ).event
    shutil.rmtree(p.root / answer["path"])
    overview = core.status(p)
    entry = next(row for row in overview["key"] if row["id"] == finding["id"])
    observation = entry["references"]["publications"][0]
    assert observation["reference_state"] == "resolved"
    assert (
        observation["recording_status"] == "complete" and observation["folder_status"] == "missing"
    )
    assert "record complete, folder missing" in core.status_text(overview)
    record = core.retrieve(p, part="records", id=finding["id"])
    assert record["evidence"]["publications"][0]["folder_status"] == "missing"
    references = core.retrieve(p, part="relations", id=finding["id"], direction="outgoing")
    assert references["items"][0]["state"] == "resolved"
    assert references["items"][0]["publication"]["folder_status"] == "missing"


@pytest.mark.parametrize("complete", [False, True])
def test_cli_interruption_preserves_complete_or_unknown_result_with_exit130(
    tmp_path, monkeypatch, capsys, complete
):
    from aelix_analytics import __main__

    p = _project(tmp_path)

    def interrupt(*args, **kwargs):
        if complete:
            return {
                "ok": True,
                "recording_status": "complete",
                "interrupted": True,
                "binding_recovered": True,
            }
        raise PublicationError(
            "publication_not_recorded",
            "retained",
            details={"binding": "unknown", "interrupted": True},
        )

    monkeypatch.setattr(build, "build_publication", interrupt)
    assert __main__._command(["--project", str(p.root), "build"]) == 130
    result = json.loads(capsys.readouterr().out)
    if complete:
        assert result["recording_status"] == "complete" and result["binding_recovered"]
    else:
        assert result["error"]["details"]["binding"] == "unknown"
