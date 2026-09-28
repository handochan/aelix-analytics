"""Recording layer for one run: data reads, saved datasets and charts.

Scripts do not have to import labkit. Import hooks wrap the usual entry points while a run is active:
Polars readers and DuckDB reads/SQL record input files; `plt.show()`, `Figure.show()` and plotly's
`fig.show()` record charts instead of opening windows. `labkit.chart(..., data=df)` adds what a hook
cannot know: a stable id, a caption and the exact plotted data.
"""

from __future__ import annotations

import contextlib
import functools
import glob
import hashlib
import importlib.abc
import importlib.util
import io
import os
import re
import shutil
import sys
from pathlib import Path

from . import _platform, core

ACTIVE: RunState | None = None

# Validated categorical order and chart chrome (dataviz reference palette, light surface).
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK_2, MUTED, GRID, AXIS, SURFACE = ("#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7",
                                          "#fcfcfb")
CJK_FONTS = ("Apple SD Gothic Neo", "AppleGothic", "Noto Sans CJK KR", "Noto Sans KR",
             "NanumGothic", "Malgun Gothic")
FONT_STACK = "system-ui, -apple-system, 'Apple SD Gothic Neo', 'Segoe UI', sans-serif"

DATA_EXT = r"\.(?:parquet|csv|tsv|json|ndjson|jsonl)(?:\.gz|\.zst)?"
# File paths inside SQL: 'data/x.parquet' literals and "data/x.parquet" quoted identifiers.
SQL_FILE_RE = re.compile(rf"'([^'\n]+?{DATA_EXT})'|\"([^\"\n]+?{DATA_EXT})\"", re.IGNORECASE)
SQL_COPY_TO_RE = re.compile(r"\bTO\s+['\"]([^'\"\n]+)['\"]", re.IGNORECASE)

POLARS_READERS = ("read_parquet", "scan_parquet", "read_csv", "scan_csv", "read_ndjson",
                  "scan_ndjson", "read_json", "read_ipc", "scan_ipc", "read_ipc_stream",
                  "read_excel", "read_delta", "scan_delta", "read_avro")
DUCKDB_READERS = ("read_parquet", "from_parquet", "read_csv", "from_csv_auto", "read_json")
DUCKDB_SQL = ("sql", "query", "execute", "from_query")
SOURCE_KWARGS = ("source", "query", "file_glob", "file_globs", "path_or_buffer", "file_name",
                 "path", "sql")
PARAM_KWARGS = ("parameters", "params")


def sql_file_literals(text: str) -> list[str]:
    return [a or b for a, b in SQL_FILE_RE.findall(text)]


def to_polars(obj):
    """Polars DataFrame from Polars, DuckDB, pandas, Arrow or dict-of-columns data."""
    import polars as pl

    if isinstance(obj, pl.DataFrame):
        return obj
    if isinstance(obj, pl.LazyFrame):
        return obj.collect()
    if isinstance(obj, pl.Series):
        return obj.to_frame()
    module = type(obj).__module__
    if module.startswith("duckdb") and hasattr(obj, "pl"):
        return obj.pl()
    if module.startswith("pandas"):
        return pl.from_pandas(obj)
    if module.startswith("pyarrow"):
        return pl.from_arrow(obj)
    if isinstance(obj, dict):
        return pl.DataFrame(obj)
    raise TypeError(f"cannot convert {type(obj).__name__} to a Polars DataFrame")


def _iter_sources(source):
    if isinstance(source, (str, os.PathLike)):
        yield os.fspath(source)
    elif isinstance(source, (list, tuple)):
        for item in source:
            yield from _iter_sources(item)


def _as_read(item: str, via: str) -> Path | None:
    """The path a reader opens for `item`, as far as the kit can tell. A leading `~` is the
    reader's to expand, and the readers differ. Polars expands it with os.path.expanduser — on
    Windows that is a home guessed beside the current user's profile where USERNAME names the
    profile directory — so that is what it read. DuckDB puts its home directory (HOME, USERPROFILE
    on Windows, or a `home_directory` the script set) in place of the `~` and keeps the rest:
    `~/x.csv` is the home's x.csv, as os.path.expanduser has it where that variable is set (a
    `home_directory` the script set, or an unset HOME, is not followed), but `~tmp.csv` is the home
    directory's own path with `tmp.csv` appended, which the kit does not follow: None, not recorded
    (3.5 and main recorded nothing there, or, where that user has a home, a file of that home, which
    DuckDB did not read). The kit's own readers follow Project.path's rule (core.expand_home)."""
    if not item.startswith("~"):
        return Path(item)
    if via.startswith("polars."):
        return Path(os.path.expanduser(item))
    if via.startswith("duckdb."):
        return Path(os.path.expanduser(item)) if Path(item).parts[0] == "~" else None
    return core.expand_home(Path(item))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RunState:
    def __init__(self, project: core.Project, run_id: str, run_dir: Path, script_file: Path):
        self.project = project
        self.run_id = run_id
        self.run_dir = run_dir
        self.charts_dir = run_dir / "charts"
        self.script_file = str(script_file)
        self.cache = core.HashCache(project)
        self.inputs: dict[str, dict] = {}
        self.saved: dict[str, str] = {}
        self.charts: list[dict] = []
        self.notes: list[tuple[str, str]] = []
        # id(figure) -> (latest record made from it, fingerprint of what was saved)
        self._by_figure: dict[int, tuple[dict, str | None]] = {}
        self._keepalive: list = []
        # charts/<id><suffix> of every live chart written under its portable name, with the chart
        # that owns it (_remember_own_names)
        self._own_names: dict[str, dict] = {}

    # --- inputs ---

    def record_read(self, source, via: str) -> None:
        for item in _iter_sources(source):
            if "://" in item:
                entry = self.inputs.setdefault(item, {"path": item, "via": set(), "external": True})
                entry["via"].add(via)
                continue
            path = _as_read(item, via)
            if path is None:
                continue
            if not path.is_absolute():
                path = Path.cwd() / path
            text = str(path)
            if any(ch in text for ch in "*?["):
                for match in sorted(glob.glob(text, recursive=True))[:500]:
                    if os.path.isfile(match):
                        self._record_file(Path(match), via)
            elif path.is_dir():
                files = [p for p in sorted(path.rglob("*"))
                         if p.is_file() and p.suffix.lower() in core.DATA_SUFFIXES]
                for p in files[:500]:
                    self._record_file(p, via)
            elif path.is_file():
                self._record_file(path, via)

    def record_sql(self, query, via: str, params=None) -> None:
        if isinstance(query, str):
            targets = set(SQL_COPY_TO_RE.findall(query))
            for literal in sql_file_literals(query):
                if literal not in targets:
                    self.record_read(literal, via)
        for value in _iter_sources(params):  # execute("... read_parquet(?)", [path])
            if value.lower().endswith(core.DATA_SUFFIXES) or any(ch in value for ch in "*?"):
                self.record_read(value, via)

    def _record_file(self, path: Path, via: str) -> None:
        rel = self.project.rel(path)
        if rel.startswith((f"runs/{self.run_id}/", ".venv/", ".lab/")):
            return
        try:
            size = path.stat().st_size
        except OSError:
            return
        entry = self.inputs.get(rel)
        # A runtime read records the content as it is when read (the script may have rewritten the
        # file since the pre-run scan); the hash cache keeps this cheap. The hash comes first, so a
        # run interrupted while hashing records no entry without a hash or a source.
        rehash = entry is None or via not in ("scan", "declared") or entry["sha256"] is None
        sha = self.cache.sha(path) if rehash else None
        if entry is None:
            self.inputs[rel] = {"path": rel, "sha256": sha, "bytes": size, "via": {via},
                                "external": not self.project.inside(path)}
            return
        if rehash:
            entry["sha256"], entry["bytes"] = sha, size
        entry["via"].add(via)

    # --- derived datasets ---

    def save_dataset(self, obj, name: str) -> Path:
        return save_dataset(self.project, obj, name, self)

    # --- charts ---

    def capture(self, obj, chart_id=None, *, title=None, caption=None, data=None,
                via="lab.chart") -> dict | None:
        """Record a chart. Every lab.chart call is its own chart (a later call with the same id
        replaces the earlier one). Automatic captures (plt.show, fig.show, figures left open)
        skip a figure that an earlier capture already saved and that has not changed since."""
        target, kind = _figure_kind(obj)
        explicit = via == "lab.chart"
        prior = self._by_figure.get(id(target)) if target is not None else None
        if prior is not None and not self._changed(target, kind, prior[1]):
            if not explicit:
                return prior[0]  # None when this figure's chart was deliberately replaced
            if prior[0] is not None and prior[0]["via"] != "lab.chart":
                self._discard(prior[0])  # shown first, then named: keep only the named chart
        if explicit and chart_id:
            wanted = core.slugify(chart_id, 40)
            for existing in [c for c in self.charts if c["id"] == wanted]:
                self._discard(existing)
        auto_title = _detect_title(target, kind)
        record = {"id": self._unique_id(chart_id or title or auto_title), "kind": kind,
                  "title": title or auto_title, "caption": caption, "via": via, "files": {},
                  "line": self._caller_line()}
        if record["title"] is None:
            record["title"] = record["id"]
        fingerprint = self._save(record, target if target is not None else obj, kind)
        if data is not None:
            self._attach_data(record, data)
        self._remember_own_names(record)
        self.charts.append(record)
        if target is not None:
            self._by_figure[id(target)] = (record, fingerprint)
            self._keepalive.append(target)
        return record

    def capture_open_figures(self, via: str, close: bool) -> None:
        plt = sys.modules.get("matplotlib.pyplot")
        if plt is None:
            return
        for num in list(plt.get_fignums()):
            fig = plt.figure(num)
            with contextlib.suppress(Exception):
                self.capture(fig, via=via)
            if close:
                plt.close(fig)

    def _remember_own_names(self, record: dict) -> None:
        """A chart whose id is a Windows device name is written under its portable name
        (charts/con_.png). A file a script writes at the id's own name (charts/con.png) is the file
        main wrote or overwrote for that chart, not a chart of its own, so the name is remembered
        here with the chart that owns it. When that chart is replaced or discarded, main unlinked
        that file, and so does _discard: one the script writes afterwards is a chart of its own, as
        on main."""
        portable = _platform.portable_stem(record["id"])
        if portable == record["id"]:
            return
        for rel in [*record["files"].values(), record.get("data")]:
            path = self.project.path(rel) if rel else None
            if path is not None and path.parent == self.charts_dir \
                    and path.name.startswith(f"{portable}."):
                own = self.project.rel(self.charts_dir / (record["id"] + path.name[len(portable):]))
                self._own_names[own] = record

    def _forget_own_names(self, record: dict) -> list[str]:
        """Drop the names `record` owned, and return them."""
        owned = [own for own, owner in self._own_names.items() if owner is record]
        for own in owned:
            del self._own_names[own]
        return owned

    def _is_own_name(self, rel: str, path: Path) -> bool:
        """Whether a file the script wrote is at a live chart's own name — also under another case
        of it (charts/CON.png), where the file system does not tell the two apart: there it is the
        file main wrote or overwrote, as it is under the name itself."""
        if rel in self._own_names:
            return True
        folded = rel.casefold()
        for own in self._own_names:
            if own.casefold() == folded:
                with contextlib.suppress(OSError):
                    if os.path.samefile(path, self.project.path(own)):
                        return True
        return False

    def register_loose_files(self) -> None:
        """Image or HTML files a script wrote into the run directory itself become charts."""
        known = {p for c in self.charts for p in c["files"].values()}
        for base in (self.run_dir, self.charts_dir):
            if not base.is_dir():
                continue
            for p in sorted(base.iterdir()):
                rel = self.project.rel(p)
                if p.is_file() and p.suffix.lower() in core.CHART_SUFFIXES and rel not in known \
                        and not self._is_own_name(rel, p):
                    cid = self._unique_id(p.stem)
                    self.charts.append({"id": cid, "kind": "file", "title": p.stem,
                                        "caption": None, "via": "file", "line": None,
                                        "files": {p.suffix.lower().lstrip("."): rel}})

    def _unique_id(self, label) -> str:
        base = core.slugify(label or "", 40)
        if not any(ch.isalpha() for ch in base):  # e.g. a Korean title leaves only digits
            base = f"chart-{len(self.charts) + 1}"
        taken = {c["id"] for c in self.charts}
        cid, n = base, 2
        while cid in taken:
            cid, n = f"{base}-{n}", n + 1
        return cid

    def _caller_line(self):
        frame = sys._getframe(1)
        while frame is not None:
            if frame.f_code.co_filename == self.script_file:
                return frame.f_lineno
            frame = frame.f_back
        return None

    def _changed(self, obj, kind: str, fingerprint: str | None) -> bool:
        if fingerprint is None:
            return True
        with contextlib.suppress(Exception):
            return _fingerprint(obj, kind) != fingerprint
        return True

    def _discard(self, record: dict) -> None:
        if record in self.charts:
            self.charts.remove(record)
        # The id's own names go with the chart's files: main's files were there (_remember_own_names).
        own = self._forget_own_names(record)
        for rel in [*record.get("files", {}).values(), record.get("data"), *own]:
            if rel and rel.startswith(f"runs/{self.run_id}/"):
                with contextlib.suppress(OSError):
                    self.project.path(rel).unlink()
        for key, (rec, fingerprint) in list(self._by_figure.items()):
            if rec is record:
                # Keep the figure tracked: unchanged, it must not come back as an automatic capture.
                self._by_figure[key] = (None, fingerprint)

    def _save(self, record: dict, obj, kind: str) -> str | None:
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        # The chart's identity is `script::id` and records, versions and reruns point at it, so it
        # is never rewritten — generated or not. Only the file it is written to has to be openable
        # on Windows, and that mapping cannot collide (see _platform.portable_stem).
        cid = _platform.portable_stem(record["id"])
        if kind == "matplotlib":
            png = _render_png(obj)
            dest = self.charts_dir / f"{cid}.png"
            dest.write_bytes(png)
            record["files"] = {"png": self.project.rel(dest)}
            return _sha(png)
        if kind == "plotly":
            text = obj.to_json()
            dest = self.charts_dir / f"{cid}.plotly.json"
            dest.write_text(text, encoding="utf-8")
            record["files"] = {"plotly": self.project.rel(dest)}
            return _sha(text.encode())
        if kind == "altair":
            dest = self.charts_dir / f"{cid}.html"
            obj.save(str(dest))
            record["files"] = {"html": self.project.rel(dest)}
            return None
        if kind == "html":
            dest = self.charts_dir / f"{cid}.html"
            dest.write_text(obj._repr_html_(), encoding="utf-8")
            record["files"] = {"html": self.project.rel(dest)}
            return None
        src = self.project.path(os.fspath(obj))
        if not src.is_file():
            raise FileNotFoundError(f"lab.chart: no file {src}")
        dest = self.charts_dir / f"{cid}{src.suffix.lower()}"
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        record["files"] = {src.suffix.lower().lstrip("."): self.project.rel(dest)}
        record["source_file"] = self.project.rel(src)
        return None

    def _attach_data(self, record: dict, data) -> None:
        frame = to_polars(data)
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        dest = self.charts_dir / f"{_platform.portable_stem(record['id'])}.data.parquet"
        frame.write_parquet(dest)
        record["data"] = self.project.rel(dest)
        record["data_rows"] = frame.height
        record["data_columns"] = frame.columns[:60]


def _render_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=144, bbox_inches="tight", metadata={"Software": None})
    return buf.getvalue()


def _fingerprint(obj, kind: str) -> str | None:
    if kind == "matplotlib":
        return _sha(_render_png(obj))
    if kind == "plotly":
        return _sha(obj.to_json().encode())
    return None


def save_dataset(project: core.Project, obj, name: str, state: RunState | None = None) -> Path:
    if not core.DATASET_NAME_RE.match(name or ""):
        raise ValueError(f"dataset name {name!r} must match {core.DATASET_NAME_RE.pattern}")
    dest = project.derived / f"{name}.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    module = type(obj).__module__
    if module.startswith("polars") and type(obj).__name__ == "LazyFrame":
        obj.sink_parquet(dest)
    elif module.startswith("duckdb") and hasattr(obj, "write_parquet"):
        obj.write_parquet(str(dest))
    else:
        to_polars(obj).write_parquet(dest)
    if state is not None:
        state.saved[project.rel(dest)] = "lab.save"
    return dest


def _figure_kind(obj):
    if isinstance(obj, (str, os.PathLike)):
        return None, "file"
    module = type(obj).__module__
    if module.startswith("plotly") and hasattr(obj, "to_plotly_json"):
        return obj, "plotly"
    if hasattr(obj, "savefig") and module.startswith("matplotlib"):
        return obj, "matplotlib"
    figure = getattr(obj, "figure", None)
    if figure is not None and hasattr(figure, "savefig"):
        return figure, "matplotlib"
    if module.startswith("altair") and hasattr(obj, "save"):
        return obj, "altair"
    if hasattr(obj, "_repr_html_"):
        return obj, "html"
    raise TypeError(f"lab.chart cannot save a {type(obj).__name__}; pass a matplotlib or plotly "
                    "figure, an object with _repr_html_, or a path to an image/HTML file")


def _detect_title(obj, kind):
    with contextlib.suppress(Exception):
        if kind == "matplotlib":
            suptitle = getattr(obj, "_suptitle", None)
            if suptitle is not None and suptitle.get_text():
                return suptitle.get_text()
            for ax in obj.axes:
                for loc in ("center", "left", "right"):
                    if ax.get_title(loc):
                        return ax.get_title(loc)
        elif kind == "plotly":
            text = obj.layout.title.text
            if text:
                return re.sub(r"<[^>]+>", "", text)
    return None


# --- import hooks ---

class _HookLoader(importlib.abc.Loader):
    def __init__(self, loader, hook):
        self._loader = loader
        self._hook = hook

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        with contextlib.suppress(Exception):
            self._hook(module)

    def __getattr__(self, name):
        return getattr(self._loader, name)


class PostImportFinder(importlib.abc.MetaPathFinder):
    """Runs a hook right after selected modules finish importing."""

    def __init__(self, hooks: dict):
        self.hooks = hooks
        self._busy: set[str] = set()

    def find_spec(self, fullname, path=None, target=None):
        hook = self.hooks.get(fullname)
        if hook is None or fullname in self._busy:
            return None
        self._busy.add(fullname)
        try:
            spec = importlib.util.find_spec(fullname)
        finally:
            self._busy.discard(fullname)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _HookLoader(spec.loader, hook)
        return spec


def _source_arg(args, kwargs, offset):
    if len(args) > offset:
        return args[offset]
    return next((kwargs[k] for k in SOURCE_KWARGS if kwargs.get(k) is not None), None)


def _wrap_read(fn, via, offset=0):
    if getattr(fn, "__lab_wrapped__", False):
        return fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if ACTIVE is not None:
            with contextlib.suppress(Exception):
                ACTIVE.record_read(_source_arg(args, kwargs, offset), via)
        return fn(*args, **kwargs)

    wrapper.__lab_wrapped__ = True  # type: ignore[attr-defined]
    return wrapper


def _wrap_sql(fn, via, offset=0):
    if getattr(fn, "__lab_wrapped__", False):
        return fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if ACTIVE is not None:
            with contextlib.suppress(Exception):
                params = args[offset + 1] if len(args) > offset + 1 else next(
                    (kwargs[k] for k in PARAM_KWARGS if kwargs.get(k) is not None), None)
                ACTIVE.record_sql(_source_arg(args, kwargs, offset), via, params)
        return fn(*args, **kwargs)

    wrapper.__lab_wrapped__ = True  # type: ignore[attr-defined]
    return wrapper


def patch_polars(pl) -> None:
    namespaces = [pl, getattr(pl, "io", None)]
    for name in POLARS_READERS:
        original = getattr(pl, name, None)
        if not callable(original) or getattr(original, "__lab_wrapped__", False):
            continue
        wrapped = _wrap_read(original, f"polars.{name}")
        for ns in namespaces:  # `from polars.io import read_parquet` gets the same wrapper
            if ns is not None and getattr(ns, name, None) is original:
                setattr(ns, name, wrapped)


def patch_duckdb(duckdb) -> None:
    conn = getattr(duckdb, "DuckDBPyConnection", None)
    for names, wrap in ((DUCKDB_READERS, _wrap_read), (DUCKDB_SQL, _wrap_sql)):
        for name in names:
            fn = getattr(duckdb, name, None)
            if callable(fn):
                with contextlib.suppress(Exception):
                    setattr(duckdb, name, wrap(fn, f"duckdb.{name}"))
            method = getattr(conn, name, None) if conn is not None else None
            if callable(method):
                with contextlib.suppress(Exception):
                    setattr(conn, name, wrap(method, f"duckdb.{name}", offset=1))


def style_matplotlib(mpl) -> None:
    if os.environ.get("LAB_STYLE") == "0":
        return
    rc = mpl.rcParams
    rc.update({
        "axes.prop_cycle": mpl.cycler(color=PALETTE),
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "semibold", "axes.titlelocation": "left",
        "axes.titlepad": 10, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.axisbelow": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "xtick.color": AXIS, "ytick.color": AXIS, "xtick.labelcolor": INK_2,
        "ytick.labelcolor": INK_2, "lines.linewidth": 2.0, "lines.solid_capstyle": "round",
        "lines.markersize": 6, "legend.frameon": False, "figure.figsize": (8.0, 4.5),
        "font.size": 10, "axes.unicode_minus": False,
    })
    with contextlib.suppress(Exception):
        from matplotlib import font_manager

        names = {f.name for f in font_manager.fontManager.ttflist}
        cjk = next((n for n in CJK_FONTS if n in names), None)
        if cjk:
            rc["font.family"] = "sans-serif"
            rc["font.sans-serif"] = [cjk, "DejaVu Sans"]


def patch_pyplot(plt) -> None:
    def show(*_args, **_kwargs):
        if ACTIVE is not None:
            ACTIVE.capture_open_figures("plt.show", close=True)

    plt.show = show
    with contextlib.suppress(Exception):
        import matplotlib.figure

        def figure_show(self, *_args, **_kwargs):
            if ACTIVE is not None:
                ACTIVE.capture(self, via="fig.show")

        matplotlib.figure.Figure.show = figure_show


def patch_plotly_io(pio) -> None:
    def show(fig, *_args, **_kwargs):
        if ACTIVE is not None:
            ACTIVE.capture(fig, via="fig.show")

    pio.show = show
    if os.environ.get("LAB_STYLE") == "0":
        return
    with contextlib.suppress(Exception):
        axis = {"gridcolor": GRID, "linecolor": AXIS, "zerolinecolor": AXIS, "tickcolor": AXIS,
                "title": {"font": {"color": INK_2}}}
        pio.templates["lab"] = {"layout": {
            "colorway": PALETTE, "font": {"family": FONT_STACK, "color": INK_2, "size": 13},
            "title": {"x": 0.01, "xanchor": "left", "font": {"color": INK, "size": 16}},
            "paper_bgcolor": SURFACE, "plot_bgcolor": SURFACE, "xaxis": axis, "yaxis": axis,
            "legend": {"bgcolor": "rgba(0,0,0,0)"},
            "margin": {"t": 60, "l": 60, "r": 24, "b": 50}}}
        pio.templates.default = "plotly_white+lab"


HOOKS = {"polars": patch_polars, "duckdb": patch_duckdb, "matplotlib": style_matplotlib,
         "matplotlib.pyplot": patch_pyplot, "plotly.io": patch_plotly_io}


@contextlib.contextmanager
def paused():
    """Suspend recording inside labkit's own bookkeeping calls."""
    global ACTIVE
    saved, ACTIVE = ACTIVE, None
    try:
        yield
    finally:
        ACTIVE = saved


def install(state: RunState) -> PostImportFinder:
    global ACTIVE
    ACTIVE = state
    pending = {}
    for name, hook in HOOKS.items():
        if name in sys.modules:
            with contextlib.suppress(Exception):
                hook(sys.modules[name])
        else:
            pending[name] = hook
    finder = PostImportFinder(pending)
    sys.meta_path.insert(0, finder)
    return finder


def uninstall() -> None:
    """Stop recording and remove the import hook — every one `install` inserted, not only the one
    the caller got back: a cancel can arrive after the insertion and before `install` returns."""
    global ACTIVE
    ACTIVE = None
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, PostImportFinder)]
