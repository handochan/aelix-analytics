# Issue #8 platform measurement — 2026-09-24

The portable boundaries of [PR #31](https://github.com/handochan/aelix-analytics/pull/31) were run
on Ubuntu 24.04 and Windows Server 2025 by GitHub Actions and on a macOS developer machine, at
one commit. On every platform the checks each platform is judged on held, the repository suite
and the aelix 0.1.0b2 integration suite passed, and Windows showed three declared limitations. This is
the durable summary: the CI artifacts expire after GitHub's retention period. What the results mean
for the contract is in [CONTRACTS.md](../CONTRACTS.md) F and G; the per-check values and test
counts are in the machine-readable result,
[2026-09-24-issue-8-platforms.json](2026-09-24-issue-8-platforms.json).

## Source and environment

| Item | Basis |
|---|---|
| Measured code | PR #31 at `0c225a9` |
| Measurement branch | `test/issue-8-platform-measurement` at `e37f142`, which is that commit plus the measurement tool (`tools/platform_smoke.py`), its self-tests and the workflow, none of which is part of PR #31. The suite it runs is the PR's own plus the tool's self-tests |
| CI run | [36096151504](https://github.com/handochan/aelix-analytics/actions/runs/36096151504): `ubuntu-latest` (image ubuntu-24.04, version 20260920.314.1) and `windows-latest` (image windows-2025-vs2026, version 20260922.246.2), each with the image's CPython 3.12.3 / 3.12.10, which uv 0.11.19 ran; CPython 3.11.16 / 3.11.9 from `actions/setup-python` for the stdlib-only leg |
| Developer machine | macOS 26.6.2 arm64: CPython 3.12.13 (smoke, suite, aelix), 3.13.13 and 3.14.5 (suite), uv 0.11.19; the measurement branch at the same commit, clean, run with the CI's commands |
| aelix | 0.1.0b2 from PyPI, in an environment of its own (`uv run --no-project --with aelix==0.1.0b2`) |

## Results

| Check | Linux | Windows | macOS |
|---|---|---|---|
| Platform smoke, required gate (`--gate required`) | held: 25 promises hold, none violated; 6 observations | held: 21 hold, 3 declared limitations, none violated; 7 observations | held: 25 hold, none violated; 6 observations |
| Repository suite (`pytest`) | 485 passed, 2 skipped | 386 passed, 100 skipped, none failed | 483 passed, 4 skipped (3.12); 483, 4 (3.13); 484, 3 (3.14) |
| aelix 0.1.0b2 integration (`tests/test_extension.py`) | 12 passed | 12 passed | 12 passed |
| Stdlib-only smoke on CPython 3.11 (what the aelix process loads) | held: 15 hold; the two probes that need uv not run | held: 14 hold, 1 declared limitation (the tree limit); the two probes that need uv not run | — |

A smoke check either makes a promise (it holds, is violated, or shows a limitation the documents
declare) or is an observation with no promise attached. The required gate fails on a violated or
unrun promise; Linux and macOS are judged on all 25 promises, Windows on 24 (a script saving a
reserved name is an observation there). A suite's skips are the tests that do not apply to the
platform: on Linux the module that needs aelix, which the integration step runs, and the lock case
only CPython 3.14 has; on macOS also the two names that are not UTF-8, which APFS refuses (3.14
runs the lock case). Windows's are listed below.

## Windows, check by check

What held, with the values that matter:

- Locks: another process is excluded (it waited 2.02 s for a holder keeping the lock 2 s), and
  a lock is released when its holder is killed.
- Replacing a record while another process reads it: a reader holding the file for 0.7 s was
  waited out (replaced after 0.707 s); one holding it for 5 s was refused loudly after
  1.973 s of retries (`WinError 5`), and the file kept its old content.
- Liveness: a live, an exited and a bogus pid (`None`, `"x"`, `0`, `-1`) are told apart.
- Asking a child to stop: `ProcessControl.interrupt` creates the cancel request file when it is
  given one, and without one declines and sends nothing. `taskkill /F /T` ended a child and its
  grandchild.
- A child printing Korean text to a pipe: with the kit's UTF-8 standard streams it arrived intact;
  without them CPython's `cp1252` default failed with `UnicodeEncodeError`.
- Deadlines in Python code and in `time.sleep` (1.019 and 1.001 s for a 1 s deadline); a cancel request
  file wakes a sleeping run; the guard records outside its regions and interrupts inside once.
- The CLI end to end; a run timeout in Python code and in a blocking DuckDB query (recorded
  `timeout` at 3.037 and 3.011 s for a 3 s deadline); a cancel sent the way the extension
  sends it (recorded `cancelled`, not forced, 1.02 s); concurrent runs serialized; a new
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
  10 s wait (10.07 s), and the record stayed `running`, listed as `incomplete`.
- A cancel while the script waits in `subprocess.run` for a child that sleeps 120 s: the same,
  forced after 10.07 s.

The deadline in those two situations was not run on Windows; that it does not reach them either
is inferred from the mechanism (a Python thread raises it) and stated as such.

Observations, with no promise attached:

- Device names on NTFS: `con`, `con.txt`, `nul.parquet` and `con_.png` were written as ordinary
  files; `nul` still opened the null device.
- Long names on NTFS: a 300-character name was refused by `open()` with `OSError` errno 22
  (`EINVAL`, no Windows error code) and by `os.mkdir()` with errno 22 and `WinError 123`; a path of
  276 characters, made of 40-character directory names, was created and a file in it opened.
- A 2 s deadline for a script that ignores SIGINT and runs 8 s: recorded `timeout` when the script
  finished (8.004 s); on Windows the deadline arrives as SIGINT, which that script had taken over.
  On Linux it is SIGALRM and the run ended at 2.002 s.
- A script saving a reserved name: the run was `ok` and warned about.
- Design probes: a console `CTRL_BREAK_EVENT` sent to `uv run` ended `uv` at once
  (`0xC000013A`) and left its child running; a cancel request file reached the child through
  `uv run` (exit 130, handler finished); called from another thread 1 s into a 10 s sleep,
  `_thread.interrupt_main` landed only when the sleep ended, and `signal.raise_signal(SIGINT)`
  woke the main thread at once.

## The repository suite on Windows

386 passed, 100 skipped and none failed, of 486. The skipped tests, by the reason each gives: 72
exercise the POSIX guard's signals; 7 use names POSIX can hold and Windows reads otherwise; 6 need directory
symbolic links, which need privileges on Windows; 4 put a stub or broken guard in place of the POSIX
one; 2 replace the delivery with `pthread_kill`; 2 need names that are not UTF-8, which Windows
refuses; and one each needs SIGALRM, a directory the process cannot list (POSIX permissions),
process groups, or `bin/lab` (a POSIX shell script), is the declared regular-expression cancel, is
the lock case only CPython 3.14 has, or is the test module that needs aelix, which is skipped in the
repository suite and run by the integration step.

Five earlier runs had Windows failures. Each failure was fixed before the next commit was measured
and has not been seen since.

The first two, [35886296088](https://github.com/handochan/aelix-analytics/actions/runs/35886296088)
and [35887916505](https://github.com/handochan/aelix-analytics/actions/runs/35887916505), measured
the PR as it stood at `17e6e76` (an earlier run, 35885814246, stopped at setting up uv), and failed
the same way: 7 of 128 suite tests and the aelix integration test.

- Six were the tests' and the doc checker's: text read and written without an encoding (Windows
  used cp1252), `chmod(0)`, which cannot take read access away on Windows, a test that wrote inside
  a region the Windows file lock holds (a second handle cannot write the byte the lock holds), and
  the doc checker, which read `C:\` as a URL scheme and let a Windows absolute link pass. The lock
  test was fixed in `bbb4ba8`; the others on the measurement branch first, and they are part of PR
  #31 since `3c7891b`.
- One was the kit's: a recorded `/tmp/...` script path, which is not absolute on Windows, was
  accepted for a rerun. It was fixed in `bbb4ba8`, with the other defects that run and its smoke
  showed (a replace refused while a reader held the file, pids `0` and `-1` read as alive).
- The integration test's cancel left the run `running` instead of `cancelled`: a console
  `CTRL_BREAK_EVENT` ended `uv` at once and left its child without a record. That is why Windows
  asks through a request file the kit watches (ADR-0003).

The third, [35977785169](https://github.com/handochan/aelix-analytics/actions/runs/35977785169) at
`bbb4ba8`, had two, fixed in `b0dc099`:

- A test made a directory named `c:` in the project, which Windows reads as a drive. The test is
  about a name POSIX can hold and Windows reads otherwise, and is now skipped there like the others.
- A script path with a 300-character name was refused and its run removed as intended, but the
  hint said to check that `runs/` could be written. Windows' `open()` reports a name that long
  with a C runtime errno and no Windows error code, so the kit's error-code test never matched; the
  hint now also follows from the name's own length.

That run's steps showed success because the measurement keeps going past a failing step; its
outcomes recorded the failure. The workflow now ends the job red when a step failed.

The fourth, [35993026796](https://github.com/handochan/aelix-analytics/actions/runs/35993026796)
at `b0dc099`, had one, fixed in `aa25c3a`: the Office lock file `~$budget.xlsx` in a project was
not read as the project's file, because Windows' Python guesses a home for any `~name` beside the
current user's profile (`C:\Users\$budget.xlsx`). The kit's own paths now expand a leading
`~name` only when that home exists, on every platform.

The fifth, [36044596511](https://github.com/handochan/aelix-analytics/actions/runs/36044596511) at
`fabb5eb`, had one: a test of a forced end read the run's log before the child had created it (a
race in the test, FileNotFoundError); it waits for the log since `9bc9d0f`.

Four earlier runs were green and are superseded, each by what was found after it:

- [36017977140](https://github.com/handochan/aelix-analytics/actions/runs/36017977140) at `652700e` and [36046510524](https://github.com/handochan/aelix-analytics/actions/runs/36046510524) at `9bc9d0f`: an
  independent review changed the code after each (the result line, the runtime recorder, the guard's
  walk).
- [36077330757](https://github.com/handochan/aelix-analytics/actions/runs/36077330757) at `c66060c`: the signal sweep below found the order of the CLI's
  give-back of its stdout (`1b31a7b`).
- [36081152541](https://github.com/handochan/aelix-analytics/actions/runs/36081152541) at `1b31a7b`: an independent review found that the CLI could wait
  without end at its exit for a thread the script left (`42faccb`).

One later run, [36444257841](https://github.com/handochan/aelix-analytics/actions/runs/36444257841) at `4458437`, measured a change that was then withdrawn
(`ad20a4e`). The change made the exit wait for a slow reader when no other thread ran Python code. The
review of that change found that it could then wait without end for a thread with no Python frame. The
code is again the one this record measured. Restoring the measurement branch to `e37f142` ran it once more,
as [36459022545](https://github.com/handochan/aelix-analytics/actions/runs/36459022545), with the same results. This record is of the run named above.

## Signals at every check point, and real ones

Two probes check the run guard beyond the suite, on the developer machine and in a Linux container
(not on Windows, whose guard the suite runs on its own). They are the developer's, kept with their
outputs in the PR-1 run's local evidence rather than in this repository; their tallies are in the
machine-readable result.

- A sweep, on the developer machine with CPython 3.12, delivers SIGINT, SIGTERM or SIGALRM at every
  check point of a run in turn (a place where CPython handles a pending signal), one run per point,
  with and without a second signal, a host and a script that leave raising handlers, a script that
  sets its own timer, a script that prints, a script that detaches its stdout, and a run as the CLI
  runs it — `runner.execute` as the CLI calls it, then the result line and the give-back of the
  CLI's stdout, as its `_emit` and `main()` do, with the points inside them swept too:
  145,427 points in 32 variants, none bad. A record never stayed `running`, no handler was left
  replaced, no timer was left running, every log held each printed line once, every result line
  that was written arrived whole and once, and the host's stdout was back after every run. The
  probe gives the stdout back once more after anything that cut the first give-back short, where
  `main()` does so after a KeyboardInterrupt only; in the variant whose host's SIGTERM handler
  raises, two points had it back only through that second give-back. `main()`'s own frame was not
  swept. The seven variants that run as the CLI does ran the kit of
  `1b31a7b`; the others ran that of `c66060c`, which differs only in the give-back, a path they
  never take.
- A real-signal fuzz sends a cancel and then, 5-60 µs later, a SIGTERM the script turned into
  `sys.exit(0)`, with the kit of `1b31a7b`: 100 of 100 recorded `cancelled` on each of macOS
  3.12, 3.13, 3.14 and Linux 3.12, including the trials where the SIGTERM cut the cancel's handler
  short at its first instruction, which the guard counts from the traceback of what cut it short.

Neither probe was run again for `0c225a9`: it differs from the kits they ran only in comments and in
how the interpreter's exit writes out earlier runs' streams, which neither probe injects into.

## Not run

- The native interactive console on Windows (Ctrl+C, Ctrl+Break) and its encoding.
- Windows with a Korean locale; a real aelix session on Windows; a Windows human entry point
  (there is none: `bin/lab` is a POSIX shell script).
- `WindowsProcessControl.alive` for another user's process.
- The deadline inside a regular expression or a subprocess wait on Windows (see above).
- CPython 3.11 beyond the stdlib part the aelix process loads; macOS on CI.

Repository CI on three platforms is Issue #8 PR-3.
