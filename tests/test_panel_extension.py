"""The /analytics panel (Issue #162). Run with an interpreter that has aelix installed, as CONTRIBUTING.md
and the CI do (the aelix legs' list of files).

A bare /analytics opens the panel where aelix's interactive screen can show one (ctx.has_ui) and is the
status, as before, where it cannot. Every action of the panel is an /analytics command line run through
the command itself, and what it shows is that command's answer: these tests drive the panel with a fake
of aelix's public dialogs (select, input, editor, confirm — as aelix-memory's tests/test_panel.py does)
and compare what it ran and wrote with the text commands.
"""

import ast
import asyncio
import importlib.util
import json
import os
import shlex
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import analytics_project

pytest.importorskip("aelix_coding_agent")

PACK = Path(__file__).resolve().parents[1]
EXT = PACK / "extension" / "aelix_analytics.py"


def fresh_module():
    spec = importlib.util.spec_from_file_location(f"aelix_analytics_ext_{uuid.uuid4().hex[:8]}", EXT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def back(options):
    return "Back"


class Pages:
    """One answer for every page of a text: Next page while there is one, then Back."""

    def __call__(self, options):
        return "Next page" if "Next page" in options else "Back"


PAGES = Pages()


class PanelUI:
    """aelix's public dialogs as the panel may use them, and nothing more (no notify, no custom):
    any other attribute is an AttributeError. Each kind of dialog takes its answers in order — for
    select a label (which must be one of the options), a function of the options, or None
    (dismissed); for input and editor a string or None; for confirm a bool — and `seen` keeps every
    dialog with its title. A dialog without an answer left fails the test."""

    __slots__ = ("answers", "seen")

    def __init__(self, selects=(), inputs=(), edits=(), confirms=()):
        self.answers = {"select": list(selects), "input": list(inputs), "editor": list(edits),
                        "confirm": list(confirms)}
        self.seen = []

    def _next(self, kind, title):
        if not self.answers[kind]:
            raise AssertionError(f"{kind} without an answer: {title}")
        return self.answers[kind].pop(0)

    async def select(self, title, options, opts=None):
        self.seen.append(("select", title, list(options)))
        if self.answers["select"] and isinstance(self.answers["select"][0], Pages):
            answer = self.answers["select"][0](list(options))
            if answer == "Back":
                self.answers["select"].pop(0)
            return answer
        answer = self._next("select", title)
        answer = answer(list(options)) if callable(answer) else answer
        assert answer is None or answer in options, (answer, options)
        return answer

    async def input(self, title, placeholder=None, opts=None):
        self.seen.append(("input", title, None))
        return self._next("input", title)

    async def editor(self, title, prefill=None):
        self.seen.append(("editor", title, None))
        return self._next("editor", title)

    async def confirm(self, title, message, opts=None):
        self.seen.append(("confirm", f"{title}\n{message}", None))
        return self._next("confirm", title)

    def left(self):
        return {kind: answers for kind, answers in self.answers.items() if answers}


def with_ui(ui, session=None):
    manager = SimpleNamespace(get_session=lambda: SimpleNamespace(session_file=session))
    return SimpleNamespace(has_ui=True, ui=ui, session_manager=manager)


def without_ui(session=None):
    manager = SimpleNamespace(get_session=lambda: SimpleNamespace(session_file=session))
    return SimpleNamespace(has_ui=False, ui=None, session_manager=manager)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A new session's module and a trusted project named by AELIX_ANALYTICS_PROJECT."""
    module = fresh_module()
    root = Path(module.core.canonical(analytics_project(tmp_path / "proj", title="패널 시험")))
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(root))
    monkeypatch.chdir(tmp_path)
    return module, root


def spy(module, monkeypatch):
    """Every /analytics command line run while the panel is open, with its answer."""
    ran = []
    command = module.analytics_command

    async def watched(args, ctx=None):
        answer = await command(args, ctx)
        ran.append((args, answer))
        return answer

    monkeypatch.setattr(module, "analytics_command", watched)
    return command, ran


def shown(ui, title):
    """The text the panel showed under `title`, its pages joined."""
    pages = [t.split("\n", 1)[1] for kind, t, _ in ui.seen
             if kind == "select" and t.split("\n", 1)[0].split(" (")[0] == title]
    return "\n".join(pages)


def journal(root):
    return [json.loads(line) for line in (root / "journal.jsonl").read_bytes().splitlines()]


# --- opening it ---

def test_a_bare_command_opens_the_panel_with_a_ui_and_is_the_status_without_one(lab):
    module, root = lab
    status = asyncio.run(module.analytics_command("status"))
    assert status.startswith("(project via AELIX_ANALYTICS_PROJECT)\nproject: 패널 시험")
    # Without aelix's interactive screen (-p, json, rpc, or no context): the status, as before.
    for ctx in (None, SimpleNamespace(), without_ui()):
        assert asyncio.run(module.analytics_command("", ctx)) == status
        assert asyncio.run(module.analytics_command("   ", ctx)) == status
    ui = PanelUI(["Close"])
    assert asyncio.run(module.analytics_command("", with_ui(ui))) == module.PANEL_CLOSED
    [(_, title, options)] = ui.seen
    first, *where, counts = title.splitlines()
    assert first == "Aelix Analytics — 패널 시험" and "".join(where) == f"{root} (via AELIX_ANALYTICS_PROJECT)"
    assert counts == "0 datasets · 0 runs · 0 charts · 0 open questions and todos"
    assert options[0] == "Status" and options[-1] == "Close"
    # A text command with a UI is still the text command; a dismissed panel is closed.
    assert asyncio.run(module.analytics_command("status", with_ui(PanelUI()))) == status
    assert asyncio.run(module.analytics_command("", with_ui(PanelUI([None])))) == module.PANEL_CLOSED


def test_without_a_project_the_panel_offers_to_use_or_create_one(tmp_path, monkeypatch):
    module = fresh_module()
    monkeypatch.delenv("AELIX_ANALYTICS_PROJECT", raising=False)
    monkeypatch.chdir(tmp_path)
    ui = PanelUI(["Close"])
    asyncio.run(module.analytics_command("", with_ui(ui)))
    [(_, title, options)] = ui.seen
    assert title.startswith("Aelix Analytics — no active project\n[no_project]")
    assert options == ["Use a project…", "Create a new project…", "Settings", "Where, context and command help",
                       "Close"]


def test_long_text_is_shown_in_pages_without_terminal_controls(lab):
    module, _ = lab
    ui = PanelUI(["Next page", "Previous page", "Next page", "Back"])
    panel = module.AnalyticsPanel(ui, None)
    text = "\x1b[31mred\x1b[0m\u202eevil\n" + "\n".join(f"line {i} " + "한글" * 40 for i in range(20))
    asyncio.run(panel.show("Info", text))
    titles = [title for _, title, _ in ui.seen]
    assert len(titles) == 4 and titles[0].startswith("Info (1/") and titles[1].startswith("Info (2/")
    assert all("\x1b" not in t and "\u202e" not in t for t in titles)
    assert all(len(t.splitlines()) <= module.PANEL_LINES + 1 for t in titles)
    widths = [sum(map(module._cells, line)) for t in titles for line in t.splitlines()[1:]]
    assert max(widths) <= module.PANEL_WIDTH
    assert ui.seen[0][2] == ["Next page", "Back"] and ui.seen[1][2] == ["Next page", "Previous page", "Back"]


# --- each action is the text command ---

def test_reads_show_the_answers_of_the_text_commands(lab, monkeypatch):
    module, root = lab
    command, ran = spy(module, monkeypatch)
    ui = PanelUI(["Status", PAGES, "Where, context and command help", "Where: project, kit, uv and settings", PAGES,
                  "Context given to the model", PAGES, "Command help", PAGES, "Back", "Close"])
    asyncio.run(command("", with_ui(ui)))
    assert [line for line, _ in ran] == ["status", "where", "context", "help"]
    for (line, answer), title in zip(ran, ("Status", "Where", "Context", "Commands"), strict=True):
        assert asyncio.run(command(line)) == answer, line
        assert shown(ui, title) == "\n".join(module.page_lines(answer)), title
    assert not ui.left()


RECORDED_AS_WRITTEN = [
    "지역별 매출은 서부가 가장 크다\n둘째 줄 --refs-json []",
    # A first word in quotes followed by option-like words: options to the plain text command, the
    # editor's text as it is to the panel (review of #162).
    '"West has highest sales" --refs-json []',
    '"Q3 매출" --refs-json 에 대한 메모',
    "'quoted' --details-json {\"a\": 1} and an unmatched ' apostrophe",
]


@pytest.mark.parametrize("text", RECORDED_AS_WRITTEN)
def test_a_record_through_the_panel_is_the_person_s_record_command_with_the_text_as_written(lab, monkeypatch, text):
    module, root = lab
    command, ran = spy(module, monkeypatch)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: None)  # the Workspace rebuild after it
    ui = PanelUI(["Record a note, finding, decision …", "finding — an established result", back, "Close"],
                 edits=[f"  {text}\n"])
    asyncio.run(command("", with_ui(ui)))
    [(line, answer)] = ran
    assert line == f"record --literal finding {text}" and answer.endswith(" finding recorded"), answer
    by_panel = journal(root)[-1]
    assert (by_panel["kind"], by_panel["text"], by_panel.get("refs", [])) == ("finding", text, [])
    assert (by_panel["actor"], by_panel["actor_basis"], by_panel["means"]) == ("human", "declared", "command")
    # The same line typed is the same record.
    assert asyncio.run(command(line)).endswith(" finding recorded")
    typed = journal(root)[-1]
    same = {k for k in by_panel if k not in ("id", "seq", "ts", "recorded_at", "request_id")}
    assert {k: by_panel[k] for k in same} == {k: typed[k] for k in same}


def test_the_plain_record_command_still_reads_options_after_a_quoted_text(lab, monkeypatch):
    module, root = lab
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: None)
    said = asyncio.run(module.analytics_command('record finding "West has highest sales" --refs-json []'))
    assert said.endswith(" finding recorded"), said
    assert journal(root)[-1]["text"] == "West has highest sales"
    said = asyncio.run(module.analytics_command("record --literal note -- not an option"))
    assert said.endswith(" note recorded") and journal(root)[-1]["text"] == "-- not an option"


def test_an_empty_or_dismissed_editor_records_nothing(lab, monkeypatch):
    module, root = lab
    before = (root / "journal.jsonl").read_bytes()
    for edit in (None, "  \n"):
        asyncio.run(module.analytics_command("", with_ui(PanelUI(
            ["Record a note, finding, decision …", "note — anything worth keeping", "Close"], edits=[edit]))))
    assert (root / "journal.jsonl").read_bytes() == before


def test_what_the_panel_does_before_the_first_prompt_carries_the_session(lab, monkeypatch, tmp_path):
    """A record and a render from the panel before the session's first prompt are in that session:
    the command's context says which (review of #162)."""
    module, root = lab
    session = str(tmp_path / "sessions" / "s1.jsonl")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: None)
    children = []

    async def spawn(argv, cwd, env, timeout, processes, request=None):
        children.append((argv, env))
        return False, 0, b'{"ok": true, "workspace": {}}\n', b""

    monkeypatch.setattr(module, "_spawn", spawn)
    monkeypatch.setattr(module, "resolve_uv", lambda processes=None: "uv")
    assert module.STATE["session"] is None
    ui = PanelUI(["Record a note, finding, decision …", "note — anything worth keeping", back,
                  "Rebuild the Workspace", back, "Close"], edits=["before the first prompt"])
    asyncio.run(module.analytics_command("", with_ui(ui, session)))
    assert journal(root)[-1]["session"] == session
    [(argv, env)] = children
    assert "render" in argv and env["AELIX_ANALYTICS_SESSION"] == session
    assert (env["AELIX_ANALYTICS_ACTOR"], env["AELIX_ANALYTICS_MEANS"]) == ("human", "command")


def write_report(folder: Path, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(f"<html><body>{body}</body></html>\n", encoding="utf-8")


def test_a_report_saved_through_the_panel_is_the_reports_save_command(lab, monkeypatch):
    """Report save and list were missing for a person in aelix (#162): /analytics reports, and the
    panel's Reports, which runs it — a new Report, then a new revision of its head."""
    module, root = lab
    command, ran = spy(module, monkeypatch)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: None)
    write_report(root / "reports_out" / "q3 review", "first")
    ui = PanelUI(["Reports", "Save a new Report from a folder…", back, "Back", "Close"],
                 inputs=["q3-review", "reports_out/q3 review", "index.html", "3분기 리뷰"])
    asyncio.run(command("", with_ui(ui)))
    lines = [line for line, _ in ran]
    assert lines[0] == "reports list --limit 100 --offset 0"
    assert lines[1] == shlex.join(["reports", "save", "q3-review", "--files", "reports_out/q3 review",
                                   "--entry", "index.html", "--title", "3분기 리뷰", "--previous", "none"])
    saved = json.loads(ran[1][1])
    assert saved["outcome"] == "saved"
    first = saved["report"]["revision_id"]
    event = journal(root)[-1]
    assert (event["kind"], event["actor"], event["means"]) == ("report_revision", "human", "command")
    # The list now holds it; choosing it shows `reports show`, and its head takes a new revision.
    ran.clear()
    pick = lambda options: next(o for o in options if "q3-review @" in o)  # noqa: E731
    asyncio.run(command("", with_ui(PanelUI(["Reports", pick, "Details", back, "Back", "Back", "Close"]))))
    assert [line for line, _ in ran] == ["reports list --limit 100 --offset 0", f"reports show q3-review@{first}"] + [
        "reports list --limit 100 --offset 0"]
    assert asyncio.run(command(ran[1][0])) == ran[1][1]
    write_report(root / "reports_out" / "q3 review", "second")
    ran.clear()
    ui = PanelUI(["Reports", pick, "Save a new revision from a folder…", back, "Back", "Close"],
                 inputs=["reports_out/q3 review", "index.html", "3분기 리뷰 (고침)"])
    asyncio.run(command("", with_ui(ui)))
    assert ran[1][0].endswith(f"--previous {first}")
    second = json.loads(ran[1][1])["report"]["revision_id"]
    listed = json.loads(asyncio.run(command("reports list")))
    assert {(r["revision_id"], r["head"]) for r in listed["reports"]} == {(first, False), (second, True)}
    # A dialog left empty saves nothing.
    ran.clear()
    asyncio.run(command("", with_ui(PanelUI(["Reports", "Save a new Report from a folder…", "Back", "Close"],
                                            inputs=["other", None]))))
    assert [line for line, _ in ran] == ["reports list --limit 100 --offset 0"] * 2


def test_the_reports_command_answers_as_the_kit_s_reports_command(lab, monkeypatch):
    module, root = lab
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: None)
    command = module.analytics_command
    assert json.loads(asyncio.run(command("reports list"))) == {"reports": [], "total": 0, "next_offset": None}
    for line, code in (("reports", "report_input_invalid"), ("reports save x", "report_input_invalid"),
                       ("reports show nope", "report_input_invalid"),
                       ("reports save Bad_Id --files f --entry index.html --title t --previous none", "name_not_portable")):
        assert f"error [{code}]" in asyncio.run(command(line)), line
    write_report(root / "out", "x")
    absolute = json.loads(asyncio.run(command(
        shlex.join(["reports", "save", "abs", "--files", str(root / "out"), "--entry", "index.html", "--title", "t",
                    "--previous", "none"]))))
    assert (absolute["outcome"], absolute["report"]["report_id"]) == ("saved", "abs")
    sdk = module.core.reports(module.core.Project(root))
    assert json.loads(asyncio.run(command("reports list"))) == sdk


def test_publish_is_confirmed_and_runs_the_publish_command(lab, monkeypatch):
    module, root = lab
    kit_calls = []

    async def kit(project, args, *, timeout, actor="agent", means="tool"):
        kit_calls.append((list(args), actor, means))
        if args[:2] == ["build", "--list"]:
            return True, {"ok": True, "publications": [
                {"publication_id": "pub_1", "recording_status": "complete", "folder_status": "same",
                 "scope": "project", "profile": "read"}]}
        return True, {"ok": True, "args": list(args)}

    monkeypatch.setattr(module, "run_kit", kit)
    command, ran = spy(module, monkeypatch)
    folder = "shared out"
    to_build = ["Share: build and publish", "Builds: publish one or make a zip", "1. pub_1 — complete, folder same, "
                "project / read", "Publish to a folder…"]
    ui = PanelUI([*to_build, "Back", "Back", "Close"], inputs=[folder], confirms=[False])
    asyncio.run(command("", with_ui(ui)))
    assert [args for args, _, _ in kit_calls] == [["build", "--list"]]  # declined: nothing published
    [(kind, asked, _)] = [s for s in ui.seen if s[0] == "confirm"]
    assert asked.startswith("Publish pub_1?") and folder in asked
    ui = PanelUI([*to_build, back, "Back", "Back", "Close"], inputs=[folder], confirms=[True])
    asyncio.run(command("", with_ui(ui)))
    line = shlex.join(["publish", "--build", "pub_1", "--to", folder])
    assert ran[-1][0] == line
    assert kit_calls[-1] == (["publish", "--build", "pub_1", "--to", folder], "human", "command")
    assert asyncio.run(command(line)) == ran[-1][1]
    assert kit_calls[-1] == kit_calls[-2]
    # Preview, a private build and the attempts are the text commands too.
    ran.clear()
    ui = PanelUI(["Share: build and publish", "Preview what a build would share", back,
                  "Build a publication privately", back, "Publish attempts", back, "Back", "Close"])
    asyncio.run(command("", with_ui(ui)))
    assert [line for line, _ in ran] == ["build --preview", "build", "publish --list"]
    assert [args for args, _, _ in kit_calls[-3:]] == [["build", "--preview"], ["build"], ["publish", "--list"]]


# --- settings ---

def test_the_settings_are_changed_through_the_settings_command(lab, monkeypatch, tmp_path):
    module, root = lab
    command, ran = spy(module, monkeypatch)
    core = module.core
    path = core.settings_file()
    always = "always — use it without asking"
    ui = PanelUI(["Settings", "Projects not on the trust list: ask", always, "Back", "Close"], confirms=[False])
    asyncio.run(command("", with_ui(ui)))
    assert ran == [] and not path.exists()  # declined: nothing written
    ui = PanelUI(["Settings", "Projects not on the trust list: ask", always, back,
                  "Projects not on the trust list: always", "never — neither ask nor use it", back,
                  lambda options: next(o for o in options if o.startswith("Folders")),
                  lambda options: next(o for o in options if o.startswith("Don't ask")), back,
                  "Back", "Close"], confirms=[True])
    asyncio.run(command("", with_ui(ui)))
    cwd = os.getcwd()
    assert [line for line, _ in ran] == ["settings unknown_projects always", "settings unknown_projects never",
                                         shlex.join(["settings", "not_asked_in", "add", cwd])]
    assert core.read_settings() == {"unknown_projects": "never", "not_asked_in": [core.canonical(cwd)],
                                    "ignored": None}
    assert asyncio.run(command("settings")) == ran[-1][1]
    assert "not_asked_in: folders where aelix does not ask" in ran[-1][1] and "not asked" in ran[-1][1]
    # Asking again in that folder takes it off the list.
    ran.clear()
    ui = PanelUI(["Settings", lambda options: next(o for o in options if o.startswith("Folders")),
                  lambda options: next(o for o in options if "Ask again in" in o), back, "Back", "Close"])
    asyncio.run(command("", with_ui(ui)))
    assert ran[0][0] == shlex.join(["settings", "not_asked_in", "remove", core.canonical(cwd)])
    assert core.read_settings()["not_asked_in"] == []


def test_the_settings_command_without_a_panel(lab, tmp_path):
    module, root = lab
    command = module.analytics_command
    shown = asyncio.run(command("settings"))
    assert shown.splitlines()[0] == f"settings: {module.core.settings_file()}"
    assert "unknown_projects: ask" in shown and "not_asked_in: none" in shown
    for line in ("settings unknown_projects sometimes", "settings colour blue", "settings not_asked_in drop x"):
        answer = asyncio.run(command(line))
        assert "error [setting_invalid]" in answer, (line, answer)
    assert not module.core.settings_file().exists()
    (tmp_path / "repo").mkdir()
    assert "  " + module.core.canonical(tmp_path / "repo") in asyncio.run(command("settings not_asked_in add repo"))
    assert "not_asked_in: none" in asyncio.run(command("settings not_asked_in remove repo"))


def test_the_settings_command_does_not_ask_about_a_project_it_finds(tmp_path, monkeypatch):
    """Changing what aelix does with a project it finds is not a command that uses one."""
    module = fresh_module()
    monkeypatch.delenv("AELIX_ANALYTICS_PROJECT", raising=False)
    analytics_project(tmp_path / "repo" / "analysis", title="found")
    monkeypatch.chdir(tmp_path / "repo")
    ui = PanelUI()
    assert "unknown_projects: never" in asyncio.run(module.analytics_command("settings unknown_projects never",
                                                                             with_ui(ui)))
    assert ui.seen == [] and not module.STATE["looked"]


def test_the_asset_suggestion_mode_is_the_assets_mode_command(lab, monkeypatch, tmp_path):
    module, root = lab
    command, ran = spy(module, monkeypatch)
    session = str(tmp_path / "session.jsonl")
    ui = PanelUI(["Settings", "Asset suggestions in this project: on_demand",
                  "always_on — on its own when one is due, in this project", back,
                  "Asset suggestions in this project: always_on", "on_demand — in this aelix session only", back,
                  "Back", "Close"])
    asyncio.run(command("", with_ui(ui, session)))
    assert [line for line, _ in ran] == ["assets mode always_on --scope project", "assets mode on_demand --scope session"]
    modes = [e for e in journal(root) if e["kind"] == "asset_mode"]
    assert [(e["details"]["mode"], e["details"]["scope"], e["actor"], e["means"]) for e in modes] == [
        ("always_on", "project", "human", "command"), ("on_demand", "session", "human", "command")]
    assert modes[1]["details"]["session"] == session
    # The panel shows the session's mode while it is set.
    ui = PanelUI(["Settings", "Back", "Close"])
    asyncio.run(command("", with_ui(ui, session)))
    assert "Asset suggestions in this project: on_demand (this session)" in ui.seen[1][2]


# --- trust and assets ---

def test_using_a_project_is_confirmed_and_runs_the_use_command(tmp_path, monkeypatch):
    module = fresh_module()
    monkeypatch.delenv("AELIX_ANALYTICS_PROJECT", raising=False)
    root = Path(module.core.canonical(analytics_project(tmp_path / "cloned", title="cloned")))
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")  # nothing found below it, so nothing is asked first
    command, ran = spy(module, monkeypatch)
    ui = PanelUI(["Use a project…", "Close"], inputs=[str(root)], confirms=[False])
    asyncio.run(command("", with_ui(ui)))
    assert ran == [] and not module.core.is_trusted(root)
    [(_, asked, _)] = [s for s in ui.seen if s[0] == "confirm"]
    assert str(root) in asked and "CONTEXT.md" in asked and "trust list" in asked
    ui = PanelUI(["Use a project…", back, lambda options: options[-1]], inputs=[str(root)], confirms=[True])
    asyncio.run(command("", with_ui(ui)))
    assert ran == [(f"use {root}", f"active project: {root} (trusted: starting aelix inside it activates it)")]
    assert module.core.is_trusted(root)
    assert ui.seen[-1][1].startswith("Aelix Analytics — cloned")  # the first screen of the project now


def test_confirmations_show_no_control_or_direction_characters(lab, monkeypatch):
    """A folder typed (or a name read) reaches aelix's dialogs without ESC or a direction override
    (review of #162)."""
    module, root = lab

    async def kit(project, args, *, timeout, actor="agent", means="tool"):
        return True, {"ok": True, "publications": [
            {"publication_id": "pub_1", "recording_status": "complete", "folder_status": "same",
             "scope": "project", "profile": "read"}]}

    monkeypatch.setattr(module, "run_kit", kit)
    bad = "out\x1b[2J\u202eevil"
    ui = PanelUI(["Switch or create a project", "Use a project…", "Back",
                  "Share: build and publish", "Builds: publish one or make a zip",
                  lambda options: options[0], "Publish to a folder…", "Back", "Back", "Close"],
                 inputs=[bad, bad], confirms=[False, False])
    asyncio.run(module.analytics_command("", with_ui(ui)))
    asked = [text for kind, text, _ in ui.seen if kind == "confirm"]
    assert len(asked) == 2 and all("evil" in text for text in asked)
    assert not [t for _, t, _ in ui.seen if "\x1b" in t or "\u202e" in t]


def test_switching_projects_shows_the_new_one_and_does_not_offer_the_one_in_use(tmp_path, monkeypatch):
    module = fresh_module()
    monkeypatch.delenv("AELIX_ANALYTICS_PROJECT", raising=False)
    repo = tmp_path / "repo"
    a = Path(module.core.canonical(analytics_project(repo / "a", title="first")))
    b = Path(module.core.canonical(analytics_project(repo / "b", title="second")))
    monkeypatch.chdir(repo)
    # Long paths are cut at their start, so the folders that tell them apart stay.
    use_a, use_b = (f"1. Use {module._tail(str(root), module.PANEL_WIDTH - 12)}" for root in (a, b))
    assert use_a.endswith("repo/a") and use_b.endswith("repo/b")
    ui = PanelUI([module.NOT_NOW,  # the question about the projects found below, before the panel
                  "Use a project…", use_a, back,
                  "Switch or create a project", "Use a project…", "Back", "Back", "Close"], confirms=[True])
    asyncio.run(module.analytics_command("", with_ui(ui)))
    titles = [title for kind, title, _ in ui.seen if kind == "select"]
    # After `use`, straight back to the first screen, which shows that project.
    after = titles.index(next(t for t in titles if t.startswith("Project"))) + 1
    assert titles[after].startswith("Aelix Analytics — first")
    [offered] = [options for kind, title, options in ui.seen
                 if kind == "select" and title.startswith("Use a project")][1:]
    assert offered == [use_b, "A project in another folder…", "Back"]


def test_a_validated_asset_is_registered_only_when_confirmed(lab, monkeypatch):
    module, root = lab
    command, ran = spy(module, monkeypatch)
    item = {"asset_id": "monthly-sales", "event_id": "e9", "title": "Monthly sales", "state": "validated"}

    async def assets_list(text, ctx):
        if text == "list":
            return json.dumps({"assets": [item]})
        return json.dumps({"ran": text})

    monkeypatch.setattr(module, "human_assets", assets_list)
    pick = "1. monthly-sales — Monthly sales [validated]"
    ui = PanelUI(["Analysis assets", pick, "Register this version…", "Back", "Back", "Close"],
                 inputs=["monthly_sales"], confirms=[False])
    asyncio.run(command("", with_ui(ui)))
    assert [line for line, _ in ran] == ["assets list"] * 2  # declined: nothing registered
    ran.clear()
    ui = PanelUI(["Analysis assets", pick, "Details", back, "Register this version…", back,
                  "Review the candidates now", back, "Back", "Close"], inputs=["monthly_sales"], confirms=[True])
    asyncio.run(command("", with_ui(ui)))
    assert [line for line, _ in ran] == ["assets list", "assets show monthly-sales",
                                         "assets register e9 --name monthly_sales", "assets list",
                                         "assets review", "assets list"]


# --- the boundary ---

def test_the_panel_uses_only_aelix_s_public_dialogs_and_imports_nothing_new():
    """PanelUI has only select, input, editor and confirm, so every flow above shows the panel needs no
    more. The extension's imports from aelix are the ones it had before the panel (the public
    extension API and the shell and skill loaders of #13 and the asset work), and the panel's code
    imports nothing."""
    tree = ast.parse(EXT.read_text(encoding="utf-8"))
    aelix = ("aelix", "aelix_agent_core", "aelix_ai", "aelix_coding_agent")
    host = set()
    for node in ast.walk(tree):
        names = ([node.module] if isinstance(node, ast.ImportFrom) and node.module
                 else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
        host |= {name for name in names if name.split(".")[0] in aelix}
    assert host == {"aelix_agent_core.harness.hooks", "aelix_agent_core.types", "aelix_ai.messages", "aelix_ai.tools",
                    "aelix_coding_agent.tools.bash", "aelix_coding_agent.util.shell_env",
                    "aelix_agent_core.harness.skills", "aelix_ai.utils._shell"}
    panel = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AnalyticsPanel")
    assert not [node for node in ast.walk(panel) if isinstance(node, (ast.Import, ast.ImportFrom))]
    called = {node.func.attr for node in ast.walk(panel) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Attribute)
              and node.func.value.attr == "ui"}
    assert called == {"select", "input", "editor", "confirm"}


def test_no_model_tool_reaches_the_settings():
    """The settings are the person's: /analytics settings and the panel, never a tool the model calls."""
    module = fresh_module()
    tree = ast.parse(EXT.read_text(encoding="utf-8"))
    tools = {t.name: t for t in module.TOOLS}
    handlers = {node.name: node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)}
    for name in ("tool_status", "tool_query", "tool_import", "tool_run", "tool_record", "tool_init"):
        names = {n.attr if isinstance(n, ast.Attribute) else n.id for n in ast.walk(handlers[name])
                 if isinstance(n, (ast.Attribute, ast.Name))}
        assert not names & {"set_unknown_projects", "stop_asking_in", "ask_again_in", "human_settings",
                            "analytics_command", "open_panel"}, name
    assert len(tools) == 6


def test_the_model_is_told_the_kit_s_command_line_is_its_and_what_is_the_user_s():
    run = {t.name: t for t in fresh_module().TOOLS}["analytics_run"]
    [line] = [g for g in run.prompt_guidelines if "analytics COMMAND" in g]
    assert len(line) <= 200 and "<" not in line
    for word in ("bash", "bin/analytics", "--help", "publish", "trust", "asset mode", "migrate", "/analytics"):
        assert word in line, word


def test_aelix_s_own_command_context_opens_the_panel_once_a_ui_is_bound(lab, tmp_path):
    """aelix 0.1.0b2's loader registers the command, and its harness builds the command's context
    (make_command_context, as its command dispatch does): headless, has_ui is False and the bare command
    is the status; with a UI bound the panel opens on that UI."""
    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.session.jsonl_repo import JsonlSessionCreateOptions, JsonlSessionRepo
    from aelix_coding_agent.extensions.loader import load_extensions

    module, root = lab

    async def bare_command(ui):
        loaded = await load_extensions([str(EXT)])
        assert loaded.errors == []
        (ext,) = loaded.extensions
        assert set(ext.commands) == {"analytics"}
        session = await JsonlSessionRepo(sessions_root=str(tmp_path / "sessions")).create(
            JsonlSessionCreateOptions(cwd=str(tmp_path)))
        harness = AgentHarness(AgentHarnessOptions(extensions=loaded.extensions, runtime=loaded.runtime,
                                                   session=session, cwd=str(tmp_path)))
        if ui is not None:
            loaded.runtime.bind_ui(ui)
        ctx = harness.make_command_context()
        return await ext.commands["analytics"].handler("", ctx), await ext.commands["analytics"].handler("status", ctx)

    headless, status = asyncio.run(bare_command(None))
    assert headless == status and headless.startswith("(project via AELIX_ANALYTICS_PROJECT)")
    ui = PanelUI(["Close"])
    opened, status = asyncio.run(bare_command(ui))
    assert opened == module.PANEL_CLOSED and status.startswith("(project via AELIX_ANALYTICS_PROJECT)")
    assert ui.seen[0][1].startswith("Aelix Analytics — 패널 시험")
