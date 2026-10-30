"""The distribution: the wheel and sdist `uv build` makes from this checkout, installed where no
checkout is (ADR-0004).

The default tests build both artifacts and install the wheel, without its non-existent dependencies,
into fresh virtual environments on the entry-point floor and on 3.12. They need uv, and the network
or uv's cache for the pinned build backend.

AELIX_ANALYTICS_TEST_INSTALL=1 adds the end-to-end leg: aelix 0.1.0b2 from PyPI beside the wheel,
aelix's own verify, status and loader, README's `uv tool install` lines, and a project driven through
the installed `analytics` by real uv — which syncs an analysis environment, so it needs the network or
a warm cache. The repository CI
(.github/workflows/ci.yml) runs it on Linux, Windows and macOS. That leg also installs, upgrades and
removes the distribution the ways a release offers (Issue #24): from a package index on loopback that
stands in for PyPI, through aelix's own extension commands, and over the wheel of an earlier kit that
the checkout's history builds byte for byte.

AELIX_ANALYTICS_TEST_DIST=DIR tests the wheel and sdist in DIR instead of building them: the release
workflow (.github/workflows/release.yml) hands the files it will publish, and one test checks they are
this checkout's build.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import textwrap
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest
from conftest import OLD_ENV, checkout_eol, record_observation, tracked_kit_sha

from aelix_analytics import cli, core

PACK = Path(__file__).resolve().parents[1]
UV = shutil.which("uv")
POSIX = os.name != "nt"
FLOOR, CURRENT = "3.11", "3.12"
AELIX = "aelix==0.1.0b2"
ANALYSIS = ("duckdb", "polars", "pyarrow", "numpy", "pandas", "matplotlib", "plotly", "pygments",
            "markdown_it")
E2E = os.environ.get("AELIX_ANALYTICS_TEST_INSTALL") == "1"
GIVEN = os.environ.get("AELIX_ANALYTICS_TEST_DIST")

pytestmark = pytest.mark.skipif(UV is None and not E2E, reason="uv builds and installs the distribution")
e2e = pytest.mark.skipif(not E2E, reason="set AELIX_ANALYTICS_TEST_INSTALL=1 (network: PyPI, "
                                         "an analysis environment)")


def run(argv, **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("timeout", 900)
    done = subprocess.run([str(a) for a in argv], capture_output=True, encoding="utf-8",
                          errors="replace", **kwargs)
    return done


def ok(argv, **kwargs) -> subprocess.CompletedProcess:
    done = run(argv, **kwargs)
    assert done.returncode == 0, f"{argv}\nstdout={done.stdout}\nstderr={done.stderr}"
    return done


def tracked(*roots: str) -> list[str]:
    """The checkout's tracked files under `roots`, as a build from a clean checkout sees them."""
    done = run(["git", "-C", PACK, "ls-files", "--", *roots])
    if done.returncode != 0:
        pytest.skip("not a git checkout: the expected member list comes from git")
    return sorted(done.stdout.splitlines())


def pyproject() -> dict:
    return tomllib.loads((PACK / "pyproject.toml").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def dist(tmp_path_factory) -> dict:
    assert UV is not None, "AELIX_ANALYTICS_TEST_INSTALL=1 needs uv on PATH"
    if GIVEN:
        out = Path(GIVEN)
    else:
        out = tmp_path_factory.mktemp("dist")
        ok([UV, "build", "--quiet", "-o", out, PACK], timeout=600)
    [wheel] = out.glob("*.whl")
    [sdist] = out.glob("*.tar.gz")
    return {"wheel": wheel, "sdist": sdist, "dir": out}


def venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def venv_python(venv: Path) -> Path:
    return venv_bin(venv) / ("python.exe" if os.name == "nt" else "python")


def installed(tmp_path: Path, wheel: Path, python: str, *extra: str) -> Path:
    venv = tmp_path / f"venv-{python}"
    ok([UV, "venv", "--quiet", "--python", python, venv])
    ok([UV, "pip", "install", "--quiet", "--python", venv_python(venv), *extra, wheel])
    return venv


def outside_env(tmp_path: Path, **extra) -> dict:
    """An environment with no trace of this checkout and nothing of the caller's aelix."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VIRTUAL_ENV", "AELIX_ANALYTICS_PROJECT", "AELIX_ANALYTICS_ACTOR",
                        "AELIX_ANALYTICS_RUNTIME_PATH", "AELIX_ANALYTICS_UV", *OLD_ENV)}
    env.update({"AELIX_ANALYTICS_TRUST_FILE": str(tmp_path / "trust.json"),
                "PYTHONDONTWRITEBYTECODE": "1", **extra})
    return env


# --- the artifacts --------------------------------------------------------------------------------


def expected_wheel_members() -> set[str]:
    """The package, its copy as the runtime root under kit/, and the extension module (Issue #9)."""
    package = [p.removeprefix("src/aelix_analytics/") for p in tracked("src/aelix_analytics")]
    members = {f"aelix_analytics/{p}" for p in package}
    members |= {f"aelix_analytics/kit/aelix_analytics/{p}" for p in package}
    members.add("aelix_analytics/extension.py")
    return members


def test_the_wheel_holds_the_package_and_nothing_else(dist):
    with zipfile.ZipFile(dist["wheel"]) as wheel:
        names = [n for n in wheel.namelist() if not n.startswith("aelix_analytics-")]
    assert len(names) == len(set(names))
    assert set(names) == expected_wheel_members(), (
        "a build ships the checkout's untracked files under src/aelix_analytics: "
        "build from a clean checkout")
    for name in ("kit/aelix_analytics/template/CONTEXT.md", "kit/aelix_analytics/template/gitignore",
                 "kit/aelix_analytics/template/gitattributes",
                 "kit/aelix_analytics/template/pyproject.toml", "kit/aelix_analytics/assets/workspace.css",
                 "kit/aelix_analytics/assets/workspace.js", "kit/aelix_analytics/assets/workspace-state.js",
                 "kit/aelix_analytics/demo_scripts/01_monthly_revenue.py",
                 "kit/aelix_analytics/__main__.py", "kit/aelix_analytics/core.py", "__main__.py",
                 "aelix-plugin.toml", "extension.py", "cli.py", "_install.py"):
        assert f"aelix_analytics/{name}" in names
    # The runtime root's copy is the package less the extension module (which is the checkout's
    # extension/aelix_analytics.py): a child never sees the aelix adapter.
    assert "aelix_analytics/kit/aelix_analytics/extension.py" not in names


def wheel_source(member: str) -> str | None:
    """The checkout file a package member of the wheel is built from; None for dist-info."""
    if member == "aelix_analytics/extension.py":
        return "extension/aelix_analytics.py"
    for prefix in ("aelix_analytics/kit/aelix_analytics/", "aelix_analytics/"):
        if member.startswith(prefix):
            return "src/aelix_analytics/" + member.removeprefix(prefix)
    return None


def test_the_wheel_members_have_no_crlf(dist):
    """ADR-0007 decision 1 and 7 ③, Issue #42's second criterion: the wheel this leg's checkout
    builds (on Windows, a checkout made with core.autocrlf=true) holds its text members with LF —
    every package member git sees as text, each the checkout file's bytes, and the dist-info. A
    binary member (git's `i/-text`; none today) is not read for line ends."""
    eol = checkout_eol()
    listed, crlf = [], []
    with zipfile.ZipFile(dist["wheel"]) as wheel:
        for member in sorted(wheel.namelist()):
            data = wheel.read(member)
            listed.append(f"{member} {hashlib.sha256(data).hexdigest()}")
            source = wheel_source(member)
            if source is None:
                assert ".dist-info/" in member, member
            else:
                assert data == (PACK / source).read_bytes(), member
                if eol[source][0] == "i/-text":
                    continue
            if b"\r\n" in data:
                crlf.append(member)
    record_observation("wheel-members", {
        "members": len(listed), "crlf": crlf,
        "list_sha256": hashlib.sha256("\n".join(listed).encode()).hexdigest(),
        "checkout_kit_sha": tracked_kit_sha()})
    assert not crlf, f"wheel members with CRLF: {crlf}"


def test_the_extension_module_is_the_checkouts_adapter(dist):
    with zipfile.ZipFile(dist["wheel"]) as wheel:
        shipped = wheel.read("aelix_analytics/extension.py")
    assert shipped == (PACK / "extension" / "aelix_analytics.py").read_bytes()


def wheel_metadata(wheel_path: Path) -> tuple[object, str]:
    with zipfile.ZipFile(wheel_path) as wheel:
        info = next(n for n in wheel.namelist() if n.endswith(".dist-info/METADATA"))
        points = next(n for n in wheel.namelist() if n.endswith(".dist-info/entry_points.txt"))
        return Parser().parsestr(wheel.read(info).decode("utf-8")), wheel.read(points).decode()


def test_the_wheel_metadata(dist):
    meta, points = wheel_metadata(dist["wheel"])
    assert meta["Name"] == "aelix-analytics"
    assert meta["Version"] == pyproject()["project"]["version"]
    assert meta["Requires-Python"] == ">=3.11"
    assert meta.get_all("Requires-Dist") is None, "the entry point installs no analysis library"
    assert "[console_scripts]\nanalytics = aelix_analytics.cli:main" in points
    scripts = points.split("[console_scripts]\n", 1)[1].split("\n[", 1)[0].split()
    assert scripts == ["analytics", "=", "aelix_analytics.cli:main"], "one command, no `lab` beside it"
    assert meta["License-Expression"] == "Apache-2.0"
    assert meta.get_all("License-File") == ["LICENSE"]
    # The GitHub handle and no email (owner's decision of 2026-10-07, Issue #24).
    assert meta.get_all("Author") == meta.get_all("Maintainer") == ["handochan"]
    assert meta.get_all("Author-email") is None and meta.get_all("Maintainer-email") is None
    with zipfile.ZipFile(dist["wheel"]) as wheel:
        version = pyproject()["project"]["version"]
        shipped = wheel.read(f"aelix_analytics-{version}.dist-info/licenses/LICENSE")
    assert shipped == (PACK / "LICENSE").read_bytes()
    assert "[aelix.extensions]\naelix-analytics = aelix_analytics.extension:setup" in points
    # What a package index shows (Issue #24): a beta, and where its source, issues, changelog and
    # security policy are.
    assert "Development Status :: 4 - Beta" in meta.get_all("Classifier")
    repository = "https://github.com/handochan/aelix-analytics"
    assert meta.get_all("Project-URL") == [
        f"Source, {repository}", f"Issues, {repository}/issues",
        f"Changelog, {repository}/blob/main/CHANGELOG.md", f"Security, {repository}/blob/main/SECURITY.md"]


def test_the_index_page_links_this_version_s_documents(dist):
    """A package index shows the description, README, where a link relative to the checkout leads
    nowhere (Issue #24). The build points each one at this version's tag on GitHub and changes nothing
    else: putting the relative links back gives README."""
    meta, _ = wheel_metadata(dist["wheel"])
    assert meta["Description-Content-Type"] == "text/markdown"
    description = meta.get_payload()
    links = re.findall(r"\]\(([^)\s]+)\)", description)
    assert links and not [link for link in links if not re.match(r"https?://", link)]
    tag = f"https://github.com/handochan/aelix-analytics/blob/v{pyproject()['project']['version']}/"
    readme = (PACK / "README.md").read_text(encoding="utf-8")
    assert f"]({tag}README.md#" not in readme and f"]({tag}" not in readme
    assert description.replace(f"]({tag}README.md#", "](#").replace(f"]({tag}", "](") == readme


def test_the_current_contract_names_the_kit_s_version():
    """The current contract's distribution row states the version (CONTRACTS E): kept equal to
    KIT_VERSION and pyproject.toml, which a version bump changes together (review of PR 3: one bump
    missed the sentence)."""
    text = (PACK / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    assert f"버전은 {core.KIT_VERSION}입니다(`KIT_VERSION`, manifest와 같음)" in text
    assert pyproject()["project"]["version"] == core.KIT_VERSION


def test_new_projects_keep_the_security_floor_the_dev_environment_tests():
    """A new project gets Pillow 13 or newer (Issue #131: up to 12.3.0 it has GHSA-8g5g-8vmr-9782 and
    eight more advisories), and the dev environment tests the kit with the template's libraries: every
    requirement of the template is a dev requirement, written the same."""
    template = tomllib.loads((PACK / "src" / "aelix_analytics" / "template" / "pyproject.toml")
                             .read_text(encoding="utf-8"))["project"]["dependencies"]
    assert "pillow>=13" in template
    assert set(template) <= set(pyproject()["dependency-groups"]["dev"])


def test_the_manifest_agrees_with_the_distribution(dist):
    with zipfile.ZipFile(dist["wheel"]) as wheel:
        manifest = tomllib.loads(wheel.read("aelix_analytics/aelix-plugin.toml").decode("utf-8"))
    plugin = manifest["plugin"]
    assert plugin["id"] == "aelix-analytics"
    assert plugin["version"] == pyproject()["project"]["version"] == core.KIT_VERSION
    assert plugin["entry"]["python"] == "aelix_analytics.extension:setup"
    assert plugin["api"] == {"level": 1, "min_level": 1}
    assert manifest["activation"] == {"on_startup_finished": True}
    assert plugin["license"] == pyproject()["project"]["license"] == "Apache-2.0"


def test_the_readme_names_the_wheel_this_version_builds():
    """README's build comment and install lines name the wheel `uv build` makes from this checkout:
    the version is pyproject's. The 0.1.0 lines once outlived the bump to 0.2.0 (Issue #9 PR-1
    review)."""
    readme = (PACK / "README.md").read_text(encoding="utf-8")
    versions = re.findall(r"aelix_analytics-([^-\s]+)-py3-none-any\.whl", readme)
    assert versions, "README names no wheel"
    assert set(versions) == {pyproject()["project"]["version"]}, versions


def archive_content(path: Path) -> list[list[str]]:
    """A wheel's or sdist's members in their order, each with the sha256 of its bytes: what the build
    made, without the archive's own headers. Python's zipfile writes into each entry's header the
    system that made it (0 on Windows, 3 elsewhere), so a wheel built on Windows is other bytes than
    the same build on Linux or macOS while every member is the same."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return [[i.filename, hashlib.sha256(archive.read(i)).hexdigest()] for i in archive.infolist()]
    with tarfile.open(path) as archive:
        return [[m.name, hashlib.sha256(archive.extractfile(m).read()).hexdigest()]
                for m in archive.getmembers() if m.isfile()]


def content_digest(path: Path) -> str:
    return hashlib.sha256(json.dumps(archive_content(path)).encode("utf-8")).hexdigest()


@pytest.mark.skipif(not GIVEN, reason="AELIX_ANALYTICS_TEST_DIST is not set: the tests build the files")
def test_the_given_files_are_this_checkout_s_build(dist, tmp_path):
    """The release publishes the files its build job made on Linux (the release workflow); a build of
    this checkout makes the same members in the same order with the same bytes, so what the tests
    installed is the tagged commit's distribution. On Linux and macOS the files are the same bytes;
    a Windows build differs only in the zip headers' system field (archive_content)."""
    out = tmp_path / "again"
    ok([UV, "build", "--quiet", "-o", out, PACK], timeout=600)
    for kind, pattern in (("wheel", "*.whl"), ("sdist", "*.tar.gz")):
        [again] = out.glob(pattern)
        assert again.name == dist[kind].name
        assert archive_content(again) == archive_content(dist[kind]), kind
        if POSIX:
            assert again.read_bytes() == dist[kind].read_bytes(), kind


def test_the_sdist_builds_the_same_wheel(dist, tmp_path):
    with tarfile.open(dist["sdist"]) as sdist:
        names = {n.split("/", 1)[1] for n in sdist.getnames() if "/" in n}
    sources = set(tracked("src/aelix_analytics")) | {
        "extension/aelix_analytics.py", "README.md", "pyproject.toml", "LICENSE"}
    assert sources <= names
    assert names - sources <= {"PKG-INFO", ".gitignore"}
    out = tmp_path / "from-sdist"
    ok([UV, "build", "--quiet", "--wheel", "-o", out, dist["sdist"]], timeout=600)
    [again] = out.glob("*.whl")
    assert archive_content(again) == archive_content(dist["wheel"])
    # The release's wheel was built on Linux; built again here on Windows, only the zip headers differ
    # (archive_content). The release rehearsal's Windows check stopped on this (run 41007605467).
    if POSIX or not GIVEN:
        assert again.read_bytes() == dist["wheel"].read_bytes()


def test_the_sdist_installs_the_same_package(dist, tmp_path):
    venv = tmp_path / "venv-sdist"
    ok([UV, "venv", "--quiet", "--python", CURRENT, venv])
    ok([UV, "pip", "install", "--quiet", "--python", venv_python(venv), dist["sdist"]], timeout=600)
    kit = ok([venv_python(venv), "-c", "from aelix_analytics._install import kit_dir; print(kit_dir())"],
             cwd=tmp_path, env=outside_env(tmp_path)).stdout.strip()
    assert sorted(p.name for p in Path(kit).iterdir()) == ["aelix_analytics"]
    assert (Path(kit) / "aelix_analytics" / "template" / "gitignore").is_file()


# --- installed where no checkout is ----------------------------------------------------------------


@pytest.mark.parametrize("python", [FLOOR, CURRENT])
def test_the_installed_distribution_finds_its_kit(dist, tmp_path, python):
    venv = installed(tmp_path, dist["wheel"], python, "--no-deps", "--offline")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    probe = textwrap.dedent("""
        import importlib.util, json, sys
        import aelix_analytics
        from aelix_analytics._install import kit_dir
        kit = kit_dir()
        print(json.dumps({
            "python": list(sys.version_info[:2]),
            "kit": str(kit),
            "entries": sorted(p.name for p in kit.iterdir()),
            "template": sorted(p.name for p in (kit / "aelix_analytics" / "template").iterdir()),
            "package": aelix_analytics.__file__,
            "ours": sorted(m for m in sys.modules if m.split(".")[0] == "aelix_analytics"),
            "labkit_importable": importlib.util.find_spec("labkit") is not None,
            "analysis": sorted(m for m in sys.modules if m.split(".")[0] in ANALYSIS),
        }))
    """).replace("ANALYSIS", repr(ANALYSIS))
    seen = json.loads(ok([venv_python(venv), "-c", probe], cwd=elsewhere,
                         env=outside_env(tmp_path)).stdout)
    assert seen["python"] == [int(x) for x in python.split(".")]
    site = Path(ok([venv_python(venv), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                   env=outside_env(tmp_path)).stdout.strip())
    assert Path(seen["kit"]) == (site / "aelix_analytics" / "kit").resolve()
    assert seen["entries"] == ["aelix_analytics"], "children get exactly the runtime root on PYTHONPATH"
    assert seen["template"] == ["CONTEXT.md", "gitattributes", "gitignore", "pyproject.toml"]
    assert Path(seen["package"]) == site / "aelix_analytics" / "__init__.py"
    # The package's __init__ imports nothing of the kit (Issue #9): what aelix and the launcher get.
    assert seen["ours"] == ["aelix_analytics", "aelix_analytics._install"], seen["ours"]
    assert seen["labkit_importable"] is False, "no top-level labkit: the old name has no alias"
    tops = sorted(p.name for p in site.iterdir()
                  if not p.name.startswith(("_", ".")) and not p.name.endswith((".dist-info", ".pth")))
    assert tops == ["aelix_analytics"], tops
    assert seen["analysis"] == []


@pytest.mark.parametrize("python", [FLOOR, CURRENT])
def test_the_console_script_is_the_launcher(dist, tmp_path, python):
    venv = installed(tmp_path, dist["wheel"], python, "--no-deps", "--offline")
    analytics = shutil.which("analytics", path=str(venv_bin(venv)))
    assert analytics is not None
    done = run([analytics, "--help"], cwd=tmp_path, env=outside_env(tmp_path))
    assert done.returncode == 0, done.stderr
    assert done.stdout == cli.USAGE


@pytest.mark.skipif(not POSIX, reason="the stand-in uv is a script; Windows starts only an .exe")
def test_the_console_script_gives_children_exactly_the_installed_kit(dist, tmp_path):
    venv = installed(tmp_path, dist["wheel"], FLOOR, "--no-deps", "--offline")
    fake_uv = tmp_path / "fake-uv"
    record = tmp_path / "env.json"
    fake_uv.write_text(f"#!{sys.executable}\nimport json, os\n"
                       f"json.dump(dict(os.environ), open({str(record)!r}, 'w'))\n",
                       encoding="utf-8")
    fake_uv.chmod(0o755)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "lab.json").write_text("{}", encoding="utf-8")
    analytics = shutil.which("analytics", path=str(venv_bin(venv)))
    ok([analytics, "status"], cwd=project, env=outside_env(tmp_path, AELIX_ANALYTICS_UV=str(fake_uv),
                                                           PYTHONPATH="/somewhere/else"))
    env = json.loads(record.read_text(encoding="utf-8"))
    kit = ok([venv_python(venv), "-c", "from aelix_analytics._install import kit_dir; print(kit_dir())"],
             env=outside_env(tmp_path)).stdout.strip()
    assert env["PYTHONPATH"] == kit
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


# --- python -m aelix_analytics of the installed package -----------------------------------------------


def tree_digest(root: Path) -> dict:
    """Every file under `root` and its bytes' digest: what a refused command must leave as it was."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("python", [FLOOR, CURRENT])
def test_the_installed_package_refuses_to_run_the_kit_itself(dist, tmp_path, python):
    """ADR-0005 confirmation 1 as the owner scoped it on 2026-10-06 (Issue #9): `python -m
    aelix_analytics` of the installed package runs no kit command, in any environment — here the
    installed one, inside a project and with a new directory to create — and writes nothing: not
    the project, not the trust list, not the new directory. Nor does the runtime root's copy run
    under another name. A call that names the runtime root on PYTHONPATH runs (it made the project)."""
    venv = installed(tmp_path, dist["wheel"], python, "--no-deps", "--offline")
    env = outside_env(tmp_path)
    kit = ok([venv_python(venv), "-c", "from aelix_analytics._install import kit_dir; print(kit_dir())"],
             env=env).stdout.strip()
    project = tmp_path / "proj"
    made = ok([venv_python(venv), "-m", "aelix_analytics", "init", project], cwd=tmp_path,
              env={**env, "PYTHONPATH": kit})
    assert json.loads(made.stdout.splitlines()[-1])["ok"] is True, made.stdout
    trust = Path(env["AELIX_ANALYTICS_TRUST_FILE"])
    before = (tree_digest(project), trust.read_bytes())
    fresh = tmp_path / "fresh"
    for argv, cwd in ((["-m", "aelix_analytics", "status"], project),
                      (["-m", "aelix_analytics", "--project", project, "record", "finding", "x"], tmp_path),
                      (["-m", "aelix_analytics", "init", fresh], tmp_path),
                      (["-m", "aelix_analytics.kit.aelix_analytics", "--project", project, "status"],
                       tmp_path)):
        done = run([venv_python(venv), *argv], cwd=cwd, env=env)
        assert done.returncode == 1, (argv, done.stdout, done.stderr)
        answer = json.loads(done.stdout.splitlines()[-1])
        assert answer["ok"] is False and answer["error"]["code"] == "not_runtime_root", answer
        assert "PYTHONPATH" in answer["error"]["hint"], answer
    assert (tree_digest(project), trust.read_bytes()) == before
    assert not fresh.exists()


# --- the end-to-end leg: aelix, and real uv ----------------------------------------------------------


def aelix_env(tmp_path: Path) -> dict:
    """Nothing of the user's aelix: its home, agent directory and settings are the test's own, and
    no model can be reached (no credentials; only list, verify, status, the loader and the tools'
    own execute run)."""
    home = tmp_path / "home"
    (home / "agent").mkdir(parents=True, exist_ok=True)
    env = outside_env(tmp_path, HOME=str(home), USERPROFILE=str(home),
                      AELIX_CODING_AGENT_DIR=str(home / "agent"),
                      XDG_CONFIG_HOME=str(home / "config"),
                      AELIX_SETTINGS_PATH=str(home / "agent" / "settings.json"))
    for name in list(env):
        if name.endswith("_API_KEY") or name.startswith(("ANTHROPIC", "OPENAI", "GEMINI")):
            del env[name]
    # uv's cache stays the caller's: a moved HOME would otherwise start it empty.
    if "UV_CACHE_DIR" not in env:
        env["UV_CACHE_DIR"] = run([UV, "cache", "dir"]).stdout.strip()
    return env


def aelix_cli(venv: Path) -> Path:
    return venv_bin(venv) / ("aelix.exe" if os.name == "nt" else "aelix")


# Loads the extensions twice in one process, as aelix does for every session it builds, and reports
# what the installed copy registered each time and what its module state was.
LOAD_PROBE = textwrap.dedent("""
    import asyncio, json, sys
    from pathlib import Path
    import aelix_coding_agent.extensions.loader as loader

    def ours(result):
        return [e for e in result.extensions if "analytics_status" in e.tools or "analytics" in e.commands]

    async def main():
        cwd, agent_dir, configured = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
        first = await loader.discover_and_load_extensions(configured, cwd=cwd, agent_dir=agent_dir)
        module = sys.modules.get("aelix_analytics.extension")
        if module is not None:
            module.STATE.update(session="/sessions/A.jsonl", project=str(cwd))
        second = await loader.discover_and_load_extensions(configured, cwd=cwd, agent_dir=agent_dir)
        seen = {"copies": [{"name": str(e.name), "manifest": e.manifest.plugin.id if e.manifest else None,
                            "tools": sorted(e.tools), "commands": sorted(e.commands),
                            "flags": sorted(e.flags),
                            "handlers": {k: len(v) for k, v in e.handlers.items()}}
                           for e in ours(second)],
                "first_copies": len(ours(first)),
                "errors": [str(err.error)[:300] for err in first.errors + second.errors],
                "analysis": sorted(m for m in sys.modules if m.split(".")[0] in ANALYSIS),
                "ours": sorted(m for m in sys.modules if m.split(".")[0] == "aelix_analytics")}
        if module is not None:
            seen.update(kit=str(module.KIT), route=module.KIT_ROUTE,
                        same_module=sys.modules.get("aelix_analytics.extension") is module,
                        state_after_rebuild={k: module.STATE[k] for k in ("session", "project")})
        print(json.dumps(seen))

    asyncio.run(main())
""").replace("in ANALYSIS", f"in {ANALYSIS!r}")


def with_aelix(tmp_path: Path, dist: dict, python: str) -> Path:
    venv = installed(tmp_path, dist["wheel"], python)
    ok([UV, "pip", "install", "--quiet", "--python", venv_python(venv), AELIX])
    return venv


@e2e
@pytest.mark.parametrize("python", [FLOOR, CURRENT])
def test_aelix_binds_and_loads_the_installed_extension(dist, tmp_path, python):
    venv = with_aelix(tmp_path, dist, python)
    env = aelix_env(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    verify = run([aelix_cli(venv), "extension", "verify", "aelix-analytics"], cwd=cwd, env=env)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "BOUND" in verify.stdout and "plugin 'aelix-analytics'" in verify.stdout
    status = json.loads(ok([aelix_cli(venv), "status", "--json"], cwd=cwd, env=env).stdout)
    [ours] = [e for e in status["discovered_extensions"] if e["name"] == "aelix-analytics"]
    assert ours["has_manifest"] is True and ours["scope"] == "entry_point"
    assert status["extension_errors"] == []
    loaded = ok([venv_python(venv), "-c", LOAD_PROBE, cwd, env["AELIX_CODING_AGENT_DIR"]], cwd=cwd,
                env=env)
    seen = json.loads(loaded.stdout.splitlines()[-1])
    # Apache-2.0 is on aelix's licence list: no "outside the v1 whitelist" warning at load.
    assert "whitelist" not in loaded.stderr, loaded.stderr
    [copy] = seen["copies"]
    assert copy["manifest"] == "aelix-analytics"
    assert copy["tools"] == ["analytics_import", "analytics_init", "analytics_query", "analytics_record",
                             "analytics_run", "analytics_status"]
    assert copy["commands"] == ["analytics"] and copy["flags"] == ["analytics-project"]
    # One handler per event, user_bash among them since kit 0.8.0 (a person's `!` kit command,
    # CONTRACTS A): the set tests/test_extension.py reads from the file's own load, each once.
    assert copy["handlers"] == {"before_agent_start": 1, "tool_execution_start": 1, "tool_execution_end": 1,
                                "agent_end": 1, "user_bash": 1}
    assert seen["errors"] == [] and seen["analysis"] == []
    # The extension's own package, its __init__ that imports nothing of the kit, and _install: core
    # and _platform come from the runtime root under a synthetic name (ADR-0003), not this package.
    assert seen["ours"] == ["aelix_analytics", "aelix_analytics._install",
                            "aelix_analytics.extension"], seen["ours"]
    assert seen["route"] == "installed"
    site = Path(ok([venv_python(venv), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                   env=env).stdout.strip())
    assert Path(seen["kit"]) == (site / "aelix_analytics" / "kit").resolve()
    # One module for every session aelix builds, and its state starts over each time (dist-1).
    assert seen["same_module"] is True
    assert seen["state_after_rebuild"] == {"session": None, "project": None}


def windows_account() -> dict:
    """Who may make the link, read as tools/platform_smoke.py reads it."""
    import ctypes
    import winreg

    seen = {}
    with contextlib.suppress(Exception):
        seen["administrator"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    unlock = r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock"
    with contextlib.suppress(OSError), winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, unlock) as key:
        seen["developer_mode"] = winreg.QueryValueEx(key, "AllowDevelopmentWithoutDevLicense")[0]
    return seen


@e2e
def test_a_checkout_symlink_beside_the_wheel_sets_up_one_copy(dist, tmp_path):
    """README's symlink route beside the installed wheel. On Windows the file link is made by
    `os.symlink`, as this runner's account may; the observation names that account's settings, so a
    record says only what ran (design review T6): Developer Mode alone, elevation alone, and a link
    made with `mklink` or `New-Item` are not run."""
    venv = with_aelix(tmp_path, dist, CURRENT)
    env = aelix_env(tmp_path)
    extensions = Path(env["AELIX_CODING_AGENT_DIR"]) / "extensions"
    extensions.mkdir(parents=True)
    try:
        os.symlink(PACK / "extension" / "aelix_analytics.py", extensions / "aelix_analytics.py")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"this account may not create a symbolic link: {exc!r}")
        raise
    record_observation("readme-symlink-route", {
        "os": os.name, "link": "os.symlink of the checkout's extension file",
        **({} if POSIX else windows_account())})
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    done = ok([venv_python(venv), "-c", LOAD_PROBE, cwd, env["AELIX_CODING_AGENT_DIR"]], cwd=cwd,
              env=env)
    seen = json.loads(done.stdout.splitlines()[-1])
    registered = [c for c in seen["copies"] if c["tools"]]
    assert len(registered) == 1, seen
    assert "is not set up: this aelix already loaded" in done.stderr


TOOLS_PROBE = textwrap.dedent("""
    import asyncio, json, sys
    from pathlib import Path
    from types import SimpleNamespace
    import aelix_coding_agent.extensions.loader as loader
    from aelix_ai.tools import ToolExecutionContext

    async def main():
        work = Path(sys.argv[1])
        result = await loader.discover_and_load_extensions([], cwd=work, agent_dir=Path(sys.argv[2]))
        [ext] = [e for e in result.extensions if "analytics_init" in e.tools]
        ctx = ToolExecutionContext(tool_call_id="call", model=SimpleNamespace(input=["text"]))
        answers = {}
        async def call(name, args):
            out = await ext.tools[name].execute(args, ctx)
            answers[name] = {"error": out.is_error,
                             "text": "\\n".join(c.text for c in out.content if hasattr(c, "text"))[:4000]}
        await call("analytics_init", {"path": str(work / "proj"), "title": "도구로 만든 프로젝트"})
        await call("analytics_import", {"source": sys.argv[3], "name": "sales"})
        (work / "proj" / "scripts" / "totals.py").write_text(
            "import aelix_analytics as analytics\\n"
            "analytics.save(analytics.read('sales'), 'copy')\\n"
            "analytics.record('finding', 'from the installed kit')\\n", encoding="utf-8")
        await call("analytics_run", {"script": "scripts/totals.py", "title": "합계", "attach_images": False})
        await call("analytics_record", {"kind": "decision", "text": "keep going"})
        print(json.dumps(answers))

    asyncio.run(main())
""")


@e2e
@pytest.mark.parametrize("python", [FLOOR, CURRENT])
def test_the_installed_extension_s_tools_run_a_project(dist, tmp_path, python):
    """The aelix half of Issue #8's criterion 4: the installed adapter creates a project from the
    installed template and runs the installed kit, called through its own tools (no model)."""
    venv = with_aelix(tmp_path, dist, python)
    env = aelix_env(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    csv = tmp_path / "sales.csv"
    csv.write_text("month,region,revenue\n2025-01,서울,100\n2025-02,부산,250\n", encoding="utf-8")
    done = ok([venv_python(venv), "-c", TOOLS_PROBE, work, env["AELIX_CODING_AGENT_DIR"], csv],
              cwd=work, env=env, timeout=1800)
    answers = json.loads(done.stdout.splitlines()[-1])
    for name, answer in answers.items():
        assert answer["error"] is False, (name, answer["text"])
    assert "environment: synced" in answers["analytics_init"]["text"]
    assert answers["analytics_run"]["text"].startswith("analytics_run: ok")
    assert (work / "proj" / "CONTEXT.md").is_file()


# Written with write_bytes, so its sha256 is this constant on every platform (ADR-0007 7 ②; text mode
# would make it CRLF on Windows, and the kit keeps a user's bytes).
SDK_SCRIPT_SHA256 = "024f3144a10e797651150974ba2b3bc0043afa48bd2714d7cb594150c9757abc"
SDK_SCRIPT = textwrap.dedent("""
    import os
    import aelix_analytics as analytics
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    print("RUN", analytics.run_id())
    print("ROOT", os.path.dirname(os.path.dirname(analytics.__file__)))
    print("PYTHONPATH", os.environ.get("PYTHONPATH"))
    sales = analytics.read("sales")
    # Sorted: group_by does not keep an order, and the rerun below compares the chart's data.
    by_region = sales.group_by("region").agg(revenue=__import__("polars").col("revenue").sum())
    by_region = by_region.sort("region")
    analytics.save(by_region, "by_region")
    fig, ax = plt.subplots()
    ax.bar(by_region["region"].to_list(), by_region["revenue"].to_list())
    analytics.visualize(fig, "by-region", title="지역별", data=by_region)
    analytics.record("finding", "두 지역")
""")


@e2e
@pytest.mark.parametrize("python", [FLOOR, CURRENT])
def test_the_installed_command_drives_a_project_without_aelix(dist, tmp_path, python):
    """Issue #8's criterion 1 without Aelix: the console script on the entry-point floor and on 3.12,
    with real uv, runs every command the Issue names in a project whose analysis environment is its
    own (>=3.12); the run's script uses the SDK, `aelix_analytics`, from the installed kit. ADR-0004 check 2
    with real uv (design review, Codex 6): a PYTHONPATH the caller set reaches the run as exactly
    the installed kit. A rerun of the run goes through real uv too."""
    venv = installed(tmp_path, dist["wheel"], python, "--no-deps", "--offline")
    listed = ok([UV, "pip", "list", "--python", venv_python(venv)]).stdout
    assert "aelix" not in listed.replace("aelix-analytics", ""), listed
    analytics = shutil.which("analytics", path=str(venv_bin(venv)))
    env = outside_env(tmp_path)
    if POSIX:  # a stand-in opener; os.startfile would open a real program
        opener_dir = tmp_path / "bin"
        opener_dir.mkdir()
        for name in ("open", "xdg-open"):
            (opener_dir / name).write_text(f"#!/bin/sh\necho \"$1\" >> '{tmp_path / 'opened'}'\n",
                                           encoding="utf-8")
            (opener_dir / name).chmod(0o755)
        env["PATH"] = f"{opener_dir}{os.pathsep}{env['PATH']}"
    work = tmp_path / "work"
    work.mkdir()
    # A title TOML would take apart (Issue #65): uv builds the environment from pyproject.toml all the same.
    title = '설치본 "분석" \\ C:\\path {{date}}\n둘째 줄'
    ok([analytics, "init", "proj", "--title", title], cwd=work, env=env)
    project = work / "proj"
    assert tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]["description"] == title
    # The environment uv made from the template keeps its security floor (Issue #131).
    [pillow] = [p["version"] for p in tomllib.loads((project / "uv.lock").read_text(encoding="utf-8"))["package"]
                if p["name"] == "pillow"]
    assert int(pillow.split(".")[0]) >= 13, pillow
    csv = tmp_path / "sales.csv"
    csv.write_text("month,region,revenue\n2025-01,서울,100\n2025-02,부산,250\n", encoding="utf-8")
    ok([analytics, "import", csv, "--name", "sales"], cwd=project, env=env)
    query = json.loads(ok([analytics, "query", "SELECT count(*) AS n FROM sales"], cwd=project,
                          env=env).stdout.splitlines()[-1])
    assert query["rows"] == [[2]]
    (project / "scripts" / "totals.py").write_bytes(SDK_SCRIPT.encode("utf-8"))
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    done = ok([analytics, "run", "scripts/totals.py", "--title", "합계"], cwd=project,
              env={**env, "PYTHONPATH": str(unrelated)})
    result = json.loads(done.stdout.splitlines()[-1])
    record = result["run"]
    assert record["status"] == "ok", result
    assert tuple(int(x) for x in record["env"]["python"].split(".")[:2]) >= (3, 12)
    # ADR-0007 7 ②·③: the script's hash and the installed kit's hash are the same on every leg.
    record_observation(f"installed-run-py{python}", {
        "script_sha256": record["script"]["sha256"], "kit_sha256": record["env"]["kit_sha256"],
        "kit_version": record["env"]["kit_version"]})
    assert record["script"]["sha256"] == SDK_SCRIPT_SHA256
    assert record["env"]["kit_sha256"] == tracked_kit_sha()
    assert record["env"]["kit_version"] == core.KIT_VERSION
    kit = ok([venv_python(venv), "-c", "from aelix_analytics._install import kit_dir; print(kit_dir())"],
             env=env).stdout.strip()
    tail = result.get("stdout_tail", "")
    assert f"ROOT {kit}" in tail
    assert f"PYTHONPATH {kit}" in tail.splitlines(), tail
    assert "RUN None" not in tail
    [chart] = record["visualizations"]
    assert record["schema"] == "analytics.run/2"
    assert chart["visualization_id"] == "by-region" and chart.get("data")
    journal = [json.loads(line) for line in
               (project / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    [finding] = [e for e in journal if e["kind"] == "finding"]
    assert record["id"] in json.dumps(finding["refs"]), finding
    again = json.loads(ok([analytics, "run", "--rerun-of", record["id"], "--title", "재실행"],
                          cwd=project, env=env).stdout.splitlines()[-1])["run"]
    assert again["status"] == "ok" and again["rerun_of"] == record["id"], again
    assert again["reproduction"]["verdict"] == "match", again["reproduction"]
    ok([analytics, "record", "decision", "계속"], cwd=project, env=env)
    ok([analytics, "render"], cwd=project, env=env)
    assert 'project: 설치본 "분석"' in ok([analytics, "status"], cwd=project, env=env).stdout
    ok([analytics, "context"], cwd=project, env=env)
    if POSIX:
        ok([analytics, "open"], cwd=project, env=env)
        assert (tmp_path / "opened").read_text(encoding="utf-8").strip().endswith("index.html")
    site = venv_bin(venv).parent
    assert not [p for p in site.rglob("*.pyc")
                if "aelix_analytics" in p.parts and f"cpython-{python.replace('.', '')}" not in p.name], \
        "a child wrote bytecode of another Python into the installation"


@e2e
def test_the_readme_s_uv_tool_install_routes(dist, tmp_path):
    """README's two install lines as written there (design review crit-3): `uv tool install
    --python 3.12` of the wheel, whose `analytics` then creates and reads a project with real uv;
    and aelix with the wheel beside it, whose verify binds the extension. The tools and their
    commands go under the test's directory; aelix's own directories are the test's as well."""
    tools = {"UV_TOOL_DIR": str(tmp_path / "tools"), "UV_TOOL_BIN_DIR": str(tmp_path / "tool-bin")}
    env = outside_env(tmp_path, **tools)
    ok([UV, "tool", "install", "--python", CURRENT, dist["wheel"]], cwd=tmp_path, env=env)
    cfg = (tmp_path / "tools" / "aelix-analytics" / "pyvenv.cfg").read_text(encoding="utf-8")
    assert f"version_info = {CURRENT}." in cfg, cfg
    analytics = shutil.which("analytics", path=tools["UV_TOOL_BIN_DIR"])
    assert analytics is not None, sorted(os.listdir(tools["UV_TOOL_BIN_DIR"]))
    work = tmp_path / "work"
    work.mkdir()
    ok([analytics, "init", "proj", "--title", "도구 설치본"], cwd=work, env=env)
    assert "도구 설치본" in ok([analytics, "status"], cwd=work / "proj", env=env).stdout
    ok([UV, "tool", "install", "--python", CURRENT, AELIX, "--with", dist["wheel"]], cwd=tmp_path,
       env=env)
    aelix = shutil.which("aelix", path=tools["UV_TOOL_BIN_DIR"])
    assert aelix is not None, sorted(os.listdir(tools["UV_TOOL_BIN_DIR"]))
    verify = run([aelix, "extension", "verify", "aelix-analytics"], cwd=work,
                 env={**aelix_env(tmp_path), **tools})
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "BOUND" in verify.stdout and "plugin 'aelix-analytics'" in verify.stdout


@e2e
def test_the_kit_s_stdlib_commands_run_on_the_floor(dist, tmp_path):
    """ADR-0002's check 1, the part the floor can run, as ADR-0004 reads it and the owner accepted it
    on 2026-09-30: the kit's module (`python -m labkit` then, `python -m aelix_analytics` since kit
    0.2.0) on CPython 3.11 with the installed runtime root on PYTHONPATH — the call the guard lets
    through (owner's decision of 2026-10-06) — for the commands that need no analysis library, and a
    run of a script that imports none (it records a note through the SDK)."""
    venv = installed(tmp_path, dist["wheel"], FLOOR, "--no-deps", "--offline")
    kit = ok([venv_python(venv), "-c", "from aelix_analytics._install import kit_dir; print(kit_dir())"],
             env=outside_env(tmp_path)).stdout.strip()
    env = outside_env(tmp_path, PYTHONPATH=kit)
    project = tmp_path / "proj"
    for args in (["init", project], ["--project", project, "status"],
                 ["--project", project, "context"],
                 ["--project", project, "checkpoint", "3.11 checkpoint"],
                 ["--project", project, "record", "finding", "3.11에서"],
                 ["--project", project, "render"]):
        done = ok([venv_python(venv), "-m", "aelix_analytics", *args], cwd=tmp_path, env=env)
        assert json.loads(done.stdout.splitlines()[-1])["ok"] is True, (args, done.stdout)
    (project / "scripts").mkdir(exist_ok=True)
    (project / "scripts" / "plain.py").write_text(
        "import aelix_analytics as analytics\n"
        "analytics.record('finding', 'a run on the floor')\n"
        "print(sum(range(10)))\n", encoding="utf-8")
    done = ok([venv_python(venv), "-m", "aelix_analytics", "--project", project, "run", "scripts/plain.py",
               "--title", "3.11에서 run"], cwd=tmp_path, env=env)
    result = json.loads(done.stdout.splitlines()[-1])
    assert result["ok"] is True and result["run"]["status"] == "ok", done.stdout
    assert result["run"]["env"]["python"].startswith("3.11."), result["run"]["env"]


@e2e
def test_pip_installs_and_uninstalls_the_wheel_without_leftovers(dist, tmp_path):
    venv = tmp_path / "venv-pip"
    ok([UV, "venv", "--quiet", "--seed", "--python", FLOOR, venv])
    ok([venv_python(venv), "-m", "pip", "install", "--quiet", "--no-deps", dist["wheel"]])
    analytics = shutil.which("analytics", path=str(venv_bin(venv)))
    env = outside_env(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    ok([analytics, "init", "proj"], cwd=work, env=env)
    ok([analytics, "status"], cwd=work / "proj", env=env)
    ok([venv_python(venv), "-m", "pip", "uninstall", "--quiet", "--yes", "aelix-analytics"])
    site = Path(ok([venv_python(venv), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                   env=env).stdout.strip())
    assert not (site / "aelix_analytics").exists(), sorted(
        str(p.relative_to(site)) for p in (site / "aelix_analytics").rglob("*"))


# --- what a release offers: an index, aelix's extension commands, upgrades, removal (Issue #24) -----

# The wheel of kit 0.25.0 that Q17 measured and the owner installed (docs/verification/
# 2026-10-22-issue-23-q17.md): its commit builds it byte for byte on Linux and macOS, and on Windows
# with the same members and bytes (archive_content), so the upgrades below start from a distribution
# a person had, not one made for the test. The platform job checks out the whole history, which holds
# the commit.
PREVIOUS_COMMIT = "b4e5b4ed93e2bcd15db61c3464442269f43d2bb0"
PREVIOUS_VERSION = "0.25.0"
PREVIOUS_WHEEL_SHA256 = "660beb63c24d1882c065579b933cf9eeb162d43d49234d7b0360c411eb36ab28"
PREVIOUS_CONTENT_SHA256 = "0938bfc002247eef7692a5d90f1ab6916760009859cb19fafcac0da8a58918a5"
LISTED = re.compile(r"aelix-analytics \[aelix-analytics ([^\]\s]+)\]")


@pytest.fixture(scope="session")
def previous(tmp_path_factory) -> Path:
    if run(["git", "-C", PACK, "cat-file", "-e", f"{PREVIOUS_COMMIT}^{{commit}}"]).returncode != 0:
        # The install leg runs the routes SUPPORT.md cites: it fails, not skips, without the commit.
        message = f"the checkout does not hold {PREVIOUS_COMMIT[:12]}: a shallow clone (fetch-depth: 0)"
        pytest.fail(message) if E2E else pytest.skip(message)
    root = tmp_path_factory.mktemp("previous")
    archive = subprocess.run(["git", "-C", str(PACK), "-c", "core.autocrlf=false", "archive",
                              "--format=tar", PREVIOUS_COMMIT], capture_output=True, timeout=300,
                             check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root / "src", filter="data")
    ok([UV, "build", "--quiet", "-o", root / "dist", root / "src"], timeout=600)
    [wheel] = (root / "dist").glob("*.whl")
    assert wheel.name == f"aelix_analytics-{PREVIOUS_VERSION}-py3-none-any.whl"
    assert content_digest(wheel) == PREVIOUS_CONTENT_SHA256
    if POSIX:
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == PREVIOUS_WHEEL_SHA256
    return wheel


class PackageIndex:
    """A PEP 503 simple index of one project on loopback, standing in for PyPI: uv and aelix resolve
    `aelix-analytics` from it by name. As uv's default index (UV_DEFAULT_INDEX) it takes PyPI's
    place, so README's commands run as written; where aelix itself has to come from PyPI, it is an
    index uv asks first (UV_INDEX). Its pages say not to cache them, so a wheel added later is seen at
    once (PyPI's are cached for minutes, which a person waits out)."""

    def __init__(self, root: Path, port: int):
        self.project = root / "simple" / "aelix-analytics"
        self.project.mkdir(parents=True)
        (root / "simple" / "index.html").write_text(
            '<!DOCTYPE html><html><body><a href="aelix-analytics/">aelix-analytics</a></body></html>\n',
            encoding="utf-8")
        self.url = f"http://127.0.0.1:{port}/simple"

    def add(self, wheel: Path) -> None:
        shutil.copyfile(wheel, self.project / wheel.name)
        links = "".join(f'<a href="{p.name}#sha256={hashlib.sha256(p.read_bytes()).hexdigest()}">'
                        f"{p.name}</a>\n" for p in sorted(self.project.glob("*.whl")))
        (self.project / "index.html").write_text(f"<!DOCTYPE html><html><body>\n{links}</body></html>\n",
                                                 encoding="utf-8")


@contextlib.contextmanager
def package_index(root: Path, *wheels: Path):
    import functools
    import http.server
    import threading

    class Pages(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-cache")
            super().end_headers()

        def log_message(self, *args):
            pass

    root.mkdir(parents=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Pages, directory=str(root)))
    index = PackageIndex(root, server.server_address[1])
    for wheel in wheels:
        index.add(wheel)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield index
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=30)


def tool_env(tmp_path: Path) -> tuple[dict, dict]:
    """aelix_env, with uv's tools and their commands under the test's directory, and loopback
    reached without a proxy."""
    tools = {"UV_TOOL_DIR": str(tmp_path / "tools"), "UV_TOOL_BIN_DIR": str(tmp_path / "tool-bin")}
    env = {**aelix_env(tmp_path), **tools}
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    return env, tools


def tool_bin(tools: dict, name: str) -> str:
    found = shutil.which(name, path=tools["UV_TOOL_BIN_DIR"])
    assert found is not None, (name, sorted(os.listdir(tools["UV_TOOL_BIN_DIR"])))
    return found


def tool_python(tools: dict, name: str) -> Path:
    return venv_python(Path(tools["UV_TOOL_DIR"]) / name)


def purelib(python: Path, env: dict) -> Path:
    return Path(ok([python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                   env=env).stdout.strip())


def installed_files(site: Path) -> dict:
    """What of this distribution is in an environment: the package's files by their sha256 (bytecode
    left out) and the names of its dist-info directories. Equal to wheel_files of a wheel, the
    environment holds that wheel's files and no other: none of an earlier version left beside them or
    under their names. (Kit 0.25.0's wheel has no file this version's lacks, so against it what an
    upgrade could leave shows in the dist-info and in the files' bytes.)"""
    package = site / "aelix_analytics"
    files = {p.relative_to(site).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in package.rglob("*") if p.is_file() and "__pycache__" not in p.parts} if package.exists() else {}
    return {"files": files, "dist_info": sorted(p.name for p in site.glob("aelix_analytics-*.dist-info"))}


def wheel_files(wheel: Path, version: str) -> dict:
    with zipfile.ZipFile(wheel) as archive:
        files = {n: hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist()
                 if n.startswith("aelix_analytics/")}
    return {"files": files, "dist_info": [f"aelix_analytics-{version}.dist-info"]}


# What an upgraded kit may write in a project it carries on: the journal (appended to, checked
# apart), the regenerable Workspace, the derived state under .analytics/, the analysis environment.
REWRITTEN = ("workspace/", ".analytics/", ".venv/")


def project_files(project: Path) -> dict[str, str]:
    """Every file of a project but the ones the kit rewrites (REWRITTEN), by its sha256."""
    return {rel: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(project.rglob("*"))
            if p.is_file() and not (rel := p.relative_to(project).as_posix()).startswith(REWRITTEN)}


def assert_carried_on(project: Path, before: dict[str, str], journal: bytes) -> None:
    """The earlier kit's project is all there, byte for byte: every file it had (the journal only
    grown), and nothing of it rewritten."""
    after = project_files(project)
    changed = sorted(rel for rel, sha in before.items() if rel != "journal.jsonl" and after.get(rel) != sha)
    assert not changed, changed
    grown = (project / "journal.jsonl").read_bytes()
    assert grown.startswith(journal) and len(grown.splitlines()) > len(journal.splitlines())


def listed_versions(text: str) -> list[str]:
    return LISTED.findall(text)


SCRIPT = ("import polars\n"
          "import aelix_analytics as analytics\n"
          "analytics.record('finding', 'a run after the upgrade')\n"
          "print(polars.DataFrame({'n': [1, 2]}).height)\n")


@e2e
def test_the_command_installs_upgrades_and_uninstalls_from_an_index(dist, previous, tmp_path):
    """README's `uv tool install aelix-analytics` once the release is on PyPI, with an index on
    loopback in PyPI's place: the earlier kit makes a project, `uv tool upgrade` brings this
    version, which carries the project on in the environment the earlier kit made, and `uv tool
    uninstall` leaves nothing of it."""
    env, tools = tool_env(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    project = work / "proj"
    with package_index(tmp_path / "index", previous) as index:
        # Only uv's tool commands get the index: the kit's own uv syncs the project from PyPI.
        indexed = {**env, "UV_DEFAULT_INDEX": index.url}
        ok([UV, "tool", "install", "--python", CURRENT, "aelix-analytics"], cwd=tmp_path, env=indexed)
        python = tool_python(tools, "aelix-analytics")
        assert installed_files(purelib(python, env)) == wheel_files(previous, PREVIOUS_VERSION)
        analytics = tool_bin(tools, "analytics")
        ok([analytics, "init", "proj", "--title", "이전 버전의 프로젝트"], cwd=work, env=env)
        ok([analytics, "record", "finding", "이전 kit이 쓴 발견"], cwd=project, env=env)
        before, journal = project_files(project), (project / "journal.jsonl").read_bytes()
        index.add(dist["wheel"])
        ok([UV, "tool", "upgrade", "aelix-analytics"], cwd=tmp_path, env=indexed)
    assert installed_files(purelib(python, env)) == wheel_files(dist["wheel"], core.KIT_VERSION)
    ok([analytics, "status"], cwd=project, env=env)
    (project / "scripts" / "after.py").write_bytes(SCRIPT.encode("utf-8"))
    done = ok([analytics, "run", "scripts/after.py", "--title", "업그레이드 뒤"], cwd=project, env=env)
    record = json.loads(done.stdout.splitlines()[-1])["run"]
    assert record["status"] == "ok" and record["env"]["kit_version"] == core.KIT_VERSION, record
    assert_carried_on(project, before, journal)
    ok([UV, "tool", "uninstall", "aelix-analytics"], cwd=tmp_path, env=env)
    assert not (Path(tools["UV_TOOL_DIR"]) / "aelix-analytics").exists()
    assert shutil.which("analytics", path=tools["UV_TOOL_BIN_DIR"]) is None


@e2e
def test_aelix_takes_the_extension_and_its_command_from_an_index(dist, previous, tmp_path):
    """README's aelix line once the release is on PyPI: aelix with aelix-analytics from the index and
    its `analytics` from the same environment (one kit). `uv tool upgrade aelix` keeps aelix's pin
    and brings this version of the extension; `uv tool uninstall aelix` removes both commands."""
    env, tools = tool_env(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    with package_index(tmp_path / "index", previous) as index:
        indexed = {**env, "UV_INDEX": index.url}
        ok([UV, "tool", "install", "--python", CURRENT, AELIX, "--with", "aelix-analytics",
            "--with-executables-from", "aelix-analytics"], cwd=tmp_path, env=indexed)
        aelix = tool_bin(tools, "aelix")
        verify = ok([aelix, "extension", "verify", "aelix-analytics"], cwd=cwd, env=env).stdout
        assert "BOUND" in verify and listed_versions(verify) == [PREVIOUS_VERSION], verify
        index.add(dist["wheel"])
        ok([UV, "tool", "upgrade", "aelix"], cwd=tmp_path, env=indexed)
    verify = ok([aelix, "extension", "verify", "aelix-analytics"], cwd=cwd, env=env).stdout
    assert "BOUND" in verify and listed_versions(verify) == [core.KIT_VERSION], verify
    python = tool_python(tools, "aelix")
    site = purelib(python, env)
    assert installed_files(site) == wheel_files(dist["wheel"], core.KIT_VERSION)
    assert "aelix==0.1.0b2" in ok([UV, "pip", "freeze", "--python", python], env=env).stdout
    listed = ok([UV, "tool", "list"], cwd=tmp_path, env=env).stdout
    assert re.search(r"^aelix v0\.1\.0b2\n- aelix\n- analytics$", listed, re.M), listed
    tool_bin(tools, "analytics")
    loaded = ok([python, "-c", LOAD_PROBE, cwd, env["AELIX_CODING_AGENT_DIR"]], cwd=cwd, env=env)
    seen = json.loads(loaded.stdout.splitlines()[-1])
    assert len(seen["copies"]) == 1 and seen["errors"] == [] and seen["analysis"] == [], seen
    assert Path(seen["kit"]) == (site / "aelix_analytics" / "kit").resolve()
    ok([UV, "tool", "uninstall", "aelix"], cwd=tmp_path, env=env)
    assert not (Path(tools["UV_TOOL_DIR"]) / "aelix").exists()
    for name in ("aelix", "analytics"):
        assert shutil.which(name, path=tools["UV_TOOL_BIN_DIR"]) is None, name


@e2e
def test_a_newer_wheel_file_replaces_the_earlier_one(dist, previous, tmp_path):
    """README: to change wheels, run the same `uv tool install` line with the new file. Over the
    earlier kit's wheel, nothing of the earlier one is left, alone or beside aelix."""
    env, tools = tool_env(tmp_path)
    for name, line in (("aelix-analytics", []), ("aelix", [AELIX, "--with"])):
        for wheel in (previous, dist["wheel"]):
            ok([UV, "tool", "install", "--python", CURRENT, *line, wheel], cwd=tmp_path, env=env)
        site = purelib(tool_python(tools, name), env)
        assert installed_files(site) == wheel_files(dist["wheel"], core.KIT_VERSION), name


# Carries on, in a new process, the project the earlier kit's tools made (TOOLS_PROBE), through the
# extension aelix now loads.
CONTINUE_PROBE = textwrap.dedent("""
    import asyncio, json, sys
    from pathlib import Path
    from types import SimpleNamespace
    import aelix_coding_agent.extensions.loader as loader
    from aelix_ai.tools import ToolExecutionContext

    async def main():
        work = Path(sys.argv[1])
        result = await loader.discover_and_load_extensions([], cwd=work, agent_dir=Path(sys.argv[2]))
        [ext] = [e for e in result.extensions if "analytics_status" in e.tools]
        ctx = ToolExecutionContext(tool_call_id="call", model=SimpleNamespace(input=["text"]))
        answers = {}
        async def call(name, args):
            out = await ext.tools[name].execute(args, ctx)
            answers[name] = {"error": out.is_error,
                             "text": "\\n".join(c.text for c in out.content if hasattr(c, "text"))[:4000]}
        await call("analytics_status", {})
        await call("analytics_run", {"script": "scripts/after.py", "title": "업그레이드 뒤",
                                     "attach_images": False})
        await call("analytics_record", {"kind": "finding", "text": "after the upgrade"})
        print(json.dumps(answers))

    asyncio.run(main())
""")


@e2e
def test_aelix_s_extension_commands_upgrade_a_wheel_and_remove_it(dist, previous, tmp_path):
    """`aelix extension install <wheel>` (README) in aelix's uv tool environment, which has no pip:
    the earlier wheel's tools make a project, installing this wheel replaces every file of the
    earlier one, the extension carries the project on, and `aelix extension remove` leaves nothing.
    aelix 0.1.0b2 records each wheel path as a source and names only the first, so `aelix extension
    update` puts the earlier wheel back; README says to install the new file instead."""
    env, tools = tool_env(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    ok([UV, "tool", "install", "--python", CURRENT, AELIX], cwd=tmp_path, env=env)
    aelix, python = tool_bin(tools, "aelix"), tool_python(tools, "aelix")
    site = purelib(python, env)

    def extension(*args) -> str:
        return ok([aelix, "extension", *args], cwd=work, env=env).stdout

    extension("install", previous, "--yes")
    assert listed_versions(extension("verify", "aelix-analytics")) == [PREVIOUS_VERSION]
    csv = tmp_path / "sales.csv"
    csv.write_text("month,region,revenue\n2025-01,서울,100\n2025-02,부산,250\n", encoding="utf-8")
    made = json.loads(ok([python, "-c", TOOLS_PROBE, work, env["AELIX_CODING_AGENT_DIR"], csv], cwd=work,
                         env=env, timeout=1800).stdout.splitlines()[-1])
    assert not [name for name, answer in made.items() if answer["error"]], made
    project = work / "proj"
    before, journal = project_files(project), (project / "journal.jsonl").read_bytes()
    [first_run] = [p for p in (project / "runs").iterdir() if (p / "run.json").is_file()]
    assert json.loads((first_run / "run.json").read_bytes())["env"]["kit_version"] == PREVIOUS_VERSION

    extension("install", dist["wheel"], "--yes")
    verify = extension("verify", "aelix-analytics")
    assert "BOUND" in verify and listed_versions(verify) == [core.KIT_VERSION], verify
    assert installed_files(site) == wheel_files(dist["wheel"], core.KIT_VERSION)
    (project / "scripts" / "after.py").write_bytes(SCRIPT.encode("utf-8"))
    carried = json.loads(ok([python, "-c", CONTINUE_PROBE, work, env["AELIX_CODING_AGENT_DIR"]], cwd=work,
                            env={**env, "AELIX_ANALYTICS_PROJECT": str(project)},
                            timeout=1800).stdout.splitlines()[-1])
    assert not [name for name, answer in carried.items() if answer["error"]], carried
    assert carried["analytics_run"]["text"].startswith("analytics_run: ok"), carried
    assert_carried_on(project, before, journal)
    kits = sorted(json.loads((p / "run.json").read_text(encoding="utf-8"))["env"]["kit_version"]
                  for p in (project / "runs").iterdir() if (p / "run.json").is_file())
    assert kits == sorted([PREVIOUS_VERSION, core.KIT_VERSION]), kits

    extension("update", "aelix-analytics", "--yes")
    assert listed_versions(extension("list")) == [PREVIOUS_VERSION]
    extension("install", dist["wheel"], "--yes")
    assert listed_versions(extension("list")) == [core.KIT_VERSION]
    extension("remove", "aelix-analytics", "--yes")
    assert listed_versions(extension("list")) == []
    assert installed_files(site) == {"files": {}, "dist_info": []}


@e2e
def test_aelix_installs_updates_and_removes_the_extension_by_name(dist, previous, tmp_path):
    """README's `aelix extension install aelix-analytics` by name, the index in PyPI's place, and
    `aelix extension update` once a newer version is there: unlike a wheel path, the name moves on.
    aelix's uv backend passes no index of its own, as it would for PyPI."""
    env, tools = tool_env(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    ok([UV, "tool", "install", "--python", CURRENT, AELIX], cwd=tmp_path, env=env)
    aelix = tool_bin(tools, "aelix")
    site = purelib(tool_python(tools, "aelix"), env)

    def extension(*args, **extra) -> str:
        return ok([aelix, "extension", *args], cwd=cwd, env={**env, **extra}).stdout

    with package_index(tmp_path / "index", previous) as index:
        extension("install", "aelix-analytics", "--yes", UV_DEFAULT_INDEX=index.url)
        assert listed_versions(extension("verify", "aelix-analytics")) == [PREVIOUS_VERSION]
        index.add(dist["wheel"])
        extension("update", "aelix-analytics", "--yes", UV_DEFAULT_INDEX=index.url)
    verify = extension("verify", "aelix-analytics")
    assert "BOUND" in verify and listed_versions(verify) == [core.KIT_VERSION], verify
    assert installed_files(site) == wheel_files(dist["wheel"], core.KIT_VERSION)
    extension("remove", "aelix-analytics", "--yes")
    assert listed_versions(extension("list")) == []
    assert installed_files(site) == {"files": {}, "dist_info": []}
