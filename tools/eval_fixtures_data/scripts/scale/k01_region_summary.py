"""지역별 매출 요약: 한국어 열의 원본을 읽어 파생 표와 Visualization을 남긴다 (평가 fixture, 실제 kit run)."""

import plotly.graph_objects as go
import polars as pl

import aelix_analytics as analytics

sales = analytics.read("sales_kr")
summary = (
    sales.group_by("지역", maintain_order=True)
    .agg(pl.col("매출액").sum().alias("매출액_합계"), pl.col("주문수").sum().alias("주문수_합계"))
    .sort("지역")
)
analytics.save(summary, "region_summary")

fig = go.Figure(go.Bar(x=summary["지역"].to_list(), y=summary["매출액_합계"].to_list(), name="매출액"))
fig.update_layout(title="지역별 매출액 합계", xaxis_title="지역", yaxis_title="매출액(원)")
analytics.visualize(fig, "region-total", title="지역별 매출액 합계",
                    caption="sales_kr의 모든 일자를 지역별로 합함", data=summary)

top = summary.sort("매출액_합계", descending=True).row(0, named=True)
analytics.record("finding", f"매출액 합계가 가장 큰 지역은 {top['지역']}({top['매출액_합계']:,}원)이다.")
print(summary)
