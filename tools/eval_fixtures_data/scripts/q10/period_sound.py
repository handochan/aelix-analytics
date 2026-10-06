"""Q10 기간 — 바른 분석: 9월 데이터가 있는 1–20일끼리 8월과 비교한다 (평가 fixture)."""

import polars as pl

import aelix_analytics as analytics

orders = analytics.read("orders")
first_days = orders.filter(pl.col("주문일").dt.day() <= 20)
august = first_days.filter(pl.col("주문일").dt.month() == 8)["금액_원"].sum()
september = first_days.filter(pl.col("주문일").dt.month() == 9)["금액_원"].sum()
change = september / august - 1
direction = "감소" if change < 0 else "증가"
analytics.record("finding", f"9월 1–20일 주문 금액은 8월 1–20일보다 {abs(change):.1%} {direction}했다.")
print(f"august 1-20 {august:,}, september 1-20 {september:,}, change {change:+.4f}")
