"""Immutable Report storage, with an injected Writer (ADR-0013 PR1).

No analysis execution, rendering, environment lookup or project-model import. Attempt locks
always precede the journal lock; renamed revisions are never rewritten or removed here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import shutil
import stat
import time
from dataclasses import replace
from pathlib import Path

from . import _platform, relations, writer

# Assigned with the kit version at integration; older writers must refuse the new write rules.
SINCE = "0.22.0"


def revision_id():
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(alphabet[(value >> (5 * shift)) & 31] for shift in reversed(range(26)))


def _fail(code, message, **details):
    raise writer.AnalyticsError(code, message, details=details or None)


def _stat(path):
    value = path.lstat()
    if _platform.is_link(value) or not stat.S_ISREG(value.st_mode):
        _fail("report_source_invalid", "Report sources must be regular files without links", path=str(path))
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _sha(path):
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _plain(root, path):
    """Reject links in the managed path, including junctions, before following any child."""
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        try:
            value = current.lstat()
        except FileNotFoundError:
            continue
        if _platform.is_link(value):
            _fail("report_path_invalid", "Report storage cannot follow a link", path=str(current))


def _source_plan(files):
    if isinstance(files, dict):
        paths = {name: Path(source).expanduser().absolute() for name, source in files.items()}
    else:
        root = Path(files).expanduser().absolute()
        if _platform.is_link(root.lstat()) or not root.is_dir():
            _fail("report_source_invalid", "--files must be a directory without links")
        paths = {}

        def walk(directory):
            for child in sorted(directory.iterdir()):
                value = child.lstat()
                if _platform.is_link(value):
                    _fail("report_source_invalid", "Report sources cannot contain links", path=str(child))
                if stat.S_ISDIR(value.st_mode):
                    walk(child)
                else:
                    paths[child.relative_to(root).as_posix()] = child
        walk(root)
    if not paths:
        _fail("report_source_invalid", "Report files cannot be empty")
    if not relations.report_paths(paths):
        _fail("report_path_invalid", "Report paths and shared directory prefixes must have one portable spelling")
    folded = set()
    for name in paths:
        if not relations.report_path(name) or name.split("/")[0].casefold() == "_analytics":
            _fail("report_path_invalid", "Report destination paths must be portable NFC relative paths", path=name)
        if name.casefold() in folded:
            _fail("report_path_invalid", "Report paths must be unique ignoring case", path=name)
        folded.add(name.casefold())
    # A file cannot simultaneously be another file's parent, even on a case-insensitive volume.
    for name in folded:
        if any("/".join(name.split("/")[:n]) in folded for n in range(1, len(name.split("/")))):
            _fail("report_path_invalid", "A Report file cannot be a directory of another file")
    return {name: (source, _stat(source), _sha(source)) for name, source in sorted(paths.items())}


class Reports:
    def __init__(self, record_writer):
        self.writer = record_writer
        self.root = record_writer.root
        self.base = self.root / "artifacts/reports"

    def _model(self, journal=None):
        return relations.ReadModel(self.root, journal or relations.Journal(writer._journal_bytes(self.writer.journal)),
                                   alive=self.writer.platform.processes.alive, sha=_sha)

    def _attempt(self, revision):
        path = self.root / writer.LAYOUT.state_dir / "report-attempts" / (revision + ".lock")
        _plain(self.root, path)
        return self.writer.platform.locks.exclusive(path)

    def _saved(self, held, report_id):
        directory = self.base / report_id
        _plain(self.root, directory)
        if directory.exists():
            for revision in directory.iterdir():
                if revision.name == ".staging":
                    continue
                _plain(self.root, revision / "manifest.json")
                if not revision.is_dir():
                    _fail("report_record_invalid", "Unexpected entry in Report revision storage")
                try:
                    manifest = json.loads((revision / "manifest.json").read_bytes())
                except (OSError, ValueError):
                    _fail("report_record_invalid", "Unreadable saved Report manifest")
                if relations.report_manifest_problem(manifest, revision.relative_to(self.root).as_posix()):
                    _fail("report_record_invalid", "Invalid saved Report manifest")
        model = self._model(held.journal)
        rows = [row for row in model.report_revisions() if row["report_id"] == report_id
                and row["recording_status"] != "prepared"]
        # Include completion events whose manifest was lost or could not be parsed.
        recorded = [(event, ref) for event in held.journal.events if event.kind == "report_revision"
                    for ref in relations._dicts(event.refs) if ref.get("report_id") == report_id]
        known = {row["revision_id"] for row in rows}
        if any(not isinstance(ref.get("revision_id"), str) or ref["revision_id"] not in known for _, ref in recorded):
            _fail("report_record_invalid", "A Report completion has no readable preserved revision")
        requests = set()
        for row in rows:
            if row.get("problem") or row["content"] != "resolved":
                _fail("report_record_invalid", "A Report revision is damaged or ambiguous", revision=row)
            request = row["request_id"].casefold()
            if request in requests:
                _fail("report_record_invalid", "One Report request identifies several saved revisions")
            requests.add(request)
        heads = [row for row in rows if row["head"]]
        complete = [row for row in rows if row["recording_status"] == "complete"]
        last = None
        by_revision = {row["revision_id"]: row for row in complete}
        for rid, revision in model._report_completions():
            if rid == report_id:
                manifest = model._saved[(rid, revision)][1]
                if manifest["previous_revision"] != last or revision not in by_revision:
                    _fail("report_record_invalid", "Completed Report revisions do not form one linear history")
                last = revision
        return model, rows, heads[0]["revision_id"] if heads else None

    def save(self, report_id, *, files, entry, title, previous_revision, request_id=None, based_on=None, metadata=None):
        self.writer.admit("save_report")
        if not relations.report_name(report_id):
            _fail("name_not_portable", "report_id must be a portable lower-case ASCII name")
        for name in (previous_revision, request_id):
            if name is not None:
                writer.check_request_id(name)
        request_id = request_id or self.writer.origin.request_id or writer.new_request_id()
        writer.check_request_id(request_id)
        # A request is stamped into both the manifest and its completion, including generated IDs.
        app = Reports(writer.Writer(self.root, platform=self.writer.platform,
                                   origin=replace(self.writer.origin, request_id=request_id),
                                   writer_version=self.writer.writer_version, clock=self.writer.clock,
                                   legacy_path=self.writer.legacy_path))
        try:
            return app._save(report_id, files, entry, title, previous_revision, request_id, based_on, metadata)
        except writer.AnalyticsError as exc:
            exc.details = {**(exc.details if isinstance(exc.details, dict) else {}), "request_id": request_id}
            raise
        except OSError as exc:
            _fail("report_io", f"Report storage failed: {exc}; retry with the same request_id", request_id=request_id)

    def _save(self, report_id, files, entry, title, previous, request, based_on, metadata):
        try:
            plan = _source_plan(files)
        except (OSError, ValueError, TypeError) as exc:
            _fail("report_source_invalid", f"Cannot read Report sources: {exc}")
        if not relations.report_path(entry) or entry not in plan:
            _fail("report_path_invalid", "entry must identify one explicitly preserved file")
        manifest = {"schema": relations.REPORT_V1, "report_id": report_id, "revision_id": "preflight",
                    "previous_revision": previous, "request_id": request,
                    "created_at": self.writer.clock().isoformat(), "actor": self.writer.origin.actor,
                    "title": title, "entry": entry,
                    "files": [{"path": name, "bytes": info[1][2], "sha256": info[2]} for name, info in plan.items()]}
        metadata_data = None
        if metadata is not None:
            problem = relations.report_metadata_problem(metadata)
            if problem:
                _fail("report_input_invalid", problem)
            metadata_data = relations.report_metadata_bytes(metadata)
            # Snapshot caller-owned containers before any copy or lock wait.
            manifest.update(json.loads(metadata_data))
            manifest["metadata_file"] = relations.REPORT_METADATA_FILE
            manifest["files"].append({"path": relations.REPORT_METADATA_FILE, "bytes": len(metadata_data),
                                      "sha256": hashlib.sha256(metadata_data).hexdigest()})
        if based_on is not None:
            # Snapshot authored evidence; never substitute the current context or data.
            try:
                manifest["based_on"] = json.loads(json.dumps(based_on, allow_nan=False))
            except (TypeError, ValueError) as exc:
                _fail("report_input_invalid", str(exc))
        try:
            manifest["request_fingerprint"] = relations.request_fingerprint(manifest)
        except (ValueError, TypeError, KeyError) as exc:
            _fail("report_input_invalid", str(exc))
        problem = relations.report_manifest_problem(manifest)
        if problem:
            _fail("report_input_invalid", problem)
        directory = self.base / report_id
        _plain(self.root, directory / ".staging")
        revision = revision_id()
        with self._attempt(revision):
            stage = directory / ".staging" / revision
            stage.parent.mkdir(parents=True, exist_ok=True)
            # Random collision creates a new attempt, never reuses any existing staging.
            try:
                stage.mkdir()
            except FileExistsError:
                return self._save(report_id, files, entry, title, previous, request, based_on, metadata)
            manifest["revision_id"] = revision
            renamed = False
            try:
                for name, (source, observed, digest) in plan.items():
                    if _stat(source) != observed:
                        _fail("report_source_changed", "Report source changed before copying", path=name)
                    target = stage / "content" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    if (_stat(source) != observed or _sha(target) != digest or _sha(source) != digest):
                        _fail("report_source_changed", "Report source changed while copying", path=name)
                if _source_plan(files) != plan:
                    _fail("report_source_changed", "Report source listing or bytes changed while copying")
                if metadata_data is not None:
                    sidecar = stage / "content" / relations.REPORT_METADATA_FILE
                    sidecar.parent.mkdir(parents=True, exist_ok=True)
                    sidecar.write_bytes(metadata_data)
                writer.write_json(stage / "manifest.json", manifest, platform=self.writer.platform)
                with self.writer.locked("save_report") as held:
                    _plain(self.root, directory)
                    model, rows, head = self._saved(held, report_id)
                    same = [row for row in rows if row["request_id"].casefold() == request.casefold()]
                    if same:
                        row = same[0]
                        saved = model._saved[(report_id, row["revision_id"])][1]
                        if saved["request_fingerprint"] != manifest["request_fingerprint"]:
                            _fail("request_conflict", "This request_id already belongs to another Report intent")
                        if row["recording_status"] == "complete":
                            return self._answer("existing", saved, model, plan)
                        if head != saved["previous_revision"]:
                            _fail("revision_conflict", "Report head changed before incomplete revision could resume", head=head)
                        self._collection_targets(model, saved)
                        held.require(SINCE)
                        self._complete(held, directory / saved["revision_id"], saved)
                        return self._answer("resumed", saved, self._model(held.journal), plan)
                    if head != previous:
                        _fail("revision_conflict", "Report head does not match previous_revision", head=head)
                    self._collection_targets(model, manifest)
                    held.require(SINCE)
                    # No replace/copy fallback: unsupported filesystems fail explicitly.
                    try:
                        self.writer.platform.files.rename_new(stage, directory / revision)
                    except _platform.RenameUnsupported as exc:
                        _fail("no_exclusive_rename", str(exc))
                    renamed = True
                    self._complete(held, directory / revision, manifest)
                    return self._answer("saved", manifest, self._model(held.journal), plan)
            finally:
                # Only this attempt's still-prepared tree is disposable. Incomplete revisions stay.
                if not renamed and stage.exists():
                    shutil.rmtree(stage)

    def _complete(self, held, directory, manifest):
        held.append("report_revision", "Report revision saved", refs=[{
            "type": "report_revision", "report_id": manifest["report_id"], "revision_id": manifest["revision_id"],
            "digest": {"sha256": _sha(directory / "manifest.json")}, "relation": "about",
            "basis": "observed", "version_binding": "author"}],
            details={"previous_revision": manifest["previous_revision"]})

    def _collection_targets(self, model, manifest):
        """Only exact prior completions with intact bytes; no implicit latest or generation."""
        collection = manifest.get("collection")
        if collection is None:
            return
        complete = set(model._report_completions())
        for member in collection["members"]:
            if member["status"] != "complete":
                continue
            key = (member["report_id"], member["revision_id"])
            saved = model._saved.get(key)
            if (key not in complete or saved is None or model._content(*saved) != "resolved"
                    or model._metadata_binding(*saved)):
                _fail("report_member_invalid", "A complete collection member must name an intact prior completed revision",
                      member_key=member["member_key"], report_id=key[0], revision_id=key[1])

    def _answer(self, outcome, manifest, model, plan):
        ref = {"report_id": manifest["report_id"], "revision_id": manifest["revision_id"]}
        row = next(row for row in model.report_revisions() if all(row[k] == v for k, v in ref.items()))
        return {"outcome": outcome, "request_id": manifest["request_id"], "report": row,
                "files": [{"source": str(plan[f["path"]][0]) if f["path"] in plan else None,
                           **({"generated_by": "kit"} if f["path"] not in plan else {}), "path": f["path"],
                           "preserved_path": f"artifacts/reports/{ref['report_id']}/{ref['revision_id']}/content/{f['path']}"}
                          for f in manifest["files"]]}

    def read(self, report_id=None, revision=None, *, limit=20, offset=0, file=None, file_offset=0, file_limit=8192, verify_files=False):
        writer.readable(self.root, "reports")
        if any(type(v) is not int or v < 0 for v in (limit, offset, file_offset, file_limit)) or not 1 <= limit <= 100 or not 1 <= file_limit <= 65536:
            _fail("report_input_invalid", "Report read limits must be bounded positive integers")
        if type(verify_files) is not bool:
            _fail("report_input_invalid", "verify_files must be a boolean")
        model = self._model()
        rows = [row for row in model.report_revisions(verify_content=verify_files) if (report_id is None or row["report_id"] == report_id)
                and (revision is None or row["revision_id"] == revision)]
        for row in rows:
            if row["recording_status"] == "complete":
                state = model.journal.replay().state_of(relations.report_key(row["report_id"], row["revision_id"]))
                row["state"] = state["state"] if state else None
        answer = {"reports": rows[offset:offset + limit], "total": len(rows),
                  "next_offset": offset + limit if offset + limit < len(rows) else None}
        if revision is not None:
            saved = model._saved.get((report_id, revision))
            if not saved:
                _fail("unknown_report", "No saved Report revision at that exact address")
            directory, manifest = saved
            answer["manifest"] = {k: v for k, v in manifest.items() if k != "files"}
            files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
            answer["files"] = files[offset:offset + limit]
            answer["files_next_offset"] = offset + limit if offset + limit < len(files) else None
            if file is not None:
                if (not relations.report_path(file) or file not in {f.get("path") for f in files if isinstance(f, dict)}
                        or rows[0].get("problem")):
                    _fail("report_path_invalid", "Select a preserved file of this valid Report manifest")
                path = self.root / directory / "content" / file
                _plain(self.root, path)
                try:
                    with open(path, "rb") as fh:
                        fh.seek(file_offset)
                        data = fh.read(file_limit)
                    size = path.stat().st_size
                except OSError as exc:
                    _fail("report_io", f"Cannot read preserved Report file: {exc}")
                answer["file"] = {"path": file, "encoding": "base64", "data": base64.b64encode(data).decode("ascii"),
                                  "offset": file_offset, "next_offset": file_offset + len(data) if file_offset + len(data) < size else None}
        return answer

    def update(self, report_id, revision, *, expected, new, reason):
        self.writer.admit("update_report")
        request_id = self.writer._request_id()
        if not relations.report_name(report_id):
            _fail("name_not_portable", "Invalid Report identity")
        writer.check_request_id(revision)
        with self.writer.locked("update_report") as held:
            model, rows, _ = self._saved(held, report_id)
            found = [row for row in rows if row["revision_id"] == revision and row["recording_status"] == "complete"]
            if not found:
                _fail("state_conflict", "Only an exact completed Report revision can change state")
            key = relations.report_key(report_id, revision)
            state = held.replay.state_of(key)
            if not state or state["state"] != expected or new not in state["transitions"]:
                _fail("state_conflict", "Report state differs or transition is not allowed", state=state)
            event = held.append("record_update", reason, refs=[{
                "type": "report_revision", "report_id": report_id, "revision_id": revision,
                "digest": {"sha256": _sha(self.root / model._saved[(report_id, revision)][0] / "manifest.json")},
                "relation": "updates", "basis": "declared", "version_binding": "author"}],
                details={"expected_state": expected, "new_state": new}, requires=SINCE)
            return {"event": event, "request_id": request_id, "state": new}

    def clean(self, report_id, revision):
        self.writer.admit("clean_report_prepared")
        if not relations.report_name(report_id):
            _fail("name_not_portable", "Invalid Report identity")
        writer.check_request_id(revision)
        with self._attempt(revision):
            with self.writer.locked("clean_report_prepared"):
                stage = self.base / report_id / ".staging" / revision
                _plain(self.root, stage)
                if not stage.is_dir() or (self.base / report_id / revision).exists():
                    _fail("report_not_prepared", "Only the explicitly named prepared attempt can be removed")
                shutil.rmtree(stage)
                return {"cleaned": {"report_id": report_id, "revision_id": revision}}
