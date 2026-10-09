"""Channel mix by region in 2025 — DuckDB SQL and plotly, the usual way (fig.show)."""

import duckdb
import plotly.express as px

mix = duckdb.sql(
    """
    SELECT region, channel, sum(revenue) / 1e6 AS revenue_m
    FROM 'data/imported/sales.parquet'
    WHERE year(order_date) = 2025
    GROUP BY ALL
    ORDER BY region, channel
    """
).pl()

fig = px.bar(mix, x="region", y="revenue_m", color="channel",
             title="Revenue and channel mix by region, 2025",
             labels={"revenue_m": "Revenue (KRW millions)", "region": "Region", "channel": "Channel"})
fig.update_traces(marker_line_width=2, marker_line_color="#fcfcfb")
fig.show()
