# Issue #42 line endings — 2026-10-06

[Issue #42](https://github.com/handochan/aelix-analytics/issues/42) implements
[ADR-0007](../decisions/0007-line-endings.md) (the owner's decision of 2026-10-03 on
[#37](https://github.com/handochan/aelix-analytics/issues/37)): the repository and the text the kit writes are LF on
every platform, a user's file keeps its bytes, a change of line endings alone is named `line_endings_only`, and a new
project gets a `.gitattributes` that turns Git's line-ending conversion off. It changes the bytes the kit writes on
Windows, so it is a record-behaviour and platform change and this record is required (operations policy, evidence
criteria). What the results mean for the contract is in [CONTRACTS.md](../CONTRACTS.md) B, E, F and G; G is the single
statement of the limits and of what was not run.

**State of this record.** The developer machine's macOS results are in. The repository CI's Linux and Windows legs (pull
request push) and its hosted macOS legs (`ci:macos` label) have not run yet: their cells say `CI 실행 예정` (to be run)
and are filled from the pull request's CI, at the head whose merge order and rebase are settled (design review C2). A
cell that says so is not a pass. The JSON beside this file,
[2026-10-06-issue-42-line-endings.json](2026-10-06-issue-42-line-endings.json), holds every value below and the
evidence files' sha256.

## Source and environment

| Item | Basis |
|---|---|
| Measured code | Branch `feat/issue-42-lf-everywhere` on `e9392d9` (origin/main: #9 public names, kit 0.3.0; #12 PR-1 and PR-2), the implementation commit of this PR. The developer machine measured its working tree before the commit; the tree's non-document files are those of the commit (the JSON's `measured_tree`: the same digest of every path but `docs/`, `README.md` and `CONTRIBUTING.md`) |
| Kit version | 0.5.0 (`KIT_VERSION`, pyproject, manifest, `uv.lock`), on the assumption that #36 merges first as 0.4.0; if #42 merges first it becomes 0.4.0 and this record is measured again (design 3.8, review C2) |
| Workflow | `.github/workflows/ci.yml`, unchanged by this PR |
| Code run (Linux, Windows) | CI 실행 예정 |
| macOS run (label) | CI 실행 예정 |
| Developer machine | macOS 26.6.2 (25G83) arm64, uv 0.11.19, Git without `core.autocrlf`. CPython 3.12.13 for the development environment (suite, end-to-end leg, smoke), 3.12.13 and 3.11.15 for the aelix integration, 3.11.15 for the launcher tests and the stdlib-only smoke. Trust list and uv were the run's own (`AELIX_ANALYTICS_TRUST_FILE`, `AELIX_ANALYTICS_UV` inside the run's evidence folder), the old `LAB_*`/`AELIX_LAB_*` names unset |

## Results

| Check | Linux | Windows | macOS (hosted) | macOS (developer machine) |
|---|---|---|---|---|
| Repository suite | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 1005 passed, 45 skipped (the Windows-only tests, the two aelix files without aelix, the end-to-end tests without `AELIX_ANALYTICS_TEST_INSTALL=1`, the cases only CPython 3.14 or another file system has) |
| Distribution with its end-to-end leg (`AELIX_ANALYTICS_TEST_INSTALL=1`, `tests/test_packaging.py tests/test_launcher.py`) | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 145 passed, 1 skipped (the Windows-only launcher case) |
| `tests/test_line_endings.py` (in the suite) | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 46 passed |
| aelix 0.1.0b2 integration, CPython 3.12 / 3.11 | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 141 / 141 passed |
| The launcher on CPython 3.11 | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 120 passed, 1 skipped (the Windows-only case) |
| Platform smoke, required gate | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | held: 25 hold, 0 violated |
| Stdlib-only smoke on CPython 3.11 | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | held: 15 hold, 0 violated (the two `not_run` are observation probes, not required) |
| Usage report (aelix 0.1.0b2, 3.12) | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 58/58 required facts, 42/42 invariants, exit 0 |

macOS writes LF in text mode, so the developer machine's passes say nothing about Windows's bytes (ADR-0002: a pass on
one platform is not another's result). They show the tests run and the constants they assert, which the Linux and
Windows legs must meet as well.

## Checkout

- Root `.gitattributes`: `* text=auto eol=lf`, and `binary` for png, jpg, jpeg, gif, webp, ico, parquet, arrow,
  feather, ipc, xlsx, zip, whl and gz (ADR-0007 decision 1; `jpeg` and `ipc` added because the kit's chart and data
  suffixes name them). `tests/fixtures/records/.gitattributes` (`* -text`) stays.
- `git add --renormalize .` after adding the file staged nothing but the file itself: no renormalization commit
  (developer machine; also measured on a scratch clone with `core.autocrlf=true` during the design).
- `git ls-files --eol` on the developer machine, when the suite ran (before this record's own two files): 106 text files `i/lf w/lf`, 90 `-text` files `i/lf w/lf`, 8 empty
  `-text` files, 1 `-text` file `i/mixed w/mixed` (the fixture's intended CRLF journal). `test_the_checkout_is_lf`
  asserts no CRLF or mixed file outside `-text`, in the index and in the work tree. Windows (core.autocrlf=true):
  CI 실행 예정.

## Wheel and kit hash

| Value | Linux | Windows | macOS (hosted) | macOS (developer machine) |
|---|---|---|---|---|
| Wheel members, CRLF in a text member | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 50 members, none |
| sha256 of the (member, sha256) list | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | `44a0c90fcebf` |
| Checkout `kit_sha()` = digest of the tracked files' blobs | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | `f602202aa658` = `f602202aa658` |
| Installed run's `env.kit_sha256` (3.11 / 3.12) | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | `f602202aa658` / `f602202aa658` |

The expected `kit_sha256` is computed from git's blobs, not from the leg's files: each leg asserting it shows the legs
agree (ADR-0007 7 ③ by transitivity, review C1). The order is POSIX path parts, case by case; Windows sorts paths
without case, so a future file with a capital in its name would make Windows's `kit_sha` differ and this test fail.

## Fixed scenario

Every value is a constant in the tests; each leg asserts it.

| What | sha256 (first 12) or bytes | Test |
|---|---|---|
| `write_json` of the fixed object (JSON types only, review C12) | `e8129fd88998` (280 bytes) | `test_records_are_the_same_bytes_everywhere` |
| `append_jsonl` of it and `{"seq": 2}` | `c9dff9a10e3c` (231 bytes, literal bytes asserted) | same |
| New project `.gitattributes` / `.gitignore` / `pyproject.toml` / `.python-version` | `6a62913aa094` / `aef713a60722` / `c7129862d02f` / `7b55f8e67b56` (`b"3.12\n"`) | `test_a_new_project_s_text_is_lf` |
| Script `scripts/lf.py` / its CRLF twin, as `script.sha256`; snapshot = the bytes written | `80b2e7c2f32f` / `e37afdb4de90` | `test_a_run_writes_lf_and_copies_bytes` |
| The run's `stdout.txt` (two prints, then `b"raw\r\n"` through the buffer) / `stderr.txt` | `b"a\nb\nraw\r\n"` / `b"e\n"` | same |
| `html` chart from a `_repr_html_` with CRLF and a lone CR; altair stand-in saved to a buffer | `58801acb0520` / `cd8a3744ac0e` | `test_charts_and_the_workspace_are_lf` |
| Workspace files the kit writes (8 files, `plotly.min.js` copied byte for byte) | no CR; `build.js` = `window.__LAB_BUILD__ = "<build>";\n` | same |
| Installed run's script (`SDK_SCRIPT`, written with `write_bytes`), 3.11 and 3.12 | `024f3144a10e` | e2e `test_the_installed_command_drives_a_project_without_aelix` |
| aelix inline code (`analytics_run` with `code`) | `7e1a4d5c3935`; code sent with CR line ends is written LF and prints `b"x\ny\n"` | `tests/test_extension.py` (aelix legs) |

The text-mode control (review C8): each leg keeps, as an observation, what a plain `open(p, "w").write("a\n")`
writes there. Developer machine: `610a` (LF). Windows: CI 실행 예정 (CRLF expected there, which is what the kit no
longer uses).

## `line_endings_only`

| Case | Result (developer machine) |
|---|---|
| LF recorded, CRLF now / CRLF recorded, LF now / LF recorded, mixed now | `changed` with the reason |
| Mixed when recorded (now LF or CRLF), lone CRs, content changed too | `changed`, no reason |
| NUL in the first 8 KiB / NUL just after it | no reason / the reason |
| Size limit (made small): equal / one byte over | the reason / no reason |
| Block ends (block sizes 1–8, sniff 3, 200 random texts each) | equal to the whole-file digests |
| Hash cache: the digests kept with the entry, an older three-value entry read, a file changed while read | kept; read; no reason and nothing kept |
| Status, its text and the Workspace (input to CRLF, then the script to CRLF, then both edited) | `STALE(data/in.csv (line endings only))`, `script edited since (line endings only)`, `Line endings only` badges and `현재 파일은 수정됨 (line endings only)`; after the edits, no reason and still stale |

Linux, Windows, macOS (hosted): CI 실행 예정.

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
  was not used). On the developer machine, the new tests run against `e9392d9`'s kit failed 20 of 46 (the guard found
  W1–W11, W13 and W14; the chart, Workspace, `line_endings_only`, CONTEXT and template tests failed), which shows they
  look at what changed; the tests whose difference is Windows's text mode passed there, as macOS writes LF.

## Not run

Real altair charts (no altair in the development or CI environments; a stand-in object checks the buffer route), a
Windows client edition or a standard-user token, a person moving a project between machines through Git with
autocrlf (Q17-b, #23), the line ends aelix's write and edit tools give, an old Windows editor's display, a real 64 MiB
file (the limit is tested with the constant made small), and the Linux, Windows and hosted macOS legs above until the
pull request's CI runs. CONTRACTS G holds the list with its reasons.

## Judgement

| Criterion | Developer machine | Linux | Windows | macOS (hosted) |
|---|---|---|---|---|
| #42 ①: records and script copies of the same content hash the same on the three platforms (CI) | pass (constants asserted) | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 |
| #42 ②: the wheel built from a Windows checkout has LF members | not_run (not Windows) | — | CI 실행 예정 | — |
| #42 ③: CONTRACTS G's line-ending sentence matches the implementation | pass (document) | | | |
| ADR-0007 7 ①: no CRLF in the checkout's text (but `-text`) | pass | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 |
| ADR-0007 7 ②: records, template copies, inline scripts and the Workspace are LF and match the constants | pass | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 |
| ADR-0007 7 ③: wheel members LF and `kit_sha256` equal to the blob digest | pass | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 |
| ADR-0007 7 ④: the CRLF `lab.event/1` fixture is read | pass | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 |
