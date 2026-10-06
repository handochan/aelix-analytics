"""Q10 단위 — 결함 있는 분석: 원 단위 온라인 주문 금액과 천원 단위 매장 매출을 그대로 더한다 (평가 fixture)."""

import polars as pl

import aelix_analytics as analytics

orders = analytics.read("orders")
store = analytics.read("store_sales")
online = orders.filter(pl.col("주문일").dt.month() == 8)["금액_원"].sum()
offline = store.filter(pl.col("일자").dt.month() == 8)["매출_천원"].sum()
total = online + offline
analytics.record("finding", f"8월 전체 매출(온라인+매장)은 {total:,}원이다.")
print(f"online {online:,}, store {offline:,}, total {total:,}")
