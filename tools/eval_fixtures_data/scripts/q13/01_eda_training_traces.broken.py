"""학습 트레이스 EDA: 센서별 분포와 트레이스 평균의 이탈 (참고 세션 재현, 첫 시도).

첫 시도는 모델 도구 analytics_record의 인자(refs)를 스크립트 API에 그대로 넘겨 실패한다(참고 세션의 기록 API 오류).
"""

import polars as pl

import aelix_analytics as analytics

traces = analytics.read("traces")
summary = (
    traces.group_by("sensor", maintain_order=True)
    .agg(pl.len().alias("n"), pl.col("value").mean().alias("mean"), pl.col("value").std().alias("std"),
         pl.col("value").min().alias("min"), pl.col("value").max().alias("max"))
    .sort("sensor")
)
print(summary)
analytics.record("finding", f"센서 {summary.height}개, 트레이스 {traces['context_id'].n_unique()}개를 확인했다.",
                 refs=["traces"])
