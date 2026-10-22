"""Write down, inside a CI job's artifact, what that job ran: so a record cites one run and nothing else.

    python tools/ci_context.py context --output FILE        the run, the commit it checked out, the event
    python tools/ci_context.py environment --output FILE    the interpreter and every distribution it sees
    python tools/ci_context.py interpreters --output FILE   the CPython 3.11 and 3.12 uv finds outside any venv

`context` reads GitHub's variables and the ones the workflow passes (CI_HEAD_SHA, CI_BASE_SHA,
CI_ACTION, CI_JOB). The commit CI measures is the pull request's head, not the merge
commit GitHub builds, and a record names it. `environment` runs inside an environment built from the
same requirements as the leg it describes (for an aelix leg, the same `uv run --isolated --with ...`
set, resolved again after the test legs), because those resolve fresh from the index on every run. Stdlib only, so it runs on the setup-python floor as well.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

GITHUB = ("GITHUB_EVENT_NAME", "GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_REF", "GITHUB_RUN_ID",
          "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion")
PASSED = ("CI_HEAD_SHA", "CI_BASE_SHA", "CI_ACTION", "CI_JOB")


def context() -> dict:
    return {**{k: os.environ.get(k) for k in GITHUB + PASSED},
            "platform": platform.platform(), "python": sys.version}


def environment() -> dict:
    return {"executable": sys.executable, "version": sys.version, "prefix": sys.prefix,
            "base_prefix": sys.base_prefix,
            "distributions": sorted(f"{d.metadata['Name']}=={d.version}"
                                    for d in importlib.metadata.distributions())}


def interpreters() -> dict:
    found = {}
    for version in ("3.11", "3.12"):
        try:
            path = subprocess.run(["uv", "python", "find", "--system", version], capture_output=True,
                                  encoding="utf-8", timeout=120, check=True).stdout.strip()
            probe = ("import json, platform, sys; print(json.dumps({'version': platform.python_version(),"
                     " 'implementation': platform.python_implementation(), 'executable': sys.executable}))")
            found[version] = json.loads(subprocess.run([path, "-c", probe], capture_output=True,
                                                       encoding="utf-8", timeout=120, check=True).stdout)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            found[version] = {"error": repr(exc)}
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write down what a CI job ran.")
    parser.add_argument("what", choices=("context", "environment", "interpreters"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    data = {"context": context, "environment": environment, "interpreters": interpreters}[args.what]()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
