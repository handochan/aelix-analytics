"""Observed activity (ADR-0015): a bash command the model ran in an active project that the kit did not
record as a run, as the extension saw it around that tool call — the command's text with secrets
redacted, when it ran, what changed in the project's files while it ran, and the bytes of the scripts it
named. Not a run: what it read, the environment it ran in and its output are not kept.

The extension calls this module in a thread of its own (it waits a bounded time and abandons the work
past it, decision 6): `before` at tool_call — the journal's end, the scripts the command names, the
state of the files — and `after` at tool_result — the state again, what changed and whether a run or an
import recorded in the same window explains it, the record. The event is the commit point: attachments
are written first under `.analytics/observed/<activity_id>/`, then the event is appended under the
journal lock; work cancelled before the append writes no event.

Standard library only (the aelix process loads it with core); the project, its writer and its hash
cache are passed in.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import _platform, relations, tree_state

KIND = relations.OBSERVED_KIND
CONTRACT = relations.OBSERVED_CONTRACT
OPERATION = "observe"
WRITER_SINCE = relations.OBSERVED_WRITER_SINCE
RULES_VERSION = 1
REDACTED = "[redacted]"
# Where an observation's attachments are, below the layout's state directory.
ATTACHMENTS = "observed"

# The bounds of decision 6 (ADR-0015).
BEFORE_SECONDS = 2.0
AFTER_SECONDS = 3.0
ENTRIES = 20_000
HASH_BYTES = 256 << 20
SCRIPTS_READ = 8
SCRIPT_BYTES = 1 << 20
SCRIPTS_BYTES = 4 << 20
COMMAND_INLINE = 4 * 1024
COMMAND_ATTACHED = 1 << 20
TEXT_BYTES = 300
TEXT_COMMAND_CHARS = 160
CHANGES_INLINE = 12 * 1024
CHANGES_ATTACHED = 10_000
SCRIPTS_INLINE = 3 * 1024
MENTIONED_INLINE = 2 * 1024
DETAILS_BYTES = 24 * 1024
# How much of the journal's end is read for its last complete line (decision 3), and how many bytes of
# the window's lines are read at most (more is `unknown`: the window is not read).
TAIL_BYTES = 64 * 1024
WINDOW_BYTES = 8 << 20
# How many words of a command are looked up as names at most (a stat each).
NAMES_LOOKED_UP = 500
# Consecutive timed-out state views, the session's first aside, that turn its change observation off.
TIMEOUTS_OFF = 3
SCRIPT_SUFFIXES = (".py", ".sql", ".r", ".jl", ".sh", ".ipynb")
# The commands that change the directory names after them are resolved in (decision 4).
CHANGE_DIRECTORY = frozenset({"cd", "pushd", "Set-Location", "set-location", "sl", "chdir"})


def new_activity_id() -> str:
    """`act-` and a lowercase ULID, as a publication_id and an attempt_id are made."""
    alphabet = "0123456789abcdefghjkmnpqrstvwxyz"
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "act-" + "".join(alphabet[(value >> (5 * n)) & 31] for n in reversed(range(26)))


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(stamp: datetime) -> str:
    return stamp.isoformat(timespec="milliseconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value) -> bytes:
    """A kit-written attachment: sorted UTF-8 JSON with an LF at its end (ADR-0007)."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


# --- redaction (decision 5, rules_version 1) ---------------------------------------------------------

# Rule 1: a name that may hold a secret — with `_` and `-` ignored, it holds one of these words — or one
# that ends in `_KEY`/`-KEY`. Case is ignored.
_SECRET_WORDS = ("token", "secret", "password", "passwd", "passphrase", "credential", "auth", "apikey",
                 "accesskey", "privatekey", "signature")


def secret_name(name: str) -> bool:
    """Whether a variable, option, key or header name may hold a secret (rule 1)."""
    folded = re.sub(r"[_-]", "", name).lower()
    return any(word in folded for word in _SECRET_WORDS) or bool(re.search(r"[_-]key$", name, re.IGNORECASE))


# A value as a shell or code text writes it: quoted (its quotes kept) or a run of what is not space,
# quote or a separator.
_VALUE = r"""(?:"(?:[^"\\\n]|\\.)*"|'[^'\n]*'|[^\s"';&|<>(){},]+)"""
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
                  re.DOTALL)
_KNOWN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]+"   # a JWT
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}"
    r"|glpat-[A-Za-z0-9_-]{16,}"
    r"|sk-[A-Za-z0-9_-]{16,}|(?:sk|rk)_live_[A-Za-z0-9]{8,}"
    r"|xox[a-z]-[A-Za-z0-9-]{10,}"
    r"|AIza[A-Za-z0-9_-]{35}"
    r"|hf_[A-Za-z0-9]{16,}|npm_[A-Za-z0-9]{16,})")
_URL_USER = re.compile(r"(?<=://)([^\s/@:'\"]+):([^\s/@'\"]+)(?=@)")
_URL_QUERY = re.compile(r"(?<=[?&])([^\s=&#'\"]+)=([^\s&#'\"]*)")
_HEADER = re.compile(r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|[A-Za-z0-9-]+)(\s*:\s*)"
                     r"([^\n'\"]+)")
_SCHEME = re.compile(r"(?i)\b(bearer|basic)(\s+)([A-Za-z0-9._~+/=-]+)")
_POWERSHELL_ENV = re.compile(r"(?i)(\$env:)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(" + _VALUE + ")")
_ASSIGNMENT = re.compile(r"(?<![\w$.-])([A-Za-z_][A-Za-z0-9_]*)(=)(" + _VALUE + ")")
_CODE_ASSIGNMENT = re.compile(r"""(?<![\w.-])([A-Za-z_][A-Za-z0-9_]*)(\s*[=:]\s*)("(?:[^"\\\n]|\\.)*"|'[^'\n]*')""")
_DICT_KEY = re.compile(r"""(["'])([^"'\n]+)\1(\s*:\s*)(""" + _VALUE + ")")
_OPTION_EQ = re.compile(r"(?<![\w-])(--?[A-Za-z][A-Za-z0-9_.-]*)(=)(" + _VALUE + ")")
_OPTION_NEXT = re.compile(r"(?<![\w-])(--?[A-Za-z][A-Za-z0-9_.-]*)([ \t]+)(" + _VALUE + ")")
_USER_PASSWORD = re.compile(r"(?<![\w-])(-u|--user)([ \t]+|=)(['\"]?)([^\s:'\"]+):([^\s'\"]+)")


def redact(text: str) -> tuple[str, int]:
    """`text` with the values rules_version 1 names replaced by `[redacted]`, and how many were. A deny
    list (decision 5): it says the rules were applied, never that no secret is left."""
    count = 0

    def sub(pattern, keep, value_group, text, *, when=None):
        nonlocal count

        def one(match):
            nonlocal count
            if when is not None and not when(match):
                return match.group(0)
            if match.group(value_group) in ("", REDACTED, f'"{REDACTED}"', f"'{REDACTED}'"):
                return match.group(0)
            count += 1
            return keep(match)
        return pattern.sub(one, text)

    def groups_before(n):
        return lambda m: "".join(m.group(i) or "" for i in range(1, n)) + REDACTED

    # Rule 6 first: a key block spans lines, and known shapes may sit where no name says so.
    text = sub(_PEM, lambda m: REDACTED, 0, text)
    text = sub(_KNOWN, lambda m: REDACTED, 0, text)
    # Rule 4: a URL's password, and every value of its query.
    text = sub(_URL_USER, lambda m: f"{m.group(1)}:{REDACTED}", 2, text)
    text = sub(_URL_QUERY, lambda m: f"{m.group(1)}={REDACTED}", 2, text)
    # Rule 5: the values of headers that carry credentials, and what follows Bearer or Basic.
    text = sub(_HEADER, lambda m: m.group(1) + m.group(2) + REDACTED, 3, text,
               when=lambda m: m.group(1).lower() in ("authorization", "proxy-authorization", "cookie",
                                                     "set-cookie") or (m.group(1).lower().startswith("x-")
                                                                       and secret_name(m.group(1))))
    text = sub(_SCHEME, lambda m: m.group(1) + m.group(2) + REDACTED, 3, text)
    # Rule 3: `-u user:password`.
    text = sub(_USER_PASSWORD, lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}:{REDACTED}", 5, text)
    # Rule 2: assignments whose name is a secret's.
    text = sub(_POWERSHELL_ENV, groups_before(4), 4, text, when=lambda m: secret_name(m.group(2)))
    text = sub(_DICT_KEY, lambda m: m.group(1) + m.group(2) + m.group(1) + m.group(3) + REDACTED, 4, text,
               when=lambda m: secret_name(m.group(2)))
    text = sub(_CODE_ASSIGNMENT, groups_before(3), 3, text, when=lambda m: secret_name(m.group(1)))
    text = sub(_ASSIGNMENT, groups_before(3), 3, text, when=lambda m: secret_name(m.group(1)))
    # Rule 3: options whose name is a secret's, `--name=value` and `--name value`.
    text = sub(_OPTION_EQ, groups_before(3), 3, text, when=lambda m: secret_name(m.group(1).lstrip("-")))
    text = sub(_OPTION_NEXT, groups_before(3), 3, text,
               when=lambda m: secret_name(m.group(1).lstrip("-")) and not m.group(3).startswith("-"))
    return text, count


# Rule of decision 5 for files: a name that may hold a secret is listed with its path, change and size
# only — never hashed or copied.
_SECRET_FILE = re.compile(r"(?i)^(?:\.env(?:\..*)?|.*\.pem|.*\.key|\.netrc|\.pgpass|id_rsa.*|id_ed25519.*"
                          r"|credentials.*|.*secret.*)$")


def secret_file(path: str) -> bool:
    return bool(_SECRET_FILE.match(PurePosixPath(path.replace("\\", "/")).name))


# --- the journal's end (decision 3) -----------------------------------------------------------------

def journal_tail(path) -> tuple[int, int | None] | None:
    """(the byte position after the journal's last LF, the seq of the last complete line before it)
    from one read of its last TAIL_BYTES; (0, None) for no journal, None when it cannot be read."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - TAIL_BYTES)
            fh.seek(start)
            data = fh.read(size - start)
    except FileNotFoundError:
        return 0, None
    except OSError:
        return None
    last = data.rfind(b"\n")
    if last < 0:
        return (start, None) if start else (0, None)
    position = start + last + 1
    seq = None
    for raw in reversed(data[:last].split(b"\n")[(1 if start else 0):]):
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if isinstance(value, dict) and type(value.get("seq")) is int:
            seq = value["seq"]
            break
    return position, seq


def window_lines(path, start: int, end: int) -> list[dict] | None:
    """The complete lines written between two tails' positions, parsed (a line that does not parse is
    left out); None when they cannot be read (the journal shrank, the window is too long, a read
    failed)."""
    if end < start or end - start > WINDOW_BYTES:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            data = fh.read(end - start)
    except OSError:
        return None
    if len(data) != end - start:
        return None
    out = []
    for raw in data.split(b"\n"):
        with contextlib.suppress(ValueError):
            value = json.loads(raw)
            if isinstance(value, dict):
                out.append(value)
    return out


# --- names a command gives (decision 4) --------------------------------------------------------------

@dataclass
class Name:
    """A word of a command that leads to a file: a script (by its suffix) or another file in the project."""

    word: str
    real: str            # the absolute path it was resolved to
    path: str | None     # the path a record holds, None outside the project
    outside: bool
    script: bool


@dataclass
class Names:
    scripts: list[Name] = field(default_factory=list)
    mentioned: list[Name] = field(default_factory=list)
    unresolved: int = 0


def _untrackable(word: str) -> bool:
    return not word or word == "-" or any(c in word for c in "$`*?[") or word.startswith("~")


def resolve_names(project, cwd: str, commands: list[list[str]], *, existing_only: bool = False) -> Names:
    """The files the words of a command lead to, resolved from aelix's working directory `cwd` and the
    literal `cd X` (pushd, Set-Location) before them; after a change of directory that cannot be
    followed (a variable, `cd -`, `cd` alone) a relative word is not resolved and is counted in
    `unresolved`. A word that leads to a regular file in the project (outside its managed directories)
    or to a script outside it is kept: a script by its suffix (SCRIPT_SUFFIXES), another file as
    mentioned. A word that leads nowhere yet but has a script's suffix is kept as a script too (the
    command may make it), unless `existing_only`."""
    names, seen = Names(), set()
    here: str | None = os.fspath(cwd)
    managed = set(project.layout.managed_dirs)
    looked = 0
    for words in commands:
        if not words:
            continue
        if words[0] in CHANGE_DIRECTORY:
            target = words[1] if len(words) == 2 else None
            if target is None or _untrackable(target) or here is None and not os.path.isabs(target):
                here = None
            else:
                here = os.path.normpath(os.path.join(here, target)) if here is not None else os.path.normpath(target)
            continue
        expect_code = False
        for word in words:
            if expect_code:
                expect_code = False
                continue  # the code of `-c`, which is command text, not a file
            if word in ("-c", "-Command", "-e"):
                expect_code = True
                continue
            if word.startswith("-") and "=" in word:
                word = word.split("=", 1)[1]
            elif word.startswith("-") or re.match(r"[A-Za-z_][A-Za-z0-9_]*=", word):
                continue
            if not word or "\n" in word or len(word) > 1024 or any(c in word for c in "$`*?"):
                continue
            absolute = os.path.isabs(word) or bool(re.match(r"[A-Za-z]:[\\/]", word))
            if not absolute and here is None:
                if "/" in word or "\\" in word or word.lower().endswith(SCRIPT_SUFFIXES):
                    names.unresolved += 1
                continue
            looked += 1
            if looked > NAMES_LOOKED_UP:
                return names
            place = os.path.normpath(word if absolute else os.path.join(here, word))
            if place in seen:
                continue
            is_script = place.lower().endswith(SCRIPT_SUFFIXES)
            exists = os.path.isfile(place)
            if not exists and (existing_only or not is_script):
                continue
            seen.add(place)
            try:
                identity, real = project.locate(place)
            except (OSError, ValueError):
                continue
            outside = os.path.isabs(identity)
            if not outside and identity.split("/", 1)[0] in managed:
                continue
            if outside and not is_script:
                continue
            item = Name(word, real, None if outside else identity, outside, is_script)
            (names.scripts if is_script else names.mentioned).append(item)
    return names


# --- the job: cancel and commit (decision 6) ---------------------------------------------------------

class Job:
    """One piece of an observation's work in a thread: the waiting side cancels it (or its deadline
    passes) and the work stops at its next check; once the work has begun its commit (the journal
    append under the lock) a cancel no longer stops it, and the waiting side says it could not confirm
    whether the record was written."""

    def __init__(self, deadline: float):
        self.deadline = deadline
        self._lock = threading.Lock()
        self._cancelled = False
        self.committing = False

    def cancel(self) -> bool:
        """Stop the work; whether its commit had begun."""
        with self._lock:
            self._cancelled = True
            return self.committing

    def cancelled(self) -> bool:
        return self._cancelled or time.monotonic() > self.deadline

    def begin_commit(self) -> bool:
        """Whether the commit may begin: not cancelled, deadline not passed. From then on it is not
        stopped."""
        with self._lock:
            if self._cancelled or time.monotonic() > self.deadline:
                return False
            self.committing = True
            return True

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())


class Sessions:
    """Each aelix session's change observation (decision 6): off once a state view read more than
    ENTRIES entries, or after TIMEOUTS_OFF timed-out state views in a row (its first view, a cold
    cache, aside). A new session measures again."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict = {}

    def off(self, session) -> bool:
        with self._lock:
            return bool(self._state.get(session, {}).get("off"))

    def viewed(self, session, outcome: str | None) -> bool:
        """Count one state view (`outcome`: None when it finished, else Stopped's reason). Whether this
        view turned the session's observation off."""
        with self._lock:
            state = self._state.setdefault(session, {"views": 0, "in_row": 0, "off": False})
            if state["off"]:
                return False
            state["views"] += 1
            if outcome == "too_many_entries":
                state["off"] = True
                return True
            if outcome == "timeout" and state["views"] > 1:
                state["in_row"] += 1
            elif outcome is None:
                state["in_row"] = 0
            if state["in_row"] >= TIMEOUTS_OFF:
                state["off"] = True
                return True
            return False


# --- before: tool_call --------------------------------------------------------------------------------

@dataclass
class Captured:
    """A script's bytes read in this window, with its state then."""

    data: bytes | None
    stat: tuple | None
    when: str            # "before" or "after"
    reason: str | None   # why the bytes are not held (over_budget, unreadable, withheld)


@dataclass
class Before:
    activity_id: str
    started: datetime
    started_mono: float
    command: str
    cwd: str
    tail: tuple | None = None
    state: tree_state.TreeStat | None = None
    not_observed: str | None = None      # why the state was not viewed or not kept
    turned_off: bool = False
    names: Names | None = None
    captured: dict = field(default_factory=dict)   # real path -> Captured
    since: int | None = None

    @classmethod
    def empty(cls, activity_id: str, command: str, cwd, reason: str) -> Before:
        """A start that saw nothing (`reason`: busy, timeout, no_before_state): the window's time only."""
        return cls(activity_id, _now(), time.monotonic(), command, os.fspath(cwd), not_observed=reason)


def _read_scripts(names: Names, job: Job | None, captured: dict, when: str, total: int = 0) -> int:
    """Read the bytes of the scripts in `names` not read yet: at most SCRIPTS_READ of them, SCRIPT_BYTES
    each and SCRIPTS_BYTES together; a name that may hold a secret is never read. The bytes read in
    all."""
    for name in names.scripts:
        if name.real in captured:
            continue
        if job is not None and job.cancelled():
            break
        st = tree_state.stat_of(name.real)
        if st is None or not os.path.isfile(name.real):
            continue
        if secret_file(name.real):
            captured[name.real] = Captured(None, st, when, "withheld")
            continue
        read = sum(1 for c in captured.values() if c.data is not None)
        if read >= SCRIPTS_READ or st[0] > SCRIPT_BYTES or total + st[0] > SCRIPTS_BYTES:
            captured[name.real] = Captured(None, st, when, "over_budget")
            continue
        try:
            with open(name.real, "rb") as fh:
                data = fh.read(SCRIPT_BYTES + 1)
        except OSError:
            captured[name.real] = Captured(None, st, when, "unreadable")
            continue
        if len(data) > SCRIPT_BYTES:
            captured[name.real] = Captured(None, st, when, "over_budget")
            continue
        total += len(data)
        captured[name.real] = Captured(data, st, when, None)
    return total


# How far before the window's start a file's mtime may be and still count as written in it, below a
# boundary made meanwhile: the file system's clock and its time resolution (FAT: 2 s).
CLOCK_SLACK_NS = 2_000_000_000


def before(project, *, activity_id: str, command: str, commands: list[list[str]], cwd: str, job: Job,
           sessions: Sessions, session, view_state: bool, not_observed: str | None = None) -> Before:
    """What the observation keeps at tool_call: the window's start, the journal's end, the bytes of the
    scripts the command names, and — when `view_state` — the state of the files within the bounds
    (ENTRIES, the job's deadline and cancel). A state view the bounds stopped keeps no state
    (`not_observed` is the reason) and counts toward turning the session's observation off."""
    started = _now()
    out = Before(activity_id, started, time.monotonic(), command, os.fspath(cwd), not_observed=not_observed)
    out.since = time.time_ns() - CLOCK_SLACK_NS
    out.tail = journal_tail(project.journal)
    with contextlib.suppress(Exception):
        out.names = resolve_names(project, cwd, commands)
        _read_scripts(out.names, job, out.captured, "before")
    if not view_state:
        return out
    try:
        if job.cancelled():
            raise tree_state.Stopped("timeout")
        out.state = tree_state.tree_stat(project, tree_state.Budget(ENTRIES, job.deadline, job.cancelled))
        out.turned_off = sessions.viewed(session, None)
    except tree_state.Stopped as stop:
        out.not_observed = "too_many_entries" if stop.reason == "too_many_entries" else "timeout"
        out.turned_off = sessions.viewed(session, stop.reason)
    except Exception:  # noqa: BLE001 - a walk that fails is a view not made, never a failed tool call
        out.not_observed = "walk_failed"
    return out


# --- after: tool_result -------------------------------------------------------------------------------

@dataclass
class Outcome:
    """What the after step did: the event written (None when nothing was), why not, and what the
    notice says about the changes."""

    event: dict | None = None
    reason: str | None = None
    unexplained: int = 0
    unknown: int = 0
    turned_off: bool = False
    observed: bool = False


def _hash(path: str, expected: tuple, budget: dict, job: Job) -> tuple[str | None, str]:
    """(sha256, hash state) of a changed file read from its bytes, never from the cache: `unstable`
    when its stat before or after the read is not `expected` (the after state's), `over_budget` past
    the bytes or the time left."""
    first = tree_state.stat_of(path)
    if first is None:
        return None, "unreadable"
    if not tree_state.same(first, expected):
        return None, "unstable"
    if job.cancelled() or budget["bytes"] + first[0] > HASH_BYTES:
        return None, "over_budget"
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(1 << 20)
                if not block:
                    break
                digest.update(block)
                if job.cancelled():
                    return None, "over_budget"
    except OSError:
        return None, "unreadable"
    budget["bytes"] += first[0]
    second = tree_state.stat_of(path)
    if second is None or not tree_state.same(second, expected):
        return None, "unstable"
    return digest.hexdigest(), "hashed"


def _explainers(project, lines: list[dict]) -> tuple[dict, dict, bool]:
    """From the window's lines: {(path, sha256): by} of the outputs of the runs completed in it and the
    files imported in it (with each import's meta file by its path alone), {path: by} of the files those
    runs deleted, and whether every run record named could be read."""
    outputs, deleted, complete = {}, {}, True
    for value in lines:
        if value.get("schema") != relations.EVENT_V2 or not isinstance(value.get("refs"), list):
            continue
        kind = value.get("kind")
        for ref in value["refs"]:
            if not isinstance(ref, dict):
                continue
            if kind == "run" and ref.get("type") == "run" and isinstance(ref.get("run_id"), str):
                run_id = ref["run_id"]
                if not relations.RUN_ID_RE.match(run_id):
                    continue
                try:
                    with open(project.runs / run_id / "run.json", encoding="utf-8") as fh:
                        record = json.load(fh)
                except (OSError, ValueError):
                    complete = False
                    continue
                by = {"type": "run", "id": run_id}
                for output in record.get("outputs") or ():
                    if not isinstance(output, dict) or not isinstance(output.get("path"), str):
                        continue
                    if output.get("change") == "deleted":
                        deleted[output["path"]] = by
                    elif isinstance(output.get("sha256"), str):
                        outputs[(output["path"], output["sha256"])] = by
            elif kind == "import" and ref.get("type") == "dataset_import" and isinstance(ref.get("path"), str):
                digest = (ref.get("digest") or {}).get("sha256") if isinstance(ref.get("digest"), dict) else None
                by = {"type": "dataset_import", "id": ref["path"]}
                if isinstance(digest, str):
                    outputs[(ref["path"], digest)] = by
                meta = str(PurePosixPath(ref["path"]).with_suffix(".meta.json"))
                outputs[(meta, None)] = by
    return outputs, deleted, complete


def _cut_utf8(text: str, limit: int) -> tuple[str, bool]:
    """The longest start of `text` whose UTF-8 is at most `limit` bytes, and whether it was cut."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text, False
    return data[:limit].decode("utf-8", errors="ignore"), True


def _fits(items: list, limit: int) -> int:
    """How many of `items` from the start fit in `limit` bytes of JSON."""
    size, count = 2, 0
    for item in items:
        size += len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 1
        if size > limit:
            break
        count += 1
    return count


def summary_text(command: str, changes: dict) -> str:
    """The event's text (decision 2): one line of the kit's, at most TEXT_BYTES — the redacted command's
    first TEXT_COMMAND_CHARS characters (fewer when their UTF-8 would not fit) and how many files changed."""
    one = " ".join(command.split())
    if changes["state"] == "observed":
        n = changes["total"]
        tail = f" — {n} file{'s' if n != 1 else ''} changed while it ran"
    else:
        tail = " — changes not observed"
    head = 'Observed outside the kit: bash "'
    room = TEXT_BYTES - len((head + '"' + tail).encode("utf-8"))
    quoted = one[:TEXT_COMMAND_CHARS]
    if len(one) > TEXT_COMMAND_CHARS or len(quoted.encode("utf-8")) > room:
        quoted = _cut_utf8(quoted, room - len("…".encode()))[0] + "…"
    return head + quoted + '"' + tail


@dataclass
class Call:
    """The tool call as tool_result gives it: its final command (after any other extension changed
    it), that command's simple commands' words, whether it runs Python outside the kit that is not
    package management alone (the extension's judgment, #170), and aelix's result."""

    tool_call_id: str | None
    command: str
    commands: list[list[str]]
    python_command: bool
    is_error: bool | None
    exit_code: int | None


def after(project, prior: Before, call: Call, *, writer, job: Job, sessions: Sessions, session,
          cache=None) -> Outcome:
    """The observation at tool_result: the state again and what changed (decision 3), the scripts
    (decision 4), the reasons (decision 1), the redacted command (decision 5), the attachments and then
    the event (decision 2) — each step within the job's deadline. Nothing is written when no reason
    holds or the job was cancelled before its commit."""
    outcome = Outcome(turned_off=prior.turned_off)
    reason = prior.not_observed
    changes_list, links, after_state, tail_after = None, 0, None, None
    if prior.state is not None:
        try:
            if job.cancelled():
                raise tree_state.Stopped("timeout")
            after_state = tree_state.tree_stat(project, tree_state.Budget(ENTRIES, job.deadline, job.cancelled))
        except tree_state.Stopped as stop:
            reason = "too_many_entries" if stop.reason == "too_many_entries" else "timeout"
            outcome.turned_off = sessions.viewed(session, stop.reason) or outcome.turned_off
        except Exception:  # noqa: BLE001 - a walk that fails is a view not made
            reason = "walk_failed"
        else:
            # The window ends when the state after has been seen whole.
            tail_after = journal_tail(project.journal)
            outcome.turned_off = sessions.viewed(session, None) or outcome.turned_off
            before_state = prior.state
            if before_state.boundaries != after_state.boundaries:
                before_state = tree_state.rekeyed(project, before_state)
            changes_list = tree_state.changes(project, before_state, after_state, prior.since)
            links = tree_state.links_changed(before_state, after_state)
            reason = None
    if tail_after is None:
        tail_after = journal_tail(project.journal)

    # The names of the command that ran: the final one, after any other extension's change. The bytes
    # read at the start count only for the names the final command has too.
    changed_after_start = call.command != prior.command
    names = Names()
    with contextlib.suppress(Exception):
        names = resolve_names(project, prior.cwd, call.commands)
    final = {n.real for n in names.scripts}
    captured = {real: c for real, c in prior.captured.items() if real in final}
    # Scripts the start did not see (made by the command: a heredoc) are read now.
    with contextlib.suppress(Exception):
        _read_scripts(names, job, captured, "after",
                      total=sum(len(c.data) for c in captured.values() if c.data is not None))
    started_bytes = {n.path: _sha(captured[n.real].data) for n in names.scripts
                     if not n.outside and n.real in captured and captured[n.real].when == "before"
                     and captured[n.real].data is not None}

    # What changed, hashed from its bytes, and explained by the runs and imports the window's lines
    # completed.
    items = []
    counts = dict.fromkeys(relations.OBSERVED_COUNTS, 0)
    if changes_list is not None:
        lines = (window_lines(project.journal, prior.tail[0], tail_after[0])
                 if prior.tail is not None and tail_after is not None else None)
        explained, deleted, runs_read = _explainers(project, lines or [])
        budget = {"bytes": 0}
        for change in changes_list:
            path, kind = change["path"], change["change"]
            item = {"path": path, "change": kind, "bytes": None, "sha256": None, "hash": None,
                    "explanation": "unknown", "by": None, "reason": None, "same_bytes_possible": False}
            if kind in ("created", "modified"):
                real = after_state.real.get(path) or os.path.join(project.root, *path.split("/"))
                item["bytes"] = change["stat"][0]
                if secret_file(path):
                    item["hash"] = "withheld"
                else:
                    item["sha256"], item["hash"] = _hash(real, change["stat"], budget, job)
                    if item["sha256"] is not None and cache is not None:
                        with contextlib.suppress(Exception):
                            cache.remember(os.path.join(project.root, *path.split("/")), change["stat"][0],
                                           change["stat"][1], item["sha256"])
                if change["identity_only"]:
                    started = started_bytes.get(path)
                    if started is not None and item["sha256"] == started:
                        continue  # the same bytes put back, as read at the start of this window
                    item["same_bytes_possible"] = started is None
            else:
                item["hash"] = kind  # deleted, unreadable
            if lines is None:
                item["reason"] = "window_unread"
            elif kind == "deleted":
                by = deleted.get(path)
                item["explanation"], item["by"] = ("explained", by) if by else ("unexplained", None)
            elif item["sha256"] is None:
                item["reason"] = "no_hash"
            elif explained.get((path, item["sha256"])) or explained.get((path, None)):
                item["explanation"] = "explained"
                item["by"] = explained.get((path, item["sha256"])) or explained.get((path, None))
            elif runs_read:
                item["explanation"] = "unexplained"
            else:
                item["reason"] = "run_record_unread"
            counts[kind] += 1
            if item["explanation"] != "explained":
                counts[item["explanation"]] += 1
            items.append(item)
        counts["total"] = len(items)
        counts["links_changed"] = links
        outcome.observed = True
        outcome.unexplained, outcome.unknown = counts["unexplained"], counts["unknown"]

    # Decision 1: why it is recorded.
    cwd_identity, cwd_outside = _cwd(project, prior.cwd)
    inside = [n for n in names.scripts if not n.outside and n.real in captured] + names.mentioned
    reasons = []
    if call.python_command and (not cwd_outside or inside):
        reasons.append("analysis_command")
    if outcome.unexplained or outcome.unknown:
        reasons.append("changes")
    if outcome.turned_off:
        reasons.append("observation_off")
    if not reasons:
        outcome.reason = "nothing_to_record"
        return outcome

    # Decision 5: the command, redacted before anything is kept of it.
    redacted, count = redact(call.command)
    full = redacted.encode("utf-8")
    truncated = len(full) > COMMAND_ATTACHED
    kept = full[:COMMAND_ATTACHED].decode("utf-8", errors="ignore").encode("utf-8") if truncated else full
    inline, cut = _cut_utf8(kept.decode("utf-8"), COMMAND_INLINE)
    attachments: dict[str, tuple[bytes, str]] = {}
    if cut or truncated:
        attachments["command.txt"] = (kept, "command")
    all_items = sorted(items, key=lambda i: i["path"])
    if changes_list is not None:
        changes = {"state": "observed", "reason": None, **{name: counts[name] for name in relations.OBSERVED_COUNTS},
                   "items": all_items, "items_complete": True}
    else:
        changes = {"state": "not_observed", "reason": reason or "not_viewed",
                   **dict.fromkeys(relations.OBSERVED_COUNTS), "items": [], "items_complete": False}
    shown = _fits(all_items, CHANGES_INLINE)
    if shown < len(all_items):
        attachments["changes.json"] = (_json_bytes(all_items[:CHANGES_ATTACHED]), "changes")
        changes["items"] = all_items[:shown]
        changes["items_complete"] = len(all_items) <= CHANGES_ATTACHED

    # Decision 4: the scripts and their copies, the other files the command named.
    script_items, copies = _scripts(project, names, captured)
    mentioned_paths = [n.path for n in names.mentioned]
    scripts = {"total": len(script_items), "items": script_items, "items_complete": True}
    mentioned = {"total": len(mentioned_paths), "unresolved": names.unresolved, "items": mentioned_paths,
                 "items_complete": True}
    overflow = {}
    shown = _fits(script_items, SCRIPTS_INLINE)
    if shown < len(script_items):
        overflow["scripts"], scripts["items"] = script_items, script_items[:shown]
    shown = _fits(mentioned_paths, MENTIONED_INLINE)
    if shown < len(mentioned_paths):
        overflow["mentioned"], mentioned["items"] = mentioned_paths, mentioned_paths[:shown]
    if overflow:
        attachments["files.json"] = (_json_bytes(overflow), "files")
    for rel, data in copies.items():
        attachments[f"scripts/{rel}"] = (data, "script")

    details = {
        "contract": CONTRACT, "activity_id": prior.activity_id,
        "performer": {"actor": "agent", "tool": "bash", "tool_call_id": call.tool_call_id},
        "started_at": _iso(prior.started), "ended_at": _iso(_now()),
        "duration_ms": max(0, round((time.monotonic() - prior.started_mono) * 1000)),
        "cwd": cwd_identity, "cwd_outside": cwd_outside,
        "command": {"text": inline, "bytes": len(full), "sha256": _sha(full),
                    "inline_complete": not cut and not truncated, "truncated": truncated,
                    "changed_after_start": changed_after_start,
                    "redaction": {"rules_version": RULES_VERSION, "count": count}},
        "result": {"is_error": call.is_error, "exit_code": call.exit_code},
        "reasons": reasons,
        "window": {"seq_before": prior.tail[1] if prior.tail else None,
                   "seq_after": tail_after[1] if tail_after else None},
        "changes": changes, "scripts": scripts, "mentioned": mentioned,
        "session_observation": "off" if (outcome.turned_off or sessions.off(session)) else "on",
        "captures": _captures(attachments),
    }
    text = summary_text(redacted, changes)
    if len(json.dumps(details, ensure_ascii=False).encode("utf-8")) > DETAILS_BYTES:
        details = _minimal(details, attachments, kept, all_items, script_items, mentioned_paths)

    # The commit: attachments first, then the event under the journal lock (the event is the commit
    # point; a folder without one is incomplete and counts for nothing).
    folder = None
    if attachments:
        if job.cancelled():
            outcome.reason = "cancelled"
            return outcome
        folder = Path(project.state_dir) / ATTACHMENTS / prior.activity_id
        try:
            folder.parent.mkdir(parents=True, exist_ok=True)
            os.mkdir(folder)  # exclusive: an existing folder is an error
        except OSError:
            outcome.reason = "attachments_failed"
            return outcome
        try:
            for name, (data, _role) in attachments.items():
                path = folder.joinpath(*name.split("/"))
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "xb") as fh:
                    fh.write(data)
        except OSError:
            _remove(folder)
            outcome.reason = "attachments_failed"
            return outcome
    try:
        with writer.locked(OPERATION, within=job.remaining()) as held:
            if not job.begin_commit():
                raise _Abandoned
            outcome.event = held.append(KIND, text, details=details)
    except _Abandoned:
        _remove(folder)
        outcome.reason = "cancelled"
    except _platform.LockBusy:
        _remove(folder)
        outcome.reason = "journal_busy"
    except Exception as exc:  # noqa: BLE001 - a refusal, a full disk: no record, the tool result stands
        _remove(folder)
        outcome.reason = getattr(exc, "code", None) or "write_failed"
    if outcome.event is not None and cache is not None:
        with contextlib.suppress(Exception):
            cache.save()
    return outcome


def _captures(attachments: dict) -> list:
    return [{"path": name, "role": role, "bytes": len(data), "sha256": _sha(data)}
            for name, (data, role) in sorted(attachments.items())]


class _Abandoned(Exception):
    pass


def _remove(folder) -> None:
    if folder is not None:
        with contextlib.suppress(OSError):
            shutil.rmtree(folder)


def _cwd(project, cwd: str) -> tuple[str | None, bool]:
    """(the project-relative POSIX path of aelix's working directory, `.` at the root, and False; or
    None and True outside the project): no absolute path is kept."""
    try:
        identity = project.rel(cwd)
    except (OSError, ValueError):
        return None, True
    return (None, True) if os.path.isabs(identity) else (identity, False)


def _scripts(project, names: Names, captured: dict) -> tuple[list, dict]:
    """The script items (decision 4) and the copies to attach, by the path a record holds. A script
    whose bytes were read is copied unless its name may hold a secret or its bytes match the redaction
    rules (neither hashed then: `withheld`), git ignores it (`ignored`), it is a notebook (`notebook`)
    or it is outside the project (`outside`: its last name, size and sha256 only). The same bytes are
    copied once and every script with them names that copy."""
    items, copies, by_sha = [], {}, {}
    ignored = _ignored(project, [n.path for n in names.scripts if not n.outside and n.real in captured
                                 and captured[n.real].data is not None])
    for name in names.scripts:
        c = captured.get(name.real)
        if c is None and not os.path.isfile(name.real):
            continue  # a name with a script's suffix that never led to a file
        shown = PurePosixPath(name.word.replace("\\", "/")).name if name.outside else name.word
        item = {"name": redact(shown)[0], "path": name.path, "bytes": None, "sha256": None, "hash": None,
                "captured": None, "stable": None, "copy": None, "copy_reason": None, "outside": name.outside}
        items.append(item)
        if c is None:  # not read in time
            item["hash"] = item["copy_reason"] = "over_budget"
            continue
        item["bytes"] = c.stat[0] if c.stat else None
        if c.when == "before":
            now = tree_state.stat_of(name.real)
            item["stable"] = now is not None and c.stat is not None and tree_state.same(now, c.stat)
        if c.reason == "withheld" or secret_file(name.real):
            item["hash"], item["copy_reason"] = "withheld", "secret_name"
            continue
        if c.data is None:
            item["hash"] = item["copy_reason"] = c.reason or "unreadable"
            continue
        item["captured"] = c.when
        if redact(c.data.decode("utf-8", errors="replace"))[1]:
            item["hash"], item["copy_reason"] = "withheld", "secret_like"
            continue
        item["sha256"], item["hash"] = _sha(c.data), "hashed"
        if name.outside:
            item["copy_reason"] = "outside"
        elif name.real.lower().endswith(".ipynb"):
            item["copy_reason"] = "notebook"
        elif name.path in ignored:
            item["copy_reason"] = "ignored"
        else:
            if item["sha256"] not in by_sha:
                by_sha[item["sha256"]] = f"scripts/{name.path}"
                copies[name.path] = c.data
            item["copy"] = by_sha[item["sha256"]]
    return items, copies


# The places a new project's .gitignore leaves out (its template), when git cannot be asked.
_TEMPLATE_IGNORED = (".venv/", "data/", "workspace/", ".analytics/cache/", "runs/")


def _ignored(project, paths: list[str]) -> set[str]:
    """The paths git ignores in the project (`git check-ignore`, once), or — without git — those below a
    place the new project template's .gitignore leaves out."""
    if not paths:
        return set()
    git = shutil.which("git")
    if git and (Path(project.root) / ".git").exists():
        import subprocess  # noqa: PLC0415 - only when there is a git work tree to ask

        with contextlib.suppress(Exception):
            done = subprocess.run([git, "-C", str(project.root), "check-ignore", "--no-index", "-z", "--stdin"],
                                  input="\0".join(paths).encode("utf-8"), capture_output=True, timeout=1.0,
                                  check=False)
            if done.returncode in (0, 1):
                return {p for p in done.stdout.decode("utf-8", errors="replace").split("\0") if p}
    return {p for p in paths if any(p.startswith(place) for place in _TEMPLATE_IGNORED)}


def _minimal(details: dict, attachments: dict, command: bytes, all_items: list, script_items: list,
             mentioned_paths: list) -> dict:
    """The least details an observation is written with when its details would not fit the budget
    (decision 2): the counts and the attachments' digests, the command and the lists whole in
    attachments (added to `attachments`)."""
    attachments.setdefault("command.txt", (command, "command"))
    if all_items:
        attachments["changes.json"] = (_json_bytes(all_items[:CHANGES_ATTACHED]), "changes")
    attachments["files.json"] = (_json_bytes({"scripts": script_items, "mentioned": mentioned_paths}), "files")
    out = dict(details)
    out["command"] = {**details["command"], "text": "", "inline_complete": False}
    out["changes"] = {**details["changes"], "items": []}
    out["scripts"] = {**details["scripts"], "items": [], "items_complete": True}
    out["mentioned"] = {**details["mentioned"], "items": [], "items_complete": True}
    out["captures"] = _captures(attachments)
    return out
