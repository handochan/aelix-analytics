"""2025년 지역별 채널 구성 — DuckDB SQL + plotly, 관용적 방식 (fig.show)."""

import duckdb
import plotly.express as px

mix = duckdb.sql(
    """
    SELECT region, channel, sum(revenue) / 1e6 AS revenue_m
    FROM 'data/raw/sales.parquet'
    WHERE year(order_date) = 2025
    GROUP BY ALL
    ORDER BY region, channel
    """
).pl()

fig = px.bar(mix, x="region", y="revenue_m", color="channel",
             title="2025년 지역별 매출과 채널 구성",
             labels={"revenue_m": "매출 (백만 원)", "region": "지역", "channel": "채널"})
fig.update_traces(marker_line_width=2, marker_line_color="#fcfcfb")
fig.show()
