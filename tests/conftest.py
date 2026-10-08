import csv
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from aelix_analytics import _platform

PACK = Path(__file__).resolve().parents[1]
KIT = PACK / "src"
# The environment variables' names before kit 0.3.0 (core.RENAMED_ENV), which the kit refuses: a
# developer's shell that still sets one must not fail every test, so no test inherits them.
OLD_ENV = ("LAB_PROJECT", "LAB_ACTOR", "LAB_SESSION", "LAB_STYLE", "AELIX_LAB_PROJECT",
           "AELIX_LAB_TRUST_FILE", "AELIX_LAB_KIT", "AELIX_LAB_UV")


def kit_env(**extra) -> dict:
    # PYTHONUNBUFFERED changes how a kit lays out its run streams, which tests check; a test that
    # wants it passes it (review 8, critic-4).
    env = {k: v for k, v in os.environ.items()
           if k not in ("VIRTUAL_ENV", "PYTHONUNBUFFERED", *OLD_ENV)}
    env.update({"PYTHONPATH": str(KIT), "MPLBACKEND": "Agg", "AELIX_ANALYTICS_ACTOR": "test",
                "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra)
    return env


def kit_cli(project, *args, timeout=300):
    """Run `python -m aelix_analytics` like the extension does; returns (exit code, last JSON object)."""
    cmd = [sys.executable, "-m", "aelix_analytics", "--project", str(project), *map(str, args)]
    # The records are not ASCII and this reads them through a pipe, which follows the locale
    # unless it is told otherwise.
    proc = subprocess.run(cmd, capture_output=True, env=kit_env(), timeout=timeout,
                          **_platform.text_kwargs())
    # Split at LF after text mode has turned CRLF and a lone CR into LF, so U+2028, U+2029 and
    # U+0085 do not split the line (CONTRACTS D).
    lines = [line for line in proc.stdout.split("\n") if line.startswith("{")]
    assert lines, f"no JSON from {cmd}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return proc.returncode, json.loads(lines[-1])


# The kinds of directory link a project can hold: a symbolic link everywhere, and on Windows a
# junction as well (no privilege needed; `os.path.islink` is False for it, `os.path.isjunction` True).
LINK_KINDS = ["symlink", "junction"] if os.name == "nt" else ["symlink"]


def dir_link(kind: str, link, target) -> Path:
    """Make `link` a directory link of `kind` to `target`. A symbolic link keeps `target`'s text as
    given (so a relative target stays relative); a junction stores an absolute path, so a relative
    `target` is taken from `link`'s directory. A symbolic link the account may not create (WinError
    1314) skips the test with that error: the hosted Windows runner allows them, and a record that
    cites a run checks that its link tests ran rather than skipped."""
    link = Path(link)
    if kind == "symlink":
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip(f"this account may not create a symbolic link: {exc!r}")
            raise
    elif kind == "junction":
        import _winapi

        _winapi.CreateJunction(os.path.abspath(os.path.join(link.parent, target)), str(link))
    else:
        raise ValueError(kind)
    return link


def record_observation(name: str, data) -> None:
    """Keep what a test measured but does not assert, as `<name>.json` in the directory
    AELIX_ANALYTICS_TEST_EVIDENCE_DIR names (the CI sets it inside the directory it uploads); nothing
    when it is unset."""
    directory = os.environ.get("AELIX_ANALYTICS_TEST_EVIDENCE_DIR")
    if not directory:
        return
    path = Path(directory) / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True, default=str),
                    encoding="utf-8", newline="\n")


# --- the checkout as git has it (ADR-0007: line endings) ----------------------------------------


def git_checkout(*args: str) -> bytes:
    """`git -C <checkout> args` with its output, as bytes. Outside a git checkout, or without git,
    the test skips — except on CI (the CI variable set, as GitHub Actions sets it), where the
    checkout is the thing measured, so not having it fails (design review C5)."""
    done = None
    if shutil.which("git") is not None:
        done = subprocess.run(["git", "-C", str(PACK), *args], capture_output=True)
    if done is None or done.returncode != 0:
        why = "git is not installed" if done is None else done.stderr.decode(errors="replace")
        if os.environ.get("CI"):
            pytest.fail(f"CI measures a git checkout, and git {' '.join(args)} failed: {why}")
        pytest.skip(f"not a git checkout: {why}")
    return done.stdout


def checkout_eol() -> dict[str, tuple[str, str, str]]:
    """Every tracked file: (index, work tree, attributes) as `git ls-files --eol` says them, e.g.
    ("i/lf", "w/lf", "attr/text=auto eol=lf")."""
    out = {}
    for entry in git_checkout("ls-files", "--eol", "-z").split(b"\0"):
        if not entry:
            continue
        info, _, name = entry.decode("utf-8").partition("\t")
        fields = info.split()
        out[name] = (fields[0], fields[1], " ".join(fields[2:]))
    return out


# The files runner.kit_sha hashes: these suffixes under the package, outside __pycache__.
KIT_SHA_SUFFIXES = (".py", ".css", ".js", ".md", ".toml")


def tracked_kit_sha() -> str:
    """runner.kit_sha's value as the checkout's tracked files make it, on any platform: each file's
    name and bytes, in the order of their POSIX path parts compared case by case (what a POSIX
    `sorted(Path.rglob)` gives). The bytes are git's blobs — LF whatever the checkout's platform —
    but a file `git ls-files -m` lists (changed locally) is read from the work tree. A leg whose
    kit_sha differs from this hashed other bytes or another order (Windows sorts paths without
    case), so every leg asserting it shows the three agree (ADR-0007 decision 7 ③)."""
    package = "src/aelix_analytics"
    names = [n for n in git_checkout("ls-files", "-z", "--", package).decode("utf-8").split("\0")
             if n and PurePosixPath(n).suffix in KIT_SHA_SUFFIXES and "__pycache__" not in n]
    changed = set(git_checkout("ls-files", "-m", "-z", "--", package).decode("utf-8").split("\0"))
    digest = hashlib.sha256()
    for name in sorted(names, key=lambda n: PurePosixPath(n).relative_to(package).parts):
        data = (PACK / name).read_bytes() if name in changed else git_checkout("cat-file", "blob",
                                                                                f":{name}")
        digest.update(PurePosixPath(name).name.encode())
        digest.update(data)
    return digest.hexdigest()


def write_sales_csv(path: Path, rows: int = 120) -> Path:
    # A user's file on purpose, CRLF rows as csv writes them; no constant-hash test uses it (ADR-0007).
    regions = ["서울", "부산", "대구"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["order_date", "region", "channel", "revenue"])
        for i in range(rows):
            month = 1 + i % 12
            writer.writerow([f"2025-{month:02d}-{1 + i % 27:02d}", regions[i % 3],
                             "online" if i % 2 else "store", 1000 + (i * 37) % 500])
    return path


@pytest.fixture(autouse=True, scope="session")
def sigint_as_an_interactive_python_has_it():
    """Tests of the run guard assume SIGINT is handled, as in an interactive Python. A suite started
    in the background inherits it ignored, and the kits and children the tests start inherit that
    too (review 8, honesty-2); the ignored disposition is put back afterwards."""
    ignored = signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    if ignored:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    if ignored:
        signal.signal(signal.SIGINT, signal.SIG_IGN)


@pytest.fixture(autouse=True)
def isolated_trust_file(tmp_path, monkeypatch):
    """Projects created by tests must never be registered in the user's real trust file, and no
    test inherits a variable of the kit's old names (OLD_ENV) from the shell."""
    for name in OLD_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AELIX_ANALYTICS_TRUST_FILE", str(tmp_path / "trust" / "lab-projects.json"))


@pytest.fixture
def project(tmp_path):
    """A new project with data/raw/sales.parquet imported."""
    sys.path.insert(0, str(KIT))
    from aelix_analytics import core

    root = tmp_path / "proj"
    core.init_project(root, title="테스트 분석", goal="테스트 목표", template_dir=KIT / "aelix_analytics" / "template",
                      origin=core.Origin("test", "sdk"))
    source = write_sales_csv(tmp_path / "sales.csv")
    code, result = kit_cli(root, "import", source, "--name", "sales", "--description", "test data")
    assert code == 0 and result["ok"], result
    return root


def write_script(project: Path, name: str, body: str) -> str:
    # A user's file on purpose, in the platform's text mode; no constant-hash test uses it (ADR-0007).
    path = project / "scripts" / name
    path.write_text(body, encoding="utf-8")
    return f"scripts/{name}"
