"""Observed activities in the Workspace and in provenance (ADR-0015 decisions 7 and 8, Issue #171 PR-2).

The records are written as the extension writes them (tests/test_observed.py's Observer); some are then
copied with other window times under the observation's own write path (the `observe` operation) to put
a window before, across or after a run's start. The provenance judgments are the read model's
(ReadModel.provenance, observed_candidates); the pages are a built Workspace read as HTML.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta

import pytest
from conftest import kit_cli
from test_observed import Observer, details, journal, proj, write  # noqa: F401 - proj is a fixture

from aelix_analytics import core, observed, workspace

assert proj  # the fixture, imported for this module's tests


def model(root):
    return core.read_model(core.Project(root))


def run(root, script: str, body: str, title: str = "r") -> dict:
    write(root / "scripts" / script, body)
    code, result = kit_cli(root, "run", f"scripts/{script}", "--title", title)
    assert code == 0 and result["run"]["status"] == "ok", result
    return result["run"]


def made_by_python(root, path: str, text: str) -> dict:
    """An observed Python command that wrote `path` with no run recording it."""
    write(root / "scripts" / "make.py", f"open({path!r}, 'w').write({text!r})\n")
    return details(Observer(root)("python scripts/make.py",
                                  lambda: subprocess.run([sys.executable, "scripts/make.py"], cwd=root, check=True)))


def copy_observation(root, *, started: datetime, ended: datetime) -> str:
    """The last observation again, with another window, written as the observation writes (operation
    `observe`): the id of the copy."""
    event = [row for row in journal(root) if row["kind"] == "observed_activity"][-1]
    d = copy.deepcopy(event["details"])
    d["activity_id"] = observed.new_activity_id()
    d["started_at"], d["ended_at"] = started.isoformat(timespec="milliseconds"), ended.isoformat(timespec="milliseconds")
    project = core.Project(root)
    with core.writer_for(project, core.Origin("system", "hook", "declared", "s1")).locked("observe") as held:
        return held.append("observed_activity", event["text"], details=d)["id"]


def input_node(found: dict, path: str) -> dict:
    return next(n for n in found["nodes"] if n["kind"] == "data" and n["path"] == path)


# --- provenance (decision 8) ---------------------------------------------------------------------------


def test_bytes_a_python_command_wrote_have_the_observation_as_an_unverified_candidate(proj):
    """A run read bytes an observed command made: the input's producer judgment is unchanged (`none`), the
    observation is a candidate in `observed_changes`, the trail goes to it by `changed_during` (basis
    `unverified`, never confirmed), stops there, and the checks count it apart."""
    made_by_python(proj, "data/derived/x.csv", "a\n1\n")
    reader = run(proj, "use.py", "import polars as pl\nprint(pl.read_csv('data/derived/x.csv'))\n")
    m = model(proj)
    before = m.provenance("run:" + reader["id"], "sources")
    data = input_node(before, "data/derived/x.csv")
    assert data["producer"] == "none"
    assert data["observed_changes"] == [{"id": "e3", "order": "before", "run_also": False}]
    edge = next(e for e in before["edges"] if e["kind"] == "changed_during")
    assert edge == {"from": "e3", "to": data["id"], "kind": "changed_during", "basis": "unverified",
                    "confirmed": False, "order": "before", "run_also": False}
    obs = next(n for n in before["nodes"] if n["id"] == "e3")
    assert obs["kind"] == "observation" and obs["command"] == "python scripts/make.py"
    assert not any(e["from"] != "e3" and e["to"] == "e3" for e in before["edges"]), "the trail stops at it"
    assert before["checks"]["observed"] == {"count": 1, "names": ["e3"]}
    assert before["checks"]["runs"]["total"] == 1
    judged = m.producer(reader["id"], "data/derived/x.csv")
    assert judged["producer"] == "none" and judged["candidates"] == []
    # What uses the observation: the file it saw change, then the run that read it.
    uses = m.provenance("e3", "uses")
    assert [n["kind"] for n in uses["nodes"]] == ["observation", "data", "run"]
    # The bounded read gives the same trail.
    answer = core.status(core.Project(proj), part="relations", id="run:" + reader["id"], direction="sources")
    assert any(n["kind"] == "observation" for n in answer["items"])


def test_time_direction_before_across_and_after_a_run_s_start(proj):
    """Decision 8: for a run's input, a window that ended before the run started is a candidate, one that
    spans its start is `uncertain`, and one that began after it is not its source (an afternoon
    observation is no candidate for a morning run's input). From the file now every observation of the
    bytes is a candidate, in time order."""
    made_by_python(proj, "data/derived/x.csv", "a\n1\n")
    reader = run(proj, "use.py", "import polars as pl\nprint(pl.read_csv('data/derived/x.csv'))\n")
    started = datetime.fromisoformat(reader["started_at"])
    across = copy_observation(proj, started=started - timedelta(seconds=5), ended=started + timedelta(seconds=5))
    later = copy_observation(proj, started=started + timedelta(hours=5), ended=started + timedelta(hours=5, seconds=1))
    m = model(proj)
    data = input_node(m.provenance("run:" + reader["id"], "sources"), "data/derived/x.csv")
    assert data["observed_changes"] == [{"id": "e3", "order": "before", "run_also": False},
                                        {"id": across, "order": "uncertain", "run_also": False}]
    now = m.trail_start("data/derived/x.csv")
    assert [c["id"] for c in now["observed_changes"]] == ["e3", across, later]
    assert all(c["order"] is None for c in now["observed_changes"])


def test_bytes_a_run_also_recorded_are_marked_and_the_run_stays_the_producer(proj):
    """Decision 8: an observation saw bytes a run also output (the run finished after the window): the
    run is the producer candidate and the observation is marked `run_also`."""
    script = "open('data/derived/y.csv', 'w').write('same\\n')\n"
    made_by_python(proj, "data/derived/y.csv", "same\n")
    run(proj, "w.py", script, "writer")
    reader = run(proj, "use.py", "import polars as pl\nprint(pl.read_csv('data/derived/y.csv'))\n")
    data = input_node(model(proj).provenance("run:" + reader["id"], "sources"), "data/derived/y.csv")
    assert data["producer"] == "resolved"
    assert data["observed_changes"] == [{"id": "e3", "order": "before", "run_also": True}]


def test_unknown_and_hashless_changes_are_no_candidates_and_a_deletion_is_said_of_a_missing_file(proj):
    """Decision 8: a change without a hash (a secret name, over the bound) is no candidate; a file missing
    now carries the last observation that saw it deleted."""
    out = run(proj, "w.py", "open('data/derived/z.csv', 'w').write('z\\n')\n", "writer")
    assert out["outputs"][0]["path"] == "data/derived/z.csv"
    Observer(proj)("rm data/derived/z.csv", lambda: (proj / "data" / "derived" / "z.csv").unlink(), python=False)
    gone = model(proj).trail_start("data/derived/z.csv")
    assert gone["state"] == "missing" and gone["observed_deleted"] == "e4"
    Observer(proj)("sh x.sh", lambda: write(proj / "data" / ".env", "v\n"), python=False)
    assert model(proj)._observed_index()[0] == {}


def test_a_record_citing_an_observation_finds_it_and_stays_an_unconfirmed_source(proj):
    """Decision 8: a finding citing the observation (an event reference) is judged as any event
    reference; the trail shows the target is an observation and counts it on the `observed` line."""
    made_by_python(proj, "data/derived/x.csv", "a\n1\n")
    core.record(core.Project(proj), "finding", "x looks right", refs=["e3"], origin=core.Origin("human", "cli"))
    found = model(proj).provenance("e4", "sources")
    assert [n["kind"] for n in found["nodes"]] == ["record", "observation"]
    assert found["checks"]["observed"]["count"] == 1
    assert found["checks"]["citations"]["total"] == 1


def test_an_observation_with_a_long_change_list_reads_its_attachment(proj):
    def act():
        for i in range(400):
            write(proj / "data" / "many" / f"file-{i:04d}.csv", f"{i}\n")

    d = details(Observer(proj)("sh gen.sh", act, python=False))
    assert len(d["changes"]["items"]) < 400
    index = model(proj)._observed_index()[0]
    assert ("data/many/file-0399.csv", hashlib.sha256(b"399\n").hexdigest()) in index
    # An attachment whose bytes are not the ones recorded is not read.
    folder = proj / ".analytics" / "observed" / d["activity_id"]
    (folder / "changes.json").write_text("[]\n", encoding="utf-8")
    assert ("data/many/file-0399.csv", hashlib.sha256(b"399\n").hexdigest()) not in model(proj)._observed_index()[0]


# --- the Workspace (decision 7) --------------------------------------------------------------------------


def build(root):
    workspace.build(core.Project(root))
    return root / "workspace"


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def relation_page(site, identity: str) -> str:
    return (site / "relations" / (hashlib.sha256(identity.encode()).hexdigest() + "-0.html")).read_text(encoding="utf-8")


def test_the_timeline_lists_an_observation_with_its_badge_and_its_own_kind_list(proj):
    made_by_python(proj, "data/derived/x.csv", "a\n1\n")
    site = build(proj)
    index = (site / "index.html").read_text(encoding="utf-8")
    row = re.search(r'<li class="ev rec"[^>]*data-kind="observed_activity".*?</li>', index, re.S).group(0)
    words = text_of(row)
    for said in ("Observed activity", "Outside the kit", "1 file changed while it ran · 1 not recorded by a run or import",
                 "Inputs and environment not captured", "system"):
        assert said in words, said
    assert (site / "lists" / "kind-observed_activity-0.html").is_file()
    assert 'lists/kind-observed_activity-0.html' in index


def test_the_record_page_shows_the_command_scripts_changes_and_what_is_not_kept(proj):
    write(proj / "scripts" / "make.py", "open('data/derived/x.csv', 'w').write('1')\n")
    canary = "canary-page-value"
    command = f"API_TOKEN={canary} python scripts/make.py " + "#" * (observed.COMMAND_INLINE + 10)
    d = details(Observer(proj)(command, lambda: subprocess.run([sys.executable, "scripts/make.py"], cwd=proj, check=True)))
    site = build(proj)
    page = relation_page(site, "e3")
    section = re.search(r'<section class="observed panel".*?</section>', page, re.S).group(0)
    words = text_of(section)
    assert "API_TOKEN=[redacted] python scripts/make.py" in words and canary not in page
    folder = f".analytics/observed/{d['activity_id']}/"
    assert f'href="../../{folder}command.txt"' in section
    assert f'href="../../{folder}scripts/scripts/make.py"' in section
    for said in ("Working directory", "exit code 0", "Python ran outside the kit", "bytes as the call started",
                 "data/derived/x.csv", "no run or import recorded it", "What this record does not say"):
        assert said in words, said


def test_record_coverage_counts_observations_and_the_calls_of_a_session_after_its_observation_was_off(proj, monkeypatch):
    monkeypatch.setattr(observed, "ENTRIES", 3)
    for i in range(5):
        write(proj / "data" / f"f{i}.csv", "x\n")
    observer = Observer(proj, session="/home/someone/.aelix/sessions/s-1.jsonl")
    off = details(observer("python a.py"))
    assert off["session_observation"] == "off"
    later = observer.before("python b.py", view=False)
    observer.after(later)
    # Telemetry holds two bash calls of that session after it: more than the one observation.
    telemetry = proj / ".analytics" / "telemetry.jsonl"
    rows = [{"ts": "2999-01-01T00:00:00+00:00", "tool": "bash", "session": observer.session, "summary": "ls"}] * 2
    telemetry.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    site = build(proj)
    words = text_of((site / "index.html").read_text(encoding="utf-8"))
    assert "Bash calls observed outside the kit 2" in words
    assert "Observed without looking at changes 2" in words
    assert "Session with change observation off 1 e3 s-1.jsonl · 2 bash calls after it: changes not observed" in words
    telemetry.unlink()
    words = text_of((build(proj) / "index.html").read_text(encoding="utf-8"))
    assert "at least 1 bash call after it" in words


def test_a_run_s_page_draws_the_observation_on_its_trail(proj):
    made_by_python(proj, "data/derived/x.csv", "a\n1\n")
    reader = run(proj, "use.py", "import polars as pl\nprint(pl.read_csv('data/derived/x.csv'))\n")
    site = build(proj)
    page = relation_page(site, "run:" + reader["id"])
    trail = re.search(r'<section id="provenance".*?</section>', page, re.S).group(0)
    words = text_of(trail)
    assert "Observed activity" in words, words
    assert "Saw change in x.csv (not verified)" in words
    assert "1 observed activity on this trail: not runs, never a confirmed source" in words
    assert re.search(r'data-e="[^"]*\d+>\d+o', trail), "the line is drawn as a possible source"


def test_a_row_whose_details_cannot_be_read_says_so(proj):
    details(Observer(proj)("python a.py"))
    rows = journal(proj)
    broken = {**rows[-1], "seq": 4, "id": "e4", "details": {"contract": "observed_activity/9"}}
    with open(proj / "journal.jsonl", "ab") as fh:
        fh.write(json.dumps(broken).encode() + b"\n")
    site = build(proj)
    assert "This kit cannot read the details of this observation" in text_of(relation_page(site, "e4"))


@pytest.mark.parametrize("profile", ["read", "evidence"])
def test_a_share_shows_no_observation_candidate(proj, profile):
    """Decision 10: a share's data and runs never carry an observation; its builder reads no trail."""
    from aelix_analytics.publishing import build as share

    made_by_python(proj, "data/derived/x.csv", "a\n1\n")
    run(proj, "use.py", "import polars as pl\nprint(pl.read_csv('data/derived/x.csv'))\n")
    origin = core.Origin("human", "cli")
    result = share.build_publication(core.Project(proj), profile=profile, origin=origin)
    for path in (proj / result["path"]).rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert b"observed_changes" not in data and b"changed_during" not in data and b"Outside the kit" not in data
