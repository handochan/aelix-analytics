"""Path identity (Issue #36, ADR-0006 9): a record names a file by one path whichever link,
spelling or route a script reached it by.

Inside the project that is the project-relative POSIX path through the namespace: a link that leads
inside is followed to where it leads, one that leads outside is a boundary, the place of a mount,
and of several boundaries that reach a file the first in code point order names it. Outside the
project it is the real absolute path. Every name is spelled as the file system stores it. The kit's
tree walk (outputs, datasets) and the identity of a path a script read come from one table, so they
agree.

Whether a file system ignores case or Unicode normalization is found where a test runs (a file made
under one spelling and looked up under another), never assumed per platform; the branch a test took
is kept as an observation. Directory links are made of every kind the platform has (LINK_KINDS:
symbolic links everywhere, junctions on Windows).
"""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import random
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

import pytest
from conftest import (
    KIT,
    LINK_KINDS,
    dir_link,
    kit_cli,
    record_observation,
    write_script,
)

from aelix_analytics import _platform, capture, core, runner

TEMPLATE = KIT / "aelix_analytics" / "template"


# --- helpers ------------------------------------------------------------------------------------

def bare(tmp_path: Path, name: str = "proj") -> Path:
    """A project's directories and marker, made by hand (no environment, no records): enough for
    the identity of paths and the walk. Its real path."""
    root = tmp_path / name
    for directory in ("data/raw", "data/derived", "scripts", "runs", ".lab"):
        (root / directory).mkdir(parents=True)
    (root / "lab.json").write_text("{}", encoding="utf-8")
    return Path(os.path.realpath(root))


def file_link(link: Path, target) -> Path:
    """A symbolic link to a file; skipped, as dir_link is, where the account may not make one."""
    try:
        os.symlink(target, link)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"this account may not create a symbolic link: {exc!r}")
        raise
    return link


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def ignores_case(directory: Path) -> bool:
    """Whether the file system holding `directory` takes another case of a name as that name."""
    probe = write(directory / "Case-Probe.tmp")
    try:
        return core.same_file(probe, directory / "case-probe.tmp")
    finally:
        probe.unlink()


def link_loop(kind: str, link: Path) -> Path:
    """`link` made into a link loop of `kind` (tests/test_platform.py's _link_loop)."""
    if kind == "symlink":
        dir_link(kind, link, link.name)
    else:
        partner = link.with_name(f"{link.name}-partner")
        partner.mkdir()
        dir_link(kind, link, partner)
        partner.rmdir()
        dir_link(kind, partner, link)
    with pytest.raises(OSError):
        os.path.realpath(link, strict=True)
    return link


def run_record(project: Path, result: dict) -> dict:
    return json.loads((Path(project) / "runs" / result["run"]["id"] / "run.json")
                      .read_text(encoding="utf-8"))


def _real(path) -> str:
    return core.canonical(path)


# --- the rules everywhere -----------------------------------------------------------------------

def test_a_stored_name_wins_and_another_spelling_is_one_only_for_the_same_entry(tmp_path,
                                                                                monkeypatch):
    directory = tmp_path / "d"
    write(directory / "Data.csv")
    spelling = core.Spelling()
    assert spelling.name(str(directory), "Data.csv") == "Data.csv"
    alias = spelling.name(str(directory), "DATA.CSV")
    assert alias == ("Data.csv" if ignores_case(directory) else "DATA.CSV")
    # Another spelling is the stored name only when it is the same directory entry.
    monkeypatch.setattr(core.Spelling, "_same_entry", staticmethod(lambda *args: False))
    assert core.Spelling().name(str(directory), "DATA.CSV") == "DATA.CSV"
    monkeypatch.undo()
    # A directory that cannot be listed keeps the name as written.
    real_scandir = os.scandir

    def refuse(path="."):
        if os.fspath(path) == str(directory):
            raise PermissionError(13, "listing refused", str(path))
        return real_scandir(path)

    monkeypatch.setattr(core.os, "scandir", refuse)
    assert core.Spelling().name(str(directory), "DATA.CSV") == "DATA.CSV"
    monkeypatch.undo()
    # A name made after the listing was read is read again: as written, or as stored.
    spelling.name(str(directory), "Data.csv")
    write(directory / "New.csv")
    assert spelling.name(str(directory), "New.csv") == "New.csv"
    later = write(directory / "Later.csv")
    assert spelling.name(str(directory), "LATER.CSV") == (
        "Later.csv" if core.same_file(later, directory / "LATER.CSV") else "LATER.CSV")


def test_a_file_system_without_inode_numbers_is_told_by_path(tmp_path, monkeypatch):
    """ADR-0006 9: where `st_ino` is 0, the same target is the same real path (stored spelling)."""
    a = write(tmp_path / "a.csv")
    write(tmp_path / "b.csv")
    alias = file_link(tmp_path / "alias.csv", a)
    real_stat = os.stat

    def no_inode(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        return os.stat_result((st.st_mode, 0, st.st_dev, *tuple(st)[3:]))

    monkeypatch.setattr(core.os, "stat", no_inode)
    assert core.file_key(a) == ("path", core.canonical(a))
    assert core.same_file(a, alias) and not core.same_file(a, tmp_path / "b.csv")


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_a_link_is_a_symbolic_link_or_a_junction(tmp_path, kind):
    target = tmp_path / "target"
    target.mkdir()
    link = dir_link(kind, tmp_path / "link", target)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _platform.is_link(os.lstat(link)) and not _platform.is_link(os.lstat(plain))
    entries = {entry.name: entry for entry in os.scandir(tmp_path)}
    assert _platform.is_link_entry(entries["link"]) and not _platform.is_link_entry(entries["plain"])
    files = file_link(tmp_path / "f.csv", write(tmp_path / "t.csv"))
    assert _platform.is_link(os.lstat(files))


def test_windows_folds_dot_dot_as_text_and_posix_follows_it():
    """M4: Win32 normalizes a path as text before it follows any link, so `b\\..\\x` opens `x`
    there whatever `b` is; POSIX goes to the parent of the directory `b` led to."""
    assert _platform.opened_path(r"C:\p\data\b\..\X\x.csv", ntpath) == r"C:\p\data\X\x.csv"
    assert _platform.opened_path("/p/data/b/../X/x.csv", posixpath) == "/p/data/b/../X/x.csv"


# --- the identity of a path ---------------------------------------------------------------------

@pytest.mark.parametrize("kind", LINK_KINDS)
def test_the_identity_of_a_path_follows_the_namespace(tmp_path, kind):
    root = bare(tmp_path)
    write(root / "data/raw/s.parquet")
    ext = tmp_path / "ext"
    write(ext / "x.csv", "ext")
    write(ext / "sub" / "y.csv")
    write(tmp_path / "X" / "x.csv", "beside ext")
    write(root / "data" / "X" / "x.csv", "inside data")
    dir_link(kind, root / "inward", root / "data" / "raw")
    dir_link(kind, root / "data" / "b", ext)
    dir_link(kind, root / "up", tmp_path)
    dir_link(kind, tmp_path / "via", tmp_path)
    file_link(root / "data" / "big.csv", write(tmp_path / "disk" / "big.csv"))
    project = core.Project(root)
    rel = project.rel
    # A link before the root is followed: the root is the real place.
    assert core.Project(tmp_path / "via" / "proj").root == root
    assert rel(tmp_path / "via" / "proj" / "data/raw/s.parquet") == "data/raw/s.parquet"
    # A link that leads inside is recorded where it leads.
    assert rel("inward/s.parquet") == "data/raw/s.parquet"
    # A directory link and a file link that lead outside are boundaries: their own paths.
    assert rel("data/b/x.csv") == "data/b/x.csv"
    assert rel("data/b/sub/../x.csv") == "data/b/x.csv"
    assert rel("data/b") == "data/b"
    assert rel("data/big.csv") == "data/big.csv"
    # Read by the target's own absolute path, or past an ancestor link, it is outside.
    assert rel(ext / "x.csv") == _real(ext / "x.csv")
    assert rel("up/ext/x.csv") == _real(ext / "x.csv")
    assert rel("up/proj/data/raw/s.parquet") == "data/raw/s.parquet"
    # `..` after a boundary: POSIX goes to the parent of the link's target, Windows folds it as
    # text. Either way the path recorded is the file the system opened.
    opened = (root / "data" / "b" / ".." / "X" / "x.csv").read_text(encoding="utf-8")
    recorded = rel("data/b/../X/x.csv")
    if os.name == "nt":
        assert opened == "inside data" and recorded == "data/X/x.csv"
    else:
        assert opened == "beside ext" and recorded == _real(tmp_path / "X" / "x.csv")
    record_observation(f"ancestor-link-{kind}", {
        "past_the_ancestor": rel("up/ext/x.csv"), "back_inside": rel("up/proj/data/raw/s.parquet"),
        "dot_dot_after_a_boundary": {"opened": opened, "recorded": recorded}})


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_two_links_to_one_directory_give_one_path(tmp_path, kind):
    """The first boundary, in code point order of its namespace path, names a file every boundary
    reaches: `data/b/x.csv` is `data/a/x.csv`, and the walk gives it once, under `data/a`. A file
    link before both names its file (`data/0.csv` < `data/a`); one after them does not."""
    root = bare(tmp_path)
    ext = tmp_path / "ext"
    write(ext / "x.csv")
    write(ext / "y.csv")
    dir_link(kind, root / "data" / "b", ext)
    dir_link(kind, root / "data" / "a", ext)
    file_link(root / "data" / "0.csv", ext / "x.csv")
    file_link(root / "data" / "z.csv", ext / "y.csv")
    project = core.Project(root)
    walked = {rel: real for rel, real, _ in project.walk()}
    assert {rel for rel in walked if rel.startswith("data/")} == {"data/0.csv", "data/a/y.csv"}
    assert project.rel("data/b/x.csv") == project.rel("data/a/x.csv") == "data/0.csv"
    assert project.rel("data/b/y.csv") == project.rel("data/z.csv") == "data/a/y.csv"
    assert project.boundaries() == (("data/0.csv", _real(ext / "x.csv")), ("data/a", _real(ext)))


def test_two_file_links_to_one_file_give_one_path(tmp_path):
    """M1: the second link to a file the first one gave is not given again."""
    root = bare(tmp_path)
    target = write(tmp_path / "X" / "f.parquet")
    file_link(root / "data" / "x1.parquet", target)
    file_link(root / "data" / "x2.parquet", target)
    project = core.Project(root)
    assert [rel for rel, _, _ in project.walk() if rel.startswith("data/")] == ["data/x1.parquet"]
    assert project.rel("data/x2.parquet") == "data/x1.parquet"


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_the_walk_follows_each_outward_target_once_and_no_other_link(tmp_path, kind):
    """Inward links, links to the root or an ancestor of it, loops, dangling links and links to
    a place an ignored top-level directory leads to are not walked, and the walk ends — a junction
    to an ancestor included, which Windows' os.walk would descend without end."""
    root = bare(tmp_path)
    ext = tmp_path / "ext"
    write(ext / "x.csv")
    venv = tmp_path / "venvs" / "p"
    write(venv / "lib" / "site.py")
    shutil.rmtree(root / ".lab")
    dir_link(kind, root / ".venv", venv)
    dir_link(kind, root / "data" / "in", root / "runs")
    dir_link(kind, root / "data" / "self", root)
    dir_link(kind, root / "data" / "up", tmp_path)
    dir_link(kind, root / "data" / "lib", venv / "lib")
    dir_link(kind, root / "data" / "ext", ext)
    dir_link(kind, ext / "back", ext)
    link_loop(kind, root / "data" / "loop")
    if kind == "symlink":
        os.symlink(tmp_path / "gone", root / "data" / "dangling", target_is_directory=True)
    project = core.Project(root)
    started = time.monotonic()
    walked = sorted(rel for rel, _, _ in project.walk())
    assert time.monotonic() - started < 60
    assert walked == ["data/ext/x.csv", "lab.json"]
    assert project.boundaries() == (("data/ext", _real(ext)),)


def test_the_walk_and_the_identity_agree_on_random_links(tmp_path):
    """Whatever the links, the path the walk gives a file is the path any route to it is
    recorded as (M2), and each file is given once. Random layouts from fixed seeds: directory and
    file links in the project's tree, in an ignored directory, in a deep __pycache__ and inside
    the outside trees, two file links to one file among them (M1)."""
    kinds = LINK_KINDS
    for seed in range(8):
        base = tmp_path / f"seed{seed}"
        rng = random.Random(seed)
        root = bare(base)
        outside = []
        for i in range(4):
            tree = base / "outside" / f"e{i}"
            write(tree / "f.csv", f"{seed}{i}")
            write(tree / "s" / "g.csv", f"{seed}{i}s")
            outside.append(tree)
        (root / ".venv").mkdir()
        (root / "data" / "__pycache__").mkdir()
        places = [root / "data", root / "data" / "raw", root / "data" / "derived", root / ".venv",
                  root / "data" / "__pycache__", *outside, *(t / "s" for t in outside)]
        targets = [*outside, *(t / "s" for t in outside), *(t / "f.csv" for t in outside)]
        routes = []
        for n in range(14):
            place, target = rng.choice(places), rng.choice(targets)
            if target.is_dir():
                link = dir_link(rng.choice(kinds), place / f"l{n}", target)
                routes += [link / "f.csv", link / "s" / "g.csv", link / "g.csv"]
            else:
                routes.append(file_link(place / f"l{n}.csv", target))
        routes += [file_link(root / "data" / "x1.csv", outside[0] / "f.csv"),
                   file_link(root / "data" / "x2.csv", outside[0] / "f.csv")]
        project = core.Project(root)
        walked = {}
        for rel, real, _ in project.walk():
            assert _real(real) not in walked.values(), (seed, rel)
            walked[rel] = _real(real)
        by_real = {real: rel for rel, real in walked.items()}
        fresh = core.Project(root)
        for rel in walked:
            assert fresh.rel(rel) == rel, (seed, rel)
        for route in routes:
            if route.exists() and _real(route) in by_real and route.is_relative_to(root):
                assert core.Project(root).rel(route) == by_real[_real(route)], (seed, route)


def test_a_path_through_an_ignored_directory_is_named_by_the_walk_where_it_reached_the_file(
        tmp_path):
    """M2: the walk does not enter .venv/ or a deep __pycache__, so a boundary there is not in its
    table; a file another boundary reaches is named by that one, any other by the boundary it was
    read through."""
    root = bare(tmp_path)
    ext, alone = tmp_path / "ext", tmp_path / "alone"
    write(ext / "f.csv")
    write(alone / "g.csv")
    (root / ".venv").mkdir()
    dir_link("symlink", root / ".venv" / "l", ext)
    dir_link("symlink", root / ".venv" / "m", alone)
    dir_link("symlink", root / "data" / "z", ext)
    project = core.Project(root)
    assert project.rel(".venv/l/f.csv") == "data/z/f.csv"
    assert project.rel(".venv/m/g.csv") == ".venv/m/g.csv"


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_a_managed_directory_reached_through_a_boundary_is_not_walked(project, tmp_path, kind):
    """M3: data/ and runs/ both lead to one disk elsewhere, runs/ below data/'s target. A run's
    own files are seen as runs/<id>/…, never as outputs under data/runs/."""
    store = tmp_path / "store"
    shutil.move(Path(project) / "data", store)
    shutil.move(Path(project) / "runs", store / "runs")
    dir_link(kind, Path(project) / "data", store)
    dir_link(kind, Path(project) / "runs", store / "runs")
    code, result = kit_cli(project, "run", write_script(project, "m3.py", "print('hello')\n"),
                           "--title", "m3")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert result["run"]["outputs"] == [], result["run"]["outputs"]
    assert not any("outside data/" in w for w in result["run"]["warnings"]), result["run"]
    run_id = result["run"]["id"]
    assert core.Project(project).rel(f"data/runs/{run_id}/stdout.txt") == \
        f"runs/{run_id}/stdout.txt"


def test_an_inward_data_link_keeps_its_datasets_at_their_real_place(project):
    """`data → storage/data` (a link inside): the datasets are listed as on main, at their real
    place, and a run reads them under that path."""
    storage = Path(project) / "storage"
    storage.mkdir()
    shutil.move(Path(project) / "data", storage / "data")
    dir_link("symlink", Path(project) / "data", storage / "data")
    datasets = core.list_datasets(core.Project(project))
    assert [(d["name"], d["path"]) for d in datasets] == [("sales", "storage/data/raw/sales.parquet")]
    body = "import aelix_analytics as analytics\nprint(analytics.read('sales').height)\n"
    code, result = kit_cli(project, "run", write_script(project, "inward.py", body), "--title", "in")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert [i["path"] for i in result["run"]["inputs"]] == ["storage/data/raw/sales.parquet"]
    record_observation("inward-data-link", {
        "datasets": [(d["name"], d["path"]) for d in datasets],
        "inputs": result["run"]["inputs"], "outputs": result["run"]["outputs"]})


def test_an_alias_link_inside_data_is_a_dataset_of_its_own_at_its_target(project, tmp_path):
    """M5 (a): `latest.parquet → v3.parquet` beside it is listed as on main, as dataset `latest`, at
    the path of the file it leads to; `analytics.read('latest')` reads it, and reading the
    directory records that file once."""
    derived = Path(project) / "data" / "derived"
    shutil.copyfile(Path(project) / "data/raw/sales.parquet", derived / "v3.parquet")
    file_link(derived / "latest.parquet", "v3.parquet")
    p = core.Project(project)
    listed = {d["name"]: d["path"] for d in core.list_datasets(p) if d["kind"] == "derived"}
    assert listed == {"latest": "data/derived/v3.parquet", "v3": "data/derived/v3.parquet"}
    body = "import aelix_analytics as analytics\nprint(analytics.read('latest').height)\n"
    code, result = kit_cli(project, "run", write_script(project, "latest.py", body), "--title", "l")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert [i["path"] for i in result["run"]["inputs"]] == ["data/derived/v3.parquet"]
    state = capture.RunState(p, "r", tmp_path / "r", tmp_path / "x.py")
    state.record_read(str(derived), "polars.read_parquet")
    assert list(state.inputs) == ["data/derived/v3.parquet"]


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_a_dataset_is_named_by_its_own_entry_and_listed_once_per_name(project, tmp_path, kind):
    """M5 (b) and S2: a file link reaching a file an earlier boundary names keeps its own name
    (`big`) with that boundary's path; two directory links to one directory list its file once."""
    shared = tmp_path / "shared"
    shared.mkdir()
    shutil.copyfile(Path(project) / "data/raw/sales.parquet", shared / "f.parquet")
    raw = Path(project) / "data" / "raw"
    dir_link(kind, raw / "a", shared)
    dir_link(kind, raw / "b", shared)
    file_link(raw / "big.parquet", shared / "f.parquet")
    datasets = core.list_datasets(core.Project(project))
    assert [(d["name"], d["path"]) for d in datasets] == [
        ("big", "data/raw/a/f.parquet"), ("f", "data/raw/a/f.parquet"),
        ("sales", "data/raw/sales.parquet")]
    assert core.view_names(datasets)["big"] == "data/raw/a/f.parquet"


def test_a_path_recorded_by_an_earlier_kit_is_read_as_the_namespace_now(tmp_path):
    """Project.legacy_identity, for #12: an absolute path an earlier kit recorded (the real path,
    as typed) is the project-relative path inside the root, the path below the first boundary
    that reaches it, else None — and None for a relative path (a decision #12 waits for)."""
    root = bare(tmp_path)
    write(root / "data/raw/s.parquet")
    ext = tmp_path / "ext"
    write(ext / "sub" / "y.csv")
    dir_link("symlink", root / "data" / "b", ext)
    project = core.Project(root)
    typed = str(root).swapcase() if ignores_case(root) else str(root)
    assert project.legacy_identity(os.path.join(typed, "data", "raw", "s.parquet")) == \
        "data/raw/s.parquet"
    assert project.legacy_identity(str(ext / "sub" / "y.csv")) == "data/b/sub/y.csv"
    assert project.legacy_identity(str(tmp_path / "elsewhere" / "z.csv")) is None
    assert project.legacy_identity("data/raw/s.parquet") is None
    assert core.PATH_IDENTITY_SINCE == "0.4.0"


def _query_file(project: Path, path: Path) -> tuple[int, dict]:
    return kit_cli(project, "query", f"SELECT content FROM read_text('{path}')")


def _refused(result: dict) -> bool:
    error = result.get("error") or {}
    return error.get("code") == "query_failed" and "Permission Error" in error.get("message", "")


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_query_reads_datasets_under_a_linked_data_directory(project, tmp_path, kind):
    """Item 1: `analytics_query` reads the views of a data/ that is a link, while its sandbox still
    judges the real path a read reaches: a file outside, a link out of the data/ target and
    `data/../` are refused (S12's controls), and so is a dataset below such a link, whose view is
    listed but whose query says why."""
    data = Path(project) / "data"
    elsewhere = tmp_path / "bigdisk" / "data"
    elsewhere.parent.mkdir()
    shutil.move(data, elsewhere)
    dir_link(kind, data, elsewhere)
    code, counted = kit_cli(project, "query", "SELECT count(*) AS n FROM sales")
    assert code == 0 and counted["rows"] == [[120]], counted
    write(elsewhere / "raw" / "note.txt", "inside")
    code, inside = _query_file(project, data / "raw" / "note.txt")
    assert code == 0 and inside["rows"] == [["inside"]], inside
    secret = write(tmp_path / "secret.txt", "secret")
    nested = tmp_path / "nested"
    shutil.copyfile(elsewhere / "raw" / "sales.parquet", write(nested / "x.txt").parent / "far.parquet")
    dir_link(kind, elsewhere / "raw" / "far", nested)
    refused = {}
    for name, path in (("outside", secret), ("link_out_of_the_target", data / "raw" / "far" / "x.txt"),
                       ("dot_dot", data / ".." / "lab.json")):
        code, result = _query_file(project, path)
        refused[name] = code == 1 and _refused(result)
        assert refused[name], (name, result)
    code, far = kit_cli(project, "query", "SELECT count(*) FROM far")
    assert code == 1 and _refused(far) and "real path" in far["error"]["hint"], far
    record_observation(f"query-linked-data-{kind}", {"count": counted["rows"], "refused": refused,
                                                     "dataset_below_a_link": far["error"]})


# --- runs ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", LINK_KINDS)
def test_two_links_to_one_directory_join_a_run_s_input_to_another_s_output(project, tmp_path,
                                                                           kind):
    """ADR-0006 9's example end to end: one run writes under data/a/, another reads the same file
    through data/b/; both record `data/a/t.parquet`, so the first is the second's producer."""
    shared = tmp_path / "shared"
    shared.mkdir()
    dir_link(kind, Path(project) / "data" / "b", shared)
    dir_link(kind, Path(project) / "data" / "a", shared)
    write_body = ("import aelix_analytics as analytics\n"
                  "analytics.read('sales').head(3).write_parquet('data/a/t.parquet')\n")
    code, wrote = kit_cli(project, "run", write_script(project, "w.py", write_body), "--title", "w")
    assert code == 0 and wrote["run"]["status"] == "ok", wrote
    read_body = "import polars as pl\nprint(pl.read_parquet('data/b/t.parquet').height)\n"
    code, read = kit_cli(project, "run", write_script(project, "r.py", read_body), "--title", "r")
    assert code == 0 and read["run"]["status"] == "ok", read
    out = [o for o in wrote["run"]["outputs"] if o["path"].endswith("t.parquet")]
    assert [o["path"] for o in out] == ["data/a/t.parquet"], wrote["run"]["outputs"]
    inputs = {i["path"]: i for i in read["run"]["inputs"]}
    assert set(inputs) == {"data/a/t.parquet"} and not inputs["data/a/t.parquet"]["external"]
    model = core.read_model(core.Project(project))
    assert model.producers("data/a/t.parquet", inputs["data/a/t.parquet"]["sha256"],
                           consumer=read["run"]["id"]) == [wrote["run"]["id"]]
    record_observation(f"two-links-one-target-{kind}", {
        "outputs": wrote["run"]["outputs"], "inputs": read["run"]["inputs"]})


def test_a_file_link_out_of_the_project_is_recorded_by_its_own_path(project, tmp_path):
    big = tmp_path / "bigdisk" / "big.parquet"
    big.parent.mkdir()
    shutil.copyfile(Path(project) / "data/raw/sales.parquet", big)
    file_link(Path(project) / "data" / "raw" / "big.parquet", big)
    body = "import aelix_analytics as analytics\nprint(analytics.read('big').height)\n"
    code, result = kit_cli(project, "run", write_script(project, "big.py", body), "--title", "b")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert [(i["path"], i["external"]) for i in result["run"]["inputs"]] == \
        [("data/raw/big.parquet", False)]
    assert not any("outside the project" in w for w in result["run"]["warnings"])
    record_observation("file-link-out", {"inputs": result["run"]["inputs"],
                                         "warnings": result["run"]["warnings"]})


def test_a_run_that_links_a_directory_names_its_files_as_the_namespace_is_after_it(project,
                                                                                tmp_path):
    """S6: a run that makes a link which becomes the first boundary to a directory reads a file by
    the old boundary; the record names the input and the unchanged files by the new one, so nothing
    shows as deleted and created."""
    shared = tmp_path / "shared"
    write(shared / "x.csv", "a,b\n1,2\n")
    dir_link("symlink", Path(project) / "data" / "a", shared)
    body = ("import os\nimport polars as pl\n"
            "print(pl.read_csv('data/a/x.csv').height)\n"
            f"os.symlink({str(shared)!r}, 'data/0', target_is_directory=True)\n")
    code, result = kit_cli(project, "run", write_script(project, "relink.py", body), "--title", "l")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert result["run"]["outputs"] == [], result["run"]["outputs"]
    assert [i["path"] for i in result["run"]["inputs"]] == ["data/0/x.csv"], result["run"]["inputs"]


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_a_script_under_an_outward_link_is_still_outside(project, tmp_path, kind):
    """Security judgments stay on the real path (ADR-0006 9): a boundary is no part of the project
    for where a script may come from."""
    outside = tmp_path / "code"
    write(outside / "x.py", "print('outside')\n")
    dir_link(kind, Path(project) / "scripts" / "ext", outside)
    code, result = kit_cli(project, "run", "scripts/ext/x.py", "--title", "out")
    assert code == 1 and result["error"]["code"] == "script_outside", result


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_a_module_under_an_outward_link_is_not_snapshotted(project, tmp_path, kind):
    outside = tmp_path / "lib"
    write(outside / "helper_mod.py", "VALUE = 7\n")
    dir_link(kind, Path(project) / "scripts" / "lib", outside)
    body = "import sys\nsys.path.insert(0, 'scripts/lib')\nimport helper_mod\nprint(helper_mod.VALUE)\n"
    code, result = kit_cli(project, "run", write_script(project, "mod.py", body), "--title", "mod")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert result["run"]["modules"] == [], result["run"]["modules"]


def test_a_rerun_of_a_project_whose_runs_directory_is_stored_in_another_case(project):
    """M8: where the file system ignores case, a project's runs/ may be stored as `Runs`; the
    snapshot is recorded under that stored name and the rerun's checks compare one canonical path
    with another, so it reruns as on main."""
    root = Path(project)
    if not ignores_case(root):
        pytest.skip("this file system minds case: `Runs` would be a directory of its own")
    os.rename(root / "runs", root / "runs-tmp")
    os.rename(root / "runs-tmp", root / "Runs")
    code, first = kit_cli(project, "run", write_script(project, "m8.py", "print(1)\n"), "--title", "m8")
    assert code == 0 and first["run"]["status"] == "ok", first
    assert first["run"]["script"]["snapshot"].startswith("Runs/"), first["run"]["script"]
    code, again = kit_cli(project, "run", "--rerun-of", first["run"]["id"], "--title", "again")
    assert code == 0 and again["run"]["status"] == "ok", again


# --- case and Unicode normalization -------------------------------------------------------------

CHART = ("import aelix_analytics as analytics\n"
         "class Page:\n"
         "    def _repr_html_(self):\n"
         "        return '<p>trend</p>'\n"
         "analytics.visualize(Page(), 'trend', title='trend')\n")


def test_a_script_named_in_another_case_keeps_one_chart_identity(project):
    """Issue #36 item 5a: where the file system ignores case, `Scripts/Analysis.py` is the stored
    `scripts/analysis.py`, so both runs are versions of one chart; where it minds case, the other
    spelling is no file."""
    write(Path(project) / "scripts" / "analysis.py", CHART)
    code, first = kit_cli(project, "run", "scripts/analysis.py", "--title", "one")
    assert code == 0, first
    code, second = kit_cli(project, "run", "Scripts/Analysis.py", "--title", "two")
    ignored = ignores_case(Path(project))
    if ignored:
        assert code == 0 and second["run"]["script"]["path"] == "scripts/analysis.py", second
        charts = core.chart_index(core.list_runs(core.Project(project)))
        assert [(c["key"], len(c["versions"])) for c in charts] == \
            [("scripts/analysis.py::trend", 2)]
    else:
        assert code == 1 and second["error"]["code"] == "script_missing", second
    # An absolute path whose project directory is written in another case.
    other = str(Path(project).parent / Path(project).name.upper() / "scripts" / "analysis.py")
    code, third = kit_cli(project, "run", other, "--title", "three")
    if ignored:
        assert code == 0 and third["run"]["script"]["path"] == "scripts/analysis.py", third
    else:
        assert code == 1 and third["error"]["code"] in ("script_outside", "script_missing"), third
    record_observation("case-aliases", {
        "ignores_case": ignored, "second": second.get("run", {}).get("script") or second.get("error"),
        "absolute_other_case": third.get("run", {}).get("script") or third.get("error")})


def test_an_input_read_in_another_case_meets_its_producer(project):
    """Item 5c: a dataset a run saved and another read as `data/derived/AGG.parquet` is one path
    where the file system ignores case; where it minds case, that file does not exist."""
    save = "import aelix_analytics as analytics\nanalytics.save(analytics.read('sales'), 'agg')\n"
    code, saved = kit_cli(project, "run", write_script(project, "agg.py", save), "--title", "agg")
    assert code == 0 and saved["run"]["status"] == "ok", saved
    body = "import polars as pl\nprint(pl.read_parquet('data/derived/AGG.parquet').height)\n"
    code, read = kit_cli(project, "run", write_script(project, "agg_read.py", body), "--title", "r")
    if ignores_case(Path(project)):
        assert code == 0 and read["run"]["status"] == "ok", read
        assert [i["path"] for i in read["run"]["inputs"]] == ["data/derived/agg.parquet"]
        p = core.Project(project)
        agg = next(d for d in core.status(p)["datasets"] if d["name"] == "agg")
        assert agg["producer"] == saved["run"]["id"] and read["run"]["id"] in agg["used_by"]
    else:
        assert read["run"]["status"] == "error", read


def test_an_nfc_name_of_an_nfd_file_is_recorded_as_stored(tmp_path):
    """Item 5: APFS opens a name in NFC that is stored in NFD; the record holds the stored NFD.
    NTFS and ext4 keep the two as two names, so there the NFC name is no file."""
    root = bare(tmp_path)
    nfd = unicodedata.normalize("NFD", "입력.csv")
    nfc = unicodedata.normalize("NFC", "입력.csv")
    write(root / "data" / "raw" / nfd)
    project = core.Project(root)
    aliased = os.path.exists(root / "data" / "raw" / nfc)
    recorded = project.rel(root / "data" / "raw" / nfc)
    if aliased:
        assert recorded == f"data/raw/{nfd}"
    else:
        assert recorded == f"data/raw/{nfc}" and not (root / "data" / "raw" / nfc).exists()
    record_observation("nfd-aliases", {"nfc_opens_the_nfd_file": aliased,
                                       "recorded_is_nfd": recorded == f"data/raw/{nfd}"})


def test_trust_of_a_project_created_under_another_case_of_its_parent(tmp_path, monkeypatch):
    """Item 5d: a project made through another case of its parent directory is trusted from the
    directory aelix starts in (its stored spelling)."""
    parent = tmp_path / "Parent"
    parent.mkdir()
    typed = tmp_path / "PARENT" / "proj"
    core.init_project(typed, title="t", template_dir=TEMPLATE, actor="test")
    monkeypatch.chdir(typed)
    found = core.find_project(os.getcwd())
    assert found is not None and core.is_trusted(found)


def test_an_old_trust_entry_in_another_case_still_trusts(tmp_path, monkeypatch):
    """A trust list a kit before 0.4.0 wrote holds the path as typed; it trusts the project where
    the file system takes that spelling as the stored one."""
    root = bare(tmp_path, "Proj")
    trust = tmp_path / "trust.json"
    monkeypatch.setenv("AELIX_ANALYTICS_TRUST_FILE", str(trust))
    other = str(root.parent / "PROJ")
    trust.write_text(json.dumps({"schema": "lab.trust/1", "projects": [other]}), encoding="utf-8")
    assert core.is_trusted(root) is ignores_case(root)


def test_view_names_give_the_kit_named_stem_first():
    """Item 6, M7: a stem that follows the kit's dataset name rule takes its name first, whatever
    kind it is; among kit names raw keeps it."""
    person = {"name": "Sales", "path": "data/raw/Sales.parquet", "kind": "raw"}
    kit_raw = {"name": "sales", "path": "data/raw/sales.parquet", "kind": "raw"}
    kit_derived = {"name": "sales", "path": "data/derived/sales.parquet", "kind": "derived"}
    assert core.view_names([person, kit_raw]) == {"sales": "data/raw/sales.parquet",
                                                  "sales_2": "data/raw/Sales.parquet"}
    assert core.view_names([person, kit_derived]) == {"sales": "data/derived/sales.parquet",
                                                      "sales_2": "data/raw/Sales.parquet"}
    assert core.view_names([kit_derived, kit_raw]) == {"sales": "data/raw/sales.parquet",
                                                       "sales_derived": "data/derived/sales.parquet"}


def test_status_warns_of_dataset_names_that_differ_only_in_case(tmp_path):
    person = {"name": "Sales", "path": "data/raw/Sales.parquet", "kind": "raw"}
    kit = {"name": "sales", "path": "data/raw/sales.parquet", "kind": "raw"}
    other = {"name": "sales_2", "path": "data/raw/sales-2.parquet", "kind": "raw"}
    # `sales_2` (a kit name) takes its own name first, so the person's file is `sales_3`; a
    # collision that is not one of case (`sales-2`, `sales_2`) is no warning.
    groups = core.case_only_datasets([person, kit, other])
    assert groups == [[{"name": "Sales", "path": "data/raw/Sales.parquet", "view": "sales_3"},
                       {"name": "sales", "path": "data/raw/sales.parquet", "view": "sales"}]]
    project = core.init_project(tmp_path / "p", title="t", template_dir=TEMPLATE, actor="test")
    st = core.status(project)
    assert st["case_only_datasets"] == []
    text = core.status_text(st)
    assert "differ only in case" not in text
    st["case_only_datasets"] = groups
    assert ("dataset names that differ only in case: Sales (data/raw/Sales.parquet, view sales_3), "
            "sales (data/raw/sales.parquet, view sales)") in core.status_text(st)


def _case_only_datasets(root: Path) -> dict:
    """A person's data/raw/Sales.parquet beside the kit's imported `sales`, where the file system
    holds both: the kit's keeps the view `sales`, and status says so."""
    sales = root / "data" / "raw" / "sales.parquet"
    person = root / "data" / "raw" / "Sales.parquet"
    try:
        with open(person, "xb") as fh:
            fh.write(b"")
    except FileExistsError:
        assert core.same_file(person, sales)
        return {"coexist": False}
    person.unlink()
    import polars as pl  # noqa: PLC0415 - the analysis environment's

    pl.read_parquet(sales).head(1).write_parquet(person)
    body = ("import aelix_analytics as analytics\n"
            "print(analytics.read('sales').height, analytics.read('sales_2').height)\n")
    code, result = kit_cli(root, "run", write_script(root, "case_only.py", body), "--title", "c")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert result["stdout_tail"].split() == ["120", "1"], result["stdout_tail"]
    st = core.status(core.Project(root))
    assert st["views"]["sales"] == "data/raw/sales.parquet"
    assert "differ only in case: Sales (data/raw/Sales.parquet, view sales_2)" in \
        core.status_text(st)
    return {"coexist": True, "views": st["views"], "groups": st["case_only_datasets"]}


def test_a_case_variant_dataset_file_does_not_take_the_kit_dataset_s_view(project):
    """Item 6 end to end: where the file system holds both (Linux), the person's file gets
    `sales_2`; where it ignores case (macOS, Windows), the two cannot both exist."""
    record_observation("case-only-datasets", _case_only_datasets(Path(project)))


@pytest.mark.skipif(os.name != "nt", reason="a directory flagged case-sensitive is Windows'")
def test_a_case_variant_dataset_in_a_case_sensitive_windows_directory(tmp_path):
    flagged = tmp_path / "case-sensitive"
    flagged.mkdir()
    done = subprocess.run(["fsutil.exe", "file", "setCaseSensitiveInfo", str(flagged), "enable"],
                          capture_output=True, timeout=60, **_platform.text_kwargs())
    assert done.returncode == 0, (done.stdout, done.stderr)
    root = flagged / "proj"
    core.init_project(root, title="대소문자", goal="대소문자 구분", template_dir=TEMPLATE,
                      actor="test")
    from conftest import write_sales_csv  # noqa: PLC0415

    code, result = kit_cli(root, "import", write_sales_csv(tmp_path / "sales.csv"), "--name",
                           "sales")
    assert code == 0, result
    observed = _case_only_datasets(root)
    assert observed["coexist"], observed
    record_observation("case-only-datasets-case-sensitive-dir", observed)


@pytest.mark.skipif(os.name != "nt", reason="8.3 short names are Windows'")
def test_a_short_name_of_a_directory_in_the_project_is_recorded_by_its_long_name(tmp_path):
    import ctypes  # noqa: PLC0415

    root = bare(tmp_path)
    long_dir = root / "longdirectoryname"
    write(long_dir / "x.csv")
    buffer = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buffer, 1024)
    short = buffer.value
    if not short or Path(short).name.lower() == "longdirectoryname":
        pytest.skip(f"no 8.3 short name on this volume: {short!r}")
    assert core.Project(root).rel(Path(short) / "x.csv") == "longdirectoryname/x.csv"


# --- record files that are links (item 4 and CONTRACTS G) --------------------------------------

def test_the_trust_list_is_replaced_at_its_link_target(tmp_path, monkeypatch):
    """Item 4: a trust list kept elsewhere (dotfiles) and linked stays a link; its target holds the
    new project. A dangling link is replaced by a file, as before."""
    target = tmp_path / "dotfiles" / "lab-projects.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"schema": "lab.trust/1", "projects": []}), encoding="utf-8")
    (tmp_path / "agent").mkdir()
    link = file_link(tmp_path / "agent" / "lab-projects.json", target)
    monkeypatch.setenv("AELIX_ANALYTICS_TRUST_FILE", str(link))
    root = bare(tmp_path)
    core.trust_project(root)
    assert os.path.islink(link) and core.is_trusted(root)
    assert json.loads(target.read_text(encoding="utf-8"))["projects"] == [str(root)]
    dangling = file_link(tmp_path / "dangling.json", tmp_path / "gone.json")
    monkeypatch.setenv("AELIX_ANALYTICS_TRUST_FILE", str(dangling))
    core.trust_project(root)
    assert not os.path.islink(dangling) and core.is_trusted(root)
    record_observation("trust-link", {"link_kept": True, "dangling_replaced_by_a_file": True})


def test_a_record_that_is_a_link_is_replaced_by_a_file(tmp_path):
    """CONTRACTS G (the intended rule): replacing a record replaces a link at its place."""
    target = write(tmp_path / "elsewhere" / "record.json", "{}")
    link = file_link(tmp_path / "record.json", target)
    core.write_json(link, {"new": True})
    replaced = not os.path.islink(link)
    assert replaced and json.loads(link.read_text(encoding="utf-8")) == {"new": True}
    assert target.read_text(encoding="utf-8") == "{}"
    record_observation("record-link-replaced", {"link_replaced_by_a_file": replaced})


def test_an_appended_log_that_is_a_link_is_written_at_its_target(tmp_path):
    """CONTRACTS G: an append (journal, telemetry, queries) follows the link."""
    target = write(tmp_path / "elsewhere" / "journal.jsonl", "")
    link = file_link(tmp_path / "journal.jsonl", target)
    core.append_jsonl(link, {"seq": 1})
    assert os.path.islink(link)
    assert target.read_text(encoding="utf-8") == '{"seq": 1}\n'
    record_observation("append-link-followed", {"link_kept": True, "written_at_target": True})


# --- the cost -----------------------------------------------------------------------------------

def _tree_stat_before(root: Path) -> dict:
    """runner.tree_stat as main had it (`e9392d9`), for the comparison."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root:
            dirnames[:] = [d for d in dirnames if d not in core.IGNORED_DIRS]
        else:
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".ipynb_checkpoints")]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            out[os.path.relpath(path, root)] = (st.st_size, st.st_mtime_ns)
    return out


def _best(fn, times: int = 3) -> tuple[float, object]:
    best, value = None, None
    for _ in range(times):
        started = time.perf_counter()
        value = fn()
        elapsed = (time.perf_counter() - started) * 1000
        best = elapsed if best is None else min(best, elapsed)
    return round(best, 1), value


def test_the_walk_at_m_scale_is_observed(tmp_path):
    """The M scale of the beta gates (300 runs, 10,000 data files): the new walk sees the files
    main's saw, and what each took is kept (`walk-m-scale`), with a linked data/ (which main did not
    walk), the identity of 10,000 paths, and 1,000 files made after a directory was listed and then
    named one by one. Times are not asserted (shared runners)."""
    root = bare(tmp_path)
    for i in range(2000):
        (root / "data" / "raw" / f"r{i:05d}.parquet").write_bytes(b"x" * 16)
    for d in range(100):
        directory = root / "data" / "derived" / f"d{d:03d}"
        directory.mkdir()
        for i in range(80):
            (directory / f"f{i:03d}.parquet").write_bytes(b"y" * 16)
    for r in range(300):
        write(root / "runs" / f"20260101-0000{r:02d}-m-{r}" / "run.json", "{}")
    before_ms, before = _best(lambda: _tree_stat_before(root))
    after_ms, after = _best(lambda: runner.tree_stat(core.Project(root)))
    assert {key.replace(os.sep, "/") for key in before} == set(after)
    paths = [root / rel for rel in after if rel.startswith("data/")]
    def identities_of(paths):
        project = core.Project(root)
        return [project.rel(path) for path in paths]

    identity_ms, identities = _best(lambda: identities_of(paths))
    assert identities == [p.relative_to(root).as_posix() for p in paths]
    listing_ms, listed = _best(lambda: core.list_datasets(core.Project(root), []))
    assert len(listed) == 10000
    # Files a run makes after the directory was listed, then named one by one (S4).
    project = core.Project(root)
    project.rel(root / "data" / "derived" / "d000" / "f000.parquet")
    made = root / "data" / "derived" / "d000"
    for i in range(1000):
        (made / f"n{i:04d}.parquet").write_bytes(b"z")
    started = time.perf_counter()
    for i in range(1000):
        assert project.rel(made / f"n{i:04d}.parquet") == f"data/derived/d000/n{i:04d}.parquet"
    made_ms = round((time.perf_counter() - started) * 1000, 1)
    # Made and read back one at a time: the directory is read again a few dozen times, not 1,000.
    one_by_one = root / "data" / "derived" / "d001"
    started = time.perf_counter()
    for i in range(1000):
        (one_by_one / f"m{i:04d}.parquet").write_bytes(b"z")
        assert project.rel(one_by_one / f"m{i:04d}.parquet") == f"data/derived/d001/m{i:04d}.parquet"
    interleaved_ms = round((time.perf_counter() - started) * 1000, 1)
    elsewhere = tmp_path / "bigdisk"
    shutil.move(root / "data", elsewhere)
    dir_link(LINK_KINDS[-1], root / "data", elsewhere)
    linked_before_ms, linked_before = _best(lambda: _tree_stat_before(root))
    linked_ms, linked = _best(lambda: runner.tree_stat(core.Project(root)))
    assert sum(1 for rel in linked if rel.startswith("data/")) == 12000
    linked_identity_ms, linked_identities = _best(lambda: identities_of(paths))
    assert linked_identities == identities
    record_observation("walk-m-scale", {
        "files": len(after), "tree_stat_before_ms": before_ms, "tree_stat_ms": after_ms,
        "identity_10000_ms": identity_ms, "list_datasets_10000_ms": listing_ms,
        "identity_of_1000_files_made_after_listing_ms": made_ms,
        "make_and_identify_1000_files_one_at_a_time_ms": interleaved_ms,
        "linked_data_kind": LINK_KINDS[-1],
        "linked_tree_stat_before_ms": linked_before_ms,
        "linked_tree_stat_before_files_under_data": sum(
            1 for key in linked_before if key.replace(os.sep, "/").startswith("data/")),
        "linked_tree_stat_ms": linked_ms, "linked_identity_10000_ms": linked_identity_ms})
