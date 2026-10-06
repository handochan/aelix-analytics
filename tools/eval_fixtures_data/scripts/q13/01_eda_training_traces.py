"""학습 트레이스 EDA: 센서별 분포와 트레이스 평균의 이탈 (참고 세션 재현).

트레이스 평균의 3σ 이탈은 센서별로 각 트레이스(context_id)의 평균을 모아 그 평균들의 분포와 비교한다.
"""

import plotly.graph_objects as go
import polars as pl
from plotly.subplots import make_subplots

import aelix_analytics as analytics

traces = analytics.read("traces")
summary = (
    traces.group_by("sensor", maintain_order=True)
    .agg(pl.len().alias("n"), pl.col("value").mean().alias("mean"), pl.col("value").std().alias("std"),
         pl.col("value").min().alias("min"), pl.col("value").max().alias("max"))
    .sort("sensor")
)
print(summary)
analytics.record("finding", f"센서 {summary.height}개, 트레이스 {traces['context_id'].n_unique()}개를 확인했다.")

trace_means = (
    traces.group_by("sensor", "context_id", maintain_order=True)
    .agg(pl.col("value").mean().alias("trace_mean"))
    .with_columns(((pl.col("trace_mean") - pl.col("trace_mean").mean().over("sensor"))
                   / pl.col("trace_mean").std().over("sensor")).alias("z"))
    .sort("sensor", "context_id")
)
analytics.save(trace_means, "trace_means")

sensors = summary["sensor"].to_list()
fig = make_subplots(rows=2, cols=2, subplot_titles=sensors)
for i, sensor in enumerate(sensors):
    values = traces.filter(pl.col("sensor") == sensor)["value"].to_list()
    fig.add_trace(go.Histogram(x=values, nbinsx=40, name=sensor), row=i // 2 + 1, col=i % 2 + 1)
fig.update_layout(title="센서별 값 분포", showlegend=False)
analytics.visualize(fig, "sensor-histograms", title="센서별 값 분포", data=summary)

fig = go.Figure()
for sensor in sensors:
    part = trace_means.filter(pl.col("sensor") == sensor)
    fig.add_trace(go.Scatter(x=part["context_id"].to_list(), y=part["z"].to_list(), mode="markers",
                             name=sensor))
fig.add_hline(y=3, line_dash="dash")
fig.add_hline(y=-3, line_dash="dash")
fig.update_layout(title="트레이스 평균의 z (센서별)", yaxis_title="z")
analytics.visualize(fig, "trace-mean-z", title="트레이스 평균의 z (센서별)", data=trace_means)

outliers = trace_means.filter(pl.col("z").abs() > 3)
if outliers.height:
    items = ", ".join(f"{r['sensor']}의 {r['context_id']}(z={r['z']:.2f})" for r in outliers.iter_rows(named=True))
    analytics.record("finding", f"트레이스 평균이 센서별 분포의 3σ 밖인 경우는 {outliers.height}건이다: {items}.")
else:
    analytics.record("finding", "트레이스 평균이 센서별 분포의 3σ 밖인 경우는 없다.")
print(outliers)
