# Issue #8 Windows inventory — 2026-10-02

[PR #35](https://github.com/handochan/aelix-analytics/pull/35), Issue #8's last part, made the tests that assumed POSIX
run on the hosted Windows runner and measured what had only been inferred: directory links and junctions, long paths
with `LongPathsEnabled` at 1 and at 0, case-sensitive directories, names that are not UTF-8, and Ctrl+C and Ctrl+Break at
a console through the installed `analytics.exe`. It also fixed `taskkill`'s bare-name lookup, the one product change the
owner allowed. At one commit, the repository CI ran Ubuntu 24.04 and Windows Server 2025 on the pull request and hosted
macOS 26 by label, and a macOS developer machine ran the legs that run there. Every leg passed. This is the durable summary: the
CI artifacts expire after GitHub's retention period. What the results mean for the contract is in
[CONTRACTS.md](../CONTRACTS.md) F and G; G is the single statement of the limitations and of what was not run. Every
test's outcome on Windows (every leg), the outcome of each test this record names on every other leg, each JUnit file's
sha256, and the observations the tests kept (each console case, each long-path route, the linked-directory runs, and the
two earlier observations this record cites, with their runs and revisions) are in
[2026-10-02-issue-8-windows-inventory.json](2026-10-02-issue-8-windows-inventory.json).

## Source and environment

| Item | Basis |
|---|---|
| Measured code | PR #35 at `4eb0955`. The commits after it change documents only |
| Workflow | `.github/workflows/ci.yml` at that commit — the repository CI as PR #35 changes it: a console step, `AELIX_ANALYTICS_TEST_EVIDENCE_DIR`, and a last test step that sets `LongPathsEnabled=0` and runs `tests/test_windows_long_paths.py` again in a new process. A pull-request run takes the workflow from GitHub's merge commit (`GITHUB_WORKFLOW_SHA` `76ab130` in both runs) and checks out the PR head |
| Code run | [36996205330](https://github.com/handochan/aelix-analytics/actions/runs/36996205330), attempt 1, `pull_request`: `ubuntu-24.04` (ImageOS `ubuntu24` 20260927.320.1) and `windows-2025` (ImageOS `win25-vs2026` 20260925.250.1), uv 0.11.19. The development environment ran on the image's CPython 3.12.3 / 3.12.10; the aelix and launcher legs on the interpreters uv found: 3.11.15 and 3.12.3 on Linux, 3.11.9 and 3.12.10 on Windows. The `floor` jobs ran `actions/setup-python`'s CPython 3.11.16 / 3.11.9 |
| macOS run | [36998792769](https://github.com/handochan/aelix-analytics/actions/runs/36998792769), attempt 1, the `ci:macos` labelled event on the same head: `macos-26` (ImageOS `macos26` 20260907.0351.1, macOS 26.6.2 arm64), CPython 3.12.10 for the development environment, 3.11.9 and 3.12.10 found by uv, 3.11.9 for the `floor` job |
| The Windows configuration | From the platform smoke's runner settings in the code run: Windows Server 2025 (`ServerDatacenter`), 10.0.26100 x64, administrator, Developer Mode 1, `LongPathsEnabled` registry 1 and effective `true`, symbolic links allowed, `%TEMP%` ignores case, `core.autocrlf=true` from the system gitconfig, console code pages 65001 in the step, ANSI `cp1252`. The token's integrity level is not in the smoke's record; the probe run 36899933022 read High integrity on the same image |
| Developer machine | macOS 26.6.2 (25G83) arm64, uv 0.11.19, a clean detached checkout of `4eb0955` (no change before or after): CPython 3.12.13 for the smoke and the suite with its end-to-end leg, 3.12.13 and 3.11.15 for the aelix integration, 3.11.15 for the launcher tests and the stdlib-only smoke, 3.13.13 and 3.14.5 for the suite without its end-to-end leg, each in an environment of its own |
| aelix | 0.1.0b2 from PyPI, in environments of their own (`uv run --isolated --no-project`). A step after the tests lists what they resolved to: 64 distributions on Linux and macOS, 66 on Windows, none of them an analysis library (the JSON keeps the lists). The two earlier records' CPython 3.12 aelix leg ran without `--isolated`, over the checkout's development environment (found in PR-3a's design review, ci-4); those records are not edited |

## Results

| Check | Linux | Windows | macOS (hosted) | macOS (developer machine) |
|---|---|---|---|---|
| Repository suite (`AELIX_ANALYTICS_TEST_INSTALL=1`; the console module has its own step) | 625 passed, 11 skipped | 523 passed, 121 skipped, none failed | 623 passed, 13 skipped | 623 passed, 13 skipped (3.12); without the end-to-end leg 613 / 23 (3.13) and 614 / 22 (3.14) |
| The native console (`tests/test_windows_console.py`) | — | 21 passed | — | — |
| Long paths again with `LongPathsEnabled=0` | — | 6 passed | — | — |
| aelix 0.1.0b2 integration, CPython 3.12 / 3.11 | 13 / 13 passed | 13 / 13 passed | 13 / 13 passed | 13 / 13 passed |
| The launcher on CPython 3.11 (`tests/test_launcher.py`) | 89 passed, 1 skipped | 46 passed, 44 skipped | 89 passed, 1 skipped | 89 passed, 1 skipped |
| Platform smoke, required gate | held: 25 hold | held: 21 hold, 3 declared limitations | held: 25 hold | held: 25 hold |
| Stdlib-only smoke on CPython 3.11 | held: 15 hold | held: 14 hold, 1 declared limitation | held: 15 hold | held: 15 hold |

The lint job passed: ruff, and the document checker (34 Markdown files, 285 local links). On main before PR #35 (run
36982675329) the Windows suite was 467 passed, 149 skipped. The skips on Linux and macOS are the Windows-only tests (the
long-path module, the decoy `taskkill`, the case-sensitive directory, the Windows start-of-child Ctrl+C), the module that
needs aelix (the aelix legs run it), and the lock case only CPython 3.14 has. macOS adds the two names that are not
UTF-8, which APFS refuses (`OSError` 92). The developer machine's 3.13 and 3.14 suites also skip the end-to-end tests,
which need `AELIX_ANALYTICS_TEST_INSTALL=1`. Windows's skips are listed [below](#what-stays-skipped-on-windows).

JUnit files cited below (sha256, first 12 characters; the full values are in the JSON): Windows suite `83af838bbe7f`,
console `01bc0493b156`, long paths at 0 `db83fbf1b3e5`, aelix 3.12 `f161a75053e9` and 3.11 `a056850036c8`, launcher
`2413a1f9e773`. Where a row cites a measured value rather than a pass, the value is an observation the test kept in the
JSON (`observations`, `observations_long_paths_off`), not the JUnit outcome.

## The inventory

One row per area: what was done and where the evidence is. A test named here is in the code run's Windows suite unless
the row says otherwise.

| Area | Handling | Evidence |
|---|---|---|
| Directory links | A `dir_link(kind, …)` helper makes a symbolic link everywhere and, on Windows, a junction as well (`LINK_KINDS`). The rerun and containment tests run for both kinds. A loop is built in one step (symbolic link) or two (junction), and its precondition is a loop's own error from a strict `realpath` (ELOOP or WinError 1921), so a dangling link cannot pass for one | `test_a_rerun_through_a_link_into_a_managed_directory_is_refused`, `…_link_loop_runs_as_on_main`, `…_does_not_follow_a_link_out_of_the_project`, `…_outward_link_is_refused_behind_a_loop_too`, `test_a_rerun_whose_recorded_snapshot_loops_is_refused[snapshot, code_root]`: all pass for `symlink` and `junction` |
| The kit beside the installed module | A real copy runs everywhere; the symbolic link stays, and Windows adds a junction | `test_the_installed_module_loaded_as_a_file_finds_the_kit_beside_it[copy, symlink, junction]` pass |
| A linked `data/` (characterization) | Observed. Only what holds everywhere is asserted: both runs end `ok`, and the dataset is at the link's target. With a symbolic link, the saving run records `outputs: []`, its inputs as external absolute paths, and the datasets without a producer — on Windows, Linux, hosted macOS and the developer machine alike. With a junction (Windows), the output is recorded (`data/derived/part.parquet`, by diff), but inputs and datasets are external absolute paths and have no producer. A junction into `runs/` makes the run record `lnk/<id>/stdout.txt` and `stderr.txt` as outputs, with three warnings; a symbolic link records nothing | observations `linked-data-symlink`, `linked-data-junction`, `link-into-runs-*` on each platform. G states it; follow-up [#36](https://github.com/handochan/aelix-analytics/issues/36) |
| Long paths, `LongPathsEnabled=1` | One route per test at a stated length. Every observation records the setting it ran with (`setting`: registry 1, effective `true`); that field, not the pass, is the evidence of the setting. Root 230 (its environment's interpreter would be 255 characters; the kit ran on the checkout's interpreter): import works, a run with outputs past 260 records them (an 81-character output; the deepest file 365), and the report builds. Root 300: the extension's `run_kit` and `sync_environment` answer `spawn_failed` (WinError 267, CreateProcess's limit on a working directory). `/lab note` and the context block work, and the report build the note schedules fails to start (WinError 267) without a word. `/lab init` creates `lab.json`, activates the project and answers `spawn_failed`. A program started with that directory fails with WinError 267. The installed `analytics.exe`, started from a short directory with `LAB_PROJECT`, runs `init` (real uv; the environment's interpreter at 325 characters) and `status`, both exit 0 | `tests/test_windows_long_paths.py` (6 pass); observations `long-paths-enabled-*` |
| Long paths, `LongPathsEnabled=0` | The last step sets the registry to 0, and the new process reads it as 0 (`setting`: registry 0, effective `false`). At 0 the tests assert that nothing hangs, and on the 203-character route that its project and script were made; what each route recorded is the measurement. Root 300: no directory can be made (WinError 3, at a 266-character path). The installed `analytics init` ends with a traceback and exit 1, no JSON line and no `lab.json`; `/lab init` answers `internal error FileNotFoundError`. Root 203 (the template's longest file fits): project, import and report work. A run whose outputs pass 260 ends `error` at the chart write (`FileNotFoundError`, line 9), with `outputs: []`, while the deepest file on disk is 285 characters, the length of the dataset path `lab.save` wrote before the chart — the output record is lost | `long-paths-off.xml` (6 pass); observations `long-paths-disabled-*`. Follow-up [#38](https://github.com/handochan/aelix-analytics/issues/38) |
| Case | The script-path names Windows can hold run there. `runs\x.py` and `RUNS/x.py` are refused as inside a managed directory, and `~no-such-user-here/a.py` by the rule for every platform. `é:`, `1:` and the `c:` test stay POSIX-only (`DRIVE_LIKE`: Windows reads them as a drive, and NTFS a colon as a stream). A dataset name is lower case everywhere: `--name Sales` is refused, and `Sales.csv` is imported as `sales`. A project inside a directory flagged case-sensitive (`fsutil … setCaseSensitiveInfo`; the precondition is asserted at the root and at `runs/`) refuses `RUNS/x.py` by the rule for every platform, as Linux does | `test_a_new_run_of_a_name_another_platform_reads_differently_is_refused_truthfully[runs\x.py, RUNS/x.py, ~no-such-user-here/a.py]`, `test_a_dataset_name_is_lower_case_everywhere`, `test_a_project_in_a_case_sensitive_windows_directory_reads_runs_in_another_case_as_linux` pass |
| Names that are not UTF-8 | On Windows the name is made as a `str` holding a lone surrogate, which NTFS keeps and lists back (asserted), and the scan leaves it out, as on Linux. The 2026-09-24 record gave the wrong reason for skipping these on Windows ("names that are not UTF-8, which Windows refuses"). Windows decodes a bytes path as UTF-8 before any system call (PEP 529), so such bytes never reached NTFS | `test_a_scanned_name_a_record_cannot_hold_is_left_out` and `…_in_a_directory_…` pass on Windows and Linux; macOS skips them (APFS) |
| POSIX-assuming tests | The query sandbox now reads the project's own `lab.json`, directly and through `data/..` (both separators on Windows), and a file through a link out of `data/` (both kinds). It expects DuckDB's `Permission Error`, with a file inside `data/` as the positive control. The unreadable output is a byte-range lock on Windows; as root on POSIX, only the assertion that needs the file unreadable is skipped. The encoding scan covers the tests' own calls, and `tools/check_docs.py` writes UTF-8 whatever the locale | `test_query_cannot_write_or_read_outside_data`, `test_query_cannot_read_through_a_link_out_of_data[symlink, junction]`, `test_finalization_survives_…_unreadable_outputs`, `test_the_shipped_code_names_the_encoding_…`, `test_the_report_on_stdout_is_utf8_whatever_the_locale` pass |
| The Windows guard's own tests | Run on Windows with the real delivery | `test_the_windows_guard_s_handler_under_a_second_signal` (6), `test_a_signal_at_the_start_of_the_guard_s_own_exit_only_records` (3), `test_the_windows_guard_refuses_a_deadline_its_timer_cannot_hold`, `test_a_windows_disarm_interrupted_holding_the_guard_s_lock_does_not_hang[exit]` (`[enter]` needs CPython 3.14), and the two watcher tests pass |
| `taskkill` | The product fix: `GetSystemDirectoryW()`'s `taskkill.exe`, with `%SystemRoot%\System32` as the fallback, never the bare name. With a decoy `taskkill.exe` in the working directory, the real tree ends, the child's own child included | `test_windows_runs_taskkill_from_the_system_directory_and_never_by_its_bare_name`, `test_a_taskkill_in_the_working_directory_is_not_the_one_that_runs`, `test_windows_kill_tree_reports_what_taskkill_could_not_do` (3) pass |
| Two projects in one host | aelix 0.1.0b2 loads one extension, and each project gets a `lab_note` tool call through the hook handlers. Each journal and telemetry holds only its own call, and the context block follows `/lab use`. The first project's files do not change while the second is used, and no report build starts. A call still running across `/lab use` is recorded where it ends, and a test pins that as it is | `test_two_projects_in_one_host_keep_their_own_records` in the aelix legs (13 pass on 3.12 and 3.11, three platforms); `test_a_tool_running_across_a_project_switch_is_recorded_where_it_ends` (suite, three platforms) |
| ADR-0004 checks 2 and 4 on Windows | The installed `analytics run` gets an unrelated `PYTHONPATH`, and the script sees exactly the installed kit. A rerun of it (`run --rerun-of`) with real uv matches. `analytics open` through the Windows port, with `os.startfile` replaced, builds a missing report and then opens it; WinError 1155 is exit 1 with the message. README's `uv tool install --python 3.12` lines run as written. README's symbolic-link route works with the file linked by `os.symlink`, on an account the observation records as administrator with Developer Mode 1 | `test_the_installed_command_drives_a_project_without_aelix[3.11, 3.12]`, `test_open_through_the_windows_port_builds_a_missing_report_then_starts_it[None, 1155]` (launcher tests, every platform), `test_the_readme_s_uv_tool_install_routes` and `test_a_checkout_symlink_beside_the_wheel_sets_up_one_copy` pass; observation `readme-symlink-route` |
| A Ctrl+C while the launcher starts a child (con-13) | Not sent again on Windows (there is no way to send SIGINT to one process); a test pins it | `test_a_ctrl_c_while_a_child_starts_is_not_sent_again_on_windows` passes (launcher tests, Windows) |

## The native console

The installed `analytics.exe` drove a real project with real uv. The wheel was built from the checkout and installed on
CPython 3.12 and 3.11. The console belonged to the test: either a pseudo console (ConPTY), where Ctrl+C is the input byte
`\x03`, or a hidden classic console. Ctrl+Break, and the classic console's Ctrl+C, came from a helper process attached to
the console (`GenerateConsoleCtrlEvent`); one observation typed Ctrl+Break as a win32-input-mode sequence instead. None of it is a key a person pressed. Each case ran **once** in this run. The
shell, a small program that survives both events, counted what it received. When it got control back, it recorded the
launcher's exit code and the run's status. All 21 tests passed.

| Case (install) | Exit code | Status at the prompt | Record | What else was observed |
|---|---|---|---|---|
| Ctrl+C, busy script (3.12, 3.11) | 3 | `cancelled` | `cancelled` | The result line came before the prompt, and the Korean title showed on the console. The run's stdout log holds the script's Korean print. At the prompt only the shell was on the console, and nothing of the job was left |
| Ctrl+C, `time.sleep` (3.12, 3.11) | 3 | `cancelled` | `cancelled` | as above |
| Ctrl+C, waiting on its own child (3.12) | 3 | `cancelled` | `cancelled` | As above. The traceback names the wait (`, in _wait`): the child, on the same console, ended on the Ctrl+C |
| Ctrl+C, a catastrophic regular expression (3.12) | 3 | `cancelled` | `cancelled` | As above. The traceback names the match (`, in match`) |
| Ctrl+C twice, the script swallowing the first (3.12) | 3 | `cancelled` | `cancelled` | The shell received both. The script had swallowed the first and ran its own 2-second sleep to the end (the record lasted 2.0 s). The launcher returned after the record |
| Ctrl+C at console code page 949 (3.12) | 3 | `cancelled` | `cancelled` | Code page 949/949; the Korean title showed correctly |
| Ctrl+C, classic console (3.12) | 3 | `cancelled` | `cancelled` | There is no output pipe here, so the console's text is not read |
| Ctrl+Break, `time.sleep` (3.12, 3.11) | `0xC000013A` | `running` | `cancelled` | The prompt came 0.020 / 0.026 s after the helper sent the event. The record ended when the 15-second sleep ended (15.05 / 15.06 s). The result line came after the prompt, and nothing was left once the kit ended |
| Ctrl+Break, busy (3.12; ConPTY and classic) | `0xC000013A` | `running` (observed, not a gate) | `cancelled` | The prompt came 0.204 / 0.155 s after the event |
| A second run started after the Ctrl+Break prompt (3.12 sleep row) | 0 | — | `ok` | Its record started 0.70 s after the first run's record ended, and the command took 16.4 s: it waited for the run lock that the still-running kit held |
| Closing the pseudo console while busy (3.12) | — | — | `incomplete` | `ClosePseudoConsole` returned, every process of the job had ended, and no stdout log was linked. Whether the kit's handler ran was not observed |
| Observation: `\x03\x03` in one write | 3 | `cancelled` | `cancelled` | One Ctrl+C was delivered, not two |
| Observation: the win32-input-mode Ctrl+Break sequence | `0xC000013A` | `running` | `cancelled` | One Ctrl+Break was delivered |
| Observation: the command typed into `cmd /d /k` and `pwsh -NoProfile` | — | — | `cancelled` | Both shells survived and showed their prompt. The typed shells' exit status is timing-dependent here, so it is not cited |
| Observation: pwsh 7.6.6 capturing `analytics status` at code pages 437 and 949 | 0 | — | — | The Korean title came out garbled in the captured text at both (`∞╜ÿ∞åö δ╢ä∞ä¥`, `肄섏넄 遺꾩꽍`): pwsh decoded the UTF-8 output with the console's code page |
| A case that fails half way | — | — | — | Every process of its job ended after the failure |

An earlier run of the same harness (36993060072, at `2b69cf1`) showed one more fact. The Ctrl+C came while the script
was still inside `CreateProcess` for its child. The run was recorded `cancelled` and the launcher returned 3. But the
child attached to the console after the event, never received it, and was still running after the run had ended. That
was measured once. CONTRACTS G states it, and [#38](https://github.com/handochan/aelix-analytics/issues/38) follows it up.
The row was then changed so that the child itself marks the run ready, and in the measured run it measured a wait.

## What stays skipped on Windows

The Windows suite's 121 skips, by reason:

- The POSIX guard's signals: 59. These are the guard-generic tests that use the POSIX guard; converting the ones that do
  not need it is [#32](https://github.com/handochan/aelix-analytics/issues/32).
- `bin/lab` is a POSIX shell script: 29.
- `os.startfile` would open a real program: 4.
- SIGTERM from outside a process is POSIX: 3.
- A drive or an NTFS stream (`DRIVE_LIKE`): 3.
- A POSIX signal: 2. A shell's background job: 2.
- The smoke's stub or broken guards stand in for the POSIX one: 4.
- One each: the module that needs aelix; bytes in the environment that are not UTF-8; a terminal's process group (the
  console module measures Windows's); POSIX's inherited signal dispositions; a POSIX signal sent to one process; a symbolic
  link to a program (uv found through a mise shim); the stand-in uv that is a script; a POSIX shell script as the opener;
  POSIX executables and `HOME`; SIGALRM; a directory the process cannot list; process groups; the declared
  regular-expression limit of the extension's cancel; the lock case only CPython 3.14 has; and the reader test of a `~`
  name POSIX can hold.

The launcher leg's 44 skips have the same POSIX reasons.

A skip ledger alone does not show completeness. These tests behave differently by platform without skipping:

- `LINK_KINDS` adds the junction cases on Windows only, and `SEPARATORS` adds the backslash on Windows.
- The guard tests that take `platform_guard` run the POSIX variant only on POSIX.
- The unreadable-output test locks a byte on Windows, where POSIX removes read access.
- `_not_utf8` builds the name as bytes on POSIX and as a `str` on Windows.
- The `taskkill` lookup test exercises the `%SystemRoot%` fallback only off Windows.
- `tests/test_windows_long_paths.py` and `tests/test_windows_console.py` are Windows-only modules; the console module runs
  in its own step.

## Closing Issue #8

| Criterion | Delivered by | Evidence | Disposition |
|---|---|---|---|
| C1 the CLI and SDK without aelix, and the extension, from one distribution | PR-2 | `tests/test_packaging.py` with its end-to-end leg on 3.11 and 3.12, three platforms (above) | met (the SDK reading is ADR-0004's decision 9) |
| C2 no analysis library in the host; analysis in the project environment | PR-1, PR-2, PR-3a | the aelix legs in isolated environments (none of the resolved distributions is an analysis library); the import checks against `ANALYSIS_MODULES` in `tests/test_platform.py` | met |
| C3 a substitute adapter, project isolation, legacy | PR-1; PR #35 | the substitute-port tests; two projects in one host (above), with the tool-across-a-switch case stated | met; which state was checked is said above |
| C4 template, assets and manifest outside a checkout | PR-2 | the packaging tests | met; the installed `analytics demo` with real uv was not run |
| C5 portable boundaries first | PR-1 | the four ports; no `sys.platform` branch at a call site | met |
| C6 entry, loading, isolation, concurrent run, timeout and cancellation per platform | PR-1/PR-2 measurements, PR-3a CI, this record | the columns above | met |
| C7 a complete Windows inventory with execution evidence | this record | the inventory and console tables | met when the owner accepts the not_run list |
| C8 the floor judged on 3.11/3.12 | PR-2, PR-3a, this record | the floor verdict below | met |
| C9 the POSIX-permission tests | PR-1 (chmod), PR #35 | the POSIX-assuming tests above; the remaining skips by reason and the silent branches | met |

ADR checks:

- **ADR-0002**: 1 is met, as ADR-0004 reads it (the owner's reading of 2026-09-30). 2, 3 and 4 are met. 5 is C9 and 6 is
  C7. 7 is met: G keeps the not_run list.
- **ADR-0003**: check 5 is met as reworded: Linux and Windows on every pull request and push to main, macOS by label,
  manual run and the weekly run. Check 6 is met through a pseudo console and a classic console. Output through Python's
  console path is correct at code pages 437 and 949. Raw bytes written to a console file descriptor, and a person's key
  press, were not run.
- **ADR-0004**: check 2 is met: the exact `PYTHONPATH` reaches the run through real uv on three platforms. Check 4 is met,
  except a real opener and the installed `analytics demo` with real uv. Check 5's Windows side is measured: console Ctrl+C
  through the launcher, and the start-of-child case pinned as not sent again. `bin/lab` parity stays POSIX. Check 7 is met,
  with the not_run items G lists.

The floor verdict: `>=3.11` holds for what runs in the installation — the launcher, the aelix extension, and `core` and
`_platform`, which the extension loads. The evidence: installs on CPython 3.11 and 3.12 on three platforms, the aelix
integration on 3.11, the stdlib-only smoke on 3.11, the launcher tests on 3.11, and the console cases through the 3.11
install. Analysis projects stay `>=3.12`. D1's interruption guarantees were measured where the kit runs, 3.12 and later. On
3.11, a run of a script that uses no analysis library is checked to run and record.

What was not run, each item with its reason, is the last item (`not_run`) of
[CONTRACTS.md G](../CONTRACTS.md#g-약속하지-않는-것). This record does not keep a second list. Issue #8 closes when the
owner accepts that list.

## The harnesses

- The console harness runs each case in a job object that kills whatever is left when it closes. It closes the console
  and the job in `finally`, whatever happened. Every wait has a limit. The helper refuses to send an event to a console
  that holds any of the test's own processes. Each case is one trial. The timing windows are not aimed at and stay
  inferred: a second Ctrl+C inside the handler-reinstall window, and a Ctrl+C before uv installs its handler or during
  the kit's import.
- The long-path module runs every process it starts through one runner, which ends the whole tree at the limit and
  bounds every wait. If `taskkill` itself fails or times out after a route has timed out, the route's descendants can
  survive until the runner machine is discarded; by then the test has already failed. Review found this, and it was
  deferred under the exit rule.
- Observations are written before the assertions, and a harness error is kept as one, so a failing case still leaves
  its evidence.

## Reviews

- **Design**: two revisions, reviewed before implementation by Codex and a Claude workflow.
- **Implementation at `4609ac0`**: Codex requested changes:
  - the `LongPathsEnabled=0` run never started;
  - a console run ran outside the harness's job;
  - a loop precondition accepted a dangling link;
  - a decoy test did not check the grandchild.

  Two independent Claude reviews approved with changes.
- **Fix at `2b69cf1`**, reviewed as a diff: Codex requested changes (cleanup on timeout paths, and the prompt-time check).
  A Claude reviewer approved with changes (the child-wait row's ready marker).
- **Fix at `9d02960`**: a narrow Codex confirmation left one finding, the cleanup gap above, deferred under the exit rule.

The product findings went to CONTRACTS G and to follow-ups [#36](https://github.com/handochan/aelix-analytics/issues/36),
[#37](https://github.com/handochan/aelix-analytics/issues/37), [#38](https://github.com/handochan/aelix-analytics/issues/38)
and [#32](https://github.com/handochan/aelix-analytics/issues/32). The only product fix was `taskkill`'s lookup.

## Earlier runs

The measured run supersedes these. The three PR #35 runs each ran the same jobs and legs as the measured code run, on
the test code of its own commit: lint, the smoke, the suite with its end-to-end leg, the console step and the
`LongPathsEnabled=0` step on Windows, the aelix and launcher legs, and the floor. The runs on main ran the workflow from
before PR #35 (no console or `LongPathsEnabled=0` step), and the probes a workflow of their own. All are attempt 1.

- [36986490189](https://github.com/handochan/aelix-analytics/actions/runs/36986490189) at `8e6cb7e` was all green
  (Windows suite 522 passed / 121 skipped, console 21, the step at 0 6). Its `LongPathsEnabled=0` route could not make a
  230-character project, which led to the shorter root (that observation is kept in the JSON's `earlier_observations`).
- [36989520133](https://github.com/handochan/aelix-analytics/actions/runs/36989520133) at `4609ac0` was all green, but at
  0 the script itself could not be written, so no run was measured. This was the review's first finding.
- [36993060072](https://github.com/handochan/aelix-analytics/actions/runs/36993060072) at `2b69cf1`: the console step
  failed one case, the child-wait row, with the `CreateProcess` timing described above (kept in `earlier_observations`).
- On main `61c035a`, before PR #35: [36982675329](https://github.com/handochan/aelix-analytics/actions/runs/36982675329)
  (push) and [36982727732](https://github.com/handochan/aelix-analytics/actions/runs/36982727732) (manual, with macOS).
- The probes on a throwaway branch (36899933022, 36951715223 and 36952098887) were input to the design. This record
  takes from them only what it attributes to them by name (the token's High integrity above; and in CONTRACTS G, NTFS
  keeping an NFD name and the checkout's files being CRLF); none of their results stands for a test of the measured code.

## Not run

See the `not_run` item of [CONTRACTS.md G](../CONTRACTS.md#g-약속하지-않는-것): it is the single list, each item with
its reason, and this record keeps no second one.
