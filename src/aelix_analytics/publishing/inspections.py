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
from urllib.parse import unquote, unquote_to_bytes, urldefrag

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
RESOURCE_ATTRS = frozenset({"src", "poster", "background", "data-payload"})
SVG_URL_ATTRS = frozenset(
    {
        "fill",
        "stroke",
        "filter",
        "clip-path",
        "mask",
        "marker",
        "marker-start",
        "marker-mid",
        "marker-end",
        "cursor",
        "color-profile",
    }
)
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
    if role == "code_snapshot":
        return "text"
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
        except (ValueError, RecursionError):
            value = None
        if (
            isinstance(value, dict)
            and set(value) <= {"data", "layout", "frames"}
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


def css_tokens(text, location="css"):
    """Tokenize resource functions and strings while retaining CSS escape semantics."""
    pos, links, values = 0, [], []

    def escape():
        nonlocal pos
        pos += 1
        if pos >= len(text):
            failed("unterminated CSS escape")
        if text[pos] in "\r\n\f":
            if text[pos : pos + 2] == "\r\n":
                pos += 1
            pos += 1
            return ""
        match = re.match(r"[0-9a-fA-F]{1,6}", text[pos:])
        if match:
            number = int(match[0], 16)
            pos += len(match[0])
            if pos < len(text) and text[pos].isspace():
                if text[pos : pos + 2] == "\r\n":
                    pos += 1
                pos += 1
            return (
                chr(number)
                if 0 < number <= 0x10FFFF and not 0xD800 <= number <= 0xDFFF
                else "\ufffd"
            )
        value = text[pos]
        pos += 1
        return value

    def skip():
        nonlocal pos
        while pos < len(text):
            if text[pos].isspace():
                pos += 1
            elif text[pos : pos + 2] == "/*":
                end = text.find("*/", pos + 2)
                if end < 0:
                    failed("unterminated CSS comment")
                pos = end + 2
            else:
                break

    def quoted():
        nonlocal pos
        quote = text[pos]
        pos += 1
        value = ""
        while pos < len(text):
            char = text[pos]
            if char == quote:
                pos += 1
                return value
            if char in "\r\n\f":
                failed("unescaped newline in CSS string")
            if char == "\\":
                value += escape()
            else:
                value += char
                pos += 1
        failed("unterminated CSS string")

    def identifier():
        nonlocal pos
        value = ""
        while pos < len(text):
            if text[pos] == "\\":
                value += escape()
            elif text[pos].isalnum() or text[pos] in "_-":
                value += text[pos]
                pos += 1
            else:
                break
        return value

    importing = False
    while pos < len(text):
        skip()
        if pos >= len(text):
            break
        start = pos
        if text[pos] in "\"'":
            value = quoted()
            values.append(ScanText(value, f"{location}:{start}"))
            if importing:
                links.append(ResourceLink(value, f"{location}:{start}"))
                importing = False
        elif text[pos] == "@":
            pos += 1
            importing = identifier().lower() == "import"
        elif text[pos].isalpha() or text[pos] in "_-\\":
            name = identifier()
            values.append(ScanText(name, f"{location}:{start}"))
            skip()
            if name.lower() == "url" and pos < len(text) and text[pos] == "(":
                pos += 1
                skip()
                if pos < len(text) and text[pos] in "\"'":
                    value = quoted()
                    skip()
                else:
                    value = ""
                    while pos < len(text) and text[pos] != ")":
                        char = text[pos]
                        if char.isspace():
                            skip()
                            if pos >= len(text) or text[pos] != ")":
                                failed("whitespace inside an unquoted CSS URL")
                            break
                        if char in "(\"'":
                            failed("invalid unquoted CSS URL")
                        if char == "\\":
                            value += escape()
                        else:
                            value += char
                            pos += 1
                if pos >= len(text) or text[pos] != ")":
                    failed("unterminated CSS URL")
                pos += 1
                links.append(ResourceLink(value, f"{location}:{start}"))
                values.append(ScanText(value, f"{location}:{start}", True))
                importing = False
        else:
            if text[pos] == ";":
                importing = False
            pos += 1
    return tuple(links), tuple(values)


def css_links(text, location="css"):
    return css_tokens(text, location)[0]


class HTMLInspection(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.strings = [ScanText(text, "raw")]
        self.links, self.ids, self.unsafe = [], [], []
        self.forbidden, self.script_text = [], []
        self._style = False
        self._script = False
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if (
            tag in ("base", "form")
            or tag == "meta"
            and (values.get("http-equiv") or "").strip().lower() == "refresh"
        ):
            self.forbidden.append(tag)
        if tag == "iframe" and (
            "sandbox" not in values
            or any(
                flag in (values.get("sandbox") or "").lower().split()
                for flag in ("allow-scripts", "allow-same-origin")
            )
        ):
            self.forbidden.append("iframe sandbox")
        if tag == "script":
            self._script = True
        if (
            tag in UNSAFE_TAGS
            or tag == "meta"
            and (values.get("http-equiv") or "").strip().lower() == "refresh"
        ):
            self.unsafe.append("scripts" if tag == "script" else "remote_resources")
        if tag == "style":
            self._style = True
        for name, value in attrs:
            value = value or ""
            loc = f"line{self.getpos()[0]}:{tag}@{name}"
            self.strings.append(
                ScanText(
                    value,
                    loc,
                    name
                    in (
                        "href",
                        "src",
                        "srcset",
                        "id",
                        "download",
                        "data-payload",
                        "data-figure",
                        "title",
                        "alt",
                        "aria-label",
                    ),
                )
            )
            if name == "id":
                self.ids.append(value)
            if name.startswith("on"):
                self.unsafe.append("scripts")
                self.forbidden.append("event handler")
            if name == "srcdoc":
                self.forbidden.append("srcdoc")
            if name == "style":
                links, values_ = css_tokens(value, loc)
                self.links.extend(links)
                self.strings.extend(values_)
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
        if tag == "script":
            self._script = False

    def handle_data(self, data):
        self.strings.append(ScanText(data, f"line{self.getpos()[0]}:text"))
        if self._style:
            links, values = css_tokens(data)
            self.links.extend(links)
            self.strings.extend(values)
        if self._script:
            self.script_text.append(data)

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


class _XMLBuilder(ET.TreeBuilder):
    def __init__(self):
        super().__init__(insert_comments=True, insert_pis=True)
        self.namespaces = []

    def start_ns(self, prefix, uri):
        self.namespaces.append((prefix, uri))

    def doctype(self, *args):
        failed("XML document/entity declarations are not permitted")


def _xml(data):
    target = _XMLBuilder()
    try:
        root = ET.fromstring(data, parser=ET.XMLParser(target=target))
    except ET.ParseError as exc:
        failed(f"invalid XML metadata/document: {type(exc).__name__}")
    return root, tuple(target.namespaces)


def _xmp(data, where):
    root, namespaces = _xml(data)
    values = [ScanText(uri, where + ":namespace") for _, uri in namespaces]
    keys = []
    for element in root.iter():
        if isinstance(element.tag, str):
            values.append(ScanText(element.tag, where + ":tag", True))
            keys.append(element.tag.rsplit("}", 1)[-1])
        for value in (element.text, element.tail):
            if value:
                values.append(ScanText(value, where + ":value"))
        for key, value in element.attrib.items():
            values.extend(
                (ScanText(key, where + ":key", True), ScanText(value, where + ":attribute"))
            )
            keys.append(key.rsplit("}", 1)[-1])
    return values, keys


def _exif(data, where):
    keys = []
    if data.startswith(b"http://ns.adobe.com/xmp/extension/"):
        failed("extended XMP metadata is not supported")
    prefix = b"http://ns.adobe.com/xap/1.0/\0"
    if data.startswith(prefix) or "XMP" in where:
        packet = data[len(prefix) :] if data.startswith(prefix) else data
        values, keys = _xmp(packet, where)
        return [*_binary_strings(data, where), *values], keys
    raw = data[6:] if data.startswith(b"Exif\x00\x00") else data
    if len(raw) < 8 or raw[:2] not in (b"II", b"MM"):
        failed("EXIF metadata has no complete TIFF header")
    order = "<" if raw[:2] == b"II" else ">"
    if struct.unpack_from(order + "H", raw, 2)[0] != 42:
        failed("unsupported EXIF TIFF header")
    seen, pending = set(), [struct.unpack_from(order + "I", raw, 4)[0]]
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
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
        if off < 8 or off + 2 > len(raw):
            failed("EXIF directory offset is outside its bytes")
        count = struct.unpack_from(order + "H", raw, off)[0]
        if off + 2 + 12 * count + 4 > len(raw):
            failed("truncated EXIF directory")
        for n in range(count):
            tag, type_, count_, value = struct.unpack_from(order + "HHII", raw, off + 2 + n * 12)
            if type_ not in sizes:
                failed("unsupported EXIF value type")
            size = sizes[type_] * count_
            if size > 4 and (value < 8 or value + size > len(raw)):
                failed("EXIF value is outside its bytes")
            if tag in names:
                keys.append(where + ":" + names[tag])
            if tag in (0x8769, 0x8825, 0xA005) and type_ == 4 and count_ == 1 and value:
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
                    if keyword.lower() == b"xml:com.adobe.xmp":
                        texts, names = _xmp(rest, loc)
                        values.extend(texts)
                        keys.extend(names)
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
                # JPEG comments are free text; only APP1 has EXIF/XMP structure.
                if marker == 0xFE:
                    texts, names = list(_binary_strings(body, "jpeg:comment")), []
                else:
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
        links = []
        frames = value.get("frames", [])
        if not isinstance(frames, list) or not all(isinstance(frame, dict) for frame in frames):
            failed("figure frames are not objects")
        for figure in (value, *frames):
            traces = figure.get("data", [])
            layout = figure.get("layout", {})
            if not isinstance(traces, list) or not isinstance(layout, dict):
                failed("figure data/layout has an invalid shape")
            for trace in traces:
                if (
                    not isinstance(trace, dict)
                    or "type" in trace
                    and not isinstance(trace["type"], str)
                ):
                    failed("figure trace has an invalid shape")
                if trace.get("type") in REMOTE_TRACES:
                    raise PublicationError("figure_not_shareable", "remote_resources")
                if trace.get("type") == "image" and isinstance(trace.get("source"), str):
                    links.append(ResourceLink(trace["source"], "plotly:data.source"))
            if any(k in layout for k in ("geo", "map", "mapbox")):
                raise PublicationError("figure_not_shareable", "remote_resources")
            template = layout.get("template", {})
            template = template if isinstance(template, dict) else {}
            layouts = (layout, template.get("layout", {}))
            for part in layouts:
                if not isinstance(part, dict) or not isinstance(part.get("images", []), list):
                    failed("figure images have an invalid shape")
                for image in part.get("images", []):
                    if isinstance(image, dict) and isinstance(image.get("source"), str):
                        links.append(ResourceLink(image["source"], "plotly:layout.images.source"))
            for source in (
                template.get("data", {}).get("image", [])
                if isinstance(template.get("data", {}), dict)
                else []
            ):
                if isinstance(source, dict) and isinstance(source.get("source"), str):
                    links.append(ResourceLink(source["source"], "plotly:template.image.source"))
        if any(re.match(r"(?:https?:)?//", link.value) for link in links):
            raise PublicationError("figure_not_shareable", "remote_resources")
        result = Inspection(format_, tuple(strings(value)), tuple(links))
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
            root, namespaces = _xml(text)
        except ET.ParseError:
            failed("invalid SVG XML")
        values, links, ids = (
            [ScanText(text, "raw"), *(ScanText(uri, "svg:namespace") for _, uri in namespaces)],
            [],
            [],
        )
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
                if k == "style" or k in SVG_URL_ATTRS:
                    refs, parts = css_tokens(v, "svg:@" + k)
                    links.extend(refs)
                    values.extend(parts)
            if tag == "style" and element.text:
                refs, parts = css_tokens(element.text, "svg:style")
                links.extend(refs)
                values.extend(parts)
        result = Inspection(format_, tuple(values), tuple(links), ids=tuple(ids))
    else:
        text = utf8(data)
        refs, parts = css_tokens(text) if format_ == "css" else ((), ())
        result = Inspection(format_, (ScanText(text, "raw"), *parts), refs)
    nested = []
    for link in result.links:
        if unquote(link.value.strip().split(",", 1)[0]).lower().startswith("data:"):
            try:
                header, payload = urldefrag(link.value.strip())[0].split(",", 1)
                header = unquote(header)
                value = (
                    base64.b64decode(unquote_to_bytes(payload), validate=True)
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
        tuple(sorted(set((*result.metadata_keys, *(k for r in nested for k in r.metadata_keys))))),
        result.ids,
    )
