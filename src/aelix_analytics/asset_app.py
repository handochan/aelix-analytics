"""Shared, model-free analysis asset lifecycle; injected Writer, no host or core imports.

Public input validation remains in the common Writer. This application owns on-demand
review and the explicit registration workflow, including incomplete-publication receipts.
It never reads the user's Aelix home, launches code, invokes a model or registers globally.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from pathlib import Path

from . import _platform, asset_policy, asset_suggestions, relations, writer
from . import asset_contract as contract

_STEPS = frozenset({("proposed", "trial"), ("trial", "validated"), ("validated", "registered"),
                    *((s, end) for s in ("proposed", "trial", "validated") for end in ("deferred", "rejected")),
                    ("deferred", "proposed"), ("rejected", "proposed"), ("registered", "deprecated")})


def _error(exc: Exception) -> writer.AnalyticsError:
    return writer.AnalyticsError("bad_asset", str(exc), hint="read docs/design/ANALYSIS-ASSETS.md for the asset contract")


class Assets:
    def __init__(self, record_writer: writer.Writer, *, policy=None, skill_discovery=None):
        self.writer = record_writer
        self.root = record_writer.root
        self.policy = policy or asset_policy.DeterministicPolicy()
        self.skill_discovery = skill_discovery

    def _safe(self, relative: str, *, directory: bool = False) -> Path:
        """A project-only regular path: inspect parents without following links out of the project."""
        try:
            contract.bundle_path(relative)
        except contract.InvalidAsset as exc:
            raise _error(exc) from exc
        path = self.root
        parts = Path(relative).parts
        for index, part in enumerate(parts):
            if not _platform.portable_name(part):
                raise writer.AnalyticsError("name_not_portable", f"asset path component {part!r} is not portable")
            path = path / part
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if _platform.is_link(info) or not (stat.S_ISDIR(info.st_mode) if index < len(parts) - 1 or directory
                                                else stat.S_ISREG(info.st_mode)):
                raise writer.AnalyticsError("unsafe_asset_path", f"asset path {relative!r} crosses a link or non-regular entry")
        return path

    def _sha(self, path) -> str | None:
        try:
            relative = Path(path).relative_to(self.root).as_posix()
            safe = self._safe(relative)
            h = hashlib.sha256()
            with safe.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            return h.hexdigest()
        except (OSError, ValueError, writer.AnalyticsError):
            return None

    def model(self, journal=None):
        journal = journal or relations.read_journal(self.root)
        return relations.ReadModel(self.root, journal, alive=self.writer.platform.processes.alive,
                                   sha=self._sha, legacy_path=self.writer.legacy_path)

    @staticmethod
    def _records(journal):
        records, warnings = [], []
        for event in journal.in_order():
            if event.kind in contract.KINDS:
                problem = contract.event_problem(event.value)
                if problem or journal.source_of(event) != event.id:
                    warnings.append({"event_id": journal.source_of(event), "reason": problem or "ambiguous_id"})
                else:
                    records.append(event.value)
        return records, warnings

    @staticmethod
    def _state(journal, proposed: dict) -> str:
        """The common replay owns chronology, identity and promotion proof for every surface."""
        return (journal.replay().state_of(proposed["id"]) or {}).get("state", "proposed")

    def list(self, asset_id=None, *, model=None) -> dict:
        if asset_id is not None:
            try:
                contract.name(asset_id)
            except contract.InvalidAsset as exc:
                raise _error(exc) from exc
        model = model if model is not None else self.model()
        records, warnings = self._records(model.journal)
        warnings += [{"event_id": entry["event_id"], "reason": entry["reason"]}
                     for entry in model.journal.replay().unapplied
                     if entry.get("reason") in ("invalid_asset_update", "invalid_asset_record")]
        found = {}
        for record in records:
            if record["kind"] == "asset_candidate":
                identity = record["details"]["asset_id"]
                if asset_id is None or asset_id == identity:
                    found.setdefault(identity, []).append(record)
        items = []
        for identity, versions in sorted(found.items()):
            current = versions[-1]
            good_chain = all(v["details"].get("previous_event_id") == (versions[i - 1]["id"] if i else None)
                             for i, v in enumerate(versions))
            items.append({"asset_id": identity, "event_id": current["id"], "title": current["details"]["title"],
                          "artifact_kind": current["details"]["artifact_kind"],
                          "state": self._state(model.journal, current) if good_chain else "conflict",
                          "versions": versions, "details": current["details"],
                          "references": model.references(current["id"]), "legacy": False})
            if asset_id is not None:
                items[-1]["history"] = [event.value for event in model.journal.in_order()
                                        if isinstance(event.value.get("details"), dict)
                                        and event.value["details"].get("asset_id") == identity]
        legacy = []
        for event in model.journal.in_order():
            if event.kind != "reuse_proposal":
                continue
            d = event.value.get("details") or {}
            if asset_id is None or d.get("proposal_id") == asset_id:
                legacy.append({"event_id": event.id, "raw_kind": event.kind, "proposal_id": d.get("proposal_id"),
                               "source": model.journal.source_of(event), "legacy": True, "record": event.value})
        return {"assets": items, "legacy_candidates": legacy, "warnings": warnings,
                "as_of_seq": model.as_of_seq, "context_version": self.context_version(model.journal)}

    @staticmethod
    def context_version(journal) -> str | None:
        for event in reversed(journal.in_order()):
            if event.kind == "context" and isinstance(event.refs, list):
                for ref in event.refs:
                    if isinstance(ref, dict) and ref.get("type") == "context_version" and ref.get("version_binding") == "author":
                        return ref.get("version")
        return None

    def assessment(self, proposed: dict, model, *, min_score=8) -> dict:
        d = proposed["details"]
        evidence = {}

        def keep(ref):
            if model.reference(ref)["confirmed"]:
                evidence[json.dumps(contract.target(ref), sort_keys=True, ensure_ascii=False)] = ref
                return True
            return False

        sources = [r for r in proposed.get("refs", []) if r.get("type") in ("run", "code_snapshot") or
                   (r.get("type") == "event" and (source := model.journal.event_of(r.get("event_id"))) is not None
                    and source.kind == "finding")]
        sources += [{"type": "run", "run_id": f["ref"]["run_id"], "relation": "uses", "basis": "observed",
                     "version_binding": "author"} for f in contract.files(d)]
        confirmed_sources = [r for r in sources if keep(r)]
        code_file = contract._source_file(d["code"], model) if d.get("code") else None
        uses = set()
        # Recorded module/script bytes establish file use, never individual function execution.
        for run_id in sorted(model._runs):
            run = model.run(run_id)
            if run.get("status") != "ok" or run.get("rerun_of"):
                continue
            inputs = contract.input_set(run)
            if inputs is None:
                continue
            script = run.get("script") or {}
            observed = {(e.get("path"), e.get("sha256")) for e in [script, *(run.get("modules") or [])]
                        if isinstance(e, dict)}
            linked = any(r.get("type") == "run" and r.get("run_id") == run_id and r.get("relation") == "uses"
                         and r in proposed.get("refs", []) for r in confirmed_sources)
            if (code_file is not None and code_file in observed) or linked:
                uses.add((script.get("path"), inputs))
                keep({"type": "run", "run_id": run_id, "relation": "uses", "basis": "observed",
                      "version_binding": "author"})
        checks = contract.passed_checks(model.journal, proposed, model)
        original = contract.original_run(d, model)
        baseline = contract.input_set(original) if original else None
        distinct = False
        for checked in checks:
            check = checked["details"]
            keep({"type": "event", "event_id": checked["id"], "relation": "supports",
                  "basis": "declared", "version_binding": "author"})
            distinct |= bool(check["boundary"] or contract.input_set(model.run(check["check_run"]["run_id"])) != baseline)
        human_need, estimated, observed_effect = False, False, False
        mismatched = set((model.journal.replay().state_of(proposed["id"]) or {}).get("mismatched_updates", []))
        for event in model.journal.in_order():
            details = event.value.get("details")
            if not isinstance(details, dict) or details.get("asset_id") != d["asset_id"]:
                continue
            if (event.kind == "record_update" and details.get("contract") == contract.CONTRACT
                    and event.value.get("actor") == "human" and not contract.event_problem(event.value)
                    and event.id not in mismatched
                    and details.get("demand", {}).get("repeated_need") is True
                    and details.get("candidate_event_id") == proposed["id"]
                    ):
                human_need = True
                keep({"type": "event", "event_id": event.id, "relation": "supports", "basis": "declared",
                      "version_binding": "author"})
            if (event.kind == "asset_effect" and details.get("candidate_event_id") == proposed["id"]
                    and not contract.event_problem(event.value)
                    and all(r["confirmed"] for r in model.references(event.id))):
                effect_refs = [r for r in event.refs if r.get("type") != "asset_candidate"]
                valid_measurement = any(keep(r) for r in effect_refs)
                estimated |= details["effect_basis"] == "estimated"
                observed_effect |= details["effect_basis"] == "observed" and valid_measurement
                keep({"type": "event", "event_id": event.id, "relation": "supports", "basis": "declared",
                      "version_binding": "author"})
        facts = {"independent_uses": len(uses), "human_repeated_need": human_need,
                 "passed_check": bool(checks), "passed_distinct_or_boundary": distinct,
                 "source_confirmed": bool(confirmed_sources), "problem": d["problem"],
                 "artifact_kind": d["artifact_kind"], "conditions": d["conditions"].strip(),
                 "exclusions": d["exclusions"].strip(), "estimated_effect": estimated,
                 "observed_effect": observed_effect}
        result = asset_policy.assessed(self.policy, facts, min_score=min_score)
        return {**result, "facts": facts, "evidence": [evidence[k] for k in sorted(evidence)],
                "as_of_seq": model.as_of_seq, "context_version": self.context_version(model.journal)}

    def set_mode(self, mode, *, scope="project"):
        return asset_suggestions.set_mode(self, mode, scope=scope)

    def suggestion_status(self, *, journal=None, marker=None, session=None):
        return asset_suggestions.status(self, journal=journal, marker=marker, session=session)

    def publish_suggestion(self, *, surface="cli_text_status"):
        return asset_suggestions.publish(self, surface=surface)

    def review(self, asset_id=None, *, surface="sdk") -> dict:
        self.writer.admit("review assets")
        result = self.list(asset_id)
        returned = []
        with self.writer.locked("review assets") as held:
            model = self.model(held.journal)
            for item in result["assets"]:
                versions = contract.candidate_versions(held.journal, item["asset_id"])
                if not versions:
                    continue
                proposed = versions[-1]
                assessed = self.assessment(proposed, model)
                assessed["requirements"]["identity"] = asset_suggestions.identity_clear(model, proposed, assessed)
                assessed["duplicate_suspicion"] = not assessed["requirements"]["identity"]
                details = {"contract": contract.CONTRACT, "asset_id": item["asset_id"],
                           "candidate_event_id": proposed["id"], "trigger": "requested", "surface": surface,
                           **{k: assessed[k] for k in ("as_of_seq", "context_version", "evidence", "scores",
                                                       "requirements", "policy_version")}}
                event = held.append("asset_notification", f"Asset candidate: {proposed['details']['title']}",
                                    refs=contract.semantic_refs("asset_notification", details), details=details,
                                    requires=contract.SINCE)
                returned.append({"candidate": proposed, "assessment": assessed, "notification": event})
        return {"assets": returned, "requested": True, "model_calls": 0}

    def update(self, event_id: str, *, expected: str, new: str, text: str, details=None) -> dict:
        self.writer.admit("update an asset")
        if details is not None and not isinstance(details, dict):
            raise writer.AnalyticsError("bad_asset", "asset update details is a JSON object")
        details = dict(details or {})
        reserved = set(details) & {"contract", "asset_id", "candidate_event_id", "expected_state", "new_state",
                                   "registration", "promotion", "scores", "evidence", "as_of_seq", "context_version",
                                   "requirements", "suggestion_history"}
        if reserved:
            raise writer.AnalyticsError("bad_asset", "asset update targets and bookkeeping are generated by the application",
                                        details={"reserved_fields": sorted(reserved)})
        if new == "registered":
            raise writer.AnalyticsError("registration_required", "register an asset explicitly before recording registered")
        with self.writer.locked("update an asset") as held:
            event = held.journal.event_of(event_id)
            if event is None or event.kind != "asset_candidate":
                raise writer.AnalyticsError("unknown_asset", f"no exact asset candidate {event_id!r}")
            current = self._state(held.journal, event.value)
            if expected != current or ((expected, new) not in _STEPS and not
                                       (expected == new and set(details) <= {"demand", "identity"} and details)):
                raise writer.AnalyticsError("state_conflict", f"asset {event_id} is {current}; {expected} -> {new} was not written")
            model = self.model(held.journal)
            try:
                contract.promotion(event.value, new, held.journal, model)
                reasons = details.get("reason_items", [])
                if not isinstance(reasons, list) or any(r not in contract.REASONS for r in reasons):
                    raise contract.InvalidAsset("reason_items names the adopted score/required conditions")
                if "demand" in details and (not isinstance(details["demand"], dict) or
                                             type(details["demand"].get("repeated_need")) is not bool):
                    raise contract.InvalidAsset("demand.repeated_need is boolean")
                if "identity" in details:
                    identity = details["identity"]
                    if not isinstance(identity, dict) or identity.get("relation") not in ("duplicate_of", "merged_into"):
                        raise contract.InvalidAsset("identity relation is duplicate_of or merged_into")
                    target = contract.exact(identity.get("target"), "asset_candidate")
                    if target["asset_id"] == event.value["details"]["asset_id"] or not model.reference(target)["confirmed"]:
                        raise contract.InvalidAsset("identity refers to a confirmed different candidate")
                    details["identity"] = {"relation": identity["relation"], "target": target}
            except contract.InvalidAsset as exc:
                raise _error(exc) from exc
            supplied = {**details, "contract": contract.CONTRACT, "asset_id": event.value["details"]["asset_id"],
                        "candidate_event_id": event_id, "expected_state": expected, "new_state": new}
            if new in ("rejected", "deferred"):
                assessed = self.assessment(event.value, model)
                supplied.update({k: assessed[k] for k in ("as_of_seq", "context_version", "evidence", "scores", "requirements")})
                supplied["requirements"]["identity"] = asset_suggestions.identity_clear(model, event.value, assessed)
                supplied["suggestion_history"] = "analysis_assets.suggestions/1"
            if expected != new and new in ("trial", "validated"):
                supplied["promotion"] = contract.promotion_proof(event.value, new, held.journal, model)
            written = held.append("record_update", text, refs=[contract.candidate_ref(supplied, "updates")],
                                  details=supplied, requires=(asset_suggestions.SINCE if new in ("rejected", "deferred") else contract.SINCE))
        return {"event": written, "state_before": expected, "state_after": new}

    def _contents(self, proposed: dict) -> list[tuple[str, bytes, dict]]:
        output = []
        for item in contract.files(proposed["details"]):
            self._safe(f"assets/candidates/{proposed['details']['asset_id']}/{proposed['id']}/{item['path']}")
            source = self._safe(item["ref"]["path"])
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != item["ref"]["digest"]["sha256"]:
                raise writer.AnalyticsError("asset_changed", f"artifact file {item['path']!r} changed since its recorded version")
            output.append((item["path"], data, item["ref"]))
        if not output:
            raise writer.AnalyticsError("bad_asset", "registration needs artifact files")
        if proposed["details"]["artifact_kind"] == "skill":
            try:
                text = next(data for p, data, _ in output if p == "SKILL.md").decode("utf-8")
            except UnicodeError as exc:
                raise writer.AnalyticsError("bad_asset", "SKILL.md is UTF-8 text") from exc
            if not text.strip():
                raise writer.AnalyticsError("bad_asset", "SKILL.md has non-empty use instructions")
        return output

    def _destination(self, proposed: dict, name: str) -> str:
        kind = proposed["details"]["artifact_kind"]
        return f".aelix/skills/{name}" if kind == "skill" else f"lib/{name}.py" if kind in ("module", "function") else f"lib/{name}"

    @staticmethod
    def _write_files(directory: Path, contents) -> None:
        directory.mkdir(parents=True, exist_ok=False)
        for relative, data, _ in contents:
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)

    def _matching_receipt(self, proposed: dict, destination: str, digest: str) -> tuple[Path, dict] | None:
        base = self._safe(".analytics/asset-registrations", directory=True)
        if not base.is_dir():
            return None
        for directory in sorted(base.iterdir()):
            if directory.is_symlink() or not directory.is_dir():
                continue
            path = self._safe((directory / "receipt.json").relative_to(self.root).as_posix())
            try:
                receipt = json.loads(path.read_bytes())
            except (OSError, ValueError):
                continue
            if (isinstance(receipt, dict) and receipt.get("state") == "published"
                    and receipt.get("schema") == "analytics.asset_registration/1"
                    and receipt.get("registration_id") == directory.name and receipt.get("candidate_event_id") == proposed["id"]
                    and receipt.get("asset_id") == proposed["details"]["asset_id"] and receipt.get("destination") == destination
                    and receipt.get("artifact_kind") == proposed["details"]["artifact_kind"]
                    and receipt.get("source") == f"assets/candidates/{proposed['details']['asset_id']}/{proposed['id']}"
                    and isinstance(receipt.get("files"), list) and receipt["files"]
                    and all(isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("sha256"), str)
                            and contract.SHA.fullmatch(f["sha256"]) for f in receipt["files"])
                    and receipt.get("artifact_digest") == digest):
                return path, receipt
        return None

    def _verify_published(self, receipt: dict, contents) -> None:
        destination = self._safe(receipt["destination"], directory=receipt["artifact_kind"] in ("package", "skill"))
        expected = receipt["files"]
        if expected != [{"path": p, "sha256": hashlib.sha256(data).hexdigest()} for p, data, _ in contents]:
            raise writer.AnalyticsError("registration_exists", "published receipt does not own this candidate's file versions")
        for relative, data, _ in contents:
            target = destination if destination.is_file() else self._safe(f"{receipt['destination']}/{relative}")
            if self._sha(target) != hashlib.sha256(data).hexdigest():
                raise writer.AnalyticsError("registration_exists", "destination differs from the owned published receipt")
        if destination.is_dir():
            actual = []
            for parent, dirs, names in os.walk(destination, followlinks=False):
                for entry in dirs:
                    self._safe((Path(parent) / entry).relative_to(self.root).as_posix(), directory=True)
                actual.extend((Path(parent) / f).relative_to(destination).as_posix() for f in names)
            if sorted(actual) != sorted(f["path"] for f in expected):
                raise writer.AnalyticsError("registration_exists", "destination has files outside the owned published receipt")

    def register(self, event_id: str, *, name: str) -> dict:
        self.writer.admit("register an asset")
        try:
            contract.name(name)
        except contract.InvalidAsset as exc:
            raise _error(exc) from exc
        lock = self._safe(".analytics/asset-registration.lock")
        with self.writer.platform.locks.exclusive(lock), self.writer.locked("register_asset") as held:
            proposed = held.journal.event_of(event_id)
            if proposed is None or proposed.kind != "asset_candidate":
                raise writer.AnalyticsError("unknown_asset", f"no exact candidate {event_id!r}")
            proposed = proposed.value
            state = self._state(held.journal, proposed)
            if state != "validated":
                raise writer.AnalyticsError("state_conflict", f"registration requires validated; {event_id} is {state}")
            try:
                contract.promotion(proposed, "validated", held.journal, self.model(held.journal))
            except contract.InvalidAsset as exc:
                raise _error(exc) from exc
            contents = self._contents(proposed)
            digest = contract.bundle_digest(proposed["details"])
            destination = self._destination(proposed, name)
            dest = self._safe(destination, directory=proposed["details"]["artifact_kind"] in ("package", "skill"))
            owned = self._matching_receipt(proposed, destination, digest)
            if dest.exists() and owned is None:
                raise writer.AnalyticsError("registration_exists", f"destination {destination!r} exists without this operation's published receipt")
            held.require(contract.SINCE)
            if owned is None:
                registration_id = secrets.token_hex(16)
                own = self._safe(f".analytics/asset-registrations/{registration_id}", directory=True)
                own.mkdir(parents=True, exist_ok=False)
                path = own / "receipt.json"
                source = f"assets/candidates/{proposed['details']['asset_id']}/{event_id}"
                receipt = {"schema": "analytics.asset_registration/1", "registration_id": registration_id,
                           "candidate_event_id": event_id, "asset_id": proposed["details"]["asset_id"],
                           "artifact_kind": proposed["details"]["artifact_kind"], "artifact_digest": digest,
                           "destination": destination, "source": source, "state": "prepared",
                           "files": [{"path": p, "sha256": hashlib.sha256(data).hexdigest()} for p, data, _ in contents],
                           "source_refs": [ref for _, _, ref in contents]}
                writer.write_json(path, receipt, platform=self.writer.platform)
                preserved = self._safe(source, directory=True)
                if preserved.exists():
                    if any(self._sha(preserved / p) != hashlib.sha256(data).hexdigest() for p, data, _ in contents):
                        raise writer.AnalyticsError("asset_changed", "preserved candidate bundle differs from this version")
                else:
                    self._write_files(preserved, contents)
                staged = own / "files"
                self._write_files(staged, contents)
                publication = staged / contents[0][0] if proposed["details"]["artifact_kind"] in ("module", "function") else staged
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    self.writer.platform.files.rename_new(publication, dest)
                    receipt["state"] = "published"
                    writer.write_json(path, receipt, platform=self.writer.platform)
                except OSError as exc:
                    raise writer.AnalyticsError("registration_incomplete", str(exc), details={"destination": destination,
                                                "receipt": path.relative_to(self.root).as_posix(), "recorded": False}) from exc
            else:
                path, receipt = owned
                self._verify_published(receipt, contents)
            self.writer.admit("complete asset registration")
            loader = {"result": "not_run", "reason": "no host loader supplied"}
            if proposed["details"]["artifact_kind"] == "skill" and self.skill_discovery is not None:
                loader = self.skill_discovery(dest)
                if not isinstance(loader, dict) or loader.get("result") != "pass":
                    raise writer.AnalyticsError("registration_incomplete", "the project skill loader did not discover this version",
                                                details={"destination": destination, "loader": loader, "recorded": False})
            details = {"contract": contract.CONTRACT, "asset_id": proposed["details"]["asset_id"],
                       "candidate_event_id": event_id, "expected_state": "validated", "new_state": "registered",
                       "registration": {**receipt, "receipt": path.relative_to(self.root).as_posix(),
                                        "loader_discovery": loader,
                                        "check_events": [c["id"] for c in contract.passed_checks(held.journal, proposed, self.model(held.journal))]}}
            try:
                event = held.append("record_update", f"Registered analysis asset {name}",
                                    refs=[contract.candidate_ref(details, "updates")], details=details,
                                    requires=contract.SINCE)
            except (OSError, writer.AnalyticsError) as exc:
                raise writer.AnalyticsError("registration_incomplete", "files were published but registration was not recorded",
                                            details={"destination": destination, "receipt": path.relative_to(self.root).as_posix(),
                                                     "recorded": False}) from exc
            receipt.update(state="recorded", registration_event_id=event["id"])
            warning = None
            try:
                writer.write_json(path, receipt, platform=self.writer.platform)
            except OSError as exc:
                # The canonical journal already completed registration. A private recovery receipt
                # failing to catch up must not report that canonical success as unregistered.
                warning = {"code": "registration_receipt_not_updated", "message": str(exc)}
        return {"event": event, "registered": True, "registration": details["registration"], "model_calls": 0,
                "warnings": [warning] if warning else []}
