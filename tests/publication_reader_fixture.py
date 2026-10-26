"""A saved publication with every reader surface (PUBLICATION-BUILD 12.6), for the reader and browser tests."""

from __future__ import annotations

import shutil
import struct
import zlib
from pathlib import Path

from aelix_analytics import core, runner
from aelix_analytics.publishing import build
from aelix_analytics.publishing.projection import ShareRequest
from aelix_analytics.report_renderers import StaticReportRenderer

ORIGIN = core.Origin("human", "cli")
FINDING = "북쪽 지역 매출이 늘었다 — the north grew"


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + b"\xff\x00\x00" * 2 + b"\x00" + b"\x00\x00\xff" * 2))
            + chunk(b"IEND", b""))


SCRIPT = """from pathlib import Path
import plotly.graph_objects as go
import aelix_analytics as a
a.visualize(go.Figure(go.Scatter(y=[1, 3, 2], name="sales")), "trend", title="Sales trend")
a.visualize(Path("figures/shape.svg"), "shape", title="Shape")
a.visualize(Path("figures/dots.png"), "dots", title="Dots")
a.visualize(go.Figure(go.Scattergeo(lat=[37.5], lon=[127.0])), "map", title="Map needs remote tiles")
"""


def project(root: Path):
    """A project whose copy has a plotly figure, an SVG, a PNG, a remote-resource map (not included), a
    finding citing the figure, a run with a code snapshot and a Report with a Korean path."""
    p = core.init_project(root, title="Reader fixture", origin=ORIGIN)
    (p.root / "figures").mkdir()
    (p.root / "figures/shape.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><circle cx="20" cy="20" r="15"/></svg>',
        encoding="utf-8")
    (p.root / "figures/dots.png").write_bytes(png())
    (p.scripts / "charts.py").write_text(SCRIPT, encoding="utf-8")
    run = runner.execute(p, script="scripts/charts.py", title="Charts", origin=ORIGIN)["run"]
    assert run["status"] == "ok", run
    core.record(p, "finding", FINDING, refs=[run["id"], "scripts/charts.py::trend"], origin=ORIGIN)
    source = root.parent / f"{root.name}-report"
    (source / "그림").mkdir(parents=True)
    (source / "주간 보고.md").write_text("# 주간\n\n[표](데이터.csv)\n", encoding="utf-8")
    (source / "데이터.csv").write_text("지역,매출\n북,3\n", encoding="utf-8")
    (source / "그림/점.png").write_bytes(png())
    (source / "page.html").write_text('<!doctype html><meta charset="utf-8"><p>HTML body</p><img src="그림/점.png" alt="dots">',
                                      encoding="utf-8")
    report = core.save_report(p, "weekly", files=source, entry="주간 보고.md", title="주간 보고서",
                              previous_revision=None, origin=ORIGIN)["report"]
    html = core.save_report(p, "page", files=source, entry="page.html", title="HTML report",
                            previous_revision=None, origin=ORIGIN)["report"]
    shutil.rmtree(source)
    return p, {"run": run["id"], "report": report["revision_id"], "html_report": html["revision_id"]}


def saved(p, *, profile="evidence", reports=(), scope="project", zip_path=None):
    request = ShareRequest(scope=scope, reports=tuple(reports))
    return build.build_publication(p, request, profile=profile, origin=ORIGIN, zip_path=zip_path,
                                   report_renderer=StaticReportRenderer())


def detached(p, result, destination: Path):
    """Copy a saved build to `destination` and remove its project, as a reader receives it."""
    folder = destination / result["publication_id"]
    shutil.copytree(p.root / result["path"], folder)
    shutil.rmtree(p.root)
    return folder
