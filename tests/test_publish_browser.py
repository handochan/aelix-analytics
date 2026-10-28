"""#21 PR-3: a target's channel page in a real browser (PUBLISH-FOLDER 7; ADR-0009 check 11).

Runs only when AELIX_ANALYTICS_TEST_BROWSER names engines, as tests/test_publication_browser.py does; CI
runs Chromium in the Linux platform leg, and Firefox and WebKit are run by hand. The page moves to the
newest version only once that version's own stylesheets and images have loaded; with one missing it
stays and says so; without scripts its meta refresh moves. Over file:// and an HTTP subpath, every
request stays inside the target.
"""

from __future__ import annotations

import os
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import pytest

from aelix_analytics import core
from aelix_analytics.publishing import build, publish

ENGINES = [name for name in os.environ.get("AELIX_ANALYTICS_TEST_BROWSER", "").split(",") if name]
if not ENGINES:
    pytest.skip("AELIX_ANALYTICS_TEST_BROWSER names no browser engine", allow_module_level=True)
try:
    from playwright import sync_api
except ImportError as exc:  # engines named but no Playwright: a failure, never a skip that passes CI
    raise RuntimeError("AELIX_ANALYTICS_TEST_BROWSER names engines; install the `browser` dependency group") from exc

ORIGIN = core.Origin("human", "cli")
CHANNEL = "projects/weekly-sales/index.html"


@pytest.fixture(scope="module")
def targets(tmp_path_factory):
    """Two builds published to one target; a copy of it whose newest version lacks its stylesheet
    (a sync folder still bringing it); both at file:// and under /a/b/ over HTTP."""
    tmp = tmp_path_factory.mktemp("channel")
    project = core.init_project(tmp / "project", title="Weekly sales", origin=ORIGIN)
    older = build.build_publication(project, origin=ORIGIN)["publication_id"]
    newer = build.build_publication(project, origin=ORIGIN)["publication_id"]
    served = tmp / "served" / "a" / "b"
    served.mkdir(parents=True)
    share = served / "공유 share"
    for pid in (older, newer):
        assert publish.publish(project, pid, share, origin=ORIGIN)["outcome"] == "published"
    partial_share = served / "partial"
    shutil.copytree(share, partial_share)
    (partial_share / "publications" / newer / "assets/publication.css").unlink()

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(tmp / "served")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bases = {}
    for name, folder in (("share", share), ("partial", partial_share)):
        bases[name] = {"file": folder.as_uri() + "/",
                       "http": f"http://127.0.0.1:{server.server_port}/a/b/{quote(folder.name)}/"}
    yield bases, older, newer
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture(scope="module", params=ENGINES)
def browser(request):
    with sync_api.sync_playwright() as playwright:
        launched = getattr(playwright, request.param).launch()
        yield launched
        launched.close()


def opened(browser, base, *, javascript=True):
    context = browser.new_context(java_script_enabled=javascript)
    blocked = []

    def guard(route):
        url = route.request.url
        if url.startswith(base) or url.startswith(("data:", "about:", "blob:")):
            route.continue_()
        else:
            blocked.append(url)
            route.abort()

    context.route("**/*", guard)
    errors = []
    page = context.new_page()
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    return context, page, blocked, errors


@pytest.mark.parametrize("scheme", ["file", "http"])
def test_the_channel_page_moves_to_the_newest_version_once_its_files_load(targets, browser, scheme):
    bases, _older, newer = targets
    base = bases["share"][scheme]
    context, page, blocked, errors = opened(browser, base)
    try:
        page.goto(base + quote(CHANNEL))
        page.wait_for_url(base + f"publications/{newer}/index.html", timeout=15000)
        page.wait_for_load_state("load")
        assert page.evaluate("getComputedStyle(document.querySelector('.load-failure')).display") == "none"
        assert blocked == [] and errors == [], (blocked[:5], errors[:5])
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["file", "http"])
def test_a_newest_version_not_yet_arrived_is_not_opened(targets, browser, scheme):
    bases, older, newer = targets
    base = bases["partial"][scheme]
    context, page, blocked, _errors = opened(browser, base)
    try:
        page.goto(base + quote(CHANNEL))
        page.wait_for_selector("#arrival-failed:not([hidden])", timeout=15000)
        assert page.url == base + quote(CHANNEL)
        assert page.is_hidden("#arrival-checking")
        assert page.get_attribute("text=Previous version >> a", "href") == f"../../publications/{older}/index.html"
        assert blocked == []
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["file", "http"])
def test_without_scripts_the_page_moves_by_its_refresh(targets, browser, scheme):
    bases, _older, newer = targets
    base = bases["share"][scheme]
    context, page, blocked, _errors = opened(browser, base, javascript=False)
    try:
        page.goto(base + quote(CHANNEL))
        page.wait_for_url(base + f"publications/{newer}/index.html", timeout=15000)
        assert blocked == []
    finally:
        context.close()
