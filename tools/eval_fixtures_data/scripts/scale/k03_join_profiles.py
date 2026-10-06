"""지역 요약과 센서 프로파일을 잇는다: 없는 열로 결합해 실패한다 (평가 fixture, 실제 kit의 오류 run)."""

import aelix_analytics as analytics

regions = analytics.read("data/derived/region_summary.parquet")
profile = analytics.read("data/derived/sensor_profile.parquet")
print(f"regions {regions.height} rows, profile {profile.height} rows")
joined = regions.join(profile, left_on="지역", right_on="region")
print(joined)
