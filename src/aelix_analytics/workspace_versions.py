"""Version histories in the Workspace (design WORKSPACE-READABILITY 5, #99).

Every target that has versions — a Visualization key, a data file, a script's code, CONTEXT.md — is
shown the same way and in the same order (mockup 9-D):

1. 'Version n of N · time'. The number is for people, counted when the Workspace is made; links and
   citations keep the ids the records hold (run ids, sha256, snapshot names).
2. A time strip: ○ a version, ● the version shown, a small dot the same content again, × a run that
   failed. It is a picture (aria-hidden); the list after it says the same in words, without a script.
3. Why a version differs from the one before, as badges; none when the records do not say.
4. Versions in a row with the same cause, folded into one row. The latest and the shown version are
   rows of their own.
5. Who used each version: the records citing it, the runs reading it, the runs of the code, the
   insights written while CONTEXT.md was so. A count of uses is a fact, not how strong a conclusion is
   (TERMINOLOGY 6).
6. A note when nobody has used the latest version yet, naming the last version used.

The numbers, groups and uses are the read model's (ReadModel.visualization_history, data_history,
code_history, context_history, correction_history); this module only shows them. The page helpers
are the Site's (as workspace_map takes them), so it imports no other part of the Workspace.
"""
from __future__ import annotations

import difflib
import hashlib
import re
from datetime import datetime
from pathlib import PurePosixPath

from . import core
from .i18n import attr, t

# Where a strip puts its first and last mark (percent of its width), and how far apart two number
# labels must be to both be written.
STRIP_FROM, STRIP_TO, LABEL_GAP = 3.0, 97.0, 7.0
# How many runs a row names before "and N more" (the run pages and the dataset page list them all).
SHOWN_RUNS = 3
# How many input names a reason badge names.
SHOWN_INPUTS = 2
# How many rows a Visualization's history shows on the run page of a version that is not the latest:
# every version's run page holding every row would grow as the square of the versions. The latest
# version's page holds them all (design 5.3: a long history folds its groups).
WINDOW_ROWS = 100
# How many marks a gallery card's strip shows: the latest ones.
CARD_POINTS = 40
# How many records before and after a record its correction history shows.
CHAIN_ROWS = 10
SCRIPT_DIR = "scripts"


def instant(text) -> float | None:
    """A time with an offset as a number to place it by; None for one without (it cannot be placed
    against those that have one)."""
    try:
        at = datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None
    return at.timestamp() if at is not None and at.tzinfo is not None else None


def script_page(script: str) -> str:
    """The file name of a script's page: its own name, readable, then 10 hex of its path, so every
    path has one page whatever its spelling (safe in a URL and on every file system)."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", PurePosixPath(script).name)[:40].strip("._") or "script"
    return f"{stem}-{hashlib.sha256(script.encode('utf-8')).hexdigest()[:10]}.html"


def arrange(groups: list[list[int]], failed: list[dict], keep: set[int]) -> list[tuple]:
    """The rows of a history, newest first: ('version', n), ('group', [n, …]) or ('failed', run) —
    a failed run where it came (after `after` versions), and the numbers in `keep` (the latest, the one
    shown) taken out of their groups as rows of their own."""
    items: list[tuple] = []
    waiting = sorted(failed, key=lambda f: f["after"])
    for group in groups:
        while waiting and waiting[0]["after"] < group[0]:
            items.append(("failed", waiting.pop(0)))
        part: list[int] = []
        for n in group:
            if n in keep:
                if part:
                    items.append(("group", part) if len(part) > 1 else ("version", part[0]))
                    part = []
                items.append(("version", n))
            else:
                part.append(n)
        if part:
            items.append(("group", part) if len(part) > 1 else ("version", part[0]))
    items += [("failed", f) for f in waiting]
    return items[::-1]


def number_range(numbers: list[int]) -> str:
    """v3, or v3–v7 for numbers in a row, or v3, v5 … as the kit's words."""
    if not numbers:
        return ""
    ordered = sorted(set(numbers))
    if len(ordered) > 1 and ordered[-1] - ordered[0] == len(ordered) - 1:
        return t("versions.range", first=ordered[0], last=ordered[-1])
    return ", ".join(t("versions.v", n=n) for n in ordered)


class Versions:
    """The version histories one build shows, from the Site's read."""

    directory = SCRIPT_DIR
    page_name = staticmethod(script_page)
    # The folding and range rules, for a history another module shows (workspace_reports).
    arrange = staticmethod(arrange)
    number_range = staticmethod(number_range)

    def __init__(self, site):
        self.site = site
        self.model = site.model
        self.context = None

    # --- shared parts ------------------------------------------------------------------------------

    def strip(self, points: list[dict], groups: list[list[int]], kind: str) -> str:
        """The time strip (design 5.1): each point a mark at its time — `version` ○, `shown` ●,
        `again` (the same content again) a small dot, `failed` × — with a band behind each group of
        points (indexes into `points`), the number labels that fit, and a bar of each point's `use`.
        Points are placed by time when every one has a time with an offset, else evenly in order.
        A picture only (aria-hidden): the list after it says the same."""
        if not points:
            return ""
        times = [instant(p.get("at")) for p in points]
        if len(points) > 1 and None not in times and max(times) > min(times):
            low, high = min(times), max(times)
            xs = [STRIP_FROM + (STRIP_TO - STRIP_FROM) * (x - low) / (high - low) for x in times]
        elif len(points) > 1:
            xs = [STRIP_FROM + (STRIP_TO - STRIP_FROM) * i / (len(points) - 1) for i in range(len(points))]
        else:
            xs = [50.0]
        uses = [p.get("use") or 0 for p in points]
        most = max(uses)
        height = 62 if most else 40
        parts = []
        for group in groups:
            if len(group) > 1:
                left, right = min(xs[i] for i in group), max(xs[i] for i in group)
                parts.append(f'<rect class="vh-band" x="{left - 1.2:.2f}%" y="4" width="{right - left + 2.4:.2f}%" '
                             f'height="22" rx="6"/>')
        parts.append('<line class="vh-axis" x1="1%" x2="99%" y1="15" y2="15"/>')
        for x, point, use in zip(xs, points, uses, strict=True):
            mark = point["mark"]
            if mark == "failed":
                parts.append(f'<text class="vh-x" x="{x:.2f}%" y="20" text-anchor="middle">×</text>')
            elif mark == "again":
                parts.append(f'<circle class="vh-again" cx="{x:.2f}%" cy="15" r="2.5"/>')
            else:
                parts.append(f'<circle class="vh-{mark}" cx="{x:.2f}%" cy="15" r="{6 if mark == "shown" else 5}"/>')
            if use:
                bar = max(2.0, 18 * use / most)
                parts.append(f'<rect class="vh-bar" x="{x:.2f}%" y="{60 - bar:.1f}" width="4" height="{bar:.1f}" '
                             f'transform="translate(-2 0)"/>')
        last = None
        labelled = [i for i, p in enumerate(points) if p.get("label")]
        must = {i for i in labelled if points[i]["mark"] == "shown"} | set(labelled[-1:])
        for i in labelled:
            near = last is not None and xs[i] - xs[last] < LABEL_GAP
            later = [j for j in must if j > i and xs[j] - xs[i] < LABEL_GAP]
            if i not in must and (near or later):
                continue
            parts.append(f'<text class="vh-label" x="{xs[i]:.2f}%" y="38" text-anchor="middle">{points[i]["label"]}</text>')
            last = i
        return (f'<svg class="vh-strip kind-{kind}" width="100%" height="{height}" aria-hidden="true" '
                f'focusable="false">{"".join(parts)}</svg>')

    @staticmethod
    def legend(items: list[str]) -> str:
        """The strip's marks in words (`version`, `shown`, `again`, `failed`, and `use_cited`,
        `use_read` or `use_insights` for the bars): the picture's own key, hidden with it."""
        marks = {"version": "○", "shown": "●", "again": "•", "failed": "×"}
        return ('<ul class="vh-legend" aria-hidden="true">' + "".join(
            f'<li><span class="vh-key vh-key-{item.partition("_")[0]}">{marks.get(item, "▮")}</span> '
            f'{t("versions.legend." + item)}</li>' for item in items) + "</ul>")

    def span(self, first, last) -> str:
        """`Aug 5 – Sep 11`: the first and last day, or the one day."""
        s = self.site
        a, b = s.fmt_date(first), s.fmt_date(last)
        return a if a == b else f"{a} – {b}"

    @staticmethod
    def row(number: str, at: str, badges: list[str], body: str, use: str, link: str, *,
            classes: str = "", row_id: str = "") -> str:
        anchor = f' id="{row_id}"' if row_id else ""
        return (f'<li class="vh-row{classes}"{anchor}><span class="vh-n">{number}</span><span class="vh-at">{at}</span>'
                f'<span class="vh-what">{"".join(badges)}{" " if badges and body else ""}{body}</span>'
                f'<span class="vh-use">{use}</span><span class="vh-link">{link}</span></li>')

    @staticmethod
    def group(summary: str, use: str, rows: list[str], *, opened: bool = False) -> str:
        return (f'<li class="vh-group"><details{" open" if opened else ""}><summary><span class="vh-sum">{summary}</span>'
                f'<span class="vh-use">{use}</span></summary><ol class="vh-list">{"".join(rows)}</ol></details></li>')

    @staticmethod
    def block(anchor: str, facts: str, strip: str, legend: str, rows: list[str], foot: str = "", *,
              heading: str = "versions.history", level: int = 2) -> str:
        """A history: its heading (`level`: under the page's sections, or under a chart's heading on a
        run page) and facts, the strip and its key, the rows, a closing note. Open: it is what the
        page is for, and it reads without a script."""
        picture = f'<div class="vh-strip-wrap">{strip}</div>' if strip else ""
        return (f'<section class="vh" id="{anchor}"><details class="vh-fold" open><summary><h{level}>{t(heading)}</h{level}> '
                f'<span class="muted">{facts}</span></summary>{picture}{legend}'
                f'<ol class="vh-list">{"".join(rows)}</ol>{foot}</details></section>')

    @staticmethod
    def showing(number: int, total: int, at: str, *, latest: bool, extra: str = "", anchor: str = "") -> str:
        """'Version n of N · time · latest' (design 5.1)."""
        tail = f' · {t("versions.latest")}' if latest else ""
        named = f' id="{anchor}"' if anchor else ""
        return (f'<p class="vh-showing"{named}><span class="badge tone-muted">{t("versions.v", n=number)}</span> '
                f'{t("versions.n_of", n=number, total=total)} · {at}{tail}{extra}</p>')

    def runs_html(self, runs: list[str], depth: int, anchor: str = "") -> str:
        s = self.site
        links = [f'<a href="{s.esc(s.run_link(r, depth, anchor))}">{s.esc(r)}</a>' for r in runs[:SHOWN_RUNS]]
        more = len(runs) - SHOWN_RUNS
        return ", ".join(links) + (" " + t("list.and_more", n=more) if more > 0 else "")

    def input_badge(self, reason: dict) -> str:
        """`Input changed` with the names of the inputs read with other bytes or newly (as written)."""
        s = self.site
        names = [s.user_text(s.dataset_name(p) or PurePosixPath(p).stem) for p in reason.get("changed") or []]
        if not names and reason.get("removed"):
            return s.badge("warning", "⇄", t("versions.reason.inputs_removed", n=len(reason["removed"])))
        more = len(names) - SHOWN_INPUTS
        shown = ", ".join(names[:SHOWN_INPUTS]) + (" " + t("list.and_more", n=more) if more > 0 else "")
        return s.badge("warning", "⇄", t("versions.reason.inputs") + (f" · {shown}" if shown else ""))

    def reason_badges(self, reasons: list[dict]) -> list[str]:
        s = self.site
        out = []
        for reason in reasons:
            kind = reason["kind"]
            if kind == "code":
                out.append(s.badge("warning", "✎", t("versions.reason.code")
                                   + f' · <span class="sha">@{s.short(reason.get("to"))}</span>'))
            elif kind == "inputs":
                out.append(self.input_badge(reason))
            elif kind == "script":
                out.append(s.badge("warning", "⇄", t("versions.reason.script") + " · "
                                   + s.user_text(PurePosixPath(reason.get("to") or "?").name)))
            elif kind in ("imported", "outside", "written"):
                out.append(s.badge("warning" if kind == "outside" else "muted", "↻", t("versions.reason." + kind)))
        return out

    @staticmethod
    def lines_badge(added, removed) -> str:
        if added is None:
            return ""
        return f'<span class="badge tone-muted">{t("versions.lines", added=added, removed=removed)}</span>'

    def failed_row(self, failure: dict, depth: int, what: str) -> str:
        """A run that ended without success: no version (design 5.2), shown where it came."""
        s = self.site
        body = [f'{t("versions.code_at")} <span class="sha">@{s.short(failure.get("code"))}</span>'] if failure.get("code") else []
        if failure.get("error"):
            body.append(s.user_text(failure["error"]))
        body.append(what)
        body += self.reruns_html(failure.get("reruns") or [], depth)
        return self.row(f'<span aria-hidden="true">×</span> {t("versions.failed_run")}', s.fmt_time(failure.get("started_at")),
                        [s.status_badge(failure.get("status"))], " · ".join(body), "",
                        f'<a href="{s.esc(s.run_link(failure["run"], depth))}">{t("versions.open_run")}</a>',
                        classes=" is-failed")

    # --- Visualizations (gallery card, the run page's chart) ----------------------------------------

    def visualization(self, key: str) -> dict:
        return self.model.visualization_history(key)

    def version_of(self, key: str, run_id: str) -> dict | None:
        return next((v for v in self.visualization(key)["versions"] if v["run"] == run_id), None)

    def chart_badge(self, key: str, run_id: str) -> str:
        """The gallery card's `v19` (mockup 6-C), titled 'Version 19 of 19'."""
        history = self.visualization(key)
        version = self.version_of(key, run_id)
        if version is None:
            return ""
        title = attr("title", "versions.n_of", n=version["number"], total=len(history["versions"]))
        return f'<span class="badge tone-viz"{title}>{t("versions.v", n=version["number"])}</span>'

    def chart_note(self, key: str, *, card: bool = False) -> str:
        """What a reader of the latest version needs first (design 5.2, mockups 6-B and 6-C): what
        changed in it, and when no record cites it yet, the last version one does. A gallery card
        (`card`) does not say that no version is cited at all: the history on the run page does."""
        history = self.visualization(key)
        if not history["versions"]:
            return ""
        latest = history["versions"][-1]
        parts = []
        changed = {r["kind"] for r in latest["reasons"]}
        if changed:
            key = ("versions.note.changed_both" if changed >= {"code", "inputs"} else
                   "versions.note.changed_code" if "code" in changed else "versions.note.changed_inputs")
            parts.append(f'<strong>{t(key, n=latest["number"])}</strong>')
        info = history["latest"]
        if not info["cited"] and len(history["versions"]) > 1 and (info["last_cited"] or not card):
            parts.append(t("versions.note.not_cited", n=latest["number"]) + " "
                         + t("versions.note.last_cited", n=info["last_cited"]) if info["last_cited"]
                         else t("versions.note.none_cited"))
        if not parts:
            return ""
        return f'<div class="callout tone-warning vh-note" role="note">{" ".join(parts)}</div>'

    def chart_strip_row(self, key: str, chart_id: str, run_id: str, depth: int) -> str:
        """The card's folded history (mockup 6-C): how many versions over which days, the strip, and the
        way to the history on the latest version's run page."""
        s = self.site
        history = self.visualization(key)
        versions = history["versions"]
        if len(versions) < 2:
            return ""
        points, groups = self.chart_points(history, run_id)
        if len(points) > CARD_POINTS:
            # The card's strip is the latest CARD_POINTS marks: the index stays small however long the
            # history (the run page has all of it).
            cut = len(points) - CARD_POINTS
            points = points[cut:]
            groups = [g for g in ([i - cut for i in group if i >= cut] for group in groups) if g]
        link = s.run_link(run_id, depth, f"#chart-{s.href(str(chart_id))}-versions")
        return (f'<div class="vh-card"><span class="vh-count">{t("versions.count", n=len(versions))}</span>'
                f'<span class="vh-days">{self.span(versions[0]["started_at"], versions[-1]["started_at"])}</span>'
                f'<div class="vh-strip-wrap">{self.strip(points, groups, "viz")}</div>'
                f'<a class="vh-open" href="{s.esc(link)}">{t("versions.history")}</a></div>')

    def chart_points(self, history: dict, shown_run: str) -> tuple[list[dict], list[list[int]]]:
        points: list[dict] = []
        place: dict[int, int] = {}
        waiting = sorted(history["failed"], key=lambda f: f["after"])
        for version in history["versions"]:
            while waiting and waiting[0]["after"] < version["number"]:
                points.append({"at": waiting.pop(0)["started_at"], "mark": "failed"})
            place[version["number"]] = len(points)
            points.append({"at": version["started_at"], "label": f'v{version["number"]}',
                           "mark": "shown" if version["run"] == shown_run else "version",
                           "use": len(version["cited_by"])})
            for again in version["reruns"]:
                points.append({"at": again["started_at"], "mark": "again"})
        points += [{"at": f["started_at"], "mark": "failed"} for f in waiting]
        groups = [[place[n] for n in group] for group in history["groups"]]
        return points, groups

    def not_a_version(self, run: dict, chart: dict, versions: list[dict], anchor: str) -> str:
        """Why a run's chart is no version (design 5.2): it re-executed another run (a version, or
        a run that is none), it has not ended, or it did not succeed."""
        s = self.site
        ended = (s.recording.get(run["id"]) or {}).get("execution_status", run.get("status"))
        of = next((v for v in versions for again in v["reruns"] if again["run"] == run["id"]), None)
        if of is not None:
            return (f'<p class="vh-showing" id="{anchor}">{t("versions.rerun_of", n=of["number"])} '
                    f'<a href="{s.esc(s.run_link(of["run"], 1, "#chart-" + s.href(str(chart.get("id")))))}">'
                    f'{t("versions.open_version", n=of["number"])}</a></p>')
        rerun_of = run.get("rerun_of")
        if isinstance(rerun_of, str) and rerun_of:
            return (f'<p class="vh-showing" id="{anchor}">{t("versions.rerun_of_run")} '
                    f'<a href="{s.esc(s.run_link(rerun_of, 1))}">{s.esc(rerun_of)}</a></p>')
        if ended in ("running", "incomplete"):
            return f'<p class="vh-showing" id="{anchor}">{t("versions.not_ended")}</p>'
        if ended != "ok":
            return f'<p class="vh-showing" id="{anchor}">{t("versions.not_a_version")}</p>'
        return f'<p class="vh-showing" id="{anchor}">{t("versions.not_of_key")}</p>'

    def chart_history(self, run: dict, chart: dict, key: str) -> str:
        """The chart on its run's page is the version it shows (design 5.3): 'Version n of N', what a
        reader needs first, and the key's whole history with this version marked. A run whose chart
        is no version says why. On the page of a version that is not the latest, a history of more
        than WINDOW_ROWS versions and failed runs shows those nearest the version (groups cut to them,
        no strip): the latest version's page holds them all."""
        s = self.site
        history = self.visualization(key)
        versions = history["versions"]
        version = self.version_of(key, run["id"])
        anchor = f'chart-{s.esc(str(chart.get("id")))}-versions'
        if version is None:
            return self.not_a_version(run, chart, versions, anchor)
        head = self.showing(version["number"], len(versions), s.fmt_time(version["started_at"]),
                            latest=version["number"] == len(versions), anchor=anchor)
        if (len(versions) < 2 and not history["failed"] and not history["named_by"]
                and not any(v["reruns"] or v["cited_by"] for v in versions)):
            return head
        # Versions and failed runs in time order; on a page that is not the latest's, those nearest.
        sequence: list[tuple] = []
        waiting = sorted(history["failed"], key=lambda f: f["after"])
        for v in versions:
            while waiting and waiting[0]["after"] < v["number"]:
                sequence.append(("failed", waiting.pop(0)))
            sequence.append(("version", v["number"]))
        sequence += [("failed", f) for f in waiting]
        windowed = version["number"] != len(versions) and len(sequence) > WINDOW_ROWS
        groups, failed = history["groups"], history["failed"]
        if windowed:
            at = sequence.index(("version", version["number"]))
            start = max(0, min(at - WINDOW_ROWS // 2, len(sequence) - WINDOW_ROWS))
            kept = sequence[start:start + WINDOW_ROWS]
            numbers = {item for kind, item in kept if kind == "version"}
            groups = [g for g in ([n for n in group if n in numbers] for group in groups) if g]
            failed = [item for kind, item in kept if kind == "failed"]
        rows = []
        for kind, item in arrange(groups, failed, {len(versions), version["number"]}):
            if kind == "failed":
                rows.append(self.failed_row(item, 1, t("versions.makes_no_version")))
            elif kind == "version":
                rows.append(self.chart_row(versions[item - 1], len(versions), version["number"], chart))
            else:
                members = [versions[n - 1] for n in item]
                cited = sum(len(v["cited_by"]) for v in members)
                first = members[0]
                summary = (f'<span class="vh-n">{t("versions.range", first=item[0], last=item[-1])} · '
                           f'{t("versions.count", n=len(item))}</span> {self.span(first["started_at"], members[-1]["started_at"])} · '
                           f'{t("versions.same_code_inputs")} <span class="sha">@{s.short(first["code"])}</span>')
                rows.append(self.group(summary, t("versions.cited_by", n=cited),
                                       [self.chart_row(v, len(versions), version["number"], chart) for v in members[::-1]]))
        named = history["named_by"]
        foot = ""
        if windowed:
            whole = s.run_link(versions[-1]["run"], 1, f"#{anchor}")
            foot += (f'<p class="vh-foot">{t("versions.window", n=WINDOW_ROWS)} '
                     f'<a href="{s.esc(whole)}">{t("versions.all_on_latest", n=len(versions))}</a></p>')
        if named:
            target = s.relations.link(key, 1) if key in s.relations.identities else ""
            foot += (f'<p class="vh-foot">{t("versions.named_without", n=len(named))}'
                     + (f' <a href="{s.esc(target)}">{t("versions.see_unbound")}</a>' if target else "") + "</p>")
        facts = " · ".join(filter(None, [t("versions.count", n=len(versions)),
                                         self.span(versions[0]["started_at"], versions[-1]["started_at"]),
                                         t("versions.failed_runs", n=len(history["failed"])) if history["failed"] else ""]))
        if windowed:
            strip = legend = ""
        else:
            points, point_groups = self.chart_points(history, run["id"])
            strip = self.strip(points, point_groups, "viz")
            legend = self.legend(["version", "shown", *(["again"] if any(v["reruns"] for v in versions) else []),
                                  *(["failed"] if history["failed"] else []),
                                  *(["use_cited"] if any(v["cited_by"] for v in versions) else [])])
        return head + self.chart_note(key) + self.block(anchor + "-list", facts, strip, legend, rows, foot, level=4)

    def chart_row(self, version: dict, total: int, shown: int, chart: dict) -> str:
        s = self.site
        n = version["number"]
        badges = self.reason_badges(version["reasons"]) + self.place_badges(n, total, shown)
        body = []
        run = s.run_by_id.get(version["run"]) or {}
        body.append(s.user_text(run.get("title") or version["run"])
                    + (s.inferred_mark(version["run"], [version["run"]]) if version["inferred"] else ""))
        body += self.reruns_html(version["reruns"], 1, cited=True)
        link = ("" if n == shown else
                f'<a href="{s.esc(s.run_link(version["run"], 1, "#chart-" + s.href(str(chart.get("id")))))}">{t("versions.open")}</a>')
        return self.row(t("versions.v", n=n), s.fmt_time(version["started_at"]), badges, " · ".join(body),
                        t("versions.cited_by", n=len(version["cited_by"])), link,
                        classes=" is-shown" if n == shown else "")

    def reruns_html(self, reruns: list[dict], depth: int, *, cited: bool = False) -> list[str]:
        """`Rerun <how it ended or its comparison>` for each rerun beside what it re-executed."""
        s = self.site
        out = []
        for again in reruns:
            said = (s.words("verdict", again["verdict"]) if again.get("verdict")
                    else s.words("status", again.get("status") or "unknown"))
            out.append(f'<a href="{s.esc(s.run_link(again["run"], depth))}">{t("versions.rerun")}</a> {said}'
                       + (f' · {t("versions.cited_by", n=len(again["cited_by"]))}'
                          if cited and again.get("cited_by") else ""))
        return out

    @staticmethod
    def place_badges(n: int, total: int, shown: int | None) -> list[str]:
        """`Latest` and `Shown`: the row of the latest version and of the version the page shows say
        so in words, not by their background alone."""
        out = []
        if n == total:
            out.append(f'<span class="badge tone-muted">{t("versions.latest")}</span>')
        if n == shown:
            out.append(f'<span class="badge tone-muted">{t("versions.shown")}</span>')
        return out

    # --- data files (the dataset page) --------------------------------------------------------------

    def data(self, path: str) -> dict:
        return self.model.data_history(path)

    def data_points(self, history: dict, shown: int) -> tuple[list[dict], list[list[int]]]:
        points: list[dict] = []
        place: dict[int, int] = {}
        events = []
        for version in history["versions"]:
            events.append((instant(version["started_at"]), 0, version["number"], "version", version["started_at"]))
            for run_id in version["writes"][1:]:
                at = (self.model.run(run_id) or {}).get("started_at")
                events.append((instant(at), 1, version["number"], "again", at))
        for failure in history["failed"]:
            events.append((instant(failure["started_at"]), 2, failure["after"], "failed", failure["started_at"]))
        timed = all(e[0] is not None for e in events)
        events.sort(key=lambda e: (e[0], e[1]) if timed else (e[2], e[1]))
        for _, _, n, mark, text in events:
            if mark == "version":
                place[n] = len(points)
                version = history["versions"][n - 1]
                points.append({"at": text, "label": f"v{n}", "mark": "shown" if n == shown else "version",
                               "use": len(version["readers"])})
            else:
                points.append({"at": text, "mark": mark})
        return points, [[place[n] for n in group] for group in history["groups"]]

    def data_history(self, ds: dict) -> str:
        """The dataset page's versions (design 5.2, mockup 9-A): the file now, what a reader needs
        first, and every version with the runs that read it."""
        s = self.site
        path = ds["path"]
        history = self.data(path)
        versions = history["versions"]
        if not versions:
            return ""
        now = history["now"]
        shown = now.get("number") if now["state"] == "version" else len(versions)
        latest = versions[-1]
        file_now = {"version": t("versions.file_now.version", n=now.get("number") or 0),
                    "failed_output": t("versions.file_now.failed_output"),
                    "other": t("versions.file_now.other"), "missing": t("versions.file_now.missing")}[now["state"]]
        head = self.showing(shown, len(versions), s.fmt_time(versions[shown - 1]["started_at"]),
                            latest=shown == len(versions), extra=f" · {file_now}")
        notes = []
        info = history["latest"]
        if not info["read"] and len(versions) > 1:
            notes.append(f'<strong>{t("versions.note.not_read", n=latest["number"])}</strong>')
            if info["last_read"]:
                last = versions[info["last_read"] - 1]
                notes.append(t("versions.note.last_read", n=last["number"]) + " "
                             + self.runs_html([r["run"] for r in last["readers"]][-1:], 1))
            if latest["reasons"]:
                notes.append(" ".join(self.reason_badges(latest["reasons"])))
        note = f'<div class="callout tone-warning vh-note" role="note">{" ".join(notes)}</div>' if notes else ""
        points, groups = self.data_points(history, shown)
        rows = []
        for kind, item in arrange(history["groups"], history["failed"], {len(versions), shown}):
            if kind == "failed":
                what = (t("versions.wrote_nothing") if not item.get("wrote") else
                        t("versions.wrote_version", n=item["wrote_version"]) if item.get("wrote_version") else
                        t("versions.wrote_not_version") + f' <span class="sha">{s.short(item["wrote"], 12)}</span>')
                rows.append(self.failed_row(item, 1, what))
            elif kind == "version":
                rows.append(self.data_row(versions[item - 1], len(versions), shown))
            else:
                members = [versions[n - 1] for n in item]
                first = members[0]
                writes = sum(len(v["writes"]) for v in members)
                code = (f' · {t("versions.written_by_code")} <span class="sha">@{s.short(first["code"])}</span>'
                        if first["code"] else "")
                summary = (f'<span class="vh-n">{t("versions.range", first=item[0], last=item[-1])} · '
                           f'{t("versions.count", n=len(item))}</span> '
                           f'{self.span(first["started_at"], members[-1]["started_at"])}{code}'
                           + (f' · {t("versions.writes", n=writes)}' if writes else ""))
                rows.append(self.group(summary, t("versions.read_by", n=sum(len(v["readers"]) for v in members)),
                                       [self.data_row(v, len(versions), shown) for v in members[::-1]]))
        foot = [t("versions.data_rule")]
        no_hash = [r for r in history["other_reads"] if not r.get("sha256")]
        other = [r for r in history["other_reads"] if r.get("sha256")]
        if no_hash:
            foot.append(t("versions.reads_no_hash", n=len(no_hash)))
        if other:
            foot.append(t("versions.reads_other", n=len(other)))
        if history["named_by"]:
            foot.append(t("versions.named_without_data", n=len(history["named_by"])))
        facts = " · ".join(filter(None, [
            t("versions.count", n=len(versions)),
            t("versions.writes", n=sum(len(v["writes"]) for v in versions)) if any(v["writes"] for v in versions) else "",
            t("versions.reads", n=sum(len(v["readers"]) for v in versions)),
            self.span(versions[0]["started_at"], versions[-1]["started_at"])]))
        legend = self.legend(["version", "shown", *(["again"] if any(len(v["writes"]) > 1 for v in versions) else []),
                              *(["failed"] if history["failed"] else []),
                              *(["use_read"] if any(v["readers"] for v in versions) else [])])
        return (f'<section class="vh-data" id="versions">{head}{note}'
                + self.block("version-history", facts, self.strip(points, groups, "data"), legend, rows,
                             f'<p class="vh-foot">{" ".join(foot)}</p>') + "</section>")

    def data_row(self, version: dict, total: int, shown: int) -> str:
        s = self.site
        n = version["number"]
        badges = self.reason_badges(version["reasons"]) + self.place_badges(n, total, shown)
        facts = []
        if version["rows"] is not None:
            facts.append(t("versions.rows", n=version["rows"]))
        if version["bytes"] is not None:
            facts.append(s.fmt_bytes(version["bytes"]))
        facts.append(f'<span class="sha">{s.short(version["sha256"], 12)}</span>')
        if version["source"] == "import":
            facts.append(t("versions.imported"))
        elif version["source"] == "read":
            facts.append(t("versions.first_read_by") + " " + self.runs_html([version["run"]], 1))
        elif version["run"]:
            facts.append(t("versions.written_by") + " " + self.runs_html([version["run"]], 1, "#outputs")
                         + (s.inferred_mark(version["run"], [version["run"]]) if version.get("inferred") else ""))
        if len(version["writes"]) > 1:
            facts.append(t("versions.written_again", n=len(version["writes"]) - 1))
        facts += self.reruns_html(version["reruns"], 1)
        readers = version["readers"]
        failed = [r["run"] for r in readers if r.get("status") != "ok"]
        inferred = [r["run"] for r in readers if r.get("inferred")]
        if readers:
            facts.append(t("versions.read_by_runs") + " " + self.runs_html([r["run"] for r in readers], 1, "#inputs")
                         + (f' ({t("versions.reads_failed", n=len(failed))})' if failed else "")
                         + (" " + s.inferred_count(inferred) if inferred else ""))
        elif n < total:
            facts.append(t("versions.replaced_unread"))
        return self.row(t("versions.v", n=n), s.fmt_time(version["started_at"]), badges, " · ".join(facts),
                        t("versions.read_by", n=len(readers)), "", classes=" is-shown" if n == shown else "",
                        row_id=f"data-v{n}")

    # --- script code (a page per script) -------------------------------------------------------------

    def code(self, script: str) -> dict:
        return self.model.code_history(script)

    def script_link(self, script: str, depth: int) -> str:
        return f"{self.site.root(depth)}{SCRIPT_DIR}/{self.site.href(script_page(script))}"

    def run_code(self, run: dict) -> str:
        """The run page's 'Code version n of N', linked to its script's page."""
        s = self.site
        script = s.script_of.get(run["id"])
        if not script:
            return ""
        history = self.code(script)
        digest = (run.get("script") or {}).get("sha256")
        version = next((v for v in history["versions"] if v["sha256"] == digest), None)
        if version is None:
            return ""
        return (f' · <a href="{s.esc(self.script_link(script, 1))}">'
                f'{t("versions.code_n_of", n=version["number"], total=len(history["versions"]))}</a>')

    def script_html(self, script: str) -> str:
        """A script's page (design 5.3, mockup 9-B): what it is and makes, its latest code version and
        what changed in it, and its history: one row per run of the same code in a row, `again` when
        earlier code came back."""
        s = self.site
        history = self.code(script)
        versions, segments = history["versions"], history["segments"]
        runs = [entry["run"] for segment in segments for entry in segment["runs"]]
        made_viz = sorted({key for segment in segments for key in segment["visualizations"]})
        made_data = sorted({path for segment in segments for path in segment["data"]})
        latest = versions[history["latest"]["number"] - 1] if versions else None
        name = PurePosixPath(script).name
        makes = []
        for key in made_viz:
            chart = next((c for c in s.charts if c["key"] == key), None)
            makes.append(f'<a href="{s.esc(s.chart_anchor(key, 1))}"{s.user_language}>'
                         f'{s.esc((chart or {}).get("title") or key.rpartition("::")[2])}</a>')
        for path in made_data:
            link = s.data_link(path, 1)
            label = s.esc(s.dataset_name(path) or path)
            makes.append(f'<a href="{s.esc(link)}"{s.user_language}>{label}</a>' if link else f"<span{s.user_language}>{label}</span>")
        facts = [f"<code{s.user_language}>{s.esc(script)}</code>"]
        if latest and latest["lines"] is not None:
            facts.append(t("versions.lines_total", n=latest["lines"]))
        facts.append(t("versions.runs", n=len(runs)))
        head = (f'<div class="page-head"><div class="title-row"><h1{s.user_language}>{s.esc(name)}</h1>'
                f'<span class="badge tone-muted">{t("versions.script")}</span></div>'
                f'<div class="sub">{" · ".join(facts)}</div>'
                + (f'<div class="sub">{t("versions.makes")} {", ".join(makes)}</div>' if makes else "") + "</div>")
        crumb = (f' <span>/</span> <a href="../index.html#runs">{t("tab.runs")}</a> <span>/</span> '
                 f'<span{s.user_language}>{s.esc(name)}</span>')
        if not versions:
            return s.page(1, name, crumb, head + f'<div class="empty">{t("versions.no_code")}</div>', "script",
                          page_path=f"{SCRIPT_DIR}/{script_page(script)}", selection=script)
        last_segment = segments[-1]
        showing = self.showing(latest["number"], len(versions),
                               s.fmt_time(last_segment["runs"][-1]["started_at"]), latest=True)
        note = self.code_note(history)
        diff = self.code_diff(latest, versions, open_=True) if latest["against"] else ""
        rows = []
        points: list[dict] = []
        groups: list[list[int]] = []
        for index, segment in enumerate(segments):
            members = []
            for entry in segment["runs"]:
                members.append(len(points))
                points.append({"at": entry["started_at"], "mark": "failed" if entry["status"] != "ok" else
                               "shown" if index == len(segments) - 1 else "version",
                               "label": f'v{segment["number"]}' if entry is segment["runs"][0] else ""})
            groups.append(members)
            rows.append(self.segment_row(segment, versions, index == len(segments) - 1))
        facts_line = " · ".join([t("versions.code_count", n=len(versions)), t("versions.runs", n=len(runs)),
                                 self.span(segments[0]["runs"][0]["started_at"], last_segment["runs"][-1]["started_at"])])
        diffs = "".join(self.code_diff(v, versions) for v in versions if v["against"] and v is not latest)
        legend = self.legend(["version", "shown", *(["failed"] if any(e["status"] != "ok" for g in segments for e in g["runs"]) else [])])
        body = (head + showing + note + diff
                + self.block("code-history", facts_line, self.strip(points, groups, "code"), legend, rows[::-1],
                             f'<p class="vh-foot">{t("versions.code_rule")}</p>', heading="versions.code_history")
                + (f'<section class="vh-diffs"><h2>{t("versions.code_changes")}</h2>{diffs}</section>' if diffs else ""))
        return s.page(1, name, crumb, body, "script", page_path=f"{SCRIPT_DIR}/{script_page(script)}",
                      selection=script)

    def code_note(self, history: dict) -> str:
        s = self.site
        segments = history["segments"]
        if len(segments) < 2:
            return ""
        last = segments[-1]
        version = history["versions"][last["number"] - 1]
        parts = []
        if last["again"]:
            parts.append(t("versions.note.code_again", n=version["number"]))
        elif version["added"] is not None:
            parts.append(t("versions.note.code_lines", n=version["number"], added=version["added"],
                           removed=version["removed"], against=version["against"]))
        if last["inputs"].get("changed"):
            parts.append(t("versions.note.code_inputs") + " " + ", ".join(
                s.user_text(s.dataset_name(p) or PurePosixPath(p).stem) for p in last["inputs"]["changed"][:SHOWN_INPUTS]))
        if not parts:
            return ""
        return f'<div class="callout tone-warning vh-note" role="note"><strong>{" ".join(parts)}</strong></div>'

    def code_diff(self, version: dict, versions: list[dict], *, open_: bool = False) -> str:
        """What changed in a code version against the code run before it first ran, as the snapshots
        hold it (the author's text: lang="")."""
        s = self.site
        against = versions[version["against"] - 1]
        old = self.snapshot_lines(against["snapshot"])
        new = self.snapshot_lines(version["snapshot"])
        if old is None or new is None:
            return ""
        diff = list(difflib.unified_diff(old, new, lineterm="", n=1))[2:]
        lines = "".join(
            f'<span class="{"add" if d.startswith("+") else "del" if d.startswith("-") else "hunk" if d.startswith("@@") else "ctx"}">{s.esc(d)}</span>\n'
            for d in diff)
        summary = (t("versions.changes_from", first=against["number"], last=version["number"]) + " "
                   + self.lines_badge(version["added"], version["removed"]))
        return (f'<details class="vh-diff" id="code-v{version["number"]}"{" open" if open_ else ""}><summary>{summary}</summary>'
                f'<pre class="diff"{s.user_language}>{lines}</pre></details>')

    def snapshot_lines(self, snapshot) -> list[str] | None:
        if not snapshot:
            return None
        try:
            return self.site.p.path(snapshot).read_bytes().decode("utf-8", errors="replace").splitlines()
        except OSError:
            return None

    def segment_row(self, segment: dict, versions: list[dict], latest: bool) -> str:
        s = self.site
        version = versions[segment["number"] - 1]
        runs = segment["runs"]
        n = segment["number"]
        label = t("versions.v_again", n=n) if segment["again"] else t("versions.v", n=n)
        badges = []
        if not segment["again"] and version["added"] is not None:
            badges.append(self.lines_badge(version["added"], version["removed"]))
        if segment["inputs"].get("changed") or segment["inputs"].get("removed"):
            badges.append(self.input_badge(segment["inputs"]))
        failed = [e for e in runs if e["status"] != "ok"]
        if failed and len(failed) == len(runs):
            badges.append(s.status_badge(failed[0]["status"]))
        badges += self.place_badges(n, n, n) if latest else []
        body = [f'<span class="sha">@{s.short(version["sha256"])}</span>'
                + (s.inferred_mark(version["run"], [version["run"]]) if version.get("inferred") and not segment["again"] else "")]
        if segment["again"]:
            body.append(t("versions.same_code_as", n=n))
        elif n == 1:
            body.append(t("versions.first_version"))
        # A rerun runs the snapshot of the run it re-executes: listed here, never as code coming back.
        body += self.reruns_html(segment.get("reruns") or [], 1)
        uses = [t("versions.runs", n=len(runs))]
        for numbers in segment["visualizations"].values():
            uses.append(t("versions.viz_short") + " " + number_range(numbers))
        for numbers in segment["data"].values():
            uses.append(t("versions.data_short") + " " + number_range(numbers))
        if len(segment["visualizations"]) + len(segment["data"]) > 2:
            uses = uses[:1] + [t("versions.made", n=sum(len(set(v)) for v in segment["visualizations"].values())
                                 + sum(len(set(v)) for v in segment["data"].values()))]
        at = (s.fmt_time(runs[0]["started_at"]) if len(runs) == 1
              else self.span(runs[0]["started_at"], runs[-1]["started_at"]))
        link = (f'<a href="#code-v{n}">{t("versions.open_diff")}</a>' if not segment["again"] and version["against"]
                else f'<a href="{s.esc(s.run_link(runs[0]["run"], 1, "#code"))}">{t("versions.open_run")}</a>')
        return self.row(label, at, badges, " · ".join(body), " · ".join(uses), link,
                        classes=(" is-shown" if latest else "") + (" is-failed" if failed and len(failed) == len(runs) else ""))

    # --- CONTEXT.md (the context page) ----------------------------------------------------------------

    def context_history(self) -> dict:
        """CONTEXT.md's versions with core's reading of its pinned sections (core.context_history)."""
        if self.context is None:
            self.context = core.context_history(self.model)
        return self.context

    def context_head(self) -> str:
        """'Version n of N' of CONTEXT.md and, when there are several, when its pinned sections last
        changed (mockup 9-C)."""
        s = self.site
        versions = self.context_history()["versions"]
        if not versions:
            return ""
        total = len(versions)
        latest = versions[-1]
        since = self.context_history()["latest"]["pinned_since"]
        note = ""
        if total > 1 and since:
            changed_at = s.fmt_time(versions[since - 1]["recorded_at"] or self.name_time(versions[since - 1]["name"]))
            note = (f'<div class="callout tone-muted vh-note" role="note"><strong>{t("versions.note.pinned_since", n=since)}</strong> '
                    f'({changed_at}). ' + (t("versions.note.pinned_kept", n=total - since) if since < total else "")
                    + "</div>")
        elif total > 1 and latest.get("pinned"):
            # Pinned sections that no version changed: the same since the first.
            note = f'<div class="callout tone-muted vh-note" role="note">{t("versions.note.pinned_unchanged")}</div>'
        return self.showing(total, total, s.fmt_time(latest["recorded_at"] or self.name_time(latest["name"])),
                            latest=True) + note

    def context_versions(self, paths: dict) -> str:
        """The context page's versions (design 5.2, mockup 9-C): every version with what it changed
        and the insights written while it was the latest. `paths`: snapshot file name -> its file
        (the Site's)."""
        history = self.context_history()
        versions = history["versions"]
        if not versions:
            return ""
        total = len(versions)
        latest = versions[-1]
        points = []
        for version in versions:
            points.append({"at": version["recorded_at"], "label": f'v{version["number"]}',
                           "mark": "shown" if version is latest else "version", "use": version["insights"] or 0})
        groups = [[n - 1 for n in group] for group in history["groups"]]
        rows = []
        texts: dict[int, list[str]] = {}

        def lines(n: int) -> list[str]:
            if n not in texts:
                texts[n] = _lines(paths.get(versions[n - 1]["name"]))
            return texts[n]

        for kind, item in arrange(history["groups"], [], {total, 1}):
            if kind == "version":
                rows.append(self.context_row(versions[item - 1], total, lines))
            else:
                members = [versions[n - 1] for n in item]
                counted = [v["insights"] for v in members if v["insights"] is not None]
                insights = ("–" if not counted else t("versions.insights", n=sum(counted)) if len(counted) == len(members)
                            else t("versions.insights_partial", n=sum(counted), counted=len(counted), total=len(members)))
                summary = (f'<span class="vh-n">{t("versions.range", first=item[0], last=item[-1])} · '
                           f'{t("versions.count", n=len(item))}</span> '
                           f'{self.span(members[0]["recorded_at"], members[-1]["recorded_at"])} · {t("versions.pinned_same")}')
                rows.append(self.group(summary, insights,
                                       [self.context_row(v, total, lines) for v in members[::-1]]))
        changed = sum(1 for v in versions if v["pinned_changed"])
        facts = " · ".join([t("versions.count", n=total),
                            self.span(versions[0]["recorded_at"], latest["recorded_at"]),
                            t("versions.pinned_changed_n", n=changed) if changed else t("versions.pinned_never")])
        legend = self.legend(["version", "shown", *(["use_insights"] if any(v["insights"] for v in versions) else [])])
        return self.block("context-history", facts, self.strip(points, groups, "context"), legend, rows,
                          f'<p class="vh-foot">{t("versions.insights_rule")}</p>')

    def context_latest_changes(self, paths: dict) -> str:
        """What the latest CONTEXT.md version changed against the one before (mockup 9-C), as written."""
        s = self.site
        versions = self.context_history()["versions"]
        if not versions:
            return ""
        latest = versions[-1]
        new = _lines(paths.get(latest["name"]))
        old = _lines(paths.get(versions[-2]["name"])) if len(versions) > 1 else []
        diff = list(difflib.unified_diff(old, new, lineterm="", n=2))[2:]
        shown = "".join(
            f'<span class="{"add" if d.startswith("+") else "del" if d.startswith("-") else "hunk" if d.startswith("@@") else "ctx"}">{s.esc(d)}</span>\n'
            for d in diff) or f'<span class="ctx">{t("context.first_version")}</span>\n'
        return (f'<section class="panel"><h3>{t("versions.what_changed", n=latest["number"])} '
                f'{self.lines_badge(latest["added"], latest["removed"])}</h3>'
                f'<pre class="diff"{s.user_language if diff else ""}>{shown}</pre></section>')

    @staticmethod
    def name_time(name: str) -> str | None:
        """The local time a snapshot's name holds (no offset), when it has no completion record."""
        try:
            return datetime.strptime(name.split("_")[0], "%Y%m%d-%H%M%S-%f").isoformat(timespec="seconds")
        except ValueError:
            return None

    def context_row(self, version: dict, total: int, lines) -> str:
        """A version: its time, what changed (lines, pinned sections), the insights written while it
        was the latest, and inside, its diff against the version before (the author's text)."""
        s = self.site
        n = version["number"]
        badges = [self.lines_badge(version["added"], version["removed"])]
        if version["pinned_changed"]:
            badges.append(s.badge("warning", "⚑", t("versions.reason.pinned")))
        badges += self.place_badges(n, total, total)
        new = lines(n)
        old = lines(n - 1) if n > 1 else []
        diff = list(difflib.unified_diff(old, new, lineterm="", n=2))[2:]
        shown = "".join(
            f'<span class="{"add" if d.startswith("+") else "del" if d.startswith("-") else "hunk" if d.startswith("@@") else "ctx"}">{s.esc(d)}</span>\n'
            for d in diff) or f'<span class="ctx">{t("context.first_version")}</span>\n'
        body = (t("versions.first_recorded") if n == 1 else "") + (
            f'<details class="vh-diff"><summary>{t("versions.what_changed", n=n)}</summary>'
            f'<pre class="diff"{s.user_language if diff else ""}>{shown}</pre></details>')
        who = ""
        if version["event"]:
            event = s.relations.events.get(version["event"])
            actor = f'{s.words("actor", event.value.get("actor"))} · ' if event else ""
            who = f' · <span class="muted">{actor}{s.esc(version["event"])}</span>'
        use = ("–" if version["insights"] is None else t("versions.insights", n=version["insights"]))
        at = s.fmt_time(version["recorded_at"] or self.name_time(version["name"]))
        link = s.relation_link("context:" + version["name"], 0)
        return self.row(t("versions.v", n=n), at + who, badges, body, use, link,
                        classes=" is-shown" if n == total else "", row_id=f'v-{s.esc(PurePosixPath(version["name"]).stem)}')

    # --- record corrections (a record's relation page) -----------------------------------------------

    def corrections(self, identity: str, level: int = 2) -> str:
        """A record's correction history (design 5.2): the records its corrections join it to, in
        journal order, without version numbers — each with its time, what it corrects and its state."""
        s = self.site
        chain = self.model.correction_history(identity)
        if not chain:
            return ""
        # The records nearest this one: a page per record holding the whole of a long history would
        # grow as its square (each record's page has the rest as counts).
        at = next(i for i, item in enumerate(chain) if item["source"] == identity)
        before, after = max(0, at - CHAIN_ROWS), min(len(chain), at + CHAIN_ROWS + 1)
        rows = [f'<li class="muted">{t("versions.chain_earlier", n=before)}</li>'] if before else []
        for item in chain[before:after]:
            source = item["source"]
            event = s.relations.events.get(source)
            text = s.user_text(str(event.value.get("text") or "")[:140]) if event else ""
            when = s.fmt_time(event.recorded_at) if event else ""
            state = item.get("state")
            badge = ""
            if state in s.RECORD_STATE:
                tone, mark = s.RECORD_STATE[state]
                words = (t("versions.corrected") if state == "superseded" else
                         t("record.conflict", n=len(item["corrected_by"])) if state == "conflict" else t("record." + state))
                badge = s.badge(tone, mark, words)
            elif state == "current":
                badge = s.badge("good", "✓", t("versions.current_record"))
            this = f' <strong>({t("versions.this_record")})</strong>' if source == identity else ""
            corrects = (f' · {t("versions.corrects")} ' + ", ".join(s.esc(c) for c in item["corrects"])) if item["corrects"] else ""
            link = s.relations.content(source, 1) or s.relations.link(source, 1)
            rows.append(f'<li><a href="{s.esc(link)}">{s.esc(source)}</a>{this} {when} {badge}{corrects}<br>{text}</li>')
        if after < len(chain):
            rows.append(f'<li class="muted">{t("versions.chain_later", n=len(chain) - after)}</li>')
        return (f'<section class="vh-corrections" id="corrections"><h{level}>{t("versions.correction_history")}</h{level}>'
                f'<p class="muted">{t("versions.correction_lead")}</p><ol class="vh-chain">{"".join(rows)}</ol></section>')


def _lines(path) -> list[str]:
    """A snapshot's lines as written (none when there is no file)."""
    return path.read_bytes().decode("utf-8", errors="replace").splitlines() if path else []
