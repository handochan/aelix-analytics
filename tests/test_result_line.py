"""The CLI's result line is one line for every reader (Issue #32 item 1, CONTRACTS D).

json.dumps(ensure_ascii=False) leaves U+2028, U+2029 and U+0085 raw, and str.splitlines() ends a
line at each of them. The extension read the kit's stdout with splitlines(), so a command whose
result held one (a run's stdout, stderr, error or title, a query's values, an imported dataset's
description) was carried out, a run recorded as it ended, but reported as kit_failed without a
result. The CLI's `record` result holds the note's text the same way; the extension records a note
in process, not through the CLI. The kit escapes the three in its result line, and the readers
split on LF. The extension's side, with the line an older kit wrote, is in tests/test_extension.py.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import KIT, kit_cli, kit_env, write_script

from aelix_analytics import __main__ as cli
from aelix_analytics import core

# The line ends json.dumps(ensure_ascii=False) leaves raw, and how the result line spells them.
ESCAPED = {"\u2028": "\\u2028", "\u2029": "\\u2029", "\x85": "\\u0085"}
# The other line ends str.splitlines() knows, which json.dumps escapes itself.
ESCAPED_BY_JSON = "\n\r\x0b\x0c\x1c\x1d\x1e"
# Each of them but LF and CR between other text; a backslash before one stays a backslash. A run
# prints it, and its stdout_tail is compared on its own.
TEXT = "줄\u2028나눔\u2029문단\x85NEL\x0bVT\x0cFF\x1cFS\x1dGS\x1eRS \\\u2028끝"
# Every line end str.splitlines() knows, and CRLF, between other text: the kit keeps an error's
# message and a note's text as they are.
LINES = "CRLF\r\nCR\rLF\n" + TEXT
TITLE = "제목\u2028둘\u2029셋\x85넷"


def test_these_are_every_line_end_str_splitlines_knows():
    ends = {chr(c) for c in range(0x110000) if len(f"a{chr(c)}b".splitlines()) > 1}
    assert ends == set(ESCAPED) | set(ESCAPED_BY_JSON)


def test_the_result_line_escapes_the_line_ends_json_leaves_raw_and_nothing_else():
    value = {"ok": True, "text": TEXT, TEXT: [TEXT, {"path": Path("a\u2028b")}],
             "kept": "한국어 \u00a0\x1f\x7f\U0001f600 \\u2028", "n": 1.5}
    line = cli._result_line(value)
    assert line.splitlines() == [line] and not set(line) & (set(ESCAPED) | set(ESCAPED_BY_JSON))
    assert json.loads(line) == json.loads(json.dumps(value, default=str))
    expected = json.dumps(value, ensure_ascii=False, default=str)
    for raw, escaped in ESCAPED.items():
        expected = expected.replace(raw, escaped)
    assert line == expected  # every other character as json.dumps wrote it


@pytest.fixture
def plain_project(tmp_path):
    """A new project and no data: the scripts here import nothing."""
    root = tmp_path / "proj"
    core.init_project(root, title="결과 줄", goal="목표", actor="test",
                      template_dir=KIT / "aelix_analytics" / "template")
    return root


def _kit(project, *args) -> subprocess.CompletedProcess:
    """The kit's stdout as bytes, as the extension reads it."""
    return subprocess.run([sys.executable, "-m", "aelix_analytics", "--project", str(project),
                           *map(str, args)], capture_output=True, env=kit_env(), timeout=300)


def _one_line(stdout: bytes) -> dict:
    """The result, which is the last line for every reader: the output holds no line end above but
    LF and a CR before one, so splitting on each line end str.splitlines() knows finds the line that
    splitting on LF alone finds."""
    for end in [*ESCAPED, *ESCAPED_BY_JSON[2:]]:
        assert end.encode("utf-8") not in stdout, (end, stdout)
    assert b"\r" not in stdout.replace(b"\r\n", b""), stdout
    text = stdout.decode("utf-8")
    assert text.endswith("\n"), text
    line = text[:-1].split("\n")[-1].removesuffix("\r")
    assert text.splitlines()[-1] == line
    return json.loads(line)


@pytest.mark.parametrize("fails", [False, True], ids=["ok", "error"])
def test_a_run_whose_result_holds_line_ends_is_read_on_one_line(plain_project, fails):
    """Through the CLI (python -m aelix_analytics, which bin/analytics and analytics pass through
    byte for byte): the run's stdout tail, its title and, when it fails, its error (LF, CR and CRLF
    too) hold the line ends; the result line is whole and reads back as the same text."""
    body = f"print({TEXT!r})\n" + (f"raise ValueError({LINES!r})\n" if fails else "")
    script = write_script(plain_project, "breaks.py", body)
    done = _kit(plain_project, "run", script, "--title", TITLE)
    assert done.returncode == (3 if fails else 0), done.stderr.decode("utf-8", "replace")
    result = _one_line(done.stdout)
    assert result["ok"] is True and result["run"]["status"] == ("error" if fails else "ok")
    assert result["run"]["title"] == TITLE and result["stdout_tail"] == TEXT + "\n"
    if fails:
        assert result["run"]["error"]["message"] == LINES
    # The tests' own reader (conftest.kit_cli) splits on LF too, after text mode's newlines.
    code, again = kit_cli(plain_project, "run", script, "--title", TITLE)
    assert code == done.returncode and again["stdout_tail"] == TEXT + "\n"


def test_a_note_s_line_ends_stay_inside_the_result_line(plain_project):
    """The commands that run no script write their result through the CLI's own stream: `record`
    holds the note's text (LF, CR and CRLF too), `status` the journal's."""
    done = _kit(plain_project, "record", "finding", LINES)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert _one_line(done.stdout)["event"]["text"] == LINES
    done = _kit(plain_project, "status")
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert any(LINES in text for text in _strings(_one_line(done.stdout))), done.stdout


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
