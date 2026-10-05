import polars as pl

summary = pl.read_csv("data/derived/region_summary.csv")
pl.DataFrame({"month": ["2026-01"], "revenue": [summary["revenue"].sum()]}).write_csv("data/derived/monthly.csv")
