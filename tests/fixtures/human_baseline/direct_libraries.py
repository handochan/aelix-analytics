"""Ordinary DuckDB and matplotlib analysis, without importing the SDK."""

import duckdb
import matplotlib.pyplot as plt

summary = duckdb.sql("""
    SELECT channel, sum(revenue) AS revenue
    FROM 'data/imported/sales.parquet'
    GROUP BY channel ORDER BY channel
""").pl()
summary.write_parquet("data/derived/channel_summary.parquet")
plt.bar(summary["channel"].to_list(), summary["revenue"].to_list())
plt.title("Revenue by channel")
plt.show()
