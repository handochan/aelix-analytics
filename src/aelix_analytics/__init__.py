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

__all__ = ["assets", "checkpoint", "datasets", "path", "project", "read", "record", "register_asset", "review_assets",
           "run_id", "save", "scan", "set_asset_mode", "sql", "status", "update_asset", "visualize",
           "save_report", "reports", "update_report", "clean_report_prepared"]

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
UNSET = object()


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


def record(kind: str, text: str, *, refs=(), details=None) -> dict:
    """A journal entry with names/typed refs and optional structured details (ADR-0011). Outside a
    run returns the written event and request ID; inside a run returns pending=True and the run ID,
    retaining a private payload until finalization. Final write failures are finalize_errors, not
    successful observations. An in-run metric_observation defaults to that run_id. Definition,
    focus and observation use their details.contract tags; free Python still needs no registration.
    Authorship follows the run or the outside-run SDK Origin. Whole text and line limits apply at
    the call and final write; no truncation. A run's later captured inputs can enlarge an observation
    line, so a pending reply is not a guarantee of final append. Refused before effects where the kit
    may not write. `gap` names its replacement workflow_feedback in bad_kind; `resolve` needs an
    explicit typed resolves ref. Named `note` before kit 0.3.0."""
    from . import capture, core

    if kind == "resolve" and not refs:
        # Preserve the original two-argument SDK refusal. Typed refs make the new explicit path.
        core.writer.check_kind(kind, tuple(k for k in core.RECORD_KINDS if k != "resolve"))
    core.writer.check_kind(kind)
    text = str(text)
    state = capture.ACTIVE
    if state is not None:
        refs = [refs] if isinstance(refs, (str, dict)) else list(refs or ())
        if kind == "checkpoint":
            prepared = core.prepare_checkpoint(state.project, text, refs=refs, details=details,
                                               origin=state.sdk_origin, pending_run=state.run_id)
            text, refs, details = prepared["text"], prepared["refs"], prepared["details"]
        else:
            refs = [*refs, state.run_id]
        if kind == "metric_observation" and isinstance(details, dict):
            details = {**details, "run_id": details.get("run_id", state.run_id)}
            if details["run_id"] != state.run_id:
                raise core.AnalyticsError("metric_reference", "an in-run observation uses this run_id")
        # Judged now as the run will write it — linked to the run, by the run's actor through the
        # SDK — with the widest seq (SEQ_BOUND): a note refused for its text or its line is this
        # call's exception, never a note the run loses when it finishes.
        core.writer_for(state.project, state.sdk_origin).check(
            kind, text, refs=refs, details=details, seq=core.writer.SEQ_BOUND, pending_run=state.run_id)
        # Preserve a JSON snapshot, so mutating a caller's dictionary after acknowledgement does
        # not change the queued record. Full final relationships are rechecked when the run ends.
        import json

        state.notes.append((kind, text, json.loads(json.dumps(refs)),
                            json.loads(json.dumps(details)) if details is not None else None))
        return {"pending": True, "run_id": state.run_id}
    else:
        return core.record(project(), kind, text, refs=refs, details=details,
                           origin=_script_origin()).to_json()


def checkpoint(text=None, *, refs=(), as_of_seq=None, context_version=UNSET, from_seq=0, limit=8):
    """Explicit authored or model-free deterministic summary at an exact call-time basis."""
    from . import capture, core

    state = capture.ACTIVE
    options = {"refs": refs, "as_of_seq": as_of_seq, "from_seq": from_seq, "limit": limit}
    if context_version is not UNSET:
        options["context_version"] = context_version
    if state is not None:
        prepared = core.prepare_checkpoint(state.project, text, origin=state.sdk_origin, pending_run=state.run_id, **options)
        answer = record("checkpoint", prepared["text"], refs=prepared["refs"], details=prepared["details"])
        answer["basis"] = {key: prepared["details"][key] for key in ("as_of_seq", "context_version", "journal_basis")}
        return answer
    return core.checkpoint(project(), text, origin=_script_origin(), **options)



def _script_origin():
    """Who a record the script API makes outside a run is by (design 4.2, OQ1): AELIX_ANALYTICS_ACTOR
    as given (declared), else `human`, which the kit fills in (inferred); the means `sdk` and the
    session AELIX_ANALYTICS_SESSION. A lab.event/1 record holds the actor `script` there, which a
    reader reads as it is."""
    from . import core

    actor = os.environ.get("AELIX_ANALYTICS_ACTOR") or None
    return core.Origin(actor or "human", "sdk", "declared" if actor else "inferred",
                       os.environ.get("AELIX_ANALYTICS_SESSION") or None)


def assets(asset_id=None) -> dict:
    """Read analysis asset candidates and exact prior versions; no review, execution or registration."""
    from . import core

    return core.assets(project(), asset_id)


def _asset_origin():
    from . import capture

    return capture.ACTIVE.sdk_origin if capture.ACTIVE is not None else _script_origin()


def save_report(report_id, *, files, entry, title, previous_revision, request_id=None, based_on=None):
    """Register authored files as one immutable Report revision, without running generation code.

    previous_revision is explicit (None for the first revision). Preserve request_id for retries.
    Evidence is only the supplied based_on, never inferred from the current project.
    """
    from . import core

    return core.save_report(project(), report_id, files=files, entry=entry, title=title,
                            previous_revision=previous_revision, request_id=request_id,
                            based_on=based_on, origin=_asset_origin())


def reports(report_id=None, revision_id=None, **options):
    """Bounded Report metadata/history or one file's exact bytes, encoded as base64."""
    from . import core

    return core.reports(project(), report_id, revision_id, **options)


def update_report(report_id, revision_id, *, expected, new, reason):
    """Withdraw or restore an exact completed revision, keeping its authored bytes immutable."""
    from . import core

    return core.update_report(project(), report_id, revision_id, expected=expected, new=new,
                              reason=reason, origin=_asset_origin())


def clean_report_prepared(report_id, revision_id):
    """Explicitly remove only the named prepared attempt after taking its lock."""
    from . import core

    return core.clean_report_prepared(project(), report_id, revision_id, origin=_asset_origin())


def review_assets(asset_id=None) -> dict:
    """Explicit model-free review; returned candidates are recorded as requested suggestions."""
    from . import core

    return core.review_assets(project(), asset_id, origin=_asset_origin())


def update_asset(event_id: str, *, expected: str, new: str, text: str, details=None) -> dict:
    """Advance, defer/reject or annotate one candidate version, preserving checks and history."""
    from . import core

    return core.update_asset(project(), event_id, expected=expected, new=new, text=text,
                             details=details, origin=_asset_origin())


def register_asset(event_id: str, *, name: str) -> dict:
    """Explicitly copy a validated version into this project; never a global installation."""
    from . import core

    return core.register_asset(project(), event_id, name=name, origin=_asset_origin())


def status(*, part=None, **options) -> dict:
    """The same overview/record/context/relation read as analytics status and analytics_status.
    No model, host or analysis library is needed by core's retrieval implementation."""
    from . import core

    return core.status(project(), part=part, **options)


def set_asset_mode(mode: str) -> dict:
    """Choose project-local on-demand or model-free automatic suggestions as a human."""
    from . import core

    return core.set_asset_mode(project(), mode, origin=_asset_origin())
