"""A migrated project read as its source was read (Issue #13 PR 2, design 5.4, T11, S13).

The migration records where it moved things (mapping.json, bound by the report the marker binds): the
source root's spellings, the targets of the links it copied, and `.lab/` as `.analytics/`. The reader
reads an older kit's paths through that mapping first (core.mapped_identity), with PR #76's conditions
kept (relations.ReadModel: an older kit's run only, a text no run of PATH_IDENTITY_SINCE recorded,
a path that is there or a run's output, `basis: inferred`), so the destination's judgments are the
source's — after the destination moves, too, and after the link's target is gone.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat

import pytest
from conftest import LINK_KINDS, dir_link
from legacy_records import LegacyProject, at

from aelix_analytics import core, migration, relations

ORIGIN = core.Origin("human", "cli", "inferred")


def migrate(source, dest, **kwargs) -> dict:
    return migration.migrate(source, dest, origin=ORIGIN, **kwargs)


def _judgments(project: core.Project, runs: list[str], paths: dict[str, list[str]]) -> dict:
    model = core.read_model(project)
    return {"producers": {f"{run}:{path}": {k: v for k, v in model.producer(run, path).items()
                                            if k not in ("sha256",)}
                          for run in runs for path in paths[run]},
            "runs": model.runs(), "references": model.references(),
            "unsupported": model.unsupported, "damaged": model.journal.summary(),
            "records": model.record_states(), "lineage": model.lineage(),
            "imports": model.imports()}


def _normalized(value, *roots):
    """`value` with each spelling of the root a record or a judgment holds as `<root>` (the longest
    first), so a source and its copy compare where only their roots differ."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    for root in sorted({str(r) for r in roots}, key=len, reverse=True):
        text = text.replace(root, "<root>")
    return json.loads(text)


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_the_reader_reads_a_migrated_project_as_it_read_its_source(tmp_path, kind):
    """T11 (design 5.4): an older kit's runs recorded the root by another spelling, a file through a
    folder link outside the root (`data -> /ext/data`) by its real path, and a file under .lab/ by its
    relative path; a run of PATH_IDENTITY_SINCE recorded one of those texts as well. Read in the
    destination through the mapping, every judgment is the source's, but the .lab/ path, which only
    the mapping reads (inferred); then also after the destination moves and the folder outside is
    gone. Without the mapping (its report changed) the older paths are not read, and status says so."""
    ext = tmp_path / "ext" / "data"
    ext.mkdir(parents=True)
    (ext / "x.parquet").write_bytes(b"x")
    p = LegacyProject(tmp_path / "src")
    dir_link(kind, p.root / "data", ext)
    spelled = str(tmp_path / "src")  # as given: on macOS /var/…, the root's real path /private/var/…
    x_sha = hashlib.sha256(b"x").hexdigest()
    a = p.file(".lab/adhoc/a.csv", b"a")
    t = p.file("tables/t.parquet", b"t")
    maker = p.run("20261001-091000-maker", started_at=at(10), ts=at(11), kit_version="0.4.0",
                outputs=[(f"{spelled}/tables/t.parquet", t), (".lab/adhoc/a.csv", a)])
    reader = p.run("20261001-092000-reader", started_at=at(20), ts=at(21), kit_version="0.4.0",
                   inputs=[(str(ext.resolve() / "x.parquet"), x_sha), (f"{spelled}/tables/t.parquet", t),
                           (".lab/adhoc/a.csv", a)])
    newer = p.run("20261001-093000-newer", started_at=at(30), ts=at(31), kit_version="0.5.0",
                  inputs=[("data/x.parquet", x_sha), ("tables/t.parquet", t)])
    p.event("finding", {"runs": [reader], "data": ["data/x.parquet"]}, text="근거")
    paths = {reader: [str(ext.resolve() / "x.parquet"), f"{spelled}/tables/t.parquet", ".lab/adhoc/a.csv"],
             newer: ["data/x.parquet", "tables/t.parquet"]}
    runs = [reader, newer]
    before = _normalized(_judgments(core.Project(p.root), runs, paths), p.root, spelled)
    dest = tmp_path / "dest"
    migrate(p.root, dest)
    after = _normalized(_judgments(core.Project(dest), runs, paths), dest, p.root, spelled)
    lab = f"{reader}:.lab/adhoc/a.csv"
    # Only the mapping reads the .lab/ path (the source read it as recorded, which is there): an
    # inference added, the judgment's basis and candidates the same.
    assert after["producers"][lab].pop("read_as") == ".analytics/adhoc/a.csv"
    [item] = [i for i in after["lineage"] if i["path"] == ".lab/adhoc/a.csv"]
    assert item.pop("read_as") == ".analytics/adhoc/a.csv"
    assert after == before
    model = core.read_model(core.Project(dest))
    assert model.read_as(reader, str(ext.resolve() / "x.parquet")) == "data/x.parquet"
    assert model.read_as(reader, f"{spelled}/tables/t.parquet") == "tables/t.parquet"
    assert model.read_as(newer, "data/x.parquet") is None  # a 0.5.0 run's text: read as recorded
    assert model.producer(reader, f"{spelled}/tables/t.parquet")["candidates"] == [maker]
    # Moved, and the folder outside gone: the same readings, from today's root.
    moved = tmp_path / "moved"
    os.rename(dest, moved)
    shutil.rmtree(tmp_path / "ext")
    model = core.read_model(core.Project(moved))
    assert model.read_as(reader, str(ext.resolve() / "x.parquet")) == "data/x.parquet"
    assert model.read_as(reader, ".lab/adhoc/a.csv") == ".analytics/adhoc/a.csv"
    st = core.status(core.Project(moved))
    assert st["project"]["migration"]["applied"] is True
    # A report whose bytes changed: the mapping is not read, and the overview says why.
    report = moved / json.loads((moved / "analytics.json").read_text(encoding="utf-8"))["migration"]["report"]
    os.chmod(report, stat.S_IREAD | stat.S_IWRITE)
    report.write_bytes(report.read_bytes() + b" ")
    st = core.status(core.Project(moved))
    assert st["project"]["migration"] == {**st["project"]["migration"], "applied": False,
                                          "reason": "report_changed"}
    assert core.read_model(core.Project(moved)).read_as(reader, ".lab/adhoc/a.csv") is None
    assert "its mapping is not applied (report_changed)" in core.status_text(st)


def test_the_mapping_s_rules_are_by_path_components():
    mapping = core.MigrationMapping([os.sep + os.path.join("a", "proj")],
                                    [(os.sep + os.path.join("ext", "data"), "data")],
                                    [(".lab/", ".analytics/")])
    sep = os.sep
    assert mapping.translate(f"{sep}a{sep}proj{sep}x{sep}y.csv") == "x/y.csv"
    assert mapping.translate(f"{sep}a{sep}proj2{sep}y.csv") is None  # not below /a/proj
    assert mapping.translate(f"{sep}ext{sep}data{sep}x.parquet") == "data/x.parquet"
    assert mapping.translate(f"{sep}ext{sep}data2{sep}x.parquet") is None
    assert mapping.translate(f"{sep}a{sep}proj{sep}.lab{sep}adhoc{sep}o.csv") == ".analytics/adhoc/o.csv"
    assert mapping.translate(".lab/context/v.md") == ".analytics/context/v.md"
    assert mapping.translate(".lab") == ".analytics"
    assert mapping.translate(".labx/v.md") is None and mapping.translate("data/x.parquet") is None
    assert mapping.translate(f"{sep}a{sep}proj") is None  # the root itself names no file
    # Read as legacy_identity reads an absolute path (normpath): `..` folded as text, and on Windows
    # either separator, so `C:\a\proj/x/y.csv` is under the root as the source's reader takes it.
    assert mapping.translate(f"{sep}a{sep}proj{sep}sub{sep}..{sep}x{sep}y.csv") == "x/y.csv"
    if os.altsep:
        assert mapping.translate(f"{sep}a{sep}proj{os.altsep}x{os.altsep}y.csv") == "x/y.csv"
        assert mapping.translate(f"{sep}ext{os.altsep}data{sep}x.parquet") == "data/x.parquet"


def test_the_reader_knows_the_migration_family():
    assert relations.READER_FORMAT == 6
    assert relations.MIGRATION_V1 in relations.KNOWN
    assert relations.unsupported_reason({"schema": "analytics.migration/1", "record": "plan"},
                                        frozenset({relations.MIGRATION_V1})) is None


def test_what_the_mapping_is_enters_the_read_s_revision(tmp_path):
    """design 5.4 (Codex #8): the mapping is read within the read's attempts and its state is part
    of the source_revision — removed, the revision changes with what the read applies, and back with
    the same bytes, it is the revision it was."""
    p = LegacyProject(tmp_path / "src")
    a = p.file(".lab/adhoc/a.csv", b"a")
    p.run("20261001-091000-maker", started_at=at(10), ts=at(11), kit_version="0.4.0",
          outputs=[(".lab/adhoc/a.csv", a)])
    dest = tmp_path / "dest"
    migrate(p.root, dest)
    project = core.Project(dest)
    first = core.read_model(project).source_revision
    assert core.read_model(project).source_revision == first
    marker = json.loads((dest / "analytics.json").read_text(encoding="utf-8"))
    mapping = dest / marker["migration"]["report"].replace("report.json", "mapping.json")
    data = mapping.read_bytes()
    mapping.unlink()
    model = core.read_model(core.Project(dest))
    assert model.source_revision != first
    assert model.read_as("20261001-091000-maker", ".lab/adhoc/a.csv") is None
    mapping.write_bytes(data)
    assert core.read_model(core.Project(dest)).source_revision == first


def test_a_marker_report_and_mapping_of_two_migrations_are_not_read_together(tmp_path):
    """design 2.4 (Codex #7): the marker's migration field, the report and the mapping must be one
    migration's — its id in each, the report and the mapping in its directory — else no mapping is
    applied (`invalid`)."""
    p = LegacyProject(tmp_path / "src")
    p.event("note", text="n")
    dest = tmp_path / "dest"
    migrate(p.root, dest)
    marker = json.loads((dest / "analytics.json").read_text(encoding="utf-8"))
    field = marker["migration"]
    assert core.migration_mapping(dest, marker)[1]["applied"] is True
    other = {**field, "id": "20261008T000000Z-00000000"}
    assert core.migration_mapping(dest, {**marker, "migration": other})[1]["reason"] == "invalid"
    renamed = dest / ".analytics" / "migration" / "20261008T000000Z-00000000"
    shutil.copytree(dest / ".analytics" / "migration" / field["id"], renamed)
    moved = {**field, "id": renamed.name, "report": f".analytics/migration/{renamed.name}/report.json"}
    _, info = core.migration_mapping(dest, {**marker, "migration": moved})
    assert info["reason"] == "invalid", info  # the report there is the other migration's
