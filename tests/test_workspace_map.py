"""#98: the Analysis map — a script and its runs as one box, an arrow where a run of one script read a
file a run of another wrote with the same path and bytes, the main flows open and the rest folded,
and the same content as words without a script (design WORKSPACE-READABILITY 7.2)."""
from __future__ import annotations

import json
import re

from legacy_records import LegacyProject, at, sha
from test_workspace_overview import status_project
from workspace_words import plain

from aelix_analytics import core, workspace, workspace_map
from aelix_analytics.workspace_map import Edge

ORIGIN = core.Origin("human", "cli", "declared")


def map_tab(p: core.Project) -> str:
    index = (p.workspace_dir / "index.html").read_text(encoding="utf-8")
    return plain(index.split('<section id="map"', 1)[1].split("</section>", 1)[0])


def nodes(html: str) -> dict[str, str]:
    """Each box of the map by its script's name: `<li class="map-node…">…</li>` (the boxes hold lists)."""
    out = {}
    for part in html.split('<li class="map-node')[1:]:
        name = re.search(r"<h4><a [^>]*>([^<]+)</a></h4>", part).group(1)
        out[name] = part
    return out


def test_a_flow_is_scripts_joined_by_the_files_their_runs_wrote_and_read(tmp_path):
    p, ids = status_project(tmp_path)
    site = workspace.Site(p)
    amap = site.map
    assert sorted(amap.nodes) == ["scripts/a.py", "scripts/b.py", "scripts/c.py"]
    assert [(e.source, e.target, e.files) for e in amap.edges] == [
        ("scripts/a.py", "scripts/b.py", ["data/derived/t.parquet"]),
        ("scripts/b.py", "scripts/c.py", ["data/derived/u.parquet"])]
    [flow] = amap.main
    assert flow.place == {"scripts/a.py": (0, 0), "scripts/b.py": (1, 0), "scripts/c.py": (2, 0)}
    assert amap.rest == []
    # c's only run failed and an insight cites it; b builds on a's first run, whose input changed.
    assert amap.nodes["scripts/c.py"].cited == {ids["correction"]}
    assert amap.nodes["scripts/b.py"].upstream and not amap.nodes["scripts/b.py"].changed
    # a's latest successful run (the rerun) reads its input as it is now, but its visualization comes
    # from its first run, whose input changed: the box says so, as the Overview's cell counts it
    # (independent review of #98, I10).
    assert amap.nodes["scripts/a.py"].changed and len(amap.nodes["scripts/a.py"].runs) == 2
    workspace.build(p)
    tab = map_tab(p)
    boxes = nodes(tab)
    assert set(boxes) == {"a.py", "b.py", "c.py"}
    assert "1 insight cites this run" in boxes["c.py"] and "Error" in boxes["c.py"]
    assert "Upstream changed" in boxes["b.py"] and "2 runs" in boxes["a.py"]
    # The words of an arrow: what a box writes and the box that reads it, without a script.
    assert re.search(r'<li class="map-out">.*?<span lang="">t\.parquet</span> → <a href="#script-[^"]+" lang="">b\.py</a>',
                     boxes["a.py"])
    attention = tab.split("Needs attention", 1)[1]
    assert "1 run did not succeed" in attention and "ValueError" not in attention
    assert "1 insight cites a run that did not succeed" in attention and f">{ids['correction']}</a>" in attention


def test_cycles_several_producers_and_exchanges_are_said_apart(tmp_path):
    q = LegacyProject(tmp_path / "p", migrated=True)
    x, y, z, w, v = (f"data/derived/{n}.parquet" for n in "xyzwv")
    # A run-level cycle: two runs started together, each reading the other's output.
    q.run("20261001-090000-cycle-a", script="scripts/cycle_a.py", started_at=at(0), ts=at(1),
          inputs=[(x, sha(b"x"))], outputs=[(y, q.file(y, b"y"))])
    q.run("20261001-090000-cycle-b", script="scripts/cycle_b.py", started_at=at(0), ts=at(2),
          inputs=[(y, sha(b"y"))], outputs=[(x, q.file(x, b"x"))])
    # Two runs that wrote the same bytes, and a run that read them.
    q.run("20261001-091000-same-1", script="scripts/same_1.py", started_at=at(10), ts=at(11), outputs=[(z, q.file(z, b"z"))])
    q.run("20261001-091100-same-2", script="scripts/same_2.py", started_at=at(11), ts=at(12), outputs=[(z, sha(b"z"))])
    q.run("20261001-091200-reader", script="scripts/reader.py", started_at=at(12), ts=at(13), inputs=[(z, sha(b"z"))])
    # Scripts that exchange files across runs (A1 → B1 → A2): not a cycle.
    q.run("20261001-092000-give", script="scripts/give.py", started_at=at(20), ts=at(21), outputs=[(w, q.file(w, b"w"))])
    q.run("20261001-092100-take", script="scripts/take.py", started_at=at(21), ts=at(22), inputs=[(w, sha(b"w"))],
          outputs=[(v, q.file(v, b"v"))])
    q.run("20261001-092200-give", script="scripts/give.py", started_at=at(22), ts=at(23), inputs=[(v, sha(b"v"))])
    p = core.Project(q.root)
    amap = workspace.Site(p).map
    assert {k for k, n in amap.nodes.items() if n.cycle} == {"scripts/cycle_a.py", "scripts/cycle_b.py"}
    assert amap.ambiguous == {z: ["20261001-091000-same-1", "20261001-091100-same-2"]}
    assert [e.ambiguous for e in amap.edges if e.target == "scripts/reader.py"] == [True, True]
    assert amap.exchanges == [["scripts/give.py", "scripts/take.py"]]
    assert not amap.nodes["scripts/give.py"].cycle
    # Scripts that reach each other are one stage: the same column, in name order.
    flow = next(f for f in amap.main + amap.rest if "scripts/give.py" in f.keys)
    assert flow.place == {"scripts/give.py": (0, 0), "scripts/take.py": (0, 1)}
    workspace.build(p)
    tab = map_tab(p)
    attention = tab.split("Needs attention", 1)[1]
    assert "1 file with several possible producers" in attention and "z.parquet" in attention
    assert "2 scripts on a cycle" in attention and "Scripts that exchange files" in attention
    assert 'class="maybe"' in tab and "(one of several possible producers)" in tab
    assert "Cycle" in nodes(tab)["cycle_a.py"] and "Cycle" not in nodes(tab)["give.py"]


def test_main_flows_are_the_largest_three_and_the_rest_fold(tmp_path):
    q = LegacyProject(tmp_path / "p", migrated=True)
    minute = 0
    for flow, runs in (("big", 4), ("mid", 3), ("small", 2), ("tiny", 1), ("lone", 1)):
        path = f"data/derived/{flow}.parquet"
        q.file(path, flow.encode())
        for n in range(runs):
            minute += 1
            source = f"scripts/{flow}_1.py" if n % 2 == 0 or runs == 1 else f"scripts/{flow}_2.py"
            if runs == 1:
                q.run(f"20261001-09{minute:02d}00-{flow}", script=source, started_at=at(minute), ts=at(minute))
            elif source.endswith("_1.py"):
                q.run(f"20261001-09{minute:02d}00-{flow}", script=source, started_at=at(minute), ts=at(minute),
                      outputs=[(path, sha(flow.encode()))])
            else:
                q.run(f"20261001-09{minute:02d}00-{flow}", script=source, started_at=at(minute), ts=at(minute),
                      inputs=[(path, sha(flow.encode()))])
    p = core.Project(q.root)
    amap = workspace.Site(p).map
    assert [f.keys for f in amap.main] == [["scripts/big_1.py", "scripts/big_2.py"], ["scripts/mid_1.py", "scripts/mid_2.py"],
                                           ["scripts/small_1.py", "scripts/small_2.py"]]
    assert [f.keys for f in amap.rest] == [["scripts/tiny_1.py"], ["scripts/lone_1.py"]]
    workspace.build(p)
    tab = map_tab(p)
    folded = tab.split('<details class="flow-more">', 1)[1]
    assert folded.startswith("<summary><strong>2 smaller flows</strong> · 2 scripts · 2 runs · ")
    # The main flows are diagrams; a smaller flow is a line per script with the same facts.
    assert tab.count('<div class="flow" role="group"') == 3
    rows = re.findall(r'<li class="flow-row">.*?</ul></li>', folded, re.S)
    assert len(rows) == 2 and ">tiny_1.py</a> · 1 run · " in rows[0] and ">lone_1.py</a> · 1 run · " in rows[1]


def test_layout_puts_scripts_by_the_longest_path_and_names(tmp_path):
    edges = [Edge("a", "c"), Edge("b", "c"), Edge("c", "d"), Edge("a", "d"), Edge("d", "e"), Edge("e", "d")]
    place, columns, rows = workspace_map.layout(["e", "d", "c", "b", "a"], edges)
    # d and e reach each other: one stage after c, by the longest path a → c → (d, e).
    assert place == {"a": (0, 0), "b": (0, 1), "c": (1, 0), "d": (2, 0), "e": (2, 1)}
    assert (columns, rows) == (3, 2)
    # The same graph gives the same places whatever order it comes in.
    assert workspace_map.layout(["a", "b", "c", "d", "e"], list(reversed(edges)))[0] == place


def test_the_map_reads_as_its_words_and_hides_only_decoration(tmp_path):
    p, _ = status_project(tmp_path)
    workspace.build(p)
    tab = map_tab(p)
    # The arrows and the file chips are what the eye reads; the words of each box say the same.
    assert '<svg class="flow-edges"' in tab and 'aria-hidden="true" focusable="false"' in tab
    assert all('aria-hidden="true"' in chip for chip in re.findall(r'<span class="map-chip[^>]*>', tab))
    assert '<ol class="flow-nodes">' in tab and "How to read it" in tab
    assert "A matching path and bytes makes a run a producer candidate, not a confirmed link." in tab
    # The Overview's summary of the map links to it.
    index = (p.workspace_dir / "index.html").read_text(encoding="utf-8")
    assert '<a class="head-link" href="#map">' in index
    summary = index.split('<ol class="map-boxes">', 1)[1].split("</ol>", 1)[0]
    assert re.findall(r'<li class="map-box kind-(\w+)">', summary) == ["data", "run", "viz", "insight"]


def test_a_box_counts_the_insights_of_its_latest_run_by_when_runs_started(tmp_path):
    """Codex review of #98: the latest run is the one that started last as an instant (offsets
    applied), and a box whose latest run failed counts the insights citing that run, not the
    script's other runs (R01, R02)."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    # 09:00 at +09:00 is 00:00Z; 01:00 at +00:00 is an hour later, though its text sorts first.
    early = q.run("20261001-090000-a", script="scripts/a.py", started_at="2026-10-01T09:00:00.000+09:00", ts=at(1))
    late = q.run("20261001-010000-a", script="scripts/a.py", status="error", started_at="2026-10-01T01:00:00.000+00:00",
                 ts=at(2))
    p = core.Project(q.root)
    core.record(p, "finding", "앞 실행이 근거", origin=ORIGIN, refs=[{"type": "run", "run_id": early, "relation": "cites"}])
    amap = workspace.Site(p).map
    node = amap.nodes["scripts/a.py"]
    assert node.latest["id"] == late and len(node.cited) == 1
    workspace.build(p)
    box = nodes(map_tab(p))["a.py"]
    assert "Latest did not succeed" in box and "1 insight · 1 confirmed" in box
    assert "cites this run" not in box


def test_runs_without_a_script_are_not_counted_as_scripts(tmp_path):
    """Codex review of #98 (R03): a run whose record names no script is a box of its own, counted
    as a run without a script."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    run = q.run("20261001-090000-loose", started_at=at(0), ts=at(1))
    record = json.loads((q.root / "runs" / run / "run.json").read_text(encoding="utf-8"))
    record["script"] = {}
    q.write_json(f"runs/{run}/run.json", record)
    p = core.Project(q.root)
    assert list(workspace.Site(p).map.nodes) == [f"run:{run}"]
    workspace.build(p)
    index = plain((p.workspace_dir / "index.html").read_text(encoding="utf-8"))
    summary = index.split('<ol class="map-boxes">', 1)[1].split("</ol>", 1)[0]
    assert "1 run without a script · 1 flow" in summary and "script" not in summary.split("1 run without a script")[0]
    assert "Flow 1 · 1 run without a script · 1 run" in map_tab(p)


def test_only_runs_on_a_cycle_are_marked_as_on_one(tmp_path):
    """Codex review of #98 (R04): a run whose upstream walk only meets a cycle further up is not on
    it; the read model's cycle is the members'."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    x, y, z = (f"data/derived/{n}.parquet" for n in "xyz")
    q.run("20261001-090000-a", script="scripts/a.py", started_at=at(0), ts=at(1), inputs=[(x, sha(b"x"))],
          outputs=[(y, q.file(y, b"y"))])
    q.run("20261001-090000-b", script="scripts/b.py", started_at=at(0), ts=at(2), inputs=[(y, sha(b"y"))],
          outputs=[(x, q.file(x, b"x"))])
    q.run("20261001-091000-c", script="scripts/c.py", started_at=at(10), ts=at(11), inputs=[(y, sha(b"y"))],
          outputs=[(z, q.file(z, b"z"))])
    p = core.Project(q.root)
    site = workspace.Site(p)
    assert site.model.upstream("20261001-091000-c")["cycle"]
    assert {k for k, n in site.map.nodes.items() if n.cycle} == {"scripts/a.py", "scripts/b.py"}
    workspace.build(p)
    tab = map_tab(p)
    assert "2 scripts on a cycle" in tab and "Cycle" not in nodes(tab)["c.py"]


def test_nothing_to_look_at_says_only_what_was_checked(tmp_path):
    """Codex review of #98 (R05): a raw input has no producer at all, and the panel does not say
    every file has one."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    raw = "data/raw/in.parquet"
    q.run("20261001-090000-a", script="scripts/a.py", started_at=at(0), ts=at(1), inputs=[(raw, q.file(raw, b"in"))])
    p = core.Project(q.root)
    workspace.build(p)
    attention = map_tab(p).split("Needs attention", 1)[1]
    assert ("Nothing needs attention: no run ended without succeeding, no script's latest successful run rests on "
            "changed inputs, no file has several possible producers and no runs read each other's output.") in attention


def test_arrows_never_pass_behind_a_box():
    """Independent review of #98 (I6): an arrow that skips a column runs through the gap under its
    source's row, and two opposite arcs of one stage part, their chips apart."""
    gap = workspace_map.NODE_H + workspace_map.GAP_Y / 2
    d, cx, cy, low = workspace_map.edge_geometry((0, 0), (2, 0))
    assert f" {gap:g}L" in d and low > workspace_map.NODE_H
    assert cx < workspace_map.NODE_W + workspace_map.GAP_X  # its chip is in the first gap
    near = workspace_map.edge_geometry((0, 0), (0, 1))
    far = workspace_map.edge_geometry((0, 1), (0, 0), outer=True)
    assert near[0] != far[0] and abs(near[2] - far[2]) >= 1.5


def test_a_rerun_that_wrote_the_same_bytes_leaves_the_arrow_certain(tmp_path):
    """Independent review of #98 (I9): between scripts, an arrow is uncertain only when runs of more
    than one script wrote the bytes read."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    t_ = "data/derived/t.parquet"
    q.run("20261001-090000-a", script="scripts/a.py", started_at=at(0), ts=at(1), outputs=[(t_, q.file(t_, b"t"))])
    q.run("20261001-091000-a", script="scripts/a.py", started_at=at(10), ts=at(11), outputs=[(t_, sha(b"t"))])
    q.run("20261001-092000-b", script="scripts/b.py", started_at=at(20), ts=at(21), inputs=[(t_, sha(b"t"))])
    p = core.Project(q.root)
    amap = workspace.Site(p).map
    assert [(e.source, e.target, e.ambiguous) for e in amap.edges] == [("scripts/a.py", "scripts/b.py", False)]
    # The read model still has two producer candidates: the panel says so, as the data pages do.
    assert amap.ambiguous == {t_: ["20261001-090000-a", "20261001-091000-a"]}
