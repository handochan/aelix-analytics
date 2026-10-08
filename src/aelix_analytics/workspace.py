"""The Workspace: static HTML generated from the project files (never edited by hand).

report/index.html        overview · charts · timeline · data · runs · context · evaluation
report/runs/<id>.html    one run: charts with plotted data, code, inputs (with freshness), outputs,
                         logs, environment, reproduction
report/data/<view>.html  one dataset: schema, statistics, preview, lineage
report/context.html      CONTEXT.md now and every recorded version with diffs

Pages use relative links only, so they work from file:// and inside a copied project.
"""

from __future__ import annotations

import contextlib
import difflib
import functools
import hashlib
import html
import json
import os
import shutil
from collections import Counter, defaultdict
from importlib import resources
from pathlib import Path
from urllib.parse import quote

from . import _platform, core, relations

ASSETS = resources.files(__package__) / "assets"
# The names the Workspace's own assets had before kit 0.6.0 (workspace.css and workspace.js since):
# a build removes them from a Workspace an earlier kit rendered.
OLD_ASSETS = ("report.css", "report.js")
PLOTLY_EMBED_LIMIT = 3_000_000
LOG_LIMIT = 200_000
# A run over hundreds of files would otherwise bury the timeline and its own page in paths.
REF_LIMIT = 12
FILE_ROW_LIMIT = 60
# The Workspace's own words are English (TERMINOLOGY 7); what people and scripts wrote is shown as
# written. An event kind's label: the stored kind stays the machine value (`gap`, `todo`).
KIND_LABEL = {"project": "Project", "import": "Import", "run": "Run", "context": "Context",
              "finding": "Finding", "decision": "Decision", "assumption": "Assumption",
              "question": "Question", "todo": "Next step", "note": "Note",
              "gap": "Workflow feedback", "resolve": "Resolution",
              # analytics.event/2's kinds (ADR-0006 1.2): `workflow_feedback` is what `gap` is.
              "workflow_feedback": "Workflow feedback", "analysis_focus": "Analysis focus",
              "metric_definition": "Metric definition", "metric_observation": "Metric observation",
              "record_update": "Record update", "checkpoint": "Analysis checkpoint",
              "reuse_proposal": "Reuse proposal", "reuse_notification": "Reuse notification",
              "reuse_mode": "Reuse mode", "report_revision": "Report revision",
              "publication": "Publication", "publish_receipt": "Publish receipt"}
STATUS = {"ok": ("good", "✓", "Succeeded"), "error": ("critical", "✕", "Error"),
          "timeout": ("serious", "⏱", "Timed out"), "cancelled": ("warning", "■", "Cancelled"),
          "incomplete": ("muted", "…", "Incomplete"), "running": ("muted", "…", "Running")}
INPUT_STATE = {"same": ("good", "✓", "Same"), "changed": ("critical", "⚠", "Changed"),
               "missing": ("critical", "⚠", "Missing"), "unknown": ("muted", "?", "Unknown")}
# A rerun comparison's verdict as the Workspace says it (TERMINOLOGY 6); the record keeps the value.
VERDICT = {"match": "Matched", "mismatch": "Different", "failed": "Run failed",
           "inconclusive": "Inconclusive"}
# A reference's state as the read judges it (ADR-0006 4), said in words and not only in colour.
REF_STATE = {"resolved": ("good", "Resolved"), "content_changed": ("critical", "Content changed"),
             "missing": ("critical", "Missing"), "version_unbound": ("muted", "Version unbound"),
             "ambiguous": ("warning", "Ambiguous"), "unknown": ("muted", "Unknown"),
             "unsupported": ("muted", "Unsupported")}
LATEST = "latest, not the cited version"
# The current state of a file a run recorded as its output.
OUTPUT_STATE = {"resolved": ("good", "✓", "Same as recorded"),
                "content_changed": ("critical", "⚠",
                                    "Overwritten since this run — this run's bytes are not preserved"),
                "missing": ("critical", "⚠", "Missing"),
                "unknown": ("muted", "?", "Unknown (no hash recorded)")}
RECORDING_REASON = {"not_final": "the run record is not final (it is running, or stopped while running)",
                    "no_completion_event": "the journal has no completion event for this run",
                    "finalize_errors": "some recording steps failed"}
SHOWN_CANDIDATES = 3
# The pages are lang="en" (the kit's words). What people and scripts wrote is in a language the
# records do not name, so its element says "unknown" (lang=""): a screen reader reads it in the
# reader's own voice rather than an English one, and nothing guesses the language (TERMINOLOGY 7).
USER = ' lang=""'


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def href(path: str) -> str:
    return quote(path, safe="/#:?=&%.-_~")


def short(sha, n=8) -> str:
    return (sha or "")[:n]


def fmt_bytes(n) -> str:
    """A size in units of 1024, named as such (KiB, MiB, GiB)."""
    if not isinstance(n, (int, float)):
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


def fmt_num(n) -> str:
    if isinstance(n, bool) or n is None:
        return "-" if n is None else str(n)
    if isinstance(n, int):
        return f"{n:,}"
    if isinstance(n, float):
        return f"{n:,.4g}" if abs(n) < 1e6 else f"{n:,.0f}"
    return esc(n)


def fmt_time(ts, with_date=True) -> str:
    if not ts:
        return "-"
    text = str(ts).replace("T", " ")
    return text[:16] if with_date else text[11:16]


def badge(tone: str, icon: str, label: str, title: str | None = None) -> str:
    t = f' title="{esc(title)}"' if title else ""
    return f'<span class="badge tone-{tone}"{t}><span aria-hidden="true">{icon}</span> {esc(label)}</span>'


def status_badge(status) -> str:
    tone, icon, label = STATUS.get(status or "", ("muted", "?", str(status)))
    return badge(tone, icon, label)


def verdict_label(verdict) -> str:
    return VERDICT.get(verdict, str(verdict))


def dataset_label(name: str, view: str) -> str:
    """How the Workspace names a dataset: by its own name (its file's stem) as written. The SQL
    view spells that name in [a-z0-9_] (`My Sales` is `my_sales`, `123sales` is `t_123sales`, a
    Korean name `___`); where the two differ, the pages show the view beside the name."""
    return name or view


def user_text(value) -> str:
    """Text people or scripts wrote, inline in a sentence of the kit's (USER)."""
    return f"<span{USER}>{esc(value)}</span>"


def kind_chip(kind) -> str:
    return (f'<span class="chip kind-{esc(kind)}"><span class="dot" aria-hidden="true"></span>'
            f'{esc(KIND_LABEL.get(kind, kind))}</span>')


@functools.lru_cache(maxsize=4096)
def slug_key(key: str) -> str:
    """Anchor for a script::chart key: readable prefix plus a hash, so similar keys never collide.
    Kept per key: every unbound reference to a chart links its gallery card."""
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    return f"{core.slugify(key.replace('::', '--'), 60) or 'chart'}-{digest}"


class Site:
    """Everything a page needs, loaded once per build from one read of the records (core.read_model):
    the events, runs, datasets and the state of every reference the pages show come from it, so they
    carry the identifiers, as_of_seq and source_revision analytics_status gives for the same
    files."""

    def __init__(self, project: core.Project):
        self.p = project
        self.cache = core.HashCache(project)
        self.model = core.read_model(project, self.cache)
        journal = self.model.journal
        # lab.event/1 and analytics.event/2 records alike, in seq order (design 5.2).
        self.lists = core.JournalLists(journal)
        self.events = self.lists.events
        self.source_of = self.lists.source
        self.runs = core.model_runs(project, self.model)
        self.run_by_id = {r["id"]: r for r in self.runs}
        self.recording = {r["run_id"]: r for r in self.model.runs()}
        self.fresh = core.Freshness(project, self.runs, self.cache, self.model)
        self.datasets = core.list_datasets(project, self.runs, model=self.model,
                                           sha=self.cache.sha)
        # The timeline shows every event, so every reference is judged once, here.
        self.model.references()
        # The events that name each run in refs.runs, in journal order (a run page's journal).
        self.related: dict[str, list[dict]] = {}
        for event in self.events:
            runs = (event.get("refs") or {}).get("runs") or []
            for run_id in dict.fromkeys(r for r in (runs if isinstance(runs, list) else [runs])
                                        if isinstance(r, str)):
                self.related.setdefault(run_id, []).append(event)
        self.views = core.view_names(self.datasets)
        self.view_by_path = {path: name for name, path in self.views.items()}
        self.name_by_path = {ds["path"]: ds["name"] for ds in self.datasets}
        self.charts = core.chart_index(self.runs, self.model.read_as)
        # Each run's script as the gallery reads it (an older kit's spelling as today's).
        self.script_of = {r["id"]: self.script_path(r) for r in self.runs}
        self.resolved = core.resolved_targets(self.events)
        # The tool log of this kit's state directory, and the new format's after it (design 5.3).
        self.telemetry = [row for place in (project.telemetry,
                                            project.root / ".analytics" / "telemetry.jsonl")
                          for row in core.read_jsonl(place)]
        self.queries = core.read_jsonl(project.queries)
        self.versions = core.context_versions(project)
        self.build = core.now().strftime("%Y%m%d-%H%M%S-%f")
        self.built_at = core.iso()
        self.uses_plotly = False
        self._fig = 0

    # --- links (depth 0 = report/, 1 = report/runs|data/) ---

    def up(self, depth: int) -> str:
        return "../" * (depth + 1)

    def root(self, depth: int) -> str:
        return "../" * depth

    def file_link(self, rel: str, depth: int) -> str:
        if not rel:
            return "#"
        # A path recorded as absolute is absolute on the platform that recorded it. os.path.isabs
        # answers for the platform rebuilding the report, so a Windows path read on Linux would be
        # linked as if it sat inside report/.
        if _platform.absolute_record_path(rel):
            # A record is data: a path no link can name (a device path, a UNC path without a
            # share) gets no link rather than stopping the build.
            try:
                return _platform.file_url(rel)
            except ValueError:
                return "#"
        return self.up(depth) + href(rel)

    def run_link(self, run_id: str, depth: int, anchor: str = "") -> str:
        return f"{self.root(depth)}runs/{href(run_id)}.html{anchor}"

    def data_link(self, path: str, depth: int) -> str | None:
        view = self.view_by_path.get(path)
        return f"{self.root(depth)}data/{href(view)}.html" if view else None

    def chart_anchor(self, key: str, depth: int) -> str:
        """The gallery card of a logical key: the key an older kit's run recorded in another
        spelling of its script is today's (ReadModel.chart_key), where its card is."""
        return f"{self.root(depth)}index.html#chart-{slug_key(self.model.chart_key(key))}"

    def script_path(self, run: dict) -> str | None:
        """A run's script path as the gallery reads it: an older kit's spelling as today's
        (ReadModel.read_as), else as recorded."""
        recorded = (run.get("script") or {}).get("path")
        return self.model.read_as(run.get("id"), recorded) or recorded

    def chart_key(self, run: dict, chart: dict) -> str:
        """A chart's logical key as the gallery reads it (core.chart_index with the model)."""
        return f"{self.script_path(run) or '?'}::{chart.get('id')}"

    def read_as_html(self, run_id: str, path, depth: int) -> str:
        """What a path an older kit's run recorded is read as, when it is read as another identity
        path (ADR-0006 9): an inference, said so, linked to the dataset it names."""
        read = self.model.read_as(run_id, path)
        if not read:
            return ""
        link = self.data_link(read, depth)
        shown = f"<code{USER}>{esc(read)}</code>"
        if link:
            shown = f'<a href="{link}">{shown}</a>'
        return (f' <small class="ref-note" title="{esc(INFERRED_TITLE)}">read as {shown} — '
                f'{esc(core.INFERRED_NOTE)}</small>')

    def dataset_name(self, path: str) -> str | None:
        """The name the Workspace shows for the dataset at `path` (dataset_label), if it is one."""
        view = self.view_by_path.get(path)
        return dataset_label(self.name_by_path.get(path) or view, view) if view else None

    def path_html(self, path: str, depth: int, note: str = "") -> str:
        link = self.data_link(path, depth)
        label = self.dataset_name(path) or path
        if link:
            title = f"{path} — {note}" if note else path
            return f'<a href="{link}" title="{esc(title)}"{USER}>{esc(label)}</a>'
        title = f' title="{esc(note)}"' if note else ""
        return f'<a href="{self.file_link(path, depth)}"{title}{USER}>{esc(path)}</a>'

    def source(self, event: dict) -> str | None:
        """How the read names an event: its id, or `journal:<line>` when several lines have it."""
        return self.source_of.get(id(event), event.get("id"))

    def anchor(self, event: dict) -> str:
        """An event's anchor on the timeline: its id, or `journal-<line>` when several lines have it."""
        source = self.source(event)
        return event.get("id") if not source or source == event.get("id") else source.replace(":", "-")

    def refs_html(self, event: dict, depth: int) -> str:
        """An event's references as the read judges them (ADR-0006 4), the same judgments
        analytics_status counts. A field that names many targets shows REF_LIMIT of them and how
        many more; a target left unbound links the latest version and says it is not the cited
        one."""
        items, hidden = [], 0
        groups: dict = {}
        for judged in self.model.references(self.source(event) or ""):
            groups.setdefault((judged.get("ref") or {}).get("field"), []).append(judged)
        for field, group in groups.items():
            shown = group if field in ("runs", "events", "resolves", "context") else group[:REF_LIMIT]
            hidden += len(group) - len(shown)
            if any(j["state"] == "version_unbound" for j in shown):
                items.append(f'<span class="ref-label" title="the record names no version: the link '
                             f'is the {LATEST}">Version unbound</span>')
            items += [self.ref_html(judged, depth) for judged in shown]
        if hidden:
            items.append(f'<span class="ref muted" title="The run page lists them all">'
                         f'{hidden:,} more</span>')
        return f'<div class="refs">{"".join(items)}</div>' if items else ""

    def ref_html(self, judged: dict, depth: int) -> str:
        """One reference: its target by the identifier analytics_status uses, linked when the read
        found it, and its state in words unless it is resolved or (said once for its field)
        unbound."""
        kind, state = judged.get("type"), judged["state"]
        target, label = judged.get("target") or {}, core.reference_label(judged)
        found = state == "resolved"
        field = (judged.get("ref") or {}).get("field")
        if kind == "run":
            body = (f'<a href="{esc(self.run_link(label, depth))}">run {esc(label)}</a>' if found
                    else f"run {esc(label)}")
        elif kind == "visualization_version" and judged.get("version_binding") != "none":
            run, chart = target.get("run_id"), target.get("visualization_id")
            anchor = f"#chart-{href(str(chart))}"
            body = (f'<a href="{esc(self.run_link(run, depth, anchor))}" title="{esc(label)}">'
                    f"chart {esc(chart)}</a>" if found and run else f"chart {esc(label)}")
        elif kind == "visualization_version":
            key = str(target.get("key") or target.get("token") or label)
            body = (f'<a href="{esc(self.chart_anchor(key, depth))}" title="{LATEST}">'
                    f"chart {esc(key)}</a>")
        elif kind in ("data_observation", "dataset_import"):
            item = str(target.get("key") or target.get("path") or label)
            path = item if item in self.view_by_path else self.views.get(item)
            # A name or path that is no dataset is still what people and scripts wrote (lang="").
            body = self.path_html(path, depth, LATEST) if path else user_text(item)
        elif kind == "event":
            prefix = "Resolves: " if field == "resolves" else ""
            body = (f'<a href="{esc(self.root(depth))}index.html#{esc(label)}">{prefix}{esc(label)}</a>'
                    if found else f"{prefix}{esc(label)}")
        elif kind == "context_version" and found:
            body = (f'<a href="{esc(self.root(depth))}context.html#v-{esc(Path(label).stem)}" '
                    f'title="{esc(label)}">View version</a>')
        else:
            body = esc(label)
        candidates = judged.get("candidates")
        if candidates and state in ("version_unbound", "ambiguous"):
            # An event target's candidates are the journal lines with its id; the others' are
            # versions.
            what = "record(s)" if kind in relations.EVENT_TARGETS else "version(s)"
            inferred = judged.get("inferred_candidates") or []
            body += (f' <small class="ref-note" title="{esc(", ".join(candidates))}">'
                     f"{len(candidates):,} candidate {what}</small>")
            if inferred:
                # The versions hold Visualization ids people and scripts wrote: only their count
                # beside the kit's English title (#10, lang="").
                body += (f' <small class="ref-note" title="{esc(INFERRED_TITLE)}">'
                         f'{len(inferred):,} {esc(core.INFERRED_NOTE)}</small>')
        if state not in ("resolved", "version_unbound"):
            tone, words = REF_STATE.get(state, ("muted", state))
            reason = f": {judged['reason']}" if judged.get("reason") else ""
            body += f' <small class="ref-state tone-{tone}">{esc(words + reason)}</small>'
        return f'<span class="ref ref-{esc(state)}">{body}</span>'

    def closed_badge(self, resolver: dict) -> str:
        """The badge of a record another one closes by refs.resolves, as the lists select (C1):
        resolved only when the read finds that reference, and otherwise the state it judges, in
        words (a forward refs.resolves is `missing`, record_absent)."""
        source = self.source(resolver) or ""
        link = f"#{esc(self.anchor(resolver))}"
        judged = next((j for j in self.model.references(source)
                       if (j.get("ref") or {}).get("field") == "resolves"), None)
        if judged is not None and judged["state"] == "resolved":
            return f' <a class="badge tone-good" href="{link}">✓ Resolved by {esc(source)}</a>'
        state = judged["state"] if judged is not None else "unknown"
        tone, words = REF_STATE.get(state, ("muted", state))
        reason = f": {judged['reason']}" if judged is not None and judged.get("reason") else ""
        return (f' <a class="badge tone-{tone}" href="{link}" title="the lists take this record as '
                f'closed by {esc(source)} (refs.resolves), and the read judges that reference '
                f'{esc(state)}">closed by {esc(source)} · {esc(words + reason)}</a>')

    def read_footer(self) -> str:
        """The read every page was made from, as analytics_status names it."""
        text = (f"as_of_seq {self.model.as_of_seq} · source_revision {self.model.source_revision}")
        if self.model.updating:
            text += " · updating: the records changed while they were read"
        return text

    def producer_html(self, candidates: list[str], depth: int) -> str:
        links = [f'<a href="{esc(self.run_link(r, depth))}">{esc(r)}</a>'
                 for r in candidates[:SHOWN_CANDIDATES]]
        more = len(candidates) - SHOWN_CANDIDATES
        return ", ".join(links) + (f" and {more:,} more" if more > 0 else "")

    # --- charts ---

    def chart_html(self, chart: dict, depth: int, *, lazy=True) -> str:
        files = chart.get("files") or {}
        title = esc(chart.get("title") or chart.get("id"))
        if files.get("png") or any(k in files for k in ("jpg", "jpeg", "svg", "gif", "webp")):
            src = files.get("png") or next(files[k] for k in ("svg", "jpg", "jpeg", "gif", "webp")
                                           if k in files)
            loading = ' loading="lazy"' if lazy else ""
            return (f'<a class="chart-img" href="{self.file_link(src, depth)}">'
                    f'<img src="{self.file_link(src, depth)}" alt="{title}"{USER}{loading}></a>')
        if files.get("plotly"):
            path = self.p.path(files["plotly"])
            try:
                size = path.stat().st_size
            except OSError:
                return '<div class="empty">The chart file is missing.</div>'
            if size > PLOTLY_EMBED_LIMIT:
                return (f'<div class="empty">This plotly figure ({fmt_bytes(size)}) is too large to '
                        f'embed in this page. <a href="{self.file_link(files["plotly"], depth)}">Open '
                        'the JSON</a></div>')
            self.uses_plotly = True
            self._fig += 1
            payload = path.read_text(encoding="utf-8").replace("</", "<\\/")
            return (f'<div class="plotly-chart" data-fig="fig{self._fig}" role="img" '
                    f'aria-label="{title}"{USER}></div><script type="application/json" '
                    f'id="fig{self._fig}">{payload}</script>')
        if files.get("html"):
            return (f'<iframe class="chart-frame" src="{self.file_link(files["html"], depth)}" '
                    f'loading="lazy" title="{title}"{USER}></iframe>')
        return '<div class="empty">No file to show for this visualization.</div>'

    def chart_card(self, chart: dict, depth: int, *, full=False) -> str:
        run = self.run_by_id.get(chart["run"]) or {}
        fresh = self.fresh.of(chart["run"])
        script = run.get("script") or {}
        badges = []
        badges += stale_badges(fresh)
        badges += producer_badges(fresh)
        if fresh["code"] == "changed":
            badges.append(badge("warning", "✎", "Script edited",
                                "The script file changed after this visualization was made"))
            if fresh.get("code_reason") == core.LINE_ENDINGS_ONLY:
                badges.append(line_endings_badge())
        elif fresh["code"] == "missing":
            badges.append(badge("warning", "✎", "Script missing",
                                "The script that made this visualization is no longer there "
                                "(renamed or deleted)"))
        versions = chart.get("versions") or []
        if len(versions) > 1:
            badges.append(f'<span class="badge tone-muted">v{len(versions)}</span>')
        if chart.get("inferred_versions"):
            badges.append(inferred_badge(
                f"{chart['inferred_versions']:,} of the {len(versions):,} version(s) of this key were "
                "recorded under another spelling of the script's path"))
        if chart.get("retired"):
            badges.append(badge("muted", "↺", "Absent from latest run"))
        run_page = self.run_link(chart["run"], depth)
        line = chart.get("line")
        code_link = (f'<a href="{run_page}#code"{USER}>{esc(script.get("path"))}</a> '
                     f'<span class="sha">@{short(script.get("sha256"))}</span>')
        if line:
            code_link += f' · <a href="{run_page}#L-main-{line}">L{line}</a>'
        inputs = []
        for entry in fresh["inputs"]:
            tone, icon, label = INPUT_STATE.get(entry["state"], INPUT_STATE["unknown"])
            reason = (f" {line_endings_badge()}" if entry.get("reason") == core.LINE_ENDINGS_ONLY
                      else "")
            inputs.append(f'<span class="input">{self.path_html(entry["path"], depth)} '
                          f'<span class="sha">{short(entry.get("sha256"))}</span>'
                          f'<span class="state tone-{tone}" title="File now: {esc(label)}">{icon}</span>'
                          f'{reason}</span>')
        if chart.get("data"):
            data_html = (f'<a href="{run_page}#chart-{esc(chart["id"])}-data">'
                         f'{fmt_num(chart.get("data_rows"))} rows · '
                         f'{len(chart.get("data_columns") or [])} columns</a>'
                         f' · <a href="{self.file_link(chart["data"], depth)}">parquet</a>')
        elif chart.get("kind") == "plotly":
            data_html = '<span class="muted">In the plotly figure JSON</span>'
        else:
            data_html = '<span class="muted">Not recorded — analytics.visualize(..., data=df)</span>'
        version_links = ""
        if full and len(versions) > 1:
            version_links = ('<div class="versions">Earlier versions: ' + " · ".join(
                f'<a href="{self.run_link(v["run"], depth)}">{fmt_time(v.get("started_at"))}</a>'
                + (f' <small title="{esc(INFERRED_TITLE)}">(inferred)</small>' if v.get("inferred") else "")
                for v in versions[-2::-1][:8]) + "</div>")
        classes = "chart-card" + (" is-stale" if fresh["stale"] else "") + \
            (" is-retired" if chart.get("retired") else "")
        caption = (f'<p class="caption"{USER}>{esc(chart["caption"])}</p>' if chart.get("caption")
                   else "")
        return f"""
<article class="{classes}" id="chart-{slug_key(chart['key'])}">
  <header><h3{USER}>{esc(chart.get('title') or chart['id'])}</h3><div class="badges">{''.join(badges)}</div></header>
  <div class="chart-body">{self.chart_html(chart, depth)}</div>
  {caption}
  <dl class="prov">
    <div><dt>Code</dt><dd>{code_link}</dd></div>
    <div><dt>Input data</dt><dd>{''.join(inputs) or '<span class="muted">No inputs detected</span>'}</dd></div>
    <div><dt>Attached data</dt><dd>{data_html}</dd></div>
    <div><dt>Run</dt><dd><a href="{run_page}">{esc(chart['run'])}</a> · {fmt_time(run.get('started_at'))} · {run.get('duration_s', '-')}s</dd></div>
  </dl>
  {version_links}
</article>"""

    # --- tables ---

    def table(self, columns, rows, *, types=None, klass="data", data_head=True) -> str:
        """A table of data: its text cells, and its column names when they are the data's
        (`data_head`), are the data's text (USER); `null` and numbers are the kit's."""
        lang = USER if data_head else ""
        head = "".join(
            f'<th{lang}>{esc(c)}{f"<small>{esc(types[i])}</small>" if types else ""}</th>'
            for i, c in enumerate(columns))
        body = []
        for row in rows:
            cells = []
            for v in row:
                if v is None:
                    cells.append('<td class="null">null</td>')
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    cells.append(f'<td class="num">{fmt_num(v)}</td>')
                else:
                    cells.append(f"<td{USER}>{esc(v)}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        return (f'<div class="table-wrap"><table class="{klass}"><thead><tr>{head}</tr></thead>'
                f'<tbody>{"".join(body)}</tbody></table></div>')

    def frame_preview(self, rel: str, limit=100) -> str:
        with contextlib.suppress(Exception):
            import polars as pl

            from .data import cell

            lf = pl.scan_parquet(self.p.path(rel))
            schema = lf.collect_schema()
            frame = lf.head(limit).collect()
            total = lf.select(pl.len()).collect().item()
            note = (f'<p class="muted">First {min(limit, total):,} of {fmt_num(total)} rows</p>'
                    if total > limit else "")
            return note + self.table(frame.columns, [[cell(v, 120) for v in r] for r in frame.rows()],
                                     types=[str(t) for t in schema.values()])
        return '<div class="empty">Could not read the data.</div>'

    # --- events ---

    def events_html(self, events, depth: int, *, filters=False) -> str:
        if not events:
            return '<div class="empty">No journal entries yet.</div>'
        out, day = [], None
        for ev in events:
            ts = str(ev.get("ts") or "")
            if ts[:10] != day:
                day = ts[:10]
                out.append(f'<li class="day">{esc(day)}</li>')
            resolved = self.resolved.get(ev.get("id"))
            state = ""
            if ev.get("kind") in core.OPEN_KINDS + core.KEY_KINDS and resolved:
                state = self.closed_badge(resolved)
            elif ev.get("kind") in core.OPEN_KINDS:
                state = ' <span class="badge tone-warning">● Open</span>'
            # A record whose id other lines share is named by its line, and so is its anchor.
            source, anchor, shown = self.source(ev), self.anchor(ev), esc(ev.get("id"))
            if anchor != ev.get("id"):
                shown += f' <small title="several journal lines have this id">{esc(source)}</small>'
            out.append(
                f'<li class="ev" id="{esc(anchor)}" data-kind="{esc(ev.get("kind"))}">'
                f'<div class="ev-head"><time>{fmt_time(ts, with_date=False)}</time>{kind_chip(ev.get("kind"))}'
                f'<span class="ev-id">{shown}</span><span class="actor">{esc(ev.get("actor"))}</span>{state}</div>'
                f'<div class="ev-text"{USER}>{esc(ev.get("text"))}</div>{self.refs_html(ev, depth)}</li>')
        chips = ""
        if filters:
            counts = Counter(e.get("kind") for e in events)
            chips = '<div class="filters" role="group" aria-label="Filter by kind"><button class="on" data-kind="">All</button>' + "".join(
                f'<button data-kind="{esc(k)}">{esc(KIND_LABEL.get(k, k))} {n}</button>'
                for k, n in sorted(counts.items(), key=lambda kv: core.EVENT_KINDS.index(kv[0])
                                   if kv[0] in core.EVENT_KINDS else 99)) + "</div>"
        return f'{chips}<ol class="timeline">{"".join(out)}</ol>'

    # --- page shell ---

    def page(self, depth: int, title: str, crumb: str, body: str, kind: str) -> str:
        root = self.root(depth)
        v = f"?v={self.build}"  # new URLs per build, so a reload never runs cached assets
        plotly = (f'<script src="{root}assets/plotly.min.js?v={core.KIT_VERSION}"></script>'
                  if self.uses_plotly else "")
        self.uses_plotly = False
        return f"""<!doctype html>
<html lang="en" data-root="{root}" data-build="{self.build}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · {esc(self.p.title())}</title>
<link rel="stylesheet" href="{root}assets/workspace.css{v}">
<link rel="stylesheet" href="{root}assets/code.css{v}">
</head>
<body class="page-{kind}">
<header class="topbar">
  <div class="crumbs"><a href="{root}index.html"{USER}>{esc(self.p.title())}</a>{crumb}</div>
  <div class="build">Rendered {fmt_time(self.built_at)} · <label><input type="checkbox" id="live" checked> Auto-reload</label></div>
</header>
<main>{body}</main>
<footer>Aelix Analytics {core.KIT_VERSION} · Generated from the run records, the journal and CONTEXT.md — do not edit by hand. · <span class="read-basis">Read at {esc(self.read_footer())}</span></footer>
{plotly}
<script src="{root}assets/workspace.js{v}"></script>
</body>
</html>
"""


def fingerprint(project: core.Project) -> str:
    parts = []
    for path in (*project.markers, project.context, project.journal, project.telemetry,
                 project.queries):
        with contextlib.suppress(OSError):
            st = path.stat()
            parts.append([path.name, st.st_size, st.st_mtime_ns])
    if project.runs.is_dir():
        for dirpath, _dirs, files in os.walk(project.runs):
            for name in files:
                if name != "run.json":
                    continue
                with contextlib.suppress(OSError):
                    st = os.stat(os.path.join(dirpath, name))
                    parts.append([os.path.join(dirpath, name), st.st_size, st.st_mtime_ns])
    # data/ as the datasets are listed: the namespace walk from it (core.Project.walk).
    for rel, real, _name, _entry, _place in project.walk(start="data"):
        with contextlib.suppress(OSError):
            st = os.stat(real)
            parts.append([rel, st.st_size, st.st_mtime_ns])
    from .runner import kit_sha

    parts.append(kit_sha())
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def build(project: core.Project, *, if_changed: bool = False) -> dict:
    """Build the Workspace. Builds are serialized per project; a waiting --if-changed build rechecks
    the fingerprint after the lock, so queued triggers collapse into one build. A write: refused
    before its lock file is made where this kit does not write (core.admit, Issue #13)."""
    core.admit(project, "render")
    with project.platform.locks.exclusive(project.state_dir / "report.lock"):
        return _build_locked(project, if_changed)


def _build_locked(project: core.Project, if_changed: bool) -> dict:
    stamp_file = project.cache / "report-fingerprint"
    current = fingerprint(project)
    index = project.workspace_dir / "index.html"
    if if_changed and index.is_file():
        with contextlib.suppress(OSError):
            if stamp_file.read_text(encoding="utf-8").strip() == current:
                return {"path": str(index), "skipped": True}
    site = Site(project)
    out = project.workspace_dir
    (out / "runs").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    _write_assets(site, out)
    pages = 0
    for run in site.runs:
        _write(out / "runs" / f"{run['id']}.html", run_page(site, run), project.platform)
        pages += 1
    for ds in site.datasets:
        view = site.view_by_path.get(ds["path"])
        if view:
            _write(out / "data" / f"{view}.html", dataset_page(site, ds, view), project.platform)
            pages += 1
    _write(out / "context.html", context_page(site), project.platform)
    _write(index, index_page(site), project.platform)
    pages += 2
    _prune(out / "runs", {f"{r['id']}.html" for r in site.runs})
    _prune(out / "data", {f"{v}.html" for v in site.views})
    for name in OLD_ASSETS:  # after the pages: none of them loads these any more
        with contextlib.suppress(OSError):
            (out / "assets" / name).unlink()
    # The old global too: a page an earlier kit made, left open, polls this file for the name it
    # knows (__LAB_BUILD__ before kit 0.6.0) and so still reloads itself into the new page.
    core.write_text(out / "assets" / "build.js",
                    f'window.__LAB_BUILD__ = window.__ANALYTICS_BUILD__ = "{site.build}";\n')
    site.cache.save()
    project.cache.mkdir(parents=True, exist_ok=True)
    core.write_text(stamp_file, current)
    return {"path": str(index), "pages": pages, "build": site.build}


def _write(path: Path, text: str, platform: _platform.Platform) -> None:
    """A Workspace page: text the kit writes, UTF-8 and LF on every platform, inside as well — a CR
    from a record's text or a run's log is read as LF by a browser anyway (ADR-0007 decision 2)."""
    tmp = path.with_name(f".{path.name}.tmp")
    core.write_text(tmp, core.newlines_as_lf(text))
    platform.files.replace(tmp, path)


def _prune(directory: Path, keep: set[str]) -> None:
    for p in directory.glob("*.html"):
        if p.name not in keep:
            with contextlib.suppress(OSError):
                p.unlink()


def _write_assets(site: Site, out: Path) -> None:
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for name in ("workspace.css", "workspace.js"):
        with resources.as_file(ASSETS / name) as source:
            shutil.copyfile(source, assets / name)
    code_css = assets / "code.css"
    with resources.as_file(ASSETS / "workspace.css") as css:
        stale = not code_css.is_file() or code_css.stat().st_mtime < css.stat().st_mtime
    if stale:
        with contextlib.suppress(Exception):
            from pygments.formatters import HtmlFormatter

            light = HtmlFormatter(style="default").get_style_defs(".code")
            dark = HtmlFormatter(style="github-dark").get_style_defs(".code")
            core.write_text(code_css, f"{light}\n@media (prefers-color-scheme: dark) {{\n{dark}\n}}\n")
    if any((c.get("files") or {}).get("plotly") for r in site.runs
           for c in relations.run_visualizations(r)):
        with contextlib.suppress(Exception):
            src = resources.files("plotly") / "package_data" / "plotly.min.js"
            dest = assets / "plotly.min.js"
            data = src.read_bytes()
            if not dest.is_file() or dest.stat().st_size != len(data):
                dest.write_bytes(data)


# --- relations as the read judges them ---

SHOWN_PLACES = 20


def _places(items: list[str]) -> str:
    more = len(items) - SHOWN_PLACES
    return ", ".join(items[:SHOWN_PLACES]) + (f" and {more:,} more" if more > 0 else "")


def records_notice(site: Site) -> str:
    """What the pages cannot show, said where the overview starts: records this kit cannot read,
    lines that do not parse (and the records recovered from them), record files not read, ids
    several lines share, and a read that kept changing."""
    m = site.model
    items = []
    if m.unsupported:
        places = [f"{u['location']} ({u['reason']})" for u in m.unsupported]
        # Only a known family's newer version is one a newer kit may read.
        newer = sum(1 for u in m.unsupported if u["reason"] == "unknown_version")
        later = (" A newer kit may read them." if newer == len(places) else
                 f" A newer kit may read the {newer:,} of an unknown version." if newer else "")
        items.append(f"<li><strong>Unsupported: {len(places):,} record(s) this kit cannot read</strong> "
                     f"are in no list of this report — {esc(_places(places))}.{later}</li>")
    journal = m.journal
    if journal.damaged:
        recovered = (f"; the record a later write appended onto {len(journal.recovered):,} of them is "
                     f"read ({esc(_places([f'journal:{n}' for n in journal.recovered]))})"
                     if journal.recovered else "")
        items.append(f"<li><strong>Damaged: {len(journal.damaged):,} journal line(s) do not parse</strong> "
                     f"({esc(_places([f'journal:{n}' for n in journal.damaged]))}){recovered}.</li>")
    if m.damaged_files:
        places = [f"{f['location']} ({f['reason']})" for f in m.damaged_files]
        items.append(f"<li><strong>Damaged: {len(places):,} record file(s) not read</strong> — "
                     f"{esc(_places(places))}.</li>")
    shared = {i: [f"journal:{n}" for n in lines] for i, lines in journal.lines_of.items()
              if len(lines) > 1}
    if shared:
        listed, cut = core.shared_ids_text(shared, SHOWN_PLACES)
        rest = (" (the kit's <code>status</code> command lists every line in its JSON "
                "<code>shared_ids</code>)" if cut else "")
        items.append(f"<li><strong>Ids on several lines:</strong> {esc(listed)}{rest} — each line is "
                     "its own record, and a reference to the id is ambiguous.</li>")
    if m.updating:
        items.append("<li><strong>Updating:</strong> the records changed while they were read; this "
                     "report shows the last of three reads.</li>")
    if not items:
        return ""
    return ('<div class="callout tone-warning"><strong>Records this report does not show as they '
            f'are</strong><ul>{"".join(items)}</ul></div>')


# What a stale producer candidate upstream means for the run that read its bytes (ADR-0006 4).
UPSTREAM_STALE = ("An input has the same bytes as the output of run(s) {runs}, whose inputs (or those "
                  "of a run before them) changed: producer candidates, not confirmed links.")


# A changed file whose bytes differ from the record only in their line ends (ADR-0007 decision 4).
LINE_ENDINGS_TITLE = ("Only the line endings differ from the recorded bytes (CRLF/LF); the hash "
                      "compares bytes, so it still counts as changed")


def line_endings_badge() -> str:
    return badge("muted", "↵", "Line endings only", LINE_ENDINGS_TITLE)


# A relation found only by reading a path a kit before 0.5.0 recorded as today's identity path
# (ADR-0006 9): an inference from today's links and names, never an observed or confirmed link.
INFERRED_TITLE = (f"A kit before {core.PATH_IDENTITY_SINCE} recorded this path by its real place or "
                  "in another spelling; it is read as today's path of the same file. An inference, "
                  "not an observed or confirmed link; the records are as they were written.")


def inferred_badge(what: str = "") -> str:
    """The mark of a relation found only through an older kit's path, `what` saying which."""
    title = f"{what}: {INFERRED_TITLE}" if what else INFERRED_TITLE
    return badge("muted", "≈", "Inferred from an older kit's path", title)


def inferred_mark(run_id: str, runs) -> str:
    """` · inferred from an older kit's path` beside a run listed only through such a reading."""
    if not runs or run_id not in runs:
        return ""
    return f' · <span class="ref-note" title="{esc(INFERRED_TITLE)}">{esc(core.INFERRED_NOTE)}</span>'


def inferred_count_html(runs) -> str:
    """` (N inferred)` for the runs of a count found only through an older kit's path."""
    if not runs:
        return ""
    return (f' <small class="ref-note" title="{esc(", ".join(runs))}: {esc(INFERRED_TITLE)}">'
            f'({len(runs):,} inferred)</small>')


def inferred_runs_note(runs) -> str:
    """` (A, B: inferred from an older kit's path)` for the runs of a list found only so."""
    if not runs:
        return ""
    more = len(runs) - SHOWN_CANDIDATES
    named = ", ".join(runs[:SHOWN_CANDIDATES]) + (f" and {more:,} more" if more > 0 else "")
    return (f' <small class="ref-note" title="{esc(INFERRED_TITLE)}">({esc(named)}: '
            f'{esc(core.INFERRED_NOTE)})</small>')


def changed_inputs_text(fresh: dict) -> list[str]:
    """A run's changed or missing inputs, each that differs only in its line ends saying so."""
    reasons = fresh.get("change_reasons") or {}
    return [f"{path} {core.LINE_ENDINGS_NOTE}" if reasons.get(path) == core.LINE_ENDINGS_ONLY
            else path for path in fresh["changed_inputs"]]


def stale_badges(fresh: dict) -> list[str]:
    """Why a run's charts may be out of date: an input of its own changed or is missing, or a run that
    output the same bytes as one of its inputs is stale. That run is a producer candidate, so the
    badge says so and does not say this run's input changed."""
    out = []
    if fresh["changed_inputs"]:
        out.append(badge("critical", "⚠", "Input changed", "; ".join(changed_inputs_text(fresh))))
    if fresh["stale_upstream"]:
        runs = fresh["stale_upstream"]
        more = len(runs) - SHOWN_CANDIDATES
        named = ", ".join(runs[:SHOWN_CANDIDATES]) + (f" and {more:,} more" if more > 0 else "")
        inferred = fresh.get("inferred_upstream") or []
        more_inferred = len(inferred) - SHOWN_CANDIDATES
        said = (f" ({', '.join(inferred[:SHOWN_CANDIDATES])}"
                + (f" and {more_inferred:,} more" if more_inferred > 0 else "")
                + f": {core.INFERRED_NOTE})" if inferred else "")
        out.append(badge("warning", "⚠", "Upstream candidate stale",
                         UPSTREAM_STALE.format(runs=named) + said))
    return out


def producer_badges(fresh: dict) -> list[str]:
    """Badges for a run whose inputs several runs could have output, or whose producers cycle."""
    out = []
    if fresh.get("ambiguous_inputs"):
        paths = fresh["ambiguous_inputs"]
        out.append(badge("warning", "?", "Producer ambiguous",
                         f"several successful runs output the same bytes as {len(paths):,} input(s): "
                         + ", ".join(paths[:SHOWN_CANDIDATES])
                         + (f" and {len(paths) - SHOWN_CANDIDATES:,} more" if len(paths) > SHOWN_CANDIDATES else "")))
    if fresh.get("cycle"):
        out.append(badge("warning", "↻", "Producer cycle",
                         "following the runs that output these inputs comes back to a run on the way"))
    if fresh.get("inferred_inputs"):
        # The paths are what people and scripts wrote, so they are not in the kit's title: the run
        # page's inputs say which (read as …).
        out.append(inferred_badge(
            f"a producer candidate of {len(fresh['inferred_inputs']):,} input(s) was found only "
            "through a path an older kit recorded; the run's inputs say which"))
    return out


def origin_html(site: Site, ds: dict, depth: int) -> str:
    """Where a dataset's current bytes come from, as far as the records say: the runs whose output
    had this path and these bytes (named as such, not as the run that made them), the imported file,
    or neither."""
    state, candidates = ds.get("producer_state"), ds.get("producer_candidates") or []
    inferred = (ds.get("inferred") or {}).get("producer_candidates") or []
    if state == "resolved":
        run = candidates[0]
        return (f'<a href="{esc(site.run_link(run, depth))}" title="same bytes as this run\'s output">'
                f"{esc(run)}</a>" + (" " + inferred_badge(f"run {run}") if inferred else ""))
    if state == "ambiguous":
        return (badge("warning", "?", "Producer ambiguous",
                      f"same bytes as the output of {len(candidates):,} runs")
                + " " + site.producer_html(candidates, depth) + inferred_runs_note(inferred))
    if ds.get("unsupported_record"):
        record = ds["unsupported_record"]
        return badge("muted", "?", "Import record not read", f"{record['location']}: {record['reason']}")
    if ds.get("source"):
        return user_text(Path(ds["source"]).name)
    if state == "unknown":
        return badge("muted", "?", "Producer unknown", "the file could not be hashed")
    if ds.get("written_by"):
        return badge("warning", "⚠", "No matching run output",
                     "no successful run output these bytes; runs that wrote this path: "
                     + ", ".join(ds["written_by"][::-1][:SHOWN_CANDIDATES]))
    return badge("critical", "⚠", "No run output recorded")


def producer_cell(site: Site, judged: dict | None, depth: int) -> str:
    """The producer judgment of one input of a run (ADR-0006 4): every successful run that output
    its path with its recorded bytes and did not start after the run, none chosen."""
    if not judged:
        return '<span class="muted">-</span>'
    candidates = judged.get("candidates") or []
    inferred = judged.get("inferred_candidates") or []
    if judged["producer"] == "resolved":
        return site.producer_html(candidates, depth) + (
            " " + inferred_badge(f"run {candidates[0]}") if inferred else "")
    if judged["producer"] == "ambiguous":
        return (badge("warning", "?", "Producer ambiguous",
                      f"{len(candidates):,} successful runs output these bytes")
                + " " + site.producer_html(candidates, depth) + inferred_runs_note(inferred))
    if judged["producer"] == "unknown":
        return badge("muted", "?", "Unknown", "the input has no recorded hash")
    return '<span class="muted">-</span>'


# --- index ---

def index_page(site: Site) -> str:
    p = site.p
    st_goal = ""
    context_text = (p.context.read_text(encoding="utf-8", errors="replace")
                    if p.context.is_file() else "")
    goal = core.context_goal(context_text)
    if goal:
        st_goal = f'<p class="goal"{USER}>{esc(goal)}</p>'
    current = [c for c in site.charts if not c.get("retired")]
    stale = [c for c in current if site.fresh.of(c["run"])["stale"]]
    failed = [r for r in site.runs if r.get("status") != "ok"]
    open_items, key_items, gaps = site.lists.open, site.lists.key, site.lists.feedback

    def tile(label, value, note="", target=""):
        return (f'<a class="tile" href="#{target}"><span class="label">{esc(label)}</span>'
                f'<span class="value">{fmt_num(value)}</span><span class="note">{note}</span></a>')

    tiles = "".join([
        tile("Datasets", len(site.datasets),
             (f"imported {sum(d['kind'] == 'imported' for d in site.datasets)} · "
              if any(d["kind"] == "imported" for d in site.datasets) else "")
             + f"raw {sum(d['kind'] == 'raw' for d in site.datasets)}"
             f" · derived {sum(d['kind'] == 'derived' for d in site.datasets)}", "data"),
        tile("Runs", len(site.runs), f"{len(failed)} not succeeded" if failed else "All succeeded",
             "runs"),
        tile("Visualizations", len(current), f"⚠ {len(stale)} with input changes" if stale
             else "No input changes", "charts"),
        tile("Open questions and next steps", len(open_items), "", "overview-open"),
        tile("Workflow feedback", len(gaps), "In Evaluation", "evaluation"),
    ])

    def item_list(items, empty):
        if not items:
            return f'<div class="empty">{empty}</div>'
        return '<ul class="items">' + "".join(
            f'<li id="key-{esc(e["id"])}">{kind_chip(e["kind"])} <span class="item-text"{USER}>{esc(e["text"])}</span>'
            f'<span class="item-meta"><a href="#{esc(site.anchor(e))}">{esc(site.source(e))}</a> · {fmt_time(e.get("ts"))}</span>'
            f'{site.refs_html(e, 0)}</li>' for e in reversed(items[-12:])) + "</ul>"

    skipped = records_notice(site)

    overview = f"""
<section id="overview" class="tab">
  <div class="hero"><h1{USER}>{esc(p.title())}</h1>{st_goal}<div class="where">Project <code{USER}>{esc(p.root)}</code></div></div>
  {skipped}
  <div class="tiles">{tiles}</div>
  <div class="columns">
    <div class="panel"><h2>Key information</h2>{item_list(key_items, 'Findings, decisions and assumptions appear here once they are recorded.')}</div>
    <div class="panel" id="overview-open"><h2>Open questions · next steps</h2>{item_list(open_items, 'No open questions or next steps.')}</div>
  </div>
  <h2 class="section-title">Recent visualizations</h2>
  <div class="gallery">{''.join(site.chart_card(c, 0) for c in current[:4]) or '<div class="empty">No visualizations yet. Run a script with analytics_run (or analytics run) and its visualizations appear here.</div>'}</div>
  <h2 class="section-title">Recent journal entries</h2>
  {site.events_html(site.events[-8:][::-1], 0)}
</section>"""

    all_cards = "".join(site.chart_card(c, 0, full=True) for c in site.charts)
    charts = f"""
<section id="charts" class="tab">
  <div class="toolbar"><h2>Visualizations</h2><label class="toggle"><input type="checkbox" id="show-retired"> Also show those absent from the latest run</label></div>
  <p class="muted">The version from the latest successful run, for each script::chart id. Each card's code, input data and attached data links lead to the records that made it.</p>
  <div class="gallery">{all_cards or '<div class="empty">No visualizations yet.</div>'}</div>
</section>"""

    timeline = f"""
<section id="timeline" class="tab">
  <div class="toolbar"><h2>Timeline</h2></div>
  {site.events_html(site.events[::-1], 0, filters=True)}
</section>"""

    data_rows = []
    for ds in site.datasets:
        view = site.view_by_path.get(ds["path"])
        origin = origin_html(site, ds, 0)
        label = dataset_label(ds["name"], view)
        also = f' <small>view <code>{esc(view)}</code></small>' if label != view else ""
        data_rows.append(
            f'<tr><td><a href="data/{href(view)}.html"{USER}>{esc(label)}</a>{also}</td><td>{esc(ds["kind"])}</td>'
            f'<td class="num">{fmt_num(ds.get("rows"))}</td><td class="num">{fmt_num(ds.get("columns"))}</td>'
            f'<td class="num">{fmt_bytes(ds.get("bytes"))}</td><td><code{USER}>{esc(ds["path"])}</code></td>'
            f'<td>{origin}</td><td class="num">{len(ds.get("used_by") or [])}'
            f'{inferred_count_html((ds.get("inferred") or {}).get("used_by"))}</td>'
            f'<td class="sha">{short(ds.get("sha256"), 12)}</td></tr>')
    data = f"""
<section id="data" class="tab">
  <div class="toolbar"><h2>Data</h2></div>
  <p class="muted">data/raw holds imported data (kept unchanged), data/derived the Parquet files runs made. Each dataset shows its name as written and, where they differ, its SQL view — the name analytics_query and analytics.sql use.</p>
  {('<div class="table-wrap"><table class="list"><thead><tr><th>Dataset</th><th>Kind</th><th>Rows</th><th>Columns</th><th>Size</th><th>Path</th><th>Source / producer candidates</th><th>Used by runs</th><th>sha256</th></tr></thead><tbody>' + ''.join(data_rows) + '</tbody></table></div>') if data_rows else '<div class="empty">No datasets yet. Import one with analytics_import (or analytics import).</div>'}
</section>"""

    run_rows = []
    for run in site.runs[::-1]:
        repro = relations.run_reproduction(run) or {}
        verdict = ""
        if repro:
            tone = {"match": "good", "mismatch": "critical", "failed": "critical"}.get(
                repro.get("verdict"), "muted")
            verdict = badge(tone, "↻", f"Rerun {verdict_label(repro.get('verdict'))}")
        run_rows.append(
            f'<tr><td>{fmt_time(run.get("started_at"))}</td><td><a href="runs/{href(run["id"])}.html">{esc(run["id"])}</a></td>'
            f'<td>{status_badge(run.get("status"))} {verdict}</td><td{USER}>{esc(run.get("title"))}</td>'
            f'<td class="num">{len(relations.run_visualizations(run))}</td><td class="num">{len(run.get("inputs") or [])}</td>'
            f'<td class="num">{sum(1 for o in run.get("outputs") or [] if o.get("change") != "deleted")}</td>'
            f'<td class="num">{run.get("duration_s", "-")}</td></tr>')
    runs = f"""
<section id="runs" class="tab">
  <div class="toolbar"><h2>Runs</h2></div>
  {('<div class="table-wrap"><table class="list"><thead><tr><th>Started</th><th>Run</th><th>Status</th><th>Title</th><th>Visualizations</th><th>Inputs</th><th>Outputs</th><th>Seconds</th></tr></thead><tbody>' + ''.join(run_rows) + '</tbody></table></div>') if run_rows else '<div class="empty">No runs yet.</div>'}
</section>"""

    context_html = render_markdown(context_text) if context_text else \
        '<div class="empty">CONTEXT.md is empty.</div>'
    version_items = "".join(
        f'<li><a href="context.html#v-{esc(v.stem)}">{fmt_time(core._version_time(v))}</a></li>'
        for v in site.versions[::-1][:12])
    # The context block's own selection, from the file as aelix's read tool numbers its lines.
    _, injected, omitted, _ = core.context_selection(core.context_file_text(p))
    prompt_note = (
        f'<p class="muted">Sections in every prompt: {section_names(injected) or "none"}.'
        + (f' Sections the prompt names with their line ranges only: '
           f'{section_names(omitted)} — the agent reads them when it needs them.'
           if omitted else "") + "</p>")
    context = f"""
<section id="context" class="tab">
  <div class="toolbar"><h2>Context <small>CONTEXT.md</small></h2><a href="context.html">{len(site.versions)} recorded version(s) →</a></div>
  {prompt_note}
  <div class="columns wide-left">
    <article class="panel markdown"{USER if context_text else ""}>{context_html}</article>
    <aside class="panel"><h3>Recent versions</h3><ul class="plain">{version_items or '<li class="muted">None</li>'}</ul></aside>
  </div>
</section>"""

    return site.page(0, "Workspace", "", overview + charts + timeline + data + runs + context
                     + evaluation_section(site), "index")


def section_names(sections: list[dict]) -> str:
    """CONTEXT.md sections by name: a heading's words are the file's (USER); a heading that is
    only a pin mark is named `Section N` by the kit."""
    names = []
    for section in sections:
        heading = section["text"].split("\n", 1)[0][3:]
        named = bool(core.PIN_RE.sub("", heading).strip())
        names.append(user_text(section["name"]) if named else esc(section["name"]))
    return ", ".join(names)


def evaluation_section(site: Site) -> str:
    runs = site.runs
    statuses = Counter(r.get("status") for r in runs)
    no_input = [r for r in runs if r.get("status") == "ok" and not r.get("inputs")
                and (relations.run_visualizations(r) or r.get("outputs"))]
    undocumented = [(r, c) for r in runs if r.get("status") == "ok"
                    for c in relations.run_visualizations(r)
                    if c.get("kind") == "matplotlib" and not c.get("data")]
    external = [(r, i) for r in runs for i in r.get("inputs") or [] if i.get("external")]
    outside = [(r, o) for r in runs for o in r.get("outputs") or [] if o.get("outside_data")]
    reruns = [r for r in runs if relations.run_reproduction(r)]
    verdicts = Counter(relations.run_reproduction(r)["verdict"] for r in reruns)
    stale = [c for c in site.charts if not c.get("retired") and site.fresh.of(c["run"])["stale"]]
    gaps = site.lists.feedback

    def row(label, value, detail=""):
        return f"<tr><th>{esc(label)}</th><td class='num'>{value}</td><td>{detail}</td></tr>"

    def run_list(items):
        return ", ".join(f'<a href="runs/{href(r["id"])}.html">{esc(r["id"])}</a>' for r in items[:6])

    signals = "".join([
        row("Run status", len(runs), " · ".join(f"{esc(STATUS.get(k, (0, 0, k))[2])} {v}"
                                               for k, v in statuses.items())),
        row("Runs with no input detected", len(no_input), run_list(no_input)),
        row("matplotlib charts without attached data", len(undocumented),
            ", ".join(f'<a href="runs/{href(r["id"])}.html"{USER}>{esc(c["id"])}</a>' for r, c in undocumented[:6])),
        row("Input files outside the project", len(external),
            ", ".join(user_text(i["path"]) for _, i in external[:4])),
        row("Files written outside data/", len(outside), ", ".join(user_text(o["path"]) for _, o in outside[:4])),
        row("Visualizations with input changes", len(stale), ", ".join(user_text(c["key"]) for c in stale[:4])),
        row("Rerun comparisons", len(reruns),
            " · ".join(f"{esc(verdict_label(k))} {v}" for k, v in verdicts.items())),
        row("analytics_query queries", len(site.queries),
            f"{sum(1 for q in site.queries if q.get('error'))} with an error"),
    ])
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for t in site.telemetry:
        by_tool[str(t.get("tool"))].append(t)
    tool_rows = "".join(
        f"<tr><td><code>{esc(tool)}</code></td><td class='num'>{len(items)}</td>"
        f"<td class='num'>{sum(1 for i in items if i.get('error'))}</td>"
        f"<td class='num'>{fmt_num(round(sum(i.get('ms') or 0 for i in items) / max(1, len(items))))}</td></tr>"
        for tool, items in sorted(by_tool.items(), key=lambda kv: -len(kv[1])))
    flagged = [t for t in site.telemetry if t.get("flags")]
    flag_items = "".join(
        f"<li><time>{fmt_time(t.get('ts'))}</time> <code>{esc(t.get('tool'))}</code> "
        f"{''.join(badge('warning', '⚑', f) for f in t['flags'])} <span class='muted'{USER}>{esc(t.get('summary'))}</span></li>"
        for t in flagged[::-1][:30])
    gap_html = site.events_html(gaps[::-1], 0) if gaps else \
        '<div class="empty">No workflow feedback yet. Record where the tools got in the way or needed a workaround with /analytics record gap or analytics_record(kind="gap").</div>'
    telemetry_html = (f'<div class="table-wrap"><table class="list"><thead><tr><th>Tool</th><th>Calls</th>'
                      f'<th>Errors</th><th>Mean ms</th></tr></thead><tbody>{tool_rows}</tbody></table></div>'
                      if tool_rows else '<div class="empty">No tool calls recorded by the aelix extension.</div>')
    return f"""
<section id="evaluation" class="tab">
  <div class="toolbar"><h2>Evaluation</h2></div>
  <p class="muted">Where the tools fell short or needed a workaround during the analysis, how much the records cover, and how the agent used its tools.</p>
  <div class="columns">
    <div class="panel"><h3>Workflow feedback ({len(gaps)})</h3>{gap_html}</div>
    <div class="panel"><h3>Record coverage</h3><div class="table-wrap"><table class="kv">{signals}</table></div></div>
  </div>
  <div class="columns">
    <div class="panel"><h3>Tool usage</h3>{telemetry_html}</div>
    <div class="panel"><h3>Possible workarounds ({len(flagged)})</h3>{('<ul class="plain flagged">' + flag_items + '</ul>') if flag_items else '<div class="empty">None</div>'}</div>
  </div>
</section>"""


# --- run page ---

def code_html(source: str, anchor: str, hl_lines=()) -> str:
    """A script as written: its comments and strings are the author's text (USER)."""
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import PythonLexer

        formatter = HtmlFormatter(linenos="table", anchorlinenos=True, lineanchors=f"L-{anchor}",
                                  hl_lines=sorted({n for n in hl_lines if n}), cssclass="code",
                                  wrapcode=True)
        return highlight(source, PythonLexer(), formatter).replace(
            '<div class="code">', f'<div class="code"{USER}>', 1)
    except Exception:  # noqa: BLE001 - fall back to plain text
        lines = source.splitlines()
        return f'<div class="code"{USER}><pre>' + "\n".join(
            f'<a id="L-{anchor}-{i}" href="#L-{anchor}-{i}" class="ln">{i:>4}</a>  {esc(line)}'
            for i, line in enumerate(lines, 1)) + "</pre></div>"


def run_page(site: Site, run: dict) -> str:
    p = site.p
    rid = run["id"]
    script = run.get("script") or {}
    fresh = site.fresh.of(rid)
    same_script = [r for r in site.runs if site.script_of[r["id"]] == site.script_of[rid]]
    pos = next((i for i, r in enumerate(same_script) if r["id"] == rid), 0)
    nav = []
    if pos > 0:
        nav.append(f'<a href="{href(same_script[pos - 1]["id"])}.html">← Previous run</a>')
    if pos < len(same_script) - 1:
        nav.append(f'<a href="{href(same_script[pos + 1]["id"])}.html">Next run →</a>')
    sub = [f"run <code>{esc(rid)}</code>", fmt_time(run.get("started_at")),
           f"{run.get('duration_s', '-')}s", f"actor {esc(run.get('actor') or '-')}"]
    if run.get("session"):
        sub.append(f'<span title="{esc(run["session"])}">aelix session {esc(Path(run["session"]).name)}</span>')
    head = f"""
<div class="page-head">
  <div class="title-row"><h1{USER}>{esc(run.get('title'))}</h1>{status_badge(run.get('status'))}</div>
  <div class="sub">{' · '.join(sub)}</div>
  <div class="sub">Ran <code{USER}>{esc(script.get('path'))}</code> · run {pos + 1} of {len(same_script)} of this script {' '.join(nav)}</div>
</div>"""
    callouts = ""
    if run.get("error"):
        err = run["error"]
        where = f' (line <a href="#L-main-{err["line"]}">{err["line"]}</a>)' if err.get("line") else ""
        fix = f'<div class="fix">How to fix: {esc(err["hint"])}</div>' if err.get("hint") else ""
        # The summary is the script's exception as it said it; the hint is the kit's.
        callouts += (f'<div class="callout tone-critical"><strong{USER}>{esc(err.get("summary"))}</strong>'
                     f'{where}{fix}</div>')
    # Each Visualization's comparison, whichever record holds it (reproduction.charts of a
    # lab.run/1 run, reproduction.visualizations of an analytics.run/2 one: design 5.3).
    repro = relations.run_reproduction(run)
    if repro:
        tone = {"match": "good", "mismatch": "critical", "failed": "critical"}.get(
            repro.get("verdict"), "muted")
        details = "".join(f"<li><code{USER}>{esc(k)}</code>: {esc(v)}</li>"
                          for k, v in {**repro["visualizations"], **repro["outputs"]}.items())
        callouts += (f'<div class="callout tone-{tone}"><strong>Rerun comparison: '
                     f'{esc(verdict_label(repro.get("verdict")))}</strong> — reran the code snapshot of '
                     f'<a href="{href(repro.get("of", ""))}.html">{esc(repro.get("of"))}</a>. Inputs '
                     f'{"same" if repro.get("inputs_same") else "different"}. A rerun is not counted as '
                     f'a version of its visualizations.<ul>{details}</ul></div>')
    if run.get("finalize_errors"):
        callouts += '<div class="callout tone-critical"><strong>Some recording steps failed</strong><ul>' + "".join(
            f"<li>{esc(w)}</li>" for w in run["finalize_errors"]) + "</ul></div>"
    if run.get("warnings"):
        # Runs recorded before warnings were grouped can carry one line per input file.
        shown = run["warnings"][:REF_LIMIT]
        rest = len(run["warnings"]) - len(shown)
        callouts += ('<div class="callout tone-warning"><strong>Recording warnings</strong><ul>'
                     + "".join(f"<li>{esc(w)}</li>" for w in shown)
                     + (f"<li class='muted'>{rest:,} more</li>" if rest > 0 else "")
                     + "</ul></div>")
    recording = site.recording.get(rid) or {}
    if recording.get("recording_status") == "incomplete":
        callouts += ('<div class="callout tone-warning"><strong>Recording incomplete</strong><ul>'
                     + "".join(f"<li><code>{esc(r)}</code>: {esc(RECORDING_REASON.get(r, r))}</li>"
                               for r in recording.get("reasons") or [])
                     + "</ul></div>")
    if fresh["changed_inputs"]:
        reasons = fresh.get("change_reasons") or {}
        callouts += ('<div class="callout tone-critical"><strong>This run\'s inputs differ from the files now.</strong> '
                     + ", ".join(user_text(path) + (f" {esc(core.LINE_ENDINGS_NOTE)}"
                                                    if reasons.get(path) == core.LINE_ENDINGS_ONLY else "")
                                 for path in fresh["changed_inputs"]) + "</div>")
    if fresh["stale_upstream"]:
        # The stale runs upstream output the same bytes as an input: candidates, not its producer.
        callouts += ('<div class="callout tone-warning"><strong>Upstream candidate stale.</strong> '
                     + UPSTREAM_STALE.format(runs=site.producer_html(fresh["stale_upstream"], 1))
                     + inferred_runs_note(fresh.get("inferred_upstream")) + "</div>")
    if fresh.get("ambiguous_inputs") or fresh.get("cycle"):
        callouts += ('<div class="callout tone-warning">' + " ".join(producer_badges(fresh))
                     + " The inputs below name every successful run that output their bytes; none is "
                     "chosen as the producer.</div>")

    charts = []
    for chart in relations.run_visualizations(run):
        key = site.chart_key(run, chart)
        data_block = ""
        if chart.get("data"):
            data_block = (f'<details class="data-block" id="chart-{esc(chart["id"])}-data"><summary>Attached data · '
                          f'{fmt_num(chart.get("data_rows"))} rows · <a href="{site.file_link(chart["data"], 1)}">parquet</a></summary>'
                          f'{site.frame_preview(chart["data"])}</details>')
        line = f' · code <a href="#L-main-{chart["line"]}">L{chart["line"]}</a>' if chart.get("line") else ""
        # The key the run recorded is the older kit's spelling (what people wrote: lang="").
        inferred_key = (f' <small class="ref-note" title="{esc(INFERRED_TITLE)}">recorded as '
                        f'<code{USER}>{esc(core.chart_key(run, chart))}</code> — '
                        f'{esc(core.INFERRED_NOTE)}</small>'
                        if key != core.chart_key(run, chart) else "")
        charts.append(f"""
<article class="chart-card wide" id="chart-{esc(chart['id'])}">
  <header><h3{USER}>{esc(chart.get('title'))}</h3><div class="badges"><span class="badge tone-muted">{esc(chart.get('kind'))} · {esc(chart.get('via'))}</span></div></header>
  <div class="chart-body">{site.chart_html(chart, 1, lazy=False)}</div>
  {f'<p class="caption"{USER}>{esc(chart["caption"])}</p>' if chart.get('caption') else ''}
  <div class="prov-line">id <code{USER}>{esc(key)}</code>{inferred_key}{line} · <a href="{site.chart_anchor(key, 1)}">Show in Visualizations</a></div>
  {data_block}
</article>""")
    charts_html = "".join(charts) or '<div class="empty">This run made no visualizations.</div>'

    hl = [c.get("line") for c in relations.run_visualizations(run)]
    if run.get("error") and run["error"].get("line"):
        hl.append(run["error"]["line"])
    code_blocks = []
    snap = script.get("snapshot")
    if snap and p.path(snap).is_file():
        state = {"same": "Same as the current file", "changed": "Current file edited",
                 "missing": "Current file missing", "n/a": "Rerun snapshot"}.get(fresh["code"],
                                                                                fresh["code"])
        if fresh.get("code_reason") == core.LINE_ENDINGS_ONLY:
            state += f" {core.LINE_ENDINGS_NOTE}"
        code_blocks.append(
            f'<div class="codefile" id="code-main"><div class="file-head"><code{USER}>{esc(script.get("path"))}</code>'
            f'<span class="sha">sha256 {short(script.get("sha256"), 12)}</span><span>{esc(state)}</span>'
            f'<a href="{site.file_link(snap, 1)}">Snapshot file</a></div>'
            f'{code_html(p.path(snap).read_text(encoding="utf-8", errors="replace"), "main", hl)}</div>')
    for i, mod in enumerate(run.get("modules") or [], 1):
        mp = p.path(mod.get("snapshot", ""))
        if mp.is_file():
            code_blocks.append(
                f'<details class="codefile"><summary><code{USER}>{esc(mod["path"])}</code> <span class="sha">'
                f'sha256 {short(mod.get("sha256"), 12)}</span> (imported local module)</summary>'
                f'{code_html(mp.read_text(encoding="utf-8", errors="replace"), f"m{i}")}</details>')

    pairs = list(zip(run.get("inputs") or [], fresh["inputs"], strict=False))
    n_inputs = len(pairs)
    # Files whose state changed are the ones a reader came for, so they survive the row cap.
    ordered = sorted(pairs, key=lambda pair: pair[1]["state"] == "same")[:FILE_ROW_LIMIT]
    input_rows = []
    for entry, f_entry in ordered:
        tone, icon, label = INPUT_STATE.get(f_entry["state"], INPUT_STATE["unknown"])
        producer = producer_cell(site, site.model.producer(rid, entry.get("path")), 1)
        input_rows.append(
            f'<tr><td>{site.path_html(entry["path"], 1)}{site.read_as_html(rid, entry.get("path"), 1)}</td>'
            f'<td>{esc(", ".join(entry.get("via") or []))}</td>'
            f'<td class="num">{fmt_bytes(entry.get("bytes"))}</td><td class="sha">{short(entry.get("sha256"), 12)}</td>'
            f'<td>{badge(tone, icon, label)}'
            f'{" " + line_endings_badge() if f_entry.get("reason") == core.LINE_ENDINGS_ONLY else ""}'
            f'</td><td>{producer}</td></tr>')
    if n_inputs > len(input_rows):
        input_rows.append(
            f'<tr><td colspan="6" class="muted">{n_inputs - len(input_rows):,} more — changed inputs '
            f'are listed first. The full list is in '
            f'<a href="{site.file_link(f"runs/{rid}/run.json", 1)}">run.json</a>.</td></tr>')
    output_rows = []
    for out in run.get("outputs") or []:
        if out.get("change") == "deleted":
            now = '<span class="muted">-</span>'
        else:
            seen = site.model.observation(rid, "output", out.get("path"))
            tone, icon, label = OUTPUT_STATE.get(seen["state"], OUTPUT_STATE["unknown"])
            now = badge(tone, icon, label, seen.get("reason"))
        output_rows.append(
            f'<tr><td>{site.path_html(out["path"], 1)}{site.read_as_html(rid, out.get("path"), 1)}</td>'
            f'<td>{esc(out.get("change"))}</td>'
            f'<td>{esc(out.get("via", ""))}</td><td class="num">{fmt_num(out.get("rows"))}</td>'
            f'<td{USER}>{esc(", ".join(c[0] for c in (out.get("schema") or [])[:12]))}</td>'
            f'<td class="sha">{short(out.get("sha256"), 12)}</td><td>{now}</td></tr>')

    def log_block(label, rel):
        if not rel:
            return ""
        path = p.path(rel)
        try:
            size = path.stat().st_size
        except OSError:
            return ""
        with open(path, "rb") as fh:
            fh.seek(max(0, size - LOG_LIMIT))
            text = fh.read().decode("utf-8", errors="replace")
        opened = " open" if label == "stderr" and run.get("status") != "ok" and size else ""
        # What the script printed is its own text; "(empty)" is the kit's.
        shown = f"<pre{USER}>{esc(text)}</pre>" if text else "<pre>(empty)</pre>"
        return (f'<details class="log"{opened}><summary>{label} · {fmt_bytes(size)}</summary>'
                f'{shown}</details>')

    env = run.get("env") or {}
    env_rows = "".join(f"<tr><th>{esc(k)}</th><td><code>{esc(v)}</code></td></tr>" for k, v in [
        ("python", env.get("python")), ("uv.lock sha256", short(env.get("uv_lock_sha256"), 16)),
        ("kit", f"{env.get('kit_version')} · {short(env.get('kit_sha256'), 12)}"),
        *sorted((env.get("packages") or {}).items())])
    related = site.related.get(rid, [])

    body = f"""{head}{callouts}
<nav class="jump"><a href="#charts">Visualizations</a><a href="#code">Code</a><a href="#inputs">Inputs</a><a href="#outputs">Outputs</a><a href="#logs">Logs</a><a href="#env">Environment</a><a href="#journal">Journal</a><a href="#reproduce">Rerun</a></nav>
<section id="charts"><h2>Visualizations ({len(charts)})</h2>{charts_html}</section>
<section id="code"><h2>Code</h2><p class="muted">A snapshot of the bytes the run executed. Highlighted lines recorded a visualization or raised the error.</p>{''.join(code_blocks) or '<div class="empty">No code snapshot.</div>'}</section>
<section id="inputs"><h2>Input data ({n_inputs})</h2><p class="muted">via: runtime (seen while polars, duckdb or lab.* read it), scan (a path literal in the code), declared (given with the run). The state compares the file's sha256 now with the record. Producer: every successful run that output this path with these bytes and did not start after this run — same bytes, not a confirmed link.</p>
{('<div class="table-wrap"><table class="list"><thead><tr><th>Path</th><th>via</th><th>Size</th><th>Recorded sha256</th><th>Now</th><th>Producer</th></tr></thead><tbody>' + ''.join(input_rows) + '</tbody></table></div>') if input_rows else '<div class="empty">No inputs detected.</div>'}</section>
<section id="outputs"><h2>Outputs ({len(output_rows)})</h2>
{('<div class="table-wrap"><table class="list"><thead><tr><th>Path</th><th>Change</th><th>via</th><th>Rows</th><th>Columns</th><th>sha256</th><th>Now</th></tr></thead><tbody>' + ''.join(output_rows) + '</tbody></table></div>') if output_rows else '<div class="empty">The run created or changed no project files.</div>'}</section>
<section id="logs"><h2>Logs</h2>{log_block('stdout', run.get('stdout'))}{log_block('stderr', run.get('stderr'))}</section>
<section id="env"><h2>Environment</h2><div class="table-wrap"><table class="kv">{env_rows}</table></div></section>
<section id="journal"><h2>Journal entries for this run</h2>{site.events_html(related, 1)}</section>
<section id="reproduce"><h2>Rerun</h2><p>Runs the same code snapshot again and compares its attached data and outputs (a rerun comparison).</p>
<pre class="cmd">aelix: analytics_run(title="Rerun comparison", rerun_of="{esc(rid)}")
CLI:   analytics run --rerun-of {esc(rid)}</pre></section>"""
    crumb = f' <span>/</span> <a href="../index.html#runs">Runs</a> <span>/</span> {esc(rid)}'
    return site.page(1, run.get("title") or rid, crumb, body, "run")


# --- dataset page ---

def dataset_page(site: Site, ds: dict, view: str) -> str:
    from . import data

    p = site.p
    try:
        prof = data.profile(p, p.path(ds["path"]), preview_rows=50, cache=site.cache)
    except Exception as exc:  # noqa: BLE001
        prof = {"columns": [], "preview": {"columns": [], "rows": []}, "error": str(exc)}
    # An import record this kit cannot read says nothing here (core.list_datasets).
    meta = (core.read_json(p.path(ds["path"]).with_suffix(".meta.json"))
            if ds["kind"] == "raw" and not ds.get("unsupported_record") else None)
    label = dataset_label(ds["name"], view)
    facts = [f'<code{USER}>{esc(ds["path"])}</code>',
             f'{fmt_num(prof.get("rows"))} rows × {len(prof.get("columns") or [])} columns',
             fmt_bytes(prof.get("bytes")), f'sha256 <span class="sha">{short(prof.get("sha256"), 16)}</span>']
    if label != view:
        facts.append(f"SQL view <code>{esc(view)}</code>")
    origin = ""
    if isinstance(meta, dict):
        src = meta.get("source") or {}
        origin = (f'<dl class="facts"><div><dt>Source file</dt><dd><code{USER}>{esc(src.get("path"))}</code></dd></div>'
                  f'<div><dt>Source sha256</dt><dd class="sha">{short(src.get("sha256"), 16)}</dd></div>'
                  f'<div><dt>Format</dt><dd>{esc(src.get("format"))}</dd></div>'
                  f'<div><dt>Imported at</dt><dd>{fmt_time(meta.get("imported_at"))}</dd></div></dl>')
        if meta.get("description"):
            origin = f'<p class="lead"{USER}>{esc(meta["description"])}</p>' + origin
    producers = ds.get("producers") or []
    candidates = ds.get("producer_candidates") or []
    inferred = ds.get("inferred") or {}
    said = core.dataset_origin(ds)
    # The file it was imported from is named by the path as written.
    current = (f"from {user_text(ds['source'])}" if ds.get("source") and said == f"from {ds['source']}"
               else esc(said))
    if ds.get("producer_state") in ("resolved", "ambiguous"):
        # Only then does a run named here hold these bytes: with `none` the runs named wrote other
        # bytes or did not succeed.
        current += (f" ({site.producer_html(candidates, 1)}). A run named here output this path with "
                    "these bytes, which is what the records show; it is not a confirmed link to the "
                    "run that made them")
        if inferred.get("producer_candidates"):
            current += (" " + inferred_badge(", ".join(inferred["producer_candidates"]))
                        + " A run marked so recorded the path by its real place or in another "
                        "spelling, and is named here by reading it as this path")
    lineage = [f"<h3>Current bytes</h3><p>{current}.</p>"]

    def change(run_id: str):
        changes = [o.get("change") for o in (site.run_by_id.get(run_id) or {}).get("outputs") or []
                   if o.get("path") == ds["path"]]
        return changes[-1] if changes else None

    if producers:
        # Every successful run that recorded this path as an output, whatever its bytes, and a
        # deletion as such: none of them is said to have made the file.
        lineage.append("<h3>Successful runs that recorded an output at this path</h3><ul class='plain'>"
                       + "".join(
            f'<li><a href="{esc(site.run_link(r, 1))}">{esc(r)}</a> {user_text((site.run_by_id.get(r) or {}).get("title"))}'
            f'{" · deleted this path" if change(r) == "deleted" else ""}'
            f'{" · same bytes as now" if r in candidates else ""}'
            f'{inferred_mark(r, inferred.get("producers"))}</li>'
            for r in dict.fromkeys(producers)) + "</ul>")
    users = ds.get("used_by") or []
    lineage.append("<h3>Runs that read this data</h3>" + ("<ul class='plain'>" + "".join(
        f'<li><a href="{site.run_link(r, 1)}">{esc(r)}</a> {user_text((site.run_by_id.get(r) or {}).get("title"))}'
        f'{inferred_mark(r, inferred.get("used_by"))}</li>'
        for r in users) + "</ul>" if users else '<div class="empty">None yet</div>'))
    # A chart's run read this data when it recorded its path, or (inferred) an older kit's path read
    # as it.
    used_charts = [c for c in site.charts if not c.get("retired") and ds["path"] in
                   {site.model.read_as(c["run"], i.get("path")) or i.get("path")
                    for i in (site.run_by_id.get(c["run"]) or {}).get("inputs") or []}]
    if used_charts:
        lineage.append("<h3>Visualizations made from this data (latest versions)</h3><ul class='plain'>" + "".join(
            f'<li><a href="{site.chart_anchor(c["key"], 1)}"{USER}>{esc(c.get("title"))}</a> <code{USER}>{esc(c["key"])}</code>'
            f'{inferred_mark(c["run"], inferred.get("used_by"))}</li>'
            for c in used_charts) + "</ul>")
    # A statistic that does not apply to a column type is "–", never "null" (which is a data value).
    schema_rows = [[c["name"], c["dtype"], c.get("nulls"),
                    *("–" if c.get(k) is None else c.get(k) for k in ("min", "max", "mean", "distinct"))]
                   for c in prof.get("columns") or []]
    preview = prof.get("preview") or {}
    body = f"""
<div class="page-head"><div class="title-row"><h1{USER}>{esc(label)}</h1><span class="badge tone-muted">{esc(ds['kind'])}</span></div>
<div class="sub">{' · '.join(facts)}</div></div>
{origin}
<section><h2>Schema and statistics</h2>{site.table(['Column', 'Type', 'null', 'min', 'max', 'mean', 'distinct≈'], schema_rows, klass='list', data_head=False)}</section>
<section><h2>Preview</h2>{site.table(preview.get('columns') or [], preview.get('rows') or [])}</section>
<section><h2>Relations</h2>{''.join(lineage)}</section>
<section><h2>Query</h2><pre class="cmd">analytics_query(sql="SELECT * FROM {esc(view)} LIMIT 20")
analytics.read("{esc(view)}")   ·   analytics.sql("SELECT count(*) FROM {esc(view)}").pl()</pre></section>"""
    crumb = f' <span>/</span> <a href="../index.html#data">Data</a> <span>/</span> {user_text(label)}'
    return site.page(1, label, crumb, body, "dataset")


# --- context page ---

def render_markdown(text: str) -> str:
    try:
        from markdown_it import MarkdownIt

        return MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).render(text)
    except Exception:  # noqa: BLE001
        return f"<pre>{esc(text)}</pre>"


def context_page(site: Site) -> str:
    p = site.p
    current = (p.context.read_text(encoding="utf-8", errors="replace")
               if p.context.is_file() else "")
    blocks = []
    versions = site.versions
    for i in range(len(versions) - 1, -1, -1):
        v = versions[i]
        new = v.read_text(encoding="utf-8", errors="replace").splitlines()
        old = versions[i - 1].read_text(encoding="utf-8", errors="replace").splitlines() if i else []
        diff = list(difflib.unified_diff(old, new, lineterm="", n=2))[2:]
        lines = "".join(
            f'<span class="{"add" if d.startswith("+") else "del" if d.startswith("-") else "hunk" if d.startswith("@@") else "ctx"}">{esc(d)}</span>\n'
            for d in diff) or '<span class="ctx">(first version)</span>\n'
        event = next((e for e in site.events if (e.get("refs") or {}).get("context") == v.name), None)
        who = f" · {esc(event.get('actor'))} · {esc(event.get('id'))}" if event else ""
        blocks.append(f'<article class="version" id="v-{esc(v.stem)}"><h3>{fmt_time(core._version_time(v))}{who}</h3>'
                      f'<pre class="diff"{USER if diff else ""}>{lines}</pre></article>')
    body = f"""
<div class="page-head"><h1>Context history</h1><div class="sub">Each change to CONTEXT.md leaves a version in .lab/context/. A new version is noticed at a run, an import, a record and a Workspace render.</div></div>
<div class="columns wide-left">
  <article class="panel markdown"{USER if current else ""}>{render_markdown(current) if current else '<div class="empty">Empty</div>'}</article>
  <aside class="panel"><h3>{len(versions)} version(s)</h3><ul class="plain">{''.join(f'<li><a href="#v-{esc(v.stem)}">{fmt_time(core._version_time(v))}</a></li>' for v in versions[::-1])}</ul></aside>
</div>
<section><h2>Changes</h2>{''.join(blocks) or '<div class="empty">No versions recorded.</div>'}</section>"""
    return site.page(0, "Context history", ' <span>/</span> Context history', body, "context")
