"""지역별 전년 대비 성장률 — Polars + matplotlib, 관용적 방식 (plt.show, 플롯 데이터 미기록)."""

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
ax.set_title("지역별 매출 성장률 (2025 vs 2024, %)")
plt.show()
