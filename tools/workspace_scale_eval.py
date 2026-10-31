"""Measure Q9's Workspace on a #41 scale fixture (#24, B6): the build, cold and warm, the check that
finds nothing changed, the build's peak memory, the pages it writes, and a browser opening the first
screen. Q10's display case is looked at in the same pages.

    python tools/workspace_scale_eval.py --fixture FIXTURE --project COPY --out NEW_DIR
        [--repeats 10] [--cold-repeats 3] [--browser chromium|none]

FIXTURE is a `tools/eval_fixtures.py scale --format v2` fixture with seed 0 and COPY a copy of its
`project/`: the builds write the copy's Workspace and cache, never the fixture's. Each build runs in a
child process of this interpreter, so its peak memory is its own: VmHWM where /proc/self/status has it
(Linux, where ru_maxrss would carry the parent's over the exec), else ru_maxrss (macOS); not on Windows.
- cold: the kit's derived directories emptied first (the hash cache and the Workspace); the OS's file
  cache is not flushed, so a cold sample is the kit's cold, not the disk's;
- warm: the cache and the Workspace there, a full build;
- unchanged: `build(if_changed=True)` with nothing changed: the detached `render --if-changed` the aelix
  extension starts after each prompt meets it (a run's end builds in full; `analytics open` opens the
  page that is there).
The time inside the child is the build's; the child's wall time adds the interpreter and the imports.
`analytics render` adds `uv run` on top, which is not measured here. The browser (Playwright's
Chromium, the `browser` dependency group) opens `workspace/index.html` from file:// in a new context
per sample and waits for `load`; navigation timing gives DOMContentLoaded and load. Page errors and
dialogs are counted. Bytes read from disk are not measured: the standard library has no portable
counter. The display case (manifest case `display_safety`, a text that looks like markup, a path and a
template) is judged twice, apart: `static` — some page holds the whole text as text and no page made
any part of it an element, an attribute or a comment (an HTML parser over every page); `browser` — in
each of those pages Chromium's document text holds the whole text and no element, link or comment was
made of it, with no dialog (`not_run` without the browser).

Writes NEW_DIR/workspace-scale-evaluation.json and prints a one-line summary. Exit status: 0 every
build succeeded, the unchanged check skipped the build, the display case's static judgment and, when
the browser ran, its browser judgment held, no page error, and the copy's journal is unchanged; 1
otherwise; 2 a usage error. The times are a record, not a gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import eval_fixtures

PACK = Path(__file__).resolve().parents[1]
SCHEMA = "aelix-analytics.workspace-scale-evaluation/1"
# What a sample's child process runs: one build, timed inside, with its peak memory.
CHILD = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
started = time.perf_counter()
from aelix_analytics import core, workspace
imported = time.perf_counter()
result = workspace.build(core.Project(sys.argv[2]), if_changed=sys.argv[3] == "1")
built = time.perf_counter()
peak = None
try:
    with open("/proc/self/status", encoding="ascii") as status:
        peak = next((int(line.split()[1]) * 1024 for line in status if line.startswith("VmHWM:")), None)
except OSError:
    pass
if peak is None:
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = peak if sys.platform == "darwin" else peak * 1024
    except ImportError:
        pass
print(json.dumps({"import_ms": (imported - started) * 1000, "build_ms": (built - imported) * 1000,
                  "skipped": bool(result.get("skipped")), "peak_rss_bytes": peak}))
"""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"n": len(values), "p50": statistics.median(values),
            "p95": ordered[math.ceil(len(ordered) * .95) - 1], "max": ordered[-1]}


def build_sample(project: Path, if_changed: bool) -> dict:
    started = time.perf_counter()
    done = subprocess.run([sys.executable, "-c", CHILD, str(PACK / "src"), str(project), "1" if if_changed else "0"],
                          capture_output=True, encoding="utf-8", timeout=3600)
    wall = (time.perf_counter() - started) * 1000
    if done.returncode:
        raise SystemExit(f"workspace_scale_eval: the build failed ({done.returncode}):\n{done.stderr[-2000:]}")
    sample = json.loads(done.stdout.strip().splitlines()[-1])
    sample["wall_ms"] = wall
    return sample


def series(samples: list[dict]) -> dict:
    peaks = [s["peak_rss_bytes"] for s in samples if s["peak_rss_bytes"] is not None]
    return {"samples": samples, "build_ms": summary([s["build_ms"] for s in samples]),
            "wall_ms": summary([s["wall_ms"] for s in samples]),
            "peak_rss_bytes": {"max": max(peaks)} if peaks else
            {"status": "not_measured", "reason": "neither /proc/self/status nor the resource module here"},
            "skipped": [s["skipped"] for s in samples]}


def pages(workspace: Path) -> dict:
    files = [p for p in workspace.rglob("*") if p.is_file()]
    html_pages = {p.relative_to(workspace).as_posix(): p.stat().st_size for p in files if p.suffix == ".html"}
    largest = max(html_pages.items(), key=lambda item: item[1]) if html_pages else (None, 0)
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files), "pages": len(html_pages),
            "index_bytes": (workspace / "index.html").stat().st_size,
            "largest_page": {"path": largest[0], "bytes": largest[1]}}


# In the browser: the whole text is in the document's text, and no element, link or comment was made
# of it (the parts that would make one: a script, an image and an SVG with handlers, a javascript: link,
# a comment).
DISPLAY_DOM = """(text) => {
  const comments = [];
  const walk = document.createTreeWalker(document, NodeFilter.SHOW_COMMENT);
  while (walk.nextNode()) comments.push(walk.currentNode.data.trim());
  return {
    text_shown: document.body.textContent.includes(text),
    elements_made: [...document.querySelectorAll('script')].filter(s => s.textContent.includes('alert("fixture")')).length
      + document.querySelectorAll('img[src="x"], img[onerror], svg[onload], [href^="javascript:alert(3)"]').length
      + comments.filter(c => c === '주석').length,
  };
}"""
SCRIPT_TYPES = ("", "text/javascript", "application/javascript", "module")


class Page(HTMLParser):
    """A page's text outside scripts and styles, and what the display case would have made of itself
    had it been left as markup: a running script, an image or SVG with a handler, a javascript: link, a
    comment."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.made, self._inside = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self._inside.append((tag, (attrs.get("type") or "").lower()))
        if ((tag == "img" and (attrs.get("src") == "x" or "onerror" in attrs)) or (tag == "svg" and "onload" in attrs)
                or (attrs.get("href") or "").startswith("javascript:alert(3)")):
            self.made.append(tag)

    def handle_endtag(self, tag):
        if self._inside and self._inside[-1][0] == tag:
            self._inside.pop()

    def handle_data(self, data):
        if not self._inside:
            self.text.append(data)
        elif self._inside[-1][0] == "script" and self._inside[-1][1] in SCRIPT_TYPES and 'alert("fixture")' in data:
            self.made.append("script")

    def handle_comment(self, data):
        if data.strip() == "주석":
            self.made.append("comment")


def display_case(manifest: dict, workspace: Path) -> dict:
    case = next(c for c in manifest["cases"] if c["id"] == "display_safety")
    text = next(c["text"] for c in case["checks"] if c["type"] == "event_text")
    made, holding = {}, []
    for page in sorted(workspace.rglob("*.html")):
        parsed = Page()
        parsed.feed(page.read_text(encoding="utf-8"))
        parsed.close()
        rel = page.relative_to(workspace).as_posix()
        if parsed.made:
            made[rel] = parsed.made
        if text in "".join(parsed.text):
            holding.append(rel)
    return {"record": case["records"]["note"], "text": text, "pages_with_markup": made,
            "pages_with_text": holding, "static": not made and bool(holding)}


def browser_samples(workspace: Path, repeats: int, display: dict) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "not_run", "reason": "playwright is not installed (uv sync --group browser)"}
    index = (workspace / "index.html").resolve().as_uri()
    timing = ("() => { const n = performance.getEntriesByType('navigation')[0];"
              " return {dom_content_loaded_ms: n.domContentLoadedEventEnd, load_ms: n.loadEventEnd}; }")
    results, errors, dialogs = [], [], []
    with sync_playwright() as play:
        launched = time.perf_counter()
        browser = play.chromium.launch()
        launch_ms = (time.perf_counter() - launched) * 1000
        for _ in range(repeats):
            context = browser.new_context()
            page = context.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
            started = time.perf_counter()
            page.goto(index, wait_until="load")
            goto_ms = (time.perf_counter() - started) * 1000
            results.append({"goto_ms": goto_ms, **page.evaluate(timing),
                            "title": page.title(), "text_chars": len(page.inner_text("body"))})
            context.close()
        shown = []
        for rel in display["pages_with_text"]:
            # Each page that holds the text: in the document as text (a closed tab's panel counts) and
            # no element made of it.
            context = browser.new_context()
            page = context.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
            page.goto((workspace / rel).resolve().as_uri(), wait_until="load")
            shown.append({"page": rel, **page.evaluate(DISPLAY_DOM, display["text"])})
            context.close()
        version = browser.version
        browser.close()
    return {"name": "chromium", "version": version, "launch_ms": launch_ms,
            "definition": "a new browser context per sample; file:// index.html until the load event",
            "samples": results, "goto_ms": summary([r["goto_ms"] for r in results]),
            "load_ms": summary([r["load_ms"] for r in results]),
            "dom_content_loaded_ms": summary([r["dom_content_loaded_ms"] for r in results]),
            "page_errors": errors, "dialogs": dialogs, "display_pages": shown}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--cold-repeats", type=int, default=3)
    parser.add_argument("--browser", choices=("chromium", "none"), default="chromium")
    args = parser.parse_args(argv)
    fixture, project, out = args.fixture.resolve(), args.project.resolve(), args.out.resolve()
    if not 2 <= args.repeats <= 100 or not 1 <= args.cold_repeats <= 20:
        parser.error("repeats must be 2..100 and cold repeats 1..20")
    if out.exists() and any(out.iterdir()):
        parser.error("out must be new or empty")
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("kind") != "scale" or manifest.get("seed") != 0 or manifest.get("format") != "v2":
        parser.error("use the generator's scale fixture with seed 0 and --format v2")
    if project == (fixture / manifest["project"]).resolve():
        parser.error("give a copy of the fixture's project: the builds write its Workspace and cache")
    journal = project / "journal.jsonl"
    if not journal.is_file() or digest(journal) != digest(fixture / manifest["project"] / "journal.jsonl"):
        parser.error("the project is not a copy of this fixture's project (its journal differs)")
    before = digest(journal)
    out.mkdir(parents=True, exist_ok=True)
    report = {"schema": SCHEMA,
              "fixture": {"scale": manifest["scale"], "format": manifest["format"], "digest": manifest["digest"],
                          "kit_commit": manifest["kit_commit"], "counts": manifest["counts"]},
              "environment": eval_fixtures.environment_record(project, cache_state="unknown"),
              "definitions": {"cold": "kit's hash cache and Workspace emptied first; OS cache not flushed",
                              "warm": "cache and Workspace present; a full build",
                              "unchanged": "build(if_changed=True) with nothing changed since the last build",
                              "build_ms": "workspace.build inside the child process",
                              "wall_ms": "the child process: interpreter, imports and build; not `uv run`",
                              "files_read_bytes": "not measured: no portable counter in the standard library"}}
    cold = []
    for _ in range(args.cold_repeats):
        eval_fixtures.clear_derived(project, "v2")
        cold.append(build_sample(project, False))
    report["cold"] = series(cold)
    report["warm"] = series([build_sample(project, False) for _ in range(args.repeats)])
    report["unchanged"] = series([build_sample(project, True) for _ in range(args.repeats)])
    workspace = project / "workspace"
    report["pages"] = pages(workspace)
    display = display_case(manifest, workspace)
    report["browser"] = (browser_samples(workspace, args.repeats, display) if args.browser == "chromium"
                         else {"status": "not_run", "reason": "--browser none"})
    shown = report["browser"].get("display_pages")
    display["browser"] = ("not_run" if shown is None else
                          bool(shown) and all(p["text_shown"] and not p["elements_made"] for p in shown)
                          and not report["browser"]["dialogs"])
    report["display_case"] = display
    report["journal"] = {"before": before, "after": digest(journal)}
    report["journal"]["unchanged"] = before == report["journal"]["after"]
    ok = (all(report["unchanged"]["skipped"]) and display["static"] and display["browser"] is not False
          and report["journal"]["unchanged"] and not report["browser"].get("page_errors"))
    report["ok"] = ok
    (out / "workspace-scale-evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                                         encoding="utf-8", newline="\n")
    line = {"ok": ok, "scale": manifest["scale"], "cold_build_p50_ms": report["cold"]["build_ms"]["p50"],
            "warm_build_p95_ms": report["warm"]["build_ms"]["p95"],
            "unchanged_p95_ms": report["unchanged"]["build_ms"]["p95"],
            "report": str(out / "workspace-scale-evaluation.json")}
    if "load_ms" in report["browser"]:
        line["browser_load_p95_ms"] = report["browser"]["load_ms"]["p95"]
    print(json.dumps(line))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
