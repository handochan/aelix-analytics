"""시점별 샘플 점수 (참고 세션 재현).

같은 sensor×step×rel_time의 프로파일과 개별 샘플을 비교한다. 기준 프로파일은 점수를 매기는 같은 학습 데이터로
추정했다. 점수 표는 컨텍스트×센서마다 최대 |z|, |z|>3 초과 횟수와 비율을 요약한다.
"""

import plotly.graph_objects as go
import polars as pl

import aelix_analytics as analytics

DISPLAY_CONTEXT = "C017"
THRESHOLD = 3.0

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

max_z = scores["abs_z"].max()
score_table = (
    scores.group_by("context_id", "sensor", maintain_order=True)
    .agg(pl.col("abs_z").max().alias("max_abs_z"),
         (pl.col("abs_z") > THRESHOLD).sum().alias("exceed_count"),
         (pl.col("abs_z") > THRESHOLD).mean().alias("exceed_ratio"))
    .sort("context_id", "sensor")
)
analytics.save(score_table, "score_table")
exceeded = score_table.filter(pl.col("exceed_count") > 0)
analytics.record("finding", f"시점별 점수: 점수 표 {score_table.height}행, |z|>{THRESHOLD:g} 초과 샘플 합 "
                            f"{score_table['exceed_count'].sum()}, 컨텍스트×센서당 평균 "
                            f"{score_table['exceed_count'].mean():.6f}, 초과가 있는 컨텍스트×센서 {exceeded.height}개.")
print(f"max |z| = {max_z:.4f}")
print(score_table.sort("max_abs_z", descending=True).head(10))
