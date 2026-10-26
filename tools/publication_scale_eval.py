"""Measure a publication of a large project (PUBLICATION-BUILD 12.7, #20 PR5): times, sizes and pages.

    python tools/publication_scale_eval.py --project DIR --out FILE

DIR is a current-format project, for instance the #41 M fixture (`tools/eval_fixtures.py scale --scale M
--seed 0`) copied into the current format with `analytics migrate`. For each profile it times reading the records once (the snapshot), the in-memory
preview over that snapshot, the saved build and, apart, the package check (leak, link and name checks) over the same bytes,
and records the package's file count, bytes, pages, largest page, timeline pages and figure payloads
(how many exceed the 3,000,000-byte "Load figure" gate, 7.7). When the leak check stops a profile's preview
(design 11.4-11.6: an included text names something the copy leaves out), the item it names is excluded and the
preview is tried again, as a person would with `--exclude`; the exclusions and their reasons are recorded.
The build writes into DIR (a publication folder and its completion event); run it on a copy. It is a record of
what was measured, not a gate.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK / "src"))
from aelix_analytics import core  # noqa: E402
from aelix_analytics.publishing import build, checks  # noqa: E402
from aelix_analytics.publishing.model import PublicationError  # noqa: E402
from aelix_analytics.publishing.projection import ShareRequest  # noqa: E402
from aelix_analytics.publishing.snapshot import capture_snapshot  # noqa: E402

GATE = 3_000_000


def shape(files):
    pages = {name: len(data) for name, data in files.items() if name.endswith(".html")}
    payloads = sorted((len(data) for name, data in files.items() if name.startswith("assets/figures/")), reverse=True)
    largest = max(pages.items(), key=lambda item: item[1]) if pages else (None, 0)
    return {
        "files": len(files), "bytes": sum(map(len, files.values())), "pages": len(pages),
        "largest_page": {"path": largest[0], "bytes": largest[1]},
        "timeline_pages": sum(name.startswith("pages/timeline/") for name in pages),
        "figure_payloads": len(payloads), "largest_payload_bytes": payloads[0] if payloads else 0,
        "payloads_over_gate": sum(size > GATE for size in payloads),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    p = core.Project(args.project.resolve())
    origin = core.Origin("system", "cli")
    source = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PACK, capture_output=True, text=True,
                            encoding="utf-8", check=False).stdout.strip()
    result = {"source_commit": source, "kit_version": core.KIT_VERSION, "python": platform.python_version(),
              "platform": platform.platform(), "machine": platform.machine(), "profiles": {}}
    model = core.read_model(p)
    result["project"] = {"events": len(model.journal.events), "runs": len(model.runs())}
    for profile in ("read", "evidence"):
        exclude, stops = [], []
        started = time.perf_counter()
        snapshot = capture_snapshot(p)
        snapshot_s = time.perf_counter() - started
        while True:
            started = time.perf_counter()
            try:
                _summary, files, private = build._assemble(p, ShareRequest(), profile=profile, exclude=exclude,
                                                           snapshot=snapshot)
            except PublicationError as exc:
                source = exc.details.get("source") or {}
                if exc.code != "leak_found" or not source.get("identity") or len(stops) >= 200:
                    raise
                stops.append({"category": source["category"], "field": source.get("field"),
                              "seconds": round(time.perf_counter() - started, 2)})
                exclude.append(source["identity"])
                print(f"{profile}: leak stop {len(stops)} at {source['category']}.{source.get('field')}", file=sys.stderr)
                continue
            break
        preview_s = time.perf_counter() - started
        started = time.perf_counter()
        checks.check_package(files, private["roles"], policy=private["policy"], pinned=private["pinned"],
                             parquet_extractor=build.parquet_strings)
        check_s = time.perf_counter() - started
        started = time.perf_counter()
        built = build.build_publication(p, profile=profile, origin=origin, exclude=exclude)
        build_s = time.perf_counter() - started
        result["profiles"][profile] = {
            "snapshot_seconds": round(snapshot_s, 2), "preview_seconds": round(preview_s, 2),
            "package_check_seconds": round(check_s, 2),
            "build_seconds": round(build_s, 2), "ok": built["ok"], "included": built["included"],
            "leak_stops": len(stops), "leak_stop_fields": sorted({f"{s['category']}.{s['field']}" for s in stops}),
            "not_included": built["not_included"], **shape(files),
        }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
