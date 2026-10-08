"""One migration in a process of its own, ended the way an interruption ends it (Issue #13 PR 2,
docs/design/RECORD-WRITER-AND-MIGRATION.md 11.1). tests/test_migration.py starts it with one JSON
argument:

    {"source": path | null, "dest": path, "stop": {...} | null, "no_links": bool, "no_renames": bool}

`stop` names a step `migration.migrate` calls `on_step` with (`step`), the how-many-th call of it
(`nth`, 1 by default) and optionally the path the call is about (`path`). At that call, by `action`:

- `exit` (the default): `os._exit(9)` — no `finally`, no flush of Python's buffers: as close to a
  kill as a test gets, on POSIX and Windows alike;
- `cut`: the ledger line about to be written goes in half, without its line end, then `os._exit(9)`
  (a line cut off by the interruption, F16);
- `hold`: a file `ready` is made and the call waits for `release` to exist, then goes on (another
  migration is tried meanwhile, T7);
- `occupy`: a file is made at the destination's path `occupy`, with the bytes `bytes`, and the
  migration goes on (someone else's file where a copy or the marker is about to be published, T19).

`between_link_and_unlink` (POSIX): the port's publish_new of `path` makes its link and the process
ends before the temporary name is removed (F6, F12). `no_links`: the port refuses to link, as on a
file system without second names (F15); `no_renames`: its rename that refuses a taken name is not
there either (the marker is then refused, Issue #13 PR 3). It prints the migration's response or its
error as one line of JSON.
"""

from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path


def main() -> None:
    args = json.loads(sys.argv[1])
    from aelix_analytics import _platform, core, migration

    stop = args.get("stop") or {}
    real = _platform.detect()
    files = real.files

    class Files:
        def replace(self, src, dst):
            files.replace(src, dst)

        def publish_new(self, src, dst):
            if args.get("no_links"):
                raise _platform.LinkUnsupported(1, "this file system has no links (driver)")
            if stop.get("between_link_and_unlink") and \
                    os.fspath(dst).replace(os.sep, "/").endswith("/" + stop["path"]):
                os.link(src, dst)
                os._exit(9)
            files.publish_new(src, dst)

        def rename_new(self, src, dst):
            if args.get("no_renames"):
                raise _platform.RenameUnsupported(95, "this file system has no exclusive rename (driver)")
            files.rename_new(src, dst)  # the real one: where links are not, it is what publishes

        def flush_handle(self, fh):
            files.flush_handle(fh)

        def flush_dir(self, path):
            files.flush_dir(path)

    platform = types.SimpleNamespace(name=real.name, locks=real.locks, files=Files(),
                                     processes=real.processes, runs=real.runs)
    seen: dict[str, int] = {}

    def on_step(name: str, info: dict) -> None:
        if name != stop.get("step"):
            return
        if stop.get("path") and info.get("path") != stop["path"]:
            return
        seen[name] = seen.get(name, 0) + 1
        if seen[name] != stop.get("nth", 1):
            return
        action = stop.get("action", "exit")
        if action == "hold":
            Path(stop["ready"]).write_text("ready", encoding="utf-8")
            deadline = time.monotonic() + 120
            while not Path(stop["release"]).exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            return
        if action == "occupy":
            Path(args["dest"], stop["occupy"]).write_bytes(stop["bytes"].encode("utf-8"))
            return
        if action == "cut":
            base = Path(args["dest"], ".analytics", "migration")
            [ledger] = list(base.glob("*/progress.jsonl")) or [next(base.iterdir()) / "progress.jsonl"]
            line = json.dumps(info, ensure_ascii=False).encode("utf-8")
            with open(ledger, "ab") as fh:
                fh.write(line[:len(line) // 2])
        os._exit(9)

    try:
        result = migration.migrate(args.get("source"), args["dest"],
                                   origin=core.Origin("human", "cli", "inferred"), platform=platform,
                                   on_step=on_step)
        print(json.dumps(result, ensure_ascii=False))
    except core.AnalyticsError as exc:
        print(json.dumps(exc.to_json(), ensure_ascii=False))


if __name__ == "__main__":
    main()
