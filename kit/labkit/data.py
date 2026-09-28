"""Datasets: import into Parquet, profile with Polars, read-only DuckDB queries over dataset views."""

from __future__ import annotations

import contextlib
import math
import os
import re
import shutil
import time
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path

from . import core

IMPORT_FORMATS = {".parquet": "parquet", ".csv": "csv", ".tsv": "tsv", ".txt": "csv",
                  ".json": "json", ".ndjson": "json", ".jsonl": "json"}


def cell(value, width: int = 200):
    """JSON-safe, display-friendly cell value."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    text = str(value)
    return text if len(text) <= width else text[:width - 1] + "…"


def _sql_literal(path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def referenced_views(query: str, views) -> set[str]:
    """Dataset views a SQL statement reads.

    Uses DuckDB's parser only (json_serialize_sql), so nothing is bound: JOIN ... USING and unknown
    columns do not matter, and CTE names are not mistaken for datasets. Statements the serializer
    does not accept fall back to names that directly follow FROM or JOIN.
    """
    import json

    import duckdb

    names = {v.lower(): v for v in views}
    found: set[str] = set()
    try:
        tree = json.loads(duckdb.sql("SELECT json_serialize_sql(?)", params=[query]).fetchone()[0])
    except (duckdb.Error, TypeError, ValueError):
        tree = {"error": True}
    if not tree.get("error"):

        def walk(node, scope: frozenset):
            # A CTE body sees the names defined before it; the rest of the query sees all of them.
            if isinstance(node, dict):
                cte_map = node.get("cte_map")
                if isinstance(cte_map, dict) and cte_map.get("map"):
                    inner = set(scope)
                    for item in cte_map["map"]:
                        walk(item.get("value"), frozenset(inner))
                        inner.add(str(item.get("key", "")).lower())
                    scope = frozenset(inner)
                if node.get("type") == "BASE_TABLE" and isinstance(node.get("table_name"), str):
                    name = node["table_name"].lower()
                    if name not in scope:
                        found.add(name)
                for key, value in node.items():
                    if key != "cte_map":
                        walk(value, scope)
            elif isinstance(node, list):
                for value in node:
                    walk(value, scope)

        walk(tree, frozenset())
    else:
        text = re.sub(r"'(?:[^']|'')*'", " ", query).lower()
        found = {m.strip('"') for m in re.findall(r'\b(?:from|join)\s+("?[\w.]+"?)', text)}
    return {names[n] for n in found if n in names}


def register_views(con, project: core.Project) -> dict[str, str]:
    from . import capture

    views = core.view_names(core.list_datasets(project))
    # Registering a view is not a read: keep the capture hooks from recording every dataset.
    with capture.paused():
        for name, rel in views.items():
            con.execute(f'CREATE OR REPLACE VIEW "{name}" AS SELECT * FROM read_parquet('
                        f'{_sql_literal(project.path(rel))})')
    return views


def profile(project: core.Project, path: Path, preview_rows: int = 20,
            cache: core.HashCache | None = None) -> dict:
    import polars as pl

    path = Path(path)
    sha = (cache or core.HashCache(project)).sha(path)
    cache_file = project.cache / "profiles" / f"{sha}-{preview_rows}.json"
    cached = core.read_json(cache_file) if sha else None
    if isinstance(cached, dict):
        return cached
    lf = pl.scan_parquet(path)
    schema = lf.collect_schema()
    exprs = []
    for i, (name, dtype) in enumerate(schema.items()):
        col = pl.col(name)
        exprs.append(col.null_count().alias(f"c{i}_nulls"))
        if dtype.is_numeric():
            exprs += [col.min().alias(f"c{i}_min"), col.max().alias(f"c{i}_max"),
                      col.mean().alias(f"c{i}_mean")]
        elif dtype.is_temporal():
            exprs += [col.min().alias(f"c{i}_min"), col.max().alias(f"c{i}_max")]
        elif dtype in (pl.String, pl.Categorical, pl.Enum) or dtype == pl.Boolean:
            exprs.append(col.approx_n_unique().alias(f"c{i}_distinct"))
    stats = lf.select([pl.len().alias("rows"), *exprs]).collect().row(0, named=True)
    columns = []
    for i, (name, dtype) in enumerate(schema.items()):
        columns.append({"name": name, "dtype": str(dtype), "nulls": stats.get(f"c{i}_nulls"),
                        "min": cell(stats.get(f"c{i}_min"), 60),
                        "max": cell(stats.get(f"c{i}_max"), 60),
                        "mean": cell(stats.get(f"c{i}_mean"), 60),
                        "distinct": stats.get(f"c{i}_distinct")})
    head = lf.head(preview_rows).collect()
    result = {"sha256": sha, "bytes": path.stat().st_size, "rows": stats["rows"],
              "columns": columns,
              "preview": {"columns": head.columns,
                          "rows": [[cell(v, 120) for v in row] for row in head.rows()]}}
    if sha:
        with contextlib.suppress(OSError):
            core.write_json(cache_file, result, platform=project.platform)
    return result


def import_file(project: core.Project, source: str, *, name: str | None = None,
                description: str | None = None, actor: str = "agent",
                session: str | None = None) -> dict:
    import duckdb

    src = core.expand_home(Path(source))  # the rule Project.path follows
    src = (src if src.is_absolute() else Path.cwd() / src).resolve()
    if not src.is_file():
        raise core.LabError("import_source_missing", f"{src} is not a readable file")
    fmt = IMPORT_FORMATS.get(src.suffix.lower())
    if fmt is None:
        raise core.LabError("import_format", f"cannot import {src.suffix or 'this file type'}",
                            hint="supported: .parquet .csv .tsv .txt .json .ndjson .jsonl; convert "
                                 "other formats inside a lab_run script and lab.save the result")
    name = name or re.sub(r"[^a-z0-9_]+", "_", src.stem.lower()).strip("_") or "dataset"
    if name[0].isdigit():
        name = f"d_{name}"
    if not core.DATASET_NAME_RE.match(name):
        raise core.LabError("dataset_name", f"invalid dataset name {name!r}",
                            hint="lowercase letters, digits and _, starting with a letter")
    # An import always creates a name the project does not have yet, so a reserved device name is
    # always a new one and is refused outright.
    core.reject_unportable_name(name, kind="dataset")
    dest = project.raw / f"{name}.parquet"
    taken = core.dataset_name_taken(project, name)
    if taken and not dest.exists():
        raise core.LabError("dataset_name_taken", f"dataset name {name!r} is already used by {taken}",
                            hint="choose another name; lab.read and lab_query resolve datasets by name")
    if dest.exists():
        raise core.LabError("dataset_exists", f"data/raw/{name}.parquet already exists",
                            hint=f"imported data is immutable: import under a new name such as "
                                 f"{name}_v2 and record why with lab_note")
    project.raw.mkdir(parents=True, exist_ok=True)
    tmp = project.raw / f".{name}.{os.getpid()}.tmp.parquet"
    started = time.perf_counter()
    retyped: list[str] = []
    try:
        if fmt == "parquet":
            shutil.copyfile(src, tmp)
            import polars as pl

            pl.scan_parquet(tmp).collect_schema()  # fails early on a file that is not Parquet
        else:
            con = duckdb.connect()
            if fmt == "json":
                rel = con.read_json(str(src))
            elif fmt == "tsv":
                rel = con.read_csv(str(src), sep="\t")
            else:
                rel = con.read_csv(str(src))
            rel, retyped = _as_polars_types(rel)
            rel.write_parquet(str(tmp), compression="zstd")
        project.platform.files.replace(tmp, dest)
    except core.LabError:
        raise
    except Exception as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise core.LabError("import_failed", f"could not read {src.name} as {fmt}: {exc}") from exc
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    info = profile(project, dest)
    st = src.stat()
    meta = {"schema": core.SCHEMA_DATASET, "name": name, "path": project.rel(dest),
            "sha256": info["sha256"], "bytes": info["bytes"], "rows": info["rows"],
            "columns": [{"name": c["name"], "dtype": c["dtype"]} for c in info["columns"]],
            "description": (description or "").strip() or None, "imported_at": core.iso(),
            "source": {"path": str(src), "sha256": core.sha256_file(src), "bytes": st.st_size,
                       "format": fmt,
                       "modified_at": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(
                           timespec="seconds")}}
    core.write_json(dest.with_suffix(".meta.json"), meta, platform=project.platform)
    text = (f"imported {name}: {info['rows']:,} rows × {len(info['columns'])} columns from "
            f"{src.name}")
    if meta["description"]:
        text += f" — {meta['description']}"
    event = core.append_event(project, "import", text, refs={"data": [meta["path"]]},
                              actor=actor, session=session)
    return {"dataset": meta, "profile": info, "event": event["id"],
            "warnings": import_warnings(name, info, retyped),
            "elapsed_s": round(time.perf_counter() - started, 3)}


# DuckDB types that survive Parquet but land in Polars as opaque bytes, so they are read as text.
TEXT_TYPES = {"UUID", "INTERVAL", "BIT", "TIME WITH TIME ZONE"}
NESTED_DTYPES = ("Struct", "List", "Array", "Object")


def _as_polars_types(rel):
    """Project a DuckDB relation so columns Polars cannot represent come out as VARCHAR."""
    casts = [name for name, dtype in zip(rel.columns, rel.types, strict=False)
             if str(dtype).upper() in TEXT_TYPES]
    if not casts:
        return rel, []
    exprs = []
    for name in rel.columns:
        quoted = '"' + str(name).replace('"', '""') + '"'
        exprs.append(f"CAST({quoted} AS VARCHAR) AS {quoted}" if name in casts else quoted)
    return rel.project(", ".join(exprs)), casts


def import_warnings(name: str, info: dict, retyped: list[str]) -> list[str]:
    warnings = []
    if retyped:
        warnings.append(f"read as text: {', '.join(retyped)} (DuckDB typed them UUID/INTERVAL, "
                        "which Polars cannot represent)")
    nested = [c["name"] for c in info["columns"]
              if str(c.get("dtype", "")).startswith(NESTED_DTYPES)]
    if info["rows"] == 1 and len(info["columns"]) <= 2 and nested:
        warnings.append(f"the source is one JSON object, so {name} is {info['rows']} row × "
                        f"{len(info['columns'])} nested column(s) and is not queryable as a table: "
                        f"unnest it in a lab_run script (lab.sql(\"SELECT unnest(...) FROM "
                        f"{name}\")) and lab.save the flat table")
    elif nested:
        warnings.append(f"nested column(s) {', '.join(nested[:6])}: flatten them in a lab_run "
                        "script before grouping or joining on their fields")
    return warnings


def query(project: core.Project, sql: str, limit: int = 50) -> dict:
    import duckdb

    limit = max(1, min(int(limit), 500))
    sql = (sql or "").strip()
    if not sql:
        raise core.LabError("query_empty", "empty SQL")
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise core.LabError("query_parse", str(exc).splitlines()[0][:500]) from exc
    if len(statements) != 1:
        raise core.LabError("query_single_statement", f"expected one statement, got "
                            f"{len(statements)}")
    kind = statements[0].type
    if kind == duckdb.StatementType.EXPLAIN:
        inner = re.sub(r"^\s*EXPLAIN\s+", "", sql, flags=re.IGNORECASE)
        if re.match(r"ANALY[SZ]E\b", inner, re.IGNORECASE):
            raise core.LabError("query_read_only", "EXPLAIN ANALYZE executes the statement",
                                hint="use plain EXPLAIN, or run the SELECT itself")
        try:
            kind = duckdb.extract_statements(inner)[0].type
        except (duckdb.Error, IndexError):
            kind = None
    if kind != duckdb.StatementType.SELECT:
        name = getattr(statements[0].type, "name", "this")
        raise core.LabError("query_read_only", f"{name} statements are not allowed here",
                            hint="lab_query is read-only exploration; write data with lab_run")
    con = duckdb.connect()
    views = register_views(con, project)
    # Only the project's data directory is reachable, and the query cannot change that.
    con.execute("SET allowed_directories = ?", [[str(project.data) + os.sep]])
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    started = time.perf_counter()
    entry: dict = {"ts": core.iso(), "sql": sql[:4000]}
    try:
        rel = con.sql(sql)
        if rel is None:
            raise core.LabError("query_no_result", "the statement returned no result set")
        columns = [{"name": n, "type": str(t)} for n, t in zip(rel.columns, rel.types,
                                                               strict=False)]
        try:
            rows = rel.limit(limit + 1).fetchall()
        except duckdb.Error:
            rows = rel.fetchmany(limit + 1)
    except core.LabError:
        raise
    except duckdb.Error as exc:
        entry["error"] = str(exc)[:500]
        core.append_jsonl(project.queries, entry)
        raise core.LabError("query_failed", str(exc).strip()[:1500],
                            hint="views: " + ", ".join(views)) from exc
    truncated = len(rows) > limit
    rows = rows[:limit]
    tables = sorted(referenced_views(sql, views))
    entry.update(rows=len(rows), truncated=truncated, tables=tables)
    core.append_jsonl(project.queries, entry)
    return {"columns": columns, "rows": [[cell(v) for v in row] for row in rows],
            "truncated": truncated, "limit": limit, "tables": tables, "views": views,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
