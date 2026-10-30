"""What the release workflow (.github/workflows/release.yml) checks before it builds, and what it hands on.

    python tools/release_check.py version [--tag vX.Y.Z]     the numbers a release has to agree on
    python tools/release_check.py notes VERSION --output FILE    the changelog's section of that version
    python tools/release_check.py sums DIR --output FILE      SHA256SUMS of the wheel and sdist in DIR

`version` reads pyproject.toml, KIT_VERSION, the aelix manifest, README's wheel names and the changelog,
and reports whether they name one version (and, with --tag, that the tag is `v` and that version). A
release publishes the files the workflow built once and verified (CONTRIBUTING.md's release steps),
so `sums` writes the hashes of those files and refuses a directory that holds anything else.

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
    if tag is not None and tag != f"v{version}":
        problems.append(f"the tag is {tag!r}, the version {version!r} wants 'v{version}'")
    return {"ok": not problems, "version": version, "tag": tag, "seen": seen, "problems": problems}


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
    else:
        version = versions()["pyproject"]
        text, problems = sums(args.directory, version)
        result = {"ok": not problems, "version": version, "output": str(args.output), "problems": problems}
        if text is not None:
            args.output.write_text(text, encoding="utf-8", newline="\n")
            result["files"] = [line.split("  ", 1)[1] for line in text.splitlines()]
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
