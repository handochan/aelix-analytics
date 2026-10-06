"""Year-over-year growth by region — Polars and matplotlib, the usual way (plt.show; the chart
records no attached data)."""

import matplotlib.pyplot as plt
import polars as pl

sales = pl.read_parquet("data/raw/sales.parquet")
yearly = (
    sales.group_by("region", pl.col("order_date").dt.year().alias("year"))
    .agg(pl.col("revenue").sum())
    .pivot(on="year", index="region", values="revenue")
)
growth = yearly.with_columns(
    ((pl.col("2025") / pl.col("2024") - 1) * 100).alias("growth_pct")
).sort("growth_pct")
growth.write_parquet("data/derived/region_growth.parquet")

fig, ax = plt.subplots(figsize=(7, 3.4))
ax.barh(growth["region"], growth["growth_pct"], height=0.5)
ax.axvline(0, color="#c3c2b7", linewidth=1)
ax.set_title("Revenue growth by region (2025 vs 2024, %)")
plt.show()
