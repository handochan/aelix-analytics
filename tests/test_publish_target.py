"""#21 PR-1: the publish target folder — its judgment, marker and channel rules (PUBLISH-FOLDER 2, 3, 9.1).

Nothing here publishes; the steps that copy and switch are PR-2's.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
from conftest import LINK_KINDS, dir_link
from platform_doubles import recording_platform

from aelix_analytics import core
from aelix_analytics.publishing import target
from aelix_analytics.publishing.model import PublicationError

ORIGIN = core.Origin("human", "cli")


@pytest.fixture
def project(tmp_path):
    return core.init_project(tmp_path / "project", title="Weekly sales", origin=ORIGIN)


def refused(reason, folder, project):
    with pytest.raises(PublicationError) as caught:
        target.judge(folder, project.root)
    assert caught.value.code == "target_refused" and caught.value.details["reason"] == reason
    return caught.value


def snapshot(folder):
    return sorted((p.relative_to(folder).as_posix(), p.read_bytes() if p.is_file() else None)
                  for p in folder.rglob("*")) if folder.exists() else None


# --- design 3: only reads ------------------------------------------------------------------------------

@pytest.mark.parametrize("contents", [(), (".DS_Store",), ("._notes",), (".analytics-target.lock",),
                                      ("desktop.ini",), ("Thumbs.db",)])
def test_a_missing_or_empty_folder_is_a_new_target_and_nothing_is_made(tmp_path, project, contents):
    folder = tmp_path / "공유 폴더"
    if contents:
        folder.mkdir()
        for name in contents:
            (folder / name).write_bytes(b"")
    before = snapshot(folder)
    view = target.judge(folder, project.root)
    assert view.marker is None and view.exists == bool(contents)
    assert view.resolved == folder.resolve() and view.given == folder.absolute()
    assert snapshot(folder) == before


@pytest.mark.parametrize("entry", ["index.html", ".staging", "publications"])
def test_a_folder_with_a_person_s_entries_is_refused_and_untouched(tmp_path, project, entry):
    """A user's index.html or .staging/ without the marker is theirs (ADR-0009 check 6)."""
    folder = tmp_path / "share"
    folder.mkdir()
    if entry.startswith(".") or entry == "publications":
        (folder / entry).mkdir()
    else:
        (folder / entry).write_bytes(b"<p>mine</p>")
    before = snapshot(folder)
    error = refused("not_empty", folder, project)
    assert error.details["entries"] == [entry]
    assert snapshot(folder) == before


def test_a_folder_named_like_the_lock_is_not_the_lock(tmp_path, project):
    folder = tmp_path / "share"
    (folder / ".analytics-target.lock").mkdir(parents=True)
    (folder / ".analytics-target.lock" / "mine.txt").write_bytes(b"a person's")
    refused("not_empty", folder, project)
    other = tmp_path / "other"
    other.mkdir()
    (other / ".analytics-target.lock").write_bytes(b"a person's bytes under the kit's name")
    refused("not_empty", other, project)


def test_an_attempt_that_ended_before_the_marker_leaves_the_folder_new(tmp_path, project):
    """The empty lock file and the marker's temporary file are the kit's own leftovers (design 3.4)."""
    folder = tmp_path / "share"
    folder.mkdir()
    (folder / ".analytics-target.lock").write_bytes(b"")
    (folder / f".analytics-target.json.{target.new_attempt_id()}.tmp").write_bytes(b'{"schema": "analytics.pu')
    assert target.judge(folder, project.root).marker is None


@pytest.mark.skipif(os.name == "nt", reason="a symbolic link loop needs a privilege on Windows")
def test_a_loop_of_links_is_refused_not_raised(tmp_path, project):
    (tmp_path / "a").symlink_to(tmp_path / "b")
    (tmp_path / "b").symlink_to(tmp_path / "a")
    refused("unresolvable", tmp_path / "a", project)


def test_a_target_and_the_project_may_not_be_inside_each_other(tmp_path, project):
    refused("overlaps_project", project.root / "share", project)
    refused("overlaps_project", project.root.parent, project)
    refused("overlaps_project", project.root, project)


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_a_link_to_or_from_the_project_is_seen_through(tmp_path, project, kind):
    """A target given through a link into the project, and a project given through a link inside the
    target: both the given paths and the resolved ones are compared (ADR-0009 decision 1, check 6)."""
    dir_link(kind, tmp_path / "into-project", project.root)
    refused("overlaps_project", tmp_path / "into-project" / "share", project)
    outside = tmp_path / "outside"
    outside.mkdir()
    dir_link(kind, outside / "linked-project", project.root)
    with pytest.raises(PublicationError) as caught:
        target.judge(outside, outside / "linked-project")
    assert caught.value.details["reason"] == "overlaps_project"


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="a case-insensitive default")
def test_a_project_named_in_another_case_still_overlaps(tmp_path, project):
    refused("overlaps_project", str(project.root).upper() + os.sep + "share", project)


def test_a_folder_inside_another_target_is_refused(tmp_path, project):
    outer = tmp_path / "outer"
    outer.mkdir()
    target.create_marker(outer, project.platform.files, created_at=core.iso())
    refused("inside_target", outer / "publications" / "inner", project)


@pytest.mark.parametrize("kind", LINK_KINDS)
@pytest.mark.parametrize("name", ["publications", ".staging", "projects", "analytics-target.json"])
def test_a_managed_name_that_is_a_link_is_refused(tmp_path, project, name, kind):
    folder = tmp_path / "share"
    folder.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    dir_link(kind, folder / name, elsewhere)
    refused("managed_link", folder, project)


def test_a_file_is_no_target(tmp_path, project):
    (tmp_path / "share").write_bytes(b"")
    refused("not_directory", tmp_path / "share", project)


def test_a_marker_of_an_unknown_format_is_not_written_over(tmp_path, project):
    folder = tmp_path / "share"
    folder.mkdir()
    (folder / "analytics-target.json").write_bytes(b'{"schema": "analytics.publish_target/9"}\n')
    with pytest.raises(PublicationError) as caught:
        target.judge(folder, project.root)
    assert caught.value.code == "target_unsupported"


# --- design 2: the marker ------------------------------------------------------------------------------

def test_the_marker_is_made_once_and_never_written_in_place(tmp_path, project):
    folder = tmp_path / "share"
    folder.mkdir()
    assert target.create_marker(folder, project.platform.files, created_at="2026-10-27T10:00:00+09:00") in (
        "publish_new", "rename_new")
    marker = (folder / "analytics-target.json").read_bytes()
    assert json.loads(marker) == {"schema": "analytics.publish_target/1", "kit_version": core.KIT_VERSION,
                                  "created_at": "2026-10-27T10:00:00+09:00"}
    assert marker.endswith(b"}\n") and b"\r" not in marker
    assert target.create_marker(folder, project.platform.files, created_at="later") == "exists"
    assert (folder / "analytics-target.json").read_bytes() == marker
    assert sorted(os.listdir(folder)) == ["analytics-target.json"]
    view = target.judge(folder, project.root)
    assert view.marker["schema"] == "analytics.publish_target/1"


def test_without_links_the_marker_is_renamed_and_without_either_it_is_refused(tmp_path):
    platform, _calls = recording_platform()
    folder = tmp_path / "share"
    folder.mkdir()
    platform.files.links = False
    assert target.create_marker(folder, platform.files, created_at="now") == "rename_new"
    other = tmp_path / "other"
    other.mkdir()
    platform.files.renames = False
    with pytest.raises(PublicationError) as caught:
        target.create_marker(other, platform.files, created_at="now")
    assert caught.value.code == "no_exclusive_rename"
    assert os.listdir(other) == []


# --- design 9.1: channels ------------------------------------------------------------------------------

def test_a_channel_name_is_nfc_and_portable():
    assert target.channel_name("weekly-sales") == "weekly-sales"
    assert target.channel_name("주간가") == "주간가"
    for bad in ("", ".staging", "a/b", "con", "CON", " lead", "a\\b"):
        with pytest.raises(PublicationError) as caught:
            target.channel_name(bad)
        assert caught.value.details["reason"] == "channel_name"


def test_names_that_differ_by_case_or_normalization_are_one_channel(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "Weekly").mkdir()
    (projects / "été").mkdir()
    (projects / ".DS_Store").write_bytes(b"")
    keys = target.channels(projects)
    assert keys[target.channel_key("weekly")] == ["Weekly"]
    assert keys[target.channel_key("été")] == ["été"]
    assert target.channel_key(".DS_Store") not in keys


def test_the_default_channel_is_the_slug_or_the_project_id():
    assert target.default_channel("Weekly Sales!", "prj_0123456789abcdef") == "weekly-sales"
    assert target.default_channel("주간 매출", "prj_0123456789abcdef") == "project-01234567"
    assert target.default_channel(None, "prj_fedcba9876543210") == "project-fedcba98"


def _latest(project_id):
    return target.canonical({"schema": "analytics.publish_channel/1", "channel": "weekly",
                             "project_id": project_id, "project_title": "Weekly", "latest": "pub-x",
                             "history": []})


def test_whose_a_channel_folder_is(tmp_path):
    folder = tmp_path / "projects" / "weekly"
    assert target.channel_state(folder, "prj_a").state == "absent"
    folder.mkdir(parents=True)
    (folder / ".DS_Store").write_bytes(b"")
    (folder / f".{target.new_attempt_id()}.latest.json.tmp").write_bytes(b"")
    assert target.channel_state(folder, "prj_a").state == "free"
    (folder / "latest.json").write_bytes(_latest("prj_a"))
    (folder / "index.html").write_bytes(b"<p>channel</p>")
    assert target.channel_state(folder, "prj_a").state == "own"
    assert target.channel_state(folder, "prj_b").state == "other"
    (folder / "notes.txt").write_bytes(b"a person's")
    state = target.channel_state(folder, "prj_a")
    assert state.state == "foreign" and state.foreign == ("notes.txt",)


def test_a_channel_page_without_its_latest_json_proves_no_owner(tmp_path):
    folder = tmp_path / "weekly"
    folder.mkdir()
    (folder / "index.html").write_bytes(b"<p>left behind</p>")
    assert target.channel_state(folder, "prj_a").state == "foreign"


def test_attempt_ids_are_portable_and_distinct():
    ids = {target.new_attempt_id() for _ in range(200)}
    assert len(ids) == 200 and all(target.ATTEMPT_ID.fullmatch(i) for i in ids)
    assert len({i.casefold() for i in ids}) == 200


def test_a_machine_s_identity_is_kept_only_as_a_digest():
    value = target.digest("B6633440-BFB6-5993-B367-847D4E2B8AEB")
    assert len(value) == 32 and "B663" not in value and value == target.digest("B6633440-BFB6-5993-B367-847D4E2B8AEB")
    assert target.digest(None) is None and target.digest("") is None
