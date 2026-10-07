"""Start aelix 0.1.0b2 with the caller's own aelix login and everything else in the run's agent directory.

    python tools/model_eval_aelix.py [--lifeline FD] AUTH_FILE [aelix arguments ...]

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
_write_raw: the process exits when they return (with 128 + the signal's number, as the default action
would). tools/model_eval.py sends SIGTERM first and SIGKILL only after a grace longer than aelix's
token request may take. --lifeline FD (POSIX): the read end of a pipe whose write end only the
harness holds; when the harness ends, even by SIGKILL, the read returns EOF and the launcher ends
what aelix started (the model's bash commands run in sessions of their own) and its own process
group, so that nothing keeps working on the caller's login unwatched.
"""

from __future__ import annotations

import contextlib
import functools
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path


class RefreshGuard:
    """Defers SIGTERM and SIGHUP while a token is being refreshed or the login written."""

    def __init__(self) -> None:
        self.active = 0
        self.pending: int | None = None

    def enter(self) -> None:
        self.active += 1

    def leave(self) -> None:
        self.active -= 1
        if self.active <= 0 and self.pending is not None:
            os._exit(128 + self.pending)

    def on_signal(self, signum, _frame) -> None:
        if self.active > 0:
            self.pending = signum
            return
        os._exit(128 + signum)

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


def watch(fd: int) -> None:
    """Block until the harness's end of the lifeline closes, then stop everything aelix started."""
    with contextlib.suppress(OSError):
        while os.read(fd, 1):
            pass
    for pid in descendants(os.getpid()):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
    with contextlib.suppress(OSError):
        os.killpg(os.getpgrp(), signal.SIGTERM)  # uv and this process: the guard defers a refresh


def main() -> None:
    args = sys.argv[1:]
    lifeline = None
    if args[:1] == ["--lifeline"] and len(args) >= 2 and args[1].isdigit():
        lifeline, args = int(args[1]), args[2:]
    if not args:
        print("usage: model_eval_aelix.py [--lifeline FD] AUTH_FILE [aelix arguments ...]", file=sys.stderr)
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
    original = auth_storage.AuthStorage.__init__

    def open_at_login(self, path=None):
        if path is not None and Path(path) == store:
            path = auth
        original(self, path)

    auth_storage.AuthStorage.__init__ = open_at_login
    guard = RefreshGuard()
    guard.install(auth_storage.AuthStorage)
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), guard.on_signal)
    if lifeline is not None and os.name != "nt":
        os.set_inheritable(lifeline, False)  # not passed on to aelix's tools
        threading.Thread(target=watch, args=(lifeline,), daemon=True).start()
    entry.main_sync()


if __name__ == "__main__":
    main()
