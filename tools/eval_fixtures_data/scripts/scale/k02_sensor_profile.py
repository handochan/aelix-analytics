"""센서별 시간대 평균: 센서 표를 lazy하게 읽어 요약하고 trend 차트를 남긴다 (평가 fixture, 실제 kit run)."""

import plotly.graph_objects as go
import polars as pl

import aelix_analytics as analytics

readings = analytics.scan("sensor_01")
profile = (
    readings.group_by("sensor", pl.col("ts").dt.hour().alias("hour"), maintain_order=True)
    .agg(pl.col("value").mean().alias("mean"), pl.len().alias("n"))
    .sort("sensor", "hour")
    .collect()
)
analytics.save(profile, "sensor_profile")

fig = go.Figure()
for sensor in sorted(set(profile["sensor"].to_list()))[:4]:
    part = profile.filter(pl.col("sensor") == sensor)
    fig.add_trace(go.Scatter(x=part["hour"].to_list(), y=part["mean"].to_list(), name=sensor))
fig.update_layout(title="센서별 시간대 평균", xaxis_title="시", yaxis_title="평균값")
analytics.visualize(fig, "trend", title="센서별 시간대 평균", data=profile)

analytics.record("note", f"센서 {profile['sensor'].n_unique()}개의 시간대 평균을 sensor_profile에 저장했다.")
print(profile.head(8))
