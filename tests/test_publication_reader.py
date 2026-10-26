"""#20 PR5: a saved copy read where its project is not (PUBLICATION-BUILD 12.6), on every CI leg."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
import zipfile
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urldefrag, urljoin, urlsplit
from urllib.request import urlopen

import pytest
from publication_reader_fixture import detached, project, saved

from aelix_analytics.publishing import checks
from aelix_analytics.publishing.render import RUNTIME_CHECK

HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")


class References(HTMLParser):
    """Every reference a page makes: navigation (`a`/`area` href) and resources (the rest)."""

    def __init__(self, data):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.feed(data)

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and key in ("href", "src", "data-payload"):
                self.links.append((value, key == "href" and tag in ("a", "area")))


def crawl(base, files):
    """Open every package file at `base` and follow every reference of every page: each resolves to a
    file of this copy under `base` (never a directory, never outside); remote navigation is allowed."""
    for name, data in files.items():
        address = urljoin(base, quote(name))
        with urlopen(address) as response:
            assert response.read() == data, name
        if not name.endswith(".html"):
            continue
        for target, navigation in References(data.decode("utf-8")).links:
            # A browser percent-encodes what an authored document leaves raw (a Report's own HTML).
            absolute = urljoin(address, quote(target, safe="/:#?&=%@+~,;!$'()*[]"))
            parsed = urlsplit(absolute)
            if parsed.scheme == "data" or navigation and parsed.scheme in ("http", "https", "mailto") \
                    and parsed.netloc != urlsplit(base).netloc:
                continue
            assert not parsed.path.endswith("/"), (name, target)
            assert absolute.startswith(base), (name, target)
            with urlopen(urldefrag(absolute)[0]) as response:
                assert response.read() is not None


def package_files(folder):
    return {f.relative_to(folder).as_posix(): f.read_bytes() for f in folder.rglob("*") if f.is_file()}


@pytest.fixture
def received(tmp_path):
    """An evidence copy with both Reports, moved under a Korean path with spaces, its project gone."""
    p, ids = project(tmp_path / "project")
    result = saved(p, reports=[("weekly", ""), ("page", "")])
    folder = detached(p, result, tmp_path / "받은 사본 a b")
    assert not (tmp_path / "project").exists()
    return folder, result, ids


def test_a_copy_reads_without_its_project_over_file_and_an_http_subpath(received, tmp_path):
    folder, result, _ids = received
    files = package_files(folder)
    assert checks.verify_package(folder)["package_sha256"] == result["package_sha256"]
    crawl(folder.as_uri() + "/", files)

    served = tmp_path / "served"
    subpath = served / "a" / "b"
    subpath.mkdir(parents=True)
    shutil.copytree(folder, subpath / folder.name)
    requests = []

    class Recorded(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def log_request(self, code="-", size="-"):
            requests.append((unquote(self.path), int(code) if str(code).isdigit() else code))

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Recorded, directory=str(served)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        crawl(f"http://127.0.0.1:{server.server_port}/a/b/{quote(folder.name)}/", files)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    prefix = f"/a/b/{folder.name}/"
    assert requests and all(path.startswith(prefix) and code == 200 for path, code in requests), [
        r for r in requests if not r[0].startswith(prefix) or r[1] != 200][:5]


def test_the_zip_extracts_with_the_platform_tools_and_verifies(tmp_path):
    p, _ids = project(tmp_path / "project")
    zip_path = tmp_path / "사본.zip"
    result = saved(p, reports=[("weekly", "")], zip_path=str(zip_path))
    assert result["ok"] and zip_path.is_file()
    expected = package_files(p.root / result["path"])
    tools = {"zipfile": None}
    if sys.platform != "win32" and shutil.which("unzip"):
        tools["unzip"] = lambda zip_file, out: ["unzip", "-q", str(zip_file), "-d", str(out)]
    if sys.platform == "darwin" and shutil.which("ditto"):
        tools["ditto"] = lambda zip_file, out: ["ditto", "-x", "-k", str(zip_file), str(out)]
    if sys.platform == "win32":
        tools["Expand-Archive"] = lambda zip_file, out: [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"Expand-Archive -LiteralPath '{zip_file}' -DestinationPath '{out}'"]
    for name, command in tools.items():
        out = tmp_path / f"풀기 {name}"
        if command is None:
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(out)
        else:
            subprocess.run(command(zip_path, out), check=True, capture_output=True)
        root = out / result["publication_id"]
        assert package_files(root) == expected, name
        assert checks.verify_package(root)["package_sha256"] == result["package_sha256"], name
    assert any("주간 보고.md" in path for path in expected)


def test_every_page_says_when_its_files_could_not_load(received):
    folder, _result, _ids = received
    # The kit's pages; a Report's own documents and its renderer's view are authored content.
    pages = {k: v.decode("utf-8") for k, v in package_files(folder).items() if k.endswith(".html")
             and "/content/" not in k and not k.endswith("/preview/report.html")}
    for name, page in pages.items():
        assert 'class="load-failure"' in page and f"<script>{RUNTIME_CHECK}</script>" in page, name
        depth = name.count("/")
        assert f'data-root="{"/".join([".."] * depth) or "."}/"' in page, name
        for _frame in re.findall(r"<iframe ", page):
            assert "If this frame is empty, its file is missing from this copy" in page, name
    readme = (folder / "README.txt").read_text(encoding="utf-8")
    assert "Extract the whole ZIP first" in readme


def test_shipped_javascript_has_one_script_maker_and_no_network_api(received):
    folder, _result, _ids = received
    runtime = (folder / "assets/publication.js").read_text(encoding="utf-8")
    for code in (runtime, RUNTIME_CHECK):
        assert not any(name in code for name in checks.NETWORK_APIS)
        assert "localStorage" not in code and "sessionStorage" not in code
    assert runtime.count('createElement("script")') == 1


class Language(HTMLParser):
    """Hangul outside an element with lang="" is UI text in a language other than the page's."""

    def __init__(self, data):
        super().__init__(convert_charrefs=True)
        self.stack, self.found = [], []
        self.feed(data)

    VOID = {"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "col", "embed", "wbr"}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        user = attrs.get("lang") == "" or bool(self.stack and self.stack[-1][1])
        for key, value in attrs.items():
            if value and HANGUL.search(value) and not user and key in ("alt", "title", "aria-label"):
                self.found.append((tag, key, value))
        if tag not in self.VOID:
            self.stack.append((tag, user))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        if HANGUL.search(data) and not (self.stack and self.stack[-1][1]):
            self.found.append((self.stack[-1][0] if self.stack else "", "text", data.strip()[:40]))


def test_package_pages_hold_korean_only_in_user_text(received):
    folder, _result, _ids = received
    for name, data in package_files(folder).items():
        if name.endswith(".html") and "/content/" not in name and not name.endswith("/preview/report.html"):
            assert Language(data.decode("utf-8")).found == [], name
