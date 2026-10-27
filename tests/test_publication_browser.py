"""#20 PR5: a saved copy in a real browser (PUBLICATION-BUILD 12.6, design OQ22).

Runs only when AELIX_ANALYTICS_TEST_BROWSER names engines (e.g. "chromium" or "chromium,firefox,webkit") and
the `browser` dependency group and those Playwright browsers are installed; CI runs Chromium in the Linux
platform leg. Every request the pages make is checked: only this copy's files, over file:// and an HTTP subpath.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest
from publication_reader_fixture import FINDING, detached, project, saved

ENGINES = [name for name in os.environ.get("AELIX_ANALYTICS_TEST_BROWSER", "").split(",") if name]
if not ENGINES:
    pytest.skip("AELIX_ANALYTICS_TEST_BROWSER names no browser engine", allow_module_level=True)
try:
    from playwright import sync_api
except ImportError as exc:  # engines named but no Playwright: a failure, never a skip that passes CI
    raise RuntimeError("AELIX_ANALYTICS_TEST_BROWSER names engines; install the `browser` dependency group") from exc


@pytest.fixture(scope="module")
def copy(tmp_path_factory):
    """An evidence copy with both Reports, its project gone, at file:// and at /a/b/<id>/ over HTTP."""
    tmp = tmp_path_factory.mktemp("browser")
    p, ids = project(tmp / "project")
    result = saved(p, reports=[("weekly", ""), ("page", "")])
    folder = detached(p, result, tmp / "받은 사본 a b")
    served = tmp / "served"
    (served / "a" / "b").mkdir(parents=True)
    shutil.copytree(folder, served / "a" / "b" / folder.name)

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(served)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bases = {"file": folder.as_uri() + "/",
             "http": f"http://127.0.0.1:{server.server_port}/a/b/{quote(folder.name)}/"}
    yield folder, bases, ids, tmp
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture(scope="module", params=ENGINES)
def browser(request):
    with sync_api.sync_playwright() as playwright:
        engine = getattr(playwright, request.param)
        launched = engine.launch()
        yield launched
        launched.close()


class Guard:
    """Allow only requests inside `base`; every other request is blocked and kept (12.6: zero outside)."""

    def __init__(self, base):
        self.base, self.blocked = base, []

    def __call__(self, route):
        url = route.request.url
        if url.startswith(self.base) or url.startswith(("data:", "about:", "blob:")):
            route.continue_()
        else:
            self.blocked.append(url)
            route.abort()


def opened(browser, base, *, javascript=True):
    context = browser.new_context(java_script_enabled=javascript)
    guard = Guard(base)
    context.route("**/*", guard)
    errors = []
    page = context.new_page()
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    return context, page, guard, errors


def kit_pages(folder):
    """The kit's pages: a Report's own documents and its renderer's view are authored content."""
    return sorted(name for name in (f.relative_to(folder).as_posix() for f in folder.rglob("*.html"))
                  if "/content/" not in name and "/preview/" not in name)


@pytest.mark.parametrize("scheme", ["file", "http"])
def test_every_page_opens_without_errors_or_requests_outside_the_copy(copy, browser, scheme):
    folder, bases, _ids, _tmp = copy
    context, page, guard, errors = opened(browser, bases[scheme])
    try:
        for name in kit_pages(folder):
            page.goto(bases[scheme] + quote(name))
            page.wait_for_load_state("load")
            for figure in page.query_selector_all("[data-payload]"):
                figure.wait_for_selector(".main-svg", timeout=15000)  # the plotly figure was drawn
            broken = page.evaluate("[...document.images].filter(i => !i.complete || !i.naturalWidth).length")
            assert broken == 0, name
            assert "Missing from this copy" not in page.inner_text("body"), name
        assert guard.blocked == [] and errors == [], (guard.blocked[:5], errors[:5])
    finally:
        context.close()


def test_a_finding_leads_to_its_figure_run_and_code_and_back(copy, browser):
    folder, bases, _ids, _tmp = copy
    context, page, guard, errors = opened(browser, bases["file"])
    try:
        page.goto(bases["file"] + "index.html")
        page.get_by_role("link", name=FINDING).first.click()
        anchor = page.url
        assert "#i" in anchor
        card = page.locator("#" + page.url.rsplit("#", 1)[1])
        card.get_by_role("link", name="Sales trend").click()
        assert "/pages/figures/" in page.url
        page.get_by_role("link", name="Charts").first.click()
        assert "/pages/runs/" in page.url
        page.locator("p", has_text="Code snapshot:").get_by_role("link").first.click()
        assert "/pages/code/" in page.url
        for _ in range(3):
            page.go_back()
        assert page.url == anchor
        assert page.evaluate("!!document.querySelector(location.hash)")
        assert guard.blocked == [] and errors == []
    finally:
        context.close()


def test_frames_are_sandboxed_from_the_page(copy, browser):
    folder, bases, ids, _tmp = copy
    context, page, _guard, _errors = opened(browser, bases["file"])
    try:
        for name in kit_pages(folder):
            page.goto(bases["file"] + quote(name))
            for frame in page.query_selector_all("iframe"):
                # May load its own files (owner 2026-10-27), never run scripts.
                assert frame.get_attribute("sandbox") == "allow-same-origin", name
                assert page.evaluate("f => f.sandbox.contains('allow-scripts')", frame) is False, name
    finally:
        context.close()


def test_without_javascript_the_band_lists_and_figure_text_remain(copy, browser):
    folder, bases, _ids, _tmp = copy
    context, page, guard, _errors = opened(browser, bases["file"], javascript=False)
    try:
        for name in kit_pages(folder):
            page.goto(bases["file"] + quote(name))
            text = page.inner_text("body")
            assert "Shared snapshot — a fixed copy that does not update" in text, name
            if page.query_selector("[data-payload]"):
                assert "Interactive figure — needs JavaScript" in text, name
        assert guard.blocked == []
    finally:
        context.close()


def test_missing_files_are_named_in_the_page(copy, browser):
    folder, _bases, _ids, tmp = copy
    damaged = Path(tempfile.mkdtemp(dir=tmp)) / "손상 사본"
    shutil.copytree(folder, damaged)
    image = next(damaged.glob("files/*.png"))
    payload = next(damaged.glob("assets/figures/*.js"))
    image.unlink()
    payload.unlink()
    (damaged / "assets/publication.css").unlink()
    base = damaged.as_uri() + "/"
    context, page, _guard, _errors = opened(browser, base)
    try:
        seen = set()
        for name in kit_pages(damaged):
            page.goto(base + quote(name))
            page.wait_for_load_state("load")
            page.wait_for_timeout(200)
            text = page.inner_text("body")
            seen |= {path for path in (f"files/{image.name}", f"assets/figures/{payload.name}")
                     if f"Missing from this copy: {path}" in text}
            assert page.locator(".load-failure").is_visible(), name  # no stylesheet
        assert seen == {f"files/{image.name}", f"assets/figures/{payload.name}"}
    finally:
        context.close()
    runtime = Path(tempfile.mkdtemp(dir=tmp)) / "실행 파일 없는 사본"
    shutil.copytree(folder, runtime)
    (runtime / "assets/publication.js").unlink()
    base = runtime.as_uri() + "/"
    context, page, _guard, _errors = opened(browser, base)
    try:
        page.goto(base + "index.html")
        page.wait_for_load_state("load")
        assert page.locator(".load-failure").is_visible()  # the inline check, the stylesheet still there
    finally:
        context.close()
    library = Path(tempfile.mkdtemp(dir=tmp)) / "plotly 없는 사본"
    shutil.copytree(folder, library)
    (library / "assets/plotly.min.js").unlink()
    base = library.as_uri() + "/"
    context, page, _guard, _errors = opened(browser, base)
    try:
        figure = next(name for name in kit_pages(library) if "data-payload" in (library / name).read_text(encoding="utf-8"))
        page.goto(base + quote(figure))
        page.wait_for_load_state("load")
        page.wait_for_timeout(200)
        assert "Missing from this copy: assets/plotly.min.js" in page.inner_text("body"), figure
    finally:
        context.close()


def test_report_bodies_open_in_their_frames(copy, browser):
    folder, bases, _ids, _tmp = copy
    context, page, guard, errors = opened(browser, bases["http"])
    try:
        bodies = []
        for name in kit_pages(folder):
            if not name.startswith("reports/") or not name.endswith("/index.html"):
                continue
            page.goto(bases["http"] + quote(name))
            page.wait_for_load_state("load")
            frame = page.frame_locator("iframe[title='Report body']")
            bodies.append(frame.locator("body").inner_text(timeout=10000))
        assert any("주간" in body for body in bodies) and any("HTML body" in body for body in bodies)
        assert guard.blocked == [] and errors == []
    finally:
        context.close()
