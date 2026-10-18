"""Measure synthetic Report storage and metadata reads; no generation, renderer or model."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK / "src"))
from aelix_analytics import _platform, core  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new evidence directory")
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    origin = core.Origin("synthetic-author", "sdk", "declared")
    project = core.Project(core.init_project(out / "project", title="Synthetic Report storage", origin=origin).root)
    source = out / "authored"
    source.mkdir()
    (source / "요약.md").write_bytes("# 합성 보고서\r\n원문 e\u0301\r\n".encode())
    (source / "attachment.bin").write_bytes(bytes(range(256)) * 4096)
    measurements = []

    def measure(name, fn):
        before = time.perf_counter()
        result = fn()
        measurements.append({"operation": name, "seconds": time.perf_counter() - before,
                             "response_utf8_bytes": len(json.dumps(result, ensure_ascii=False).encode())})
        return result

    def save(previous, request):
        return core.save_report(project, "synthetic", files=source, entry="요약.md", title="합성 보고서",
                                previous_revision=previous, request_id=request, origin=origin)

    first = measure("save_v1", lambda: save(None, "synthetic-1"))
    revision = first["report"]["revision_id"]
    directory = project.root / "artifacts/reports/synthetic" / revision

    def hashes():
        return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(directory.rglob("*")) if p.is_file()}

    preserved = hashes()
    retry = measure("same_request", lambda: save(None, "synthetic-1"))
    assert retry["outcome"] == "existing"
    (source / "요약.md").write_bytes("# 새 합성 버전\n".encode())
    measure("save_v2", lambda: save(revision, "synthetic-2"))
    measure("list", lambda: core.reports(project))
    measure("show_v1_metadata", lambda: core.reports(project, "synthetic", revision))
    measure("read_v1_file_range", lambda: core.reports(project, "synthetic", revision, file="attachment.bin", file_limit=1024))
    for expected, new in (("current", "withdrawn"), ("withdrawn", "current")):
        measure(new, lambda expected=expected, new=new: core.update_report(project, "synthetic", revision, expected=expected, new=new,
                                                reason="synthetic authored state change", origin=origin))
    assert hashes() == preserved
    result = {
        "schema": "aelix-analytics.report-storage-eval/1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PACK, text=True).strip(),
        "tracked_dirty": bool(subprocess.check_output(["git", "diff", "HEAD", "--name-only"], cwd=PACK)),
        "command": "uv run --locked python tools/report_storage_eval.py --out <new directory>",
        "environment": {"python": platform.python_version(), "platform": sys.platform, "kit_version": core.KIT_VERSION},
        "fixture": {"kind": "synthetic authored files", "file_count": 2, "attachment_bytes": 1048576},
        "measurements": measurements,
        "assertions": {"same_request_existing": True, "v1_hashes_unchanged_after_v2_and_state_updates": True},
        "preserved_v1_sha256": preserved,
        "not_run": ["generation", "ReportRenderer", "Workspace UI", "real model", "owner acceptance"],
        "tokens": {"status": "not_measured"},
    }
    _platform.write_text(out / "report-storage.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    rows = ["# Synthetic Report storage measurement", "", f"Source: `{result['source_commit']}`; tracked dirty: {result['tracked_dirty']}.", "",
            "| Operation | Seconds | Response UTF-8 bytes |", "|---|---:|---:|"]
    rows.extend(f"| {m['operation']} | {m['seconds']:.6f} | {m['response_utf8_bytes']} |" for m in measurements)
    rows.extend(["", "Assertions passed: same request reused; v1 bytes unchanged after v2 and withdrawal/restore.", "",
                 "Synthetic local storage only. Generation, renderer, Workspace UI, actual model and owner acceptance not run. Tokens not measured.", ""])
    _platform.write_text(out / "report-storage.md", "\n".join(rows))
    print(json.dumps({"ok": True, "assertions": result["assertions"], "source_commit": result["source_commit"]}))


if __name__ == "__main__":
    main()
