"""Check repository documentation offline and retain exact input/evidence metadata.

Run with the repository's locked development environment. Python examples are parsed,
not executed. External URLs, their fragments, and rendered browser behavior are not checked.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import re
import shlex
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from html.parser import HTMLParser
from importlib.metadata import version
from pathlib import Path
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from markdown_it import MarkdownIt
from referencing.exceptions import Unresolvable

STATE_SCHEMA = "docs/schemas/dev-state.schema.json"
STATE_FAMILY = "aelix-analytics.dev-state/1"
STATE_EXAMPLE = "docs/templates/WORK-STATE.example.json"
MARKDOWN = MarkdownIt("commonmark").enable("table")
# A drive path (C:\x, C:/x, and C:x, relative to that drive's current directory), or one that
# starts at a backslash (\x, a UNC \\server\share), as a Windows checkout writes them. Matched after
# percent-decoding: the Markdown parser encodes a backslash in a link destination as %5C. No URL
# scheme is one letter long.
WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:|\\)")


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_json(text: str):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"non-JSON numeric constant: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def require_local_schema_refs(value):
    """Keep schema example validation offline, even when a schema is edited in a PR."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "$dynamicRef"} and child and not child.startswith("#"):
                raise ValueError(f"nonlocal schema reference is not supported offline: {child}")
            require_local_schema_refs(child)
    elif isinstance(value, list):
        for child in value:
            require_local_schema_refs(child)


class HTMLReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("name"):
            self.ids.add(attrs["name"])
        key = "href" if tag == "a" else "src" if tag == "img" else None
        if key and attrs.get(key):
            self.links.append(attrs[key])


def heading_ids(tokens):
    result = set()
    generated = set()
    for i, token in enumerate(tokens):
        if token.type == "heading_open":
            text = "".join(t.content for t in tokens[i + 1].children or []
                           if t.type in {"text", "code_inline", "image"})
            base = re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")
            slug, number = base, 0
            while slug in generated:
                number += 1
                slug = f"{base}-{number}"
            generated.add(slug)
            result.add(slug)
        html = []
        if token.type == "html_block":
            html.append(token.content)
        html.extend(t.content for t in token.children or [] if t.type == "html_inline")
        for value in html:
            parser = HTMLReferences()
            parser.feed(value)
            result.update(parser.ids)
    return result


class Check:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.tracked = self.names("ls-files", "--cached", "-z")
        self.selected = self.tracked | self.names("ls-files", "--others", "--exclude-standard", "-z")
        self.inputs = {}
        self.data = {}
        self.tokens = {}
        self.errors = []
        self.counts = Counter()
        self.external_links = []
        self.unchecked_fragments = []
        self.deleted = []

    def names(self, *args):
        return {p.decode("utf-8") for p in git(self.root, *args).split(b"\0") if p}

    def error(self, path, message, line=None):
        self.errors.append({"path": path, "line": line, "message": message})

    def read(self, rel):
        if rel in self.data:
            return self.data[rel]
        path = self.root / rel
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("symlink or path outside the repository is not portable")
        data = path.read_bytes()
        self.inputs[rel] = {"path": rel, "sha256": digest(data), "bytes": len(data),
                            "tracked": rel in self.tracked}
        self.data[rel] = data
        return data

    def markdown(self, rel):
        if rel not in self.tokens:
            self.tokens[rel] = MARKDOWN.parse(self.read(rel).decode("utf-8"))
        return self.tokens[rel]

    def link(self, rel, target, line):
        # A Windows path would otherwise read as a URL: urlsplit takes `C:` for a scheme.
        if WINDOWS_ABSOLUTE.match(unquote(target)):
            self.counts["local_links"] += 1
            self.error(rel, f"absolute filesystem link is not portable: {target}", line)
            return
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            self.external_links.append({"source": rel, "line": line, "target": target})
            return
        self.counts["local_links"] += 1
        if Path(unquote(parsed.path)).is_absolute():
            self.error(rel, f"absolute filesystem link is not portable: {target}", line)
            return
        path = self.root / rel if not parsed.path else (self.root / rel).parent / unquote(parsed.path)
        if not path.resolve().is_relative_to(self.root):
            self.error(rel, f"link escapes repository: {target}", line)
            return
        name = path.resolve().relative_to(self.root).as_posix()
        if path.is_dir():
            prefix = "" if name == "." else name + "/"
            if not any(p.startswith(prefix) for p in self.selected):
                self.error(rel, f"directory has no Git-visible content: {target}", line)
            return
        if name not in self.selected:
            self.error(rel, f"link target is missing or ignored: {target}", line)
            return
        try:
            self.read(name)
            if parsed.fragment and path.suffix.lower() == ".md":
                if unquote(parsed.fragment) not in heading_ids(self.markdown(name)):
                    self.error(rel, f"missing Markdown anchor: {target}", line)
            elif parsed.fragment:
                self.unchecked_fragments.append({"source": rel, "target": target})
        except (OSError, UnicodeError, ValueError) as exc:
            self.error(rel, f"cannot read link {target}: {exc}", line)

    def check_markdown(self, rel):
        tokens = self.markdown(rel)
        lines = self.read(rel).decode("utf-8").splitlines()
        self.counts["markdown_files"] += 1
        for token in tokens:
            line = token.map[0] + 1 if token.map else None
            if token.type == "fence":
                start, end = token.map
                last = re.sub(r"^[\s>]*", "", lines[end - 1])
                closing = re.fullmatch(re.escape(token.markup[0]) + "{" + str(len(token.markup)) + r",}\s*", last)
                if end - 1 <= start or not closing:
                    self.error(rel, "unclosed fenced code block", line)
                language = token.info.split()[0] if token.info.strip() else ""
                try:
                    if language == "json":
                        self.counts["json_examples"] += 1
                        strict_json(token.content)
                    elif language in {"python", "py"}:
                        self.counts["python_examples"] += 1
                        ast.parse(token.content, filename=rel)
                except (ValueError, SyntaxError) as exc:
                    self.error(rel, f"invalid {language} example: {exc}", line)
            children = token.children or []
            for child in children:
                if child.type == "link_open":
                    self.link(rel, child.attrGet("href"), line)
                elif child.type == "image":
                    self.link(rel, child.attrGet("src"), line)
            for html in ([token.content] if token.type == "html_block" else []) + [
                    t.content for t in children if t.type == "html_inline"]:
                parser = HTMLReferences()
                parser.feed(html)
                for target in parser.links:
                    self.link(rel, target, line)

    def check_json(self, rel):
        value = strict_json(self.read(rel).decode("utf-8"))
        self.counts["json_files"] += 1
        if rel == STATE_EXAMPLE and (not isinstance(value, dict)
                                     or value.get("schema") != STATE_FAMILY):
            raise ValueError("unsupported schema in the development-state example")
        if isinstance(value, dict) and "$schema" in value:
            Draft202012Validator.check_schema(value)
        if isinstance(value, dict) and value.get("schema") == STATE_FAMILY:
            schema = strict_json(self.read(STATE_SCHEMA).decode("utf-8"))
            Draft202012Validator.check_schema(schema)
            require_local_schema_refs(schema)
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            self.counts["dev_state_examples"] += 1
            for error in validator.iter_errors(value):
                field = "/".join(str(p) for p in error.absolute_path)
                self.error(rel, f"state {field or '/'}: {error.message}")

    def run(self):
        source = self.source()
        for rel in sorted(self.selected):
            is_json = rel.endswith(".json") and rel.startswith(("docs/", ".github/"))
            if not (rel.endswith(".md") or is_json):
                continue
            if not (self.root / rel).exists() and rel in self.tracked:
                self.deleted.append(rel)
                continue
            try:
                if is_json:
                    self.check_json(rel)
                else:
                    self.check_markdown(rel)
            except (OSError, UnicodeError, ValueError, SchemaError, Unresolvable) as exc:
                self.error(rel, str(exc))
        for rel in ["pyproject.toml", "uv.lock"]:
            if rel in self.selected and (self.root / rel).exists():
                self.read(rel)
        for rel, data in self.data.items():
            if not (self.root / rel).is_file() or (self.root / rel).read_bytes() != data:
                self.error(rel, "input changed during validation; rerun the check")
        if source != self.source() or self.selected != (
                self.names("ls-files", "--cached", "-z")
                | self.names("ls-files", "--others", "--exclude-standard", "-z")):
            self.error(".", "Git source or file selection changed during validation; rerun")
        inputs = sorted(self.inputs.values(), key=lambda item: item["path"])
        return {
            "schema": "aelix-analytics.doc-check/1",
            "ok": not self.errors,
            "checked_at": datetime.now(UTC).isoformat(),
            "source": source,
            "selection": "Git tracked plus non-ignored untracked Markdown anywhere; JSON under docs/ and .github/",
            "deleted_documents": self.deleted,
            "files": inputs,
            "input_snapshot_sha256": digest(json.dumps(inputs, sort_keys=True).encode()),
            "counts": dict(self.counts),
            "errors": self.errors,
            "not_checked": {"external_links": self.external_links,
                            "non_markdown_fragments": self.unchecked_fragments,
                            "execution": ["Python examples", "browser rendering", "product E2E"]},
        }

    def source(self):
        return {"head": git(self.root, "rev-parse", "HEAD").decode().strip(),
                "dirty": bool(git(self.root, "status", "--porcelain")),
                "tracked_diff_sha256": digest(git(self.root, "diff", "--binary", "HEAD"))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="JSON evidence inside the root's ignored .devstate/")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = args.output.resolve() if args.output else None
    if output:
        if not output.is_relative_to(root / ".devstate"):
            parser.error("--output must be inside this worktree's .devstate/")
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(output)])
        if ignored.returncode != 0:
            parser.error("--output must be ignored by Git and must not replace a tracked input")
        if output.exists():
            try:
                previous = strict_json(output.read_text(encoding="utf-8"))
                if not isinstance(previous, dict) or previous.get("schema") != "aelix-analytics.doc-check/1":
                    raise ValueError("not a documentation-check report")
            except (OSError, ValueError) as exc:
                parser.error(f"refusing to overwrite existing non-report file: {exc}")
    try:
        result = Check(root).run()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"schema": "aelix-analytics.doc-check/1", "ok": False,
                  "errors": [{"message": str(exc)}]}
    actual_argv = [sys.executable, str(Path(__file__).resolve()),
                   *(sys.argv[1:] if argv is None else argv)]
    result["invocation"] = {"argv": actual_argv, "command": shlex.join(actual_argv),
                            "cwd": str(Path.cwd()), "root": str(root)}
    result["checker"] = {"path": str(Path(__file__).resolve()),
                         "sha256": digest(Path(__file__).read_bytes()), "python": platform.python_version(),
                         "markdown_it": version("markdown-it-py"), "jsonschema": version("jsonschema")}
    value = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".docs-", suffix=".tmp", delete=False) as stream:
            stream.write(value)
            temporary = Path(stream.name)
        try:
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    if output:
        print(json.dumps({"ok": result["ok"], "counts": result.get("counts", {}),
                          "errors": result["errors"], "report": str(output)}, ensure_ascii=False))
    else:
        print(value, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    # The report holds paths and messages in Korean; a pipe or a console otherwise follows the
    # locale (cp1252 on the Windows runner), where they cannot be written.
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
