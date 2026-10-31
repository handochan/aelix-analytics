"""What the release workflow (.github/workflows/release.yml) checks before it builds, and what it hands on.

    python tools/release_check.py version [--tag vX.Y.Z]     the numbers a release has to agree on
    python tools/release_check.py notes VERSION --output FILE    the changelog's section of that version
    python tools/release_check.py sums DIR --output FILE [--expect FILE]   SHA256SUMS of the files in DIR
    python tools/release_check.py pypi VERSION --sums FILE [--wait SECONDS]   PyPI holds those files

`version` reads pyproject.toml, KIT_VERSION, the aelix manifest, README's wheel names and the changelog,
and reports whether they name one version (and, with --tag, that the tag is `v` and that version), and
whether that version's changelog section, which becomes the release notes, links only full URLs. A
release publishes the candidate the release rehearsal measured (CONTRIBUTING.md's release steps):
`sums` writes the hashes of the wheel and sdist, refuses a directory that holds anything else, and
with --expect (docs/releases/<version>.sha256, the candidate's SHA256SUMS) refuses other bytes. `pypi`
reads PyPI's JSON API for the version until it lists exactly those files with those hashes, or the
wait runs out.

Prints one JSON object. Exit status: 0 everything agrees, 1 something does not (the object says what),
2 a usage error. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
NAME = "aelix_analytics"
KIT_VERSION_RE = re.compile(r'^KIT_VERSION = "([^"]+)"$', re.M)
SECTION_RE = re.compile(r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?$", re.M)


def versions(pack: Path = PACK) -> dict:
    """Every place that names the version, as written there."""
    project = tomllib.loads((pack / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core = (pack / "src" / NAME / "core.py").read_text(encoding="utf-8")
    kit = KIT_VERSION_RE.search(core)
    manifest = tomllib.loads((pack / "src" / NAME / "aelix-plugin.toml").read_text(encoding="utf-8"))
    readme = (pack / "README.md").read_text(encoding="utf-8")
    return {"pyproject": project["version"], "kit_version": kit[1] if kit else None,
            "manifest": manifest["plugin"]["version"],
            "readme_wheels": sorted(set(re.findall(rf"{NAME}-([^-\s]+)-py3-none-any\.whl", readme)))}


def changelog_sections(pack: Path = PACK) -> list[dict]:
    text = (pack / "CHANGELOG.md").read_text(encoding="utf-8")
    found = list(SECTION_RE.finditer(text))
    return [{"version": m[1], "date": m[2], "start": m.end(),
             "end": found[i + 1].start() if i + 1 < len(found) else len(text)}
            for i, m in enumerate(found)]


def check_version(tag: str | None, pack: Path = PACK) -> dict:
    seen = versions(pack)
    version = seen["pyproject"]
    problems = []
    for where in ("kit_version", "manifest"):
        if seen[where] != version:
            problems.append(f"{where} is {seen[where]!r}, pyproject.toml's is {version!r}")
    if seen["readme_wheels"] != [version]:
        problems.append(f"README names the wheels of {seen['readme_wheels']}, not only {version}")
    sections = {s["version"]: s for s in changelog_sections(pack)}
    if version not in sections:
        problems.append(f"CHANGELOG.md has no section [{version}]")
    elif sections[version]["date"] is None:
        problems.append(f"CHANGELOG.md's section [{version}] has no date")
    if version in sections:
        # The section becomes the GitHub Release's notes, where a link relative to the checkout leads nowhere.
        for target in relative_links(notes(version, pack)):
            problems.append(f"CHANGELOG.md's section [{version}] links {target!r}; the release notes need a full URL")
    if tag is not None and tag != f"v{version}":
        problems.append(f"the tag is {tag!r}, the version {version!r} wants 'v{version}'")
    return {"ok": not problems, "version": version, "tag": tag, "seen": seen, "problems": problems}


# Where a Markdown text links: inline links and images (with or without a title or angle brackets),
# reference definitions (not PowerShell's `[Console]::`) and HTML attributes. GitHub and PyPI follow all
# of them (Codex review of the 0.32.0 candidate: a titled link got past the first form of this check).
LINKS = (re.compile(r"\]\(\s*<?([^)\s>]+)"), re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?([^\s>:][^\s>]*)", re.M),
         re.compile(r"""\b(?:href|src)\s*=\s*["']([^"']+)""", re.I))
FULL_URL = re.compile(r"[a-z][a-z0-9+.-]*:", re.I)


def link_targets(text: str) -> list[str]:
    return [target for form in LINKS for target in form.findall(text)]


def relative_links(text: str) -> list[str]:
    """The targets that lead somewhere only beside the checkout's files."""
    return [target for target in link_targets(text) if not FULL_URL.match(target)]


def notes(version: str, pack: Path = PACK) -> str | None:
    text = (pack / "CHANGELOG.md").read_text(encoding="utf-8")
    for section in changelog_sections(pack):
        if section["version"] == version:
            return text[section["start"]:section["end"]].strip() + "\n"
    return None


def sums(directory: Path, version: str) -> tuple[str | None, list[str]]:
    """SHA256SUMS of exactly the wheel and the sdist of `version`, or the problems with `directory`.
    The `.gitignore` that `uv build -o` writes beside them (one `*`) is not a release file and is
    left out; any other file is a problem."""
    expected = {f"{NAME}-{version}-py3-none-any.whl", f"{NAME}-{version}.tar.gz"}
    present = {p.name for p in directory.iterdir()} if directory.is_dir() else set()
    if (directory / ".gitignore").is_file() and (directory / ".gitignore").read_bytes() == b"*":
        present.discard(".gitignore")
    problems = [f"missing {name}" for name in sorted(expected - present)]
    problems += [f"not a release file: {name}" for name in sorted(present - expected)]
    if problems:
        return None, problems
    lines = [f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}"
             for name in sorted(expected)]
    return "\n".join(lines) + "\n", []


def pypi_files(version: str, url: str) -> dict[str, str]:
    """What the index lists for the version: file name -> sha256."""
    import urllib.request

    with urllib.request.urlopen(url.format(version=version), timeout=60) as answer:
        listed = json.load(answer)
    return {f["filename"]: f["digests"]["sha256"] for f in listed.get("urls", [])}


def check_pypi(version: str, sums_text: str, url: str, wait: float, every: float = 15.0) -> dict:
    """Poll the index until it lists exactly the files of `sums_text`, by hash; PyPI's JSON API can
    lag an upload by a minute or so."""
    import time
    import urllib.error

    expected = {name: sha for sha, name in (line.split("  ", 1) for line in sums_text.splitlines())}
    deadline = time.monotonic() + wait
    while True:
        try:
            seen, error = pypi_files(version, url), None
        except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
            seen, error = {}, repr(exc)
        if seen == expected or time.monotonic() >= deadline:
            break
        time.sleep(every)
    problems = [] if seen == expected else [f"the index lists {seen or 'nothing'}, the release is {expected}"]
    if error and problems:
        problems.append(f"last read failed: {error}")
    return {"ok": not problems, "version": version, "url": url.format(version=version), "files": seen,
            "problems": problems}


PYPI_JSON = "https://pypi.org/pypi/aelix-analytics/{version}/json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a release's version, notes and files.")
    sub = parser.add_subparsers(dest="what", required=True)
    v = sub.add_parser("version", help="the numbers a release has to agree on")
    v.add_argument("--tag", help="the tag being released, e.g. v0.32.0")
    n = sub.add_parser("notes", help="the changelog's section of a version")
    n.add_argument("version")
    n.add_argument("--output", required=True, type=Path)
    s = sub.add_parser("sums", help="SHA256SUMS of the release files")
    s.add_argument("directory", type=Path)
    s.add_argument("--output", required=True, type=Path)
    s.add_argument("--expect", type=Path, help="the candidate's SHA256SUMS: refuse other bytes")
    q = sub.add_parser("pypi", help="the index lists exactly the release's files")
    q.add_argument("version")
    q.add_argument("--sums", required=True, type=Path)
    q.add_argument("--wait", type=float, default=0.0, help="seconds to keep asking (default: once)")
    q.add_argument("--url", default=PYPI_JSON, help="the JSON API's URL, {version} filled in")
    args = parser.parse_args(argv)
    if args.what == "version":
        result = check_version(args.tag)
    elif args.what == "notes":
        body = notes(args.version)
        result = {"ok": body is not None, "version": args.version, "output": str(args.output)}
        if body is None:
            result["problems"] = [f"CHANGELOG.md has no section [{args.version}]"]
        else:
            args.output.write_text(body, encoding="utf-8", newline="\n")
    elif args.what == "pypi":
        result = check_pypi(args.version, args.sums.read_text(encoding="utf-8"), args.url, args.wait)
    else:
        version = versions()["pyproject"]
        text, problems = sums(args.directory, version)
        if text is not None and args.expect is not None:
            recorded = args.expect.read_text(encoding="utf-8") if args.expect.is_file() else None
            if recorded is None:
                problems = [f"no candidate recorded: {args.expect} does not exist"]
            elif recorded != text:
                problems = [f"not the candidate {args.expect} records: built {text.split()}, recorded "
                            f"{recorded.split()}"]
        result = {"ok": not problems, "version": version, "output": str(args.output), "problems": problems}
        if text is not None and not problems:
            args.output.write_text(text, encoding="utf-8", newline="\n")
            result["files"] = [line.split("  ", 1)[1] for line in text.splitlines()]
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
