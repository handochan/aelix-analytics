"""The repository CI and the contributing guide name the same commands and pins (Issue #22).

.github/workflows/ci.yml is read as text: the repository has no YAML library, and what drifts is a
string inside a command. Only commands count: each step's `run:` value and the lines of the guide's
```sh blocks, a `#` comment dropped, so a command commented out is a command gone. What this keeps the
same:

- the pytest and pytest-timeout the isolated legs install (`--with pytest==…`) and the ones uv.lock
  holds, so updating the lock cannot leave those legs on other versions, and the guide's commands;
- the aelix version: one in every leg, the same in the guide's and the README's commands;
- the aelix legs: every test module that skips itself without aelix (a module-level
  `pytest.importorskip("aelix_coding_agent")`) is in both legs, and the guide's command runs the same
  files. A module left out runs nowhere: the development environment has no aelix, so it skips there
  too (tests/test_checkpoint_extension.py was left out until #22). The legs are CPython 3.12 and 3.11,
  each isolated from the checkout's environment (`--isolated --no-project`) with the thread timeout;
- the uv version every setup-uv step sets up, and the one the guide names;
- the operating systems: every job's matrix is the three the guide's table names, with no `include:`
  or `exclude:`, and pull requests trigger on opened, synchronize and reopened only;
- the required checks the guide names (the main ruleset's) are the names the jobs report, every job's:
  a renamed job would leave a required check waiting forever, and a new job would not be required;
- the guide's verification commands are the CI's: each is a step's command, give or take the reporting
  options (`--output`, `--junitxml`, `--basetemp`, `-ra`) and the environment it sets.
"""
import ast
import re
import tomllib
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
WORKFLOW = (PACK / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
GUIDE = (PACK / "CONTRIBUTING.md").read_text(encoding="utf-8")
README = (PACK / "README.md").read_text(encoding="utf-8")


def locked(name: str) -> str:
    lock = tomllib.loads((PACK / "uv.lock").read_text(encoding="utf-8"))
    versions = {package["version"] for package in lock["package"] if package["name"] == name}
    assert len(versions) == 1, (name, versions)
    return versions.pop()


def uncommented(command: str) -> str:
    return "" if command.lstrip().startswith("#") else re.sub(r"\s+#.*$", "", command).strip()


def commands(markdown: str) -> list[str]:
    """The shell commands of a document's ```sh blocks, a line continued with `\\` joined to the next."""
    out = []
    for block in re.findall(r"```sh\n(.*?)```", markdown, re.S):
        out += [uncommented(line) for line in block.replace("\\\n", " ").splitlines()]
    return [command for command in out if command]


def workflow_commands() -> list[str]:
    """Every step's `run:` value: the one line, or each line of a `|` block (indented past `run:`)."""
    out, block = [], None
    for line in WORKFLOW.splitlines():
        if block is not None and (not line.strip() or len(line) - len(line.lstrip()) > block):
            out.append(line)
            continue
        block = None
        found = re.match(r"^(\s*(?:- )?)run: (.*)$", line)
        if found and found.group(2).strip() in ("|", ">"):
            block = len(found.group(1))
        elif found:
            out.append(found.group(2))
    return [command for command in map(uncommented, out) if command]


RUN = "\n".join(workflow_commands())
CODE = "\n".join(line for line in WORKFLOW.splitlines() if not line.lstrip().startswith("#"))


def listed_tests(command: str) -> set[str]:
    return {word for word in command.split() if re.fullmatch(r"tests/test_\w+\.py", word)}


def aelix_legs(lines: list[str]) -> list[str]:
    return [line for line in lines if "--with aelix==" in line and "-m pytest" in line]


ISOLATED = ("--isolated", "--no-project", "-p no:cacheprovider", "--timeout-method=thread")


def needs_aelix() -> set[str]:
    out = set()
    for path in sorted((PACK / "tests").glob("test_*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            call = node.value if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign)) else None
            if (isinstance(call, ast.Call) and ast.unparse(call.func) == "pytest.importorskip" and call.args
                    and isinstance(call.args[0], ast.Constant) and str(call.args[0].value).startswith("aelix")):
                out.add(f"tests/{path.name}")
    return out


def test_the_isolated_legs_install_the_locked_pytest():
    for name in ("pytest", "pytest-timeout"):
        pin = rf"--with {re.escape(name)}==([^\s`]+)"
        assert set(re.findall(pin, RUN)) == {locked(name)}, name
        assert set(re.findall(pin, GUIDE)) == {locked(name)}, name


def test_one_aelix_version_in_the_ci_the_guide_and_the_readme():
    pins = set(re.findall(r"aelix==([^\s`]+)", RUN))
    assert len(pins) == 1, pins
    assert set(re.findall(r"aelix==([^\s`]+)", "\n".join(commands(GUIDE)))) == pins
    assert set(re.findall(r"aelix==([^\s`]+)", "\n".join(commands(README)))) == pins


def test_every_module_that_needs_aelix_runs_in_the_aelix_legs():
    wanted = needs_aelix()
    assert "tests/test_checkpoint_extension.py" in wanted, wanted
    legs = aelix_legs(workflow_commands())
    assert sorted(re.search(r"--python (\S+)", leg).group(1) for leg in legs) == ["3.11", "3.12"], legs
    guide = aelix_legs(commands(GUIDE)) + aelix_legs(commands(README))
    assert len(guide) == 2, guide  # the guide's command and the README's
    for leg in legs + guide:
        files = listed_tests(leg)
        assert files == wanted, sorted(files ^ wanted)
        assert all((PACK / file).is_file() for file in files), files
        assert all(option in leg for option in ISOLATED), leg


def test_the_guide_names_the_uv_the_jobs_set_up():
    lines = WORKFLOW.splitlines()
    steps = [n for n, line in enumerate(lines) if "uses: astral-sh/setup-uv@" in line]
    assert len(steps) == 2, steps  # lint and platform
    versions = []
    for n in steps:  # the step's own `with:`: the lines up to the next step
        step = []
        for line in lines[n + 1:]:
            if line.lstrip().startswith("- "):
                break
            step.append(line)
        version = re.search(r"^\s+version: [\"']?([\d.]+)[\"']?\s*$", "\n".join(step), re.M)
        assert version, ("a setup-uv step without a pinned version", lines[n])
        versions.append(version.group(1))
    assert len(set(versions)) == 1, versions
    assert set(re.findall(r"uv `([\d.]+)`", GUIDE)) == set(versions)


def test_every_job_runs_on_the_three_systems_the_guide_names():
    matrices = re.findall(r"^\s+os: \[(.*)\]$", WORKFLOW, re.M)
    assert len(matrices) == 2, matrices  # platform and floor
    systems = {tuple(word.strip() for word in matrix.split(",")) for matrix in matrices}
    assert systems == {("ubuntu-24.04", "windows-2025", "macos-26")}, systems
    assert not re.search(r"^\s+(include|exclude):", CODE, re.M)
    assert re.search(r"pull_request:\n\s+types: \[(.*)\]", CODE).group(1) == "opened, synchronize, reopened"
    for system in ("ubuntu-24.04", "windows-2025", "macos-26"):
        assert f"`{system}`" in GUIDE, system


def job_names() -> set[str]:
    """The check names the jobs report: each job's `name:`, a matrix's systems put in for `matrix.os`."""
    systems = re.search(r"^\s+os: \[(.*)\]$", CODE, re.M).group(1).split(", ")
    names = set()
    for name in re.findall(r"^    name: (.+)$", CODE, re.M):
        names |= {name.replace("${{ matrix.os }}", system) for system in systems} if "matrix.os" in name else {name}
    return names


def test_the_required_checks_the_guide_names_are_names_the_jobs_report():
    rules = GUIDE[GUIDE.index("- main은 ruleset `main`"):]
    rules = rules[:rules.index("\n- ", 1)]
    named = set(re.findall(r"`((?:lint|platform|floor) · [^`]+)`", rules))
    assert named == job_names(), sorted(named ^ job_names())  # every job is a required check


REPORTING = ("--output", "--junitxml", "--basetemp")


def words(command: str) -> list[str]:
    """A command's words, the variables it sets, the reporting options and their values left out."""
    out, skip = [], False
    for word in command.split():
        if skip or re.fullmatch(r"[A-Z_]+=\S*", word):
            skip = False
            continue
        if word in REPORTING:
            skip = True
            continue
        out.append("python" if word == "python3.11" else word)
    return [word for word in out if word != "-ra"]


# The tools the CI's steps run; the guide's other commands (the evaluation fixtures, the model harness)
# are local tools of their own sections.
CI_TOOLS = ("uv sync", "pytest", "ruff check", "tools/check_docs.py", "tools/platform_smoke.py", "tools/usage_report.py")


def test_the_guides_verification_commands_are_the_cis():
    section = GUIDE[GUIDE.index("## 현재 개발 검증"):GUIDE.index("### 저장소 CI")]
    steps = [set(words(command)) for command in workflow_commands()]
    checked = 0
    for command in commands(section):
        if not any(tool in command for tool in CI_TOOLS) or "--tokenizer" in command:
            continue  # counting tokens with a downloaded tokenizer: a local measurement, not a CI step
        assert any(set(words(command)) <= step for step in steps), command
        checked += 1
    assert checked >= 9, checked
    assert 'AELIX_ANALYTICS_TEST_INSTALL: "1"' in CODE  # the guide's AELIX_ANALYTICS_TEST_INSTALL=1
