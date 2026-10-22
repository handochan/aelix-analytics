"""Explicit byte loading and exclusive package writing; the renderer never opens files."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import zipfile
from collections import Counter
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .. import _platform, relations
from . import checks
from .checks import check_names
from .extract import parquet_strings
from .inspections import detect_format, inspect_bytes
from .model import FileNotice, Omission, PublicationError


def source_bytes(project, entry):
    if not relations._inside(entry.source):
        raise PublicationError("source_changed", "copy source is outside the project")
    try:
        path = (project.root / entry.source).resolve(strict=True)
        path.relative_to(project.root.resolve())
        if not path.is_file():
            raise OSError("not a regular file")
        data = path.read_bytes()
    except (ValueError, OSError) as exc:
        raise PublicationError(
            "source_changed", "copy source is missing or outside the project"
        ) from exc
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry.current_sha256 or entry.recorded_sha256 and digest != entry.recorded_sha256:
        raise PublicationError(
            "source_changed", "copy source changed after the snapshot was observed"
        )
    return data


def payload_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).translate(
        {
            ord("<"): "\\u003c",
            ord(">"): "\\u003e",
            ord("&"): "\\u0026",
            0x2028: "\\u2028",
            0x2029: "\\u2029",
        }
    )


def figure_points(value):
    def lengths(part):
        if isinstance(part, dict):
            if isinstance(part.get("bdata"), str) and isinstance(part.get("dtype"), str):
                units = {
                    "i1": 1,
                    "u1": 1,
                    "b1": 1,
                    "i2": 2,
                    "u2": 2,
                    "i4": 4,
                    "u4": 4,
                    "i8": 8,
                    "u8": 8,
                    "f4": 4,
                    "f8": 8,
                }
                if part["dtype"] not in units:
                    raise PublicationError("inspection_failed", "unknown Plotly typed-array dtype")
                try:
                    data = base64.b64decode(part["bdata"], validate=True)
                    if len(data) % units[part["dtype"]]:
                        raise ValueError("array byte count")
                    size = len(data) // units[part["dtype"]]
                    if "shape" in part:
                        dimensions = [int(v.strip()) for v in str(part["shape"]).split(",")]
                        if any(v < 0 for v in dimensions):
                            raise ValueError("array dimension")
                        size = dimensions[0]
                    return [size]
                except (ValueError, binascii.Error) as exc:
                    raise PublicationError(
                        "inspection_failed", "invalid Plotly typed array"
                    ) from exc
            return [n for value in part.values() for n in lengths(value)]
        return [len(part)] if isinstance(part, list) else []

    return sum(
        max(lengths(trace), default=0)
        for figure in (value, *value.get("frames", []))
        for trace in figure.get("data", [])
        if isinstance(trace, dict)
    )


def _user_links(inspection):
    for link in inspection.links:
        value = unquote(link.value).strip()
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise PublicationError("figure_not_shareable", "remote_resources") from exc
        if parsed.scheme.lower() == "javascript":
            raise PublicationError("figure_not_shareable", "scripts")
        if (
            parsed.scheme
            and parsed.scheme.lower() not in ("http", "https", "mailto", "data")
            or parsed.netloc
            and not parsed.scheme
            or value.startswith(("/", "\\"))
            or link.resource
            and (parsed.scheme.lower() not in ("", "data") or parsed.netloc)
        ):
            raise PublicationError("figure_not_shareable", "remote_resources")


def prepare_files(project, projection):
    """Read only approved sources, compare all digests, inspect, deduplicate and derive payloads."""
    files, roles, pinned, derived, rejected, slots = {}, {}, {}, {}, {}, {}
    points = {}
    counts = Counter()
    for entry in projection.copy_plan:
        try:
            data = source_bytes(project, entry)
            if detect_format(data, role=entry.role, path=entry.package_path) == "plotly":
                try:
                    from plotly.graph_objects import Figure

                    Figure(json.loads(data.decode("utf-8")), skip_invalid=False)
                except ImportError as exc:
                    raise PublicationError(
                        "renderer_unavailable", "plotly is required to validate this figure"
                    ) from exc
                except (ValueError, TypeError, RecursionError) as exc:
                    raise PublicationError(
                        "inspection_failed",
                        "figure JSON does not match the renderer's Plotly schema",
                    ) from exc
            inspection = inspect_bytes(
                data, role=entry.role, path=entry.package_path, parquet_extractor=parquet_strings
            )
            if inspection and inspection.format in ("html", "svg", "plotly"):
                _user_links(inspection)
        except PublicationError as exc:
            if exc.code != "figure_not_shareable" or entry.role != "figure":
                raise PublicationError(
                    exc.code,
                    exc.message,
                    details={
                        **exc.details,
                        "path": entry.package_path,
                        "sources": [
                            {
                                "category": entry.item.category,
                                "identity": entry.item.identity,
                                "role": entry.role,
                            }
                        ],
                    },
                ) from exc
            rejected[entry.item, entry.package_path] = FileNotice(entry.role, exc.message)
            counts["file", exc.message] += 1
            continue
        if inspection is None:
            rejected[entry.item, entry.package_path] = FileNotice(entry.role, "format")
            counts["file", "format"] += 1
            continue
        path = entry.package_path
        format_ = inspection.format
        if format_ == "plotly":
            value = json.loads(data.decode("utf-8"))
            text = payload_json(value)
            key = hashlib.sha256(text.encode()).hexdigest()
            path = f"assets/figures/{key}.js"
            data = f'window.AnalyticsFigures["{key}"] = {text};\n'.encode()
            format_ = "payload"
            points[path] = figure_points(value)
            derived.setdefault(path, []).append(
                {
                    "item": entry.item,
                    "role": entry.role,
                    "source": entry.source,
                    "recorded_sha256": entry.recorded_sha256,
                    "source_sha256": entry.current_sha256,
                }
            )
        if path in files and files[path] != data:
            raise PublicationError("package_check_failed", "content-address name collision")
        files[path] = data
        roles[path] = "generated" if format_ == "payload" else entry.role
        slots[entry.item, entry.package_path] = (path, format_)
    items = []
    for item in projection.model.items:
        notices = list(item.file_notices)
        kept = []
        for slot in item.files:
            reject = rejected.get((item.key, slot.package_path))
            if reject:
                notices.append(reject)
            else:
                path, format_ = slots[item.key, slot.package_path]
                kept.append(replace(slot, package_path=path, format=format_))
        items.append(replace(item, files=tuple(kept), file_notices=tuple(notices)))
    counts.update({(o.category, o.reason): o.count for o in projection.model.not_included})
    omitted = tuple(
        Omission(category, reason, count) for (category, reason), count in sorted(counts.items())
    )
    prepared = replace(
        projection,
        model=replace(projection.model, items=tuple(items), not_included=omitted),
        ledger=replace(projection.ledger, not_included=omitted),
    )
    assets = Path(__file__).with_name("assets")
    for name in ("publication.css", "publication.js"):
        data = (assets / name).read_bytes()
        path = "assets/" + name
        files[path] = data
        roles[path] = "library"
        pinned[path] = data
    notice = "Aelix Analytics publication resources — Apache-2.0.\n"
    renderer = {"libraries": []}
    try:
        renderer["libraries"].append(
            {"name": "markdown-it-py", "version": metadata.version("markdown-it-py")}
        )
    except metadata.PackageNotFoundError:
        # render will refuse a context needing the renderer, rather than inserting blank text.
        pass
    if points:
        try:
            import plotly

            bundle = Path(plotly.__file__).parent / "package_data/plotly.min.js"
            data = bundle.read_bytes()
            dist = metadata.distribution("plotly")
            licenses = [
                p for p in dist.files or () if str(p).endswith(".dist-info/licenses/LICENSE.txt")
            ]
            if not licenses:
                raise OSError("plotly license is unavailable")
            license_text = Path(dist.locate_file(licenses[0])).read_text(encoding="utf-8")
            notice += (
                "\nplotly.py "
                + plotly.__version__
                + " / bundled plotly.js\n"
                + data[: data.find(b"*/") + 2].decode("utf-8")
                + "\n"
                + license_text
                + "\n"
            )
            path = "assets/plotly.min.js"
            files[path] = data
            roles[path] = "library"
            pinned[path] = data
            js_version = re.search(rb"plotly\.js v([0-9A-Za-z.+-]+)", data[:512])
            renderer["libraries"].append(
                {
                    "name": "plotly.js",
                    "version": js_version[1].decode("ascii") if js_version else "unknown",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "license": "MIT",
                }
            )
        except (ImportError, OSError, UnicodeError) as exc:
            raise PublicationError(
                "renderer_unavailable",
                "plotly.js and its license are required for included interactive figures",
            ) from exc
    files["assets/NOTICE.txt"] = notice.encode()
    roles["assets/NOTICE.txt"] = "generated"
    check_names(files)
    return (
        prepared,
        files,
        roles,
        pinned,
        {"derived": derived, "points": points, "renderer": renderer},
    )


def write_package(directory, files):
    """An explicitly called exclusive writer. Preview never calls it or creates a directory."""
    check_names(files)
    root = Path(directory)
    if root.exists():
        raise PublicationError("package_check_failed", "package destination already exists")
    root.mkdir()
    write_files(root, files)
    return root


def write_files(directory, files):
    """Write checked files exclusively into a folder the caller has already created and owns."""
    check_names(files)
    root = Path(directory)
    for name, data in sorted(files.items()):
        path = root / name
        current = root
        candidates = [root]
        for part in path.relative_to(root).parts:
            current = current / part
            candidates.append(current)
        for current in candidates:
            try:
                if _platform.is_link(current.lstat()):
                    raise PublicationError(
                        "package_check_failed", "package writes cannot follow a link"
                    )
            except FileNotFoundError:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


class _UTF8ZipInfo(zipfile.ZipInfo):
    def _encodeFilenameFlags(self):
        return self.filename.encode("utf-8"), self.flag_bits | 0x800


def write_zip(directory, destination, *, project_root, binding):
    """A deterministic transport of an already bound folder, never an overwrite or project output."""
    source = Path(directory)
    target = Path(destination).expanduser().absolute()
    root = Path(project_root).absolute()
    # Compare directory identities too: resolve() retains alternate case spellings on
    # case-insensitive volumes. The migration overlap check uses the same inode rule.
    from ..core import same_file

    if (
        target.is_relative_to(root)
        or target.resolve().is_relative_to(root.resolve())
        or any(same_file(parent, root) for parent in target.parents)
        or target.exists()
        or target.is_symlink()
    ):
        raise PublicationError(
            "zip_path_invalid", "ZIP target must be outside the project and must not exist"
        )
    verified = checks.verify_package(source, binding=binding)
    manifest = verified["manifest"]
    names = sorted(["publication.json", *(row["path"] for row in manifest["files"])])
    owned = None
    try:
        with target.open("xb") as stream:
            stat = os.fstat(stream.fileno())
            owned = stat.st_dev, stat.st_ino
            with zipfile.ZipFile(
                stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                for name in names:
                    info = _UTF8ZipInfo(
                        manifest["publication_id"] + "/" + name, (1980, 1, 1, 0, 0, 0)
                    )
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, (source / name).read_bytes(), compresslevel=9)
            stream.flush()
            os.fsync(stream.fileno())
        checks.verify_package(target, binding=binding)
    except BaseException as exc:
        if owned is not None:
            try:
                current = target.lstat()
                if _platform.is_link(current) or (current.st_dev, current.st_ino) != owned:
                    raise OSError("ZIP target identity changed")
                target.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup:
                if isinstance(exc, PublicationError):
                    exc.details.update(cleanup_path=str(target), cleanup_error=str(cleanup))
                else:
                    raise PublicationError(
                        "package_check_failed",
                        "failed ZIP could not be cleaned up",
                        details={
                            "cleanup_path": str(target),
                            "cleanup_error": str(cleanup),
                            "cause": type(exc).__name__,
                        },
                    ) from exc
        if isinstance(exc, FileExistsError):
            raise PublicationError("zip_path_invalid", "ZIP target already exists") from exc
        raise
    return str(target)
