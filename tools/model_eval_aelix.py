"""Start aelix 0.1.0b2 with the caller's own aelix login and everything else in the run's agent directory.

    python tools/model_eval_aelix.py AUTH_FILE [aelix arguments ...]

tools/model_eval.py starts this with the Python that has aelix (uv run --with aelix==0.1.0b2 python)
for a model whose configuration says its credentials come from aelix's own login (auth.source
aelix_login, e.g. the openai-codex provider's ChatGPT login), with AELIX_CODING_AGENT_DIR set to the
run's agent directory.

aelix 0.1.0b2 keeps its state in one agent directory: AELIX_CODING_AGENT_DIR, else ~/.aelix/agent
(cli/config.py get_agent_dir), holds settings.json, trust.json, models.json, update_check.json and
auth.json, and the command line opens its credential store as AuthStorage(<agent directory>/auth.json)
(cli/entry.py, both the --list-models path and the main path). No variable or flag moves the store
alone: AELIX_AUTH_PATH is read only by an AuthStorage made without a path, which the command line
never makes. This launcher changes that one path and nothing else: an AuthStorage opened at
<agent directory>/auth.json is opened at AUTH_FILE. aelix then reads, refreshes and writes the login
itself, in place, as the caller's own aelix does: an expired token is refreshed under the file's lock
and written back by replacing AUTH_FILE atomically in its own directory (aelix_ai/oauth/auth_storage.py
_write_raw: AUTH_FILE.tmp, then os.replace), so a refreshed token stays the caller's. The agent
directory (settings, trust, models.json, update check, sessions) stays the run's.

Not a link or a copy in the agent directory: aelix writes a refreshed login by replacing
<agent directory>/auth.json, so over a link it would replace the link with a new file inside the run
(the new tokens would land there, and the caller's file would keep a refresh token the server has
already used); a copy does the same from the start. With this launcher no file in the agent
directory names or holds the login.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: model_eval_aelix.py AUTH_FILE [aelix arguments ...]", file=sys.stderr)
        raise SystemExit(2)
    if not os.environ.get("AELIX_CODING_AGENT_DIR"):
        print("model_eval_aelix: AELIX_CODING_AGENT_DIR is not set: the agent directory would be the "
              "caller's own and nothing would be isolated", file=sys.stderr)
        raise SystemExit(2)
    auth = Path(sys.argv.pop(1))

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
    entry.main_sync()


if __name__ == "__main__":
    main()
