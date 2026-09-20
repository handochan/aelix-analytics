# Issue #7 independent review — 2026-09-20

An independent review of [PR #29](https://github.com/handochan/aelix-analytics/pull/29) was
performed on `3af6e2a4b4e52ba3c04c7ad583df06c1ab5d197e`, separate from the author's self-review
behind the [2026-09-19 verification result](2026-09-19-baseline.md). It reproduced that result,
found one regression the baseline could not observe, and verified the fix in
`32efa6013cceec5e1c79f41bcf1337d0eade093e`. Runtime code and the current contract are unchanged.
This does not widen the release-gate scope of the baseline.

## Source and environment

| Item | Inspected basis |
|---|---|
| Reviewed revision | `3af6e2a4b4e52ba3c04c7ad583df06c1ab5d197e` (PR #29 head at review time) |
| Fix revision | `32efa6013cceec5e1c79f41bcf1337d0eade093e` (`tests/test_human_baseline.py` only) |
| Runtime | `8b63fd09a1c6452e74ad52579c27c40a7fbe0474`, unchanged by this review |
| Machine | macOS 26.6.2, Apple Silicon arm64; Python 3.12.13 |
| Dependency installation | Repository `uv.lock` `ddc1d075ad5a614ed90f722455d15551f275aa1e5dff6380f713a8033b5a0f3f`, uv 0.11.19 — the same environment as 2026-09-19 |
| Tooling | pytest 9.1.1, Ruff 0.16.7, jsonschema 4.26.0, markdown-it-py 4.2.0 |

## Reproduced before any change

| Check | Result |
|---|---|
| `uv run --locked pytest -q -ra` | 68 passed, 1 skipped — matches the recorded result |
| `uv run --locked ruff check kit tests extension tools` | passed |
| `git diff --check` | passed |
| `uv run --locked python tools/check_docs.py` | ok; 27 Markdown, 216 local links, 4 JSON — matches |
| Fixture bytes | all seven hashes match `fixture_sha256` in the 2026-09-19 record |
| Expected values | recomputed from the source CSV independently of this repository's code: 1/1/90, 2/4/510, 3/5/580 per region; 6 rows, 10 units, 1180 total; online 820 and store 360 |

The single skip is the extension module, because Aelix is absent from this environment.
The separate host suite and shell smoke were not re-executed; see the scope section.

## Finding and fix

The baseline asserted `"error" not in report` only on the `report` command's response, which
cannot carry that key: `report.build` returns `{path, pages, build}` or `{path, skipped}`, and an
exception fails the exit-code assertion first. The responses that can carry `{"error": ...}` are
`import` and `note`, which route through `_safe_report` and still report `ok=true` with exit 0
when the build fails. No other assertion in the suite inspected a report payload, so a naming or
storage change that broke report regeneration on those paths would have passed this baseline —
the class of regression the baseline exists to catch.

The assertion now runs in the CLI helper, so `import`, `note` and `report` are all covered, and
the `report` command's own response shape is pinned instead. Confirmed by replacing a generated
project's `report` directory with a file: both scenarios then fail at `import` with the
`NotADirectoryError` payload, where previously they passed. Two smaller corrections came with it:
the helper now selects the JSON line the way [the shared test helpers](../../tests/conftest.py)
do, and the live-code edit fails loudly if its replacement matches nothing.

Two further observations were not applied here, because applying them would mean editing the
record of an execution that already happened. The shell-wrapper entry records `commands: 8`
without `argv` or a log hash, unlike the repository and host entries, so it is the one claim a
reader can neither replay nor cross-check; and `fixture_sha256` covers the fixture files but not
the assertions in `tests/test_human_baseline.py`, which this record pins by revision only. Both
are evidence-format work for the repeatable-verification Issue, which already depends on this one.

## Re-verified after the fix

| Check | Result | Evidence |
|---|---|---|
| `uv run --locked pytest -q -ra` | 68 passed, 1 skipped | JUnit: 69 cases, 0 failures, 0 errors, 1 skipped |
| `uv run --locked ruff check kit tests extension tools` | passed | Console |
| `git diff --check` | passed | Console |
| `uv run --locked python tools/check_docs.py` | ok; 27 Markdown, 216 local links, 4 JSON at the fix revision, then 28/226/4 once this record was added | Retained reports |

## Scope of this review

This review re-ran the repository suite only. The Aelix integration suite and the real `bin/lab`
smoke recorded on 2026-09-19 were not re-executed. The fix changes one test module and no runtime
code, but that is a reasoned scope limit, not a fresh result for those checks. Clean distribution
installation, real-model conversation, human-agent-human handoff, model-call blocking or
instrumentation, human usability, browser navigation and other operating systems remain
unverified, as the earlier record already states. Q8/Q13/Q15/Q17 stay release gates with broader
requirements; see [the beta criteria](../BETA-QUALITY-GATES.md).

The 2026-09-19 record and its [machine-readable result](2026-09-19-baseline.json) are kept
unchanged. They describe the execution of that date, when the independent review had not run, and
[the operations policy](../PROJECT-OPERATIONS.md) does not allow a later check to be written back
into an earlier one.

## Inspect and replay

- [Portable instructions](../BASELINE-VERIFICATION.md)
- [Regression assertions](../../tests/test_human_baseline.py)
- [Synthetic fixture and independent expected values](../../tests/fixtures/human_baseline/README.md)
- [The result this review reproduced](2026-09-19-baseline.md)

Review evidence is local under `.devstate/runs/` in the Issue #7 worktree: run
`87445924-a0cd-4916-939c-06b027a9495e` holds the document check of the reviewed revision, and run
`cec7d941-eebf-49d0-b34d-32c67809252d` holds the document check, JUnit result and retained project
directories of the re-verification. These contain machine paths and are not part of the PR.
