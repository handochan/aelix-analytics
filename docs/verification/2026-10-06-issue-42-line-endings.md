# Issue #42 line endings — 2026-10-06

[Issue #42](https://github.com/handochan/aelix-analytics/issues/42) implements
[ADR-0007](../decisions/0007-line-endings.md) (the owner's decision of 2026-10-03 on
[#37](https://github.com/handochan/aelix-analytics/issues/37)): the repository and the text the kit writes are LF on
every platform, a user's file keeps its bytes, a change of line endings alone is named `line_endings_only`, and a new
project gets a `.gitattributes` that turns Git's line-ending conversion off. It changes the bytes the kit writes on
Windows, so it is a record-behaviour and platform change and this record is required (operations policy, evidence
criteria). What the results mean for the contract is in [CONTRACTS.md](../CONTRACTS.md) B, E, F and G; G is the single
statement of the limits and of what was not run.

**State of this record.** Every column is in. The developer machine's macOS results were measured again after the
implementation review (the values of the first measurement, at `7d8e449`, are replaced). The repository CI ran on
`d23ddb8`, the head of [PR #62](https://github.com/handochan/aelix-analytics/pull/62): its Linux and Windows legs on the
pull request's `opened` event and its hosted macOS legs on the `ci:macos` label, attempt 1 each, and every job passed.
`main` was still `e9392d9` when these cells were filled, so no rebase was pending and this is the head the merge order
(#42 before #36) settles (design review C2). The commit that filled these cells changes this record only. The JSON
beside this file, [2026-10-06-issue-42-line-endings.json](2026-10-06-issue-42-line-endings.json), holds every value
below, each run's job ids and durations, and the evidence files' sha256.

## Source and environment

| Item | Basis |
|---|---|
| Measured code | `d23ddb8` on branch `feat/issue-42-lf-everywhere` on `e9392d9` (origin/main: #9 public names, kit 0.3.0; #12 PR-1 and PR-2), the review-fix commit of PR #62 (on the implementation commit `7d8e449`). The CI legs checked out `d23ddb8`. The developer machine measured its working tree before the commit; the tree's non-document files are those of the commit (the JSON's `measured_tree`: the same digest `f9a9d35c9642` of every path but `docs/`, `README.md` and `CONTRIBUTING.md`) |
| Kit version | 0.4.0 (`KIT_VERSION`, pyproject, manifest, `uv.lock`): #42 merges before #36, which takes the next version when it is rebased (design 3.8, review C2) |
| Workflow | `.github/workflows/ci.yml`, unchanged by this PR |
| Code run (Linux, Windows) | [37411845626](https://github.com/handochan/aelix-analytics/actions/runs/37411845626), attempt 1, `pull_request` (`opened`) on head `d23ddb8` (the workflow from GitHub's merge commit `80f58d7`). `platform · ubuntu-24.04` job 112101782931, 11m34s (ImageOS `ubuntu24` 20260927.320.1) and `platform · windows-2025` job 112101782964, 28m43s (ImageOS `win25-vs2026` 20260925.250.1); `floor` jobs 112101782885 and 112101783020; `lint` job 112101782799. uv 0.11.19. The development environment ran on CPython 3.12.3 / 3.12.10; the aelix and launcher legs on the interpreters uv found, 3.11.15 and 3.12.3 on Linux, 3.11.9 and 3.12.10 on Windows; the `floor` jobs on `actions/setup-python`'s 3.11.16 / 3.11.9. Windows has `core.autocrlf=true` from the system gitconfig (`C:/Program Files/Git/etc/gitconfig`, the platform smoke's runner record) |
| macOS run (label) | [37411847760](https://github.com/handochan/aelix-analytics/actions/runs/37411847760), attempt 1, the `ci:macos` labelled event on the same head: `platform · macos-26` job 112101790976, 8m58s (ImageOS `macos26` 20260907.0351.1, macOS 26.6.2 arm64), `floor` job 112101791126. CPython 3.12.10 for the development environment, 3.11.9 and 3.12.10 found by uv, 3.11.9 for the `floor` job |
| Developer machine | macOS 26.6.2 (25G83) arm64, uv 0.11.19, Git without `core.autocrlf`. CPython 3.12.13 for the development environment (suite, end-to-end leg, smoke), 3.12.13 and 3.11.15 for the aelix integration, 3.11.15 for the launcher tests and the stdlib-only smoke. Trust list and uv were the run's own (`AELIX_ANALYTICS_TRUST_FILE`, `AELIX_ANALYTICS_UV` inside the run's evidence folder), the old `LAB_*`/`AELIX_LAB_*` names unset |

## Results

| Check | Linux | Windows | macOS (hosted) | macOS (developer machine) |
|---|---|---|---|---|
| Repository suite | 1051 passed, 12 skipped (with the end-to-end leg) | 934 passed, 137 skipped, none failed (with the end-to-end leg) | 1049 passed, 14 skipped (with the end-to-end leg) | 1039 passed, 45 skipped (the Windows-only tests, the two aelix files without aelix, the end-to-end tests without `AELIX_ANALYTICS_TEST_INSTALL=1`, the cases only CPython 3.14 or another file system has) |
| Distribution with its end-to-end leg (`AELIX_ANALYTICS_TEST_INSTALL=1`, `tests/test_packaging.py tests/test_launcher.py`) | in the suite: 145 passed, 1 skipped | in the suite: 86 passed, 60 skipped (the POSIX-only launcher cases and the stand-in uv script) | in the suite: 145 passed, 1 skipped | 145 passed, 1 skipped (the Windows-only launcher case) |
| `tests/test_line_endings.py` (in the suite; also run alone) | 80 passed (in the suite) | 80 passed (in the suite) | 80 passed (in the suite) | 80 passed |
| aelix 0.1.0b2 integration, CPython 3.12 / 3.11 | 141 / 141 passed | 141 / 141 passed | 141 / 141 passed | 141 / 141 passed |
| The launcher on CPython 3.11 | 120 passed, 1 skipped | 62 passed, 59 skipped (the POSIX-only cases) | 120 passed, 1 skipped | 120 passed, 1 skipped (the Windows-only case) |
| Platform smoke, required gate | held: 25 hold, 0 violated | held: 21 hold, 3 declared limitations, 0 violated | held: 25 hold, 0 violated | held: 25 hold, 0 violated |
| Stdlib-only smoke on CPython 3.11 | held: 15 hold, 0 violated (3.11.16) | held: 14 hold, 1 declared limitation, 0 violated (3.11.9) | held: 15 hold, 0 violated (3.11.9) | held: 15 hold, 0 violated (the two `not_run` are observation probes, not required) |
| Usage report (aelix 0.1.0b2, 3.12) | 58/58 required facts, 42/42 invariants, exit 0 | 58/58, 42/42, exit 0 | 58/58, 42/42, exit 0 | 58/58 required facts, 42/42 invariants, exit 0 |

macOS writes LF in text mode, so the developer machine's passes say nothing about Windows's bytes (ADR-0002: a pass on
one platform is not another's result). They show the tests run and the constants they assert, which the Linux and
Windows legs met as well.

In CI the suite step sets `AELIX_ANALYTICS_TEST_INSTALL=1`, so the end-to-end leg runs inside it (the distribution row
counts `tests/test_packaging.py` and `tests/test_launcher.py` in that JUnit) and the console module has a step of its own;
`tests/test_line_endings.py` was not run alone there. The skips on Linux (12) are the Windows-only tests (the long-path
module, `taskkill`, the case-sensitive directory, the Windows launcher case), the lock case only CPython 3.14 has and the
two aelix files collected without aelix; hosted macOS adds the two names that are not UTF-8, which APFS refuses (14).
Windows's 137 are the POSIX-only tests (the POSIX guard's signals, `bin/analytics` as a shell script, POSIX signals and
process groups, names Windows reads otherwise, the stand-in uv script) and the same two aelix files. In the same job
Windows also ran the native console module (21 passed) and long paths with `LongPathsEnabled=0` (6 passed); they belong
to the Issue #8 inventory, not to this record. The `stdlib-only` smokes' two `not_run` are the observation probes on
every leg. The lint job passed: ruff, and the document checker (55 Markdown files, 499 local links, 9 JSON files, no
error).

JUnit files (sha256, first 12 characters; the full values are in the JSON): suite Linux `3c7b68a29b1c`, Windows
`50e72dd4cd49`, hosted macOS `974f45ce98ec`; aelix 3.12 / 3.11 Linux `1af1f7bbb98c` / `a19c546ff01e`, Windows
`3c39a4134a3a` / `39ce921e47b7`, hosted macOS `fc87fc5da538` / `91b6b63aa7d5`; launcher Linux `2834149ca3d9`, Windows
`65794f86380a`, hosted macOS `2358ed8af8be`.

## Checkout

- Root `.gitattributes`: `* text=auto eol=lf`, and `binary` for png, jpg, jpeg, gif, webp, ico, parquet, arrow,
  feather, ipc, xlsx, zip, whl and gz (ADR-0007 decision 1; `jpeg` and `ipc` added because the kit's chart and data
  suffixes name them). `tests/fixtures/records/.gitattributes` (`* -text`) stays.
- `git add --renormalize .` after adding the file staged nothing but the file itself: no renormalization commit
  (developer machine; also measured on a scratch clone with `core.autocrlf=true` during the design).
- `git ls-files --eol` on the developer machine, when the suite ran: 108 text files `i/lf w/lf` (this record's two files among them), 90 `-text` files `i/lf w/lf`, 8 empty
  `-text` files, 1 `-text` file `i/mixed w/mixed` (the fixture's intended CRLF journal). `test_the_checkout_is_lf`
  asserts no CRLF or mixed file outside `-text`, in the index and in the work tree. On the CI legs the observation
  (`checkout-eol`) is byte for byte the developer machine's on Linux, Windows and hosted macOS (sha256 `34dea976ca3a`:
  the same four counts and the same one CRLF file kept as is), and `test_the_checkout_is_lf` passed on each. On Windows
  that checkout was made with `core.autocrlf=true` from the system gitconfig: the root `.gitattributes`'s `eol=lf`
  decides, and the work tree is LF.

## Wheel and kit hash

| Value | Linux | Windows | macOS (hosted) | macOS (developer machine) |
|---|---|---|---|---|
| Wheel members, CRLF in a text member | 50 members, none | 50 members, none | 50 members, none | 50 members, none |
| sha256 of the (member, sha256) list | `4031237c57e3` | `4031237c57e3` | `4031237c57e3` | `4031237c57e3` |
| Checkout `kit_sha()` = digest of the tracked files' blobs | `a226e3e8c3a1` = `a226e3e8c3a1` | `a226e3e8c3a1` = `a226e3e8c3a1` | `a226e3e8c3a1` = `a226e3e8c3a1` | `a226e3e8c3a1` = `a226e3e8c3a1` |
| Installed run's `env.kit_sha256` (3.11 / 3.12) | `a226e3e8c3a1` / `a226e3e8c3a1` | `a226e3e8c3a1` / `a226e3e8c3a1` | `a226e3e8c3a1` / `a226e3e8c3a1` | `a226e3e8c3a1` / `a226e3e8c3a1` |

The CI values are the legs' observations (`wheel-members`, `kit-sha`, `installed-run-py3.11`, `installed-run-py3.12`),
each file byte for byte the developer machine's (sha256 `8f91b475edb8`, `ea977ef8c319`, `575e4f96411c`), and the tests
that assert them (`test_the_wheel_members_have_no_crlf`, `test_the_kit_sha_is_the_tracked_files_digest`,
`test_the_installed_command_drives_a_project_without_aelix[3.11, 3.12]`) passed on each leg. The installed run's script
`script.sha256` is `024f3144a10e` on every leg. On Windows the wheel was built by `uv build` from the checkout made with
`core.autocrlf=true`.

The expected `kit_sha256` is computed from git's blobs, not from the leg's files: each leg asserting it shows the legs
agree (ADR-0007 7 ③ by transitivity, review C1). The order is POSIX path parts, case by case; Windows sorts paths
without case, so a future file with a capital in its name would make Windows's `kit_sha` differ and this test fail.

## Fixed scenario

Every value is a constant in the tests; each leg asserts it.

| What | sha256 (first 12) or bytes | Test |
|---|---|---|
| `write_json` of the fixed object (JSON types only, review C12) | `e8129fd88998` (280 bytes) | `test_records_are_the_same_bytes_everywhere` |
| `append_jsonl` of it and `{"seq": 2}` | `c9dff9a10e3c` (231 bytes, literal bytes asserted) | same |
| New project `.gitattributes` / `.gitignore` / `pyproject.toml` / `.python-version` | `66b748264be1` / `aef713a60722` / `c7129862d02f` / `7b55f8e67b56` (`b"3.12\n"`) | `test_a_new_project_s_text_is_lf` |
| Script `scripts/lf.py` / its CRLF twin, as `script.sha256`; snapshot = the bytes written | `80b2e7c2f32f` / `e37afdb4de90` | `test_a_run_writes_lf_and_copies_bytes` |
| The run's `stdout.txt` (two prints, then `b"raw\r\n"` through the buffer) / `stderr.txt` | `b"a\nb\nraw\r\n"` / `b"e\n"` | same |
| `html` chart from a `_repr_html_` with CRLF and a lone CR; altair stand-in saved to a buffer | `58801acb0520` / `cd8a3744ac0e` | `test_charts_and_the_workspace_are_lf` |
| Workspace files the kit writes (8 files, `plotly.min.js` copied byte for byte) | no CR; `build.js` = `window.__LAB_BUILD__ = "<build>";\n` | same |
| Installed run's script (`SDK_SCRIPT`, written with `write_bytes`), 3.11 and 3.12 | `024f3144a10e` | e2e `test_the_installed_command_drives_a_project_without_aelix` |
| aelix inline code (`analytics_run` with `code`) | `7e1a4d5c3935`; code sent with CR line ends is written LF and prints `b"x\ny\n"` | `tests/test_extension.py` (aelix legs) |

The tests above passed on Linux, Windows and hosted macOS, and the observations each leg kept hold the same values as
the developer machine's: `fixed-records` (`run.json` and `journal.jsonl`), `new-project`, `run-bytes` (script copies and
`stdout.txt`) and `workspace` (the last three byte for byte the same file on every leg). The trust list's digest in
`fixed-records` differs by leg because the list holds the project's path; the test asserts it LF and canonical, not a
constant. The aelix inline code constant is asserted by `test_import_query_and_run_through_the_project_environment`,
which passed in both aelix legs of each OS.

The text-mode control (review C8): each leg keeps, as an observation, what a plain `open(p, "w").write("a\n")`
writes there. Developer machine, Linux and hosted macOS: `610a` (LF). Windows: `610d0a` (CRLF), which is what the kit
no longer uses.

## `line_endings_only`

| Case | Result (developer machine) |
|---|---|
| LF recorded, CRLF now / CRLF recorded, LF now / LF recorded, mixed now | `changed` with the reason |
| Mixed when recorded (now LF or CRLF), lone CRs, content changed too | `changed`, no reason |
| NUL in the first 8 KiB / NUL just after it | no reason / the reason |
| Size limit (made small): equal / one byte over | the reason / no reason |
| Block ends (block sizes 1–8, sniff 3, 200 random texts each) | equal to the whole-file digests |
| Hash cache: the digests kept with the entry, an older three-value entry read, a file changed while read | kept; read; no reason and nothing kept |
| Hash cache: a file that could not be read once / a file the rule does not judge (NUL in its head) | no reason and nothing kept, then judged at the next look / `null` kept and not read again |
| Status, its text and the Workspace (input to CRLF, then the script to CRLF, then both edited) | `STALE(data/in.csv (line endings only))`, `script edited since (line endings only)`, `Line endings only` badges and `현재 파일은 수정됨 (line endings only)`; after the edits, no reason and still stale |

Linux, Windows, macOS (hosted): the same cases passed (in `tests/test_line_endings.py`, 80 passed on each leg), and
each leg's status text (observation `line-endings-only`) reads `STALE(data/in.csv (line endings only))`; its paths and
times are the leg's own.

## CONTEXT

`CONTEXT.md` turned CRLF, then back to LF: the event text is `CONTEXT.md line endings changed` each time and the version
file holds the bytes as they were; a real edit after it is `CONTEXT.md updated (+1 -0 lines)`.

## Old records

CRLF `lab.json`, `run.json`, `data/raw/sales.meta.json`, the trust list and `journal.jsonl` were read by status (nothing
damaged or unsupported) and the Workspace; a new note was appended with LF after the CRLF lines and none of the other
files was rewritten (`test_old_crlf_records_are_read_and_kept`). The mixed-version fixture's CRLF journal is read as
before (`tests/test_record_contract.py`).

## Before

- The checkout on the Windows runner was CRLF (`core.autocrlf=true` from the system gitconfig, measured in the
  [Windows inventory](2026-10-02-issue-8-windows-inventory.md) and probe run 36899933022).
- The kit writing CRLF on Windows (records, template copies, inline scripts, Workspace, run logs) is **inferred** from
  CPython's text mode; no failing Windows run of the new tests at `e9392d9` was made (the optional test-first CI run
  was not used). The Windows leg's text-mode control (`610d0a`, above) shows that the mode `e9392d9`'s kit wrote with
  writes CRLF there; the old kit itself was not run on Windows, so this stays inferred. On the developer machine, the
  new tests of `7d8e449` run against `e9392d9`'s kit failed 20 of 46 (the
  guard found W1–W11, W13 and W14; the chart, Workspace, `line_endings_only`, CONTEXT and template tests failed), which
  shows they look at what changed; the tests whose difference is Windows's text mode passed there, as macOS writes LF.
  The guard as fixed after the implementation review (an explicit `newline` must be LF; import aliases, `**kwargs` and
  logging's file handlers counted) found the same 14 places in `e9392d9`'s kit and none in the measured tree.

## Not run

Real altair charts (no altair in the development or CI environments; a stand-in object checks the buffer route), a
Windows client edition or a standard-user token, a person moving a project between machines through Git with
autocrlf (Q17-b, #23), the line ends aelix's write and edit tools give, an old Windows editor's display, a real 64 MiB
file (the limit is tested with the constant made small). CONTRACTS G holds the list with its reasons.

## Judgement

| Criterion | Developer machine | Linux | Windows | macOS (hosted) |
|---|---|---|---|---|
| #42 ①: records and script copies of the same content hash the same on the three platforms (CI) | constants hold on this machine; judged by CI | pass | pass | pass |
| #42 ②: the wheel built from a Windows checkout has LF members | not_run (not Windows) | — | pass | — |
| #42 ③: CONTRACTS G's line-ending sentence matches the implementation | pass (document) | | | |
| ADR-0007 7 ①: no CRLF in the checkout's text (but `-text`) | pass | pass | pass | pass |
| ADR-0007 7 ②: records, template copies, inline scripts and the Workspace are LF and match the constants | pass | pass | pass | pass |
| ADR-0007 7 ③: wheel members LF and `kit_sha256` equal to the blob digest | pass | pass | pass | pass |
| ADR-0007 7 ④: the CRLF `lab.event/1` fixture is read | pass | pass | pass | pass |

#42 ① is met: every leg asserted the same constants and passed, and the values the legs kept are equal to each other
and to the developer machine's (records `e8129fd88998` / `c9dff9a10e3c`, the new project's four files, script copies
`80b2e7c2f32f` / `e37afdb4de90`, the installed run's script `024f3144a10e`, the two charts, `kit_sha256`
`a226e3e8c3a1`). #42 ② is met: on Windows, from a checkout made with `core.autocrlf=true`, the wheel's 50 members hold
no CRLF and their (member, sha256) list is `4031237c57e3`, the same as on Linux, hosted macOS and the developer machine.
No value the record expects is missing from the CI, and none differs.
