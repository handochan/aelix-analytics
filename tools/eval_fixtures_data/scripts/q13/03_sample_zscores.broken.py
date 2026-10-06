"""시점별 샘플 점수 (참고 세션 재현, 첫 시도).

같은 sensor×step×rel_time의 프로파일과 개별 샘플을 비교한다. 파생 표 2개와 차트 2개를 남긴 뒤 Polars의
스칼라 접근 오류로 실패한다(참고 세션의 구현 오류).
"""

import plotly.graph_objects as go
import polars as pl

import aelix_analytics as analytics

DISPLAY_CONTEXT = "C017"

traces = analytics.read("traces")
profile = (
    traces.group_by("sensor", "step", "rel_time", maintain_order=True)
    .agg(pl.col("value").mean().alias("mean"), pl.col("value").std().alias("std"), pl.len().alias("n"))
    .sort("sensor", "step", "rel_time")
)
analytics.save(profile, "timepoint_profile")

scores = (
    traces.join(profile.select("sensor", "step", "rel_time", "mean", "std"), on=["sensor", "step", "rel_time"])
    .with_columns(((pl.col("value") - pl.col("mean")) / pl.col("std")).alias("z"))
    .with_columns(pl.col("z").abs().alias("abs_z"))
    .select("context_id", "sensor", "step", "rel_time", "t", "value", "z", "abs_z")
    .sort("context_id", "sensor", "t")
)
analytics.save(scores, "sample_scores")

fig = go.Figure(go.Histogram(x=scores["z"].to_list(), nbinsx=60))
fig.update_layout(title="시점별 z 분포", xaxis_title="z")
analytics.visualize(fig, "z-distribution", title="시점별 z 분포")

shown = (
    scores.filter((pl.col("context_id") == DISPLAY_CONTEXT) & (pl.col("sensor") == "pressure"))
    .join(profile.filter(pl.col("sensor") == "pressure"), on=["sensor", "step", "rel_time"])
    .sort("t")
)
fig = go.Figure()
fig.add_trace(go.Scatter(x=shown["t"].to_list(), y=(shown["mean"] + 3 * shown["std"]).to_list(), name="+3σ",
                         line={"dash": "dot"}))
fig.add_trace(go.Scatter(x=shown["t"].to_list(), y=(shown["mean"] - 3 * shown["std"]).to_list(), name="-3σ",
                         line={"dash": "dot"}))
fig.add_trace(go.Scatter(x=shown["t"].to_list(), y=shown["value"].to_list(), name=DISPLAY_CONTEXT))
fig.update_layout(title=f"pressure의 시점별 밴드({DISPLAY_CONTEXT})", xaxis_title="t")
analytics.visualize(fig, "pressure-band", title=f"pressure의 시점별 밴드({DISPLAY_CONTEXT})")

max_z = scores["abs_z"].max().item()
print(f"max |z| = {max_z:.4f}")
