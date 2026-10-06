"""Monthly revenue trend — the aelix_analytics API way (analytics.read / analytics.save /
analytics.visualize)."""

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
ax.set_title("Monthly revenue by channel")
ax.set_ylabel("KRW millions")
ax.legend(loc="upper left")
analytics.visualize(fig, "monthly-revenue",
                    caption="Monthly revenue per channel, after discounts.", data=monthly)
