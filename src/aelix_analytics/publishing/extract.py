"""Analysis-environment extraction; policy receives strings and never imports pyarrow."""

from __future__ import annotations

import base64
import io
import json

from .inspections import strings
from .model import Inspection, PublicationError, ScanText


def parquet_strings(data):
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise PublicationError(
            "inspection_failed", "pyarrow is required to inspect attached Parquet data"
        ) from exc
    values = []

    def binary(value, where):
        if isinstance(value, bytes):
            # Binary columns can contain UTF-8 or UTF-16 metadata; do not silently skip them.
            for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
                text = value.decode(encoding, errors="replace")
                values.append(ScanText(text, where))
                try:
                    values.extend(strings(json.loads(text), where))
                except (ValueError, TypeError):
                    pass
        elif isinstance(value, str):
            values.append(ScanText(value, where))
        elif isinstance(value, dict):
            for key, part in value.items():
                binary(key, where + ".key")
                binary(part, where + "." + str(key))
        elif isinstance(value, (list, tuple)):
            for n, part in enumerate(value):
                binary(part, f"{where}[{n}]")

    def metadata(meta, where):
        for key, value in (meta or {}).items():
            binary(key, where + ".key")
            binary(value, where)
            if key == b"ARROW:schema":
                arrow = pa.ipc.read_schema(pa.BufferReader(base64.b64decode(value, validate=True)))
                schema(arrow, where + ".ARROW:schema")

    def field(item, where):
        values.append(ScanText(item.name, where + ".name", True))
        metadata(item.metadata, where + ".metadata")
        typ = item.type
        if pa.types.is_dictionary(typ):
            typ = typ.value_type
        if pa.types.is_struct(typ) or pa.types.is_map(typ):
            for child in typ:
                field(child, where + "." + child.name)
        elif (
            pa.types.is_list(typ) or pa.types.is_large_list(typ) or pa.types.is_fixed_size_list(typ)
        ):
            field(typ.value_field, where + ".list")

    def schema(value, where):
        metadata(value.metadata, where + ".metadata")
        for item in value:
            field(item, where + "." + item.name)

    try:
        file = pq.ParquetFile(io.BytesIO(data))
        metadata(file.metadata.metadata, "parquet.metadata")
        # ARROW:schema decoded above may carry metadata absent from the materialized schema.
        schema(file.schema_arrow, "schema")
        for group in range(file.metadata.num_row_groups):
            row_group = file.metadata.row_group(group)
            for n in range(row_group.num_columns):
                column = row_group.column(n)
                values.append(ScanText(column.path_in_schema, "statistics.column", True))
                stats = column.statistics
                if stats is not None and stats.has_min_max:
                    binary(stats.min, "statistics.min")
                    binary(stats.max, "statistics.max")
        for batch in file.iter_batches(batch_size=8192):
            binary(batch.to_pylist(), "rows")
    except Exception as exc:
        if isinstance(exc, PublicationError):
            raise
        raise PublicationError(
            "inspection_failed", f"cannot inspect Parquet metadata/values: {type(exc).__name__}"
        ) from exc
    return Inspection("parquet", tuple(values))
