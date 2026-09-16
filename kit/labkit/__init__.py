"""Script API for aelix-analytics-baseline lab projects.

    import labkit as lab

    sales = lab.read("sales")                          # Polars DataFrame of data/raw/sales.parquet
    monthly = lab.sql("SELECT ... FROM sales").pl()    # DuckDB over every dataset view
    lab.save(monthly, "monthly_sales")                 # data/derived/monthly_sales.parquet
    lab.chart(fig, "monthly-trend", title="…", caption="…", data=monthly)

Inside a lab run every call is recorded. Plain `pl.read_parquet(...)`, DuckDB SQL over file literals,
`plt.show()` and plotly `fig.show()` are captured too; `lab.chart(..., data=df)` additionally keeps a
stable chart id, a caption and the exact plotted data. Outside a run the calls still work but record
nothing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import capture, core

__version__ = core.KIT_VERSION
__all__ = ["chart", "datasets", "note", "path", "project", "read", "run_id", "save", "scan", "sql"]

_CONNECTION = None


def project() -> core.Project:
    if capture.ACTIVE is not None:
        return capture.ACTIVE.project
    root = core.find_project(os.environ.get("LAB_PROJECT") or os.getcwd())
    if root is None:
        raise RuntimeError("not inside a lab project (no lab.json in this directory or above)")
    return core.Project(root)


def run_id() -> str | None:
    return capture.ACTIVE.run_id if capture.ACTIVE is not None else None


def path(rel) -> Path:
    """Absolute path of a project-relative path."""
    return project().path(rel)


def datasets() -> dict[str, Path]:
    """Dataset view name -> absolute Parquet path."""
    p = project()
    return {name: p.path(rel) for name, rel in core.view_names(core.list_datasets(p)).items()}


def _dataset_path(dataset) -> Path:
    text = os.fspath(dataset)
    if text.endswith(".parquet") or "/" in text or os.sep in text:
        return project().path(text)
    known = datasets()
    if text not in known:
        raise KeyError(f"unknown dataset {text!r}; available: {', '.join(known) or 'none'}")
    return known[text]


def _record(p: Path, via: str) -> None:
    if capture.ACTIVE is not None:
        capture.ACTIVE.record_read(str(p), via)


def read(dataset, **kwargs):
    """Polars DataFrame of a dataset name (see datasets()) or a Parquet path."""
    import polars as pl

    p = _dataset_path(dataset)
    _record(p, "lab.read")
    return pl.read_parquet(p, **kwargs)


def scan(dataset, **kwargs):
    """Polars LazyFrame of a dataset name or a Parquet path."""
    import polars as pl

    p = _dataset_path(dataset)
    _record(p, "lab.scan")
    return pl.scan_parquet(p, **kwargs)


def sql(query: str, **frames):
    """DuckDB relation for `query`. Every dataset is a view named after it; keyword arguments
    register extra Polars/Arrow frames under their names. Call .pl() for a Polars DataFrame."""
    import duckdb

    from . import data

    global _CONNECTION
    if _CONNECTION is None:
        _CONNECTION = duckdb.connect()
    p = project()
    views = data.register_views(_CONNECTION, p)
    for name, frame in frames.items():
        _CONNECTION.register(name, frame)
    if capture.ACTIVE is not None:
        for name in data.referenced_views(query, views):
            _record(p.path(views[name]), "lab.sql")
    return _CONNECTION.sql(query)


def save(obj, name: str) -> Path:
    """Write a Polars/DuckDB/Arrow/pandas table to data/derived/<name>.parquet."""
    if capture.ACTIVE is not None:
        return capture.ACTIVE.save_dataset(obj, name)
    return capture.save_dataset(project(), obj, name)


def chart(obj, id: str | None = None, *, title: str | None = None, caption: str | None = None,
          data=None) -> dict:
    """Record a chart: a matplotlib Figure/Axes, a plotly Figure, an object with _repr_html_, or a
    path to an image/HTML file. `id` should be short ASCII (it names the chart across versions);
    `data` is the table that was plotted and is saved next to the chart."""
    state = capture.ACTIVE
    if state is None:
        p = project()
        stamp = core.now().strftime("%Y%m%d-%H%M%S")
        run_dir = p.lab / "adhoc" / stamp
        state = capture.RunState(p, f"adhoc-{stamp}", run_dir, Path(sys.argv[0]).resolve())
        record = state.capture(obj, id, title=title, caption=caption, data=data)
        print(f"labkit: not inside a lab run; chart saved under {run_dir} but not recorded",
              file=sys.stderr)
        return record
    return state.capture(obj, id, title=title, caption=caption, data=data)


def note(kind: str, text: str) -> None:
    """Journal entry linked to the current run (written when the run finishes)."""
    if kind not in core.NOTE_KINDS or kind == "resolve":
        raise ValueError(f"kind must be one of {', '.join(k for k in core.NOTE_KINDS if k != 'resolve')}")
    text = str(text)
    if capture.ACTIVE is not None:
        capture.ACTIVE.notes.append((kind, text))
    else:
        core.add_note(project(), kind, text, actor="script")
