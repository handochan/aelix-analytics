"""The copy migration (Issue #13 PR 2, docs/design/RECORD-WRITER-AND-MIGRATION.md section 6).

What each guarantee of design section 1 is tested by here (the T numbers are section 11's):

| guarantee | tests |
|---|---|
| S1 the source is not written but its lock files | every test compares the source's tree, read by an independent walk, before and after (`tree`), lock files apart: T1, T2, T6, T5 |
| S2 one commit point, markers and trust only after the checks | T3 (`assert_uncommitted` at every interruption before the marker), T19, the F table |
| S3 sizes and hashes in the report, the marker binds it | T4 |
| S4 the same bytes, places moved only as the mapping says | T4, the F table (`copies` equal to an uninterrupted migration's) |
| S5 a source changed under the copy fails without a marker | T5 |
| S6 an interrupted destination resumes or is refused; nothing of anyone's overwritten or removed | T6 (the F table F1-F16 and the rows of design 6.10), T19 |
| S7 one migration a destination, the older kit's locks without waiting | T7, tests/test_contracts.py (T15) |
| S8 a write judged first; a destination not committed refused | T12 in tests/test_marker_gate.py, T3 here |
| S10 an interruption stays visible: migration_incomplete | T3 |
| S12 a user's bytes copied as they are (CRLF) | T16 |
| S13 reading the copy as the source was read | T11, in tests/test_migration_reader.py |

The F table of design 6.11 is FAULTS: each row is interrupted in a process of its own
(tests/migration_driver.py, `os._exit` at the step), judged as it stands (`incomplete` before the
marker, the older kit's search finds nothing), then the same migration is called again in this
process and has to give the copies an uninterrupted migration gives.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import KIT, LINK_KINDS, dir_link, kit_cli, kit_env
from jsonschema import Draft202012Validator, FormatChecker
from legacy_records import LegacyProject, at

from aelix_analytics import _platform, core, migration, writer

ROOT = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).with_name("migration_driver.py")
SCHEMA = json.loads((ROOT / "docs" / "schemas" / "analytics-records.schema.json").read_text(encoding="utf-8"))
RECORD = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
LINE = Draft202012Validator({"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"],
                             "$ref": "#/$defs/migration_progress_line"}, format_checker=FormatChecker())
ORIGIN = core.Origin("human", "cli", "inferred")
BIG = "data/raw/big.parquet"  # larger than a copy's chunk: a temporary file can be cut in half
POSIX = os.name != "nt"
ROOT_USER = POSIX and os.geteuid() == 0


# --- the independent walk ------------------------------------------------------------------------

def tree(root: Path, skip=()) -> dict[str, tuple]:
    """Every entry under `root` as the test sees it, not as the migration does: a file's size and
    sha256, a link's text, a directory, anything else by its type; links not followed. `skip` holds
    paths (and the trees under them) left out."""
    out: dict[str, tuple] = {}
    stack = [""]
    while stack:
        rel = stack.pop()
        with os.scandir(root / rel if rel else root) as listing:
            entries = sorted(listing, key=lambda e: e.name)
        for entry in entries:
            path = f"{rel}/{entry.name}" if rel else entry.name
            if any(path == s or path.startswith(s + "/") for s in skip):
                continue
            st = os.lstat(entry.path)
            if _platform.is_link(st):
                out[path] = ("link", os.readlink(entry.path))
            elif stat.S_ISDIR(st.st_mode):
                out[path] = ("dir",)
                stack.append(path)
            elif stat.S_ISREG(st.st_mode):
                with open(entry.path, "rb") as fh:
                    out[path] = ("file", st.st_size, hashlib.sha256(fh.read()).hexdigest())
            else:
                out[path] = ("other", stat.S_IFMT(st.st_mode))
    return out


LOCKS = (".lab/run.lock", ".lab/lock")


def source_tree(root: Path) -> dict:
    """The source as S1 compares it: everything but the older kit's two lock files, which a
    migration may make (and which `assert_locks_named` checks apart)."""
    return tree(root, skip=LOCKS)


def copies(dest: Path) -> dict:
    """What a destination holds of its source: everything but the marker and the migration's own
    directory, which are this migration's and differ between two migrations of one source — and the
    migration's id in the line the .gitignore block starts with (design 6.13), which does too."""
    out = tree(dest, skip=(writer.MARKER, migration.MIGRATIONS))
    if out.get(".gitignore", ("",))[0] == "file":
        data = (dest / ".gitignore").read_bytes()
        ids = [p.name for p in (dest / migration.MIGRATIONS).iterdir()] if (dest / migration.MIGRATIONS).is_dir() else []
        for migration_id in ids:
            data = data.replace(migration_id.encode(), b"<id>")
        out[".gitignore"] = ("file", len(data), hashlib.sha256(data).hexdigest())
    return out


def mdir(dest: Path) -> Path:
    [directory] = list((dest / migration.MIGRATIONS).iterdir())
    return directory


def ledger(dest: Path) -> migration.Ledger:
    return migration.Ledger.read(mdir(dest))


def old_find_project(start: Path) -> Path | None:
    """The search of a kit before #13 (lab.json only): it must not find a destination."""
    for candidate in (start, *start.parents):
        if (candidate / "lab.json").is_file():
            return candidate
    return None


# --- sources --------------------------------------------------------------------------------------

def rich_source(base: Path) -> tuple[Path, Path]:
    """A lab.json project as an older kit leaves one, with what design 11.2's T1 lists: a journal of
    LF and CRLF lines, a line a /1 writer appended onto a cut one, a /2 line, a line of an unknown
    family and a cut last line; Korean and NFD names, an empty file, a file larger than a chunk, a
    read-only file; .lab/'s history, logs, cache and locks; what is not copied (.venv/, report/,
    __pycache__/, .DS_Store); scripts with the older SDK's names; links leading outside (a folder and a
    file), a link inside made again, a dangling link inside; an older kit's run that recorded the root
    and the outside folder by their absolute paths. Returns (the source, the folder outside it)."""
    ext = base / "ext"
    (ext / "nested").mkdir(parents=True)
    (ext / "nested" / "reading.csv").write_bytes(b"t,v\r\n1,2\r\n")
    (base / "ext_file.csv").write_bytes(b"outside file\n")
    p = LegacyProject(base / "src", title="원본 분석")
    p.file("CONTEXT.md", "## Goal\n원본 목표\n".encode())
    p.file(".gitignore", (migration.TEMPLATE / "gitignore").read_bytes())
    p.file("pyproject.toml", b"[project]\nname = 'x'\n")
    p.file(".lab/context/20261001-090000-000000_aaaaaaaaaaaa.md", b"## Goal\nold\n")
    p.file(".lab/telemetry.jsonl", b'{"tool": "analytics_status"}\n')
    p.file(".lab/queries.jsonl", b'{"sql": "select 1"}\r\n')
    p.file(".lab/adhoc/20261001-090000/out.csv", b"a\n1\n")
    p.file(".lab/cache/hashes.json", b"{}")
    p.file(".lab/lock", b"")
    p.file("report/index.html", b"<html></html>")
    p.file(".venv/pyvenv.cfg", b"home = /x\n")
    p.file("scripts/__pycache__/a.cpython-312.pyc", b"\x00\x01")
    p.file(".DS_Store", b"\x00\x00\x00\x01Bud1")
    p.file("data/raw/a.parquet", b"PAR1a")
    p.file(BIG, os.urandom(2 * migration.CHUNK + 12345))
    p.file("data/derived/매출.parquet", "한글".encode())
    p.file("data/derived/" + "café.csv", b"nfd\n")
    p.file("data/derived/empty.csv", b"")
    p.file("scripts/old.py", b"import labkit as lab\r\nlab.note('gap', 'x')\r\nlab.chart(fig, 'trend')\r\n")
    p.file("scripts/ok.py", b"import aelix_analytics as analytics\nanalytics.record('finding', 'y')\n")
    readonly = p.root / "scripts" / "frozen.py"
    readonly.write_bytes(b"print('frozen')\n")
    os.chmod(readonly, stat.S_IREAD)
    os.symlink(str(ext), p.root / "linked", target_is_directory=True)
    os.symlink(str(base / "ext_file.csv"), p.root / "one.csv")
    os.symlink(os.path.join("raw", "a.parquet"), p.root / "data" / "alias.parquet")
    os.symlink("missing.csv", p.root / "data" / "dangling.csv")
    p.event("finding", text="첫 발견")
    p.raw(json.dumps({"schema": "lab.event/1", "seq": 2, "id": "e2", "ts": at(2), "kind": "note",
                      "actor": "test", "text": "CRLF", "refs": {}}).encode() + b"\r\n")
    p.seq = 2
    p.raw(b'{"schema": "lab.event/1", "seq": 3, "id": "e3", "ts": "2026-10-01T09:0')  # cut
    p.event("note", text="appended onto the cut line")  # P7
    p.line({"schema": "analytics.event/2", "seq": 5, "id": "e5", "recorded_at": at(5), "kind": "note",
            "actor": "human", "text": "a /2 line", "refs": []})
    p.line({"schema": "other.family/1", "seq": 6, "id": "e6"})
    p.seq = 6
    p.run("20261001-091000-old", inputs=[(str(p.root / "data" / "raw" / "a.parquet"),
                                          hashlib.sha256(b"PAR1a").hexdigest()),
                                         (str(ext.resolve() / "nested" / "reading.csv"),
                                          hashlib.sha256(b"t,v\r\n1,2\r\n").hexdigest())],
          outputs=[("data/derived/매출.parquet", hashlib.sha256("한글".encode()).hexdigest())],
          kit_version="0.4.0")
    p.raw(b'{"schema": "lab.event/1", "seq": 9, "id": "e9", "ts": "2026-10')  # the last line, cut
    return p.root, ext


def plain_source(base: Path, name: str = "src") -> Path:
    p = LegacyProject(base / name)
    p.file("data/raw/a.parquet", b"PAR1a")
    p.file(".lab/context/20261001-090000-000000_aaaaaaaaaaaa.md", b"ctx")
    p.file(".gitignore", b".lab/cache/\nreport/\n")
    p.event("note", text="n")
    return p.root


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    """One rich source for the F table, and the copies an uninterrupted migration of it gives."""
    base = tmp_path_factory.mktemp("migration-source")
    patch = pytest.MonkeyPatch()
    patch.setenv("AELIX_ANALYTICS_TRUST_FILE", str(base / "trust" / "lab-projects.json"))
    try:
        source, ext = rich_source(base)
        before = source_tree(source)
        result = migration.migrate(source, base / "reference", origin=ORIGIN)
    finally:
        patch.undo()
    assert result["ok"] and not result["already_migrated"], result
    return {"source": source, "ext": ext, "reference": copies(base / "reference"), "before": before,
            "result": result, "base": base}


def migrate(source, dest, **kwargs) -> dict:
    return migration.migrate(source, dest, origin=ORIGIN, **kwargs)


def refused(code: str, call, **kwargs) -> core.AnalyticsError:
    with pytest.raises(core.AnalyticsError) as caught:
        call(**kwargs) if kwargs else call()
    assert caught.value.code == code, caught.value.to_json()
    return caught.value


def interrupt(source, dest: Path, stop: dict, *, no_links: bool = False, code: int = 9) -> dict | None:
    """The migration in a process of its own, ended at `stop` (tests/migration_driver.py)."""
    args = {"source": None if source is None else str(source), "dest": str(dest), "stop": stop,
            "no_links": no_links}
    done = subprocess.run([sys.executable, str(DRIVER), json.dumps(args)], capture_output=True,
                          env=kit_env(), timeout=300, **_platform.text_kwargs())
    assert done.returncode == code, (done.returncode, done.stdout, done.stderr)
    lines = [line for line in done.stdout.split("\n") if line.startswith("{")]
    return json.loads(lines[-1]) if lines else None


def assert_uncommitted(dest: Path, *, trusted: bool = False) -> None:
    """T3: before its marker a destination is no project to any kit — refused by this one
    (migration_incomplete, reads included), passed over by an older one — and not in the trust list
    (unless it was trusted once committed, `trusted`)."""
    assert not (dest / writer.MARKER).exists()
    assert writer.marker_state(dest).state == "incomplete"
    assert writer.find_project(dest) == dest
    for call in (lambda: core.admit(dest, "record"), lambda: core.readable(dest, "status")):
        refused("migration_incomplete", call)
    assert old_find_project(dest) is None
    assert core.is_trusted(dest) is trusted


def assert_committed(dest: Path, source: Path | None = None, *, trusted: bool = True) -> dict:
    """A committed destination as S2-S4 say: the marker binds the report, the report the mapping and
    every copy, the records follow the schema, the ledger's lines theirs, no temporary file is left,
    and (`trusted`: in this test's trust list) the destination is trusted."""
    marker = json.loads((dest / writer.MARKER).read_text(encoding="utf-8"))
    assert writer.marker_state(dest).state == "current"
    assert not list(RECORD.iter_errors(marker)), list(RECORD.iter_errors(marker))
    report_bytes = (dest / marker["migration"]["report"]).read_bytes()
    assert hashlib.sha256(report_bytes).hexdigest() == marker["migration"]["report_sha256"]
    report = json.loads(report_bytes)
    own = mdir(dest)
    for name in ("report.json", "mapping.json", *sorted(p.name for p in own.glob("plan*.json"))):
        value = json.loads((own / name).read_text(encoding="utf-8"))
        assert not list(RECORD.iter_errors(value)), (name, [e.message for e in RECORD.iter_errors(value)])
    mapping = (dest / report["mapping"]["path"]).read_bytes()
    assert hashlib.sha256(mapping).hexdigest() == report["mapping"]["sha256"]
    assert set(migration.compare_copies(dest, report).values()) == {"same"}
    for item in report["added"]:
        assert hashlib.sha256((dest / item["path"]).read_bytes()).hexdigest() == item["sha256"], item
    for raw in (own / "progress.jsonl").read_bytes().split(b"\n"):
        value = migration._loads(raw) if raw.strip() else None
        if value is not None:  # a line cut off is skipped by the reader as well (F16)
            assert not list(LINE.iter_errors(value)), value
    read = migration.Ledger.read(own)
    assert read.committed and (read.trusted or not read.lines[-1]["step"] == "trusted")
    copied = {f.get("to") or f["path"] for f in report["files"]}
    leftovers = [p for p in tree(dest) if os.path.basename(p).endswith(".part") and p not in copied]
    assert leftovers == [f for f in report["leftover_temp"]], leftovers
    assert core.is_trusted(dest) or not trusted
    if source is not None:
        assert report["source"]["path"] == str(core.Project(source).root)
    return report


# --- a migration from end to end (T1, T4, T16, S4) --------------------------------------------------

def test_a_migration_copies_bytes_moves_only_what_the_mapping_says_and_commits_last(shared):
    base, source = shared["base"], shared["source"]
    dest = base / "reference"
    report = assert_committed(dest, source, trusted=False)  # the fixture's own trust list
    assert source_tree(source) == shared["before"], "S1: the source changed"
    copied = copies(dest)
    original = source_tree(source)
    moved = {".lab/context/20261001-090000-000000_aaaaaaaaaaaa.md":
             ".analytics/context/20261001-090000-000000_aaaaaaaaaaaa.md",
             ".lab/telemetry.jsonl": ".analytics/telemetry.jsonl",
             ".lab/queries.jsonl": ".analytics/queries.jsonl",
             ".lab/adhoc/20261001-090000/out.csv": ".analytics/adhoc/20261001-090000/out.csv"}
    for rel, value in original.items():
        if rel in ("lab.json", ".gitignore", ".DS_Store") or rel.split("/")[0] in (".venv", "report") \
                or "__pycache__" in rel or rel.startswith(".lab/cache") or rel in ("linked", "one.csv"):
            continue
        if rel == ".lab" or rel.startswith(".lab/") and rel not in moved and value[0] == "dir":
            continue
        assert copied[moved.get(rel, rel)] == value, rel  # S4: the same bytes (CRLF too, S12)
    # Links outside: their targets' bytes at the link's path, the links themselves gone.
    assert copied["linked"] == ("dir",)
    assert copied["linked/nested/reading.csv"][2] == hashlib.sha256(b"t,v\r\n1,2\r\n").hexdigest()
    assert copied["one.csv"] == ("file", 13, hashlib.sha256(b"outside file\n").hexdigest())
    assert copied["data/alias.parquet"] == ("link", os.path.join("raw", "a.parquet"))  # it stays inside
    assert copied["data/dangling.csv"] == ("link", "missing.csv")
    # Nothing of the older layout, nothing excluded.
    for rel in copied:
        assert not rel.startswith((".lab", "report", ".venv")) and "__pycache__" not in rel, rel
        assert rel != ".DS_Store" and rel != "lab.json", rel
    excluded = {(e["path"], e["reason"]) for e in report["excluded"]}
    assert excluded >= {(".venv", "environment"), ("report", "workspace"), (".lab/cache", "cache"),
                        (".lab/lock", "lock"), ("scripts/__pycache__", "cache"),
                        (".DS_Store", "os_metadata")}, excluded
    # lab.json's bytes are kept under the migration's directory, never as lab.json.
    kept = (dest / report["source"]["marker"]["kept_at"]).read_bytes()
    assert kept == (source / "lab.json").read_bytes() and not (dest / "lab.json").exists()
    # .gitignore: the copy with the block appended, and only that.
    before = (source / ".gitignore").read_bytes()
    after = (dest / ".gitignore").read_bytes()
    assert after.startswith(before) and report["transformed"][0]["before"] == \
        hashlib.sha256(before).hexdigest()
    added = report["transformed"][0]["added"]
    assert added == [".analytics/cache/", ".analytics/lock", ".analytics/adhoc/", "workspace/",
                     "runs/*/visualizations/*.data.parquet", *migration.GITIGNORE_ALWAYS]
    # The marker: the source's title and creation time, a new project id, the report bound.
    marker = json.loads((dest / "analytics.json").read_text(encoding="utf-8"))
    assert (marker["title"], marker["created_at"]) == ("원본 분석", at(0))
    assert marker["project_id"] == report["destination"]["project_id"] == shared["result"]["project_id"]
    # The read-only file is read-only in the copy too; the root and .analytics/ stay writable.
    assert not os.stat(dest / "scripts" / "frozen.py").st_mode & stat.S_IWUSR
    assert os.access(dest, os.W_OK) and os.access(dest / ".analytics", os.W_OK)
    # The journal's boundary is the copy's: its cut lines counted, nothing rewritten.
    assert report["source"]["journal"]["damaged"] == 2 and report["source"]["journal"]["recovered"] == 1
    assert report["source"]["journal"]["unsupported"] == 1
    assert (dest / "journal.jsonl").read_bytes() == (source / "journal.jsonl").read_bytes()


def assert_locks_named(dest: Path, source: Path) -> None:
    """S1: a lock file the migration made in the source is named in its ledger and its report."""
    read = ledger(dest)
    existed = {item["path"]: item["existed"] for item in read.start["locks"]}
    for rel in LOCKS:
        if not existed[rel]:
            made = [item for v in read.lines if v["step"] == "locked" and isinstance(v.get("locks"), list)
                    for item in v["locks"] if item["path"] == rel and item.get("created")]
            assert made or not (source / rel).exists(), rel


def test_the_report_and_the_proposals(shared):
    """T4 and design 6.13: the scripts with the older SDK's names get a proposal (their bytes kept but
    the edits, CRLF included), .gitattributes is proposed and not made, and the copies are not
    touched."""
    dest = shared["base"] / "reference"
    report = json.loads((mdir(dest) / "report.json").read_text(encoding="utf-8"))
    proposals = report["proposals"]
    [script] = proposals["scripts"]
    assert script["path"] == "scripts/old.py"
    proposed = (dest / script["proposal"]).read_bytes()
    assert proposed == (b"import aelix_analytics as lab\r\nlab.record('workflow_feedback', 'x')\r\n"
                        b"lab.visualize(fig, 'trend')\r\n")
    assert (dest / "scripts" / "old.py").read_bytes() == (shared["source"] / "scripts" / "old.py").read_bytes()
    assert b"-import labkit as lab" in (dest / proposals["scripts_diff"]).read_bytes()
    assert (dest / proposals["gitattributes"]).read_bytes() == (migration.TEMPLATE / "gitattributes").read_bytes()
    assert not (dest / ".gitattributes").exists()
    links = {link["path"]: link["treatment"] for link in report["links"]}
    assert links == {"linked": "copied_as_directory", "one.csv": "copied_as_file"}
    summary = (mdir(dest) / "report.md").read_text(encoding="utf-8")
    assert summary.startswith(f"# Migration {report['migration_id']}") and "kept as it was" in summary


@pytest.mark.parametrize(("script", "proposed", "manual"), [
    (b"from labkit import note, chart as c\nnote('gap', 'x')\nc(f, 'y')\n",
     b"from aelix_analytics import record as note, visualize as c\nnote('workflow_feedback', 'x')\nc(f, 'y')\n", 0),
    (b"import labkit\nlabkit.note('finding', 'x')\n",
     b"import aelix_analytics as labkit\nlabkit.record('finding', 'x')\n", 0),
    (b"import aelix_analytics as a\na.record(\"gap\", 'x')\n",
     b"import aelix_analytics as a\na.record(\"workflow_feedback\", 'x')\n", 0),
    (b"import importlib\nlab = importlib.import_module('labkit')\n", None, 1),
    (b"x = 1\n", None, 0),
    (b"def broken(:\n", None, 0),
])
def test_a_script_proposal_renames_only_the_older_names(script, proposed, manual):
    new, changes, reasons = migration.script_proposal(script)
    assert new == proposed
    assert len(reasons) == manual and all(r["reason"] == "manual" for r in reasons)
    assert bool(changes) is (proposed is not None)


@pytest.mark.parametrize(("before", "lines"), [
    (b".lab/cache/\nreport/\n", [".analytics/cache/", "workspace/", *migration.GITIGNORE_ALWAYS]),
    (b"data/\r\n.lab/lock\r\n", [".analytics/lock", *migration.GITIGNORE_ALWAYS]),
    (b"report/\nworkspace/\n.analytics/run.lock", [*migration.GITIGNORE_ALWAYS[1:]]),
    (b"", list(migration.GITIGNORE_ALWAYS)),
])
def test_the_gitignore_block_follows_the_source_s_choices(before, lines):
    """design 6.13: an older template line the source has gets its new name; a line it does not have
    stays its choice; the new files' lines always; the file's own line end; nothing before changes."""
    after, added = migration.gitignore_block(before, "20261008T010203Z-a1b2c3d4")
    assert added == lines and after.startswith(before)
    eol = b"\r\n" if b"\r\n" in before else b"\n"
    tail = after[len(before):]
    assert tail.endswith(eol) and (b"\r\n" in tail) == (eol == b"\r\n")


# --- the F table (T6, design 6.11) ---------------------------------------------------------------

# name: (the stop, what calling the same migration again gives: "resume" or "already" or an error
# code, whether that call needs the source given)
FAULTS = {
    "F1-dest-locked": ({"step": "dest_locked"}, "resume"),
    "F1-migration-dir": ({"step": "migration_dir"}, "resume"),
    "F1-start-cut": ({"step": "ledger:start", "action": "cut"}, "resume"),
    "F2-start": ({"step": "start"}, "resume"),
    "F2-probe": ({"step": "probe_made"}, "resume"),
    "F3-locked": ({"step": "locked"}, "resume"),
    "F4-plan-written": ({"step": "plan_written"}, "resume"),
    "F4-plan": ({"step": "plan"}, "resume"),
    "F5-begin": ({"step": "begin", "path": BIG}, "resume"),
    "F5-temp-partial": ({"step": "temp_partial", "path": BIG}, "resume"),
    "F5-temp-full": ({"step": "temp_full", "path": BIG}, "resume"),
    "F6-published": ({"step": "published", "path": BIG}, "resume"),
    "F6-link-unlink": ({"between_link_and_unlink": True, "path": BIG}, "resume"),
    "F7-rechecking": ({"step": "rechecking"}, "resume"),
    "F8-source-checked": ({"step": "source_checked"}, "resume"),
    "F8-verified": ({"step": "verified"}, "resume"),
    "F9-transform-begin": ({"step": "transform_begin"}, "resume"),
    "F9-transform-temp": ({"step": "transform_temp"}, "resume"),
    "F9-transform-written": ({"step": "transform_written"}, "resume"),
    "F9-transform-replaced": ({"step": "transform_replaced"}, "resume"),
    "F10-mapping": ({"step": "mapping_written"}, "resume"),
    "F10-report-written": ({"step": "report_written"}, "resume"),
    "F10-reported": ({"step": "reported"}, "resume"),
    "F10-durable": ({"step": "durable"}, "resume"),
    "F11-marker-begin": ({"step": "marker_begin"}, "resume"),
    "F11-marker-temp": ({"step": "marker_temp"}, "resume"),
    "F11-marker-written": ({"step": "marker_written"}, "resume"),
    "F12-published": ({"step": "marker_published"}, "already"),
    "F12-link-unlink": ({"between_link_and_unlink": True, "path": "analytics.json"}, "already"),
    "F13-committed": ({"step": "committed"}, "already"),
    "F14-trusted": ({"step": "trusted"}, "already"),
    "F16-copied-cut": ({"step": "ledger:copied", "action": "cut", "nth": 3}, "resume"),
    "F16-source-checked-cut": ({"step": "ledger:source_checked", "action": "cut"}, "resume"),
    "F16-reported-cut": ({"step": "ledger:reported", "action": "cut"}, "resume"),
    "F16-committed-cut": ({"step": "ledger:committed", "action": "cut"}, "already"),
}
COMMITTED = {"F12-published", "F12-link-unlink", "F13-committed", "F14-trusted", "F16-committed-cut"}
SEEN: dict[str, dict] = {}


@pytest.mark.parametrize("name", FAULTS)
def test_an_interrupted_migration_resumes_to_the_same_copies(shared, tmp_path, name):
    stop, expected = FAULTS[name]
    if stop.get("between_link_and_unlink") and not POSIX:
        pytest.skip("Windows publishes by a rename: there is no window between a link and an unlink")
    source = shared["source"]
    dest = tmp_path / "dest"
    interrupt(source, dest, stop)
    assert source_tree(source) == shared["before"], "S1"
    if name in COMMITTED:
        assert writer.marker_state(dest).state == "current"
        assert old_find_project(dest) is None
    else:
        assert_uncommitted(dest)
    stray = tree(dest)
    result = migrate(source, dest)
    assert result["ok"] and result["already_migrated"] is (expected == "already"), result
    if name == "F12-link-unlink":
        assert not any(p.endswith(".part") for p in tree(dest)), "the marker's second name stays"
    report = assert_committed(dest, source)
    assert copies(dest) == shared["reference"], name
    assert source_tree(source) == shared["before"], "S1"
    assert_locks_named(dest, source)
    again = migrate(source, dest)
    assert again["already_migrated"] and again["compared"] == {"same": len(report["files"])}
    SEEN[name] = {"interrupted": sorted(set(stray) - set(copies(dest)))[:5], "result": expected,
                  "steps": [v["step"] for v in ledger(dest).lines][-4:]}


@pytest.mark.parametrize("stop", ["direct_intent", "direct_created"])
def test_a_direct_write_where_links_cannot_be_made(shared, tmp_path, stop):
    """F15 (R20): where the file system gives no second name, a copy is written directly — its
    intent first, then the file made exclusively, then its identity. Cut before the file is made,
    the same call goes on; cut after it is made and before its identity is written, the file cannot
    be told to be this migration's and the call is refused (occupied), the file kept."""
    source = shared["source"]
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": stop, "path": BIG}, no_links=True)
    assert_uncommitted(dest)
    if stop == "direct_intent":
        interrupt(source, dest, {}, no_links=True, code=0)
        assert copies(dest) == shared["reference"]
        assert_committed(dest, source)
        assert any(v["step"] == "direct" for v in ledger(dest).lines)
    else:
        before = tree(dest)
        error = refused("destination_mismatch", lambda: migrate(source, dest))
        assert error.details == {"reason": "occupied", "path": BIG}
        assert tree(dest) == {**before, **{k: v for k, v in tree(dest).items() if k.endswith(
            "progress.jsonl")}}, "nothing removed"
    assert source_tree(source) == shared["before"]


def test_the_fault_matrix_is_kept(shared):
    """The F table's observations, for the measurement record (not asserted here)."""
    from conftest import record_observation

    record_observation("issue-13-migration/fault-matrix", SEEN)


# --- the rows of design 6.10 (T6 a–o) -------------------------------------------------------------

def _copy_source(tmp_path: Path) -> Path:
    source = plain_source(tmp_path)
    (source / "scripts").mkdir()
    for n in range(4):
        (source / "scripts" / f"s{n}.py").write_bytes(f"print({n})\n".encode())
    return source


def test_a_copied_file_changed_between_two_calls_is_refused_and_nothing_removed(tmp_path):
    """(a): before the source was checked, a file copied already changed in the source: the copy
    cannot be overwritten, so source_changed, and nothing is removed."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "copied", "path": "scripts/s1.py"})
    (source / "scripts" / "s0.py").write_bytes(b"print('changed')\n")
    before = tree(dest)
    error = refused("source_changed", lambda: migrate(source, dest))
    assert "scripts/s0.py" in error.details["paths"]
    assert {k: v for k, v in tree(dest).items() if not k.endswith("progress.jsonl")} == \
        {k: v for k, v in before.items() if not k.endswith("progress.jsonl")}


def test_a_file_not_copied_yet_that_changed_is_copied_as_it_is_now(tmp_path):
    """(b): a file the interrupted migration had not copied yet changed: the new plan takes it as it
    is, and the copy is the source as it is now."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "copied", "path": "scripts/s0.py"})
    (source / "scripts" / "s3.py").write_bytes(b"print('later')\n")
    (source / "scripts" / "s4.py").write_bytes(b"print('new')\n")
    migrate(source, dest)
    assert_committed(dest, source)
    assert (dest / "scripts" / "s3.py").read_bytes() == b"print('later')\n"
    assert (dest / "scripts" / "s4.py").read_bytes() == b"print('new')\n"
    assert len(migration.Ledger.read(mdir(dest)).plans) == 2


@pytest.mark.parametrize("what", ["journal_appended", "source_removed", "space_short", "link_target_gone"])
def test_after_the_source_check_the_source_is_not_read_again(tmp_path, what):
    """(c), F8 (design 6.1, 6.2, D28): checked already, a migration goes on from its snapshot — the
    source changed, removed (and not given), its outside folder gone, or the disk short of a second
    copy — with no source-side check, and its mapping is an uninterrupted migration's."""
    source = _copy_source(tmp_path)
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "e.csv").write_bytes(b"e\n")
    os.symlink(str(ext), source / "linked", target_is_directory=True)
    reference = tmp_path / "reference"
    migrate(source, reference)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "source_checked"})
    given = source
    kwargs = {}
    if what == "journal_appended":
        with open(source / "journal.jsonl", "ab") as fh:
            fh.write(json.dumps({"schema": "lab.event/1", "seq": 2, "id": "e2", "ts": at(5), "kind": "note",
                                 "actor": "test", "text": "late", "refs": {}}).encode() + b"\n")
    elif what == "source_removed":
        shutil.rmtree(source)
        given = None
    elif what == "space_short":
        kwargs["free_space"] = 1
    else:
        shutil.rmtree(ext)
    result = migrate(given, dest, **kwargs)
    assert result["ok"] and not result["already_migrated"]
    report = assert_committed(dest)
    ref_mapping = json.loads((mdir(reference) / "mapping.json").read_text(encoding="utf-8"))
    mapping = json.loads((mdir(dest) / "mapping.json").read_text(encoding="utf-8"))
    for value in (ref_mapping, mapping):
        value.pop("migration_id")
        value["root"].pop("to")
        value["renamed"] = [{**r, "to": r["to"].split("/")[-1]} for r in value["renamed"]]
    assert mapping == ref_mapping
    assert copies(dest) == copies(reference)
    if what == "journal_appended":
        again = migrate(source, dest)
        assert again["source_since"]["journal_lines"] == 1 and again["source_since"]["seq"] == [2, 2]
    if what == "source_removed":
        assert report["source"]["given"] == "not_given"


def test_a_gitignore_transform_resumes_from_its_hash_and_refuses_another(shared, tmp_path):
    """(d), F9: a .gitignore that is neither the copy nor its transform is refused."""
    source = shared["source"]
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "transform_written"})
    (dest / ".gitignore").write_bytes(b"changed by a person\n")
    refused("destination_mismatch", lambda: migrate(source, dest))


def test_a_committed_destination_whose_marker_went_is_published_again(shared, tmp_path):
    """(e)(f), 6.10's last row: committed, then the marker removed, the source gone and not given:
    the same marker bytes are published again where the report and the copies are the ledger's."""
    source = shared["source"]
    dest = tmp_path / "dest"
    interrupt(source, dest, {}, code=0)
    marker = (dest / "analytics.json").read_bytes()
    (dest / "analytics.json").unlink()
    assert_uncommitted(dest, trusted=True)
    result = migrate(None, dest)
    assert result["already_migrated"] and result.get("republished")
    assert (dest / "analytics.json").read_bytes() == marker
    (dest / "analytics.json").unlink()
    (dest / "scripts" / "ok.py").write_bytes(b"edited\n")
    error = refused("destination_mismatch", lambda: migrate(None, dest))
    assert "scripts/ok.py" in error.details["paths"]


@pytest.mark.parametrize("damage", ["order", "two_ledgers", "unknown_line"])
def test_a_ledger_that_is_no_state_is_refused_and_nothing_removed(tmp_path, damage):
    """(g): lines out of order (source_checked with no copy), two migrations' records, or a line that
    parses and is no step: destination_mismatch, nothing removed."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "plan"})
    progress = mdir(dest) / "progress.jsonl"
    if damage == "order":
        migration.append_line(progress, {"step": "source_checked", "at": at(1)})
        migration.append_line(progress, {"step": "verified", "at": at(1)})
    elif damage == "two_ledgers":
        other = dest / migration.MIGRATIONS / "20261008T000000Z-00000000"
        other.mkdir()
        shutil.copy(progress, other / "progress.jsonl")
    else:
        migration.append_line(progress, {"step": "rewound", "at": at(1)})
    before = tree(dest)
    refused("destination_mismatch", lambda: migrate(source, dest))
    assert tree(dest) == before


def test_a_temporary_file_whose_path_left_the_source_is_kept(tmp_path):
    """(h): a copy begun (its temporary file whole) whose path the source no longer has: the
    temporary file is not removed — its bytes may be nowhere else — and the report names it."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "temp_full", "path": "scripts/s2.py"})
    (source / "scripts" / "s2.py").unlink()
    result = migrate(source, dest)
    report = json.loads((mdir(dest) / "report.json").read_text(encoding="utf-8"))
    [kept] = report["leftover_temp"]
    assert kept.startswith("scripts/.mig-") and (dest / kept).read_bytes() == b"print(2)\n"
    assert result["leftover_temp"] == [kept]
    assert_committed(dest, source)


@pytest.mark.parametrize("stray", ["file", "changed_copy", "directory"])
def test_what_the_ledger_does_not_account_for_is_refused(tmp_path, stray):
    """(i): a file no plan names, a copy whose bytes changed, a directory no plan names."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "copied", "path": "scripts/s3.py"})
    if stray == "file":
        (dest / "notes.txt").write_bytes(b"mine")
    elif stray == "changed_copy":
        (dest / "scripts" / "s0.py").write_bytes(b"edited")
    else:
        (dest / "mine").mkdir()
    before = tree(dest)
    error = refused("destination_mismatch", lambda: migrate(source, dest))
    assert error.details["reason"] == "unaccounted"
    assert {k: v for k, v in tree(dest).items() if not k.endswith("progress.jsonl")} == \
        {k: v for k, v in before.items() if not k.endswith("progress.jsonl")}


def test_what_a_person_s_tools_leave_is_passed_over(tmp_path):
    """(j), OQ16: .venv/ at the root, __pycache__/ and the OS's views anywhere — not in the plan — are
    passed over and left as they are."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "copied", "path": "scripts/s1.py"})
    (dest / ".venv").mkdir()
    (dest / ".venv" / "pyvenv.cfg").write_bytes(b"x")
    (dest / ".DS_Store").write_bytes(b"x")
    (dest / "scripts" / "Thumbs.db").write_bytes(b"x")
    (dest / "scripts" / "__pycache__").mkdir()
    migrate(source, dest)
    assert (dest / ".venv" / "pyvenv.cfg").read_bytes() == b"x"
    assert (dest / "scripts" / "Thumbs.db").exists() and (dest / ".DS_Store").exists()
    assert writer.marker_state(dest).state == "current"


@pytest.mark.parametrize("ledger_bytes", [None, b"", b'{"step": "start", "at": "2026-'])
def test_a_migration_directory_without_its_first_line_is_a_new_migration(tmp_path, ledger_bytes):
    """(k), F1: a migration directory with no ledger, an empty one, or its first line cut, and nothing
    else, is step 2's own: removed, and the migration starts again under a new id."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    old = dest / migration.MIGRATIONS / "20261008T000000Z-00000000"
    old.mkdir(parents=True)
    (dest / migration.DEST_LOCK).write_bytes(b"")
    if ledger_bytes is not None:
        (old / "progress.jsonl").write_bytes(ledger_bytes)
    assert_uncommitted(dest)
    result = migrate(source, dest)
    assert result["migration_id"] != old.name and not old.exists()
    assert_committed(dest, source)


def test_a_cut_ledger_line_is_skipped_and_the_next_one_starts_on_its_own_line(tmp_path):
    """(n), F16: the line cut off is skipped, the state is the line before it's, and the next append
    puts an LF first."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "ledger:copied", "action": "cut", "nth": 2})
    data = (mdir(dest) / "progress.jsonl").read_bytes()
    assert not data.endswith(b"\n")
    read = ledger(dest)
    assert len(read.copied) == 1
    migrate(source, dest)
    data = (mdir(dest) / "progress.jsonl").read_bytes()
    lines = data.split(b"\n")
    cut = [line for line in lines if line and migration._loads(line) is None]
    assert len(cut) == 1, "the cut line stays where it was, alone on its line"
    assert_committed(dest, source)


@pytest.mark.skipif(not POSIX or ROOT_USER, reason="a folder's write permission: POSIX, not as root")
def test_a_read_only_source_is_copied_without_its_locks_and_the_destination_stays_writable(tmp_path):
    """(o), T1, OQ15: a source no lock file can be made in (read-only folders and files) is copied
    without locks (`locks: not_taken` and why, in the ledger and the report) and kept by the
    comparison alone; the destination's root and .analytics/ are writable, so steps 9 and 11 end."""
    source = plain_source(tmp_path)
    os.unlink(source / ".lab" / "context" / "20261001-090000-000000_aaaaaaaaaaaa.md")
    paths = [Path(d) for d, _, _ in os.walk(source)]
    for directory, _, names in os.walk(source):
        for name in names:
            os.chmod(Path(directory, name), stat.S_IREAD)
    for directory in reversed(paths):
        os.chmod(directory, stat.S_IREAD | stat.S_IEXEC)
    try:
        before = tree(source)
        dest = tmp_path / "dest"
        result = migrate(source, dest)
        assert result["ok"]
        assert tree(source) == before, "nothing made in a read-only source, its locks included"
        report = assert_committed(dest)
        assert report["source"]["locks"]["taken"] == "not_taken"
        assert "PermissionError" in report["source"]["locks"]["reason"]
        assert os.access(dest, os.W_OK) and os.access(dest / ".analytics", os.W_OK)
        assert not os.stat(dest / ".gitignore").st_mode & stat.S_IWUSR
    finally:
        for directory in paths:
            os.chmod(directory, stat.S_IRWXU)


def test_a_source_without_its_state_folder_makes_none(tmp_path):
    """T1, OQ15: no .lab/: no folder made in the source for a lock; `locks: not_taken`."""
    p = LegacyProject(tmp_path / "src")
    p.file("data/raw/a.parquet", b"a")
    before = tree(p.root)
    migrate(p.root, tmp_path / "dest")
    assert tree(p.root) == before and not (p.root / ".lab").exists()
    report = assert_committed(tmp_path / "dest")
    assert report["source"]["locks"] == {"taken": "not_taken", "reason": "no_state_dir"}


def test_a_worktree_s_git_file_is_not_copied(tmp_path):
    """T1, OQ4: a .git file (a worktree's pointer) is reported, not copied; a .git/ folder is."""
    source = plain_source(tmp_path)
    (source / ".git").write_bytes(b"gitdir: /elsewhere/.git/worktrees/x\n")
    migrate(source, tmp_path / "dest")
    report = assert_committed(tmp_path / "dest")
    assert not (tmp_path / "dest" / ".git").exists()
    assert {"path": ".git", "reason": "git_worktree_pointer"} in report["not_copied"]
    other = plain_source(tmp_path, "src2")
    (other / ".git" / "refs").mkdir(parents=True)
    (other / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    migrate(other, tmp_path / "dest2")
    assert (tmp_path / "dest2" / ".git" / "HEAD").read_bytes() == b"ref: refs/heads/main\n"
    report = assert_committed(tmp_path / "dest2")
    assert any("second repository" in note for note in report["notes"])


def test_a_file_larger_than_64_mib_is_copied_whole(tmp_path):
    """T1: a file past 64 MiB, streamed in chunks."""
    source = plain_source(tmp_path)
    with open(source / "data" / "raw" / "large.bin", "wb") as fh:
        fh.truncate((64 << 20) + 7)
        fh.seek(-7, os.SEEK_END)
        fh.write(b"the end")
    before = source_tree(source)
    migrate(source, tmp_path / "dest")
    assert source_tree(source) == before
    assert_committed(tmp_path / "dest")
    assert tree(tmp_path / "dest")["data/raw/large.bin"] == before["data/raw/large.bin"]


# --- the destination's rules (T2) ------------------------------------------------------------------

@pytest.mark.parametrize("kind", LINK_KINDS)
def test_a_destination_that_overlaps_a_place_the_migration_reads_is_refused(tmp_path, kind):
    """T2, M1: the source, a folder inside it or above it, another spelling of it, and a link's target
    outside (`data -> /ext/data` with --to /ext/data/new, /ext/data, /ext) — overlaps_source with the
    place, and nothing written anywhere."""
    ext = tmp_path / "ext" / "data"
    ext.mkdir(parents=True)
    (ext / "x.parquet").write_bytes(b"x")
    source = plain_source(tmp_path)
    os.symlink(str(ext), source / "linked", target_is_directory=True)
    alias = dir_link(kind, tmp_path / "alias", source)
    before, ext_before = tree(tmp_path / "src"), tree(tmp_path / "ext")
    for dest, place in ((source / "inside", str(source)), (tmp_path, str(source)),
                        (alias / "sub", str(source)), (ext / "new", str(ext.resolve())),
                        (ext, str(ext.resolve())), (tmp_path / "ext", str(ext.resolve()))):
        error = refused("destination_invalid", lambda d=dest: migrate(source, d))
        assert error.details["reason"] == "overlaps_source", dest
        assert core.canonical(error.details["with"]["path"]) == core.canonical(place), (dest, error.details)
    assert tree(tmp_path / "src") == before and tree(tmp_path / "ext") == ext_before
    assert not (ext / "new").exists() and not (source / "inside").exists()
    # The source itself is judged by its marker first (design 6.9): a lab.json project, not empty.
    assert refused("destination_invalid", lambda: migrate(source, source)).details["reason"] == "not_empty"


def test_the_destination_s_other_rules(tmp_path):
    """T2: not empty (a Thumbs.db too), a .DS_Store alone taken as empty (as init does), a file, a
    marker above it, a disk too small — each refused before anything is written."""
    source = plain_source(tmp_path)
    full = tmp_path / "full"
    full.mkdir()
    (full / "x.txt").write_bytes(b"x")
    assert refused("destination_invalid", lambda: migrate(source, full)).details["reason"] == "not_empty"
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    (thumbs / "Thumbs.db").write_bytes(b"x")
    assert refused("destination_invalid", lambda: migrate(source, thumbs)).details["reason"] == "not_empty"
    a_file = tmp_path / "a_file"
    a_file.write_bytes(b"x")
    assert refused("destination_invalid", lambda: migrate(source, a_file)).details["reason"] == \
        "not_a_directory"
    for name in ("lab.json", "analytics.json"):
        above = tmp_path / f"above-{name}"
        above.mkdir()
        (above / name).write_bytes(b"{}")
        error = refused("destination_invalid", lambda a=above: migrate(source, a / "dest"))
        assert error.details["reason"] == "inside_project" and not (above / "dest").exists()
    small = refused("insufficient_space", lambda: migrate(source, tmp_path / "small", free_space=1000))
    assert small.details["free"] == 1000 and not (tmp_path / "small").exists()
    finder = tmp_path / "finder"
    finder.mkdir()
    (finder / ".DS_Store").write_bytes(b"\x00")
    migrate(source, finder)
    assert (finder / ".DS_Store").read_bytes() == b"\x00"
    assert_committed(finder)


def test_the_source_s_own_refusals(tmp_path):
    """T2, OQ10, OQ12: a link outside whose target is gone, a nested project, a name of .lab/ the
    current layout manages, a source that is not a lab.json project, no source — refused before
    anything is written; the dry run shows a missing target first."""
    source = plain_source(tmp_path)
    os.symlink(str(tmp_path / "unmounted" / "data"), source / "external")
    error = refused("source_unreadable", lambda: migrate(source, tmp_path / "d1"))
    assert error.details == {"reason": "link_target_missing",
                             "links": [{"path": "external", "text": str(tmp_path / "unmounted" / "data")}]}
    dry = refused("source_unreadable", lambda: migrate(source, tmp_path / "d1", dry_run=True))
    assert dry.details["plan"]["links_outside"][0]["target"] == "missing"
    os.unlink(source / "external")
    (source / "sub").mkdir()
    (source / "sub" / "lab.json").write_bytes(b"{}")
    assert refused("nested_project", lambda: migrate(source, tmp_path / "d2")).details["paths"] == ["sub/lab.json"]
    shutil.rmtree(source / "sub")
    (source / ".lab" / "migration.lock").write_bytes(b"")
    error = refused("destination_invalid", lambda: migrate(source, tmp_path / "d3"))
    assert error.details["pairs"] == [[".lab/migration.lock", ".analytics/migration.lock"]]
    os.unlink(source / ".lab" / "migration.lock")
    (source / "analytics.json").write_bytes(b"{}")
    assert refused("source_not_legacy", lambda: migrate(source, tmp_path / "d4")).details["state"] == "conflict"
    os.unlink(source / "analytics.json")
    refused("no_project", lambda: migrate(None, tmp_path / "d5"))
    for n in range(1, 6):
        assert not (tmp_path / f"d{n}").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="a name not UTF-8: Linux keeps the bytes it is given")
def test_a_name_that_is_not_utf8_is_refused(tmp_path):
    """T2, OQ12: the bytes and the escaped name are reported; nothing is written."""
    source = plain_source(tmp_path)
    bad = os.fsdecode(b"data/raw/caf\xe9.csv")
    (source / bad).write_bytes(b"x")
    error = refused("source_unreadable", lambda: migrate(source, tmp_path / "dest"))
    [item] = error.details["paths"]
    assert item["path"] == "data/raw/caf\\xe9.csv"
    assert base64.b64decode(item["base64"]).endswith(b"caf\xe9.csv")
    assert not (tmp_path / "dest").exists()


def test_names_the_destination_folds_together_are_refused_only_where_it_folds(tmp_path, monkeypatch):
    """T2, D26: two names that differ only in case are copied as they are where the destination tells
    them apart, and refused (name_collision) where the probe finds that it folds; the probe is
    the file system's answer, stood for here so both branches run on one machine."""
    source = plain_source(tmp_path)
    (source / "data" / "raw" / "Sales.csv").write_bytes(b"1")
    if not (source / "data" / "raw" / "sales.csv").exists():
        (source / "data" / "raw" / "sales.csv").write_bytes(b"2")
    twins = len([p for p in (source / "data" / "raw").iterdir() if p.name.lower() == "sales.csv"]) == 2
    real = migration.probe_destination
    monkeypatch.setattr(migration, "probe_destination",
                        lambda probe, link: {**real(probe, link), "case_folds": True})
    if twins:
        error = refused("destination_invalid", lambda: migrate(source, tmp_path / "folds"))
        assert error.details["reason"] == "name_collision"
        assert error.details["pairs"] == [["data/raw/Sales.csv", "data/raw/sales.csv"]]
        assert_uncommitted(tmp_path / "folds")
    monkeypatch.setattr(migration, "probe_destination",
                        lambda probe, link: {**real(probe, link), "case_folds": False,
                                             "normalization_folds": False})
    migrate(source, tmp_path / "apart")
    assert_committed(tmp_path / "apart")
    (source / "data" / "raw" / ".mig-0123abcd-0.part").write_bytes(b"a file named like a temporary one")
    migrate(source, tmp_path / "temp-name")
    assert (tmp_path / "temp-name" / "data" / "raw" / ".mig-0123abcd-0.part").read_bytes() == \
        b"a file named like a temporary one"


def test_a_relative_link_that_leaves_the_root_is_not_made_again(tmp_path):
    """T2, OQ3 (D29): `/a/proj`'s `x -> ../proj/y` names the source from a sibling destination: its
    file's bytes are copied (made again it would lead into the source); a dangling `z -> ../proj/w`
    is not copied; editing the copy leaves the source's bytes as they are."""
    source = plain_source(tmp_path, "proj")
    (source / "y.csv").write_bytes(b"y")
    os.symlink(os.path.join("..", "proj", "y.csv"), source / "x.csv")
    os.symlink(os.path.join("..", "proj", "w.csv"), source / "z.csv")
    dest = tmp_path / "proj-new"
    migrate(source, dest)
    report = assert_committed(dest)
    assert not os.path.islink(dest / "x.csv") and (dest / "x.csv").read_bytes() == b"y"
    assert not os.path.lexists(dest / "z.csv")
    assert {"path": "z.csv", "reason": "target_missing", "text": os.path.join("..", "proj", "w.csv")} \
        in report["not_copied"]
    (dest / "x.csv").write_bytes(b"edited")
    assert (source / "y.csv").read_bytes() == b"y"


# --- concurrency (T5, T7, T19) ---------------------------------------------------------------------

@pytest.mark.parametrize(("step", "change"), [
    ("copied", "telemetry"), ("copied", "added"), ("copied", "removed"), ("rechecking", "script")])
def test_a_source_changed_under_the_copy_fails_without_a_marker(tmp_path, step, change):
    """T5, S5: an append without the lock (telemetry), a file added, a file removed, a script edited,
    at a point of the copy: source_changed with the path, no marker, and the destination resumable
    or refused by the same rule later."""
    source = _copy_source(tmp_path)
    (source / ".lab" / "telemetry.jsonl").write_bytes(b"{}\n")
    done = []

    def on_step(name, info):
        if name != step or done:
            return
        if change == "telemetry" and info.get("path") != ".analytics/telemetry.jsonl":
            return  # appended once its copy is made
        if change in ("added", "removed") and info.get("path") != "data/raw/a.parquet":
            return
        done.append(name)
        if change == "telemetry":
            with open(source / ".lab" / "telemetry.jsonl", "ab") as fh:
                fh.write(b'{"late": true}\n')
        elif change == "added":
            (source / "data" / "raw" / "new.parquet").write_bytes(b"new")
        elif change == "removed":
            (source / "scripts" / "s3.py").unlink()
        else:
            (source / "scripts" / "s0.py").write_bytes(b"print('edited')\n")

    dest = tmp_path / "dest"
    error = refused("source_changed", lambda: migrate(source, dest, on_step=on_step))
    expected = {"telemetry": ".lab/telemetry.jsonl", "added": "data/raw/new.parquet",
                "removed": "scripts/s3.py", "script": "scripts/s0.py"}[change]
    assert expected in error.details["paths"], error.details
    assert_uncommitted(dest)
    assert ledger(dest).lines[-1]["step"] == "failed"


OLD_WRITER = """
import sys
sys.path.insert(0, {kit!r})
from aelix_analytics import core
project = core.Project({root!r})
open({ready!r}, "w").close()
core.add_note(project, "note", "written while the migration held the lock", origin=core.Origin("test", "cli"))
"""


def test_an_older_kit_s_journal_write_waits_for_the_migration_and_is_counted_after(tmp_path):
    """T5, design 6.12: an older kit's record waits on the journal lock the migration holds, lands
    after it lets go (step 7), is not in the copy, and calling again counts it."""
    source = _copy_source(tmp_path)
    ready = tmp_path / "writer-started"
    started = []

    def on_step(name, info):
        if name == "plan" and not started:
            started.append(subprocess.Popen(
                [sys.executable, "-c", OLD_WRITER.format(kit=str(KIT), root=str(source), ready=str(ready))],
                env=kit_env()))
            deadline = time.monotonic() + 60
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(0.5)  # it is waiting on .lab/lock now
            assert started[0].poll() is None, "the older kit's write did not wait"

    dest = tmp_path / "dest"
    migrate(source, dest, on_step=on_step)
    assert started[0].wait(60) == 0
    assert b"written while the migration held the lock" in (source / "journal.jsonl").read_bytes()
    assert b"written while" not in (dest / "journal.jsonl").read_bytes()
    again = migrate(source, dest)
    assert again["source_since"]["journal_lines"] == 1


def test_one_migration_a_destination_and_none_while_a_run_holds_the_source(tmp_path):
    """T7, S7: a second migration to a destination one holds is destination_busy; a migration while
    another process holds the source's run lock is source_busy at once, and resumes once it is free."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    ready, release = tmp_path / "ready", tmp_path / "release"
    args = {"source": str(source), "dest": str(dest),
            "stop": {"step": "plan", "action": "hold", "ready": str(ready), "release": str(release)}}
    holder = subprocess.Popen([sys.executable, str(DRIVER), json.dumps(args)], env=kit_env(),
                              stdout=subprocess.PIPE, **_platform.text_kwargs())
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists()
        refused("destination_busy", lambda: migrate(source, dest))
        refused("source_busy", lambda: migrate(source, tmp_path / "other"))
    finally:
        release.write_text("go", encoding="utf-8")
        out, _ = holder.communicate(timeout=120)
    assert json.loads(out.strip().split("\n")[-1])["ok"]
    assert_committed(dest, source)
    script = ("import sys, time\nsys.path.insert(0, {kit!r})\nfrom aelix_analytics import core\n"
              "with core.run_lock(core.Project({root!r})):\n    open({ready!r}, 'w').close()\n"
              "    time.sleep(30)\n").format(kit=str(KIT), root=str(source), ready=str(tmp_path / "run-held"))
    runner = subprocess.Popen([sys.executable, "-c", script], env=kit_env())
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / "run-held").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        began = time.monotonic()
        error = refused("source_busy", lambda: migrate(source, tmp_path / "second"))
        assert time.monotonic() - began < 10 and error.details["lock"] == ".lab/run.lock"
        assert_uncommitted(tmp_path / "second")
    finally:
        runner.kill()
        runner.wait(30)
    migrate(source, tmp_path / "second")
    assert_committed(tmp_path / "second", source)


@pytest.mark.parametrize("where", [BIG, "analytics.json"])
def test_a_name_taken_meanwhile_is_never_overwritten(shared, tmp_path, where):
    """T19, M5: another process makes a file at the path a copy (or the marker) is about to be
    published at: destination_mismatch (occupied), that file's bytes as they were, the temporary file
    named in the ledger, and calling again gives the same refusal."""
    source = shared["source"]
    dest = tmp_path / "dest"
    stop = {"step": "temp_full", "path": BIG} if where == BIG else {"step": "marker_written"}
    result = interrupt(source, dest, {**stop, "action": "occupy", "occupy": where,
                                      "bytes": "someone else's"}, code=0)
    assert result["error"]["code"] == "destination_mismatch" and result["error"]["details"] == {
        "reason": "occupied", "path": where}
    assert (dest / where).read_bytes() == b"someone else's"
    read = ledger(dest)
    temp = (read.begun[BIG]["temp"] if where == BIG else read.marker["temp"])
    assert (dest / (f"data/raw/{temp}" if where == BIG else temp)).exists(), "the temporary file stays"
    # Called again: the same refusal (the marker's path holds no marker, so it is judged as one).
    code = "destination_mismatch" if where == BIG else "marker_invalid"
    error = refused(code, lambda: migrate(source, dest))
    if where == BIG:
        assert error.details == {"reason": "occupied", "path": BIG}
    assert (dest / where).read_bytes() == b"someone else's"


# --- the destination judged again (T21, design 6.9) -------------------------------------------------

def test_a_committed_destination_called_again(shared, tmp_path):
    """T21: the same source's committed destination is already_migrated — given, gone and not given,
    its outside folder gone, its disk short — trust added where it is missing and nothing else
    written; another source's is not_empty; a marker rewritten by a newer kit (its migration field
    kept) is already_migrated with a warning; the dry run says the same and writes nothing."""
    source = shared["source"]
    dest = shared["base"] / "reference"
    before = tree(dest)
    for kwargs in ({}, {"free_space": 1}, {"dry_run": True}):
        result = migrate(source, dest, **kwargs)
        assert result["already_migrated"], (kwargs, result)
    assert tree(dest) == before
    other = plain_source(tmp_path, "other")
    error = refused("destination_invalid", lambda: migrate(other, dest))
    assert error.details["reason"] == "not_empty" and error.details["source"] == str(core.Project(source).root)
    # Not trusted any more: the call adds it, and writes nothing in the destination.
    trust = Path(os.environ["AELIX_ANALYTICS_TRUST_FILE"])
    saved = trust.read_bytes() if trust.exists() else None
    trust.write_text(json.dumps({"schema": "lab.trust/1", "projects": []}), encoding="utf-8")
    try:
        assert not core.is_trusted(dest)
        result = migrate(None, dest)
        assert result["already_migrated"] and result["trusted"] and core.is_trusted(dest)
        assert result["source_since"] == {"source": "not_given", "path": str(core.Project(source).root)}
    finally:
        if saved is not None:
            trust.write_bytes(saved)
    assert tree(dest) == before


def test_a_marker_a_newer_kit_rewrote_before_the_commit_line_is_taken(shared, tmp_path):
    """T21, design 6.9 `marker_rewritten`: published, the `committed` line not written, and the marker
    then rewritten (min_writer_version raised, its migration field kept): already_migrated with the
    warning, the line written; a marker whose migration field changed is destination_mismatch."""
    source = shared["source"]
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "marker_published"})
    marker = json.loads((dest / "analytics.json").read_text(encoding="utf-8"))
    (dest / "analytics.json").write_text(json.dumps({**marker, "min_writer_version": "0.9.0"}), encoding="utf-8")
    result = migrate(source, dest)
    assert result["already_migrated"] and result["marker_warnings"] == ["marker_rewritten"]
    assert ledger(dest).committed
    second = tmp_path / "second"
    interrupt(source, second, {"step": "marker_published"})
    marker = json.loads((second / "analytics.json").read_text(encoding="utf-8"))
    (second / "analytics.json").write_text(json.dumps({**marker, "migration": {**marker["migration"],
                                                                            "report_sha256": "0" * 64}}),
                                           encoding="utf-8")
    refused("destination_mismatch", lambda: migrate(source, second))


@pytest.mark.parametrize(("markers", "code"), [
    ({"analytics.json": b"{", "lab.json": b"{}"}, "marker_conflict"),
    ({"analytics.json": b"[1]"}, "marker_invalid"),
    ({"analytics.json": json.dumps({"schema": "analytics.project/3"}).encode()}, "project_too_new"),
    ({"lab.json": b"{}"}, "destination_invalid"),
    ({"analytics.json": json.dumps({"schema": "analytics.project/2", "project_id": "p", "title": "t",
                                    "created_at": at(0), "min_writer_version": "0.7.0"}).encode()},
     "destination_invalid"),
])
def test_a_destination_with_a_marker_is_judged_by_it_first(tmp_path, markers, code):
    """T21, M4: two markers, a broken one, an unknown one, a lab.json, a project no migration made —
    each that marker's error, for the call and for its dry run, nothing written."""
    source = plain_source(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    for name, data in markers.items():
        (dest / name).write_bytes(data)
    before = tree(dest)
    refused(code, lambda: migrate(source, dest))
    refused(code, lambda: migrate(source, dest, dry_run=True))
    assert tree(dest) == before


def test_the_dry_run_writes_nothing_and_says_what_would_happen(shared, tmp_path):
    """design 6.9: the dry run reads only — no lock file, no destination — and gives the source's
    plan (files, bytes, links outside first, the .gitignore lines, the proposals, the room needed)."""
    source = shared["source"]
    before = source_tree(source)
    locks = {rel: (source / rel).exists() for rel in LOCKS}
    result = migrate(source, tmp_path / "dest", dry_run=True)
    assert result["state"] == "new" and not (tmp_path / "dest").exists()
    plan = result["plan"]
    assert plan["links_outside"][0]["path"] == "linked" and plan["script_proposals"] == 1
    assert plan["files"] == shared["result"]["files"]
    assert plan["folding_judged"] is False and plan["gitignore_lines"]
    assert source_tree(source) == before and {r: (source / r).exists() for r in LOCKS} == locks
    interrupt(source, tmp_path / "half", {"step": "copied", "nth": 2})
    half = tree(tmp_path / "half")
    resumable = migrate(source, tmp_path / "half", dry_run=True)
    assert resumable["state"] == "resumable" and resumable["copied"] == 2
    assert tree(tmp_path / "half") == half


# --- the ports through the migration (T15 assembly) and Windows' own cases --------------------------

def test_the_migration_goes_through_the_platform_it_is_given(tmp_path):
    """The bundle of ports a migration is given is the one used: its locks, its create-if-absent
    publication, its flushes."""
    from platform_doubles import recording_platform

    bundle, calls = recording_platform()
    source = plain_source(tmp_path)
    migrate(source, tmp_path / "dest", platform=bundle)
    used = {what for what, _ in calls}
    assert {"locks.try_exclusive", "files.publish_new", "files.flush_handle", "files.flush_dir",
            "files.replace"} <= used, used
    assert ("files.publish_new", "analytics.json") in calls
    assert_committed(tmp_path / "dest", source)
    bundle, calls = recording_platform()
    bundle.files.links = False
    migrate(plain_source(tmp_path, "src2"), tmp_path / "direct", platform=bundle)
    assert any(v["step"] == "direct" for v in ledger(tmp_path / "direct").lines)
    assert_committed(tmp_path / "direct")


@pytest.mark.skipif(os.name != "nt", reason="a file another process holds open without sharing: Windows")
def test_a_source_file_another_program_holds_open_is_reported(tmp_path):
    """design 11.4: a source file opened by another process without sharing is source_unreadable
    (in_use), the source as it was, no marker."""
    import ctypes

    source = plain_source(tmp_path)
    held = source / "data" / "raw" / "a.parquet"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(str(held), 0x80000000, 0, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        before = tree(source, skip=("data/raw/a.parquet", *LOCKS))
        error = refused("source_unreadable", lambda: migrate(source, tmp_path / "dest"))
        assert error.details["reason"] == "in_use"
        assert tree(source, skip=("data/raw/a.parquet", *LOCKS)) == before
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    assert not (tmp_path / "dest" / "analytics.json").exists()


def test_a_read_only_copy_is_flushed_and_its_temporary_file_removed(tmp_path):
    """design 6.4 (Windows: FlushFileBuffers needs a handle that writes): a read-only source file's
    copy is flushed through its temporary file before it is published and made read-only, and a
    resumed migration removes a read-only temporary file it named."""
    source = plain_source(tmp_path)
    frozen = source / "scripts" / "frozen.py"
    frozen.parent.mkdir(exist_ok=True)
    frozen.write_bytes(b"x = 1\n")
    os.chmod(frozen, stat.S_IREAD)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "temp_full", "path": "scripts/frozen.py"})
    [temp] = [p for p in (dest / "scripts").iterdir() if p.name.endswith(".part")]
    os.chmod(temp, stat.S_IREAD)
    migrate(source, dest)
    assert not temp.exists() and not os.stat(dest / "scripts" / "frozen.py").st_mode & stat.S_IWUSR
    assert_committed(dest, source)


def test_names_other_systems_reserve_are_copied_with_a_warning(tmp_path):
    """design 6.2: a name Windows reserves or cannot keep (con, a trailing dot) is copied where the
    destination keeps it, and the plan and the report warn; where it refuses, the file's error is
    destination_write_failed and there is no marker (the Windows leg, when such a source exists)."""
    source = plain_source(tmp_path)
    names = ["con.txt", "aux"] if os.name != "nt" else []
    for name in names:
        (source / "data" / "raw" / name).write_bytes(b"x")
    if not names:
        pytest.skip("Windows makes no such name; the case runs on the POSIX legs")
    migrate(source, tmp_path / "dest")
    report = assert_committed(tmp_path / "dest")
    warned = {w["path"] for w in report["warnings"] if w["reason"] == "unportable_name"}
    assert warned == {f"data/raw/{name}" for name in names}


# --- T3 through the kit's command line, and T16 ------------------------------------------------------

def test_the_kit_s_command_line_in_a_destination_not_committed(tmp_path):
    """T3: the kit's commands in a destination a migration has not committed: migration_incomplete
    for a write and a read, the tree unchanged."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "copied", "path": "scripts/s1.py"})
    before = tree(dest)
    for args in (("record", "finding", "x"), ("status",)):
        code, result = kit_cli(dest, *args)
        assert (code, result["error"]["code"]) == (1, "migration_incomplete"), result
        assert result["error"]["details"]["migrations"][0]["step"] == "copied"
    assert tree(dest) == before


def test_the_migration_s_own_files_are_lf_and_the_copies_keep_their_bytes(shared):
    """T16: what the migration writes is UTF-8 and LF on every platform; a CRLF record and a CRLF script
    are copied as they are."""
    dest = shared["base"] / "reference"
    for path in mdir(dest).rglob("*"):
        if path.is_file() and path.suffix in (".json", ".jsonl", ".md") and path.name != "source-marker.json":
            assert b"\r\n" not in path.read_bytes(), path
    assert b"\r\n" in (dest / "journal.jsonl").read_bytes()
    assert (dest / "scripts" / "old.py").read_bytes().count(b"\r\n") == 3
    assert (dest / "analytics.json").read_bytes().endswith(b"}\n")


# --- more of design 6.1, 6.2, 6.9 (T2, T4, T21) ------------------------------------------------------

def test_a_copy_changed_after_the_commit_is_reported_when_called_again(tmp_path):
    """T4: calling again compares every copy with the report — a copy edited since is `changed`, one
    removed `missing`, an appended journal the same up to the report's length — and writes nothing."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    migrate(source, dest)
    (dest / "scripts" / "s0.py").write_bytes(b"edited after\n")
    (dest / "scripts" / "s1.py").unlink()
    with open(dest / "journal.jsonl", "ab") as fh:
        fh.write(b'{"schema": "analytics.event/2", "seq": 2}\n')
    before = tree(dest)
    result = migrate(source, dest)
    assert result["already_migrated"]
    assert result["differs"] == ["scripts/s0.py", "scripts/s1.py"]
    assert result["compared"]["changed"] == 1 and result["compared"]["missing"] == 1
    assert tree(dest) == before


def test_a_committed_destination_whose_source_lost_its_link_target_is_already_migrated(tmp_path):
    """T21: the folder an outside link led to is gone; the committed destination of that source is
    already_migrated all the same — no source-side check for a destination that does not read it."""
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "e.csv").write_bytes(b"e")
    source = plain_source(tmp_path)
    os.symlink(str(ext), source / "linked", target_is_directory=True)
    dest = tmp_path / "dest"
    migrate(source, dest)
    shutil.rmtree(ext)
    result = migrate(source, dest)
    assert result["already_migrated"] and result["compared"] == {"same": len(
        json.loads((mdir(dest) / "report.json").read_text(encoding="utf-8"))["files"])}


def test_the_marker_takes_what_the_source_s_marker_has_and_says_what_it_lacks(tmp_path):
    """design 2.4: the title and creation time from lab.json; without them the folder's name and
    the migration's start, and the report says the source had none."""
    p = LegacyProject(tmp_path / "plain-folder")
    (p.root / "lab.json").write_bytes(b'{"schema": "lab.project/1"}\n')
    p.event("note", text="n")
    migrate(p.root, tmp_path / "dest")
    marker = json.loads((tmp_path / "dest" / "analytics.json").read_text(encoding="utf-8"))
    report = assert_committed(tmp_path / "dest")
    assert marker["title"] == "plain-folder" and marker["created_at"] == report["started_at"]
    assert sum("has no title" in n or "has no created_at" in n for n in report["notes"]) == 2


def test_a_destination_called_from_inside_itself_or_with_another_source(tmp_path):
    """design 6.1: called with the destination as its source, a migration that has to read its source
    refuses (source_not_legacy, incomplete), one that checked it already goes on; called with another
    source at any step, the destination is another source's (not_empty)."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "plan"})
    error = refused("source_not_legacy", lambda: migrate(dest, dest))
    assert error.details["state"] == "incomplete"
    other = plain_source(tmp_path, "other")
    error = refused("destination_invalid", lambda: migrate(other, dest))
    assert error.details["reason"] == "not_empty" and error.details["source"] == str(core.Project(source).root)
    second = tmp_path / "second"
    interrupt(source, second, {"step": "verified"})
    result = migrate(second, second)
    assert result["ok"] and result["source"]["given"] == "not_given"
    assert_committed(second)


def test_another_spelling_of_the_source_is_the_source(tmp_path):
    """T2: where the file system folds case, the source spelled in other letters is the source: a
    destination inside it overlaps it."""
    source = plain_source(tmp_path, "Proj")
    other = tmp_path / "PROJ"
    if not other.exists():
        pytest.skip("this file system tells case apart")
    error = refused("destination_invalid", lambda: migrate(source, other / "new"))
    assert error.details["reason"] == "overlaps_source"
    assert not (source / "new").exists()


def test_a_state_folder_that_is_a_link_leading_outside_is_copied_without_its_cache_and_locks(tmp_path):
    """OQ4, design 6.6: `.lab` a link to a folder outside the source: its history is copied under
    .analytics/ like the folder's, its cache and lock files are not, and the place joins R."""
    outside = tmp_path / "state"
    (outside / "context").mkdir(parents=True)
    (outside / "context" / "20261001-090000-000000_aaaaaaaaaaaa.md").write_bytes(b"ctx")
    (outside / "cache").mkdir()
    (outside / "cache" / "h.json").write_bytes(b"{}")
    (outside / "lock").write_bytes(b"")
    p = LegacyProject(tmp_path / "src")
    p.event("note", text="n")
    os.symlink(str(outside), p.root / ".lab", target_is_directory=True)
    dest = tmp_path / "dest"
    migrate(p.root, dest)
    report = assert_committed(dest)
    copied = copies(dest)
    assert copied[".analytics/context/20261001-090000-000000_aaaaaaaaaaaa.md"][0] == "file"
    assert ".analytics/cache" not in copied and ".analytics/lock" not in copied
    assert {(e["path"], e["reason"]) for e in report["excluded"]} >= {(".lab/cache", "cache"),
                                                                     (".lab/lock", "lock")}
    error = refused("destination_invalid", lambda: migrate(p.root, outside / "new"))
    assert error.details["reason"] == "overlaps_source"


def test_a_link_made_where_a_planned_folder_goes_is_not_followed(tmp_path):
    """design 6.3 (ledger first, nothing written outside the destination): something that makes a
    link where the plan puts a folder, while the migration copies, does not lead the copies through
    it: destination_mismatch (occupied), the link's target as it was."""
    source = _copy_source(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    dest = tmp_path / "dest"

    def on_step(name, info):
        if name == "plan":
            os.symlink(str(elsewhere), dest / "scripts", target_is_directory=True)

    error = refused("destination_mismatch", lambda: migrate(source, dest, on_step=on_step))
    assert error.details == {"reason": "occupied", "path": "scripts"}
    assert list(elsewhere.iterdir()) == []


# --- links where the migration keeps its records, and the names it chooses (S1, S6) -------------------

@pytest.mark.parametrize("where", [".analytics", ".analytics/migration", "migration_dir", "ledger"])
def test_a_link_where_the_migration_keeps_its_records_is_never_followed(tmp_path, where):
    """S1, design 6.3: a link (to an empty folder or a file of the source) at .analytics/,
    .analytics/migration/, a migration's directory or its ledger: destination_invalid
    (managed_link) before anything is written, for the call and its dry run — the source and the
    link's target as they were."""
    source = plain_source(tmp_path)
    (source / "empty").mkdir()
    dest = tmp_path / "dest"
    if where in (".analytics", ".analytics/migration"):
        (dest / where).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(str(source / "empty"), dest / where, target_is_directory=True)
    else:
        interrupt(source, dest, {"step": "plan"})
        directory = mdir(dest)
        if where == "migration_dir":
            moved = tmp_path / "moved"
            shutil.move(str(directory), str(moved))
            os.symlink(str(moved), directory, target_is_directory=True)
        else:
            (source / "progress.jsonl").write_bytes((directory / "progress.jsonl").read_bytes())
            os.unlink(directory / "progress.jsonl")
            os.symlink(str(source / "progress.jsonl"), directory / "progress.jsonl")
    before, dest_before = source_tree(source), tree(dest)
    for kwargs in ({}, {"dry_run": True}):
        error = refused("destination_invalid", lambda k=kwargs: migrate(source, dest, **k))
        assert error.details["reason"] == "managed_link", error.details
    assert source_tree(source) == before, "S1"
    assert tree(dest) == dest_before


def test_a_link_planted_at_the_migration_s_own_temporary_names_is_never_written_through(tmp_path):
    """S1 (Codex #1): a link at the name a file of the migration's own directory is written through
    (mapping.json.tmp) and at a copy's temporary name, each leading to a read-only file of the
    source: the resumed migration removes the links as links and commits; the source's bytes and
    modes are as they were."""
    source = _copy_source(tmp_path)
    (source / "payload.csv").write_bytes(b"source bytes\n")
    os.chmod(source / "payload.csv", stat.S_IREAD)
    try:
        dest = tmp_path / "dest"
        interrupt(source, dest, {"step": "temp_partial", "path": "scripts/s1.py"})
        read = ledger(dest)
        temp = dest / "scripts" / read.begun["scripts/s1.py"]["temp"]
        os.unlink(temp)
        os.symlink(str(source / "payload.csv"), temp)
        os.symlink(str(source / "payload.csv"), mdir(dest) / "mapping.json.tmp")
        before = source_tree(source)
        mode = stat.S_IMODE(os.stat(source / "payload.csv").st_mode)
        migrate(source, dest)
        assert source_tree(source) == before, "S1"
        assert stat.S_IMODE(os.stat(source / "payload.csv").st_mode) == mode
        assert not os.path.lexists(temp)
        report = assert_committed(dest, source)
        assert not os.path.islink(mdir(dest) / "mapping.json")
        assert report["mapping"]["sha256"] == hashlib.sha256(
            (mdir(dest) / "mapping.json").read_bytes()).hexdigest()
    finally:
        os.chmod(source / "payload.csv", stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.parametrize("name", [".mig-gitignore-0.part", ".analytics-json-0.part"])
def test_a_source_file_named_like_a_temporary_file_of_the_migration_is_copied(tmp_path, name):
    """design 6.3 step 5, 6.4, T2 (Codex #3): the .gitignore transform's and the marker's temporary
    names are chosen apart from the plan's names, so a source file named like one is a copy like any
    other — committed in one call, in the report, the same when called again."""
    source = plain_source(tmp_path)
    (source / name).write_bytes(b"a user's file with this name\n")
    dest = tmp_path / "dest"
    result = migrate(source, dest)
    assert result["ok"] and not result["already_migrated"]
    report = assert_committed(dest, source)
    assert (dest / name).read_bytes() == b"a user's file with this name\n"
    assert any(f["path"] == name for f in report["files"])
    again = migrate(source, dest)
    assert again["already_migrated"] and again["differs"] == []


def test_a_temporary_name_something_else_made_first_is_not_this_migration_s(tmp_path):
    """S6 (Codex #3): something makes a file at the temporary name a `begin` line gave before the
    migration makes it: destination_mismatch (occupied) at that name, the file kept; called again,
    the same refusal and the file kept; once a person removes it, the migration goes on."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    taken = []

    def on_step(name, info):
        if name == "begin" and info.get("path") == "scripts/s2.py":
            path = dest / "scripts" / info["temp"]
            path.write_bytes(b"theirs")
            taken.append(path)

    error = refused("destination_mismatch", lambda: migrate(source, dest, on_step=on_step))
    [path] = taken
    assert error.details == {"reason": "occupied", "path": f"scripts/{path.name}"}
    assert ledger(dest).foreign == {f"scripts/{path.name}"}
    error = refused("destination_mismatch", lambda: migrate(source, dest))
    assert error.details == {"reason": "occupied", "path": f"scripts/{path.name}"}
    assert path.read_bytes() == b"theirs"
    path.unlink()
    migrate(source, dest)
    assert_committed(dest, source)


# --- step 8 and step 7 compare everything (S3-S5) -----------------------------------------------------

@pytest.mark.parametrize("when", ["after_source_checked", "before_verify"])
def test_a_copy_removed_before_the_verification_is_refused(tmp_path, when):
    """design 6.3 step 8, 6.10 (Codex #4): a copy removed after the source was checked (then resumed
    without the source) or within the call before step 8: destination_mismatch (unaccounted) with
    the path missing, no marker, nothing removed."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    if when == "after_source_checked":
        interrupt(source, dest, {"step": "source_checked"})
        os.unlink(dest / "scripts" / "s1.py")
        before = tree(dest)
        error = refused("destination_mismatch", lambda: migrate(None, dest))
        assert tree(dest) == before, "nothing removed, nothing written"
        dry = refused("destination_mismatch", lambda: migrate(None, dest, dry_run=True))
        assert dry.details["state"] == "refused" and dry.details["missing"] == ["scripts/s1.py"]
    else:
        def on_step(name, info):
            if name == "rechecking":
                os.unlink(dest / "scripts" / "s1.py")

        error = refused("destination_mismatch", lambda: migrate(source, dest, on_step=on_step))
    assert error.details["reason"] == "unaccounted" and error.details["missing"] == ["scripts/s1.py"]
    assert_uncommitted(dest)


def test_a_folder_removed_after_the_source_check_is_refused(tmp_path):
    """design 6.3 step 8 (the fix check): an empty folder the plan names, removed from the copy after
    the source was checked, then resumed without the source: destination_mismatch (unaccounted) with
    the folder missing — the dry run the same — nothing removed or written, no marker."""
    source = _copy_source(tmp_path)
    (source / "emptydir").mkdir()
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "source_checked"})
    os.rmdir(dest / "emptydir")
    before = tree(dest)
    error = refused("destination_mismatch", lambda: migrate(None, dest))
    assert error.details["reason"] == "unaccounted" and error.details["missing"] == ["emptydir"]
    dry = refused("destination_mismatch", lambda: migrate(None, dest, dry_run=True))
    assert dry.details["state"] == "refused" and dry.details["missing"] == ["emptydir"]
    assert tree(dest) == before, "nothing removed, nothing written"
    assert_uncommitted(dest)


@pytest.mark.parametrize("change", ["outside_link_removed", "empty_folder_added", "link_retargeted"])
def test_a_change_in_the_source_s_list_under_the_copy_is_source_changed(tmp_path, change):
    """S5, design 6.3 step 7 (Codex #5): an outside link removed, an empty folder added, an outside
    link led elsewhere — just before the source is checked again: source_changed, no marker."""
    source = _copy_source(tmp_path)
    for name in ("ext", "ext2"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "x.csv").write_bytes(b"x")
    os.symlink(str(tmp_path / "ext"), source / "linked", target_is_directory=True)
    dest = tmp_path / "dest"

    def on_step(name, info):
        if name != "rechecking":
            return
        if change == "outside_link_removed":
            os.unlink(source / "linked")
        elif change == "empty_folder_added":
            (source / "new-folder").mkdir()
        else:
            os.unlink(source / "linked")
            os.symlink(str(tmp_path / "ext2"), source / "linked", target_is_directory=True)

    error = refused("source_changed", lambda: migrate(source, dest, on_step=on_step))
    expected = "new-folder" if change == "empty_folder_added" else "linked"
    assert expected in error.details["paths"], error.details
    assert_uncommitted(dest)


@pytest.mark.skipif(not POSIX or ROOT_USER, reason="a folder's write permission: POSIX, not as root")
def test_a_read_only_folder_cut_while_a_file_of_it_is_copied_resumes(tmp_path):
    """F5 in a read-only folder: the resumed call copies the file again, removes the earlier
    temporary file while the folder is writable, then gives the folder its mode — and commits."""
    p = LegacyProject(tmp_path / "src")
    p.file("ro/a.bin", b"a")
    p.file("ro/big.bin", os.urandom(3 * migration.CHUNK))
    p.file("zz/after.bin", b"z")
    p.event("note", text="n")
    os.chmod(p.root / "ro", 0o555)
    dest = tmp_path / "dest"
    try:
        interrupt(p.root, dest, {"step": "temp_partial", "path": "ro/big.bin"})
        assert [x.name for x in (dest / "ro").iterdir() if x.name.endswith(".part")]
        migrate(p.root, dest)
        assert_committed(dest, p.root)
        assert stat.S_IMODE(os.stat(dest / "ro").st_mode) == 0o555
        assert migrate(p.root, dest)["already_migrated"]
    finally:
        os.chmod(p.root / "ro", 0o755)
        if (dest / "ro").exists():
            os.chmod(dest / "ro", 0o755)


# --- the source's .lab/, folders reached twice, a destination spelled with .. (6.2, 6.6) ------------

@pytest.mark.parametrize("kind", ["inside_link", "file", "inside_dangling"])
def test_a_state_folder_that_is_no_folder_of_its_own_is_refused(tmp_path, kind):
    """design 6.6: .lab a link to a folder inside the source (`.lab -> state/lab`), a file, or a
    dangling link whose text stays inside: made again or copied, it would be no folder at
    .analytics/ — destination_invalid (name_collision) before anything is written."""
    p = LegacyProject(tmp_path / "src")
    p.file("state/lab/context/20261001-090000-000000_aaaaaaaaaaaa.md", b"ctx")
    p.event("note", text="n")
    if (p.root / ".lab").exists():
        shutil.rmtree(p.root / ".lab")
    if kind == "inside_link":
        os.symlink(os.path.join("state", "lab"), p.root / ".lab", target_is_directory=True)
    elif kind == "file":
        (p.root / ".lab").write_bytes(b"not a folder")
    else:
        os.symlink(os.path.join("state", "gone"), p.root / ".lab")
    before = tree(p.root)
    error = refused("destination_invalid", lambda: migrate(p.root, tmp_path / "dest"))
    assert error.details["reason"] == "name_collision"
    assert error.details["pairs"] == [[".lab", ".analytics"]]
    assert tree(p.root) == before and not (tmp_path / "dest").exists()


def test_a_folder_two_outside_links_reach_is_copied_once_and_said_where(tmp_path):
    """OQ3, design 6.6: `a -> /ext/sub` and `b -> /ext`: the folder is copied once, at `a`, and
    `b/sub` is in not_copied (copied_elsewhere, at `a`)."""
    (tmp_path / "ext" / "sub").mkdir(parents=True)
    (tmp_path / "ext" / "sub" / "x.csv").write_bytes(b"x")
    (tmp_path / "ext" / "top.csv").write_bytes(b"t")
    p = LegacyProject(tmp_path / "src")
    p.event("note", text="n")
    os.symlink(str(tmp_path / "ext" / "sub"), p.root / "a", target_is_directory=True)
    os.symlink(str(tmp_path / "ext"), p.root / "b", target_is_directory=True)
    dest = tmp_path / "dest"
    migrate(p.root, dest)
    report = assert_committed(dest, p.root)
    assert {"path": "b/sub", "reason": "copied_elsewhere", "at": "a"} in report["not_copied"]
    assert (dest / "a" / "x.csv").read_bytes() == b"x" and not (dest / "b" / "sub").exists()


@pytest.mark.skipif(not POSIX, reason="`..` after a link: POSIX resolves it from the link's target")
def test_a_destination_spelled_with_dotdot_after_a_link_is_the_operating_system_s(tmp_path):
    """design 6.2 (Codex #9): `alias/../new` with `alias` a link into the source names the source's
    `new` to the operating system, and so to the migration: overlaps_source, nothing written."""
    source = plain_source(tmp_path)
    (source / "inner").mkdir()
    os.symlink(str(source / "inner"), tmp_path / "alias", target_is_directory=True)
    error = refused("destination_invalid", lambda: migrate(source, f"{tmp_path / 'alias'}/../new"))
    assert error.details["reason"] == "overlaps_source"
    assert not (source / "new").exists() and not (tmp_path / "new").exists()


# --- the files a report binds, and the marker's migration field (S3, design 2.4, 6.10) ---------------

def test_a_file_the_report_binds_that_went_is_made_again_before_the_marker(tmp_path):
    """design 6.10 (Codex #6): the mapping removed and report.md edited after `durable`: the resumed
    call writes the report and its files again (a new `reported` line) before the marker; and,
    committed, the marker removed with the mapping, the marker is published again with the mapping
    made again with the bytes the report binds."""
    source = _copy_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "durable"})
    reported = len([v for v in ledger(dest).lines if v["step"] == "reported"])
    os.unlink(mdir(dest) / "mapping.json")
    (mdir(dest) / "report.md").write_bytes(b"edited\n")
    migrate(None, dest)
    assert_committed(dest)
    assert len([v for v in ledger(dest).lines if v["step"] == "reported"]) == reported + 1
    assert core.status(core.Project(dest))["project"]["migration"]["applied"] is True
    mapping = (mdir(dest) / "mapping.json").read_bytes()
    os.unlink(dest / "analytics.json")
    os.unlink(mdir(dest) / "mapping.json")
    result = migrate(None, dest)
    assert result["already_migrated"] and result["republished"]
    assert (mdir(dest) / "mapping.json").read_bytes() == mapping
    assert_committed(dest)


@pytest.mark.parametrize("where", ["report_path", "report_bytes", "mapping_bytes"])
def test_a_marker_naming_another_migration_s_report_is_refused_and_not_read(tmp_path, where):
    """design 2.4 (Codex #7, the fix check): a marker whose migration field names another migration's
    report, or names its own migration's report path where another migration's report bytes are, or
    whose report binds another migration's mapping bytes in its own directory — each bound by its
    sha256: the call and its dry run are destination_mismatch (invalid) and write nothing, and the
    reader applies no mapping (`invalid`)."""
    first, second = _copy_source(tmp_path), plain_source(tmp_path, "other")
    a, b = tmp_path / "a", tmp_path / "b"
    migrate(first, a)
    migrate(second, b)
    marker_b = json.loads((b / "analytics.json").read_text(encoding="utf-8"))
    marker = json.loads((a / "analytics.json").read_text(encoding="utf-8"))
    if where == "report_path":
        shutil.copytree(mdir(b), a / migration.MIGRATIONS / mdir(b).name)
        marker["migration"] = {**marker["migration"], "report": marker_b["migration"]["report"],
                               "report_sha256": marker_b["migration"]["report_sha256"]}
    elif where == "report_bytes":
        data = (mdir(b) / "report.json").read_bytes()
        (mdir(a) / "report.json").write_bytes(data)
        marker["migration"] = {**marker["migration"], "report_sha256": hashlib.sha256(data).hexdigest()}
    else:
        data = (mdir(b) / "mapping.json").read_bytes()
        (mdir(a) / "mapping.json").write_bytes(data)
        report = json.loads((mdir(a) / "report.json").read_text(encoding="utf-8"))
        report["mapping"]["sha256"] = hashlib.sha256(data).hexdigest()
        report_data = json.dumps(report).encode()
        (mdir(a) / "report.json").write_bytes(report_data)
        marker["migration"] = {**marker["migration"],
                               "report_sha256": hashlib.sha256(report_data).hexdigest()}
    (a / "analytics.json").write_text(json.dumps(marker), encoding="utf-8")
    before = tree(a)
    error = refused("destination_mismatch", lambda: migrate(first, a))
    assert error.details["reason"] == "invalid"
    dry = refused("destination_mismatch", lambda: migrate(first, a, dry_run=True))
    assert dry.details["reason"] == "invalid"
    assert tree(a) == before, "nothing written"
    assert core.status(core.Project(a))["project"]["migration"]["reason"] == "invalid"
