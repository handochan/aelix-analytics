"""Aelix Analytics: the script API of a run, and the package everything else of it is in.

    import aelix_analytics as analytics

    sales = analytics.read("sales")                        # Polars DataFrame of data/raw/sales.parquet
    monthly = analytics.sql("SELECT ... FROM sales").pl()  # DuckDB over every dataset view
    analytics.save(monthly, "monthly_sales")               # data/derived/monthly_sales.parquet
    analytics.chart(fig, "monthly-trend", title="…", caption="…", data=monthly)

Inside a run every call is recorded. Plain `pl.read_parquet(...)`, DuckDB SQL over file literals,
`plt.show()` and plotly `fig.show()` are captured too; `analytics.chart(..., data=df)` additionally
keeps a stable chart id, a caption and the exact plotted data. Outside a run the calls still work but
record nothing. Before kit 0.2.0 this API was the module `labkit` (`import labkit as lab`).

A run gets this package from the entry point that started it: `analytics`, `bin/lab` and the aelix
extension give the project's environment exactly one directory on PYTHONPATH — the runtime root, the
checkout's `src/` or an installation's `aelix_analytics/kit/` — and `python -m aelix_analytics` runs
the kit's command line from it (__main__). The installed distribution is this package too: the human
command line (`cli`), where its kit is (`_install`), the aelix extension (`extension`) and the
manifest, with a copy of the whole package as the runtime root under `kit/`. aelix imports this
package when it loads the extension, so at module level it imports the standard library only: the
SDK's functions import the kit's modules when they are called, and `__version__` reads `core` when it
is asked for (PEP 562). Nothing else is answered by `__getattr__`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import core

__all__ = ["chart", "datasets", "note", "path", "project", "read", "run_id", "save", "scan", "sql"]


def __getattr__(name: str):
    # Called for a name this module does not have, `hasattr` and `from aelix_analytics import x` of a
    # submodule included: only `__version__` imports anything.
    if name == "__version__":
        from .core import KIT_VERSION

        return KIT_VERSION
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_CONNECTION = None


def project() -> core.Project:
    from . import capture, core

    if capture.ACTIVE is not None:
        return capture.ACTIVE.project
    root = core.find_project(os.environ.get("LAB_PROJECT") or os.getcwd())
    if root is None:
        raise RuntimeError("not inside a lab project (no lab.json in this directory or above)")
    return core.Project(root)


def run_id() -> str | None:
    from . import capture

    return capture.ACTIVE.run_id if capture.ACTIVE is not None else None


def path(rel) -> Path:
    """Absolute path of a project-relative path."""
    return project().path(rel)


def datasets() -> dict[str, Path]:
    """Dataset view name -> absolute Parquet path."""
    from . import core

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
    from . import capture

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

    from . import capture, data

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
    from . import capture

    if capture.ACTIVE is not None:
        return capture.ACTIVE.save_dataset(obj, name)
    return capture.save_dataset(project(), obj, name)


def chart(obj, id: str | None = None, *, title: str | None = None, caption: str | None = None,
          data=None) -> dict:
    """Record a chart: a matplotlib Figure/Axes, a plotly Figure, an object with _repr_html_, or a
    path to an image/HTML file. `id` should be short ASCII (it names the chart across versions);
    `data` is the table that was plotted and is saved next to the chart."""
    from . import capture, core

    state = capture.ACTIVE
    if state is None:
        p = project()
        stamp = core.now().strftime("%Y%m%d-%H%M%S")
        run_dir = p.lab / "adhoc" / stamp
        state = capture.RunState(p, f"adhoc-{stamp}", run_dir, Path(sys.argv[0]).resolve())
        record = state.capture(obj, id, title=title, caption=caption, data=data)
        print(f"aelix_analytics: not inside a lab run; chart saved under {run_dir} but not recorded",
              file=sys.stderr)
        return record
    return state.capture(obj, id, title=title, caption=caption, data=data)


def note(kind: str, text: str) -> None:
    """Journal entry linked to the current run (written when the run finishes)."""
    from . import capture, core

    if kind not in core.NOTE_KINDS or kind == "resolve":
        raise ValueError(f"kind must be one of {', '.join(k for k in core.NOTE_KINDS if k != 'resolve')}")
    text = str(text)
    if capture.ACTIVE is not None:
        capture.ACTIVE.notes.append((kind, text))
    else:
        core.add_note(project(), kind, text, actor="script")
