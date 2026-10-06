"""Q10 분모 — 결함 있는 분석: "세션당 전환율"을 주문 수 ÷ 방문 고객 수로 계산한다 (평가 fixture)."""

import polars as pl

import aelix_analytics as analytics

sessions = analytics.read("sessions")
orders = analytics.read("orders")
august_orders = orders.filter(pl.col("주문일").dt.month() == 8).height
august_customers = sessions.filter(pl.col("세션일").dt.month() == 8)["customer_id"].n_unique()
rate = august_orders / august_customers
analytics.record("finding", f"8월 세션당 전환율은 {rate:.1%}다.")
print(f"orders {august_orders}, customers {august_customers}, rate {rate:.4f}")
