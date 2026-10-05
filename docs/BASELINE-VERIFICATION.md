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

The current rendering command is `report`. This baseline does not add the proposed
`render` command, change runtime behavior, or treat agent-driven automated CLI execution
as an actual human usability study. It tests the human entrypoint's default `actor=human`
without an inherited agent session.

## Reproduce the kit checks

From the repository root, use the locked development environment:

```sh
uv sync --locked
uv run --locked pytest -q tests/test_human_baseline.py
uv run --locked pytest -q tests/test_kit.py tests/test_doc_checks.py
uv run --locked ruff check kit tests extension src tools
```

For retained artifacts, replace `<run-id>` with a fresh UUID and keep each execution's
directory unique. Pytest deletes an existing `--basetemp` directory; do not reuse a path
that an earlier handoff cites.

```sh
uv run --locked pytest -q tests/test_human_baseline.py \
  --basetemp .devstate/runs/<run-id>/evidence/human \
  --junitxml .devstate/runs/<run-id>/evidence/human.xml
uv run --locked python tools/check_docs.py \
  --output .devstate/runs/<run-id>/evidence/docs.json
```

Each human scenario writes `commands.jsonl` beside its generated project. It contains
the exact argv, working directory, time, exit code, stdout and stderr. Runs additionally
retain `run.json`, code snapshots, logs, input hashes, chart files/data and the runtime
environment. Repository checks must also retain the inspected source revision or dirty
snapshot as required by [PROJECT-OPERATIONS.md](PROJECT-OPERATIONS.md).

These automated scenarios use `python -m aelix_analytics` (named `labkit` before kit 0.2.0) with the
checkout's `src/` on `PYTHONPATH` in the locked development environment.
The shell wrapper is a separate smoke check: use `bin/lab init` with a fresh project and
an isolated `AELIX_LAB_TRUST_FILE`, then import the fixture and execute its scripts with
`LAB_PROJECT` pointing at that project. This exercises the wrapper's project environment
creation; it is not a distribution-installation or cross-platform support claim.

## Reproduce the Aelix integration checks

The extension test module skips when Aelix is absent. A green kit run therefore does not
establish host integration. Use a separate interpreter with the intended Aelix and pytest
installed, and record both the package versions and the actual source revision when the
packages are editable installs:

```sh
AELIX_TEST_PYTHON=/path/to/aelix-environment/bin/python
"$AELIX_TEST_PYTHON" -m pytest -q -p no:cacheprovider tests/test_extension.py -o addopts=""
```

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
