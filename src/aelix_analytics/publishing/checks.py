"""Shared package rules, byte inspections and independent leak checks (design11)."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import stat
import unicodedata
import zipfile
from dataclasses import fields, is_dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .inspections import inspect_bytes
from .model import LeakPolicy, PublicationError, ScanText, Text

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


def reject(message, **details):
    raise PublicationError("package_check_failed", message, details=details)


def normalized(value):
    return unicodedata.normalize("NFC", unquote(value)).replace("\\/", "/").replace("\\\\", "\\")


def check_names(paths):
    seen = set()
    for path in paths:
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or "\\" in path
            or "\x00" in path
        ):
            reject("invalid package path", path=path)
        if len(path) + 33 > MAX_PACKAGE_PATH:
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
    return max((len(p) + 33 for p in paths), default=0)


def resolve_link(path, link, files, ids=None):
    value = unquote(link.value).strip()
    if not value:
        return None
    if any(ord(c) < 32 for c in value) or "\\" in value:
        reject("invalid URL characters", path=path, location=link.location)
    try:
        parsed = urlsplit(value)
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


def check_strings(values, policy, *, path, source=None, strong_only=False):
    warnings = []
    for part in values:
        text = normalized(part.value)
        collapsed = " ".join(text.split())
        for token in policy.tokens:
            value = normalized(token.value)
            if (
                token.mode == "weak"
                and not part.name_slot
                or strong_only
                and token.mode != "strong"
            ):
                continue
            found = (
                " ".join(value.split()) in collapsed
                if token.mode in ("sentence", "warning")
                else bool(re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text))
                if token.mode == "weak"
                else value in text
            )
            if not found:
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
    for path, data in sorted(files.items()):
        role = roles[path]
        if role == "library":
            if (pinned or {}).get(path) != data:
                reject("library bytes do not match their pinned source", path=path)
            result = inspect_bytes(data, role=role, path=path)
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
            from .inspections import HTMLInspection

            parsed = HTMLInspection(data.decode("utf-8"))
            if any("base" in reason for reason in parsed.unsafe):
                reject("base element is not allowed", path=path)
            if re.search(
                r'<\s*(?:base\b|form\b)|<meta\b[^>]*http-equiv\s*=\s*["\']?refresh',
                data.decode("utf-8"),
                re.I,
            ):
                reject("base, forms and refresh are forbidden", path=path)
        if path == "assets/publication.js" and any(
            name in data.decode("utf-8") for name in NETWORK_APIS
        ):
            reject("publication runtime contains a network API", path=path)
        warnings.extend(check_strings(result.strings, policy, path=path))
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

    if root.is_symlink():
        reject("package root is a link")
    walk(root)
    for directory in directories:
        if not any(p.startswith(directory + "/") for p in out):
            reject("package contains an empty or unlisted directory", path=directory)
    return out, metadata


def verify_package(path, binding=None):
    """Verify a folder or deterministic ZIP against its own manifest; #21 reuses this function."""
    metadata = []
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
                    files = {}
                    for info in infos:
                        if (
                            not info.filename.startswith(prefix)
                            or info.is_dir()
                            or info.flag_bits & 1
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
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                reject(f"cannot inspect package ZIP: {exc}")
    check_names(files)
    try:
        raw = files["publication.json"]
        manifest = json.loads(raw.decode("utf-8"))
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
        listed = manifest["files"]
        if not isinstance(listed, list) or len({r["path"] for r in listed}) != len(listed):
            reject("duplicate manifest files")
        if set(files) != {
            "publication.json",
            *(r["path"] for r in listed),
        } or "publication.json" in {r["path"] for r in listed}:
            reject("file list differs from manifest")
        for item in listed:
            name = item["path"]
            if (
                len(files[name]) != item["bytes"]
                or hashlib.sha256(files[name]).hexdigest() != item["sha256"]
            ):
                reject("file bytes differ from manifest", path=name)
        if content_hash(files) != manifest["content_sha256"]:
            reject("content hash differs from manifest")
        digest = hashlib.sha256(raw).hexdigest()
        if binding is not None and (
            binding.get("package_sha256") != digest
            or binding.get("publication_id") != manifest["publication_id"]
        ):
            reject("manifest differs from its binding")
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
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
