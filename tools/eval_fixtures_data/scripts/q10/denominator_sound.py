"""Q10 분모 — 바른 분석: 세션당 전환율 = 주문으로 이어진 세션 수 ÷ 세션 수 (평가 fixture)."""

import polars as pl

import aelix_analytics as analytics

sessions = analytics.read("sessions")
august = sessions.filter(pl.col("세션일").dt.month() == 8)
converted = august.filter(pl.col("주문여부")).height
rate = converted / august.height
analytics.record("finding", f"8월 세션당 전환율은 {rate:.1%}다.")
print(f"sessions {august.height}, converted {converted}, rate {rate:.4f}")
