"""Line endings (ADR-0007, Issue #42): the repository and the text the kit writes are LF on every
platform, a user's file keeps its bytes, and a change of line endings alone is named as such.

Every leg asserts the same constants — bytes and sha256 values set on Linux and macOS, or computed
from git's blobs, which do not depend on the platform — so the three platforms agree with each other
by transitivity (design 4.1). The files a test hands the kit as a user's are written with
`write_bytes`: text mode would turn them into CRLF on Windows, and the kit keeps a user's bytes
(ADR-0007 decision 3), so a constant would then fail for the wrong reason (design review M2). What a
leg measured beside its assertions goes to record_observation.
"""

from __future__ import annotations

import ast
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest
from conftest import (
    KIT,
    checkout_eol,
    kit_cli,
    record_observation,
    tracked_kit_sha,
)

from aelix_analytics import core, runner

PACK = Path(__file__).resolve().parents[1]
KIT_MODULES = PACK / "src" / "aelix_analytics"
EXTENSION = PACK / "extension" / "aelix_analytics.py"
TEMPLATE = KIT / "aelix_analytics" / "template"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_project(tmp_path: Path, name: str = "proj") -> core.Project:
    return core.init_project(tmp_path / name, title="Line endings", goal="Keep the bytes",
                             template_dir=TEMPLATE, origin=core.Origin("test", "sdk"))


# --- the checkout ---------------------------------------------------------------------------------


def test_the_checkout_is_lf():
    """ADR-0007 decision 7 ①: no tracked text file has a CRLF, in the index or in this leg's work
    tree, but those `-text` keeps as they are (the fixtures, with their intended CRLF journal). On a
    Windows runner (core.autocrlf=true) this holds only because of the root .gitattributes."""
    eol = checkout_eol()
    assert eol, "git lists no tracked file"
    as_is = sorted(name for name, (_, _, attr) in eol.items() if "-text" in attr.split("/", 1)[1])
    crlf = sorted(name for name, (index, work, attr) in eol.items()
                  if name not in as_is and ({index, work} & {"i/crlf", "i/mixed", "w/crlf", "w/mixed"}))
    record_observation("checkout-eol", {
        "counts": {" ".join(k): v for k, v in sorted(Counter(eol.values()).items())},
        "kept_as_is_with_crlf": [n for n in as_is if eol[n][1] in ("w/crlf", "w/mixed")],
        "kept_as_is": len(as_is)})
    assert not crlf, f"tracked text with CRLF line ends: {crlf}"
    assert eol["tests/fixtures/records/mixed-version/journal.jsonl"][1] == "w/mixed", \
        "the fixture's intended CRLF lines must stay (tests/fixtures/records/.gitattributes)"


def test_the_kit_sha_is_the_tracked_files_digest():
    """ADR-0007 decision 7 ③ for the checkout: env.kit_sha256 of a run is the digest of the tracked
    package files' blobs, on every leg (the e2e leg of tests/test_packaging.py asserts the same of
    an installed kit's run)."""
    expected = tracked_kit_sha()
    record_observation("kit-sha", {"kit_sha": runner.kit_sha(), "tracked": expected})
    assert runner.kit_sha() == expected


# --- the helper -----------------------------------------------------------------------------------


def test_the_helper_writes_lf_and_keeps_the_text(tmp_path):
    path = tmp_path / "a.txt"
    core.write_text(path, "one\ntwo\n")
    core.write_text(path, "three\n", append=True)
    assert path.read_bytes() == b"one\ntwo\nthree\n"
    # The helper does not change the text: a CR the caller passes is written as it is.
    core.write_text(path, "x\r\ny\n")
    assert path.read_bytes() == b"x\r\ny\n"
    assert core.newlines_as_lf("a\r\nb\rc\n") == "a\nb\nc\n"
    with core.open_text(tmp_path / "b.txt", "x") as fh:
        fh.write("é\n")
    assert (tmp_path / "b.txt").read_bytes() == b"\xc3\xa9\n"  # UTF-8, no BOM
    for mode in ("r", "wb", "w+", "rt"):
        with pytest.raises(ValueError):
            core.open_text(tmp_path / "c.txt", mode)
    # What this leg's text mode does by default, for the record (design review C8): CRLF on
    # Windows, LF elsewhere. Observed, not asserted; text mode's default on purpose.
    with open(tmp_path / "plain.txt", "w", encoding="utf-8") as fh:
        fh.write("a\n")
    record_observation("text-mode-default", {"open_w_a_newline": (tmp_path / "plain.txt").read_bytes().hex()})


# --- records and a new project --------------------------------------------------------------------

# JSON's own types only (design review C12): write_json's default=str would spell a Path with this
# platform's separator.
FIXED = {"schema": "lab.run/1", "id": "20261006-120000-fixed", "title": "줄 끝 · line ends",
         "text": "two\nlines\r\nand a lone\rCR", "numbers": [1, 2.5, -3], "flags": [True, False, None],
         "nested": {"empty": {}, "list": []}}
FIXED_JSON_SHA256 = "e8129fd88998afcde5678cdf291f835338bdc468d514318c518532a1ad6f0fc9"
FIXED_JSONL = (
    b'{"schema": "lab.run/1", "id": "20261006-120000-fixed", "title": "\xec\xa4\x84 \xeb\x81\x9d '
    b'\xc2\xb7 line ends", "text": "two\\nlines\\r\\nand a lone\\rCR", "numbers": [1, 2.5, -3], '
    b'"flags": [true, false, null], "nested": {"empty": {}, "list": []}}\n{"seq": 2}\n')
FIXED_JSONL_SHA256 = "c9dff9a10e3cacebdb7d2dc9e7717b5ef7be7e759df6b34630c38a5aba9c11a5"


def canonical(data: bytes) -> bytes:
    return (json.dumps(json.loads(data), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def test_records_are_the_same_bytes_everywhere(tmp_path):
    """ADR-0007 decision 2 and 7 ②, Issue #42's first criterion for records: the same object is the
    same bytes, and the same sha256, on every platform."""
    core.write_json(tmp_path / "run.json", FIXED)
    written = (tmp_path / "run.json").read_bytes()
    assert written == canonical(written) and b"\r" not in written
    assert sha(written) == FIXED_JSON_SHA256
    core.append_jsonl(tmp_path / "journal.jsonl", FIXED)
    core.append_jsonl(tmp_path / "journal.jsonl", {"seq": 2})
    appended = (tmp_path / "journal.jsonl").read_bytes()
    assert appended == FIXED_JSONL and sha(appended) == FIXED_JSONL_SHA256
    core.trust_project(tmp_path)
    trust = core.trust_file().read_bytes()
    assert b"\r" not in trust and trust == canonical(trust)
    record_observation("fixed-records", {"run.json": sha(written), "journal.jsonl": sha(appended),
                                         "trust": sha(trust)})


# The copies of a project made with title "Line endings": the template's bytes with the values
# filled in (an edit of the template changes these).
NEW_PROJECT_SHA256 = {
    ".gitattributes": "66b748264be11611b9428c43a486454118278853b83c13397133c946929b6c15",
    ".gitignore": "9a7128ef037b02a37df92002ecc27e3f2901b95f2a19d85f440e98cd830da87f",
    "pyproject.toml": "09f536b1d9faf106e903d4614a308e28fc23e15cf0c346e939de82e828bb4b28",
    ".python-version": "7b55f8e67b5623c4bef3fa691288da9437d79d3aba156de48d481db32ac7d16d",
}


def test_a_new_project_s_text_is_lf(tmp_path):
    project = new_project(tmp_path)
    root = project.root
    seen = {name: sha((root / name).read_bytes()) for name in NEW_PROJECT_SHA256}
    record_observation("new-project", seen)
    assert seen == NEW_PROJECT_SHA256
    assert (root / ".python-version").read_bytes() == b"3.12\n"
    assert (root / ".gitattributes").read_bytes() == (TEMPLATE / "gitattributes").read_bytes()
    assert (root / ".gitattributes").read_bytes().endswith(b"\n* -text\n")
    assert (root / ".gitignore").read_bytes() == (TEMPLATE / "gitignore").read_bytes()
    for name in ("analytics.json", "CONTEXT.md", "journal.jsonl", ".analytics/cache/hashes.json"):
        if (root / name).exists():
            assert b"\r" not in (root / name).read_bytes(), name
    assert (root / "analytics.json").read_bytes() == canonical((root / "analytics.json").read_bytes())
    versions = core.context_versions(project)
    assert [v.read_bytes() for v in versions] == [(root / "CONTEXT.md").read_bytes()]


def test_values_filled_into_the_template_are_lf(tmp_path):
    """A title or goal with a CR (from a tool's argument) does not bring one into the copies (design
    review C4); analytics.json and the journal keep the text, escaped by JSON."""
    project = core.init_project(tmp_path / "proj", title="Two\r\nlines", goal="Goal\r\nwith\rends",
                                template_dir=TEMPLATE, origin=core.Origin("test", "sdk"))
    context = project.context.read_bytes()
    assert b"\r" not in context and b"Goal\nwith\nends" in context and b"# Two\nlines" in context
    assert core.read_json(project.marker)["title"] == "Two\r\nlines"
    assert b"\r" not in project.marker.read_bytes() and b"\r" not in project.journal.read_bytes()


def test_a_new_project_is_judged_without_its_gitattributes(tmp_path):
    """The template's .gitattributes is a project file, like .gitignore: not an output of a run."""
    assert ".gitattributes" in runner.PROJECT_FILES
    assert "gitattributes" in core.TEMPLATE_FILES


# --- a run ----------------------------------------------------------------------------------------

RUN_SCRIPT = (b'import sys\n'
              b'print("a")\n'
              b'print("b")\n'
              b'sys.stdout.flush()\n'
              b'sys.stdout.buffer.write(b"raw\\r\\n")\n'
              b'sys.stdout.buffer.flush()\n'
              b'print("e", file=sys.stderr)\n')
RUN_SCRIPT_CRLF = RUN_SCRIPT.replace(b"\n", b"\r\n")
RUN_SHA256 = {"lf.py": "80b2e7c2f32f3eb60eb1806c2d241cdd6bf4fd58ee8f5af1c5d974f697c5800a",
              "crlf.py": "e37afdb4de90efb9237b76db6373de43fa8ace22ac94026e13e1ead4aa63d33e"}


def test_a_run_writes_lf_and_copies_bytes(tmp_path):
    """The kit's run log is LF (the script's print), the script's own bytes are copied and hashed as
    they are — an LF and a CRLF script have their own constant sha256 everywhere — and bytes the
    script writes to the buffer land as they are (ADR-0007 decisions 2 and 3)."""
    project = new_project(tmp_path)
    seen = {}
    for name, source in (("lf.py", RUN_SCRIPT), ("crlf.py", RUN_SCRIPT_CRLF)):
        (project.scripts / name).write_bytes(source)
        code, result = kit_cli(project.root, "run", f"scripts/{name}", "--title", name)
        assert code == 0 and result["ok"], result
        record = result["run"]
        assert record["status"] == "ok", record
        run_dir = project.runs / record["id"]
        assert record["script"]["sha256"] == RUN_SHA256[name] == sha(source)
        assert project.path(record["script"]["snapshot"]).read_bytes() == source
        assert (run_dir / "stdout.txt").read_bytes() == b"a\nb\nraw\r\n"
        assert (run_dir / "stderr.txt").read_bytes() == b"e\n"
        assert b"\r" not in (run_dir / "run.json").read_bytes()
        seen[name] = {"script.sha256": record["script"]["sha256"],
                      "stdout.txt": sha((run_dir / "stdout.txt").read_bytes()),
                      "kit_sha256": record["env"]["kit_sha256"]}
    assert b"\r" not in project.journal.read_bytes()
    record_observation("run-bytes", seen)


# --- charts and the Workspace ---------------------------------------------------------------------

CHART_SCRIPT = b'''import aelix_analytics as analytics
import plotly.graph_objects as go


class Html:
    def _repr_html_(self):
        return "<div>\\r\\n<pre>a\\rb</pre>\\r\\n</div>"


class FakeAltair:
    """Stands in for an altair chart: the kit must hand it a buffer and format="html"."""

    def save(self, fp, format=None):
        if hasattr(fp, "write") and format == "html":
            fp.write("<html>\\r\\n<body>chart</body>\\r\\n</html>\\n")
        else:
            open(fp, "wb").write(b"given a path")


FakeAltair.__module__ = "altair.vegalite.v5.api"

analytics.visualize(Html(), "html-chart", title="html")
analytics.visualize(go.Figure(go.Bar(x=[1, 2], y=[3, 4])), "plotly-chart", title="plotly")
analytics.visualize(FakeAltair(), "altair-chart", title="altair")
print("a log line with a CR\\r")
analytics.record("note", "two\\r\\nlines")
'''
HTML_CHART = b"<div>\n<pre>a\nb</pre>\n</div>"
ALTAIR_CHART = b"<html>\n<body>chart</body>\n</html>\n"


def test_charts_and_the_workspace_are_lf(tmp_path):
    """ADR-0007 decision 2 and 7 ②: the chart files the kit writes and every Workspace file it writes
    are LF — the HTML inside as well, though a record's text and a run's log hold a CR — and the
    third-party plotly.min.js is copied byte for byte."""
    project = new_project(tmp_path)
    (project.scripts / "charts.py").write_bytes(CHART_SCRIPT)
    code, result = kit_cli(project.root, "run", "scripts/charts.py", "--title", "charts")
    assert code == 0 and result["run"]["status"] == "ok", result
    run_id = result["run"]["id"]
    charts = project.runs / run_id / "visualizations"
    assert (charts / "html-chart.html").read_bytes() == HTML_CHART
    assert (charts / "altair-chart.html").read_bytes() == ALTAIR_CHART
    assert b"\r" not in (charts / "plotly-chart.plotly.json").read_bytes()
    code, rendered = kit_cli(project.root, "render")
    assert code == 0 and rendered["ok"], rendered
    written = sorted(p for p in project.workspace_dir.rglob("*") if p.is_file())
    assert {"index.html", "context.html", f"{run_id}.html", "build.js", "code.css", "workspace.css",
            "workspace.js", "plotly.min.js"} <= {p.name for p in written}
    with_cr = [p.relative_to(project.root).as_posix() for p in written
               if p.name != "plotly.min.js" and b"\r" in p.read_bytes()]
    assert not with_cr, with_cr
    assert re.fullmatch(rb'window\.__LAB_BUILD__ = window\.__ANALYTICS_BUILD__ = "[0-9-]+";\n',
                        (project.workspace_dir / "assets" / "build.js").read_bytes())
    import plotly

    shipped = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    assert (project.workspace_dir / "assets" / "plotly.min.js").read_bytes() == shipped.read_bytes()
    assert b"\r" not in (project.cache / "report-fingerprint").read_bytes()
    run_page = (project.workspace_dir / "runs" / f"{run_id}.html").read_text(encoding="utf-8")
    assert "a log line with a CR\n" in run_page
    record_observation("workspace", {"files": len(written), "html_chart": sha(HTML_CHART),
                                     "altair_chart": sha(ALTAIR_CHART)})


# --- line endings only ----------------------------------------------------------------------------

LF_TABLE = b"region,revenue\nseoul,100\nbusan,250\n"
CRLF_TABLE = LF_TABLE.replace(b"\n", b"\r\n")


def judged(project: core.Project, path: Path, recorded: bytes, now: bytes) -> bool:
    path.write_bytes(now)
    cache = core.HashCache(project)
    assert cache.sha(path) == sha(now)
    return cache.line_endings_only(path, sha(recorded))


@pytest.mark.parametrize(("recorded", "now", "only"), [
    (LF_TABLE, CRLF_TABLE, True),
    (CRLF_TABLE, LF_TABLE, True),
    (LF_TABLE, b"region,revenue\r\nseoul,100\nbusan,250\r\n", True),  # mixed now, LF recorded
    (b"region,revenue\r\nseoul,100\nbusan,250\n", LF_TABLE, False),  # mixed when recorded
    (b"region,revenue\r\nseoul,100\nbusan,250\n", CRLF_TABLE, False),
    (LF_TABLE, LF_TABLE.replace(b"\n", b"\r"), False),  # lone CRs
    (LF_TABLE, CRLF_TABLE.replace(b"250", b"251"), False),  # the content changed too
    (b"\0" + LF_TABLE, b"\0" + CRLF_TABLE, False),  # a NUL in the first 8 KiB: not judged
    (b"x" * (8 << 10) + b"\0" + LF_TABLE, b"x" * (8 << 10) + b"\0" + CRLF_TABLE, True),
], ids=["lf-to-crlf", "crlf-to-lf", "lf-to-mixed", "mixed-to-lf", "mixed-to-crlf", "lone-cr",
        "content-too", "nul-in-head", "nul-after-head"])
def test_line_endings_only_cases(tmp_path, recorded, now, only):
    project = new_project(tmp_path)
    assert judged(project, project.root / "data" / "t.csv", recorded, now) is only


def test_line_endings_only_up_to_the_size_limit(tmp_path, monkeypatch):
    project = new_project(tmp_path)
    path = project.root / "data" / "t.csv"
    monkeypatch.setattr(core, "LINE_ENDINGS_MAX_BYTES", len(CRLF_TABLE))
    assert judged(project, path, LF_TABLE, CRLF_TABLE) is True
    monkeypatch.setattr(core, "LINE_ENDINGS_MAX_BYTES", len(CRLF_TABLE) - 1)
    assert judged(project, path, LF_TABLE, CRLF_TABLE) is False


def reference_digests(data: bytes) -> tuple[str, str]:
    lf = data.replace(b"\r\n", b"\n")
    return sha(lf), sha(lf.replace(b"\n", b"\r\n"))


def test_line_ending_digests_across_block_ends(tmp_path, monkeypatch):
    """A CRLF split by a block end is still one line end: the digests read in blocks equal those of
    the whole file, for every block size and short random texts of CR, LF and one other byte."""
    rng = random.Random(42)
    path = tmp_path / "f"
    for block in (1, 2, 3, 5, 8):
        monkeypatch.setattr(core, "LINE_ENDINGS_BLOCK", block)
        monkeypatch.setattr(core, "LINE_ENDINGS_SNIFF_BYTES", 3)
        for _ in range(200):
            data = bytes(rng.choice(b"\r\na") for _ in range(rng.randrange(0, 24)))
            path.write_bytes(data)
            assert core.line_ending_digests(path) == reference_digests(data), (block, data)
            assert core.line_ending_digests_of(data) == reference_digests(data), (block, data)


def test_the_cache_keeps_the_digests_and_reads_older_entries(tmp_path, monkeypatch):
    """The digests are kept with the file's entry ([size, mtime_ns, sha256, [lf, crlf]]), an entry of
    three values (an older kit's) is still read, and a file that changes while it is read gets no
    reason and no kept digests (design review C7)."""
    project = new_project(tmp_path)
    path = project.root / "data" / "t.csv"
    path.write_bytes(CRLF_TABLE)
    cache = core.HashCache(project)
    current = cache.sha(path)
    assert cache.line_endings_only(path, sha(LF_TABLE)) is True
    cache.save()
    entry = core.read_json(cache.file)[str(path)]
    assert entry[2] == current and entry[3] == list(reference_digests(CRLF_TABLE))
    with monkeypatch.context() as patched:
        patched.setattr(core, "line_ending_digests", lambda _path: pytest.fail("read again"))
        again = core.HashCache(project)
        assert again.sha(path) == current and again.line_endings_only(path, sha(LF_TABLE)) is True
    older = core.HashCache(project)
    older.data[str(path)] = entry[:3]
    assert older.sha(path) == current

    def changing(target):
        digests = reference_digests(target.read_bytes())
        target.write_bytes(target.read_bytes() + b"more\n")
        return digests

    other = project.root / "data" / "u.csv"
    other.write_bytes(CRLF_TABLE)
    fresh = core.HashCache(project)
    fresh.sha(other)
    monkeypatch.setattr(core, "line_ending_digests", changing)
    assert fresh.line_endings_only(other, sha(LF_TABLE)) is False
    assert len(fresh.data[str(other)]) == 3


def test_a_file_not_read_is_not_kept_as_not_judged(tmp_path):
    """A file that could not be read gets no reason and nothing kept for its size and time, so the
    next look reads it; only a file the rule does not judge (a NUL in its head here) keeps null, and
    is not read again for that size and time (implementation review)."""
    project = new_project(tmp_path)
    path = project.root / "data" / "t.csv"
    path.write_bytes(CRLF_TABLE)
    cache = core.HashCache(project)
    cache.sha(path)
    refused = []

    def refuse_once(file, mode="r", *args, **kwargs):
        if Path(file) == path and not refused:
            refused.append(file)
            raise PermissionError(f"denied: {file}")
        return open(file, mode, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(core, "open", refuse_once, raising=False)
        assert cache.line_endings_only(path, sha(LF_TABLE)) is False  # could not be read
        assert refused == [path] and len(cache.data[str(path)]) == 3  # nothing kept
        assert cache.line_endings_only(path, sha(LF_TABLE)) is True  # read the next time
    assert cache.data[str(path)][3] == list(reference_digests(CRLF_TABLE))
    with pytest.raises(OSError):
        core.line_ending_digests(project.root / "data" / "absent.csv")

    nul = project.root / "data" / "nul.csv"
    nul.write_bytes(b"\0" + CRLF_TABLE)
    cache.sha(nul)
    assert cache.line_endings_only(nul, sha(b"\0" + LF_TABLE)) is False
    assert cache.data[str(nul)][3] is None
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(core, "line_ending_digests", lambda _path: pytest.fail("read again"))
        assert cache.line_endings_only(nul, sha(b"\0" + LF_TABLE)) is False


JUDGED_SCRIPT = b'''import aelix_analytics as analytics


class Html:
    def _repr_html_(self):
        return "<p>judged</p>"


analytics.visualize(Html(), "judged", title="judged")
'''


def test_line_endings_only_reaches_status_and_the_workspace(tmp_path):
    """ADR-0007 decision 4: a changed input or script whose line ends alone changed stays `changed`
    (and its chart stale), and status, its text and the Workspace say `line endings only`."""
    project = new_project(tmp_path)
    data = project.root / "data" / "in.csv"
    data.write_bytes(LF_TABLE)
    script = project.scripts / "judged.py"
    script.write_bytes(JUDGED_SCRIPT)
    code, result = kit_cli(project.root, "run", "scripts/judged.py", "--title", "judged",
                           "--input", "data/in.csv")
    assert code == 0 and result["run"]["status"] == "ok", result
    run_id = result["run"]["id"]

    data.write_bytes(CRLF_TABLE)
    st = core.status(project)
    [row] = st["charts"]
    assert row["stale"] is True and row["changed_inputs"] == ["data/in.csv"]
    assert row["change_reasons"] == {"data/in.csv": core.LINE_ENDINGS_ONLY}
    assert row["code"] == "same" and row["code_reason"] is None
    text = core.status_text(st)
    assert "STALE(data/in.csv (line endings only))" in text, text
    code, cli = kit_cli(project.root, "status")
    assert cli["status"]["charts"][0]["change_reasons"] == {"data/in.csv": "line_endings_only"}
    fresh = core.Freshness(project, core.list_runs(project)).of(run_id)
    [entry] = [i for i in fresh["inputs"] if i["path"] == "data/in.csv"]
    assert entry == {"path": "data/in.csv", "state": "changed", "sha256": sha(LF_TABLE),
                     "reason": "line_endings_only"}

    script.write_bytes(JUDGED_SCRIPT.replace(b"\n", b"\r\n"))
    st = core.status(project)
    assert st["charts"][0]["code"] == "changed"
    assert st["charts"][0]["code_reason"] == core.LINE_ENDINGS_ONLY
    assert "script edited since (line endings only)" in core.status_text(st)
    code, rendered = kit_cli(project.root, "render")
    assert code == 0, rendered
    page = (project.workspace_dir / "runs" / f"{run_id}.html").read_text(encoding="utf-8")
    assert "Line endings only" in page and '<span lang="">data/in.csv</span> (line endings only)' in page
    assert "Current file edited (line endings only)" in page
    index = (project.workspace_dir / "index.html").read_text(encoding="utf-8")
    assert "data/in.csv (line endings only)" in index

    data.write_bytes(CRLF_TABLE + b"daegu,5\r\n")
    script.write_bytes(JUDGED_SCRIPT + b"# edited, more than the line ends\n")
    st = core.status(project)
    assert st["charts"][0]["change_reasons"] == {} and st["charts"][0]["code_reason"] is None
    assert st["charts"][0]["stale"] is True
    assert "line endings only" not in core.status_text(st)
    record_observation("line-endings-only", {"status_text": text})


def test_a_context_whose_line_endings_changed_says_so(tmp_path):
    project = new_project(tmp_path)
    lf = project.context.read_bytes()
    project.context.write_bytes(lf.replace(b"\n", b"\r\n"))
    event = core.sync_context(project, origin=core.Origin("test", "sdk"))
    assert event["text"] == "CONTEXT.md line endings changed"
    assert core.context_versions(project)[-1].read_bytes() == lf.replace(b"\n", b"\r\n")
    project.context.write_bytes(lf)
    assert core.sync_context(project, origin=core.Origin("test", "sdk"))["text"] == "CONTEXT.md line endings changed"
    project.context.write_bytes(lf.replace(b"\n", b"\r\n") + b"more\r\n")
    assert core.sync_context(project, origin=core.Origin("test", "sdk"))["text"] == "CONTEXT.md updated (+1 -0 lines)"


# --- older records --------------------------------------------------------------------------------


def test_old_crlf_records_are_read_and_kept(project):
    """ADR-0007 decision 6: records a kit wrote with CRLF (Windows before kit 0.4.0) are read as they
    are, and nothing rewrites them for their line ends; a new event is appended with LF after the
    CRLF lines (the mixed-version fixture of tests/test_record_contract.py is such a journal)."""
    root = project
    (root / "scripts" / "plain.py").write_bytes(b"print('ok')\n")
    code, result = kit_cli(root, "run", "scripts/plain.py", "--title", "plain")
    assert code == 0 and result["run"]["status"] == "ok", result
    run_json = root / "runs" / result["run"]["id"] / "run.json"
    trust = core.trust_file()
    crlf_files = [root / "analytics.json", run_json, root / "data" / "imported" / "sales.meta.json", trust,
                  root / "journal.jsonl"]
    for path in crlf_files:
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    before = {path: path.read_bytes() for path in crlf_files}
    st = core.status(core.Project(root))
    assert st["damaged"] == {"journal": [], "recovered": [], "files": []}, st["damaged"]
    assert st["unsupported"] == [] and st["counts"]["runs"] == 1 and st["counts"]["datasets"] == 1
    assert core.is_trusted(root)
    code, rendered = kit_cli(root, "render")
    assert code == 0 and rendered["ok"], rendered
    code, noted = kit_cli(root, "record", "note", "after the CRLF lines")
    assert code == 0 and noted["ok"], noted
    journal = (root / "journal.jsonl").read_bytes()
    assert journal.startswith(before[root / "journal.jsonl"])
    added = journal[len(before[root / "journal.jsonl"]):]
    assert added.endswith(b"\n") and b"\r" not in added
    for path in crlf_files[:-1]:
        assert path.read_bytes() == before[path], path
    assert [e["text"] for e in core.read_events(core.Project(root))][-1] == "after the CRLF lines"


# --- the guard: the kit writes text through its helper ------------------------------------------

# Openers that take something other than a file path (the first argument of webbrowser.open is a URL).
NON_FILE_OPENERS = frozenset({"webbrowser", "request", "socket", "zipfile", "tarfile", "shelve",
                              "dbm", "sqlite3",
                              # os.open gives a descriptor, no text layer: what is written through it is
                              # bytes (os.write) or goes through a file object this guard reads
                              # (open(fd, mode)). The migration's locks and exclusive files (#13 PR 2).
                              "os"})
COMPRESSED_OPENERS = frozenset({"gzip", "bz2", "lzma"})
TEMPFILES = frozenset({"TemporaryFile", "NamedTemporaryFile", "SpooledTemporaryFile"})
# logging's handlers that open their file in text mode with the platform's line ends.
FILE_HANDLERS = frozenset({"FileHandler", "WatchedFileHandler", "RotatingFileHandler",
                           "TimedRotatingFileHandler"})
HELPER = "open_text"  # _platform.open_text: the one text-mode open for writing (design 3.2)
# Where the helper is (since Issue #13 PR 1; core before), and the modules a call to it may name: core
# gives the helpers under the same names.
HELPER_MODULE = "_platform.py"
HELPER_RECEIVERS = ("core", "_platform")
UNKNOWN = object()  # an argument that `*args` or `**kwargs` may give


class Imports(NamedTuple):
    """A module's import bindings: a name bound to a module (`import io as x`: x → "io") and a name
    bound to something a module holds (`from io import open as x`: x → ("io", "open"))."""
    modules: dict[str, str]
    names: dict[str, tuple[str, str]]

    @classmethod
    def of(cls, tree: ast.AST) -> Imports:
        modules, names = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    modules[bound] = alias.name.rsplit(".", 1)[-1] if alias.asname else bound
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    names[alias.asname or alias.name] = ((node.module or "").rsplit(".", 1)[-1],
                                                         alias.name)
        return cls(modules, names)

    def module(self, name: str) -> str:
        """What a receiver's name stands for: `core` for `from . import core as kit`."""
        if name in self.names:
            return self.names[name][1]
        return self.modules.get(name, name)

    def callee(self, func: ast.AST) -> tuple[str, str, bool]:
        """(receiver, name, bare) of a call's function, through the aliases: `text_open(...)` after
        `from io import open as text_open` is ("io", "open", False), a builtin `open(...)` is
        ("", "open", True), `p.open(...)` is ("p", "open", False)."""
        if isinstance(func, ast.Name):
            if func.id in self.names:
                module, name = self.names[func.id]
                return module, name, False
            return "", func.id, True
        if isinstance(func, ast.Attribute):
            value = func.value
            return (self.module(value.id) if isinstance(value, ast.Name) else ""), func.attr, False
        return "", "", False


def _argument(call: ast.Call, name: str, position: int | None):
    """The argument `name` of `call`, by keyword or at `position`: None when it is not given, UNKNOWN
    when `*args` or `**kwargs` may give it."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    if position is not None:
        for index, value in enumerate(call.args):
            if isinstance(value, ast.Starred):
                return UNKNOWN
            if index == position:
                return value
    if any(keyword.arg is None for keyword in call.keywords):
        return UNKNOWN
    return None


def _writes_text(call: ast.Call, position: int, default: str, *, text_flag: str = "") -> bool:
    """Whether the mode (at `position`, or `mode=`) opens for writing in text mode: a literal mode
    with w, a, x or + and no b (with `text_flag`, only one that has it). A mode that is not a
    literal, or that `*args`/`**kwargs` may give, cannot be shown to be binary or read-only, so it
    counts."""
    value = _argument(call, "mode", position)
    if value is None:
        mode = default
    elif isinstance(value, ast.Constant) and isinstance(value.value, str):
        mode = value.value
    else:
        return True
    if text_flag and text_flag not in mode:
        return False
    return "b" not in mode and any(c in mode for c in "wax+")


def _writes_lf(call: ast.Call, position: int, imports: Imports) -> bool:
    """Whether the call's `newline` (by keyword or at `position`) is the literal "\\n" or a reference
    to _platform.TEXT_NEWLINE. None (the default: "\\r\\n" on Windows), any other constant and a
    value the guard cannot read are not."""
    value = _argument(call, "newline", position)
    if isinstance(value, ast.Constant):
        return value.value == "\n"
    if isinstance(value, ast.Name):
        return imports.names.get(value.id, ("", value.id))[1] == "TEXT_NEWLINE"
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
        return value.attr == "TEXT_NEWLINE" and imports.module(value.value.id) == "_platform"
    return False


def _text_write(node: ast.Call, imports: Imports, in_helper_module: bool,
                in_helper: bool) -> str | None:
    """What a call writes as text outside the helper (design 4.4), or None."""
    receiver, name, bare = imports.callee(node.func)
    if name == "write_text":
        if (bare and in_helper_module) or receiver in HELPER_RECEIVERS:
            return None  # the helper
        return "write_text"
    if name == "TextIOWrapper":
        return None if _writes_lf(node, 3, imports) else "TextIOWrapper without an LF newline="
    if name in FILE_HANDLERS:
        return f"logging.{name}"
    if name == "basicConfig" and receiver == "logging":
        return None if _argument(node, "filename", None) is None else "logging.basicConfig(filename=)"
    if name in TEMPFILES:
        return f"tempfile.{name}" if _writes_text(node, 0, "w+b") else None
    if name == "fdopen" and receiver == "os":
        return "os.fdopen" if _writes_text(node, 1, "r") else None
    if name != "open":
        return None
    if receiver in COMPRESSED_OPENERS:
        return f"{receiver}.open" if _writes_text(node, 1, "rb", text_flag="t") else None
    if bare or receiver in ("io", "codecs", "builtins"):
        if in_helper:  # open(file, mode, buffering, encoding, errors, newline)
            return None if _writes_lf(node, 5, imports) else "open without an LF newline= (the helper)"
        return f"{receiver + '.' if receiver else ''}open" if _writes_text(node, 1, "r") else None
    if receiver in NON_FILE_OPENERS:
        return None
    # Path.open(mode, buffering, encoding, errors, newline)
    return ".open" if _writes_text(node, 0, "r") else None


def text_writes_in(tree: ast.AST, where: str, *, in_helper_module: bool) -> list[str]:
    """Every call in a module's `tree` that writes text without the helper (_platform.open_text or
    _platform.write_text, also named through core); `in_helper_module` for _platform.py, where the
    helper's own body opens the file."""
    imports = Imports.of(tree)
    helper_body = set()
    if in_helper_module:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == HELPER:
                helper_body |= {id(sub) for sub in ast.walk(node)}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            what = _text_write(node, imports, in_helper_module, id(node) in helper_body)
            if what:
                found.append(f"{where}:{node.lineno} {what}")
    return found


def kit_text_writes(paths) -> list[str]:
    """Every call in `paths` that writes text without the helper (open_text or write_text)."""
    found = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        where = path.relative_to(PACK).as_posix() if PACK in path.parents else path.name
        found += text_writes_in(tree, where, in_helper_module=path.name == HELPER_MODULE
                                and path.parent == KIT_MODULES)
    return found


def kit_sources() -> list[Path]:
    paths = sorted(KIT_MODULES.glob("*.py")) + [EXTENSION]
    assert len(paths) > 10 and all(p.is_file() for p in paths), paths
    return paths


def test_kit_text_writes_go_through_the_helper():
    """ADR-0007 decision 2: one helper writes the kit's text, so it is LF on every platform. Scope:
    the kit's modules (core.py too) and the aelix extension; not the demo scripts, which are copied
    into a project as a user's files, nor the repository's tools and test helpers. Out of reach of
    this scan, and covered by a test of its own (test_charts_and_the_workspace_are_lf): a library
    that opens a path it is given (altair's `save(path)`, W12). Also out of its reach: an opener
    bound by assignment (`opener = open`) or passed as a value."""
    offenders = kit_text_writes(kit_sources())
    assert not offenders, "text written outside open_text/write_text:\n" + "\n".join(offenders)


@pytest.mark.parametrize(("source", "found"), [
    ("open(p, 'w')", True), ("open(p, 'wb')", False), ("open(p)", False), ("open(p, mode)", True),
    ("open(p, **kw)", True), ("open(p, *rest)", True), ("open(p, 'w', newline='\\n')", True),
    ("io.open(p, 'a', encoding='utf-8')", True), ("codecs.open(p, 'w')", True),
    ("from io import open as text_open\ntext_open(p, 'w')", True),
    ("from io import open as text_open\ntext_open(p, 'rb')", False),
    ("from codecs import open as codecs_open\ncodecs_open(p, 'w')", True),
    ("from builtins import open as builtin_open\nbuiltin_open(p, 'a')", True),
    ("import io as text_io\ntext_io.open(p, 'w')", True),
    ("p.open('w')", True), ("p.open('rb')", False), ("p.open(mode='x')", True), ("p.open(**kw)", True),
    ("os.fdopen(fd, 'w')", True), ("os.fdopen(fd, 'wb')", False),
    ("os.open(p, os.O_RDWR | os.O_CREAT)", False), ("open(os.open(p, flags), 'w')", True),
    ("open(os.open(p, flags), 'wb')", False),
    ("from os import fdopen\nfdopen(fd, 'w')", True), ("gzip.open(p, 'wt')", True),
    ("gzip.open(p, 'wb')", False), ("tempfile.NamedTemporaryFile(mode='w')", True),
    ("tempfile.NamedTemporaryFile()", False),
    ("from tempfile import NamedTemporaryFile as temporary\ntemporary(mode='w')", True),
    ("io.TextIOWrapper(b, encoding='utf-8')", True),
    ("io.TextIOWrapper(b, encoding='utf-8', newline=None)", True),
    ("io.TextIOWrapper(b, 'utf-8', None, None)", True),
    ("io.TextIOWrapper(b, encoding='utf-8', newline='\\r\\n')", True),
    ("io.TextIOWrapper(b, encoding='utf-8', newline='')", True),
    ("io.TextIOWrapper(b, encoding='utf-8', newline=ends)", True),
    ("io.TextIOWrapper(b, **kw)", True),
    ("from io import TextIOWrapper as Wrapper\nWrapper(b, encoding='utf-8')", True),
    ("io.TextIOWrapper(b, encoding='utf-8', newline='\\n')", False),
    ("io.TextIOWrapper(b, 'utf-8', None, '\\n')", False),
    ("io.TextIOWrapper(b, encoding='utf-8', newline=_platform.TEXT_NEWLINE)", False),
    ("io.TextIOWrapper(b, encoding='utf-8', newline=TEXT_NEWLINE)", False),
    ("logging.FileHandler(p)", True), ("from logging import FileHandler\nFileHandler(p)", True),
    ("logging.handlers.RotatingFileHandler(p)", True), ("logging.basicConfig(filename=p)", True),
    ("logging.basicConfig(level=10)", False),
    ("p.write_text('x')", True), ("core.write_text(p, 'x')", False),
    ("_platform.write_text(p, 'x')", False), ("from ._platform import write_text\nwrite_text(p, 'x')", False),
    ("from aelix_analytics import core as kit\nkit.write_text(p, 'x')", False),
    ("webbrowser.open(url)", False), ("p.write_bytes(b'')", False),
])
def test_the_guard_finds_each_form(tmp_path, source, found):
    module = tmp_path / "module.py"
    module.write_text(source + "\n", encoding="utf-8")
    assert bool(kit_text_writes([module])) is found


@pytest.mark.parametrize(("newline", "found"), [
    ("_platform.TEXT_NEWLINE", False), ("'\\n'", False), ("None", True), ("'\\r\\n'", True),
    ("ends", True), ("", True),
])
def test_the_guard_reads_the_helper_s_newline(newline, found):
    """The helper's own open is the one text-mode open the guard allows, and only with LF."""
    argument = f", newline={newline}" if newline else ""
    source = (f"def {HELPER}(path, mode):\n"
              f"    return open(path, mode, encoding='utf-8'{argument})\n")
    assert bool(text_writes_in(ast.parse(source), HELPER_MODULE, in_helper_module=True)) is found
