"""PR2 byte/channel acceptance; preview is not a saved/bound publication or browser acceptance."""

from __future__ import annotations

import base64
import io
import json
import struct
import subprocess
import sys
import threading
import zlib
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
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
        for name, data in files.items():
            assert urlopen((destination / name).as_uri()).read() == data
            with urlopen(f"http://127.0.0.1:{server.server_port}/copy/{quote(name)}") as response:
                assert response.read() == data
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
