"""Measure synthetic PR2 metadata/partial collections and byte-only rendering separately."""
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
from aelix_analytics.report_renderers import StaticReportRenderer  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new synthetic evidence directory")
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    measurements = []

    def measure(name, fn):
        start = time.perf_counter()
        answer = fn()
        measurements.append({"operation": name, "seconds": time.perf_counter() - start})
        return answer

    origin = core.Origin("synthetic-author", "sdk", "declared")
    project = core.init_project(out / "project", title="Synthetic Report collections", origin=origin)
    source = out / "authored"
    source.mkdir()

    def generate_fixture():
        data = "# 합성 보고서\r\n한글 원문 e\u0301\r\n".encode()
        (source / "요약.md").write_bytes(data)
        return data

    authored = measure("authored_fixture_generation", generate_fixture)

    def save(name, *, previous=None, request="synthetic-1", metadata=None):
        return core.save_report(project, name, files=source, entry="요약.md", title="합성 보고서",
                                previous_revision=previous, request_id=request, metadata=metadata, origin=origin)

    members = []
    for n in range(18):
        answer = measure("save_member_" + str(n), lambda n=n: save("column-" + str(n)))
        members.append({"member_key": "컬럼 " + str(n), "status": "complete", "report_id": "column-" + str(n),
                        "revision_id": answer["report"]["revision_id"]})
    members += [{"member_key": "실패", "status": "failed", "reason": "합성 실패"},
                {"member_key": "미생성", "status": "not_generated", "reason": "합성 미생성"}]

    def metadata():
        return {"collection": {"contract": "report_collection/1", "members": members}, "summary": "한글 e\u0301",
                "scope": {"threshold": 0.5}, "content_status": "partial", "unknown_optional": {"preserved": True}}

    first = measure("save_collection_v1", lambda: save("collection", metadata=metadata()))
    revision = first["report"]["revision_id"]
    directory = project.root / "artifacts/reports/collection" / revision

    def hashes():
        return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.rglob("*") if p.is_file()}

    preserved = hashes()
    assert measure("same_request", lambda: save("collection", metadata=metadata()))["outcome"] == "existing"
    query = measure("collection_metadata_query", lambda: core.reports(project, "collection", revision))
    measure("collection_verified_query", lambda: core.reports(project, "collection", revision, verify_files=True))
    late = save("failed-column")
    members[18] = {"member_key": "실패", "status": "complete", "report_id": "failed-column", "revision_id": late["report"]["revision_id"]}
    second = measure("save_collection_v2", lambda: save("collection", previous=revision, request="synthetic-2", metadata=metadata()))
    renderer = StaticReportRenderer()
    files, paths = {"요약.md": authored}, {"요약.md": "original/요약.md"}
    view = measure("render_v1", lambda: renderer.render(files, entry="요약.md", paths=paths))
    assert measure("render_v1_again", lambda: renderer.render(files, entry="요약.md", paths=paths)) == view
    assert first["report"]["collection_counts"] == {"requested": 20, "complete": 18, "failed": 1, "not_generated": 1}
    assert second["report"]["collection_counts"] == {"requested": 20, "complete": 19, "failed": 0, "not_generated": 1}
    assert hashes() == preserved
    data = {
        "schema": "aelix-analytics.report-collection-eval/1", "recorded_at": datetime.now(UTC).isoformat(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PACK, text=True, encoding="utf-8").strip(),
        "tracked_dirty": bool(subprocess.check_output(["git", "diff", "HEAD", "--name-only"], cwd=PACK)),
        "environment": {"python": platform.python_version(), "platform": sys.platform, "kit_version": core.KIT_VERSION},
        "command": "uv run --locked python tools/report_collection_eval.py --out <new directory>",
        "fixture": {"kind": "synthetic authored reports", "requested": 20, "initial_complete": 18, "authored_bytes": len(authored)},
        "measurements": measurements, "metadata_response_utf8_bytes": len(json.dumps(query, ensure_ascii=False).encode()),
        "rendered_bytes": sum(len(value) for value in view.files.values()), "preserved_v1_sha256": preserved,
        "assertions": {"partial_counts": True, "failed_member_added_as_new_snapshot": True, "same_request_existing": True,
                       "repeat_render_deterministic": True, "v1_bytes_metadata_membership_unchanged": True},
        "not_run": ["actual model/generator", "Workspace integration", "browser UI", "owner acceptance", "publication shareability"],
        "tokens": {"status": "not_measured"},
    }
    _platform.write_text(out / "report-collection.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    text = ["# Synthetic Report collection/renderer measurement", "", "Source: `" + data["source_commit"] + "`.", "",
            "| Operation | Seconds |", "|---|---:|"]
    text += [f"| {row['operation']} | {row['seconds']:.6f} |" for row in measurements]
    text += ["", "Assertions: 20 requested/18 complete/1 failed/1 not_generated; later 19 complete in a new snapshot; old bytes/metadata/members unchanged; same request reused; repeat render deterministic.", "",
             "Fixture generation is a literal file write, not a model or real generator. Workspace/browser/owner/publication acceptance not run. Tokens not measured.", ""]
    _platform.write_text(out / "report-collection.md", "\n".join(text))
    print(json.dumps({"ok": True, "source_commit": data["source_commit"], "assertions": data["assertions"]}))


if __name__ == "__main__":
    main()
