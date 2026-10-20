# Current behavior baseline

This is the regression baseline for [Issue #7](https://github.com/handochan/aelix-analytics/issues/7),
starting from runtime commit `8b63fd09a1c6452e74ad52579c27c40a7fbe0474`.
It records the current [contract](CONTRACTS.md) before naming, packaging and storage changes.
It is separate from the historical Engine comparison in [EVALUATION.md](EVALUATION.md).
The first recorded execution is in the [2026-09-19 verification result](verification/2026-09-19-baseline.md),
and its independent review with the resulting fix is in the
[2026-09-20 review result](verification/2026-09-20-baseline-review.md).

## Inputs and expected behavior

The [six-row synthetic fixture](../tests/fixtures/human_baseline/README.md) includes source
CSV, independent expected values, SQL and editable Python scripts. No real user data or
session is required. The existing [kit regressions](../tests/test_kit.py) cover additional
failure, timeout, concurrency, query, schema and HTML-reference cases.

| Scenario | Observable result |
|---|---|
| Init/import/query | New project, immutable imported data, source hash, exact per-region SQL results |
| SDK analysis | Correct per-region output and plotted data, input hash and executed-code snapshot |
| Findings/context | Unicode notes, run/chart/data references, resolved question and preserved context version |
| Failure/repair | Exit 3 with `run.status=error`, traceback, then a separate successful run; failed record unchanged |
| Edit/replay/history | Filtered chart becomes version 2; original snapshot replays after live code deletion; old run bytes unchanged |
| Report regeneration | Historical run pages exist, no analysis rerun, retained run files unchanged |
| Ordinary libraries | DuckDB/Polars output and matplotlib capture without an SDK import; missing plotted data remains explicit |

The rendering command is `render` (`report` before kit 0.3.0, which renamed the public names only).
This baseline does not change runtime behavior, or treat agent-driven automated CLI execution
as an actual human usability study. It tests the human entrypoint's default `actor=human`
without an inherited agent session.

## Reproduce the kit checks

From the repository root, use the locked development environment:

```sh
uv sync --locked
uv run --locked pytest -q --timeout 600 --timeout-method=thread tests/test_human_baseline.py
uv run --locked pytest -q --timeout 600 --timeout-method=thread tests/test_kit.py tests/test_doc_checks.py
uv run --locked ruff check tests extension src tools
```

The commands the repository CI runs, and their pins, are in [CONTRIBUTING.md](../CONTRIBUTING.md#현재-개발-검증);
that guide is the current reference where the two differ.

For retained artifacts, replace `<run-id>` with a fresh UUID and keep each execution's
directory unique. Pytest deletes an existing `--basetemp` directory; do not reuse a path
that an earlier handoff cites.

```sh
uv run --locked pytest -q --timeout 600 --timeout-method=thread tests/test_human_baseline.py \
  --basetemp .devstate/runs/<run-id>/evidence/human \
  --junitxml .devstate/runs/<run-id>/evidence/human.xml
uv run --locked python tools/check_docs.py \
  --output .devstate/runs/<run-id>/evidence/docs.json
```

Each human scenario writes `commands.jsonl` beside its generated project. It contains
the exact argv, working directory, time, exit code, stdout and stderr. Runs additionally
retain `run.json`, code snapshots, logs, input hashes, chart files/data and the runtime
environment. Repository checks must also retain the inspected source revision or dirty
snapshot as required by [PROJECT-OPERATIONS.md](PROJECT-OPERATIONS.md). A new baseline record lists the
sha256 of `tests/test_human_baseline.py` beside the fixture files' hashes, since its assertions are part of
what was checked (the 2026-09-19 record pins them only through its `verification_revision`).

These automated scenarios use `python -m aelix_analytics` (named `labkit` before kit 0.2.0) with the
checkout's `src/` on `PYTHONPATH` in the locked development environment.
The shell wrapper is a separate smoke check: use `bin/analytics init` (`bin/lab` before kit 0.3.0) with
a fresh project and an isolated `AELIX_ANALYTICS_TRUST_FILE`, then import the fixture and execute its scripts with
`AELIX_ANALYTICS_PROJECT` pointing at that project. The variables' old names (`AELIX_LAB_*`, `LAB_*`) are
refused with an error since kit 0.3.0, so unset them first. This exercises the wrapper's project environment
creation; it is not a distribution-installation or cross-platform support claim. From the repository root
(POSIX shell):

```sh
S=.devstate/runs/<run-id>/evidence/shell
unset AELIX_LAB_HOME AELIX_LAB_TRUST_FILE AELIX_LAB_PROJECT AELIX_LAB_UV AELIX_LAB_KIT LAB_PROJECT LAB_ACTOR LAB_SESSION LAB_STYLE
export AELIX_ANALYTICS_TRUST_FILE="$PWD/$S/trust.json"
bin/analytics init "$S/project" --title "Shell smoke" --goal "Compare regional revenue"
export AELIX_ANALYTICS_PROJECT="$PWD/$S/project"
bin/analytics import tests/fixtures/human_baseline/sales.csv --name sales --description "Six synthetic rows"
cp tests/fixtures/human_baseline/region_summary.py "$S/project/scripts/"
bin/analytics run scripts/region_summary.py --title "All channels"
bin/analytics status --part records
```

Record it as the other checks are recorded: each command's argv and exit code, and the sha256 of the raw
log (stdout and stderr). The 2026-09-19 record's `shell_wrapper` has only a command count and a result;
it is the record of a run already made and stays as it is (Issue #22).

## Reproduce the Aelix integration checks

The extension test modules skip when Aelix is absent. A green kit run therefore does not
establish host integration. Run them in the isolated environment [CONTRIBUTING.md](../CONTRIBUTING.md#현재-개발-검증)
gives (`uv run --isolated --no-project … --with aelix==… --with pytest==… --with pytest-timeout==…`, the five
modules, `--timeout-method=thread`), which is the one the CI runs, and record the resolved package versions.
The 2026-09-19 baseline ran `tests/test_extension.py` alone with another interpreter
(`"$AELIX_TEST_PYTHON" -m pytest -q -p no:cacheprovider tests/test_extension.py -o addopts=""`); an editable
Aelix install also needs its actual source revision recorded.

The [extension suite](../tests/test_extension.py) invokes the real extension loader and
registered tools/hooks, including a project uv subprocess, error handling and cancellation.
Tool calls use controlled contexts; this does not exercise an actual model conversation
or interactive TUI. Do not copy an adjacent checkout's test/CI result as this repository's result.

## Scope of the evidence

Keep execution success, input/code traceability, rerun comparison, expected-value checks
and human conclusion review separate. Snapshot replay here uses the same environment
and current input bytes; it does not demonstrate historical source-data retention or
reproduction in another environment. The saved run hashes demonstrate that these operations
preserved this fixture's prior records, not a general immutability guarantee.

This baseline contributes examples for Q8, Q13, Q15 and Q17. It does not pass those release
gates in full: clean distribution installation, model-call blocking/instrumentation,
actual human usability, real-model handoff, browser navigation and other supported hosts
remain separate verification work. See [BETA-QUALITY-GATES.md](BETA-QUALITY-GATES.md).
