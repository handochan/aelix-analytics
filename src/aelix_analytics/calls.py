"""The call ledger (experiment: Issue #196, branch exp/session-substrate — not adopted, not a contract):
one line for every tool call of a session in an active project — the kit's tools, the host's, another
extension's, an MCP server's — as the host's hooks saw it: which tool, when it ran, the hashes and
sizes of its arguments and of the result the model got, which calls ran while it ran, and for the
host's file tools the project file it named. No argument text and no result bytes.

The bytes of a result are held apart, for a while (hold): in the project's cache, which git ignores,
by their hash, up to a size and an age. A result a record's claim was found in is kept (keep) in a
directory that ignores itself until its owner says otherwise. Neither is a record, and a
publication holds neither.

An auxiliary log beside telemetry (`<state dir>/calls.jsonl`): no journal seq, and no reader of records
reads it. It is what a later step compares the project's files and a record's claims against, so it
says what was seen and never what a call did: a call's window holds what ran in it, not what it caused.

Standard library only (the aelix process loads it with core).
"""

from __future__ import annotations

import collections
import contextlib
import dataclasses
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from . import _platform, observed
from .writer import append_jsonl

SCHEMA = "analytics.call/0"
FILE = "calls.jsonl"
# The host's own tools (aelix 0.1.0b2). A tool of neither list is another extension's or an MCP
# server's: the host does not say which (its ToolInfo holds a name and a description only).
HOST_TOOLS = frozenset({"bash", "read", "write", "edit", "grep", "find", "ls"})
KIT_PREFIX = "analytics_"
SOURCES = ("kit", "host", "other")
# What a host file tool did to the file its `path` names.
FILE_TOOLS = {"read": "read", "write": "wrote", "edit": "wrote"}

# Past this many bytes of text a result (or the arguments, or the details) is counted and not hashed:
# the hash is made on the host's event loop, some 30 ms at this size.
HASHED_BYTES = 16 << 20
KEYS = 24
KEY_CHARS = 48
OVERLAPS = 16
# A read looks at the ledger's end: this many bytes of it.
TAIL_BYTES = 4 << 20
READ_LIMIT = 200

# Held results: below the project's cache directory, one file a result, named by its hash.
HELD = "held"
HELD_RESULT_BYTES = 4 << 20
HELD_TOTAL_BYTES = 256 << 20
HELD_SECONDS = 14 * 24 * 3600
HELD_ENTRIES = 20_000
# Kept results: below the state directory, never pruned.
KEPT = "kept"
IGNORE_ALL = "# Results of tool calls: they may hold private data. Remove this file only for\n" \
             "# results you are allowed to commit.\n*\n"


def source_of(tool: str) -> str:
    """Whose tool `tool` is, from its name: the kit's, the host's, or another's."""
    if tool.startswith(KIT_PREFIX):
        return "kit"
    return "host" if tool in HOST_TOOLS else "other"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _plain(value):
    """What a value JSON has no form for is hashed as: its fields, its members, or the name of its
    type — never its repr, which may hold an address and differ between two runs."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"bytes": sha256(bytes(value))}
    if isinstance(value, (set, frozenset)):
        return sorted(map(str, value))
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    return f"<{type(value).__name__}>"


def canonical(value) -> bytes | None:
    """The bytes a value is hashed as (JSON, keys sorted, no spaces, UTF-8), or None for a value that
    has no such form (keys of mixed types, a cycle)."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          default=_plain).encode("utf-8", "surrogatepass")
    except (TypeError, ValueError, RecursionError):
        return None


def _digest(value) -> dict:
    raw = canonical(value)
    if raw is None:
        return {"unreadable": True}
    if len(raw) > HASHED_BYTES:
        return {"bytes": len(raw), "too_large": True}
    return {"sha256": sha256(raw), "bytes": len(raw)}


def digest_args(args) -> dict:
    """What the ledger keeps of a call's arguments: the hash and size of their canonical form and the
    names of the arguments. No values."""
    args = args if isinstance(args, dict) else {}
    return {**_digest(args), "keys": sorted(str(key)[:KEY_CHARS] for key in args)[:KEYS]}


def _field(block, name: str):
    return block.get(name) if isinstance(block, dict) else getattr(block, name, None)


def _block(block) -> tuple[dict, int, int]:
    """(a result block as it is hashed, its text bytes, its image bytes). An image is hashed by the
    hash of its data as sent (base64 text), so the block's form stays small."""
    text = _field(block, "text")
    if isinstance(text, str):
        return {"type": "text", "text": text}, len(text.encode("utf-8", "surrogatepass")), 0
    data = _field(block, "data")
    if isinstance(data, (str, bytes, bytearray)):
        raw = data.encode("utf-8", "surrogatepass") if isinstance(data, str) else bytes(data)
        mime = _field(block, "mime_type") or _field(block, "mimeType") or ""
        return {"type": "image", "mime": str(mime), "sha256": sha256(raw), "bytes": len(raw)}, 0, len(raw)
    return {"type": type(block).__name__}, 0, 0


def digest_result(content, details=None) -> tuple[dict, bytes | None]:
    """(what the ledger keeps of a result, the canonical bytes its hash is of — None when it was not
    hashed). The result is its blocks in order, as the caller saw them (the extension: at its
    tool_result handler, with what the handlers before it and it itself added; what a handler after
    it adds is not in it). `details` — an MCP result's structuredContent, a tool's own record of what
    it did — is hashed apart."""
    blocks = content if isinstance(content, (list, tuple)) else []
    forms, text_bytes, images, image_bytes = [], 0, 0, 0
    for block in blocks:
        form, text, image = _block(block)
        forms.append(form)
        text_bytes += text
        image_bytes += image
        images += form["type"] == "image"
    kept = {"blocks": len(forms), "text_bytes": text_bytes, "images": images, "image_bytes": image_bytes}
    raw = canonical(forms) if text_bytes <= HASHED_BYTES else None
    if raw is not None:
        kept["sha256"] = sha256(raw)
    else:
        kept["too_large" if text_bytes > HASHED_BYTES else "unreadable"] = True
    if details is not None:
        kept["details"] = _digest(details)
    return kept, raw


def file_of(project, tool: str, args, base=None) -> dict | None:
    """For a host file tool, the file below the project its `path` names: {"read": rel} or
    {"wrote": rel}, from the path's text (Project.below: no walk, no listing). A relative path is the
    host's — below `base`, its working directory — not the project's. None for another tool and for
    a path outside the project, whose name is not kept."""
    did = FILE_TOOLS.get(tool)
    path = args.get("path") if isinstance(args, dict) else None
    if did is None or not isinstance(path, str) or not path:
        return None
    with contextlib.suppress(OSError, ValueError):
        path = os.path.expanduser(path)
        if base is not None and not os.path.isabs(path):
            path = os.path.join(os.fspath(base), path)
        below = project.below(path)
        if below:
            return {did: "/".join(part for part in below if part)}
    return None


def entry(*, call, session, tool: str, started, ended: str, ms, error: bool, args, result=None,
          overlaps=(), file=None, observed=None, kit_command: bool = False, provenance=None) -> dict:
    """A ledger line. `result` is digest_result's first value, or None for a call no result was seen
    of (one another extension blocked before it ran, a cancel)."""
    line = {"schema": SCHEMA, "call": call, "session": session, "tool": tool, "source": source_of(tool),
            "started": started, "ended": ended, "ms": ms, "error": bool(error),
            "args": digest_args(args), "result": result}
    overlaps = sorted(str(other) for other in overlaps)
    if overlaps:
        line["overlaps"] = overlaps[:OVERLAPS]
        if len(overlaps) > OVERLAPS:
            line["overlaps_more"] = len(overlaps) - OVERLAPS
    if file:
        line["file"] = file
    if observed:
        line["observed"] = observed
    if kit_command:  # a bash call that is one kit command line: its output repeats the kit's records
        line["kit_command"] = True
    if provenance:  # what the tool said of its own call (provenance_of)
        line["provenance"] = provenance
    return line


def path_of(project) -> Path:
    return project.state_dir / FILE


def append(project, line: dict) -> None:
    append_jsonl(path_of(project), line)


def _tail(path: Path, size: int) -> tuple[list[str], bool]:
    """(the whole lines in the last `size` bytes of the file, whether the file starts before them)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            start = max(0, end - size)
            fh.seek(start)
            raw = fh.read()
    except OSError:
        return [], False
    if start:
        raw = raw.partition(b"\n")[2]  # the line the cut fell in
    return raw.decode("utf-8", "replace").splitlines(), bool(start)


def read(project, *, session=None, tool=None, source=None, since=None, limit: int = READ_LIMIT) -> dict:
    """The ledger's latest lines, oldest first: {"calls": [...], "more": whether earlier ones that
    match were left out, "summary": counts over the lines returned}. `since` is a `started` time (ISO):
    calls that ended at or after it. Lines of another schema and unreadable lines are passed over."""
    lines, cut = _tail(path_of(project), TAIL_BYTES)
    found = []
    for text in lines:
        try:
            line = json.loads(text)
        except ValueError:
            continue
        if not isinstance(line, dict) or line.get("schema") != SCHEMA:
            continue
        if session is not None and line.get("session") != session:
            continue
        if tool is not None and line.get("tool") != tool:
            continue
        if source is not None and line.get("source") != source:
            continue
        if since is not None and str(line.get("ended") or "") < since:
            continue
        found.append(line)
    limit = max(1, int(limit))
    kept = found[-limit:]
    return {"calls": kept, "more": cut or len(found) > len(kept), "summary": summary(kept)}


def summary(lines) -> dict:
    """Counts over ledger lines: by whose tool, by tool, the errors, the calls that ran while another
    did, the project files the host's file tools named, and the calls with an observation."""
    by_source = collections.Counter(line.get("source") for line in lines)
    by_tool = collections.Counter(line.get("tool") for line in lines)
    files = {"read": set(), "wrote": set()}
    for line in lines:
        for did, rel in (line.get("file") or {}).items():
            if did in files:
                files[did].add(rel)
    return {"calls": len(lines),
            "by_source": {name: by_source[name] for name in SOURCES if by_source[name]},
            "by_tool": dict(sorted(by_tool.items(), key=lambda item: (-item[1], str(item[0])))),
            "errors": sum(1 for line in lines if line.get("error")),
            "overlapping": sum(1 for line in lines if line.get("overlaps")),
            "files_read": len(files["read"]), "files_written": len(files["wrote"]),
            "observed": sum(1 for line in lines if line.get("observed"))}


def text_of(found: dict) -> str:
    """read()'s answer as lines for a person: one per call, then the counts."""
    out = []
    for line in found["calls"]:
        result = line.get("result") or {}
        digest = (result.get("sha256") or "-")[:12]
        size = result.get("text_bytes", 0) + result.get("image_bytes", 0)
        extra = []
        for did, rel in (line.get("file") or {}).items():
            extra.append(f"{did} {rel}")
        if line.get("overlaps"):
            extra.append(f"with {len(line['overlaps']) + line.get('overlaps_more', 0)} other(s)")
        if line.get("observed"):
            extra.append(f"observed {line['observed']}")
        if line.get("error"):
            extra.append("error")
        out.append(f"{line.get('started', '?')}  {line.get('source', '?'):5}  {line.get('tool', '?')}  "
                   f"{line.get('ms')} ms  result {digest} ({size} B)"
                   + (f"  [{'; '.join(extra)}]" if extra else ""))
    counts = found["summary"]
    by = ", ".join(f"{name} {n}" for name, n in counts["by_source"].items()) or "none"
    out.append(f"{counts['calls']} call(s): {by}; {counts['errors']} error(s); "
               f"{counts['overlapping']} ran with another; files read {counts['files_read']}, "
               f"written {counts['files_written']}; observed {counts['observed']}"
               + ("; earlier calls not shown" if found["more"] else ""))
    return "\n".join(out)


# --- the bytes of results: held for a while, kept when a record's claim was found in them ---

def _place(base: Path, digest: str) -> Path:
    return base / digest[:2] / f"{digest}.json"


def held_path(project, digest: str) -> Path:
    return _place(project.cache / HELD, digest)


def kept_path(project, digest: str) -> Path:
    return _place(project.state_dir / KEPT, digest)


def _ignored(directory: Path) -> None:
    """Make `directory` one git ignores by itself: a project made before this place existed has no
    line for it in its own .gitignore."""
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory / ".gitignore"
    if not ignore.exists():
        _platform.write_text(ignore, IGNORE_ALL)


def _put(path: Path, raw: bytes) -> None:
    """`raw` at `path`, whole or not at all: written beside it and moved over."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{secrets.token_hex(4)}.part")
    try:
        partial.write_bytes(raw)
        os.replace(partial, path)
    finally:
        with contextlib.suppress(OSError):
            partial.unlink()


def hold(project, digest: str, raw: bytes) -> str:
    """Hold a result's canonical bytes (digest_result's second value) under their hash: `held`,
    `too_large` (past HELD_RESULT_BYTES, nothing written) or `failed`. The same bytes held before are
    not written again."""
    if len(raw) > HELD_RESULT_BYTES:
        return "too_large"
    try:
        path = held_path(project, digest)
        if not path.exists():
            _ignored(project.cache / HELD)
            _put(path, raw)
    except OSError:
        return "failed"
    return "held"


def held(project, digest: str) -> bytes | None:
    """A result's bytes by their hash, from the kept ones or the held ones; None when neither has
    them. Bytes that are not that hash's (a file changed where it lay) are not given."""
    for path in (kept_path(project, digest), held_path(project, digest)):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if sha256(raw) == digest:
            return raw
    return None


def held_size(project, digest: str) -> int | None:
    for path in (kept_path(project, digest), held_path(project, digest)):
        with contextlib.suppress(OSError):
            return path.stat().st_size
    return None


def keep(project, digest: str) -> bool:
    """Keep a held result past the held ones' pruning: copied to the kept directory, which ignores
    itself. True when it is kept (now or before)."""
    try:
        path = kept_path(project, digest)
        if path.exists():
            return True
        raw = held(project, digest)
        if raw is None:
            return False
        _ignored(project.state_dir / KEPT)
        _put(path, raw)
    except OSError:
        return False
    return True


def prune(project, *, now: float | None = None) -> dict:
    """Remove the held results older than HELD_SECONDS, then the oldest until what is left is at
    most HELD_TOTAL_BYTES: {"looked", "removed", "bytes", "partial"}. It looks at HELD_ENTRIES files
    at most (`partial` past that). The kept results are not touched."""
    now = time.time() if now is None else now
    base = project.cache / HELD
    found, partial = [], False
    with contextlib.suppress(OSError):
        for directory in sorted(base.iterdir()):
            if not directory.is_dir():
                continue
            with os.scandir(directory) as entries:
                for item in entries:
                    if not item.name.endswith(".json"):
                        continue
                    if len(found) >= HELD_ENTRIES:
                        partial = True
                        break
                    with contextlib.suppress(OSError):
                        stat = item.stat()
                        found.append((stat.st_mtime, stat.st_size, item.path))
            if partial:
                break
    found.sort()
    total = sum(size for _, size, _ in found)
    removed = 0
    for modified, size, path in found:
        if now - modified <= HELD_SECONDS and total <= HELD_TOTAL_BYTES:
            break
        with contextlib.suppress(OSError):
            os.unlink(path)
            removed += 1
            total -= size
    return {"looked": len(found), "removed": removed, "bytes": total, "partial": partial}


# --- what a tool says of its own call ---
#
# A tool the kit knows nothing of needs to do nothing: its call has a line, its result is held, a
# record's numbers are looked for in it, and a file that changes while it runs is told with its
# window. A tool that says where its result is from and which files it wrote makes that exact. It
# says it in one of two ways, with the same fields:
#   - in its result's `details`, under PROVENANCE_KEY (an MCP server: in its structuredContent);
#   - on the host's event bus, on PROVENANCE_CHANNEL, with the call's `tool_call_id` — for a tool
#     whose details are another contract's.
# Fields, all optional: `source` (what was asked: a system, a table, a URL), `query` (the question
# as text), `as_of` (the time the answer is of), `rows` (a count), `wrote` and `read` (paths of files).

PROVENANCE_KEY = "analytics_provenance"
PROVENANCE_CHANNEL = "aelix-analytics:provenance"
PROVENANCE_TEXTS = ("source", "query", "as_of")
PROVENANCE_CHARS = 300
PROVENANCE_PATHS = 32


def said_in(details):
    """What a result's details say of the call under PROVENANCE_KEY (a mapping), else None."""
    said = details.get(PROVENANCE_KEY) if isinstance(details, dict) else getattr(details, PROVENANCE_KEY, None)
    return said if isinstance(said, dict) else None


def provenance_of(project, said, base=None) -> dict | None:
    """What the ledger keeps of what a tool said of its call: the text fields with the kit's
    redaction rules applied (observed.redact) and cut to PROVENANCE_CHARS, `rows` when it is a count,
    and of `wrote` and `read` the files below the project as records hold them (a relative path is
    the host's, below `base`) — a path outside the project is counted, not kept. None when it says
    nothing of these."""
    if not isinstance(said, dict):
        return None
    out = {}
    for name in PROVENANCE_TEXTS:
        value = said.get(name)
        if isinstance(value, str) and value.strip():
            out[name] = observed.redact(value.strip())[0][:PROVENANCE_CHARS]
    rows = said.get("rows")
    if type(rows) is int and rows >= 0:
        out["rows"] = rows
    outside = 0
    for name in ("wrote", "read"):
        paths = said.get(name)
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, (list, tuple)):
            continue
        kept = []
        for path in paths[:PROVENANCE_PATHS]:
            if not isinstance(path, str) or not path:
                continue
            below = None
            with contextlib.suppress(OSError, ValueError):
                path = os.path.expanduser(path)
                if base is not None and not os.path.isabs(path):
                    path = os.path.join(os.fspath(base), path)
                below = project.below(path)
            if below:
                kept.append("/".join(part for part in below if part))
            else:
                outside += 1
        if kept:
            out[name] = sorted(set(kept))
    if outside:
        out["outside"] = outside
    return out or None
