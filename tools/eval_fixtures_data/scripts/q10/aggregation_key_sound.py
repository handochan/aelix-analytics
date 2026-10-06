"""Q10 집계 키 — 바른 분석: 주문 단위로 금액을 합하고 품목 수는 따로 센다 (평가 fixture)."""

import polars as pl

import aelix_analytics as analytics

orders = analytics.read("orders")
items = analytics.read("order_items")
item_counts = items.group_by("order_id", maintain_order=True).agg(pl.len().alias("품목수"))
by_region = (
    orders.join(item_counts, on="order_id", how="left")
    .group_by("지역", maintain_order=True)
    .agg(pl.col("금액_원").sum().alias("주문금액_합계"), pl.col("품목수").sum().alias("품목수_합계"))
    .sort("지역")
)
analytics.save(by_region, "region_amount")
seoul = by_region.filter(pl.col("지역") == "서울")["주문금액_합계"][0]
analytics.record("finding", f"서울 지역의 주문 금액 합계(8월 1일–9월 20일)는 {seoul:,}원이다.")
print(by_region)
