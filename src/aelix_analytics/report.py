"""Static HTML report generated from the project files (never edited by hand).

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
import hashlib
import html
import json
import os
import shutil
from collections import Counter, defaultdict
from importlib import resources
from pathlib import Path
from urllib.parse import quote

from . import _platform, core

ASSETS = resources.files(__package__) / "assets"
PLOTLY_EMBED_LIMIT = 3_000_000
LOG_LIMIT = 200_000
# A run over hundreds of files would otherwise bury the timeline and its own page in paths.
REF_LIMIT = 12
FILE_ROW_LIMIT = 60
KIND_LABEL = {"project": "프로젝트", "import": "가져오기", "run": "실행", "context": "맥락",
              "finding": "발견", "decision": "결정", "assumption": "가정", "question": "질문",
              "todo": "할 일", "note": "메모", "gap": "gap", "resolve": "해결"}
STATUS = {"ok": ("good", "✓", "성공"), "error": ("critical", "✕", "오류"),
          "timeout": ("serious", "⏱", "시간 초과"), "cancelled": ("warning", "■", "취소"),
          "incomplete": ("muted", "…", "미완료"), "running": ("muted", "…", "실행 중")}
INPUT_STATE = {"same": ("good", "✓", "같음"), "changed": ("critical", "⚠", "변경됨"),
               "missing": ("critical", "⚠", "없음"), "unknown": ("muted", "?", "확인 불가")}


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def href(path: str) -> str:
    return quote(path, safe="/#:?=&%.-_~")


def short(sha, n=8) -> str:
    return (sha or "")[:n]


def fmt_bytes(n) -> str:
    if not isinstance(n, (int, float)):
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
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


def kind_chip(kind) -> str:
    return (f'<span class="chip kind-{esc(kind)}"><span class="dot" aria-hidden="true"></span>'
            f'{esc(KIND_LABEL.get(kind, kind))}</span>')


def slug_key(key: str) -> str:
    """Anchor for a script::chart key: readable prefix plus a hash, so similar keys never collide."""
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    return f"{core.slugify(key.replace('::', '--'), 60) or 'chart'}-{digest}"


class Site:
    """Everything a page needs, loaded once per build."""

    def __init__(self, project: core.Project):
        self.p = project
        self.events = core.read_events(project)
        self.runs = core.list_runs(project)
        self.run_by_id = {r["id"]: r for r in self.runs}
        self.cache = core.HashCache(project)
        self.fresh = core.Freshness(project, self.runs, self.cache)
        self.datasets = core.list_datasets(project, self.runs)
        self.views = core.view_names(self.datasets)
        self.view_by_path = {path: name for name, path in self.views.items()}
        self.charts = core.chart_index(self.runs)
        self.resolved = core.resolved_targets(self.events)
        self.telemetry = core.read_jsonl(project.telemetry)
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
        return f"{self.root(depth)}index.html#chart-{slug_key(key)}"

    def path_html(self, path: str, depth: int) -> str:
        link = self.data_link(path, depth)
        label = self.view_by_path.get(path) or path
        if link:
            return f'<a href="{link}" title="{esc(path)}">{esc(label)}</a>'
        return f'<a href="{self.file_link(path, depth)}">{esc(path)}</a>'

    def refs_html(self, refs: dict, depth: int) -> str:
        items = []
        for run_id in refs.get("runs") or []:
            if run_id in self.run_by_id:
                items.append(f'<a class="ref" href="{self.run_link(run_id, depth)}">run {esc(run_id)}</a>')
            else:
                items.append(f'<span class="ref">run {esc(run_id)}</span>')
        for key in (refs.get("charts") or [])[:REF_LIMIT]:
            items.append(f'<a class="ref" href="{self.chart_anchor(key, depth)}">chart '
                         f'{esc(key.split("::")[-1])}</a>')
        data = refs.get("data") or []
        for item in data[:REF_LIMIT]:
            path = item if item in self.view_by_path else self.views.get(item)
            items.append(f'<span class="ref">{self.path_html(path, depth)}</span>' if path
                         else f'<span class="ref">{esc(item)}</span>')
        hidden = max(0, len(data) - REF_LIMIT) + max(0, len(refs.get("charts") or []) - REF_LIMIT)
        if hidden:
            items.append(f'<span class="ref muted" title="전체 목록은 run 페이지에 있습니다">'
                         f'그 외 {hidden:,}개</span>')
        for ev in refs.get("events") or []:
            items.append(f'<a class="ref" href="{self.root(depth)}index.html#{esc(ev)}">{esc(ev)}</a>')
        if refs.get("resolves"):
            target = refs["resolves"]
            items.append(f'<a class="ref" href="{self.root(depth)}index.html#{esc(target)}">'
                         f'해결: {esc(target)}</a>')
        if refs.get("context"):
            items.append(f'<a class="ref" href="{self.root(depth)}context.html#v-'
                         f'{esc(Path(refs["context"]).stem)}">버전 보기</a>')
        for other in (refs.get("other") or [])[:REF_LIMIT]:
            items.append(f'<span class="ref">{esc(other)}</span>')
        return f'<div class="refs">{"".join(items)}</div>' if items else ""

    # --- charts ---

    def chart_html(self, chart: dict, depth: int, *, lazy=True) -> str:
        files = chart.get("files") or {}
        title = esc(chart.get("title") or chart.get("id"))
        if files.get("png") or any(k in files for k in ("jpg", "jpeg", "svg", "gif", "webp")):
            src = files.get("png") or next(files[k] for k in ("svg", "jpg", "jpeg", "gif", "webp")
                                           if k in files)
            loading = ' loading="lazy"' if lazy else ""
            return (f'<a class="chart-img" href="{self.file_link(src, depth)}">'
                    f'<img src="{self.file_link(src, depth)}" alt="{title}"{loading}></a>')
        if files.get("plotly"):
            path = self.p.path(files["plotly"])
            try:
                size = path.stat().st_size
            except OSError:
                return '<div class="empty">차트 파일이 없습니다.</div>'
            if size > PLOTLY_EMBED_LIMIT:
                return (f'<div class="empty">plotly 그림이 커서 ({fmt_bytes(size)}) 이 페이지에 넣지 '
                        f'않았습니다. <a href="{self.file_link(files["plotly"], depth)}">JSON 열기</a></div>')
            self.uses_plotly = True
            self._fig += 1
            payload = path.read_text(encoding="utf-8").replace("</", "<\\/")
            return (f'<div class="plotly-chart" data-fig="fig{self._fig}" role="img" '
                    f'aria-label="{title}"></div><script type="application/json" '
                    f'id="fig{self._fig}">{payload}</script>')
        if files.get("html"):
            return (f'<iframe class="chart-frame" src="{self.file_link(files["html"], depth)}" '
                    f'loading="lazy" title="{title}"></iframe>')
        return '<div class="empty">표시할 차트 파일이 없습니다.</div>'

    def chart_card(self, chart: dict, depth: int, *, full=False) -> str:
        run = self.run_by_id.get(chart["run"]) or {}
        fresh = self.fresh.of(chart["run"])
        script = run.get("script") or {}
        badges = []
        if fresh["stale"]:
            reasons = fresh["changed_inputs"] + [f"상위 run {u}" for u in fresh["stale_upstream"]]
            badges.append(badge("critical", "⚠", "입력 변경됨", "; ".join(reasons)))
        if fresh["code"] == "changed":
            badges.append(badge("warning", "✎", "스크립트 수정됨",
                                "이 차트를 만든 뒤 스크립트 파일이 바뀌었습니다"))
        elif fresh["code"] == "missing":
            badges.append(badge("warning", "✎", "스크립트 없음",
                                "이 차트를 만든 스크립트 파일이 지금은 없습니다(이름 변경·삭제)"))
        versions = chart.get("versions") or []
        if len(versions) > 1:
            badges.append(f'<span class="badge tone-muted">v{len(versions)}</span>')
        if chart.get("retired"):
            badges.append(badge("muted", "↺", "최신 run에 없음"))
        run_page = self.run_link(chart["run"], depth)
        line = chart.get("line")
        code_link = (f'<a href="{run_page}#code">{esc(script.get("path"))}</a> '
                     f'<span class="sha">@{short(script.get("sha256"))}</span>')
        if line:
            code_link += f' · <a href="{run_page}#L-main-{line}">L{line}</a>'
        inputs = []
        for entry in fresh["inputs"]:
            tone, icon, label = INPUT_STATE.get(entry["state"], INPUT_STATE["unknown"])
            inputs.append(f'<span class="input">{self.path_html(entry["path"], depth)} '
                          f'<span class="sha">{short(entry.get("sha256"))}</span>'
                          f'<span class="state tone-{tone}" title="현재 파일: {esc(label)}">{icon}</span></span>')
        if chart.get("data"):
            data_html = (f'<a href="{run_page}#chart-{esc(chart["id"])}-data">'
                         f'{fmt_num(chart.get("data_rows"))}행 · {len(chart.get("data_columns") or [])}열</a>'
                         f' · <a href="{self.file_link(chart["data"], depth)}">parquet</a>')
        elif chart.get("kind") == "plotly":
            data_html = '<span class="muted">plotly 그림 JSON에 포함</span>'
        else:
            data_html = '<span class="muted">기록 안 됨 — analytics.visualize(..., data=df)</span>'
        version_links = ""
        if full and len(versions) > 1:
            version_links = ('<div class="versions">이전 버전: ' + " · ".join(
                f'<a href="{self.run_link(v["run"], depth)}">{fmt_time(v.get("started_at"))}</a>'
                for v in versions[-2::-1][:8]) + "</div>")
        classes = "chart-card" + (" is-stale" if fresh["stale"] else "") + \
            (" is-retired" if chart.get("retired") else "")
        caption = f'<p class="caption">{esc(chart["caption"])}</p>' if chart.get("caption") else ""
        return f"""
<article class="{classes}" id="chart-{slug_key(chart['key'])}">
  <header><h3>{esc(chart.get('title') or chart['id'])}</h3><div class="badges">{''.join(badges)}</div></header>
  <div class="chart-body">{self.chart_html(chart, depth)}</div>
  {caption}
  <dl class="prov">
    <div><dt>코드</dt><dd>{code_link}</dd></div>
    <div><dt>입력 데이터</dt><dd>{''.join(inputs) or '<span class="muted">감지된 입력 없음</span>'}</dd></div>
    <div><dt>플롯 데이터</dt><dd>{data_html}</dd></div>
    <div><dt>실행</dt><dd><a href="{run_page}">{esc(chart['run'])}</a> · {fmt_time(run.get('started_at'))} · {run.get('duration_s', '-')}s</dd></div>
  </dl>
  {version_links}
</article>"""

    # --- tables ---

    def table(self, columns, rows, *, types=None, klass="data") -> str:
        head = "".join(
            f'<th>{esc(c)}{f"<small>{esc(types[i])}</small>" if types else ""}</th>'
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
                    cells.append(f"<td>{esc(v)}</td>")
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
            note = (f'<p class="muted">{fmt_num(total)}행 중 처음 {min(limit, total):,}행</p>'
                    if total > limit else "")
            return note + self.table(frame.columns, [[cell(v, 120) for v in r] for r in frame.rows()],
                                     types=[str(t) for t in schema.values()])
        return '<div class="empty">데이터를 읽지 못했습니다.</div>'

    # --- events ---

    def events_html(self, events, depth: int, *, filters=False) -> str:
        if not events:
            return '<div class="empty">아직 기록이 없습니다.</div>'
        out, day = [], None
        for ev in events:
            ts = str(ev.get("ts") or "")
            if ts[:10] != day:
                day = ts[:10]
                out.append(f'<li class="day">{esc(day)}</li>')
            resolved = self.resolved.get(ev.get("id"))
            state = ""
            if ev.get("kind") in core.OPEN_KINDS + core.KEY_KINDS and resolved:
                state = (f' <a class="badge tone-good" href="#{esc(resolved["id"])}">✓ 해결됨 '
                         f'{esc(resolved["id"])}</a>')
            elif ev.get("kind") in core.OPEN_KINDS:
                state = ' <span class="badge tone-warning">● 열림</span>'
            out.append(
                f'<li class="ev" id="{esc(ev.get("id"))}" data-kind="{esc(ev.get("kind"))}">'
                f'<div class="ev-head"><time>{fmt_time(ts, with_date=False)}</time>{kind_chip(ev.get("kind"))}'
                f'<span class="ev-id">{esc(ev.get("id"))}</span><span class="actor">{esc(ev.get("actor"))}</span>{state}</div>'
                f'<div class="ev-text">{esc(ev.get("text"))}</div>{self.refs_html(ev.get("refs") or {}, depth)}</li>')
        chips = ""
        if filters:
            counts = Counter(e.get("kind") for e in events)
            chips = '<div class="filters" role="group" aria-label="종류 필터"><button class="on" data-kind="">전체</button>' + "".join(
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
<html lang="ko" data-root="{root}" data-build="{self.build}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · {esc(self.p.title())}</title>
<link rel="stylesheet" href="{root}assets/report.css{v}">
<link rel="stylesheet" href="{root}assets/code.css{v}">
</head>
<body class="page-{kind}">
<header class="topbar">
  <div class="crumbs"><a href="{root}index.html">{esc(self.p.title())}</a>{crumb}</div>
  <div class="build">빌드 {fmt_time(self.built_at)} · <label><input type="checkbox" id="live" checked> 자동 새로고침</label></div>
</header>
<main>{body}</main>
<footer>Aelix Analytics {core.KIT_VERSION} · run 기록·journal·CONTEXT.md에서 생성된 페이지입니다. 직접 편집하지 마세요.</footer>
{plotly}
<script src="{root}assets/report.js{v}"></script>
</body>
</html>
"""


def fingerprint(project: core.Project) -> str:
    parts = []
    for path in (project.marker, project.context, project.journal, project.telemetry,
                 project.queries):
        with contextlib.suppress(OSError):
            st = path.stat()
            parts.append([path.name, st.st_size, st.st_mtime_ns])
    for base in (project.runs, project.data):
        if base.is_dir():
            for dirpath, _dirs, files in os.walk(base):
                for name in files:
                    if base == project.runs and name != "run.json":
                        continue
                    with contextlib.suppress(OSError):
                        st = os.stat(os.path.join(dirpath, name))
                        parts.append([os.path.join(dirpath, name), st.st_size, st.st_mtime_ns])
    from .runner import kit_sha

    parts.append(kit_sha())
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def build(project: core.Project, *, if_changed: bool = False) -> dict:
    """Build the report. Builds are serialized per project; a waiting --if-changed build rechecks
    the fingerprint after the lock, so queued triggers collapse into one build."""
    with project.platform.locks.exclusive(project.lab / "report.lock"):
        return _build_locked(project, if_changed)


def _build_locked(project: core.Project, if_changed: bool) -> dict:
    stamp_file = project.cache / "report-fingerprint"
    current = fingerprint(project)
    index = project.report / "index.html"
    if if_changed and index.is_file():
        with contextlib.suppress(OSError):
            if stamp_file.read_text(encoding="utf-8").strip() == current:
                return {"path": str(index), "skipped": True}
    site = Site(project)
    out = project.report
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
    (out / "assets" / "build.js").write_text(f'window.__LAB_BUILD__ = "{site.build}";\n',
                                             encoding="utf-8")
    site.cache.save()
    project.cache.mkdir(parents=True, exist_ok=True)
    stamp_file.write_text(current, encoding="utf-8")
    return {"path": str(index), "pages": pages, "build": site.build}


def _write(path: Path, text: str, platform: _platform.Platform) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    platform.files.replace(tmp, path)


def _prune(directory: Path, keep: set[str]) -> None:
    for p in directory.glob("*.html"):
        if p.name not in keep:
            with contextlib.suppress(OSError):
                p.unlink()


def _write_assets(site: Site, out: Path) -> None:
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for name in ("report.css", "report.js"):
        with resources.as_file(ASSETS / name) as source:
            shutil.copyfile(source, assets / name)
    code_css = assets / "code.css"
    with resources.as_file(ASSETS / "report.css") as css:
        stale = not code_css.is_file() or code_css.stat().st_mtime < css.stat().st_mtime
    if stale:
        with contextlib.suppress(Exception):
            from pygments.formatters import HtmlFormatter

            light = HtmlFormatter(style="default").get_style_defs(".code")
            dark = HtmlFormatter(style="github-dark").get_style_defs(".code")
            code_css.write_text(f"{light}\n@media (prefers-color-scheme: dark) {{\n{dark}\n}}\n",
                                encoding="utf-8")
    if any((c.get("files") or {}).get("plotly") for r in site.runs for c in r.get("charts") or []):
        with contextlib.suppress(Exception):
            src = resources.files("plotly") / "package_data" / "plotly.min.js"
            dest = assets / "plotly.min.js"
            data = src.read_bytes()
            if not dest.is_file() or dest.stat().st_size != len(data):
                dest.write_bytes(data)


# --- index ---

def index_page(site: Site) -> str:
    p = site.p
    st_goal = ""
    context_text = (p.context.read_text(encoding="utf-8", errors="replace")
                    if p.context.is_file() else "")
    goal = core.context_goal(context_text)
    if goal:
        st_goal = f'<p class="goal">{esc(goal)}</p>'
    current = [c for c in site.charts if not c.get("retired")]
    stale = [c for c in current if site.fresh.of(c["run"])["stale"]]
    failed = [r for r in site.runs if r.get("status") != "ok"]
    open_items = [e for e in site.events if e.get("kind") in core.OPEN_KINDS
                  and e["id"] not in site.resolved]
    key_items = [e for e in site.events if e.get("kind") in core.KEY_KINDS
                 and e["id"] not in site.resolved]
    gaps = [e for e in site.events if e.get("kind") == "gap"]

    def tile(label, value, note="", target=""):
        return (f'<a class="tile" href="#{target}"><span class="label">{esc(label)}</span>'
                f'<span class="value">{fmt_num(value)}</span><span class="note">{note}</span></a>')

    tiles = "".join([
        tile("데이터셋", len(site.datasets), f"raw {sum(d['kind'] == 'raw' for d in site.datasets)}"
             f" · derived {sum(d['kind'] == 'derived' for d in site.datasets)}", "data"),
        tile("실행", len(site.runs), f"성공 아님 {len(failed)}" if failed else "모두 성공", "runs"),
        tile("차트", len(current), f"⚠ 입력 변경 {len(stale)}" if stale else "최신", "charts"),
        tile("열린 질문·할 일", len(open_items), "", "overview-open"),
        tile("gap 기록", len(gaps), "평가용", "evaluation"),
    ])

    def item_list(items, empty):
        if not items:
            return f'<div class="empty">{empty}</div>'
        return '<ul class="items">' + "".join(
            f'<li id="key-{esc(e["id"])}">{kind_chip(e["kind"])} <span class="item-text">{esc(e["text"])}</span>'
            f'<span class="item-meta"><a href="#{esc(e["id"])}">{esc(e["id"])}</a> · {fmt_time(e.get("ts"))}</span>'
            f'{site.refs_html(e.get("refs") or {}, 0)}</li>' for e in reversed(items[-12:])) + "</ul>"

    unreadable = core.unreadable_records(p)
    skipped = ""
    if unreadable:
        detail = ", ".join(f"{schema} {count}건" for schema, count in sorted(unreadable.items()))
        skipped = ('<div class="callout tone-critical"><strong>이 리포트에서 빠진 기록이 있습니다.</strong> '
                   f'{esc(detail)} — 더 새로운 kit이 쓴 기록이라 이 버전은 읽지 못합니다. '
                   'kit을 업데이트한 뒤 리포트를 다시 만드세요.</div>')

    overview = f"""
<section id="overview" class="tab">
  <div class="hero"><h1>{esc(p.title())}</h1>{st_goal}<div class="where">프로젝트 <code>{esc(p.root)}</code></div></div>
  {skipped}
  <div class="tiles">{tiles}</div>
  <div class="columns">
    <div class="panel"><h2>핵심 정보</h2>{item_list(key_items, '발견·결정·가정이 기록되면 여기에 모입니다.')}</div>
    <div class="panel" id="overview-open"><h2>열린 질문 · 할 일</h2>{item_list(open_items, '열린 질문이 없습니다.')}</div>
  </div>
  <h2 class="section-title">최근 차트</h2>
  <div class="gallery">{''.join(site.chart_card(c, 0) for c in current[:4]) or '<div class="empty">아직 차트가 없습니다. analytics_run으로 스크립트를 실행하면 여기에 나타납니다.</div>'}</div>
  <h2 class="section-title">최근 기록</h2>
  {site.events_html(site.events[-8:][::-1], 0)}
</section>"""

    all_cards = "".join(site.chart_card(c, 0, full=True) for c in site.charts)
    charts = f"""
<section id="charts" class="tab">
  <div class="toolbar"><h2>차트</h2><label class="toggle"><input type="checkbox" id="show-retired"> 최신 run에서 빠진 차트도 보기</label></div>
  <p class="muted">스크립트::차트 id마다 가장 최근 성공 run의 버전입니다. 각 카드의 코드·입력 데이터·플롯 데이터 링크가 그 차트를 만든 기록을 가리킵니다.</p>
  <div class="gallery">{all_cards or '<div class="empty">아직 차트가 없습니다.</div>'}</div>
</section>"""

    timeline = f"""
<section id="timeline" class="tab">
  <div class="toolbar"><h2>타임라인</h2></div>
  {site.events_html(site.events[::-1], 0, filters=True)}
</section>"""

    data_rows = []
    for ds in site.datasets:
        view = site.view_by_path.get(ds["path"])
        if ds.get("producer"):
            origin = f'<a href="{site.run_link(ds["producer"], 0)}">{esc(ds["producer"])}</a>'
        elif ds.get("source"):
            origin = esc(Path(ds["source"]).name)
        else:
            origin = badge("critical", "⚠", "생성 run 없음")
        data_rows.append(
            f'<tr><td><a href="data/{href(view)}.html">{esc(view)}</a></td><td>{esc(ds["kind"])}</td>'
            f'<td class="num">{fmt_num(ds.get("rows"))}</td><td class="num">{fmt_num(ds.get("columns"))}</td>'
            f'<td class="num">{fmt_bytes(ds.get("bytes"))}</td><td><code>{esc(ds["path"])}</code></td>'
            f'<td>{origin}</td><td class="num">{len(ds.get("used_by") or [])}</td>'
            f'<td class="sha">{short(ds.get("sha256"), 12)}</td></tr>')
    data = f"""
<section id="data" class="tab">
  <div class="toolbar"><h2>데이터</h2></div>
  <p class="muted">data/raw는 가져온 원본(불변), data/derived는 run이 만든 Parquet입니다. 이름은 analytics_query·analytics.sql에서 쓰는 view 이름입니다.</p>
  {('<div class="table-wrap"><table class="list"><thead><tr><th>view</th><th>종류</th><th>행</th><th>열</th><th>크기</th><th>경로</th><th>출처 / 생성 run</th><th>사용 run</th><th>sha256</th></tr></thead><tbody>' + ''.join(data_rows) + '</tbody></table></div>') if data_rows else '<div class="empty">아직 데이터셋이 없습니다. analytics_import로 가져오세요.</div>'}
</section>"""

    run_rows = []
    for run in site.runs[::-1]:
        repro = run.get("reproduction") or {}
        verdict = ""
        if repro:
            tone = {"match": "good", "mismatch": "critical", "failed": "critical"}.get(
                repro.get("verdict"), "muted")
            verdict = badge(tone, "↻", f"재실행 {repro.get('verdict')}")
        run_rows.append(
            f'<tr><td>{fmt_time(run.get("started_at"))}</td><td><a href="runs/{href(run["id"])}.html">{esc(run["id"])}</a></td>'
            f'<td>{status_badge(run.get("status"))} {verdict}</td><td>{esc(run.get("title"))}</td>'
            f'<td class="num">{len(run.get("charts") or [])}</td><td class="num">{len(run.get("inputs") or [])}</td>'
            f'<td class="num">{sum(1 for o in run.get("outputs") or [] if o.get("change") != "deleted")}</td>'
            f'<td class="num">{run.get("duration_s", "-")}</td></tr>')
    runs = f"""
<section id="runs" class="tab">
  <div class="toolbar"><h2>실행</h2></div>
  {('<div class="table-wrap"><table class="list"><thead><tr><th>시작</th><th>run</th><th>상태</th><th>제목</th><th>차트</th><th>입력</th><th>출력</th><th>초</th></tr></thead><tbody>' + ''.join(run_rows) + '</tbody></table></div>') if run_rows else '<div class="empty">아직 실행 기록이 없습니다.</div>'}
</section>"""

    context_html = render_markdown(context_text) if context_text else \
        '<div class="empty">CONTEXT.md가 비어 있습니다.</div>'
    version_items = "".join(
        f'<li><a href="context.html#v-{esc(v.stem)}">{fmt_time(core._version_time(v))}</a></li>'
        for v in site.versions[::-1][:12])
    # The context block's own selection, from the file as aelix's read tool numbers its lines.
    _, injected, omitted, _ = core.context_selection(core.context_file_text(p))
    prompt_note = (
        f'<p class="muted">프롬프트에 매번 들어가는 섹션: {esc(", ".join(s["name"] for s in injected)) or "없음"}.'
        + (f' 제목과 줄 범위만 들어가는 섹션: {esc(", ".join(s["name"] for s in omitted))} '
           '— 에이전트가 필요할 때 읽어 갑니다.' if omitted else "") + "</p>")
    context = f"""
<section id="context" class="tab">
  <div class="toolbar"><h2>맥락 <small>CONTEXT.md</small></h2><a href="context.html">버전 기록 {len(site.versions)}개 →</a></div>
  {prompt_note}
  <div class="columns wide-left">
    <article class="panel markdown">{context_html}</article>
    <aside class="panel"><h3>최근 버전</h3><ul class="plain">{version_items or '<li class="muted">없음</li>'}</ul></aside>
  </div>
</section>"""

    return site.page(0, "분석 리포트", "", overview + charts + timeline + data + runs + context
                     + evaluation_section(site), "index")


def evaluation_section(site: Site) -> str:
    runs = site.runs
    statuses = Counter(r.get("status") for r in runs)
    no_input = [r for r in runs if r.get("status") == "ok" and not r.get("inputs")
                and (r.get("charts") or r.get("outputs"))]
    undocumented = [(r, c) for r in runs if r.get("status") == "ok" for c in r.get("charts") or []
                    if c.get("kind") == "matplotlib" and not c.get("data")]
    external = [(r, i) for r in runs for i in r.get("inputs") or [] if i.get("external")]
    outside = [(r, o) for r in runs for o in r.get("outputs") or [] if o.get("outside_data")]
    reruns = [r for r in runs if r.get("reproduction")]
    verdicts = Counter((r.get("reproduction") or {}).get("verdict") for r in reruns)
    stale = [c for c in site.charts if not c.get("retired") and site.fresh.of(c["run"])["stale"]]
    gaps = [e for e in site.events if e.get("kind") == "gap"]

    def row(label, value, detail=""):
        return f"<tr><th>{esc(label)}</th><td class='num'>{value}</td><td>{detail}</td></tr>"

    def run_list(items):
        return ", ".join(f'<a href="runs/{href(r["id"])}.html">{esc(r["id"])}</a>' for r in items[:6])

    signals = "".join([
        row("run 상태", len(runs), " · ".join(f"{esc(STATUS.get(k, (0, 0, k))[2])} {v}"
                                             for k, v in statuses.items())),
        row("입력이 감지되지 않은 run", len(no_input), run_list(no_input)),
        row("플롯 데이터 없는 matplotlib 차트", len(undocumented),
            ", ".join(f'<a href="runs/{href(r["id"])}.html">{esc(c["id"])}</a>' for r, c in undocumented[:6])),
        row("프로젝트 밖 입력 파일", len(external), ", ".join(esc(i["path"]) for _, i in external[:4])),
        row("data/ 밖에 쓴 파일", len(outside), ", ".join(esc(o["path"]) for _, o in outside[:4])),
        row("입력 변경으로 stale인 차트", len(stale), ", ".join(esc(c["key"]) for c in stale[:4])),
        row("재실행 검증", len(reruns), " · ".join(f"{esc(k)} {v}" for k, v in verdicts.items())),
        row("analytics_query 조회", len(site.queries),
            f"오류 {sum(1 for q in site.queries if q.get('error'))}"),
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
        f"{''.join(badge('warning', '⚑', f) for f in t['flags'])} <span class='muted'>{esc(t.get('summary'))}</span></li>"
        for t in flagged[::-1][:30])
    gap_html = site.events_html(gaps[::-1], 0) if gaps else \
        '<div class="empty">아직 gap 기록이 없습니다. 사용하다 막히거나 우회가 필요했던 지점을 /analytics record gap 또는 analytics_record(kind="gap")로 남기세요.</div>'
    telemetry_html = (f'<div class="table-wrap"><table class="list"><thead><tr><th>도구</th><th>호출</th>'
                      f'<th>오류</th><th>평균 ms</th></tr></thead><tbody>{tool_rows}</tbody></table></div>'
                      if tool_rows else '<div class="empty">aelix 확장이 기록한 도구 호출이 없습니다.</div>')
    return f"""
<section id="evaluation" class="tab">
  <div class="toolbar"><h2>평가</h2></div>
  <p class="muted">분석을 진행하며 드러난 부족한 점과 우회가 필요했던 지점을 모읍니다.</p>
  <div class="columns">
    <div class="panel"><h3>gap 기록 ({len(gaps)})</h3>{gap_html}</div>
    <div class="panel"><h3>기록 품질 신호</h3><div class="table-wrap"><table class="kv">{signals}</table></div></div>
  </div>
  <div class="columns">
    <div class="panel"><h3>에이전트 도구 사용</h3>{telemetry_html}</div>
    <div class="panel"><h3>우회 의심 호출 ({len(flagged)})</h3>{('<ul class="plain flagged">' + flag_items + '</ul>') if flag_items else '<div class="empty">없음</div>'}</div>
  </div>
</section>"""


# --- run page ---

def code_html(source: str, anchor: str, hl_lines=()) -> str:
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import PythonLexer

        formatter = HtmlFormatter(linenos="table", anchorlinenos=True, lineanchors=f"L-{anchor}",
                                  hl_lines=sorted({n for n in hl_lines if n}), cssclass="code",
                                  wrapcode=True)
        return highlight(source, PythonLexer(), formatter)
    except Exception:  # noqa: BLE001 - fall back to plain text
        lines = source.splitlines()
        return '<div class="code"><pre>' + "\n".join(
            f'<a id="L-{anchor}-{i}" href="#L-{anchor}-{i}" class="ln">{i:>4}</a>  {esc(line)}'
            for i, line in enumerate(lines, 1)) + "</pre></div>"


def run_page(site: Site, run: dict) -> str:
    p = site.p
    rid = run["id"]
    script = run.get("script") or {}
    fresh = site.fresh.of(rid)
    same_script = [r for r in site.runs if (r.get("script") or {}).get("path") == script.get("path")]
    pos = next((i for i, r in enumerate(same_script) if r["id"] == rid), 0)
    nav = []
    if pos > 0:
        nav.append(f'<a href="{href(same_script[pos - 1]["id"])}.html">← 이전 run</a>')
    if pos < len(same_script) - 1:
        nav.append(f'<a href="{href(same_script[pos + 1]["id"])}.html">다음 run →</a>')
    sub = [f"run <code>{esc(rid)}</code>", fmt_time(run.get("started_at")),
           f"{run.get('duration_s', '-')}초", f"actor {esc(run.get('actor') or '-')}"]
    if run.get("session"):
        sub.append(f'<span title="{esc(run["session"])}">aelix 세션 {esc(Path(run["session"]).name)}</span>')
    head = f"""
<div class="page-head">
  <div class="title-row"><h1>{esc(run.get('title'))}</h1>{status_badge(run.get('status'))}</div>
  <div class="sub">{' · '.join(sub)}</div>
  <div class="sub"><code>{esc(script.get('path'))}</code> 에서 실행 · 같은 스크립트의 run {pos + 1}/{len(same_script)} {' '.join(nav)}</div>
</div>"""
    callouts = ""
    if run.get("error"):
        err = run["error"]
        where = f' (line <a href="#L-main-{err["line"]}">{err["line"]}</a>)' if err.get("line") else ""
        fix = f'<div class="fix">고치는 법: {esc(err["hint"])}</div>' if err.get("hint") else ""
        callouts += (f'<div class="callout tone-critical"><strong>{esc(err.get("summary"))}</strong>'
                     f'{where}{fix}</div>')
    repro = run.get("reproduction")
    if repro:
        tone = {"match": "good", "mismatch": "critical", "failed": "critical"}.get(
            repro.get("verdict"), "muted")
        details = "".join(f"<li><code>{esc(k)}</code>: {esc(v)}</li>"
                          for k, v in {**repro.get("charts", {}), **repro.get("outputs", {})}.items())
        callouts += (f'<div class="callout tone-{tone}"><strong>재실행 검증: {esc(repro.get("verdict"))}</strong> — '
                     f'원본 <a href="{href(repro.get("of", ""))}.html">{esc(repro.get("of"))}</a>의 코드 스냅샷을 다시 실행했습니다. '
                     f'입력 {"같음" if repro.get("inputs_same") else "다름"}. 재실행 run은 차트 버전에 포함되지 않습니다.<ul>{details}</ul></div>')
    if run.get("finalize_errors"):
        callouts += '<div class="callout tone-critical"><strong>기록 중 일부 단계가 실패했습니다</strong><ul>' + "".join(
            f"<li>{esc(w)}</li>" for w in run["finalize_errors"]) + "</ul></div>"
    if run.get("warnings"):
        # Runs recorded before warnings were grouped can carry one line per input file.
        shown = run["warnings"][:REF_LIMIT]
        rest = len(run["warnings"]) - len(shown)
        callouts += ('<div class="callout tone-warning"><strong>기록 경고</strong><ul>'
                     + "".join(f"<li>{esc(w)}</li>" for w in shown)
                     + (f"<li class='muted'>그 외 {rest:,}건</li>" if rest > 0 else "")
                     + "</ul></div>")
    if fresh["stale"]:
        callouts += ('<div class="callout tone-critical"><strong>이 run의 입력이 지금 파일과 다릅니다.</strong> '
                     + esc(", ".join(fresh["changed_inputs"] + [f"상위 run {u}" for u in fresh["stale_upstream"]]))
                     + "</div>")

    charts = []
    for chart in run.get("charts") or []:
        key = core.chart_key(run, chart)
        data_block = ""
        if chart.get("data"):
            data_block = (f'<details class="data-block" id="chart-{esc(chart["id"])}-data"><summary>플롯 데이터 · '
                          f'{fmt_num(chart.get("data_rows"))}행 · <a href="{site.file_link(chart["data"], 1)}">parquet</a></summary>'
                          f'{site.frame_preview(chart["data"])}</details>')
        line = f' · 코드 <a href="#L-main-{chart["line"]}">L{chart["line"]}</a>' if chart.get("line") else ""
        charts.append(f"""
<article class="chart-card wide" id="chart-{esc(chart['id'])}">
  <header><h3>{esc(chart.get('title'))}</h3><div class="badges"><span class="badge tone-muted">{esc(chart.get('kind'))} · {esc(chart.get('via'))}</span></div></header>
  <div class="chart-body">{site.chart_html(chart, 1, lazy=False)}</div>
  {f'<p class="caption">{esc(chart["caption"])}</p>' if chart.get('caption') else ''}
  <div class="prov-line">id <code>{esc(key)}</code>{line} · <a href="{site.chart_anchor(key, 1)}">갤러리에서 보기</a></div>
  {data_block}
</article>""")
    charts_html = "".join(charts) or '<div class="empty">이 run은 차트를 만들지 않았습니다.</div>'

    hl = [c.get("line") for c in run.get("charts") or []]
    if run.get("error") and run["error"].get("line"):
        hl.append(run["error"]["line"])
    code_blocks = []
    snap = script.get("snapshot")
    if snap and p.path(snap).is_file():
        state = {"same": "현재 파일과 같음", "changed": "현재 파일은 수정됨", "missing": "현재 파일 없음",
                 "n/a": "재실행 스냅샷"}.get(fresh["code"], fresh["code"])
        code_blocks.append(
            f'<div class="codefile" id="code-main"><div class="file-head"><code>{esc(script.get("path"))}</code>'
            f'<span class="sha">sha256 {short(script.get("sha256"), 12)}</span><span>{esc(state)}</span>'
            f'<a href="{site.file_link(snap, 1)}">스냅샷 파일</a></div>'
            f'{code_html(p.path(snap).read_text(encoding="utf-8", errors="replace"), "main", hl)}</div>')
    for i, mod in enumerate(run.get("modules") or [], 1):
        mp = p.path(mod.get("snapshot", ""))
        if mp.is_file():
            code_blocks.append(
                f'<details class="codefile"><summary><code>{esc(mod["path"])}</code> <span class="sha">'
                f'sha256 {short(mod.get("sha256"), 12)}</span> (import된 로컬 모듈)</summary>'
                f'{code_html(mp.read_text(encoding="utf-8", errors="replace"), f"m{i}")}</details>')

    pairs = list(zip(run.get("inputs") or [], fresh["inputs"], strict=False))
    n_inputs = len(pairs)
    # Files whose state changed are the ones a reader came for, so they survive the row cap.
    ordered = sorted(pairs, key=lambda pair: pair[1]["state"] == "same")[:FILE_ROW_LIMIT]
    input_rows = []
    for entry, f_entry in ordered:
        tone, icon, label = INPUT_STATE.get(f_entry["state"], INPUT_STATE["unknown"])
        input_rows.append(
            f'<tr><td>{site.path_html(entry["path"], 1)}</td><td>{esc(", ".join(entry.get("via") or []))}</td>'
            f'<td class="num">{fmt_bytes(entry.get("bytes"))}</td><td class="sha">{short(entry.get("sha256"), 12)}</td>'
            f'<td>{badge(tone, icon, label)}</td></tr>')
    if n_inputs > len(input_rows):
        input_rows.append(
            f'<tr><td colspan="5" class="muted">그 외 {n_inputs - len(input_rows):,}개 — 변경된 '
            f'입력을 먼저 보여줍니다. 전체 목록은 '
            f'<a href="{site.file_link(f"runs/{rid}/run.json", 1)}">run.json</a>에 있습니다.</td></tr>')
    output_rows = []
    for out in run.get("outputs") or []:
        output_rows.append(
            f'<tr><td>{site.path_html(out["path"], 1)}</td><td>{esc(out.get("change"))}</td>'
            f'<td>{esc(out.get("via", ""))}</td><td class="num">{fmt_num(out.get("rows"))}</td>'
            f'<td>{esc(", ".join(c[0] for c in (out.get("schema") or [])[:12]))}</td>'
            f'<td class="sha">{short(out.get("sha256"), 12)}</td></tr>')

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
        return (f'<details class="log"{opened}><summary>{label} · {fmt_bytes(size)}</summary>'
                f'<pre>{esc(text) or "(비어 있음)"}</pre></details>')

    env = run.get("env") or {}
    env_rows = "".join(f"<tr><th>{esc(k)}</th><td><code>{esc(v)}</code></td></tr>" for k, v in [
        ("python", env.get("python")), ("uv.lock sha256", short(env.get("uv_lock_sha256"), 16)),
        ("kit", f"{env.get('kit_version')} · {short(env.get('kit_sha256'), 12)}"),
        *sorted((env.get("packages") or {}).items())])
    related = [e for e in site.events if rid in ((e.get("refs") or {}).get("runs") or [])]

    body = f"""{head}{callouts}
<nav class="jump"><a href="#charts">차트</a><a href="#code">코드</a><a href="#inputs">입력</a><a href="#outputs">출력</a><a href="#logs">로그</a><a href="#env">환경</a><a href="#journal">기록</a><a href="#reproduce">재현</a></nav>
<section id="charts"><h2>차트 ({len(run.get('charts') or [])})</h2>{charts_html}</section>
<section id="code"><h2>코드</h2><p class="muted">run이 실행한 바이트 그대로의 스냅샷입니다. 강조된 줄은 차트를 기록한 줄과 오류 줄입니다.</p>{''.join(code_blocks) or '<div class="empty">코드 스냅샷이 없습니다.</div>'}</section>
<section id="inputs"><h2>입력 데이터 ({n_inputs})</h2><p class="muted">via: 런타임 감지(polars·duckdb·lab.*), scan(코드의 경로 리터럴), declared(호출 시 지정). 상태는 지금 파일의 sha256과 기록을 비교합니다.</p>
{('<div class="table-wrap"><table class="list"><thead><tr><th>경로</th><th>via</th><th>크기</th><th>기록 sha256</th><th>현재</th></tr></thead><tbody>' + ''.join(input_rows) + '</tbody></table></div>') if input_rows else '<div class="empty">감지된 입력이 없습니다.</div>'}</section>
<section id="outputs"><h2>출력 ({len(output_rows)})</h2>
{('<div class="table-wrap"><table class="list"><thead><tr><th>경로</th><th>변경</th><th>via</th><th>행</th><th>열</th><th>sha256</th></tr></thead><tbody>' + ''.join(output_rows) + '</tbody></table></div>') if output_rows else '<div class="empty">프로젝트 파일을 만들거나 바꾸지 않았습니다.</div>'}</section>
<section id="logs"><h2>로그</h2>{log_block('stdout', run.get('stdout'))}{log_block('stderr', run.get('stderr'))}</section>
<section id="env"><h2>환경</h2><div class="table-wrap"><table class="kv">{env_rows}</table></div></section>
<section id="journal"><h2>관련 기록</h2>{site.events_html(related, 1)}</section>
<section id="reproduce"><h2>재현</h2><p>같은 코드 스냅샷을 다시 실행하고 차트 데이터·출력을 비교합니다.</p>
<pre class="cmd">aelix: analytics_run(title="재실행 검증", rerun_of="{esc(rid)}")
CLI:   lab run --rerun-of {esc(rid)}</pre></section>"""
    crumb = f' <span>/</span> <a href="../index.html#runs">실행</a> <span>/</span> {esc(rid)}'
    return site.page(1, run.get("title") or rid, crumb, body, "run")


# --- dataset page ---

def dataset_page(site: Site, ds: dict, view: str) -> str:
    from . import data

    p = site.p
    try:
        prof = data.profile(p, p.path(ds["path"]), preview_rows=50, cache=site.cache)
    except Exception as exc:  # noqa: BLE001
        prof = {"columns": [], "preview": {"columns": [], "rows": []}, "error": str(exc)}
    meta = core.read_json(p.path(ds["path"]).with_suffix(".meta.json")) if ds["kind"] == "raw" else None
    facts = [f'<code>{esc(ds["path"])}</code>', f'{fmt_num(prof.get("rows"))}행 × {len(prof.get("columns") or [])}열',
             fmt_bytes(prof.get("bytes")), f'sha256 <span class="sha">{short(prof.get("sha256"), 16)}</span>']
    origin = ""
    if isinstance(meta, dict):
        src = meta.get("source") or {}
        origin = (f'<dl class="facts"><div><dt>원본 파일</dt><dd><code>{esc(src.get("path"))}</code></dd></div>'
                  f'<div><dt>원본 sha256</dt><dd class="sha">{short(src.get("sha256"), 16)}</dd></div>'
                  f'<div><dt>형식</dt><dd>{esc(src.get("format"))}</dd></div>'
                  f'<div><dt>가져온 시각</dt><dd>{fmt_time(meta.get("imported_at"))}</dd></div></dl>')
        if meta.get("description"):
            origin = f'<p class="lead">{esc(meta["description"])}</p>' + origin
    producers = ds.get("producers") or []
    lineage = []
    if producers:
        lineage.append("<h3>이 파일을 만든 run</h3><ul class='plain'>" + "".join(
            f'<li><a href="{site.run_link(r, 1)}">{esc(r)}</a> {esc((site.run_by_id.get(r) or {}).get("title"))}'
            f'{" · 현재 버전" if i == len(producers) - 1 else ""}</li>'
            for i, r in enumerate(producers)) + "</ul>")
    users = ds.get("used_by") or []
    lineage.append("<h3>이 데이터를 읽은 run</h3>" + ("<ul class='plain'>" + "".join(
        f'<li><a href="{site.run_link(r, 1)}">{esc(r)}</a> {esc((site.run_by_id.get(r) or {}).get("title"))}</li>'
        for r in users) + "</ul>" if users else '<div class="empty">아직 없음</div>'))
    used_charts = [c for c in site.charts if not c.get("retired") and ds["path"] in
                   {i["path"] for i in (site.run_by_id.get(c["run"]) or {}).get("inputs") or []}]
    if used_charts:
        lineage.append("<h3>이 데이터를 쓴 차트 (최신 버전)</h3><ul class='plain'>" + "".join(
            f'<li><a href="{site.chart_anchor(c["key"], 1)}">{esc(c.get("title"))}</a> <code>{esc(c["key"])}</code></li>'
            for c in used_charts) + "</ul>")
    # A statistic that does not apply to a column type is "–", never "null" (which is a data value).
    schema_rows = [[c["name"], c["dtype"], c.get("nulls"),
                    *("–" if c.get(k) is None else c.get(k) for k in ("min", "max", "mean", "distinct"))]
                   for c in prof.get("columns") or []]
    preview = prof.get("preview") or {}
    body = f"""
<div class="page-head"><div class="title-row"><h1>{esc(view)}</h1><span class="badge tone-muted">{esc(ds['kind'])}</span></div>
<div class="sub">{' · '.join(facts)}</div></div>
{origin}
<section><h2>스키마와 통계</h2>{site.table(['열', '타입', 'null', 'min', 'max', 'mean', 'distinct≈'], schema_rows, klass='list')}</section>
<section><h2>미리보기</h2>{site.table(preview.get('columns') or [], preview.get('rows') or [])}</section>
<section><h2>계보</h2>{''.join(lineage)}</section>
<section><h2>조회</h2><pre class="cmd">analytics_query(sql="SELECT * FROM {esc(view)} LIMIT 20")
analytics.read("{esc(view)}")   ·   analytics.sql("SELECT count(*) FROM {esc(view)}").pl()</pre></section>"""
    crumb = f' <span>/</span> <a href="../index.html#data">데이터</a> <span>/</span> {esc(view)}'
    return site.page(1, view, crumb, body, "dataset")


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
            for d in diff) or '<span class="ctx">(첫 버전)</span>\n'
        event = next((e for e in site.events if (e.get("refs") or {}).get("context") == v.name), None)
        who = f" · {esc(event.get('actor'))} · {esc(event.get('id'))}" if event else ""
        blocks.append(f'<article class="version" id="v-{esc(v.stem)}"><h3>{fmt_time(core._version_time(v))}{who}</h3>'
                      f'<pre class="diff">{lines}</pre></article>')
    body = f"""
<div class="page-head"><h1>맥락 기록</h1><div class="sub">CONTEXT.md가 바뀔 때마다 .lab/context/에 버전이 남습니다. 새 버전은 run·import·note·리포트 빌드 때 감지됩니다.</div></div>
<div class="columns wide-left">
  <article class="panel markdown">{render_markdown(current) if current else '<div class="empty">비어 있음</div>'}</article>
  <aside class="panel"><h3>버전 {len(versions)}개</h3><ul class="plain">{''.join(f'<li><a href="#v-{esc(v.stem)}">{fmt_time(core._version_time(v))}</a></li>' for v in versions[::-1])}</ul></aside>
</div>
<section><h2>변경 내역</h2>{''.join(blocks) or '<div class="empty">기록된 버전이 없습니다.</div>'}</section>"""
    return site.page(0, "맥락 기록", ' <span>/</span> 맥락 기록', body, "context")
