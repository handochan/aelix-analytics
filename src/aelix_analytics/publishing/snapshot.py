"""I/O adapter: freeze the existing reader and current file observations before policy runs."""
from __future__ import annotations

import hashlib
from collections import Counter

from .. import core, relations, writer
from .basis import PUBLICATION_KINDS, content_basis
from .inspections import detect_format
from .model import ItemKey, Omission, PublicationError, Reference
from .projection import ShareSnapshot, SnapshotFile, SnapshotItem, freeze


def identity(category, *parts):
    """Opaque identities prevent a read-profile key from exposing an evidence-only path."""
    return ItemKey(category, hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest())


def _key(ref_type, target):
    if not isinstance(target, dict):
        return None
    if isinstance(target.get("event_id"), str):
        return ItemKey("event", target["event_id"])
    if ref_type == "run" and isinstance(target.get("run_id"), str):
        return ItemKey("run", target["run_id"])
    if ref_type == "visualization_version" and all(isinstance(target.get(k), str) for k in ("run_id", "visualization_id")):
        return ItemKey("visualization", f"{target['run_id']}/{target['visualization_id']}")
    if ref_type == "context_version" and isinstance(target.get("version"), str):
        return ItemKey("context", target["version"])
    if ref_type == "dataset_import" and isinstance(target.get("path"), str):
        return identity("dataset", target["path"])
    if ref_type == "code_snapshot" and all(isinstance(target.get(k), str) for k in ("run_id", "path")):
        return identity("code", target["run_id"], target["path"])
    if ref_type == "data_observation" and all(isinstance(target.get(k), str) for k in ("run_id", "role", "path")):
        return identity("data_observation", target["run_id"], target["role"], target["path"])
    if ref_type == "report_revision" and all(isinstance(target.get(k), str) for k in ("report_id", "revision_id")):
        return ItemKey("report", f"{target['report_id']}/{target['revision_id']}")
    return None


def _refs(rows, *, journal=None, before=None):
    def known(value, allowed):
        return value if isinstance(value, str) and value in allowed else None

    out = []
    categories = {"dataset_import": "dataset", "data_observation": "data_observation", "code_snapshot": "code",
                  "context_version": "context", "visualization_version": "visualization", "report_revision": "report", "run": "run"}
    for r in rows:
        type_ = known(r.get("type"), relations.REF_TYPES)
        category = categories.get(type_, "event")
        target = _key(type_, r.get("target"))
        if type_ == "publication" and r.get("state") == "resolved" and journal is not None:
            value = r.get("target")
            pid = value.get("publication_id") if isinstance(value, dict) else None
            state, _reason, _candidates, event = relations.publication_record(journal, pid, before=before)
            if state == "resolved":
                # Delivery records never enter the package's content closure. Preserve a real
                # event target until the common projection turns it into not_included.
                target = ItemKey("event", event["id"])
        out.append(Reference(known(r.get("relation"), relations.RELATIONS),
            known(r.get("version_binding"), relations.BINDINGS), known(r.get("basis"), relations.BASES), r["state"], r.get("reason"),
            category, target, r.get("confirmed", False),
            tuple(ItemKey(category, candidate)
                  for candidate in r.get("candidates") or () if isinstance(candidate, str))))
    return tuple(out)


def _file(project, sha, source, role, recorded=None):
    if recorded is not None and (not isinstance(recorded, str) or not writer.SHA256_RE.fullmatch(recorded)):
        return SnapshotFile(source if isinstance(source, str) else "", role, "", None, None, reason="invalid_digest")
    if not isinstance(source, str) or not relations._inside(source):
        return SnapshotFile("", role, "", recorded, None, reason="outside_project")
    path = project.root / source
    try:
        real = path.resolve(strict=True)
        real.relative_to(project.root.resolve())
        if not real.is_file():
            raise OSError("not a regular file")
        digest = sha(real)
        size = real.stat().st_size
        if recorded is not None and digest != recorded:
            return SnapshotFile(source, role, "", recorded, digest, size)
        if role not in ("figure", "attached_data", "code_snapshot", "report_file"):
            # Input/output/context observations need the frozen digest, not a copy candidate's
            # full format payload. The context loader below separately compares its text bytes.
            return SnapshotFile(source, role, "", recorded, digest, size)
        with open(real, "rb") as fh:
            data = fh.read()
    except ValueError:
        return SnapshotFile(source, role, "", recorded, None, reason="outside_project")
    except OSError:
        return SnapshotFile(source, role, "", recorded, None, reason="file_absent")
    if hashlib.sha256(data).hexdigest() != digest:
        raise PublicationError("source_changed", "file changed after its digest was observed")
    format_ = detect_format(data, role=role, path=source) or "unsupported"
    if role == "code_snapshot" and format_ == "text" and path.suffix.lower() == ".py":
        format_ = "python"
    suffix = {"png": ".png", "jpeg": ".jpg", "gif": ".gif", "webp": ".webp", "svg": ".svg",
              "html": ".html", "plotly": ".json", "parquet": ".parquet", "python": ".py"}.get(format_, "")
    return SnapshotFile(source, role, suffix, recorded, digest, size, format_)


REPORT_TEXT_FIELDS = ("summary", "purpose", "change_reason", "content_status")  # owner 2026-10-26 (OD1)
MEMBER_FIELDS = ("member_key", "status", "reason", "report_id", "revision_id")  # owner 2026-10-26 (OD2)


def _report_items(project, sha, model, by_source):
    """Saved Report versions in completion order. A version is a root only when selected; the scope
    check, not this adapter, decides whether a selected one may be shared (PUBLICATION-BUILD 6.2)."""
    out = []
    for report_id, history in sorted(model.report_histories().items()):
        total = len(history["versions"])
        for version in history["versions"]:
            manifest, rev = version["manifest"], version["revision_id"]
            basis = manifest.get("based_on") if isinstance(manifest.get("based_on"), dict) else {}
            metadata = version["metadata"]
            values = {
                "report_id": report_id, "revision_id": rev, "title": manifest.get("title"),
                "created_at": manifest.get("created_at"), "entry": manifest.get("entry"),
                "actor_role": manifest.get("actor") if manifest.get("actor") in ("human", "agent", "system") else "unknown",
                "state": version["state"], "number": version["number"], "total": total,
                "based_on": {k: basis[k] for k in ("as_of_seq", "context_version") if basis.get(k) is not None},
                **{k: metadata[k] for k in REPORT_TEXT_FIELDS if isinstance(metadata.get(k), str)},
                # Private judgments for the scope check and the manifest, never projected fields.
                "_problem": version["problem"], "_content": version["content"], "_directory": version["directory"],
            }
            collection = metadata.get("collection")
            if isinstance(collection, dict) and isinstance(collection.get("members"), list):
                values["collection"] = {"members": [
                    {k: member[k] for k in MEMBER_FIELDS if isinstance(member.get(k), str)}
                    for member in collection["members"] if isinstance(member, dict)]}
            files = []
            if version["directory"] and not version["problem"]:
                try:
                    values["_manifest_sha256"] = sha(project.root / version["directory"] / "manifest.json")
                except OSError:
                    values["_problem"] = "manifest_unavailable"
                for entry in manifest.get("files") or ():
                    if not isinstance(entry, dict) or entry.get("path") == relations.REPORT_METADATA_FILE:
                        continue  # the kit's sidecar holds the metadata fields, not authored content
                    path = entry.get("path")
                    source = f"{version['directory']}/content/{path}" if relations.report_path(path) else None
                    files.append(_file(project, sha, source, "report_file", entry.get("sha256")))
            key = relations.report_key(report_id, rev)
            out.append(SnapshotItem(ItemKey("report", f"{report_id}/{rev}"), "report_revision", freeze(values),
                                    version["seq"] or 0,
                                    references=_refs(by_source.get(key, ()), journal=model.journal, before=version["seq"]),
                                    files=tuple(files)))
        for attempt in history["attempts"]:
            # Saving attempts are not versions (ADR-0006 7): never shared, but a selection names them.
            out.append(SnapshotItem(ItemKey("report", f"{report_id}/{attempt['revision_id']}"), "report_attempt",
                                    freeze({"report_id": report_id, "revision_id": attempt["revision_id"]}),
                                    complete=False))
    return out


def capture_snapshot(project, *, reader=core.read_model, validate_reference=relations.reference_problem):
    """Read once with one memoized digest source for judgments, content and copy candidates."""
    hashes = core.HashCache(project)
    digests = {}

    def sha(path):
        identity = project.canonical(path)
        if identity not in digests:
            digests[identity] = hashes.sha(path)
        return digests[identity]

    class SnapshotHashes:
        pass

    frozen_hashes = SnapshotHashes()
    frozen_hashes.sha = sha
    model = reader(project, cache=frozen_hashes)
    if model.updating:
        raise PublicationError("records_updating", "records changed while the snapshot was read")
    basis, delivery_lines = content_basis(model.journal, validate_reference=validate_reference)
    rows = model.references()
    by_source = {}
    for row in rows:
        by_source.setdefault(row["from"], []).append(row)
    delivery = {e.line for e in model.journal.events if e.kind in PUBLICATION_KINDS}
    replay_journal = relations.Journal(b"\n".join(b"" if n in delivery else raw for n, raw in
                                      enumerate(model.journal.data.split(b"\n"), 1)))
    states = replay_journal.replay()
    marker = core.read_json(project.marker, {})
    title = marker.get("title") if isinstance(marker, dict) else None
    title = title if isinstance(title, str) and title.strip() else None
    items = [SnapshotItem(ItemKey("project", "project"), "project", freeze({"title": title}), root=True)]
    for event in model.journal.in_order():
        if event.line in delivery_lines or event.kind in PUBLICATION_KINDS:
            continue
        values = dict(event.value)
        values["recorded_at"] = event.recorded_at
        name = model.journal.source_of(event)
        state = states.state_of(name)
        values.update(actor_role=values.get("actor") if values.get("actor") in ("human", "agent", "system") else "unknown",
                      state=(state or {}).get("state", "unknown"),
                      state_history=(state or {}).get("corrected_by", []))
        problem = relations.metric_event_problem(event.kind, event.value.get("details"), event.refs)
        items.append(SnapshotItem(ItemKey("event", name), event.kind, freeze(values), event.seq or 0,
                                  references=_refs(by_source.get(name, ()), journal=model.journal, before=event.seq),
                                  problem="unsupported" if problem else None))
    run_status = {v["run_id"]: v for v in model.runs()}
    latest = {}
    # Reuse the reader's instant/legacy-path ordering; ISO strings with different offsets do not
    # sort by chronology. The latest successful script run can omit a formerly present key.
    for script, ids in model._script_runs().items():
        for rid in reversed(ids):
            status, run = run_status[rid], model.run(rid)
            if status["recording_status"] == "complete" and status["execution_status"] == "ok" and not run.get("rerun_of"):
                latest[script] = rid
                break
    for rid, status in sorted(run_status.items()):
        run = model.run(rid)
        complete = status["recording_status"] == "complete" and status["execution_status"] != "running"
        values = {**run, "recording_status": status["recording_status"], "recording_reasons": status["reasons"],
                  "actor_role": run.get("actor") if run.get("actor") in ("human", "agent", "system") else "unknown",
                  "warning_count": len(run.get("warnings") or ()), "finalize_error_count": len(run.get("finalize_errors") or ())}
        attachments = []
        for script in (run.get("script") or {}, *(run.get("modules") or ())):
            path = script.get("snapshot")
            if path:
                key = identity("code", rid, path)
                file = _file(project, sha, path, "code_snapshot", script.get("sha256"))
                observed = ("missing" if not file.current_sha256 else "unknown" if not file.recorded_sha256 else
                            "same" if file.current_sha256 == file.recorded_sha256 else "changed")
                items.append(SnapshotItem(key, "code", freeze({"path": script.get("path"), "sha256": script.get("sha256"),
                                                             "observation": observed}),
                                          complete=complete, files=(file,)))
                attachments.append(key)
        for role, entries in (("input", run.get("inputs") or ()), ("output", run.get("outputs") or ())):
            for entry in entries:
                key = identity("data_observation", rid, role, str(entry.get("path")))
                meta = {k: v for k, v in entry.items() if k in ("path", "sha256", "bytes", "rows", "schema")}
                meta["role"] = role
                if entry.get("external"):
                    meta["path"] = "Outside the project"
                    meta["_outside"] = True
                    meta["observation"] = "unknown"
                else:
                    observed_file = _file(project, sha, entry.get("path"), "metadata", entry.get("sha256"))
                    meta["observation"] = ("missing" if not observed_file.current_sha256 else
                                           "unknown" if not observed_file.recorded_sha256 else
                                           "same" if observed_file.current_sha256 == observed_file.recorded_sha256 else "changed")
                items.append(SnapshotItem(key, "data_observation", freeze(meta), complete=complete))
                attachments.append(key)
        items.append(SnapshotItem(ItemKey("run", rid), "run", freeze(values), model.completion_seq(rid) or 0,
                                  complete=complete, attachments=tuple(attachments)))
        script = (run.get("script") or {}).get("path")
        for visual in relations.run_visualizations(run):
            if not isinstance(visual.get("id"), str):
                continue
            files_ = tuple(_file(project, sha, path, "figure", visual.get("sha256"))
                           for path in (visual.get("files") or {}).values())
            if visual.get("data"):
                files_ += (_file(project, sha, visual["data"], "attached_data", visual.get("data_sha256")),)
            values = {**visual, "visualization_id": visual["id"]}
            logical_script = model.chart_key(visual["key"]).rpartition("::")[0]
            root = latest.get(logical_script) == rid
            items.append(SnapshotItem(ItemKey("visualization", f"{rid}/{visual['id']}"), "visualization", freeze(values),
                                      model.completion_seq(rid) or 0, root=root,
                                      complete=complete and status["execution_status"] == "ok",
                                      attachments=(ItemKey("run", rid),), files=files_))
    for _place, meta in model._metas:
        if isinstance(meta.get("path"), str):
            items.append(SnapshotItem(identity("dataset", meta["path"]), "dataset", freeze(meta)))
    contexts = {}
    for event in model.journal.in_order():
        if event.kind != "context":
            continue
        if event.schema == relations.EVENT_V1:
            raw = event.refs.get("context") if isinstance(event.refs, dict) else None
            names = raw if isinstance(raw, list) else [raw]
            refs = [{"version": name} for name in names if isinstance(name, str)]
        else:
            refs = [r for r in event.refs or () if isinstance(r, dict) and r.get("type") == "context_version"]
        for ref in refs:
            if isinstance(ref.get("version"), str):
                contexts[ref["version"]] = (ref, event.recorded_at, event.seq or 0)
    for version, (ref, recorded_at, seq) in sorted(contexts.items()):
        current = version == basis.content.context_version
        path = relations.context_file(project.root, version)
        expected = ref.get("digest", {}).get("sha256") if isinstance(ref.get("digest"), dict) else None
        values = {"version": version, "recorded_at": recorded_at}
        try:
            if path is None:
                raise PublicationError("context_missing", "recorded CONTEXT version is missing")
            descriptor = _file(project, sha, path.relative_to(project.root).as_posix(), "context", expected)
            if descriptor.reason or not descriptor.current_sha256:
                raise PublicationError("context_missing", "recorded CONTEXT is not an inside-project regular file")
            data = path.read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            if actual != descriptor.current_sha256:
                raise PublicationError("source_changed", "CONTEXT changed after the frozen digest was observed")
            if expected:
                if actual != expected:
                    raise PublicationError("context_changed", "recorded CONTEXT bytes changed")
            else:
                import re

                prefix = re.search(r"_([0-9a-f]{8,64})\.md$", version)
                if prefix is None or not actual.startswith(prefix[1]):
                    raise PublicationError("context_unverified", "CONTEXT has no verifiable recorded digest")
            try:
                values["text"] = data.decode("utf-8")
            except UnicodeError as exc:
                raise PublicationError("context_unsupported", "CONTEXT is not UTF-8 text") from exc
            values["observation"] = "same"
        except PublicationError as exc:
            if current:
                raise
            values["observation"] = "missing" if exc.code == "context_missing" else "changed" if exc.code == "context_changed" else "unknown"
        items.append(SnapshotItem(ItemKey("context", version), "context" if current else "context_history",
                                  freeze(values), seq=seq, root=True))
    items.extend(_report_items(project, sha, model, by_source))
    counts = Counter()
    nonnormal = len(delivery - delivery_lines)
    if nonnormal:
        counts["journal", "unsupported_delivery"] = nonnormal
    for reason, values in (("damaged", model.journal.damaged), ("recovered", model.journal.recovered),
                           ("unsupported", model.journal.unsupported)):
        if values:
            counts["journal", reason] = len(values)
    unique = {item.key: item for item in items}
    private_values = (marker["project_id"],) if isinstance(marker, dict) and isinstance(marker.get("project_id"), str) else ()
    return ShareSnapshot(basis, tuple(unique[k] for k in sorted(unique)),
                         tuple(Omission(cat, reason, n) for (cat, reason), n in sorted(counts.items())), private_values)
