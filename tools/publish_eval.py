"""Measure `analytics publish` (PUBLISH-FOLDER 4, 6, 8; #21 PR-2): times, interruptions, two at once.

    python tools/publish_eval.py --work DIR --out FILE [--m-project DIR] [--volume DIR] [--pairs N]

DIR is an empty scratch folder this tool fills with small projects and publish targets. It records:
- a small project's publish times, five times over: first, the same build again (reuse), a newer
  build, the older one again (not_newer);
- the interruption at each of the eight step boundaries, for a first and a later publish: what the
  receipt says each pointer holds against what the files hold, and the rerun's result;
- N pairs of processes publishing an older and a newer build to one target at the same moment, with
  the target's lock and with a lock the file system does not have (injected), and how each ended;
- with --m-project (a current-format project with two completed builds, for instance the #20 PR5 M
  copy), the time to publish the first, the second, and the first again, with files and bytes;
- with --volume (a mounted folder, for instance an exFAT image), two publishes to a new folder there.
It is a record of what was measured, not a gate; one run is not p50/p95.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK / "src"))
from aelix_analytics import core, relations  # noqa: E402
from aelix_analytics.publishing import build, publish  # noqa: E402

ORIGIN = core.Origin("human", "cli")
BOUNDARIES = ("init", "lock", "transfer", "check", "commit", "final_check", "switch", "channel_page")
CHILD = """\
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[6])
from aelix_analytics import _platform, core
from aelix_analytics.publishing import publish
project = core.Project(sys.argv[1])
if sys.argv[4] == "unsupported":
    real = project.platform.locks
    class NoTargetLocks:
        exclusive = staticmethod(real.exclusive)
        try_exclusive = staticmethod(real.try_exclusive)
        def exclusive_within(self, path, seconds):
            if Path(path).name == ".analytics-target.lock":
                raise _platform.LockUnavailable(publish.errno.ENOLCK, "no locks here")
            return real.exclusive_within(path, seconds)
    project.platform.locks = NoTargetLocks()
while not os.path.exists(sys.argv[5]):
    time.sleep(0.002)
started = time.perf_counter()
result = publish.publish(project, sys.argv[2], sys.argv[3], origin=core.Origin("human", "cli"))
print(result["outcome"], "receipt" in result, round(time.perf_counter() - started, 4), result["lock"]["state"])
"""


def timed(call):
    started = time.perf_counter()
    result = call()
    return result, round(time.perf_counter() - started, 4)


def latest_of(share):
    path = share / "projects/weekly-sales/latest.json"
    return json.loads(path.read_text(encoding="utf-8"))["latest"] if path.exists() else None


def page_of(share):
    return publish._page_points_to(share / "projects/weekly-sales/index.html")[0]


def small(work):
    rows = {"first": [], "again": [], "newer": [], "older": []}
    files = None
    for rep in range(5):
        p = core.init_project(work / f"small{rep}", title="Weekly sales", origin=ORIGIN)
        a = build.build_publication(p, origin=ORIGIN)["publication_id"]
        b = build.build_publication(p, origin=ORIGIN)["publication_id"]
        share = work / f"share{rep}"
        for key, pid in (("first", a), ("again", a), ("newer", b), ("older", a)):
            result, seconds = timed(lambda p=p, pid=pid, share=share: publish.publish(p, pid, share, origin=ORIGIN))
            rows[key].append({"seconds": seconds, "outcome": result["outcome"], "transfer": result["steps"]["transfer"],
                              "switch": result["steps"]["switch"]})
        files = sum(1 for f in (share / "publications" / a).rglob("*") if f.is_file())
    out = {key: {"median_seconds": statistics.median(r["seconds"] for r in v), "max_seconds": max(r["seconds"] for r in v),
                 "results": sorted({(r["outcome"], r["transfer"], r["switch"]) for r in v})} for key, v in rows.items()}
    out["files_per_version"] = files
    return out


def boundaries(work):
    rows = []
    for first in (True, False):
        for step in BOUNDARIES:
            p = core.init_project(work / f"b-{step}-{first}", title="Weekly sales", origin=ORIGIN)
            share = work / f"bs-{step}-{first}"
            earlier = None
            if not first:
                earlier = build.build_publication(p, origin=ORIGIN)["publication_id"]
                publish.publish(p, earlier, share, origin=ORIGIN)
            pid = build.build_publication(p, origin=ORIGIN)["publication_id"]

            def stop(at, step=step):
                if at == step:
                    raise KeyboardInterrupt

            publish._reached = stop
            try:
                result = publish.publish(p, pid, share, origin=ORIGIN)
            finally:
                publish._reached = lambda at: None
            held = {pid: "new", earlier: "old", None: "missing"}
            row = {"first": first, "step": step, "outcome": result["outcome"], "receipt": "receipt" in result,
                   "latest_json": held.get(latest_of(share), "other"), "channel_page": held.get(page_of(share), "other"),
                   "receipt_pointers": result.get("pointers"), "final_check": result["steps"]["final_check"]}
            again = publish.publish(p, pid, share, origin=ORIGIN)  # the files are read above, before the rerun
            rows.append({**row, "rerun": [again["outcome"], again["steps"]["transfer"], again["steps"]["switch"]]})
    return rows


def pairs(work, count):
    child = work / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    out = {}
    for locks in ("held", "unsupported"):
        rows = []
        for rep in range(count):
            p = core.init_project(work / f"two-{locks}-{rep}", title="Weekly sales", origin=ORIGIN)
            older = build.build_publication(p, origin=ORIGIN)["publication_id"]
            newer = build.build_publication(p, origin=ORIGIN)["publication_id"]
            share, go = work / f"two-share-{locks}-{rep}", work / f"go-{locks}-{rep}"
            runs = [subprocess.Popen([sys.executable, str(child), str(p.root), pid, str(share), locks, str(go), str(PACK / "src")],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
                    for pid in (older, newer)]
            time.sleep(1.0)  # both are past their imports and wait for the same file
            go.write_bytes(b"")
            ended = [run.communicate(timeout=300) for run in runs]
            name = {newer: "newer", older: "older"}
            recorded = relations.publish_attempts(relations.Journal(p.journal.read_bytes()))
            rows.append({"older": ended[0][0].split(), "newer": ended[1][0].split(), "codes": [r.returncode for r in runs],
                         "stderr_tail": [err[-400:] for _out, err in ended if err],
                         "latest": name.get(latest_of(share)), "page": name.get(page_of(share)),
                         "recording": [r["recording_status"] for r in recorded],
                         "staging_left": len(os.listdir(share / ".staging"))})
        out[locks] = rows
    return out


def m_scale(work, source):
    m = work / "m"
    shutil.copytree(source, m, symlinks=True)
    project = core.Project(m)
    pubs = sorted(d.name for d in (m / ".analytics/publications").iterdir() if d.name.startswith("pub-"))
    share = work / "m-share"
    rows = []
    for case, pid in (("first", pubs[0]), ("second", pubs[1]), ("first_again", pubs[0])):
        result, seconds = timed(lambda pid=pid: publish.publish(project, pid, share, origin=ORIGIN))
        receipt = [json.loads(line) for line in project.journal.read_text(encoding="utf-8").splitlines() if line.strip()][-1]
        rows.append({"case": case, "seconds": seconds, "outcome": result["outcome"], "transfer": result["steps"]["transfer"],
                     "switch": result["steps"]["switch"], "receipt_bytes": len(json.dumps(receipt, ensure_ascii=False).encode())})
    for pid in pubs:
        files = [f for f in (share / "publications" / pid).rglob("*") if f.is_file()]
        rows.append({"publication": pid, "files": len(files), "bytes": sum(f.stat().st_size for f in files)})
    return rows


def volume(work, mount):
    p = core.init_project(work / "volume-project", title="Weekly sales", origin=ORIGIN)
    pid = build.build_publication(p, origin=ORIGIN)["publication_id"]
    folder = Path(mount) / f"publish-eval-{os.getpid()}"
    rows = []
    for attempt in ("first", "again"):
        try:
            result = publish.publish(p, pid, folder, origin=ORIGIN)
            rows.append({"attempt": attempt, "outcome": result["outcome"], "failure": result.get("failure"),
                         "receipt": "receipt" in result, "lock": result["lock"],
                         "left": sorted(x.name for x in folder.iterdir()) if folder.exists() else None})
        except Exception as exc:  # noqa: BLE001 - a refusal before the start record is a result here
            rows.append({"attempt": attempt, "refused": f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--m-project", type=Path)
    parser.add_argument("--volume")
    parser.add_argument("--pairs", type=int, default=20)
    args = parser.parse_args()
    if args.work.exists() and any(args.work.iterdir()):
        parser.error("--work must be an empty or new folder")
    args.work.mkdir(parents=True, exist_ok=True)
    out = {"kit_version": core.KIT_VERSION, "python": sys.version.split()[0], "platform": sys.platform,
           "small": small(args.work), "boundaries": boundaries(args.work), "pairs": pairs(args.work, args.pairs)}
    if args.m_project:
        out["m"] = m_scale(args.work, args.m_project)
    if args.volume:
        out["volume"] = volume(args.work, args.volume)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(args.out)}))


if __name__ == "__main__":
    main()
