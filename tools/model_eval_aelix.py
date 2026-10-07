"""Start aelix 0.1.0b2 with the caller's own aelix login and everything else in the run's agent directory.

    python tools/model_eval_aelix.py [--lifeline FD --report FD] [--redact-run DIR] [--redact-work DIR]
                                     AUTH_FILE [aelix arguments ...]

tools/model_eval.py starts this with the Python that has aelix (uv run --with aelix==0.1.0b2 python)
for a model whose configuration says its credentials come from aelix's own login (auth.source
aelix_login, e.g. the openai-codex provider's ChatGPT login), with AELIX_CODING_AGENT_DIR set to the
run's agent directory.

aelix 0.1.0b2 keeps its state in one agent directory: AELIX_CODING_AGENT_DIR, else ~/.aelix/agent
(cli/config.py get_agent_dir), holds settings.json, trust.json, models.json, update_check.json and
auth.json, and the command line opens its credential store as AuthStorage(<agent directory>/auth.json)
(cli/entry.py, both the --list-models path and the main path). No variable or flag moves the store
alone: AELIX_AUTH_PATH is read only by an AuthStorage made without a path, which the command line
never makes. This launcher changes that one path: an AuthStorage opened at <agent directory>/auth.json
is opened at AUTH_FILE. aelix then reads, refreshes and writes the login itself, in place, as the
caller's own aelix does: an expired token is refreshed under the file's lock and written back by
replacing AUTH_FILE atomically in its own directory (aelix_ai/oauth/auth_storage.py _write_raw:
AUTH_FILE.tmp, then os.replace), so a refreshed token stays the caller's. The agent directory
(settings, trust, models.json, update check, sessions) stays the run's.

Not a link or a copy in the agent directory: aelix writes a refreshed login by replacing
<agent directory>/auth.json, so over a link it would replace the link with a new file inside the run
(the new tokens would land there, and the caller's file would keep a refresh token the server has
already used); a copy does the same from the start. With this launcher no file in the agent
directory names or holds the login. AUTH_FILE itself must be a regular file (not a link) that
exists: aelix's lock would otherwise create an empty login there.

Stopping. A refresh is a POST to the provider, whose answer rotates the refresh token, then the write
of the login; a process killed between the two leaves the caller's file with a used refresh token.
SIGTERM and SIGHUP are therefore deferred while aelix is inside AuthStorage.get_oauth_api_key or
_write_raw: the process ends when they return (with 128 + the signal's number, as the default action
would). tools/model_eval.py sends SIGTERM first and SIGKILL only after a grace longer than aelix's
token request may take. --lifeline FD (POSIX): the read end of a pipe whose write end only the
harness holds; when the harness ends, even by SIGKILL, the read returns EOF and the launcher ends
what aelix started (the model's bash commands run in sessions of their own) and its own process
group, so that nothing keeps working on the caller's login unwatched.

Masking the login's exact values (POSIX: --lifeline, --report and the directories to mask). This
process is the one that holds the login: it keeps, in memory only, every credential value aelix's
AuthStorage read, wrote or returned (the provider entries' strings but the fields that are not
credentials, at least MIN_VALUE_LENGTH characters; the API keys get_*api_key resolved) and, once,
what the login's file holds when the run ends (another aelix of the caller may have refreshed it).
When aelix's main has returned, or a SIGTERM/SIGHUP stops it (after a token refresh in progress), it
leaves the work root (chdir /), tells the harness on --report that it is waiting, and waits for the
harness's commands on the lifeline: `all` (after the harness has waited for what still works in the
work root and moved it into the run) masks every file under --redact-run and --redact-work by each
value's forms (forms_of), `record` masks the run's run.json once more after the harness wrote it,
`exit` ends the process with aelix's exit status. When the lifeline closes instead (the harness is
gone), it stops aelix's descendants, waits for them for ORPHAN_GRACE seconds and masks everything on
its own. A masked file replaces the old one (a temporary file in its directory, then os.replace),
links are not followed (a link holding a value in its target is replaced by a link to the masked
target), a name holding a value is renamed, and a file that is the login's own or has a name outside
the masked directories (a hard link) is not read. Its report (JSON lines on --report) holds counts and
relative paths only: never a value, a value's length or its hash. The values are never written,
printed, logged or passed on.
"""

from __future__ import annotations

import base64
import contextlib
import functools
import json
import logging
import os
import queue
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import Counter
from pathlib import Path
from urllib.parse import quote, quote_plus

USAGE = ("usage: model_eval_aelix.py [--lifeline FD --report FD] [--redact-run DIR] [--redact-work DIR] "
         "AUTH_FILE [aelix arguments ...]")
OPTIONS = ("--lifeline", "--report", "--redact-run", "--redact-work")
# The shortest value compared: a credential is longer, and a shorter field of an entry ("Bearer",
# "oauth", a scope's word) would mark ordinary text.
MIN_VALUE_LENGTH = 16
# The fields of a login's provider entries that are not credentials (aelix 0.1.0b2, aelix_ai/oauth/:
# the openai-codex entry's accountId and its token response's token_type and scope, the github-copilot
# entry's enterpriseUrl, an account's or organization's uuid, email and name); every other string is.
NOT_CREDENTIALS = frozenset({"type", "token_type", "scope", "expires", "expires_in", "expires_at",
                             "accountid", "account_id", "enterpriseurl", "enterprise_url", "uuid",
                             "email", "email_address", "name", "endpoint", "provider"})
# How long the launcher waits for aelix's descendants to end once the harness is gone, before it masks.
ORPHAN_GRACE = 10
MAX_LISTED = 200


class RefreshGuard:
    """Defers SIGTERM and SIGHUP while a token is being refreshed or the login written. finisher(code,
    why) ends the process: by default at once; with masking, through Ending.finish."""

    def __init__(self) -> None:
        self.active = 0
        self.pending: int | None = None
        self.finisher = lambda code, _why: os._exit(code)

    def enter(self) -> None:
        self.active += 1

    def leave(self) -> None:
        self.active -= 1
        if self.active <= 0 and self.pending is not None:
            signum, self.pending = self.pending, None
            self.finisher(128 + signum, f"signal {signum}, after the login was written")

    def on_signal(self, signum, _frame) -> None:
        if self.active > 0:
            self.pending = signum
            return
        self.finisher(128 + signum, f"signal {signum}")

    def install(self, storage) -> None:
        get, write = storage.get_oauth_api_key, storage._write_raw
        guard = self

        @functools.wraps(get)
        async def get_oauth_api_key(self, *args, **kwargs):
            guard.enter()
            try:
                return await get(self, *args, **kwargs)
            finally:
                guard.leave()

        @functools.wraps(write)
        def _write_raw(self, *args, **kwargs):
            guard.enter()
            try:
                return write(self, *args, **kwargs)
            finally:
                guard.leave()

        storage.get_oauth_api_key = get_oauth_api_key
        storage._write_raw = _write_raw


# --- the login's values and their forms -----------------------------------------------------------

class Values:
    """The login's credential values this process has held, in memory only: never written, printed,
    logged, passed on or returned."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def __len__(self) -> int:
        return len(self._values)

    def add(self, value) -> None:
        if not isinstance(value, str) or len(value) < MIN_VALUE_LENGTH:
            return
        # A stored key aelix resolves (aelix_ai/oauth/_resolve_config.py): "!command" runs the
        # command, the name of a set variable stands for its value. What it resolves to comes back
        # through get_api_key and is kept there.
        try:
            reference = value.startswith("!") or bool(os.environ.get(value))
        except (ValueError, TypeError):  # not a variable's name (a NUL in it)
            reference = False
        if not reference:
            self._values.add(value)

    def add_entries(self, data) -> None:
        """Every string in a login's provider entries ({provider: {field: value}}), but the fields
        that are not credentials (NOT_CREDENTIALS)."""
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(key, str) and key.lower() in NOT_CREDENTIALS:
                    continue
                if isinstance(value, str):
                    self.add(value)
                else:
                    self.add_entries(value)
        elif isinstance(data, (list, tuple)):
            for item in data:
                if isinstance(item, str):
                    self.add(item)
                else:
                    self.add_entries(item)

    def add_storage(self, storage) -> None:
        """What an AuthStorage holds in memory: its entries, runtime keys and resolved keys."""
        with contextlib.suppress(Exception):
            self.add_entries(getattr(storage, "_data", None))
        for name in ("_runtime_overrides", "_resolve_cache"):
            with contextlib.suppress(Exception):
                for value in (getattr(storage, name, None) or {}).values():
                    self.add(value)

    def install(self, storage) -> None:
        """Keep what AuthStorage reads (_read_raw), writes (_write_raw: a refreshed login) and returns
        (the API key a get_*api_key resolved)."""
        keep = self
        read, write = storage._read_raw, storage._write_raw

        @functools.wraps(read)
        def _read_raw(self, *args, **kwargs):
            data = read(self, *args, **kwargs)
            keep.add_entries(data)
            return data

        @functools.wraps(write)
        def _write_raw(self, data, *args, **kwargs):
            keep.add_entries(data)
            return write(self, data, *args, **kwargs)

        def returning(method):
            @functools.wraps(method)
            async def wrapper(self, *args, **kwargs):
                result = await method(self, *args, **kwargs)
                keep.add(result)
                return result
            return wrapper

        storage._read_raw = _read_raw
        storage._write_raw = _write_raw
        for name in ("get_oauth_api_key", "get_api_key", "get_api_key_cascade"):
            if callable(getattr(storage, name, None)):
                setattr(storage, name, returning(getattr(storage, name)))

    def forms(self) -> list[tuple[bytes, bytes]]:
        """Every form of every value with its marker, the longest first."""
        found: dict[bytes, bytes] = {}
        for value in self._values:
            for form, mark in forms_of(value):
                found.setdefault(form, mark)
        return sorted(found.items(), key=lambda item: -len(item[0]))


def marker(length: int) -> bytes:
    """tools/model_eval.py's redaction_marker: as long as what it replaces, so that a binary file
    keeps its layout."""
    if length < 10:
        return b"*" * length
    return b"<redacted" + b"*" * (length - 10) + b">"


def forms_of(value: str) -> list[tuple[bytes, bytes]]:
    """The forms a value takes in a file, each with its marker: its UTF-8 bytes; JSON-escaped (ASCII
    or not, with "/" escaped as "\\/" or not, and escaped twice: a JSON string inside a JSON string,
    as a tool result in an event line); URL-encoded (quote, quote_plus, "/" kept or not, upper- or
    lower-case hex); base64 and base64url at each of the three alignments (the characters that only
    the value's bits decide, so also inside a longer encoded run); the lower- and upper-case hex of
    its UTF-8 bytes; UTF-16LE and UTF-16BE. Not: a value split across lines (a wrapped base64 or hex
    dump, a folded header) or encoded otherwise (compressed, a part of it)."""
    raw = value.encode("utf-8")
    texts = {value}
    for escaped in (json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1]):
        for text in (escaped, escaped.replace("/", "\\/")):
            texts |= {text, json.dumps(text)[1:-1]}
    for encoded in (quote(value, safe=""), quote(value), quote_plus(value), quote_plus(value, safe="/")):
        texts |= {encoded, re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), encoded)}
    found = {text.encode("utf-8") for text in texts}
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        for shift in range(3):
            encoded = encode(b"\0" * shift + raw)
            found.add(encoded[-(-8 * shift // 6):8 * (shift + len(raw)) // 6])
    found |= {raw.hex().encode("ascii"), raw.hex().upper().encode("ascii")}
    forms = [(form, marker(len(form))) for form in found if form]
    for codec in ("utf-16-le", "utf-16-be"):
        form = value.encode(codec)
        forms.append((form, marker(len(form) // 2).decode("ascii").encode(codec)))
    return forms


def mask(data: bytes, forms) -> tuple[bytes, int]:
    """data with every form replaced by its marker, and how many were."""
    count = 0
    for form, mark in forms:
        if form in data:
            count += data.count(form)
            data = data.replace(form, mark)
    return data, count


def masked_name(name: bytes, forms) -> tuple[bytes, int]:
    """A file name with every form replaced by "redacted": a name has no layout to keep, and a
    marker of the form's length would tell the length in the paths the report lists."""
    count = 0
    for form, _ in forms:
        if form in name:
            count += name.count(form)
            name = name.replace(form, b"redacted")
    return name, count


# --- masking the run's files ----------------------------------------------------------------------

def replace_bytes(path: str, data: bytes) -> None:
    """data as a new file in path's place (tools/model_eval.py's replace_bytes): never written into
    the old file, so another name of it is not written through."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".masked-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        with contextlib.suppress(OSError):
            os.chmod(tmp, stat.S_IMODE(os.lstat(path).st_mode))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def replace_link(path: str, target: bytes) -> None:
    """A new symbolic link to target in path's place (the link is replaced, never followed)."""
    directory = os.fsencode(os.path.dirname(path))
    for attempt in range(100):
        tmp = os.path.join(directory, b".masked-link-%d-%d" % (os.getpid(), attempt))
        try:
            os.symlink(target, tmp)
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError("no free name for the masked link")
    try:
        os.replace(tmp, os.fsencode(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_private(path: str, st: os.stat_result) -> bytes:
    """A regular file's bytes, opened without following a link or blocking on a pipe; an OSError when
    the name no longer is the file the walk saw."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        now = os.fstat(fd)
        if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            raise OSError(0, "the file changed while it was masked")
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def unique_name(path: str) -> str:
    if not os.path.lexists(path):
        return path
    for n in range(1, 1000):
        candidate = f"{path}-{n}"
        if not os.path.lexists(candidate):
            return candidate
    raise FileExistsError("no free name for the masked name")


class Masking:
    """One pass over the directories to mask: counts and relative paths, never a value."""

    def __init__(self, forms, login_ids: set) -> None:
        self.forms = forms
        self.login_ids = login_ids
        self.files = self.findings = 0
        self.masked_files: list[str] = []
        self.renamed: list[str] = []
        self.links_rewritten: list[str] = []
        self.unreadable: list[dict] = []
        self.missing: list[str] = []

    def rel(self, path: str, root: str, prefix: str) -> str:
        """path relative to root with every component's values masked (the names as they are after
        the pass)."""
        parts = Path(os.path.relpath(path, root)).parts
        return prefix + "/".join(os.fsdecode(masked_name(os.fsencode(p), self.forms)[0]) for p in parts)

    def failed(self, rel: str, why: str) -> None:
        self.unreadable.append({"file": rel, "error": why})

    def walk(self, root: str, prefix: str) -> list[tuple[str, os.stat_result]]:
        found = []

        def onerror(exc: OSError) -> None:
            where = exc.filename if isinstance(exc.filename, str) else root
            self.failed(self.rel(where, root, prefix), f"not listed: {exc.strerror or type(exc).__name__}")

        for dirpath, dirnames, filenames in os.walk(root, onerror=onerror, followlinks=False):
            for name in sorted(dirnames + filenames):
                path = os.path.join(dirpath, name)
                try:
                    found.append((path, os.lstat(path)))
                except OSError as exc:
                    self.failed(self.rel(path, root, prefix), f"not looked at: {exc.strerror or type(exc).__name__}")
        return found

    def file(self, path: str, st: os.stat_result, rel: str, names: Counter) -> None:
        key = (st.st_dev, st.st_ino)
        if key in self.login_ids:
            self.failed(rel, "the login's own file: not read (the harness's sweep removes this name)")
            return
        if st.st_nlink > names.get(key, 1):
            self.failed(rel, "a file with another name outside the masked directories (a hard link): not read")
            return
        try:
            data = read_private(path, st)
        except OSError as exc:
            self.failed(rel, f"not read: {exc.strerror or type(exc).__name__}")
            return
        self.files += 1
        masked, count = mask(data, self.forms)
        if not count:
            return
        try:
            replace_bytes(path, masked)
        except OSError as exc:
            self.failed(rel, f"not masked: {exc.strerror or type(exc).__name__}")
            return
        self.findings += count
        self.masked_files.append(rel)

    def link(self, path: str, rel: str) -> None:
        try:
            target = os.readlink(os.fsencode(path))
        except OSError as exc:
            self.failed(rel, f"a link not read: {exc.strerror or type(exc).__name__}")
            return
        masked, count = mask(target, self.forms)
        if not count:
            return
        try:
            replace_link(path, masked)
        except OSError as exc:
            self.failed(rel, f"a link not masked: {exc.strerror or type(exc).__name__}")
            return
        self.findings += count
        self.links_rewritten.append(rel)

    def name(self, path: str, rel_after) -> None:
        new, count = masked_name(os.fsencode(os.path.basename(path)), self.forms)
        if not count:
            return
        try:
            target = unique_name(os.path.join(os.path.dirname(path), os.fsdecode(new)))
            os.rename(path, target)
        except OSError as exc:
            self.failed(rel_after(path), f"a name holding a value was not changed: {exc.strerror or type(exc).__name__}")
            return
        self.findings += count
        self.renamed.append(rel_after(target))

    def trees(self, roots: list[tuple[Path, str]]) -> None:
        listed = []
        for root, prefix in roots:
            base = str(root)
            if not os.path.lexists(base):
                self.missing.append(prefix or ".")
                continue
            if os.path.islink(base) or not os.path.isdir(base):
                self.failed(prefix or ".", "not a directory (a link is not followed)")
                continue
            listed.append((base, prefix, self.walk(base, prefix)))
        # The names of each file with several, over all the directories: one whose names are all here
        # is masked name by name; one with a name elsewhere is not read.
        names = Counter((st.st_dev, st.st_ino) for _, _, items in listed for _, st in items
                        if stat.S_ISREG(st.st_mode) and st.st_nlink > 1)
        for base, prefix, items in listed:
            for path, st in items:
                if stat.S_ISREG(st.st_mode):
                    self.file(path, st, self.rel(path, base, prefix), names)
                elif stat.S_ISLNK(st.st_mode):
                    self.link(path, self.rel(path, base, prefix))
            # Names last, the deepest first: a renamed directory would move what is below it.
            for path, _ in sorted(items, key=lambda item: item[0].count(os.sep), reverse=True):
                self.name(path, lambda p, b=base, x=prefix: self.rel(p, b, x))

    def single(self, path: Path, rel: str) -> None:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            self.missing.append(rel)
            return
        except OSError as exc:
            self.failed(rel, f"not looked at: {exc.strerror or type(exc).__name__}")
            return
        if stat.S_ISREG(st.st_mode):
            self.file(str(path), st, rel, Counter())

    def report(self) -> dict:
        return {"files": self.files, "findings": self.findings, "masked_files": self.masked_files[:MAX_LISTED],
                "renamed": self.renamed[:MAX_LISTED], "links_rewritten": self.links_rewritten[:MAX_LISTED],
                "unreadable": self.unreadable[:MAX_LISTED], "missing": self.missing}


# --- processes ------------------------------------------------------------------------------------

def descendants(root: int) -> list[int]:
    """The processes below root, from ps (empty when it cannot be read)."""
    try:
        done = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.append(child)
                stack.append(child)
    return found


def stop_descendants(sig: int = signal.SIGTERM) -> list[int]:
    pids = descendants(os.getpid())
    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, sig)
    return pids


def watch(fd: int) -> None:
    """Without masking: block until the harness's end of the lifeline closes, then stop everything
    aelix started."""
    with contextlib.suppress(OSError):
        while os.read(fd, 1):
            pass
    stop_descendants()
    with contextlib.suppress(OSError):
        os.killpg(os.getpgrp(), signal.SIGTERM)  # uv and this process: the guard defers a refresh


# --- the end of a run that is masked --------------------------------------------------------------

class Ending:
    """The end of the launcher when the run is masked: after aelix's main returned, on SIGTERM and
    SIGHUP (RefreshGuard: after a refresh in progress) and when the lifeline closes."""

    def __init__(self, values: Values, gather, roots: list[tuple[Path, str]], login_ids,
                 lifeline: int | None, report: int | None) -> None:
        self.values = values
        self.gather = gather  # puts what the AuthStorages hold (and, once, the login's file) into values
        self.roots = roots
        self.login_ids = login_ids
        self.lifeline = lifeline
        self.report = report
        self.commands: queue.Queue = queue.Queue()
        self.finishing = False
        self.masked = False

    def listen(self) -> None:
        """The harness's commands, a line each; at EOF (the harness is gone, even killed outright) None,
        and what aelix started is stopped, then aelix (whose stop ends here through finish)."""
        buffer = b""
        while True:
            try:
                chunk = os.read(self.lifeline, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self.commands.put(line.decode("ascii", errors="replace").strip())
        self.commands.put(None)
        stop_descendants()
        if not self.finishing:
            with contextlib.suppress(OSError):
                os.killpg(os.getpgrp(), signal.SIGTERM)  # uv and this process: the guard defers a refresh

    def send(self, message: dict) -> None:
        """One report line to the harness: counts and relative paths. It is masked once more by the
        values' forms as a last guard (a name it lists was already masked)."""
        if self.report is None:
            return
        data = json.dumps(message, ensure_ascii=True).encode("ascii") + b"\n"
        with contextlib.suppress(Exception):
            data = mask(data, self.values.forms())[0]
        with contextlib.suppress(OSError):
            while data:
                data = data[os.write(self.report, data):]

    def run(self, scope: str) -> None:
        """One masking pass: `all` (every directory to mask) or `record` (the run's run.json)."""
        result: dict = {"event": "masked", "scope": scope}
        try:
            self.gather()
            masking = Masking(self.values.forms(), self.login_ids())
            if scope == "record":
                run = next((root for root, prefix in self.roots if prefix == ""), None)
                if run is None:
                    masking.missing.append("run.json")
                else:
                    masking.single(run / "run.json", "run.json")
            else:
                masking.trees(self.roots)
            result.update(masking.report(), values=len(self.values))
        except Exception as exc:  # noqa: BLE001 - reported by its type only: a message could hold a path
            result.update(error=type(exc).__name__, values=len(self.values))
        if scope == "all":
            self.masked = True
        self.send(result)

    def orphaned(self) -> None:
        """The harness is gone: what aelix started is stopped (SIGKILL after ORPHAN_GRACE) before the
        files are masked, so that nothing writes into them afterwards."""
        deadline = time.monotonic() + ORPHAN_GRACE
        while True:
            # aelix's event loop no longer reaps its children: an ended one would stay listed.
            with contextlib.suppress(ChildProcessError, OSError):
                while os.waitpid(-1, os.WNOHANG)[0] > 0:
                    pass
            if not stop_descendants() or time.monotonic() > deadline:
                break
            time.sleep(0.5)
        stop_descendants(signal.SIGKILL)

    def finish(self, code: int, why: str) -> None:
        """Never returns: aelix's state is flushed, the harness is told and its commands are done, then
        the process ends with code. A signal while it runs only returns (it is ending already)."""
        if self.finishing:
            return
        self.finishing = True
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()
        with contextlib.suppress(Exception):
            logging.shutdown()
        with contextlib.suppress(OSError):
            os.chdir(os.path.abspath(os.sep))  # out of the work root, which the harness moves
        if self.lifeline is None or self.report is None:  # no harness to answer: mask now
            self.run("all")
            os._exit(code)
        self.send({"event": "waiting", "pid": os.getpid(), "code": code, "why": why})
        while True:
            command = self.commands.get()
            if command is None:  # the harness is gone
                self.orphaned()
                self.run("all")
                os._exit(code)
            if command in ("all", "record"):
                self.run(command)
            elif command == "exit":
                if not self.masked:
                    self.run("all")
                os._exit(code)


def exit_code(exc: SystemExit) -> int:
    """The status the interpreter would exit with for exc."""
    if exc.code is None:
        return 0
    if isinstance(exc.code, int):
        return exc.code
    print(exc.code, file=sys.stderr)
    return 1


def file_id(path: Path) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def main() -> None:
    args = sys.argv[1:]
    options: dict[str, str] = {}
    while len(args) >= 2 and args[0] in OPTIONS:
        options[args[0]], args = args[1], args[2:]
    fds = {name: options[name] for name in ("--lifeline", "--report") if name in options}
    if not args or any(not value.isdigit() for value in fds.values()) or \
            ("--report" in options) != ("--lifeline" in options):
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)
    if not os.environ.get("AELIX_CODING_AGENT_DIR"):
        print("model_eval_aelix: AELIX_CODING_AGENT_DIR is not set: the agent directory would be the "
              "caller's own and nothing would be isolated", file=sys.stderr)
        raise SystemExit(2)
    auth = Path(args[0])
    sys.argv = [sys.argv[0], *args[1:]]
    if auth.is_symlink() or not auth.is_file():
        print(f"model_eval_aelix: {auth} is not a regular file (missing, or a link): aelix would create "
              "an empty login there, or replace the link when it refreshes a token", file=sys.stderr)
        raise SystemExit(2)

    from aelix_ai.oauth import auth_storage
    from aelix_coding_agent.cli import config, entry

    store = Path(config.get_agent_dir()) / "auth.json"
    if auth == store:
        print(f"model_eval_aelix: {auth} is the agent directory's own store: give the login's file",
              file=sys.stderr)
        raise SystemExit(2)
    storage = auth_storage.AuthStorage
    original = auth_storage.AuthStorage.__init__
    opened: list = []  # every store aelix made: what each holds in memory is compared at the end

    def open_at_login(self, path=None):
        if path is not None and Path(path) == store:
            path = auth
        original(self, path)
        opened.append(self)

    auth_storage.AuthStorage.__init__ = open_at_login
    guard = RefreshGuard()
    guard.install(storage)
    values = Values()
    values.install(storage)
    lifeline = int(fds["--lifeline"]) if "--lifeline" in fds and os.name != "nt" else None
    report = int(fds["--report"]) if "--report" in fds and os.name != "nt" else None
    for fd in (lifeline, report):
        if fd is not None:
            os.set_inheritable(fd, False)  # not passed on to aelix's tools
    roots = [(Path(options[name]), prefix) for name, prefix in (("--redact-run", ""),
                                                                ("--redact-work", "work_root_left/"))
             if name in options]
    ending = None
    if roots:
        start_id = file_id(auth)
        read_file = []

        def gather() -> None:
            for item in list(opened):
                values.add_storage(item)
            if not read_file:  # once: what the login's file holds now (another aelix may have refreshed it)
                read_file.append(True)
                with contextlib.suppress(Exception):
                    storage(auth)._read_raw()

        def login_ids() -> set:
            return {key for key in (start_id, file_id(auth)) if key is not None}

        ending = Ending(values, gather, roots, login_ids, lifeline, report)
        guard.finisher = ending.finish
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), guard.on_signal)
    if lifeline is not None:
        target = ending.listen if ending is not None else functools.partial(watch, lifeline)
        threading.Thread(target=target, daemon=True).start()
    if ending is None:
        entry.main_sync()
        return
    try:
        entry.main_sync()
        code = 0
    except SystemExit as exc:
        code = exit_code(exc)
    except KeyboardInterrupt:
        code = 130
    except BaseException:  # noqa: BLE001 - aelix's own crash: its traceback, then the run is masked
        traceback.print_exc()
        code = 1
    ending.finish(code, "aelix returned")


if __name__ == "__main__":
    main()
