"""The Overview's analysis status and the Analysis map (design WORKSPACE-READABILITY 7, #98).

Both come from the one read a build makes (core.read_model), through the Site that holds it, so they
count what the read model judges and nothing else:

- The status says each state of the analysis apart (design 7.1): the evidence insights cite, the
  runs, the inputs changed since, the open items, rerun comparisons, and analysis checks and
  conclusion reviews. It never adds them into one score: citing an exact version is not a reviewed
  conclusion (TERMINOLOGY 6).
- The map draws each script with its runs as one box, and an arrow where a run of one script read a
  file a run of another wrote with the same path and bytes (the read model's producer candidates).
  It is not row-level lineage, and a producer candidate is not a confirmed link (design 7.2).

The pages' helpers are the Site's (as workspace_relations takes them), so this module imports no
other part of the Workspace.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

from . import relations
from .i18n import t

# The strongest citation an insight has, strongest first (design 7.1): a confirmed link, a target
# found that is not one, a name with no version, a target changed since it was cited, any other
# problem (missing, ambiguous, unknown, unsupported), and nothing cited at all.
EVIDENCE = ("confirmed", "found", "unbound", "changed", "problem", "none")
EVIDENCE_TONE = {"confirmed": "good", "found": "link", "unbound": "warning", "changed": "critical",
                 "problem": "critical", "none": "muted"}
# A reference that changes a record's state rather than citing evidence (ADR-0006 6).
STATE_RELATIONS = frozenset({"supersedes", "updates", "resolves"})
# A run that did not end as it should (the status words say which): not `ok`, not still running.
UNFINISHED = frozenset({"running"})
MAIN_FLOWS = 3
# The geometry of a flow, in rem, so a reader's text size scales boxes, gaps and arrows together.
# The stylesheet takes them from the canvas (--node-w, …), so the arrows meet the boxes at any text size.
NODE_W, NODE_H, GAP_X, GAP_Y = 13.0, 11.5, 7.0, 1.25
# How far above its arrow a file's chip sits, so the arrow and its head stay in sight.
CHIP_LIFT = 0.8
SHOWN_NAMES = 4


def evidence_class(judgments) -> str:
    """The strongest citation among a record's references (EVIDENCE): what the read model judged,
    its `confirmed` (ADR-0006 3) first. References that change a record's state are not evidence."""
    best = len(EVIDENCE) - 1
    for judged in judgments:
        if judged.get("relation") in STATE_RELATIONS:
            continue
        state = judged.get("state")
        rank = (0 if judged.get("confirmed") else 1 if state == "resolved" else 2 if state == "version_unbound"
                else 3 if state == "content_changed" else 4)
        best = min(best, rank)
    return EVIDENCE[best]


def failed(run: dict) -> bool:
    return run.get("status") not in ("ok", *UNFINISHED)


def started(run: dict) -> tuple:
    """A run's place in time: its start as an instant (offsets applied), then its id. A start that
    cannot be read, or one without an offset, comes after the ones that can, by its text."""
    text = run.get("started_at")
    try:
        at = datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        at = None
    if at is not None and at.tzinfo is not None:
        return (0, at.timestamp(), "", run["id"])
    return (1, 0.0, str(text or ""), run["id"])


class AnalysisStatus:
    """The six cells of the Overview's analysis status (design 7.1), each from its own source:
    reference judgments, run status, Freshness, record states and rerun comparisons."""

    def __init__(self, site):
        self.site = site
        lists = site.lists
        # The current findings, decisions and assumptions (C1's key information), each counted by its
        # strongest citation.
        self.insights = lists.key
        self.evidence = Counter(evidence_class(site.model.references(site.source(e) or "")) for e in self.insights)
        self.runs = site.runs
        self.statuses = Counter(r.get("status") for r in self.runs)
        self.not_ok = [r for r in self.runs if failed(r)]
        current = [c for c in site.charts if not c.get("retired")]
        self.charts = current
        self.changed, self.upstream = [], []
        for chart in current:
            fresh = site.fresh.of(chart["run"])
            if fresh["changed_inputs"]:
                self.changed.append(chart)
            elif fresh["stale_upstream"]:
                self.upstream.append(chart)
        self.open = lists.open
        self.open_kinds = Counter(e.get("kind") for e in self.open)
        self.reruns = [(r, relations.run_reproduction(r)) for r in self.runs if relations.run_reproduction(r)]
        self.verdicts = Counter(rep.get("verdict") for _, rep in self.reruns)

    def counts(self) -> dict:
        """The numbers every cell shows, as one dict (what the tests compare with the read model)."""
        return {"insights": len(self.insights), "evidence": {k: self.evidence.get(k, 0) for k in EVIDENCE},
                "runs": dict(self.statuses), "changed": len(self.changed), "upstream": len(self.upstream),
                "open": dict(self.open_kinds), "reruns": len(self.reruns), "verdicts": dict(self.verdicts),
                "checks": None, "reviews": None}

    def html(self) -> str:
        cells = [self.evidence_cell(), self.runs_cell(), self.inputs_cell(), self.open_cell(),
                 self.rerun_cell(), self.review_cell()]
        return (f'<div class="status"><div class="section-head">'
                f'<h2 id="status-title">{t("analysis.title")}</h2><p class="muted">{t("analysis.lead")}</p></div>'
                f'<div class="status-grid">{"".join(cells)}</div></div>')

    @staticmethod
    def cell(name: str, icon: str, title: str, head: str, body: str = "", link: str = "") -> str:
        head_html = f'<p class="cell-head">{head}</p>' if head else ""
        return (f'<article class="status-cell cell-{name}"><h3><span class="cell-icon" aria-hidden="true">{icon}</span>'
                f'{title}</h3>{head_html}{body}{link}</article>')

    @staticmethod
    def more(href_: str, words_html: str) -> str:
        return f'<p class="cell-link"><a href="{href_}">{words_html} <span aria-hidden="true">→</span></a></p>'

    def evidence_cell(self) -> str:
        total = len(self.insights)
        if not total:
            return self.cell("evidence", "🔗", t("analysis.evidence.title"), t("analysis.none_recorded"),
                             f'<p class="muted">{t("analysis.evidence.empty")}</p>')
        confirmed = self.evidence.get("confirmed", 0)
        bar = "".join(f'<span class="seg tone-{EVIDENCE_TONE[k]}" style="flex-grow:{self.evidence[k]}"></span>'
                      for k in EVIDENCE if self.evidence.get(k))
        legend = " · ".join(t(f"analysis.evidence.{k}", n=self.evidence[k]) for k in EVIDENCE if self.evidence.get(k))
        link = self.more("#insights", t("analysis.evidence.link")) if total - confirmed else ""
        return self.cell("evidence", "🔗", t("analysis.evidence.title"),
                         t("analysis.evidence.head", n=confirmed, total=total),
                         f'<div class="evidence-bar" aria-hidden="true">{bar}</div><p class="cell-note">{legend}</p>', link)

    def runs_cell(self) -> str:
        s = self.site
        if not self.runs:
            return self.cell("runs", "▷", t("analysis.runs.title"), t("analysis.none_recorded"),
                             f'<p class="muted">{t("analysis.runs.empty")}</p>')
        ok = self.statuses.get("ok", 0)
        others = " ".join(s.status_badge(k) + f' <span class="n">{s.fmt_num(v)}</span>'
                          for k, v in sorted(self.statuses.items(), key=lambda kv: str(kv[0])) if k != "ok")
        last = self.runs[-1]
        note = (f'<p class="cell-note">{t("analysis.runs.last")} {s.fmt_time(last.get("started_at"))} — '
                f'<a href="runs/{s.href(last["id"])}.html"{s.user_language}>{s.esc(last.get("title") or last["id"])}</a></p>')
        link = self.more("#runs", t("analysis.runs.link")) if self.not_ok else ""
        return self.cell("runs", "▷", t("analysis.runs.title"),
                         f'{t("analysis.runs.head", n=ok)} {others}', note, link)

    def inputs_cell(self) -> str:
        if not self.charts:
            return self.cell("inputs", "⚠", t("analysis.inputs.title"), t("analysis.none_recorded"),
                             f'<p class="muted">{t("analysis.inputs.empty")}</p>')
        if not self.changed and not self.upstream:
            return self.cell("inputs", "✓", t("analysis.inputs.title"), t("analysis.inputs.none"),
                             f'<p class="cell-note">{t("analysis.inputs.none_note", n=len(self.charts))}</p>')
        note = (f'<p class="cell-note">{t("analysis.inputs.direct", n=len(self.changed))}'
                + (f' {t("analysis.inputs.upstream", n=len(self.upstream))}' if self.upstream else "") + "</p>")
        total = len(self.changed) + len(self.upstream)
        return self.cell("inputs", "⚠", t("analysis.inputs.title"),
                         t("analysis.inputs.head", n=total), note,
                         self.more("#charts", t("analysis.inputs.link")))

    def open_cell(self) -> str:
        s = self.site
        if not self.open:
            return self.cell("open", "?", t("analysis.open.title"), t("analysis.open.none"),
                             f'<p class="muted">{t("analysis.open.empty")}</p>')
        newest = max((e.get("ts") or "" for e in self.open), default="")
        note = (f'<p class="cell-note">{t("analysis.open.questions", n=self.open_kinds.get("question", 0))} · '
                f'{t("analysis.open.todos", n=self.open_kinds.get("todo", 0))}. {t("analysis.open.newest")} {s.fmt_time(newest)}</p>')
        return self.cell("open", "?", t("analysis.open.title"),
                         t("analysis.open.head", n=len(self.open)), note,
                         self.more("lists/open-0.html", t("analysis.open.link", n=len(self.open))))

    def rerun_cell(self) -> str:
        s = self.site
        if not self.reruns:
            return self.cell("rerun", "↻", t("analysis.rerun.title"), t("analysis.not_run"),
                             f'<p class="muted">{t("analysis.rerun.empty")}</p>')
        verdicts = " · ".join(f'{s.words("verdict", k)} {s.fmt_num(v)}'
                              for k, v in sorted(self.verdicts.items(), key=lambda kv: str(kv[0])))
        return self.cell("rerun", "↻", t("analysis.rerun.title"),
                         t("analysis.rerun.head", n=len(self.reruns)),
                         f'<p class="cell-note">{verdicts}. {t("analysis.rerun.note")}</p>')

    def review_cell(self) -> str:
        # No record kind holds an analysis check (keys, units, denominators, periods) or a person's
        # review of a conclusion yet (design 6.2): both say so, apart.
        body = (f'<dl class="cell-lines"><dt>{t("analysis.review.checks")}</dt><dd>{t("analysis.none_recorded")}</dd>'
                f'<dt>{t("analysis.review.conclusions")}</dt><dd>{t("analysis.none_recorded")}</dd></dl>'
                f'<p class="muted">{t("analysis.review.empty")}</p>')
        return self.cell("review", "☑", t("analysis.review.title"), "", body)


# --- the map ---------------------------------------------------------------------------------------


@dataclass
class Node:
    """One script and its runs (oldest first): a box of the map."""

    key: str
    script: str | None
    runs: list[dict] = field(default_factory=list)
    charts: int = 0
    cited: set = field(default_factory=set)
    confirmed: set = field(default_factory=set)
    reads: list = field(default_factory=list)
    changed: bool = False
    upstream: bool = False
    cycle: bool = False

    @property
    def latest(self) -> dict:
        return self.runs[-1]

    @property
    def name(self) -> str:
        return PurePosixPath(self.script).name if self.script else str(self.latest.get("title") or self.latest["id"])


@dataclass
class Edge:
    """Files a run of `source` wrote that a run of `target` read with the same path and bytes."""

    source: str
    target: str
    files: list = field(default_factory=list)
    ambiguous: bool = False


@dataclass
class Flow:
    """Scripts joined by arrows (a weakly connected part of the map), laid out in columns."""

    keys: list
    place: dict = field(default_factory=dict)  # key -> (column, row)
    columns: int = 1
    rows: int = 1
    runs: int = 0
    first: str = ""


def _components(keys, edges) -> list[list[str]]:
    """The weakly connected parts of the script graph, each sorted."""
    near = defaultdict(set)
    for edge in edges:
        near[edge.source].add(edge.target)
        near[edge.target].add(edge.source)
    seen, parts = set(), []
    for key in sorted(keys):
        if key in seen:
            continue
        stack, part = [key], []
        seen.add(key)
        while stack:
            node = stack.pop()
            part.append(node)
            for other in sorted(near[node]):
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        parts.append(sorted(part))
    return parts


def _strong(keys, succ) -> list[list[str]]:
    """Tarjan's strongly connected parts of a flow, iterative and in name order (deterministic)."""
    index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]
    for root in sorted(keys):
        if root in index:
            continue
        work = [(root, iter(sorted(succ[root])))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            node, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(sorted(succ[nxt]))))
                elif nxt in on:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                part = []
                while True:
                    member = stack.pop()
                    on.discard(member)
                    part.append(member)
                    if member == node:
                        break
                out.append(sorted(part))
    return out


def layout(keys, edges) -> tuple[dict, int, int]:
    """Columns by the longest path once scripts that reach each other are one stage, rows by name
    (design 7.2): {key: (column, row)}, columns, rows."""
    keys = sorted(keys)
    succ = defaultdict(set)
    for edge in edges:
        succ[edge.source].add(edge.target)
    parts = _strong(keys, succ)
    part_of = {k: i for i, part in enumerate(parts) for k in part}
    preds = defaultdict(set)
    for edge in edges:
        a, b = part_of[edge.source], part_of[edge.target]
        if a != b:
            preds[b].add(a)
    stage: dict[int, int] = {}

    def depth(i: int) -> int:
        # The longest path to a part; the parts form a DAG, so the walk ends (iteratively, by memo).
        pending = [i]
        while pending:
            j = pending[-1]
            todo = [p for p in preds[j] if p not in stage]
            if todo:
                pending.extend(todo)
                continue
            pending.pop()
            stage[j] = 1 + max((stage[p] for p in preds[j]), default=-1)
        return stage[i]

    columns: dict[int, list[str]] = defaultdict(list)
    for i, part in enumerate(parts):
        columns[depth(i)].extend(part)
    place = {}
    for column, members in columns.items():
        for row, key in enumerate(sorted(members)):
            place[key] = (column, row)
    return place, max(columns) + 1 if columns else 1, max((len(m) for m in columns.values()), default=1)


class AnalysisMap:
    """The scripts, their runs and the files between them (design 7.2)."""

    def __init__(self, site):
        self.site = site
        model = site.model
        runs = sorted(site.runs, key=started)
        self.nodes: dict[str, Node] = {}
        node_of: dict[str, str] = {}
        for run in runs:
            script = site.script_of.get(run["id"])
            key = script or f"run:{run['id']}"
            self.nodes.setdefault(key, Node(key, script)).runs.append(run)
            node_of[run["id"]] = key
        self.node_of = node_of
        # Who cites each run: the current insights (C1's key information), and of them those whose
        # citation of the run is a confirmed link (ADR-0006 3).
        insights = {site.source(e) for e in site.lists.key}
        self.citing: dict[str, set] = defaultdict(set)
        confirmed: dict[str, set] = defaultdict(set)
        for judged in model.references():
            target = judged.get("target") or {}
            run_id = target.get("run_id")
            if judged.get("from") in insights and judged.get("relation") not in STATE_RELATIONS and run_id in node_of:
                self.citing[run_id].add(judged["from"])
                if judged.get("confirmed"):
                    confirmed[run_id].add(judged["from"])
        imported = {ds["path"]: ds["name"] for ds in site.datasets if ds.get("kind") in ("imported", "raw")}
        charts_by_run = Counter(c["run"] for c in site.charts if not c.get("retired"))
        edges: dict[tuple, Edge] = {}
        self.ambiguous: dict[str, list] = {}
        reads_from: dict[str, set] = defaultdict(set)
        for run in runs:
            key = node_of[run["id"]]
            node = self.nodes[key]
            node.charts += charts_by_run.get(run["id"], 0)
            node.cited |= self.citing.get(run["id"], set())
            node.confirmed |= confirmed.get(run["id"], set())
            for entry in run.get("inputs") or []:
                path = entry.get("path")
                if path in imported and imported[path] not in node.reads:
                    node.reads.append(imported[path])
                candidates, _ = model.producer_candidates(path, entry.get("sha256"), consumer=run["id"])
                reads_from[run["id"]].update(candidates)
                if len(candidates) > 1:
                    self.ambiguous.setdefault(path, candidates)
                # Between scripts, a file is uncertain only when runs of more than one script wrote
                # those bytes (a rerun that wrote them again is the same script's).
                several = len({node_of.get(c) for c in candidates}) > 1
                for candidate in candidates:
                    source = node_of.get(candidate)
                    if source is None or source == key:
                        continue
                    edge = edges.setdefault((source, key), Edge(source, key))
                    if path not in edge.files:
                        edge.files.append(path)
                    edge.ambiguous |= several
        # A run is on a cycle when a run it read from reaches it again by producer candidates: a member
        # of the cycle the read model's upstream walk reports (ReadModel.upstream), not a run whose walk
        # only meets one further up.
        for run in runs:
            if any(run["id"] in model.upstream(p)["upstream"] for p in reads_from[run["id"]]):
                self.nodes[node_of[run["id"]]].cycle = True
        # A box's input marks judge the runs its visualizations come from (the gallery's, as the
        # Overview's Input changes cell counts them) and its latest successful run.
        shown = defaultdict(set)
        for chart in site.charts:
            if not chart.get("retired") and chart["run"] in node_of:
                shown[node_of[chart["run"]]].add(chart["run"])
        for node in self.nodes.values():
            judged = set(shown[node.key])
            done = [r for r in node.runs if r.get("status") == "ok"]
            if done:
                judged.add(done[-1]["id"])
            fresh = [site.fresh.of(r) for r in sorted(judged)]
            node.changed = any(f["changed_inputs"] for f in fresh)
            node.upstream = not node.changed and any(f["stale_upstream"] for f in fresh)
        self.edges = sorted(edges.values(), key=lambda e: (e.source, e.target))
        for edge in self.edges:
            edge.files.sort()
        flows = []
        for keys in _components(self.nodes, self.edges):
            inner = [e for e in self.edges if e.source in keys]
            place, columns, rows = layout(keys, inner)
            flow = Flow(keys, place, columns, rows, sum(len(self.nodes[k].runs) for k in keys),
                        min(str(self.nodes[k].runs[0].get("started_at") or "") for k in keys))
            flows.append(flow)
        cited = {id(f): len(set().union(*(self.nodes[k].cited for k in f.keys))) for f in flows}
        ranked = sorted(flows, key=lambda f: (-f.runs, -cited[id(f)], f.first, f.keys[0]))
        main = {id(f) for f in ranked[:MAIN_FLOWS]}
        self.main = sorted((f for f in flows if id(f) in main), key=lambda f: (f.first, f.keys[0]))
        self.rest = sorted((f for f in flows if id(f) not in main), key=lambda f: (f.first, f.keys[0]))
        # Scripts that read each other's files with no run-level cycle: they exchange files (a
        # repeated analysis, A1 → B1 → A2), which is not a cycle (design 7.2).
        self.exchanges = []
        for flow in flows:
            inner = [e for e in self.edges if e.source in flow.keys]
            succ = defaultdict(set)
            for edge in inner:
                succ[edge.source].add(edge.target)
            for part in _strong(flow.keys, succ):
                if len(part) > 1 and not any(self.nodes[k].cycle for k in part):
                    self.exchanges.append(part)

    # --- what the Overview shows of it ---

    def summary_html(self, status: AnalysisStatus) -> str:
        """The map's summary on the Overview (design 7.1): imported data → runs → visualizations →
        insights, in the kinds' own colours and shapes and named in words (design 4)."""
        s = self.site
        imported = [ds for ds in s.datasets if ds.get("kind") in ("imported", "raw")]
        derived = [ds for ds in s.datasets if ds.get("kind") == "derived"]
        names = ", ".join(s.esc(ds["name"]) for ds in imported[:SHOWN_NAMES])
        more = len(imported) - SHOWN_NAMES
        names_html = (f'<p class="box-sub"{s.user_language}>{names}</p>' if names else "") + (
            f'<p class="box-sub">{t("list.and_more", n=more)}</p>' if more > 0 else "")
        made_by = len({c["run"] for c in status.charts})
        changed = len(status.changed) + len(status.upstream)
        boxes = [
            self.box("data", t("map.kind.imported"), len(imported), names_html),
            self.box("run", t("map.kind.runs"), len(s.runs),
                     f'<p class="box-sub">{self.scripts_words(self.nodes)} · {t("mapsum.flows", n=len(self.main) + len(self.rest))}</p>'
                     + (f'<p>{s.badge("critical", "⊗", t("mapsum.failed", n=len(status.not_ok)))}</p>' if status.not_ok else "")),
            self.box("viz", t("map.kind.viz"), len(status.charts),
                     f'<p class="box-sub">{t("mapsum.made_by", n=made_by)} · {t("mapsum.derived", n=len(derived))}</p>'
                     + (f'<p>{s.badge("warning", "⚠", t("mapsum.changed", n=changed))}</p>' if changed else "")),
            self.box("insight", t("map.kind.insights"), len(status.insights),
                     f'<p class="box-sub">{t("mapsum.confirmed", n=status.evidence.get("confirmed", 0))}</p>'
                     + (f'<p>{s.badge("muted", "–", t("mapsum.cite_nothing", n=status.evidence.get("none", 0)))}</p>'
                        if status.evidence.get("none") else "")),
        ]
        return (f'<div class="map-summary"><div class="section-head">'
                f'<h2 id="mapsum-title">{t("tab.map")}</h2><p class="muted">{t("mapsum.lead")}</p>'
                f'<a class="head-link" href="#map">{t("mapsum.open")} <span aria-hidden="true">→</span></a></div>'
                f'<ol class="map-boxes">{"".join(boxes)}</ol></div>')

    def scripts_words(self, keys) -> str:
        """How many scripts, and how many runs without a script (a box of their own each)."""
        scripts = sum(1 for k in keys if self.nodes[k].script)
        alone = sum(1 for k in keys if not self.nodes[k].script)
        return " · ".join(([t("map.scripts", n=scripts)] if scripts or not alone else [])
                          + ([t("map.no_script", n=alone)] if alone else []))

    def box(self, kind: str, title: str, n: int, body: str) -> str:
        return (f'<li class="map-box kind-{kind}"><p class="box-kind"><span class="kind-mark" aria-hidden="true"></span>{title}</p>'
                f'<p class="box-n">{self.site.fmt_num(n)}</p>{body}</li>')

    # --- the tab ---

    def tab_html(self) -> str:
        if not self.nodes:
            body = f'<div class="empty">{t("map.empty")}</div>'
        else:
            flows = "".join(self.flow_html(f, n) for n, f in enumerate(self.main, 1))
            if self.rest:
                keys = [k for f in self.rest for k in f.keys]
                runs = sum(len(self.nodes[k].runs) for k in keys)
                charts = sum(self.nodes[k].charts for k in keys)
                cited = set().union(*(self.nodes[k].cited for k in keys))
                exact = set().union(*(self.nodes[k].confirmed for k in keys))
                marks = []
                if any(self.nodes[k].cycle for k in keys):
                    marks.append(t("map.more.cycle"))
                if any(e.ambiguous for e in self.edges if e.target in keys):
                    marks.append(t("map.more.ambiguous"))
                flows += (f'<details class="flow-more"><summary><strong>{t("map.more", n=len(self.rest))}</strong> · '
                          f'{self.scripts_words(keys)} · {t("map.runs", n=runs)} · {t("map.viz", n=charts)} · '
                          f'{t("map.insights", n=len(cited))} ({t("map.confirmed", n=len(exact))})'
                          + (f'<br><span class="muted">{" · ".join(marks)}</span>' if marks else "") + '</summary>'
                          + '<ol class="flow-rows">' + "".join(self.rows_html(f, n) for n, f in enumerate(self.rest, len(self.main) + 1))
                          + '</ol></details>')
            as_list = f'<details class="as-list"><summary>{t("map.as_list")}</summary></details>'
            body = (f'<div class="map-layout"><div class="map-flows">{as_list}{flows}</div>'
                    f'<aside class="map-side">{self.attention_html()}{self.legend_html()}</aside></div>')
        return (f'<section id="map" class="tab"><div class="toolbar"><h2>{t("tab.map")}</h2></div>'
                f'<p class="muted">{t("map.lead")}</p>{body}</section>')

    def flow_html(self, flow: Flow, number: int) -> str:
        s = self.site
        head = (f'<h3 id="flow-{number}">{t("map.flow", n=number)} · {self.scripts_words(flow.keys)} · '
                f'{t("map.runs", n=flow.runs)}</h3>')
        width = flow.columns * NODE_W + (flow.columns - 1) * GAP_X
        height = flow.rows * NODE_H + (flow.rows - 1) * GAP_Y
        inner = [e for e in self.edges if e.source in flow.keys]
        paths, chips = [], []
        pairs = {(e.source, e.target) for e in inner}
        for edge in inner:
            d, cx, cy, low = edge_geometry(flow.place[edge.source], flow.place[edge.target],
                                           outer=(edge.target, edge.source) in pairs and edge.source > edge.target)
            height = max(height, low)
            dash = ' class="maybe"' if edge.ambiguous else ""
            paths.append(f'<path d="{d}"{dash}/>')
            label = PurePosixPath(edge.files[0]).stem + (f" +{len(edge.files) - 1}" if len(edge.files) > 1 else "")
            mark = '<span aria-hidden="true">⚠ </span>' if edge.ambiguous else ""
            # The same files are in the writing box's words (`map-out`): the chip is what the eye reads.
            chips.append(f'<span class="map-chip{" maybe" if edge.ambiguous else ""}" style="left:{cx:g}rem;top:{cy:g}rem"'
                         f' title="{s.esc(", ".join(edge.files))}" lang="" aria-hidden="true">{mark}{s.esc(label)}</span>')
        svg = (f'<svg class="flow-edges" viewBox="0 0 {width:g} {height:g}" style="width:{width:g}rem;height:{height:g}rem" '
               f'aria-hidden="true" focusable="false"><defs><marker id="arrow-{number}" viewBox="0 0 6 6" refX="5" refY="3" '
               f'markerWidth="0.55" markerHeight="0.55" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0L6 3L0 6z"/></marker></defs>'
               f'<g marker-end="url(#arrow-{number})">{"".join(paths)}</g></svg>') if inner else ""
        nodes = "".join(self.node_html(self.nodes[k], flow, inner) for k in sorted(flow.keys, key=lambda k: flow.place[k]))
        return (f'<div class="flow" role="group" aria-labelledby="flow-{number}">{head}<div class="flow-scroll">'
                f'<div class="flow-canvas" style="width:{width:g}rem;height:{height:g}rem;--node-w:{NODE_W:g}rem;--node-h:{NODE_H:g}rem;'
                f'--gap-x:{GAP_X:g}rem;--gap-y:{GAP_Y:g}rem">{svg}{"".join(chips)}'
                f'<ol class="flow-nodes">{nodes}</ol></div></div></div>')

    def rows_html(self, flow: Flow, number: int) -> str:
        """A smaller flow, folded: a line per script with the facts of its box, in the order of its
        columns. The first page of a large project stays small; the main flows carry the diagram."""
        s = self.site
        inner = [e for e in self.edges if e.source in flow.keys]
        items = []
        for key in sorted(flow.keys, key=lambda k: flow.place[k]):
            node = self.nodes[key]
            latest = node.latest
            facts = [t("map.runs", n=len(node.runs)), s.status_badge(latest.get("status")),
                     t("map.viz", n=node.charts), f'{t("map.insights", n=len(node.cited))} ({t("map.confirmed", n=len(node.confirmed))})']
            facts += [w for w, on in ((t("map.input_changed"), node.changed), (t("map.upstream_changed"), node.upstream),
                                      (t("map.cycle"), node.cycle)) if on]
            outs = [f'<span{s.user_language}>{s.esc(", ".join(PurePosixPath(f).name for f in e.files))}</span> → '
                    f'<a href="#{self.anchor(self.nodes[e.target])}"{s.user_language}>{s.esc(self.nodes[e.target].name)}</a>'
                    + (f' {t("map.possible_producers")}' if e.ambiguous else "") for e in inner if e.source == key]
            title = s.title_attr(node.script) if node.script else ""
            items.append(f'<li id="{self.anchor(node)}"><a href="runs/{s.href(latest["id"])}.html"{title}{s.user_language}>'
                         f'{s.esc(node.name)}</a> · {" · ".join(facts)}'
                         + (f' · {t("map.writes")} {"; ".join(outs)}' if outs else "") + '</li>')
        return (f'<li class="flow-row"><p class="row-head">{t("map.flow", n=number)} · {self.scripts_words(flow.keys)} · '
                f'{t("map.runs", n=flow.runs)}</p><ul class="plain">{"".join(items)}</ul></li>')

    def node_html(self, node: Node, flow: Flow, inner: list) -> str:
        s = self.site
        column, row = flow.place[node.key]
        latest = node.latest
        state = s.status_badge(latest.get("status"))
        if failed(latest) and len(node.runs) > 1:
            state = s.badge("critical", "✕", t("map.latest_failed"))
        flags = []
        if node.changed:
            flags.append(s.badge("warning", "⚠", t("map.input_changed")))
        elif node.upstream:
            flags.append(s.badge("warning", "⚠", t("map.upstream_changed")))
        if node.cycle:
            flags.append(s.badge("warning", "↻", t("map.cycle")))
        on_latest = len(self.citing.get(latest["id"], ())) if failed(latest) else 0
        insights = (f'<li class="fact-insight{" alert" if on_latest else ""}">'
                    + (t("map.cite_failed", n=on_latest) if on_latest
                       else f'{t("map.insights", n=len(node.cited))} · {t("map.confirmed", n=len(node.confirmed))}') + '</li>')
        reads = (f'<li class="fact-data">{t("map.reads")} <span{s.user_language}>{s.esc(", ".join(node.reads[:3]))}</span>'
                 + (f' {t("list.and_more", n=len(node.reads) - 3)}' if len(node.reads) > 3 else "") + '</li>') if node.reads else ""
        outs = []
        for edge in inner:
            if edge.source == node.key:
                target = self.nodes[edge.target]
                outs.append(f'<li><span{s.user_language}>{s.esc(", ".join(PurePosixPath(f).name for f in edge.files))}</span> → '
                            f'<a href="#{self.anchor(target)}"{s.user_language}>{s.esc(target.name)}</a>'
                            + (f' {t("map.possible_producers")}' if edge.ambiguous else "") + '</li>')
        writes = f'<li class="map-out">{t("map.writes")}<ul>{"".join(outs)}</ul></li>' if outs else ""
        title = s.title_attr(node.script) if node.script else ""
        link = f'runs/{s.href(latest["id"])}.html'
        return (f'<li class="map-node{" is-failed" if failed(latest) else ""}" id="{self.anchor(node)}" '
                f'style="grid-column:{column + 1};grid-row:{row + 1}">'
                f'<p class="node-kind">{t("map.script") if node.script else t("map.run")} · {t("map.runs", n=len(node.runs))}</p>'
                f'<h4><a href="{link}"{title}{s.user_language}>{s.esc(node.name)}</a></h4>'
                f'<p class="node-state">{state} {s.fmt_time(latest.get("started_at"))}</p>'
                + (f'<p class="node-flags">{" ".join(flags)}</p>' if flags else "")
                + f'<ul class="node-facts"><li class="fact-viz">{t("map.viz", n=node.charts)}</li>{insights}{reads}{writes}</ul></li>')

    def anchor(self, node: Node) -> str:
        return "script-" + format(hash_text(node.key), "x")[:10]

    def attention_html(self) -> str:
        """What needs a look (design 7.2's marks): runs that did not succeed and the insights citing
        them, visualizations whose inputs changed, files with several possible producers, run-level
        cycles, and scripts that exchange files without one."""
        s = self.site
        items = []
        broken = [r for n in self.nodes.values() for r in n.runs if failed(r)]
        if broken:
            shown = ", ".join(f'<a href="runs/{s.href(r["id"])}.html"{s.user_language}>{s.esc(r.get("title") or r["id"])}</a>'
                              f' ({s.fmt_time(r.get("started_at"))})' for r in broken[:3])
            kinds = sorted({(r.get("error") or {}).get("type") for r in broken} - {None})
            why = f' · <code{s.user_language}>{s.esc(", ".join(kinds))}</code>' if kinds else ""
            more = len(broken) - 3
            items.append(("critical", "⊗", t("map.attn.failed", n=len(broken)),
                          shown + (f' {t("list.and_more", n=more)}' if more > 0 else "") + why))
            cited = sorted(set().union(*(self.citing.get(r["id"], set()) for r in broken)), key=_seq)
            if cited:
                names = sorted({self.nodes[self.node_of[r["id"]]].name for r in broken if self.citing.get(r["id"])})
                links = ", ".join(self.record_link(c) for c in cited[:8]) + (
                    " " + t("list.and_more", n=len(cited) - 8) if len(cited) > 8 else "")
                items.append(("critical", "⊗", t("map.attn.cite_failed", n=len(cited)),
                              f'<span{s.user_language}>{s.esc(", ".join(names[:4]))}</span>'
                              + (f' {t("list.and_more", n=len(names) - 4)}' if len(names) > 4 else "") + f' · {links}'))
        changed = [n for n in self.nodes.values() if n.changed]
        upstream = [n for n in self.nodes.values() if n.upstream]
        if changed or upstream:
            names = ", ".join(f'<span{s.user_language}>{s.esc(n.name)}</span>' for n in (changed + upstream)[:4])
            items.append(("warning", "⚠", t("map.attn.changed", n=len(changed) + len(upstream)),
                          f'{names} · {t("map.attn.changed_note")}'))
        if self.ambiguous:
            files = ", ".join(f'<span{s.user_language}>{s.esc(PurePosixPath(p).name)}</span>' for p in sorted(self.ambiguous)[:4])
            items.append(("warning", "⚠", t("map.attn.ambiguous", n=len(self.ambiguous)),
                          f'{files} · {t("map.attn.ambiguous_note")}'))
        cycles = sorted({n.key for n in self.nodes.values() if n.cycle})
        if cycles:
            names = ", ".join(f'<span{s.user_language}>{s.esc(self.nodes[k].name)}</span>' for k in cycles[:4])
            items.append(("warning", "↻", t("map.attn.cycle", n=len(cycles)), f'{names} · {t("map.attn.cycle_note")}'))
        for part in self.exchanges:
            names = ", ".join(f'<span{s.user_language}>{s.esc(self.nodes[k].name)}</span>' for k in part)
            items.append(("muted", "⇄", t("map.attn.exchange"), f'{names} · {t("map.attn.exchange_note")}'))
        if not items:
            body = f'<p class="muted">{t("map.attn.none")}</p>'
        else:
            body = '<ul class="attention">' + "".join(
                f'<li class="tone-{tone}"><span class="attn-mark" aria-hidden="true">{mark}</span><div><strong>{head}</strong>'
                f'<p>{detail}</p></div></li>' for tone, mark, head, detail in items) + "</ul>"
        return f'<div class="panel"><h3>{t("map.attn.title")}</h3>{body}</div>'

    def record_link(self, source: str) -> str:
        s = self.site
        link = s.relations.content(source, 0) or s.relations.link(source, 0)
        return f'<a href="{s.esc(link)}">{s.esc(source)}</a>'

    def legend_html(self) -> str:
        rows = [("legend-run", t("map.legend.run")), ("legend-chip", t("map.legend.chip")),
                ("legend-dots", t("map.legend.facts")), ("legend-marks", t("map.legend.marks"))]
        items = "".join(f'<li><span class="legend-sample {k}" aria-hidden="true"></span>{words}</li>' for k, words in rows)
        return (f'<div class="panel legend"><h3>{t("map.legend.title")}</h3><ul class="plain">{items}</ul>'
                f'<p class="muted">{t("map.legend.note")}</p></div>')


def edge_geometry(source: tuple, target: tuple, *, outer: bool = False) -> tuple[str, float, float, float]:
    """An arrow between two boxes, in rem: (SVG path, chip x, chip y, lowest y it reaches). Columns
    next to each other get a curve with the file's chip above it. An arrow that skips columns runs
    through the gap under its source's row, so it never passes behind a box, and its chip sits where
    it leaves. Boxes of one stage (scripts that read each other's files) get an arc beside their
    column; `outer` is the second of two opposite arcs, drawn wider with its chip lower."""
    (c1, r1), (c2, r2) = source, target
    y1, y2 = r1 * (NODE_H + GAP_Y) + NODE_H / 2, r2 * (NODE_H + GAP_Y) + NODE_H / 2
    if c1 == c2:
        x = c1 * (NODE_W + GAP_X) + NODE_W
        bend = x + GAP_X * (0.85 if outer else 0.55)
        d = f"M{x:g} {y1:g}C{bend:g} {y1:g} {bend:g} {y2:g} {x + 0.2:g} {y2:g}"
        return d, x + GAP_X * (0.62 if outer else 0.41), (y1 + y2) / 2 + (0.75 if outer else -0.75), max(y1, y2)
    x1, x2 = c1 * (NODE_W + GAP_X) + NODE_W, c2 * (NODE_W + GAP_X)
    if c2 == c1 + 1:
        mid = (x1 + x2) / 2
        d = f"M{x1:g} {y1:g}C{mid:g} {y1:g} {mid:g} {y2:g} {x2 - 0.2:g} {y2:g}"
        return d, mid, (y1 + y2) / 2 - CHIP_LIFT, max(y1, y2)
    gap = r1 * (NODE_H + GAP_Y) + NODE_H + GAP_Y / 2
    xa, xb, xc, xd = x1 + GAP_X * 0.35, x1 + GAP_X * 0.6, x2 - GAP_X * 0.6, x2 - GAP_X * 0.35
    d = (f"M{x1:g} {y1:g}C{xa:g} {y1:g} {xa:g} {gap:g} {xb:g} {gap:g}L{xc:g} {gap:g}"
         f"C{xd:g} {gap:g} {xd:g} {y2:g} {x2 - 0.2:g} {y2:g}")
    return d, x1 + GAP_X * 0.5, (y1 + gap) / 2 + CHIP_LIFT, gap + GAP_Y / 2


def hash_text(text: str) -> int:
    """A digest of a key as a number (a stable anchor suffix: hash() changes per process)."""
    return int(hashlib.sha1(text.encode()).hexdigest()[:12], 16)


def _seq(source: str) -> tuple:
    digits = "".join(ch for ch in source if ch.isdigit())
    return (int(digits) if digits else 0, source)
