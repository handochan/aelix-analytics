"""The release workflow keeps CONTRIBUTING.md's release steps (Issue #24).

.github/workflows/release.yml is read as text, as tests/test_ci_workflow.py reads ci.yml. What this
keeps: every action pinned to a commit (the same commit where ci.yml uses that action too) and the same
uv; the check installing the files the build job made, on the three systems; and publishing only for a
tag, after that check, through the `pypi` environment's approval and Trusted Publishing, with no stored
secret.
"""
import re
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
RELEASE = (PACK / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
CI = (PACK / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
PIN = re.compile(r"uses: ([\w./-]+)@(\S+)(?: # (\S+))?")


def jobs(text: str) -> dict[str, str]:
    """Each job's lines, by its key under `jobs:`."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


JOBS = jobs(RELEASE)


def test_the_actions_are_pinned_to_the_commits_ci_uses():
    ci = {name: sha for name, sha, _ in PIN.findall(CI)}
    pins = PIN.findall(RELEASE)
    assert pins
    for name, sha, version in pins:
        assert re.fullmatch(r"[0-9a-f]{40}", sha) and version, (name, sha, version)
        if name in ci:
            assert sha == ci[name], name


def test_the_uv_is_the_ci_s():
    found = set(re.findall(r'version: "(\d+\.\d+\.\d+)"', RELEASE))
    assert found and found == set(re.findall(r'version: "(\d+\.\d+\.\d+)"', CI))


def test_the_check_installs_the_built_files_on_three_systems():
    assert list(JOBS) == ["build", "verify", "pypi", "github-release"]
    verify = JOBS["verify"]
    assert "os: [ubuntu-24.04, windows-2025, macos-26]" in verify
    assert "fetch-depth: 0" in verify and "needs: build" in verify
    assert 'AELIX_ANALYTICS_TEST_INSTALL: "1"' in verify
    assert "AELIX_ANALYTICS_TEST_DIST: ${{ runner.temp }}/release-dist" in verify
    assert "tests/test_packaging.py" in verify
    upload = JOBS["build"].split("name: release-dist", 1)[1].split("if-no-files-found", 1)[0]
    assert "dist/*.whl" in upload and "dist/*.tar.gz" in upload


def test_a_failed_check_in_a_pipeline_stops_the_step():
    """A `run: |` block that pipes a check into tee has to set pipefail, or tee's success hides the
    check's failure (independent review of PR #140, M3)."""
    blocks = re.findall(r"run: \|\n((?:\s{10}.*\n)+)", RELEASE)
    assert blocks
    for block in blocks:
        assert block.lstrip().startswith("set -euo pipefail\n"), block


def test_a_tag_publishes_the_measured_candidate_from_main():
    build = JOBS["build"]
    assert "fetch-depth: 0" in build
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in build
    assert "release_check.py version --tag \"$TAG\"" in build
    assert "format('docs/releases/{0}.sha256', steps.version.outputs.version)" in build
    assert '--expect "$EXPECT"' in build
    # A tag push sets both; a manual run on a tag ref sets neither.
    assert build.count("github.event_name == 'push' &&") == 2


def test_only_a_checked_tag_is_published_with_the_owner_s_approval():
    pypi, release = JOBS["pypi"], JOBS["github-release"]
    assert "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in pypi
    assert "skip-existing: true" in pypi
    upload = pypi.index("pypa/gh-action-pypi-publish@")
    assert pypi.index('release_check.py pypi "$VERSION" --sums SHA256SUMS') > upload
    assert "needs: [build, verify]" in pypi and "needs: [build, pypi]" in release
    assert "name: pypi\n" in pypi and "id-token: write" in pypi
    assert "contents: write" in release and "--verify-tag" in release
    assert "sha256sum --check ../SHA256SUMS" in release
    # Trusted Publishing: no token, password or secret anywhere in the workflow.
    assert not re.search(r"secrets\.|password|api[-_]token", RELEASE, re.I)
    assert "permissions:\n  contents: read" in RELEASE
