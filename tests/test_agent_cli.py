"""T22 (design RECORD-WRITER-AND-MIGRATION 11.2): who the kit's command line records when aelix runs it.

aelix 0.1.0b2 loads this extension with its own loader and builds a harness from it (on("user_bash")
included, no stand-in hook bus); no model, no network, no login, a HOME and an agent directory of the
test's own. The kit's command line is run the two ways aelix runs a shell command:

- the agent's bash tool (create_bash_tool, what aelix registers) → `agent`, declared, `cli`, and this
  session (the extension put them in the process's environment, OQ1);
- a person's `!` command (cli/repl.handle_user_bash, which emits user_bash on the harness's real hook
  bus) → `human`, declared, for a line that is one kit command; any other line — one that does not call
  the kit, or calls it beside another command (`echo x; analytics …`) — gets no result from this
  extension, and a kit call in it records `agent`, declared (the owner's decision of 2026-10-09 as
  corrected, #13 issuecomment-6072183477).

A second extension that returns its own shell for every `!` command, and one that blocks every `!`
command, are each registered before and after this one, and what ran is written down (design 4.2:
aelix keeps the last result a user_bash handler returns, of any kind). What was seen is kept as observations (AELIX_ANALYTICS_TEST_EVIDENCE_DIR,
issue-13-writer/agent-cli-*) for the measurement record. Run with aelix, as tests/test_extension.py:

    uv run --isolated --no-project --python 3.12 --with aelix==0.1.0b2 --with pytest==9.1.1
        --with pytest-timeout==2.4.0 python -m pytest -q -p no:cacheprovider --timeout 300
        --timeout-method=thread tests/test_agent_cli.py
"""

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest
from conftest import record_observation

pytest.importorskip("aelix_coding_agent")

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions  # noqa: E402
from aelix_agent_core.harness.hooks import (  # noqa: E402
    BeforeAgentStartHookEvent,
    UserBashHookEvent,
    UserBashResult,
)
from aelix_agent_core.session.jsonl_repo import (  # noqa: E402
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
)
from aelix_ai.tools import ToolExecutionContext  # noqa: E402
from aelix_coding_agent.cli.repl import handle_user_bash  # noqa: E402
from aelix_coding_agent.extensions.loader import load_extensions  # noqa: E402
from aelix_coding_agent.tools.bash import create_bash_tool  # noqa: E402

from aelix_analytics import core  # noqa: E402

PACK = Path(__file__).resolve().parents[1]
EXT = PACK / "extension" / "aelix_analytics.py"
KIT = PACK / "src"

# Another extension's shell for every `!` command (a sandbox's, a remote one's): aelix's local shell,
# which writes down that it ran.
OTHER = '''
from aelix_agent_core.harness.hooks import UserBashResult
from aelix_coding_agent.tools.bash import create_local_bash_operations

RAN = []


class OtherShell:
    def __init__(self):
        self.local = create_local_bash_operations()

    async def exec(self, command, cwd, *, on_data, signal=None, timeout=None, env=None):
        RAN.append(command)
        return await self.local.exec(command, cwd, on_data=on_data, signal=signal, timeout=timeout,
                                     env=env)


def on_user_bash(event, ctx):
    return UserBashResult(operations=OtherShell())


def setup(aelix):
    aelix.on("user_bash", on_user_bash)
'''


# Another extension that refuses every `!` command (a policy's): it answers with a result of its own,
# which aelix shows instead of running the command (cli/repl.handle_user_bash).
BLOCKER = '''
from aelix_agent_core.harness.hooks import UserBashResult


class Blocked:
    output = "blocked by policy"


def on_user_bash(event, ctx):
    return UserBashResult(result=Blocked())


def setup(aelix):
    aelix.on("user_bash", on_user_bash)
'''


def python_command(*args: str) -> str:
    """This interpreter running the kit, as a command line every shell aelix may pick reads the same
    way (sh, zsh, bash; PowerShell and cmd take an unquoted path as the program)."""
    if os.name == "nt":
        if " " in sys.executable:
            pytest.skip(f"not_run: an interpreter path with a space ({sys.executable}) needs the "
                        "Windows shell's own quoting")
        program = sys.executable
    else:
        program = shlex.quote(sys.executable)
    return " ".join([program, "-m", "aelix_analytics", *args])


def compound(kit_command: str) -> str:
    """A `!` line that runs another command, then the kit: `;` separates commands in every shell aelix
    may pick (sh, zsh, bash, PowerShell)."""
    return f"echo T22-before; {kit_command}"


def trap_line(kit_command: str) -> str:
    """A kit command, then `$'…'` with an escaped quote, a second command and a `#` comment holding a
    quote (the check of 2026-10-09)."""
    return f"{kit_command} $'Q3\\'s numbers' ; echo T22-SECOND-RAN  # it's fine"


def last_event(root: Path) -> dict:
    lines = [json.loads(line) for line in (root / "journal.jsonl").read_text(encoding="utf-8")
             .splitlines() if line.strip()]
    return lines[-1]


def author(event: dict) -> dict:
    return {key: event.get(key) for key in ("actor", "actor_basis", "means", "session", "text")}


@pytest.fixture
def aelix_session(tmp_path, monkeypatch):
    """A HOME, an aelix agent directory and a trust list of the test's own; the kit on PYTHONPATH for
    the shells' children; none of the authorship variables set; a project named by
    AELIX_ANALYTICS_PROJECT (what a person sets for aelix, A)."""
    home = tmp_path / "home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent-dir"))
    monkeypatch.setenv("AELIX_ANALYTICS_TRUST_FILE", str(tmp_path / "trust" / "analytics-projects.json"))
    monkeypatch.setenv("AELIX_ANALYTICS_UV", str(tmp_path / "no-uv" / "uv"))
    monkeypatch.setenv("PYTHONPATH", str(KIT))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    for name in ("AELIX_ANALYTICS_ACTOR", "AELIX_ANALYTICS_SESSION", "AELIX_ANALYTICS_MEANS",
                 "VIRTUAL_ENV", "PYTHONUNBUFFERED"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "proj"
    core.init_project(root, title="T22", origin=core.Origin("test", "sdk"))
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(root))
    monkeypatch.chdir(tmp_path)
    other = tmp_path / "other_extension.py"
    other.write_text(OTHER, encoding="utf-8")
    return tmp_path, root, other


async def build(tmp_path: Path, extensions: list[Path]):
    loaded = await load_extensions([str(p) for p in extensions])
    assert loaded.errors == [], loaded.errors
    session = await JsonlSessionRepo(sessions_root=str(tmp_path / "sessions")).create(
        JsonlSessionCreateOptions(cwd=str(tmp_path)))
    harness = AgentHarness(AgentHarnessOptions(extensions=loaded.extensions, runtime=loaded.runtime,
                                               session=session, cwd=str(tmp_path)))
    return loaded, harness, session


def handler_of(loaded, name: str, path: Path):
    """The `name` handler the extension loaded from `path` registered."""
    for extension in loaded.extensions:
        for handler in extension.handlers.get(name, ()):
            if Path(handler.__code__.co_filename).resolve() == path.resolve():
                return handler
    raise AssertionError(f"{path} registered no {name} handler")


def test_the_agent_s_command_line_records_the_agent_and_a_person_s_the_person(aelix_session):
    """No other extension: the agent's bash tool runs the kit as `agent` with this session; a
    person's `!` line that is one kit command gets this extension's shell and records `human`; a `!`
    line that does not call the kit, or calls it after another command, gets no result from it, and
    aelix runs it as it would — the kit call in that line records `agent`, declared, from the process
    environment."""
    tmp_path, root, _other = aelix_session

    async def scenario():
        loaded, harness, session = await build(tmp_path, [EXT])
        assert os.environ["AELIX_ANALYTICS_ACTOR"] == "agent"
        assert "AELIX_ANALYTICS_SESSION" not in os.environ
        # The first prompt of the session: the extension learns which session it is.
        await harness.hooks.emit(BeforeAgentStartHookEvent(prompt="hi", system_prompt="BASE"))
        assert session.session_file and os.environ["AELIX_ANALYTICS_SESSION"] == session.session_file
        bash = create_bash_tool(str(tmp_path))
        result = await bash.execute({"command": python_command("record", "note", "T22-agent"),
                                     "timeout": 120}, ToolExecutionContext())
        agent = author(last_event(root))
        output = await handle_user_bash(harness, python_command("record", "note", "T22-person"),
                                        exclude_from_context=True, cwd=str(tmp_path))
        person = author(last_event(root))
        other = await harness.hooks.emit(UserBashHookEvent(command="echo T22", cwd=str(tmp_path)))
        echoed = await handle_user_bash(harness, "echo T22", exclude_from_context=True,
                                        cwd=str(tmp_path))
        line = compound(python_command("record", "note", "T22-compound"))
        compound_result = await harness.hooks.emit(UserBashHookEvent(command=line, cwd=str(tmp_path)))
        compound_out = await handle_user_bash(harness, line, exclude_from_context=True, cwd=str(tmp_path))
        mixed = {"handler_result": compound_result, "output": compound_out.strip()[:40],
                 "recorded": author(last_event(root))}
        # The extension's own kit children are not the shell's: their author is the one it passes.
        before = handler_of(loaded, "before_agent_start", EXT)
        child = before.__globals__["child_env"](core.PLATFORM.processes, "human", "command")
        return result, agent, output, person, other, echoed, mixed, session.session_file, child

    result, agent, output, person, other, echoed, mixed, session_file, child = asyncio.run(scenario())
    assert agent == {"actor": "agent", "actor_basis": "declared", "means": "cli",
                     "session": session_file, "text": "T22-agent"}, (agent, result)
    assert person == {"actor": "human", "actor_basis": "declared", "means": "cli",
                      "session": session_file, "text": "T22-person"}, (person, output)
    assert other is None and echoed.strip() == "T22"
    assert mixed["handler_result"] is None and mixed["output"].startswith("T22-before"), mixed
    assert mixed["recorded"] == {"actor": "agent", "actor_basis": "declared", "means": "cli",
                                 "session": session_file, "text": "T22-compound"}, mixed
    assert (child["AELIX_ANALYTICS_ACTOR"], child.get("AELIX_ANALYTICS_SESSION")) == ("human",
                                                                                     session_file)
    record_observation("issue-13-writer/agent-cli-alone", {
        "python": sys.version.split()[0], "platform": sys.platform,
        "shell": os.environ.get("SHELL"), "agent_bash_tool": agent, "person_bang": person,
        "person_bang_not_the_kit": {"handler_result": None, "ran": "aelix's own local shell"},
        "person_bang_compound": {"line": "echo T22-before; <kit record>", "handler_result": None,
                                 "recorded": mixed["recorded"]},
        "extension_kit_child": {"actor": child["AELIX_ANALYTICS_ACTOR"],
                                "means": child.get("AELIX_ANALYTICS_MEANS")}})


@pytest.mark.parametrize("order", ["other first", "analytics first"])
def test_another_extension_s_shell_and_this_one_s_the_last_one_runs(aelix_session, order):
    """design 4.2: aelix runs the last shell a user_bash handler returns. With another extension's
    shell registered first, this extension's runs a person's kit command (it records `human`, and the
    other shell is set aside for that command); registered last, the other shell runs it and the record
    says `agent` (a limit CONTRACTS A states). A command that does not call the kit, and a line that
    calls it after another command, are the other shell's in both orders (recorded `agent`)."""
    tmp_path, root, other_path = aelix_session
    extensions = [other_path, EXT] if order == "other first" else [EXT, other_path]

    async def scenario():
        loaded, harness, session = await build(tmp_path, extensions)
        await harness.hooks.emit(BeforeAgentStartHookEvent(prompt="hi", system_prompt="BASE"))
        ran = handler_of(loaded, "user_bash", other_path).__globals__["RAN"]
        kit_command = python_command("record", "note", f"T22-{order.replace(' ', '-')}")
        returned = await harness.hooks.emit(UserBashHookEvent(command=kit_command, cwd=str(tmp_path)))
        await handle_user_bash(harness, kit_command, exclude_from_context=True, cwd=str(tmp_path))
        kit = {"returned_shell": type(returned.operations).__name__, "other_shell_ran": list(ran),
               "recorded": author(last_event(root))}
        ran.clear()
        returned = await harness.hooks.emit(UserBashHookEvent(command="echo T22", cwd=str(tmp_path)))
        echoed = await handle_user_bash(harness, "echo T22", exclude_from_context=True,
                                        cwd=str(tmp_path))
        not_kit = {"returned_shell": type(returned.operations).__name__, "other_shell_ran": list(ran),
                   "output": echoed.strip()}
        ran.clear()
        line = compound(python_command("record", "note", f"T22-{order.replace(' ', '-')}-compound"))
        returned = await harness.hooks.emit(UserBashHookEvent(command=line, cwd=str(tmp_path)))
        await handle_user_bash(harness, line, exclude_from_context=True, cwd=str(tmp_path))
        mixed = {"returned_shell": type(returned.operations).__name__,
                 "other_shell_ran": len(ran), "recorded": author(last_event(root))}
        return kit, not_kit, mixed, session.session_file

    kit, not_kit, mixed, session_file = asyncio.run(scenario())
    if order == "other first":
        assert kit["returned_shell"] == "PersonShell" and kit["other_shell_ran"] == []
        assert (kit["recorded"]["actor"], kit["recorded"]["actor_basis"]) == ("human", "declared")
    else:
        assert kit["returned_shell"] == "OtherShell" and len(kit["other_shell_ran"]) == 1
        assert (kit["recorded"]["actor"], kit["recorded"]["actor_basis"]) == ("agent", "declared")
    assert kit["recorded"]["means"] == "cli" and kit["recorded"]["session"] == session_file
    assert not_kit == {"returned_shell": "OtherShell", "other_shell_ran": ["echo T22"],
                       "output": "T22"}
    assert (mixed["returned_shell"], mixed["other_shell_ran"]) == ("OtherShell", 1), mixed
    assert (mixed["recorded"]["actor"], mixed["recorded"]["actor_basis"]) == ("agent", "declared"), mixed
    assert mixed["recorded"]["text"].endswith("-compound"), mixed
    record_observation(f"issue-13-writer/agent-cli-{order.replace(' ', '-')}", {
        "order": order, "python": sys.version.split()[0], "platform": sys.platform,
        "kit_command": kit, "not_the_kit": not_kit, "compound_line": mixed})


@pytest.mark.parametrize("order", ["blocker first", "blocker last"])
def test_an_extension_that_blocks_a_command_is_set_aside_when_it_comes_first(aelix_session, order):
    """The limit CONTRACTS A states (review of PR 3, the owner's decision of 2026-10-09 as corrected):
    aelix keeps the last UserBashResult of any kind (hooks._reducer_user_bash), not the last shell.
    With an extension that blocks every `!` command registered before this one, this extension's shell
    replaces the block for a line that is one kit command — it runs, and records `human` — while a
    line that calls the kit after another command, one that does not call it, and one whose quoting a
    shell reads otherwise (`$'…'`) stay blocked (nothing written). Registered after this one, the block
    holds for all four."""
    tmp_path, root, _other = aelix_session
    blocker = tmp_path / "blocker_extension.py"
    blocker.write_text(BLOCKER, encoding="utf-8")
    extensions = [blocker, EXT] if order == "blocker first" else [EXT, blocker]

    async def scenario():
        _loaded, harness, _session = await build(tmp_path, extensions)
        await harness.hooks.emit(BeforeAgentStartHookEvent(prompt="hi", system_prompt="BASE"))
        journal = (root / "journal.jsonl").read_bytes()
        kit_command = python_command("record", "note", f"T22-{order.replace(' ', '-')}")
        kit_out = await handle_user_bash(harness, kit_command, exclude_from_context=True, cwd=str(tmp_path))
        kit = {"output": kit_out.strip()[-40:], "written": (root / "journal.jsonl").read_bytes() != journal,
               "recorded": author(last_event(root))}
        echoed = await handle_user_bash(harness, "echo T22", exclude_from_context=True, cwd=str(tmp_path))
        journal = (root / "journal.jsonl").read_bytes()
        line = compound(python_command("record", "note", f"T22-{order.replace(' ', '-')}-compound"))
        mixed_out = await handle_user_bash(harness, line, exclude_from_context=True, cwd=str(tmp_path))
        mixed = {"output": mixed_out.strip(), "written": (root / "journal.jsonl").read_bytes() != journal}
        # The check of 2026-10-09: a line a POSIX reading took for one kit command while bash and zsh
        # read `$'…'` otherwise (the `;` is real there, the `#` comment balances the quotes).
        trap_out = await handle_user_bash(harness, trap_line(python_command("record", "note")),
                                          exclude_from_context=True, cwd=str(tmp_path))
        trap = {"output": trap_out.strip(), "written": (root / "journal.jsonl").read_bytes() != journal}
        return kit, echoed.strip(), mixed, trap

    kit, echoed, mixed, trap = asyncio.run(scenario())
    assert echoed == "blocked by policy"
    assert mixed == {"output": "blocked by policy", "written": False}, mixed
    assert trap == {"output": "blocked by policy", "written": False}, trap
    if order == "blocker first":
        assert kit["written"] and (kit["recorded"]["actor"], kit["recorded"]["actor_basis"]) == (
            "human", "declared"), kit
    else:
        assert kit["output"] == "blocked by policy" and not kit["written"], kit
    record_observation(f"issue-13-writer/agent-cli-{order.replace(' ', '-')}", {
        "order": order, "python": sys.version.split()[0], "platform": sys.platform,
        "kit_command": kit, "not_the_kit": echoed, "compound_line": mixed,
        "ansi_c_quote_line": {"line": trap_line("<kit record note>"), **trap}})


def test_the_user_bash_result_type_is_aelix_s(aelix_session):
    """The handler's answer is aelix's own UserBashResult (the reducer keeps only that type)."""
    tmp_path, _root, _other = aelix_session

    async def scenario():
        loaded, harness, _session = await build(tmp_path, [EXT])
        return await harness.hooks.emit(UserBashHookEvent(command="analytics status",
                                                          cwd=str(tmp_path)))

    assert isinstance(asyncio.run(scenario()), UserBashResult)
