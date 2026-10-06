"""Populate a fresh project with synthetic sales data, three example runs, notes and a rerun.

The data is invented (seeded, deterministic) and says nothing about any real business.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date
from importlib import resources
from pathlib import Path

from . import _platform, core, data

SCRIPTS = resources.files(__package__) / "demo_scripts"
REGIONS = {"서울": (1.00, 0.55), "경기": (0.80, 0.45), "부산": (0.45, 0.25), "대구": (0.35, 0.12)}

CONTEXT = """# {title}

> 이 파일은 분석의 **현재 맥락**입니다. 사람과 에이전트가 함께 고칩니다.
> 바뀔 때마다 `.lab/context/`에 버전이 남고 journal 타임라인에 기록됩니다.

## 목표
온라인 전환 속도가 지역마다 어떻게 다른지 파악해 2026년 매장 운영 계획의 근거로 쓴다.

## 핵심 질문
- 온라인 매출은 언제 매장 매출을 넘어섰나?
- 지역별 성장률 차이는 얼마나 큰가?

## 데이터 출처
| 데이터셋 | 출처 | 기간·범위 | 비고 |
|---|---|---|---|
| sales | 합성 데모 데이터 | 2024-01 ~ 2025-12, 일×지역×채널 | 가상 수치 |

## 정의와 규칙
- 매출(revenue): 할인 적용 후 금액, 원 단위
- 월 기준: 주문일(order_date)의 달

## 가정과 제약
- 반품·취소는 반영되지 않은 데이터다

## 현재 결론
- (아직 없음)

## 다음 할 일
- 월별 추세 확인
"""


def generate(path: Path) -> Path:
    import numpy as np
    import polars as pl

    rng = np.random.default_rng(20260916)
    days = pl.date_range(date(2024, 1, 1), date(2025, 12, 31), "1d", eager=True)
    n = len(days)
    years = np.arange(n) / 365.0
    season = 1 + 0.12 * np.sin(2 * np.pi * (years - 0.2))
    weekday = np.array([d.weekday() for d in days.to_list()])
    frames = []
    for region, (scale, online_growth) in REGIONS.items():
        for channel in ("online", "store"):
            share = 0.40 if channel == "online" else 0.60
            trend = (1 + online_growth) ** years if channel == "online" else 0.94 ** years
            weekend = np.where(weekday >= 5, 0.95 if channel == "online" else 1.25, 1.0)
            revenue = 9_000_000 * scale * share * season * trend * weekend * rng.normal(1, 0.08, n)
            orders = np.maximum(1, np.round(revenue / rng.normal(48_000, 3_000, n)))
            discount = rng.uniform(0, 0.15, n)
            discount[rng.random(n) < 0.03] = np.nan
            frames.append(pl.DataFrame({
                "order_date": days, "region": region, "channel": channel,
                "orders": orders.astype(np.int64), "revenue": np.round(revenue).astype(np.int64),
                "discount_rate": np.round(discount, 3),
            }).with_columns(pl.col("discount_rate").fill_nan(None)))
    table = pl.concat(frames).sort("order_date", "region", "channel")
    path.parent.mkdir(parents=True, exist_ok=True)
    table.write_csv(path)
    return path


def _run(project: core.Project, script: str, title: str, actor: str, extra=()) -> dict:
    env = dict(os.environ)
    kit = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = kit + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["AELIX_ANALYTICS_ACTOR"] = actor
    env["MPLBACKEND"] = "Agg"
    cmd = [sys.executable, "-m", "aelix_analytics", "--project", str(project.root), "run",
           *([script] if script else []), "--title", title, *extra]
    proc = subprocess.run(cmd, capture_output=True, env=env, timeout=600,
                          **_platform.text_kwargs())
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise core.LabError("demo_run_failed", f"{script or extra}: no result",
                            details={"stderr": proc.stderr[-2000:]})
    result = json.loads(lines[-1])
    if not result.get("ok"):
        raise core.LabError("demo_run_failed", f"{script or extra}: {result.get('error')}")
    return result["run"]


def populate(project: core.Project, actor: str = "human", session=None) -> dict:
    if core.list_runs(project) or core.list_datasets(project):
        raise core.LabError("demo_not_empty", "demo needs a new, empty project",
                            hint="analytics demo NEW_DIR creates a new project and fills it")
    csv = generate(project.lab / "demo" / "sales_2024_2025.csv")
    data.import_file(project, str(csv), name="sales",
                     description="합성 주문 데이터 2024–2025 (일×지역×채널, 가상 수치)",
                     actor=actor, session=session)
    core.write_text(project.context, CONTEXT.format(title=project.title()))
    core.sync_context(project, actor=actor, session=session)
    for script in sorted((s for s in SCRIPTS.iterdir() if s.name.endswith(".py")),
                         key=lambda s: s.name):
        with resources.as_file(script) as source:
            shutil.copyfile(source, project.scripts / script.name)

    first = _run(project, "scripts/01_monthly_revenue.py", "월별 매출 추세", actor)
    question = core.add_note(project, "question", "온라인 매출은 언제 매장 매출을 넘어섰나?",
                             actor=actor, session=session)
    second = _run(project, "scripts/02_channel_mix.py", "2025년 지역별 채널 구성", actor)
    third = _run(project, "scripts/03_region_growth.py", "지역별 성장률", actor)

    import polars as pl

    monthly = pl.read_parquet(project.derived / "monthly_revenue.parquet")
    wide = monthly.pivot(on="channel", index="month", values="revenue").sort("month")
    crossed = wide.filter(pl.col("online") > pl.col("store"))
    month = crossed["month"][0].strftime("%Y-%m") if crossed.height else None
    finding_text = (f"전체 온라인 월 매출이 {month}에 처음 매장 매출을 넘어섰다." if month
                    else "기간 내 온라인 월 매출은 매장 매출을 넘지 못했다.")
    core.add_note(project, "finding", finding_text, refs=[first["id"]], resolves=question["id"],
                  actor=actor, session=session)
    core.add_note(project, "assumption", "반품·취소가 없는 데이터로 보고 매출을 그대로 합산한다.",
                  refs=["sales"], actor=actor, session=session)
    core.add_note(project, "decision", "지역 비교는 2025년 연간 합계로 한다.",
                  refs=[second["id"], third["id"]], actor=actor, session=session)
    core.add_note(project, "todo", "지역별 성장률 차트에 플롯 데이터를 기록하도록 03 스크립트를 "
                  "analytics.visualize(data=...)로 바꾸기", refs=[third["id"]], actor=actor, session=session)
    text = project.context.read_text(encoding="utf-8").replace(
        "- (아직 없음)", f"- {finding_text} (run {first['id']})")
    core.write_text(project.context, text)
    core.sync_context(project, actor=actor, session=session)
    rerun = _run(project, "", "월별 매출 재실행 검증", actor, ["--rerun-of", first["id"]])

    from . import report

    return {"runs": [first["id"], second["id"], third["id"], rerun["id"]],
            "reproduction": rerun.get("reproduction"), "report": report.build(project)}
