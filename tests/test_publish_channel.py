"""#21 PR-3: the channel page's arrival check, `--switch-to`, saved targets and what PR-2 left to it
(PUBLISH-FOLDER 7, 8, 9.2, 9.3; ADR-0009).

What ADR-0009's "구현에서 확인할 것" gives PR-3 here: 3 (an interruption between the two pointers with
builds A and B and a switch), 4 (a switch only after its version is checked), 7 (the receipt of a
channel taken over), 10 (a forced end with the channel page between). The page in real browsers is
tests/test_publish_browser.py; another account reading a target is tools/publish_access_check.py.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from aelix_analytics import core, relations
from aelix_analytics.publishing import build, publish, target
from aelix_analytics.publishing.model import PublicationError

ORIGIN = core.Origin("human", "cli")
ROOT = Path(__file__).resolve().parents[1]


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


def page(share, channel="weekly-sales"):
    return (share / "projects" / channel / "index.html").read_text(encoding="utf-8")


def points_to(share, channel="weekly-sales"):
    return publish._page_points_to(share / "projects" / channel / "index.html")[0]


def arrival(text):
    return json.loads(html.unescape(re.search(r'data-check="([^"]*)"', text).group(1)))


def interrupt_at(monkeypatch, step):
    def stop(at):
        if at == step:
            raise KeyboardInterrupt

    monkeypatch.setattr(publish, "_reached", stop)


# --- the channel page (design 7) ---------------------------------------------------------------------

def test_the_channel_page_checks_the_newest_version_s_own_files_before_it_moves(project, tmp_path):
    share = tmp_path / "share"
    pid = built(project)
    publish.publish(project, pid, share, origin=ORIGIN)
    text = page(share)
    script = re.search(r"<script>(.*?)</script>", text, re.S).group(1)
    assert script == publish.ARRIVAL_SCRIPT and text.count("<script") == 1
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    policy = html.unescape(re.search(r'http-equiv="Content-Security-Policy" content="([^"]*)"', text).group(1))
    assert f"script-src 'sha256-{digest}'" in policy and "connect-src" not in policy and "default-src 'none'" in policy
    check = arrival(text)
    assert check["entry"] == f"../../publications/{pid}/index.html" and check["seconds"] == publish.ARRIVAL_SECONDS
    assert {"kind": "stylesheet", "href": f"../../publications/{pid}/assets/publication.css"} in check["check"]
    channel = share / "projects/weekly-sales"
    for item in check["check"]:
        assert item["kind"] in ("stylesheet", "image")
        assert item["href"].startswith(f"../../publications/{pid}/") and (channel / item["href"]).resolve().is_file()
    assert f'<noscript><meta http-equiv="refresh" content="0; url=../../publications/{pid}/index.html"></noscript>' in text
    assert publish.NOT_ARRIVED in text and publish.ACCESS_SENTENCE in text


def test_an_entry_s_loads_outside_the_version_or_its_manifest_are_not_checked(tmp_path):
    version = tmp_path / "publications" / "pub-01jzzzzzzzzzzzzzzzzzzzzzzz"
    (version / "assets").mkdir(parents=True)
    (version / "index.html").write_text(
        '<link rel="stylesheet" href="assets/a.css"><link rel="Stylesheet preload" href="assets/b%20c.css">'
        '<link rel="stylesheet" href="https://example.org/x.css"><link rel="stylesheet" href="../../x.css">'
        '<img src="data:image/png;base64,AA"><img src="assets/i.png"><img src="assets/i.png"><img src="/abs.png">'
        '<link rel="icon" href="assets/icon.png"><img src="assets/unlisted.png">', encoding="utf-8")
    manifest = {"entry": "index.html", "files": [{"path": p} for p in (
        "index.html", "assets/a.css", "assets/b c.css", "assets/i.png", "assets/icon.png")]}
    prefix = f"../../publications/{version.name}/"
    assert publish.arrival_resources(version, manifest) == [
        {"kind": "stylesheet", "href": prefix + "assets/a.css"},
        {"kind": "stylesheet", "href": prefix + "assets/b%20c.css"},
        {"kind": "image", "href": prefix + "assets/i.png"}]
    (version / "index.html").unlink()
    assert publish.arrival_resources(version, manifest) == []


def test_the_page_says_same_content_and_an_older_build_after_a_switch(project, tmp_path):
    share = tmp_path / "share"
    a = built(project)
    b = built(project)  # nothing changed between the two builds: the same content
    publish.publish(project, a, share, origin=ORIGIN)
    publish.publish(project, b, share, origin=ORIGIN)
    assert "same content as the version below" in page(share)
    assert "points to an older build" not in page(share)
    publish.switch(project, a, share, origin=ORIGIN)
    assert "This channel points to an older build, chosen by switching." in page(share)
    assert points_to(share) == a and arrival(page(share))["entry"] == f"../../publications/{a}/index.html"


# --- switching (design 8) ----------------------------------------------------------------------------

def test_switch_to_points_the_channel_back_and_records_a_switch(project, tmp_path):
    share = tmp_path / "share"
    a, b = built(project), built(project)
    publish.publish(project, a, share, origin=ORIGIN)
    publish.publish(project, b, share, origin=ORIGIN)
    before = {h["publication_id"]: h for h in latest(share)["history"]}
    result = publish.switch(project, a, share, origin=ORIGIN)
    assert result["ok"] and result["outcome"] == "published"
    assert result["steps"] == {"transfer": "not_started", "check": "not_started", "commit": "not_started",
                               "final_check": "done", "switch": "done"}
    assert result["pointers"] == {"latest_json": "new", "channel_page": "new"}
    assert latest(share)["latest"] == a and points_to(share) == a
    after = {h["publication_id"]: h for h in latest(share)["history"]}
    assert after[a]["latest_since"] > before[a]["latest_since"] and after[a]["build_seq"] == before[a]["build_seq"]
    assert after[a]["attempt_id"] == result["attempt_id"] and after[b] == before[b]
    start, receipt = events(project, "publish_attempt")[-1], events(project, "publish_receipt")[-1]
    assert start["details"]["action"] == receipt["details"]["action"] == "switch"
    assert start["text"] == f"Switching share to {a}" and receipt["text"] == f"Switched share to {a}"
    assert receipt["details"]["binding"] == "project_record" and receipt["refs"][0]["basis"] == "observed"
    assert "commit_method" not in receipt["details"]
    assert relations.publish_details_problem("publish_receipt", receipt["details"]) is None
    # A publish of the newer build again moves the channel forward (the switch is not a pin).
    assert publish.publish(project, b, share, origin=ORIGIN)["steps"]["switch"] == "done"
    assert latest(share)["latest"] == b
    assert publish.switch(project, b, share, origin=ORIGIN)["steps"]["switch"] == "already"


def test_a_switch_without_this_project_s_binding_checks_the_history_s_hash(project, tmp_path):
    """Another copy of the project (the same project_id, another computer) holds no binding of the
    version: the channel history's package_sha256 is the check, and the reference is declared."""
    copy = tmp_path / "copy"
    shutil.copytree(project.root, copy)
    share = tmp_path / "share"
    a, b = built(project), built(project)
    publish.publish(project, a, share, origin=ORIGIN)
    publish.publish(project, b, share, origin=ORIGIN)
    other = core.Project(copy)
    result = publish.switch(other, a, share, origin=ORIGIN)
    assert result["outcome"] == "published" and latest(share)["latest"] == a
    receipt = events(other, "publish_receipt")[-1]
    assert receipt["details"]["binding"] == "channel_history"
    assert receipt["refs"][0]["basis"] == "declared"
    assert receipt["refs"][0]["digest"]["sha256"] == receipt["details"]["package_sha256"]
    # A version this copy never bound and the history does not hold is no switch.
    with pytest.raises(PublicationError) as caught:
        publish.switch(other, "pub-01jzzzzzzzzzzzzzzzzzzzzzzz", share, origin=ORIGIN)
    assert caught.value.code == "not_bound"


def test_a_switch_is_refused_before_anything_is_written_when_it_has_nothing_to_go_to(project, tmp_path):
    share = tmp_path / "share"
    a, b = built(project), built(project)
    publish.publish(project, a, share, origin=ORIGIN)
    journal = project.journal.read_bytes()
    with pytest.raises(PublicationError) as caught:  # bound here, never published there
        publish.switch(project, b, share, origin=ORIGIN)
    assert caught.value.code == "not_on_target"
    with pytest.raises(PublicationError) as caught:  # a folder that is no target
        publish.switch(project, a, tmp_path / "empty", origin=ORIGIN)
    assert caught.value.code == "not_on_target" and not (tmp_path / "empty").exists()
    path = share / "projects/weekly-sales/latest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["history"][0]["package_sha256"] = "0" * 64
    path.write_bytes(target.canonical(value))
    with pytest.raises(PublicationError) as caught:  # the binding and the history disagree
        publish.switch(project, a, share, origin=ORIGIN)
    assert caught.value.code == "version_differs"
    assert project.journal.read_bytes() == journal


def test_a_switch_to_a_changed_version_stops_and_leaves_the_pointers(project, tmp_path):
    share = tmp_path / "share"
    a, b = built(project), built(project)
    publish.publish(project, a, share, origin=ORIGIN)
    publish.publish(project, b, share, origin=ORIGIN)
    (share / "publications" / a / "about.html").write_bytes(b"changed")
    result = publish.switch(project, a, share, origin=ORIGIN)
    assert result["outcome"] == "failed" and result["failure"]["code"] == "version_mismatch"
    assert result["steps"]["final_check"] == "failed" and result["steps"]["switch"] == "not_started"
    assert latest(share)["latest"] == b and points_to(share) == b
    shutil.rmtree(share / "publications" / a)
    gone = publish.switch(project, a, share, origin=ORIGIN)  # in the history, gone from the folder
    assert gone["failure"]["code"] == "not_on_target" and latest(share)["latest"] == b


def test_a_switch_is_superseded_when_latest_changes_before_it(project, tmp_path, monkeypatch):
    share = tmp_path / "share"
    a, b, c = built(project), built(project), built(project)
    for pid in (a, b, c):
        publish.publish(project, pid, share, origin=ORIGIN)
    path = share / "projects/weekly-sales/latest.json"

    def meanwhile(at):
        if at == "switch":  # another publish (no lock on this file system) moved latest to b
            value = json.loads(path.read_text(encoding="utf-8"))
            path.write_bytes(target.canonical({**value, "latest": b}))

    monkeypatch.setattr(publish, "_reached", meanwhile)
    result = publish.switch(project, a, share, origin=ORIGIN)
    assert result["ok"] and result["outcome"] == "superseded" and latest(share)["latest"] == b


def test_an_interrupted_switch_between_the_pointers_is_finished_by_the_next_run(project, tmp_path, monkeypatch):
    """ADR-0009 check 3: builds A and B, a switch back to A stopped after latest.json and before the
    page. The receipt says which pointer holds what; the next switch only draws the page, and a
    publish of B instead moves both pointers to B."""
    share = tmp_path / "share"
    a, b = built(project), built(project)
    publish.publish(project, a, share, origin=ORIGIN)
    publish.publish(project, b, share, origin=ORIGIN)
    interrupt_at(monkeypatch, "channel_page")
    stopped = publish.switch(project, a, share, origin=ORIGIN)
    assert stopped["outcome"] == "interrupted" and stopped["pointers"] == {"latest_json": "new", "channel_page": "old"}
    assert latest(share)["latest"] == a and points_to(share) == b
    monkeypatch.setattr(publish, "_reached", lambda at: None)
    again = publish.switch(project, a, share, origin=ORIGIN)
    assert again["outcome"] == "published" and again["steps"]["switch"] == "already" and points_to(share) == a
    interrupt_at(monkeypatch, "channel_page")
    publish.switch(project, b, share, origin=ORIGIN)
    monkeypatch.setattr(publish, "_reached", lambda at: None)
    assert (latest(share)["latest"], points_to(share)) == (b, a)
    forward = publish.publish(project, b, share, origin=ORIGIN)
    assert forward["steps"]["switch"] == "already" and points_to(share) == b


@pytest.mark.parametrize("step", ["final_check", "switch", "channel_page"])
def test_an_interrupted_switch_leaves_only_checked_versions_in_the_pointers(project, tmp_path, monkeypatch, step):
    share = tmp_path / "share"
    a, b = built(project), built(project)
    publish.publish(project, a, share, origin=ORIGIN)
    publish.publish(project, b, share, origin=ORIGIN)
    interrupt_at(monkeypatch, step)
    result = publish.switch(project, a, share, origin=ORIGIN)
    assert result["outcome"] == "interrupted" and "receipt" in result
    assert latest(share)["latest"] in (a, b) and points_to(share) in (a, b)
    assert relations.publish_details_problem("publish_receipt", events(project, "publish_receipt")[-1]["details"]) is None
    monkeypatch.setattr(publish, "_reached", lambda at: None)
    assert publish.switch(project, a, share, origin=ORIGIN)["outcome"] == "published"
    assert (latest(share)["latest"], points_to(share)) == (a, a)


def test_a_forced_end_of_a_switch_before_its_receipt_is_traced_with_its_channel(project, tmp_path):
    """ADR-0009 check 10 with the channel page: a switch ends after both pointers, before its receipt.
    The next attempt finds the trace in the channel the start record named (PR-2 follow-up f4)."""
    share = tmp_path / "share"
    a, b = built(project), built(project)
    publish.publish(project, a, share, origin=ORIGIN, channel="weekly")
    publish.publish(project, b, share, origin=ORIGIN, channel="weekly")
    script = (
        "import os\n"
        "from aelix_analytics import core\n"
        "from aelix_analytics.publishing import publish\n"
        "def end(at):\n"
        "    if at == 'receipt':\n"
        "        os._exit(9)\n"
        "publish._reached = end\n"
        f"publish.switch(core.Project({str(project.root)!r}), {a!r}, {str(share)!r}, origin=core.Origin('human', 'cli'),"
        " channel='weekly')\n"
    )
    done = subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                          capture_output=True, timeout=120)
    assert done.returncode == 9, done.stderr
    assert latest(share, "weekly")["latest"] == a and points_to(share, "weekly") == a
    publish.publish(project, built(project), share, origin=ORIGIN, channel="other")
    traces = events(project, "publish_receipt")[-1]["details"]["observations"]["unrecorded_attempts"]
    assert len(traces) == 1 and traces[0]["publication_id"] == a and traces[0]["latest_is_its_version"]


def test_the_command_switches_and_refuses_what_a_switch_does_not_take(project, tmp_path, capsys, monkeypatch):
    from aelix_analytics import __main__ as cli

    share = tmp_path / "share"
    a, b = built(project), built(project)
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(project.root))
    for pid in (a, b):
        assert cli.main(["publish", "--build", pid, "--to", str(share)]) == 0
    capsys.readouterr()
    assert cli.main(["publish", "--switch-to", a, "--to", str(share)]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "published" and latest(share)["latest"] == a
    assert cli.main(["publish", "--switch-to", a, "--to", str(share), "--reassign-channel"]) != 0
    assert "publish_input_invalid" in capsys.readouterr().out
    assert cli.main(["publish", "--switch-to", a]) != 0
    assert cli.main(["publish", "--switch-to", "pub-01jzzzzzzzzzzzzzzzzzzzzzzz", "--to", str(share)]) != 0
    assert "not_bound" in capsys.readouterr().out


def test_a_channel_taken_over_names_its_former_owner_in_the_receipt(project, tmp_path):
    """ADR-0009 check 7: `--reassign-channel` and the receipt's channel fields."""
    share = tmp_path / "share"
    other = core.init_project(tmp_path / "other", title="Weekly sales", origin=ORIGIN)
    theirs = built(other)
    publish.publish(other, theirs, share, origin=ORIGIN)
    result = publish.publish(project, built(project), share, origin=ORIGIN, reassign=True)
    assert result["outcome"] == "published"
    channel = events(project, "publish_receipt")[-1]["details"]["channel"]
    assert channel == {"name": "weekly-sales", "reassigned_from": core.read_json(other.marker, {})["project_id"],
                       "previous_latest": theirs}
    assert [h["publication_id"] for h in latest(share)["history"]][0] == theirs  # the history is kept


def test_after_a_switch_to_the_former_owner_s_version_a_new_build_waits_for_a_switch_back(project, tmp_path):
    """CONTRACTS D7: the build order compares one project's builds, so a channel switched to a version
    another project published (its history after a takeover) is not moved forward by a publish."""
    share = tmp_path / "share"
    other = core.init_project(tmp_path / "other", title="Weekly sales", origin=ORIGIN)
    theirs = built(other)
    publish.publish(other, theirs, share, origin=ORIGIN)
    mine = built(project)
    publish.publish(project, mine, share, origin=ORIGIN, reassign=True)
    assert publish.switch(project, theirs, share, origin=ORIGIN)["outcome"] == "published"
    newer = built(project)
    assert publish.publish(project, newer, share, origin=ORIGIN)["outcome"] == "not_newer"
    assert latest(share)["latest"] == theirs
    assert publish.switch(project, mine, share, origin=ORIGIN)["outcome"] == "published"
    assert publish.publish(project, newer, share, origin=ORIGIN)["outcome"] == "published"
    assert latest(share)["latest"] == newer


# --- saved targets (design 9.3) ----------------------------------------------------------------------

def test_a_saved_target_is_judged_only_and_used_by_name(project, tmp_path, capsys, monkeypatch):
    from aelix_analytics import __main__ as cli

    share = tmp_path / "team share"
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(project.root))
    assert target.ignores_saved_targets(project.root)  # a new project's .gitignore has the line
    assert cli.main(["publish", "--save-target", "team", "--to", str(share)]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["saved"]["name"] == "team" and saved["saved"]["is_target"] is False and saved["replaced"] is None
    assert not share.exists()  # the judgment only: nothing is made there
    stored = json.loads((project.root / target.SAVED_TARGETS).read_text(encoding="utf-8"))
    assert stored == {"schema": target.SAVED_TARGETS_SCHEMA, "targets": {"team": os.fspath(share.absolute())}}
    pid = built(project)
    assert cli.main(["publish", "--build", pid, "--target", "team"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "published" and latest(share)["latest"] == pid
    start, receipt = events(project, "publish_attempt")[-1], events(project, "publish_receipt")[-1]
    assert start["details"]["target"]["name"] == receipt["details"]["target"]["name"] == "team"
    assert start["text"] == f"Publishing {pid} to team" and receipt["text"] == f"Published {pid} to team"
    assert cli.main(["publish", "--list", "--target", "team"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["saved_targets"] == {"team": os.fspath(share.absolute())}
    assert listed["target"]["name"] == "team" and listed["target"]["is_target"]
    assert listed["target"]["channels"][0]["latest"] == pid and listed["target"]["channels"][0]["state"] == "own"
    elsewhere = tmp_path / "elsewhere"
    assert cli.main(["publish", "--save-target", "team", "--to", str(elsewhere)]) == 0
    assert json.loads(capsys.readouterr().out)["replaced"] == os.fspath(share.absolute())


def test_no_target_is_saved_where_git_would_keep_it(project, tmp_path, capsys, monkeypatch):
    from aelix_analytics import __main__ as cli

    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(project.root))
    ignore = project.root / ".gitignore"
    ignore.write_text(ignore.read_text(encoding="utf-8").replace(target.SAVED_TARGETS + "\n", ""), encoding="utf-8")
    assert cli.main(["publish", "--save-target", "team", "--to", str(tmp_path / "share")]) != 0
    assert "targets_not_ignored" in capsys.readouterr().out
    assert not (project.root / target.SAVED_TARGETS).exists()
    assert cli.main(["publish", "--build", built(project), "--target", "team"]) != 0
    assert "target_unknown" in capsys.readouterr().out
    assert cli.main(["publish", "--save-target", "../up", "--to", str(tmp_path / "share")]) != 0
    assert "publish_input_invalid" in capsys.readouterr().out
    with pytest.raises(SystemExit):  # one place to publish to
        cli.main(["publish", "--build", "x", "--to", str(tmp_path / "a"), "--target", "team"])
    capsys.readouterr()
    (project.root / target.SAVED_TARGETS).write_text("[]", encoding="utf-8")
    assert cli.main(["publish", "--list"]) == 0  # the attempts still show; the file's trouble is a field
    listed = json.loads(capsys.readouterr().out)
    assert listed["saved_targets"] is None and listed["saved_targets_error"]["code"] == "saved_targets_invalid"
    for argv in (["--switch-to", ""], ["--save-target", "", "--to", str(tmp_path / "s")], ["--build", "x", "--target", ""]):
        assert cli.main(["publish", *argv]) != 0
        assert "publish_input_invalid" in capsys.readouterr().out
    assert cli.main(["publish", "--switch-to", "not-an-id", "--to", str(tmp_path / "s")]) != 0
    assert "publish_input_invalid" in capsys.readouterr().out


def test_a_saved_target_s_folder_is_a_private_value_of_a_build(project, tmp_path):
    from aelix_analytics.publishing.snapshot import capture_snapshot

    folder = tmp_path / "private share"
    publish.save_target(project, "team", folder, origin=ORIGIN)
    assert os.fspath(folder.absolute()) in capture_snapshot(project).private_values


# --- what others may read (design 9.2) ---------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_folder_others_cannot_open_is_a_warning_and_nothing_is_changed(project, tmp_path):
    shut = tmp_path / "shut"
    shut.mkdir(mode=0o700)
    os.chmod(shut, 0o700)
    result = publish.publish(project, built(project), shut / "share", origin=ORIGIN)
    assert result["outcome"] == "published"
    assert [w["code"] for w in result["warnings"]] == ["others_cannot_read"]
    posix = events(project, "publish_receipt")[-1]["details"]["access"]["posix"]
    assert posix["others_can_read"] is False and os.fspath(shut) in posix["blocked"]
    assert posix["group_can_read"] is False and os.fspath(shut) in posix["group_blocked"]
    assert stat.S_IMODE(os.stat(shut).st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_folder_every_account_can_open_gives_no_warning(project):
    base = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        os.chmod(base, 0o755)
        if any(not os.stat(p).st_mode & stat.S_IXOTH for p in base.resolve().parents):
            pytest.skip("/tmp is not open to other accounts here")
        old = os.umask(0o022)
        try:
            result = publish.publish(project, built(project), base / "share", origin=ORIGIN)
        finally:
            os.umask(old)
        assert result["outcome"] == "published" and "warnings" not in result
        assert events(project, "publish_receipt")[-1]["details"]["access"]["posix"]["others_can_read"] is True
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_the_first_publish_raises_an_older_writer_floor_to_the_publish_records(project, tmp_path):
    """Design 6.4: a project whose floor is below the publish records' (made by an older kit) gets
    PUBLISH_WRITER_SINCE before its first publish_attempt, so an older kit writes nothing beside them."""
    pid = built(project)
    marker = core.read_json(project.marker, {})
    project.marker.write_bytes(target.canonical({**marker, "min_writer_version": "0.29.0"}))
    publish.publish(project, pid, tmp_path / "share", origin=ORIGIN)
    assert core.read_json(project.marker, {})["min_writer_version"] == relations.PUBLISH_WRITER_SINCE


def test_the_gitignore_line_counts_only_when_no_later_line_undoes_it(tmp_path):
    ignore = tmp_path / ".gitignore"
    for text, kept_out in ((".analytics/publish-targets.json\n", True), ("/.analytics/publish-targets.json\n", True),
                           (".analytics/publish-targets.json\n!.analytics/publish-targets.json\n", False),
                           ("!.analytics/publish-targets.json\n.analytics/publish-targets.json\n", True),
                           (".analytics/\n", False), ("", False)):
        ignore.write_text(text, encoding="utf-8")
        assert target.ignores_saved_targets(tmp_path) is kept_out, text


def test_a_saved_targets_file_with_a_relative_folder_is_not_the_kit_s(project):
    (project.root / target.SAVED_TARGETS).write_bytes(target.canonical(
        {"schema": target.SAVED_TARGETS_SCHEMA, "targets": {"team": "share"}}))
    with pytest.raises(PublicationError) as caught:
        target.saved_targets(project.root)
    assert caught.value.code == "saved_targets_invalid"


def test_a_saved_target_s_real_folder_is_a_private_value_too(project, tmp_path):
    from aelix_analytics.publishing.snapshot import capture_snapshot

    real = tmp_path / "real share"
    real.mkdir()
    link = tmp_path / "linked share"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symbolic links here")
    publish.save_target(project, "team", link, origin=ORIGIN)
    values = capture_snapshot(project).private_values
    assert os.fspath(link.absolute()) in values and os.path.realpath(link) in values


def test_every_load_of_the_entry_is_checked_however_many(tmp_path):
    version = tmp_path / "publications" / "pub-01jzzzzzzzzzzzzzzzzzzzzzzz"
    version.mkdir(parents=True)
    names = [f"i{n}.png" for n in range(30)]
    (version / "index.html").write_text("".join(f'<img src="{n}">' for n in names), encoding="utf-8")
    found = publish.arrival_resources(version, {"files": [{"path": n} for n in ["index.html", *names]]})
    assert len(found) == 30


def test_a_channel_this_kit_cannot_read_is_named_in_the_receipt(project, tmp_path):
    """The receipt of a publish stopped by another channel's unreadable latest.json names that file
    (as before PR-3's channel lookup)."""
    share = tmp_path / "share"
    publish.publish(project, built(project), share, origin=ORIGIN)
    odd = share / "projects/aaa"
    odd.mkdir()
    (odd / "latest.json").write_bytes(b'{"schema": "analytics.publish_channel/9"}')
    result = publish.publish(project, built(project), share, origin=ORIGIN)
    assert result["outcome"] == "failed" and result["failure"]["step"] == "channel"
    assert result["failure"]["paths"] == [os.fspath(odd / "latest.json")]


def test_a_trace_finds_its_channel_by_the_channel_s_key(project, tmp_path):
    """A start record keeps --channel as given; its trace looks in the folder that name's key finds,
    so a name that differs by case still finds it on a file system that tells case apart."""
    share = tmp_path / "share"
    a = built(project)
    publish.publish(project, a, share, origin=ORIGIN, channel="weekly")
    script = (
        "import os\n"
        "from aelix_analytics import core\n"
        "from aelix_analytics.publishing import publish\n"
        "def end(at):\n"
        "    if at == 'receipt':\n"
        "        os._exit(9)\n"
        "publish._reached = end\n"
        f"publish.publish(core.Project({str(project.root)!r}), {built(project)!r}, {str(share)!r},"
        " origin=core.Origin('human', 'cli'), channel='Weekly')\n"
    )
    done = subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                          capture_output=True, timeout=120)
    assert done.returncode == 9, done.stderr
    publish.publish(project, built(project), share, origin=ORIGIN, channel="other")
    traces = events(project, "publish_receipt")[-1]["details"]["observations"]["unrecorded_attempts"]
    assert len(traces) == 1 and traces[0]["latest_is_its_version"]
