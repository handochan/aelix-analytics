"""월별 매출 추세 — aelix_analytics API 방식 (analytics.read / analytics.save / analytics.visualize)."""

import matplotlib.pyplot as plt
import polars as pl

import aelix_analytics as analytics

sales = analytics.read("sales")
monthly = (
    sales.with_columns(pl.col("order_date").dt.truncate("1mo").alias("month"))
    .group_by("month", "channel")
    .agg(pl.col("revenue").sum().alias("revenue"))
    .sort("month", "channel")
)
analytics.save(monthly, "monthly_revenue")

fig, ax = plt.subplots()
for channel in ["online", "store"]:
    part = monthly.filter(pl.col("channel") == channel)
    ax.plot(part["month"], part["revenue"] / 1e6, label=channel)
ax.set_title("월별 매출 (채널별)")
ax.set_ylabel("백만 원")
ax.legend(loc="upper left")
analytics.visualize(fig, "monthly-revenue", caption="채널별 월 매출 합계. 할인 적용 후 금액 기준.",
          data=monthly)
