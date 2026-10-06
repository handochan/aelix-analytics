"""Q10 기간 — 결함 있는 분석: 20일까지만 있는 9월의 합계를 8월 한 달 전체와 비교한다 (평가 fixture)."""

import polars as pl

import aelix_analytics as analytics

orders = analytics.read("orders")
august = orders.filter(pl.col("주문일").dt.month() == 8)["금액_원"].sum()
september = orders.filter(pl.col("주문일").dt.month() == 9)["금액_원"].sum()
change = september / august - 1
direction = "감소" if change < 0 else "증가"
analytics.record("finding", f"9월 주문 금액은 8월보다 {abs(change):.1%} {direction}했다.")
print(f"august {august:,}, september {september:,}, change {change:+.4f}")
