"""The context in three parts (experiment: branch exp/session-substrate, not adopted): what holds for
a session, the state at its start, and what someone else recorded since (core.context_static,
context_brief, context_delta). The classic core.context_block is tested where it was."""

import sys

import pytest
from conftest import KIT

sys.path.insert(0, str(KIT))
from aelix_analytics import core  # noqa: E402

SESSION = "sessions/this.jsonl"
AGENT = core.Origin("agent", "tool", "declared", SESSION)
OTHER = core.Origin("agent", "tool", "declared", "sessions/other.jsonl")
PERSON = core.Origin("human", "cli", "inferred")
HOOK = core.Origin("system", "hook", "declared", SESSION)


@pytest.fixture
def made(tmp_path):
    root = tmp_path / "work" / "proj"
    project = core.init_project(root, title="세 부분", goal="목표",
                                template_dir=KIT / "aelix_analytics" / "template",
                                origin=core.Origin("test", "sdk"))
    return tmp_path, project


def test_the_static_part_does_not_change_with_the_records(made):
    """What the system prompt gets: the same bytes before and after records of every kind the classic
    block's text follows (a finding, a question, a CONTEXT.md edit), and no sequence number in it."""
    tmp_path, project = made
    before = core.context_static(project, base=tmp_path)
    classic_before = core.context_block(project, session=SESSION)
    core.record(project, "finding", "3월에 달라졌다", origin=AGENT)
    core.record(project, "question", "왜 달라졌나?", origin=PERSON)
    project.context.write_text(project.context.read_text(encoding="utf-8") + "\n## Added\nx\n", encoding="utf-8")
    core.sync_context(project, origin=HOOK)
    assert core.context_static(project, base=tmp_path) == before
    assert core.context_block(project, session=SESSION) != classic_before
    assert "as_of_seq" not in before and "source_revision" not in before
    assert "3월에 달라졌다" not in core.context_static(project, base=tmp_path)


def test_paths_are_relative_to_the_working_directory_when_the_project_is_below_it(made):
    tmp_path, project = made
    static = core.context_static(project, base=tmp_path)
    assert "root: work/proj (title: 세 부분) — paths here are relative to the working directory" in static
    assert "workspace: work/proj/workspace/index.html" in static
    assert str(tmp_path) not in static
    # The project itself as the working directory, and a working directory elsewhere.
    assert "root: . (title: 세 부분)" in core.context_static(project, base=project.root)
    elsewhere = core.context_static(project, base=tmp_path / "work" / "other")
    assert f"root: {project.root} (title: 세 부분)" in elsewhere and "relative to" not in elsewhere
    assert core.shown_path(project.journal, tmp_path) == "work/proj/journal.jsonl"
    assert core.shown_path(project.journal) == str(project.journal)
    assert core.shown_path(tmp_path / "work2" / "x", tmp_path / "work") == str(tmp_path / "work2" / "x")


def test_the_brief_holds_what_the_classic_block_shows_below_its_first_lines(made):
    tmp_path, project = made
    core.record(project, "finding", "3월에 달라졌다", origin=AGENT)
    core.record(project, "question", "왜 달라졌나?", origin=AGENT)
    brief = core.context_brief(project, session=SESSION, base=tmp_path)
    classic = core.context_block(project, session=SESSION)
    text = brief["text"]
    assert text.startswith("# Analytics project brief\n")
    assert f"as_of_seq {brief['as_of_seq']};" in text and brief["observed"] == 0
    for section in ("## CONTEXT.md", "## Key information (unresolved journal entries)", "## Open questions and todos"):
        assert section in text and section in classic
    assert "3월에 달라졌다" in text and "왜 달라졌나?" in text
    assert str(tmp_path) not in text
    # Everything the classic block says below its header lines is in the brief, with shown paths.
    below = classic[classic.index("## CONTEXT.md"):].replace(str(project.root), "work/proj")
    assert below in text


def test_the_delta_tells_what_others_recorded_and_not_what_this_session_did(made):
    tmp_path, project = made
    seen = core.context_brief(project, session=SESSION, base=tmp_path)["as_of_seq"]
    core.record(project, "finding", "내가 남긴 발견", origin=AGENT)
    quiet = core.context_delta(project, seen, session=SESSION, base=tmp_path)
    assert quiet["text"] == "" and quiet["as_of_seq"] > seen
    theirs = core.record(project, "finding", "다른 세션의 발견", origin=OTHER).event
    persons = core.record(project, "decision", "사람이 정한 것", origin=PERSON).event
    delta = core.context_delta(project, seen, session=SESSION, base=tmp_path)
    text = delta["text"]
    assert text.startswith("# Analytics project update\n")
    assert f"- {theirs['id']} finding (agent): 다른 세션의 발견" in text
    assert f"- {persons['id']} decision (human): 사람이 정한 것" in text
    assert "내가 남긴 발견" not in text
    # From where the last delta ended, nothing is told twice.
    assert core.context_delta(project, delta["as_of_seq"], session=SESSION, base=tmp_path)["text"] == ""


def test_a_context_version_the_hook_found_is_told_unless_the_agent_edited_the_file(made):
    tmp_path, project = made
    seen = core.context_brief(project, session=SESSION, base=tmp_path)["as_of_seq"]
    project.context.write_text(project.context.read_text(encoding="utf-8") + "\n## Added\nx\n", encoding="utf-8")
    event = core.sync_context(project, origin=HOOK)
    assert event is not None
    told = core.context_delta(project, seen, session=SESSION, base=tmp_path)["text"]
    assert "context: CONTEXT.md has a new version — read work/proj/CONTEXT.md again" in told
    assert core.context_delta(project, seen, session=SESSION, base=tmp_path, own_context_edit=True)["text"] == ""


def test_the_delta_shows_the_latest_records_and_says_how_many_it_left_out(made):
    tmp_path, project = made
    seen = core.context_brief(project, session=SESSION, base=tmp_path)["as_of_seq"]
    for n in range(5):
        core.record(project, "note", f"메모 {n}", origin=PERSON)
    text = core.context_delta(project, seen, session=SESSION, base=tmp_path, max_items=3)["text"]
    assert "메모 4" in text and "메모 2" in text and "메모 1" not in text
    assert "2 earlier record(s) not shown: analytics_status part=records since=" in text


def test_without_a_session_an_agent_s_record_is_taken_for_this_conversation_s(made):
    tmp_path, project = made
    seen = core.context_brief(project, base=tmp_path)["as_of_seq"]
    core.record(project, "note", "세션 없는 에이전트", origin=core.Origin("agent", "tool"))
    core.record(project, "note", "사람", origin=PERSON)
    core.record(project, "note", "다른 세션", origin=OTHER)
    text = core.context_delta(project, seen, session=None, base=tmp_path)["text"]
    assert "사람" in text and "다른 세션" in text and "세션 없는 에이전트" not in text
