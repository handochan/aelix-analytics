"""Synthetic lab.json projects for the reader's tests, written record by record.

A `LegacyProject` writes the files a /1 kit leaves — lab.json, journal lines, run.json, dataset
records and the files they name — without running anything, so a test states exactly which records
exist. Journal lines are written as the /1 writer writes them (UTF-8, one JSON object and LF per
line); `raw` appends bytes as they are, for cut-off and appended lines.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# The .gitignore a kit before the new writer (kit 0.8.0) gave a new project: its template's bytes,
# the older names a migration renames (design 6.13).
LEGACY_GITIGNORE = (
    b'.venv/\n'
    b'__pycache__/\n'
    b'.lab/cache/\n'
    b'.lab/lock\n'
    b'.lab/adhoc/\n'
    b'# Generated from runs/, journal.jsonl and CONTEXT.md; rebuild with `analytics render`.\n'
    b'report/\n'
    b'# Data may be private. Remove these lines only for data you are allowed to commit.\n'
    b'data/\n'
    b'runs/*/charts/*.data.parquet\n'
)


def at(minute: int, hour: int = 9, offset: str = "+09:00") -> str:
    """A /1 timestamp on 2026-10-01."""
    return f"2026-10-01T{hour:02d}:{minute:02d}:00.000{offset}"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# The marker of a project a migration made (design 2.4, 5.5): the new format's, over records an
# older kit wrote — what this kit reads, renders and appends to.
MIGRATED_MARKER = {"schema": "analytics.project/2", "project_id": "prj_0000000000000001",
                   "created_at": at(0), "min_writer_version": "0.8.0"}


class LegacyProject:
    """`migrated`: the same records under an analytics.json marker (MIGRATED_MARKER) instead of
    lab.json — a migrated project, which this kit writes to (a lab.json project it only reads)."""

    def __init__(self, root: Path, title: str = "Legacy", *, migrated: bool = False):
        self.root = Path(root)
        self.root.mkdir(parents=True)
        if migrated:
            self.write_json("analytics.json", {**MIGRATED_MARKER, "title": title})
        else:
            self.write_json("lab.json", {"schema": "lab.project/1", "title": title, "created_at": at(0)})
        self.seq = 0
        self.minute = 0

    def write_json(self, rel: str, value) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def file(self, rel: str, data: bytes) -> str:
        """Write a file of the project and return the sha256 of its bytes."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return sha(data)

    def raw(self, data: bytes) -> None:
        with open(self.root / "journal.jsonl", "ab") as fh:
            fh.write(data)

    def line(self, value: dict) -> None:
        self.raw(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n")

    def event(self, kind: str, refs: dict | None = None, *, ts: str | None = None, seq: int | None = None,
              schema: str = "lab.event/1", text: str = "") -> str:
        """Append a /1 event with the next seq (or the one given) and return its id."""
        if seq is None:
            self.seq += 1
            seq = self.seq
        self.minute += 1
        self.line({"schema": schema, "seq": seq, "id": f"e{seq}", "ts": ts or at(self.minute % 60),
                   "kind": kind, "actor": "test", "text": text or kind, "refs": refs or {}})
        return f"e{seq}"

    def run(self, run_id: str, *, script: str = "scripts/a.py", status: str = "ok",
            started_at: str | None = None, inputs=(), outputs=(), charts=(), pid=None,
            finalize_errors=None, schema: str = "lab.run/1", completion: bool = True,
            ts: str | None = None, kit_version: str | None = None) -> str:
        """Write runs/<id>/run.json and, with `completion`, the kind=run event the runner writes after
        it (charts only when the run succeeded). `inputs` and `outputs` are (path, sha256) pairs;
        each chart gets a file under the run directory. With `kit_version` the record has an `env`
        holding it, as a kit's run does; without, it has none (a reader takes it for an older kit's,
        ADR-0006 9)."""
        chart_records = []
        for chart_id in charts:
            rel = f"runs/{run_id}/charts/{chart_id}.plotly.json"
            self.file(rel, b"{}")
            chart_records.append({"id": chart_id, "kind": "plotly", "title": chart_id, "caption": None,
                                  "via": "lab.chart", "files": {"plotly": rel}, "line": 1})
        snapshot = f"runs/{run_id}/code/{script}"
        code = self.file(snapshot, f"# {run_id}\n".encode())
        record = {"schema": schema, "id": run_id, "title": run_id, "status": status,
                  "started_at": started_at or at(self.minute % 60), "actor": "test", "session": None,
                  "rerun_of": None, "script": {"path": script, "sha256": code, "snapshot": snapshot},
                  "modules": [],
                  "inputs": [{"path": p, "bytes": 1, "sha256": h, "via": ["scan"], "external": False}
                             for p, h in inputs],
                  "outputs": [{"path": p, "change": "created", "bytes": 1, "sha256": h, "via": "diff"}
                              for p, h in outputs],
                  "charts": chart_records}
        if pid is not None:
            record["pid"] = pid
        if kit_version is not None:
            record["env"] = {"kit_version": kit_version}
        if finalize_errors:
            record["finalize_errors"] = list(finalize_errors)
        self.write_json(f"runs/{run_id}/run.json", record)
        if completion:
            refs = {"runs": [run_id], "data": [p for p, _ in outputs] + [p for p, _ in inputs]}
            if status == "ok":
                refs["charts"] = [f"{script}::{c}" for c in charts]
            self.event("run", {k: v for k, v in refs.items() if v}, ts=ts)
        return run_id

    def dataset(self, name: str, *, imported_at: str, schema: str = "lab.dataset/1") -> str:
        """Write data/raw/<name>.parquet (placeholder bytes) and its dataset record; returns its path."""
        path = f"data/raw/{name}.parquet"
        digest = self.file(path, name.encode())
        self.write_json(f"data/raw/{name}.meta.json",
                        {"schema": schema, "name": name, "path": path, "sha256": digest, "bytes": len(name),
                         "rows": 1, "columns": [], "description": None, "imported_at": imported_at,
                         "source": {"path": f"/elsewhere/{name}.csv"}})
        return path
