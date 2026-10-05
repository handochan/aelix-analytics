import polars as pl

sales = pl.read_csv("data/sales.csv")
summary = sales.group_by("region").agg(pl.col("revenue").sum())
summary.write_csv("data/derived/region_summary.csv")
