"""`analytics migrate`, the kit's command (Issue #13 PR 3, docs/design/RECORD-WRITER-AND-MIGRATION.md
6.1, 6.9, 6.10): the engine tests/test_migration.py tests, opened as `python -m aelix_analytics
migrate --to DEST [--dry-run]`.

What is checked here is what the command adds: the source found as every command finds its project
(none found, or found to be the destination itself, is a call without a source), the one JSON line
and its exit status (0 for a migration that went on, committed or confirmed already, and for a dry run
that would; 1 for a refusal, the dry run's with its plan), the origin of the call (OQ14: an agent's is
taken and recorded as the agent's), the standard library only, and design 11.2's T21 through the
command and its dry run: every call that is refused, or that confirms a committed destination,
leaves the destination and the source as they were and the trust list too, but for the entry a
confirmation adds where it is missing. tests/test_launcher.py runs the launchers' `migrate`, and with
a real uv (T18) the environment they prepare after it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import kit_env
from test_migration import (
    LOCKS,
    POSIX,
    assert_committed,
    assert_uncommitted,
    interrupt,
    ledger,
    mdir,
    plain_source,
    source_tree,
    tree,
)

from aelix_analytics import _platform, core, migration, writer

TEMPLATE = Path(__file__).resolve().parents[1] / "src" / "aelix_analytics" / "template"


def command(*args, cwd: Path, actor: str | None = "test", **extra) -> tuple[int, dict]:
    """`python -m aelix_analytics ARGS` from `cwd`, as a person's terminal runs it: (exit status, the
    last line of stdout as JSON). `actor` is AELIX_ANALYTICS_ACTOR (None: not set)."""
    env = kit_env(**extra)
    if "AELIX_ANALYTICS_PROJECT" not in extra:
        env.pop("AELIX_ANALYTICS_PROJECT", None)
    if actor is None:
        env.pop("AELIX_ANALYTICS_ACTOR", None)
    else:
        env["AELIX_ANALYTICS_ACTOR"] = actor
    done = subprocess.run([sys.executable, "-m", "aelix_analytics", *map(str, args)], cwd=cwd,
                          capture_output=True, env=env, timeout=300, **_platform.text_kwargs())
    lines = [line for line in done.stdout.split("\n") if line.startswith("{")]
    assert lines, (done.returncode, done.stdout, done.stderr)
    assert len(lines) == 1, done.stdout  # one JSON line, as every command
    return done.returncode, json.loads(lines[-1])


def trust_bytes() -> bytes | None:
    path = Path(os.environ["AELIX_ANALYTICS_TRUST_FILE"])
    return path.read_bytes() if path.exists() else None


def linked_source(base: Path) -> tuple[Path, Path]:
    """A lab.json project with a folder outside it reached by a link (OQ3), and that folder."""
    source = plain_source(base)
    ext = base / "ext"
    ext.mkdir()
    (ext / "reading.csv").write_bytes(b"t,v\n1,2\n")
    os.symlink(str(ext), source / "linked", target_is_directory=True)
    (source / "scripts").mkdir()
    return source, ext


# --- one migration through the command (G3-01) ----------------------------------------------------

def test_a_migration_from_inside_the_source_commits_and_leaves_the_source_as_it_was(tmp_path):
    source = plain_source(tmp_path)
    (source / "scripts").mkdir()
    before = source_tree(source)
    dest = tmp_path / "new"
    code, result = command("migrate", "--to", dest, cwd=source / "scripts")
    assert code == 0 and result["ok"] and result["already_migrated"] is False, result
    assert result["destination"] == str(dest.resolve())
    assert result["source"] == {"path": str(source.resolve()), "given": "given"}
    report = assert_committed(dest, source)
    assert (report["actor"], report["actor_basis"], report["means"]) == ("test", "declared", "cli")
    assert source_tree(source) == before
    assert core.is_trusted(dest)
    # The same call again: confirmed, nothing copied, exit 0.
    code, again = command("migrate", "--to", dest, cwd=source)
    assert code == 0 and again["already_migrated"] is True and again["compared"]["same"] > 0, again
    # Given by --project (the kit's option) and by AELIX_ANALYTICS_PROJECT, as every command.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert command("--project", source, "migrate", "--to", dest, cwd=elsewhere)[1]["already_migrated"]
    assert command("migrate", "--to", dest, cwd=elsewhere,
                   AELIX_ANALYTICS_PROJECT=str(source))[1]["already_migrated"]


def test_the_dry_run_writes_nothing_and_exits_by_what_would_happen(tmp_path):
    """design 6.9: no destination, no lock file in the source or the destination; 0 where the
    migration would go on, 1 and the error where it would be refused — with the plan in its details,
    the links outside first (OQ12)."""
    source, _ = linked_source(tmp_path)
    before = source_tree(source)
    dest = tmp_path / "new"
    code, result = command("migrate", "--to", dest, "--dry-run", cwd=source)
    assert code == 0 and result["dry_run"] and result["state"] == "new", result
    assert list(result["plan"])[0] == "links_outside" and result["plan"]["links_outside"][0]["path"] == "linked"
    assert result["plan"]["folding_judged"] is False and result["plan"]["publication_judged"] is False
    assert not dest.exists() and not any((source / rel).exists() for rel in LOCKS)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "notes.txt").write_text("mine\n", encoding="utf-8")
    code, refused = command("migrate", "--to", occupied, "--dry-run", cwd=source)
    assert code == 1 and refused["error"]["code"] == "destination_invalid", refused
    details = refused["error"]["details"]
    assert details["reason"] == "not_empty" and details["dry_run"] is True and details["state"] == "refused"
    assert details["plan"]["files"] == result["plan"]["files"]
    assert tree(occupied) == {"notes.txt": tree(occupied)["notes.txt"]}
    assert source_tree(source) == before


def test_what_a_dry_run_refuses_first_is_a_link_outside_whose_target_is_gone(tmp_path):
    """OQ12: a link outside whose target is not there (a disk not mounted) is refused — by the dry
    run too, the links listed first in its plan — and nothing is written."""
    source, ext = linked_source(tmp_path)
    shutil.rmtree(ext)
    before = source_tree(source)
    for extra in (["--dry-run"], []):
        code, result = command("migrate", "--to", tmp_path / "new", *extra, cwd=source)
        assert code == 1 and result["error"]["code"] == "source_unreadable", result
        details = result["error"]["details"]
        assert details["reason"] == "link_target_missing" and details["links"][0]["path"] == "linked"
        if extra:
            assert details["plan"]["links_outside"][0] == {"path": "linked", "text": str(ext),
                                                           "target": "missing"}
        assert not (tmp_path / "new").exists()
    assert source_tree(source) == before


@pytest.mark.skipif(sys.platform != "linux", reason="a name that is not UTF-8: Linux's file systems keep "
                                                     "one, APFS and NTFS refuse it")
def test_what_a_dry_run_refuses_first_is_a_name_that_is_not_utf8(tmp_path):
    source = plain_source(tmp_path)
    (source / os.fsdecode(b"data/raw/caf\xe9.csv")).write_bytes(b"x\n")
    for extra in (["--dry-run"], []):
        code, result = command("migrate", "--to", tmp_path / "new", *extra, cwd=source)
        assert code == 1 and result["error"]["details"]["reason"] == "name_not_utf8", result
        assert not (tmp_path / "new").exists()


def test_an_interrupted_migration_goes_on_by_the_same_command(tmp_path):
    source = plain_source(tmp_path)
    dest = tmp_path / "new"
    interrupt(source, dest, {"step": "copied"})
    assert_uncommitted(dest)
    code, dry = command("migrate", "--to", dest, "--dry-run", cwd=source)
    assert code == 0 and dry["state"] == "resumable", dry
    code, result = command("migrate", "--to", dest, cwd=source)
    assert code == 0 and result["already_migrated"] is False, result
    assert_committed(dest, source)


def test_a_call_without_a_source_goes_on_only_where_the_source_was_checked(tmp_path):
    """design 6.1: found nowhere, or found to be the destination itself (called from inside it), the
    call has no source — it goes on with a migration whose ledger checked its source, and is refused
    where the source is still to be read: no_project, or source_not_legacy for the destination."""
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    source = plain_source(tmp_path)
    early = tmp_path / "early"
    interrupt(source, early, {"step": "plan"})
    before = tree(early)
    code, result = command("migrate", "--to", early, cwd=nowhere)
    assert (code, result["error"]["code"]) == (1, "no_project"), result
    code, result = command("migrate", "--to", ".", cwd=early)
    assert (code, result["error"]["code"]) == (1, "source_not_legacy"), result
    assert result["error"]["details"] == {"state": "incomplete"}
    assert tree(early) == before
    checked = tmp_path / "checked"
    interrupt(source, checked, {"step": "verified"})
    shutil.rmtree(source)  # the source is not read again after its check (design 6.10)
    code, result = command("migrate", "--to", checked, cwd=nowhere)
    assert code == 0 and result["source"]["given"] == "not_given" and not result["already_migrated"]
    assert_committed(checked)


def test_refusals_change_nothing(tmp_path):
    """A source in the current format, a destination inside the source or inside another project,
    a destination that is a file: each its refusal and exit 1, the source and the destination as
    they were."""
    current = core.init_project(tmp_path / "current", template_dir=TEMPLATE,
                                origin=core.Origin("test", "cli")).root
    code, result = command("migrate", "--to", tmp_path / "x", cwd=current)
    assert (code, result["error"]["code"]) == (1, "source_not_legacy")
    assert result["error"]["details"] == {"state": "current"}
    source = plain_source(tmp_path)
    before = source_tree(source)
    (tmp_path / "file").write_text("x", encoding="utf-8")
    for dest, reason in ((source / "inside", "overlaps_source"), (current / "inside", "inside_project"),
                         (tmp_path / "file", "not_a_directory")):
        code, result = command("migrate", "--to", dest, cwd=source)
        assert code == 1 and result["error"]["details"]["reason"] == reason, result
    assert source_tree(source) == before
    assert not (current / "inside").exists() and not (source / "inside").exists()


@pytest.mark.parametrize("stop", [{"step": "migration_dir"}, {"step": "copied", "nth": 2}])
def test_nothing_is_made_inside_a_destination_not_committed_and_its_migration_goes_on(tmp_path, stop):
    """OQ9 as the owner widened it (2026-10-09): below a destination a migration left before its
    marker — its lock and a migration directory without a line (F1), or copies (F6) — the kit's
    init (from anywhere, a relative path from inside it too) and `migrate --to` (another source's,
    this source's, a dry run) are destination_invalid (inside_project, naming that destination) and
    leave it, the sources and the trust list as they were; the first migration then goes on by the
    same command and commits."""
    source = plain_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, stop)
    assert_uncommitted(dest)
    other = plain_source(tmp_path, "other")
    before, sources, trusted = tree(dest), (source_tree(source), source_tree(other)), trust_bytes()
    calls = [(("init", dest / "inner"), tmp_path), (("init", dest / "deeper" / "new", "--title", "t"), tmp_path),
             (("init", "inner"), dest), (("migrate", "--to", dest / "inner"), other),
             (("migrate", "--to", dest / "inner"), source), (("migrate", "--to", dest / "x", "--dry-run"), other)]
    for args, cwd in calls:
        code, result = command(*args, cwd=cwd)
        assert (code, result["error"]["code"]) == (1, "destination_invalid"), (args, result)
        details = result["error"]["details"]
        assert (details["reason"], details["project"]) == ("inside_project", str(dest.resolve())), args
        assert "a migration that has not committed" in result["error"]["message"], result
    assert tree(dest) == before and (source_tree(source), source_tree(other)) == sources
    assert trust_bytes() == trusted
    code, result = command("migrate", "--to", dest, cwd=source)
    assert code == 0 and result["already_migrated"] is False, result
    assert_committed(dest, source)


# --- who the call is by (OQ14, T14's migrate row) --------------------------------------------------

@pytest.mark.parametrize(("actor", "expected"), [(None, ("human", "inferred")),
                                                 ("agent", ("agent", "declared"))])
def test_the_call_s_origin_is_in_the_ledger_the_report_and_the_answer(tmp_path, actor, expected):
    """OQ14: a migration an agent calls (AELIX_ANALYTICS_ACTOR=agent, an aelix agent's bash) is taken
    and recorded as the agent's, declared; a person's call without the variable is `human`,
    inferred. Both through the command line (means `cli`), in the ledger's start, the report and
    every answer — the dry run's, the commit's and a confirmation's — with the session when there
    is one."""
    source = plain_source(tmp_path)
    dest = tmp_path / "new"
    said = dict(zip(("actor", "actor_basis", "means"), (*expected, "cli"), strict=True))
    code, dry = command("migrate", "--to", dest, "--dry-run", cwd=source, actor=actor)
    assert code == 0 and dry["dry_run"] and {k: dry.get(k) for k in said} == said, dry
    assert "session" not in dry
    code, result = command("migrate", "--to", dest, cwd=source, actor=actor)
    assert code == 0, result
    assert {k: result.get(k) for k in said} == said and "session" not in result, result
    start = ledger(dest).start
    assert (start["origin"]["actor"], start["origin"]["actor_basis"], start["origin"]["means"]) == (
        *expected, "cli")
    report = json.loads((mdir(dest) / "report.json").read_text(encoding="utf-8"))
    assert (report["actor"], report["actor_basis"], report["means"]) == (*expected, "cli")
    code, again = command("migrate", "--to", dest, cwd=source, actor=actor,
                          AELIX_ANALYTICS_SESSION="session-1")
    assert code == 0 and again["already_migrated"] is True, again
    assert {k: again.get(k) for k in (*said, "session")} == {**said, "session": "session-1"}


def test_a_means_outside_the_list_is_refused_before_anything(tmp_path):
    source = plain_source(tmp_path)
    before = source_tree(source)
    code, result = command("migrate", "--to", tmp_path / "new", cwd=source,
                           AELIX_ANALYTICS_MEANS="bash")
    assert (code, result["error"]["code"]) == (1, "bad_means")
    assert not (tmp_path / "new").exists() and source_tree(source) == before


# --- the command itself ----------------------------------------------------------------------------

def test_the_command_runs_on_the_standard_library_only(tmp_path):
    """design 6.1: the launchers start it outside any project environment (uv run --no-project),
    where the analysis libraries are not: it imports none of them, as init does not."""
    source = plain_source(tmp_path)
    code = ("import json, sys\nfrom aelix_analytics.__main__ import main\n"
            f"status = main(['migrate', '--to', {str(tmp_path / 'new')!r}])\n"
            "heavy = sorted(m for m in ('duckdb', 'polars', 'pyarrow', 'pandas', 'numpy', 'matplotlib')"
            " if m in sys.modules)\n"
            "print(json.dumps({'status': status, 'heavy': heavy}))\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=source, capture_output=True, env=kit_env(),
                          timeout=300, **_platform.text_kwargs())
    last = json.loads(done.stdout.strip().split("\n")[-1])
    assert last == {"status": 0, "heavy": []}, (done.stdout, done.stderr)


@pytest.mark.parametrize("args", [["migrate"], ["migrate", "--dry"], ["migrate", "--t", "x"],
                                  ["migrate", "--to", "x", "--force"], ["migrate", "--to"]])
def test_a_call_the_command_does_not_take_is_a_usage_error(tmp_path, args):
    """No destination, an abbreviated option (the launchers read --to and --dry-run as given: an
    abbreviation they do not know must not reach them), an option it has not: usage and 2, nothing
    written."""
    source = plain_source(tmp_path)
    before = tree(tmp_path)
    env = kit_env()
    done = subprocess.run([sys.executable, "-m", "aelix_analytics", *args], cwd=source, env=env,
                          capture_output=True, timeout=120, **_platform.text_kwargs())
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert tree(tmp_path) == before


# --- the destination judged again, through the command and its dry run (T21) ---------------------

MARKED = [
    ({"analytics.json": b"{", "lab.json": b"{}"}, "marker_conflict"),
    ({"analytics.json": b"[1]"}, "marker_invalid"),
    ({"analytics.json": json.dumps({"schema": "analytics.project/3"}).encode()}, "project_too_new"),
    ({"lab.json": b"{}"}, "destination_invalid"),
]


@pytest.mark.parametrize(("markers", "code"), MARKED)
def test_a_destination_with_a_marker_is_refused_by_it_and_nothing_changes(tmp_path, markers, code):
    source = plain_source(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    for name, data in markers.items():
        (dest / name).write_bytes(data)
    trust, before, source_before = trust_bytes(), tree(dest), source_tree(source)
    for extra in ([], ["--dry-run"]):
        status, result = command("migrate", "--to", dest, *extra, cwd=source)
        assert (status, result["error"]["code"]) == (1, code), result
    assert tree(dest) == before and source_tree(source) == source_before and trust_bytes() == trust


def committed(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A lab.json project with a folder outside it, and its destination committed by the command."""
    source, ext = linked_source(tmp_path)
    dest = tmp_path / "dest"
    code, result = command("migrate", "--to", dest, cwd=source)
    assert code == 0, result
    return source, ext, dest


def test_another_source_s_committed_destination_is_not_empty(tmp_path):
    _, _, dest = committed(tmp_path)
    other = plain_source(tmp_path, "other")
    trust, before = trust_bytes(), tree(dest)
    for extra in ([], ["--dry-run"]):
        code, result = command("migrate", "--to", dest, *extra, cwd=other)
        assert code == 1 and result["error"]["details"]["reason"] == "not_empty", result
    assert tree(dest) == before and trust_bytes() == trust


@pytest.mark.parametrize("case", ["as_it_is", "untrusted", "source_gone", "outside_gone", "short_disk"])
def test_the_same_source_s_committed_destination_is_already_migrated(tmp_path, case):
    """T21: confirmed — given or not, the source gone, its folder outside gone, the disk short — and
    nothing written but the trust entry a confirmation adds where it is missing (the dry run says it
    would); the destination and the source as they were."""
    source, ext, dest = committed(tmp_path)
    cwd = source
    if case == "untrusted":
        Path(os.environ["AELIX_ANALYTICS_TRUST_FILE"]).write_text(
            json.dumps({"schema": "analytics.trust/1", "projects": []}), encoding="utf-8")
    elif case == "source_gone":
        shutil.rmtree(source)
        cwd = dest  # from inside the destination: the call has no source
    elif case == "outside_gone":
        shutil.rmtree(ext)
    trust, before = trust_bytes(), tree(dest)
    source_before = source_tree(source) if source.exists() else None
    if case == "short_disk":
        run = ("import sys\nfrom aelix_analytics import migration\n"
               "migration.Migration._free = lambda self, existing: 1\n"
               "from aelix_analytics.__main__ import main\n"
               f"sys.exit(main(['migrate', '--to', {str(dest)!r}] + sys.argv[1:]))\n")
        results = []
        for extra in (["--dry-run"], []):
            done = subprocess.run([sys.executable, "-c", run, *extra], cwd=cwd, env=kit_env(),
                                  capture_output=True, timeout=300, **_platform.text_kwargs())
            results.append((done.returncode, json.loads(done.stdout.strip().split("\n")[-1])))
    else:
        results = [command("migrate", "--to", dest, *extra, cwd=cwd) for extra in (["--dry-run"], [])]
    (dry_code, dry), (code, result) = results
    assert dry_code == 0 and dry["already_migrated"] and dry["state"] == "committed", dry
    assert code == 0 and result["already_migrated"], result
    assert tree(dest) == before
    if source_before is not None:
        assert source_tree(source) == source_before
    if case == "untrusted":
        assert dry["todo"] == ["add the destination to the trust list"]
        assert result["trusted"] and core.is_trusted(dest) and trust_bytes() != trust
    else:
        assert dry["todo"] == [] and trust_bytes() == trust
    if case == "source_gone":
        assert result["source"]["given"] == "not_given"
        assert result["source_since"]["source"] == "not_given"


def test_a_marker_a_newer_kit_rewrote_before_the_commit_line_is_taken_by_the_command(tmp_path):
    """T21 `marker_rewritten`: published, its `committed` line not written (F12), then the marker's
    min_writer_version raised by a newer kit: confirmed with the warning, the line written; the dry
    run says it and writes nothing."""
    source = plain_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"step": "marker_published"})
    marker = json.loads((dest / "analytics.json").read_text(encoding="utf-8"))
    (dest / "analytics.json").write_text(json.dumps({**marker, "min_writer_version": "0.9.0"}),
                                         encoding="utf-8")
    before = tree(dest)
    code, dry = command("migrate", "--to", dest, "--dry-run", cwd=source)
    assert code == 0 and dry["warnings"] == ["marker_rewritten"]
    assert "write the committed line" in dry["todo"] and tree(dest) == before
    code, result = command("migrate", "--to", dest, cwd=source)
    assert code == 0 and result["already_migrated"] and result["marker_warnings"] == ["marker_rewritten"]
    assert ledger(dest).committed


@pytest.mark.skipif(not POSIX, reason="Windows publishes by a rename: no second name between a link "
                                      "and an unlink")
def test_the_marker_s_temporary_name_left_beside_it_is_removed_by_the_command(tmp_path):
    """T21: the marker's temporary file is the marker itself (F12 between the link and the unlink):
    the dry run names its removal and leaves it, the call removes it and confirms."""
    source = plain_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, {"between_link_and_unlink": True, "path": "analytics.json"})
    [temp] = ledger(dest).marker_temps
    assert os.path.samefile(dest / temp, dest / "analytics.json")
    before = tree(dest)
    code, dry = command("migrate", "--to", dest, "--dry-run", cwd=source)
    assert code == 0 and "remove the marker's temporary file" in dry["todo"] and tree(dest) == before
    code, result = command("migrate", "--to", dest, cwd=source)
    assert code == 0 and result["already_migrated"] and not (dest / temp).exists()

    def but_the_temp_and_the_ledger(entries: dict) -> dict:
        return {k: v for k, v in entries.items() if k != temp and not k.endswith("progress.jsonl")}

    assert but_the_temp_and_the_ledger(tree(dest)) == but_the_temp_and_the_ledger(before)
    assert ledger(dest).committed


def test_the_marker_where_the_file_system_has_neither_link_nor_rename_is_refused_by_the_command(
        tmp_path):
    """The owner's decision of 2026-10-08 through the command: stopped at the probe, before any copy
    (destination_invalid, no_exclusive_rename, exit 1); the source as it was."""
    source = plain_source(tmp_path)
    before = source_tree(source)
    dest = tmp_path / "dest"
    error = interrupt(source, dest, {}, no_links=True, no_renames=True, code=0)
    assert error["error"]["details"]["reason"] == "no_exclusive_rename"
    # The kit's own command sees the destination as not committed, and the call made again where
    # the rename is there goes on from the probe.
    code, result = command("status", cwd=dest)
    assert (code, result["error"]["code"]) == (1, "migration_incomplete")
    code, result = command("migrate", "--to", dest, cwd=source)
    assert code == 0 and not result["already_migrated"], result
    assert source_tree(source) == before
    assert ledger(dest).probed["publish"] in migration.PUBLICATIONS


# --- a file system of another kind, where a test machine has one mounted (opt-in) -----------------

FOREIGN = [p for p in os.environ.get("AELIX_ANALYTICS_TEST_FOREIGN_FS", "").split(os.pathsep) if p]


@pytest.mark.skipif(not FOREIGN, reason="AELIX_ANALYTICS_TEST_FOREIGN_FS names no folder on another "
                                        "file system (a FAT or exFAT volume, say)")
@pytest.mark.parametrize("where", FOREIGN or ["-"])
def test_a_migration_onto_a_file_system_of_another_kind(tmp_path, where):
    """On a mounted volume of another file system (FAT, exFAT: AELIX_ANALYTICS_TEST_FOREIGN_FS, folders
    separated as PATH is). The port there: a taken name is never replaced by rename_new, and a free
    one is renamed or RenameUnsupported. The migration there commits with its marker published by a
    link or by that rename — never written in place — and a second call finds it migrated, or it
    stops at the probe without a marker (no_exclusive_rename): never at the AppleDouble `._` files
    macOS writes beside every file it makes on such a volume, which are the OS's (the owner's
    decision of 2026-10-09; before it, every such migration stopped at step 1, not_empty). Which of
    the two the volume gave is recorded."""
    from conftest import record_observation

    files = _platform.detect().files
    probe = Path(where) / f"aelix-port-{os.getpid()}-{tmp_path.name}"
    probe.mkdir()
    try:
        (probe / "a.tmp").write_bytes(b"A")
        (probe / "taken.json").write_bytes(b"T")
        with pytest.raises(OSError) as raised:
            files.rename_new(probe / "a.tmp", probe / "taken.json")
        assert isinstance(raised.value, (FileExistsError, _platform.RenameUnsupported)), raised.value
        assert (probe / "taken.json").read_bytes() == b"T" and (probe / "a.tmp").read_bytes() == b"A"
        try:
            files.rename_new(probe / "a.tmp", probe / "free.json")
            port = "renamed"
            assert (probe / "free.json").read_bytes() == b"A" and not (probe / "a.tmp").exists()
        except _platform.RenameUnsupported as exc:
            port = f"RenameUnsupported {exc.errno}"
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    source = plain_source(tmp_path)
    before = source_tree(source)
    dest = Path(where) / f"aelix-migrate-{os.getpid()}-{tmp_path.name}"
    try:
        code, result = command("migrate", "--to", dest, cwd=source)
        seen = {"rename_new": port, "exit": code,
                "result": "committed" if code == 0 else result["error"]["code"],
                "reason": None if code == 0 else (result["error"].get("details") or {}).get("reason")}
        directories = [d for d in (dest / migration.MIGRATIONS).glob("*") if not d.name.startswith("._")] \
            if (dest / migration.MIGRATIONS).is_dir() else []
        if directories:
            read = migration.Ledger.read(directories[0])
            assert writer.MARKER not in read.direct_intent, "the marker was written in place"
            seen["publish"] = (read.probed or {}).get("publish")
        if code == 0:
            assert writer.marker_state(dest).state == "current"
            code, again = command("migrate", "--to", dest, cwd=source)
            assert code == 0 and again["already_migrated"] and again["differs"] == [], again
            seen["again"] = "already_migrated"
        else:
            assert (result["error"]["code"], seen["reason"]) == ("destination_invalid", "no_exclusive_rename"), result
            assert not (dest / writer.MARKER).exists(), result
        seen["appledouble"] = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("._*"))[:20]
        assert source_tree(source) == before
        record_observation(f"issue-13-writer/foreign-fs-{Path(where).name}", seen)
    finally:
        shutil.rmtree(dest, ignore_errors=True)


# --- the launchers with a real uv (T18, and T3 without an environment) -----------------------------

REAL_UV = os.environ.get("AELIX_ANALYTICS_UV") or shutil.which("uv")
LAUNCHERS = ["launcher", pytest.param("bin/analytics", marks=pytest.mark.skipif(
    not POSIX, reason="bin/analytics is a POSIX shell script"))]
PYPROJECT = (b"[project]\nname = \"t18\"\nversion = \"0.0.0\"\nrequires-python = \">=3.12\"\n"
             b"dependencies = []\n\n[tool.uv]\npackage = false\n")


def launcher(which: str, *args, cwd: Path, **extra) -> subprocess.CompletedProcess:
    """`analytics ARGS` (the installed command's code, this checkout's kit) or bin/analytics, with the
    real uv, as a person's terminal runs it; `extra` is added to its environment."""
    from test_launcher import BIN, RUN_LAUNCHER

    env = kit_env(AELIX_ANALYTICS_UV=REAL_UV, **extra)
    for name in ("AELIX_ANALYTICS_ACTOR", "AELIX_ANALYTICS_PROJECT", "PYTHONPATH"):
        env.pop(name, None)
    argv = ["sh", str(BIN), *args] if which == "bin/analytics" else [sys.executable, "-c", RUN_LAUNCHER, *args]
    if which == "launcher":
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=600)


def uv_source(base: Path, name: str = "src", *, fits: bool = True) -> Path:
    """A lab.json project with an environment as an older kit's leaves one: a pyproject without
    dependencies, its uv.lock, and a .venv/ uv made — the lock made to fit, or then made not to (a
    dependency added after it)."""
    source = plain_source(base, name)
    (source / "pyproject.toml").write_bytes(PYPROJECT)
    subprocess.run([REAL_UV, "lock", "--project", str(source), "--quiet"], check=True, timeout=300)
    subprocess.run([REAL_UV, "venv", "--python", "3.12", "--quiet", str(source / ".venv")], check=True,
                   timeout=300)
    if not fits:
        (source / "pyproject.toml").write_bytes(PYPROJECT.replace(b"dependencies = []",
                                                                  b'dependencies = ["idna"]'))
    return source


@pytest.mark.skipif(REAL_UV is None, reason="uv is not installed")
@pytest.mark.parametrize("which", LAUNCHERS)
@pytest.mark.parametrize("fits", [True, False])
def test_the_launchers_migrate_without_touching_the_source_s_environment(tmp_path, which, fits):
    """T18 (design 6.1): the launchers' migrate runs the kit outside any environment, so the source's
    .venv/ and uv.lock are as they were (listed and hashed, the whole source compared); after the
    commit `uv sync --locked` prepares the destination from its lock as it is: a lock that fits is
    not rewritten and the environment is made, one that does not fit is not rewritten either — the
    sync fails, the migration stays done (exit 0) and the command says what failed."""
    source = uv_source(tmp_path, fits=fits)
    before = source_tree(source)
    lock = (source / "uv.lock").read_bytes()
    dest = tmp_path / "dest"
    done = launcher(which, "migrate", "--to", str(dest), cwd=source)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert json.loads(done.stdout.decode("utf-8").strip().split("\n")[-1])["ok"] is True
    assert source_tree(source) == before, "S1: the source's environment or lock changed"
    assert (dest / "uv.lock").read_bytes() == lock, "the destination's lock was rewritten"
    assert_committed(dest, source)
    stderr = done.stderr.decode("utf-8")
    if fits:
        assert (dest / ".venv" / "pyvenv.cfg").is_file()
        assert f"the project is {dest.resolve()} now" in stderr
    else:
        assert "the migration is done, but preparing the environment of" in stderr
    record_t18(which, fits, done.returncode, dest, stderr)


def moved_home(tmp_path: Path) -> tuple[Path, dict]:
    """A home of the test's own for `~`, with uv's cache and its Pythons still the caller's (a moved
    HOME would otherwise start both empty)."""
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "USERPROFILE": str(home)}
    for name, ask in (("UV_CACHE_DIR", ["cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["python", "dir"])):
        env[name] = os.environ.get(name) or subprocess.run(
            [REAL_UV, *ask], check=True, capture_output=True, timeout=60,
            **_platform.text_kwargs()).stdout.strip()
    return home, env


def extra_package(venv: Path) -> None:
    """A package installed in `venv` that its project's lock does not have, as a person adds one:
    an exact `uv sync` of that project would remove it."""
    site = next(venv.glob("lib/python3*/site-packages"), None) or venv / "Lib" / "site-packages"
    (site / "t18extra.py").write_text("VALUE = 1\n", encoding="utf-8")
    info = site / "t18extra-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: t18extra\nVersion: 1.0\n",
                                   encoding="utf-8")
    (info / "INSTALLER").write_text("pip\n", encoding="utf-8")
    (info / "RECORD").write_text("t18extra.py,,\nt18extra-1.0.dist-info/METADATA,,\n"
                                 "t18extra-1.0.dist-info/INSTALLER,,\nt18extra-1.0.dist-info/RECORD,,\n",
                                 encoding="utf-8")


@pytest.mark.skipif(REAL_UV is None, reason="uv is not installed")
@pytest.mark.parametrize("which", LAUNCHERS)
@pytest.mark.parametrize("given", [["--to=~/x"], ["--to", "~/x"]])
@pytest.mark.parametrize("venv", ["none", "extra package"])
def test_a_destination_given_with_a_tilde_is_prepared_where_the_kit_made_it(tmp_path, which, given,
                                                                             venv):
    """T18 with `~` (review of PR 3): `--to=~/x` reaches the launchers as it is in every POSIX shell,
    and so does a quoted '~/x' or one cmd.exe passes. The kit makes the destination in the home; the
    launchers prepare the environment of the folder the kit's answer names, never `<cwd>/~/x`, below
    which uv would find the source's pyproject and sync the source — making a .venv/ where it had
    none, or removing a package a person added to it. The source's .venv/ and uv.lock stay byte for
    byte as they were."""
    source = uv_source(tmp_path)
    if venv == "none":
        shutil.rmtree(source / ".venv")
    else:
        extra_package(source / ".venv")
    before = source_tree(source)
    home, env = moved_home(tmp_path)
    done = launcher(which, "migrate", *given, cwd=source, **env)
    assert done.returncode == 0, (done.stdout, done.stderr)
    dest = home / "x"
    answer = json.loads(done.stdout.decode("utf-8").strip().split("\n")[-1])
    assert answer["ok"] is True and answer["destination"] == str(dest.resolve()), answer
    assert source_tree(source) == before, "S1: the source's environment or lock changed"
    assert not (source / "~").exists()
    assert (source / ".venv").exists() == (venv != "none")
    assert_committed(dest, source)
    assert (dest / ".venv" / "pyvenv.cfg").is_file()
    stderr = done.stderr.decode("utf-8")
    assert stderr.endswith(f"the project is {dest.resolve()} now, and the source is as it was "
                           f"(in aelix: /analytics use {dest.resolve()})\n"), stderr
    from conftest import record_observation

    record_observation(f"issue-13-writer/uv-tilde-{which.replace('/', '-')}-{'equals' if len(given) == 1 else 'apart'}-"
                       f"{venv.replace(' ', '-')}",
                       {"argv": ["migrate", *given], "exit": done.returncode,
                        "destination": "<home>/x", "destination_venv": True, "source_unchanged": True,
                        "literal_tilde_folder": False,
                        "stderr_last_line": stderr.strip().split("\n")[-1].replace(str(dest.resolve()), "<dest>")})


def record_t18(which: str, fits: bool, code: int, dest: Path, stderr: str) -> None:
    from conftest import record_observation

    record_observation(f"issue-13-writer/uv-{which.replace('/', '-')}-{'fits' if fits else 'does-not-fit'}",
                       {"exit": code, "destination_venv": (dest / ".venv").is_dir(),
                        "lock_unchanged": True, "source_unchanged": True,
                        "stderr_last_line": stderr.strip().split("\n")[-1].replace(str(dest.resolve()),
                                                                                   "<dest>")})


@pytest.mark.skipif(REAL_UV is None, reason="uv is not installed")
@pytest.mark.parametrize("which", LAUNCHERS)
@pytest.mark.parametrize("stop", [{"step": "migration_dir"}, {"step": "copied", "nth": 2},
                                  {"step": "verified"}, {"step": "marker_written"}])
def test_the_launchers_make_no_environment_in_a_destination_not_committed(tmp_path, which, stop):
    """T3 (③) with a real uv: in a destination a migration left before its marker — at F1, F6, F8
    and F11 — the launchers' status, record and open each give the kit's migration_incomplete and
    1, and no `.venv/` is made there (`uv run --project` would make one before the kit refused); the
    destination is as it was."""
    source = uv_source(tmp_path)
    dest = tmp_path / "dest"
    interrupt(source, dest, stop)
    assert_uncommitted(dest)
    before = tree(dest)
    for args in (["status"], ["record", "finding", "x"], ["open"]):
        done = launcher(which, *args, cwd=dest)
        assert done.returncode == 1, (args, done.stdout, done.stderr)
        assert json.loads(done.stdout.decode("utf-8").strip().split("\n")[-1])["error"]["code"] == \
            "migration_incomplete", (args, done.stdout)
        assert not (dest / ".venv").exists(), args
    assert tree(dest) == before
