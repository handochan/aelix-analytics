"""Aelix Analytics: the script API of a run, and the package everything else of it is in.

    import aelix_analytics as analytics

    sales = analytics.read("sales")                        # Polars DataFrame of data/imported/sales.parquet
    monthly = analytics.sql("SELECT ... FROM sales").pl()  # DuckDB over every dataset view
    analytics.save(monthly, "monthly_sales")               # data/derived/monthly_sales.parquet
    analytics.visualize(fig, "monthly-trend", title="…", caption="…", data=monthly)

Inside a run every call is recorded. Plain `pl.read_parquet(...)`, DuckDB SQL over file literals,
`plt.show()` and plotly `fig.show()` are captured too; `analytics.visualize(..., data=df)` additionally
keeps a stable chart id, a caption and the exact plotted data. Outside a run the calls still work but
record nothing. Before kit 0.2.0 this API was the module `labkit` (`import labkit as lab`); before kit
0.3.0 `record` was `note` and `visualize` was `chart`. No alias is kept: the old names fail with the new
one in the error (ADR-0005 decision 9). A run records what each call did by the function's own name
(its `via`: analytics.read, analytics.scan, analytics.sql, analytics.save, analytics.visualize) since
the new record format (kit 0.8.0, Issue #13); a lab.run/1 record holds the older lab.* names.

A run gets this package from the entry point that started it: `analytics`, `bin/analytics` and the aelix
extension give the project's environment exactly one directory on PYTHONPATH — the runtime root, the
checkout's `src/` or an installation's `aelix_analytics/kit/` — and `python -m aelix_analytics` runs
the kit's command line from it (__main__). The installed distribution is this package too: the human
command line (`cli`), where its kit is (`_install`), the aelix extension (`extension`) and the
manifest, with a copy of the whole package as the runtime root under `kit/`. aelix imports this
package when it loads the extension, so at module level it imports the standard library only: the
SDK's functions import the kit's modules when they are called, and `__version__` reads `core` when it
is asked for (PEP 562). `__getattr__` imports nothing else: the functions' old names get an
AttributeError that says the new one, and any other name a plain one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import core

__all__ = ["datasets", "path", "project", "read", "record", "run_id", "save", "scan", "sql", "visualize"]

# The script API's functions renamed in kit 0.3.0 (Issue #9), by their old names. The version is fixed:
# it names the boundary, core.NAMES_RENAMED_IN, which this module may not import at load.
_RENAMED = {"note": "record", "chart": "visualize"}
_RENAMED_IN = "0.3.0"


def __getattr__(name: str):
    # Called for a name this module does not have, `hasattr` and `from aelix_analytics import x` of a
    # submodule included: only `__version__` imports anything.
    if name == "__version__":
        from .core import KIT_VERSION

        return KIT_VERSION
    if name in _RENAMED:
        raise AttributeError(
            f"{__name__}.{name} was renamed {__name__}.{_RENAMED[name]} in kit {_RENAMED_IN}; call "
            f"{_RENAMED[name]}(...) with the same arguments. A run recorded by an earlier kit (its "
            f"env.kit_version is below {_RENAMED_IN}) re-runs as it was only with that kit")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_CONNECTION = None


def project() -> core.Project:
    """The run's project, or outside a run the nearest one at or above AELIX_ANALYTICS_PROJECT or the
    working directory (a directory with an analytics.json or a lab.json). One with both markers is
    refused (marker_conflict, an AnalyticsError): which format to read it as cannot be told. Each
    write function judges the project again before it writes (core.admit, Issue #13)."""
    from . import capture, core

    if capture.ACTIVE is not None:
        return capture.ACTIVE.project
    if os.environ.get("LAB_PROJECT"):
        # Outside a run; in one, the kit's command line has refused it already.
        raise RuntimeError(core.renamed_env_message("LAB_PROJECT"))
    root = core.find_project(os.environ.get("AELIX_ANALYTICS_PROJECT") or os.getcwd())
    if root is None:
        raise RuntimeError("not inside an analytics project (no analytics.json or lab.json in this "
                           "directory or above)")
    core.readable(root, "read the project")
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

    from . import capture

    p = _dataset_path(dataset)
    _record(p, capture.READ)
    return pl.read_parquet(p, **kwargs)


def scan(dataset, **kwargs):
    """Polars LazyFrame of a dataset name or a Parquet path."""
    import polars as pl

    from . import capture

    p = _dataset_path(dataset)
    _record(p, capture.SCAN)
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
            _record(p.path(views[name]), capture.SQL)
    return _CONNECTION.sql(query)


def save(obj, name: str) -> Path:
    """Write a Polars/DuckDB/Arrow/pandas table to data/derived/<name>.parquet."""
    from . import capture

    if capture.ACTIVE is not None:
        return capture.ACTIVE.save_dataset(obj, name)
    return capture.save_dataset(project(), obj, name)


def visualize(obj, id: str | None = None, *, title: str | None = None, caption: str | None = None,
              data=None) -> dict:
    """Record a visualization — a chart: a matplotlib Figure/Axes, a plotly Figure, an object with
    _repr_html_, or a path to an image/HTML file. `id` should be short ASCII (it names the chart
    across versions); `data` is the table that was plotted and is saved next to the chart. Named
    `chart` before kit 0.3.0; its record's `via` is "analytics.visualize" (a lab.run/1 record's
    "lab.chart")."""
    from . import capture, core

    state = capture.ACTIVE
    if state is None:
        p = project()
        core.admit(p, "visualize")  # before .analytics/adhoc/ is made
        stamp = core.now().strftime("%Y%m%d-%H%M%S")
        run_dir = p.state_dir / "adhoc" / stamp
        state = capture.RunState(p, f"adhoc-{stamp}", run_dir, Path(sys.argv[0]).resolve())
        record = state.capture(obj, id, title=title, caption=caption, data=data)
        print(f"aelix_analytics: not inside an analytics run; chart saved under {run_dir} but not "
              "recorded", file=sys.stderr)
        return record
    return state.capture(obj, id, title=title, caption=caption, data=data)


def record(kind: str, text: str) -> None:
    """Journal entry: in a run linked to the run (written when the run finishes), by the run's
    actor; outside a run written now, by AELIX_ANALYTICS_ACTOR, else `human` (inferred), through the
    SDK (design 4.2). Named `note` before kit 0.3.0. `kind` is one a person or an agent records
    (writer.RECORD_KINDS) but `resolve`, which needs a target the SDK does not take; `gap` is refused
    with its new name, workflow_feedback (bad_kind). The text is kept whole: over its limit it is
    refused (text_too_long, OQ2), never cut. Refused when it is made where this kit does not write
    (core.admit, Issue #13): in a run, an error in the script."""
    from . import capture, core

    # The kinds a person or an agent records, but `resolve`: it needs a target the SDK does not take.
    core.writer.check_kind(kind, tuple(k for k in core.RECORD_KINDS if k != "resolve"))
    text = str(text)
    if capture.ACTIVE is not None:
        core.admit(capture.ACTIVE.project, "record")
        core.writer.check_text(text)
        capture.ACTIVE.notes.append((kind, text))
    else:
        core.record(project(), kind, text, origin=_script_origin())



def _script_origin():
    """Who a record the script API makes outside a run is by (design 4.2, OQ1): AELIX_ANALYTICS_ACTOR
    as given (declared), else `human`, which the kit fills in (inferred); the means `sdk` and the
    session AELIX_ANALYTICS_SESSION. A lab.event/1 record holds the actor `script` there, which a
    reader reads as it is."""
    from . import core

    actor = os.environ.get("AELIX_ANALYTICS_ACTOR") or None
    return core.Origin(actor or "human", "sdk", "declared" if actor else "inferred",
                       os.environ.get("AELIX_ANALYTICS_SESSION") or None)
