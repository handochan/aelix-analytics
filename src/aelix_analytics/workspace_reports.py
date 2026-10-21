"""The Reports tab and a page per Report (#19 PR3; ADR-0013 7, design REPORT-ARTIFACTS 5).

A Report is shown in the order design REPORT-ARTIFACTS 5 reads it: the list, then a Report's page —
its versions the way every versioned target is shown (#99: 'Version n of N · time', the strip and the
list), and for each version, before its body, when and on what it was made (created at, based on,
scope), its evidence, its collection's members and its files; then the body.

- The numbers are the completion order's (ReadModel.report_histories): a withdrawal, a restoration
  or a re-render changes neither. That a version was recorded, that its files are the bytes it names,
  the content status its author gave and a review of its conclusion are four things, each said apart;
  nothing here reviews a conclusion, and a member's relation is no evidence of the document's.
- The body is never put in a Workspace page (ADR-0013 7; owner decision of 2026-10-21): the injected
  ReportRenderer makes it a document of its own, its Content-Security-Policy intact, from the
  version's preserved bytes only when they are the bytes its manifest names, beside a copy of those
  files for its links (the renderer takes no path outside its folder). The originals stay where the
  records keep them (artifacts/reports/…), and the page links them there.
- What people wrote (titles, summaries, purposes, member keys, reasons, scopes) is shown as written
  (lang=""); the kit's words are the catalog's.

The histories are the read model's; the page helpers are the Site's (as workspace_versions takes
them), so this module imports no other part of the Workspace.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath

from . import core, relations
from .i18n import t

REPORT_DIR = "reports"
# Where a version's body document goes in its view folder (the renderer's own name for it).
PREVIEW = "preview/report.html"
# The copies of a version's files its body document links to, in its view folder.
COPIES = "files"
# What a view folder keeps to know it is the view of these bytes by this renderer.
STAMP = "view.json"
# A history this long or shorter shows every version as a row of its own; a longer one folds the
# versions between the first and the latest into one row (each keeps its section and anchor).
FOLD_AFTER = 5
# How many lines a version's comparison with the one before shows.
DIFF_LINES = 200
TEXT_ENTRY = (".md", ".markdown", ".txt")
# The renderer's reasons for a version it has no body for (CONTRACTS: StaticReportRenderer): no document
# is written, the page links the originals. Any other reason is said beside a document that is made.
NO_BODY = frozenset({"html_download_only", "format_download_only", "text_encoding_unsupported"})
CONTENT_TONE = {"resolved": ("good", "✓"), "missing": ("critical", "⊗"), "content_changed": ("critical", "⊗"),
                "not_checked": ("muted", "–")}
MEMBER_TONE = {"complete": ("good", "✓"), "failed": ("critical", "✗"), "not_generated": ("warning", "–")}


def report_page(report_id: str) -> str:
    """A Report's page: its id, readable, then 10 hex of it, so two ids differing in case alone have
    two pages on a file system that does not tell case apart."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", report_id)[:40].strip("._") or "report"
    return f"{stem}-{hashlib.sha256(report_id.encode('utf-8')).hexdigest()[:10]}.html"


def view_folder(report_id: str, revision_id: str) -> str:
    """A version's view folder, beside its Report's page: `<page stem>/<revision>`."""
    return f"{report_page(report_id)[:-5]}/{revision_id}"


class Reports:
    """The Reports one build shows, from the Site's read; `renderer` the injected ReportRenderer
    (None: no body documents, each version says so)."""

    directory = REPORT_DIR
    page_name = staticmethod(report_page)

    def __init__(self, site, renderer=None):
        self.site = site
        self.model = site.model
        self.renderer = renderer
        self.histories = self.model.report_histories()
        saved = [h for h in self.histories.values() if h["versions"]]
        # Newest first, by the completion of each Report's latest version; Reports with no version yet
        # (only attempts) after them, by id.
        self.order = (sorted(saved, key=lambda h: h["versions"][-1]["seq"] or 0, reverse=True)
                      + sorted((h for h in self.histories.values() if not h["versions"]), key=lambda h: h["report_id"]))
        self.by_revision = {(v["report_id"], v["revision_id"]): v for h in saved for v in h["versions"]}
        # Filled by write_views: (report_id, revision_id) -> {"href", "supported", "reasons"}.
        self.views: dict[tuple[str, str], dict] = {}

    # --- links -------------------------------------------------------------------------------------

    def link(self, report_id: str, depth: int, revision_id: str | None = None) -> str:
        anchor = f"#v-{self.site.href(revision_id)}" if revision_id else ""
        return f"{self.site.root(depth)}{REPORT_DIR}/{report_page(report_id)}{anchor}"

    def has(self, report_id, revision_id=None) -> bool:
        if revision_id is None:
            return report_id in self.histories
        return (report_id, revision_id) in self.by_revision

    def title(self, report_id: str, revision_id: str | None = None) -> str:
        """The title a version was saved with, else the latest one's, else the id (as written)."""
        version = self.by_revision.get((report_id, revision_id)) if revision_id else None
        history = self.histories.get(report_id)
        if version is None and history and history["versions"]:
            version = history["versions"][-1]
        return str((version or {}).get("manifest", {}).get("title") or report_id)

    # --- the tab and its continuation pages -------------------------------------------------------

    def table(self, histories: list[dict], depth: int) -> str:
        """The Reports' rows, the latest version of each (the list never reads a body)."""
        s = self.site
        if not histories:
            return f'<div class="empty">{t("reports.empty")}</div>'
        head = "".join(f"<th>{t('reports.col.' + c)}</th>" for c in ("report", "version", "saved", "state", "summary"))
        rows = []
        for history in histories:
            rid = history["report_id"]
            versions = history["versions"]
            latest = versions[-1] if versions else None
            name = (f'<a href="{s.esc(self.link(rid, depth))}"{s.user_language}>{s.esc(self.title(rid))}</a>'
                    f' <small><code>{s.esc(rid)}</code></small>')
            if latest is None:
                rows.append(f'<tr id="report-{s.esc(rid)}"><td>{name}</td><td>{t("reports.no_version")}</td><td></td>'
                            f'<td>{self.attempts_badge(history)}</td><td></td></tr>')
                continue
            meta = latest["metadata"]
            summary = " ".join(f"<span{s.user_language}>{s.esc(meta[k])}</span>" for k in ("summary", "purpose")
                               if isinstance(meta.get(k), str) and meta[k])
            rows.append(f'<tr id="report-{s.esc(rid)}"><td>{name}</td>'
                        f'<td>{t("versions.n_of", n=latest["number"], total=len(versions))}</td>'
                        f'<td>{s.fmt_time(latest["recorded_at"])}</td><td>{" ".join(self.badges(latest))}</td>'
                        f'<td>{summary}</td></tr>')
        return (f'<div class="table-wrap"><table class="list reports"><thead><tr>{head}</tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>')

    def badges(self, version: dict) -> list[str]:
        """A version's states, each apart: withdrawn, its files' bytes, the content status its
        author gave, its collection's members. Recording is said by its being a version at all."""
        s = self.site
        out = []
        if version["state"] == "withdrawn":
            out.append(s.badge("muted", "⊘", t("record.withdrawn")))
        if version["content"] != "resolved":
            tone, mark = CONTENT_TONE.get(version["content"], ("muted", "?"))
            out.append(s.badge(tone, mark, s.words("reports.content", version["content"])))
        if version.get("problem"):
            out.append(s.badge("warning", "⚠", t("reports.problem")))
        status = version["metadata"].get("content_status")
        if status:
            out.append(s.badge("muted", "◐" if status == "partial" else "●", s.words("reports.content_status", status)))
        counts = version.get("collection_counts")
        if counts:
            whole = counts["complete"] == counts["requested"]
            out.append(s.badge("good" if whole else "warning", "▦",
                               t("reports.collection.counts", complete=counts["complete"], requested=counts["requested"])))
        return out

    def attempts_badge(self, history: dict) -> str:
        n = len(history["attempts"])
        return self.site.badge("muted", "…", t("reports.attempts.count", n=n)) if n else ""

    # --- a Report's page --------------------------------------------------------------------------

    def page_html(self, history: dict) -> str:
        s = self.site
        rid, versions = history["report_id"], history["versions"]
        latest = versions[-1] if versions else None
        name = report_page(rid)
        title = self.title(rid)
        facts = [f"<code>{s.esc(rid)}</code>", t("versions.count", n=len(versions))]
        if versions:
            facts.append(s.history.span(versions[0]["recorded_at"], latest["recorded_at"]))
        head = (f'<div class="page-head"><div class="title-row"><h1{s.user_language}>{s.esc(title)}</h1>'
                f'<span class="badge tone-muted">{t("reports.badge")}</span></div>'
                f'<div class="sub">{" · ".join(facts)}</div></div>')
        crumb = (f' <span>/</span> <a href="../index.html#reports">{t("tab.reports")}</a> <span>/</span> '
                 f'<span{s.user_language}>{s.esc(title)}</span>')
        body = [head]
        if latest is None:
            body.append(f'<div class="empty">{t("reports.no_version")}</div>')
        else:
            if latest["state"] == "withdrawn":
                body.append(f'<div class="callout tone-muted record-note" role="note"><strong>{t("reports.latest_withdrawn")}</strong> '
                            f'{t("relations.note.kept")}</div>')
            body.append(s.history.showing(latest["number"], len(versions), s.fmt_time(latest["recorded_at"]), latest=True))
            body.append(self.history_html(versions))
            body += [self.version_html(v, versions) for v in reversed(versions)]
        if history["attempts"]:
            body.append(self.attempts_html(history))
        return s.page(1, title, crumb, "".join(body), "report", page_path=f"{REPORT_DIR}/{name}", selection=rid)

    def history_html(self, versions: list[dict]) -> str:
        """The versions as #99 shows every target's: the strip, then a row each (the versions between
        the first and the latest folded when there are many), each linking its section."""
        history = self.site.history
        total = len(versions)
        points = [{"at": v["recorded_at"], "mark": "shown" if v["number"] == total else "version",
                   "label": f'v{v["number"]}', "use": len(self.cited_by(v))} for v in versions]
        keep = set(range(1, total + 1)) if total <= FOLD_AFTER else {1, total}
        rows = []
        for kind, item in history.arrange([list(range(1, total + 1))], [], keep):
            if kind == "version":
                rows.append(self.history_row(versions[item - 1], total))
            else:
                members = [self.history_row(versions[n - 1], total) for n in item[::-1]]
                cited = sum(len(self.cited_by(versions[n - 1])) for n in item)
                rows.append(history.group(history.number_range(item), t("reports.cited", n=cited) if cited else "", members))
        facts = " · ".join([t("versions.count", n=total), history.span(versions[0]["recorded_at"], versions[-1]["recorded_at"])])
        legend = history.legend(["version", "shown", *(["use_cited"] if any(p["use"] for p in points) else [])])
        return history.block("report-history", facts, history.strip(points, [], "report"), legend, rows,
                             f'<p class="vh-foot">{t("reports.history_rule")}</p>')

    def history_row(self, version: dict, total: int) -> str:
        s = self.site
        meta = version["metadata"]
        said = meta.get("change_reason") or meta.get("summary")
        body = f"<span{s.user_language}>{s.esc(said)}</span>" if isinstance(said, str) and said else ""
        cited = len(self.cited_by(version))
        link = f'<a href="#v-{s.esc(self.site.href(version["revision_id"]))}">{t("reports.open_version")}</a>'
        return s.history.row(t("versions.v", n=version["number"]), s.fmt_time(version["recorded_at"]),
                             self.badges(version), body, t("reports.cited", n=cited) if cited else "", link,
                             classes=" vh-latest" if version["number"] == total else "")

    def version_html(self, version: dict, versions: list[dict]) -> str:
        """One version: when and on what it was made, then its evidence, members, files, body and who
        cites it. The latest is open; the others are folded, each with its anchor."""
        s = self.site
        rev = version["revision_id"]
        latest = version["number"] == len(versions)
        summary = (f'<h2>{t("versions.v", n=version["number"])} <span class="muted">· {s.fmt_time(version["recorded_at"])}</span></h2>'
                   f' {" ".join(self.badges(version))}')
        parts = [self.facts_html(version), self.evidence_html(version), self.members_html(version),
                 self.files_html(version), self.compare_html(version, versions), self.cited_html(version)]
        return (f'<section class="report-version" id="v-{s.esc(self.site.href(rev))}"><details{" open" if latest else ""}>'
                f'<summary>{summary}</summary>{"".join(p for p in parts if p)}</details></section>')

    def facts_html(self, version: dict) -> str:
        """When and on what the version was made (REPORT-ARTIFACTS 5: before its body), then what
        else its author said of it, and its states, each in words."""
        s = self.site
        manifest, meta = version["manifest"], version["metadata"]
        rows = [("created_at", s.fmt_time(manifest.get("created_at"))), ("saved", s.fmt_time(version["recorded_at"]))]
        basis = manifest.get("based_on") if isinstance(manifest.get("based_on"), dict) else None
        if basis is None:
            rows.append(("based_on", t("reports.based_on.none")))
        else:
            said = []
            if isinstance(basis.get("as_of_seq"), int):
                said.append(t("reports.based_on.as_of", n=basis["as_of_seq"]))
            context = basis.get("context_version")
            if isinstance(context, str) and context:
                stem = PurePosixPath(context).stem
                said.append(f'<a href="{s.esc(s.root(1))}context.html#v-{s.esc(self.site.href(stem))}">CONTEXT.md</a>')
            refs = basis.get("refs") if isinstance(basis.get("refs"), list) else []
            said.append(t("reports.based_on.refs", n=len(refs)))
            rows.append(("based_on", " · ".join(said)))
        for key in ("scope", "generation"):
            if key in meta:
                rows.append((key, f'<pre class="report-json"{s.user_language}>{s.esc(json.dumps(meta[key], ensure_ascii=False, indent=2))}</pre>'))
        for key in ("summary", "purpose", "change_reason"):
            if isinstance(meta.get(key), str) and meta[key]:
                rows.append((key, f"<span{s.user_language}>{s.esc(meta[key])}</span>"))
        if meta.get("content_status"):
            rows.append(("content_status", s.words("reports.content_status", meta["content_status"])))
        rows.append(("author", f"<span{s.user_language}>{s.esc(manifest.get('actor'))}</span>"))
        rows.append(("state", t("record.withdrawn") if version["state"] == "withdrawn" else t("reports.state.current")))
        tone, mark = CONTENT_TONE.get(version["content"], ("muted", "?"))
        rows.append(("content", s.badge(tone, mark, s.words("reports.content", version["content"]))))
        if version.get("problem"):
            rows.append(("problem", f"<code>{s.esc(version['problem'])}</code>"))
        rows.append(("review", t("reports.review_apart")))
        items = "".join(f"<dt>{t('reports.fact.' + key)}</dt><dd>{value}</dd>" for key, value in rows)
        return f'<dl class="facts report-facts">{items}</dl>'

    def judged(self, version: dict) -> list[dict]:
        return self.model.references(relations.report_key(version["report_id"], version["revision_id"]))

    def evidence_html(self, version: dict) -> str:
        """The document's own evidence (based_on.refs), a badge each with its judgment; a version
        that names none says it is linked to no evidence, not that it has none."""
        s = self.site
        rows = [j for j in self.judged(version) if not j.get("structural")]
        items = " ".join(s.ref_html(j, 1) for j in rows)
        body = f'<p class="refs">{items}</p>' if rows else f'<p class="muted">{t("reports.no_evidence")}</p>'
        return f'<section class="report-part"><h3>{t("reports.evidence")}</h3>{body}<p class="muted">{t("reports.evidence_rule")}</p></section>'

    def members_html(self, version: dict) -> str:
        """A collection's members as the snapshot holds them (ADR-0013 5): each key as written, its
        status, a reason for one that failed or was not made, the exact version of one that is
        complete, judged. Counted from the members, never from a number given."""
        s = self.site
        collection = version["metadata"].get("collection")
        if not isinstance(collection, dict) or not isinstance(collection.get("members"), list):
            return ""
        structural = {(j.get("ref") or {}).get("index"): j for j in self.judged(version) if j.get("structural")}
        counts = version.get("collection_counts") or relations.collection_counts(collection)
        head = "".join(f"<th>{t('reports.member.col.' + c)}</th>" for c in ("key", "status", "reason", "report"))
        rows = []
        for index, member in enumerate(collection["members"]):
            status = member.get("status")
            tone, mark = MEMBER_TONE.get(status, ("muted", "?"))
            reason = member.get("reason")
            target = ""
            if status == "complete":
                judged = structural.get(index)
                mid, mrev = member.get("report_id"), member.get("revision_id")
                if judged and judged["state"] == "resolved" and self.has(mid, mrev):
                    target = (f'<a href="{s.esc(self.link(mid, 1, mrev))}"{s.user_language}>{s.esc(self.title(mid, mrev))}</a> '
                              f'{s.badge("good", "✓", t("ref.resolved"))}')
                else:
                    target = (f"<code>{s.esc(mid)}@{s.esc(mrev)}</code> "
                              + (s.ref_words(judged["state"], judged.get("reason")) if judged else t("reports.member.unjudged")))
            scope = (f'<pre class="report-json"{s.user_language}>{s.esc(json.dumps(member["scope"], ensure_ascii=False))}</pre>'
                     if "scope" in member else "")
            rows.append(f'<tr id="m-{s.esc(self.site.href(version["revision_id"]))}-{index}">'
                        f'<td><span{s.user_language}>{s.esc(member.get("member_key"))}</span>{scope}</td>'
                        f'<td>{s.badge(tone, mark, s.words("reports.member", status))}</td>'
                        f'<td>{f"<span{s.user_language}>{s.esc(reason)}</span>" if isinstance(reason, str) else ""}</td>'
                        f'<td>{target}</td></tr>')
        said = [t("reports.collection.counts", complete=counts["complete"], requested=counts["requested"])]
        said += [t(f"reports.collection.{k}", n=counts[k]) for k in ("failed", "not_generated") if counts[k]]
        return (f'<section class="report-part"><h3>{t("reports.collection")}</h3><p>{" · ".join(said)}</p>'
                f'<details class="report-members"><summary>{t("reports.members", n=counts["requested"])}</summary>'
                f'<div class="table-wrap"><table class="list"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
                f'</details><p class="muted">{t("reports.collection_rule")}</p></section>')

    def user_files(self, version: dict) -> list[dict]:
        """The files its author saved: the manifest's, the kit's metadata sidecar left out."""
        files = version["manifest"].get("files")
        return [f for f in files if isinstance(f, dict) and f.get("path") != relations.REPORT_METADATA_FILE] if isinstance(files, list) else []

    def files_html(self, version: dict) -> str:
        """The body document, or why there is none, then each file: its original where the records
        keep it, its size and hash."""
        s = self.site
        view = self.views.get((version["report_id"], version["revision_id"])) or {"href": None, "reasons": ["not_rendered"]}
        if view.get("href"):
            opener = f'<p><a class="action primary" href="{s.esc(view["href"])}">{t("reports.view.open")}</a></p>'
        else:
            opener = ""
        reasons = "".join(f'<li>{s.words("reports.view.reason", r)}</li>' for r in dict.fromkeys(view.get("reasons") or []))
        notes = f'<ul class="plain report-reasons">{reasons}</ul>' if reasons else ""
        entry = version["manifest"].get("entry")
        rows = []
        for item in self.user_files(version):
            path = str(item.get("path"))
            original = s.file_link(f'{version["directory"]}/content/{path}', 1)
            mark = f' <span class="badge tone-muted">{t("reports.file.entry")}</span>' if path == entry else ""
            rows.append(f'<tr><td><a href="{s.esc(original)}"><code{s.user_language}>{s.esc(path)}</code></a>{mark}</td>'
                        f'<td class="num">{s.fmt_bytes(item.get("bytes"))}</td><td class="sha">{s.short(item.get("sha256"), 12)}</td></tr>')
        head = "".join(f"<th>{t('reports.file.col.' + c)}</th>" for c in ("path", "size", "sha"))
        return (f'<section class="report-part"><h3>{t("reports.files")}</h3>{opener}{notes}'
                f'<div class="table-wrap"><table class="list"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
                f'<p class="muted">{t("reports.files_rule")}</p></section>')

    def text_of(self, version: dict) -> list[str] | None:
        """The entry's lines, when it is text and its bytes are the ones saved; else None."""
        entry = version["manifest"].get("entry")
        if version["content"] != "resolved" or not isinstance(entry, str) or not entry.lower().endswith(TEXT_ENTRY):
            return None
        try:
            return (self.model.root / version["directory"] / "content" / entry).read_bytes().decode("utf-8").splitlines()
        except (OSError, UnicodeError):
            return None

    def compare_html(self, version: dict, versions: list[dict]) -> str:
        """How the version differs from the one before (REPORT-ARTIFACTS 5): the entry's changed
        lines when both are text, else that they cannot be compared here."""
        if version["number"] == 1:
            return ""
        s = self.site
        before = versions[version["number"] - 2]
        a, b = self.text_of(before), self.text_of(version)
        heading = f'<h3>{t("reports.compare", n=before["number"])}</h3>'
        if a is None or b is None:
            return f'<section class="report-part">{heading}<p class="muted">{t("reports.compare_no")}</p></section>'
        lines = [line for line in difflib.unified_diff(a, b, lineterm="", n=1)][2:]
        if not lines:
            return f'<section class="report-part">{heading}<p class="muted">{t("reports.compare_same")}</p></section>'
        added = sum(1 for line in lines if line.startswith("+"))
        removed = sum(1 for line in lines if line.startswith("-"))
        shown = lines[:DIFF_LINES]
        more = len(lines) - len(shown)
        marks = {"+": "add", "-": "del", "@": "hunk"}
        body = "\n".join(f'<span class="{marks[line[:1]]}">{s.esc(line)}</span>' if line[:1] in marks else s.esc(line)
                         for line in shown) + (f"\n{t('list.and_more', n=more)}" if more > 0 else "")
        return (f'<section class="report-part">{heading}<details><summary>{t("reports.compare_lines", added=added, removed=removed)}'
                f'</summary><pre class="diff"{s.user_language}>{body}</pre></details></section>')

    def cited_by(self, version: dict) -> list[str]:
        """The records citing this exact version (ReadModel.report_citations)."""
        return self.model.report_citations(version["report_id"], version["revision_id"])

    def cited_html(self, version: dict) -> str:
        s = self.site
        key = relations.report_key(version["report_id"], version["revision_id"])
        cited = self.cited_by(version)
        said = t("reports.cited", n=len(cited)) if cited else t("reports.not_cited")
        link = (f' {s.relation_link(key, 1, klass="action")}' if key in s.relations.identities else "")
        return f'<section class="report-part"><h3>{t("reports.used")}</h3><p>{said}{link}</p></section>'

    def attempts_html(self, history: dict) -> str:
        """Saving attempts that are no version (prepared, or with no valid completion), apart."""
        s = self.site
        rows = "".join(f'<li><code>{s.esc(a["revision_id"])}</code> · {s.words("reports.recording", a["recording_status"])}'
                       + (f' · <code>{s.esc(a["problem"])}</code>' if a.get("problem") else "") + "</li>"
                       for a in history["attempts"])
        return (f'<section class="report-part"><h2>{t("reports.attempts")}</h2><ul class="plain">{rows}</ul>'
                f'<p class="muted">{t("reports.attempts_rule")}</p></section>')

    # --- the body documents -----------------------------------------------------------------------

    def write_views(self, out: Path) -> int:
        """Each version's body document, from the renderer, in its view folder: written when the
        version's files are the bytes it names, kept as it is when its folder already holds the view
        of those bytes by this renderer, and every other folder under reports/ removed. Returns how
        many documents there are."""
        folder_root = out / REPORT_DIR
        folder_root.mkdir(parents=True, exist_ok=True)
        keep: set[str] = set()
        identity = (f"{type(self.renderer).__module__}.{type(self.renderer).__qualname__}" if self.renderer else None)
        for (rid, rev), version in self.by_revision.items():
            name = view_folder(rid, rev)
            if self.renderer is None:
                self.views[(rid, rev)] = {"href": None, "reasons": ["no_renderer"]}
                continue
            if version["content"] != "resolved":
                self.views[(rid, rev)] = {"href": None, "reasons": ["content_not_resolved"]}
                continue
            files = self.user_files(version)
            stamp = {"report_id": rid, "revision_id": rev, "entry": version["manifest"].get("entry"),
                     "files": [[f.get("path"), f.get("sha256")] for f in files], "renderer": identity,
                     "kit": core.KIT_VERSION}
            folder = folder_root / name
            kept = _read_stamp(folder / STAMP)
            if kept is None or kept.get("stamp") != stamp:
                kept = self._render(version, files, folder, stamp)
            href = f"{name}/{PREVIEW}" if kept.get("written") else None
            self.views[(rid, rev)] = {"href": href, "reasons": kept.get("reasons") or []}
            if kept.get("written"):
                keep.add(name)
        _prune_views(folder_root, keep)
        return len(keep)

    def _render(self, version: dict, files: list[dict], folder: Path, stamp: dict) -> dict:
        root = self.model.root / version["directory"] / "content"
        try:
            data = {str(f["path"]): (root / str(f["path"])).read_bytes() for f in files}
            view = self.renderer.render(data, entry=str(version["manifest"].get("entry")),
                                        paths={path: f"{COPIES}/{path}" for path in data})
        except (OSError, ValueError, KeyError):
            # Bytes that vanished since the read, or inputs the renderer refuses: no document.
            return {"stamp": stamp, "written": False, "reasons": ["renderer_refused"]}
        kept = {"stamp": stamp, "written": PREVIEW in view.files and not NO_BODY.intersection(view.reasons),
                "reasons": list(view.reasons)}
        if folder.exists():
            shutil.rmtree(folder)
        if not kept["written"]:
            # No body: nothing of it (nor a copy of an authored HTML file) stays in the Workspace.
            return kept
        for path, content in {**view.files, **{f"{COPIES}/{p}": b for p, b in data.items()}}.items():
            target = folder / path
            if not target.resolve().is_relative_to(folder.resolve()):
                continue  # a path out of its folder is no view file (the renderer checks this too)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        core.write_text(folder / STAMP, json.dumps(kept, ensure_ascii=False, sort_keys=True))
        return kept


def _read_stamp(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _prune_views(root: Path, keep: set[str]) -> None:
    """Remove the view folders no version has now (`<page stem>/<revision>`), and a page stem's
    folder left empty; the pages (`*.html` beside them) are pruned by the build."""
    for stem in root.iterdir() if root.is_dir() else []:
        if not stem.is_dir():
            continue
        for revision in stem.iterdir():
            if revision.is_dir() and f"{stem.name}/{revision.name}" not in keep:
                shutil.rmtree(revision, ignore_errors=True)
        if not any(stem.iterdir()):
            stem.rmdir()
