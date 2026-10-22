"""Measure a small synthetic publication's bytes, identity separation and actual completion."""

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
from aelix_analytics import core, workspace, workspace_relations  # noqa: E402
from aelix_analytics.publishing import build, checks, package, state  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new evidence directory")
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    source = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PACK, text=True, encoding="utf-8"
    ).strip()
    dirty = bool(subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=PACK))
    origin = core.Origin("human", "cli", "declared")
    p = core.init_project(out / "project", title="Synthetic publication", origin=origin)
    core.record(p, "finding", "한국어 e\u0301 public finding with original text.", origin=origin)
    hidden = core.record(p, "finding", "EXCLUDED_PUBLICATION_CANARY_784320", origin=origin).event[
        "id"
    ]
    core.sync_context(p, origin=core.system_origin(origin))
    observations = []
    assertions = {}

    def tree():
        return {
            f.relative_to(p.root).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in p.root.rglob("*")
            if f.is_file()
        }

    for profile in ("read", "evidence"):
        before = tree()
        preview, _files = build.preview_package(p, profile=profile, exclude=[hidden])
        assertions[profile + "_preview_no_writes"] = tree() == before
        copies = []
        for attempt in range(2):
            start = time.perf_counter()
            result = build.build_publication(p, profile=profile, exclude=[hidden], origin=origin)
            elapsed = time.perf_counter() - start
            directory = p.root / result["path"]
            verified = checks.verify_package(directory)
            event = next(
                e.value
                for e in core.read_model(p).journal.events
                if e.kind == "publication"
                and e.value["details"]["publication_id"] == result["publication_id"]
            )
            raw = (directory / "publication.json").read_bytes()
            assertions[f"{profile}_{attempt}_bound"] = state.verify(
                directory, model=core.read_model(p)
            )["project_binding_checked"]
            assertions[f"{profile}_{attempt}_canary_absent"] = (
                b"EXCLUDED_PUBLICATION_CANARY_784320"
                not in b"".join(f.read_bytes() for f in directory.rglob("*") if f.is_file())
            )
            observations.append(
                {
                    "profile": profile,
                    "attempt": attempt + 1,
                    "seconds_observed": elapsed,
                    "publication_id": result["publication_id"],
                    "files": result["files"],
                    "bytes": result["bytes"],
                    "manifest_bytes": len(raw),
                    "event_bytes_with_lf": len(json.dumps(event, ensure_ascii=False).encode()) + 1,
                    "package_sha256": verified["package_sha256"],
                    "content_sha256": result["content_sha256"],
                    "record": result["record"],
                    "basis": result["basis"],
                    "checks": result["checks"],
                    "renderer": result["renderer"],
                }
            )
            copies.append((result, directory, event))
        first, second = copies
        assertions[profile + "_new_identity"] = (
            first[0]["publication_id"] != second[0]["publication_id"]
            and first[0]["package_sha256"] != second[0]["package_sha256"]
        )
        assertions[profile + "_equal_content"] = (
            first[0]["content_sha256"] == second[0]["content_sha256"] == preview["content_sha256"]
        )
        assertions[profile + "_basis_monotonic"] = (
            second[0]["basis"]["as_of_seq"] >= first[0]["record"]["seq"]
            and second[0]["basis"]["content_seq"] == first[0]["basis"]["content_seq"]
        )
        archive_a, archive_b = out / (profile + "-a.zip"), out / (profile + "-b.zip")
        for archive in (archive_a, archive_b):
            package.write_zip(second[1], archive, project_root=p.root, binding=second[2]["details"])
        assertions[profile + "_zip_repeat_bytes"] = archive_a.read_bytes() == archive_b.read_bytes()
        assertions[profile + "_zip_verified"] = checks.verify_package(
            archive_a, binding=second[2]["details"]
        )["ok"]
    assertions["all_complete"] = all(
        row["recording_status"] == "complete" and row["folder_status"] == "same"
        for row in state.list_publications(core.read_model(p))["publications"]
    )
    # 같은 원장의 정체성과 현재 전달 폴더를 Workspace에서도 따로 관찰한다.
    selected = observations[0]
    ref = {"type": "publication", "publication_id": selected["publication_id"],
           "digest": {"sha256": selected["package_sha256"]}, "relation": "cites",
           "basis": "declared", "version_binding": "author"}
    citing = core.record(p, "finding", "Publication delivery observation", refs=[ref], origin=origin).event
    folder = p.root / ".analytics/publications" / selected["publication_id"]
    index_file = folder / "index.html"
    original = index_file.read_bytes()
    workspace_observations = []
    for folder_status in ("same", "changed", "missing"):
        if folder_status == "changed":
            index_file.write_bytes(b"changed")
        elif folder_status == "missing":
            index_file.unlink()
            # 실제 폴더 부재를 관찰하되 다른 build 폴더는 건드리지 않는다.
            hidden_folder = folder.with_name("measurement-held-folder")
            folder.rename(hidden_folder)
        journal_before = p.journal.read_bytes()
        workspace.build(p, if_changed=True)
        html = (p.workspace_dir / "index.html").read_text(encoding="utf-8")
        relation = (p.workspace_dir / "relations" / workspace_relations.name(citing["id"])).read_text(encoding="utf-8")
        for page_name, page in (("timeline", html), ("relation", relation)):
            assertions[f"workspace_{folder_status}_{page_name}"] = (
                'data-recording="complete"' in page and f'data-folder="{folder_status}"' in page
                and selected["publication_id"] in page and "Recorded package found" in page)
        assertions[f"workspace_{folder_status}_journal_unchanged"] = p.journal.read_bytes() == journal_before
        assertions[f"workspace_{folder_status}_reference_resolved"] = core.read_model(p).reference(ref)["state"] == "resolved"
        workspace_observations.append({"folder_status": folder_status, "first_html_bytes": len(html.encode("utf-8")),
                                       "relation_html_bytes": len(relation.encode("utf-8"))})
    hidden_folder.rename(folder)
    index_file.write_bytes(original)
    assertions["restored_folder_valid"] = state.observe(core.read_model(p), selected["publication_id"])["folder_status"] == "same"
    result = {
        "schema": "aelix-analytics.publication-build-eval/1",
        "observed_at": datetime.now(UTC).isoformat(),
        "source_commit": source,
        "tracked_dirty": dirty,
        "kit_version": core.KIT_VERSION,
        "python": platform.python_version(),
        "platform": platform.system(),
        "command": "PYTHONPATH=src python tools/publication_build_eval.py --out <new directory>",
        "scope": "small synthetic project; timing observations are not a performance gate or M-scale claim",
        "observations": observations,
        "workspace_observations": workspace_observations,
        "assertions": assertions,
        "hold": all(assertions.values()),
        "not_run": [
            "record-format implementation reviews1+1/correctiondiff1",
            "browser/CSP/owner/other-reader/OS ZIP tools/M-scale",
            "Report/collection sharing (PR4)",
            "model/token measurement",
            "remote final-head CI at measurement time",
        ],
    }
    (out / "publication-build.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ok": result["hold"],
                "source_commit": source,
                "tracked_dirty": dirty,
                "assertions": len(assertions),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["hold"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
