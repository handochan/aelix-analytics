"""#158: a run ID shortened at a hyphen — the date, time and number people and models write
(`YYYYMMDD-HHMMSS-NN` of `YYYYMMDD-HHMMSS-NN-name`) — reads, binds and reruns as the one run it starts.
One that starts several runs is refused with them, and a record keeps the run's whole ID."""
import pytest
from conftest import kit_cli, write_script
from legacy_records import LegacyProject

from aelix_analytics import core, relations

ORIGIN = core.Origin("human", "cli", "declared")
EDA = "20261104-012254-01-eda-distributions"
MODEL, MODEL_2 = "20261104-013000-02-model", "20261104-013000-02-model-2"


def project_with_runs(tmp_path):
    """A project this kit writes to, with three runs: two of them start with the same date, time and
    number (a run made in the same second by the same script)."""
    legacy = LegacyProject(tmp_path / "p", migrated=True)
    for run_id in (EDA, MODEL, MODEL_2):
        legacy.run(run_id)
    return core.Project(legacy.root)


def test_a_run_id_names_its_run_or_the_one_run_it_starts(tmp_path):
    root = tmp_path / "p"
    for name in (EDA, MODEL, MODEL_2, "20261104-020000-03"):
        (root / "runs" / name).mkdir(parents=True)
    assert relations.runs_named(root, EDA) == [EDA]
    assert relations.runs_named(root, "20261104-012254-01") == [EDA]
    assert relations.runs_named(root, "20261104-012254-01-eda") == [EDA]
    # A whole ID is its own run, though it starts another's.
    assert relations.runs_named(root, MODEL) == [MODEL]
    assert relations.runs_named(root, "20261104-013000-02") == [MODEL, MODEL_2]
    assert relations.runs_named(root, "20261104-020000-03") == ["20261104-020000-03"]
    # Cut inside a part, too short to be a run ID, another kind of ID, or the start of no run: none.
    for text in ("20261104-012254-0", "20261104-012254-01-ed", "20261104-012254", "e1", "run:" + EDA,
                 "20261104-999999-01", None):
        assert relations.runs_named(root, text) == []


def test_a_record_binds_a_shortened_run_id_to_the_whole_one(tmp_path):
    p = project_with_runs(tmp_path)
    written = core.record(p, "finding", "분포가 오른쪽으로 치우쳤다", refs=["20261104-012254-01", EDA], origin=ORIGIN)
    shortened, whole = written.event["refs"]
    # The kit chose the run from a name: inferred, the name kept as the token (ADR-0006 3), and the
    # whole ID, so the reference binds that run whatever runs come later.
    assert shortened == {"type": "run", "run_id": EDA, "token": "20261104-012254-01", "relation": "cites",
                         "basis": "declared", "version_binding": "inferred"}
    assert whole == {"type": "run", "run_id": EDA, "relation": "cites", "basis": "declared",
                     "version_binding": "author"}
    assert [(b["input"], b["binding"]) for b in written.bindings] == [("20261104-012254-01", "inferred"),
                                                                      (EDA, "author")]
    judged = core.read_model(p).references(written.event["id"])
    assert [(j["state"], j["confirmed"]) for j in judged] == [("resolved", False), ("resolved", True)]
    (p.root / "runs" / (EDA + "-2")).mkdir()
    assert core.read_model(p).references(written.event["id"])[0]["target"]["run_id"] == EDA


def test_a_shortened_run_id_that_starts_several_runs_is_refused_with_them(tmp_path):
    p = project_with_runs(tmp_path)
    before = p.journal.read_bytes()
    with pytest.raises(core.AnalyticsError) as error:
        core.record(p, "finding", "두 모델을 비교했다", refs=["20261104-013000-02"], origin=ORIGIN)
    assert error.value.code == "ambiguous_id"
    assert error.value.details == {"id": "20261104-013000-02", "candidates": [MODEL, MODEL_2]}
    assert p.journal.read_bytes() == before
    # An ID that starts no run is written as it was given, as before.
    unknown = core.record(p, "note", "없는 run", refs=["20261104-012254-09"], origin=ORIGIN)
    assert unknown.event["refs"][0]["run_id"] == "20261104-012254-09"
    assert unknown.event["refs"][0]["version_binding"] == "author"


def test_reads_take_a_shortened_run_id_and_say_which_run_they_read(tmp_path):
    p = project_with_runs(tmp_path)
    core.record(p, "finding", "분포가 치우쳤다", refs=[EDA], origin=ORIGIN)
    record = core.status(p, part="records", id="run:20261104-012254-01")
    assert record["id"] == "run:" + EDA and record["location"] == f"runs/{EDA}/run.json"
    assert record["resolved_id"] == {"input": "run:20261104-012254-01", "id": "run:" + EDA}
    # A cursor of the shortened read goes on with either spelling: the request is the whole ID's.
    first = core.status(p, part="records", id="run:20261104-012254-01", limit=2)
    assert first["has_more"]
    assert core.status(p, part="records", id="run:" + EDA, limit=2, cursor=first["cursor"])["returned"]
    one_hop = core.status(p, part="relations", id="run:20261104-012254-01", direction="incoming")
    assert one_hop["items"] == core.status(p, part="relations", id="run:" + EDA, direction="incoming")["items"]
    trail = core.status(p, part="relations", id="run:20261104-012254-01", direction="sources")
    assert trail["trail"]["start"] == core.status(p, part="relations", id="run:" + EDA,
                                                  direction="sources")["trail"]["start"]
    assert "resolved_id" not in core.status(p, part="records", id="run:" + EDA)
    with pytest.raises(core.AnalyticsError) as error:
        core.status(p, part="records", id="run:20261104-013000-02")
    assert error.value.code == "ambiguous_id"
    assert error.value.details["candidates"] == ["run:" + MODEL, "run:" + MODEL_2]


def test_the_cli_and_the_sdk_resolve_a_shortened_run_id_the_same_way(tmp_path, monkeypatch):
    import aelix_analytics as analytics

    p = project_with_runs(tmp_path)
    monkeypatch.setenv("AELIX_ANALYTICS_PROJECT", str(p.root))
    sdk = analytics.status(part="records", id="run:20261104-012254-01")
    code, cli = kit_cli(p.root, "status", "--part", "records", "--id", "run:20261104-012254-01")
    assert code == 0 and cli["status"]["resolved_id"] == sdk["resolved_id"] == {
        "input": "run:20261104-012254-01", "id": "run:" + EDA}
    code, noted = kit_cli(p.root, "record", "finding", "분포가 치우쳤다", "--ref", "20261104-012254-01")
    assert code == 0 and noted["event"]["refs"][0]["run_id"] == EDA
    assert noted["event"]["refs"][0]["token"] == "20261104-012254-01"
    written = analytics.record("note", "SDK도 같다", refs=["20261104-012254-01"])
    assert written["event"]["refs"][0] == noted["event"]["refs"][0]
    code, refused = kit_cli(p.root, "record", "finding", "x", "--ref", "20261104-013000-02")
    assert code == 1 and refused["error"]["code"] == "ambiguous_id"
    assert refused["error"]["details"]["candidates"] == [MODEL, MODEL_2]


def test_a_rerun_takes_a_run_id_shortened_at_a_hyphen(project):
    """`--rerun-of` takes the date, time and number of a run when they start one run's ID; the new
    run's rerun_of and its comparison name that run's whole ID. Starting several, it is refused."""
    _, one = kit_cli(project, "run", write_script(project, "07_short.py", "x = 1\n"), "--title", "t")
    whole = one["run"]["id"]
    short = "-".join(whole.split("-")[:3])
    assert short.endswith("-07") and short != whole
    code, again = kit_cli(project, "run", "--rerun-of", short, "--title", "다시")
    assert code == 0 and again["run"]["rerun_of"] == whole
    assert again["run"]["reproduction"]["of"] == whole
    # Another run of the same second and number (the rerun may be one already, made in that second).
    (project / "runs" / (short + "-other")).mkdir()
    started = sorted(p.name for p in (project / "runs").iterdir() if p.name.startswith(short + "-"))
    code, refused = kit_cli(project, "run", "--rerun-of", short, "--title", "다시")
    assert code == 1 and refused["error"]["code"] == "ambiguous_id"
    assert refused["error"]["details"]["candidates"] == started and whole in started
