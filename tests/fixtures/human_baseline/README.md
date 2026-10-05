# Synthetic human analysis baseline

These six invented orders contain no customer data, personal sessions or copied production
records. They exercise the kit's CLI and SDK contract (`python -m aelix_analytics` and
`import aelix_analytics`, named `labkit` when Issue #7 started).
The fixture uses current `lab.* /1` records and the `render` command (`report` before kit 0.3.0);
future storage formats are outside its scope.

The expected values in [expected.json](expected.json) were calculated from
[sales.csv](sales.csv), independently of the Python and SQL implementations:

| Region | Orders | Units | Revenue |
|---|---:|---:|---:|
| 대구 | 1 | 1 | 90 |
| 부산 | 2 | 4 | 150 + 360 = 510 |
| 서울 | 3 | 5 | 200 + 120 + 260 = 580 |
| Total | 6 | 10 | 1180 |

Online revenue is 360 for 부산 and 460 for 서울 (820 total); store revenue is 360.
Unequal group sizes and totals make a missing grouping key visible even if the overall
total remains correct.

- [by_region.sql](by_region.sql) queries the imported dataset through the CLI.
- [region_summary.py](region_summary.py) uses Polars with SDK reads, output saving and
  a Plotly chart whose plotted data is retained. The test edits the live script to select
  online orders, deletes that script, then replays the original run's code snapshot.
- [failure.py](failure.py) deliberately raises an error. Its run record and traceback must
  survive a later successful repair of the live script.
- [direct_libraries.py](direct_libraries.py) uses DuckDB, Polars and matplotlib without
  importing the SDK. Automatic image capture must not be reported as a plotted-data record.

Run [the regression scenarios](../../test_human_baseline.py) from the repository root:

```sh
uv run --locked pytest -q tests/test_human_baseline.py
```

Each scenario retains `project/` and `commands.jsonl` in its pytest temporary directory.
For a retained evidence run, use the commands in the
[baseline verification guide](../../../docs/BASELINE-VERIFICATION.md).

Generated projects contain local absolute paths and environment fingerprints. Keep them
in ignored development evidence; the committed inputs and expected values are portable.
