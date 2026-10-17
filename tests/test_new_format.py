"""What the kit writes once the common writer is every entry point's (Issue #13 PR 3, design 2, 4.2,
4.9, 6.13, 6.14, 7 and 9).

The kit writes the new format only: the marker analytics.json (analytics.project/2), the records
analytics.event/2, analytics.run/2 and analytics.dataset/1, the trust list analytics.trust/1, under
the new layout's names (.analytics/, workspace/, data/imported/, runs/<id>/visualizations/), each
record by the Origin its entry point gives (who, how, whether it was said or filled in). The /1 writer
and the names it used are gone from the kit; a lab.json project is read only (tests/test_marker_gate.py).
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from conftest import KIT, PACK, git_checkout, kit_cli, kit_env, write_sales_csv, write_script
from jsonschema import Draft202012Validator, FormatChecker
from legacy_records import LegacyProject
from workspace_words import plain

from aelix_analytics import _platform, core, migration, writer

TEMPLATE = KIT / "aelix_analytics" / "template"
SCHEMA = json.loads((PACK / "docs" / "schemas" / "analytics-records.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
# design 2.5: the order of an analytics.event/2 line's keys (`session`, `request_id` and `details`
# only when there is one).
EVENT_KEYS = ("schema", "seq", "id", "recorded_at", "kind", "actor", "actor_basis", "means", "session",
              "request_id", "text", "refs", "details")


def schema_errors(record) -> list[str]:
    return [f"{list(e.absolute_path)}: {e.message}" for e in VALIDATOR.iter_errors(record)]


def journal(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "journal.jsonl").read_text(encoding="utf-8").splitlines()]


def cli(root: Path, *args, **env) -> tuple[int, dict]:
    """`python -m aelix_analytics` with the test environment changed by `env` (None removes a name):
    (exit status, its JSON line)."""
    environment = kit_env()
    for name, value in env.items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    done = subprocess.run([sys.executable, "-m", "aelix_analytics", "--project", str(root), *map(str, args)],
                          capture_output=True, env=environment, timeout=300, **_platform.text_kwargs())
    lines = [line for line in done.stdout.split("\n") if line.startswith("{")]
    assert lines, done.stderr
    return done.returncode, json.loads(lines[-1])


def tree(root: Path) -> dict:
    """Every entry's bytes, modification time and kind, lock files included."""
    out = {}
    for path in sorted(root.rglob("*")):
        st = os.lstat(path)
        out[path.relative_to(root).as_posix()] = (
            st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
    return out


SDK_RUN = """\
import matplotlib.pyplot as plt
import polars as pl

import aelix_analytics as analytics

sales = analytics.read("sales")
part = analytics.sql("SELECT region, sum(revenue) AS revenue FROM sales GROUP BY 1 ORDER BY 1").pl()
analytics.save(part, "by_region")
fig, ax = plt.subplots()
ax.bar(part["region"], part["revenue"])
analytics.visualize(fig, "by-region", title="by region", data=part)
analytics.record("finding", "recorded in the run")
"""


def test_every_record_an_entry_point_writes_is_the_new_format_s(project):
    """S9 and design 2: init, import, a run that makes every SDK call that records, a record with
    references and a closed question, the SDK outside a run and a render write nothing but the new
    format, and every record is valid against the record schema — the marker, each journal line (its
    keys in design 2.5's order), the run record (its Visualizations with their hashes), the import
    record and the trust list."""
    root = Path(project)
    code, ran = kit_cli(root, "run", write_script(root, "01_all.py", SDK_RUN), "--title", "all")
    assert code == 0 and ran["run"]["status"] == "ok", ran
    code, question = kit_cli(root, "record", "question", "which region?")
    assert code == 0, question
    code, answer = kit_cli(root, "record", "finding", "서울", "--ref", ran["run"]["id"], "--ref",
                           "by-region", "--ref", "sales", "--resolves", question["event"]["id"])
    assert code == 0, answer
    sdk = subprocess.run([sys.executable, "-c", "import aelix_analytics as a; a.record('note', 'from a script')"],
                         capture_output=True, env=kit_env(AELIX_ANALYTICS_PROJECT=str(root)), timeout=120,
                         **_platform.text_kwargs())
    assert sdk.returncode == 0, sdk.stderr
    assert kit_cli(root, "render")[0] == 0

    marker = json.loads((root / "analytics.json").read_text(encoding="utf-8"))
    assert marker["schema"] == "analytics.project/2" and schema_errors(marker) == []
    lines = journal(root)
    assert {e["schema"] for e in lines} == {"analytics.event/2"}
    for event in lines:
        assert schema_errors(event) == [], event
        assert tuple(event) == tuple(k for k in EVENT_KEYS if k in event), event
    run = json.loads((root / "runs" / ran["run"]["id"] / "run.json").read_text(encoding="utf-8"))
    assert schema_errors(run) == [], schema_errors(run)
    [vis] = run["visualizations"]
    assert vis["sha256"] == core.sha256_file(root / vis["files"]["png"])
    assert vis["data_sha256"] == core.sha256_file(root / vis["data"])
    assert vis["files"]["png"].startswith(f"runs/{run['id']}/visualizations/")
    meta = json.loads((root / "data" / "imported" / "sales.meta.json").read_text(encoding="utf-8"))
    assert schema_errors(meta) == [], schema_errors(meta)
    trust = json.loads(Path(os.environ["AELIX_ANALYTICS_TRUST_FILE"]).read_text(encoding="utf-8"))
    assert trust["schema"] == "analytics.trust/1" and schema_errors(trust) == []
    # The completion events: the run's and the import's `about` references (design 4.8).
    run_event = next(e for e in lines if e["kind"] == "run")
    assert run_event["refs"] == [{"type": "run", "run_id": run["id"], "relation": "about",
                                  "basis": "observed", "version_binding": "author"}]
    import_event = next(e for e in lines if e["kind"] == "import")
    assert import_event["refs"] == [{"type": "dataset_import", "path": "data/imported/sales.parquet",
                                     "digest": {"sha256": meta["sha256"]}, "relation": "about",
                                     "basis": "observed", "version_binding": "author"}]
    for name in (".lab", "report", "lab.json", "data/raw"):
        assert not (root / name).exists(), name


def test_each_entry_point_says_who_wrote_and_how(project):
    """design 4.2 and OQ1: the command line records AELIX_ANALYTICS_ACTOR as said (declared), else
    `human` filled in (inferred); the means AELIX_ANALYTICS_MEANS (the extension's), else `cli`; the
    session AELIX_ANALYTICS_SESSION, left out when there is none. A run records them in its record and
    its completion event, and the SDK's calls in it are the run's actor through the SDK. A means
    outside the closed list is refused before anything is written (bad_means)."""
    root = Path(project)
    actor = {"AELIX_ANALYTICS_ACTOR": None, "AELIX_ANALYTICS_MEANS": None, "AELIX_ANALYTICS_SESSION": None}
    assert cli(root, "record", "note", "a person", **actor)[0] == 0
    assert cli(root, "record", "note", "an agent", **{**actor, "AELIX_ANALYTICS_ACTOR": "agent",
                                                      "AELIX_ANALYTICS_SESSION": "s-1"})[0] == 0
    assert cli(root, "record", "note", "a tool", **{**actor, "AELIX_ANALYTICS_ACTOR": "agent",
                                                    "AELIX_ANALYTICS_MEANS": "tool"})[0] == 0
    seen = [(e["text"], e["actor"], e["actor_basis"], e["means"], e.get("session")) for e in journal(root)[-3:]]
    assert seen == [("a person", "human", "inferred", "cli", None), ("an agent", "agent", "declared", "cli", "s-1"),
                    ("a tool", "agent", "declared", "tool", None)]

    code, ran = cli(root, "run", write_script(root, "02_origin.py", SDK_RUN), "--title", "origin", **actor)
    assert code == 0 and ran["run"]["status"] == "ok", ran
    run = ran["run"]
    assert (run["actor"], run["actor_basis"], run["means"], run["session"]) == ("human", "inferred", "cli", None)
    *_, completion, in_run = journal(root)
    assert (completion["kind"], completion["actor"], completion["actor_basis"], completion["means"]) == (
        "run", "human", "inferred", "cli")
    assert (in_run["text"], in_run["actor"], in_run["actor_basis"], in_run["means"]) == (
        "recorded in the run", "human", "inferred", "sdk")

    before = tree(root)
    code, refused = cli(root, "record", "note", "x", AELIX_ANALYTICS_MEANS="telepathy")
    assert (code, refused["error"]["code"]) == (1, "bad_means"), refused
    assert refused["error"]["details"] == {"means": "telepathy", "allowed": list(writer.MEANS)}
    assert tree(root) == before


def test_the_script_api_outside_a_run_writes_as_a_person_through_the_sdk(project, monkeypatch):
    """design 4.2 (OQ1): the SDK outside a run records `human` filled in (inferred), or the actor
    AELIX_ANALYTICS_ACTOR says (declared), with the means `sdk` — no longer the actor `script`."""
    import aelix_analytics

    root = Path(project)
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(root))
    for name in ("AELIX_ANALYTICS_ACTOR", "AELIX_ANALYTICS_SESSION"):
        monkeypatch.delenv(name, raising=False)
    aelix_analytics.record("note", "by a person")
    monkeypatch.setenv("AELIX_ANALYTICS_ACTOR", "agent")
    monkeypatch.setenv("AELIX_ANALYTICS_SESSION", "s-2")
    aelix_analytics.record("note", "by an agent")
    seen = [(e["actor"], e["actor_basis"], e["means"], e.get("session")) for e in journal(root)[-2:]]
    assert seen == [("human", "inferred", "sdk", None), ("agent", "declared", "sdk", "s-2")]
    with pytest.raises(core.AnalyticsError) as caught:
        aelix_analytics.record("resolve", "needs a target")
    assert caught.value.code == "bad_kind" and "resolve" not in caught.value.details["kinds"]


def test_the_demo_runs_its_children_as_the_caller_asked(project, monkeypatch):
    """design 4.2: the demo's runs are the caller's — its actor (only when it was said: a filled-in
    actor stays filled in), means and session go to each child, and nothing the caller did not say."""
    from aelix_analytics import demo

    seen = []

    def run(cmd, *, env, **kwargs):
        seen.append({k: env.get(k) for k in ("AELIX_ANALYTICS_ACTOR", "AELIX_ANALYTICS_MEANS",
                                             "AELIX_ANALYTICS_SESSION")})
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true, "run": {"id": "r"}}\n', stderr="")

    monkeypatch.setattr(demo.subprocess, "run", run)
    monkeypatch.setenv("AELIX_ANALYTICS_ACTOR", "someone else")
    monkeypatch.setenv("AELIX_ANALYTICS_SESSION", "another session")
    p = core.Project(project)
    demo._run(p, "scripts/x.py", "x", core.Origin("human", "command", "inferred"))
    demo._run(p, "scripts/x.py", "x", core.Origin("agent", "tool", "declared", "s-3"))
    assert seen == [{"AELIX_ANALYTICS_ACTOR": None, "AELIX_ANALYTICS_MEANS": "command",
                     "AELIX_ANALYTICS_SESSION": None},
                    {"AELIX_ANALYTICS_ACTOR": "agent", "AELIX_ANALYTICS_MEANS": "tool",
                     "AELIX_ANALYTICS_SESSION": "s-3"}]


def test_an_older_name_of_a_kind_is_refused_with_its_new_name(project, monkeypatch):
    """design 2.8: `gap` is workflow_feedback's older name. The command line, the SDK and core.record
    refuse it as bad_kind naming the new one (not argparse's usage error), and nothing is written —
    not even the context version a record syncs first; a kind of the kit's own is no record kind."""
    import aelix_analytics

    root = Path(project)
    (root / "CONTEXT.md").write_text("# changed\n", encoding="utf-8")
    before = tree(root)
    code, result = kit_cli(root, "record", "gap", "the tools got in the way")
    assert code == 1 and result["error"]["code"] == "bad_kind", result
    assert result["error"]["details"]["use"] == "workflow_feedback"
    assert "workflow_feedback" in result["error"]["hint"]
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(root))
    with pytest.raises(core.AnalyticsError) as caught:
        aelix_analytics.record("gap", "x")
    assert caught.value.details["use"] == "workflow_feedback"
    for kind in ("gap", "project", "import", "run", "context", "made_up"):
        with pytest.raises(core.AnalyticsError) as caught:
            core.record(core.Project(root), kind, "x", origin=core.Origin("test", "sdk"))
        assert caught.value.code == "bad_kind", kind
    assert tree(root) == before
    assert core.RECORD_KINDS == ("finding", "decision", "assumption", "question", "todo", "note",
                                 "workflow_feedback", "metric_definition", "analysis_focus",
                                 "metric_observation", "asset_candidate", "asset_check", "asset_effect", "checkpoint", "resolve")
    assert kit_cli(root, "record", "workflow_feedback", "the tools got in the way")[0] == 0


def test_a_write_answers_with_its_request_id(project, tmp_path):
    """design 4.9: every write's answer holds a request_id — record, import, run, init, render and
    migrate here, the demo's in tests/test_language.py — the kit makes one when the caller gave
    none, and then the record does not hold it (only a caller's is kept)."""
    from legacy_records import LegacyProject

    root = Path(project)
    source = LegacyProject(tmp_path / "older")
    source.event("note", text="n")
    answers = {
        "record": kit_cli(root, "record", "note", "x")[1],
        "import": kit_cli(root, "import", tmp_path / "sales.csv", "--name", "again")[1],
        "run": kit_cli(root, "run", write_script(root, "03_id.py", "print(1)\n"), "--title", "id")[1],
        "init": cli(tmp_path, "init", tmp_path / "made")[1],
        "render": kit_cli(root, "render")[1],
        "migrate": cli(source.root, "migrate", "--to", tmp_path / "migrated")[1],
        "migrate --dry-run": cli(source.root, "migrate", "--to", tmp_path / "other", "--dry-run")[1],
    }
    for name, answer in answers.items():
        assert answer["ok"] and writer.REQUEST_ID_RE.fullmatch(answer["request_id"]), (name, answer)
    assert answers["record"]["bindings"] == [] and "targets" not in answers["record"]
    assert not [e for e in journal(root) if "request_id" in e]
    run = json.loads((root / "runs" / answers["run"]["run"]["id"] / "run.json").read_text(encoding="utf-8"))
    assert "request_id" not in run


def test_closing_a_question_twice_writes_both_and_says_so(project):
    """T13 (design 4.6, OQ11): the command line's --resolves answers with the target's state before
    and after; closing a closed question is written all the same, and the answer names the record
    that closed it."""
    root = Path(project)
    question = kit_cli(root, "record", "question", "why March?")[1]["event"]["id"]
    code, first = kit_cli(root, "record", "finding", "a price change", "--resolves", question)
    assert code == 0 and first["targets"] == [{"event_id": question, "relation": "resolves",
                                               "state_before": "open", "state_after": "resolved"}]
    code, second = kit_cli(root, "record", "resolve", "closed again", "--resolves", question)
    assert code == 0 and second["targets"] == [{
        "event_id": question, "relation": "resolves", "state_before": "resolved", "state_after": "resolved",
        "made_by": {"id": first["event"]["id"]}}]
    assert [e["id"] for e in journal(root)][-2:] == [first["event"]["id"], second["event"]["id"]]


def test_a_text_over_the_limit_is_refused_whole(project):
    """design 2.9 (OQ2): the command line keeps a long text whole, and refuses one over 32 KiB of
    UTF-8 (text_too_long) with nothing written — not the context version a record syncs first."""
    root = Path(project)
    long = "가" * (writer.TEXT_LIMIT // 3)
    assert kit_cli(root, "record", "note", long)[0] == 0
    assert journal(root)[-1]["text"] == long
    (root / "CONTEXT.md").write_text("# changed\n", encoding="utf-8")
    before = tree(root)
    code, result = kit_cli(root, "record", "note", long + "가")
    assert (code, result["error"]["code"]) == (1, "text_too_long"), result
    assert result["error"]["details"]["text_bytes"] == writer.TEXT_LIMIT + 1
    assert tree(root) == before


# --- what the kit composes, and what a record refuses before it writes (review of PR 3) -------------

# A long field made of what JSON escapes most (a control character takes six bytes) and of three-byte
# characters: 15,000 characters, 35,000 bytes of UTF-8 — over the text limit — and, escaped, over the
# line limit. It is given on the kit's command line, which Windows caps at 32,767 characters in all
# (CreateProcess; WinError 206 beyond it, the CI of PR 3): so the field is long in bytes, not in
# characters, and fits there with the rest of the command.
LONG = "\x01분석" * 5_000
WINDOWS_COMMAND_LINE = 32_767


def test_the_long_field_is_over_both_limits_and_fits_a_windows_command_line():
    """What the next test stands on: written whole, LONG would be refused (text_too_long) by either
    limit; quoted, it is not; and the init title too is over the text limit. Each fits on a Windows
    command line with room for the interpreter, the project and the other arguments."""
    escaped = json.dumps(LONG, ensure_ascii=False).encode("utf-8")
    assert len(LONG.encode("utf-8")) > writer.TEXT_LIMIT and len(escaped) > writer.LINE_LIMIT
    assert len(json.dumps(writer.excerpt(LONG), ensure_ascii=False).encode("utf-8")) < writer.TEXT_LIMIT
    assert len(TITLE.strip().encode("utf-8")) > writer.TEXT_LIMIT
    assert max(len(LONG), len(TITLE)) + 2_000 < WINDOWS_COMMAND_LINE


# A project's title of plain text (it goes into pyproject.toml and CONTEXT.md too).
TITLE = "분석 제목 " * 2_500


def test_a_long_field_is_kept_whole_and_quoted_by_the_event_the_kit_composes(project, tmp_path):
    """OQ2 concerns the text a caller records: a text the kit composes — an import's event, a run's
    completion, a project's creation — quotes a field it keeps whole elsewhere (writer.excerpt:
    the first EXCERPT_CHARS characters and `…`), so a long description or title never leaves an
    import, a run or a project without its event (kit 0.7.0 recorded them, cut at 4,000)."""
    root = Path(project)
    head = writer.excerpt(LONG)
    assert len(head) == writer.EXCERPT_CHARS + 1 and head.endswith("…")

    code, imported = cli(root, "import", tmp_path / "sales.csv", "--name", "described", "--description", LONG)
    assert code == 0 and imported["event"], imported
    meta = json.loads((root / "data" / "imported" / "described.meta.json").read_text(encoding="utf-8"))
    assert meta["description"] == LONG
    event = journal(root)[-1]
    assert event["kind"] == "import" and event["text"].endswith(" — " + head) and schema_errors(event) == []

    code, ran = cli(root, "run", write_script(root, "04_long.py", "print(1)\n"), "--title", LONG)
    assert code == 0 and ran["event"] and "finalize_errors" not in ran["run"], ran
    run = json.loads((root / "runs" / ran["run"]["id"] / "run.json").read_text(encoding="utf-8"))
    assert run["title"] == LONG
    assert journal(root)[-1]["text"] == f"{head} — ok"
    # A rerun takes its source's title unless --title is given: the same quote.
    code, rerun = cli(root, "run", "--rerun-of", ran["run"]["id"])
    assert code == 0 and rerun["event"] and "finalize_errors" not in rerun["run"], rerun
    assert journal(root)[-1]["text"].startswith(f"{head} — ok; rerun of {ran['run']['id']}")
    model = core.read_model(core.Project(root))
    assert {r["recording_status"] for r in model.runs() if r["run_id"] in (ran["run"]["id"], rerun["run"]["id"])} == {
        "complete"}

    # A title goes into pyproject.toml and CONTEXT.md too: a long one of text those take.
    title = TITLE
    code, made = cli(tmp_path, "init", tmp_path / "titled", "--title", title)
    assert code == 0 and made["title"] == title.strip(), made
    first = journal(tmp_path / "titled")
    assert [e["kind"] for e in first] == ["project", "context"]
    assert first[0]["text"] == f"project created: {writer.excerpt(title.strip())}"
    marker = json.loads((tmp_path / "titled" / "analytics.json").read_text(encoding="utf-8"))
    assert marker["title"] == title.strip()
    assert core.is_trusted(tmp_path / "titled")
    for event in first:
        assert schema_errors(event) == [], event


def test_a_composed_text_fits_its_line_whatever_its_fields_hold():
    """The bound behind the quote: a run's completion text with a title of the characters JSON
    escapes most, the longest error summary and a rerun's verdict is under both limits."""
    title, summary = writer.excerpt("\x01" * 50_000), "\x01" * 300
    text = f"{title} — error ({summary}); rerun of 20261009-120000-a: differs"
    line = json.dumps({"schema": "analytics.event/2", "seq": writer.SEQ_BOUND, "id": f"e{writer.SEQ_BOUND}",
                       "recorded_at": "2026-10-09T12:00:00.000+09:00", "kind": "run", "actor": "agent",
                       "actor_basis": "declared", "means": "tool", "session": "s" * 1024,
                       "request_id": "r" * 128, "text": text,
                       "refs": [{"type": "run", "run_id": "20261009-120000-" + "a" * 61, "relation": "about",
                                 "basis": "observed", "version_binding": "author"}]},
                      ensure_ascii=False).encode("utf-8")
    assert len(text.encode("utf-8")) <= writer.TEXT_LIMIT and len(line) + 1 < writer.LINE_LIMIT


def test_the_widest_seq_judges_a_line_at_most_22_bytes_longer_than_it_is_written(project):
    """CONTRACTS C1 (the fix-diff check of PR 3): a note made in a run is judged with SEQ_BOUND,
    whose twelve digits are in the line twice (`seq` and `id`); the line written has its real seq, of
    one digit at least, so the line judged is longer by 2 × (12 - its digits): at most 22 bytes."""
    made = core.writer_for(core.Project(project), core.Origin("test", "sdk"))

    def line(seq: int) -> int:
        return len(writer._encode(made._event(seq, "note", "x", [], None)))

    assert len(str(writer.SEQ_BOUND)) == 12
    assert {digits: line(writer.SEQ_BOUND) - line(10 ** (digits - 1)) for digits in (1, 2, 6, 12)} == \
        {1: 22, 2: 20, 6: 12, 12: 0}


def test_a_script_that_exits_with_a_long_message_keeps_its_completion_event(project):
    """`sys.exit(<message>)`: the run is an error whose summary is bounded as an exception's (300
    characters) and whose message is whole in run.json; its completion event is written."""
    root = Path(project)
    message = "validation failed: " + "row 1 bad; " * 4000
    script = write_script(root, "05_exit.py", f"import sys\nsys.exit({message!r})\n")
    code, ran = cli(root, "run", script, "--title", "exit")
    assert code == 3 and ran["run"]["status"] == "error" and ran["event"], ran
    assert "finalize_errors" not in ran["run"]
    error = ran["run"]["error"]
    assert error["message"] == message and len(error["summary"]) == 300
    assert error["summary"] == f"SystemExit({message})"[:300]
    assert journal(root)[-1]["text"] == f"exit — error ({error['summary']})"


IN_RUN_NOTES = """\
import aelix_analytics as analytics

try:
    analytics.record("note", "\\x01" * 9000 + "end")
except Exception as exc:
    print("refused", type(exc).__name__, exc.code, exc.details["line_bytes"] > exc.details["line_limit"])
analytics.record("note", "kept")
"""


def test_a_note_in_a_run_whose_line_is_over_the_limit_is_the_script_s_error(project):
    """design 4.3: a refusal in a script is an exception. A note made in a run is written when the
    run finishes, so its line is judged when the script makes it, with the widest seq
    (writer.SEQ_BOUND): an escape-heavy note over the line limit raises text_too_long there, and
    every note that returned is written."""
    root = Path(project)
    code, ran = cli(root, "run", write_script(root, "06_notes.py", IN_RUN_NOTES), "--title", "notes")
    assert code == 0 and ran["run"]["status"] == "ok", ran
    assert "refused AnalyticsError text_too_long True" in ran["stdout_tail"], ran["stdout_tail"]
    assert "finalize_errors" not in ran["run"]
    *_, completion, kept = journal(root)
    assert completion["kind"] == "run" and (kept["kind"], kept["text"]) == ("note", "kept")


@pytest.mark.parametrize("args, code", [
    (["note", "\x01" * 9000 + "x"], "text_too_long"),
    (["finding", "x", "--resolves", "e999"], "unknown_event"),
    (["resolve", "closes nothing"], "resolve_needs_target"),
    (["gap", "x"], "bad_kind"),
    (["note", "   "], "empty_note"),
    (["finding", "x", "--resolves", "e1"], None),
], ids=["line", "unknown target", "no target", "old kind", "empty", "accepted"])
def test_a_record_refused_before_its_lock_leaves_the_context_unsynced(project, args, code):
    """A record the writer refuses before its lock — over the line limit with a text under 32 KiB,
    a closed record that is not there, a `resolve` without one, an old kind, an empty text — is
    refused before the context sync the command line makes first (core.check_record): CONTEXT.md
    changed, and still no `context` event and no version. One that is accepted syncs it and is
    written after it (e1 is the project's event: a record without a state, written all the same)."""
    root = Path(project)
    with (root / "CONTEXT.md").open("a", encoding="utf-8") as fh:
        fh.write("more\n")
    before = tree(root)
    status, result = kit_cli(root, "record", *args)
    if code is None:
        assert status == 0, result
        assert [e["kind"] for e in journal(root)[-2:]] == ["context", "finding"]
        return
    assert (status, result["error"]["code"]) == (1, code), result
    assert tree(root) == before


def test_the_trust_list_is_the_new_one_and_the_older_one_is_read_only(tmp_path, monkeypatch):
    """design 6.14: this kit writes analytics-projects.json (analytics.trust/1) in the host's agent
    directory and reads the older kits' lab-projects.json without writing it: a root listed there
    activates a lab.json project, never a project of the new format by itself. A list
    AELIX_ANALYTICS_TRUST_FILE names is the one list, read in either family and written as
    analytics.trust/1; a list that holds the root already is left as it is."""
    agent = tmp_path / "agent"
    agent.mkdir()
    monkeypatch.delenv("AELIX_ANALYTICS_TRUST_FILE")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    older = LegacyProject(tmp_path / "older").root
    newer = core.init_project(tmp_path / "newer", template_dir=TEMPLATE, origin=core.Origin("test", "sdk")).root
    (agent / "analytics-projects.json").unlink()  # what init wrote: the list starts from the older one
    lab_list = agent / "lab-projects.json"
    lab_list.write_text(json.dumps({"schema": "lab.trust/1", "projects": [core.canonical(older),
                                                                          core.canonical(newer)]}),
                        encoding="utf-8")
    kept = lab_list.read_bytes()
    assert core.trust_file() == agent / "analytics-projects.json"
    assert core.is_trusted(older) and not core.is_trusted(newer)
    core.trust_project(newer)
    written = json.loads((agent / "analytics-projects.json").read_text(encoding="utf-8"))
    assert written == {"schema": "analytics.trust/1", "projects": [core.canonical(newer)]}
    assert schema_errors(written) == [] and core.is_trusted(newer)
    assert lab_list.read_bytes() == kept

    one = tmp_path / "one-list.json"
    one.write_text(json.dumps({"schema": "lab.trust/1", "projects": [core.canonical(older)]}), encoding="utf-8")
    monkeypatch.setenv("AELIX_ANALYTICS_TRUST_FILE", str(one))
    assert core.legacy_trust_file() is None and core.is_trusted(older) and not core.is_trusted(newer)
    core.trust_project(older)
    assert json.loads(one.read_text(encoding="utf-8"))["schema"] == "lab.trust/1"  # as it was
    core.trust_project(newer)
    assert json.loads(one.read_text(encoding="utf-8")) == {
        "schema": "analytics.trust/1", "projects": sorted([core.canonical(older), core.canonical(newer)])}


def test_the_template_ignores_the_new_names_a_migration_adds():
    """design 6.13: a new project's .gitignore names what the migration adds to a copy's — the new
    names of the older template's lines and the kit's files it always adds — one list; no older name
    is left, and data/ stays ignored."""
    lines = (TEMPLATE / "gitignore").read_text(encoding="utf-8").splitlines()
    added = [new for _old, new in migration.GITIGNORE_RENAMED] + list(migration.GITIGNORE_ALWAYS)
    assert set(added) <= set(lines), set(added) - set(lines)
    rules = [line for line in lines if line and not line.startswith("#")]
    assert set(rules) == {".venv/", "__pycache__/", "data/", *added}, rules
    assert not [line for line in rules if line.startswith((".lab", "report")) or "/charts/" in line]


def test_a_project_s_paths_follow_its_marker(tmp_path):
    """design 2.3 and 9: Project's places are the layout its marker picks — the new format's for an
    analytics.json project and for a directory with no marker yet (what init makes), the older
    layout's for a lab.json project, which this kit reads only; the older state directory is read
    beside the new one."""
    newer = core.init_project(tmp_path / "newer", template_dir=TEMPLATE, origin=core.Origin("test", "sdk"))
    older = core.Project(LegacyProject(tmp_path / "older").root)
    empty = core.Project(tmp_path)
    for project, layout in ((newer, writer.LAYOUT), (older, writer.LEGACY_LAYOUT), (empty, writer.LAYOUT)):
        root = project.root
        assert project.layout is layout
        assert (project.marker, project.state_dir, project.workspace_dir, project.imported) == (
            root / layout.marker, root / layout.state_dir, root / layout.workspace_dir,
            root / layout.imported_dir)
        assert (project.cache, project.context_history, project.telemetry, project.queries) == (
            project.state_dir / "cache", project.state_dir / "context",
            project.state_dir / "telemetry.jsonl", project.state_dir / "queries.jsonl")
        assert project.legacy_state_dir == root / ".lab"
    assert (newer.marker.name, newer.state_dir.name, newer.workspace_dir.name) == (
        "analytics.json", ".analytics", "workspace")


def test_a_write_follows_the_marker_it_was_admitted_by(tmp_path):
    """S9 and design 4.3 (review of PR 3): a Project made under lab.json whose marker became a valid
    analytics.json before a write is admitted takes that marker's paths at the gate (core.admit):
    the import goes to data/imported/ with its record and event, and nothing goes to the older
    layout's data/raw/."""
    from aelix_analytics import data

    source = write_sales_csv(tmp_path / "sales.csv")
    root = LegacyProject(tmp_path / "swap").root
    project = core.Project(root)
    assert project.layout is writer.LEGACY_LAYOUT
    (root / "lab.json").unlink()
    (root / "analytics.json").write_text(json.dumps({
        "schema": "analytics.project/2", "project_id": "prj_00000000000000aa", "title": "swap",
        "created_at": "2026-10-09T00:00:00+09:00", "min_writer_version": core.KIT_VERSION,
        "kit_version": core.KIT_VERSION}), encoding="utf-8")
    result = data.import_file(project, str(source), name="swapped", description=None,
                              origin=core.Origin("human", "cli", "inferred"))
    assert project.layout is writer.LAYOUT and project.imported == root / "data" / "imported"
    assert result["dataset"]["path"] == "data/imported/swapped.parquet"
    assert (root / "data" / "imported" / "swapped.meta.json").is_file()
    assert not (root / "data" / "raw").exists()
    assert journal(root)[-1]["kind"] == "import"


def test_the_context_is_versioned_where_this_kit_writes_and_not_again_after_a_migration(tmp_path):
    """design 4.8: a CONTEXT.md version goes to .analytics/context/, its `context` record citing it by
    its sha256; the last version of either state directory counts, so the same CONTEXT.md a
    migration copied under .lab/context/ is not versioned again."""
    project = core.init_project(tmp_path / "p", template_dir=TEMPLATE, origin=core.Origin("test", "sdk"))
    root = project.root
    [version] = (root / ".analytics" / "context").iterdir()
    copied = root / ".lab" / "context" / version.name
    copied.parent.mkdir(parents=True)
    os.replace(version, copied)
    before = tree(root)
    assert core.sync_context(project, origin=core.Origin("test", "sdk")) is None
    assert tree(root) == before
    (root / "CONTEXT.md").write_bytes(b"# Goal\nchanged\n")
    event = core.sync_context(project, origin=core.Origin("system", "cli"))
    [made] = (root / ".analytics" / "context").iterdir()
    assert made.read_bytes() == b"# Goal\nchanged\n"
    assert event["kind"] == "context" and event["refs"] == [{
        "type": "context_version", "version": made.name,
        "digest": {"sha256": hashlib.sha256(b"# Goal\nchanged\n").hexdigest()}, "relation": "about",
        "basis": "observed", "version_binding": "author"}]
    assert (event["actor"], event["means"]) == ("system", "cli")


# What the /1 writer was, and its truncation (design 9, G1-16): none of it is left in the kit or the
# extension.
GONE = {"add_note", "append_event", "_append_event_locked", "resolve_ref_aliases", "NOTE_KINDS",
        "MAX_EVENT_TEXT", "MAX_EVENT_REFS", "note_cut", "project_lock"}
# The families of the older format: read by the read model, named by the older layout and read by the
# migration; written nowhere.
OLDER_FAMILIES = {"lab.project/1", "lab.event/1", "lab.run/1", "lab.dataset/1", "lab.trust/1"}
OLDER_FAMILIES_READ_IN = {"relations.py", "writer.py", "migration.py"}


def test_the_older_writer_and_its_names_are_gone():
    files = [*sorted((PACK / "src" / "aelix_analytics").glob("*.py")), PACK / "extension" / "aelix_analytics.py"]
    left, families = [], []
    for path in files:
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            name = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                    else node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None)
            if name in GONE:
                left.append(f"{path.name}:{node.lineno} {name}")
            if isinstance(node, ast.Constant) and node.value in OLDER_FAMILIES \
                    and path.name not in OLDER_FAMILIES_READ_IN:
                families.append(f"{path.name}:{node.lineno} {node.value}")
    assert not left, left
    assert not families, families
    # The /1 writer's journal lock was core's; the Writer's is its own (Writer.locked).
    assert not hasattr(core, "journal_lock") and not hasattr(core, "project_lock")


RERUN = """\
import matplotlib.pyplot as plt
import polars as pl

import aelix_analytics as analytics

df = pl.DataFrame({"x": [1, 2, 3], "y": [2.0, 4.0, 6.0]})
analytics.save(df, "doubled")
fig, ax = plt.subplots()
ax.plot(df["x"], df["y"])
analytics.visualize(fig, "trend", title="Trend", data=df)
"""


def v1_kit_root(base: Path) -> Path:
    """The runtime root of the kit before the new format, the commit tools/eval_fixtures.py names
    (V1_KIT_COMMIT, design OQ7), out of the checkout's history: the kit that writes lab.json projects."""
    text = (PACK / "tools" / "eval_fixtures.py").read_text(encoding="utf-8")
    commit = re.search(r'^V1_KIT_COMMIT = "([0-9a-f]{40})"', text, re.MULTILINE).group(1)
    with tarfile.open(fileobj=io.BytesIO(git_checkout("archive", "--format=tar", commit, "src"))) as archive:
        archive.extractall(base, filter="data")
    return base / "src"


def test_a_migrated_run_reruns_as_it_reruns_where_it_came_from(tmp_path):
    """T11 (design 5.5, S13): a lab.run/1 run the kit before the new format made is rerun by that
    kit in its lab.json project, and by this kit in the project's migrated copy. The two reruns —
    a lab.run/1 and an analytics.run/2 record — compare the run alike: this kit's
    reproduction.visualizations, verdict, outputs and inputs_same are the older rerun's
    reproduction.charts, verdict, outputs and inputs_same, and its run page shows the comparison."""
    older_env = kit_env(PYTHONPATH=str(v1_kit_root(tmp_path / "v1-kit")))

    def older(*args) -> tuple[int, dict]:
        done = subprocess.run([sys.executable, "-m", "aelix_analytics", *map(str, args)], capture_output=True,
                              env=older_env, timeout=300, **_platform.text_kwargs())
        lines = [line for line in done.stdout.split("\n") if line.startswith("{")]
        assert lines, done.stderr
        return done.returncode, json.loads(lines[-1])

    source, destination = tmp_path / "source", tmp_path / "destination"
    code, made = older("init", source, "--title", "before the new format")
    assert code == 0 and (source / "lab.json").is_file(), made
    write_script(source, "a.py", RERUN)
    code, first = older("--project", source, "run", "scripts/a.py", "--title", "first")
    assert code == 0, first
    run_id = first["run"]["id"]
    migration.migrate(source, destination, origin=core.Origin("test", "cli"), trust=lambda root: None)

    code, there = older("--project", source, "run", "--rerun-of", run_id)
    assert code == 0, there
    code, here = kit_cli(destination, "run", "--rerun-of", run_id)
    assert code == 0, here
    before = json.loads((source / "runs" / there["run"]["id"] / "run.json").read_text(encoding="utf-8"))
    after = json.loads((destination / "runs" / here["run"]["id"] / "run.json").read_text(encoding="utf-8"))
    assert (before["schema"], after["schema"]) == ("lab.run/1", "analytics.run/2")
    assert before["reproduction"]["charts"] == after["reproduction"]["visualizations"] == {"trend": "same data"}
    for name in ("of", "verdict", "outputs", "inputs_same"):
        assert after["reproduction"][name] == before["reproduction"][name], name
    assert after["reproduction"]["verdict"] == "match"
    assert not schema_errors(after)
    page = plain((destination / "workspace" / "runs" / f"{here['run']['id']}.html").read_text(encoding="utf-8"))
    assert "Rerun comparison: Match" in page and '<code lang="">trend</code>: same data' in page
