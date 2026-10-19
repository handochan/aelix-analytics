"""Provenance trails on a relation page (design WORKSPACE-READABILITY 6, #100).

A trail is the read model's (ReadModel.provenance): which records, runs, Visualization versions and
data a thing was made from, or what was made from it, with the judgments they come from. This module
lays it out and says it in the kit's words; it makes no judgment of its own.

One ordered list is the trail's words and its boxes (mockups 7-B and 7-C). Each item is a box: its
kind in words, its name, its state as a mark and words, and a line saying how it joins the item next
to it toward the start. Without a script, or on a narrow screen, the list is the vertical path. With
one, a wide screen places the same boxes on the grid the generator gives them — the start at the
bottom of what it comes from and at the top of what uses it, side branches left and right — and
draws the lines between them from a short list of edges. The lines are a picture (aria-hidden); the
words in the boxes say every one of them.

The page helpers are the Site's (as workspace_map takes them), so it imports no other part of the
Workspace."""
from __future__ import annotations

from pathlib import PurePosixPath

from .i18n import t

# A data file's state now against the bytes the trail met (design 4): its tone and mark, and its words
# (`provenance.state.<state>`).
DATA_STATE = {"same": ("good", "✓"), "changed": ("warning", "⚠"), "missing": ("critical", "⊗"), "unknown": ("muted", "?")}
# A run's status, as the Workspace marks it everywhere (workspace.STATUS).
STATUS = {"ok": ("good", "✓"), "error": ("critical", "✕"), "timeout": ("serious", "⏱"),
          "cancelled": ("warning", "■"), "incomplete": ("muted", "…"), "running": ("muted", "…")}
# An edge in the list of lines the page draws: [from, to, kind, flags], `from` the box the arrow
# leaves (what came first) and `to` the box it reaches. Kinds: c (a record cites: dashed), r (a run
# read), w (a run wrote), m (a run made); flags: i (inferred from an older kit's path), y (a cycle),
# a (one of several possible sources).
EDGE_KIND = {"cites": "c", "read": "r", "wrote": "w", "made": "m"}
# The record kinds whose colour token a box takes (design 4); others take the muted one.
RECORD_TOKENS = frozenset({"finding", "decision", "assumption", "question", "todo", "gap", "note",
                           "context", "project", "resolve", "run", "import"})


class Provenance:
    """The trails of the relation pages, read once per identity and direction."""

    def __init__(self, site):
        self.site = site
        self.model = site.model
        self._trails: dict[tuple, dict | None] = {}
        self._described: dict[tuple, tuple[str, str, str]] = {}

    def trail(self, identity: str, direction: str) -> dict | None:
        key = (identity, direction)
        if key not in self._trails:
            self._trails[key] = self.model.provenance(identity, direction, input_state=self.site.fresh.input_state)
        return self._trails[key]

    def directions(self, identity: str) -> list[str]:
        """The directions with a trail to show (design 6.1): what a record comes from; what a run, a
        Visualization version or a data file comes from and what uses it. A trail of its start alone
        shows nothing."""
        first = self.trail(identity, "sources")
        if first is None:
            return []
        shown = ["sources"] if len(first["nodes"]) > 1 else []
        if first["nodes"][0]["kind"] in ("run", "visualization", "data"):
            uses = self.trail(identity, "uses")
            if uses is not None and len(uses["nodes"]) > 1:
                shown.append("uses")
        return shown

    # --- the tab ----------------------------------------------------------------------------------

    def tab_html(self, identity: str) -> str:
        """The Provenance tab of a relation page, or '' when there is no trail to show."""
        directions = self.directions(identity)
        if not directions:
            return ""
        switch = ""
        if len(directions) > 1:
            switch = ('<p class="pv-dir">' + " ".join(
                f'<a href="#trail-{d}" data-dir="{d}">{t("provenance.direction." + d)}</a>' for d in directions) + "</p>")
        trails = "".join(self.trail_html(self.trail(identity, d)) for d in directions)
        return (f'<section id="provenance" class="tab"><h2>{t("tab.provenance")}</h2>{switch}'
                f'<div class="pv-layout"><div class="pv-trails">{trails}</div>'
                f'{self.checks_html(self.trail(identity, directions[0]))}</div></section>')

    def trail_html(self, trail: dict) -> str:
        direction = trail["direction"]
        prefix = "pv-" + direction[0]
        nodes = trail["nodes"]
        index = {node["id"]: n for n, node in enumerate(nodes)}
        parent, extra = tree(trail)
        place, columns, rows = layout(trail, parent)
        lines = []
        for edge in trail["edges"]:
            a, b = index[edge["from"]], index[edge["to"]]
            # The arrow goes the way the data went: from what was read, from what a run wrote or made,
            # from what a record cites to the record.
            first, then = (b, a) if edge["kind"] in ("read", "cites") else (a, b)
            flags = ("i" if edge.get("basis") == "inferred" or edge.get("read_as") else "") + (
                "y" if edge.get("cycle") else "") + ("a" if edge.get("producer") == "ambiguous" else "")
            lines.append(f"{first}>{then}{EDGE_KIND[edge['kind']]}{flags}")
        order = sorted(range(len(nodes)), key=lambda n: (place[n][0] if direction == "uses" else -place[n][0], place[n][1]))
        items = "".join(self.node_html(trail, n, parent, extra, place, rows, prefix) for n in order)
        total, omitted = trail["total"], trail["omitted"]
        counts = ""
        if omitted["nodes"] or omitted["edges"]:
            counts = (f'<p class="pv-note">{t("provenance.omitted", nodes=len(nodes), total=total["nodes"], edges=len(trail["edges"]), all=total["edges"])}'
                      f' {t("provenance.omitted_more")}</p>')
        if trail["cycles"]:
            counts += f'<p class="pv-note">{t("provenance.cycle_note")}</p>'
        if any(edge["kind"] == "wrote" and not edge.get("recorded") for edge in trail["edges"]):
            counts += f'<p class="pv-note">{t("provenance.wrote_note")}</p>'
        summary = t("provenance.summary", runs=sum(1 for node in nodes if node["kind"] == "run"),
                    files=sum(1 for node in nodes if node["kind"] == "data"))
        return (f'<div class="pv-trail" id="trail-{direction}"><h3>{t("provenance.direction." + direction)} '
                f'<small>{summary}</small></h3>{counts}'
                f'<div class="pv-scroll"><ol class="pv-list" data-e="{" ".join(lines)}" style="--cols:{columns}">'
                f'{items}</ol></div></div>')

    # --- a box ------------------------------------------------------------------------------------

    def node_html(self, trail: dict, n: int, parent: dict, extra: dict, place: dict, rows: int, prefix: str) -> str:
        s = self.site
        node = trail["nodes"][n]
        row, column = place[n]
        grid_row = row + 1 if trail["direction"] == "uses" else rows - row
        # A box says the same of a node on every page it is on: made once (its join line and place differ).
        key = (node["id"], n == 0, node.get("state"), node.get("producer"), tuple(node.get("current_writers") or ()),
               node.get("read_as"))
        if key not in self._described:
            self._described[key] = self.box(node, n == 0)
        classes, body = self._described[key]
        joins = [self.join_html(trail, n, other, edge, prefix) for other, edge in ([parent[n]] if n in parent else []) + extra.get(n, [])]
        more = ""
        if node.get("continues") and n:
            page = self.page(node)
            if page:
                more = f' <a class="pv-more" href="{s.esc(page)}#provenance">{t("provenance.continue")}</a>'
        joined = "".join(f'<p class="pj">{j}</p>' for j in joins if j)
        return f'<li class="{classes}" id="{prefix}-{n}" style="grid-area:{grid_row}/{column + 1}">{body}{joined}{more}</li>'

    def box(self, node: dict, start: bool) -> tuple[str, str]:
        """(classes, HTML) of a box but its id, place and join lines: kind, start, record kind, data state."""
        kind = node["kind"]
        classes = ["pv", "pv-" + {"record": "rec", "visualization": "viz"}.get(kind, kind)]
        if start:
            classes.append("is-start")
        if kind == "record" and node.get("record_kind") in RECORD_TOKENS:
            classes.append("kind-" + node["record_kind"])
        if kind == "data":
            classes.append("st-" + str(node.get("state")))
        what, name, facts = self.describe(node, start)
        return " ".join(classes), f'<p class="pk">{what}</p><p class="pn">{name}</p>{facts}'

    def describe(self, node: dict, start: bool) -> tuple[str, str, str]:
        """(kind words, name HTML, facts HTML) of one box."""
        s = self.site
        kind = node["kind"]
        if kind == "record":
            what = s.words("kind", node.get("record_kind"))
            if start:
                what += " · " + t("provenance.this_record")
            text = s.user_text(node.get("summary")[:100] + ("…" if node.get("text_chars", 0) > 100 else ""))
            name = text if start else f'<a href="{s.esc(self.page(node))}">{s.esc(node["id"])}</a> {text}'
            state = node.get("state")
            facts = ""
            if state in s.RECORD_STATE:
                # As the record's own page says it (Site.record_state_html), in a box's few bytes.
                words = (t("versions.corrected") if state == "superseded" else
                         t("record.conflict", n=node.get("corrections") or 2) if state == "conflict" else t("record." + state))
                facts = f'<p class="pf">{marked(*s.RECORD_STATE[state], words)}</p>'
            return what, name, facts
        if kind == "run":
            run = node["run"]
            title = node.get("title") or run
            name = f'<a href="{s.run_link(run, 1)}"{s.user_language}>{s.esc(title)}</a>'
            script = PurePosixPath(node["script"]).name if node.get("script") else "?"
            facts = (f'<p class="pf"><code{s.user_language}>{s.esc(script)} @{s.esc(s.short(node.get("code")))}</code></p>'
                     f'<p class="pf">{marked(*STATUS.get(node.get("status") or "", ("muted", "?")), s.words("status", node.get("status")))} '
                     f'{s.fmt_time(node.get("started_at"))}'
                     + (f' · {t("provenance.rerun_of")} <code{s.user_language}>{s.esc(node["rerun_of"])}</code>' if node.get("rerun_of") else "")
                     + "</p>")
            return t("relations.what.run"), name, facts
        if kind == "visualization":
            title = node.get("title") or node["visualization"]
            name = f'<a href="{s.run_link(node["run"], 1, "#chart-" + s.href(str(node["visualization"])))}"{s.user_language}>{s.esc(title)}</a>'
            facts = (f'<p class="pf">{t("versions.n_of", n=node["version"], total=node["versions"])}</p>'
                     if node.get("version") and node["versions"] > 1 else
                     "" if node.get("version") else f'<p class="pf">{t("versions.makes_no_version")}</p>')
            return t("relations.visualization"), name, facts
        if kind == "data":
            path = node["path"]
            label = s.dataset_name(path) or PurePosixPath(path).name
            link = s.data_link(path, 1) or s.relations.link(path, 1)
            name = f'<a href="{s.esc(link)}"{s.user_language}>{s.esc(label)}</a>'
            state = node.get("state") or "unknown"
            mark = marked(*DATA_STATE.get(state, ("muted", "?")), t("provenance.state." + state))
            sha = s.short(node.get("sha256"))
            bits = [mark, f'<code{s.user_language}>{s.esc(sha)}</code>' if sha else ""]
            if node.get("version") and node["versions"] > 1:
                bits.append(t("versions.n_of", n=node["version"], total=node["versions"]))
            facts = f'<p class="pf">{" ".join(b for b in bits if b)}</p>'
            if node.get("producer") == "ambiguous":
                facts += f'<p class="pf">{marked("warning", "⚠", t("badge.producer_ambiguous"))}</p>'
            if node.get("read_as"):
                facts += f'<p class="pf ref-note">{t("inferred.note")}</p>'
            writers = node.get("current_writers")
            if writers is not None:
                facts += f'<p class="pf">{t("provenance.now_bytes_of")} ' + (
                    ", ".join(self.run_name(r) for r in writers[:3]) if writers else t("provenance.no_matching_output")) + "</p>"
            return t("provenance.kind.imported" if node.get("imported") else "provenance.kind.derived"), name, facts
        if kind == "context":
            link = f'{s.root(1)}context.html#v-{s.href(PurePosixPath(node["version"]).stem)}'
            return t("relations.what.context"), f'<a href="{s.esc(link)}"{s.user_language}>{s.esc(node["version"])}</a>', ""
        if kind == "code":
            return t("relations.what.code"), s.user_text(node.get("path")), self.ref_state(node)
        if kind == "inputs":
            states = ", ".join(f'{t("provenance.state." + k)} {s.fmt_num(v)}' for k, v in node["states"].items()
                               if f"provenance.state.{k}" in DATA_KEYS)
            return t("provenance.kind.inputs"), t("provenance.inputs", n=node["count"]), f'<p class="pf">{states}</p>'
        return t("relations.what.name"), s.user_text(node.get("label") or "?"), self.ref_state(node)

    def ref_state(self, node: dict) -> str:
        s = self.site
        state = node.get("state")
        return f'<p class="pf">{marked(*s.REF_STATE.get(state, ("muted", "?")), s.ref_words(state, node.get("reason")))}</p>'

    def join_html(self, trail: dict, n: int, other: int, edge: dict, prefix: str) -> str:
        """How box `n` joins box `other` (toward the start), in its own words: `Read by`, `Wrote`,
        `Made by`, `Cited by`, `Cites`, … and the other box's name, linked to it; with the judgment the
        edge comes from where it qualifies it."""
        s = self.site
        node, near = trail["nodes"][n], trail["nodes"][other]
        mine = edge["from"] == node["id"]
        kind = edge["kind"]
        words = {("read", True): "read", ("read", False): "read_by", ("wrote", True): "wrote",
                 ("wrote", False): "written_by", ("made", True): "made", ("made", False): "made_by",
                 ("cites", True): "cites", ("cites", False): "cited_by"}[(kind, mine)]
        label = self.short_name(near)
        out = f'{t("provenance.join." + words)} <a href="#{prefix}-{other}"{s.user_language}>{s.esc(label)}</a>'
        if kind == "cites":
            # A found target says what was found, as on the relation cards: a run, a record, an exact version.
            state = edge.get("state")
            cited = (near if mine else node)["kind"]
            judged = (t("ref.resolved." + cited) if state == "resolved" and cited in ("run", "record")
                      else s.ref_words(state, edge.get("reason")))
            out += " " + marked(*s.REF_STATE.get(state, ("muted", "?")), judged)
            if edge.get("confirmed"):
                out += f' <span class="muted">{t("relations.confirmed")}</span>'
        if kind == "read" and edge.get("how") and edge["how"][0] != "read":
            out += f' <span class="muted">{t("provenance.how." + edge["how"][0])}</span>'
        if edge.get("basis") == "inferred" or edge.get("read_as"):
            out += f' <span class="ref-note">{t("inferred.note")}</span>'
        if edge.get("cycle"):
            out += f' <span class="muted">↻ {t("provenance.cycle")}</span>'
        return out

    def short_name(self, node: dict) -> str:
        kind = node["kind"]
        if kind == "record":
            return node["id"]
        if kind == "run":
            return node.get("title") or node["run"]
        if kind == "visualization":
            return node.get("title") or str(node["visualization"])
        if kind == "data":
            return self.site.dataset_name(node["path"]) or PurePosixPath(node["path"]).name
        if kind == "context":
            return node["version"]
        if kind == "inputs":
            return node["id"]
        return str(node.get("label") or node.get("path") or "?")

    def run_name(self, run_id: str) -> str:
        s = self.site
        run = s.run_by_id.get(run_id) or {}
        return f'<a href="{s.run_link(run_id, 1)}"{s.user_language}>{s.esc(run.get("title") or run_id)}</a>'

    def page(self, node: dict) -> str | None:
        """The relation page a box's trail goes on from (its selected version page, design 6.3)."""
        s = self.site
        kind = node["kind"]
        identity = (node["id"] if kind in ("record", "run", "visualization", "context")
                    else node["path"] if kind == "data" else None)
        return s.relations.link(identity, 1) if identity in s.relations.identities else None

    # --- what can be checked (design 6.2) ----------------------------------------------------------

    def checks_html(self, trail: dict) -> str:
        """Each thing that can be checked on its own line, never one score: the start's citations,
        the runs, their code, the imported and the derived data now, rerun comparisons, analysis
        checks and conclusion reviews (none recorded in this record format)."""
        s = self.site
        checks = trail["checks"]
        lines = []

        def line(tone: str, head: str, body: str = "", items: list[str] | None = None) -> None:
            # What a line names (runs, files) is a list of its own: each a line, its links full-size targets.
            listed = "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>" if items else ""
            lines.append(f'<li class="ck-{tone}"><strong>{head}</strong>{(" " + body) if body else ""}{listed}</li>')

        cited = checks["citations"]
        if cited is None:
            line("none", t("provenance.check.no_citation"))
        else:
            others = [f'{s.ref_words(state)} {s.fmt_num(n)}' for state, n in cited["states"].items() if state != "resolved"]
            line("good" if cited["confirmed"] == cited["total"] else "warn",
                 t("provenance.check.cited", confirmed=cited["confirmed"], n=cited["total"]), " · ".join(others))
        runs = checks["runs"]
        if runs["total"]:
            failed = [f'{self.run_name(r["run"])} {s.words("status", r["status"])}' for r in runs["not_succeeded"][:3]]
            line("good" if not runs["not_succeeded"] else "warn",
                 t("provenance.check.runs", ok=runs["succeeded"], n=runs["total"]), items=failed)
        else:
            line("none", t("provenance.check.no_runs"))
        code = checks["code"]
        if code:
            kept = len(code.get("resolved", []))
            rest = [f'{t("provenance.code." + state)} {s.fmt_num(len(rows))}' for state, rows in code.items() if state != "resolved"]
            line("good" if not rest else "warn", t("provenance.check.code", kept=kept, n=sum(len(r) for r in code.values())),
                 " · ".join(rest))
        for family in ("imported", "derived"):
            files = checks[family]
            if not files["total"]:
                line("none", t(f"provenance.check.no_{family}"))
                continue
            not_same = files["not_same"]
            body = []
            for row in not_same[:4]:
                label = s.dataset_name(row["path"]) or PurePosixPath(row["path"]).name
                said = f'<span{s.user_language}>{s.esc(label)}</span> {t("provenance.state." + row["state"])}'
                if "current_writers" in row:
                    said += " · " + t("provenance.now_bytes_of") + " " + (
                        ", ".join(self.run_name(r) for r in row["current_writers"][:2]) if row["current_writers"]
                        else t("provenance.no_matching_output"))
                body.append(said)
            line("good" if not not_same else "warn",
                 t(f"provenance.check.{family}", same=files["total"] - len(not_same), n=files["total"]), items=body)
        reruns = checks["reruns"]
        if reruns:
            verdicts: dict[str, int] = {}
            for rerun in reruns:
                verdicts[str(rerun["verdict"])] = verdicts.get(str(rerun["verdict"]), 0) + 1
            line("good" if set(verdicts) == {"match"} else "warn", t("provenance.check.reruns", n=len(reruns)),
                 " · ".join(f'{s.words("verdict", v)} {s.fmt_num(k)}' for v, k in verdicts.items()))
        else:
            line("none", t("provenance.check.no_reruns"))
        line("none", t("provenance.check.reviews"))
        return (f'<aside class="pv-checks"><h3>{t("provenance.check.title")}</h3><ul>{"".join(lines)}</ul>'
                f'<p class="muted">{t("provenance.scope")}</p></aside>')


# The data states the catalog names (provenance.state.<state>).
DATA_KEYS = frozenset(f"provenance.state.{state}" for state in DATA_STATE)


def marked(tone: str, mark: str, words: str) -> str:
    """A state in a box: its mark (drawn by the style sheet from `data-m`, read by no screen reader) and
    its words, never colour alone (design 4). The badge of the other pages, in fewer bytes: a trail
    repeats it on every box."""
    return f'<b class="st tone-{tone}" data-m="{mark}">{words}</b>'


def tree(trail: dict) -> tuple[dict, dict]:
    """The trail as a tree from its start: each box's parent and the edge to it (`parent[n]`: (box,
    edge), the box before it that it was reached from), and the other edges, each kept by its later box
    (`extra[n]`: [(box, edge)]). The nodes are in the order the read reached them, so the first edge
    that joins a box to one before it is the one it was reached by."""
    index = {node["id"]: n for n, node in enumerate(trail["nodes"])}
    parent: dict[int, tuple[int, dict]] = {}
    extra: dict[int, list] = {}
    for edge in trail["edges"]:
        a, b = index[edge["from"]], index[edge["to"]]
        later, earlier = max(a, b), min(a, b)
        if later not in parent and later != 0:
            parent[later] = (earlier, edge)
        else:
            extra.setdefault(later, []).append((earlier, edge))
    return parent, extra


def layout(trail: dict, parent: dict) -> tuple[dict[int, tuple[int, int]], int, int]:
    """Grid places of the boxes (design 6.4): {box: (row, column)}, columns, rows; row 0 is the start's,
    and rows go away from it (the page puts it at the bottom of what it comes from, at the top of what
    uses it). A box that goes on — a run, or anything the trail goes on from — stands above (below) the
    box it joins; one that ends the trail stands beside it: what a run read on the left and what it made
    on the right (what cites it on the left, what it wrote on the right, toward uses). A run with nothing
    going on puts its first ending input (output) in line, as the end of the path. Boxes that go on from
    one box stand side by side, each with the width its own boxes need."""
    nodes = trail["nodes"]
    count = len(nodes)
    uses = trail["direction"] == "uses"
    children: list[list[int]] = [[] for _ in nodes]
    for n in range(1, count):
        if n in parent:
            children[parent[n][0]].append(n)
    chain: list[list[int]] = [[] for _ in nodes]
    lefts: list[list[int]] = [[] for _ in nodes]
    rights: list[list[int]] = [[] for _ in nodes]
    for n in range(count):
        ends = []
        for c in children[n]:
            (chain[n] if nodes[c]["kind"] == "run" or children[c] else ends).append(c)
        if not chain[n] and nodes[n]["kind"] == "run":
            inline = next((c for c in ends if nodes[c]["kind"] in ("data", "inputs")), None)
            if inline is not None:
                chain[n].append(inline)
                ends.remove(inline)
        for c in ends:
            kind = nodes[c]["kind"]
            # Toward uses, what cites stands on the left; else what a run read is on its left, the rest on the right.
            left = kind == "record" if uses else nodes[n]["kind"] == "run" and kind in ("data", "inputs")
            (lefts[n] if left else rights[n]).append(c)
    # A box's parent comes before it in the trail's order, so widths add up from the last box back.
    width = [3] * count
    for n in range(count - 1, -1, -1):
        if chain[n]:
            width[n] = max(3, sum(width[c] for c in chain[n]))
    place: dict[int, tuple[int, int]] = {}
    todo = [(0, 0, 0)]
    while todo:
        n, row, left = todo.pop()
        center = left + 1
        place[n] = (row, center)
        for i, c in enumerate(lefts[n]):
            place[c] = (row + i, center - 1)
        for i, c in enumerate(rights[n]):
            place[c] = (row + i, center + 1)
        band = max(1, len(lefts[n]), len(rights[n]))
        x = left
        for c in chain[n]:
            todo.append((c, row + band, x))
            x += width[c]
    # A box no edge of the tree reaches (none should be) stands at the end.
    for n in range(count):
        if n not in place:
            place[n] = (max(r for r, _ in place.values()) + 1, 1)
    rows = max(r for r, _ in place.values()) + 1
    columns = max(c for _, c in place.values()) + 1
    return place, max(columns, 3), rows

