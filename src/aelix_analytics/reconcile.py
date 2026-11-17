"""Reconciliation (experiment: Issue #196, branch exp/session-substrate — not adopted, not a contract):
what changed in the project's files since the last look that no record says — no run's outputs, no
import, no observed activity — with the tool call it may be from.

The observation of a bash call (ADR-0015) looks at the files before and after that one call. This
looks at them once in a while (the extension: when a prompt starts and when the agent stops), whoever
changed them: another extension's tool or an MCP server's, the host's write and edit tools, a session
in another process, a person. A change is told with what the call ledger has for it: the write or edit
call that named the file, else the calls whose window holds the file's modification time, else nothing
— it was changed outside every call this project's ledger saw.

A call's window holding a file's time says the file changed while the call ran, never that the call
changed it. What it finds is a line of an auxiliary log (`<state dir>/reconciled.jsonl`); the state it
compares with is in the project's cache.

Standard library only (the aelix process loads it with core).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from datetime import datetime

from . import _platform, calls, observed, tree_state
from .writer import append_jsonl

SCHEMA = "analytics.reconcile/0"
FILE = "reconciled.jsonl"
SNAPSHOT = "reconcile.json"       # below the project's cache directory
SNAPSHOT_SCHEMA = "analytics.reconcile-state/0"
ENTRIES = observed.ENTRIES
SECONDS = 3.0
HASH_BYTES = 64 << 20             # of changed files hashed in one look
CHANGES = 100                     # of one line
CANDIDATES = 4
SLACK_SECONDS = 2.0               # a file system's clock and a call's are not the same clock
READ_LIMIT = 20
# The host's tools that write no file: never what a change may be from.
READ_ONLY = frozenset({"read", "grep", "find", "ls"})
# The host's tools the agent writes files with itself.
OWN_WRITES = frozenset({"write", "edit"})


def _snapshot_path(project):
    return project.cache / SNAPSHOT


def _load(project) -> dict | None:
    try:
        with open(_snapshot_path(project), encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("schema") != SNAPSHOT_SCHEMA or not isinstance(value.get("state"), dict):
        return None
    return value


def _save(project, state: tree_state.TreeStat, tail, at: str) -> None:
    value = {"schema": SNAPSHOT_SCHEMA, "at": at, "journal": tail[0] if tail else None,
             "state": {rel: list(stat) for rel, stat in state.items()},
             "real": dict(state.real), "boundaries": [list(pair) for pair in state.boundaries]}
    path = _snapshot_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        with _platform.open_text(partial, "w") as fh:
            json.dump(value, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(partial, path)
    finally:
        with contextlib.suppress(OSError):
            partial.unlink()


def _before(value: dict) -> tree_state.TreeStat:
    out = tree_state.TreeStat()
    for rel, stat in value["state"].items():
        if isinstance(stat, list) and len(stat) == 3:
            out[rel] = tuple(stat)
    out.real = {rel: real for rel, real in (value.get("real") or {}).items() if isinstance(real, str)}
    out.boundaries = tuple(tuple(pair) for pair in value.get("boundaries") or () if isinstance(pair, list))
    return out


def _sha(path: str, budget: dict) -> str | None:
    """sha256 of a changed file's bytes, None past the look's HASH_BYTES or when it cannot be read."""
    try:
        size = os.path.getsize(path)
        if budget["bytes"] + size > HASH_BYTES:
            return None
        budget["bytes"] += size
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _recorded(project, lines: list[dict]) -> tuple[dict, dict, set]:
    """What the journal's lines since the last look say changed: the outputs of runs and the imports
    (observed._explainers), and the paths the observed activities among them list."""
    outputs, deleted, _complete = observed._explainers(project, lines)
    seen = set()
    for value in lines:
        if value.get("kind") != observed.KIND:
            continue
        items = ((value.get("details") or {}).get("changes") or {}).get("items") or ()
        seen.update(item["path"] for item in items if isinstance(item, dict) and isinstance(item.get("path"), str))
    return outputs, deleted, seen


def _seconds(stamp) -> float | None:
    try:
        return datetime.fromisoformat(str(stamp)).timestamp()
    except ValueError:
        return None


def _from(change: dict, ledger: list[dict], since: float | None) -> dict | None:
    """What the call ledger has for a change, among the calls that ended since the last look:
    {"call", "tool", "how": "named"} for the latest write or edit call that named the file, else
    {"candidates": [...], "how": "window"} for the calls whose window holds its modification time
    (newest first), else None."""
    path = change["path"]
    lately = []
    for line in reversed(ledger):
        started, ended = _seconds(line.get("started")), _seconds(line.get("ended"))
        if ended is None or (since is not None and ended < since - SLACK_SECONDS):
            continue
        lately.append((line, started, ended))
    for line, _started, _ended in lately:
        # The file a write or edit call named, or one a tool said it wrote (calls.provenance_of).
        if (line.get("file") or {}).get("wrote") == path or path in ((line.get("provenance") or {}).get("wrote") or ()):
            return {"call": line.get("call"), "tool": line.get("tool"), "how": "named"}
    modified = change.get("modified")
    if modified is None:
        return None
    found = []
    for line, started, ended in lately:
        if line.get("tool") in READ_ONLY or started is None:
            continue
        if started - SLACK_SECONDS <= modified <= ended + SLACK_SECONDS:
            found.append({"call": line.get("call"), "tool": line.get("tool"), "source": line.get("source")})
            if len(found) == CANDIDATES:
                break
    return {"candidates": found, "how": "window"} if found else None


def look(project, *, session=None, at: str, deadline: float | None = None) -> dict | None:
    """Look at the project's files and compare them with the last look's: the line written for the
    changes no record says, or None when there is none (the first look, nothing changed, everything
    recorded) and when the look was stopped by its bounds (ENTRIES, SECONDS) — the state compared
    with is then left as it was."""
    deadline = time.monotonic() + SECONDS if deadline is None else deadline
    tail = observed.journal_tail(project.journal)
    try:
        state = tree_state.tree_stat(project, tree_state.Budget(ENTRIES, deadline))
    except tree_state.Stopped:
        return None
    last = _load(project)
    if last is None:
        _save(project, state, tail, at)
        return None
    before = _before(last)
    if before.boundaries != state.boundaries:
        before = tree_state.rekeyed(project, before)
    found = tree_state.changes(project, before, state)
    if not found:
        _save(project, state, tail, at)
        return None
    lines = None
    if tail is not None and isinstance(last.get("journal"), int):
        lines = observed.window_lines(project.journal, last["journal"], tail[0])
    outputs, deleted, observed_paths = _recorded(project, lines or [])
    ledger = calls.read(project, limit=calls.READ_LIMIT)["calls"]
    budget = {"bytes": 0}
    told = []
    for change in found:
        path, kind = change["path"], change["change"]
        if path in observed_paths or (kind == "deleted" and path in deleted):
            continue
        item = {"path": path, "change": kind}
        if kind in ("created", "modified"):
            if change.get("identity_only"):
                continue  # the same size and time: replaced by a file that reads the same
            real = state.real.get(path) or os.path.join(project.root, *path.split("/"))
            item["bytes"] = change["stat"][0]
            item["modified"] = change["stat"][1] / 1e9
            if (path, None) in outputs:
                continue
            if not observed.secret_file(path):
                digest = _sha(real, budget)
                if digest is not None:
                    if (path, digest) in outputs:
                        continue
                    item["sha256"] = digest
        origin = _from(item, ledger, _seconds(last.get("at")))
        if origin is not None:
            item["from"] = origin
        item.pop("modified", None)
        told.append(item)
    _save(project, state, tail, at)
    if not told:
        return None
    line = {"schema": SCHEMA, "at": at, "since": last.get("at"), "session": session,
            "changes": told[:CHANGES], "total": len(told),
            "records_read": lines is not None}
    append_jsonl(project.state_dir / FILE, line)
    return line


def said(line: dict, limit: int = 5) -> str:
    """A reconciliation's line as the model is told it at a prompt; nothing when every change it
    lists is a file the host's write or edit tool named — the agent's own writes, which it knows of."""
    items = [item for item in line["changes"]
             if not ((item.get("from") or {}).get("how") == "named" and item["from"].get("tool") in OWN_WRITES)]
    if not items:
        return ""
    parts = []
    for item in items[:limit]:
        origin = item.get("from")
        if origin is None:
            where = "outside any tool call seen"
        elif origin["how"] == "named":
            where = f"by {origin['tool']}"
        else:
            where = "while " + ", ".join(sorted({c["tool"] for c in origin["candidates"]})) + " ran"
        parts.append(f"{item['path']} ({item['change']}, {where})")
    more = len(items) - len(parts) + max(0, line["total"] - len(line["changes"]))
    return ("aelix-analytics: files changed since the last look that no run, import or observation recorded: "
            + "; ".join(parts) + (f"; and {more} more (`analytics changes`)" if more > 0 else "")
            + ". Record data with analytics_import or analytics_run.")


def read(project, *, limit: int = READ_LIMIT) -> dict:
    """The log's latest lines, oldest first: {"looks": [...], "more"}."""
    lines, cut = calls._tail(project.state_dir / FILE, calls.TAIL_BYTES)
    found = []
    for text in lines:
        try:
            line = json.loads(text)
        except ValueError:
            continue
        if isinstance(line, dict) and line.get("schema") == SCHEMA:
            found.append(line)
    kept = found[-max(1, int(limit)):]
    return {"looks": kept, "more": cut or len(found) > len(kept)}


def text_of(found: dict) -> str:
    out = []
    for line in found["looks"]:
        out.append(f"{line.get('at')} (since {line.get('since')}): {line.get('total')} change(s) no record says")
        for item in line.get("changes") or []:
            origin = item.get("from")
            if origin is None:
                where = "outside any tool call seen"
            elif origin["how"] == "named":
                where = f"by {origin['tool']} {origin['call']}"
            else:
                where = "while " + ", ".join(f"{c['tool']} {c['call']}" for c in origin["candidates"]) + " ran"
            out.append(f"  {item.get('path')}: {item.get('change')}, {where}")
    out.append(f"{len(found['looks'])} look(s) with changes" + ("; earlier ones not shown" if found["more"] else ""))
    return "\n".join(out)
