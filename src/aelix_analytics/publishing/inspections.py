"""Byte-format inspection, independent of the renderer and of file names (design11.5)."""

from __future__ import annotations

import base64
import binascii
import json
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from html.parser import HTMLParser
from urllib.parse import unquote_to_bytes

from .model import Inspection, PublicationError, ResourceLink, ScanText

MAX_DATA_DEPTH = 4
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
REMOTE_TRACES = frozenset(
    {
        "scattergeo",
        "choropleth",
        "scattermapbox",
        "choroplethmapbox",
        "densitymapbox",
        "scattermap",
        "choroplethmap",
        "densitymap",
    }
)
RESOURCE_ATTRS = frozenset({"src", "poster", "data-payload"})
UNSAFE_TAGS = frozenset({"script", "iframe", "object", "embed", "base", "foreignobject", "form"})


def failed(message):
    raise PublicationError("inspection_failed", message)


def utf8(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        failed("a permitted text file is not strict UTF-8")


def detect_format(data: bytes, *, role="", path=""):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"PAR1") and data.endswith(b"PAR1"):
        return "parquet"
    if data.startswith((b"%PDF", b"PK\x03\x04", b"\x1f\x8b")):
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    stripped = text.lstrip("\ufeff \t\r\n")
    # Comments before the document element do not turn an HTML/SVG fragment into plain text.
    stripped = re.sub(r"^(?:<!--.*?-->\s*)+", "", stripped, flags=re.S)
    if re.match(r"(?:<\?xml[^>]*>\s*)?<svg(?:\s|>)", stripped, re.I):
        return "svg"
    if stripped.startswith("window.AnalyticsFigures["):
        return "payload"
    if stripped.startswith(("{", "[")):
        try:
            value = json.loads(stripped)
        except ValueError:
            value = None
        if (
            isinstance(value, dict)
            and isinstance(value.get("data"), list)
            and isinstance(value.get("layout", {}), dict)
        ):
            return "plotly"
    if re.match(r"<(?:!doctype\s+html|html\b|[a-zA-Z][\w:-]*(?:\s|>))", stripped, re.I):
        return "html"
    if role == "library":
        return "css" if path.endswith(".css") else "javascript"
    if role in ("generated", "identity") and path.endswith(".css"):
        return "css"
    if role in ("generated", "identity") and path.endswith(".js"):
        return "javascript"
    return "text"


def strings(value, location="$"):
    """JSON keys and values, after JSON escapes have been decoded."""
    if isinstance(value, str):
        yield ScanText(value, location)
    elif isinstance(value, dict):
        for key, part in value.items():
            yield ScanText(key, location + ".key", True)
            yield from strings(part, f"{location}[{key!r}]")
    elif isinstance(value, list):
        for n, part in enumerate(value):
            yield from strings(part, f"{location}[{n}]")


def css_links(text, location="css"):
    # Decode CSS escapes before tokenizing; comments do not create resource references.
    value = re.sub(
        r"\\([0-9a-fA-F]{1,6})\s?|\\([^\r\n0-9a-fA-F])",
        lambda m: (
            chr(int(m[1], 16)) if m[1] and 0 < int(m[1], 16) <= 0x10FFFF else (m[2] or "\ufffd")
        ),
        text,
    )
    value = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
    pattern = (
        r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^\s)]+))\s*\)|@import\s+(?:"([^"]*)"|'([^']*)')"""
    )
    for match in re.finditer(pattern, value, flags=re.I):
        yield ResourceLink(next(v for v in match.groups() if v is not None), location)


class HTMLInspection(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.strings = [ScanText(text, "raw")]
        self.links, self.ids, self.unsafe = [], [], []
        self._style = False
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if (
            tag in UNSAFE_TAGS
            or tag == "meta"
            and (values.get("http-equiv") or "").lower() == "refresh"
        ):
            self.unsafe.append("scripts" if tag == "script" else "remote_resources")
        if tag == "style":
            self._style = True
        for name, value in attrs:
            value = value or ""
            loc = f"line{self.getpos()[0]}:{tag}@{name}"
            self.strings.append(ScanText(value, loc, True))
            if name == "id":
                self.ids.append(value)
            if name.startswith("on"):
                self.unsafe.append("scripts")
            if name == "style":
                self.links.extend(css_links(value, loc))
            elif name == "srcset":
                for candidate in re.finditer(
                    r"(?:data:[^\s]+|[^\s,]+)(?:\s+\d+(?:\.\d+)?[wx])?", value
                ):
                    url = candidate[0].split()[0].rstrip(",")
                    self.links.append(ResourceLink(url, loc))
            elif name == "href":
                self.links.append(ResourceLink(value, loc, tag not in ("a", "area")))
            elif name in RESOURCE_ATTRS or tag == "object" and name == "data":
                self.links.append(ResourceLink(value, loc))
            elif tag == "form" and name == "action":
                self.unsafe.append("scripts")

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if tag == "style":
            self._style = False

    def handle_data(self, data):
        self.strings.append(ScanText(data, f"line{self.getpos()[0]}:text"))
        if self._style:
            self.links.extend(css_links(data))

    def handle_comment(self, data):
        self.strings.append(ScanText(data, f"line{self.getpos()[0]}:comment"))


def _binary_strings(data, location):
    yield ScanText(data.decode("utf-8", errors="replace"), location)
    yield ScanText(data.decode("latin-1"), location)
    if b"\x00" in data:
        for encoding in ("utf-16-le", "utf-16-be"):
            for offset in (0, 1):
                yield ScanText(
                    data[offset:].decode(encoding, errors="replace"), location + ":" + encoding
                )


def _expanded(data):
    try:
        obj = zlib.decompressobj()
        result = obj.decompress(data, MAX_EXPANDED_BYTES + 1)
        if (
            len(result) > MAX_EXPANDED_BYTES
            or obj.unconsumed_tail
            or not obj.eof
            or obj.unused_data
        ):
            failed("compressed metadata is incomplete or exceeds the32MiB inspection limit")
        return result
    except zlib.error as exc:
        failed(f"compressed image metadata cannot be decoded: {exc}")


def _exif(data, where):
    keys = []
    raw = data[6:] if data.startswith(b"Exif\x00\x00") else data
    if len(raw) >= 8 and raw[:2] in (b"II", b"MM"):
        order = "<" if raw[:2] == b"II" else ">"
        seen, pending = set(), [struct.unpack_from(order + "I", raw, 4)[0]]
        names = {
            0x010F: "Make",
            0x0110: "Model",
            0x0131: "Software",
            0x013B: "Artist",
            0x8298: "Copyright",
            0x8825: "GPS",
            0xA431: "BodySerialNumber",
            0xA435: "LensSerialNumber",
        }
        while pending:
            off = pending.pop()
            if off in seen:
                continue
            seen.add(off)
            if off + 2 > len(raw):
                failed("EXIF directory offset is outside its bytes")
            count = struct.unpack_from(order + "H", raw, off)[0]
            if off + 2 + 12 * count + 4 > len(raw):
                failed("truncated EXIF directory")
            for n in range(count):
                tag, type_, count_, value = struct.unpack_from(
                    order + "HHII", raw, off + 2 + n * 12
                )
                if tag in names:
                    keys.append(where + ":" + names[tag])
                if tag in (0x8769, 0x8825, 0xA005) and type_ == 4 and count_ == 1:
                    pending.append(value)
            next_ = struct.unpack_from(order + "I", raw, off + 2 + 12 * count)[0]
            if next_:
                pending.append(next_)
    return list(_binary_strings(data, where)), keys


def _image(data, format_):
    values, keys = [], []
    if format_ == "png":
        pos, ended = 8, False
        while pos < len(data):
            if pos + 12 > len(data):
                failed("truncated PNG chunk")
            length = struct.unpack_from(">I", data, pos)[0]
            kind = data[pos + 4 : pos + 8]
            body = data[pos + 8 : pos + 8 + length]
            if (
                pos + 12 + length > len(data)
                or zlib.crc32(kind + body) & 0xFFFFFFFF
                != struct.unpack_from(">I", data, pos + 8 + length)[0]
            ):
                failed("invalid PNG chunk length or checksum")
            loc = "png:" + kind.decode("ascii", errors="replace")
            if kind in (b"tEXt", b"zTXt", b"iTXt"):
                try:
                    keyword, rest = body.split(b"\0", 1)
                    keys.append(keyword.decode("latin-1"))
                    if kind == b"zTXt":
                        if rest[0] != 0:
                            failed("unsupported PNG compression")
                        rest = _expanded(rest[1:])
                    elif kind == b"iTXt":
                        flag, method = rest[0], rest[1]
                        language, translated, rest = rest[2:].split(b"\0", 2)
                        if flag not in (0, 1) or method != 0:
                            failed("unsupported PNG international text compression")
                        values.extend(_binary_strings(language + translated, loc))
                        rest = _expanded(rest) if flag else rest
                    values.extend(_binary_strings(keyword + b" " + rest, loc))
                except (ValueError, IndexError):
                    failed("malformed PNG text metadata")
            elif kind == b"eXIf":
                texts, names = _exif(body, loc)
                values.extend(texts)
                keys.extend(names)
            elif kind[0] & 32 and kind not in (b"IDAT",):
                values.extend(_binary_strings(body, loc))
            pos += length + 12
            if kind == b"IEND":
                ended = True
                break
        if not ended or pos != len(data):
            failed("PNG has missing terminator or trailing bytes")
    elif format_ == "jpeg":
        pos, ended = 2, False
        while pos < len(data):
            if data[pos] != 0xFF:
                pos += 1
                continue
            while pos < len(data) and data[pos] == 0xFF:
                pos += 1
            if pos == len(data):
                break
            marker = data[pos]
            pos += 1
            if marker == 0xD9:
                ended = True
                break
            if marker in (0, 1, *range(0xD0, 0xD8)):
                continue
            if pos + 2 > len(data):
                failed("truncated JPEG segment")
            length = int.from_bytes(data[pos : pos + 2], "big")
            if length < 2 or pos + length > len(data):
                failed("invalid JPEG segment length")
            body = data[pos + 2 : pos + length]
            if marker in (0xE1, 0xFE):
                texts, names = _exif(body, "jpeg:" + hex(marker))
                values.extend(texts)
                keys.extend(names)
                keys.append("JPEG APP1" if marker == 0xE1 else "JPEG comment")
            pos += length
        if not ended or pos != len(data):
            failed("JPEG has missing terminator or trailing bytes")
    elif format_ == "gif":
        if len(data) < 13:
            failed("truncated GIF header")
        pos = 13 + (3 * (2 ** ((data[10] & 7) + 1)) if data[10] & 0x80 else 0)
        ended = False
        while pos < len(data):
            tag = data[pos]
            pos += 1
            if tag == 0x3B:
                ended = True
                break
            metadata = False
            if tag == 0x21:
                if pos >= len(data):
                    failed("truncated GIF extension")
                metadata = data[pos] in (0xFE, 0xFF)
                pos += 1
            elif tag == 0x2C:
                if pos + 9 > len(data):
                    failed("truncated GIF image")
                packed = data[pos + 8]
                pos += 9
                pos += (3 * (2 ** ((packed & 7) + 1)) if packed & 0x80 else 0) + 1
            else:
                failed("unknown GIF block")
            block = bytearray()
            while True:
                if pos >= len(data):
                    failed("truncated GIF subblock")
                size = data[pos]
                pos += 1
                if not size:
                    break
                if pos + size > len(data):
                    failed("truncated GIF subblock bytes")
                if metadata:
                    block.extend(data[pos : pos + size])
                pos += size
            if metadata:
                values.extend(_binary_strings(bytes(block), "gif:extension"))
                keys.append("GIF comment/application")
        if not ended or pos != len(data):
            failed("GIF has missing terminator or trailing bytes")
    elif format_ == "webp":
        if len(data) < 12 or int.from_bytes(data[4:8], "little") + 8 != len(data):
            failed("invalid WebP RIFF length")
        pos = 12
        while pos < len(data):
            if pos + 8 > len(data):
                failed("truncated WebP chunk")
            kind = data[pos : pos + 4]
            size = int.from_bytes(data[pos + 4 : pos + 8], "little")
            pos += 8
            if pos + size + (size & 1) > len(data):
                failed("truncated WebP chunk bytes")
            if kind in (b"EXIF", b"XMP "):
                texts, names = _exif(data[pos : pos + size], "webp:" + kind.decode())
                values.extend(texts)
                keys.extend(names)
                keys.append(kind.decode())
            pos += size + (size & 1)
    return Inspection(format_, tuple(values), metadata_keys=tuple(sorted(set(keys))))


def inspect_bytes(data, *, role, path="", parquet_extractor=None, depth=0):
    if depth > MAX_DATA_DEPTH:
        failed("nested data URI exceeds depth4")
    format_ = detect_format(data, role=role, path=path)
    allowed = {
        "figure": {"png", "jpeg", "gif", "webp", "svg", "html", "plotly"},
        "attached_data": {"parquet"},
        "code_snapshot": {"text"},
        "report_file": {"png", "jpeg", "gif", "webp", "svg", "html", "parquet", "text", "css"},
        "generated": {"html", "css", "javascript", "payload", "text"},
        "identity": {"html", "javascript", "text"},
        "library": {"javascript", "css"},
        "inline": {"png", "jpeg", "gif", "webp", "svg", "html", "text", "css"},
    }
    if format_ not in allowed.get(role, set()):
        return None
    if format_ in ("png", "jpeg", "gif", "webp"):
        result = _image(data, format_)
    elif format_ == "parquet":
        if parquet_extractor is None:
            failed("pyarrow extractor unavailable")
        result = parquet_extractor(data)
    elif format_ in ("plotly", "payload"):
        text = utf8(data)
        if format_ == "payload":
            match = re.fullmatch(
                r'window\.AnalyticsFigures\["([a-f0-9]{64})"\]\s*=\s*(.*);\s*', text, re.S
            )
            if not match:
                failed("malformed figure payload wrapper")
            text = match[2]
        try:
            value = json.loads(text, parse_constant=lambda value: failed("nonfinite JSON"))
        except ValueError:
            failed("invalid figure JSON")
        if not isinstance(value, dict) or not isinstance(value.get("data"), list):
            failed("figure payload must be an object with data")
        layout = value.get("layout") or {}
        if (
            any(isinstance(t, dict) and t.get("type") in REMOTE_TRACES for t in value["data"])
            or any(k in layout for k in ("geo", "map", "mapbox"))
            or any(
                isinstance(i, dict)
                and isinstance(i.get("source"), str)
                and re.match(r"(?:https?:)?//", i["source"])
                for i in layout.get("images", [])
            )
        ):
            raise PublicationError("figure_not_shareable", "remote_resources")
        links = tuple(
            ResourceLink(i["source"], "plotly:layout.images.source")
            for i in layout.get("images", [])
            if isinstance(i, dict) and isinstance(i.get("source"), str)
        )
        result = Inspection(format_, tuple(strings(value)), links)
    elif format_ == "html":
        parsed = HTMLInspection(utf8(data))
        if role in ("figure", "report_file", "inline") and parsed.unsafe:
            raise PublicationError("figure_not_shareable", parsed.unsafe[0])
        result = Inspection(
            format_, tuple(parsed.strings), tuple(parsed.links), ids=tuple(parsed.ids)
        )
    elif format_ == "svg":
        text = utf8(data)
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
            failed("SVG document/entity declarations are not permitted")
        try:
            root = ET.fromstring(
                text, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
            )
        except ET.ParseError:
            failed("invalid SVG XML")
        values, links, ids = [ScanText(text, "raw")], [], []
        for element in root.iter():
            tag = (
                element.tag.rsplit("}", 1)[-1].lower()
                if isinstance(element.tag, str)
                else "comment"
            )
            if tag in UNSAFE_TAGS:
                raise PublicationError("figure_not_shareable", "scripts")
            for value in (element.text, element.tail):
                if value:
                    values.append(ScanText(value, "svg:" + tag))
            for k, v in element.attrib.items():
                k = k.rsplit("}", 1)[-1].lower()
                values.append(ScanText(v, "svg:@" + k, True))
                if k.startswith("on"):
                    raise PublicationError("figure_not_shareable", "scripts")
                if k == "id":
                    ids.append(v)
                if k == "href":
                    links.append(ResourceLink(v, "svg:@href"))
                if k == "style":
                    links.extend(css_links(v, "svg:style"))
            if tag == "style" and element.text:
                links.extend(css_links(element.text, "svg:style"))
        result = Inspection(format_, tuple(values), tuple(links), ids=tuple(ids))
    else:
        text = utf8(data)
        result = Inspection(
            format_, (ScanText(text, "raw"),), tuple(css_links(text)) if format_ == "css" else ()
        )
    nested = []
    for link in result.links:
        if link.value.lower().startswith("data:"):
            try:
                header, payload = link.value.split(",", 1)
                value = (
                    base64.b64decode(payload, validate=True)
                    if header.lower().endswith(";base64")
                    else unquote_to_bytes(payload)
                )
            except (ValueError, binascii.Error):
                failed("malformed data URI")
            if len(value) > MAX_EXPANDED_BYTES:
                failed("data URI exceeds32MiB inspection limit")
            child = inspect_bytes(
                value,
                role="inline",
                path=path,
                parquet_extractor=parquet_extractor,
                depth=depth + 1,
            )
            if child is None:
                failed("data URI has an uninspectable format")
            nested.append(child)
    return Inspection(
        result.format,
        (*result.strings, *(s for r in nested for s in r.strings)),
        (*result.links, *(link for r in nested for link in r.links)),
        result.metadata_keys,
        result.ids,
    )
