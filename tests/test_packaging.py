"""The distribution: the wheel and sdist `uv build` makes from this checkout, installed where no
checkout is (ADR-0004).

The default tests build both artifacts and install the wheel, without its non-existent dependencies,
into fresh virtual environments on the entry-point floor and on 3.12. They need uv, and the network
or uv's cache for the pinned build backend.

AELIX_ANALYTICS_TEST_INSTALL=1 adds the end-to-end leg: aelix 0.1.0b2 from PyPI beside the wheel,
aelix's own verify, status and loader, README's `uv tool install` lines, and a project driven through
the installed `analytics` by real uv — which syncs an analysis environment, so it needs the network or
a warm cache. The repository CI
(.github/workflows/ci.yml) runs it on Linux and Windows, and on macOS when asked.
"""

from __future__ import annotations

import contextlib
import hashlib
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
                 "kit/aelix_analytics/assets/workspace.js",
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


def test_the_current_contract_names_the_kit_s_version():
    """The current contract's distribution row states the version (CONTRACTS E): kept equal to
    KIT_VERSION and pyproject.toml, which a version bump changes together (review of PR 3: one bump
    missed the sentence)."""
    text = (PACK / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    assert f"버전은 {core.KIT_VERSION}입니다(`KIT_VERSION`, manifest와 같음)" in text
    assert pyproject()["project"]["version"] == core.KIT_VERSION


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
    assert sum(copy["handlers"].values()) == 4
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
    ok([analytics, "init", "proj", "--title", "설치본 분석"], cwd=work, env=env)
    project = work / "proj"
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
    assert "설치본 분석" in ok([analytics, "status"], cwd=project, env=env).stdout
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
