"""#100: provenance trails — what a record, a run, a Visualization version or a data file was made
from, and what was made from it, followed run by run from the judgments the reader already makes
(design WORKSPACE-READABILITY 6), the same in the bounded read (the owner's decision 3, design 12)
and on the relation pages."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest
from conftest import PACK
from legacy_records import LegacyProject, at, sha

from aelix_analytics import core, relations, workspace, workspace_relations

ORIGIN = core.Origin("human", "cli", "declared")
SENSOR, SALES = "data/raw/sensor.parquet", "data/raw/sales.parquet"
A, B = "data/derived/a.parquet", "data/derived/b.parquet"


def run_id(minute: int, name: str) -> str:
    return f"20261001-09{minute:02d}00-{name}"


def set_run(q: LegacyProject, rid: str, **fields) -> None:
    path = q.root / "runs" / rid / "run.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(fields)
    q.write_json(f"runs/{rid}/run.json", record)


def chain_project(tmp_path) -> tuple[core.Project, dict]:
    """S's e151 in small: a finding cites run C, which read b.parquet that run B wrote from a.parquet
    (written by run A from the imported sensor data) and the imported sales data. Later runs A2 and B2
    wrote a and b again, so the bytes C and B read are changed since; a run E read a's first bytes
    before A started, so it is no use of A's output."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    q.dataset("sensor", imported_at=at(0))
    q.dataset("sales", imported_at=at(0))
    sensor, sales = sha(b"sensor"), sha(b"sales")
    ids = {}
    ids["early"] = q.run(run_id(1, "e"), script="scripts/e.py", started_at=at(1), ts=at(2), inputs=[(A, sha(b"a-1"))])
    ids["a"] = q.run(run_id(3, "a"), script="scripts/a.py", started_at=at(3), ts=at(4), inputs=[(SENSOR, sensor)],
                     outputs=[(A, sha(b"a-1"))], charts=["ca"])
    ids["b"] = q.run(run_id(5, "b"), script="scripts/b.py", started_at=at(5), ts=at(6),
                     inputs=[(A, sha(b"a-1")), (SALES, sales)], outputs=[(B, sha(b"b-1"))], charts=["cb"])
    ids["c"] = q.run(run_id(7, "c"), script="scripts/c.py", started_at=at(7), ts=at(8), inputs=[(B, sha(b"b-1"))],
                     charts=["cc"])
    ids["a2"] = q.run(run_id(9, "a"), script="scripts/a.py", started_at=at(9), ts=at(10), inputs=[(SENSOR, sensor)],
                      outputs=[(A, q.file(A, b"a-2"))], charts=["ca"])
    ids["b2"] = q.run(run_id(11, "b"), script="scripts/b.py", started_at=at(11), ts=at(12),
                      inputs=[(A, sha(b"a-2")), (SALES, sales)], outputs=[(B, q.file(B, b"b-2"))], charts=["cb"])
    set_run(q, ids["b"], inputs=[{"path": A, "bytes": 1, "sha256": sha(b"a-1"), "via": ["analytics.read", "scan"],
                                  "external": False},
                                 {"path": SALES, "bytes": 1, "sha256": sales, "via": ["declared"], "external": False}])
    p = core.Project(q.root)
    ids["finding"] = core.record(p, "finding", "결론 — c가 보여 줌", origin=ORIGIN,
                                 refs=[{"type": "run", "run_id": ids["c"], "relation": "cites"}]).event["id"]
    ids["note"] = core.record(p, "note", "a의 첫 출력을 봄", origin=ORIGIN,
                              refs=[{"type": "run", "run_id": ids["a"], "relation": "cites"}]).event["id"]
    return p, ids


def trail(p: core.Project, start: str, direction: str = "sources", **options) -> dict:
    model = core.read_model(p)
    fresh = core.Freshness(p, [], None, model)
    return model.provenance(start, direction, input_state=fresh.input_state, **options)


def nodes_of(found: dict) -> dict[str, dict]:
    return {node["id"]: node for node in found["nodes"]}


def data(path: str, content: bytes) -> str:
    return f"data:{path}@{sha(content)}"


# --- the read model ------------------------------------------------------------------------------

def test_a_record_s_trail_goes_through_its_runs_to_the_imported_data(tmp_path):
    p, ids = chain_project(tmp_path)
    found = trail(p, ids["finding"])
    nodes = nodes_of(found)
    run = {k: "run:" + ids[k] for k in ("a", "b", "c")}
    # Three runs, nearest first: the cited run and what it read and made, then each producer candidate.
    assert [(n["id"], n["step"]) for n in found["nodes"]] == [
        (ids["finding"], 0), (run["c"], 1), (data(B, b"b-1"), 1), (f"{ids['c']}/cc", 1),
        (run["b"], 2), (data(A, b"a-1"), 2), (data(SALES, b"sales"), 2), (f"{ids['b']}/cb", 2),
        (run["a"], 3), (data(SENSOR, b"sensor"), 3), (f"{ids['a']}/ca", 3)]
    assert found["omitted"] == {"nodes": 0, "edges": 0} and found["cycles"] == []
    # Imported data is unchanged; the derived files read are changed since, with the runs whose output the
    # bytes now are (a same-bytes observation, not a confirmed link).
    assert [(nodes[k]["imported"], nodes[k]["state"]) for k in (data(SENSOR, b"sensor"), data(SALES, b"sales"))] == [
        (True, "same"), (True, "same")]
    assert (nodes[data(B, b"b-1")]["state"], nodes[data(B, b"b-1")]["current_writers"]) == ("changed", [ids["b2"]])
    assert (nodes[data(A, b"a-1")]["state"], nodes[data(A, b"a-1")]["current_writers"]) == ("changed", [ids["a2"]])
    # The version numbers of #99: the bytes read are the first of two, the charts the first of two.
    assert (nodes[data(A, b"a-1")]["version"], nodes[data(A, b"a-1")]["versions"]) == (1, 2)
    assert (nodes[f"{ids['c']}/cc"]["version"], nodes[f"{ids['c']}/cc"]["versions"]) == (1, 1)
    edges = {(e["from"], e["kind"], e["to"]): e for e in found["edges"]}
    cites = edges[(ids["finding"], "cites", run["c"])]
    assert (cites["state"], cites["version_binding"], cites["confirmed"]) == ("resolved", "author", True)
    wrote = edges[(run["b"], "wrote", data(B, b"b-1"))]
    assert (wrote["producer"], wrote["basis"]) == ("resolved", "observed")
    # How a run recorded each input (design 6.1): read while it ran first, then the scan; declared.
    assert edges[(run["b"], "read", data(A, b"a-1"))]["how"] == ["read", "scan"]
    assert edges[(run["b"], "read", data(SALES, b"sales"))]["how"] == ["declared"]
    checks = found["checks"]
    assert checks["citations"] == {"total": 1, "states": {"resolved": 1}, "confirmed": 1}
    assert checks["runs"] == {"total": 3, "succeeded": 3, "not_succeeded": []}
    assert checks["code"] == {"resolved": {"count": 3, "runs": sorted(ids[k] for k in ("a", "b", "c"))}}
    assert checks["imported"]["states"] == {"same": 2} and checks["derived"]["states"] == {"changed": 2}
    assert checks["reruns"] == {"total": 0, "verdicts": {}, "runs": []}
    assert checks["analysis_checks"] is None and checks["conclusion_review"] is None
    # The same records give the same trail.
    assert trail(p, ids["finding"]) == found


def test_depth_counts_runs_and_marks_where_the_trail_stops(tmp_path):
    p, ids = chain_project(tmp_path)
    one = trail(p, ids["finding"], depth=1)
    assert [n["id"] for n in one["nodes"]] == [ids["finding"], "run:" + ids["c"], data(B, b"b-1"), f"{ids['c']}/cc"]
    assert [n["id"] for n in one["nodes"] if n.get("beyond_depth")] == [data(B, b"b-1")]
    assert one["omitted"] == {"nodes": 0, "edges": 0}  # what depth leaves out is not counted
    two = trail(p, "run:" + ids["c"], depth=2)
    assert [n["step"] for n in two["nodes"] if n["kind"] == "run"] == [1, 2]


def test_what_uses_bytes_is_the_runs_whose_producer_candidate_wrote_them(tmp_path):
    p, ids = chain_project(tmp_path)
    found = trail(p, "run:" + ids["a"], "uses")
    nodes = nodes_of(found)
    # A wrote a-1, read by B and not by E, which read those bytes before A started (no producer candidate).
    assert "run:" + ids["b"] in nodes and "run:" + ids["early"] not in nodes
    assert "run:" + ids["c"] in nodes and nodes["run:" + ids["c"]]["step"] == 3
    # The note citing A uses it; the kit's own completion record of A does not.
    assert ids["note"] in nodes
    assert not any(n["kind"] == "record" and n.get("record_kind") == "run" for n in found["nodes"])
    wrote = next(e for e in found["edges"] if e["kind"] == "wrote" and e["from"] == "run:" + ids["a"])
    assert wrote["recorded"] is True
    # From the data file: its bytes now, read by the runs that recorded them.
    now = trail(p, SENSOR, "uses")
    assert now["nodes"][0]["id"] == data(SENSOR, b"sensor") and now["nodes"][0]["state"] == "same"
    assert {n["id"] for n in now["nodes"] if n["kind"] == "run" and n["step"] == 1} == {
        "run:" + ids["a"], "run:" + ids["a2"]}


def test_several_possible_sources_are_all_shown_as_branches(tmp_path):
    q = LegacyProject(tmp_path / "p", migrated=True)
    x = ("data/derived/x.parquet", q.file("data/derived/x.parquet", b"x"))
    one = q.run(run_id(1, "one"), script="scripts/one.py", started_at=at(1), ts=at(2), outputs=[x])
    two = q.run(run_id(3, "two"), script="scripts/two.py", started_at=at(3), ts=at(4), outputs=[x])
    use = q.run(run_id(5, "use"), script="scripts/use.py", started_at=at(5), ts=at(6), inputs=[x])
    found = trail(core.Project(q.root), "run:" + use)
    node = nodes_of(found)[f"data:{x[0]}@{x[1]}"]
    assert node["producer"] == "ambiguous"
    wrote = [e for e in found["edges"] if e["kind"] == "wrote"]
    assert sorted(e["from"] for e in wrote) == ["run:" + one, "run:" + two]
    assert {e["producer"] for e in wrote} == {"ambiguous"}


def test_a_cycle_is_said_where_the_walk_comes_back_and_it_stops_there(tmp_path):
    q = LegacyProject(tmp_path / "p", migrated=True)
    x = ("data/derived/x.parquet", q.file("data/derived/x.parquet", b"x"))
    y = ("data/derived/y.parquet", q.file("data/derived/y.parquet", b"y"))
    first = q.run("20261001-090000-cycle-a", script="scripts/a.py", started_at=at(0), ts=at(1), inputs=[y], outputs=[x])
    second = q.run("20261001-090000-cycle-b", script="scripts/b.py", started_at=at(0), ts=at(1), inputs=[x], outputs=[y])
    found = trail(core.Project(q.root), "run:" + first)
    assert [n["id"] for n in found["nodes"]] == ["run:" + first, f"data:{y[0]}@{y[1]}", "run:" + second,
                                                 f"data:{x[0]}@{x[1]}"]
    assert found["cycles"] == [{"from": "run:" + first, "to": f"data:{x[0]}@{x[1]}"}]
    assert [e for e in found["edges"] if e.get("cycle")] == [
        {"from": "run:" + first, "to": f"data:{x[0]}@{x[1]}", "kind": "wrote", "producer": "resolved",
         "basis": "observed", "cycle": True}]


def wide_project(tmp_path, inputs: int) -> tuple[core.Project, str, list[str]]:
    """A run reading `inputs` derived files, each written by a run of its own from one imported file."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    q.dataset("base", imported_at=at(0))
    writers, read = [], []
    for n in range(inputs):
        path = f"data/derived/t{n:02d}.parquet"
        digest = q.file(path, f"t{n}".encode())
        writers.append(q.run(f"20261001-08{n:02d}00-w{n:02d}", script=f"scripts/w{n:02d}.py", started_at=at(n, 8),
                             ts=at(n, 8), inputs=[("data/raw/base.parquet", sha(b"base"))], outputs=[(path, digest)]))
        read.append((path, digest))
    wide = q.run("20261001-100000-wide", script="scripts/wide.py", started_at=at(0, 10), ts=at(1, 10), inputs=read)
    return core.Project(q.root), wide, writers


def test_the_budget_keeps_the_nearest_steps_and_counts_what_it_leaves_out(tmp_path):
    p, wide, writers = wide_project(tmp_path, 12)
    found = trail(p, "run:" + wide)
    assert len(found["nodes"]) == relations.TRAIL_BUDGET[0] and len(found["edges"]) <= relations.TRAIL_BUDGET[1]
    # The run, its 12 inputs, then the first 7 of their writers; each writer's input is on the next step.
    assert [n["step"] for n in found["nodes"]] == [1] * 13 + [2] * 7
    assert found["total"] == {"nodes": 26, "edges": 36}
    assert found["omitted"] == {"nodes": 6, "edges": 17}
    shown = {n["id"] for n in found["nodes"]}
    # Every node shown is joined to the start by edges shown (the tree is kept before other edges).
    assert all(e["from"] in shown and e["to"] in shown for e in found["edges"])
    continues = [n["id"] for n in found["nodes"] if n.get("continues")]
    assert "run:" + writers[0] in continues and "run:" + wide not in continues
    # The next read of the same records is the same answer.
    assert trail(p, "run:" + wide) == found


def test_a_run_reading_many_files_shows_them_as_one_node(tmp_path):
    p, wide, _ = wide_project(tmp_path, relations.TRAIL_FOLD + 1)
    found = trail(p, "run:" + wide)
    assert [n["kind"] for n in found["nodes"]] == ["run", "inputs"]
    assert found["nodes"][1]["count"] == relations.TRAIL_FOLD + 1 and found["nodes"][1]["states"] == {
        "same": relations.TRAIL_FOLD + 1}


def test_reruns_failed_and_unended_runs_are_said_as_recorded(tmp_path):
    p, ids = chain_project(tmp_path)
    q = LegacyProject.__new__(LegacyProject)
    q.root, q.seq, q.minute = p.root, 100, 30
    rerun = q.run(run_id(20, "c"), script="scripts/c.py", started_at=at(20), ts=at(21), inputs=[(B, sha(b"b-1"))])
    set_run(q, rerun, rerun_of=ids["c"], reproduction={"of": ids["c"], "verdict": "match", "inputs_same": True,
                                                       "charts": {}, "outputs": {}})
    failed = q.run(run_id(22, "f"), script="scripts/f.py", status="error", started_at=at(22), ts=at(23),
                   inputs=[(SALES, sha(b"sales"))])
    running = q.run(run_id(24, "r"), script="scripts/r.py", status="running", started_at=at(24), pid=999999999,
                    completion=False)
    cites = core.record(p, "finding", "세 run", origin=ORIGIN, refs=[
        {"type": "run", "run_id": rid, "relation": "cites"} for rid in (ids["c"], failed, running)]).event["id"]
    found = trail(p, cites)
    nodes = nodes_of(found)
    assert (nodes["run:" + failed]["status"], nodes["run:" + running]["status"]) == ("error", "incomplete")
    checks = found["checks"]
    assert checks["runs"]["not_succeeded"] == [{"run": failed, "status": "error"}, {"run": running, "status": "incomplete"}]
    assert checks["reruns"] == {"total": 1, "verdicts": {"match": 1}, "runs": [{"run": rerun, "of": ids["c"], "verdict": "match"}]}
    assert set(checks["code"]["resolved"]["runs"]) >= {ids["c"], failed}


def test_a_state_change_is_history_and_a_name_without_a_version_is_not_followed(tmp_path):
    p, ids = chain_project(tmp_path)
    corrected = core.record(p, "finding", "정정", origin=ORIGIN, refs=[
        {"type": "event", "event_id": ids["finding"], "relation": "supersedes"},
        {"type": "visualization_version", "key": "scripts/c.py::cc", "relation": "cites", "version_binding": "none"}]).event["id"]
    found = trail(p, corrected)
    assert [n["kind"] for n in found["nodes"]] == ["record", "name"]
    assert found["nodes"][1]["state"] == "version_unbound" and found["nodes"][1]["candidates"] == 1
    assert found["checks"]["citations"] == {"total": 1, "states": {"version_unbound": 1}, "confirmed": 0}


def test_an_older_kit_s_records_and_paths_are_read_as_the_reader_reads_them(tmp_path, monkeypatch):
    """A lab.event/1 record's refs.runs, and a run of an older kit whose input path is read as today's
    path of another run's output: the producer candidate is inferred, never observed."""
    q = LegacyProject(tmp_path / "p")
    agg = ("data/derived/agg.parquet", q.file("data/derived/agg.parquet", b"agg"))
    maker = q.run(run_id(1, "maker"), script="scripts/m.py", started_at=at(1), ts=at(2),
                  outputs=[("data/derived/AGG.parquet", agg[1])])
    absolute = os.path.join(os.path.realpath(q.root), "data", "derived", "agg.parquet")
    consumer = q.run(run_id(3, "consumer"), script="scripts/u.py", started_at=at(3), ts=at(4), inputs=[(absolute, agg[1])])
    finding = q.event("finding", {"runs": [consumer]}, ts=at(5), text="옛 기록")
    monkeypatch.setattr(core.Project, "legacy_identity",
                        lambda self, recorded: {absolute: agg[0], "data/derived/AGG.parquet": agg[0]}.get(recorded))
    found = trail(core.Project(q.root), finding)
    nodes = nodes_of(found)
    node = nodes[f"data:{agg[0]}@{agg[1]}"]
    assert (node["read_as"], node["recorded_path"]) == (agg[0], absolute)
    read = next(e for e in found["edges"] if e["kind"] == "read")
    assert read["read_as"] == agg[0]
    wrote = next(e for e in found["edges"] if e["kind"] == "wrote")
    assert (wrote["from"], wrote["basis"]) == ("run:" + maker, "inferred")
    cites = next(e for e in found["edges"] if e["kind"] == "cites")
    assert (cites["basis"], cites["version_binding"]) == ("declared", "author")


# --- the bounded read (design 12, decision 3) ------------------------------------------------------

def test_the_bounded_read_answers_a_trail_with_the_workspace_s_budget(tmp_path, monkeypatch):
    from conftest import kit_cli

    import aelix_analytics as analytics

    p, ids = chain_project(tmp_path)
    core_read = core.status(p, part="relations", id=ids["finding"], direction="sources")
    found = trail(p, ids["finding"])
    assert core_read["items"] == found["nodes"] and core_read["edges"] == found["edges"]
    assert (core_read["returned"], core_read["total"], core_read["has_more"], core_read["cursor"]) == (11, 11, False, None)
    assert core_read["trail"]["checks"] == found["checks"] and core_read["trail"]["budget"] == {"nodes": 20, "edges": 30}
    assert core_read["source_revision"] and core_read["as_of_seq"] == core.read_model(p).as_of_seq
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(p.root))
    sdk = analytics.status(part="relations", id=ids["finding"], direction="sources")
    code, cli = kit_cli(p.root, "status", "--part", "relations", "--id", ids["finding"], "--direction", "sources")
    assert code == 0 and cli["ok"]
    for other in (sdk, cli["status"]):
        assert other["items"] == core_read["items"] and other["edges"] == core_read["edges"]
        assert other["trail"] == core_read["trail"] and other["source_revision"] == core_read["source_revision"]
    # Where depth stops it, the trail goes on from what is beyond: from a data file's bytes, from the runs
    # its producer judgment named, with the same direction and depth.
    one = core.status(p, part="relations", id=ids["finding"], direction="sources", depth=1)
    assert one["has_more"] and one["trail"]["continue_from"] == [
        {"node": data(B, b"b-1"), "read": {"part": "relations", "id": "run:" + ids["b"], "direction": "sources", "depth": 1}}]
    # A data path starts at its bytes now; the bytes a trail met start where the trail names them.
    assert core.status(p, part="relations", id=B, direction="sources")["items"][0]["id"] == data(B, b"b-2")
    met = core.status(p, part="relations", id=data(B, b"b-1"), direction="sources")
    assert [n["id"] for n in met["items"]][:2] == [data(B, b"b-1"), "run:" + ids["b"]] and met["items"][0]["state"] == "changed"


def test_over_budget_the_read_names_the_nodes_to_go_on_from(tmp_path):
    p, wide, writers = wide_project(tmp_path, 12)
    read = core.status(p, part="relations", id="run:" + wide, direction="sources")
    assert read["has_more"] and read["omitted"] == {"count": 6, "edges": 17, "reason": "trail_budget"}
    assert {"node": "run:" + writers[0], "read": {"part": "relations", "id": "run:" + writers[0],
                                                   "direction": "sources"}} in read["trail"]["continue_from"]


@pytest.mark.parametrize("options, message", [
    ({"direction": "both", "depth": 2}, "depth is a whole number"),
    ({"direction": "sources", "depth": 0}, "depth is a whole number"),
    ({"direction": "sources", "depth": True}, "depth is a whole number"),
    ({"direction": "sources", "cursor": "x"}, "has no cursor"),
    ({"direction": "upward"}, "unknown retrieval part or direction"),
])
def test_trail_arguments_are_checked(tmp_path, options, message):
    p, ids = chain_project(tmp_path)
    with pytest.raises(core.AnalyticsError) as raised:
        core.status(p, part="relations", id=ids["finding"], **options)
    assert raised.value.code == "bad_query" and message in raised.value.message
    with pytest.raises(core.AnalyticsError) as raised:
        core.status(p, part="records", direction="sources")
    assert raised.value.code == "bad_query"
    with pytest.raises(core.AnalyticsError) as raised:
        core.status(p, part="relations", id="e999", direction="uses")
    assert raised.value.code == "record_not_found"


def test_one_hop_reads_are_as_they_were(tmp_path):
    p, ids = chain_project(tmp_path)
    one = core.status(p, part="relations", id=ids["finding"], limit=1)
    assert "trail" not in one and "edges" not in one
    assert [row["direction"] for row in one["items"]] == ["outgoing"] and one["read_more"] is None
    # A page of one hop names no depth in its next read.
    many = core.status(p, part="relations", id="run:" + ids["a"], limit=1)
    assert many["has_more"] and "depth" not in many["read_more"]


def test_a_historical_trail_knows_only_the_journal_up_to_its_seq(tmp_path):
    p, ids = chain_project(tmp_path)
    before = core.read_model(p).as_of_seq
    later = core.record(p, "finding", "나중", origin=ORIGIN,
                        refs=[{"type": "run", "run_id": ids["c"], "relation": "cites"}]).event["id"]
    with pytest.raises(core.AnalyticsError):
        core.status(p, part="relations", id=later, direction="sources", as_of_seq=before)
    now = core.status(p, part="relations", id=ids["finding"], direction="sources", as_of_seq=before)
    assert now["as_of_seq"] == before and now["returned"] == 11


# --- the relation page --------------------------------------------------------------------------

def relation_page(p: core.Project, identity: str, page: int = 0) -> str:
    return (p.workspace_dir / "relations" / workspace_relations.name(identity, page)).read_text(encoding="utf-8")


def section(html: str, start: str, end: str) -> str:
    return html.split(start, 1)[1].split(end, 1)[0]


def test_the_relation_page_shows_the_trail_as_its_words_and_its_boxes(tmp_path):
    p, ids = chain_project(tmp_path)
    workspace.build(p)
    html = relation_page(p, ids["finding"])
    # Provenance first, then the one-hop relations of #18, then History (design 6.4).
    assert (html.index('id="record"') < html.index('<section id="provenance" class="tab">')
            < html.index('<section id="related" class="tab"') < html.index('<section id="history" class="tab">')
            < html.index('<section id="technical">'))
    tab = section(html, '<section id="provenance" class="tab">', '<section id="related"')
    boxes = re.findall(r'<li class="pv ([^"]+)" id="pv-s-(\d+)" style="grid-area:(\d+)/(\d+)">(.*?)</li>', tab)
    assert len(boxes) == 11
    kinds = {int(n): (classes, body) for classes, n, _, _, body in boxes}
    # Each box names its kind in words, its state as a mark and words, and how it joins the next box.
    assert "<k-w>Finding</k-w> · <k-w>This record</k-w>" in kinds[0][1] and "is-start" in kinds[0][0]
    assert kinds[1][0] == "pv-run" and "<k-w>Run</k-w>" in kinds[1][1] and "<k-w>Cited by</k-w>" in kinds[1][1]
    assert "pv-data st-changed" in kinds[2][0] and "<k-w>Derived data</k-w>" in kinds[2][1]
    assert 'data-m="⚠"><k-w>Changed since read</k-w>' in kinds[2][1] and "<k-w>Now the bytes of</k-w>" in kinds[2][1]
    assert "<k-w>Imported data</k-w>" in kinds[6][1] and 'data-m="✓"><k-w>Unchanged</k-w>' in kinds[6][1]
    assert kinds[3][0] == "pv-viz" and "<k-w>Made by</k-w>" in kinds[3][1]
    assert all('<p class="pj">' in body for n, (_, body) in kinds.items() if n)
    # The start is at the bottom of what it comes from, the first run in line above it.
    place = {int(n): (int(row), int(col)) for _, n, row, col, _ in boxes}
    assert place[0][0] == max(r for r, _ in place.values()) and place[1] == (place[0][0] - 1, place[0][1])
    edges = re.search(r'data-e="([^"]*)"', tab).group(1).split()
    assert "1>0c" in edges and len(edges) == 10
    # What can be checked, each on its own line, and what the trail is not.
    checks = section(tab, '<aside class="pv-checks">', "</aside>")
    assert checks.count("<li class=\"ck-") == 7 and "row-level lineage" in checks
    assert "1 of 1 citations are exact links" in checks and "0 of 2 derived files unchanged" in checks
    # A record with no citation has no Provenance tab; its relations come first.
    assert '<section id="provenance"' in relation_page(p, ids["note"])  # the note cites run A
    uncited = core.record(p, "note", "인용 없음", origin=ORIGIN).event["id"]
    workspace.build(p)
    assert '<section id="provenance"' not in relation_page(p, uncited)
    # A run's page shows both directions, one switch apart.
    run_page = relation_page(p, "run:" + ids["a"])
    assert '<a href="#trail-sources" data-dir="sources">' in run_page and 'id="trail-uses"' in run_page


def test_the_trail_pages_switch_language_with_the_rest(tmp_path):
    p, ids = chain_project(tmp_path)
    workspace.build(p)
    html = relation_page(p, ids["finding"])
    words = set(re.findall(r'<k-w(?: k="([a-z_.]+)")?[^>]*>([^<]*)</k-w>', section(html, '<section id="provenance"', '<section id="related"')))
    keys = {key for key, _ in words if key}
    assert {"provenance.summary", "provenance.check.cited", "provenance.check.runs"} <= keys


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_controller_shows_one_direction_and_puts_the_tabs_under_the_record(tmp_path):
    """Nonbrowser controller test (mocked DOM): the relation page's section bar goes before its first
    section, a trail's direction link shows that trail and hides the other, and a page with no wide
    graph draws no lines."""
    script = PACK / "tests" / "workspace_provenance.cjs"
    result = subprocess.run(["node", str(script), str(PACK / "src" / "aelix_analytics" / "assets")],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout


# --- what the reviews of #121 found (Codex R1–R10, independent I1–I11, N1–N8) -------------------------

def test_reruns_are_counted_once_with_what_each_re_executed(tmp_path):
    """R3·I1: a rerun of a rerun names the rerun it re-executed, and each rerun counts once even when the
    original and its reruns are all on the trail (a matching rerun is a producer candidate too)."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    x = ("data/derived/x.parquet", q.file("data/derived/x.parquet", b"x"))
    o = q.run(run_id(1, "o"), script="scripts/o.py", started_at=at(1), ts=at(2), outputs=[x])
    r1 = q.run(run_id(3, "o"), script="scripts/o.py", started_at=at(3), ts=at(4), outputs=[x])
    r2 = q.run(run_id(5, "o"), script="scripts/o.py", started_at=at(5), ts=at(6), outputs=[x])
    for rerun, of in ((r1, o), (r2, r1)):
        set_run(q, rerun, rerun_of=of, reproduction={"of": of, "verdict": "match", "inputs_same": True, "charts": {}, "outputs": {}})
    unchecked = q.run(run_id(7, "o"), script="scripts/o.py", started_at=at(7), ts=at(8))
    set_run(q, unchecked, rerun_of=o)
    use = q.run(run_id(9, "use"), script="scripts/use.py", started_at=at(9), ts=at(10), inputs=[x])
    p = core.Project(q.root)
    cites = core.record(p, "finding", "x", origin=ORIGIN, refs=[{"type": "run", "run_id": use, "relation": "cites"}]).event["id"]
    reruns = trail(p, cites)["checks"]["reruns"]
    assert reruns["total"] == 3 and reruns["verdicts"] == {"match": 2, "none": 1}
    assert sorted((r["run"], r["of"]) for r in reruns["runs"]) == sorted([(r1, o), (r2, r1), (unchecked, o)])
    workspace.build(p)
    assert "<k-w>No comparison recorded</k-w> 1" in relation_page(p, cites)


def cycle_project(tmp_path):
    q = LegacyProject(tmp_path / "p", migrated=True)
    x = ("data/derived/x.parquet", q.file("data/derived/x.parquet", b"x"))
    y = ("data/derived/y.parquet", q.file("data/derived/y.parquet", b"y"))
    a = q.run("20261001-090000-cycle-a", script="scripts/a.py", started_at=at(0), ts=at(1), inputs=[y], outputs=[x])
    b = q.run("20261001-090000-cycle-b", script="scripts/b.py", started_at=at(0), ts=at(1), inputs=[x], outputs=[y])
    return core.Project(q.root), a, b


def test_a_cycle_between_two_branches_is_found(tmp_path):
    """R4·I7: a record citing both runs of a cycle reaches each first from the record; the cycle between
    them is still found, as upstream() finds it."""
    p, a, b = cycle_project(tmp_path)
    both = core.record(p, "finding", "both", origin=ORIGIN,
                       refs=[{"type": "run", "run_id": r, "relation": "cites"} for r in (a, b)]).event["id"]
    found = trail(p, both)
    assert core.read_model(p).upstream(a)["cycle"] and found["cycles"]
    assert len({n["id"] for n in found["nodes"]}) == len(found["nodes"])


def test_what_uses_bytes_follows_only_references_the_reader_found(tmp_path):
    """I4·R5: a reference judged missing (a record not there when cited, a digest that differs) is no use:
    the trail of uses stops where the trail of sources stops, and no record cycle appears."""
    p, ids = chain_project(tmp_path)
    q = LegacyProject.__new__(LegacyProject)
    q.root, q.seq, q.minute = p.root, 0, 40
    later = core.read_model(p).journal.next_seq + 1
    forward = core.record(p, "note", "앞으로의 기록을 가리킴", origin=ORIGIN,
                          refs=[{"type": "event", "event_id": f"e{later}", "relation": "cites"}]).event["id"]
    target = core.record(p, "note", "나중 기록", origin=ORIGIN,
                         refs=[{"type": "event", "event_id": forward, "relation": "cites"}]).event["id"]
    assert target == f"e{later}"
    uses = trail(p, target, "uses")
    assert forward not in nodes_of(uses) and uses["cycles"] == []
    wrong = core.record(p, "finding", "다른 바이트", origin=ORIGIN, refs=[
        {"type": "data_observation", "run_id": ids["b"], "role": "output", "path": B, "relation": "cites",
         "digest": {"sha256": sha(b"other")}}]).event["id"]
    exact = core.record(p, "finding", "같은 바이트", origin=ORIGIN, refs=[
        {"type": "data_observation", "run_id": ids["b"], "role": "output", "path": B, "relation": "cites",
         "digest": {"sha256": sha(b"b-1")}}]).event["id"]
    found = nodes_of(trail(p, "run:" + ids["b"], "uses"))
    assert exact in found and wrong not in found


def test_uses_find_citations_and_readers_through_an_older_kit_s_path(tmp_path, monkeypatch):
    """R6: a record citing an older run's input in its own spelling uses those bytes, and the reader's edge
    says the path was read as today's (inferred)."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    agg = ("data/derived/agg.parquet", q.file("data/derived/agg.parquet", b"agg"))
    maker = q.run(run_id(1, "maker"), script="scripts/m.py", started_at=at(1), ts=at(2), outputs=[agg], kit_version="0.5.0")
    # The real path an older kit recorded: there on every platform, so the reader finds the cited input (a
    # spelling only a case-insensitive file system finds would be judged missing elsewhere, and is no use).
    old = os.path.join(os.path.realpath(q.root), "data", "derived", "agg.parquet")
    reader = q.run(run_id(3, "reader"), script="scripts/r.py", started_at=at(3), ts=at(4), inputs=[(old, agg[1])])
    monkeypatch.setattr(core.Project, "legacy_identity", lambda self, recorded: {old: agg[0]}.get(recorded))
    p = core.Project(q.root)
    cites = core.record(p, "finding", "옛 철자로 인용", origin=ORIGIN, refs=[
        {"type": "data_observation", "run_id": reader, "role": "input", "path": old, "relation": "cites"}]).event["id"]
    found = trail(p, "run:" + maker, "uses")
    assert cites in nodes_of(found)
    read = next(e for e in found["edges"] if e["kind"] == "read")
    assert (read["from"], read["read_as"]) == ("run:" + reader, agg[0])


def test_a_directory_or_a_uri_start_is_unknown_not_missing(tmp_path):
    """R7·N1: bytes the reader cannot hash — a directory, a URI — are of unknown state, as a run's input is."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    (q.root / "data" / "folder").mkdir(parents=True)
    q.run(run_id(1, "a"), script="scripts/a.py", started_at=at(1), ts=at(2),
          inputs=[("data/folder", None), ("s3://bucket/table", None)])
    p = core.Project(q.root)
    model = core.read_model(p)
    assert model.trail_start("data/folder")["state"] == "unknown"
    assert model.trail_start("s3://bucket/table")["state"] == "unknown"
    assert model._observed_input_state({"path": "data/folder", "sha256": "0" * 64}) == "unknown"


def test_the_start_is_never_where_a_read_goes_on(tmp_path):
    """I5·R2: when the start's own edges pass the budget, the read goes on from what is beyond it, never
    from the start again; a data file's bytes are named so that the read keeps them."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    outputs = [(f"data/derived/o{n:02d}.parquet", q.file(f"data/derived/o{n:02d}.parquet", f"o{n}".encode())) for n in range(25)]
    run = q.run(run_id(1, "wide"), script="scripts/w.py", started_at=at(1), ts=at(2), outputs=outputs)
    p = core.Project(q.root)
    read = core.status(p, part="relations", id="run:" + run, direction="uses")
    ids_ = [go["read"]["id"] for go in read["trail"]["continue_from"]]
    assert ids_ and "run:" + run not in ids_ and all(i.startswith("data:data/derived/o") for i in ids_)
    first = core.status(p, part="relations", id=ids_[0], direction="uses")
    assert first["items"][0]["id"] == ids_[0]


def test_answers_are_the_caller_s_to_change(tmp_path):
    """R10: changing a list in an answer changes nothing the next read gives."""
    p, ids = chain_project(tmp_path)
    model = core.read_model(p)
    fresh = core.Freshness(p, [], None, model)
    first = model.provenance(ids["finding"], input_state=fresh.input_state)
    next(e for e in first["edges"] if e["kind"] == "read")["how"].append("tampered")
    second = model.provenance(ids["finding"], input_state=fresh.input_state)
    assert all("tampered" not in e.get("how", []) for e in second["edges"])


def test_an_id_several_lines_share_is_ambiguous_in_a_trail_read(tmp_path):
    """I10: as in every other read."""
    q = LegacyProject(tmp_path / "p", migrated=True)
    q.event("finding", {}, seq=1, text="first")
    q.event("finding", {}, seq=1, text="again")
    with pytest.raises(core.AnalyticsError) as raised:
        core.status(core.Project(q.root), part="relations", id="e1", direction="sources")
    assert raised.value.code == "ambiguous_id"


def test_each_direction_has_its_checks_and_goes_on_in_its_own_direction(tmp_path):
    """R1·I6·N2: a run's page shows what can be checked along each direction with that direction; a
    uses trail's continue links open that direction (or the relations of a page with none); an output is
    compared with what was written."""
    p, ids = chain_project(tmp_path)
    workspace.build(p)
    html = relation_page(p, "run:" + ids["a"])
    sources = section(html, '<div class="pv-trail" id="trail-sources">', '<div class="pv-trail" id="trail-uses">')
    uses = section(html, '<div class="pv-trail" id="trail-uses">', '<section id="related"')
    assert sources.count('<aside class="pv-checks">') == 1 and uses.count('<aside class="pv-checks">') == 1
    assert "1 of 1 runs succeeded" in sources and "1 of 1 runs succeeded" not in uses
    assert "<k-w>Changed since written</k-w>" in uses and "<k-w>Changed since written</k-w>" not in sources
    assert "#provenance" not in uses


def test_every_relation_page_keeps_its_history(tmp_path):
    """I2·N6: a corrected record's later relation pages show its correction history too, inside the History
    tab under its heading; a path with versions and no dataset page says how many."""
    p, ids = chain_project(tmp_path)
    for n in range(20):
        core.record(p, "note", f"인용 {n}", origin=ORIGIN, refs=[{"type": "event", "event_id": ids["finding"], "relation": "cites"}])
    core.record(p, "finding", "정정", origin=ORIGIN, refs=[{"type": "event", "event_id": ids["finding"], "relation": "supersedes"}])
    workspace.build(p)
    for page in (0, 1):
        history = section(relation_page(p, ids["finding"], page), '<section id="history" class="tab">', "</section>\n<section")
        assert "<h3><k-w>Correction history</k-w></h3>" in history and "Nothing records" not in history


def test_the_codec_keeps_a_relation_page_s_tab(tmp_path):
    """R8: the URL state (C11) keeps the Provenance, All relations and History tabs."""
    script = ("const codec = require(process.argv[1]);"
              "for (const tab of ['provenance', 'related', 'history']) {"
              "  const v = codec.normalize({v: 1, page: 'relations/a-0.html', tab});"
              "  if (!v || v.tab !== tab) { console.error(tab, JSON.stringify(v)); process.exit(1); } }")
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(["node", "-e", script, str(PACK / "src" / "aelix_analytics" / "assets" / "workspace-state.js")],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
