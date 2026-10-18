"""Explicit byte loading and exclusive package writing; the renderer never opens files."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .. import relations
from .checks import check_names
from .extract import parquet_strings
from .inspections import inspect_bytes
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
            return [n for value in part.values() for n in lengths(value)]
        return [len(part)] if isinstance(part, list) else []

    return sum(
        max(lengths(trace), default=0) for trace in value.get("data", []) if isinstance(trace, dict)
    )


def _user_links(inspection):
    for link in inspection.links:
        value = unquote(link.value).strip()
        parsed = urlsplit(value)
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
        data = source_bytes(project, entry)
        try:
            inspection = inspect_bytes(
                data, role=entry.role, path=entry.package_path, parquet_extractor=parquet_strings
            )
            if inspection and inspection.format in ("html", "svg", "plotly"):
                _user_links(inspection)
        except PublicationError as exc:
            if exc.code != "figure_not_shareable" or entry.role != "figure":
                raise
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
            renderer["libraries"].append(
                {
                    "name": "plotly",
                    "version": plotly.__version__,
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
    for name, data in sorted(files.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    return root
