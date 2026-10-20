"""The repository CI and the contributing guide name the same commands and pins (Issue #22).

.github/workflows/ci.yml is read as text: the repository has no YAML library, and what drifts is a
string inside a command. What this keeps the same:

- the pytest and pytest-timeout the isolated legs install (`--with pytest==…`) and the ones uv.lock
  holds, so updating the lock cannot leave those legs on other versions, and the guide's commands;
- the aelix version: one in every leg, the same in the guide's and the README's commands;
- the aelix legs' files: every test module that skips itself without aelix (a module-level
  `pytest.importorskip("aelix_coding_agent")`) is in both aelix legs, and the guide's command runs the
  same files. A module left out runs nowhere: the development environment has no aelix, so it skips
  there too (tests/test_checkpoint_extension.py was left out until #22);
- the uv version the jobs set up and the one the guide names;
- the operating systems: every job's matrix is the three the guide's table names.
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


def commands(markdown: str) -> list[str]:
    """The shell commands of a document's ```sh blocks, a line continued with `\\` joined to the next."""
    out = []
    for block in re.findall(r"```sh\n(.*?)```", markdown, re.S):
        out += [line.strip() for line in block.replace("\\\n", " ").splitlines() if line.strip()]
    return out


def listed_tests(command: str) -> set[str]:
    return {word for word in command.split() if re.fullmatch(r"tests/test_\w+\.py", word)}


def aelix_legs(lines: list[str]) -> list[set[str]]:
    return [listed_tests(line) for line in lines if "--with aelix==" in line and "-m pytest" in line]


def needs_aelix() -> set[str]:
    out = set()
    for path in sorted((PACK / "tests").glob("test_*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            call = node.value if isinstance(node, ast.Expr) else None
            if (isinstance(call, ast.Call) and ast.unparse(call.func) == "pytest.importorskip" and call.args
                    and isinstance(call.args[0], ast.Constant) and str(call.args[0].value).startswith("aelix")):
                out.add(f"tests/{path.name}")
    return out


def test_the_isolated_legs_install_the_locked_pytest():
    for name in ("pytest", "pytest-timeout"):
        pin = rf"--with {re.escape(name)}==([^\s`]+)"
        assert set(re.findall(pin, WORKFLOW)) == {locked(name)}, name
        assert set(re.findall(pin, GUIDE)) == {locked(name)}, name


def test_one_aelix_version_in_the_ci_the_guide_and_the_readme():
    pins = set(re.findall(r"aelix==([^\s`]+)", WORKFLOW))
    assert len(pins) == 1, pins
    assert set(re.findall(r"aelix==([^\s`]+)", GUIDE)) == pins
    assert set(re.findall(r"aelix==([^\s`]+)", README)) == pins


def test_every_module_that_needs_aelix_runs_in_the_aelix_legs():
    wanted = needs_aelix()
    assert "tests/test_checkpoint_extension.py" in wanted, wanted
    legs = aelix_legs(WORKFLOW.splitlines())
    assert len(legs) == 2, legs  # CPython 3.12 and 3.11
    for files in legs:
        assert files == wanted, sorted(files ^ wanted)
        assert all((PACK / file).is_file() for file in files), files
    guide = aelix_legs(commands(GUIDE))
    assert guide and all(files == wanted for files in guide), guide


def test_the_guide_names_the_uv_the_jobs_set_up():
    versions = set(re.findall(r"astral-sh/setup-uv@\w+ # v[\d.]+\n\s+with:\n\s+version: \"([\d.]+)\"", WORKFLOW))
    assert len(versions) == 1, versions
    assert set(re.findall(r"uv `([\d.]+)`", GUIDE)) == versions


def test_every_job_runs_on_the_three_systems_the_guide_names():
    matrices = re.findall(r"^\s+os: \[(.*)\]$", WORKFLOW, re.M)
    assert len(matrices) == 2, matrices  # platform and floor
    systems = {tuple(word.strip() for word in matrix.split(",")) for matrix in matrices}
    assert systems == {("ubuntu-24.04", "windows-2025", "macos-26")}, systems
    assert "github.event.label" not in WORKFLOW and "labeled" not in WORKFLOW
    for system in ("ubuntu-24.04", "windows-2025", "macos-26"):
        assert f"`{system}`" in GUIDE, system
