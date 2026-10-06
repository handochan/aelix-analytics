"""The layers of the shipped code and the direction of its imports (ADR-0001, Issue #43).

Every Python file the distribution ships — the package `src/aelix_analytics` and the aelix extension in
`extension/` — is read with `ast`. Every import in it is judged against the layer table below: at module
level, inside a function or a method, under `if`, `try` or `with`, and under `if TYPE_CHECKING:`. So is
every dynamic load (`spec_from_file_location`, `import_module`, …), which has to be declared with what
it loads. The table is the one in docs/ARCHITECTURE.md section 11; a test below keeps the two the same.

The rules, by the name a failure gives:

- registration: every shipped `.py` is in MODULES, and every entry of MODULES is a shipped file.
- layer-direction: a module imports a module of the package only when the importing layer may import
  that layer (LAYERS). A lower layer importing a higher one, two sibling layers importing each other,
  and the host side importing the project environment's layers all fail, unless ALLOWLIST lists the
  edge with why it exists and when it goes.
- external-library: a module imports a library outside the package only when its layer may use that
  library's category (EXTERNAL; the standard library is `sys.stdlib_module_names`). A third-party
  module no category lists fails too.
- import-time: the module level of IMPORT_TIME_STDLIB_ONLY's files runs where nothing but the standard
  library may be imported (aelix imports the package when it loads the installed extension).
- dynamic-load: a dynamic load is declared in DYNAMIC_LOADS, and what it loads is judged like an import.
- allowlist: each ALLOWLIST entry says why and when it is removed, is still imported, and is an edge
  the table does not allow already.

A new module is registered in MODULES with its layer, and in the document's table, in the PR that adds
it (ADR-0001 "배치"). What this does not see: code built from strings and run with `exec`, resource
reads, and which modules a process really ends up holding — tests/test_platform.py checks the aelix
process's `sys.modules` at run time.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

PACK = Path(__file__).resolve().parents[1]
# What the distribution ships as Python (pyproject.toml: the package and the extension).
SHIPPED_DIRS = ("src/aelix_analytics", "extension")
PACKAGE = "aelix_analytics"
# The extension's checkout file is installed as this module (pyproject.toml, ADR-0004).
EXTENSION_FILE = "extension/aelix_analytics.py"
EXTENSION_MODULE = f"{PACKAGE}.extension"
ARCHITECTURE = PACK / "docs" / "ARCHITECTURE.md"


# --- the table ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Layer:
    level: int
    may_import: frozenset[str]
    external: frozenset[str]  # categories of EXTERNAL, and "stdlib"
    role: str


def _layer(level: int, may_import: tuple[str, ...], external: tuple[str, ...], role: str) -> Layer:
    return Layer(level, frozenset(may_import), frozenset(("stdlib", *external)), role)


PROJECT_LAYERS = ("core", "ports", "read_model", "runtime", "workspace")

LAYERS: dict[str, Layer] = {
    "ports": _layer(0, (), (), "the platform ports and their per-OS implementations (ADR-0003)"),
    "read_model": _layer(0, (), (), "the records read as ADR-0006 says (ADR-0001 decision 5)"),
    "distribution": _layer(0, (), (), "where the installed distribution keeps its runtime root"),
    "core": _layer(1, ("ports", "read_model"), (),
                   "the project model: domain and shared application, not split yet"),
    "runtime": _layer(2, ("core", "ports", "read_model", "runtime"), ("analysis",),
                      "execution, capture and data adapters, in the project's environment"),
    "workspace": _layer(2, ("core", "ports", "read_model"), ("analysis", "rendering"),
                        "the Workspace renderer, in the project's environment"),
    "sdk": _layer(3, PROJECT_LAYERS, ("analysis",), "the script API: an entry point"),
    "kit_entry": _layer(3, PROJECT_LAYERS, ("analysis",),
                        "the kit's command line in the project's environment: an entry point"),
    "launcher": _layer(3, ("distribution", "ports"), (),
                       "the human `analytics` command, standard library only (3.11)"),
    "host_adapter": _layer(3, ("distribution", "core", "ports", "read_model"), ("host",),
                           "the aelix extension, inside the aelix process"),
    "user_scripts": _layer(4, ("sdk",), ("analysis",),
                           "example analysis scripts copied into a project as the user's files"),
}

# Libraries outside the package, by top-level name. A third-party module that is not here fails the
# check: a new dependency is classified before a layer may use it.
EXTERNAL: dict[str, str] = {
    **dict.fromkeys(("polars", "duckdb", "numpy", "pyarrow", "pandas", "matplotlib", "plotly"),
                    "analysis"),
    **dict.fromkeys(("pygments", "markdown_it"), "rendering"),
    **dict.fromkeys(("aelix", "aelix_agent_core", "aelix_ai", "aelix_coding_agent"), "host"),
}

# Every shipped Python file, by its path in the checkout, and its layer. A key ending in `/*.py` covers
# every file of that directory.
MODULES: dict[str, str] = {
    "src/aelix_analytics/_platform.py": "ports",
    "src/aelix_analytics/relations.py": "read_model",
    "src/aelix_analytics/_install.py": "distribution",
    "src/aelix_analytics/core.py": "core",
    "src/aelix_analytics/capture.py": "runtime",
    "src/aelix_analytics/data.py": "runtime",
    "src/aelix_analytics/demo.py": "runtime",
    "src/aelix_analytics/runner.py": "runtime",
    "src/aelix_analytics/report.py": "workspace",
    "src/aelix_analytics/__init__.py": "sdk",
    "src/aelix_analytics/__main__.py": "kit_entry",
    "src/aelix_analytics/cli.py": "launcher",
    "extension/aelix_analytics.py": "host_adapter",
    "src/aelix_analytics/demo_scripts/*.py": "user_scripts",
}


@dataclass(frozen=True)
class Allowed:
    why: str
    remove_when: str


# Imports the table does not allow and the code still has, each with why and when it goes. Keyed by
# (importing file, imported file).
ALLOWLIST: dict[tuple[str, str], Allowed] = {
    ("src/aelix_analytics/runner.py", "src/aelix_analytics/report.py"): Allowed(
        why="runner._finalize rebuilds the Workspace when a run ends and puts it in the run's result "
            "(a failure is report_error).",
        remove_when="the Workspace refresh after a run is called by the entry point (__main__, the "
                    "extension) or by the follow-up step of the common write path "
                    "(docs/ARCHITECTURE.md section 7) instead of by runner."),
    ("src/aelix_analytics/report.py", "src/aelix_analytics/runner.py"): Allowed(
        why="report.fingerprint puts the kit's hash, runner.kit_sha(), into the Workspace's "
            "fingerprint.",
        remove_when="kit_sha moves to a layer both may import (core, or a module below it)."),
    ("src/aelix_analytics/report.py", "src/aelix_analytics/data.py"): Allowed(
        why="the dataset page profiles a dataset with data.profile, and Site.frame_preview formats "
            "cells with data.cell.",
        remove_when="the Workspace gets dataset profiles and cell formatting from a read model or "
                    "from the entry point, not from the data adapter."),
    ("src/aelix_analytics/demo.py", "src/aelix_analytics/report.py"): Allowed(
        why="demo.populate builds the Workspace at its end and returns it.",
        remove_when="the entry point's demo command builds the Workspace after populate, as it does "
                    "after import and record (__main__._safe_report)."),
}


@dataclass(frozen=True)
class DynamicLoad:
    target: str | None  # the file it loads, judged like an import; None: it loads nothing of its own
    why: str


# Calls that load a module without an import statement, by (file, enclosing function, call).
DYNAMIC_CALLS = frozenset({"__import__", "import_module", "spec_from_file_location",
                           "spec_from_loader", "find_spec", "SourceFileLoader", "run_module",
                           "run_path"})
DYNAMIC_LOADS: dict[tuple[str, str, str], DynamicLoad] = {
    ("src/aelix_analytics/cli.py", "_load_platform", "spec_from_file_location"): DynamicLoad(
        "src/aelix_analytics/_platform.py",
        "the launcher loads the ports from the runtime root by file, as the extension does (ADR-0003)"),
    ("extension/aelix_analytics.py", "_load_core", "spec_from_file_location"): DynamicLoad(
        "src/aelix_analytics/core.py",
        "the aelix process loads core (and through it _platform and relations) under a synthetic "
        "package (ADR-0003 decision 3)"),
    ("src/aelix_analytics/capture.py", "PostImportFinder.find_spec", "find_spec"): DynamicLoad(
        None, "the import hook finds the module the script itself imports; it adds no module"),
}

# Files whose module level runs where only the standard library may be imported.
IMPORT_TIME_STDLIB_ONLY: dict[str, str] = {
    "src/aelix_analytics/__init__.py":
        "aelix imports the package when it loads the installed extension, and `analytics` when it "
        "starts: its module level imports the standard library only and no module of the kit "
        "(ADR-0004; tests/test_platform.py::test_importing_the_package_imports_nothing_of_the_kit)",
}


# --- reading the imports -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Found:
    """One import, or one dynamic load (`call` set), in a module."""

    path: str
    line: int
    scope: str  # the enclosing function's qualified name, or "" at module level
    context: tuple[str, ...]  # "if", "try", "with", "loop", "match", "type-only", outermost first
    target: str  # the module imported, dotted (for a dynamic load, the call's text)
    call: str | None = None
    at_import: bool = True  # runs when the module itself is imported

    def where(self) -> str:
        parts = [f"in {self.scope}" if self.scope else "at module level"]
        parts += [c if c == "type-only" else f"inside {c}" for c in dict.fromkeys(self.context)]
        return f"{self.path}:{self.line} ({', '.join(parts)})"


def module_name(path: str) -> str | None:
    """The dotted name a shipped file is imported as, or None for a file nothing imports (a script
    whose name is not an identifier)."""
    if path == EXTENSION_FILE:
        return EXTENSION_MODULE
    pure = PurePosixPath(path)
    if not pure.is_relative_to("src") or pure.suffix != ".py":
        return None
    parts = list(pure.relative_to("src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if all(p.isidentifier() for p in parts) else None


def _package_of(path: str, name: str | None) -> str | None:
    if name is None:
        return None
    return name if path.endswith("/__init__.py") else name.rpartition(".")[0] or None


def _type_checking(test: ast.expr) -> bool:
    return ((isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
            or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"))


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


# The statements whose body is inside a block, by what the block is called in a message.
BLOCKS = {ast.Try: "try", ast.TryStar: "try", ast.With: "with", ast.AsyncWith: "with",
          ast.For: "loop", ast.AsyncFor: "loop", ast.While: "loop", ast.Match: "match"}


class _Reader:
    def __init__(self, path: str, internal: frozenset[str]) -> None:
        self.path = path
        self.name = module_name(path)
        self.package = _package_of(path, self.name)
        self.internal = internal
        self.found: list[Found] = []
        self.unresolved: list[tuple[int, str]] = []

    def read(self, tree: ast.Module) -> list[Found]:
        for node in tree.body:
            self._visit(node, (), (), False, False)
        return self.found

    def _add(self, node, scope, context, in_function, type_only, target, call=None) -> None:
        self.found.append(Found(self.path, node.lineno, ".".join(scope), context, target, call,
                                at_import=not in_function and not type_only))

    def _from_base(self, node: ast.ImportFrom) -> str | None:
        if node.level == 0:
            return node.module
        if self.package is None:
            return None
        parts = self.package.split(".")
        if node.level - 1 >= len(parts):
            return None
        base = ".".join(parts[:len(parts) - (node.level - 1)])
        return f"{base}.{node.module}" if node.module else base

    def _visit(self, node, scope, context, in_function, type_only) -> None:
        visit = self._visit
        if isinstance(node, ast.Import):
            for alias in node.names:
                self._add(node, scope, context, in_function, type_only, alias.name)
            return
        if isinstance(node, ast.ImportFrom):
            base = self._from_base(node)
            if base is None:
                self.unresolved.append((node.lineno, "." * node.level + (node.module or "")))
                return
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                target = candidate if candidate in self.internal else base
                self._add(node, scope, context, in_function, type_only, target)
            return
        if isinstance(node, ast.Call):
            callee = _callee(node)
            if callee in DYNAMIC_CALLS:
                self._add(node, scope, context, in_function, type_only, ast.unparse(node)[:80],
                          call=callee)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in (*node.decorator_list, node.args, node.returns):
                if sub is not None:
                    visit(sub, scope, context, in_function, type_only)
            for sub in node.body:
                visit(sub, (*scope, node.name), context, True, type_only)
            return
        if isinstance(node, ast.Lambda):
            visit(node.args, scope, context, in_function, type_only)
            visit(node.body, (*scope, "<lambda>"), context, True, type_only)
            return
        if isinstance(node, ast.ClassDef):
            for sub in (*node.decorator_list, *node.bases, *node.keywords):
                visit(sub, scope, context, in_function, type_only)
            for sub in node.body:
                visit(sub, (*scope, node.name), context, in_function, type_only)
            return
        if isinstance(node, ast.If):
            visit(node.test, scope, context, in_function, type_only)
            guarded = _type_checking(node.test)
            for sub in node.body:
                visit(sub, scope, (*context, "type-only" if guarded else "if"), in_function,
                      type_only or guarded)
            for sub in node.orelse:
                visit(sub, scope, (*context, "if"), in_function, type_only)
            return
        kind = BLOCKS.get(type(node))
        inner = (*context, kind) if kind else context
        for field_name, value in ast.iter_fields(node):
            # A block's statements are inside it; its header (a with's items, a loop's iterable, an
            # if's test) is evaluated where the block is.
            nested = kind and field_name in ("body", "handlers", "orelse", "finalbody", "cases")
            for sub in value if isinstance(value, list) else [value]:
                if isinstance(sub, ast.AST):
                    visit(sub, scope, inner if nested else context, in_function, type_only)


def read_imports(path: str, source: str, internal: frozenset[str]) -> tuple[list[Found], list]:
    reader = _Reader(path, internal)
    return reader.read(ast.parse(source, filename=path)), reader.unresolved


# --- the check -----------------------------------------------------------------------------------

def _layer_of(path: str, modules: dict[str, str]) -> str | None:
    if path in modules:
        return modules[path]
    for key, layer in modules.items():
        if key.endswith("/*.py") and fnmatch.fnmatchcase(path, key):
            return layer
    return None


def _category(top: str, external: dict[str, str]) -> str | None:
    return "stdlib" if top in sys.stdlib_module_names else external.get(top)


def _edge_reason(a: str, b: str, layers: dict[str, Layer]) -> str:
    la, lb = layers[a].level, layers[b].level
    if lb > la:
        return f"a lower layer imports a higher one ({a} is level {la}, {b} level {lb})"
    if lb == la:
        return (f"{a} and {b} are sibling layers (level {la}): an entry point assembles them, and "
                "neither imports the other")
    return f"{a} does not depend on {b}"


def _allowed(a: str, b: str, layers: dict[str, Layer]) -> bool:
    return b in layers[a].may_import


def check(sources: dict[str, str], *, modules: dict[str, str] = MODULES,
          layers: dict[str, Layer] = LAYERS, external: dict[str, str] = EXTERNAL,
          allowlist: dict[tuple[str, str], Allowed] = ALLOWLIST,
          dynamic: dict[tuple[str, str, str], DynamicLoad] = DYNAMIC_LOADS,
          import_time: dict[str, str] = IMPORT_TIME_STDLIB_ONLY) -> list[str]:
    """Every rule broken by `sources` ({path in the checkout: source}), one message each."""
    out: list[str] = []
    layer_of = {path: _layer_of(path, modules) for path in sources}
    for path, layer in sorted(layer_of.items()):
        if layer is None:
            out.append(f"{path} is in no layer: breaks rule registration — register it in MODULES "
                       "(tests/test_layers.py) and in the table of docs/ARCHITECTURE.md section 11, "
                       "in the PR that adds it")
        elif layer not in layers:
            out.append(f"{path} is registered in layer {layer!r}, which LAYERS does not have: breaks "
                       "rule registration")
    for key in modules:
        if not any(p == key or (key.endswith("/*.py") and fnmatch.fnmatchcase(p, key))
                   for p in sources):
            out.append(f"MODULES registers {key}, which is not shipped: breaks rule registration — "
                       "remove the entry")
    names = {path: module_name(path) for path in sources}
    by_name = {name: path for path, name in names.items() if name}
    internal = frozenset(by_name)
    used_allowances: set[tuple[str, str]] = set()
    used_loads: set[tuple[str, str, str]] = set()

    def judge_edge(found: Found, a_path: str, b_path: str, shown: str) -> None:
        a, b = layer_of[a_path], layer_of.get(b_path)
        if a not in layers or b not in layers or a_path == b_path or _allowed(a, b, layers):
            return
        if (a_path, b_path) in allowlist:
            used_allowances.add((a_path, b_path))
            return
        may = ", ".join(sorted(layers[a].may_import)) or "no layer of the package"
        out.append(f"{found.where()} imports {shown}: {a} → {b} ({a_path} → {b_path}) breaks rule "
                   f"layer-direction — {_edge_reason(a, b, layers)}. {a} may import: {may}. Invert "
                   "it (an entry point passes in what is needed), move the code, or add an "
                   "ALLOWLIST entry with why and its removal condition")

    for path, source in sorted(sources.items()):
        layer = layer_of[path]
        if layer not in layers:
            continue
        found_all, unresolved = read_imports(path, source, internal)
        for line, text in unresolved:
            out.append(f"{path}:{line} imports {text}, a relative import that resolves to no "
                       "package: breaks rule registration")
        for found in found_all:
            if found.call is not None:
                key = (path, found.scope or "<module>", found.call)
                declared = dynamic.get(key)
                if declared is None:
                    out.append(f"{found.where()} loads a module dynamically ({found.target}): "
                               "breaks rule dynamic-load — declare it in DYNAMIC_LOADS with the "
                               "file it loads, so the edge is checked like an import")
                    continue
                used_loads.add(key)
                if declared.target is not None and declared.target not in layer_of:
                    out.append(f"{found.where()} is declared to load {declared.target}, which is "
                               "not shipped: breaks rule dynamic-load — correct DYNAMIC_LOADS")
                elif declared.target is not None:
                    judge_edge(found, path, declared.target, f"{declared.target} (dynamically)")
                continue
            top = found.target.split(".")[0]
            if top == PACKAGE:
                # `import a.b` names a module; `from a.b import c` is the module a.b unless a.b.c is
                # one (the reader decided which), so the target is a module name as it stands.
                if found.target not in by_name:
                    out.append(f"{found.where()} imports {found.target}, which is no module of the "
                               "package: breaks rule registration")
                    continue
                if path in import_time and found.at_import:
                    out.append(f"{found.where()} imports {found.target} when the module is "
                               f"imported: breaks rule import-time — {import_time[path]}")
                    continue
                judge_edge(found, path, by_name[found.target], found.target)
                continue
            category = _category(top, external)
            if category is None:
                out.append(f"{found.where()} imports {found.target}, a third-party module no "
                           "category lists: breaks rule external-library — classify it in "
                           "EXTERNAL (analysis, rendering, host, or a new category in the "
                           "document's table) before a layer uses it")
                continue
            if path in import_time and found.at_import and category != "stdlib":
                out.append(f"{found.where()} imports {found.target} ({category}) when the module is "
                           f"imported: breaks rule import-time — {import_time[path]}")
                continue
            if category not in layers[layer].external:
                cats = ", ".join(sorted(layers[layer].external))
                out.append(f"{found.where()} imports {found.target}: {layer} → {category} library "
                           f"breaks rule external-library — {layer} may use: {cats}")

    for (a_path, b_path), allowed in sorted(allowlist.items()):
        edge = f"ALLOWLIST entry {a_path} → {b_path}"
        if not allowed.why.strip() or not allowed.remove_when.strip():
            out.append(f"{edge} lacks why or its removal condition: breaks rule allowlist — every "
                       "exception says why it exists and when it is removed")
        a, b = layer_of.get(a_path), layer_of.get(b_path)
        if a in layers and b in layers and _allowed(a, b, layers):
            out.append(f"{edge} is an edge the table allows ({a} → {b}): breaks rule allowlist — "
                       "remove the entry")
        elif (a_path, b_path) not in used_allowances:
            out.append(f"{edge} is no longer imported: breaks rule allowlist — its removal "
                       f"condition was met ({allowed.remove_when!r}); remove the entry")
    for key in sorted(set(dynamic) - used_loads):
        out.append(f"DYNAMIC_LOADS declares {key}, which the code no longer calls: breaks rule "
                   "dynamic-load — remove the declaration")
    return out


def shipped_paths(root: Path = PACK) -> list[str]:
    paths = []
    for directory in SHIPPED_DIRS:
        base = root / directory
        assert base.is_dir(), f"the shipped directory {directory} moved"
        paths += [p.relative_to(root).as_posix() for p in sorted(base.rglob("*.py"))
                  if "__pycache__" not in p.parts]
    return paths


def shipped_sources(root: Path = PACK) -> dict[str, str]:
    return {path: (root / path).read_text(encoding="utf-8") for path in shipped_paths(root)}


# --- the shipped code ------------------------------------------------------------------------------

def test_the_shipped_code_keeps_its_layers():
    sources = shipped_sources()
    assert len(sources) >= 15, sorted(sources)
    violations = check(sources)
    assert not violations, "\n\n".join(violations)


def test_every_allowlist_entry_says_why_and_when_it_goes():
    """Acceptance criterion 2 of Issue #43, on its own so a failure names it."""
    for edge, allowed in ALLOWLIST.items():
        assert allowed.why.strip() and allowed.remove_when.strip(), edge
    assert set(ALLOWLIST) == {
        ("src/aelix_analytics/runner.py", "src/aelix_analytics/report.py"),
        ("src/aelix_analytics/report.py", "src/aelix_analytics/runner.py"),
        ("src/aelix_analytics/report.py", "src/aelix_analytics/data.py"),
        ("src/aelix_analytics/demo.py", "src/aelix_analytics/report.py"),
    }, "the exceptions changed: update docs/ARCHITECTURE.md section 11 with them"


def test_the_reader_sees_every_place_an_import_can_be():
    source = """
import os
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from . import core
try:
    import polars
except ImportError:
    from . import data
else:
    import duckdb
finally:
    import json
if os.name == "nt":
    import msvcrt
elif os.name == "posix":
    import fcntl
else:
    import webbrowser
with open(__file__, "rb"):
    import zlib
for _ in ():
    import bz2
while False:
    import lzma
match os.name:
    case "nt":
        import winreg
class Holder:
    import csv
    def method(self):
        from .runner import kit_sha
        def inner():
            import pygments.lexers
        return inner
async def coroutine():
    import asyncio
handler = lambda: __import__("pickle")
def load():
    import importlib
    return importlib.import_module("aelix_analytics.report")
"""
    internal = frozenset({"aelix_analytics", "aelix_analytics.core", "aelix_analytics.data",
                          "aelix_analytics.runner", "aelix_analytics.report"})
    found, unresolved = read_imports("src/aelix_analytics/sample.py", source, internal)
    seen = {(f.target if f.call is None else f.call, f.scope, f.context, f.at_import)
            for f in found}
    assert not unresolved
    assert seen == {
        ("os", "", (), True), ("typing", "", (), True),
        ("aelix_analytics.core", "", ("type-only",), False),
        ("polars", "", ("try",), True), ("aelix_analytics.data", "", ("try",), True),
        ("duckdb", "", ("try",), True), ("json", "", ("try",), True),
        ("msvcrt", "", ("if",), True), ("fcntl", "", ("if", "if"), True),
        ("webbrowser", "", ("if", "if"), True), ("zlib", "", ("with",), True),
        ("bz2", "", ("loop",), True), ("lzma", "", ("loop",), True),
        ("winreg", "", ("match",), True), ("csv", "Holder", (), True),
        ("aelix_analytics.runner", "Holder.method", (), False),
        ("pygments.lexers", "Holder.method.inner", (), False),
        ("asyncio", "coroutine", (), False), ("__import__", "<lambda>", (), False),
        ("importlib", "load", (), False), ("import_module", "load", (), False),
    }


# --- a deliberate violation fails, and says which edge and which rule ----------------------------

def _append(text: str) -> Callable[[str], str]:
    return lambda source: source.rstrip("\n") + "\n\n\n" + text.strip("\n") + "\n"


def _replace(old: str, new: str) -> Callable[[str], str]:
    def change(source: str) -> str:
        assert old in source, f"the case's anchor {old!r} is not in the module any more"
        return source.replace(old, new, 1)
    return change


CORE = "src/aelix_analytics/core.py"
RELATIONS = "src/aelix_analytics/relations.py"
PLATFORM = "src/aelix_analytics/_platform.py"
REPORT = "src/aelix_analytics/report.py"
RUNNER = "src/aelix_analytics/runner.py"
CAPTURE = "src/aelix_analytics/capture.py"
INIT = "src/aelix_analytics/__init__.py"
CLI = "src/aelix_analytics/cli.py"
DEMO_SCRIPT = "src/aelix_analytics/demo_scripts/01_monthly_revenue.py"

VIOLATIONS = {
    # A lower layer importing a higher one, wherever the import is.
    "core imports the workspace at module level": (
        CORE, _append("from . import report"),
        ["core → workspace", "core.py → src/aelix_analytics/report.py", "layer-direction",
         "a lower layer imports a higher one", "at module level"]),
    "core imports the runtime inside a function": (
        CORE, _append("def _later():\n    from . import runner\n    return runner"),
        ["core → runtime", "layer-direction", "in _later"]),
    "core imports the workspace inside try": (
        CORE, _append("try:\n    from .report import build\nexcept ImportError:\n    build = None"),
        ["core → workspace", "layer-direction", "inside try"]),
    "core imports the runtime inside if": (
        CORE, _append("if os.name == 'nt':\n    from . import capture"),
        ["core → runtime", "layer-direction", "inside if"]),
    "core imports the workspace for typing only": (
        CORE, _append("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
                      "    from aelix_analytics import report"),
        ["core → workspace", "layer-direction", "type-only"]),
    "core imports the runtime by an absolute dotted name": (
        CORE, _append("def _later():\n    import aelix_analytics.runner as r\n    return r"),
        ["core → runtime", "imports aelix_analytics.runner", "layer-direction"]),
    "the read model imports core": (
        RELATIONS, _append("def _later():\n    from . import core\n    return core"),
        ["read_model → core", "relations.py → src/aelix_analytics/core.py", "layer-direction"]),
    "the ports import the read model": (
        PLATFORM, _append("def _later():\n    from . import relations\n    return relations"),
        ["ports → read_model", "sibling layers", "layer-direction"]),
    # Domain and shared application code using host, UI or analysis libraries.
    "core imports an analysis library": (
        CORE, _append("def _frame(path):\n    import polars as pl\n    return pl.read_parquet(path)"),
        ["imports polars", "core → analysis library", "external-library",
         "core may use: stdlib"]),
    "core imports the host's API": (
        CORE, _append("try:\n    from aelix_ai.tools import ToolResult\nexcept ImportError:\n"
                      "    ToolResult = None"),
        ["imports aelix_ai.tools", "core → host library", "external-library", "inside try"]),
    "the read model imports a rendering library": (
        RELATIONS, _append("def _html(code):\n    from pygments import highlight\n"
                           "    return highlight"),
        ["imports pygments", "read_model → rendering library", "external-library"]),
    "the ports import an analysis library": (
        PLATFORM, _append("if os.name == 'nt':\n    import numpy"),
        ["imports numpy", "ports → analysis library", "external-library"]),
    # Sibling adapters, and the host side reaching into the project environment.
    "the workspace imports the capture adapter": (
        REPORT, _append("def _hooks():\n    from . import capture\n    return capture"),
        ["workspace → runtime", "report.py → src/aelix_analytics/capture.py", "sibling layers"]),
    "the capture adapter imports the workspace": (
        CAPTURE, _append("def _page():\n    from .report import build\n    return build"),
        ["runtime → workspace", "sibling layers", "layer-direction"]),
    "the extension imports the runner": (
        EXTENSION_FILE, _append("def _in_process():\n    from aelix_analytics import runner\n"
                                "    return runner"),
        ["host_adapter → runtime", "aelix_analytics.py → src/aelix_analytics/runner.py",
         "host_adapter does not depend on runtime", "layer-direction"]),
    "the extension imports an analysis library": (
        EXTENSION_FILE, _append("def _table(rows):\n    import polars\n    return polars.DataFrame(rows)"),
        ["imports polars", "host_adapter → analysis library", "external-library"]),
    "the launcher imports core": (
        CLI, _append("def _status():\n    from . import core\n    return core"),
        ["launcher → core", "launcher does not depend on core", "layer-direction"]),
    "the example script imports a kit module": (
        DEMO_SCRIPT, _append("from aelix_analytics import core"),
        ["user_scripts → core", "layer-direction"]),
    # The package's module level, which runs inside aelix.
    "the package imports an analysis library when it is imported": (
        INIT, _replace("from typing import TYPE_CHECKING\n",
                       "from typing import TYPE_CHECKING\n\ntry:\n    import polars\n"
                       "except ImportError:\n    polars = None\n"),
        ["imports polars (analysis) when the module is imported", "import-time", "inside try"]),
    "the package imports a kit module when it is imported": (
        INIT, _replace("from typing import TYPE_CHECKING\n",
                       "from typing import TYPE_CHECKING\n\nfrom . import core as _core\n"),
        ["imports aelix_analytics.core when the module is imported", "import-time"]),
    # A dynamic load nobody declared, and a library nobody classified.
    "core loads a module dynamically": (
        CORE, _append("def _later():\n    import importlib\n"
                      "    return importlib.import_module('aelix_analytics.report')"),
        ["loads a module dynamically", "import_module", "dynamic-load", "in _later"]),
    "the runner imports an unclassified library": (
        RUNNER, _append("def _fetch(url):\n    import requests\n    return requests.get(url)"),
        ["imports requests", "no category lists", "external-library"]),
}


@pytest.mark.parametrize("case", sorted(VIOLATIONS))
def test_a_deliberate_violation_fails_and_names_the_edge_and_the_rule(case):
    path, change, expected = VIOLATIONS[case]
    sources = shipped_sources()
    sources[path] = change(sources[path])
    violations = check(sources)
    assert len(violations) == 1, "\n\n".join(violations) or "the check passed"
    for text in expected:
        assert text in violations[0], (text, violations[0])


def test_a_module_nobody_registered_fails():
    sources = shipped_sources()
    sources["src/aelix_analytics/writer.py"] = "from . import core\n"
    violations = check(sources)
    assert violations == [
        "src/aelix_analytics/writer.py is in no layer: breaks rule registration — register it in "
        "MODULES (tests/test_layers.py) and in the table of docs/ARCHITECTURE.md section 11, in the "
        "PR that adds it"]


def test_a_registered_module_that_is_gone_fails():
    modules = {**MODULES, "src/aelix_analytics/writer.py": "core"}
    violations = check(shipped_sources(), modules=modules)
    assert violations == ["MODULES registers src/aelix_analytics/writer.py, which is not shipped: "
                          "breaks rule registration — remove the entry"], violations


def test_an_exception_whose_edge_is_gone_fails():
    sources = shipped_sources()
    sources[REPORT] = sources[REPORT].replace("    from .runner import kit_sha\n\n", "    kit_sha = str\n")
    violations = check(sources)
    assert len(violations) == 1 and violations[0].startswith(
        "ALLOWLIST entry src/aelix_analytics/report.py → src/aelix_analytics/runner.py is no longer "
        "imported: breaks rule allowlist"), violations


def test_an_exception_without_a_removal_condition_fails():
    edge = ("src/aelix_analytics/demo.py", "src/aelix_analytics/report.py")
    allowlist = {**ALLOWLIST, edge: Allowed(why=ALLOWLIST[edge].why, remove_when=" ")}
    violations = check(shipped_sources(), allowlist=allowlist)
    assert violations == [
        "ALLOWLIST entry src/aelix_analytics/demo.py → src/aelix_analytics/report.py lacks why or "
        "its removal condition: breaks rule allowlist — every exception says why it exists and when "
        "it is removed"], violations


def test_an_exception_the_table_allows_anyway_fails():
    edge = ("src/aelix_analytics/runner.py", "src/aelix_analytics/capture.py")
    allowlist = {**ALLOWLIST, edge: Allowed(why="x", remove_when="y")}
    violations = check(shipped_sources(), allowlist=allowlist)
    assert violations == [
        "ALLOWLIST entry src/aelix_analytics/runner.py → src/aelix_analytics/capture.py is an edge "
        "the table allows (runtime → runtime): breaks rule allowlist — remove the entry"], violations


def test_a_declared_dynamic_load_that_is_gone_fails():
    sources = shipped_sources()
    sources[CLI] = sources[CLI].replace("importlib.util.spec_from_file_location(name, path)",
                                        "None")
    violations = check(sources)
    assert violations == [
        "DYNAMIC_LOADS declares ('src/aelix_analytics/cli.py', '_load_platform', "
        "'spec_from_file_location'), which the code no longer calls: breaks rule dynamic-load — "
        "remove the declaration"], violations


def test_a_declared_dynamic_load_is_judged_like_an_import():
    """The extension's load of core is declared; declaring it as a load of the runner instead makes
    it the edge host_adapter → runtime."""
    key = ("extension/aelix_analytics.py", "_load_core", "spec_from_file_location")
    dynamic = {**DYNAMIC_LOADS, key: DynamicLoad("src/aelix_analytics/runner.py", "x")}
    violations = check(shipped_sources(), dynamic=dynamic)
    assert len(violations) == 1, violations
    assert "host_adapter → runtime" in violations[0] and "(dynamically)" in violations[0]
    assert "in _load_core" in violations[0], violations[0]


# --- the document's table is this table -------------------------------------------------------------

CATEGORY_WORDS = {"stdlib": "stdlib", "분석": "analysis", "표시": "rendering", "호스트": "host"}


def _tables(text: str, heading: str) -> list[list[list[str]]]:
    start = text.index(heading)
    end = text.find("\n## ", start)
    tables, current = [], []
    for line in text[start:end if end != -1 else len(text)].splitlines():
        if line.startswith("|"):
            current.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def test_the_architecture_document_shows_this_table():
    """docs/ARCHITECTURE.md section 11 shows the layers, their modules and the exceptions; this keeps
    it from turning into a stale statement (as tools/platform_smoke.py's DECLARED does for CONTRACTS G)."""
    tables = _tables(ARCHITECTURE.read_text(encoding="utf-8"), "### 계층과 의존 방향 검사")
    layer_table = next(t for t in tables if t[0][0] == "계층")
    edge_table = next(t for t in tables if t[0][0] == "허용 간선")
    shown = {}
    for row in layer_table[2:]:
        name = row[0].strip("`")
        shown[name] = {
            "level": int(row[1]),
            "modules": set(re.findall(r"`([^`]+\.py)`", row[3])),
            "may_import": set(re.findall(r"`([a-z_]+)`", row[4])),
            "external": {CATEGORY_WORDS[w.strip()] for w in row[5].split(",")},
        }
    assert shown == {
        name: {"level": layer.level,
               "modules": {path for path, layer_name in MODULES.items() if layer_name == name},
               "may_import": set(layer.may_import), "external": set(layer.external)}
        for name, layer in LAYERS.items()}
    edges = set()
    for row in edge_table[2:]:
        a, b = re.findall(r"`([^`]+)`", row[0])
        assert row[3] and row[2], f"the document's exception {a} → {b} lacks why or when it goes"
        edges.add((a, b))
    assert edges == {(PurePosixPath(a).name, PurePosixPath(b).name) for a, b in ALLOWLIST}
