"""The Workspace: static HTML generated from the project files (never edited by hand).

workspace/index.html        overview · charts · timeline · data · runs · context · evaluation
workspace/runs/<id>.html    one run: charts with plotted data, code, inputs (with freshness),
                            outputs, logs, environment, reproduction
workspace/data/<view>.html  one dataset: schema, statistics, preview, lineage
workspace/context.html      CONTEXT.md now and every recorded version with diffs

In the project's Workspace directory (Project.workspace_dir: workspace/ of the format this kit
writes; report/ of a lab.json project, which this kit does not build). Pages use relative links
only, so they work from file:// and inside a copied project.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import html
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from importlib import resources
from pathlib import Path
from urllib.parse import quote

from . import _platform, core, i18n, relations
from .i18n import attr, t

ASSETS = resources.files(__package__) / "assets"
# The names the Workspace's own assets had before kit 0.6.0 (workspace.css and workspace.js since):
# a build removes them from a Workspace an earlier kit rendered.
OLD_ASSETS = ("report.css", "report.js")
PLOTLY_EMBED_LIMIT = 3_000_000
LOG_LIMIT = 200_000
# A run over hundreds of files would otherwise bury the timeline and its own page in paths.
REF_LIMIT = 12
FILE_ROW_LIMIT = 60
INDEX_LIMIT = 50
CHART_INDEX_LIMIT = 20
# The Workspace's own words are messages of i18n.py: English as written, Korean when a reader
# switches (TERMINOLOGY 7); what people and scripts wrote is shown as written. An event kind's
# words are `kind.<kind>`: the stored kind stays the machine value (`gap`, `todo`).
# A run's status: its tone and mark; its words are `status.<status>`.
STATUS = {"ok": ("good", "✓"), "error": ("critical", "✕"), "timeout": ("serious", "⏱"),
          "cancelled": ("warning", "■"), "incomplete": ("muted", "…"), "running": ("muted", "…")}
# An input's state now (`input.<state>`).
INPUT_STATE = {"same": ("good", "✓"), "changed": ("critical", "⚠"), "missing": ("critical", "⚠"),
               "unknown": ("muted", "?")}
# A reference's state as the read judges it (ADR-0006 4): its tone and mark, and its words
# (`ref.<state>.<reason>`, else `ref.<state>`), never colour alone (design WORKSPACE-READABILITY 4).
# The stored values stay as written under a relation's Details. A rerun comparison's verdict is
# `verdict.<verdict>` (TERMINOLOGY 6); the record keeps the value.
REF_STATE = {"resolved": ("good", "✓"), "content_changed": ("critical", "⊗"), "missing": ("critical", "⊗"),
             "version_unbound": ("warning", "⚠"), "ambiguous": ("warning", "⚠"), "unknown": ("warning", "⚠"),
             "unsupported": ("muted", "–")}
# A record's own state when it is not as it was made (ADR-0006 6): `record.<state>`.
RECORD_STATE = {"superseded": ("muted", "↺"), "conflict": ("warning", "⚠"), "withdrawn": ("muted", "⊘")}
# How many references a list row shows before "N more" (the relation page lists them all).
ROW_REFS = 4
# The current state of a file a run recorded as its output (`output.<state>`).
OUTPUT_STATE = {"resolved": ("good", "✓"), "content_changed": ("critical", "⚠"),
                "missing": ("critical", "⚠"), "unknown": ("muted", "?")}
SHOWN_CANDIDATES = 3
# The index section a page's way back goes to when the page a reader came from is not known: the
# section its kind is listed in (`back.tab.<section>` names it).
BACK = {"run": "runs", "dataset": "data", "relations": "insights", "context": "context", "list": "timeline",
        "script": "runs"}
# The pages are lang="en" (the kit's words). What people and scripts wrote is in a language the
# records do not name, so its element says "unknown" (lang=""): a screen reader reads it in the
# reader's own voice rather than an English one, and nothing guesses the language (TERMINOLOGY 7).
USER = ' lang=""'


def words(prefix: str, value) -> str:
    """The kit's words for a stored value (`prefix.value`), or the value itself as stored when the
    catalog has none (a value a newer kit wrote)."""
    key = f"{prefix}.{value}"
    return t(key) if i18n.has(key) else esc(value)


def comparison_words(value) -> str:
    """A rerun comparison's value as the runner wrote it (runner.compare_runs: "same data", …)."""
    key = "comparison." + str(value).replace(" ", "_")
    return t(key) if i18n.has(key) else esc(value)


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


STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?")


def fmt_day(y: str, m: str, d: str) -> str:
    """A date in English (time.date.<month>: "Aug 3, 2026"). A page shows it in its language from
    the <time datetime> it is in (workspace-i18n.js)."""
    return esc(i18n.message(f"time.date.{int(m)}", y=y, d=int(d)))


def fmt_time(ts, with_date=True) -> str:
    """A record's time as its record holds it: the wall clock of the record's own offset, never the
    reader's zone (design WORKSPACE-READABILITY 3.1). A date carries the stamp in <time datetime>;
    a time alone (under a day heading) is the same in every language."""
    if not ts:
        return "-"
    text = str(ts)
    found = STAMP.match(text)
    if not found or found.group(4) is None or not 1 <= int(found.group(2)) <= 12:
        text = text.replace("T", " ")
        return esc(text[:16]) if with_date else f"<time>{esc(text[11:16])}</time>"
    y, m, d, hh, mm = found.groups()
    if not with_date:
        return f"<time>{hh}:{mm}</time>"
    return f'<time datetime="{esc(text)}">{fmt_day(y, m, d)} {hh}:{mm}</time>'


def fmt_date(ts) -> str:
    """The day of a record's time (a timeline's day heading), in the page's language."""
    found = STAMP.match(str(ts or ""))
    if not found or not 1 <= int(found.group(2)) <= 12:
        return esc(str(ts or "")[:10])
    y, m, d = found.group(1, 2, 3)
    return f'<time datetime="{y}-{m}-{d}">{fmt_day(y, m, d)}</time>'


def title_attr(text) -> str:
    """A title of what people and scripts wrote (paths, ids): as written, not the kit's words."""
    return f' title="{esc(text)}"' if text else ""


def badge(tone: str, icon: str, label: str, title: str = "") -> str:
    """`label` is HTML of the kit's words (t); `title` an attribute (attr, or title_attr)."""
    return f'<span class="badge tone-{tone}"{title}><span aria-hidden="true">{icon}</span> {label}</span>'


def status_badge(status) -> str:
    tone, icon = STATUS.get(status or "", ("muted", "?"))
    return badge(tone, icon, words("status", status))


def verdict_label(verdict) -> str:
    return words("verdict", verdict)


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
            f'{words("kind", kind)}</span>')


def ref_words(state, reason=None) -> str:
    """A reference judgment in the kit's words: its reason's when the catalog says it apart
    (`ref.missing.file_absent`), else its state's."""
    return t(f"ref.{state}.{reason}") if reason and i18n.has(f"ref.{state}.{reason}") else words("ref", state)


@functools.cache
def _kind_list(kind: str) -> str:
    english = i18n.message(f"kind.{kind}")
    alike = [key[5:] for key, text in i18n.catalog(i18n.DEFAULT).items()
             if key.startswith("kind.") and text == english]
    return min(alike, key=lambda k: (core.EVENT_KINDS.index(k) if k in core.EVENT_KINDS else len(core.EVENT_KINDS), k))


def kind_list(kind):
    """The kind whose own list holds a stored kind's records: kinds the catalog names alike share one
    list (`gap` is the /1 name of `workflow_feedback`, `reuse_proposal` an earlier `asset_candidate`),
    so a kind filter never shows two links that say the same. The first by EVENT_KINDS, else by name."""
    return _kind_list(kind) if isinstance(kind, str) and i18n.has(f"kind.{kind}") else kind


def kind_file(kind) -> str:
    """The part of a kind's list page names (`lists/kind-<part>-<n>.html`): the kind as stored when it
    is a plain name, else `x-` and a digest of it (a `-` no plain name has), so any stored kind has a
    page of its own and none can name a path."""
    text = str(kind)
    return text if re.fullmatch(r"[a-z][a-z0-9_]{0,47}", text) else "x-" + hashlib.sha1(text.encode()).hexdigest()[:12]


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

    esc = staticmethod(esc)
    user_text = staticmethod(user_text)
    words = staticmethod(words)
    ref_words = staticmethod(ref_words)
    fmt_time = staticmethod(fmt_time)
    fmt_date = staticmethod(fmt_date)
    fmt_num = staticmethod(fmt_num)
    fmt_bytes = staticmethod(fmt_bytes)
    short = staticmethod(short)
    href = staticmethod(href)
    title_attr = staticmethod(title_attr)
    badge = staticmethod(badge)
    status_badge = staticmethod(status_badge)
    kind_chip = staticmethod(kind_chip)
    user_language = USER
    REF_STATE = REF_STATE
    RECORD_STATE = RECORD_STATE

    def __init__(self, project: core.Project):
        from .workspace_map import AnalysisMap, AnalysisStatus
        from .workspace_relations import RelationPages
        from .workspace_versions import Versions

        self.p = project
        self.cache = core.HashCache(project)
        self.model = core.read_model(project, self.cache)
        journal = self.model.journal
        # lab.event/1 and analytics.event/2 records alike, in seq order (design 5.2).
        self.lists = core.JournalLists(journal)
        self.events = self.lists.events
        self.source_of = self.lists.source
        # The records the index's lists show (the newest INDEX_LIMIT): a link to one of them goes to its
        # row there. Read once: every row and relation card asks.
        self.recent_sources = {self.source(e) for e in self.events[-INDEX_LIMIT:]}
        # The project's title, read from its marker once (every page shows it twice).
        self.project_title = project.title()
        self._kind_counts: dict[str, tuple[list, Counter]] = {}
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
        # The tool and query logs of both state directories (design 5.3): this kit's (.analytics/),
        # and an older kit's (.lab/) — a migrated project's copy of them is in .analytics/ already.
        logs = dict.fromkeys((project.state_dir, project.legacy_state_dir))
        self.telemetry = [row for place in logs for row in core.read_jsonl(place / "telemetry.jsonl")]
        self.queries = [row for place in logs for row in core.read_jsonl(place / "queries.jsonl")]
        # The CONTEXT.md versions of both state directories (design 5.3), as a reference resolves them.
        self.versions = core.context_versions(project)
        self.build = core.now().strftime("%Y%m%d-%H%M%S-%f")
        self.built_at = core.iso()
        self.uses_plotly = False
        self._fig = 0
        self.relations = RelationPages(self)
        # The Overview's analysis status and the Analysis map (design WORKSPACE-READABILITY 7, #98),
        # from the same read.
        self.status = AnalysisStatus(self)
        self.map = AnalysisMap(self)
        # Every version history the pages show (design WORKSPACE-READABILITY 5, #99), from the same read.
        self.history = Versions(self)

    # --- links (depth 0 = workspace/, 1 = workspace/runs|data/) ---

    def up(self, depth: int) -> str:
        return "../" * (depth + 1)

    def root(self, depth: int) -> str:
        return "../" * depth

    def file_link(self, rel: str, depth: int) -> str:
        if not rel:
            return "#"
        # A path recorded as absolute is absolute on the platform that recorded it. os.path.isabs
        # answers for the platform rebuilding the report, so a Windows path read on Linux would be
        # linked as if it sat inside workspace/.
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
        key = self.model.chart_key(key)
        position = next((i for i, chart in enumerate(self.charts) if chart["key"] == key), None)
        page = f"lists/charts-{position // CHART_INDEX_LIMIT}.html" if position is not None and position >= CHART_INDEX_LIMIT else "index.html"
        return f"{self.root(depth)}{page}#chart-{slug_key(key)}"

    def relation_link(self, identity: str, depth: int, *, record: bool = False, klass: str = "rec-action") -> str:
        """The one way from an item to its relation page (design WORKSPACE-READABILITY 8.1):
        `Relations N`, N the 1-hop references that page lists. A record with none says `Open`: its
        page still holds the record, its state and its stored fields."""
        n = len(self.model.reference_neighborhood(identity))
        if record and not n:
            return f'<a class="{klass}" href="{self.relations.link(identity, depth, anchor="#record")}">{t("action.open")}</a>'
        return f'<a class="{klass}" href="{self.relations.link(identity, depth)}">{t("action.relations", n=n)}</a>'

    def relation_actions(self, identity: str, depth: int) -> str:
        return f'<div class="workspace-actions">{self.relation_link(identity, depth, klass="action")}</div>'

    @staticmethod
    def inferred_mark(run_id: str, runs) -> str:
        return inferred_mark(run_id, runs)

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
        return (f' <small class="ref-note"{inferred_title()}>{t("inferred.read_as")} {shown} — '
                f'{t("inferred.note")}</small>')

    def dataset_name(self, path: str) -> str | None:
        """The name the Workspace shows for the dataset at `path` (dataset_label), if it is one."""
        view = self.view_by_path.get(path)
        return dataset_label(self.name_by_path.get(path) or view, view) if view else None

    def path_html(self, path: str, depth: int) -> str:
        """A path people or scripts wrote, linked to its dataset page (titled by the path) or file.
        That a link is the latest version and not a cited one is said by its reference's badge
        (ref_html), in the kit's words."""
        link = self.data_link(path, depth)
        label = self.dataset_name(path) or path
        if link:
            return f'<a href="{link}" title="{esc(path)}"{USER}>{esc(label)}</a>'
        return f'<a href="{self.file_link(path, depth)}"{USER}>{esc(path)}</a>'

    def source(self, event: dict) -> str | None:
        """How the read names an event: its id, or `journal:<line>` when several lines have it."""
        return self.source_of.get(id(event), event.get("id"))

    def anchor(self, event: dict) -> str:
        """An event's anchor on the timeline: its id, or `journal-<line>` when several lines have it."""
        source = self.source(event)
        return event.get("id") if not source or source == event.get("id") else source.replace(":", "-")

    def refs_html(self, event: dict, depth: int, *, limit: int | None = None, empty: bool = False) -> str:
        """An event's references as the read judges them (ADR-0006 4), the same judgments
        analytics_status counts, a badge each (design WORKSPACE-READABILITY 8.1). A field that names
        many targets shows REF_LIMIT of them and how many more; a list row shows `limit` in all and
        leaves the rest to its relation page. `empty`: say so when the record cites nothing."""
        shown, hidden = [], 0
        groups: dict = {}
        for judged in self.model.references(self.source(event) or ""):
            groups.setdefault((judged.get("ref") or {}).get("field"), []).append(judged)
        for group in groups.values():
            shown += group[:REF_LIMIT]
            hidden += max(0, len(group) - REF_LIMIT)
        if limit is not None and len(shown) > limit:
            hidden += len(shown) - limit
            shown = shown[:limit]
        items = [self.ref_html(judged, depth) for judged in shown]
        if hidden:
            items.append(f'<span class="ref-more"{attr("title", "refs.more_title")}>{t("refs.more", n=hidden)}</span>')
        if not items and empty:
            items.append(f'<span class="ref tone-muted">{t("refs.none")}</span>')
        return f'<span class="refs">{"".join(items)}</span>' if items else ""

    def ref_html(self, judged: dict, depth: int) -> str:
        """One reference as a badge: a mark and the judgment in words, never colour alone, and the
        target by the identifier analytics_status uses (what people wrote stays lang=""), linked where
        the read found it. A found record a reference corrects or closes says so (`Corrects e3`); a
        name left unbound links the latest version and says it is not the cited one. The stored
        values are under the relation's Details on its relation page."""
        kind, state = judged.get("type"), judged["state"]
        target, label = judged.get("target") or {}, core.reference_label(judged)
        found = state == "resolved"
        tone = REF_STATE.get(state, ("muted", "?"))[0]
        problem = ref_words(state, judged.get("reason"))
        if state == "version_unbound":
            # The link is to the latest version: said where the badge says no version was named.
            problem = f'<span{attr("title", "refs.latest")}>{problem}</span>'
        if kind in relations.EVENT_TARGETS:
            link = self.relations.content(label, depth) or self.relations.link(label, depth)
            shown = f'<a href="{esc(link)}">{esc(label)}</a>' if found else esc(label)
            relation = judged.get("relation")
            if relation in ("supersedes", "resolves"):
                lead = t("refs.corrects" if relation == "supersedes" else "refs.resolves")
                if found:
                    return self.ref_badge("link", lead, shown, state)
                return self.ref_badge(tone, lead, shown, state, tail=problem,
                                      note=self.recorded_later(judged, depth) + self.candidates_note(judged))
            if found:
                event = self.relations.events.get(label)
                return self.ref_badge(tone, words("kind", event.kind) if event else t("ref.resolved"), shown, state)
            return self.ref_badge(tone, problem, shown, state, sep=True,
                                  note=self.recorded_later(judged, depth) + self.candidates_note(judged))
        if kind == "run":
            if found:
                return self.ref_badge(tone, t("refs.run_found"), f'<a href="{esc(self.run_link(label, depth))}">{esc(label)}</a>', state)
            return self.ref_badge(tone, problem, f'{t("refs.run")} {esc(label)}', state, sep=True)
        if kind == "visualization_version" and judged.get("version_binding") != "none":
            run, chart = target.get("run_id"), target.get("visualization_id")
            if found and run:
                title = next((v.get("title") for v in relations.run_visualizations(self.run_by_id.get(run) or {})
                              if v.get("id") == chart and v.get("title")), None)
                anchor = f"#chart-{href(str(chart))}"
                shown = (f'<a href="{esc(self.run_link(run, depth, anchor))}" title="{esc(label)}"{USER}>'
                         f'{esc(title or chart)}</a>')
                return self.ref_badge(tone, t("ref.resolved"), shown, state, sep=True)
            return self.ref_badge(tone, problem, f'{t("refs.chart")} <span{USER}>{esc(label)}</span>', state,
                                  sep=True, note=self.candidates_note(judged))
        if kind == "visualization_version":
            # A logical key is `script::chart`: the chart's id shows, the whole key is its title.
            key = str(target.get("key") or target.get("token") or label)
            shown = (f'<a href="{esc(self.chart_anchor(key, depth))}"><span title="{esc(key)}"{USER}>'
                     f'{esc(key.rsplit("::", 1)[-1])}</span></a>')
            return self.ref_badge(tone, problem, shown, state, sep=True, note=self.candidates_note(judged))
        if kind in ("data_observation", "dataset_import"):
            item = str(target.get("key") or target.get("path") or label)
            path = item if item in self.view_by_path else self.views.get(item)
            # A name or path that is no dataset is still what people and scripts wrote (lang="").
            shown = self.path_html(path, depth) if path else user_text(item)
            return self.ref_badge(tone, t("ref.resolved") if found else problem, shown, state, sep=True,
                                  note=self.candidates_note(judged))
        if kind == "context_version" and found:
            shown = (f'<a href="{esc(self.root(depth))}context.html#v-{esc(Path(label).stem)}" '
                     f'title="{esc(label)}">CONTEXT.md</a>')
            return self.ref_badge(tone, t("ref.resolved"), shown, state, sep=True)
        return self.ref_badge(tone, t("ref.resolved") if found else problem, f"<span{USER}>{esc(label)}</span>",
                              state, sep=True, note=self.candidates_note(judged))

    @staticmethod
    def ref_badge(tone: str, lead: str, target: str, state: str, *, sep: bool = False,
                  tail: str = "", note: str = "") -> str:
        """A reference badge: `lead target`, a dot before the target when `lead` is a judgment rather
        than its kind, then the judgment of a relation's target (`tail`) and a note. Its mark (✓ ⚠ ⊗ –
        →, REF_STATE's) is the stylesheet's, by tone: decoration beside the words, a few bytes less on
        every badge of a list (design 9)."""
        return (f'<span class="ref tone-{tone}" data-state="{esc(state)}">{lead}{" ·" if sep else ""} {target}'
                f'{f" · {tail}" if tail else ""}{note}</span>')

    def candidates_note(self, judged: dict) -> str:
        """How many versions (or records) a name left unbound or ambiguous could be."""
        candidates = judged.get("candidates")
        if not candidates or judged["state"] not in ("version_unbound", "ambiguous"):
            return ""
        # An event target's candidates are the journal lines with its id; the others' are versions.
        what = "refs.candidate_records" if judged.get("type") in relations.EVENT_TARGETS else "refs.candidate_versions"
        out = f' <small class="ref-note" title="{esc(", ".join(candidates))}">{t(what, n=len(candidates))}</small>'
        inferred = judged.get("inferred_candidates") or []
        if inferred:
            # The versions hold Visualization ids people and scripts wrote: only their count beside
            # the kit's title (#10, lang="").
            out += f' <small class="ref-note"{inferred_title()}>{t("inferred.count_note", n=len(inferred))}</small>'
        return out

    def recorded_later(self, judged: dict, depth: int) -> str:
        """` recorded later: e187` for a record cited before it was written: the read finds it
        `missing` (`record_absent`) and the one line with its id comes after the citing record."""
        if judged.get("reason") != "record_absent" or judged.get("type") not in relations.EVENT_TARGETS:
            return ""
        journal = self.model.journal
        event_id = (judged.get("target") or {}).get("event_id")
        lines = journal.lines_of.get(event_id, []) if isinstance(event_id, str) else []
        citing = journal.event_of(judged.get("from") or "")
        seq = journal.seq_of.get(lines[0]) if len(lines) == 1 else None
        if seq is None or citing is None or citing.seq is None or seq <= citing.seq:
            return ""
        link = self.relations.content(event_id, depth) or self.relations.link(event_id, depth)
        return f' <small class="ref-note">{t("refs.recorded_later")} <a href="{esc(link)}">{esc(event_id)}</a></small>'

    def closed_badge(self, resolver: dict, depth: int = 0, closed: str | None = None) -> str:
        """The badge of a record another one closes by refs.resolves, as the lists select (C1):
        resolved only when the read finds that reference, and otherwise the state it judges, in
        words (a forward refs.resolves is `missing`, record_absent). The reference is the resolver's
        `resolves` one naming `closed` (a /1 `refs.resolves` and a /2 reference alike)."""
        source = self.source(resolver) or ""
        link = "#" + esc(self.anchor(resolver)) if depth == 0 and source in self.recent_sources else esc(self.relations.content(source, depth) or self.relations.link(source, depth))
        closing = [j for j in self.model.references(source) if j.get("relation") == "resolves"]
        judged = next((j for j in closing if (j.get("target") or {}).get("event_id") == closed),
                      closing[0] if closing else None)
        if judged is not None and judged["state"] == "resolved":
            return (f'<a class="badge tone-good" href="{link}"><span aria-hidden="true">✓</span> '
                    f'{t("records.resolved_by")} {esc(source)}</a>')
        state = judged["state"] if judged is not None else "unknown"
        tone, mark = REF_STATE.get(state, ("muted", "?"))
        reason = judged.get("reason") if judged is not None else None
        return (f'<a class="badge tone-{tone}" href="{link}"{attr("title", "records.closed_title", id=source, state=state)}>'
                f'<span aria-hidden="true">{mark}</span> {t("records.closed_by")} {esc(source)} · {ref_words(state, reason)}</a>')

    def record_state_html(self, event: dict, depth: int) -> str:
        """A record's state, shown only when it is not as it was made (design 8.1): a question or
        next step open or closed (C1's selection); a record corrected, corrected by several records,
        or withdrawn (ADR-0006 6). A current record shows nothing."""
        kind = event.get("kind")
        resolver = self.resolved.get(event.get("id"))
        closed = ""
        if kind in core.OPEN_KINDS + core.KEY_KINDS and resolver:
            closed = self.closed_badge(resolver, depth, event.get("id"))
        elif kind in core.OPEN_KINDS:
            closed = badge("warning", "●", t("badge.open"))
        # A finding a record closed can also have been corrected or withdrawn: both are said.
        state = self.model.journal.replay().state_of(self.source(event) or "") or {}
        shown = state.get("state")
        if shown not in RECORD_STATE:
            return closed
        tone, mark = RECORD_STATE[shown]
        by = state.get("corrected_by") or []
        if shown == "superseded" and by:
            link = self.relations.content(by[0], depth) or self.relations.link(by[0], depth)
            return closed + (f'<a class="badge tone-{tone}" href="{esc(link)}"><span aria-hidden="true">{mark}</span> '
                             f'{t("record.superseded_by")} {esc(by[0])}</a>')
        if shown == "conflict":
            return closed + badge(tone, mark, t("record.conflict", n=len(by)), title_attr(", ".join(by)))
        return closed + badge(tone, mark, t("record." + shown))

    def record_row(self, ev: dict, depth: int, *, row_id: str, time_only: bool = False) -> str:
        """One record in a list (design 8.1): its kind, its text as written with its state when that
        changed, then its id, time and actor and its references as badges, and one link to its
        relations. Rows of every list are alike."""
        source = self.source(ev)
        kind = ev.get("kind")
        shown_id = esc(ev.get("id"))
        if self.anchor(ev) != ev.get("id"):
            shown_id += f' <small{attr("title", "events.shared_id_title")}>{esc(source)}</small>'
        state = self.record_state_html(ev, depth)
        return (f'<li class="ev rec" id="{esc(row_id)}" data-kind="{esc(kind)}" data-selection="{quote(source or "", safe="")}">'
                f'<span class="rec-kind kind-{esc(kind)}"><span class="dot" aria-hidden="true"></span>{words("kind", kind)}</span>'
                f'<div class="rec-main"><div class="rec-line"><span class="rec-text"{USER}>{esc(ev.get("text"))}</span>{state}</div>'
                f'<div class="rec-meta"><span class="rec-id">{shown_id}</span> · {fmt_time(ev.get("ts"), with_date=not time_only)} · '
                f'<span class="actor">{words("actor", ev.get("actor"))}</span>'
                f'{self.refs_html(ev, depth, limit=ROW_REFS, empty=kind in core.KEY_KINDS)}</div></div>'
                f'{self.relation_link(source, depth, record=True)}</li>')

    def kind_nav(self, family: str, depth: int, current: str | None = None) -> str:
        """A list's kind filter (design 8.2, owner decision A): links to each kind's own list pages,
        counted over every record, so it filters the whole journal and works without a script.
        `family`: timeline (every kind) or insights (findings, decisions and assumptions)."""
        if family not in self._kind_counts:
            records = self.events if family == "timeline" else [e for e in self.events if e.get("kind") in core.KEY_KINDS]
            self._kind_counts[family] = (records, Counter(kind_list(e.get("kind")) for e in records))
        records, counts = self._kind_counts[family]
        root = self.root(depth)

        def link(target: str, words_html: str, n: int, on: bool) -> str:
            mark = ' class="on" aria-current="page"' if on else ""
            return f'<a href="{target}"{mark}>{words_html} <span class="n">{fmt_num(n)}</span></a>'

        order = sorted(counts, key=lambda k: (core.EVENT_KINDS.index(k) if k in core.EVENT_KINDS else len(core.EVENT_KINDS), str(k)))
        items = [link(f"{root}index.html#{family}", t("filters.all"), len(records), current is None)]
        items += [link(f"{root}lists/kind-{kind_file(k)}-0.html", words("kind", k), counts[k], k == current) for k in order]
        return f'<nav class="kinds"{attr("aria-label", "filters.by_kind")}>{"".join(items)}</nav>'

    def read_footer(self) -> str:
        """The read every page was made from, as analytics_status names it (the names are
        identifiers, the same in every language)."""
        text = esc(f"as_of_seq {self.model.as_of_seq} · source_revision {self.model.source_revision}")
        if self.model.updating:
            text += " · " + t("footer.updating")
        return text

    def producer_html(self, candidates: list[str], depth: int) -> str:
        links = [f'<a href="{esc(self.run_link(r, depth))}">{esc(r)}</a>'
                 for r in candidates[:SHOWN_CANDIDATES]]
        more = len(candidates) - SHOWN_CANDIDATES
        return ", ".join(links) + (" " + t("list.and_more", n=more) if more > 0 else "")

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
                return f'<div class="empty">{t("chart.file_missing")}</div>'
            if size > PLOTLY_EMBED_LIMIT:
                return (f'<div class="empty">{t("chart.too_large", size=fmt_bytes(size))} '
                        f'<a href="{self.file_link(files["plotly"], depth)}">{t("chart.open_json")}</a></div>')
            self.uses_plotly = True
            self._fig += 1
            payload = path.read_text(encoding="utf-8").replace("</", "<\\/")
            return (f'<div class="plotly-chart" data-fig="fig{self._fig}" role="img" '
                    f'aria-label="{title}"{USER}></div><script type="application/json" '
                    f'id="fig{self._fig}">{payload}</script>')
        if files.get("html"):
            return (f'<iframe class="chart-frame" src="{self.file_link(files["html"], depth)}" '
                    f'loading="lazy" title="{title}"{USER}></iframe>')
        return f'<div class="empty">{t("chart.no_file")}</div>'

    def chart_card(self, chart: dict, depth: int, *, full=False) -> str:
        run = self.run_by_id.get(chart["run"]) or {}
        fresh = self.fresh.of(chart["run"])
        script = run.get("script") or {}
        badges = []
        badges += stale_badges(fresh)
        badges += producer_badges(fresh)
        if fresh["code"] == "changed":
            badges.append(badge("warning", "✎", t("badge.script_edited"), attr("title", "badge.script_edited_title")))
            if fresh.get("code_reason") == core.LINE_ENDINGS_ONLY:
                badges.append(line_endings_badge())
        elif fresh["code"] == "missing":
            badges.append(badge("warning", "✎", t("badge.script_missing"), attr("title", "badge.script_missing_title")))
        versions = chart.get("versions") or []
        badges.insert(0, self.history.chart_badge(chart["key"], chart["run"]))
        if chart.get("inferred_versions"):
            badges.append(inferred_badge("inferred.title_versions", n=chart["inferred_versions"], total=len(versions)))
        if chart.get("retired"):
            badges.append(badge("muted", "↺", t("badge.retired")))
        run_page = self.run_link(chart["run"], depth)
        line = chart.get("line")
        code_link = (f'<a href="{run_page}#code"{USER}>{esc(script.get("path"))}</a> '
                     f'<span class="sha">@{short(script.get("sha256"))}</span>')
        if line:
            code_link += f' · <a href="{run_page}#L-main-{line}">L{line}</a>'
        inputs = []
        # The inputs several successful runs output the same bytes as: marked here, beside the path
        # people wrote, as the card's Producer ambiguous badge says.
        ambiguous = set(fresh.get("ambiguous_inputs") or [])
        for entry in fresh["inputs"]:
            state = entry["state"] if entry["state"] in INPUT_STATE else "unknown"
            tone, icon = INPUT_STATE[state]
            reason = (f" {line_endings_badge()}" if entry.get("reason") == core.LINE_ENDINGS_ONLY
                      else "")
            several = (f'<span class="state tone-warning"{attr("title", "card.producer_ambiguous")}>?</span>'
                       if entry["path"] in ambiguous else "")
            inputs.append(f'<span class="input">{self.path_html(entry["path"], depth)} '
                          f'<span class="sha">{short(entry.get("sha256"))}</span>'
                          f'<span class="state tone-{tone}"{attr("title", "card.file_now." + state)}>{icon}</span>'
                          f'{several}{reason}</span>')
        if chart.get("data"):
            data_html = (f'<a href="{run_page}#chart-{esc(chart["id"])}-data">'
                         f'{t("card.rows_columns", rows=fmt_num(chart.get("data_rows")), columns=len(chart.get("data_columns") or []))}</a>'
                         f' · <a href="{self.file_link(chart["data"], depth)}">parquet</a>')
        elif chart.get("kind") == "plotly":
            data_html = f'<span class="muted">{t("card.in_plotly_json")}</span>'
        else:
            data_html = f'<span class="muted">{t("card.data_not_recorded")}</span>'
        # The card's versions (design 5.1, mockup 6-C): what a reader needs first about the latest, and
        # the folded strip with the way to the whole history on the latest version's run page.
        version_links = (self.history.chart_note(chart["key"], card=True) + self.history.chart_strip_row(
            chart["key"], chart["id"], chart["run"], depth)) if full else ""
        card_id = ("chart-" if full else "recent-chart-") + slug_key(chart["key"])
        classes = "chart-card" + (" is-stale" if fresh["stale"] else "") + \
            (" is-retired" if chart.get("retired") else "")
        caption = (f'<p class="caption"{USER}>{esc(chart["caption"])}</p>' if chart.get("caption")
                   else "")
        return f"""
<article class="{classes}" id="{card_id}" data-selection="{quote(chart["run"] + "/" + str(chart["id"]), safe="")}">
  <header><h3{USER}>{esc(chart.get('title') or chart['id'])}</h3><div class="badges">{''.join(badges)}</div></header>
  <div class="chart-body">{self.chart_html(chart, depth)}</div>
  {caption}
  <dl class="prov">
    <div><dt>{t("card.code")}</dt><dd>{code_link}</dd></div>
    <div><dt>{t("card.input_data")}</dt><dd>{''.join(inputs) or f'<span class="muted">{t("card.no_inputs")}</span>'}</dd></div>
    <div><dt>{t("card.attached_data")}</dt><dd>{data_html}</dd></div>
    <div><dt>{t("card.run")}</dt><dd><a href="{run_page}">{esc(chart['run'])}</a> · {fmt_time(run.get('started_at'))} · {run.get('duration_s', '-')}s</dd></div>
  </dl>
  {version_links}
  {self.relation_actions(chart["run"] + "/" + str(chart["id"]), depth)}
</article>"""

    # --- tables ---

    def table(self, columns, rows, *, types=None, klass="data", data_head=True) -> str:
        """A table of data: its text cells, and its column names when they are the data's
        (`data_head`), are the data's text (USER); `null` and numbers are the kit's. Column names
        that are not the data's are the kit's words, as HTML (t)."""
        lang = USER if data_head else ""
        head = "".join(
            f'<th{lang}>{esc(c) if data_head else c}{f"<small>{esc(types[i])}</small>" if types else ""}</th>'
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
            note = (f'<p class="muted">{t("data.preview_first", shown=min(limit, total), total=total)}</p>'
                    if total > limit else "")
            return note + self.table(frame.columns, [[cell(v, 120) for v in r] for r in frame.rows()],
                                     types=[str(dtype) for dtype in schema.values()])
        return f'<div class="empty">{t("data.unreadable")}</div>'

    # --- events ---

    def events_html(self, events, depth: int, *, prefix="") -> str:
        """Records by day as given (newest first), a row each (record_row) under each day's date."""
        if not events:
            return f'<div class="empty">{t("events.empty")}</div>'
        out, day = [], None
        for ev in events:
            ts = str(ev.get("ts") or "")
            if ts[:10] != day:
                day = ts[:10]
                out.append(f'<li class="day">{fmt_date(ts)}</li>')
            # A record whose id other lines share is named by its line, and so is its anchor.
            out.append(self.record_row(ev, depth, row_id=prefix + self.anchor(ev), time_only=True))
        return f'<ol class="timeline records">{"".join(out)}</ol>'

    # --- page shell ---

    def page(self, depth: int, title: str, crumb: str, body: str, kind: str, *,
             page_path: str | None = None, selection: str = "", title_key: str = "",
             title_args: dict | None = None, back: str = "", actions: str = "") -> str:
        """A page. Its title is `title` as written, or the kit's words of `title_key` (then the
        page switches them too); either way the project's title follows as written. A page other
        than the index starts with its way back, named by where it goes (design 8.1): the index
        section `back` (by default the one its kind is listed in), or the page a reader came from
        (workspace.js), then `actions`."""
        root = self.root(depth)
        page_path = page_path or ("index.html" if kind == "index" else "context.html" if kind == "context" else "")
        if kind != "index":
            back = back or BACK.get(kind, "overview")
            body = (f'<div class="workspace-actions"><a class="back" data-return data-back="back.tab.{back}" '
                    f'href="{root}index.html#{back}"><span aria-hidden="true">←</span> {t("back.tab." + back)}</a>{actions}</div>' + body)
        v = f"?v={self.build}"  # new URLs per build, so a reload never runs cached assets
        # Plotly is read when a figure is about to show (workspace.js), not before the page paints.
        plotly = (f' data-plotly="{root}assets/plotly.min.js?v={core.KIT_VERSION}"' if self.uses_plotly else "")
        self.uses_plotly = False
        if title_key:
            # The kit's words first, then what follows them as written (a selected identity).
            title = i18n.message(title_key, **(title_args or {})) + (f" · {title}" if title else "")
            switched = f' data-i18n-doctitle="{title_key}"' + (
                f' data-i18n-doctitle-args="{esc(json.dumps(title_args, sort_keys=True))}"' if title_args else "")
        else:
            switched = ""
        return f"""<!doctype html>
<html lang="en" data-root="{root}" data-build="{self.build}" data-source="{esc(self.model.source_revision)}" data-page="{href(page_path)}" data-selection="{quote(selection, safe="")}"{switched}{plotly}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · {esc(self.project_title)}</title>
<link rel="stylesheet" href="{root}assets/workspace.css{v}">
<link rel="stylesheet" href="{root}assets/code.css{v}">
<script src="{root}assets/i18n.js{v}"></script>
</head>
<body class="page-{kind}">
<header class="topbar">
  <div class="crumbs"><a href="{root}index.html"{USER}>{esc(self.project_title)}</a>{crumb}</div>
  <div class="build" id="page-menu">{t("chrome.rendered")} {fmt_time(self.built_at)} · <label class="live"><input type="checkbox" id="live" checked> {t("chrome.auto_reload")}</label></div>
</header>
<main><p class="state-notice" role="status" hidden></p>{body}</main>
<footer>Aelix Analytics {core.KIT_VERSION} · {t("chrome.generated")} · <span class="read-basis">{t("chrome.read_at")} {self.read_footer()}</span></footer>
<script src="{root}assets/workspace-state.js{v}"></script>
<script src="{root}assets/workspace.js{v}"></script>
</body>
</html>
"""


def imported_note(site) -> str:
    """Where the imported data is (kept unchanged): data/imported, and data/raw a lab.json project's."""
    if site.p.layout.imported_dir == "data/raw":
        return t("data.imported_note.raw")
    if any(ds["kind"] == "raw" for ds in site.datasets):
        return t("data.imported_note.both")
    return t("data.imported_note.imported")


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
    # The catalogs are .json, which kit_sha leaves out: a change of words alone rebuilds the pages.
    parts.append(i18n.digest())
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def build(project: core.Project, *, if_changed: bool = False) -> dict:
    """Build the Workspace. Builds are serialized per project; a waiting --if-changed build rechecks
    the fingerprint after the lock, so queued triggers collapse into one build. A write: refused
    before its lock file is made where this kit does not write (core.admit, Issue #13)."""
    core.admit(project, "render")
    with project.platform.locks.exclusive(project.state_dir / project.layout.workspace_lock):
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
    (out / "relations").mkdir(parents=True, exist_ok=True)
    (out / "lists").mkdir(parents=True, exist_ok=True)
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
    # A page per script path with its code versions (design 5.3).
    (out / site.history.directory).mkdir(parents=True, exist_ok=True)
    scripts = {site.history.page_name(script): script for script in site.model.scripts()}
    for name, script in scripts.items():
        _write(out / site.history.directory / name, site.history.script_html(script), project.platform)
        pages += 1
    _prune(out / site.history.directory, set(scripts))
    relation_names = set()
    for identity in sorted(site.relations.identities):
        count = len(site.model.reference_neighborhood(identity))
        for n in range(max(1, (count + site.relations.page_size - 1) // site.relations.page_size)):
            filename = site.relations.filename(identity, n)
            _write(out / "relations" / filename, site.relations.render(identity, n), project.platform)
            relation_names.add(filename)
            pages += 1
    _prune(out / "relations", relation_names)
    pages += write_list_pages(site, out)
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
    for name in ("workspace.css", "workspace.js", "workspace-state.js"):
        with resources.as_file(ASSETS / name) as source:
            shutil.copyfile(source, assets / name)
    # The runtime and both catalogs in one script the page loads in its head (i18n.py).
    core.write_text(assets / "i18n.js", i18n.script())
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
    return esc(", ".join(items[:SHOWN_PLACES])) + (" " + t("list.and_more", n=more) if more > 0 else "")


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
        later = (" " + t("notice.newer_kit_all") if newer == len(places) else
                 " " + t("notice.newer_kit_some", n=newer) if newer else "")
        items.append(f"<li><strong>{t('notice.unsupported', n=len(places))}</strong> "
                     f"{t('notice.unsupported_where')} {_places(places)}.{later}</li>")
    journal = m.journal
    if journal.damaged:
        recovered = (f"; {t('notice.recovered', n=len(journal.recovered))} "
                     f"({_places([f'journal:{n}' for n in journal.recovered])})"
                     if journal.recovered else "")
        items.append(f"<li><strong>{t('notice.damaged_lines', n=len(journal.damaged))}</strong> "
                     f"({_places([f'journal:{n}' for n in journal.damaged])}){recovered}.</li>")
    if m.damaged_files:
        places = [f"{f['location']} ({f['reason']})" for f in m.damaged_files]
        items.append(f"<li><strong>{t('notice.damaged_files', n=len(places))}</strong> — "
                     f"{_places(places)}.</li>")
    shared = {i: [f"journal:{n}" for n in lines] for i, lines in journal.lines_of.items()
              if len(lines) > 1}
    if shared:
        listed, cut = core.shared_ids_text(shared, SHOWN_PLACES)
        rest = " " + t("notice.shared_ids_more") if cut else ""
        items.append(f"<li><strong>{t('notice.shared_ids')}</strong> {esc(listed)}{rest} — "
                     f"{t('notice.shared_ids_meaning')}</li>")
    if m.updating:
        items.append(f"<li><strong>{t('notice.updating')}</strong> {t('notice.updating_meaning')}</li>")
    if not items:
        return ""
    return (f'<div class="callout tone-warning"><strong>{t("notice.title")}</strong>'
            f'<ul>{"".join(items)}</ul></div>')


def line_endings_badge() -> str:
    """A changed file whose bytes differ from the record only in their line ends (ADR-0007
    decision 4)."""
    return badge("muted", "↵", t("badge.line_endings"), attr("title", "badge.line_endings_title"))


# A relation found only by reading a path a kit before 0.5.0 recorded as today's identity path
# (ADR-0006 9): an inference from today's links and names, never an observed or confirmed link.
def inferred_title(key: str = "inferred.title", **args) -> str:
    """The title attribute of such an inference, `key` saying which (inferred.title_*)."""
    return attr("title", key, since=core.PATH_IDENTITY_SINCE, **args)


def inferred_badge(key: str = "inferred.title", **args) -> str:
    """The mark of a relation found only through an older kit's path."""
    return badge("muted", "≈", t("inferred.badge"), inferred_title(key, **args))


def inferred_mark(run_id: str, runs) -> str:
    """` · inferred from an older kit's path` beside a run listed only through such a reading."""
    if not runs or run_id not in runs:
        return ""
    return f' · <span class="ref-note"{inferred_title()}>{t("inferred.note")}</span>'


def inferred_count_html(runs) -> str:
    """` (N inferred)` for the runs of a count found only through an older kit's path."""
    if not runs:
        return ""
    return (f' <small class="ref-note"{inferred_title("inferred.title_runs", runs=", ".join(runs))}>'
            f'{t("inferred.count", n=len(runs))}</small>')


def inferred_runs_note(runs) -> str:
    """` (A, B: inferred from an older kit's path)` for the runs of a list found only so."""
    if not runs:
        return ""
    more = len(runs) - SHOWN_CANDIDATES
    named = esc(", ".join(runs[:SHOWN_CANDIDATES])) + (" " + t("list.and_more", n=more) if more > 0 else "")
    return f' <small class="ref-note"{inferred_title()}>({named}: {t("inferred.note")})</small>'


def title_ids(ids: list[str]) -> str:
    """Run ids as a value of the kit's words in a title: the first few, and "…" for the rest, whose
    number the message says beside them (an id is the same in every language; "and 2 more" would
    not be)."""
    return ", ".join(ids[:SHOWN_CANDIDATES]) + (", …" if len(ids) > SHOWN_CANDIDATES else "")


def stale_badges(fresh: dict) -> list[str]:
    """Why a run's charts may be out of date: an input of its own changed or is missing, or a run that
    output the same bytes as one of its inputs is stale. That run is a producer candidate, so the
    badge says so and does not say this run's input changed. A badge's title holds the kit's words
    and counts: the inputs a person wrote are listed by the card and the run page beside it."""
    out = []
    if fresh["changed_inputs"]:
        out.append(badge("critical", "⚠", t("badge.input_changed"),
                         attr("title", "badge.input_changed_title", n=len(fresh["changed_inputs"]))))
    if fresh["stale_upstream"]:
        runs, inferred = fresh["stale_upstream"], fresh.get("inferred_upstream") or []
        if inferred:
            title = attr("title", "stale.upstream_title_inferred", n=len(runs), runs=title_ids(runs),
                         m=len(inferred), inferred=title_ids(inferred))
        else:
            title = attr("title", "stale.upstream_title", n=len(runs), runs=title_ids(runs))
        out.append(badge("warning", "⚠", t("badge.upstream_stale"), title))
    return out


def producer_badges(fresh: dict) -> list[str]:
    """Badges for a run whose inputs several runs could have output, or whose producers cycle."""
    out = []
    if fresh.get("ambiguous_inputs"):
        out.append(badge("warning", "?", t("badge.producer_ambiguous"),
                         attr("title", "badge.producer_ambiguous_inputs_title", n=len(fresh["ambiguous_inputs"]))))
    if fresh.get("cycle"):
        out.append(badge("warning", "↻", t("badge.producer_cycle"), attr("title", "badge.producer_cycle_title")))
    if fresh.get("inferred_inputs"):
        # The paths are what people and scripts wrote, so they are not in the kit's title: the run
        # page's inputs say which (read as …).
        out.append(inferred_badge("inferred.title_inputs", n=len(fresh["inferred_inputs"])))
    return out


def origin_html(site: Site, ds: dict, depth: int) -> str:
    """Where a dataset's current bytes come from, as far as the records say: the runs whose output
    had this path and these bytes (named as such, not as the run that made them), the imported file,
    or neither."""
    state, candidates = ds.get("producer_state"), ds.get("producer_candidates") or []
    inferred = (ds.get("inferred") or {}).get("producer_candidates") or []
    if state == "resolved":
        run = candidates[0]
        return (f'<a href="{esc(site.run_link(run, depth))}"{attr("title", "data.same_bytes_title")}>'
                f"{esc(run)}</a>" + (" " + inferred_badge("inferred.title_run", run=run) if inferred else ""))
    if state == "ambiguous":
        return (badge("warning", "?", t("badge.producer_ambiguous"),
                      attr("title", "badge.producer_ambiguous_runs_title", n=len(candidates)))
                + " " + site.producer_html(candidates, depth) + inferred_runs_note(inferred))
    if ds.get("unsupported_record"):
        record = ds["unsupported_record"]
        return badge("muted", "?", t("badge.import_not_read"),
                     attr("title", "badge.import_not_read_title", reason=record["reason"]))
    if ds.get("source"):
        return user_text(Path(ds["source"]).name)
    if state == "unknown":
        return badge("muted", "?", t("badge.producer_unknown"), attr("title", "badge.producer_unknown_title"))
    if ds.get("written_by"):
        return badge("warning", "⚠", t("badge.no_matching_output"),
                     attr("title", "badge.no_matching_output_title",
                          runs=", ".join(ds["written_by"][::-1][:SHOWN_CANDIDATES])))
    return badge("critical", "⚠", t("badge.no_run_output"))


def producer_cell(site: Site, judged: dict | None, depth: int) -> str:
    """The producer judgment of one input of a run (ADR-0006 4): every successful run that output
    its path with its recorded bytes and did not start after the run, none chosen."""
    if not judged:
        return '<span class="muted">-</span>'
    candidates = judged.get("candidates") or []
    inferred = judged.get("inferred_candidates") or []
    if judged["producer"] == "resolved":
        return site.producer_html(candidates, depth) + (
            " " + inferred_badge("inferred.title_run", run=candidates[0]) if inferred else "")
    if judged["producer"] == "ambiguous":
        return (badge("warning", "?", t("badge.producer_ambiguous"),
                      attr("title", "badge.producer_ambiguous_count_title", n=len(candidates)))
                + " " + site.producer_html(candidates, depth) + inferred_runs_note(inferred))
    if judged["producer"] == "unknown":
        return badge("muted", "?", t("input.unknown"), attr("title", "badge.no_recorded_hash_title"))
    return '<span class="muted">-</span>'


# --- index ---

def index_page(site: Site) -> str:
    p = site.p
    context_text = (p.context.read_text(encoding="utf-8", errors="replace")
                    if p.context.is_file() else "")
    overview = overview_section(site, context_text)

    all_cards = "".join(site.chart_card(c, 0, full=True) for c in site.charts[:CHART_INDEX_LIMIT])
    charts = f"""
<section id="charts" class="tab" data-count="{len(site.status.charts)}">
  <div class="toolbar"><h2>{t("tab.charts")}</h2><label class="toggle"><input type="checkbox" id="show-retired"> {t("charts.show_retired")}</label></div>
  <p class="muted">{t("charts.lead")}</p>
  {list_notice("charts", len(site.charts), CHART_INDEX_LIMIT)}
  <div class="gallery">{all_cards or f'<div class="empty">{t("charts.empty")}</div>'}</div>
</section>"""

    timeline = f"""
<section id="timeline" class="tab" data-count="{len(site.events)}">
  <div class="toolbar"><h2>{t("tab.timeline")}</h2></div>
  {site.kind_nav("timeline", 0)}
  {list_notice("timeline", len(site.events), INDEX_LIMIT)}
  {site.events_html(site.events[-INDEX_LIMIT:][::-1], 0)}
</section>"""

    data_rows = []
    for ds in site.datasets[:INDEX_LIMIT]:
        view = site.view_by_path.get(ds["path"])
        origin = origin_html(site, ds, 0)
        label = dataset_label(ds["name"], view)
        also = f' <small>{t("data.view")} <code>{esc(view)}</code></small>' if label != view else ""
        data_rows.append(
            f'<tr id="data-{esc(view)}"><td><a href="data/{href(view)}.html"{USER}>{esc(label)}</a>{also}</td><td>{words("data.kind", ds["kind"])}</td>'
            f'<td class="num">{fmt_num(ds.get("rows"))}</td><td class="num">{fmt_num(ds.get("columns"))}</td>'
            f'<td class="num">{fmt_bytes(ds.get("bytes"))}</td><td><code{USER}>{esc(ds["path"])}</code></td>'
            f'<td>{origin}</td><td class="num">{len(ds.get("used_by") or [])}'
            f'{inferred_count_html((ds.get("inferred") or {}).get("used_by"))}</td>'
            f'<td class="sha">{short(ds.get("sha256"), 12)}</td></tr>')
    data_head = "".join(f"<th>{t('data.col.' + c)}</th>" for c in
                        ("dataset", "kind", "rows", "columns", "size", "path", "source", "used_by")) + "<th>sha256</th>"
    data = f"""
<section id="data" class="tab" data-count="{len(site.datasets)}">
  <div class="toolbar"><h2>{t("tab.data")}</h2></div>
  {list_notice("data", len(site.datasets), INDEX_LIMIT)}
  <p class="muted">{imported_note(site)} {t("data.lead")}</p>
  {(f'<div class="table-wrap"><table class="list"><thead><tr>{data_head}</tr></thead><tbody>' + ''.join(data_rows) + '</tbody></table></div>') if data_rows else f'<div class="empty">{t("data.empty")}</div>'}
</section>"""

    run_rows = []
    for run in site.runs[-INDEX_LIMIT:][::-1]:
        repro = relations.run_reproduction(run) or {}
        verdict = ""
        if repro:
            tone = {"match": "good", "mismatch": "critical", "failed": "critical"}.get(
                repro.get("verdict"), "muted")
            verdict = badge(tone, "↻", f'{t("runs.rerun")} {verdict_label(repro.get("verdict"))}')
        run_rows.append(
            f'<tr id="run-{esc(run["id"])}"><td>{fmt_time(run.get("started_at"))}</td><td><a href="runs/{href(run["id"])}.html">{esc(run["id"])}</a></td>'
            f'<td>{status_badge(run.get("status"))} {verdict}</td><td{USER}>{esc(run.get("title"))}</td>'
            f'<td class="num">{len(relations.run_visualizations(run))}</td><td class="num">{len(run.get("inputs") or [])}</td>'
            f'<td class="num">{sum(1 for o in run.get("outputs") or [] if o.get("change") != "deleted")}</td>'
            f'<td class="num">{run.get("duration_s", "-")}</td></tr>')
    run_head = "".join(f"<th>{t('runs.col.' + c)}</th>" for c in
                       ("started", "run", "status", "title", "charts", "inputs", "outputs", "seconds"))
    runs = f"""
<section id="runs" class="tab" data-count="{len(site.runs)}">
  <div class="toolbar"><h2>{t("tab.runs")}</h2></div>
  {list_notice("runs", len(site.runs), INDEX_LIMIT)}
  {(f'<div class="table-wrap"><table class="list"><thead><tr>{run_head}</tr></thead><tbody>' + ''.join(run_rows) + '</tbody></table></div>') if run_rows else f'<div class="empty">{t("runs.empty")}</div>'}
</section>"""

    context_html = render_markdown(context_text) if context_text else \
        f'<div class="empty">{t("context.empty")}</div>'
    version_items = "".join(
        f'<li><a href="context.html#v-{esc(v.stem)}">{fmt_time(core._version_time(v))}</a></li>'
        for v in site.versions[::-1][:12])
    # The context block's own selection, from the file as aelix's read tool numbers its lines.
    _, injected, omitted, _ = core.context_selection(core.context_file_text(p))
    prompt_note = (
        f'<p class="muted">{t("context.in_prompt")} {section_names(injected) or t("context.none")}.'
        + (f' {t("context.by_range")} {section_names(omitted)} — {t("context.by_range_tail")}'
           if omitted else "") + "</p>")
    context = f"""
<section id="context" class="tab">
  <div class="toolbar"><h2>{t("tab.context")} <small>CONTEXT.md</small></h2><a href="context.html">{t("context.versions_link", n=len(site.versions))} →</a></div>
  {prompt_note}
  <div class="columns wide-left">
    <article class="panel markdown"{USER if context_text else ""}>{context_html}</article>
    <aside class="panel"><h3>{t("context.recent_versions")}</h3><ul class="plain">{version_items or f'<li class="muted">{t("context.no_versions")}</li>'}</ul></aside>
  </div>
</section>"""

    return site.page(0, "", "", overview + insights_section(site) + charts + site.map.tab_html() + timeline + data + runs + context
                     + asset_section(site) + evaluation_section(site), "index", title_key="page.workspace")


# How many records each Overview list shows before its link to all of them (design 7.1), and how
# many characters of a pinned CONTEXT.md section the direction panel shows before its link.
OVERVIEW_ROWS = 6
OVERVIEW_CHARTS = 3
PINNED_CHARS = 600


def overview_section(site: Site, context_text: str) -> str:
    """The Overview (design WORKSPACE-READABILITY 7.1): where the analysis is going (CONTEXT.md's
    pinned sections as written, and the analysis focus), the analysis status, each state apart, the
    map's summary, the current findings and the open items six each, and the rest folded."""
    p, status = site.p, site.status
    _, sections = core.context_sections(core.context_file_text(p)) if context_text else ("", [])
    goal_section = next((sec for sec in sections if core.GOAL_RE.match(section_heading(sec))), None)
    goal = core.context_goal(context_text)
    key_all = [e for e in site.events if e.get("kind") in core.KEY_KINDS]
    kicker = (f'<p class="kicker">{t("overview.kicker")} · {t("overview.as_of")} {fmt_time(site.built_at)} · '
              f'{t("overview.record", n=site.model.as_of_seq or 0)}</p>')
    if goal_section is not None:
        body, cut = section_body(goal_section)
        goal_html = (f'<p class="goal-label">{t("overview.goal")}</p><div class="goal markdown"{USER}>{render_markdown(body)}</div>'
                     + (f'<p class="panel-link"><a href="#context">{t("overview.read_rest")}</a></p>' if cut else ""))
    else:
        # A first line of CONTEXT.md found without a goal heading: shown, not called the goal.
        goal_html = f'<p class="goal"{USER}>{esc(goal)}</p>' if goal else ""
    hero = (f'<div class="hero">{kicker}<h1{USER}>{esc(p.title())}</h1>{goal_html}{overview_facts(site, key_all)}'
            f'<div class="where">{t("overview.project")} <code{USER}>{esc(p.root)}</code></div></div>')
    key_items, open_items = site.lists.key, site.lists.open

    def rows(items, prefix: str, empty_key: str) -> str:
        if not items:
            return f'<div class="empty">{t(empty_key)}</div>'
        return '<ol class="records">' + "".join(
            site.record_row(e, 0, row_id=prefix + site.anchor(e)) for e in reversed(items[-OVERVIEW_ROWS:])) + "</ol>"

    def panel(panel_id: str, title_key: str, shown_key: str, items, prefix, empty_key, link: str, link_words: str,
              total: int) -> str:
        shown = min(len(items), OVERVIEW_ROWS)
        count = f'<span class="muted">{t(shown_key, shown=shown, total=len(items))}</span>' if items else ""
        # The way to all of them, where there is anything to see there.
        more = f'<p class="panel-link"><a href="{link}">{link_words} <span aria-hidden="true">→</span></a></p>' if total else ""
        return (f'<div class="panel" id="{panel_id}"><div class="panel-head"><h2>{t(title_key)}</h2>{count}</div>'
                f'{rows(items, prefix, empty_key)}{more}</div>')

    lists = ('<div class="columns">'
             + panel("overview-key", "overview.key", "overview.shown_current", key_items, "key-", "overview.key_empty",
                     "#insights", t("overview.all_insights", n=len(key_all)), len(key_all))
             + panel("overview-open", "overview.open", "overview.shown_open", open_items, "key-", "overview.open_empty",
                     "lists/open-0.html", t("overview.all_open", n=len(open_items)), len(open_items))
             + "</div>")
    current = status.charts
    charts = (f'<details class="fold" open><summary><h2>{t("overview.recent_charts")}</h2>'
              + (f' <span class="muted">{t("overview.shown_total", shown=min(len(current), OVERVIEW_CHARTS), total=len(current))}</span>' if current else "")
              + '</summary><div class="gallery">'
              + (''.join(site.chart_card(c, 0) for c in current[:OVERVIEW_CHARTS]) or f'<div class="empty">{t("overview.charts_empty")}</div>')
              + f'</div><p class="panel-link"><a href="#charts">{t("overview.all_charts")} <span aria-hidden="true">→</span></a></p></details>')
    recent = site.events[-8:][::-1]
    latest = (f' · {t("overview.latest")} {esc(recent[0].get("id"))} {words("kind", recent[0].get("kind"))}, {fmt_time(recent[0].get("ts"))}'
              if recent else "")
    activity = (f'<details class="fold"><summary><h2>{t("overview.recent_events")}</h2> <span class="muted">'
                f'{t("overview.entries", n=len(recent))}{latest}</span></summary>{site.events_html(recent, 0, prefix="recent-")}</details>')
    return (f'<section id="overview" class="tab"><div class="overview-head">{hero}{direction_panel(site, sections, goal_section)}</div>'
            f'{records_notice(site)}{status.html()}{site.map.summary_html(status)}{lists}{charts}{activity}'
            f'{coverage_fold(site)}</section>')


def overview_facts(site: Site, key_all: list[dict]) -> str:
    """What the records hold, in numbers (design 7.1's summary, made without a model): a line of the
    work and its period, and a line of the insights. A count each, so English says one and many."""
    imported = sum(1 for ds in site.datasets if ds.get("kind") in ("imported", "raw"))
    derived = sum(1 for ds in site.datasets if ds.get("kind") == "derived")
    work = " · ".join([t("overview.fact.runs", n=len(site.runs)), t("overview.fact.imported", n=imported),
                       t("overview.fact.charts", n=len(site.status.charts)), t("overview.fact.derived", n=derived)])
    stamps = [e.get("ts") for e in site.events if e.get("ts")]
    period = (f' · {t("overview.fact.period")} {fmt_date(min(stamps))} – {fmt_date(max(stamps))}' if stamps else "")
    kinds = Counter(e.get("kind") for e in key_all)
    states = Counter((site.model.journal.replay().state_of(site.source(e) or "") or {}).get("state") for e in key_all)
    parts = [t(f"overview.fact.{k}", n=kinds[k]) for k in core.KEY_KINDS if kinds[k]]
    changed = states.get("superseded", 0) + states.get("conflict", 0)
    if changed:
        parts.append(t("overview.fact.corrected", n=changed))
    if states.get("withdrawn"):
        parts.append(t("overview.fact.withdrawn", n=states["withdrawn"]))
    insights = t("overview.fact.insights", n=len(key_all)) + (": " + " · ".join(parts) if parts else "")
    return f'<p class="facts">{work}{period}</p><p class="facts">{insights}</p>'


def section_heading(section: dict) -> str:
    """A CONTEXT.md section's heading as written, its pin mark aside (not the prompt's shortened
    name)."""
    return core.PIN_RE.sub("", section["text"].split("\n", 1)[0][3:]).strip()


def section_body(section: dict) -> tuple[str, bool]:
    """A section's words under its heading, up to PINNED_CHARS (cut at a line), and whether it was cut."""
    body = section["text"].split("\n", 1)[1] if "\n" in section["text"] else ""
    if len(body) <= PINNED_CHARS:
        return body, False
    return body[:PINNED_CHARS].rsplit("\n", 1)[0], True


def direction_panel(site: Site, sections: list[dict], goal_section: dict | None) -> str:
    """Where the analysis is going now (design 7.1): CONTEXT.md's pinned sections with their headings
    and words as written (the goal's own section is the hero's), and the analysis focus and metrics
    the journal holds (#14)."""
    pinned = [s for s in sections if s["pinned"] and s is not goal_section]
    blocks = []
    for section in pinned:
        body, cut = section_body(section)
        # A heading that is only a pin mark is the section's number in the kit's words.
        heading = section_heading(section)
        title = user_text(heading) if heading else t("overview.section", n=sections.index(section) + 1)
        blocks.append(f'<div class="pinned"><h3>{title}</h3><div class="markdown"{USER}>{render_markdown(body)}</div>'
                      + (f'<p class="panel-link"><a href="#context">{t("overview.read_rest")}</a></p>' if cut else "") + "</div>")
    if not blocks:
        blocks.append(f'<p class="muted">{t("overview.no_pinned")}</p>')
    view = core.metric_records(site.p, model=site.model)
    focus = view.get("current_focus")
    if focus is not None:
        details = focus["details"]
        metrics = ", ".join(m["metric_id"] for m in details["metrics"][:6])
        blocks.append(f'<div class="pinned"><h3>{t("overview.focus")}</h3><p{USER}>{esc(details.get("question"))}</p>'
                      + (f'<p>{t("overview.metrics")} <span{USER}>{esc(metrics)}</span></p>' if metrics else "") + "</div>")
    elif view.get("focus_state") == "conflict":
        blocks.append(f'<p>{badge("warning", "⚠", t("overview.focus_conflict"))}</p>')
    defined = [d for d in view["definitions"] if d.get("state") == "current"]
    if focus is None:
        blocks.append(f'<p class="muted">{t("overview.metrics_defined", n=len(defined)) if defined else t("overview.metrics_none")}</p>')
    return (f'<aside class="direction panel"><div class="panel-head"><h2>{t("overview.direction")}</h2>'
            f'<a href="#context">{t("overview.open_context")}</a></div><p class="muted">{t("overview.direction_from")}</p>'
            f'{"".join(blocks)}</aside>')


def coverage_fold(site: Site) -> str:
    """Record coverage and Workflow feedback, folded (design 7.1): the runs a record leaves something
    out of, and the feedback, both in full in Evaluation."""
    runs = site.runs
    gaps = {r["id"] for r in runs if r.get("status") == "ok" and not r.get("inputs")
            and (relations.run_visualizations(r) or r.get("outputs"))}
    gaps |= {r["id"] for r in runs if r.get("status") == "ok" for c in relations.run_visualizations(r)
             if c.get("kind") == "matplotlib" and not c.get("data")}
    gaps |= {r["id"] for r in runs for i in r.get("inputs") or [] if i.get("external")}
    feedback = site.lists.feedback
    note = f'{t("overview.coverage_gaps", n=len(gaps))} · {t("overview.feedback_items", n=len(feedback))}'
    return (f'<details class="fold"><summary><h2>{t("overview.coverage")}</h2> <span class="muted">{note}</span></summary>'
            f'<p>{t("overview.coverage_lead")} <a href="#evaluation">{t("overview.coverage_link")} <span aria-hidden="true">→</span></a></p></details>')


def list_notice(kind: str, total: int, limit: int, depth: int = 0) -> str:
    more = max(0, total - limit)
    if not more:
        return ""
    return (f'<p class="list-scope">{t("list.showing", shown=min(total, limit), total=total, more=more)} '
            f'<a href="{"../" * depth}lists/{kind}-0.html">{t("list.browse_all." + kind)}</a></p>')


def insights_section(site: Site) -> str:
    """Findings, decisions and assumptions, newest first: a row each (design 8.1), the kind filter
    over all of them (8.2), and where the rest are."""
    events = [e for e in site.events if e.get("kind") in core.KEY_KINDS]
    rows = "".join(site.record_row(e, 0, row_id="insight-" + site.anchor(e)) for e in events[-INDEX_LIMIT:][::-1])
    return (f'<section id="insights" class="tab" data-count="{len(events)}"><div class="toolbar"><h2>{t("tab.insights")}</h2></div>'
            f'<p class="muted">{t("insights.lead")} <a href="#assets">{t("insights.assets_link")}</a></p>'
            + (site.kind_nav("insights", 0) if events else "") + list_notice("insights", len(events), INDEX_LIMIT)
            + (f'<ol class="records">{rows}</ol>' if rows else f'<div class="empty">{t("insights.empty")}</div>') + '</section>')


# The index section a list family continues: where its pages' way back goes.
LIST_TAB = {"timeline": "timeline", "insights": "insights", "feedback": "evaluation", "charts": "charts",
            "runs": "runs", "data": "data", "open": "overview"}


def list_page(site: Site, out: Path, filename: str, heading: str, scope: str, content: str, nav: str, *,
              back: str, title_key: str = "", title: str = "") -> None:
    body = (f'<h1>{heading}</h1><p class="list-scope">{scope}</p>' + content
            + f'<nav class="continuation"{attr("aria-label", "list.pages")}>{nav}</nav>')
    _write(out / "lists" / filename, site.page(1, title, f' <span>/</span> {t("list.crumb")}', body, "list",
                                              page_path="lists/" + filename, title_key=title_key, back=back),
           site.p.platform)


def write_list_pages(site: Site, out: Path) -> int:
    """The pages that continue the index's lists (`lists/<family>-<n>.html`), and each journal
    kind's own list (`lists/kind-<kind>-<n>.html`, design 8.2, owner decision A of 2026-10-14) that
    the kind filters of the Timeline and Insights link to: every record of the kind, newest first."""
    groups = {"timeline": list(reversed(site.events)), "insights": [e for e in reversed(site.events) if e.get("kind") in core.KEY_KINDS],
              "feedback": list(reversed(site.lists.feedback)), "charts": site.charts,
              "runs": list(reversed(site.runs)), "data": site.datasets, "open": list(reversed(site.lists.open))}
    keep = set()
    for kind, records in groups.items():
        limit = CHART_INDEX_LIMIT if kind == "charts" else INDEX_LIMIT
        for page in range(max(1, (len(records) + limit - 1) // limit)):
            filename = f"{kind}-{page}.html"
            subset = records[page * limit:(page + 1) * limit]
            if kind == "timeline":
                content = site.kind_nav("timeline", 1) + site.events_html(subset, 1)
            elif kind == "open":
                content = ('<ol class="records">' + "".join(site.record_row(e, 1, row_id="open-" + site.anchor(e)) for e in subset)
                           + "</ol>" if subset else f'<div class="empty">{t("overview.open_empty")}</div>')
            elif kind == "insights":
                content = site.kind_nav("insights", 1) + ('<ol class="records">' + "".join(
                    site.record_row(e, 1, row_id="insight-" + site.anchor(e)) for e in subset) + "</ol>"
                    if subset else f'<div class="empty">{t("insights.empty")}</div>')
            elif kind == "feedback":
                content = site.events_html(subset, 1)
            elif kind == "charts":
                content = (f'<label class="toggle"><input type="checkbox" id="show-retired"> {t("charts.show_retired")}</label>'
                           + '<div class="gallery">' + ''.join(site.chart_card(c, 1, full=True) for c in subset) + '</div>')
            else:
                content = '<ul class="plain items">' + ''.join(
                    f'<li id="{"run-" + esc(r["id"]) if kind == "runs" else "data-" + esc(site.view_by_path.get(r["path"]) or "")}">'
                    f'<a href="{site.run_link(r["id"], 1) if kind == "runs" else site.data_link(r["path"], 1)}"{USER}>'
                    f'{esc(r.get("title") or r.get("name") or r.get("id"))}</a> '
                    f'{site.relation_link("run:" + r["id"] if kind == "runs" else r["path"], 1)}</li>'
                    for r in subset) + '</ul>'
            nav = (f'<a href="{kind}-{page-1}.html">{t("list.previous." + kind)}</a>' if page else '')
            if (page+1) * limit < len(records):
                nav += f' <a href="{kind}-{page+1}.html">{t("list.next." + kind)}</a>'
            start = page * limit + 1 if subset else 0
            scope = t("list.items", start=start, end=page * limit + len(subset), total=len(records),
                      outside=len(records) - len(subset))
            list_page(site, out, filename, t("list.title." + kind), scope, content, nav, back=LIST_TAB[kind],
                      title_key="list.title." + kind)
            keep.add(filename)
    by_kind: dict[str, list[dict]] = {}
    for event in reversed(site.events):
        by_kind.setdefault(kind_list(event.get("kind")), []).append(event)
    for kind, records in by_kind.items():
        part, named = kind_file(kind), i18n.has(f"kind.{kind}")
        for page in range((len(records) + INDEX_LIMIT - 1) // INDEX_LIMIT):
            filename = f"kind-{part}-{page}.html"
            subset = records[page * INDEX_LIMIT:(page + 1) * INDEX_LIMIT]
            nav = (f'<a href="kind-{part}-{page-1}.html">{t("list.previous.kind")}</a>' if page else '')
            if (page + 1) * INDEX_LIMIT < len(records):
                nav += f' <a href="kind-{part}-{page+1}.html">{t("list.next.kind")}</a>'
            scope = t("list.items", start=page * INDEX_LIMIT + 1, end=page * INDEX_LIMIT + len(subset),
                      total=len(records), outside=len(records) - len(subset))
            list_page(site, out, filename, words("kind", kind), scope,
                      site.kind_nav("timeline", 1, current=kind) + site.events_html(subset, 1), nav,
                      back="timeline", title_key=f"kind.{kind}" if named else "", title="" if named else str(kind))
            keep.add(filename)
    _prune(out / "lists", keep)
    return len(keep)


def asset_section(site: Site) -> str:
    """Read-only candidates and stored assessments; rendering never starts policy scoring."""
    app = core.asset_application(site.p, core.Origin("system", "sdk"))
    view = app.list(model=site.model)
    mode = app.suggestion_status(journal=site.model.journal)
    by_id = {event.id: event.value for event in site.model.journal.events
             if site.model.journal.source_of(event) == event.id}
    items = []
    for asset in view["assets"]:
        versions = []
        for candidate in reversed(asset["versions"]):
            event_id = candidate["id"]
            snapshots = [event for event in by_id.values() if event.get("kind") in ("asset_notification", "record_update")
                         and isinstance(event.get("details"), dict)
                         and event["details"].get("candidate_event_id") == event_id
                         and isinstance(event["details"].get("scores"), dict)
                         and not relations.asset_event_problem(event)]
            snapshot = snapshots[-1] if snapshots else None
            scores = (snapshot.get("details") or {}).get("scores") if snapshot else None
            score = t("assets.no_score")
            if (isinstance(scores, dict) and set(scores) == {"demand", "checks", "scope", "benefit"}
                    and all(type(value) is int for value in scores.values())):
                score = t("assets.score", score=sum(scores.values()), seq=str(snapshot["details"].get("as_of_seq")),
                          id=snapshot["id"])
            target = ("#" + site.anchor(candidate)) if event_id in site.recent_sources else site.relations.link(event_id, 0, anchor="#record")
            source = f'<a href="{esc(target)}">{esc(event_id)} · {t("assets.journal_refs")}</a>'
            versions.append(f'<details id="asset-{esc(event_id)}"><summary>{source} · {score}</summary>'
                            f'<p{USER}>{esc(candidate["details"]["problem"])}</p>{site.refs_html(candidate, 0)}'
                            f'<h4>{t("assets.stored_fields")}</h4><pre{USER}>{esc(json.dumps(candidate, ensure_ascii=False, indent=2))}</pre>'
                            + (f'<h4>{t("assets.assessment")} {esc(snapshot["id"])}</h4><pre{USER}>{esc(json.dumps(snapshot, ensure_ascii=False, indent=2))}</pre>'
                               if snapshot else "") + '</details>')
        items.append(f'<article class="panel"><h3{USER}>{esc(asset["title"])}</h3>'
                     f'<p>{t("assets.asset")} <code>{esc(asset["asset_id"])}</code> · {esc(asset["artifact_kind"])} · '
                     f'{t("assets.state")} {esc(asset["state"])} · {t("assets.versions", n=len(asset["versions"]))}</p>{"".join(versions)}</article>')
    for old in view["legacy_candidates"]:
        anchor = old["source"].replace(":", "-")
        items.append(f'<article class="panel"><h3>{t("assets.legacy")}</h3><p>{t("assets.raw_kind")} {esc(old["raw_kind"])} · '
                     f'proposal_id: <span{USER}>{esc(old["proposal_id"])}</span> · '
                     f'<a href="#{esc(anchor)}">{esc(old["source"])}</a></p>'
                     f'<pre{USER}>{esc(json.dumps(old["record"], ensure_ascii=False, indent=2))}</pre></article>')
    pending = ''.join(f'<li><a href="#{esc(notice["event_id"])}">{esc(notice["event_id"])}</a> '
                      f'<span{USER}>{esc(notice["text"])}</span> · {esc(notice["surface"])}</li>' for notice in mode["pending"])
    errors = ''.join(f'<li>{esc(error["code"])}: {esc(error["message"])}</li>' for error in mode["settings_errors"])
    return (f'<section id="assets" class="tab"><div class="toolbar"><h2>{t("tab.assets")}</h2></div>'
            f'<p>{t("assets.mode")} {esc(mode["mode"])}. {t("assets.lead")}</p>'
            + (f'<ul>{errors}</ul>' if errors else '')
            + f'<h3>{t("assets.pending")}</h3><ul>{pending or "<li>" + t("assets.no_pending") + "</li>"}</ul>'
            + ''.join(items) + '</section>')


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

    def row(label_key, value, detail=""):
        return f"<tr><th>{t(label_key)}</th><td class='num'>{value}</td><td>{detail}</td></tr>"

    def run_list(items):
        return ", ".join(f'<a href="runs/{href(r["id"])}.html">{esc(r["id"])}</a>' for r in items[:6])

    signals = "".join([
        row("evaluation.run_status", len(runs), " · ".join(f"{words('status', k)} {v}" for k, v in statuses.items())),
        row("evaluation.no_input", len(no_input), run_list(no_input)),
        row("evaluation.undocumented", len(undocumented),
            ", ".join(f'<a href="runs/{href(r["id"])}.html"{USER}>{esc(c["id"])}</a>' for r, c in undocumented[:6])),
        row("evaluation.external", len(external), ", ".join(user_text(i["path"]) for _, i in external[:4])),
        row("evaluation.outside", len(outside), ", ".join(user_text(o["path"]) for _, o in outside[:4])),
        row("evaluation.stale", len(stale), ", ".join(user_text(c["key"]) for c in stale[:4])),
        row("evaluation.reruns", len(reruns), " · ".join(f"{verdict_label(k)} {v}" for k, v in verdicts.items())),
        row("evaluation.queries", len(site.queries),
            t("evaluation.query_errors", n=sum(1 for q in site.queries if q.get('error')))),
    ])
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for tel in site.telemetry:
        by_tool[str(tel.get("tool"))].append(tel)
    tool_rows = "".join(
        f"<tr><td><code>{esc(tool)}</code></td><td class='num'>{len(items)}</td>"
        f"<td class='num'>{sum(1 for i in items if i.get('error'))}</td>"
        f"<td class='num'>{fmt_num(round(sum(i.get('ms') or 0 for i in items) / max(1, len(items))))}</td></tr>"
        for tool, items in sorted(by_tool.items(), key=lambda kv: -len(kv[1]))[:INDEX_LIMIT])
    flagged = [tel for tel in site.telemetry if tel.get("flags")]
    flag_items = "".join(
        f"<li>{fmt_time(tel.get('ts'))} <code>{esc(tel.get('tool'))}</code> "
        f"{''.join(badge('warning', '⚑', esc(f)) for f in tel['flags'])} <span class='muted'{USER}>{esc(tel.get('summary'))}</span></li>"
        for tel in flagged[::-1][:30])
    gap_html = site.events_html(gaps[-INDEX_LIMIT:][::-1], 0, prefix="feedback-") if gaps else \
        f'<div class="empty">{t("evaluation.feedback_empty")}</div>'
    tool_head = "".join(f"<th>{t('evaluation.col.' + c)}</th>" for c in ("tool", "calls", "errors", "mean_ms"))
    telemetry_html = (f'<div class="table-wrap"><table class="list"><thead><tr>{tool_head}</tr></thead><tbody>{tool_rows}</tbody></table></div>'
                      if tool_rows else f'<div class="empty">{t("evaluation.tools_empty")}</div>')
    return f"""
<section id="evaluation" class="tab" data-count="{len(gaps)}">
  <div class="toolbar"><h2>{t("tab.evaluation")}</h2></div>
  <p class="muted">{t("evaluation.lead")}</p>
  <div class="columns">
    <div class="panel"><h3>{t("evaluation.feedback", n=len(gaps))}</h3>{list_notice("feedback", len(gaps), INDEX_LIMIT)}{gap_html}</div>
    <div class="panel"><h3>{t("evaluation.coverage")}</h3><div class="table-wrap"><table class="kv">{signals}</table></div></div>
  </div>
  <div class="columns">
    <div class="panel"><h3>{t("evaluation.tools")}</h3>{telemetry_html}</div>
    <div class="panel"><h3>{t("evaluation.workarounds", n=len(flagged))}</h3>{('<ul class="plain flagged">' + flag_items + '</ul>') if flag_items else f'<div class="empty">{t("evaluation.none")}</div>'}</div>
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
        nav.append(f'<a href="{href(same_script[pos - 1]["id"])}.html">← {t("run.previous")}</a>')
    if pos < len(same_script) - 1:
        nav.append(f'<a href="{href(same_script[pos + 1]["id"])}.html">{t("run.next")} →</a>')
    sub = [f"{t('run.label')} <code>{esc(rid)}</code>", fmt_time(run.get("started_at")),
           f"{run.get('duration_s', '-')}s", f"{t('run.actor')} {words('actor', run.get('actor') or '-')}"]
    if run.get("session"):
        sub.append(f'<span title="{esc(run["session"])}">{t("run.session")} {esc(Path(run["session"]).name)}</span>')
    head = f"""
<div class="page-head">
  <div class="title-row"><h1{USER}>{esc(run.get('title'))}</h1>{status_badge(run.get('status'))}</div>
  <div class="sub">{' · '.join(sub)}</div>
  <div class="sub">{t("run.ran")} <code{USER}>{esc(script.get('path'))}</code>{site.history.run_code(run)} · {t("run.position", n=pos + 1, total=len(same_script))} {' '.join(nav)}</div>
</div>"""
    callouts = ""
    if run.get("error"):
        err = run["error"]
        where = f' ({t("run.line")} <a href="#L-main-{err["line"]}">{err["line"]}</a>)' if err.get("line") else ""
        # The hint is the kit's, written in English where it is made (core): said so in a Korean page.
        fix = (f'<div class="fix">{t("run.how_to_fix")} <span lang="en">{esc(err["hint"])}</span></div>'
               if err.get("hint") else "")
        # The summary is the script's exception as it said it; the hint is the kit's.
        callouts += (f'<div class="callout tone-critical"><strong{USER}>{esc(err.get("summary"))}</strong>'
                     f'{where}{fix}</div>')
    # Each Visualization's comparison, whichever record holds it (reproduction.visualizations of an
    # analytics.run/2 run, reproduction.charts of a lab.run/1 one: design 5.3).
    repro = relations.run_reproduction(run)
    if repro:
        tone = {"match": "good", "mismatch": "critical", "failed": "critical"}.get(
            repro.get("verdict"), "muted")
        details = "".join(f"<li><code{USER}>{esc(k)}</code>: {comparison_words(v)}</li>"
                          for k, v in {**repro["visualizations"], **repro["outputs"]}.items())
        inputs_same = t("run.inputs_same") if repro.get("inputs_same") else t("run.inputs_different")
        callouts += (f'<div class="callout tone-{tone}"><strong>{t("run.rerun_comparison")} '
                     f'{verdict_label(repro.get("verdict"))}</strong> — {t("run.reran_snapshot")} '
                     f'<a href="{href(repro.get("of", ""))}.html">{esc(repro.get("of"))}</a>. {inputs_same} '
                     f'{t("run.rerun_not_version")}<ul>{details}</ul></div>')
    if run.get("finalize_errors"):
        callouts += (f'<div class="callout tone-critical"><strong>{t("run.finalize_failed")}</strong><ul>' + "".join(
            f'<li lang="en">{esc(w)}</li>' for w in run["finalize_errors"]) + "</ul></div>")
    if run.get("warnings"):
        # Runs recorded before warnings were grouped can carry one line per input file.
        shown = run["warnings"][:REF_LIMIT]
        rest = len(run["warnings"]) - len(shown)
        callouts += (f'<div class="callout tone-warning"><strong>{t("run.warnings")}</strong><ul>'
                     + "".join(f'<li lang="en">{esc(w)}</li>' for w in shown)
                     + (f"<li class='muted'>{t('refs.more', n=rest)}</li>" if rest > 0 else "")
                     + "</ul></div>")
    recording = site.recording.get(rid) or {}
    if recording.get("recording_status") == "incomplete":
        callouts += (f'<div class="callout tone-warning"><strong>{t("run.recording_incomplete")}</strong><ul>'
                     + "".join(f"<li><code>{esc(r)}</code>: {words('recording', r)}</li>"
                               for r in recording.get("reasons") or [])
                     + "</ul></div>")
    if fresh["changed_inputs"]:
        reasons = fresh.get("change_reasons") or {}
        callouts += (f'<div class="callout tone-critical"><strong>{t("run.inputs_differ")}</strong> '
                     + ", ".join(user_text(path) + (" " + t("run.line_endings_note")
                                                    if reasons.get(path) == core.LINE_ENDINGS_ONLY else "")
                                 for path in fresh["changed_inputs"]) + "</div>")
    if fresh["stale_upstream"]:
        # The stale runs upstream output the same bytes as an input: candidates, not its producer.
        callouts += (f'<div class="callout tone-warning"><strong>{t("run.upstream_stale")}</strong> '
                     + t("stale.upstream_lead") + " " + site.producer_html(fresh["stale_upstream"], 1)
                     + t("stale.upstream_tail") + inferred_runs_note(fresh.get("inferred_upstream")) + "</div>")
    if fresh.get("ambiguous_inputs") or fresh.get("cycle"):
        callouts += ('<div class="callout tone-warning">' + " ".join(producer_badges(fresh))
                     + " " + t("run.producers_listed") + "</div>")

    charts = []
    for chart in relations.run_visualizations(run):
        key = site.chart_key(run, chart)
        data_block = ""
        if chart.get("data"):
            data_block = (f'<details class="data-block" id="chart-{esc(chart["id"])}-data"><summary>{t("card.attached_data")} · '
                          f'{t("run.rows", rows=fmt_num(chart.get("data_rows")))} · <a href="{site.file_link(chart["data"], 1)}">parquet</a></summary>'
                          f'{site.frame_preview(chart["data"])}</details>')
        line = f' · {t("run.code_word")} <a href="#L-main-{chart["line"]}">L{chart["line"]}</a>' if chart.get("line") else ""
        # The key the run recorded is the older kit's spelling (what people wrote: lang="").
        inferred_key = (f' <small class="ref-note"{inferred_title()}>{t("inferred.recorded_as")} '
                        f'<code{USER}>{esc(core.chart_key(run, chart))}</code> — '
                        f'{t("inferred.note")}</small>'
                        if key != core.chart_key(run, chart) else "")
        charts.append(f"""
<article class="chart-card wide" id="chart-{esc(chart['id'])}" data-selection="{quote(rid + "/" + str(chart["id"]), safe="")}">
  <header><h3{USER}>{esc(chart.get('title'))}</h3><div class="badges"><span class="badge tone-muted">{esc(chart.get('kind'))} · {esc(chart.get('via'))}</span></div></header>
  <div class="chart-body">{site.chart_html(chart, 1, lazy=False)}</div>
  {f'<p class="caption"{USER}>{esc(chart["caption"])}</p>' if chart.get('caption') else ''}
  <div class="prov-line">id <code{USER}>{esc(key)}</code>{inferred_key}{line} · <a href="{site.chart_anchor(key, 1)}">{t("run.show_in_charts")}</a></div>
  {site.history.chart_history(run, chart, key)}
  {data_block}
  {site.relation_actions(rid + "/" + str(chart["id"]), 1)}
</article>""")
    charts_html = "".join(charts) or f'<div class="empty">{t("run.no_charts")}</div>'

    hl = [c.get("line") for c in relations.run_visualizations(run)]
    if run.get("error") and run["error"].get("line"):
        hl.append(run["error"]["line"])
    code_blocks = []
    snap = script.get("snapshot")
    if snap and p.path(snap).is_file():
        state = words("code", fresh["code"])
        if fresh.get("code_reason") == core.LINE_ENDINGS_ONLY:
            state += " " + t("run.line_endings_note")
        code_blocks.append(
            f'<div class="codefile" id="code-main"><div class="file-head"><code{USER}>{esc(script.get("path"))}</code>'
            f'<span class="sha">sha256 {short(script.get("sha256"), 12)}</span><span>{state}</span>'
            f'<a href="{site.file_link(snap, 1)}">{t("run.snapshot_file")}</a></div>'
            f'{code_html(p.path(snap).read_text(encoding="utf-8", errors="replace"), "main", hl)}</div>')
    for i, mod in enumerate(run.get("modules") or [], 1):
        mp = p.path(mod.get("snapshot", ""))
        if mp.is_file():
            code_blocks.append(
                f'<details class="codefile"><summary><code{USER}>{esc(mod["path"])}</code> <span class="sha">'
                f'sha256 {short(mod.get("sha256"), 12)}</span> ({t("run.local_module")})</summary>'
                f'{code_html(mp.read_text(encoding="utf-8", errors="replace"), f"m{i}")}</details>')

    pairs = list(zip(run.get("inputs") or [], fresh["inputs"], strict=False))
    n_inputs = len(pairs)
    # Files whose state changed are the ones a reader came for, so they survive the row cap.
    ordered = sorted(pairs, key=lambda pair: pair[1]["state"] == "same")[:FILE_ROW_LIMIT]
    input_rows = []
    for entry, f_entry in ordered:
        state = f_entry["state"] if f_entry["state"] in INPUT_STATE else "unknown"
        tone, icon = INPUT_STATE[state]
        producer = producer_cell(site, site.model.producer(rid, entry.get("path")), 1)
        input_rows.append(
            f'<tr><td>{site.path_html(entry["path"], 1)}{site.read_as_html(rid, entry.get("path"), 1)}</td>'
            f'<td>{esc(", ".join(entry.get("via") or []))}</td>'
            f'<td class="num">{fmt_bytes(entry.get("bytes"))}</td><td class="sha">{short(entry.get("sha256"), 12)}</td>'
            f'<td>{badge(tone, icon, words("input", state))}'
            f'{" " + line_endings_badge() if f_entry.get("reason") == core.LINE_ENDINGS_ONLY else ""}'
            f'</td><td>{producer}</td></tr>')
    if n_inputs > len(input_rows):
        input_rows.append(
            f'<tr><td colspan="6" class="muted">{t("run.inputs_more", n=n_inputs - len(input_rows))} '
            f'<a href="{site.file_link(f"runs/{rid}/run.json", 1)}">run.json</a>.</td></tr>')
    output_rows = []
    for out in run.get("outputs") or []:
        if out.get("change") == "deleted":
            now = '<span class="muted">-</span>'
        else:
            seen = site.model.observation(rid, "output", out.get("path"))
            state = seen["state"] if seen["state"] in OUTPUT_STATE else "unknown"
            tone, icon = OUTPUT_STATE[state]
            now = badge(tone, icon, words("output", state), title_attr(seen.get("reason")))
        output_rows.append(
            f'<tr><td>{site.path_html(out["path"], 1)}{site.read_as_html(rid, out.get("path"), 1)}</td>'
            f'<td>{words("change", out.get("change"))}</td>'
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
        shown = f"<pre{USER}>{esc(text)}</pre>" if text else f"<pre>{t('run.log_empty')}</pre>"
        return (f'<details class="log"{opened}><summary>{label} · {fmt_bytes(size)}</summary>'
                f'{shown}</details>')

    env = run.get("env") or {}
    env_rows = "".join(f"<tr><th>{esc(k)}</th><td><code>{esc(v)}</code></td></tr>" for k, v in [
        ("python", env.get("python")), ("uv.lock sha256", short(env.get("uv_lock_sha256"), 16)),
        ("kit", f"{env.get('kit_version')} · {short(env.get('kit_sha256'), 12)}"),
        *sorted((env.get("packages") or {}).items())])
    related = site.related.get(rid, [])
    jump = "".join(f'<a href="#{anchor}">{t("run.nav." + anchor)}</a>'
                   for anchor in ("charts", "code", "inputs", "outputs", "logs", "env", "journal", "reproduce"))
    input_head = "".join(f"<th>{t('run.col.' + c)}</th>" for c in ("path", "via", "size", "recorded_sha", "now", "producer"))
    output_head = "".join(f"<th>{t('run.col.' + c)}</th>" for c in ("path", "change", "via", "rows", "columns")) + \
        f"<th>sha256</th><th>{t('run.col.now')}</th>"

    body = f"""{head}{callouts}
<nav class="jump">{jump}</nav>
<section id="charts"><h2>{t("run.h.charts", n=len(charts))}</h2>{charts_html}</section>
<section id="code"><h2>{t("run.h.code")}</h2><p class="muted">{t("run.code_lead")}</p>{''.join(code_blocks) or f'<div class="empty">{t("run.no_code")}</div>'}</section>
<section id="inputs"><h2>{t("run.h.inputs", n=n_inputs)}</h2><p class="muted">{t("run.inputs_lead")}</p>
{(f'<div class="table-wrap"><table class="list"><thead><tr>{input_head}</tr></thead><tbody>' + ''.join(input_rows) + '</tbody></table></div>') if input_rows else f'<div class="empty">{t("run.no_inputs")}</div>'}</section>
<section id="outputs"><h2>{t("run.h.outputs", n=len(output_rows))}</h2>
{(f'<div class="table-wrap"><table class="list"><thead><tr>{output_head}</tr></thead><tbody>' + ''.join(output_rows) + '</tbody></table></div>') if output_rows else f'<div class="empty">{t("run.no_outputs")}</div>'}</section>
<section id="logs"><h2>{t("run.h.logs")}</h2>{log_block('stdout', run.get('stdout'))}{log_block('stderr', run.get('stderr'))}</section>
<section id="env"><h2>{t("run.h.env")}</h2><div class="table-wrap"><table class="kv">{env_rows}</table></div></section>
<section id="journal"><h2>{t("run.h.journal")}</h2>{site.events_html(related, 1)}</section>
<section id="reproduce"><h2>{t("run.h.rerun")}</h2><p>{t("run.rerun_lead")}</p>
<pre class="cmd">aelix: analytics_run(title="Rerun comparison", rerun_of="{esc(rid)}")
CLI:   analytics run --rerun-of {esc(rid)}</pre></section>"""
    crumb = f' <span>/</span> <a href="../index.html#runs">{t("tab.runs")}</a> <span>/</span> {esc(rid)}'
    return site.page(1, run.get("title") or rid, crumb, body, "run", page_path="runs/" + rid + ".html",
                     selection="run:" + rid, actions=site.relation_link("run:" + rid, 1, klass="action"))


# --- dataset page ---

def _ids(items: list[str], field: str) -> str:
    """Run ids, SHOWN_CANDIDATES of them, how many more, and where all of them are listed: the
    status JSON's `field` (core.dataset_origin says the same)."""
    more = len(items) - SHOWN_CANDIDATES
    if more <= 0:
        return esc(", ".join(items))
    return (esc(", ".join(items[:SHOWN_CANDIDATES])) + " " + t("list.and_more", n=more)
            + f" ({t('list.status_lists_all')} <code>{esc(field)}</code>)")


def dataset_origin_html(ds: dict) -> str:
    """core.dataset_origin in the kit's switchable words: where a dataset's current bytes come from,
    as far as the records say (ADR-0006 4). A run is named as having output the same bytes, not as
    having made them, and one found only through an older kit's path says so (ADR-0006 9)."""
    state = ds.get("producer_state")
    candidates = ds.get("producer_candidates") or []
    inferred = ds.get("inferred") or {}

    def inferred_ids(runs, field: str) -> str:
        return f"; {t('inferred.note')}: {_ids(runs, 'inferred.' + field)}" if runs else ""

    if state == "resolved":
        return t("dataset.origin.resolved", run=candidates[0]) + (
            f" ({t('inferred.note')})" if inferred.get("producer_candidates") else "")
    if state == "ambiguous":
        return (t("dataset.origin.ambiguous", n=len(candidates)) + " " + _ids(candidates, "producer_candidates")
                + inferred_ids(inferred.get("producer_candidates"), "producer_candidates"))
    if ds.get("unsupported_record"):
        record = ds["unsupported_record"]
        return f'{t("dataset.origin.import_not_read")} ({user_text(record["location"])}: {esc(record["reason"])})'
    if ds.get("source"):
        return f'{t("dataset.origin.from")} {user_text(ds["source"])}'
    if state == "unknown":
        return t("dataset.origin.unknown")
    written = ds.get("written_by") or []
    if written:
        return (t("dataset.origin.no_match") + " " + _ids(written[::-1], "written_by")
                + inferred_ids((inferred.get("written_by") or [])[::-1], "written_by"))
    return t("dataset.origin.none")


def dataset_page(site: Site, ds: dict, view: str) -> str:
    from . import data

    p = site.p
    try:
        prof = data.profile(p, p.path(ds["path"]), preview_rows=50, cache=site.cache)
    except Exception as exc:  # noqa: BLE001
        prof = {"columns": [], "preview": {"columns": [], "rows": []}, "error": str(exc)}
    # An import record this kit cannot read says nothing here (core.list_datasets).
    meta = (core.read_json(p.path(ds["path"]).with_suffix(".meta.json"))
            if ds["kind"] in core.IMPORT_FAMILIES and not ds.get("unsupported_record") else None)
    label = dataset_label(ds["name"], view)
    facts = [f'<code{USER}>{esc(ds["path"])}</code>',
             t("dataset.rows_columns", rows=fmt_num(prof.get("rows")), columns=len(prof.get("columns") or [])),
             fmt_bytes(prof.get("bytes")), f'sha256 <span class="sha">{short(prof.get("sha256"), 16)}</span>']
    if label != view:
        facts.append(f"{t('dataset.sql_view')} <code>{esc(view)}</code>")
    origin = ""
    if isinstance(meta, dict):
        src = meta.get("source") or {}
        origin = (f'<dl class="facts"><div><dt>{t("dataset.source_file")}</dt><dd><code{USER}>{esc(src.get("path"))}</code></dd></div>'
                  f'<div><dt>{t("dataset.source_sha")}</dt><dd class="sha">{short(src.get("sha256"), 16)}</dd></div>'
                  f'<div><dt>{t("dataset.format")}</dt><dd>{esc(src.get("format"))}</dd></div>'
                  f'<div><dt>{t("dataset.imported_at")}</dt><dd>{fmt_time(meta.get("imported_at"))}</dd></div></dl>')
        if meta.get("description"):
            origin = f'<p class="lead"{USER}>{esc(meta["description"])}</p>' + origin
    producers = ds.get("producers") or []
    candidates = ds.get("producer_candidates") or []
    inferred = ds.get("inferred") or {}
    current = dataset_origin_html(ds)
    if ds.get("producer_state") in ("resolved", "ambiguous"):
        # Only then does a run named here hold these bytes: with `none` the runs named wrote other
        # bytes or did not succeed.
        current += f" ({site.producer_html(candidates, 1)}). {t('dataset.candidate_meaning')}"
        if inferred.get("producer_candidates"):
            current += (" " + inferred_badge("inferred.title_runs", runs=", ".join(inferred["producer_candidates"]))
                        + " " + t("dataset.inferred_meaning"))
    lineage = [f"<h3>{t('dataset.current_bytes')}</h3><p>{current}.</p>"]

    def change(run_id: str):
        changes = [o.get("change") for o in (site.run_by_id.get(run_id) or {}).get("outputs") or []
                   if o.get("path") == ds["path"]]
        return changes[-1] if changes else None

    if producers:
        # Every successful run that recorded this path as an output, whatever its bytes, and a
        # deletion as such: none of them is said to have made the file.
        lineage.append(f"<h3>{t('dataset.producers')}</h3><ul class='plain'>"
                       + "".join(
            f'<li><a href="{esc(site.run_link(r, 1))}">{esc(r)}</a> {user_text((site.run_by_id.get(r) or {}).get("title"))}'
            f'{" · " + t("dataset.deleted_path") if change(r) == "deleted" else ""}'
            f'{" · " + t("dataset.same_as_now") if r in candidates else ""}'
            f'{inferred_mark(r, inferred.get("producers"))}</li>'
            for r in dict.fromkeys(producers)) + "</ul>")
    users = ds.get("used_by") or []
    lineage.append(f"<h3>{t('dataset.readers')}</h3>" + ("<ul class='plain'>" + "".join(
        f'<li><a href="{site.run_link(r, 1)}">{esc(r)}</a> {user_text((site.run_by_id.get(r) or {}).get("title"))}'
        f'{inferred_mark(r, inferred.get("used_by"))}</li>'
        for r in users) + "</ul>" if users else f'<div class="empty">{t("dataset.none_yet")}</div>'))
    # A chart's run read this data when it recorded its path, or (inferred) an older kit's path read
    # as it.
    used_charts = [c for c in site.charts if not c.get("retired") and ds["path"] in
                   {site.model.read_as(c["run"], i.get("path")) or i.get("path")
                    for i in (site.run_by_id.get(c["run"]) or {}).get("inputs") or []}]
    if used_charts:
        lineage.append(f"<h3>{t('dataset.charts')}</h3><ul class='plain'>" + "".join(
            f'<li><a href="{site.chart_anchor(c["key"], 1)}"{USER}>{esc(c.get("title"))}</a> <code{USER}>{esc(c["key"])}</code>'
            f'{inferred_mark(c["run"], inferred.get("used_by"))}</li>'
            for c in used_charts) + "</ul>")
    # A statistic that does not apply to a column type is "–", never "null" (which is a data value).
    schema_rows = [[c["name"], c["dtype"], c.get("nulls"),
                    *("–" if c.get(k) is None else c.get(k) for k in ("min", "max", "mean", "distinct"))]
                   for c in prof.get("columns") or []]
    preview = prof.get("preview") or {}
    schema_head = [t("dataset.col." + c) for c in ("column", "type", "null", "min", "max", "mean", "distinct")]
    body = f"""
<div class="page-head"><div class="title-row"><h1{USER}>{esc(label)}</h1><span class="badge tone-muted">{words("data.kind", ds['kind'])}</span></div>
<div class="sub">{' · '.join(facts)}</div></div>
{origin}
{site.history.data_history(ds)}
<section><h2>{t("dataset.h.schema")}</h2>{site.table(schema_head, schema_rows, klass='list', data_head=False)}</section>
<section><h2>{t("dataset.h.preview")}</h2>{site.table(preview.get('columns') or [], preview.get('rows') or [])}</section>
<section><h2>{t("dataset.h.relations")}</h2>{''.join(lineage)}</section>
<section><h2>{t("dataset.h.query")}</h2><pre class="cmd">analytics_query(sql="SELECT * FROM {esc(view)} LIMIT 20")
analytics.read("{esc(view)}")   ·   analytics.sql("SELECT count(*) FROM {esc(view)}").pl()</pre></section>"""
    crumb = f' <span>/</span> <a href="../index.html#data">{t("tab.data")}</a> <span>/</span> {user_text(label)}'
    return site.page(1, label, crumb, body, "dataset", page_path="data/" + view + ".html",
                     selection=ds["path"], actions=site.relation_link(ds["path"], 1, klass="action"))


# --- context page ---

def render_markdown(text: str) -> str:
    try:
        from markdown_it import MarkdownIt

        return MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).render(text)
    except Exception:  # noqa: BLE001
        return f"<pre>{esc(text)}</pre>"


def context_page(site: Site) -> str:
    """CONTEXT.md now, its pinned sections, and every recorded version (design 5.2, mockup 9-C): what
    each changed, whether its pinned sections changed, and the insights written while it was the
    latest. A version keeps its anchor (#v-<snapshot name>), where references to it lead."""
    p = site.p
    current = (p.context.read_text(encoding="utf-8", errors="replace")
               if p.context.is_file() else "")
    pinned = core.context_sections(core.context_file_text(p))[1] if current else []
    pinned = [section for section in pinned if section["pinned"]]
    pins = "".join(
        f'<div class="pinned"><h3>{user_text(section_heading(section)) if section_heading(section) else t("overview.section", n=n)}</h3>'
        f'<div class="markdown"{USER}>{render_markdown(section_body(section)[0])}</div></div>'
        for n, section in enumerate(pinned, 1)) or f'<p class="muted">{t("overview.no_pinned")}</p>'
    paths = {v.name: v for v in site.versions}
    history = site.history.context_versions(paths)
    body = f"""
<div class="page-head"><h1>{t("page.context_history")}</h1><div class="sub">{t("context.history_lead")}</div></div>
{site.history.context_head()}
<div class="columns">
  {site.history.context_latest_changes(paths)}
  <aside class="panel"><h3>{t("context.pinned_now")}</h3>{pins}</aside>
</div>
{history or f'<div class="empty">{t("context.no_versions_recorded")}</div>'}
<details class="fold" id="now"><summary><h2>{t("context.now")}</h2></summary>
  <article class="panel markdown"{USER if current else ""}>{render_markdown(current) if current else f'<div class="empty">{t("context.page_empty")}</div>'}</article>
</details>"""
    return site.page(0, "", f' <span>/</span> {t("page.context_history")}', body, "context",
                     title_key="page.context_history")
