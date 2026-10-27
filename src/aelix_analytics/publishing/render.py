"""A pure shared-model renderer. It receives no Project, source records, ledger or file access."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import posixpath
from urllib.parse import quote

from .model import Object, PublicationError, PublicationIdentity, Text, page_key

PAGE_SIZE = 200
LABELS = {
    "analysis_focus": "Analysis focus",
    "metric_definition": "Metric definition",
    "metric_observation": "Metric observation",
    "finding": "Finding",
    "decision": "Decision",
    "question": "Question",
    "todo": "Next check",
    "assumption": "Assumption",
    "note": "Note",
    "run": "Run",
    "visualization": "Visualization",
    "context": "Context",
    "context_history": "Earlier context",
    "dataset": "Dataset",
    "code": "Code",
    "data_observation": "Data observation",
    "report_revision": "Report version",
}
FIELD_LABELS = {
    "id": "Record",
    "text": "Text",
    "recorded_at": "Recorded",
    "started_at": "Started",
    "ended_at": "Ended",
    "actor_role": "Author role",
    "state": "State at this point",
    "state_history": "State history",
    "status": "Execution",
    "recording_status": "Recording",
    "recording_reasons": "Recording details",
    "title": "Title",
    "caption": "Caption",
    "details.metric_id": "Metric",
    "details.name": "Name",
    "details.definition_event_id": "Definition record",
    "details.definition": "Definition",
    "details.scope": "Scope",
    "details.result": "Result",
    "details.run_id": "Run",
    "details.change_reason": "Reason",
    "details.recomputed_from": "Recomputed from",
    "observation": "Checked when this copy was built",
}
REASONS = {
    "profile": "outside this profile",
    "file_absent": "file missing",
    "format": "file format not included",
    "scripts": "scripts are not allowed",
    "remote_resources": "remote resources are not allowed",
    "content_changed": "recorded bytes changed",
    "no_digest": "no recorded code hash",
    "outside_project": "outside the project",
    "invalid_digest": "invalid recorded hash",
}


# 7.6: with JavaScript on but publication.js missing, show the page's own "could not load" notice.
RUNTIME_CHECK = (
    'window.addEventListener("load",function(){if(!window.AnalyticsRuntime){'
    'var n=document.querySelector(".load-failure");if(n){n.style.display="block";}}});'
)
# Owner 2026-10-27 after measuring Chromium, Firefox and WebKit over file:// and HTTP (7.4): the pages
# load only this copy's files, make no connection and run only the kit's scripts. plotly writes styles.
PAGE_POLICY = (
    "default-src 'self'; script-src 'self' 'sha256-"
    + base64.b64encode(hashlib.sha256(RUNTIME_CHECK.encode()).digest()).decode()
    + "'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; connect-src 'none'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'"
)
# Checked user HTML may load its own files in its frame but never runs scripts (owner 2026-10-27).
SANDBOX = 'sandbox="allow-same-origin"'


def esc(value):
    return html.escape(str(value), quote=True).replace("\r", "&#13;")


key_name = page_key


def _plain(value):
    if isinstance(value, Text):
        return value.value
    if isinstance(value, Object):
        return {k.value: _plain(v) for k, v in value.entries}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    return value


def _shown(value):
    value = _plain(value)
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    )


def render(
    model,
    *,
    profile="read",
    rules_version=1,
    context_status="recorded",
    file_sizes=(),
    identity=None,
    scope="project",
):
    if scope not in ("project", "reports", "collection"):
        raise PublicationError("package_check_failed", "unknown publication scope")
    if context_status not in ("pending", "recorded", "not_recorded"):
        raise PublicationError("package_check_failed", "unknown context status")
    if type(rules_version) is not int or rules_version < 1:
        raise PublicationError("package_check_failed", "unknown projection rules version")
    if identity is not None and (
        not isinstance(identity, PublicationIdentity)
        or not isinstance(identity.publication_id, str)
        or not isinstance(identity.built_at, str)
        or type(identity.as_of_seq) is not int
        or identity.as_of_seq < 0
    ):
        raise PublicationError("package_check_failed", "unknown copy identity")
    sizes = dict(file_sizes)
    points = dict(model.facts.points)
    pages = {}
    targets = {}
    anchors = {}
    events = []
    for item in model.items:
        token = key_name(item.key)
        if token in anchors and anchors[token] != item.key:
            raise PublicationError("package_check_failed", "item page hash collision")
        anchors[token] = item.key
        if item.key.category == "event":
            events.append(item)
            targets[item.key] = f"pages/timeline/{(len(events) - 1) // PAGE_SIZE + 1}.html#i{token}"
        elif item.key.category == "project":
            targets[item.key] = "index.html"
        elif item.key.category == "report":
            targets[item.key] = f"reports/{token}/index.html"
        else:
            folder = {
                "run": "runs",
                "dataset": "data",
                "visualization": "figures",
                "context": "context",
                "code": "code",
                "data_observation": "observations",
            }.get(item.key.category, "items")
            targets[item.key] = f"pages/{folder}/{token}.html"

    def relative(path, target):
        file, sep, fragment = target.partition("#")
        value = posixpath.relpath(file, posixpath.dirname(path) or ".")
        return value + ("#" + fragment if sep else "")

    def href(path, target):
        # A Report file keeps its authored name, which may hold "#", "%" or spaces (7.1).
        return quote(posixpath.relpath(target, posixpath.dirname(path) or "."), safe="/")

    def link(path, target, label):
        return f'<a href="{esc(relative(path, target))}">{label}</a>'

    def user(value):
        language = "en" if isinstance(value, Text) and value.origin == "kit" else ""
        return f'<span lang="{language}">' + esc(_shown(value)) + "</span>"

    def title(item):
        return next(
            (
                user(f.value)
                for f in item.fields
                if f.name in ("title", "details.name", "text", "name")
            ),
            esc(LABELS.get(item.kind, "Included item")),
        )

    def fields(item):
        return {f.name: f.value for f in item.fields}

    def withdrawn(item):
        state = fields(item).get("state")
        return isinstance(state, Text) and state.value == "withdrawn"

    report_items = [i for i in model.items if i.key.category == "report"]
    # A collection copy names its collection in the band; a Report copy names each selected version.
    headline_reports = (
        []
        if scope == "project"
        else [r for r in report_items if "collection.members" in fields(r)] or report_items
        if scope == "collection"
        else report_items
    )

    def band(path):
        basis = model.basis
        recorded = (
            f"Records up to {esc(basis.content_recorded_at)} (e{basis.content_seq})"
            if basis.content_recorded_at
            else f"Records up to e{basis.content_seq} (recorded time unknown)"
        )
        context = (
            "Context: pending preview — not recorded"
            if context_status == "pending"
            else "Context version: " + esc(basis.context_version)
            + ("" if any(i.key.category == "context" and i.key.identity == basis.context_version
                         for i in model.items) else " (not included in this publication)")
            if basis.context_version
            else "Context: not recorded at this point"
        )
        reports = "".join(
            f"<p>Report {title(r)} · Version {esc(fields(r).get('number'))} · saved "
            f"{user(fields(r)['created_at']) if 'created_at' in fields(r) else 'at an unknown time'}"
            + (f" · based on records up to e{esc(fields(r)['based_on.as_of_seq'])}" if "based_on.as_of_seq" in fields(r) else "")
            + (" · Withdrawn" if withdrawn(r) else "") + "</p>"
            for r in headline_reports)
        return (
            f'<div class="band"><p>Shared snapshot — a fixed copy that does not update</p><p>{user(model.title)}</p>'
            f"<p>{recorded}</p><p>{context}</p>{reports}<p>Analysis: {'complete' if model.analysis_state == 'complete' else 'in progress'} (declared when built)</p>"
            f"<p>Scope: {esc(profile)} profile · {sum(o.count for o in model.not_included)} items not included · "
            f"{link(path, 'index.html#scope', 'Scope and limits')}</p></div>"
        )

    has_plotly = any(f.format == "payload" for i in model.items for f in i.files)

    def page(path, name, body, *, user_title=False):
        scripts = (
            f'<script src="{esc(relative(path, "assets/plotly.min.js"))}" defer></script>'
            if has_plotly
            else ""
        )
        root = posixpath.relpath(".", posixpath.dirname(path) or ".")
        value = (
            f'<!doctype html>\n<html lang="en" data-root="{esc(root)}/"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{esc(PAGE_POLICY)}"><link rel="icon" href="data:,">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="referrer" content="no-referrer"><meta name="robots" content="noindex"><title lang="{"" if user_title else "en"}">{esc(name)}</title>'
            f"<script>{RUNTIME_CHECK}</script>"
            f'<link rel="stylesheet" href="{esc(relative(path, "assets/publication.css"))}">{scripts}'
            f'<script src="{esc(relative(path, "assets/publication.js"))}" defer></script></head><body>'
            f'<a class="skip" href="#main">Skip to content</a><p class="load-failure">This page could not load its files. '
            f"If you opened it from inside a ZIP archive, extract the whole archive first; otherwise this copy is incomplete.</p>"
            f"<header>{band(path)}<nav>{link(path, 'index.html', 'Overview')} {link(path, 'about.html', 'About this copy')}</nav></header>"
            f'<main id="main">{body}</main><footer>This copy is a fixed snapshot. Its source may have changed.</footer></body></html>\n'
        )
        pages[path] = value.encode("utf-8")

    def card(item, path):
        body = f'<article class="card" id="i{key_name(item.key)}"><h2>{esc(LABELS.get(item.kind, "Included item"))}</h2>'
        if item.key.category == "context":
            text = next((f.value.value for f in item.fields if f.name == "text"), None)
            if text is not None:
                from markdown_it import MarkdownIt

                body += (
                    '<div lang="">'
                    + MarkdownIt("commonmark", {"html": False}).render(text)
                    + "</div>"
                )
        body += "<dl>"
        for field in item.fields:
            if item.key.category == "context" and field.name == "text":
                continue
            name = FIELD_LABELS.get(
                field.name,
                field.name.removeprefix("details.")
                .replace("_", " ")
                .replace(".", " ")
                .capitalize(),
            )
            language = "en" if isinstance(field.value, Text) and field.value.origin == "kit" else ""
            body += f'<dt>{esc(name)}</dt><dd><pre lang="{language}">{esc(_shown(field.value))}</pre></dd>'
        body += "</dl>" + references(item, path) + attachments(item, path)
        return body + files_html(item, path) + "</article>"

    def attachments(item, path):
        labels = {"run": "Made by run", "code": "Code snapshot", "data_observation": "Input or output"}
        return "".join(
            f"<p>{labels.get(key.category, 'Attached')}: "
            f"{link(path, targets[key], title(next(i for i in model.items if i.key == key)))}</p>"
            for key in item.attachments if key in targets)

    def references(item, path, *, skip=()):
        body = ""
        for ref in item.references:
            if ref.relation in skip:
                continue
            target = (
                link(
                    path,
                    targets[ref.target],
                    title(next(i for i in model.items if i.key == ref.target)),
                )
                if ref.target in targets
                else esc(ref.category)
            )
            # Original states and binding/confirmed remain separate visible facts.
            body += f"<p>{esc(ref.relation or 'Reference')}: {target} · {esc(ref.state)}"
            if ref.reason:
                body += " · " + esc(ref.reason)
            body += (
                " · "
                + (
                    "Confirmed link"
                    if ref.confirmed
                    else "Inferred link"
                    if ref.binding == "inferred"
                    else "Unconfirmed link"
                )
                + "</p>"
            )
            if ref.candidates:
                body += (
                    "<ul>"
                    + "".join(
                        "<li>" + link(path, targets[k], "Possible included version") + "</li>"
                        for k in ref.candidates
                    )
                    + "</ul>"
                )
            if ref.omitted_candidates:
                body += f"<p>{ref.omitted_candidates} other candidates are not included.</p>"
        return body

    def files_html(item, path):
        body = ""
        for file in item.files:
            target = relative(path, file.package_path)
            url = esc(target)
            if file.role == "figure":
                if file.format == "html":
                    body += f'<iframe src="{url}" {SANDBOX} title="Included figure"></iframe><p>If this frame is empty, its file is missing from this copy: {esc(file.package_path)}</p>'
                elif file.format == "payload":
                    key = Pure_name(file.package_path)
                    size = sizes.get(file.package_path, 0)
                    button = (
                        f'<button type="button">Load figure ({size / 1048576:.1f} MiB)</button>'
                        if size > 3000000
                        else ""
                    )
                    body += f'<div role="img" aria-label="Interactive figure" data-figure="{key}" data-payload="{url}"><p>Interactive figure — needs JavaScript</p>{button}</div><p>Interactive figures include the data they plot ({points.get(file.package_path, 0)} points).</p>'
                else:
                    body += f'<img src="{url}" alt="Included visualization" loading="lazy">'
            else:
                body += f'<p><a href="{url}" download>{"Data attached by the script" if file.role == "attached_data" else "Code snapshot"}</a></p>'
            body += (
                '<p class="muted">'
                + (
                    "Compared with the recorded hash"
                    if file.verified == "recorded_hash"
                    else "No hash was recorded; checked when this copy was built"
                )
                + "</p>"
            )
        for notice in item.file_notices:
            body += (
                "<p>Not included: "
                + esc(REASONS.get(notice.reason, notice.reason.replace("_", " ")))
                + "</p>"
            )
        return body

    views = {key: (view, shown, reasons) for key, view, shown, reasons in model.facts.report_views}

    def report_page(item, path):
        """A Report version (7.3, 7.5): its basis apart from this copy's, its body only as a separate
        document (an iframe without scripts) and its original files to download."""
        token, values = key_name(item.key), fields(item)
        body = f'<article class="card report" id="i{token}"><h1>{title(item)}</h1>'
        number, total = values.get("number"), values.get("total")
        body += f"<p>Version {esc(number)} of {esc(total)}</p>"
        report_id = _plain(values.get("report_id"))
        shared = {fields(o).get("number") for o in report_items if _plain(fields(o).get("report_id")) == report_id}
        if type(number) is int and type(total) is int and any(n not in shared for n in range(number + 1, total + 1)):
            body += "<p>A later version existed when this copy was built — not included.</p>"
        if withdrawn(item):
            body += "<p><strong>Withdrawn</strong> — this version was withdrawn when this copy was built.</p>"
        body += "<dl>"
        for name, label in (("created_at", "Created"), ("actor_role", "Author role"), ("state", "State at this point"),
                            ("report_id", "Report"), ("revision_id", "Version identity"), ("entry", "Entry file"),
                            ("summary", "Summary"), ("purpose", "Purpose"), ("change_reason", "Reason for this version"),
                            ("content_status", "Content status (as the author declared)")):
            if name in values:
                body += f"<dt>{label}</dt><dd>{user(values[name])}</dd>"
        if "based_on.as_of_seq" in values:
            body += f"<dt>Based on</dt><dd>Records up to e{esc(values['based_on.as_of_seq'])}</dd>"
        if "based_on.context_version" in values:
            version = values["based_on.context_version"]
            key = next((i.key for i in model.items if i.key.category == "context"
                        and i.key.identity == _plain(version)), None)
            shown = (link(path, targets[key], user(version)) if key
                     else user(version) + " (not included in this publication)")
            body += f"<dt>Based on CONTEXT version</dt><dd>{shown}</dd>"
        body += "</dl><p>A saved Report is not a review of its conclusions.</p>"
        members = values.get("collection.members")
        if members is not None:
            rows = [_plain(m) for m in members]
            counts = {s: sum(r.get("status") == s for r in rows) for s in ("complete", "failed", "not_generated")}
            body += (f"<h2>Collection</h2><p>{counts['complete']} of {len(rows)} complete · {counts['failed']} failed · "
                     f"{counts['not_generated']} not generated</p><table><thead><tr><th>Member</th><th>Status</th>"
                     "<th>Reason</th><th>Version</th></tr></thead><tbody>")
            for row, member in zip(rows, members, strict=True):
                target = next((o for o in report_items
                               if _plain(fields(o).get("report_id")) == row.get("report_id")
                               and _plain(fields(o).get("revision_id")) == row.get("revision_id")), None)
                cell = (link(path, targets[target.key], "Open member version") if target and "report_id" in row
                        else "Not included in this publication")
                entries = {k.value: v for k, v in member.entries}
                body += ("<tr>" + "".join(f"<td>{user(entries[k]) if k in entries else ''}</td>"
                                          for k in ("member_key", "status", "reason")) + f"<td>{cell}</td></tr>")
            body += "</tbody></table><p>Membership is the collection's structure, not evidence for its text.</p>"
        refs = references(item, path, skip=("includes",) if members is not None else ())
        body += "<h2>Evidence</h2>" + (refs or "<p>This version does not point to any evidence (no links).</p>")
        entry = _plain(values.get("entry"))
        folder = f"reports/{token}/content/"
        entry_slot = next((f for f in item.files if f.package_path == folder + str(entry)), None)
        view, shown, reasons = views.get(token, ("", False, ()))
        body += "<h2>Report body</h2>"
        if entry_slot is not None and entry_slot.format == "html":
            body += (f'<iframe src="{esc(href(path, entry_slot.package_path))}" {SANDBOX} title="Report body"></iframe>'
                     "<p>If this frame is empty, its file is missing from this copy: "
                     f'<span lang="">{esc(entry_slot.package_path)}</span></p>')
        elif entry_slot is not None and entry_slot.format in ("svg", "png", "jpeg", "gif", "webp"):
            body += f'<img src="{esc(href(path, entry_slot.package_path))}" alt="Report body" loading="lazy">'
        elif view:
            body += (f'<iframe src="{esc(relative(path, view))}" {SANDBOX} title="Report body"></iframe>'
                     f"<p>If this frame is empty, its file is missing from this copy: {esc(view)}</p>"
                     f'<p>{link(path, view, "Open the report body on its own page")}</p>')
            if not shown:
                body += ("<p>Parts of this body are offered as downloads only: "
                         + esc(", ".join(r.replace("_", " ") for r in reasons)) + ".</p>")
        else:
            body += "<p>This body is not displayed in this copy. Download its files below.</p>"
        body += "<h2>Files</h2><ul>"
        for slot in item.files:
            name = slot.package_path[len(folder):]
            body += (f'<li><a href="{esc(href(path, slot.package_path))}" download><span lang="">{esc(name)}</span></a>'
                     f" ({sizes.get(slot.package_path, 0)} bytes)</li>")
        return body + "</ul><p>Compared with the hashes the Report recorded.</p></article>"

    # A page for each included exact target; only included keys have page names.
    for item in model.items:
        if item.key.category == "report":
            path = targets[item.key]
            title_text = _plain(fields(item).get("title"))
            page(path, title_text or "Report version", report_page(item, path), user_title=bool(title_text))
        elif item.key.category not in ("event", "project"):
            path = targets[item.key]
            page(path, LABELS.get(item.kind, "Included item"), card(item, path))
    for n in range(0, len(events), PAGE_SIZE):
        path = f"pages/timeline/{n // PAGE_SIZE + 1}.html"
        navigation = (
            "<nav>"
            + "".join(
                link(path, f"pages/timeline/{k}.html", f"Records page{k}")
                for k in range(1, (len(events) - 1) // PAGE_SIZE + 2)
            )
            + "</nav>"
        )
        page(
            path,
            "Included records",
            navigation + "".join(card(item, path) for item in events[n : n + PAGE_SIZE]),
        )
    path = "index.html"
    body = "<h1>" + user(model.title) + "</h1><p>Included analysis and evidence at this point.</p>"
    order = {
        "context": 0,
        "analysis_focus": 1,
        "metric_definition": 2,
        "question": 3,
        "finding": 4,
        "decision": 5,
        "todo": 6,
        # A Report copy shows its selection first; a project copy lists them after the analysis (7.3).
        "report_revision": -1 if scope != "project" else 7,
        "visualization": 8,
    }
    for item in sorted(
        (i for i in model.items if i.key.category != "project"), key=lambda i: order.get(i.kind, 9)
    ):
        body += (
            "<p>"
            + esc(LABELS.get(item.kind, "Included item"))
            + ": "
            + link(path, targets[item.key], title(item))
            + (" · Withdrawn" if item.key.category == "report" and withdrawn(item) else "")
            + "</p>"
        )
    body += (
        '<section id="scope"><h2>Scope and limits</h2><p>Profile: '
        + esc(profile)
        + f". Rules version {rules_version}.</p>"
    )
    body += (
        "<p>Code and data are not included in this publication; it cannot be re-run from this copy.</p>"
        if not any(
            f.role in ("code_snapshot", "attached_data") for item in model.items for f in item.files
        )
        else "<p>Input data is not included.</p>"
    )
    body += "<table><thead><tr><th>Category</th><th>Reason not included</th><th>Count</th></tr></thead><tbody>"
    for omission in (*model.not_included, *model.diagnostics):
        body += f"<tr><td>{esc(omission.category)}</td><td>{esc(omission.reason.replace('_', ' '))}</td><td>{omission.count}</td></tr>"
    body += "</tbody></table><p>Missing, content changed, unknown, unsupported, unbound and not included are distinct states.</p>"
    body += f"<p>Files without a recorded hash: {model.facts.no_recorded_hash}.</p>"
    for status, count in model.facts.inspection:
        body += f"<p>{esc(status.replace('_', ' '))}: {count} files.</p>"
    body += "<p>Resource counts exclude publication.json, which is checked separately when a copy is saved.</p>"
    for notice in model.facts.notices:
        labels = {
            "path_pattern": "General path pattern warning",
            "excluded_field_line": "An excluded diagnostic line also appears in included content",
            "image_metadata": {
                "gps": "GPS metadata",
                "author": "Author/software metadata",
                "serial": "Device serial metadata",
                "other": "Image metadata",
            }[notice.metadata_kind],
        }
        body += f"<p>{esc(labels[notice.code])}: {esc(notice.path)}</p>"
    body += "<p>"
    body += "Checks do not detect paraphrases, arbitrary encodings in text, text in image pixels, or secrets in included content.</p></section>"
    page(path, model.title.value, body, user_title=model.title.origin == "user")
    about = "<h1>About this copy</h1>"
    metadata = None
    if identity is None:
        about += "<p>Preview only. No publication has been saved or recorded.</p>"
    else:
        metadata = {
            "publication_id": identity.publication_id,
            "built_at": identity.built_at,
            "as_of_seq": identity.as_of_seq,
        }
        about += (
            f"<dl><dt>Copy identity</dt><dd>{esc(identity.publication_id)}</dd>"
            f"<dt>Built at</dt><dd>{esc(identity.built_at)}</dd>"
            f"<dt>Records through</dt><dd>e{identity.as_of_seq}</dd></dl>"
        )
    page(
        "about.html",
        "About this copy",
        about
        + body[body.index('<section id="scope">') :]
        + '<script src="assets/identity.js" defer></script>',
    )
    payload = json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).translate(
        {
            ord("<"): "\\u003c",
            ord(">"): "\\u003e",
            ord("&"): "\\u0026",
            0x2028: "\\u2028",
            0x2029: "\\u2029",
        }
    )
    # Null in a preview invents neither an ID nor a timestamp. Keeping the resource in both
    # modes makes content-file bytes independent of whether a fresh identity was allocated.
    pages["assets/identity.js"] = f"window.AnalyticsPublication = {payload};\n".encode()
    pages["README.txt"] = (
        b"Open index.html after copying the whole folder. Extract the whole ZIP first. This is a fixed snapshot and does not update.\n"
    )
    return pages


def Pure_name(path):
    return path.rsplit("/", 1)[-1].split(".")[0]
