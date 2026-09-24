# Issue #8 platform measurement — 2026-09-24

The portable boundaries of [PR #31](https://github.com/handochan/aelix-analytics/pull/31) were run
on Ubuntu 24.04 and Windows Server 2025 by GitHub Actions and on a macOS developer machine, at one
commit. On every platform the checks each platform is judged on held, the repository suite and the
aelix 0.1.0b2 integration suite passed, and ⟦F: Windows showed three declared limitations⟧. This is
the durable summary: the CI artifacts expire after GitHub's retention period. What the results mean
for the contract is in [CONTRACTS.md](../CONTRACTS.md) F and G; the per-check values and test
counts are in ⟦F: the machine-readable result, 2026-09-24-issue-8-platforms.json⟧.

## Source and environment

| Item | Basis |
|---|---|
| Measured code | PR #31 at `⟦F: sha⟧` |
| Measurement branch | `test/issue-8-platform-measurement` at `⟦F: sha⟧`, which is that commit plus the measurement tool (`tools/platform_smoke.py`), its self-tests, the workflow, and test changes that let the suite run on Windows (explicit UTF-8, drive-letter links, an unreadable output file made by a lock). None of it is part of PR #31 |
| CI run | [⟦F: id⟧](⟦F: url⟧): `ubuntu-latest` (image ubuntu-24.04 ⟦F⟧) and `windows-latest` (image windows-2025-vs2026 ⟦F⟧), each with CPython ⟦F: 3.12.3 / 3.12.10⟧ from uv 0.11.19; CPython ⟦F: 3.11.x⟧ from `actions/setup-python` for the stdlib-only leg |
| Developer machine | macOS 26.6.2 arm64: CPython 3.12.13 (smoke, suite, aelix), 3.13.13 and 3.14.5 (suite), uv 0.11.19 |
| aelix | 0.1.0b2 from PyPI, in an environment of its own (`uv run --no-project --with aelix==0.1.0b2`) |

## Results

| Check | Linux | Windows | macOS |
|---|---|---|---|
| Platform smoke, required gate (`--gate required`) | ⟦F⟧ | ⟦F⟧ | ⟦F⟧ |
| Repository suite (`pytest`) | ⟦F⟧ | ⟦F⟧ | ⟦F⟧ |
| aelix 0.1.0b2 integration (`tests/test_extension.py`) | ⟦F⟧ | ⟦F⟧ | ⟦F⟧ |
| Stdlib-only smoke on CPython 3.11 (what the aelix process loads) | ⟦F⟧ | ⟦F⟧ | — |

A smoke check either makes a promise (it holds, is violated, or shows a limitation the documents
declare) or is an observation with no promise attached. The required gate fails on a violated or
unrun promise; Linux is judged on all ⟦F: 25⟧ promises, Windows on ⟦F: 24⟧.

## Windows, check by check

What held, with the values that matter:

- Locks: another process is excluded (it waited ⟦F: 2.01⟧ s for a holder keeping the lock 2 s), and
  a lock is released when its holder is killed.
- Replacing a record while another process reads it: a reader holding the file for 0.7 s was
  waited out (replaced after ⟦F: 0.708⟧ s); one holding it for 5 s was refused loudly after
  ⟦F: 1.97⟧ s of retries (`WinError 5`), and the file kept its old content.
- Liveness: a live, an exited and a bogus pid (`None`, `"x"`, `0`, `-1`) are told apart.
- Asking a child to stop: `ProcessControl.interrupt` creates the cancel request file when it is
  given one, and without one declines and sends nothing. `taskkill /F /T` ended a child and its
  grandchild.
- A child printing Korean text to a pipe: with the kit's UTF-8 standard streams it arrived intact;
  without them CPython's `cp1252` default failed with `UnicodeEncodeError`.
- Deadlines in Python code and in `time.sleep` (⟦F: 1.0⟧ s for a 1 s deadline); a cancel request
  file wakes a sleeping run; the guard records outside its regions and interrupts inside once.
- The CLI end to end; a run timeout in Python code and in a blocking DuckDB query (recorded
  `timeout` at ⟦F: 3.02 and 3.03⟧ s for a 3 s deadline); a cancel sent the way the extension
  sends it (recorded `cancelled`, not forced, ⟦F: 1.14⟧ s); concurrent runs serialized; a new
  reserved dataset name refused; a reserved chart id written to a portable file.

Declared limitations, as [CONTRACTS.md](../CONTRACTS.md) G states them. The tool reports a
limitation as declared only when the measured tree's document says it in the words the tool
quotes, and only in the form it states (for the two cancels: ended by force after the extension's
wait, the record left `running` and listed `incomplete`, the kit process gone); anything else is a
violation:

- `taskkill /T` after the process-tree leader exited: the grandchild survived, and the kit said so
  (`could not end the process tree of pid …; its descendants may still be running`).
- A cancel while the script is inside a catastrophic regular expression: the ask never reached
  the main thread, which held the GIL in `_sre`; the extension ended the tree by force after its
  10 s wait (⟦F: 10.08⟧ s), and the record stayed `running`, listed as `incomplete`.
- A cancel while the script waits in `subprocess.run` for a child that sleeps 120 s: the same,
  forced after ⟦F: 10.06⟧ s.

The deadline in those two situations was not run on Windows; that it does not reach them either
is inferred from the mechanism (a Python thread raises it) and stated as such.

Observations, with no promise attached:

- Device names on NTFS: `con`, `con.txt`, `nul.parquet` and `con_.png` were written as ordinary
  files; `nul` still opened the null device.
- ⟦F: long names: what open() and os.mkdir() raised for a 300-character name and a path longer
  than 260 characters⟧.
- A 2 s deadline for a script that ignores SIGINT and runs 8 s: recorded `timeout` when the script
  finished (⟦F: 8.0⟧ s); on Windows the deadline arrives as SIGINT, which that script had taken over.
  On Linux it is SIGALRM and the run ended at ⟦F: 2.0⟧ s.
- A script saving a reserved name: the run was `ok` and warned about.
- Design probes: a console `CTRL_BREAK_EVENT` sent to `uv run` ended `uv` at once
  (`0xC000013A`) and left its child running; a cancel request file reached the child through
  `uv run` (exit 130, handler finished); called from another thread 1 s into a 10 s sleep,
  `_thread.interrupt_main` landed only when the sleep ended, and `signal.raise_signal(SIGINT)`
  woke the main thread at once.

## The repository suite on Windows

⟦F: counts⟧. The ⟦F: 75⟧ skipped tests are ones that cannot run there: ⟦F: 56⟧ exercise the POSIX
guard's signals (the Windows guard has its own), and the rest need POSIX names, directory
symlinks (which need privileges on Windows), `pthread_kill`, process groups, POSIX permissions or
SIGALRM, or are the declared regular-expression limitation. The test module that needs aelix is
skipped in the repository suite and run by the integration step.

Two earlier runs had Windows failures, each fixed before the next run and not seen since. The
first, [35977785169](https://github.com/handochan/aelix-analytics/actions/runs/35977785169) at
`bbb4ba8`, had two, fixed in `b0dc099`:

- A test made a directory named `c:` in the project, which Windows reads as a drive. The test is
  about a name POSIX can hold and Windows reads otherwise, and is now skipped there like the others.
- A script path with a 300-character name was refused and its run removed as intended, but the
  hint said to check that `runs/` could be written. Windows' `open()` reports a name that long
  with a C runtime errno and no Windows error code, so the kit's error-code test never matched; the
  hint now also follows from the name's own length.

That run's steps showed success because the measurement keeps going past a failing step; its
outcomes recorded the failure. The workflow now ends the job red when a step failed.

The second, [35993026796](https://github.com/handochan/aelix-analytics/actions/runs/35993026796)
at `b0dc099`, had one, fixed in `aa25c3a`: the Office lock file `~$budget.xlsx` in a project was
not read as the project's file, because Windows' Python guesses a home for any `~name` beside the
current user's profile (`C:\Users\$budget.xlsx`). A leading `~name` is now expanded only when
that home exists, on every platform.

## Not run

- The native interactive console on Windows (Ctrl+C, Ctrl+Break) and its encoding.
- Windows with a Korean locale; a real aelix session on Windows; a Windows human entry point
  (there is none: `bin/lab` is a POSIX shell script).
- `WindowsProcessControl.alive` for another user's process.
- The deadline inside a regular expression or a subprocess wait on Windows (see above).
- CPython 3.11 beyond the stdlib part the aelix process loads; macOS on CI.

Repository CI on three platforms is Issue #8 PR-3.
