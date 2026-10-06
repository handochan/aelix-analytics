"""스텝 프로파일과 컨텍스트×스텝 점수 (참고 세션 재현).

점수는 트레이스의 스텝 평균을 sensor×step의 전체 샘플 평균·표준편차로 표준화한다. 평가 값(스텝 평균)과
분모 표준편차(전체 샘플)의 집계 단위가 다르다.
"""

import plotly.graph_objects as go
import polars as pl

import aelix_analytics as analytics

traces = analytics.read("traces")
step_profile = (
    traces.group_by("sensor", "step", maintain_order=True)
    .agg(pl.col("value").mean().alias("mean"), pl.col("value").std().alias("std"), pl.len().alias("n"))
    .sort("sensor", "step")
)
analytics.save(step_profile, "step_profile")

step_scores = (
    traces.group_by("context_id", "sensor", "step", maintain_order=True)
    .agg(pl.col("value").mean().alias("step_mean"))
    .join(step_profile.select("sensor", "step", "mean", "std"), on=["sensor", "step"])
    .with_columns(((pl.col("step_mean") - pl.col("mean")) / pl.col("std")).alias("score"))
    .select("context_id", "sensor", "step", "step_mean", "score")
    .sort("context_id", "sensor", "step")
)
analytics.save(step_scores, "step_scores")

worst = (
    step_scores.group_by("sensor", "step", maintain_order=True)
    .agg(pl.col("score").abs().max().alias("max_abs_score"))
    .sort("sensor", "step")
)
fig = go.Figure()
for sensor in worst["sensor"].unique(maintain_order=True).to_list():
    part = worst.filter(pl.col("sensor") == sensor)
    fig.add_trace(go.Bar(x=part["step"].to_list(), y=part["max_abs_score"].to_list(), name=sensor))
fig.update_layout(title="스텝별 최대 |점수|", xaxis_title="step", yaxis_title="max |score|", barmode="group")
analytics.visualize(fig, "step-score-max", title="스텝별 최대 |점수|", data=worst)

top = step_scores.with_columns(pl.col("score").abs().alias("abs")).sort("abs", descending=True).row(0, named=True)
analytics.record("finding", f"컨텍스트×스텝 점수의 최대 |score|는 {abs(top['score']):.3f}"
                            f"({top['context_id']}, {top['sensor']}, step {top['step']})이다.")
print(worst)
