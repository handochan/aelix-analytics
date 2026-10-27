"""Shared package rules, byte inspections and independent leak checks (design11)."""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import posixpath
import re
import stat
import unicodedata
import zipfile
import zlib
from dataclasses import fields, is_dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .. import relations
from .basis import _time
from .inspections import HTMLInspection, detect_format, inspect_bytes
from .model import ItemKey, LeakPolicy, PublicationError, ScanText, Text, page_key, private_field

IDENTITY_FILES = frozenset({"publication.json", "about.html", "assets/identity.js"})
OS_METADATA = frozenset({".DS_Store", "desktop.ini", "Thumbs.db"})
MAX_PACKAGE_PATH = 240
DEVICE = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)
PATH_PATTERN = re.compile(
    r'file://|(?:^|[\s"\'])~[/\\]|(?:^|[\s"\'])(?:[a-zA-Z]:[/\\]|\\\\|/Users/|/home/)'
)
NETWORK_APIS = (
    "fetch",
    "XMLHttpRequest",
    "WebSocket",
    "EventSource",
    "navigator.sendBeacon",
    "import(",
)


# What a ReportRenderer made in a Report's folder: not its page, not its original files (#20 PR4).
REPORT_VIEW = re.compile(r"reports/[0-9a-f]{16}/(?!content/|index\.html$).+")


def reject(message, **details):
    raise PublicationError("package_check_failed", message, details=details)


def normalized(value):
    return unicodedata.normalize("NFC", unquote(value)).replace("\\/", "/").replace("\\\\", "\\")


def check_names(paths):
    paths = tuple(paths)
    seen = set()
    nodes = {}
    for path in paths:
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or "\\" in path
            or "\x00" in path
        ):
            reject("invalid package path", path=path)
        try:
            length = len(path.encode("utf-16-le")) // 2 + 31
            overlong_part = any(len(part.encode("utf-8")) > 255 for part in path.split("/"))
        except UnicodeError:
            reject("package path is not a Unicode scalar string", path=path)
        if length > MAX_PACKAGE_PATH or overlong_part:
            reject(
                "package path exceeds the portable relative path bound",
                path=path,
                limit=MAX_PACKAGE_PATH,
            )
        parts = path.split("/")
        if any(
            p in ("", ".", "..")
            or p[-1:] in (".", " ")
            or DEVICE.match(p)
            or any(ord(c) < 32 or c in '<>:"|?*' for c in p)
            or unicodedata.normalize("NFC", p) != p
            for p in parts
        ):
            reject("nonportable package path", path=path)
        folded = path.casefold()
        if folded in seen:
            reject("casefold package path collision", path=path)
        seen.add(folded)
        for n in range(1, len(parts) + 1):
            prefix = "/".join(parts[:n])
            node = (prefix, n == len(parts))
            if prefix.casefold() in nodes and nodes[prefix.casefold()] != node:
                reject("file/directory or casefold directory collision", path=path)
            nodes[prefix.casefold()] = node
    return max((len(p.encode("utf-16-le")) // 2 + 31 for p in paths), default=0)


def resolve_link(path, link, files, ids=None):
    value = unquote(link.value).strip()
    if not value:
        return None
    if any(ord(c) < 32 for c in value) or "\\" in value:
        reject("invalid URL characters", path=path, location=link.location)
    try:
        # Split before decoding: an escaped "#" or "?" in a file name (a Report's own path) is part
        # of the path, not a fragment or query.
        raw = urlsplit(link.value.strip())
        parsed = raw._replace(path=unquote(raw.path), fragment=unquote(raw.fragment))
    except ValueError:
        reject("malformed URL", path=path, location=link.location)
    scheme = parsed.scheme.lower()
    if scheme == "data" and link.resource:
        return None  # inspect_bytes recursively inspects these bytes.
    if scheme:
        if not link.resource and scheme in ("http", "https", "mailto"):
            return value
        reject("forbidden URL scheme or external resource", path=path, location=link.location)
    if parsed.netloc or value.startswith("/") or re.match(r"^[a-zA-Z]:", value):
        reject("absolute or network package reference", path=path, location=link.location)
    target = (
        posixpath.normpath(posixpath.join(posixpath.dirname(path), parsed.path))
        if parsed.path
        else path
    )
    if (
        target == ".."
        or target.startswith("../")
        or target not in files
        or parsed.path.endswith("/")
    ):
        reject(
            "package reference does not name a file in this copy",
            path=path,
            location=link.location,
            target=target,
        )
    if (
        parsed.fragment
        and ids is not None
        and target in ids
        and unquote(parsed.fragment) not in ids[target]
    ):
        reject("package reference fragment is absent", path=path, location=link.location)
    return None


def check_model(model, policy: LeakPolicy):
    """Check authored text before render so a refusal names its exact source field."""
    warnings = []

    def visit(value):
        if isinstance(value, Text):
            loc = (
                f"{value.source[0].category}:{value.source[0].identity}.{value.source[1]}"
                if value.source
                else "kit-label"
            )
            warnings.extend(
                check_strings(
                    (ScanText(value.value, loc),), policy, path="model", source=value.source
                )
            )
        elif is_dataclass(value):
            for f in fields(value):
                visit(getattr(value, f.name))
        elif isinstance(value, tuple):
            for part in value:
                visit(part)

    visit(model)
    return tuple(warnings)


class _Literals:
    """Every value of a set found in a text, in time linear in the text (12.7: thousands of tokens)."""

    WIDTH = 8

    def __init__(self, entries):
        self.prefixes, self.short = {}, {}
        for value, n in entries:
            group = self.prefixes.setdefault(value[: self.WIDTH], {}) if len(value) >= self.WIDTH else self.short
            group.setdefault(value, []).append(n)

    def find(self, text):
        found = [n for value, ns in self.short.items() if value in text for n in ns]
        prefixes, width = self.prefixes, self.WIDTH
        for i in range(len(text) - width + 1):
            group = prefixes.get(text[i : i + width])
            if group:
                found.extend(n for value, ns in group.items() if text.startswith(value, i) for n in ns)
        return found


class _Words:
    """Values with a non-word character or an end on both sides, as `(?<!\\w)value(?!\\w)` finds them."""

    def __init__(self, entries):
        self.values = {}
        for value, n in entries:
            self.values.setdefault(value, []).append(n)
        self.longest = max(map(len, self.values), default=0)

    def find(self, text):
        if not self.values:
            return []
        starts = [m.start() for m in re.finditer(r"(?<!\w)", text)]
        ends = [m.start() for m in re.finditer(r"(?!\w)", text)]
        found = []
        for start in starts:
            window = ends[bisect.bisect_right(ends, start) : bisect.bisect_right(ends, start + self.longest)]
            for end in window:
                found.extend(self.values.get(text[start:end], ()))
        return found


def _inside(text, value, covers, *, word):
    """Whether every occurrence of `value` lies inside an occurrence of one of `covers`."""
    spans = []
    for cover in covers:
        start = text.find(cover)
        while start != -1:
            spans.append((start, start + len(cover)))
            start = text.find(cover, start + 1)
    pattern = r"(?<!\w)" + re.escape(value) + r"(?!\w)" if word else re.escape(value)
    return all(
        any(a <= m.start() and m.start() + len(value) <= b for a, b in spans)
        for m in re.finditer("(?=" + pattern + ")", text)
    )


_MATCHERS = None


def _matchers(policy):
    """Normalise and index a policy's tokens once; check_model and check_package reuse one policy."""
    global _MATCHERS
    cached = _MATCHERS
    if cached is None or cached[0] is not policy:
        groups = {"lines": [], "strong": [], "weak": []}
        covers, inside = dict(policy.covers), {}
        for n, token in enumerate(policy.tokens):
            value = normalized(token.value)
            if token.mode in ("sentence", "warning"):
                groups["lines"].append((" ".join(value.split()), n))
            else:
                groups[token.mode].append((value, n))
                if token.value in covers and token.source is not None and not private_field(token.field):
                    inside[n] = (value, tuple(normalized(c) for c in covers[token.value]))
        cached = (
            policy,
            _Literals(groups["lines"]),
            _Literals(groups["strong"]),
            _Words(groups["weak"]),
            inside,
        )
        _MATCHERS = cached
    return cached[1:]


def check_strings(values, policy, *, path, source=None, strong_only=False, own_names=frozenset()):
    """`own_names`: figure names a run's own code may hold (LeakPolicy.code_names); only tokens
    from a figure's `visualization_id` are skipped."""
    warnings = []
    lines, strong, weak, inside = _matchers(policy)
    for part in values:
        text = normalized(part.value)
        found = strong.find(text)
        if not strong_only:
            found += lines.find(" ".join(text.split()))
            if part.name_slot:
                found += weak.find(text)
        # Policy order and one finding per token, as one pass over every token gave them.
        for n in sorted(set(found)):
            token = policy.tokens[n]
            # Only a figure's name (2026-10-27), never another field that holds the same value.
            if token.field == "visualization_id" and token.source.category == "visualization" and token.value in own_names:
                continue
            if n in inside and _inside(text, *inside[n], word=token.mode == "weak"):
                continue
            detail = {
                "path": path,
                "location": part.location,
                "cause": "source"
                if source
                else "renderer"
                if path.endswith((".html", ".css", ".js"))
                else "copied_file",
            }
            if source:
                detail["source"] = {
                    "category": source[0].category,
                    "identity": source[0].identity,
                    "field": source[1],
                }
            if token.mode == "warning":
                warnings.append({**detail, "reason": "excluded_field_line"})
            else:
                raise PublicationError(
                    "leak_found",
                    "excluded content or a local environment value is present",
                    details=detail,
                )
        if not strong_only and PATH_PATTERN.search(text):
            warnings.append({"path": path, "location": part.location, "reason": "path_pattern"})
    return tuple(warnings)


def check_package(files, roles, *, policy=None, parquet_extractor=None, pinned=None):
    """One memory view for preview/build/verify; never reads the project or renderer inputs."""
    policy = policy if policy is not None else LeakPolicy()
    check_names(files)
    if set(roles) != set(files) or any(not isinstance(data, bytes) for data in files.values()):
        reject("every package byte file needs exactly one role")
    inspected, warnings, remote = {}, [], set()
    code_names = dict(policy.code_names)
    for path, data in sorted(files.items()):
        role = roles[path]
        if role == "library":
            if (pinned or {}).get(path) != data:
                reject("library bytes do not match their pinned source", path=path)
            result = inspect_bytes(data, role=role, path=path)
            if result is None:
                raise PublicationError(
                    "inspection_failed",
                    "pinned library is not an inspectable text resource",
                    details={"path": path},
                )
            if path == "assets/publication.js" and any(
                name in data.decode("utf-8") for name in NETWORK_APIS
            ):
                reject("publication runtime contains a network API", path=path)
            warnings.extend(check_strings(result.strings, policy, path=path, strong_only=True))
            inspected[path] = {
                "format": result.format,
                "inspection": "pinned_scanned",
                "metadata_keys": [],
            }
            continue
        try:
            result = inspect_bytes(data, role=role, path=path, parquet_extractor=parquet_extractor)
        except PublicationError as exc:
            if exc.code == "figure_not_shareable":
                reject("an unsafe user figure reached the package", path=path, reason=exc.message)
            raise PublicationError(
                exc.code, exc.message, details={**exc.details, "path": path}
            ) from exc
        if result is None:
            raise PublicationError(
                "inspection_failed",
                "an uninspectable file reached the package",
                details={"path": path},
            )
        if path.endswith(".html"):
            # Generated pages may use scripts but not executable navigation or hidden fetch paths.
            parsed = HTMLInspection(data.decode("utf-8"))
            if parsed.forbidden or any(
                name in script for script in parsed.script_text for name in NETWORK_APIS
            ):
                reject(
                    "base, forms, refresh, unsafe frames and executable handlers are forbidden",
                    path=path,
                )
        if path == "assets/publication.js" and any(
            name in data.decode("utf-8") for name in NETWORK_APIS
        ):
            reject("publication runtime contains a network API", path=path)
        own = code_names.get(hashlib.sha256(data).hexdigest()) if role == "code_snapshot" else None
        warnings.extend(check_strings(result.strings, policy, path=path, own_names=own or frozenset()))
        for key in result.metadata_keys:
            warnings.append({"path": path, "reason": "image_metadata", "key": key})
        inspected[path] = {
            "format": result.format,
            "inspection": "inspected",
            "metadata_keys": list(result.metadata_keys),
            "_result": result,
        }
    ids = {
        p: set(v["_result"].ids)
        for p, v in inspected.items()
        if "_result" in v and v["format"] in ("html", "svg")
    }
    for path, item in inspected.items():
        result = item.pop("_result", None)
        for link in result.links if result else ():
            value = resolve_link(path, link, files, ids)
            if value:
                remote.add(value)
    return {
        "files": inspected,
        "remote_links": sorted(remote),
        "warnings": warnings,
        "inspection": {
            k: sum(v["inspection"] == k for v in inspected.values())
            for k in ("inspected", "pinned_scanned", "not_inspectable")
        },
        "longest_relative_path": check_names(files),
    }


def content_hash(files):
    rows = [
        {"path": p, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for p, data in sorted(files.items())
        if p not in IDENTITY_FILES
    ]
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _folder_files(root):
    out = {}
    directories = []
    metadata = []

    def walk(directory):
        for entry in sorted(os.scandir(directory), key=lambda e: e.name):
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or getattr(path.lstat(), "st_reparse_tag", 0):
                reject("package contains a link or junction", path=relative)
            if stat.S_ISDIR(mode):
                directories.append(relative)
                walk(path)
            elif stat.S_ISREG(mode):
                if entry.name in OS_METADATA:
                    metadata.append(relative)
                else:
                    out[relative] = path.read_bytes()
            else:
                reject("package contains a nonregular entry", path=relative)

    if root.is_symlink() or getattr(root.lstat(), "st_reparse_tag", 0):
        reject("package root is a link")
    walk(root)
    for directory in directories:
        if not any(p.startswith(directory + "/") for p in out):
            reject("package contains an empty or unlisted directory", path=directory)
    return out, metadata


def _manifest_shape(value):
    """Validate the accepted manifest fields, without creating a stored publication (PR3)."""
    def exact(part, keys):
        return isinstance(part, dict) and set(part) == set(keys)

    def need(condition, message):
        if not condition:
            reject("invalid publication manifest: " + message)

    def nonnegative(part):
        return type(part) is int and part >= 0

    def digest(part):
        return relations._reference_digest({"sha256": part}) is not None

    def item_key(part):
        return (
            exact(part, ("category", "identity"))
            and part["category"]
            in {
                "project",
                "event",
                "run",
                "visualization",
                "context",
                "code",
                "dataset",
                "data_observation",
                "report",
            }
            and isinstance(part["identity"], str)
            and bool(part["identity"])
        )

    need(
        isinstance(value["publication_id"], str)
        and relations.PUBLICATION_ID.fullmatch(value["publication_id"]),
        "identity",
    )
    need(_time(value["built_at"]) is not None, "built_at")
    basis = value["basis"]
    need(
        exact(basis, ("as_of_seq", "content_seq", "content_recorded_at", "context_version")),
        "basis fields",
    )
    need(
        nonnegative(basis["as_of_seq"])
        and nonnegative(basis["content_seq"])
        and basis["content_seq"] <= basis["as_of_seq"],
        "basis sequence",
    )
    need(
        basis["content_recorded_at"] is None or _time(basis["content_recorded_at"]) is not None,
        "content time",
    )
    need(
        basis["context_version"] is None
        or isinstance(basis["context_version"], str)
        and bool(basis["context_version"]),
        "context version",
    )
    need(
        exact(value["analysis_state"], ("value", "basis"))
        and value["analysis_state"]["value"] in ("in_progress", "complete")
        and value["analysis_state"]["basis"] == "declared",
        "declared analysis state",
    )
    profile = value["profile"]
    need(
        exact(profile, ("id", "rules_version"))
        and isinstance(profile["id"], str)
        and bool(profile["id"])
        and type(profile["rules_version"]) is int
        and profile["rules_version"] > 0,
        "profile",
    )
    need(value["ui_locale"] == "en", "locale")
    scope = value["scope"]
    need(
        exact(scope, ("kind", "reports"))
        and scope["kind"] in ("project", "reports")
        and isinstance(scope["reports"], list),
        "scope",
    )
    need(scope["kind"] == "project" or bool(scope["reports"]), "a Report copy names its selected versions")
    need(
        isinstance(value["included"], list) and all(item_key(k) for k in value["included"]),
        "included keys",
    )
    included = {(k["category"], k["identity"]) for k in value["included"]}
    need(len(included) == len(value["included"]), "duplicate included keys")
    need(
        isinstance(value["not_included"], list)
        and all(
            exact(o, ("category", "reason", "count"))
            and isinstance(o["category"], str)
            and isinstance(o["reason"], str)
            and type(o["count"]) is int
            and o["count"] > 0
            for o in value["not_included"]
        ),
        "omission counts",
    )
    need(
        isinstance(value["reader_features"], list)
        and len(set(value["reader_features"])) == len(value["reader_features"])
        and all(
            v in ("read", "relations", "downloads", "interactive_figures")
            for v in value["reader_features"]
        ),
        "reader features",
    )
    renderer = value["renderer"]
    need(
        exact(renderer, ("kit_version", "format", "libraries"))
        and relations.version_key(renderer["kit_version"]) is not None
        and type(renderer["format"]) is int
        and renderer["format"] == 1
        and isinstance(renderer["libraries"], list),
        "renderer",
    )
    for lib in renderer["libraries"]:
        need(
            isinstance(lib, dict)
            and {"name", "version"} <= set(lib) <= {"name", "version", "sha256", "license"}
            and all(isinstance(lib[k], str) and lib[k] for k in ("name", "version")),
            "renderer library",
        )
        need(
            "sha256" not in lib or lib["sha256"] is None or digest(lib["sha256"]), "library digest"
        )
        need("license" not in lib or isinstance(lib["license"], str), "library license")
    need(
        isinstance(value["remote_links"], list)
        and all(
            isinstance(v, str) and urlsplit(v).scheme in ("http", "https", "mailto")
            for v in value["remote_links"]
        ),
        "remote navigation links",
    )
    need(isinstance(value["limits"], dict), "limits")
    need(digest(value["content_sha256"]), "content digest")
    summary = value["checks"]
    need(
        exact(summary, ("format", "result", "inspection"))
        and type(summary["format"]) is int
        and summary["format"] == 1
        and summary["result"] == "pass",
        "checks",
    )
    need(
        exact(summary["inspection"], ("inspected", "pinned_scanned", "not_inspectable"))
        and all(nonnegative(v) for v in summary["inspection"].values())
        and summary["inspection"]["not_inspectable"] == 0,
        "inspection counts",
    )
    need(isinstance(value["files"], list), "file inventory")
    counts = {
        "inspected": 1,
        "pinned_scanned": 0,
        "not_inspectable": 0,
    }  # manifest itself is inspected separately.
    for row in value["files"]:
        need(
            isinstance(row, dict)
            and {"path", "bytes", "sha256", "origin", "format", "inspection"}
            <= set(row)
            <= {
                "path",
                "bytes",
                "sha256",
                "origin",
                "format",
                "inspection",
                "sources",
                "derived_from",
            },
            "file fields",
        )
        need(
            isinstance(row["path"], str) and nonnegative(row["bytes"]) and digest(row["sha256"]),
            "file identity",
        )
        need(
            row["origin"] in ("generated", "bundled", "copied", "identity")
            and row["inspection"] in ("inspected", "pinned_scanned"),
            "file origin/inspection",
        )
        need(
            row["format"]
            in (
                "html",
                "css",
                "javascript",
                "payload",
                "png",
                "jpeg",
                "gif",
                "webp",
                "svg",
                "plotly",
                "parquet",
                "python",
                "text",
            ),
            "file format",
        )
        need(
            (row["origin"] == "bundled") == (row["inspection"] == "pinned_scanned"),
            "bundled inspection",
        )
        counts[row["inspection"]] += 1
        if row["origin"] == "copied":
            need(isinstance(row.get("sources"), list) and bool(row["sources"]), "copied provenance")
        for name in ("sources", "derived_from"):
            if name not in row:
                continue
            need(isinstance(row[name], list) and bool(row[name]), "source list")
            for source in row[name]:
                need(
                    isinstance(source, dict)
                    and "item" in source
                    and item_key(source["item"])
                    and (source["item"]["category"], source["item"]["identity"]) in included,
                    "source item",
                )
                if name == "sources":
                    need(
                        {"item", "role", "verified"}
                        <= set(source)
                        <= {
                            "item",
                            "role",
                            "verified",
                            "sha256",
                            "recorded_sha256",
                            "current_sha256",
                        }
                        and source["role"]
                        in ("figure", "code_snapshot", "attached_data", "report_file"),
                        "source role",
                    )
                    need(
                        source["verified"]
                        in ("recorded_hash", "no_recorded_hash", "kit_generated"),
                        "source verification",
                    )
                    recorded = source.get("recorded_sha256")
                    if source["verified"] == "recorded_hash":
                        need(digest(recorded), "recorded source digest")
                    elif source["verified"] == "no_recorded_hash":
                        need(recorded is None, "unrecorded source digest")
                need(
                    all(
                        v is None or digest(v)
                        for k, v in source.items()
                        if k in ("sha256", "recorded_sha256", "current_sha256", "source_sha256")
                    ),
                    "source digest",
                )
    need(counts == summary["inspection"], "inspection inventory differs from summary")
    # ADR-0014 decision 4: each selected exact version and where its every file is in this copy.
    rows = {row["path"]: row for row in value["files"]}
    selected = set()
    for report in scope["reports"]:
        need(
            exact(report, ("report_id", "revision_id", "version", "state", "manifest_sha256", "files"))
            and relations.report_name(report["report_id"])
            and isinstance(report["revision_id"], str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", report["revision_id"]) is not None
            and type(report["version"]) is int
            and report["version"] > 0
            and report["state"] in ("current", "withdrawn")
            and digest(report["manifest_sha256"])
            and isinstance(report["files"], list)
            and bool(report["files"]),
            "selected Report",
        )
        pair = (report["report_id"], report["revision_id"])
        need(pair not in selected, "duplicate selected Report")
        selected.add(pair)
        token = page_key(ItemKey("report", "/".join(pair)))
        need(("report", token) in included, "selected Report is not an included item")
        for file in report["files"]:
            need(
                exact(file, ("path", "sha256", "package_path"))
                and relations.report_path(file["path"])
                and digest(file["sha256"])
                and file["package_path"] == f"reports/{token}/content/{file['path']}",
                "selected Report file",
            )
            row = rows.get(file["package_path"])
            need(
                row is not None
                and row["sha256"] == file["sha256"]
                and row["origin"] == "copied"
                and any(source["role"] == "report_file" for source in row["sources"]),
                "selected Report file is not its recorded copy",
            )


def _verification_view(data, role, name):
    observed = detect_format(data, role=role, path=name)
    view = None
    if observed in ("html", "css", "svg", "payload"):
        try:
            view = inspect_bytes(data, role=role, path=name)
        except PublicationError as exc:
            if exc.code != "figure_not_shareable":
                raise
            # As check_package: unsafe user content in a saved copy is a package defect.
            reject("an unsafe user document is in the package", path=name, reason=exc.message)
        if view is None:
            reject("file role does not allow its format", path=name)
        if observed == "html":
            parsed = HTMLInspection(data.decode("utf-8"))
            if parsed.forbidden or any(
                word in script for script in parsed.script_text for word in NETWORK_APIS
            ):
                reject("forbidden executable HTML behavior", path=name)
    if name == "assets/publication.js" and any(word in data.decode("utf-8") for word in NETWORK_APIS):
        reject("publication runtime contains a network API", path=name)
    return observed, view


def verify_package(path, binding=None, *, inspection_cache=None):
    """Verify a folder or deterministic ZIP against its own manifest; #21 reuses this function."""
    metadata = []
    zip_root = None
    if isinstance(path, dict):
        files = dict(path)
    else:
        root = Path(path)
        if root.is_dir():
            files, metadata = _folder_files(root)
        else:
            try:
                with zipfile.ZipFile(root) as z:
                    infos = z.infolist()
                    names = [i.filename for i in infos]
                    if len(names) != len(set(names)):
                        reject("duplicate ZIP entry")
                    manifests = [n for n in names if n.endswith("/publication.json")]
                    if len(manifests) != 1:
                        reject("ZIP needs one publication root")
                    prefix = manifests[0][: -len("publication.json")]
                    if not re.fullmatch(r"pub-[0-9a-hjkmnp-tv-z]{26}/", prefix):
                        reject("invalid ZIP publication root")
                    zip_root = prefix[:-1]
                    files = {}
                    for info in infos:
                        if (
                            not info.filename.startswith(prefix)
                            or info.is_dir()
                            or info.flag_bits & 1
                            or not info.flag_bits & 0x800
                            or info.compress_type != zipfile.ZIP_DEFLATED
                            or info.date_time != (1980, 1, 1, 0, 0, 0)
                            or info.extra
                            or info.comment
                            or info.create_system != 3
                            or info.external_attr >> 16 != 0o100644
                        ):
                            reject("ZIP entry metadata is not deterministic or safe")
                        name = info.filename[len(prefix) :]
                        check_names([name])
                        files[name] = z.read(info)
                    if z.comment:
                        reject("ZIP has a comment")
            except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error, UnicodeError) as exc:
                reject(f"cannot inspect package ZIP: {exc}")
    check_names(files)
    try:
        raw = files["publication.json"]

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    reject("duplicate publication manifest key")
                result[key] = value
            return result

        manifest = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda value: reject("nonfinite publication JSON"),
        )
        required = {
            "schema",
            "publication_id",
            "built_at",
            "entry",
            "basis",
            "analysis_state",
            "ui_locale",
            "profile",
            "scope",
            "included",
            "not_included",
            "reader_features",
            "renderer",
            "remote_links",
            "limits",
            "checks",
            "content_sha256",
            "files",
        }
        if (
            not isinstance(manifest, dict)
            or set(manifest) != required
            or manifest["schema"] != "analytics.publication/1"
            or manifest["entry"] != "index.html"
        ):
            reject("unknown or incomplete publication manifest")
        if not re.fullmatch(r"pub-[0-9a-hjkmnp-tv-z]{26}", manifest["publication_id"]):
            reject("invalid publication identity")
        _manifest_shape(manifest)
        if zip_root is not None and zip_root != manifest["publication_id"]:
            reject("ZIP root differs from its manifest identity")
        listed = manifest["files"]
        if not isinstance(listed, list) or len({r["path"] for r in listed}) != len(listed):
            reject("duplicate manifest files")
        expected = {"publication.json", *(r["path"] for r in listed)}
        if set(files) != expected or "publication.json" in {r["path"] for r in listed}:
            # Which files (#21: a publish attempt names what it found): the first 20 of each.
            reject(
                "file list differs from manifest",
                unlisted=sorted(set(files) - expected)[:20],
                missing=sorted(expected - set(files))[:20],
            )
        for item in listed:
            name = item["path"]
            if (
                len(files[name]) != item["bytes"]
                or hashlib.sha256(files[name]).hexdigest() != item["sha256"]
            ):
                reject("file bytes differ from manifest", path=name)
        if content_hash(files) != manifest["content_sha256"]:
            reject("content hash differs from manifest")
        # These rules do not require the sender's private records or a Parquet environment.
        # Original leak/metadata inspections stay pinned by the manifest/binding byte hashes.
        views = {}
        for row in listed:
            name = row["path"]
            origin = row["origin"]
            role = (
                "library"
                if origin == "bundled"
                else "identity"
                if origin == "identity"
                else "generated"
            )
            if origin == "copied":
                role = row["sources"][0]["role"]
            elif origin == "generated" and REPORT_VIEW.fullmatch(name):
                role = "report_view"  # a ReportRenderer's document, checked as authored content
            # Only memoize pure inspections within this observation batch. Every invocation
            # still walks the folder, reads all bytes and checks their hashes above, even when
            # size/mtime/ctime are unchanged. Never trust a stat key or a persisted cache.
            cache_key = (role, name, row["sha256"])
            if inspection_cache is not None and cache_key in inspection_cache:
                observed, inspection = inspection_cache[cache_key]
            else:
                observed, inspection = _verification_view(files[name], role, name)
                if inspection_cache is not None:
                    if len(inspection_cache) >= 64:
                        inspection_cache.pop(next(iter(inspection_cache)))
                    inspection_cache[cache_key] = observed, inspection
            declared = "text" if row["format"] == "python" else row["format"]
            if observed != declared:
                reject("file format differs from the manifest", path=name)
            if inspection is not None:
                views[name] = inspection
        ids = {
            name: set(value.ids) for name, value in views.items() if value.format in ("html", "svg")
        }
        remote = set()
        for name, view in views.items():
            for link in view.links:
                target = resolve_link(name, link, files, ids)
                if target:
                    remote.add(target)
        if sorted(remote) != sorted(manifest["remote_links"]):
            reject("remote navigation list differs from manifest")
        digest = hashlib.sha256(raw).hexdigest()
        if binding is not None:
            expected = {
                "publication_id": manifest["publication_id"],
                "format": manifest["schema"],
                "package_sha256": digest,
                "content_sha256": manifest["content_sha256"],
                "profile": manifest["profile"],
                "scope": manifest["scope"]["kind"],
                "basis": {k: v for k, v in manifest["basis"].items() if k != "content_recorded_at"},
                "analysis_state": manifest["analysis_state"],
                "checks": manifest["checks"],
            }
            if not isinstance(binding, dict) or any(
                binding.get(k) != v for k, v in expected.items()
            ):
                reject("manifest differs from its binding")
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, PublicationError):
            raise
        reject(f"invalid publication manifest: {type(exc).__name__}")
    return {
        "ok": True,
        "manifest": manifest,
        "package_sha256": digest,
        "content_sha256": manifest["content_sha256"],
        "files": len(files),
        "metadata": metadata,
    }
