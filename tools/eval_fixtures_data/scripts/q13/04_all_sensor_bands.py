"""전체 센서의 시점별 밴드 (참고 세션 재현: 사용자 요청으로 한 센서의 비교를 모든 센서로 넓힌다).

첨부 표에는 표시한 트레이스의 관측값과 프로파일 평균만 있다. 밴드 상·하한은 입력 timepoint_profile에 있다.
"""

import plotly.graph_objects as go
import polars as pl
from plotly.subplots import make_subplots

import aelix_analytics as analytics

DISPLAY_CONTEXT = "C017"

scores = analytics.read("data/derived/sample_scores.parquet")
profile = analytics.read("data/derived/timepoint_profile.parquet")
shown = (
    scores.filter(pl.col("context_id") == DISPLAY_CONTEXT)
    .join(profile, on=["sensor", "step", "rel_time"])
    .sort("sensor", "t")
)
sensors = sorted(set(shown["sensor"].to_list()))
fig = make_subplots(rows=len(sensors), cols=1, shared_xaxes=True, subplot_titles=sensors)
for row, sensor in enumerate(sensors, start=1):
    part = shown.filter(pl.col("sensor") == sensor)
    upper = (part["mean"] + 3 * part["std"]).to_list()
    lower = (part["mean"] - 3 * part["std"]).to_list()
    fig.add_trace(go.Scatter(x=part["t"].to_list(), y=upper, line={"width": 0}, showlegend=False), row=row, col=1)
    fig.add_trace(go.Scatter(x=part["t"].to_list(), y=lower, fill="tonexty", line={"width": 0}, showlegend=False),
                  row=row, col=1)
    fig.add_trace(go.Scatter(x=part["t"].to_list(), y=part["mean"].to_list(), name=f"{sensor} 평균",
                             line={"dash": "dot"}), row=row, col=1)
    fig.add_trace(go.Scatter(x=part["t"].to_list(), y=part["value"].to_list(), name=f"{sensor} {DISPLAY_CONTEXT}"),
                  row=row, col=1)
fig.update_layout(title=f"전체 센서의 시점별 밴드({DISPLAY_CONTEXT}, 평균±3σ)", height=900)
attached = shown.select("sensor", "t", "value", pl.col("mean").alias("profile_mean"))
analytics.visualize(fig, "all-sensor-bands", title=f"전체 센서의 시점별 밴드({DISPLAY_CONTEXT})", data=attached)
print(attached.head(8))
