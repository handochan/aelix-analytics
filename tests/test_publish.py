"""#21 PR-2: publishing a bound build to a folder and its records (PUBLISH-FOLDER 4, 6, 8; ADR-0009).

What ADR-0009's "구현에서 확인할 것" gives PR-2: 3 (interruption at each boundary), 4 (reuse, refusal,
files outside the manifest, metadata), 5 (the build changing during a publish), 7 (a channel's owner),
9 (a busy and an unsupported lock, another attempt's live staging), 10 (a forced end before the
receipt, a receipt that cannot be written). The channel page's arrival check and --switch-to are PR-3.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from aelix_analytics import _platform, core, relations, writer
from aelix_analytics.publishing import build, publish, target
from aelix_analytics.publishing.model import PublicationError

ORIGIN = core.Origin("human", "cli")
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "docs/schemas/analytics-records.schema.json").read_text(encoding="utf-8"))


@pytest.fixture
def project(tmp_path):
    return core.init_project(tmp_path / "project", title="Weekly sales", origin=ORIGIN)


def built(p):
    return build.build_publication(p, origin=ORIGIN)["publication_id"]


def events(p, kind=None):
    rows = [json.loads(line) for line in p.journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [e for e in rows if kind is None or e["kind"] == kind]


def latest(share, channel="weekly-sales"):
    return json.loads((share / "projects" / channel / "latest.json").read_text(encoding="utf-8"))


def tree(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


def test_a_first_publish_makes_the_target_and_switches_both_pointers(project, tmp_path):
    pid = built(project)
    share = tmp_path / "공유 폴더"
    result = publish.publish(project, pid, share, origin=ORIGIN)
    assert result["ok"] and result["outcome"] == "published"
    assert result["steps"] == {"transfer": "done", "check": "done", "commit": "done", "final_check": "done",
                               "switch": "done"}
    assert result["pointers"] == {"latest_json": "new", "channel_page": "new"}
    assert (share / "analytics-target.json").is_file() and (share / "publications" / pid / "index.html").is_file()
    assert latest(share)["latest"] == pid and latest(share)["project_title"] == "Weekly sales"
    assert f'content="{pid}"' in (share / "projects/weekly-sales/index.html").read_text(encoding="utf-8")
    assert os.listdir(share / ".staging") == []
    bound = build_folder(project, pid)
    assert {n: (share / "publications" / pid / n).read_bytes() for n in tree(bound) if (bound / n).is_file()} == {
        n: (bound / n).read_bytes() for n in tree(bound) if (bound / n).is_file()}
    start, receipt = events(project, "publish_attempt"), events(project, "publish_receipt")
    assert len(start) == len(receipt) == 1 and start[0]["seq"] < receipt[0]["seq"]
    assert start[0]["details"]["attempt_id"] == receipt[0]["details"]["attempt_id"]
    assert receipt[0]["refs"][0]["digest"]["sha256"] == receipt[0]["details"]["package_sha256"]
    # The floor is at least the publish records' (a new project's is this kit's own version).
    floor = core.read_json(project.marker, {})["min_writer_version"]
    assert relations.version_key(floor) >= relations.version_key(relations.PUBLISH_WRITER_SINCE)
    assert result["access_note"].startswith("Anyone who can open")


def build_folder(p, pid):
    return p.root / ".analytics/publications" / pid


def test_the_records_follow_the_schema_and_a_receipt_names_what_it_needs(project, tmp_path):
    publish.publish(project, built(project), tmp_path / "share", origin=ORIGIN)
    validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
    folder = tmp_path / "mine"
    folder.mkdir()
    publish.publish(project, built(project), tmp_path / "share", origin=ORIGIN, channel="other")
    (build_folder(project, pid := built(project)) / "about.html").write_bytes(b"changed")
    assert publish.publish(project, pid, tmp_path / "share", origin=ORIGIN)["outcome"] == "failed"
    for event in events(project):
        errors = sorted(validator.iter_errors(event), key=str)
        assert not errors, (event["kind"], errors[:1])
    receipt = events(project, "publish_receipt")[0]
    assert relations.publish_details_problem("publish_receipt", receipt["details"]) is None
    broken = {**receipt["details"], "outcome": "failed"}
    broken.pop("failure", None)
    assert relations.publish_details_problem("publish_receipt", broken) == "a failed receipt names its failure"
    assert not Draft202012Validator(SCHEMA).is_valid({**receipt, "details": broken})


def test_the_same_build_again_is_reused_and_an_older_build_is_not_newer(project, tmp_path):
    share = tmp_path / "share"
    a = built(project)
    publish.publish(project, a, share, origin=ORIGIN)
    again = publish.publish(project, a, share, origin=ORIGIN)
    assert again["outcome"] == "published" and again["steps"]["transfer"] == "reused" and again["steps"]["switch"] == "already"
    b = built(project)
    newer = publish.publish(project, b, share, origin=ORIGIN)
    assert newer["outcome"] == "published" and latest(share)["latest"] == b
    older = publish.publish(project, a, share, origin=ORIGIN)
    assert older["ok"] and older["outcome"] == "not_newer" and latest(share)["latest"] == b
    assert [h["publication_id"] for h in latest(share)["history"]] == [a, b]


def test_build_order_is_the_binding_s_seq_not_its_clock(project, tmp_path, monkeypatch):
    """ADR-0009 check 3: two builds whose build times run against their seq are ordered by seq."""
    share = tmp_path / "share"
    monkeypatch.setattr(core, "iso", lambda: "2026-12-31T00:00:00+09:00")
    first = built(project)
    monkeypatch.setattr(core, "iso", lambda: "2026-01-01T00:00:00+09:00")
    second = built(project)
    monkeypatch.undo()
    publish.publish(project, first, share, origin=ORIGIN)
    assert publish.publish(project, second, share, origin=ORIGIN)["outcome"] == "published"
    assert latest(share)["latest"] == second


@pytest.mark.parametrize("first", [True, False], ids=["first", "later"])
@pytest.mark.parametrize("step", ["init", "lock", "transfer", "check", "commit", "final_check", "switch",
                                  "channel_page"])
def test_an_interrupted_publish_leaves_only_checked_versions_in_the_pointers(project, tmp_path, monkeypatch, step, first):
    """ADR-0009 check 3 for a first and a later publish: the receipt says what each pointer holds, and
    running it again ends in reuse and a switch."""
    share = tmp_path / "share"
    earlier = None
    if not first:
        earlier = built(project)
        publish.publish(project, earlier, share, origin=ORIGIN)
    pid = built(project)

    def stop(at):
        if at == step:
            raise KeyboardInterrupt

    monkeypatch.setattr(publish, "_reached", stop)
    result = publish.publish(project, pid, share, origin=ORIGIN)
    monkeypatch.undo()
    assert result["outcome"] == "interrupted" and result["failure"]["step"] == step
    path = share / "projects/weekly-sales/latest.json"
    pointed = latest(share)["latest"] if path.exists() else None
    page = page_points_to(share) if path.parent.joinpath("index.html").exists() else None
    assert pointed in (earlier, pid) and page in (earlier, pid)
    if pid in (pointed, page):  # only once the version was checked where it lies
        assert result["steps"]["final_check"] == "done"
    receipt = events(project, "publish_receipt")[-1]["details"]
    if receipt["steps"]["switch"] != "not_started":
        held = {pid: "new", earlier: "old", None: "missing"}
        assert receipt["pointers"] == {"latest_json": held[pointed], "channel_page": held[page]}
    if (share / ".staging").exists():
        assert os.listdir(share / ".staging") == []
    again = publish.publish(project, pid, share, origin=ORIGIN)
    assert again["outcome"] == "published" and latest(share)["latest"] == pid
    assert again["steps"]["transfer"] in ("done", "reused")


def test_an_interrupted_old_attempt_run_again_does_not_take_latest_back(project, tmp_path, monkeypatch):
    """ADR-0009 check 3: A's switch is cut off, B is published, A run again is not_newer."""
    share = tmp_path / "share"
    a = built(project)
    b = built(project)

    def stop(at):
        if at == "switch":
            raise KeyboardInterrupt

    monkeypatch.setattr(publish, "_reached", stop)
    assert publish.publish(project, a, share, origin=ORIGIN)["outcome"] == "interrupted"
    monkeypatch.undo()
    assert publish.publish(project, b, share, origin=ORIGIN)["outcome"] == "published"
    assert publish.publish(project, a, share, origin=ORIGIN)["outcome"] == "not_newer"
    assert latest(share)["latest"] == b


def test_another_version_under_the_same_id_is_refused_and_left_alone(project, tmp_path):
    """ADR-0009 check 4: a version is never overwritten; a file outside its manifest is refused
    without being removed; system metadata does not change the judgment."""
    share = tmp_path / "share"
    pid = built(project)
    publish.publish(project, pid, share, origin=ORIGIN)
    version = share / "publications" / pid
    (version / ".DS_Store").write_bytes(b"")
    (version / "._index.html").write_bytes(b"\x00\x05\x16\x07")
    assert publish.publish(project, pid, share, origin=ORIGIN)["steps"]["transfer"] == "reused"
    (version / "pages" / "extra.html").write_bytes(b"<p>someone's</p>")
    before = tree(version)
    result = publish.publish(project, pid, share, origin=ORIGIN)
    assert result["outcome"] == "failed" and result["failure"]["code"] == "version_differs"
    assert tree(version) == before and (version / "pages" / "extra.html").read_bytes() == b"<p>someone's</p>"


def test_files_beside_the_build_are_not_copied_and_a_changed_build_does_not_switch(project, tmp_path):
    """ADR-0009 checks 4 and 5: only what the bound manifest lists is copied; a build file changed
    after its binding stops before the switch; the project's analysis is never read again."""
    share = tmp_path / "share"
    pid = built(project)
    folder = build_folder(project, pid)
    (folder / "notes.txt").write_bytes(b"not in the manifest")
    first = publish.publish(project, pid, share, origin=ORIGIN)
    assert first["outcome"] == "published" and not (share / "publications" / pid / "notes.txt").exists()
    other = built(project)
    (build_folder(project, other) / "about.html").write_bytes(b"<p>changed after the build</p>")
    changed = publish.publish(project, other, share, origin=ORIGIN)
    assert changed["outcome"] == "failed" and changed["failure"]["step"] == "check"
    assert latest(share)["latest"] == pid and not (share / "publications" / other).exists()
    third = built(project)
    (build_folder(project, third) / "publication.json").write_bytes(b"{}")
    manifest = publish.publish(project, third, share, origin=ORIGIN)
    assert manifest["failure"]["code"] == "build_changed" and latest(share)["latest"] == pid


def test_a_refusal_before_the_start_record_records_nothing(project, tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()
    (folder / "index.html").write_bytes(b"<p>a person's page</p>")
    before = project.journal.read_bytes()
    with pytest.raises(PublicationError) as caught:
        publish.publish(project, built(project), folder, origin=ORIGIN)
    assert caught.value.code == "target_refused" and caught.value.details["reason"] == "not_empty"
    journal = project.journal.read_bytes()
    assert not [e for e in events(project) if e["kind"].startswith("publish")]
    with pytest.raises(PublicationError) as caught:
        publish.publish(project, "pub-" + "0" * 26, tmp_path / "share", origin=ORIGIN)
    assert caught.value.code == "not_bound" and project.journal.read_bytes() == journal
    assert sorted(os.listdir(folder)) == ["index.html"] and not (tmp_path / "share").exists()
    assert before != journal  # only the build wrote


def test_another_project_s_channel_is_taken_only_when_asked(project, tmp_path):
    """ADR-0009 check 7: the same title is the same channel name; the second project is refused
    until it asks with --reassign-channel, which its receipt records."""
    share = tmp_path / "share"
    publish.publish(project, built(project), share, origin=ORIGIN)
    other = core.init_project(tmp_path / "other", title="Weekly sales", origin=ORIGIN)
    pid = built(other)
    refused = publish.publish(other, pid, share, origin=ORIGIN)
    assert refused["outcome"] == "failed" and refused["failure"]["code"] == "channel_owned"
    taken = publish.publish(other, pid, share, origin=ORIGIN, reassign=True)
    assert taken["outcome"] == "published" and latest(share)["latest"] == pid
    receipt = events(other, "publish_receipt")[-1]["details"]
    owner = core.read_json(project.marker, {})["project_id"]
    assert receipt["channel"]["reassigned_from"] == owner
    assert len(latest(share)["history"]) == 2


def test_a_busy_target_waits_then_stops_and_an_unsupported_lock_goes_on(project, tmp_path, monkeypatch):
    """ADR-0009 check 9: a lock held past the wait is target_busy in a receipt; a file system without
    locks is published without one and says so."""
    share = tmp_path / "share"
    pid = built(project)
    publish.publish(project, pid, share, origin=ORIGIN)
    monkeypatch.setattr(publish, "LOCK_WAIT_SECONDS", 0.2)
    with project.platform.locks.try_exclusive(share / ".analytics-target.lock"):
        busy = publish.publish(project, built(project), share, origin=ORIGIN)
    assert busy["failure"]["code"] == "target_busy" and busy["lock"] == {"state": "busy"}
    assert events(project, "publish_receipt")[-1]["details"]["failure"]["code"] == "target_busy"

    class NoLocksOnTheTarget:
        """The target's file system has no locks; the project's (the journal's) still does."""

        def __init__(self, real):
            self.real = real

        def exclusive(self, path):
            return self.real.exclusive(path)

        def try_exclusive(self, path):
            return self.real.try_exclusive(path)

        def exclusive_within(self, path, seconds):
            if Path(path).name == ".analytics-target.lock":
                raise _platform.LockUnavailable(publish.errno.ENOLCK, "no locks here")
            return self.real.exclusive_within(path, seconds)

    pid = built(project)
    monkeypatch.setattr(project.platform, "locks", NoLocksOnTheTarget(project.platform.locks))
    unlocked = publish.publish(project, pid, share, origin=ORIGIN)
    assert unlocked["outcome"] == "published" and unlocked["lock"]["state"] == "unsupported"


def test_another_attempt_s_live_staging_is_kept_and_an_old_one_goes(project, tmp_path):
    """ADR-0009 decision 5: another user's or machine's attempt is kept until 24 hours without an
    update; one of this machine, host and user whose process ended goes."""
    share = tmp_path / "share"
    publish.publish(project, built(project), share, origin=ORIGIN)
    staging = share / ".staging"

    def leftover(machine, updated, pid=999999):
        aid = target.new_attempt_id()
        (staging / aid).mkdir()
        (staging / aid / "x.html").write_bytes(b"x")
        (staging / f"{aid}.json").write_bytes(target.canonical({
            "schema": "analytics.publish_staging/1", "attempt_id": aid, "project_id": "prj_x",
            "publication_id": "pub-" + "0" * 26, "machine": machine, "host": target.digest(publish.socket.gethostname()),
            "user": publish._user(), "pid": pid, "started_at": updated, "updated_at": updated}))
        return aid

    me = target.digest(project.platform.processes.machine_identity())
    live = leftover("another-machine", core.iso())
    old = leftover("another-machine", "2020-01-01T00:00:00+00:00")
    ended = leftover(me, core.iso()) if me else None
    publish.publish(project, built(project), share, origin=ORIGIN)
    names = set(os.listdir(staging))
    assert {live, f"{live}.json"} <= names
    assert old not in names and f"{old}.json" not in names
    if ended:
        assert ended not in names


def test_a_forced_end_before_the_receipt_is_traced_by_the_next_publish(project, tmp_path):
    """ADR-0009 check 10: the process ends after the switch, before its receipt; its start record
    stays, reads as not recorded, and the next publish to that target names its traces once."""
    share = tmp_path / "share"
    pid = built(project)
    script = (
        "import os, sys\n"
        "from aelix_analytics import core\n"
        "from aelix_analytics.publishing import publish\n"
        "def end(at):\n"
        "    if at == 'receipt':\n"
        "        os._exit(9)\n"
        "publish._reached = end\n"
        f"publish.publish(core.Project({str(project.root)!r}), {pid!r}, {str(share)!r}, origin=core.Origin('human', 'cli'))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    done = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=120)
    assert done.returncode == 9, done.stderr
    assert latest(share)["latest"] == pid
    journal = project.journal.read_bytes()
    rows = relations.publish_attempts(relations.Journal(journal))
    assert [r["recording_status"] for r in rows] == ["incomplete"] and rows[0]["reason"] == "no_completion_event"
    nxt = publish.publish(project, pid, share, origin=ORIGIN)
    assert project.journal.read_bytes().startswith(journal)
    traces = events(project, "publish_receipt")[-1]["details"]["observations"]["unrecorded_attempts"]
    assert [t["attempt_id"] for t in traces] == [rows[0]["attempt_id"]] and traces[0]["latest_is_its_version"]
    assert nxt["outcome"] == "published"
    publish.publish(project, pid, share, origin=ORIGIN)
    assert events(project, "publish_receipt")[-1]["details"]["observations"]["unrecorded_attempts"] == []
    from aelix_analytics import workspace
    workspace.build(project)
    pages = "".join(p.read_text(encoding="utf-8") for p in (project.root / "workspace").rglob("*.html"))
    assert "Publish result not recorded" in pages


def test_a_receipt_that_cannot_be_written_is_said_with_the_target_s_result(project, tmp_path, monkeypatch):
    """ADR-0009 decision 7: the command gives the target's result and the record's failure, and
    ends in failure; the attempt then reads as one whose result was not recorded."""
    share = tmp_path / "share"
    pid = built(project)
    with core.writer_for(project, ORIGIN).locked("publish") as held:
        held_class = type(held)
    appended = held_class.append

    def refusing(self, kind, *args, **kwargs):
        if kind == "publish_receipt":
            raise OSError("the disk is full")
        return appended(self, kind, *args, **kwargs)

    monkeypatch.setattr(held_class, "append", refusing)
    result = publish.publish(project, pid, share, origin=ORIGIN)
    assert not result["ok"] and result["error"]["code"] == "receipt_not_recorded"
    assert result["addresses"]["version"].endswith(f"/publications/{pid}/index.html")
    assert latest(share)["latest"] == pid
    assert [r["recording_status"] for r in relations.publish_attempts(relations.Journal(project.journal.read_bytes()))] == [
        "incomplete"]


def test_a_build_after_a_publish_never_names_the_target_s_folder(project, tmp_path):
    """PUBLISH-FOLDER 6.4: the receipt's paths are private values of every later copy. The folder
    here lies under the temporary folder, itself a private value, so the snapshot's values are
    asked directly too: without 6.4 they would not hold the target."""
    from aelix_analytics.publishing.snapshot import capture_snapshot

    share = tmp_path / "team share"
    result = publish.publish(project, built(project), share, origin=ORIGIN)
    values = capture_snapshot(project).private_values
    assert os.fspath(share.resolve()) in values and os.fspath(share) in values
    assert result["addresses"]["version"] in values
    core.record(project, "finding", f"copied from {share.resolve()} by hand", origin=ORIGIN)
    with pytest.raises(PublicationError) as caught:
        build.build_publication(project, origin=ORIGIN)
    assert caught.value.code == "leak_found"


def test_publish_records_are_publication_family_kinds(project, tmp_path):
    from aelix_analytics.publishing.basis import PUBLICATION_KINDS
    from aelix_analytics.publishing.snapshot import capture_snapshot

    publish.publish(project, built(project), tmp_path / "share", origin=ORIGIN)
    assert {"publication", "publish_attempt", "publish_receipt"} <= PUBLICATION_KINDS
    snapshot = capture_snapshot(project)
    last_content = max(e["seq"] for e in events(project) if e["kind"] not in PUBLICATION_KINDS)
    assert snapshot.basis.content.content_seq == last_content < snapshot.basis.as_of_seq


def test_the_command_publishes_and_lists(project, tmp_path, capsys, monkeypatch):
    from aelix_analytics import __main__ as cli

    pid = built(project)
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(project.root))
    assert cli.main(["publish", "--build", pid, "--to", str(tmp_path / "share")]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["outcome"] == "published" and "Anyone who can open" in out.err
    assert cli.main(["publish", "--list"]) == 0
    listed = json.loads(capsys.readouterr().out)["attempts"]
    assert listed[0]["receipt"]["outcome"] == "published" and listed[0]["recording_status"] == "complete"
    assert cli.main(["publish", "--build", pid]) != 0


def interrupt_at(monkeypatch, step):
    def stop(at):
        if at == step:
            raise KeyboardInterrupt

    monkeypatch.setattr(publish, "_reached", stop)


def page_points_to(share, channel="weekly-sales"):
    return publish._page_points_to(share / "projects" / channel / "index.html")[0]


def test_a_latest_json_replaced_by_another_publish_is_not_drawn_on_the_page(project, tmp_path, monkeypatch):
    """Without a lock, another publish may replace latest.json right after this one: its version was
    not checked here, so the page is not drawn from it and the attempt is superseded (design 8)."""
    share = tmp_path / "share"
    a = built(project)
    publish.publish(project, a, share, origin=ORIGIN)
    b, c = built(project), built(project)
    written = publish._write_json

    def raced(path, value, files, attempt_id):
        written(path, value, files, attempt_id)
        if path.name == "latest.json":
            written(path, {**value, "latest": c}, files, attempt_id)

    monkeypatch.setattr(publish, "_write_json", raced)
    result = publish.publish(project, b, share, origin=ORIGIN)
    assert result["ok"] and result["outcome"] == "superseded" and result["steps"]["switch"] == "superseded"
    assert result["pointers"]["latest_json"] == "other" and page_points_to(share) == a


def test_a_link_inside_the_build_folder_is_refused_at_any_level(project, tmp_path):
    """Design 4, transfer (3): a folder of the build replaced by a link to the same bytes elsewhere
    is not followed, even though every byte would match the binding."""
    share = tmp_path / "share"
    pid = built(project)
    folder = build_folder(project, pid)
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(folder / "pages", elsewhere)
    shutil.rmtree(folder / "pages")
    try:
        os.symlink(elsewhere, folder / "pages", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this account cannot make a link: {exc}")
    result = publish.publish(project, pid, share, origin=ORIGIN)
    assert result["outcome"] == "failed" and result["failure"]["code"] == "build_changed"
    assert result["steps"]["transfer"] == "failed" and not (share / "publications" / pid).exists()


def test_a_channel_page_that_cannot_be_redrawn_is_a_recorded_failure(project, tmp_path, monkeypatch):
    """latest.json already names this version; the page write fails: the receipt says so (6.2)."""
    share = tmp_path / "share"
    a = built(project)
    publish.publish(project, a, share, origin=ORIGIN)
    b = built(project)
    interrupt_at(monkeypatch, "channel_page")
    publish.publish(project, b, share, origin=ORIGIN)
    monkeypatch.undo()
    assert latest(share)["latest"] == b and page_points_to(share) == a

    def refused(*args):
        raise PermissionError("read-only page")

    monkeypatch.setattr(publish, "_write_page", refused)
    result = publish.publish(project, b, share, origin=ORIGIN)
    assert "receipt" in result and result["outcome"] == "failed"
    assert result["failure"]["code"] == "switch_failed" and result["steps"]["switch"] == "failed"
    # The page still holds what it held when this attempt began.
    assert result["pointers"] == {"latest_json": "new", "channel_page": "old"}
    monkeypatch.undo()
    again = publish.publish(project, b, share, origin=ORIGIN)
    assert again["outcome"] == "published" and again["steps"]["switch"] == "already" and page_points_to(share) == b


def test_a_receipt_of_an_unknown_version_is_not_a_result(project, tmp_path, monkeypatch):
    share = tmp_path / "share"
    pid = built(project)
    interrupt_at(monkeypatch, "receipt")
    with pytest.raises(KeyboardInterrupt):
        publish.publish(project, pid, share, origin=ORIGIN)
    monkeypatch.undo()
    start = events(project, "publish_attempt")[-1]
    later = {**start, "schema": "analytics.event/99", "kind": "publish_receipt", "seq": start["seq"] + 1,
             "id": f"e{start['seq'] + 1}", "details": {**start["details"], "outcome": "published"}}
    with project.journal.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(later) + "\n")
    rows = publish.attempts(project)["attempts"]
    assert [(r["recording_status"], r["receipt"]) for r in rows] == [("incomplete", None)]


def test_the_receipt_keeps_its_references_and_fits_a_journal_line(project, tmp_path, monkeypatch):
    """6.2: the receipt's refs are the start record's even when it ends before any check; every
    list shrinks to fit, its full count kept."""
    share = tmp_path / "share"
    interrupt_at(monkeypatch, "transfer")
    publish.publish(project, built(project), share, origin=ORIGIN)
    monkeypatch.undo()
    start, receipt = events(project, "publish_attempt")[-1], events(project, "publish_receipt")[-1]
    assert receipt["details"]["package_sha256"] is None and receipt["refs"] == start["refs"]
    assert start["refs"][0]["digest"]["sha256"]
    long = os.fspath(share) + "/" + "가" * 300
    monkeypatch.setattr(publish, "_access", lambda root, version: {
        "reread": "done", "posix": {"others_can_read": False, "blocked": [f"{long}/{n}" for n in range(200)],
                                    "blocked_count": 200}, "windows_acl": "not_checked"})
    result = publish.publish(project, built(project), share, origin=ORIGIN)
    assert result["ok"] and "receipt" in result
    blocked = events(project, "publish_receipt")[-1]["details"]["access"]["posix"]
    assert blocked["blocked_count"] == 200 and len(blocked["blocked"]) < 200 and blocked["truncated"]


def test_pointers_that_differ_are_made_to_agree_by_the_next_publish_after_a_check(project, tmp_path, monkeypatch):
    """Design 8: B stopped between its pointers; publishing A again is not_newer, and draws the
    page from latest.json only once B is checked where it lies."""
    share = tmp_path / "share"
    a = built(project)
    publish.publish(project, a, share, origin=ORIGIN)
    b = built(project)
    interrupt_at(monkeypatch, "channel_page")
    publish.publish(project, b, share, origin=ORIGIN)
    monkeypatch.undo()
    (share / "publications" / b / "about.html").write_bytes(b"<p>not yet arrived</p>")
    unchecked = publish.publish(project, a, share, origin=ORIGIN)
    assert unchecked["outcome"] == "not_newer" and page_points_to(share) == a
    repair = events(project, "publish_receipt")[-1]["details"]["observations"]["channel_page_repair"]
    assert repair == {"publication_id": b, "state": "not_verified", "basis": "project_record"}
    shutil.copyfile(build_folder(project, b) / "about.html", share / "publications" / b / "about.html")
    repaired = publish.publish(project, a, share, origin=ORIGIN)
    assert repaired["outcome"] == "not_newer" and page_points_to(share) == b
    assert repaired["pointers"] == {"latest_json": "old", "channel_page": "other"}  # drawn from B, not this attempt
    assert events(project, "publish_receipt")[-1]["details"]["observations"]["channel_page_repair"]["state"] == "done"


def test_failures_after_the_lock_name_their_step_reason_and_path(project, tmp_path):
    share = tmp_path / "share"
    publish.publish(project, built(project), share, origin=ORIGIN)
    pid = built(project)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        os.symlink(elsewhere, share / "publications" / pid, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this account cannot make a link: {exc}")
    result = publish.publish(project, pid, share, origin=ORIGIN)
    assert result["failure"]["step"] == "transfer" and result["failure"]["reason"] == "managed_link"
    assert result["failure"]["paths"] == [os.fspath(share / "publications" / pid)]
    os.unlink(share / "publications" / pid)
    (share / "projects/odd").mkdir()
    (share / "projects/odd/latest.json").write_bytes(b'{"schema": "analytics.publish_channel/9"}')
    odd = publish.publish(project, pid, share, origin=ORIGIN)
    assert odd["outcome"] == "failed" and odd["failure"]["step"] == "channel"


def test_a_file_under_a_version_s_name_is_not_a_version(project, tmp_path):
    """Its own deterministic ZIP under the version's name would verify; it is still not a folder."""
    share = tmp_path / "share"
    publish.publish(project, built(project), share, origin=ORIGIN)
    from aelix_analytics.publishing.package import write_zip

    pid = built(project)
    event, folder, _journal = publish.binding(project, pid)
    write_zip(folder, tmp_path / "copy.zip", project_root=project.root, binding=event["details"])
    from aelix_analytics.publishing import checks
    assert checks.verify_package(tmp_path / "copy.zip", binding=event["details"])  # it would verify
    (share / "publications" / pid).write_bytes((tmp_path / "copy.zip").read_bytes())
    result = publish.publish(project, pid, share, origin=ORIGIN)
    assert result["outcome"] == "failed" and result["failure"]["code"] == "version_differs"
    assert latest(share)["latest"] != pid


def test_after_the_switch_an_interruption_does_not_change_the_outcome(project, tmp_path, monkeypatch):
    share = tmp_path / "share"
    pid = built(project)

    def stop(root, version):
        raise KeyboardInterrupt

    monkeypatch.setattr(publish, "_access", stop)
    result = publish.publish(project, pid, share, origin=ORIGIN)
    assert result["ok"] and result["outcome"] == "published" and "receipt" in result
    details = events(project, "publish_receipt")[-1]["details"]
    assert details["observations"]["after_switch"]["step"] == "cleanup"
    assert details["access"]["reread"] == "not_checked" and details["addresses"]


def test_reassign_on_the_project_s_own_channel_still_compares_build_order(project, tmp_path):
    """ADR-0009 decision 5: only a publish that takes another project's channel over skips it."""
    share = tmp_path / "share"
    a, b = built(project), built(project)
    publish.publish(project, b, share, origin=ORIGIN)
    older = publish.publish(project, a, share, origin=ORIGIN, reassign=True)
    assert older["outcome"] == "not_newer" and latest(share)["latest"] == b
    assert "older build" not in (share / "projects/weekly-sales/index.html").read_text(encoding="utf-8")


def test_a_bad_channel_name_is_refused_before_anything_is_written(project, tmp_path):
    pid = built(project)
    journal = project.journal.read_bytes()
    with pytest.raises(PublicationError):
        publish.publish(project, pid, tmp_path / "share", origin=ORIGIN, channel="../up")
    assert project.journal.read_bytes() == journal and not (tmp_path / "share").exists()


def test_a_live_attempt_on_this_machine_keeps_its_staging_and_temporary_files_go_with_their_owner(project, tmp_path):
    share = tmp_path / "share"
    publish.publish(project, built(project), share, origin=ORIGIN)
    staging, channel = share / ".staging", share / "projects/weekly-sales"
    me = target.digest(project.platform.processes.machine_identity())
    if me is None:
        pytest.skip("this machine has no identity to compare")
    aid, gone = target.new_attempt_id(), target.new_attempt_id()
    (staging / aid).mkdir()
    (staging / f"{aid}.json").write_bytes(target.canonical({
        "schema": "analytics.publish_staging/1", "attempt_id": aid, "project_id": "prj_x",
        "publication_id": "pub-" + "0" * 26, "machine": me, "host": target.digest(publish.socket.gethostname()),
        "user": publish._user(), "pid": os.getpid(), "started_at": core.iso(), "updated_at": core.iso()}))
    (channel / f".{aid}.latest.json.tmp").write_bytes(b"{}")
    (channel / f".{gone}.index.html.tmp").write_bytes(b"x")
    old = 1_000_000_000
    os.utime(channel / f".{gone}.index.html.tmp", (old, old))
    publish.publish(project, built(project), share, origin=ORIGIN)
    assert (staging / aid).exists() and (channel / f".{aid}.latest.json.tmp").exists()
    assert not (channel / f".{gone}.index.html.tmp").exists()
    assert [k for k in events(project, "publish_receipt")[-1]["details"]["observations"]["kept"]
            if "not a publisher name" in k.get("reason", "")] == []


def test_the_launcher_runs_publish_and_its_list(project, tmp_path):
    if os.name == "nt":
        pytest.skip("bin/analytics is the POSIX launcher; Windows runs analytics.cmd")
    pid = built(project)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "AELIX_ANALYTICS_PROJECT": str(project.root)}
    done = subprocess.run([str(ROOT / "bin/analytics"), "publish", "--build", pid, "--to", str(tmp_path / "share")],
                          env=env, capture_output=True, timeout=300, cwd=project.root)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["outcome"] == "published"
    listed = subprocess.run([str(ROOT / "bin/analytics"), "publish", "--list"], env=env, capture_output=True,
                            timeout=300, cwd=project.root)
    assert listed.returncode == 0 and json.loads(listed.stdout)["attempts"][0]["recording_status"] == "complete"


@pytest.mark.parametrize("locks", ["held", "unsupported"])
def test_two_publishes_at_once_end_with_a_checked_latest_and_both_receipts(project, tmp_path, locks):
    """ADR-0009 check 9 for PR-2: two processes publish an older and a newer build to one target at
    the same moment. With the lock one waits; without one (a file system that has none) either may
    win the switch. Either way latest.json and the page name a version checked there, and each
    attempt has its receipt."""
    share = tmp_path / "share"
    older, newer = built(project), built(project)
    go = tmp_path / "go"
    script = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "from aelix_analytics import _platform, core\n"
        "from aelix_analytics.publishing import publish\n"
        "project = core.Project(sys.argv[1])\n"
        "if sys.argv[4] == 'unsupported':\n"
        "    real = project.platform.locks\n"
        "    class NoTargetLocks:\n"
        "        exclusive = staticmethod(real.exclusive)\n"
        "        try_exclusive = staticmethod(real.try_exclusive)\n"
        "        def exclusive_within(self, path, seconds):\n"
        "            if Path(path).name == '.analytics-target.lock':\n"
        "                raise _platform.LockUnavailable(publish.errno.ENOLCK, 'no locks here')\n"
        "            return real.exclusive_within(path, seconds)\n"
        "    project.platform.locks = NoTargetLocks()\n"
        "while not os.path.exists(sys.argv[5]):\n"
        "    time.sleep(0.005)\n"
        "r = publish.publish(project, sys.argv[2], sys.argv[3], origin=core.Origin('human', 'cli'))\n"
        "print(r['outcome'], 'receipt' in r)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    runs = [subprocess.Popen([sys.executable, "-c", script, str(project.root), pid, str(share), locks, str(go)],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            for pid in (older, newer)]
    go.write_bytes(b"")
    outs = [run.communicate(timeout=300) for run in runs]
    assert all(run.returncode == 0 for run in runs), outs
    results = [out.split() for out, _ in outs]
    assert all(found == "True" for _, found in results), outs
    assert {outcome for outcome, _ in results} <= {"published", "not_newer", "superseded"}
    pointed = latest(share)["latest"]
    assert pointed in (older, newer) and page_points_to(share) in (older, newer)
    if locks == "held":
        assert pointed == newer and page_points_to(share) == newer
    for pid in {pointed, page_points_to(share)}:
        event, _folder, _journal = publish.binding(project, pid)
        publish._verify(share / "publications" / pid, pid, event["details"])
    rows = relations.publish_attempts(relations.Journal(project.journal.read_bytes()))
    assert [r["recording_status"] for r in rows] == ["complete", "complete"]
    assert os.listdir(share / ".staging") == []


@pytest.mark.parametrize("trouble", ["interrupt", "unread"])
def test_redrawing_the_page_of_a_version_already_latest_is_recorded_either_way(project, tmp_path, monkeypatch, trouble):
    """latest.json names B, the page A; B again: a stop while the page is drawn, or a page that
    cannot be read back after it, is a receipt that says so, never a success."""
    share = tmp_path / "share"
    publish.publish(project, built(project), share, origin=ORIGIN)
    b = built(project)
    interrupt_at(monkeypatch, "channel_page")
    publish.publish(project, b, share, origin=ORIGIN)
    monkeypatch.undo()
    if trouble == "interrupt":
        interrupt_at(monkeypatch, "channel_page")
        expected = "interrupted"
    else:
        read = publish._page_points_to
        drawn = []
        written = publish._write_page

        def write(*args):
            written(*args)
            drawn.append(True)

        monkeypatch.setattr(publish, "_write_page", write)
        monkeypatch.setattr(publish, "_page_points_to", lambda path: (None, "unknown") if drawn else read(path))
        expected = "unknown"
    result = publish.publish(project, b, share, origin=ORIGIN)
    assert "receipt" in result and result["outcome"] == expected and not result["ok"]
    assert result["steps"]["switch"] in ("interrupted", "unknown") and result["failure"]


def test_hand_edited_publish_lines_of_any_shape_are_read_not_raised(project, tmp_path):
    """A start record or receipt with a field of another type is not a result, and reading it
    (the list, a later publish, the content basis) never stops (ADR-0006 6)."""
    from aelix_analytics.publishing.basis import content_basis

    share = tmp_path / "share"
    pid = built(project)
    publish.publish(project, pid, share, origin=ORIGIN)
    receipt = events(project, "publish_receipt")[-1]
    odd = []
    for key in ("steps", "pointers", "observations", "target", "lock", "reports", "failure", "channel"):
        for value in ([], {}, 1, None, [[1]], {"transfer": []}, {"latest_json": [], "channel_page": {}}):
            details = json.loads(json.dumps(receipt["details"]))
            if isinstance(details.get(key), dict) and isinstance(value, dict):
                details[key] = {**details[key], **value}
            else:
                details[key] = value
            odd.append(details)
    lines = [json.dumps({**receipt, "seq": receipt["seq"] + n + 1, "id": f"e{receipt['seq'] + n + 1}", "details": d})
             for n, d in enumerate(odd)]
    with project.journal.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    journal = relations.Journal(project.journal.read_bytes())
    problems = [relations.publish_details_problem("publish_receipt", d) for d in odd]  # never raises
    # Steps of another shape are never a receipt ({} merges into the steps and leaves them as they were).
    assert all(isinstance(p, str) for n, p in enumerate(problems[:7]) if n != 1) and problems[1] is None
    assert publish.attempts(project)["ok"]
    content_basis(journal, validate_reference=writer.reference_problem)
    assert publish.publish(project, pid, share, origin=ORIGIN)["outcome"] == "published"
