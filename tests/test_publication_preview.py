"""PR2 byte/channel acceptance; preview is not a saved/bound publication or browser acceptance."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import threading
import zlib
from dataclasses import replace
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urldefrag, urljoin, urlsplit
from urllib.request import urlopen

import pytest
from conftest import kit_env
from publication_doubles import RecordingProjection

from aelix_analytics import core, runner
from aelix_analytics.publishing import build, checks, package
from aelix_analytics.publishing.extract import parquet_strings
from aelix_analytics.publishing.inspections import inspect_bytes
from aelix_analytics.publishing.model import (
    BanToken,
    ItemKey,
    LeakPolicy,
    PublicationError,
    ResourceLink,
    ScanText,
)
from aelix_analytics.publishing.projection import PROJECTIONS, ReadProjection, ShareRequest
from aelix_analytics.publishing.scope import PROFILES, leak_policy
from aelix_analytics.publishing.snapshot import capture_snapshot

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = core.Origin("human", "sdk")
CANARY = "EXCLUDED_CONTENT_CANARY_604d09"
POLICY = LeakPolicy((BanToken(CANARY, "strong"),))


def png(*extra):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + b"".join(chunk(k, v) for k, v in extra)
        + chunk(b"IDAT", zlib.compress(b"\x00\x20\x40\x60"))
        + chunk(b"IEND", b"")
    )


def image(format_, canary=False):
    from PIL import Image

    out = io.BytesIO()
    options = {}
    if canary and format_ == "GIF":
        options["comment"] = CANARY.encode()
    if canary and format_ == "WEBP":
        exif = Image.Exif()
        exif[0x013B] = CANARY
        options["exif"] = exif
    Image.new("RGB", (1, 1), (20, 40, 60)).save(out, format=format_, **options)
    data = out.getvalue()
    if canary and format_ == "JPEG":
        body = CANARY.encode()
        data = data[:2] + b"\xff\xfe" + struct.pack(">H", len(body) + 2) + body + data[2:]
    return data


@pytest.mark.parametrize(
    "data",
    [
        png((b"tEXt", b"Author\0" + CANARY.encode())),
        png((b"zTXt", b"Author\0\0" + zlib.compress(CANARY.encode()))),
        png((b"iTXt", b"Author\0\x01\x00\0\0" + zlib.compress(CANARY.encode()))),
        b'<svg xmlns="http://www.w3.org/2000/svg"><!--'
        + CANARY.encode()
        + b'--><path d="M0 0"/></svg>',
        b'<html><p title="' + CANARY.encode() + b'">Allowed</p></html>',
        b'{"data":[{"type":"scatter","y":[1],"meta":"' + CANARY.encode() + b'"}],"layout":{}}',
        b'<!--include--><img src="data:image/png;base64,'
        + base64.b64encode(png((b"tEXt", b"Author\0" + CANARY.encode())))
        + b'">',
    ],
)
def test_canary_extraction_from_encoded_attributes_payload_images_svg_and_data_uri(data):
    result = inspect_bytes(data, role="figure")
    assert result is not None
    with pytest.raises(PublicationError, match="excluded content") as caught:
        checks.check_strings(result.strings, POLICY, path="files/copy")
    assert caught.value.code == "leak_found"


@pytest.mark.parametrize("format_", ["JPEG", "GIF", "WEBP"])
def test_compressed_image_metadata_canary_and_positive_control(format_):
    inspected = inspect_bytes(image(format_), role="figure")
    assert inspected is not None
    checks.check_strings(inspected.strings, POLICY, path="clean")
    with pytest.raises(PublicationError, match="excluded content"):
        checks.check_strings(
            inspect_bytes(image(format_, True), role="figure").strings, POLICY, path="metadata"
        )


def test_true_positive_text_is_preserved_and_type_lies_are_not_figures():
    for raw in (
        png(),
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>Included \xeb\x82\xb4\xec\x9a\xa9</text></svg>',
        b"<p>Included content</p>",
        b'{"data":[{"type":"scatter","y":[1,2]}],"layout":{}}',
    ):
        assert inspect_bytes(raw, role="figure") is not None
    for raw in (
        b"x,y\n1,2\n",
        b"%PDF-1.7\n",
        b"PK\x03\x04office",
        b"\x1f\x8bcompressed",
        b"\xfftext",
    ):
        assert inspect_bytes(raw, role="figure", path="files/fake.png") is None
    with pytest.raises(PublicationError) as caught:
        inspect_bytes(png()[:-3], role="figure")
    assert caught.value.code == "inspection_failed"
    with pytest.raises(PublicationError):
        inspect_bytes(png((b"zTXt", b"k\0\0not-zlib")), role="figure")


@pytest.mark.parametrize(
    "raw",
    [
        b'<p onclick="alert(1)">No</p>',
        b"<script>alert(1)</script>",
        b'<iframe src="a.html"></iframe>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
        b'{"data":[{"type":"scattergeo","lat":[1]}],"layout":{}}',
        b'{"data":[],"layout":{"map":{}}}',
    ],
)
def test_user_executable_or_remote_plotly_content_is_not_shareable(raw):
    with pytest.raises(PublicationError) as caught:
        inspect_bytes(raw, role="figure")
    assert caught.value.code == "figure_not_shareable"


def test_nested_parquet_values_statistics_and_arrow_schema_are_inspected():
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            pa.field(
                "values",
                pa.list_(
                    pa.struct(
                        [pa.field("message", pa.string(), metadata={b"author": CANARY.encode()})]
                    )
                ),
            )
        ],
        metadata={b"json": json.dumps({"nested": CANARY}).encode()},
    )
    table = pa.Table.from_pylist([{"values": [{"message": CANARY}]}], schema=schema)
    out = io.BytesIO()
    pq.write_table(table, out)
    result = inspect_bytes(out.getvalue(), role="attached_data", parquet_extractor=parquet_strings)
    assert any(s.value == CANARY for s in result.strings)
    assert any("ARROW:schema" in s.location and s.value == CANARY for s in result.strings)
    with pytest.raises(PublicationError):
        checks.check_strings(result.strings, POLICY, path="files/table.parquet")
    with pytest.raises(PublicationError, match="extractor unavailable"):
        inspect_bytes(out.getvalue(), role="attached_data")
    assert inspect_bytes(out.getvalue(), role="figure") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://remote.test/x",
        "//remote.test/x",
        "/root",
        "../escape",
        "file:///tmp/x",
        "C:/x",
        "javascript:alert(1)",
        "folder/",
    ],
)
def test_resource_and_relative_link_policy_refuses_external_or_missing_files(url):
    with pytest.raises(PublicationError) as caught:
        checks.resolve_link("index.html", ResourceLink(url, "src"), {"index.html": b""})
    assert caught.value.code == "package_check_failed"


def test_navigation_resources_escaped_css_and_fragments_use_the_same_rules():
    assert (
        checks.resolve_link(
            "index.html",
            ResourceLink("https://remote.test/page", "href", False),
            {"index.html": b""},
        )
        == "https://remote.test/page"
    )
    files = {"index.html": b"", "assets/x.css": b""}
    checks.resolve_link("index.html", ResourceLink("assets/x.css", "href"), files)
    checks.resolve_link(
        "index.html", ResourceLink("#scope", "href", False), files, {"index.html": {"scope"}}
    )
    with pytest.raises(PublicationError):
        checks.resolve_link(
            "index.html", ResourceLink("#absent", "href", False), files, {"index.html": {"scope"}}
        )
    raw = b'<style>@import "https://remote.test/s.css";p{background:u\\72l(https://remote.test/p.png)}</style>'
    inspected = inspect_bytes(raw, role="figure")
    assert {link.value for link in inspected.links} == {
        "https://remote.test/s.css",
        "https://remote.test/p.png",
    }
    with pytest.raises(PublicationError):
        package._user_links(inspected)


@pytest.mark.parametrize(
    "name",
    [
        "../x",
        "/x",
        "C:/x",
        "a\\b",
        "con.html",
        "assets/NUL.txt",
        "trail.",
        "trail ",
        "n\u0303.html",
        "a//b",
        "a/" + ("x" * 230),
    ],
)
def test_name_policy_is_portable_and_does_not_repair_names(name):
    with pytest.raises(PublicationError):
        checks.check_names([name])


def test_casefold_collisions_and_unpinned_or_network_runtime_fail():
    with pytest.raises(PublicationError):
        checks.check_names(["A.html", "a.html"])
    with pytest.raises(PublicationError, match="pinned"):
        checks.check_package({"assets/lib.js": b"function x(){}"}, {"assets/lib.js": "library"})
    js = b'fetch("https://remote.test")'
    with pytest.raises(PublicationError, match="network"):
        checks.check_package(
            {"assets/publication.js": js},
            {"assets/publication.js": "library"},
            pinned={"assets/publication.js": js},
        )


def tree_bytes(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".venv" not in p.parts
    }


def test_preview_pending_context_is_memory_only_and_uses_injected_strategy(tmp_path, monkeypatch):
    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    core.record(p, "finding", CANARY, origin=ORIGIN)
    p.context.write_text("New pending context\n", encoding="utf-8")
    before = tree_bytes(p.root)
    chosen = RecordingProjection()

    def forbidden(*args, **kwargs):
        pytest.fail("preview used a default strategy, lock, context sync or writer")

    monkeypatch.setitem(PROJECTIONS, "read", forbidden)
    monkeypatch.setattr(core, "sync_context", forbidden)
    monkeypatch.setattr(package, "write_package", forbidden)
    summary, files = build.preview_package(p, projection=chosen)
    assert chosen.requests == [ShareRequest()]
    assert (
        summary["profile"]["id"] == "recording" and summary["basis"]["context_status"] == "pending"
    )
    assert b"pending preview" in files["index.html"]
    assert CANARY.encode() not in b"".join(files.values())
    assert tree_bytes(p.root) == before
    assert not (p.root / ".analytics" / "publications").exists()


def test_model_refusal_names_source_and_adversarial_scope_never_reaches_the_loader(
    tmp_path, monkeypatch
):
    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    (p.scripts / "p.py").write_text("print(1)\n", encoding="utf-8")
    run = runner.execute(p, script="scripts/p.py", title="Private run", origin=ORIGIN)["run"]
    core.record(p, "finding", "This refers to " + run["id"], origin=ORIGIN)
    request = ShareRequest(exclude=frozenset({ItemKey("run", run["id"])}))
    with pytest.raises(PublicationError) as caught:
        build.preview_package(p, request)
    assert caught.value.code == "leak_found" and caught.value.details["source"]["field"] == "text"
    snapshot = capture_snapshot(p)
    base = ReadProjection().project(snapshot, request)

    class Adversarial:
        id, rules_version = "adversarial", 1

        def project(self, snapshot, request):
            return replace(
                base,
                ledger=replace(
                    base.ledger, included=(*base.ledger.included, ItemKey("run", run["id"]))
                ),
            )

    monkeypatch.setattr(build, "prepare_files", lambda *a: pytest.fail("bad scope reached loader"))
    with pytest.raises(PublicationError, match="projection_out_of_scope"):
        build.preview_package(p, request, projection=Adversarial())
    policy = leak_policy(
        snapshot,
        request,
        PROFILES["read"],
        replace(
            base,
            ledger=replace(
                base.ledger, included=(*base.ledger.included, ItemKey("run", run["id"]))
            ),
        ),
    )
    with pytest.raises(PublicationError):
        checks.check_strings((ScanText(run["id"], "raw"),), policy, path="adversarial.txt")


def test_public_preview_cli_real_project_has_no_side_writes_and_no_implicit_build(tmp_path):
    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    before = tree_bytes(p.root)
    done = subprocess.run(
        [sys.executable, "-m", "aelix_analytics", "--project", str(p.root), "build", "--preview"],
        env=kit_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    value = json.loads(done.stdout)
    assert value["preview"] and value["checks"]["result"] == "pass"
    assert "publication_id" not in value and tree_bytes(p.root) == before
    for args, code in [
        (["--profile", "unknown"], "profile_unknown"),
        (["--scope", "reports"], "scope_unsupported"),
        (["--collection", "group@version"], "scope_unsupported"),
    ]:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "aelix_analytics",
                "--project",
                str(p.root),
                "build",
                "--preview",
                *args,
            ],
            env=kit_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert done.returncode == 1 and json.loads(done.stdout)["error"]["code"] == code
    done = subprocess.run(
        [sys.executable, "-m", "aelix_analytics", "--project", str(p.root), "build"],
        env=kit_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 2 and tree_bytes(p.root) == before


def test_same_preview_bytes_and_static_file_and_http_subpath_crawler(tmp_path):
    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    core.record(p, "finding", "Included finding <b>kept as text</b>", origin=ORIGIN)
    one, files = build.preview_package(p)
    two, again = build.preview_package(p)
    assert one == two and files == again
    (tmp_path / "served").mkdir()
    destination = package.write_package(tmp_path / "served" / "copy", files)
    with pytest.raises(PublicationError):
        package.write_package(destination, files)

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Quiet, directory=str(tmp_path / "served"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:

        class References(HTMLParser):
            def __init__(self, data):
                super().__init__(convert_charrefs=True)
                self.links = []
                self.feed(data)

            def handle_starttag(self, tag, attrs):
                for key, value in attrs:
                    if value and key in ("href", "src", "data-payload"):
                        self.links.append((value, key == "href" and tag in ("a", "area")))

        for name, data in files.items():
            for base in (
                destination.as_uri() + "/",
                f"http://127.0.0.1:{server.server_port}/copy/",
            ):
                address = urljoin(base, quote(name))
                with urlopen(address) as response:
                    assert response.read() == data
                if not name.endswith(".html"):
                    continue
                for target, navigation in References(data.decode("utf-8")).links:
                    absolute = urljoin(address, target)
                    parsed = urlsplit(absolute)
                    if (
                        parsed.scheme == "data"
                        or navigation
                        and parsed.scheme in ("http", "https", "mailto")
                        and parsed.netloc != urlsplit(base).netloc
                    ):
                        continue
                    assert not parsed.path.endswith("/"), (
                        "a file reader cannot open a directory link"
                    )
                    assert (
                        parsed.scheme == urlsplit(base).scheme
                        and parsed.netloc == urlsplit(base).netloc
                    )
                    with urlopen(urldefrag(absolute)[0]) as response:
                        assert response.read()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_css_tokenizer_decodes_quoted_escapes_and_does_not_invent_links_in_content_strings():
    raw = b'p{content:"url(https://example.test/is-text)";background:u\\72l("https\\3a //remote.test/a\\"b.png")}'
    inspected = inspect_bytes(raw, role="generated", path="assets/test.css")
    assert [link.value for link in inspected.links] == ['https://remote.test/a"b.png']
    encoded = "".join("\\" + format(ord(char), "x") + " " for char in CANARY)
    css = ('p{content:"' + encoded + '"}').encode()
    with pytest.raises(PublicationError):
        checks.check_strings(
            inspect_bytes(css, role="generated", path="a.css").strings, POLICY, path="a.css"
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'<meta content=">" http-equiv=" refresh " content="0;https://remote.test">',
        b'<div onclick="alert(1)">bad</div>',
        b'<iframe src="index.html"></iframe>',
        b'<iframe srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;" sandbox></iframe>',
        b'<script>fetch("https://remote.test")</script>',
    ],
)
def test_generated_html_cannot_bypass_resource_rules_through_inline_execution(raw):
    with pytest.raises(PublicationError) as caught:
        checks.check_package({"index.html": raw}, {"index.html": "generated"})
    assert caught.value.code == "package_check_failed"


def test_leading_space_data_uri_is_still_inspected_and_nested_depth_is_bounded():
    raw = (
        b'<img src=" \tdata:image/png;base64,'
        + base64.b64encode(png((b"tEXt", b"Author\0" + CANARY.encode())))
        + b'">'
    )
    result = inspect_bytes(raw, role="figure")
    with pytest.raises(PublicationError):
        checks.check_strings(result.strings, POLICY, path="data.html")
    for _ in range(6):
        raw = b'<img src="data:text/html;base64,' + base64.b64encode(raw) + b'">'
    with pytest.raises(PublicationError, match="depth4"):
        inspect_bytes(raw, role="figure")


def test_actual_plotly_payload_is_safe_escaped_deduplicated_and_bundle_is_scanned(
    tmp_path, monkeypatch
):
    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    script = p.scripts / "plot.py"
    script.write_text(
        "import aelix_analytics as a\nimport plotly.graph_objects as go\n"
        'f=go.Figure(go.Scatter(y=[1,2],customdata=["</script>","\\u2028" ]))\n'
        'a.visualize(f,"one",title="Included figure")\na.visualize(f,"two",title="Second copy")\n',
        encoding="utf-8",
    )
    assert (
        runner.execute(p, script="scripts/plot.py", title="Public chart", origin=ORIGIN)["run"][
            "status"
        ]
        == "ok"
    )
    summary, files = build.preview_package(p)
    payloads = [name for name in files if name.startswith("assets/figures/")]
    assert len(payloads) == 1 and summary["limits"]["interactive_figure_points"] == 2
    payload = files[payloads[0]]
    assert (
        b"</script>" not in payload
        and b"\\u003c/script\\u003e" in payload
        and b"\\u2028" in payload
    )
    assert "assets/plotly.min.js" in files and b"MIT" in files["assets/NOTICE.txt"]
    import plotly

    fake = tmp_path / "fake_plotly"
    (fake / "package_data").mkdir(parents=True)
    (fake / "package_data/plotly.min.js").write_text("/* " + str(p.root) + " */", encoding="utf-8")
    monkeypatch.setattr(plotly, "__file__", str(fake / "__init__.py"))
    with pytest.raises(PublicationError) as caught:
        build.preview_package(p)
    assert caught.value.code == "leak_found"


def test_excluded_copied_code_literal_fails_only_where_code_is_in_the_profile(tmp_path):
    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    private = core.record(p, "note", CANARY, origin=ORIGIN).event
    (p.scripts / "p.py").write_text("# " + CANARY + "\nprint(1)\n", encoding="utf-8")
    run = runner.execute(p, script="scripts/p.py", title="Public run", origin=ORIGIN)["run"]
    core.record(
        p,
        "finding",
        "Calculation",
        refs=[core.kit_reference("run", run_id=run["id"])],
        origin=ORIGIN,
    )
    request = ShareRequest(exclude=frozenset({ItemKey("event", private["id"])}))
    assert build.preview_package(p, request, profile="read")[0]["ok"]
    with pytest.raises(PublicationError) as caught:
        build.preview_package(p, request, profile="evidence")
    assert caught.value.code == "leak_found" and caught.value.details["cause"] == "copied_file"


def test_preview_open_audit_allows_only_declared_read_channels_and_no_writes():
    script = r"""
import sys,os,tempfile
from pathlib import Path
from importlib import metadata
from aelix_analytics import core
from aelix_analytics.publishing import build,package
with tempfile.TemporaryDirectory() as temporary:
    project=core.init_project(Path(temporary)/'p',title='Public',origin=core.Origin('human','sdk'))
    private=project.root/'not-a-record.txt';private.write_text('must not read',encoding='utf-8')
    build.preview_package(project) # rendering imports are prepared before the I/O boundary
    allowed={p.resolve() for p in project.root.rglob('*') if p.is_file() and p!=private}
    allowed.add((project.root/'.analytics/cache/hashes.json').resolve())
    allowed.update(p.resolve() for p in Path(package.__file__).with_name('assets').iterdir())
    dist=metadata.distribution('markdown-it-py')
    allowed.update(Path(dist.locate_file(p)).resolve() for p in dist.files if str(p).endswith('/METADATA'))
    tempfile.tempdir=None # expose accidental temporary-directory probes inside the preview.
    armed=[True]
    def audit(event,args):
        if not armed[0] or event!='open' or not isinstance(args[0],(str,bytes,os.PathLike)):
            return
        path=Path(os.fsdecode(args[0])).resolve();mode=args[1] or '';flags=args[2]
        if any(c in mode for c in 'wa+') or flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
            raise AssertionError('preview attempted a write')
        if path not in allowed:
            raise AssertionError('preview opened an undeclared source: '+str(path))
    sys.addaudithook(audit)
    for op in (lambda:private.read_bytes(),lambda:project.context.open('wb')):
        try:op()
        except AssertionError:pass
        else:raise AssertionError('I/O audit negative control failed')
    build.preview_package(project)
    armed[0]=False # tempfile teardown is outside the product's I/O boundary.
    print('PASS: declared reads, no write/lock/context sync')
"""
    done = subprocess.run(
        [sys.executable, "-c", script],
        env=kit_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("PASS: declared reads")


def test_renderer_has_no_project_or_file_channel_after_modules_are_loaded():
    script = r"""
import sys
from markdown_it import MarkdownIt
from aelix_analytics.publishing.render import render
from aelix_analytics.publishing.model import *
MarkdownIt('commonmark',{'html':False}).render('warmup')
key=ItemKey('context','recorded.md')
model=ShareModel(ContentBasis(1,None,'recorded.md'),'in_progress',Text('Public','user',(ItemKey('project','project'),'title')),
    (ShareItem(key,'context',(Field('text',Text('Included **context**','user',(key,'text'))),)),),())
def audit(event,args):
    if event=='open':raise AssertionError('renderer opened a file')
sys.addaudithook(audit)
files=render(model)
assert b'Included <strong>context</strong>' in b''.join(files.values())
print('PASS: pure renderer')
"""
    done = subprocess.run(
        [sys.executable, "-c", script],
        env=kit_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "PASS: pure renderer"


def manifest_fixture():
    from aelix_analytics.publishing.checks import content_hash

    files = {
        "index.html": b'<!doctype html><html lang="en"><p>Public</p></html>',
        "about.html": b'<!doctype html><html lang="en"><p>About this copy</p></html>',
    }
    value = {
        "schema": "analytics.publication/1",
        "publication_id": "pub-" + "0" * 26,
        "built_at": "2026-10-01T00:00:00+00:00",
        "entry": "index.html",
        "basis": {
            "as_of_seq": 1,
            "content_seq": 1,
            "content_recorded_at": "2026-10-01T00:00:00+00:00",
            "context_version": None,
        },
        "analysis_state": {"value": "in_progress", "basis": "declared"},
        "ui_locale": "en",
        "profile": {"id": "read", "rules_version": 1},
        "scope": {"kind": "project", "reports": []},
        "included": [{"category": "project", "identity": "project"}],
        "not_included": [],
        "reader_features": ["read", "relations"],
        "renderer": {"kit_version": "0.21.0", "format": 1, "libraries": []},
        "remote_links": [],
        "limits": {},
        "checks": {
            "format": 1,
            "result": "pass",
            "inspection": {"inspected": 3, "pinned_scanned": 0, "not_inspectable": 0},
        },
        "content_sha256": content_hash(files),
        "files": [
            {
                "path": p,
                "bytes": len(b),
                "sha256": hashlib.sha256(b).hexdigest(),
                "origin": "identity" if p == "about.html" else "generated",
                "format": "html",
                "inspection": "inspected",
            }
            for p, b in sorted(files.items())
        ],
    }
    return value, files


def with_manifest(value, files):
    return {
        **files,
        "publication.json": (
            json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode(),
    }


def test_verify_package_memory_and_folder_refuses_mutation_extra_files_and_links(tmp_path):
    value, files = manifest_fixture()
    package_files = with_manifest(value, files)
    assert checks.verify_package(package_files)["ok"]
    folder = package.write_package(tmp_path / "package", package_files)
    (folder / ".DS_Store").write_bytes(b"OS metadata")
    assert checks.verify_package(folder)["metadata"] == [".DS_Store"]
    (folder / "index.html").write_bytes(b"changed")
    with pytest.raises(PublicationError, match="bytes differ"):
        checks.verify_package(folder)
    (folder / "index.html").write_bytes(files["index.html"])
    (folder / "unexpected.txt").write_bytes(b"not declared")
    with pytest.raises(PublicationError, match="file list"):
        checks.verify_package(folder)
    (folder / "unexpected.txt").unlink()
    (folder / "empty").mkdir()
    with pytest.raises(PublicationError, match="empty"):
        checks.verify_package(folder)
    (folder / "empty").rmdir()
    try:
        (folder / "link").symlink_to(folder / "index.html")
    except OSError:
        pytest.skip("platform does not allow fixture symlinks; prior assertions already executed")
    with pytest.raises(PublicationError, match="link"):
        checks.verify_package(folder)


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["basis"].update(content_seq=2),
        lambda m: m["basis"].update(content_seq=True),
        lambda m: m["analysis_state"].update(basis="observed"),
        lambda m: m["profile"].update(rules_version=False),
        lambda m: m["reader_features"].append("search"),
        lambda m: m["included"][0].update(unknown=CANARY),
        lambda m: m["files"][0].update(inspection="not_inspectable"),
        lambda m: m["files"][0].update(origin="unknown"),
        lambda m: m.update(project_id=CANARY),
        lambda m: m["renderer"].update(source_revision=CANARY),
    ],
)
def test_verify_package_validates_the_known_manifest_contract_not_only_its_file_hashes(change):
    value, files = manifest_fixture()
    change(value)
    with pytest.raises(PublicationError):
        checks.verify_package(with_manifest(value, files))


def test_verify_package_checks_binding_semantics_in_addition_to_digest():
    value, files = manifest_fixture()
    view = with_manifest(value, files)
    binding = {
        "publication_id": value["publication_id"],
        "format": value["schema"],
        "package_sha256": hashlib.sha256(view["publication.json"]).hexdigest(),
        "content_sha256": value["content_sha256"],
        "profile": value["profile"],
        "scope": value["scope"]["kind"],
        "basis": {k: v for k, v in value["basis"].items() if k != "content_recorded_at"},
        "analysis_state": value["analysis_state"],
        "checks": value["checks"],
    }
    assert checks.verify_package(view, binding)["ok"]
    with pytest.raises(PublicationError, match="binding"):
        checks.verify_package(view, {**binding, "profile": {"id": "evidence", "rules_version": 1}})


def test_excluded_legacy_device_names_and_kit_vocabulary_do_not_make_generated_names_unusable(
    tmp_path,
):
    from aelix_analytics.publishing.projection import SnapshotItem, freeze

    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    snapshot = capture_snapshot(p)
    names = ("con", "nul", "publication", "stylesheet", "en")
    snapshot = replace(
        snapshot,
        items=(
            *snapshot.items,
            *(
                SnapshotItem(ItemKey("dataset", "opaque-" + n), "dataset", freeze({"name": n}))
                for n in names
            ),
        ),
    )
    summary, files = build.preview_package(p, snapshot=snapshot)
    assert summary["ok"] and summary["included"].get("dataset", 0) == 0
    assert all("/con." not in name and "/nul." not in name for name in files)


def test_human_launcher_requires_existing_environment_and_never_bootstraps_it(
    tmp_path, monkeypatch, capsys
):
    from aelix_analytics import cli

    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(p.root))
    monkeypatch.setattr(cli, "_uv", lambda *a: pytest.fail("preview bootstrapped uv"))
    calls = []

    def child(self, argv, env, **kwargs):
        calls.append(
            (
                argv,
                {k: env.get(k) for k in ("PYTHONDONTWRITEBYTECODE", "VIRTUAL_ENV", "PYTHONPATH")},
            )
        )
        return 0

    monkeypatch.setattr(cli._Children, "run", child)
    assert cli.run(["build", "--preview"], kit=ROOT / "src") == 1
    assert not (p.root / ".venv").exists() and not calls
    assert "existing project environment" in capsys.readouterr().err
    python = p.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    assert cli.run(["build", "--preview"], kit=ROOT / "src") == 0
    assert calls[0][0] == [
        str(python),
        "-m",
        "aelix_analytics",
        "--project",
        str(p.root),
        "build",
        "--preview",
    ]
    assert calls[0][1]["PYTHONDONTWRITEBYTECODE"] == "1" and calls[0][1]["VIRTUAL_ENV"] is None


def test_unobserved_inspection_facts_cannot_be_injected_by_a_projection(tmp_path):
    from aelix_analytics.publishing.model import RenderFacts

    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    snapshot = capture_snapshot(p)
    base = ReadProjection().project(snapshot, ShareRequest())

    class Invented:
        id, rules_version = "invented", 1

        def project(self, *args):
            return replace(base, model=replace(base.model, facts=RenderFacts(no_recorded_hash=999)))

    with pytest.raises(PublicationError, match="unobserved inspection facts"):
        build.preview_package(p, projection=Invented())


def test_final_scope_has_inspection_facts_and_plotly_count(tmp_path):
    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    summary, files = build.preview_package(p)
    assert (str(summary["checks"]["inspection"]["inspected"]) + " files.").encode() in files[
        "index.html"
    ]
    assert b"Files without a recorded hash: 0." in files["index.html"]
    assert (
        summary["renderer"]["kit_version"] == core.KIT_VERSION and summary["checks"]["format"] == 1
    )


@pytest.mark.parametrize(
    "paths", [["a", "a/b"], ["A/a", "a/b"], ["😀" * 100 + ".html"], ["bad\ud800.html"]]
)
def test_portable_names_account_for_directory_spelling_and_utf8_utf16_lengths(paths):
    with pytest.raises(PublicationError):
        checks.check_names(paths)


def test_self_consistent_manifest_with_forbidden_links_is_not_verified():
    value, files = manifest_fixture()
    files["index.html"] = b'<!doctype html><html><img src="https://remote.test/private"></html>'
    value["files"] = [
        {
            **row,
            "bytes": len(files[row["path"]]),
            "sha256": hashlib.sha256(files[row["path"]]).hexdigest(),
        }
        for row in value["files"]
    ]
    value["content_sha256"] = checks.content_hash(files)
    with pytest.raises(PublicationError, match="external resource"):
        checks.verify_package(with_manifest(value, files))


def test_manifest_duplicate_keys_and_nonfinite_json_are_refused():
    value, files = manifest_fixture()
    view = with_manifest(value, files)
    duplicate = b'{"entry":"private",' + view["publication.json"][1:]
    with pytest.raises(PublicationError, match="duplicate"):
        checks.verify_package({**view, "publication.json": duplicate})
    invalid = view["publication.json"].replace(b'"limits":{}', b'"limits":{"unsupported":NaN}')
    with pytest.raises(PublicationError, match="nonfinite"):
        checks.verify_package({**view, "publication.json": invalid})


def test_xml_namespace_and_xmp_entities_are_decoded_in_svg_png_jpeg_webp():
    escaped = "".join("&#" + str(ord(c)) + ";" for c in CANARY).encode()
    packet = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:r="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><r:RDF><r:Description Artist="'
        + escaped
        + b'"/></r:RDF></x:xmpmeta>'
    )
    body = b"http://ns.adobe.com/xap/1.0/\0" + packet
    jpeg = image("JPEG")
    jpeg = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + jpeg[2:]
    webp = image("WEBP")
    extra = b"XMP " + struct.pack("<I", len(packet)) + packet + (b"\0" if len(packet) & 1 else b"")
    webp = webp[:4] + struct.pack("<I", len(webp) + len(extra) - 8) + webp[8:] + extra
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:unused="urn:'
        + escaped
        + b'"><path d="M0 0"/></svg>'
    )
    for data in (jpeg, webp, svg, png((b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + packet))):
        inspected = inspect_bytes(data, role="figure")
        with pytest.raises(PublicationError, match="excluded content"):
            checks.check_strings(inspected.strings, POLICY, path="metadata")


def test_plotly_frames_image_sources_typed_arrays_and_record_disguises():
    import numpy as np
    import plotly.graph_objects as go

    from aelix_analytics.publishing.package import figure_points

    value = json.loads(
        go.Figure(go.Scatter(y=np.array([1.0, 2.0]), customdata=[[1, 2, 3], [4, 5, 6]])).to_json()
    )
    assert figure_points(value) == 2
    value["frames"] = [{"name": "next", "data": [{"type": "scatter", "y": [3, 4]}]}]
    assert figure_points(value) == 4
    assert inspect_bytes(json.dumps(value).encode(), role="figure") is not None
    for figure in (
        {"data": [{"type": "image", "source": "https://remote.test/a.png"}], "layout": {}},
        {"data": [], "layout": {}, "frames": [{"data": [{"type": "scattergeo"}]}]},
    ):
        with pytest.raises(PublicationError) as caught:
            inspect_bytes(json.dumps(figure).encode(), role="figure")
        assert caught.value.code == "figure_not_shareable"
    assert (
        inspect_bytes(b'{"schema":"analytics.run/2","data":[],"layout":{}}', role="figure") is None
    )
    assert (
        inspect_bytes(
            b"<html>invalid Python source but readable code snapshot</html>", role="code_snapshot"
        )
        is not None
    )


def test_exact_relative_path_bound_is_physically_writable_on_the_running_platform(tmp_path):
    name = "x" * 209
    assert checks.check_names([name]) == 240
    root = package.write_package(tmp_path / ("pub-" + "0" * 26), {name: b"portable"})
    assert (root / name).read_bytes() == b"portable"
    measured = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "relative_utf16": 240,
        "absolute_utf16": len(str(root / name).encode("utf-16-le")) // 2,
        "write_read": "pass",
    }
    output = os.environ.get("AELIX_ANALYTICS_TEST_EVIDENCE_DIR")
    if output:
        path = Path(output) / "publication-path-bound.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(measured, sort_keys=True) + "\n", encoding="utf-8")


def test_strategy_cannot_omit_the_required_basis_context_and_export_its_identity(tmp_path):
    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    snapshot = capture_snapshot(p)
    base = ReadProjection().project(snapshot, ShareRequest())
    removed = {i.key for i in snapshot.items if i.kind == "context"}

    class MissingBasis:
        id, rules_version = "missing-basis", 1

        def project(self, *args):
            return replace(
                base,
                model=replace(
                    base.model, items=tuple(i for i in base.model.items if i.key not in removed)
                ),
                ledger=replace(
                    base.ledger, included=tuple(k for k in base.ledger.included if k not in removed)
                ),
            )

    with pytest.raises(PublicationError, match="basis CONTEXT"):
        build.preview_package(p, projection=MissingBasis())


def test_included_canary_remains_authored_content_in_both_profiles(tmp_path):
    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    core.record(p, "finding", CANARY + "\r\n원문", origin=ORIGIN)
    for profile in ("read", "evidence"):
        summary, files = build.preview_package(p, profile=profile)
        assert summary["ok"] and CANARY.encode() in b"".join(files.values())
        assert b"&#13;" in b"".join(files.values())
        assert all(
            b"\r" not in data
            for path, data in files.items()
            if path.endswith((".html", ".txt", ".css", ".js"))
        )


def test_zip_verifier_checks_root_utf8_and_fixed_metadata_before_trusting_files(tmp_path):
    import zipfile

    value, files = manifest_fixture()
    view = with_manifest(value, files)

    class UTF8Info(zipfile.ZipInfo):
        def _encodeFilenameFlags(self):
            return self.filename.encode("utf-8"), self.flag_bits | 0x800

    def archive(prefix, utf8=True, date=(1980, 1, 1, 0, 0, 0)):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as writer:
            for name, data in sorted(view.items()):
                info = (UTF8Info if utf8 else zipfile.ZipInfo)(prefix + name, date_time=date)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                writer.writestr(info, data)
        target = tmp_path / "package.zip"
        target.write_bytes(out.getvalue())
        return target

    assert checks.verify_package(archive(value["publication_id"] + "/"))["ok"]
    for prefix, utf8, date in [
        ("../" + value["publication_id"] + "/", True, (1980, 1, 1, 0, 0, 0)),
        (value["publication_id"] + "/", False, (1980, 1, 1, 0, 0, 0)),
        (value["publication_id"] + "/", True, (2026, 1, 1, 0, 0, 0)),
        ("pub-" + "1" * 26 + "/", True, (1980, 1, 1, 0, 0, 0)),
    ]:
        with pytest.raises(PublicationError):
            checks.verify_package(archive(prefix, utf8, date))


@pytest.mark.parametrize(
    "attribute", ["fill", "stroke", "filter", "clip-path", "mask", "marker-start", "cursor"]
)
def test_svg_presentation_urls_share_the_css_resource_and_data_uri_checks(attribute):
    remote = f'<svg xmlns="http://www.w3.org/2000/svg"><path {attribute}="url(https://remote.test/a.svg#paint)"/></svg>'.encode()
    inspected = inspect_bytes(remote, role="figure")
    assert inspected.links[0].value == "https://remote.test/a.svg#paint"
    with pytest.raises(PublicationError, match="remote_resources"):
        package._user_links(inspected)
    with pytest.raises(PublicationError, match="external resource"):
        checks.check_package({"files/figure.svg": remote}, {"files/figure.svg": "figure"})
    child = (
        '<svg xmlns="http://www.w3.org/2000/svg"><metadata>'
        + CANARY
        + '</metadata><linearGradient id="paint"/></svg>'
    ).encode()
    raw = (
        f'<svg xmlns="http://www.w3.org/2000/svg"><path {attribute}="url(data:image/svg+xml;base64,'.encode()
        + base64.b64encode(child)
        + b'#paint)"/></svg>'
    )
    with pytest.raises(PublicationError, match="excluded content"):
        checks.check_strings(
            inspect_bytes(raw, role="figure").strings, POLICY, path="files/copy.svg"
        )
    local = f'<svg xmlns="http://www.w3.org/2000/svg"><linearGradient id="paint"/><path {attribute}="url(#paint)"/></svg>'.encode()
    checks.check_package({"files/figure.svg": local}, {"files/figure.svg": "figure"})


def test_full_preview_rejects_svg_data_uri_canary_and_omits_remote_resources(tmp_path):
    child = (
        '<svg xmlns="http://www.w3.org/2000/svg"><metadata>' + CANARY + "</metadata></svg>"
    ).encode()
    cases = {
        "remote.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="url(https://remote.test/a.svg#paint)"/></svg>',
        "remote.html": b'<body background="https://remote.test/bg.png"><p>Included</p></body>',
        "private.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="url(data:image/svg+xml;base64,'
        + base64.b64encode(child)
        + b')"/></svg>',
    }
    for n, (name, raw) in enumerate(cases.items()):
        p = core.init_project(tmp_path / f"p{n}", title="Public", origin=ORIGIN)
        core.record(p, "note", CANARY, origin=ORIGIN)
        (p.root / name).write_bytes(raw)
        (p.scripts / "show.py").write_text(
            "from pathlib import Path\nimport aelix_analytics as a\na.visualize(Path("
            + repr(name)
            + '), "one", title="Included figure")\n',
            encoding="utf-8",
        )
        assert (
            runner.execute(p, script="scripts/show.py", title="Public chart", origin=ORIGIN)["run"][
                "status"
            ]
            == "ok"
        )
        if name == "private.svg":
            with pytest.raises(PublicationError) as caught:
                build.preview_package(p)
            assert caught.value.code == "leak_found"
        else:
            summary, files = build.preview_package(p)
            assert raw not in files.values()
            assert any(row["reason"] == "remote_resources" for row in summary["not_included"])


@pytest.mark.parametrize(
    "value",
    [
        b"II",
        b"II" + struct.pack("<HIH", 41, 8, 0) + bytes(4),
        b"II"
        + struct.pack("<HIH", 42, 8, 1)
        + struct.pack("<HHII", 0x13B, 2, 100, 100000)
        + bytes(4),
    ],
)
def test_malformed_exif_cannot_be_reported_as_inspected(value):
    with pytest.raises(PublicationError) as caught:
        checks.check_package(
            {"files/meta.png": png((b"eXIf", value))}, {"files/meta.png": "figure"}
        )
    assert caught.value.code == "inspection_failed"


def test_nested_image_metadata_warns_on_the_enclosing_preview_file(tmp_path):
    from PIL import Image

    output = io.BytesIO()
    exif = Image.Exif()
    exif[0x013B] = "Public author"
    Image.new("RGB", (1, 1)).save(output, format="JPEG", exif=exif)
    raw = b'<img src="data:image/jpeg;base64,' + base64.b64encode(output.getvalue()) + b'">'
    inspected = inspect_bytes(raw, role="figure")
    assert any("Artist" in key for key in inspected.metadata_keys)
    p = core.init_project(tmp_path / "p", title="Public", origin=ORIGIN)
    (p.root / "source.html").write_bytes(raw)
    (p.scripts / "show.py").write_text(
        'from pathlib import Path\nimport aelix_analytics as a\na.visualize(Path("source.html"), "one", title="Public figure")\n',
        encoding="utf-8",
    )
    assert (
        runner.execute(p, script="scripts/show.py", title="Public chart", origin=ORIGIN)["run"][
            "status"
        ]
        == "ok"
    )
    summary, files = build.preview_package(p)
    assert any(
        row["reason"] == "image_metadata" and "Artist" in row["key"] for row in summary["warnings"]
    )
    assert b"Author/software metadata" in files["index.html"]


def test_parquet_map_schema_keys_values_and_field_metadata_are_inspected():
    import pyarrow as pa
    import pyarrow.parquet as pq

    for where in ("clean", "key", "value", "metadata"):
        typ = pa.map_(
            pa.string(),
            pa.field(
                "value",
                pa.string(),
                metadata={b"author": CANARY.encode()} if where == "metadata" else None,
            ),
        )
        schema = pa.schema([pa.field("values", typ)])
        out = io.BytesIO()
        table = pa.Table.from_pylist(
            [
                {
                    "values": [
                        (
                            CANARY if where == "key" else "name",
                            CANARY if where == "value" else "value",
                        )
                    ]
                }
            ],
            schema=schema,
        )
        pq.write_table(table, out)
        inspected = parquet_strings(out.getvalue())
        if where == "clean":
            checks.check_strings(inspected.strings, POLICY, path="files/table.parquet")
        else:
            with pytest.raises(PublicationError, match="excluded content"):
                checks.check_strings(inspected.strings, POLICY, path="files/table.parquet")
