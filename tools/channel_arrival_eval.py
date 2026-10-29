"""Measure the channel page's arrival check in real browsers (#21 PR-3, PUBLISH-FOLDER 7).

    uv run --locked --group browser python tools/channel_arrival_eval.py --out FILE [--engines chromium,firefox,webkit]

Two parts, each over file:// and an HTTP subpath (/a/b/), in each engine named:
- the events: what a page under projects/<channel>/ sees for a version's stylesheet loaded with
  media "not all", the same with media "print", and an image, each present and missing — and
  whether the stylesheet applied to the page and the CSP's 'self' admitted the files;
- the kit's page: a target with two published builds, and a copy whose newest version lacks its
  stylesheet; whether the page moved to the newest version and how long it took, or stayed and showed
  its notice; and without scripts, whether its refresh moved.
It is a record of what was measured, not a gate; tests/test_publish_browser.py is the gate.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK / "src"))
from aelix_analytics import core  # noqa: E402
from aelix_analytics.publishing import build, publish  # noqa: E402

ORIGIN = core.Origin("human", "cli")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
CASES = [("stylesheet_not_all", "assets/here.css"), ("stylesheet_not_all_missing", "assets/gone.css"),
         ("stylesheet_print", "assets/here.css"), ("image", "here.png"), ("image_missing", "gone.png")]
PROBE = """(function(){var out={},list=%s,left=list.length;
function done(k,v){out[k]=v;if(!--left){document.title=JSON.stringify(out);}}
list.forEach(function(x){var el=x[0].indexOf("image")===0?new Image():document.createElement("link");
el.onload=function(){done(x[0],"load")};el.onerror=function(){done(x[0],"error")};
if(x[0].indexOf("image")===0){el.src=x[1];}else{el.rel="stylesheet";el.media=x[0].indexOf("print")>0?"print":"not all";
el.href=x[1];document.head.appendChild(el);}});
setTimeout(function(){if(left){out.unanswered=left;document.title=JSON.stringify(out);}},10000);})();"""


def serve(root: Path):
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def events_fixture(root: Path) -> Path:
    share = root / "a" / "b" / "events"
    (share / "publications/v/assets").mkdir(parents=True)
    (share / "projects/ch").mkdir(parents=True)
    (share / "publications/v/assets/here.css").write_text("p{color:rgb(255,0,0)}", encoding="utf-8")
    (share / "publications/v/here.png").write_bytes(PNG)
    script = PROBE % json.dumps([[name, "../../publications/v/" + path] for name, path in CASES])
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    policy = (f"default-src 'none'; script-src 'sha256-{digest}'; style-src 'self'; img-src 'self' data:; "
              "object-src 'none'; base-uri 'none'; form-action 'none'")  # the channel page's, with this script
    (share / "projects/ch/index.html").write_text(
        f'<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{policy}">'
        f'<title>probe</title><script>{script}</script></head><body><p id="p">text</p></body></html>', encoding="utf-8")
    return share


def page_fixture(root: Path):
    project = core.init_project(root / "project", title="Weekly sales", origin=ORIGIN)
    ids = [build.build_publication(project, origin=ORIGIN)["publication_id"] for _ in range(2)]
    share = root / "served" / "a" / "b" / "share"
    for pid in ids:
        publish.publish(project, pid, share, origin=ORIGIN)
    partial_share = share.with_name("partial")
    shutil.copytree(share, partial_share)
    (partial_share / "publications" / ids[-1] / "assets/publication.css").unlink()
    return share, partial_share, ids


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--engines", default="chromium,firefox,webkit")
    args = parser.parse_args()
    from playwright import sync_api

    work = Path(tempfile.mkdtemp(prefix="channel-arrival-"))
    os.environ["AELIX_ANALYTICS_TRUST_FILE"] = str(work / "trust" / "lab-projects.json")
    for name in ("AELIX_LAB_HOME", "AELIX_LAB_TRUST_FILE"):
        os.environ.pop(name, None)
    out = {"kit_version": core.KIT_VERSION, "platform": sys.platform, "arrival_seconds": publish.ARRIVAL_SECONDS,
           "events": {}, "page": {}}
    try:
        served = work / "served"
        events_share = events_fixture(served)
        share, partial_share, ids = page_fixture(work)
        server = serve(served)
        port = server.server_port

        def bases(folder):
            return {"file": folder.as_uri() + "/", "http": f"http://127.0.0.1:{port}/a/b/{quote(folder.name)}/"}

        with sync_api.sync_playwright() as playwright:
            for engine in args.engines.split(","):
                browser = getattr(playwright, engine).launch()
                out["browser_versions"] = {**out.get("browser_versions", {}), engine: browser.version}
                for scheme, base in bases(events_share).items():
                    page = browser.new_page()
                    page.goto(base + "projects/ch/index.html")
                    page.wait_for_function("document.title.startsWith('{')", timeout=20000)
                    out["events"][f"{engine}/{scheme}"] = {
                        **json.loads(page.title()),
                        "stylesheet_applied": page.evaluate("getComputedStyle(document.getElementById('p')).color")
                        == "rgb(255, 0, 0)"}
                    page.close()
                for scheme in ("file", "http"):
                    row = out["page"][f"{engine}/{scheme}"] = {}
                    for case, folder in (("complete", share), ("missing_stylesheet", partial_share)):
                        base = bases(folder)[scheme]
                        page = browser.new_page()
                        started = time.perf_counter()
                        page.goto(base + "projects/weekly-sales/index.html")
                        try:
                            page.wait_for_url(base + f"publications/{ids[-1]}/index.html", timeout=20000)
                            row[case] = {"moved": True, "seconds": round(time.perf_counter() - started, 3)}
                        except sync_api.TimeoutError:
                            row[case] = {"moved": False, "notice": page.is_visible("#arrival-failed")}
                        page.close()
                    context = browser.new_context(java_script_enabled=False)
                    page = context.new_page()
                    base = bases(share)[scheme]
                    page.goto(base + "projects/weekly-sales/index.html")
                    try:
                        page.wait_for_url(base + f"publications/{ids[-1]}/index.html", timeout=20000)
                        row["no_script"] = {"moved": True}
                    except sync_api.TimeoutError:
                        row["no_script"] = {"moved": False}
                    context.close()
                browser.close()
        server.shutdown()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(args.out)}))


if __name__ == "__main__":
    main()
