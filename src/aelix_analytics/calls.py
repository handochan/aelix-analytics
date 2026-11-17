"""The call ledger (experiment: Issue #196, branch exp/session-substrate — not adopted, not a contract):
one line for every tool call of a session in an active project — the kit's tools, the host's, another
extension's, an MCP server's — as the host's hooks saw it: which tool, when it ran, the hashes and
sizes of its arguments and of the result the model got, which calls ran while it ran, and for the
host's file tools the project file it named. No argument text and no result bytes.

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
from pathlib import Path

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
          overlaps=(), file=None, observed=None) -> dict:
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
