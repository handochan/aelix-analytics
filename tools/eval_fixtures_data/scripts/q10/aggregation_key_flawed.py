"""Q10 집계 키 — 결함 있는 분석: 주문을 품목과 결합한 뒤 주문 금액을 합한다 (평가 fixture).

결합하면 주문 한 건이 품목 수만큼 여러 행이 되어 주문 금액이 그만큼 여러 번 더해진다.
"""

import polars as pl

import aelix_analytics as analytics

orders = analytics.read("orders")
items = analytics.read("order_items")
joined = orders.join(items, on="order_id")
by_region = joined.group_by("지역", maintain_order=True).agg(pl.col("금액_원").sum().alias("주문금액_합계")).sort("지역")
analytics.save(by_region, "region_amount_joined")
seoul = by_region.filter(pl.col("지역") == "서울")["주문금액_합계"][0]
analytics.record("finding", f"서울 지역의 주문 금액 합계(8월 1일–9월 20일)는 {seoul:,}원이다.")
print(f"orders {orders.height} rows, joined {joined.height} rows")
print(by_region)
