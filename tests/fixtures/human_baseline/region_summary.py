"""Human-editable SDK analysis with an explicit chart-data record."""

import plotly.graph_objects as go
import polars as pl

import labkit as lab

sales = lab.read("sales")
summary = sales.group_by("region").agg(
    pl.len().alias("orders"),
    pl.col("units").sum(),
    pl.col("revenue").sum(),
).sort("region")
lab.save(summary, "region_summary")
figure = go.Figure(go.Bar(x=summary["region"].to_list(), y=summary["revenue"].to_list()))
lab.chart(figure, "region-revenue", title="Revenue by region", data=summary)
print("Total revenue:", summary["revenue"].sum())
