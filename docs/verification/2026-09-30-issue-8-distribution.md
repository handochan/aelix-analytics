# Issue #8 distribution measurement — 2026-09-30

The minimal distribution of [PR #33](https://github.com/handochan/aelix-analytics/pull/33) — one wheel with the
`lab` launcher, the aelix extension and the kit — was built from the checkout, installed into fresh CPython 3.11 and
3.12 environments and run on Ubuntu 24.04 and Windows Server 2025 by GitHub Actions and on a macOS developer machine,
at one commit. On every platform each distribution test that applies there passed: aelix 0.1.0b2 bound and loaded
the installed extension, whose tools created and ran a project, and the installed `lab` drove a project through real
uv without aelix. PR-1's platform checks held as they did for PR #31. This is the durable summary: the CI artifacts
expire after GitHub's retention period. What the results mean for the contract is in [CONTRACTS.md](../CONTRACTS.md)
F and G; the per-check values, the test counts and the outcome of each distribution test are in the machine-readable
result, [2026-09-30-issue-8-distribution.json](2026-09-30-issue-8-distribution.json).

## Source and environment

| Item | Basis |
|---|---|
| Measured code | PR #33 at `7316a8f`. The later commits change documents, a docstring and comments only |
| Measurement branch | `test/issue-8-pr2-measurement` at `cd287d1`, which is that commit plus PR-1's measurement tool (`tools/platform_smoke.py`), its self-tests and the workflow, none of which is part of PR #33. For PR-2 the workflow adds the suite's end-to-end leg (`AELIX_ANALYTICS_TEST_INSTALL=1`), the aelix integration on CPython 3.11, and a record of the interpreters uv found |
| CI run | [36647326310](https://github.com/handochan/aelix-analytics/actions/runs/36647326310): `ubuntu-latest` (image ubuntu-24.04, version 20260920.314.1) and `windows-latest` (image windows-2025-vs2026, version 20260922.246.2), with uv 0.11.19. The development environment ran on the image's CPython 3.12.3 / 3.12.10. The distribution's install environments and the aelix integration ran on the interpreters uv found, which a step after them recorded: CPython 3.11.15 (uv's own build) and 3.12.3 on Linux, 3.11.9 (the image's) and 3.12.10 on Windows. The stdlib-only leg ran on CPython 3.11.16 / 3.11.9 from `actions/setup-python` |
| Developer machine | macOS 26.6.2 arm64, uv 0.11.19: CPython 3.12.13 for the smoke and the suite, 3.11.15 and 3.12.13 for the distribution's install environments and the aelix integration, 3.11.15 for the stdlib-only smoke: the measurement branch at the same commit, clean, run with the CI's commands. Then 3.13.13 and 3.14.5 for the suite without its end-to-end leg, in environments of their own, which have no CI counterpart |
| aelix | 0.1.0b2 from PyPI: beside the wheel in the distribution's tests, and in an environment of its own for `tests/test_extension.py` (`uv run --no-project --with aelix==0.1.0b2`) |

## Results

| Check | Linux | Windows | macOS |
|---|---|---|---|
| The distribution (`tests/test_packaging.py`, with its end-to-end leg) | 20 passed | 18 passed, 2 skipped (POSIX only) | 20 passed |
| The launcher against `bin/lab`'s contract (`tests/test_launcher.py`) | 87 passed | 43 passed, 44 skipped | 87 passed |
| Repository suite (`pytest`, `AELIX_ANALYTICS_TEST_INSTALL=1`), both rows above included | 612 passed, 2 skipped | 464 passed, 149 skipped, none failed | 610 passed, 4 skipped (3.12); without the end-to-end leg, 601 passed, 13 skipped (3.13) and 602 passed, 12 skipped (3.14) |
| aelix 0.1.0b2 integration (`tests/test_extension.py`), CPython 3.12 / 3.11 | 12 / 12 passed | 12 / 12 passed | 12 / 12 passed |
| Platform smoke, required gate (`--gate required`) | held: 25 hold, none violated; 6 observations | held: 21 hold, 3 declared limitations, none violated; 7 observations | held: 25 hold, none violated; 6 observations |
| Stdlib-only smoke on CPython 3.11 (what the aelix process loads) | held: 15 hold | held: 14 hold, 1 declared limitation (the tree limit) | held: 15 hold |

The stdlib-only smoke leaves out the two probes that need uv; that is what the mode is for. The platform smoke's
checks and its declared limitations are PR-1's; their values are in the JSON. The suite's skips on Linux are the
module that needs aelix, which the integration step runs, and the lock case only CPython 3.14 has; macOS adds the two
names that are not UTF-8, which APFS refuses, and on 3.13 and 3.14 the nine end-to-end tests, which run only with
`AELIX_ANALYTICS_TEST_INSTALL=1` (3.14 runs the lock case). Windows's are listed below.

## What the distribution tests ran

The suite builds the wheel and the sdist once from the checkout. Every test that runs installed code does so in
fresh environments of its own, never in the checkout's development environment.

- **The artifacts.** `uv build` makes the wheel and the sdist from the checkout. The wheel holds exactly the tracked
  files of `src/aelix_analytics/` and `kit/labkit/` plus `aelix_analytics/extension.py`, which is the checkout's
  `extension/aelix_lab.py` byte for byte. Its metadata declares `Requires-Python >=3.11`, no dependencies, the `lab`
  console script and the `aelix.extensions` entry point. The manifest agrees with it (id, version, entry point, API
  level, activation) and carries the licence placeholder. The sdist holds the sources, builds the same wheel byte for
  byte, and installed on CPython 3.12 gives a kit of files holding `labkit` alone, with its template.
- **An installation without aelix**, in fresh CPython 3.11 and 3.12 environments: the kit is a directory of files in
  site-packages holding `labkit` alone; there is no top-level `labkit` and no analysis library is imported; `lab --help`
  prints `bin/lab`'s usage. In the 3.11 environment a child gets exactly the installed kit on `PYTHONPATH` (POSIX: the
  stand-in uv that records it is a script).
- **aelix 0.1.0b2 beside the wheel**, on 3.11 and 3.12: `aelix extension verify` reports it BOUND to its manifest;
  `aelix status --json` lists it as an entry-point extension with a manifest and no errors; aelix's loader, run twice
  in one process as aelix does for each session it builds, sets up one copy with the six tools, the `/lab` command and
  its flag, imports one module whose state starts over, takes the installed kit, and imports no analysis library.
- **The README's symlink route beside the wheel** sets up one copy and says why the other is not set up (POSIX,
  CPython 3.12).
- **The installed extension's tools**, on 3.11 and 3.12, called through their own `execute` with no model:
  `lab_init` (with a real `uv sync` of the project environment), `lab_import`, `lab_run` of a script that uses the
  kit, `lab_note`.
- **The installed `lab` without aelix**, on 3.11 and 3.12, with real uv: `init`, `import`, `query`, `run` of a
  script that uses the SDK (a chart with its data, a saved dataset, a note that refers to the run), `note`, `report`,
  `status`, `context`, and `open` with a stand-in opener (POSIX). The run's analysis environment is CPython 3.12 or
  later, and no child writes bytecode of another Python into the installation.
- **`python -m labkit` on CPython 3.11** with the installed kit: `init`, `status`, `context`, `note`, `report`.
- **pip** installs the wheel into a 3.11 environment, `lab init` and `lab status` run, and after uninstalling no
  `aelix_analytics` directory is left in site-packages.

## Windows, test by test

Of the distribution tests, two do not apply: the stand-in uv that records a child's environment is a script, and
Windows starts only an `.exe` by name; the README's symlink route is a POSIX one. Everything else ran and passed,
including the installed `lab` and the installed extension's tools with real uv on 3.11 and 3.12.

The launcher's tests that ran on Windows are the ones that need no POSIX shell, signal or opener: the usage, every
command's arguments and environment as uv receives them, the output passed through byte for byte, the exit statuses,
the steps of `init` and `demo`, a missing uv, no step after a Ctrl+C, and a signal arriving before `lab open`'s
opener. The 44 skipped are, by the reason each gives: 28 run `bin/lab`, a POSIX shell script; 4 open a document,
which `os.startfile` would do with a real program; 3 send SIGTERM from outside the process, which only POSIX can; 2
end the kit with a POSIX signal; 2 use a shell's background job; and one each needs a terminal's process group
(Windows consoles are Issue #8 PR-3's), POSIX's inherited signal dispositions, a POSIX signal sent to one process, a
symbolic link to a program, or bytes that are not UTF-8 in the environment.

The other 103 skips are 98 in the platform tests, 4 in the smoke's self-tests and the module that needs aelix. All
but three are PR-1's tests, skipped for the reasons [its record](2026-09-24-issue-8-platforms.md) gives. The three
are PR-2's platform tests that are POSIX-only: the POSIX opener, the POSIX program lookup, and the installed extension
loaded by its path, whose test links the kit beside it with a directory symbolic link.

## Earlier runs

Two earlier runs measured PR #33 before the last of the reviews' fixes, with the same checks, and every job passed in
both:

- [36622190907](https://github.com/handochan/aelix-analytics/actions/runs/36622190907) at `7cfe851` (measurement
  branch `d8eca1a`): the implementation reviews then changed `lab open`, the launcher's messages and `find_program`
  (`20799e7`) and the documents (`7c80b46`). The launcher tests were 82 then; the fixes added five.
- [36641189780](https://github.com/handochan/aelix-analytics/actions/runs/36641189780) at `7c80b46` (measurement
  branch `c177ad6`), with the counts above: the review of those fixes then changed documents, docstrings, a comment
  and one test's environment (`7316a8f`), and this record is of the run that measured them.

## Not run

- Windows: `lab open` and `/lab open` through `os.startfile`; the README's symlink route; the launcher at a native
  console (Ctrl+C, Ctrl+Break, closing the window) — Issue #8 PR-3; SIGTERM from another process, which Windows does
  not deliver to a handler; a real aelix session.
- Any platform: an aelix session with a model using the installed extension; a real document opener (every
  `lab open` above used a stand-in `open`/`xdg-open`); `lab demo` and `lab run --rerun-of` through the installed wheel
  with real uv; upgrading the installed wheel while aelix runs or while a run imports the kit; `install.sh`; a
  Korean-locale Windows.
- Other architectures (Linux and Windows on ARM, macOS on x86_64) and other install environments than CPython 3.11
  and 3.12.
