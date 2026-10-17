"""Static, source-qualified one-hop reference pages. Semantics belong to ReadModel.

A page reads as people read a record (design WORKSPACE-READABILITY 8.1, mockups 2-B and 3-B): the
record's own text as its title, a note when it was corrected or withdrawn, then what it cites and
what cites it, each relation with its judgment in words and its stored values under Details, and
the record's stored state and fields at the end."""
from __future__ import annotations

import hashlib
import json

from . import relations
from .i18n import attr, t

PAGE_SIZE = 18
# A record text this long or shorter is the page's heading; a longer one follows a heading that
# names the record.
TITLE_LIMIT = 200


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
                identity in s.recent_sources) else self.link(identity, depth, anchor="#record")
        if identity.startswith("run:") and identity[4:] in s.run_by_id:
            return s.run_link(identity[4:], depth)
        if identity in self.visualizations:
            run, v = self.visualizations[identity]
            return s.run_link(run["id"], depth, "#chart-" + core_href(str(v["id"])))
        if identity in self.context:
            return f"{s.root(depth)}context.html#v-{core_href(self.context[identity].stem)}"
        return None

    def what(self, identity: str) -> str:
        """The kit's words for what an identity is: a record's kind, a run, a visualization, data, a
        CONTEXT.md version, or a name the records hold."""
        s = self.site
        if identity in self.events:
            return s.words("kind", self.events[identity].kind)
        if identity.startswith("run:"):
            return t("relations.what.run")
        if identity in self.visualizations:
            return t("relations.visualization")
        if identity in self.context:
            return t("relations.what.context")
        if identity in s.view_by_path:
            return t("relations.what.data")
        return t("relations.what.name")

    def title(self, identity: str) -> str:
        """HTML of what people wrote that names an identity (lang=""): a record's text, a run's or a
        visualization's title, a dataset's name; else the identity itself."""
        s = self.site
        if identity in self.events:
            return s.user_text(str(self.events[identity].value.get("text") or ""))
        if identity.startswith("run:") and identity[4:] in s.run_by_id:
            return s.user_text(s.run_by_id[identity[4:]].get("title") or identity[4:])
        if identity in self.visualizations:
            return s.user_text(self.visualizations[identity][1].get("title") or identity)
        if identity in s.view_by_path:
            return s.user_text(s.dataset_name(identity) or identity)
        return s.user_text(identity)

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

    def judgment(self, row: dict) -> str:
        """A relation's judgment as a badge: a mark and words (never colour alone); a found target
        says what was found (`Record found`, `Run found`, `Exact version`)."""
        s = self.site
        state = row["state"]
        tone, mark = s.REF_STATE.get(state, ("muted", "?"))
        if state == "resolved":
            found = ("record" if row.get("type") in relations.EVENT_TARGETS
                     else "run" if row.get("type") == "run" else "")
            words = t("ref.resolved." + found) if found else t("ref.resolved")
        else:
            words = s.ref_words(state, row.get("reason"))
        return s.badge(tone, mark, words)

    def details(self, row: dict, location: str) -> str:
        """A relation's Details (mockup 2-B): how the link is known, its version and whether it is a
        confirmed link, in words, and where it is recorded; inside, the judgment as the read returns
        it, its stored values as written (ADR-0006 3 and 4)."""
        s = self.site
        basis, binding = row.get("basis"), row.get("version_binding")
        said = [s.words("relations.basis", basis) if basis is not None else t("relations.unknown_basis"),
                s.words("relations.binding", binding) if binding is not None else t("relations.unknown"),
                t("relations.confirmed") if row.get("confirmed") else t("relations.not_confirmed"), location]
        detail = json.dumps({k: v for k, v in row.items() if k != "direction"}, ensure_ascii=False, indent=2)
        return (f'<details class="relation-details"><summary>{t("relations.details")}: {" · ".join(said)}</summary>'
                f'<pre{s.user_language}>{s.esc(detail)}</pre></details>')

    def card(self, row: dict, other: str | None) -> str:
        """One relation (design 8.1): what the other side is and how it relates, its name, its
        judgment, where to open it, and its stored values under Details."""
        s = self.site
        esc = s.esc
        relation = s.words("relation", row["relation"]) if row.get("relation") else t("relations.unknown_relation")
        what = self.what(other) if other else t("relations.unknown_target")
        content = self.content(other, 1) if other and (row["direction"] == "incoming" or (
            row["state"] == "resolved" and row.get("version_binding") != "none")) else None
        actions = []
        if content:
            kind = ("record" if other in self.events else "run" if other.startswith("run:")
                    else "chart" if other in self.visualizations else "context" if other in self.context else "")
            actions.append(f'<a class="primary" href="{esc(content)}">'
                           f'{t("relations.open." + kind) if kind else t("relations.open_exact")}</a>')
        if other:
            actions.append(f'<a href="{self.link(other, 1)}">{t("relations.its_relations")}</a>')
        location = (esc("journal:" + str(row["line"])) if row.get("line") else t("relations.saved_manifest"))
        # A record or a run is named by its id beside its kind (a run's without its `run:` prefix).
        named = (" " + esc(other[4:] if other.startswith("run:") else other)
                 if other and (other in self.events or other.startswith("run:")) else "")
        return (f'<li class="relation-item" data-direction="{row["direction"]}">'
                f'<div class="relation-head"><div class="relation-what"><p class="kicker">{what}{named} · {relation}</p>'
                f'<p class="relation-name">{self.title(other) if other else t("relations.unknown_target")}</p>'
                + (f'<p class="relation-id">{s.user_text(other)}</p>' if other and not named else "")
                + f'</div>{self.judgment(row)}</div>'
                f'<div class="relation-actions">{" ".join(actions)}</div>{self.details(row, location)}</li>')

    def note(self, identity: str) -> str:
        """What a reader of a record needs first when it is no longer as it was made (ADR-0006 6):
        corrected, corrected by several that disagree, or withdrawn. The record stays as written."""
        s = self.site
        state = self.model.journal.replay().state_of(identity) or {}
        shown, by = state.get("state"), state.get("corrected_by") or []
        if shown == "superseded" and by:
            other = self.events.get(by[0])
            link = self.content(by[0], 1) or self.link(by[0], 1)
            what = (s.words("kind", other.kind) + " ") if other else ""
            text = f' “{s.user_text(str(other.value.get("text") or "")[:140])}”' if other else ""
            body = (f'<strong>{t("relations.note.superseded")}</strong> {t("relations.note.corrected_by")} '
                    f'{what}{s.esc(by[0])}{text}. {t("relations.note.kept")}'
                    f'<br><a href="{s.esc(link)}">{t("relations.note.open_correction")} →</a>')
        elif shown == "conflict":
            body = (f'<strong>{t("relations.note.conflict")}</strong> {t("relations.note.corrected_by")} '
                    + ", ".join(f'<a href="{s.esc(self.link(e, 1))}">{s.esc(e)}</a>' for e in by)
                    + f'. {t("relations.note.kept")}')
        elif shown == "withdrawn":
            body = f'<strong>{t("relations.note.withdrawn")}</strong> {t("relations.note.kept")}'
        else:
            return ""
        return f'<div class="callout tone-muted record-note" role="note">{body}</div>'

    def render(self, identity: str, page: int = 0) -> str:
        s = self.site
        USER, esc = s.user_language, s.esc
        rows = self.model.reference_neighborhood(identity)
        shown = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        sides = {"incoming": [], "outgoing": []}
        cards = {"incoming": [], "outgoing": []}
        for i, row in enumerate(shown):
            target = relations.query_target(row)
            target = target if isinstance(target, str) else None
            other = row["from"] if row["direction"] == "incoming" else target
            label = self.label(other) if other else t("relations.unknown_target")
            sides[row["direction"]].append(
                f'<div class="relation-node ref-{esc(row["state"])}" data-direction="{row["direction"]}" data-edge="{i}">'
                f'<div>{label}</div><small>{self.judgment(row)}</small></div>')
            cards[row["direction"]].append(self.card(row, other))
        total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        nav = (f'<a href="{self.link(identity, 1, page-1)}">{t("relations.previous")}</a> ' if page else "")
        if page + 1 < total_pages:
            nav += f'<a href="{self.link(identity, 1, page+1)}">{t("relations.next")}</a>'
        begin = page * PAGE_SIZE + 1 if shown else 0
        counts = t("relations.counts", begin=begin, end=page * PAGE_SIZE + len(shown), total=len(rows),
                   outside=len(rows) - len(shown))
        # The record: its own text as the heading (a long one under a heading naming it), then its id,
        # time, author and place in the journal.
        event = self.events.get(identity)
        what = self.what(identity)
        if event:
            text = str(event.value.get("text") or "")
            heading = (f"<h1{USER}>{esc(text)}</h1>" if len(text) <= TITLE_LIMIT else
                       f'<h1>{what} {esc(identity)}</h1><p class="record-text"{USER}>{esc(text)}</p>')
            meta = [esc(identity), s.fmt_time(event.recorded_at), s.words("actor", event.value.get("actor")),
                    t("relations.journal_line", n=event.line)]
            badges = s.kind_chip(event.kind) + s.record_state_html(event.value, 1)
        else:
            heading = f"<h1>{self.title(identity)}</h1>"
            meta = [f"<span{USER}>{esc(identity)}</span>"]
            badges = f'<span class="badge tone-muted">{what}</span>'
        head = (f'<header class="page-head record-head" id="record"><div class="badges">{badges}</div>{heading}'
                f'<p class="sub">{" · ".join(meta)}</p></header>')
        # Where the selected content itself is: the exact version, or the latest (said so).
        content = self.content(identity, 1)
        actions = []
        if content and not content.endswith("#record"):
            actions.append(f'<a href="{esc(content)}">{t("relations.open_exact")}</a>')
        if identity in self.visualizations:
            run, v = self.visualizations[identity]
            actions.append(f'<a href="{s.chart_anchor(s.chart_key(run, v), 1)}">{t("relations.view_latest")}</a>')
        elif identity in {c["key"] for c in s.charts}:
            actions.append(f'<a href="{s.chart_anchor(identity, 1)}">{t("relations.view_latest_unbound")}</a>')
        elif identity in s.view_by_path:
            actions.append(f'<a href="{s.data_link(identity, 1)}">{t("relations.view_current_data")}</a>')
        actions.append(f'<a href="{self.link(identity, 1)}" data-reset>{t("action.reset")}</a>')
        updating = " " + t("relations.updating") if self.model.updating else ""
        n_out, n_in = len(cards["outgoing"]), len(cards["incoming"])
        groups = "".join(
            f'<section class="relation-group" data-direction="{direction}"><h3>{t(key, n=len(cards[direction]))}</h3>'
            f'<ol class="relation-list">{"".join(cards[direction])}</ol></section>'
            for direction, key in (("outgoing", "relations.group.cites"), ("incoming", "relations.group.cited_by"))
            if cards[direction])
        no_in = "<p>" + t("relations.no_incoming") + "</p>"
        no_out = "<p>" + t("relations.no_outgoing") + "</p>"
        diagram = (f'<details class="relation-diagram"><summary>{t("relations.diagram")}</summary><p>{t("relations.legend")}</p>'
                   f'<div class="relation-map" aria-hidden="true"><svg class="relation-connectors"></svg>'
                   f'<div class="relation-in"><p class="side">{t("relations.side.in")}</p>{"".join(sides["incoming"]) or no_in}</div>'
                   f'<div class="relation-selected"><strong>{t("relations.selected")}</strong><div{USER}>{esc(identity)}</div></div>'
                   f'<div class="relation-out"><p class="side">{t("relations.side.out")}</p>{"".join(sides["outgoing"]) or no_out}</div></div></details>')
        filters = (f'<div class="filters relation-filters" role="group"{attr("aria-label", "relations.filter")}>'
                   f'<button class="on" data-kind="">{t("relations.all", n=len(shown))}</button>'
                   f'<button data-kind="outgoing">{t("relations.cites", n=n_out)}</button>'
                   f'<button data-kind="incoming">{t("relations.cited_by", n=n_in)}</button></div>') if shown else ""
        state = self.model.journal.replay().state_of(identity) if event else None
        payload = self.model.record_payload(identity)
        technical = ""
        if event:
            # The kind as the record holds it (`gap` and `workflow_feedback` stay apart).
            technical += (f'<p>{t("relations.recorded_kind")} <code>{esc(event.kind)}</code> · '
                          f'<code>{esc(event.recorded_at)}</code></p>'
                          f'<div id="history"><h3>{t("relations.history")}</h3>'
                          f'<pre{USER}>{esc(json.dumps(state, ensure_ascii=False, indent=2))}</pre></div>')
        else:
            technical += f'<div id="history"><h3>{t("relations.history")}</h3><p>{t("relations.no_state")}</p></div>'
        if payload:
            technical += (f'<h3>{t("relations.full_record")} · {esc(payload[1])}</h3>'
                          f'<pre{USER}>{esc(json.dumps(payload[0], ensure_ascii=False, indent=2))}</pre>')
        elif not event:
            technical += f'<p>{t("relations.no_content")}</p>'
        empty = f'<p>{t("relations.empty")}</p>'
        body = f'''{head}{self.note(identity)}
<p class="relation-scope">{t("relations.scope")}{updating}</p>
<section id="related"><h2>{t("relations.heading")}</h2><p>{counts}</p>
{filters}
<div id="evidence" class="relation-groups">{groups or empty}</div>
<nav class="continuation"{attr("aria-label", "relations.pages")}>{nav}</nav>
{diagram}</section>
<section id="technical"><details class="technical"><summary>{t("relations.technical")}</summary>{technical}</details></section>'''
        return s.page(1, identity, f' <span>/</span> {t("page.relations")}', body, "relations",
                      page_path="relations/" + name(identity, page), selection=identity, title_key="page.relations",
                      actions=" ".join(actions))


def core_href(value: str) -> str:
    # Match Workspace's path quoting without a circular import.
    from urllib.parse import quote
    return quote(value, safe="/")
