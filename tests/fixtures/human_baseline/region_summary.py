"""Human-editable SDK analysis with an explicit chart-data record."""

import plotly.graph_objects as go
import polars as pl

import aelix_analytics as analytics

sales = analytics.read("sales")
summary = sales.group_by("region").agg(
    pl.len().alias("orders"),
    pl.col("units").sum(),
    pl.col("revenue").sum(),
).sort("region")
analytics.save(summary, "region_summary")
figure = go.Figure(go.Bar(x=summary["region"].to_list(), y=summary["revenue"].to_list()))
analytics.chart(figure, "region-revenue", title="Revenue by region", data=summary)
print("Total revenue:", summary["revenue"].sum())
