# Record-contract examples (ADR-0006)

Six synthetic projects, each with the judgments a reader of
[ADR-0006](../../../docs/decisions/0006-common-record-contract.md) must report when it reads the
project as it is. The records follow the [record schema](../../../docs/schemas/analytics-records.schema.json);
each `expected.json` follows [expected.schema.json](expected.schema.json). No kit writes these
families yet: the reader of Issue #12 and the writer of Issue #13 are tested against these cases.

| Case | What it holds |
|---|---|
| `normal` | Exact evidence, a reference given by name and bound by inference (resolves, not a confirmed link), a correction, a withdrawn decision, two complete Report revisions, a producer found by path and hash |
| `interrupted` | A running record whose process is gone, a final run without its completion event, a Report revision renamed before its completion event, a prepared staging directory, a cut-off last journal line |
| `missing` | A deleted run, an unknown visualization id, an overwritten derived table, a deleted code snapshot, an absent event, an input recorded without a hash, a Report revision whose content file was deleted |
| `conflict` | Two corrections of one finding, a save on a previous revision that is no longer the head, one `request_id` with two fingerprints, a status change from a state the record is not in |
| `mixed-version` | `lab.event/1` lines (CRLF) and `analytics.event/2` lines (LF) in one seq space, legacy references, two producers of the same path and hash |
| `future-version` | What a newer writer can leave: an unknown family version, kind, reference type and family, a run record of an unknown version, an unknown optional field, a marker that requires a newer writer |

## Conventions

- Each case is a project root after the #13 conversion: `analytics.json` (`analytics.project/2`)
  and `journal.jsonl`. Runs stay `lab.run/1`; the next run family is #13's to define. Files of the
  old layout are not moved (ADR-0006 section 9): `mixed-version` keeps its `lab.event/1` lines and
  `.lab/context/`.
- `expected.json` describes reading the case as it is. Each entry of `writes` is an independent
  attempt against the case as it is, not a sequence. Its `result` is `saved` (a new record),
  `existing` (the same request already completed: its result comes back), `resumed` (a revision
  renamed before its completion event: only the event is appended), or the error the kit returns
  (`request_conflict`, `revision_conflict`, `state_conflict`, `writer_too_old`).
- `references[].from` is an event id (`e6`), or `report:<report_id>/<revision_id>` for a manifest's
  `based_on.refs`. `ref` is `{"index": i}` into an `analytics.event/2` refs array, or
  `{"field": f, "value": v}` for a `lab.event/1` refs entry.
- `confirmed` follows ADR-0006 section 3: an `author` binding with an `observed` or `declared` basis
  that is `resolved`.
- Visualization candidates are `<run_id>/<visualization_id>`; lineage candidates are run ids.
- `unsupported.items[].location` is `journal:<line>` or the path of a record file.
- The request fingerprint is the sha256 of `{report_id, previous_revision, title, entry, files, based_on}`
  (`files` as `[{path, sha256}]` by path) serialized with sorted keys, no whitespace and UTF-8
  (ADR-0006 section 8). A write request carries the same fields.
- IDs, times and `env` values are synthetic. The running record in `interrupted` holds pid
  4294967295, which no process has on Linux, macOS or Windows.
- `.gitattributes` here is `* -text`: hashes are part of the expectations, and
  `mixed-version/journal.jsonl` keeps CRLF on its `lab.event/1` lines (as a Windows kit wrote them)
  and LF on its `analytics.event/2` lines (ADR-0007).
- Markdown files inside the cases hold no links; the documentation check reads them.

## Checks

[test_record_contract.py](../../test_record_contract.py) checks the schemas, every record against
them, hashes and fingerprints against the bytes, and each judgment against the rule ADR-0006 states
for it. It is not the reader. When a case changes, update its hashes in the same commit; the test
names any that no longer match.

```sh
uv run --locked pytest -q tests/test_record_contract.py
```
