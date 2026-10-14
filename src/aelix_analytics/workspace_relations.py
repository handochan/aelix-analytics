"""Static, source-qualified one-hop reference pages. Semantics belong to ReadModel."""
from __future__ import annotations

import hashlib
import json

from . import relations
from .i18n import attr, t

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
        """HTML naming an identity: the kit's words for its kind, its id, and what people wrote
        (lang="") — never one string mixing the two."""
        s = self.site
        if identity in self.events:
            event = self.events[identity]
            return (f"{s.words('kind', event.kind)} {s.esc(identity)}: "
                    f"{s.user_text(str(event.value.get('text') or '')[:100])}")
        if identity in self.visualizations:
            return (f"{t('relations.visualization')} {s.esc(identity)}: "
                    f"{s.user_text(self.visualizations[identity][1].get('title') or '')}")
        return s.user_text(identity)

    def qualification(self, row: dict) -> str:
        """A judgment as stored values (the read's enums) and the kit's words around them; the
        relation a record wrote is its own text."""
        s = self.site
        relation = s.user_text(row["relation"]) if row.get("relation") else t("relations.unknown_relation")
        basis = s.esc(row["basis"]) if row.get("basis") else t("relations.unknown_basis")
        binding = s.esc(row["version_binding"]) if row.get("version_binding") else t("relations.unknown")
        confirmed = t("relations.confirmed") if row.get("confirmed") else t("relations.not_confirmed")
        return f"{relation} · {basis} · {s.esc(row['state'])} · {t('relations.binding')} {binding} · {confirmed}"

    def render(self, identity: str, page: int = 0) -> str:
        s = self.site
        USER, esc = s.user_language, s.esc
        rows = self.model.reference_neighborhood(identity)
        shown = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        sides = {"incoming": [], "outgoing": []}
        items = []
        for i, row in enumerate(shown):
            target = relations.query_target(row)
            target = target if isinstance(target, str) else None
            other = row["from"] if row["direction"] == "incoming" else target
            label = self.label(other) if other else t("relations.unknown_target")
            qualification = self.qualification(row)
            sides[row["direction"]].append(
                f'<div class="relation-node ref-{esc(row["state"])}" data-direction="{row["direction"]}" data-edge="{i}">'
                f'<div>{label}</div><small>{qualification}</small></div>')
            source_link = self.link(row["from"], 1, anchor="#record")
            other_link = self.link(other, 1) if other else None
            node = f'<a href="{other_link}">{label}</a>' if other_link else label
            content = self.content(other, 1) if other and (row["direction"] == "incoming" or (
                row["state"] == "resolved" and row.get("version_binding") != "none")) else None
            # The text alternative reads in the edge's direction, like the diagram: source → target.
            edge = (f"{node} → {t('relations.selected')}" if row["direction"] == "incoming"
                    else f"{t('relations.selected')} → {node}")
            detail = json.dumps({k: v for k, v in row.items() if k != "direction"}, ensure_ascii=False, indent=2)
            content_action = f' · <a href="{esc(content)}">{t("relations.open_exact")}</a>' if content else ""
            location = (esc("journal:" + str(row["line"])) if row.get("line") else t("relations.saved_manifest"))
            items.append(f'<li class="relation-item" data-direction="{row["direction"]}"><p>{edge}</p>'
                         f'<p>{qualification}{esc(": " + row["reason"]) if row.get("reason") else ""}</p>'
                         f'<p>{t("relations.source_record")} <a href="{source_link}"{USER}>{esc(row["from"])}</a> · '
                         f'{t("relations.location")} {location}{content_action}</p>'
                         f'<details><summary>{t("relations.recorded")}</summary><pre{USER}>{esc(detail)}</pre></details></li>')
        total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        nav = (f'<a href="{self.link(identity, 1, page-1)}">{t("relations.previous")}</a> ' if page else "")
        if page + 1 < total_pages:
            nav += f'<a href="{self.link(identity, 1, page+1)}">{t("relations.next")}</a>'
        begin = page * PAGE_SIZE + 1 if shown else 0
        counts = t("relations.counts", begin=begin, end=page * PAGE_SIZE + len(shown), total=len(rows),
                   outside=len(rows) - len(shown))
        event = self.events.get(identity)
        payload = self.model.record_payload(identity)
        record = ""
        if event:
            state = self.model.journal.replay().state_of(identity)
            record = (f'<p class="lead"{USER}>{esc(event.value.get("text"))}</p>'
                      f'<p>{t("relations.recorded_kind")} {s.words("kind", event.kind)} · {esc(event.recorded_at)}</p>')
            record += f'<div id="history"><h2>{t("relations.history")}</h2><pre{USER}>{esc(json.dumps(state, ensure_ascii=False, indent=2))}</pre></div>'
        if not event:
            record += f'<div id="history"><h2>{t("relations.history")}</h2><p>{t("relations.no_state")}</p></div>'
        if payload:
            record += f'<details><summary>{t("relations.full_record")} · {esc(payload[1])}</summary><pre{USER}>{esc(json.dumps(payload[0], ensure_ascii=False, indent=2))}</pre></details>'
        if not record:
            record = f'<p>{t("relations.no_content")}</p>'
        content = self.content(identity, 1)
        exact = f'<a href="{esc(content)}">{t("relations.open_exact")}</a>' if content and not content.endswith("#record") else ""
        latest = ""
        if identity in self.visualizations:
            run, v = self.visualizations[identity]
            latest = f'<a href="{s.chart_anchor(s.chart_key(run, v), 1)}">{t("relations.view_latest")}</a>'
        elif identity in {c["key"] for c in s.charts}:
            latest = f'<a href="{s.chart_anchor(identity, 1)}">{t("relations.view_latest_unbound")}</a>'
        elif identity in s.view_by_path:
            latest = f'<a href="{s.data_link(identity, 1)}">{t("relations.view_current_data")}</a>'
        updating = " " + t("relations.updating") if self.model.updating else ""
        body = f'''<div class="page-head"><h1>{t("relations.selected")}</h1><p class="selected-id"{USER}>{esc(identity)}</p>
<p>{self.label(identity)}</p><div class="workspace-actions"><a data-return href="../index.html#insights">{t("action.return")}</a> {exact} {latest}<a href="{self.link(identity, 1)}" data-reset>{t("action.reset")}</a></div></div>
<p class="state-notice" role="status" hidden></p>
<p class="relation-scope">{t("relations.scope")}{updating}</p>
<section id="related"><h2>{t("action.related")}</h2><p>{counts}</p>
<details class="relation-diagram"><summary>{t("relations.diagram")}</summary><p>{t("relations.legend")}</p>
<div class="relation-map" aria-hidden="true"><svg class="relation-connectors"></svg><div class="relation-in">{''.join(sides['incoming']) or f'<p>{t("relations.no_incoming")}</p>'}</div>
<div class="relation-selected"><strong>{t("relations.selected")}</strong><div{USER}>{esc(identity)}</div></div><div class="relation-out">{''.join(sides['outgoing']) or f'<p>{t("relations.no_outgoing")}</p>'}</div></div></details>
<div class="filters relation-filters" role="group"{attr("aria-label", "relations.filter")}><button class="on" data-kind="">{t("relations.both")}</button><button data-kind="incoming">{t("relations.incoming")}</button><button data-kind="outgoing">{t("relations.outgoing")}</button></div>
<p>{t("relations.list_note")}</p>
<ol id="evidence" class="relation-list">{''.join(items) or f'<li>{t("relations.empty")}</li>'}</ol><nav class="continuation"{attr("aria-label", "relations.pages")}>{nav}</nav></section>
<section id="record"><h2>{t("relations.record")}</h2>{record}</section>'''
        return s.page(1, identity, f' <span>/</span> {t("page.relations")}', body, "relations",
                      page_path="relations/" + name(identity, page), selection=identity, title_key="page.relations")


def core_href(value: str) -> str:
    # Match Workspace's path quoting without a circular import.
    from urllib.parse import quote
    return quote(value, safe="/")
