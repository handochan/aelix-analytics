"""Conservative byte-only Report views, injected by an entry/Workspace adapter.

Markdown is rendered with raw HTML disabled. HTML, SVG and other arbitrary formats keep download
links only; no authored document is inserted into the host DOM. This is not a publication checker.
"""
from __future__ import annotations

import html
import posixpath
from collections.abc import Mapping
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlsplit

from .report_renderer import ReportView

PREVIEW_PATH = "preview/report.html"


def _path(value):
    # Local package paths, not URLs. The storage contract has additional portable-name rules.
    return (isinstance(value, str) and bool(value) and all(part not in ("", ".", "..") for part in value.split("/"))
            and not any(char in value for char in "\\:\x00?#") and not any(ord(char) < 32 for char in value))


def _inputs(files, entry, paths):
    if (not isinstance(files, Mapping) or not isinstance(paths, Mapping) or not files
            or not all(_path(key) and isinstance(value, bytes) for key, value in files.items())
            or not isinstance(entry, str) or entry not in files or set(paths) != set(files)
            or not all(_path(value) for value in paths.values())
            or len({path.casefold() for path in paths.values()}) != len(paths)
            or PREVIEW_PATH.casefold() in {path.casefold() for path in paths.values()}):
        raise ValueError("Renderer needs selected bytes, an entry and distinct explicit local package paths")
    # Package output names must also be realizable together on a case-insensitive filesystem.
    names = [PREVIEW_PATH, *paths.values()]
    prefixes, folded = {}, {name.casefold() for name in names}
    for name in names:
        parts = name.split("/")
        for n in range(1, len(parts) + 1):
            prefix = "/".join(parts[:n])
            if prefixes.setdefault(prefix.casefold(), prefix) != prefix or (n < len(parts) and prefix.casefold() in folded):
                raise ValueError("Renderer output paths have conflicting file or directory spellings")
    return dict(files), dict(paths)


def _link(path):
    return quote(posixpath.relpath(path, posixpath.dirname(PREVIEW_PATH)), safe="/")


class _MappedHTML(HTMLParser):
    """Only parse HTML produced by markdown-it with authored HTML disabled."""

    def __init__(self, entry, paths):
        super().__init__(convert_charrefs=False)
        self.entry, self.paths = entry, paths
        self.parts, self.reasons = [], []

    def _url(self, value):
        try:
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or parsed.query:
                raise ValueError
            path = unquote(parsed.path, errors="strict")
            if not path and parsed.fragment:
                return "#" + quote(unquote(parsed.fragment), safe="")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(self.entry), path))
            if not _path(path) and (path.startswith("/") or "\\" in path or "\x00" in path):
                raise ValueError
            if resolved not in self.paths:
                raise ValueError
            return _link(self.paths[resolved]) + ("#" + quote(unquote(parsed.fragment), safe="") if parsed.fragment else "")
        except (ValueError, UnicodeError):
            self.reasons.append("unmapped_or_external_resource")
            return None

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.reasons.append("image_download_only")
            self.parts.append("<span>" + html.escape(dict(attrs).get("alt", "")) + "</span>")
            return
        clean = []
        for name, value in attrs:
            if name in ("href", "src"):
                value = self._url(value or "")
                if value is None:
                    continue
            clean.append((name, value))
        if tag == "a" and any(name == "href" and not value.startswith("#") for name, value in clean):
            clean.append(("download", None))
        self.parts.append("<" + tag + "".join(" " + key + ('="' + html.escape(value, quote=True) + '"' if value is not None else "")
                                              for key, value in clean) + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.parts.append("</" + tag + ">")

    def handle_data(self, data):
        self.parts.append(html.escape(data))

    def handle_entityref(self, name):
        self.parts.append("&" + name + ";")

    def handle_charref(self, name):
        self.parts.append("&#" + name + ";")


class StaticReportRenderer:
    # What a Workspace keeps a view of these bytes by (workspace_reports.renderer_key): a change of
    # what this renderer makes changes the key.
    cache_key = "aelix_analytics.StaticReportRenderer/1"

    def render(self, files: Mapping[str, bytes], *, entry: str, paths: Mapping[str, str]) -> ReportView:
        files, paths = _inputs(files, entry, paths)
        originals = {name: _link(path) for name, path in paths.items()}
        kind = posixpath.splitext(entry)[1].lower()
        reasons = []
        supported = False
        if kind in (".md", ".markdown", ".txt"):
            try:
                text = files[entry].decode("utf-8")
            except UnicodeError:
                reasons.append("text_encoding_unsupported")
                body = ""
            else:
                supported = True
                if kind == ".txt":
                    body = "<pre>" + html.escape(text) + "</pre>"
                else:
                    from markdown_it import MarkdownIt

                    parser = _MappedHTML(entry, paths)
                    parser.feed(MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).render(text))
                    body = "".join(parser.parts)
                    reasons.extend(dict.fromkeys(parser.reasons))
                    supported = not reasons
        else:
            body = ""
            reasons.append("html_download_only" if kind in (".html", ".htm", ".svg") else "format_download_only")
        downloads = "<ul>" + "".join('<li><a download href="' + html.escape(link, quote=True) + '">'
                                      + html.escape(name) + "</a></li>" for name, link in originals.items()) + "</ul>"
        # No scripts, styles, frames, remote resources or authored HTML. CSP is a second boundary.
        page = ('<!doctype html><html><head><meta charset="utf-8">'
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'none\'; '
                'base-uri \'none\'; form-action \'none\'">'
                '<title>Report</title></head><body>' + body + downloads + "</body></html>").encode("utf-8")
        return ReportView({PREVIEW_PATH: page}, originals, supported, tuple(reasons))
