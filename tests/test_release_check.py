"""tools/release_check.py: what the release workflow checks before it builds, and the files it hands on."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PACK = Path(__file__).resolve().parents[1]
TOOL = PACK / "tools" / "release_check.py"
spec = importlib.util.spec_from_file_location("release_check", TOOL)
release_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_check)

VERSION = release_check.versions()["pyproject"]


def tool(*args: str, cwd: Path = PACK) -> tuple[int, dict]:
    done = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, encoding="utf-8",
                          cwd=cwd, timeout=120)
    return done.returncode, json.loads(done.stdout)


def pack_copy(tmp_path: Path) -> Path:
    """The files `version` reads, copied so a test can make them disagree."""
    root = tmp_path / "pack"
    for rel in ("pyproject.toml", "README.md", "CHANGELOG.md", "src/aelix_analytics/core.py",
                "src/aelix_analytics/aelix-plugin.toml"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACK / rel, root / rel)
    return root


def test_this_checkout_names_one_version():
    code, result = tool("version")
    assert code == 0 and result["ok"] is True, result
    seen = result["seen"]
    assert seen["pyproject"] == seen["kit_version"] == seen["manifest"] == VERSION
    assert seen["readme_wheels"] == [VERSION]


def test_a_tag_has_to_be_v_and_the_version():
    code, result = tool("version", "--tag", f"v{VERSION}")
    assert code == 0 and result["ok"] is True, result
    code, result = tool("version", "--tag", VERSION)
    assert code == 1 and result["ok"] is False
    assert result["problems"] == [f"the tag is {VERSION!r}, the version {VERSION!r} wants 'v{VERSION}'"]


@pytest.mark.parametrize("where", ["kit_version", "manifest", "readme", "changelog", "changelog_date"])
def test_any_place_that_names_another_version_fails(tmp_path, where):
    root = pack_copy(tmp_path)
    other = "9.9.9"

    def swap(rel: str, old: str, new: str) -> None:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        assert old in text
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    if where == "kit_version":
        swap("src/aelix_analytics/core.py", f'KIT_VERSION = "{VERSION}"', f'KIT_VERSION = "{other}"')
    elif where == "manifest":
        swap("src/aelix_analytics/aelix-plugin.toml", f'version = "{VERSION}"', f'version = "{other}"')
    elif where == "readme":
        swap("README.md", f"aelix_analytics-{VERSION}-py3-none-any.whl", f"aelix_analytics-{other}-py3-none-any.whl")
    elif where == "changelog":
        swap("CHANGELOG.md", f"## [{VERSION}]", f"## [{other}]")
    else:
        text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        text = re.sub(rf"^## \[{re.escape(VERSION)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", f"## [{VERSION}]",
                      text, count=1, flags=re.M)
        (root / "CHANGELOG.md").write_text(text, encoding="utf-8")
    result = release_check.check_version(None, root)
    assert result["ok"] is False and len(result["problems"]) == 1, result


def test_the_notes_are_the_changelog_section_and_nothing_after_it(tmp_path):
    code, result = tool("notes", VERSION, "--output", str(tmp_path / "notes.md"))
    assert code == 0, result
    body = (tmp_path / "notes.md").read_text(encoding="utf-8")
    text = (PACK / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index(f"## [{VERSION}]")
    following = text.index("\n## [", start + 1)
    assert body == text[start:following].split("\n", 1)[1].strip() + "\n"
    assert "## [" not in body
    code, result = tool("notes", "9.9.9", "--output", str(tmp_path / "none.md"))
    assert code == 1 and not (tmp_path / "none.md").exists()


def release_files(directory: Path, version: str = VERSION) -> dict[str, bytes]:
    directory.mkdir(parents=True, exist_ok=True)
    files = {f"aelix_analytics-{version}-py3-none-any.whl": b"wheel bytes",
             f"aelix_analytics-{version}.tar.gz": b"sdist bytes"}
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return files


def test_the_sums_name_the_two_files_by_their_hashes(tmp_path):
    files = release_files(tmp_path / "dist")
    (tmp_path / "dist" / ".gitignore").write_bytes(b"*")  # what `uv build -o` leaves beside them
    code, result = tool("sums", str(tmp_path / "dist"), "--output", str(tmp_path / "SHA256SUMS"))
    assert code == 0, result
    assert (tmp_path / "SHA256SUMS").read_bytes() == "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items())).encode()
    # The format `sha256sum --check` reads.
    assert result["files"] == sorted(files)


@pytest.mark.parametrize("change", ["extra", "missing", "other_version", "other_gitignore"])
def test_the_sums_refuse_a_directory_that_is_not_exactly_the_release(tmp_path, change):
    dist = tmp_path / "dist"
    files = release_files(dist)
    if change == "extra":
        (dist / "notes.txt").write_text("x", encoding="utf-8")
    elif change == "missing":
        (dist / sorted(files)[0]).unlink()
    elif change == "other_version":
        release_files(dist, "9.9.9")
    else:
        (dist / ".gitignore").write_text("*\n!keep\n", encoding="utf-8")
    code, result = tool("sums", str(dist), "--output", str(tmp_path / "SHA256SUMS"))
    assert code == 1 and result["problems"], result
    assert not (tmp_path / "SHA256SUMS").exists()


@pytest.mark.parametrize("recorded", ["same", "other", "absent"])
def test_a_tag_s_files_have_to_be_the_recorded_candidate(tmp_path, recorded):
    files = release_files(tmp_path / "dist")
    candidate = tmp_path / f"{VERSION}.sha256"
    text = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items()))
    if recorded == "same":
        candidate.write_text(text, encoding="utf-8")
    elif recorded == "other":
        candidate.write_text(text.replace(text[:8], "00000000", 1), encoding="utf-8")
    code, result = tool("sums", str(tmp_path / "dist"), "--output", str(tmp_path / "SHA256SUMS"),
                        "--expect", str(candidate))
    if recorded == "same":
        assert code == 0 and (tmp_path / "SHA256SUMS").read_text(encoding="utf-8") == text, result
    else:
        assert code == 1 and result["problems"], result
        assert not (tmp_path / "SHA256SUMS").exists()


def pypi_answer(directory: Path, version: str, files: dict[str, str]) -> str:
    """A JSON API answer on disk, read through a file: URL in the index's place."""
    (directory / f"{version}.json").write_text(json.dumps(
        {"urls": [{"filename": name, "digests": {"sha256": sha}} for name, sha in files.items()]}),
        encoding="utf-8")
    return (directory.as_uri() + "/{version}.json")


@pytest.mark.parametrize("listed", ["same", "other_hash", "one_missing", "absent"])
def test_pypi_has_to_list_exactly_the_release_s_files(tmp_path, listed):
    files = {name: hashlib.sha256(data).hexdigest() for name, data in release_files(tmp_path / "dist").items()}
    sums = tmp_path / "SHA256SUMS"
    sums.write_text("".join(f"{sha}  {name}\n" for name, sha in sorted(files.items())), encoding="utf-8")
    answer = tmp_path / "answer"
    answer.mkdir()
    if listed == "same":
        url = pypi_answer(answer, VERSION, files)
    elif listed == "other_hash":
        url = pypi_answer(answer, VERSION, {**files, sorted(files)[0]: "0" * 64})
    elif listed == "one_missing":
        url = pypi_answer(answer, VERSION, dict(sorted(files.items())[:1]))
    else:
        url = answer.as_uri() + "/{version}.json"
    code, result = tool("pypi", VERSION, "--sums", str(sums), "--url", url)
    assert (code == 0) is (listed == "same"), result
    if listed == "absent":
        assert result["files"] == {} and result["problems"], result
