"""Read-only publication record/folder observations, independent of build and rendering.

The caller supplies the common ReadModel. This module never repairs records, acquires a write
lock, executes analysis or equates a resolved journal reference with intact delivery bytes.
"""

from __future__ import annotations

from pathlib import Path

from .. import _platform, relations
from . import checks
from .model import PublicationError


def _ready(model):
    if model.updating:
        raise PublicationError("records_updating", "records changed while being read; try again")


def _folder(root, pid):
    path = root / ".analytics/publications" / pid
    try:
        for part in (root / ".analytics", path.parent, path):
            if part.exists() or part.is_symlink():
                if _platform.is_link(part.lstat()):
                    raise PublicationError(
                        "package_check_failed", "publication path contains a link"
                    )
        if not path.exists():
            return path, False
        return path, True
    except OSError:
        raise  # the caller reports an unknown observation, not a byte mismatch


def observe(model, publication_id):
    """One observation: journal completion and current folder verification are separate facts."""
    _ready(model)
    if not isinstance(publication_id, str) or not relations.PUBLICATION_ID.fullmatch(
        publication_id
    ):
        raise PublicationError("scope_invalid", "invalid publication identity")
    state, reason, candidates, event = relations.publication_record(model.journal, publication_id)
    complete = state == "resolved"
    row = {
        "publication_id": publication_id,
        "recording_status": "complete" if complete else "incomplete",
        "recording_reason": None if complete else reason,
        "record_state": state,
        "record": {"id": event["id"], "seq": event["seq"]} if complete else None,
        "candidates": candidates,
        "folder_checked": True,
        "folder_status": None,
        "folder_problem": None,
        "package_sha256": event["details"]["package_sha256"] if complete else None,
        "content_sha256": event["details"]["content_sha256"] if complete else None,
        "profile": event["details"]["profile"] if complete else None,
        "scope": event["details"]["scope"] if complete else None,
        "basis": event["details"]["basis"] if complete else None,
        "renderer": None,
        "same_content_as": None,
    }
    try:
        path, exists = _folder(Path(model.root), publication_id)
        if not exists:
            row["folder_status"] = "missing"
        else:
            if state == "missing":
                row["recording_reason"] = (
                    "no_completion_event" if (path / "publication.json").is_file() else "not_final"
                )
            verified = checks.verify_package(path, binding=event["details"] if complete else None)
            if verified["manifest"]["publication_id"] != publication_id:
                raise PublicationError(
                    "package_check_failed", "folder and manifest identities differ"
                )
            row["folder_status"] = "same"
            row["renderer"] = verified["manifest"]["renderer"]
    except PublicationError as exc:
        row["folder_status"] = "changed"
        row["folder_problem"] = {"code": exc.code, "message": exc.message}
    except OSError as exc:
        # An unreadable directory is an unknown observation, not an observed byte difference.
        row.update(
            folder_checked=False,
            folder_status=None,
            folder_problem={"code": "package_check_failed", "message": type(exc).__name__},
        )
    return row


def list_publications(model):
    _ready(model)
    ids = {pid for pid in model.journal._publications if relations.PUBLICATION_ID.fullmatch(pid)}
    base = Path(model.root) / ".analytics/publications"
    try:
        if base.exists() or base.is_symlink():
            if _platform.is_link(base.lstat()):
                raise PublicationError("package_check_failed", "publication directory is a link")
            ids.update(p.name for p in base.iterdir() if relations.PUBLICATION_ID.fullmatch(p.name))
    except OSError as exc:
        raise PublicationError("package_check_failed", "cannot list publication folders") from exc
    rows = [observe(model, pid) for pid in sorted(ids)]
    rows.sort(
        key=lambda row: (
            row["record"]["seq"] if row["record"] else float("inf"),
            row["publication_id"],
        )
    )
    previous = []
    for row in rows:
        if row["recording_status"] != "complete" or row["folder_status"] != "same":
            continue
        match = next(
            (
                old
                for old in reversed(previous)
                if old["renderer"] == row["renderer"]
                and old["content_sha256"] == row["content_sha256"]
            ),
            None,
        )
        if match:
            row["same_content_as"] = match["publication_id"]
        previous.append(row)
    return {"ok": True, "publications": rows, "basis": {"as_of_seq": model.as_of_seq}}


def verify(path, *, model=None):
    """Verify delivery bytes. Without original records, explicitly omit project binding proof."""
    if model is not None:
        _ready(model)
    result = checks.verify_package(Path(path))
    pid = result["manifest"]["publication_id"]
    binding_checked = False
    recording_status = None
    if model is not None:
        state, reason, _candidates, event = relations.publication_record(model.journal, pid)
        if state != "resolved":
            raise PublicationError(
                "publication_not_recorded",
                "no unique supported completion record",
                details={"publication_id": pid, "record_state": state, "reason": reason},
            )
        result = checks.verify_package(Path(path), binding=event["details"])
        binding_checked = True
        recording_status = "complete"
    return {
        **result,
        "project_binding_checked": binding_checked,
        "recording_status": recording_status,
    }
