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
REGIONS = {"Seoul": (1.00, 0.55), "Gyeonggi": (0.80, 0.45), "Busan": (0.45, 0.25),
           "Daegu": (0.35, 0.12)}

CONTEXT = """# {title}

> The **current context** of this analysis, kept up to date by people and agents together.
> Each change leaves a version in `.lab/context/` and an entry on the journal's timeline.

## Goal <!-- pin -->
Find out how fast each region moves its sales online, as a basis for the 2026 store plan.

## Analysis focus <!-- pin -->
- When did online revenue overtake store revenue?
- How far apart are the regions' growth rates?

## Next steps <!-- pin -->
- Check the monthly trend

## Current findings
- (none yet)

## Definitions and rules
- Revenue: the amount after discounts, in KRW
- Month: the month of the order date (order_date)

## Data sources
| Dataset | Source | Period and scope | Notes |
|---|---|---|---|
| sales | synthetic demo data | 2024-01 to 2025-12, day × region × channel | invented figures |

## Assumptions and limitations
- Returns and cancellations are not in the data
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
    # Split at LF after text mode has turned CRLF and a lone CR into LF, so U+2028, U+2029 and
    # U+0085 do not split the line (CONTRACTS D). The last line that starts with "{" is the result.
    lines = [line for line in proc.stdout.split("\n") if line.startswith("{")]
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
                     description="Synthetic orders 2024–2025 (day × region × channel, invented "
                                 "figures)",
                     actor=actor, session=session)
    core.write_text(project.context, CONTEXT.format(title=project.title()))
    core.sync_context(project, actor=actor, session=session)
    for script in sorted((s for s in SCRIPTS.iterdir() if s.name.endswith(".py")),
                         key=lambda s: s.name):
        with resources.as_file(script) as source:
            shutil.copyfile(source, project.scripts / script.name)

    first = _run(project, "scripts/01_monthly_revenue.py", "Monthly revenue trend", actor)
    question = core.add_note(project, "question", "When did online revenue overtake store revenue?",
                             actor=actor, session=session)
    second = _run(project, "scripts/02_channel_mix.py", "Channel mix by region in 2025", actor)
    third = _run(project, "scripts/03_region_growth.py", "Growth by region", actor)

    import polars as pl

    monthly = pl.read_parquet(project.derived / "monthly_revenue.parquet")
    wide = monthly.pivot(on="channel", index="month", values="revenue").sort("month")
    crossed = wide.filter(pl.col("online") > pl.col("store"))
    month = crossed["month"][0].strftime("%Y-%m") if crossed.height else None
    finding_text = (f"Total online revenue first overtook store revenue in {month}." if month
                    else "Online revenue never overtook store revenue in a month of the period.")
    core.add_note(project, "finding", finding_text, refs=[first["id"]], resolves=question["id"],
                  actor=actor, session=session)
    core.add_note(project, "assumption", "The data has no returns or cancellations, so revenue is "
                  "summed as is.", refs=["sales"], actor=actor, session=session)
    core.add_note(project, "decision", "Regions are compared by their 2025 annual totals.",
                  refs=[second["id"], third["id"]], actor=actor, session=session)
    core.add_note(project, "todo", "Change script 03 to analytics.visualize(data=...) so the growth "
                  "chart records its data", refs=[third["id"]], actor=actor, session=session)
    text = project.context.read_text(encoding="utf-8").replace(
        "- (none yet)", f"- {finding_text} (run {first['id']})")
    core.write_text(project.context, text)
    core.sync_context(project, actor=actor, session=session)
    rerun = _run(project, "", "Rerun comparison of the monthly revenue", actor,
                 ["--rerun-of", first["id"]])

    from . import report

    return {"runs": [first["id"], second["id"], third["id"], rerun["id"]],
            "reproduction": rerun.get("reproduction"), "report": report.build(project)}
