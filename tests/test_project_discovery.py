"""Projects below the aelix working directory and the user's settings for activating them (Issue #169):
core.find_projects_below's depth, exclusions and limits, and the settings file analytics.settings/1 beside
the trust list (core.read_settings, set_unknown_projects, stop_asking_in, and ask_again_in of #162). The
extension's questions are in tests/test_extension.py, its settings screen in tests/test_panel_extension.py."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from conftest import LINK_KINDS, PACK, dir_link
from jsonschema import Draft202012Validator, FormatChecker

from aelix_analytics import core, writer

SCHEMA = json.loads((PACK / "docs" / "schemas" / "analytics-records.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def schema_errors(record) -> list[str]:
    return [f"{list(e.absolute_path)}: {e.message}" for e in VALIDATOR.iter_errors(record)]


def marker(directory: Path, name: str = "analytics.json") -> Path:
    """A directory with a marker file: the search finds markers and does not judge them."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("{}\n", encoding="utf-8")
    return directory


def below(start: Path, **limits) -> tuple[list[str], bool]:
    found, complete = core.find_projects_below(start, **limits)
    base = Path(core.canonical(start))
    return [p.relative_to(base).as_posix() for p in found], complete


# --- the search ---

def test_the_search_finds_markers_to_depth_three_nearest_first(tmp_path):
    repo = tmp_path / "repo"
    marker(repo / "zeta")
    marker(repo / "alpha" / "beta", "lab.json")
    marker(repo / "a" / "b" / "c")
    marker(repo / "deep" / "er" / "than" / "three")
    # A migration's destination that has not committed stops the search as it stops find_project.
    (repo / "moving" / ".analytics" / "migration").mkdir(parents=True)
    (repo / "plain" / "dir").mkdir(parents=True)
    assert below(repo) == (["moving", "zeta", "alpha/beta", "a/b/c"], True)
    assert below(repo, depth=2) == (["moving", "zeta", "alpha/beta"], True)
    # The working directory itself and its parents are find_project's, not this search's.
    marker(repo)
    assert below(repo)[0] == ["moving", "zeta", "alpha/beta", "a/b/c"]


def test_the_search_does_not_enter_hidden_tool_or_found_directories(tmp_path):
    repo = tmp_path / "repo"
    for skipped in (".git", ".venv", ".hidden/inner", "node_modules/pkg", "__pycache__", "x/node_modules"):
        marker(repo / skipped)
    project = marker(repo / "proj")
    marker(project / "scripts" / "nested")  # inside a project: the project's own directory
    marker(project / "runs")
    assert below(repo) == (["proj"], True)


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_the_search_does_not_follow_links(tmp_path, kind):
    outside = marker(tmp_path / "elsewhere" / "proj").parent
    repo = tmp_path / "repo"
    repo.mkdir()
    dir_link(kind, repo / "linked", outside)
    dir_link(kind, repo / "linked-project", outside / "proj")
    assert below(repo) == ([], True)


def test_a_marker_in_another_case_counts_where_the_file_system_ignores_case(tmp_path):
    repo = tmp_path / "repo"
    marker(repo / "proj", "Analytics.JSON")
    ignores_case = (repo / "proj" / "analytics.json").exists()
    assert below(repo)[0] == (["proj"] if ignores_case else [])


def test_the_home_directory_and_a_file_system_root_are_not_searched(tmp_path, monkeypatch):
    users = tmp_path / "Users"
    home = users / "me"
    marker(home / "analyses" / "q3")
    marker(users / "other" / "proj")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert core.find_projects_below(home) == ([], False)
    assert below(home / "analyses") == (["q3"], True)
    # Started above it (/Users, C:\Users): the walk does not go into the home directory either, so
    # it reads none of its folders (Desktop, Documents, Downloads).
    home_listed = []
    scandir = os.scandir
    real_home = Path(core.canonical(home))

    def watched(path):
        if Path(path) == real_home or real_home in Path(path).parents:
            home_listed.append(path)
        return scandir(path)

    monkeypatch.setattr(core.os, "scandir", watched)
    assert below(users) == (["other/proj"], True)
    assert home_listed == []

    def listed(*args, **kwargs):
        raise AssertionError(f"a file system root was listed: {args}")

    monkeypatch.setattr(core.os, "scandir", listed)
    assert core.find_projects_below(Path(tmp_path.anchor)) == ([], False)


def test_the_search_stops_at_its_limits_and_keeps_what_it_found(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    marker(repo / "aaa")
    for i in range(30):
        (repo / f"d{i:02}" / "e").mkdir(parents=True)
    marker(repo / "zzz" / "x")
    # Each directory is listed once: the start, and then each directory the walk reaches (the
    # repository's parents may be listed too, by the spelling of its path: not counted).
    base = Path(core.canonical(repo))
    calls = []
    scandir = os.scandir

    def counted(path):
        if Path(path) == base or base in Path(path).parents:
            calls.append(Path(path))
        return scandir(path)

    monkeypatch.setattr(core.os, "scandir", counted)
    assert below(repo) == (["aaa", "zzz/x"], True)
    # the start, its 32 directories, and the 31 below those that are not projects
    assert len(calls) == len(set(calls)) == 1 + 32 + 31
    calls.clear()
    # The number of directories: what it found before the limit is kept, and it says it stopped.
    assert below(repo, max_dirs=5) == (["aaa"], False)
    assert len(calls) == 1 + 5
    # The time: a clock that has run out from the start stops it at the first entry.
    ticks = iter([0.0] + [9.0] * 100)
    assert below(repo, seconds=1.0, clock=lambda: next(ticks)) == ([], False)


class CountedListing:
    """os.scandir's context of one directory, counting the entries the walk took from it."""

    def __init__(self, inner):
        self.inner, self.read = inner, 0

    def __enter__(self):
        self.entries = self.inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self.inner.__exit__(*exc)

    def __iter__(self):
        for entry in self.entries:
            self.read += 1
            yield entry


def test_the_time_runs_out_in_the_middle_of_a_directory(tmp_path, monkeypatch):
    """A directory of very many entries (or a slow one) does not hold the walk past its time: the
    clock is looked at after each entry, not only between directories (review of PR #177)."""
    repo = tmp_path / "repo"
    marker(repo / "aaa")
    for i in range(200):
        (repo / "huge" / f"d{i:03}").mkdir(parents=True)
    marker(repo / "zzz")
    listings = {}
    scandir = os.scandir

    def counted(path):
        listing = CountedListing(scandir(path))
        listings[Path(path).name] = listing
        return listing

    monkeypatch.setattr(core.os, "scandir", counted)
    now = [0.0]

    def clock():  # 10 ms a look: the time (half a second) runs out after about 50 looks
        now[0] += 0.01
        return now[0]

    assert below(repo, seconds=0.5, clock=clock) == (["aaa"], False)
    assert 0 < listings["huge"].read < 100, listings["huge"].read
    assert "zzz" not in listings


def test_the_search_of_a_big_tree_is_short(tmp_path):
    """A tree of 6,000 directories within depth three, a project at its far end: the walk stops at
    its limit (BELOW_MAX_DIRS directories) or its time (BELOW_SECONDS), whichever is first, and
    finds the project beside the start before either."""
    repo = tmp_path / "big"
    for i in range(60):
        for j in range(100):
            (repo / f"a{i:02}" / f"b{j:03}").mkdir(parents=True)
    marker(repo / "aaa-project")
    began = time.monotonic()
    found, complete = core.find_projects_below(repo)
    took = time.monotonic() - began
    assert [p.name for p in found] == ["aaa-project"] and complete is False
    # BELOW_SECONDS and one directory's listing: a generous bound for a slow CI disk.
    assert took < core.BELOW_SECONDS + 1.0, took


def test_a_directory_that_cannot_be_listed_is_passed_over(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    marker(repo / "ok")
    (repo / "locked" / "inside").mkdir(parents=True)
    scandir = os.scandir

    def refusing(path):
        if Path(path).name == "locked":
            raise PermissionError(13, "Permission denied", str(path))
        return scandir(path)

    monkeypatch.setattr(core.os, "scandir", refusing)
    assert below(repo) == (["ok"], True)


# --- the settings ---

def test_the_settings_are_beside_the_trust_list_and_default_to_asking(tmp_path, monkeypatch):
    assert core.settings_file() == core.trust_file().parent / "analytics-settings.json"
    assert not core.settings_file().exists()
    assert core.read_settings() == {"unknown_projects": "ask", "not_asked_in": [], "ignored": None}
    assert core.SCHEMA_SETTINGS == writer.FAMILIES["settings"] == "analytics.settings/1"
    # Without AELIX_ANALYTICS_TRUST_FILE, beside analytics-projects.json in the host's agent directory.
    monkeypatch.delenv("AELIX_ANALYTICS_TRUST_FILE")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    assert core.settings_file() == tmp_path / "agent" / "analytics-settings.json"
    assert core.trust_file() == tmp_path / "agent" / "analytics-projects.json"


@pytest.mark.parametrize("mode", ["ask", "always", "never"])
def test_each_value_of_the_setting_is_written_as_analytics_settings_1(mode):
    assert core.set_unknown_projects(mode)["unknown_projects"] == mode
    stored = json.loads(core.settings_file().read_text(encoding="utf-8"))
    assert stored == {"schema": "analytics.settings/1", "unknown_projects": mode}
    assert schema_errors(stored) == []
    assert core.read_settings() == {"unknown_projects": mode, "not_asked_in": [], "ignored": None}
    assert b"\r" not in core.settings_file().read_bytes()
    if hasattr(os, "getuid"):  # this user's alone where the platform has owners
        assert core.settings_file().stat().st_mode & 0o777 == 0o600


def by_hand(text: str) -> Path:
    """A settings file a person wrote, this user's alone (a group-writable one is not used)."""
    path = core.settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_a_value_the_kit_does_not_know_is_refused_and_a_file_with_one_is_not_used():
    """The schema's enum is closed, and the reader and the writer agree with it: a file holding a
    value analytics.settings/1 does not have is not valid, so it is read as the defaults (ignored:
    invalid) and not written over (review of PR #177)."""
    with pytest.raises(core.AnalyticsError) as refused:
        core.set_unknown_projects("sometimes")
    assert refused.value.code == "setting_invalid" and not core.settings_file().exists()
    for value in ({"schema": "analytics.settings/1", "unknown_projects": "sometimes", "not_asked_in": ["/a"]},
                  {"schema": "analytics.settings/1", "unknown_projects": "always", "not_asked_in": "not a list"},
                  {"schema": "analytics.settings/1", "unknown_projects": "always", "not_asked_in": ["/a", "/a"]}):
        assert schema_errors(value)
        text = json.dumps(value)
        path = by_hand(text)
        assert core.read_settings() == {"unknown_projects": "ask", "not_asked_in": [], "ignored": "invalid"}
        with pytest.raises(core.AnalyticsError) as refused:
            core.set_unknown_projects("never")
        assert refused.value.code == "settings_unsupported" and refused.value.details["reason"] == "invalid"
        assert path.read_text(encoding="utf-8") == text


def test_folders_not_to_ask_in_are_kept_canonical_and_only_that_folder(tmp_path):
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    assert core.asks_in(repo) and core.asks_in(repo / "sub")
    stored = core.stop_asking_in(repo / "sub" / "..")
    assert stored["not_asked_in"] == [core.canonical(repo)]
    assert core.stop_asking_in(repo) == stored  # already there: left as it is
    assert not core.asks_in(repo) and core.asks_in(repo / "sub")
    value = json.loads(core.settings_file().read_text(encoding="utf-8"))
    assert value == {"schema": "analytics.settings/1", "not_asked_in": [core.canonical(repo)]}
    assert schema_errors(value) == []
    core.set_unknown_projects("never")
    assert core.read_settings() == {"unknown_projects": "never", "not_asked_in": [core.canonical(repo)],
                                    "ignored": None}


def test_a_folder_taken_off_the_list_is_asked_in_again(tmp_path):
    """core.ask_again_in, the settings screen's undo of "Don't ask in this folder" (#162): by the
    folder's canonical path or as it is stored, so a folder that is gone since can be taken off too."""
    repo, gone = tmp_path / "repo", tmp_path / "gone"
    (repo / "sub").mkdir(parents=True)
    gone.mkdir()
    assert core.ask_again_in(repo) == core.read_settings() and not core.settings_file().exists()
    core.stop_asking_in(repo)
    core.stop_asking_in(gone)
    gone.rmdir()
    stored = core.ask_again_in(repo / "sub" / "..")
    assert stored["not_asked_in"] == [core.canonical(gone)] and core.asks_in(repo)
    assert core.ask_again_in(stored["not_asked_in"][0])["not_asked_in"] == []
    value = json.loads(core.settings_file().read_text(encoding="utf-8"))
    assert value == {"schema": "analytics.settings/1", "not_asked_in": []} and schema_errors(value) == []
    by_hand("{not json")
    with pytest.raises(core.AnalyticsError) as refused:
        core.ask_again_in(repo)
    assert refused.value.code == "settings_unsupported"


def test_a_write_keeps_fields_it_does_not_know_and_leaves_any_file_it_does_not_use_alone():
    """Only a file read_settings uses is written: another version, a broken or a hand-edited file
    that is not valid is left as it is (review of PR #177)."""
    path = by_hand(json.dumps({"schema": "analytics.settings/1", "panel": {"open": True}}))
    core.set_unknown_projects("always")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": "analytics.settings/1", "panel": {"open": True}, "unknown_projects": "always"}
    for text in (json.dumps({"schema": "analytics.settings/2", "unknown_projects": "always"}), "{not json",
                 json.dumps(["analytics.settings/1"]), json.dumps({"unknown_projects": "always"})):
        by_hand(text)
        assert core.read_settings() == {"unknown_projects": "ask", "not_asked_in": [], "ignored": "invalid"}
        with pytest.raises(core.AnalyticsError) as refused:
            core.set_unknown_projects("never")
        assert refused.value.code == "settings_unsupported", text
        with pytest.raises(core.AnalyticsError):
            core.stop_asking_in(Path.cwd())
        assert path.read_text(encoding="utf-8") == text


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="file owners and modes are POSIX's; Windows is a documented limit")
def test_a_settings_file_another_account_could_have_written_is_not_used(monkeypatch):
    """Beside a trust list in a shared directory (AELIX_ANALYTICS_TRUST_FILE in /tmp) another account
    could plant `always`: a file this user does not own, or one group or others may write, is not
    used and not written over (review of PR #177)."""
    text = json.dumps({"schema": "analytics.settings/1", "unknown_projects": "always"})
    path = by_hand(text)
    assert core.read_settings()["unknown_projects"] == "always"
    path.chmod(0o620)
    assert core.read_settings() == {"unknown_projects": "ask", "not_asked_in": [], "ignored": "writable_by_others"}
    path.chmod(0o602)
    assert core.read_settings()["ignored"] == "writable_by_others"
    with pytest.raises(core.AnalyticsError) as refused:
        core.set_unknown_projects("never")
    assert refused.value.details["reason"] == "writable_by_others"
    path.chmod(0o600)
    monkeypatch.setattr(core.os, "getuid", lambda: os.stat(path).st_uid + 1)
    assert core.read_settings() == {"unknown_projects": "ask", "not_asked_in": [], "ignored": "not_owned"}
    with pytest.raises(core.AnalyticsError) as refused:
        core.set_unknown_projects("never")
    assert refused.value.details["reason"] == "not_owned"
    assert path.read_text(encoding="utf-8") == text


def test_the_schema_holds_the_settings_family():
    assert schema_errors({"schema": "analytics.settings/1"}) == []
    assert schema_errors({"schema": "analytics.settings/1", "unknown_projects": "always",
                          "not_asked_in": ["/a", "/b"]}) == []
    assert schema_errors({"schema": "analytics.settings/1", "unknown_projects": "yes"})
    assert schema_errors({"schema": "analytics.settings/1", "not_asked_in": ["/a", "/a"]})
    assert schema_errors({"schema": "analytics.settings/1", "not_asked_in": [""]})
