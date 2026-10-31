"""Measure Issue #15 on an existing #41 scale fixture; never generate or rewrite its records.

Generate with tools/eval_fixtures.py, then give a v1 fixture's synthetic project migrated explicitly
(a copy), or a copy of a v2 fixture's project as it is (#24, B6: what this kit writes), and:
python tools/retrieval_eval.py --fixture FIXTURE --project PROJECT --out NEW_DIR --repeats 10
Cold means derived retrieval cache absent, not an OS page-cache flush. Warm repeats in this process.
No model/tokenizer/host runs: bytes are bytes and every token value is not_measured.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import eval_fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aelix_analytics import core  # noqa: E402


class SourceIndex:
    def candidates(self, documents, query, revision, policy):
        return None, {"adapter": "source", "state": "disabled"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timing(project, query, repeats, *, index=None):
    samples, sizes, last = [], [], None
    for _ in range(repeats):
        start = time.perf_counter()
        last = core.retrieve(project, query=query, limit=8, index=index)
        samples.append((time.perf_counter() - start) * 1000)
        sizes.append(len(core.retrieval_text(last).encode("utf-8")))
    ordered = sorted(samples)
    return {"samples_ms": samples, "p50_ms": statistics.median(samples),
            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1], "max_response_bytes": max(sizes),
            "returned": last["returned"], "total": last["total"], "index": last["index"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args(argv)
    fixture, out = Path(args.fixture).resolve(), Path(args.out).resolve()
    if args.repeats < 2 or args.repeats > 100 or (out.exists() and any(out.iterdir())):
        parser.error("repeats must be 2..100 and out must be new or empty")
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("kind") != "scale" or manifest.get("seed") != 0:
        parser.error("use the generator's scale fixture with seed 0")
    fmt = manifest.get("format", "v1")
    if fmt not in ("v1", "v2"):
        parser.error(f"format {fmt!r}: use a v1 or v2 fixture")
    original = fixture / manifest["project"] / "journal.jsonl"
    before = digest(original)
    if fmt == "v2":
        # The fixture's project is in this kit's format: a copy of it is measured, never the fixture.
        copy = Path(args.project).resolve() / "journal.jsonl"
        if copy == original.resolve() or not copy.is_file() or digest(copy) != before:
            parser.error("give a copy of the v2 fixture's project (the measurement writes its cache)")
    p = core.Project(args.project)
    model = core.read_model(p)
    cases = {case["id"]: case["records"] for case in manifest["cases"]}
    long_id = cases["long_korean"]["note"]
    long_text = model.journal.event_of(long_id).value["text"]
    tasks = [("old_finding", "야간 배송", [cases["old_finding"]["finding"]]),
             ("correction", "세종 성장률", list(cases["correction"].values())),
             ("withdrawal", "연간 합계", list(cases["withdrawal"].values())),
             ("long_korean", "센서", [long_id])]
    cache = p.cache / "retrieval.sqlite3"
    if core.admit(p, "measure the synthetic retrieval cache", side=True) is None:
        parser.error("project must be a synthetic copy this kit writes (a v1 fixture's migrated explicitly)")
    out.mkdir(parents=True, exist_ok=True)
    report = {"schema": "aelix-analytics.retrieval-evaluation/1",
              "fixture": {"scale": manifest["scale"], "digest": manifest["digest"],
                          "generator": manifest["generator"],
                          "source_kit_commit": eval_fixtures.V1_KIT_COMMIT if fmt == "v1" else manifest["kit_commit"],
                          "format": fmt, "migrated_copy": fmt == "v1"},
              "environment": eval_fixtures.environment_record(p.root),
              "definitions": {"cold": "derived retrieval cache removed; OS cache not flushed",
                              "warm": "same process, same machine, existing validated index",
                              "rebuild": "corrupt derived SQLite payload, then reconstruction",
                              "response": "complete structured first page, UTF-8 bytes"},
              "tokens": {"status": "not_measured", "reason": "no tokenizer supplied"},
              "model": {"status": "not_run", "reason": "deterministic model-free evaluation"},
              "query_source": "Supplemental lexical queries authored for #15; #41 labels do not declare search queries",
              "limits": "First-page recall is separate from complete paged scope; no semantic-answer or real-model success claim",
              "tasks": []}
    for name, query, expected in tasks:
        cache.unlink(missing_ok=True)
        cold = timing(p, query, 1)
        warm = timing(p, query, args.repeats)
        direct = timing(p, query, args.repeats, index=SourceIndex())
        with contextlib.closing(sqlite3.connect(cache)) as db, db:
            db.execute("UPDATE documents SET text='corrupt projection' WHERE id=?", (expected[0],))
        rebuild = timing(p, query, 1)
        page = core.retrieve(p, query=query, limit=8)
        got = [row["id"] for row in page["items"]]
        started = time.perf_counter()
        complete, pages, all_ids = core.retrieve(p, query=query, limit=100), 0, set()
        while True:
            pages += 1
            all_ids.update(row["id"] for row in complete["items"])
            if not complete["has_more"]:
                break
            complete = core.retrieve(p, **complete["read_more"])
        paged = {"pages": pages, "limit": 100, "returned_unique_ids": len(all_ids),
                 "elapsed_ms": (time.perf_counter() - started) * 1000,
                 "recall": len(all_ids & set(expected)) / len(expected)}
        details = {}
        for record_id in expected:
            detail = core.retrieve(p, id=record_id)
            details[record_id] = {"state": detail["record_state"],
                                  "legacy_resolved_by": detail["legacy_resolved_by"],
                                  "evidence": detail["evidence"]}
        task = {"id": name, "query": query, "expected_ids": expected, "returned_ids": got,
                "first_page_recall": len(set(got) & set(expected)) / len(expected),
                "complete_paged_scope": paged,
                "scope": page["search_scope"], "cold": cold, "warm": warm,
                "source_direct": direct, "rebuild": rebuild, "details": details}
        report["tasks"].append(task)
        core.write_text(out / f"{name}.json", core.retrieval_text(page) + "\n")
    supplemental = core.retrieve(p, query=long_text[:80], limit=8)
    report["known_text_lookup"] = {"purpose": "exact known-text lexical lookup, not an unseen question or semantic-recall trial",
                                   "query": long_text[:80], "expected_id": long_id,
                                   "returned_ids": [row["id"] for row in supplemental["items"]]}
    report["original_journal"] = {"before": before, "after": digest(original)}
    report["original_journal"]["unchanged"] = before == report["original_journal"]["after"]
    report["recall_pass"] = all(task["complete_paged_scope"]["recall"] == 1 for task in report["tasks"])
    core.write_text(out / "retrieval-evaluation.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": report["recall_pass"] and report["original_journal"]["unchanged"],
                      "scale": manifest["scale"], "report": str(out / "retrieval-evaluation.json")}))
    return 0 if report["recall_pass"] and report["original_journal"]["unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
