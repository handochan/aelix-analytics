"""Static, source-qualified one-hop reference pages. Semantics belong to ReadModel."""
from __future__ import annotations

import hashlib
import json

from . import relations

PAGE_SIZE = 18


def name(identity: str, page: int = 0) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest() + f"-{page}.html"


class RelationPages:
    page_size = PAGE_SIZE
    filename = staticmethod(name)

    def __init__(self, site):
        self.site = site
        self.model = site.model
        self.model.prepare_reference_neighborhoods()
        self.events = {self.model.journal.source_of(e): e for e in self.model.journal.events}
        self.visualizations = {f"{run['id']}/{v['id']}": (run, v)
                               for run in site.runs for v in relations.run_visualizations(run)}
        self.context = {"context:" + p.name: p for p in site.versions}
        self.identities = set(self.events) | {"run:" + r["id"] for r in site.runs}
        self.identities.update(self.visualizations)
        self.identities.update(self.context)
        self.identities.update(ds["path"] for ds in site.datasets)
        for row in self.model.references():
            self.identities.add(row["from"])
            target = relations.query_target(row)
            if isinstance(target, str):
                self.identities.add(target)

    def link(self, identity: str, depth: int, page: int = 0, anchor: str = "") -> str:
        return f"{self.site.root(depth)}relations/{name(identity, page)}{anchor}"

    def content(self, identity: str, depth: int) -> str | None:
        s = self.site
        if identity in self.events:
            return f"{s.root(depth)}index.html#{s.anchor(self.events[identity].value)}" if (
                any(s.source(e) == identity for e in s.events[-50:])) else self.link(identity, depth, anchor="#record")
        if identity.startswith("run:") and identity[4:] in s.run_by_id:
            return s.run_link(identity[4:], depth)
        if identity in self.visualizations:
            run, v = self.visualizations[identity]
            return s.run_link(run["id"], depth, "#chart-" + core_href(str(v["id"])))
        if identity in self.context:
            return f"{s.root(depth)}context.html#v-{core_href(self.context[identity].stem)}"
        return None

    def label(self, identity: str) -> str:
        if identity in self.events:
            event = self.events[identity]
            return f"{event.kind} {identity}: {str(event.value.get('text') or '')[:100]}"
        if identity in self.visualizations:
            return f"Visualization {identity}: {self.visualizations[identity][1].get('title') or ''}"
        return identity

    def render(self, identity: str, page: int = 0) -> str:
        s = self.site
        USER, esc, user_text = s.user_language, s.esc, s.user_text
        rows = self.model.reference_neighborhood(identity)
        shown = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        sides = {"incoming": [], "outgoing": []}
        items = []
        for i, row in enumerate(shown):
            target = relations.query_target(row)
            target = target if isinstance(target, str) else None
            other = row["from"] if row["direction"] == "incoming" else target
            label = self.label(other) if other else "Unknown target"
            qualification = (f"{row.get('relation') or 'unknown relation'} · {row.get('basis') or 'unknown basis'} · "
                             f"{row['state']} · binding {row.get('version_binding') or 'unknown'} · "
                             f"{'confirmed link' if row.get('confirmed') else 'not a confirmed link'}")
            sides[row["direction"]].append(
                f'<div class="relation-node ref-{esc(row["state"])}" data-direction="{row["direction"]}" data-edge="{i}">'
                f'<div{USER}>{esc(label)}</div><small{USER}>{esc(qualification)}</small></div>')
            source_link = self.link(row["from"], 1, anchor="#record")
            other_link = self.link(other, 1) if other else None
            node = f'<a href="{other_link}"{USER}>{esc(label)}</a>' if other_link else user_text(label)
            content = self.content(other, 1) if other and (row["direction"] == "incoming" or (
                row["state"] == "resolved" and row.get("version_binding") != "none")) else None
            # The text alternative reads in the edge's direction, like the diagram: source → target.
            edge = f"{node} → Selected version" if row["direction"] == "incoming" else f"Selected version → {node}"
            detail = json.dumps({k: v for k, v in row.items() if k != "direction"}, ensure_ascii=False, indent=2)
            content_action = f' · <a href="{esc(content)}">Open exact content</a>' if content else ""
            items.append(f'<li class="relation-item" data-direction="{row["direction"]}"><p>{edge}</p>'
                         f'<p{USER}>{esc(qualification)}{": " + esc(row["reason"]) if row.get("reason") else ""}</p>'
                         f'<p>Source record <a href="{source_link}"{USER}>{esc(row["from"])}</a> · '
                         f'location {esc("journal:" + str(row["line"]) if row.get("line") else "saved manifest")}'
                         f'{content_action}</p>'
                         f'<details><summary>Recorded reference and judgment</summary><pre{USER}>{esc(detail)}</pre></details></li>')
        total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        nav = (f'<a href="{self.link(identity, 1, page-1)}">Previous relations</a> ' if page else "")
        if page + 1 < total_pages:
            nav += f'<a href="{self.link(identity, 1, page+1)}">Next relations</a>'
        begin = page * PAGE_SIZE + 1 if shown else 0
        counts = f"Relations {begin}–{page * PAGE_SIZE + len(shown)} of {len(rows)} · {len(rows)-len(shown)} outside this page"
        event = self.events.get(identity)
        payload = self.model.record_payload(identity)
        record = ""
        if event:
            state = self.model.journal.replay().state_of(identity)
            record = f'<p class="lead"{USER}>{esc(event.value.get("text"))}</p><p>Recorded kind: {esc(event.kind)} · {esc(event.recorded_at)}</p>'
            record += f'<div id="history"><h2>History and state</h2><pre{USER}>{esc(json.dumps(state, ensure_ascii=False, indent=2))}</pre></div>'
        if not event:
            record += '<div id="history"><h2>History and state</h2><p>No journal state for this selected identity. Reference qualification and exact-version links are listed below.</p></div>'
        if payload:
            record += f'<details><summary>Full stored record · {esc(payload[1])}</summary><pre{USER}>{esc(json.dumps(payload[0], ensure_ascii=False, indent=2))}</pre></details>'
        if not record:
            record = '<p>This query identity has no journal content in this view. The links and judgments below describe the observed reference scope.</p>'
        content = self.content(identity, 1)
        exact = f'<a href="{esc(content)}">Open exact content</a>' if content and not content.endswith("#record") else ""
        latest = ""
        if identity in self.visualizations:
            run, v = self.visualizations[identity]
            latest = f'<a href="{s.chart_anchor(s.chart_key(run, v), 1)}">View latest version</a>'
        elif identity in {c["key"] for c in s.charts}:
            latest = f'<a href="{s.chart_anchor(identity, 1)}">View latest version (unbound name)</a>'
        elif identity in s.view_by_path:
            latest = f'<a href="{s.data_link(identity, 1)}">View current data (not historical bytes)</a>'
        body = f'''<div class="page-head"><h1>Selected version</h1><p class="selected-id"{USER}>{esc(identity)}</p>
<p{USER}>{esc(self.label(identity))}</p><div class="workspace-actions"><a data-return href="../index.html#insights">Return to origin</a> {exact} {latest}<a href="{self.link(identity, 1)}" data-reset>Reset view</a></div></div>
<p class="state-notice" role="status" hidden></p>
<p class="relation-scope">One-hop recorded references. Evidence availability is checked now; this view does not prove the conclusion, causal links or row-level lineage. {"Records changed while read; counts and judgments may be partial." if self.model.updating else ""}</p>
<section id="related"><h2>Related items</h2><p>{counts}</p>
<details class="relation-diagram"><summary>Small directed neighborhood</summary><p>Blue border: selected version. Amber: unbound, ambiguous or unknown. Red: missing or changed evidence. Labels carry the same state.</p>
<div class="relation-map" aria-hidden="true"><svg class="relation-connectors"></svg><div class="relation-in">{''.join(sides['incoming']) or '<p>No incoming references on this page</p>'}</div>
<div class="relation-selected"><strong>Selected version</strong><div{USER}>{esc(identity)}</div></div><div class="relation-out">{''.join(sides['outgoing']) or '<p>No outgoing references on this page</p>'}</div></div></details>
<div class="filters relation-filters" role="group" aria-label="Filter relations on this page"><button class="on" data-kind="">Both directions</button><button data-kind="incoming">Incoming</button><button data-kind="outgoing">Outgoing</button></div>
<p>Direction filters apply to this page. The list is the text alternative to the diagram; every relation has the same qualification.</p>
<ol id="evidence" class="relation-list">{''.join(items) or '<li>No recorded references in this scope; absence is not proof of no relationship.</li>'}</ol><nav class="continuation" aria-label="Relation pages">{nav}</nav></section>
<section id="record"><h2>Record</h2>{record}</section>'''
        return s.page(1, f"Relations · {identity}", ' <span>/</span> Relations', body, "relations",
                      page_path="relations/" + name(identity, page), selection=identity)


def core_href(value: str) -> str:
    # Match Workspace's path quoting without a circular import.
    from urllib.parse import quote
    return quote(value, safe="/")
