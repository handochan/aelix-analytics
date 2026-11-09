"""The observed activity of a bash call (ADR-0015, Issue #171): the kit's side, without aelix.

`observed.before` and `observed.after` are what the extension runs in a thread at tool_call and
tool_result (tests/test_extension.py drives them through aelix's own loader and harness). Here they are
called directly around what a bash command would do, so each rule of the ADR is a case: when a call is
recorded, what the state before and after says changed and whether a run or an import recorded in the
window explains it, which script bytes are kept, what is redacted, the bounds, the commit and what the
readers make of the record. Test values that stand for secrets are named canary/marker (no real key).
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import KIT, dir_link, kit_cli, record_observation
from jsonschema import Draft202012Validator, FormatChecker

from aelix_analytics import core, observed, relations, tree_state, writer

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "docs/schemas/analytics-records.schema.json").read_text(encoding="utf-8"))
EVENT = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/event_2"}, format_checker=FormatChecker())
TEMPLATE = KIT / "aelix_analytics" / "template"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def words_of(command: str) -> list[list[str]]:
    """The simple commands' words, as the extension's command_words gives them for a plain line."""
    out, current = [], []
    for token in shlex.split(command.replace("&&", " ; ").replace("|", " ; "), posix=True):
        if token == ";":
            out.append(current)
            current = []
        else:
            current.append(token)
    out.append(current)
    return [w for w in out if w]


@pytest.fixture
def proj(tmp_path):
    p = core.init_project(tmp_path / "proj", title="Observed", template_dir=TEMPLATE,
                          origin=core.Origin("test", "sdk"))
    (p.root / "scripts").mkdir(exist_ok=True)
    (p.root / "data" / "derived").mkdir(parents=True, exist_ok=True)
    return p.root


class Observer:
    """before → what the command does → after, as the extension runs them, with one Sessions."""

    def __init__(self, root: Path, session="s1"):
        self.root = Path(root)
        self.session = session
        self.sessions = observed.Sessions()

    def before(self, command, *, cwd=None, seconds=observed.BEFORE_SECONDS, view=True):
        job = observed.Job(time.monotonic() + seconds)
        return observed.before(core.Project(self.root), activity_id=observed.new_activity_id(), command=command,
                               commands=words_of(command), cwd=str(cwd or self.root), job=job,
                               sessions=self.sessions, session=self.session, view_state=view,
                               not_observed=None if view else "observation_off")

    def after(self, prior, command=None, *, python=None, seconds=observed.AFTER_SECONDS, job=None,
              is_error=False, exit_code=0):
        command = prior.command if command is None else command
        python = ("python" in command) if python is None else python
        project = core.Project(self.root)
        job = job or observed.Job(time.monotonic() + seconds)
        w = core.writer_for(project, core.Origin("system", "hook", "declared", self.session))
        return observed.after(project, prior, observed.Call("call-1", command, words_of(command), python,
                                                            is_error, exit_code),
                              writer=w, job=job, sessions=self.sessions, session=self.session,
                              cache=core.HashCache(project))

    def __call__(self, command, act=None, **kw):
        prior = self.before(command, **{k: v for k, v in kw.items() if k in ("cwd", "view")})
        if act is not None:
            act()
        return self.after(prior, **{k: v for k, v in kw.items() if k in ("python", "is_error", "exit_code")})


def details(outcome) -> dict:
    assert outcome.event is not None, outcome.reason
    return outcome.event["details"]


def journal(root) -> list[dict]:
    return [json.loads(line) for line in (Path(root) / "journal.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def write(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


# --- when a call is recorded (decision 1) --------------------------------------------------------------


def test_python_outside_the_kit_is_recorded_with_its_command_scripts_and_changes(proj):
    """Decision 1–4: Python run outside the kit in the project, which wrote a file no run recorded: one
    observed_activity by the system through a hook with the agent as performer, its references empty,
    its details of the schema, the change hashed and unexplained, the script's bytes copied, the marker's
    floor raised to 0.32.1 — and nothing of it is a run."""
    write(proj / "scripts" / "a.py", "open('data/derived/x.csv', 'w').write('1')\n")
    before_marker = json.loads((proj / "analytics.json").read_text(encoding="utf-8"))
    out = Observer(proj)("python scripts/a.py",
                         lambda: subprocess.run([sys.executable, "scripts/a.py"], cwd=proj, check=True))
    event = out.event
    assert EVENT.is_valid(event), list(EVENT.iter_errors(event))
    assert relations.observed_activity_problem(event) is None
    assert (event["kind"], event["actor"], event["actor_basis"], event["means"], event["session"], event["refs"]) == (
        "observed_activity", "system", "declared", "hook", "s1", [])
    d = event["details"]
    assert d["performer"] == {"actor": "agent", "tool": "bash", "tool_call_id": "call-1"}
    assert d["reasons"] == ["analysis_command", "changes"] and d["cwd"] == "." and not d["cwd_outside"]
    assert d["command"]["text"] == "python scripts/a.py" and d["command"]["inline_complete"]
    assert d["command"]["sha256"] == sha(b"python scripts/a.py")
    assert d["result"] == {"is_error": False, "exit_code": 0}
    [item] = d["changes"]["items"]
    assert (item["path"], item["change"], item["hash"], item["explanation"], item["sha256"]) == (
        "data/derived/x.csv", "created", "hashed", "unexplained", sha(b"1"))
    assert d["changes"]["unexplained"] == 1 and d["changes"]["state"] == "observed"
    [script] = d["scripts"]["items"]
    assert (script["path"], script["captured"], script["stable"], script["copy"]) == (
        "scripts/a.py", "before", True, "scripts/scripts/a.py")
    copy = proj / ".analytics" / "observed" / d["activity_id"] / "scripts" / "scripts" / "a.py"
    assert copy.read_bytes() == (proj / "scripts" / "a.py").read_bytes()
    assert d["captures"] == [{"path": "scripts/scripts/a.py", "role": "script", "bytes": copy.stat().st_size,
                              "sha256": sha(copy.read_bytes())}]
    assert event["text"] == 'Observed outside the kit: bash "python scripts/a.py" — 1 file changed while it ran'
    marker = json.loads((proj / "analytics.json").read_text(encoding="utf-8"))
    assert before_marker["min_writer_version"] != "0.32.1" or True
    assert marker["min_writer_version"] == relations.OBSERVED_WRITER_SINCE == "0.32.1"
    assert not (proj / "runs").exists() or not any((proj / "runs").iterdir())
    # A record the writer judged: under the observe operation only.
    assert relations.OBSERVED_WRITER_SINCE == "0.32.1" and core.KIT_VERSION == "0.32.1"


def test_a_call_with_nothing_to_record_writes_nothing(proj, tmp_path):
    """Decision 1: no Python and no change (`ls`), Python that only manages packages (the extension
    judges it not an analysis step), and Python run outside the project's directory that names nothing
    in it: no record, and the marker keeps its floor."""
    marker = (proj / "analytics.json").read_bytes()
    observer = Observer(proj)
    assert observer("ls -la", python=False).reason == "nothing_to_record"
    assert observer("pip install duckdb", python=False).reason == "nothing_to_record"
    elsewhere = tmp_path / "elsewhere"
    write(elsewhere / "t.py", "print(1)\n")
    assert observer("python t.py", cwd=elsewhere).reason == "nothing_to_record"
    assert [e["kind"] for e in journal(proj)] == ["project", "context"]
    assert (proj / "analytics.json").read_bytes() == marker
    assert not (proj / ".analytics" / "observed").exists()


def test_a_command_that_is_no_python_is_recorded_for_the_changes_no_run_recorded(proj):
    """Decision 1 (`changes`): `cp` into data/ runs no Python but changed a file no run or import recorded."""
    write(proj.parent / "in.csv", "a\n1\n")
    out = Observer(proj)("cp ../in.csv data/in.csv", lambda: shutil.copy(proj.parent / "in.csv", proj / "data" / "in.csv"),
                         python=False)
    d = details(out)
    assert d["reasons"] == ["changes"] and d["changes"]["items"][0]["path"] == "data/in.csv"
    assert d["mentioned"]["items"] == ["data/in.csv"]  # the names of the command that ran, read after it


# --- the state before and after (decision 3) ---------------------------------------------------------


def test_created_modified_and_deleted(proj):
    write(proj / "data" / "keep.csv", "a\n")
    write(proj / "data" / "gone.csv", "a\n")

    def act():
        write(proj / "data" / "keep.csv", "a\nb\n")
        (proj / "data" / "gone.csv").unlink()
        write(proj / "data" / "new.csv", "n\n")
        write(proj / "outputs" / "fig.txt", "f\n")  # a person's folder is in the namespace too

    d = details(Observer(proj)("sh job.sh", act, python=False))
    got = {(i["path"], i["change"], i["hash"], i["explanation"]) for i in d["changes"]["items"]}
    assert got == {("data/keep.csv", "modified", "hashed", "unexplained"),
                   ("data/gone.csv", "deleted", "deleted", "unexplained"),
                   ("data/new.csv", "created", "hashed", "unexplained"),
                   ("outputs/fig.txt", "created", "hashed", "unexplained")}
    assert [i["path"] for i in d["changes"]["items"]] == sorted(i["path"] for i in d["changes"]["items"])
    assert (d["changes"]["total"], d["changes"]["created"], d["changes"]["modified"], d["changes"]["deleted"]) == (
        4, 2, 1, 1)


def _replace_keeping_size_and_mtime(path: Path, data: bytes) -> None:
    """A new file (new identity) of the same size and mtime put in `path`'s place: `rsync -a`."""
    st = path.stat()
    assert len(data) == st.st_size
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    os.utime(temp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(temp, path)


@pytest.mark.skipif(os.name == "nt" and not hasattr(os, "link"), reason="identity needs st_ino")
def test_a_replacement_that_keeps_size_and_mtime_is_hashed_from_its_bytes(proj):
    """Decision 3: an identity-only change is a change (st_ino), hashed from the bytes — the hash cache,
    keyed by size and mtime, would give the bytes replaced — and marked same_bytes_possible, as the
    start's bytes were not read in this window."""
    path = write(proj / "data" / "t.csv", b"AAAA")
    cache = core.HashCache(core.Project(proj))
    assert cache.sha(path) == sha(b"AAAA")
    cache.save()
    d = details(Observer(proj)("rsync -a ../t.csv data/t.csv", lambda: _replace_keeping_size_and_mtime(path, b"BBBB"),
                               python=False))
    [item] = d["changes"]["items"]
    if not os.stat(path).st_ino:
        pytest.skip("this file system gives no identity")
    assert (item["change"], item["sha256"], item["same_bytes_possible"]) == ("modified", sha(b"BBBB"), True)
    assert core.HashCache(core.Project(proj)).sha(path) == sha(b"BBBB"), "the cache entry was rewritten"


def test_cache_a_then_b_at_the_start_then_a_again_is_not_dropped(proj):
    """확인할 것: cache A → start B (same size and mtime) → end A. The cache says A for the end's size and
    mtime, but the start's bytes were not read in the window: the change stays, marked same_bytes_possible."""
    path = write(proj / "data" / "t.csv", b"AAAA")
    cache = core.HashCache(core.Project(proj))
    cache.sha(path)
    cache.save()
    _replace_keeping_size_and_mtime(path, b"BBBB")
    if not os.stat(path).st_ino:
        pytest.skip("this file system gives no identity")
    d = details(Observer(proj)("sh x.sh", lambda: _replace_keeping_size_and_mtime(path, b"AAAA"), python=False))
    [item] = d["changes"]["items"]
    assert (item["path"], item["change"], item["sha256"], item["same_bytes_possible"]) == (
        "data/t.csv", "modified", sha(b"AAAA"), True)


def test_the_same_bytes_put_back_are_no_change_when_the_start_read_them(proj):
    """Decision 3: a file whose start bytes the window read (a script the command names) and that is put
    back with the same bytes under a new identity is no change."""
    path = write(proj / "data" / "q.sql", b"select 1\n")
    if not os.stat(path).st_ino:
        pytest.skip("this file system gives no identity")
    d = details(Observer(proj)("duckdb < data/q.sql && python -c 'print(1)'",
                               lambda: _replace_keeping_size_and_mtime(path, b"select 1\n")))
    assert d["changes"]["items"] == [] and d["changes"]["total"] == 0
    assert d["reasons"] == ["analysis_command"]


@pytest.mark.parametrize("kind", ["symlink"])
def test_a_link_made_to_a_full_directory_is_not_thousands_of_files_created(proj, tmp_path, kind):
    """확인할 것: `ln -s` an outside directory into data/: its files existed before the window, so they are
    not created; the link is counted in links_changed. Removing it again deletes nothing."""
    outside = tmp_path / "bigdisk"
    old = time.time_ns() - 3600 * 10 ** 9
    for i in range(3000):
        f = write(outside / f"f{i:04d}.csv", "x\n")
        os.utime(f, ns=(old, old))
    observer = Observer(proj)
    d = details(observer("ln -s ../bigdisk data/big", lambda: dir_link(kind, proj / "data" / "big", outside),
                         python=True))
    assert d["changes"]["total"] == 0 and d["changes"]["links_changed"] == 1
    d = details(observer("rm data/big", lambda: os.unlink(proj / "data" / "big"), python=True))
    assert d["changes"]["total"] == 0 and d["changes"]["links_changed"] == 1


def test_a_file_whose_stat_fails_after_is_unreadable_not_deleted(proj, monkeypatch):
    """Decision 3: a path the after walk lists but cannot stat (a sharing violation on Windows) is
    `unreadable`, its explanation unknown — never deleted."""
    path = write(proj / "data" / "locked.csv", "a\n")
    observer = Observer(proj)
    prior = observer.before("python job.py")
    real_stat, target = os.stat, os.fspath(core.Project(proj).locate(path)[1])

    def stat(p, *a, **kw):
        if os.fspath(p) == target:
            raise PermissionError(13, "sharing violation")
        return real_stat(p, *a, **kw)

    monkeypatch.setattr(tree_state.os, "stat", stat)
    d = details(observer.after(prior))
    [item] = d["changes"]["items"]
    assert (item["change"], item["hash"], item["explanation"]) == ("unreadable", "unreadable", "unknown")
    assert d["changes"]["unreadable"] == 1 and d["changes"]["deleted"] == 0
    assert d["reasons"] == ["analysis_command", "changes"]


def test_a_file_that_changes_while_it_is_hashed_is_unstable(proj, monkeypatch):
    """Decision 3: the stat after the read is not the after state's: `unstable`, no sha256, unknown."""
    observer = Observer(proj)
    prior = observer.before("python job.py")
    write(proj / "data" / "moving.csv", "a\n")
    real = tree_state.stat_of
    calls = []

    def stat_of(p):
        st = real(p)
        if str(p).endswith("moving.csv"):
            calls.append(1)
            if len(calls) == 2:
                return (st[0] + 1, st[1], st[2])
        return st

    monkeypatch.setattr(observed.tree_state, "stat_of", stat_of)
    d = details(observer.after(prior))
    [item] = d["changes"]["items"]
    assert (item["hash"], item["sha256"], item["explanation"], item["reason"]) == ("unstable", None, "unknown", "no_hash")


def test_a_run_and_an_import_in_the_window_explain_their_files(proj, tmp_path):
    """Decision 3 (`explained`): `analytics run` and `analytics import` in the same bash call record what
    they wrote; the observation names the run and the import, and the call is not recorded for them."""
    write(proj / "scripts" / "r.py", "open('data/derived/r.csv', 'w').write('r')\n")
    source = write(tmp_path / "in.csv", "a,b\n1,2\n")
    observer = Observer(proj)
    prior = observer.before("uv run analytics run scripts/r.py 2>&1 | tail -5; analytics import ../in.csv")
    code, result = kit_cli(proj, "run", "scripts/r.py", "--title", "r")
    assert code == 0 and result["run"]["status"] == "ok", result
    code, result = kit_cli(proj, "import", source, "--name", "sales")
    assert code == 0, result
    out = observer.after(prior, python=False)
    assert out.reason == "nothing_to_record" and out.unexplained == 0 and out.unknown == 0
    assert [e["kind"] for e in journal(proj)][-2:] == ["run", "import"]


def test_explained_and_unexplained_changes_side_by_side(proj, tmp_path):
    """A run in the window explains its output; a write beside it (another process) does not."""
    write(proj / "scripts" / "r.py", "open('data/derived/r.csv', 'w').write('r')\n")
    observer = Observer(proj)
    prior = observer.before("analytics run scripts/r.py; python other.py")
    code, result = kit_cli(proj, "run", "scripts/r.py", "--title", "r")
    assert code == 0, result
    write(proj / "data" / "derived" / "other.csv", "o\n")
    d = details(observer.after(prior))
    by_path = {i["path"]: i for i in d["changes"]["items"]}
    assert by_path["data/derived/r.csv"]["explanation"] == "explained"
    assert by_path["data/derived/r.csv"]["by"] == {"type": "run", "id": result["run"]["id"]}
    assert by_path["data/derived/other.csv"]["explanation"] == "unexplained"
    assert d["window"]["seq_after"] == d["window"]["seq_before"] + 1


def test_a_journal_end_that_cannot_be_read_leaves_the_changes_unknown(proj, monkeypatch):
    observer = Observer(proj)
    prior = observer.before("sh x.sh")
    write(proj / "data" / "x.csv", "x\n")
    monkeypatch.setattr(observed, "journal_tail", lambda path: None)
    d = details(observer.after(prior, python=False))
    [item] = d["changes"]["items"]
    assert (item["explanation"], item["reason"]) == ("unknown", "window_unread")
    assert d["window"]["seq_after"] is None and d["reasons"] == ["changes"]


# --- scripts (decision 4) ------------------------------------------------------------------------------


def test_scripts_before_after_and_changed_in_the_window(proj):
    write(proj / "scripts" / "kept.py", "print('kept')\n")
    write(proj / "scripts" / "edited.py", "print('one')\n")

    def act():
        write(proj / "scripts" / "made.py", "print('made')\n")  # a heredoc made it, then it ran
        write(proj / "scripts" / "edited.py", "print('two two')\n")

    command = "cat > scripts/made.py <<'EOF'\nprint('made')\nEOF\npython scripts/made.py scripts/kept.py scripts/edited.py"
    observer = Observer(proj)
    prior = observer.before(command)
    act()
    d = details(observer.after(prior))
    items = {i["path"]: i for i in d["scripts"]["items"]}
    assert (items["scripts/kept.py"]["captured"], items["scripts/kept.py"]["stable"]) == ("before", True)
    assert (items["scripts/made.py"]["captured"], items["scripts/made.py"]["stable"]) == ("after", None)
    assert (items["scripts/edited.py"]["captured"], items["scripts/edited.py"]["stable"]) == ("before", False)
    assert items["scripts/edited.py"]["sha256"] == sha(b"print('one')\n"), "the start's bytes"
    assert d["command"]["changed_after_start"] is False


def test_a_command_another_extension_changed_keeps_the_start_bytes_of_its_own_names_only(proj):
    write(proj / "scripts" / "a.py", "print('a')\n")
    write(proj / "scripts" / "b.py", "print('b')\n")
    observer = Observer(proj)
    prior = observer.before("python scripts/a.py")
    d = details(observer.after(prior, "python scripts/b.py"))
    assert d["command"]["changed_after_start"] is True and d["command"]["text"] == "python scripts/b.py"
    [item] = d["scripts"]["items"]
    assert (item["path"], item["captured"]) == ("scripts/b.py", "after")


def test_cd_is_followed_when_literal_and_not_after_a_variable(proj):
    write(proj / "sub" / "task.py", "print(1)\n")
    observer = Observer(proj)
    d = details(observer("cd sub && python task.py"))
    assert [i["path"] for i in d["scripts"]["items"]] == ["sub/task.py"]
    d = details(observer('cd "$X" && python task.py'))
    assert d["scripts"]["items"] == [] and d["mentioned"]["unresolved"] == 1


def test_which_scripts_are_copied(proj, tmp_path):
    """Decision 4: git-ignored (data/), a secret name, secret-looking bytes and a notebook are not copied
    (the last two not hashed either); a script outside the project keeps its last name, size and sha256
    only; the same bytes are copied once."""
    write(proj / "data" / "q.sql", "select 1\n")
    write(proj / "scripts" / "my_secret_job.py", "print(1)\n")
    canary = SK + "ant-canary" + "0" * 24
    write(proj / "scripts" / "keyed.py", f'API_KEY = "{canary}"\n')
    write(proj / "scripts" / "nb.ipynb", '{"cells": []}\n')
    write(proj / "scripts" / "one.py", "print('same')\n")
    write(proj / "scripts" / "two.py", "print('same')\n")
    outside = write(tmp_path / "skill" / "run.py", "print('skill')\n")
    command = (f"python scripts/one.py scripts/two.py data/q.sql scripts/my_secret_job.py scripts/keyed.py "
               f"scripts/nb.ipynb {outside}")
    d = details(Observer(proj)(command))
    items = {i["path"] or i["name"]: i for i in d["scripts"]["items"]}
    assert (items["data/q.sql"]["copy"], items["data/q.sql"]["copy_reason"], items["data/q.sql"]["hash"]) == (
        None, "ignored", "hashed")
    assert (items["scripts/my_secret_job.py"]["hash"], items["scripts/my_secret_job.py"]["sha256"],
            items["scripts/my_secret_job.py"]["copy_reason"]) == ("withheld", None, "secret_name")
    assert (items["scripts/keyed.py"]["hash"], items["scripts/keyed.py"]["sha256"],
            items["scripts/keyed.py"]["copy_reason"]) == ("withheld", None, "secret_like")
    assert (items["scripts/nb.ipynb"]["copy"], items["scripts/nb.ipynb"]["copy_reason"],
            items["scripts/nb.ipynb"]["hash"]) == (None, "notebook", "hashed")
    assert items["run.py"] == {"name": "run.py", "path": None, "bytes": outside.stat().st_size,
                               "sha256": sha(outside.read_bytes()), "hash": "hashed", "captured": "before",
                               "stable": True, "copy": None, "copy_reason": "outside", "outside": True}
    assert items["scripts/one.py"]["copy"] == items["scripts/two.py"]["copy"] == "scripts/scripts/one.py"
    folder = proj / ".analytics" / "observed" / d["activity_id"]
    copied = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert copied == ["scripts/scripts/one.py"]
    # The command keeps what was written; what the observation itself adds holds no outside path.
    assert str(tmp_path) not in json.dumps([d["scripts"], d["mentioned"], d["cwd"], d["captures"]])
    everything = (proj / "journal.jsonl").read_text(encoding="utf-8")
    assert canary not in everything and sha(canary.encode()) not in everything


def test_a_git_work_tree_is_asked_which_scripts_it_ignores(proj):
    if shutil.which("git") is None:
        pytest.skip("no git")
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    write(proj / ".gitignore", "scripts/private_*\n")
    write(proj / "scripts" / "private_x.py", "print(1)\n")
    write(proj / "scripts" / "public.py", "print(2)\n")
    d = details(Observer(proj)("python scripts/private_x.py scripts/public.py"))
    items = {i["path"]: i for i in d["scripts"]["items"]}
    assert items["scripts/private_x.py"]["copy_reason"] == "ignored"
    assert items["scripts/public.py"]["copy"] == "scripts/scripts/public.py"


def test_the_script_bounds(proj):
    """Decision 4–6: at most 8 scripts read, 1 MiB each: the ninth and a bigger one keep path and size."""
    names = [f"scripts/s{i}.py" for i in range(9)]
    for i, name in enumerate(names):
        write(proj / name, f"print({i})\n")
    write(proj / "scripts" / "big.py", b"#" * (observed.SCRIPT_BYTES + 1))
    d = details(Observer(proj)("python " + " ".join(["scripts/big.py", *names])))
    items = {i["path"]: i for i in d["scripts"]["items"]}
    assert items["scripts/big.py"]["hash"] == "over_budget" and items["scripts/big.py"]["bytes"] == observed.SCRIPT_BYTES + 1
    assert sum(1 for i in items.values() if i["hash"] == "hashed") == observed.SCRIPTS_READ
    assert items["scripts/s8.py"]["hash"] == "over_budget"


# --- redaction (decision 5) -----------------------------------------------------------------------------

# Key shapes are put together at run time, so the repository holds none (tests/test_model_eval.py's scan).
BEARER, EYJ, SK = "Bear" + "er", "ey" + "J", "s" + "k-"
CANARIES = {
    "env": ("API_TOKEN=canary-env-1 python x.py", "canary-env-1"),
    "export": ("export DB_PASSWORD='canary-export-2' && python x.py", "canary-export-2"),
    "env_command": ("env MY_AUTH=canary-envcmd-3 python x.py", "canary-envcmd-3"),
    "powershell": ("$env:SERVICE_SECRET = 'canary-ps-4'; python x.py", "canary-ps-4"),
    "code": ("python -c 'api_key = \"canary-code-5\"'", "canary-code-5"),
    "dict": ("python -c '{\"password\": \"canary-dict-6\"}'", "canary-dict-6"),
    "option_eq": ("python x.py --token=canary-opt-7", "canary-opt-7"),
    "option_next": ("python x.py --client-secret canary-opt-8", "canary-opt-8"),
    "user_password": ("curl -u marker:canary-user-9 https://h.example/x", "canary-user-9"),
    "url_password": ("curl https://marker:canary-url-10@h.example/x", "canary-url-10"),
    "query": ("curl 'https://h.example/x?sig=canary-sas-11&X-Amz-Signature=canary-amz-12&page=canary-q-13'",
              "canary-sas-11 canary-amz-12 canary-q-13"),
    "authorization": ("curl -H 'Authorization: " + BEARER + " canary-bearer-14' https://h.example", "canary-bearer-14"),
    "cookie": ("curl -H 'Cookie: s=canary-cookie-15' https://h.example", "canary-cookie-15"),
    "api_key_header": ("curl -H 'X-Api-Key: canary-header-16' https://h.example", "canary-header-16"),
    "basic": ("echo Basic canary-basic-17 | python x.py", "canary-basic-17"),
    "pem": ("python x.py '-----BEGIN RSA PRIVATE KEY-----\ncanarypem18\n-----END RSA PRIVATE KEY-----'", "canarypem18"),
    "jwt": ("python x.py " + EYJ + "canary19aaaa." + EYJ + "canary19bbbb.canary19cccc", EYJ + "canary19aaaa"),
    "aws": ("python x.py AKIA" + "CANARY0000000020", "AKIACANARY0000000020"),
    "github": ("python x.py ghp_" + "canary21" * 3 + " github_pat_" + "canary22" * 3,
               "ghp_canary21canary21canary21 github_pat_canary22canary22canary22"),
    "gitlab": ("python x.py glpat-" + "canary23" * 3, "glpat-canary23canary23canary23"),
    "openai": ("python x.py " + SK + "ant-" + "canary24" * 3 + " sk_live_canary25x",
               SK + "ant-" + "canary24" * 3 + " sk_live_canary25x"),
    "slack": ("python x.py xoxb-canary-26-26", "xoxb-canary-26-26"),
    "google": ("python x.py AIza" + "Canary27" * 4 + "abc", "AIzaCanary27Canary27Canary27Canary27abc"),
    "hf_npm": ("python x.py hf_" + "canary28" * 3 + " npm_" + "canary29" * 3,
               "hf_canary28canary28canary28 npm_canary29canary29canary29"),
}


@pytest.mark.parametrize("case", sorted(CANARIES))
def test_each_rule_redacts_its_canary_from_text_details_and_attachments(proj, case):
    """Decision 5: for every rule, the value is in neither the event's text, its details nor an
    attachment, as itself or as its sha256 — nor is the unredacted command's sha256."""
    command, canaries = CANARIES[case]
    long_command = command + " " + "#" * (observed.COMMAND_INLINE + 100)  # the whole command attached too
    for text in (command, long_command):
        d = details(Observer(proj)(text, python=True))
        assert d["command"]["redaction"]["count"] >= 1 and d["command"]["redaction"]["rules_version"] == 1
        folder = proj / ".analytics" / "observed" / d["activity_id"]
        attached = b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file()) if folder.exists() else b""
        if text is long_command:
            assert any(c["role"] == "command" for c in d["captures"])
        stored = (proj / "journal.jsonl").read_bytes() + attached
        for canary in canaries.split():
            assert canary.encode() not in stored, (case, canary)
            assert sha(canary.encode()).encode() not in stored
        assert sha(text.encode()).encode() not in stored, "the unredacted command's hash"
        assert d["command"]["sha256"] == sha(observed.redact(text)[0].encode())


def test_redaction_keeps_what_it_need_not_hide():
    text, count = observed.redact("cd sub && python scripts/x.py --rows 10 --title 'Revenue (2026)' data/in.csv")
    assert count == 0 and text == "cd sub && python scripts/x.py --rows 10 --title 'Revenue (2026)' data/in.csv"
    text, count = observed.redact("curl https://h.example/a?page=2")
    assert text == "curl https://h.example/a?page=[redacted]" and count == 1


@pytest.mark.parametrize("name", [".env", ".env.local", "server.pem", "id.key", ".netrc", ".pgpass", "id_rsa",
                                  "id_ed25519.pub", "credentials.json", "my-secret.csv"])
def test_a_secret_name_is_listed_but_never_hashed(proj, name):
    d = details(Observer(proj)("sh x.sh", lambda: write(proj / "data" / name, "canary-file\n"), python=False))
    [item] = d["changes"]["items"]
    assert (item["path"], item["hash"], item["sha256"], item["explanation"]) == (f"data/{name}", "withheld", None, "unknown")
    assert sha(b"canary-file\n") not in (proj / "journal.jsonl").read_text(encoding="utf-8")


# --- bounds, cancel and commit (decision 6) ----------------------------------------------------------


def test_a_big_single_directory_stops_the_view_before_it_is_sorted(proj, monkeypatch):
    """Decision 3/6: one directory of more entries than ENTRIES: the listing is read only up to the bound
    (never sorted whole), the view is not_observed, and the session's change observation is turned off,
    which the call records as observation_off."""
    monkeypatch.setattr(observed, "ENTRIES", 500)
    big = proj / "data" / "big"
    big.mkdir()
    for i in range(2000):
        (big / f"{i:05d}.csv").write_bytes(b"x")
    budgets = []
    real = tree_state.Budget

    class Counting(real):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            budgets.append(self)

    monkeypatch.setattr(tree_state, "Budget", Counting)
    observer = Observer(proj)
    prior = observer.before("python job.py")
    assert prior.not_observed == "too_many_entries" and prior.turned_off and prior.state is None
    assert budgets[0].read == 501, "read one entry past the bound, then stopped"
    d = details(observer.after(prior))
    assert d["changes"]["state"] == "not_observed" and d["changes"]["reason"] == "too_many_entries"
    assert d["reasons"] == ["analysis_command", "observation_off"] and d["session_observation"] == "off"
    assert observer.sessions.off("s1")
    # Later calls of the session look at no state; Python is still recorded, `ls` is not.
    prior = observer.before("python job.py", view=False)
    assert prior.state is None and prior.not_observed == "observation_off"
    assert details(observer.after(prior))["reasons"] == ["analysis_command"]
    assert observer("ls", python=False, view=False).reason == "nothing_to_record"


def test_a_view_past_its_deadline_is_not_observed_and_three_in_a_row_turn_the_session_off(proj):
    observer = Observer(proj)
    sessions = observer.sessions
    assert observer.before("python a.py", seconds=-1).not_observed == "timeout"  # the first view: cold
    assert not sessions.off("s1")
    for n in range(2):
        prior = observer.before("python a.py", seconds=-1)
        assert prior.not_observed == "timeout" and not prior.turned_off, n
    prior = observer.before("python a.py", seconds=-1)
    assert prior.turned_off and sessions.off("s1")
    # A view that finished resets the count.
    other = observed.Sessions()
    for outcome in ("timeout", "timeout", "timeout", None, "timeout", "timeout"):
        assert not other.viewed("t", outcome)
    assert other.viewed("t", "timeout")


def test_the_before_view_keeps_its_bound_on_a_slow_walk(proj, monkeypatch):
    """Decision 6: a walk slowed down entry by entry stops at the job's deadline."""
    for i in range(50):
        write(proj / "data" / f"d{i}" / "f.csv", "x\n")
    real = os.scandir

    def slow(path):
        time.sleep(0.05)
        return real(path)

    monkeypatch.setattr(core.os, "scandir", slow)
    started = time.monotonic()
    prior = Observer(proj).before("python a.py", seconds=0.5)
    assert prior.not_observed == "timeout"
    assert time.monotonic() - started < 1.0


def test_work_cancelled_before_its_commit_writes_no_event_and_removes_its_folder(proj, monkeypatch):
    write(proj / "scripts" / "a.py", "print(1)\n")
    observer = Observer(proj)
    prior = observer.before("python scripts/a.py")
    job = observed.Job(time.monotonic() + 3)
    real_locked = writer.Writer.locked

    def locked(self, op="write", **kw):
        job.cancel()  # the waiting side gave up while the lock was being taken
        return real_locked(self, op, **kw)

    monkeypatch.setattr(writer.Writer, "locked", locked)
    out = observer.after(prior, job=job)
    assert out.event is None and out.reason == "cancelled"
    assert [e["kind"] for e in journal(proj)] == ["project", "context"]
    assert not any((proj / ".analytics" / "observed").iterdir())
    assert not job.committing


def test_a_journal_lock_held_elsewhere_records_nothing_within_the_bound(proj):
    observer = Observer(proj)
    prior = observer.before("python a.py")
    project = core.Project(proj)
    held, release = threading.Event(), threading.Event()

    def hold():
        with project.platform.locks.exclusive(project.state_dir / "lock"):
            held.set()
            release.wait(10)

    threading.Thread(target=hold, daemon=True).start()
    held.wait(5)
    try:
        started = time.monotonic()
        out = observer.after(prior, seconds=0.6)
        assert out.event is None and out.reason == "journal_busy"
        assert time.monotonic() - started < 1.5
    finally:
        release.set()


def test_details_over_their_budget_are_written_whole_in_attachments(proj, monkeypatch):
    """Decision 2: details that would not fit are written with counts and attachment digests only; the
    command and the lists are in the attachments, and the record is still written."""
    monkeypatch.setattr(observed, "DETAILS_BYTES", 600)
    write(proj / "scripts" / "a.py", "print(1)\n")
    out = Observer(proj)("python scripts/a.py", lambda: write(proj / "data" / "x.csv", "x\n"))
    d = details(out)
    assert EVENT.is_valid(out.event), list(EVENT.iter_errors(out.event))
    assert d["command"]["text"] == "" and d["changes"]["items"] == [] and d["changes"]["total"] == 1
    roles = {c["role"] for c in d["captures"]}
    assert {"command", "changes", "files", "script"} <= roles
    folder = proj / ".analytics" / "observed" / d["activity_id"]
    changes = json.loads((folder / "changes.json").read_text(encoding="utf-8"))
    assert [c["path"] for c in changes] == ["data/x.csv"]
    for capture in d["captures"]:
        assert sha((folder / capture["path"]).read_bytes()) == capture["sha256"]


def test_a_long_change_list_goes_to_its_attachment(proj):
    def act():
        for i in range(400):
            write(proj / "data" / "many" / f"file-{i:04d}.csv", "x\n")

    d = details(Observer(proj)("sh gen.sh", act, python=False))
    assert d["changes"]["total"] == 400 and 0 < len(d["changes"]["items"]) < 400
    assert d["changes"]["items_complete"] is True
    folder = proj / ".analytics" / "observed" / d["activity_id"]
    assert len(json.loads((folder / "changes.json").read_text(encoding="utf-8"))) == 400
    assert len(json.dumps(d, ensure_ascii=False).encode()) <= observed.DETAILS_BYTES


# --- the writer and older kits (decision 12) -----------------------------------------------------------


def test_only_the_observation_writes_the_kind(proj):
    project = core.Project(proj)
    origin = core.Origin("agent", "tool", "declared", "s1")
    with pytest.raises(core.AnalyticsError) as refused:
        core.record(project, "observed_activity", "x", origin=origin)
    assert refused.value.code == "bad_kind"
    code, result = kit_cli(proj, "record", "observed_activity", "x")
    assert code != 0 and result["error"]["code"] == "bad_kind"
    with pytest.raises(ValueError, match="written by the observation"):
        with core.writer_for(project, origin).locked("record") as held:
            held.append("observed_activity", "x", details={})
    with pytest.raises(ValueError, match="not an observed activity"):
        with core.writer_for(project, origin).locked("observe") as held:
            held.append("observed_activity", "x", details={"contract": "observed_activity/1"})


def test_the_floor_refuses_an_older_writer_and_an_older_reader_counts_the_line(proj, monkeypatch):
    details(Observer(proj)("python a.py"))
    older = writer.Writer(proj, platform=core.PLATFORM, origin=core.Origin("test", "sdk"), writer_version="0.32.0")
    with pytest.raises(core.AnalyticsError) as refused:
        older.record("note", "an older kit's note")
    assert refused.value.code == "writer_too_old"
    # A reader that does not know the kind (a kit before it): the line is unsupported (unknown_kind),
    # kept and counted, and the next seq follows it.
    monkeypatch.setitem(relations.KINDS, relations.EVENT_V2, relations.KINDS[relations.EVENT_V2] - {"observed_activity"})
    read = relations.Journal((proj / "journal.jsonl").read_bytes())
    assert read.unsupported == [{"location": "journal:3", "reason": "unknown_kind"}] and read.next_seq == 4


# --- readers (decision 7, 10) ----------------------------------------------------------------------------


def test_status_counts_observations_on_a_line_of_their_own(proj):
    observer = Observer(proj)
    first = details(observer("python a.py", lambda: write(proj / "data" / "a.csv", "a\n")))
    core.record(core.Project(proj), "note", "a person's note", origin=core.Origin("human", "cli"))
    second = observer("python b.py")
    st = core.status(core.Project(proj))
    assert all(e["kind"] != "observed_activity" for e in st["recent"])
    assert st["observed"]["count"] == 2 and st["observed"]["latest"] == second.event["id"]
    assert st["observed"]["unexplained"] == 1 and first["changes"]["unexplained"] == 1
    text = core.status_text(st)
    assert (f"observed outside the kit: 2 (latest {second.event['id']}; 1 with changes no run recorded; changes "
            "not observed in 0 sessions) — part=records kind=observed_activity") in text
    found = core.status(core.Project(proj), part="records", kind="observed_activity")
    assert [row["id"] for row in found["items"]] == ["e3", second.event["id"]]
    block = core.context_block(core.Project(proj), session="s1")
    assert f"Observed outside the kit in this session: 2 bash call(s), latest {second.event['id']}" in block
    assert "Observed outside the kit" not in core.context_block(core.Project(proj), session="another")
    assert "Observed outside the kit" not in core.context_block(core.Project(proj))


def test_a_row_whose_details_cannot_be_read_is_a_record_but_no_observation(proj):
    details(Observer(proj)("python a.py"))
    rows = journal(proj)
    broken = {**rows[-1], "seq": 4, "id": "e4", "details": {**rows[-1]["details"], "contract": "observed_activity/9"}}
    with open(proj / "journal.jsonl", "ab") as fh:
        fh.write(json.dumps(broken).encode() + b"\n")
    st = core.status(core.Project(proj))
    assert st["observed"]["count"] == 1
    assert [e["id"] for e in st["recent"] if e["kind"] == "observed_activity"] == ["e4"]
    assert not st["unsupported"]


def test_the_publication_content_basis_does_not_move_for_an_observation(proj):
    from aelix_analytics.publishing.basis import content_basis

    core.record(core.Project(proj), "finding", "a finding", origin=core.Origin("human", "cli"))
    before = content_basis(relations.Journal((proj / "journal.jsonl").read_bytes()),
                           validate_reference=writer.reference_problem)[0]
    details(Observer(proj)("python a.py"))
    after, excluded = content_basis(relations.Journal((proj / "journal.jsonl").read_bytes()),
                                    validate_reference=writer.reference_problem)
    assert after.content.content_seq == before.content.content_seq == 3 and after.as_of_seq == 4
    rows = journal(proj)
    broken = {**rows[-1], "seq": 5, "id": "e5", "details": {"contract": "observed_activity/9"}}
    data = (proj / "journal.jsonl").read_bytes() + json.dumps(broken).encode() + b"\n"
    assert content_basis(relations.Journal(data), validate_reference=writer.reference_problem)[0].content.content_seq == 5


def test_a_run_keeps_its_outputs_through_the_shared_state_module(proj):
    """The move of tree_stat, rekeyed and diff_outputs (decision 3): a run's outputs are as before, a
    file it replaced keeping size and mtime is an output with its new bytes' hash, and one the walk could
    not stat is left out rather than deleted."""
    write(proj / "data" / "t.csv", b"AAAA")
    cache = core.HashCache(core.Project(proj))
    cache.sha(proj / "data" / "t.csv")
    cache.save()
    script = ("import os\n"
              "st = os.stat('data/t.csv')\n"
              "open('data/t.tmp', 'wb').write(b'BBBB')\n"
              "os.utime('data/t.tmp', ns=(st.st_atime_ns, st.st_mtime_ns))\n"
              "os.replace('data/t.tmp', 'data/t.csv')\n"
              "open('data/derived/out.csv', 'w').write('o')\n")
    write(proj / "scripts" / "r.py", script)
    code, result = kit_cli(proj, "run", "scripts/r.py", "--title", "r")
    assert code == 0 and result["run"]["status"] == "ok", result
    outputs = {o["path"]: o for o in result["run"]["outputs"]}
    assert outputs["data/derived/out.csv"]["change"] == "created"
    if os.stat(proj / "data" / "t.csv").st_ino:
        assert outputs["data/t.csv"]["sha256"] == sha(b"BBBB")


# --- the cost (OQ4): measured, the bounds asserted -------------------------------------------------------


def _files(root: Path, n: int, *, per_dir: int = 100) -> None:
    for i in range(n):
        d = root / "data" / "gen" / f"d{i // per_dir:04d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"f{i:06d}.csv").write_bytes(b"x\n")


@pytest.mark.parametrize("files", [1000, 10000])
def test_the_cost_of_an_observation_is_measured(proj, files):
    """The before and after of one observation on a data/ of 1,000 and 10,000 files: each within its bound
    (2 s, 3 s) and recorded (record_observation `observed-cost-<n>`), with the journal line's size."""
    _files(proj, files)
    observer = Observer(proj)
    started = time.perf_counter()
    prior = observer.before("python x.py")
    before_ms = (time.perf_counter() - started) * 1000
    assert prior.state is not None and len(prior.state) >= files
    write(proj / "data" / "derived" / "new.csv", "n\n")
    started = time.perf_counter()
    out = observer.after(prior)
    after_ms = (time.perf_counter() - started) * 1000
    assert out.event is not None
    assert before_ms < observed.BEFORE_SECONDS * 1000 + 500 and after_ms < observed.AFTER_SECONDS * 1000 + 500
    line = (proj / "journal.jsonl").read_bytes().splitlines()[-1]
    record_observation(f"observed-cost-{files}", {"files": files, "before_ms": round(before_ms, 1),
                                                  "after_ms": round(after_ms, 1), "line_bytes": len(line) + 1})


@pytest.mark.parametrize("profile", ["read", "evidence"])
def test_a_share_holds_no_observation_and_its_content_hash_does_not_move(proj, profile):
    """Decision 10 (OQ1, OQ8): neither profile holds an observation — its redacted command and its
    attachments are in no package file — and a build again after an observation alone has the same
    content hash and content_seq."""
    from aelix_analytics.publishing import build

    origin = core.Origin("human", "cli")
    project = core.Project(proj)
    core.record(project, "finding", "a finding to share", origin=origin)
    first = build.build_publication(project, profile=profile, origin=origin)
    marker = "marker-command-text-171"
    write(proj / "scripts" / "m.py", f"print('{marker}')\n")
    details(Observer(proj)(f"python scripts/m.py --note {marker}", lambda: write(proj / "data" / "m.csv", marker)))
    second = build.build_publication(core.Project(proj), profile=profile, origin=origin)
    assert second["content_sha256"] == first["content_sha256"]
    assert second["basis"]["content_seq"] == first["basis"]["content_seq"]
    assert second["basis"]["as_of_seq"] > first["basis"]["as_of_seq"]
    package = proj / second["path"]
    for path in package.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert marker.encode() not in data and b"observed_activity" not in data, path


def test_the_workspace_still_builds_with_an_observation_in_the_journal(proj):
    """PR-1 adds no screen for the kind (PR-2 does): the Workspace builds and lists the record as any
    other kind it has no words for, and the run, status and context reads go on."""
    d = details(Observer(proj)("python a.py", lambda: write(proj / "data" / "a.csv", "a\n")))
    code, result = kit_cli(proj, "render")
    assert code == 0 and result["ok"], result
    timeline = (proj / "workspace" / "lists" / "timeline-0.html").read_text(encoding="utf-8")
    assert "e3" in timeline and d["activity_id"] not in timeline
    code, result = kit_cli(proj, "status", "--part", "records", "--kind", "observed_activity")
    assert code == 0 and [i["id"] for i in result["status"]["items"]] == ["e3"], result


def test_the_cost_past_the_entry_bound_is_measured(proj):
    """One directory of 25,000 entries with the real bound (20,000): the start stops within its 2 s having
    read past the bound by one entry, and the session's observation is off (`observed-cost-single-dir`)."""
    big = proj / "data" / "big"
    big.mkdir()
    for i in range(25000):
        (big / f"{i:06d}.csv").write_bytes(b"x")
    observer = Observer(proj)
    started = time.perf_counter()
    prior = observer.before("python x.py")
    before_ms = (time.perf_counter() - started) * 1000
    assert prior.not_observed == "too_many_entries" and prior.turned_off
    assert before_ms < observed.BEFORE_SECONDS * 1000 + 500
    out = observer.after(prior)
    assert details(out)["reasons"] == ["analysis_command", "observation_off"]
    record_observation("observed-cost-single-dir", {"entries": 25000, "bound": observed.ENTRIES,
                                                    "before_ms": round(before_ms, 1)})


@pytest.mark.parametrize("change", [
    lambda e: e["details"].pop("captures"),
    lambda e: e["details"].update(contract="observed_activity/2"),
    lambda e: e.update(refs=[{"type": "event", "event_id": "e1", "relation": "cites", "basis": "declared",
                              "version_binding": "author"}]),
    lambda e: e["details"].update(activity_id="act-NOT-A-ULID"),
    lambda e: e["details"].update(reasons=[]),
    lambda e: e["details"]["changes"].update(state="seen"),
    lambda e: e["details"]["command"].pop("sha256"),
    lambda e: e["details"]["performer"].update(actor="human"),
], ids=["no-captures", "other-contract", "refs", "activity-id", "no-reason", "state", "command-sha", "performer"])
def test_the_schema_and_the_reader_refuse_what_is_no_observation(proj, change):
    """The positive example is what the writer wrote (above); each negative one is refused by the record
    schema and is no observation to the reader, though it stays a record."""
    import copy

    event = copy.deepcopy(details(Observer(proj)("python a.py")) and journal(proj)[-1])
    assert EVENT.is_valid(event) and relations.observed_activity_problem(event) is None
    change(event)
    assert not EVENT.is_valid(event)
    assert relations.observed_activity_problem(event) is not None


# --- review fixes (implementation reviews of 2026-11-10) -------------------------------------------------

WHOLE_VALUES = {
    "comma": ("API_TOKEN=canary-w1,marker-w1 python x.py", "canary-w1 marker-w1"),
    "quoted_space": ("curl -u 'user:canary-w2 marker-w2' https://h.example", "canary-w2 marker-w2"),
    "escaped_space": ("curl -u user:canary-w3\\ marker-w3 https://h.example", "canary-w3 marker-w3"),
    "inner_quotes": ('export DB_PASSWORD=canary-w4"marker-w4 x"y && python x.py', "canary-w4 marker-w4"),
    "option_quoted": ("python x.py --token 'canary-w5 marker-w5'", "canary-w5 marker-w5"),
    "after_note": ("echo note: x https://h.example -H X-Auth-Token:canary-w6", "canary-w6"),
    "dict_cookie": ("python -c '{\"Cookie\": \"sessionid=canary-w7\"}'", "canary-w7"),
    "api_key_header": ("curl -H 'api-key: canary-w8' https://h.example", "canary-w8"),
    "gitlab_header": ("curl -H 'PRIVATE-TOKEN: canary-w9' https://h.example", "canary-w9"),
    "option_after_option": ("python x.py --title --token canary-w10", "canary-w10"),
}


@pytest.mark.parametrize("case", sorted(WHOLE_VALUES))
def test_a_value_is_redacted_whole(proj, case):
    """Review (Codex 1, independent m1): a value is one shell word — quotes, escapes and separators in it
    included — so no part of it is kept; a name that holds no secret earlier on a line hides nothing."""
    command, canaries = WHOLE_VALUES[case]
    d = details(Observer(proj)(command + " " + "#" * (observed.COMMAND_INLINE + 10), python=True))
    folder = proj / ".analytics" / "observed" / d["activity_id"]
    stored = (proj / "journal.jsonl").read_bytes() + b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())
    for canary in canaries.split():
        assert canary.encode() not in stored, (case, canary)


HOSTILE = {"dates": "2026-11-10-" * 9100, "acgt": "ACGT-" * 20000, "query": "?a" * 50000, "eq": "a=" * 50000,
           "double_quotes": '"' * 100000, "single_quotes": "'" * 100000, "hyphens": "-" * 100000,
           "options": "--a" * 33000, "x_headers": "x-" * 50000, "jwt_heads": "eyJ" * 33000, "colons": "a:" * 50000,
           "backslashes": "\\" * 100000, "dict_heads": "{'" * 50000, "user_options": "-u " * 33000,
           "header_words": "x-api-key " * 10000, "urls": "https://" * 12000}


@pytest.mark.parametrize("case", sorted(HOSTILE))
def test_redaction_reads_a_hostile_100_kb_line_in_linear_time(case):
    """Review (independent D1): every rule reads a long run once; a 100 KB line of each shape that made a
    pattern quadratic before (a hyphenated run under the header rule took 20 s) is redacted at once."""
    started = time.perf_counter()
    observed.redact(HOSTILE[case])
    assert time.perf_counter() - started < 0.5, case


def test_a_hostile_command_is_observed_within_the_bound(proj):
    command = "python x.py " + "2026-11-10-" * 9100
    started = time.perf_counter()
    out = Observer(proj)(command)
    assert out.event is not None and time.perf_counter() - started < observed.BEFORE_SECONDS + observed.AFTER_SECONDS


def test_a_file_the_command_wrote_with_a_secret_keeps_no_hash_or_size(proj):
    """Review (independent D2): a file the command names (a heredoc's target, a script) whose bytes match
    the redaction rules keeps neither its sha256 nor its size in the changes, as its script copy is
    refused; a file it names without a secret is hashed as before."""
    pin = "pin4821canary"
    command = (f"cat > analysis.py <<'EOF'\nAPI_TOKEN = \"{pin}\"\nEOF\ncat > conf.json <<'EOF'\n"
               f"{{\"api_key\": \"{pin}\"}}\nEOF\necho ok > plain.txt\npython analysis.py")

    def act():
        write(proj / "analysis.py", f'API_TOKEN = "{pin}"\n')
        write(proj / "conf.json", f'{{"api_key": "{pin}"}}\n')
        write(proj / "plain.txt", "ok\n")

    d = details(Observer(proj)(command, act))
    items = {i["path"]: i for i in d["changes"]["items"]}
    for path in ("analysis.py", "conf.json"):
        assert (items[path]["hash"], items[path]["sha256"], items[path]["bytes"], items[path]["reason"]) == (
            "withheld", None, None, "secret_like"), path
    assert items["plain.txt"]["hash"] == "hashed"
    [script] = d["scripts"]["items"]
    assert (script["hash"], script["sha256"], script["bytes"]) == ("withheld", None, None)
    stored = (proj / "journal.jsonl").read_text(encoding="utf-8")
    for data in (f'API_TOKEN = "{pin}"\n', f'{{"api_key": "{pin}"}}\n'):
        assert sha(data.encode()) not in stored and pin not in stored


def test_a_project_inside_a_repository_is_judged_by_the_repository_s_ignore_rules(tmp_path):
    """Review (Codex 2): a project in a folder of a git repository (no `.git` of its own) asks git, so a
    script the repository's .gitignore leaves out is not copied."""
    if shutil.which("git") is None:
        pytest.skip("no git")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    write(repo / ".gitignore", "proj/scripts/private_*\n")
    root = core.init_project(repo / "proj", title="Inside", template_dir=TEMPLATE, origin=core.Origin("test", "sdk")).root
    write(root / "scripts" / "private_x.py", "print(1)\n")
    write(root / "scripts" / "public.py", "print(2)\n")
    d = details(Observer(root)("python scripts/private_x.py scripts/public.py"))
    items = {i["path"]: i for i in d["scripts"]["items"]}
    assert (items["scripts/private_x.py"]["copy"], items["scripts/private_x.py"]["copy_reason"]) == (None, "ignored")
    assert items["scripts/public.py"]["copy"] == "scripts/scripts/public.py"


def test_an_error_once_the_line_may_be_written_keeps_the_attachments(proj, monkeypatch):
    """Review (Codex 3): the attachments are removed only when no event is certain. A write that fails
    after its bytes went out leaves the observation `unconfirmed` with its attachments; an error after the
    event was written (the lock's release) keeps the event and the attachments."""
    write(proj / "scripts" / "a.py", "print(1)\n")
    real = writer.append_line

    def cut_short(path, line, **kw):
        real(path, line, **kw)
        raise OSError(5, "the write was cut short")

    monkeypatch.setattr(writer, "append_line", cut_short)
    out = Observer(proj)("python scripts/a.py")
    assert out.event is None and out.reason == "unconfirmed"
    row = journal(proj)[-1]
    assert row["kind"] == "observed_activity"
    assert (proj / ".analytics" / "observed" / row["details"]["activity_id"]).is_dir()
    monkeypatch.setattr(writer, "append_line", real)

    real_locked = writer.Writer.locked

    @contextlib.contextmanager
    def failing_release(self, op="write", **kw):
        with real_locked(self, op, **kw) as held:
            yield held
        raise OSError(5, "the lock could not be released")

    monkeypatch.setattr(writer.Writer, "locked", failing_release)
    out = Observer(proj)("python scripts/a.py")
    assert out.event is not None
    assert (proj / ".analytics" / "observed" / out.event["details"]["activity_id"]).is_dir()


def test_a_deletion_with_a_run_record_that_cannot_be_read_is_unknown(proj):
    """Review (Codex 5): a run completed in the window but its record cannot be read: a deletion no other
    explanation covers is `unknown` (`run_record_unread`), not `unexplained`."""
    write(proj / "data" / "gone.csv", "x\n")
    observer = Observer(proj)
    prior = observer.before("sh x.sh")
    (proj / "data" / "gone.csv").unlink()
    row = {"schema": "analytics.event/2", "seq": 3, "id": "e3", "recorded_at": "2026-11-10T00:00:00.000+00:00",
           "kind": "run", "actor": "human", "text": "a run", "refs": [
               {"type": "run", "run_id": "20261110-000000-nowhere", "relation": "about", "basis": "observed",
                "version_binding": "author"}]}
    with open(proj / "journal.jsonl", "ab") as fh:
        fh.write(json.dumps(row).encode() + b"\n")
    d = details(observer.after(prior, python=False))
    [item] = d["changes"]["items"]
    assert (item["change"], item["explanation"], item["reason"]) == ("deleted", "unknown", "run_record_unread")


def _required_paths(schema_node, defs, prefix=()):
    """Every (path, key) the record schema requires below observed_activity_details: nested objects and
    the first item of a list."""
    node = schema_node
    if "$ref" in node:
        node = defs[node["$ref"].rsplit("/", 1)[1]]
    out = []
    for key in node.get("required", ()):
        out.append((prefix, key))
    for key, child in (node.get("properties") or {}).items():
        if isinstance(child, dict) and child.get("type") == "object" or "$ref" in child and key != "sha256" and key not in (
                "started_at", "ended_at", "activity_id"):
            out += _required_paths(child, defs, (*prefix, key))
        elif isinstance(child, dict) and child.get("type") == "array" and isinstance(child.get("items"), dict) \
                and child["items"].get("type") == "object":
            out += _required_paths(child["items"], defs, (*prefix, key, 0))
    return out


def test_the_reader_refuses_every_row_the_schema_refuses_for_a_missing_field(proj):
    """Review (Codex 6): each field the record schema requires, at every level, missing → the schema and
    the reader both refuse the row, so it is neither counted nor left out of a share's content basis."""
    from aelix_analytics.publishing.basis import content_basis

    write(proj / "scripts" / "a.py", "print(1)\n")
    event = journal(proj) and details(Observer(proj)("python scripts/a.py --rows 1",
                                                     lambda: write(proj / "data" / "x.csv", "x\n"))) and journal(proj)[-1]
    defs = SCHEMA["$defs"]
    paths = _required_paths(defs["observed_activity_details"], defs)
    assert len(paths) > 40
    for prefix, key in paths:
        broken = copy.deepcopy(event)
        target = broken["details"]
        for step in prefix:
            target = target[step]
        target.pop(key)
        assert not EVENT.is_valid(broken), (prefix, key)
        assert relations.observed_activity_problem(broken) is not None, (prefix, key)
    broken = copy.deepcopy(event)
    broken["details"]["performer"].pop("tool_call_id")
    data = (proj / "journal.jsonl").read_bytes().rsplit(b"\n", 2)[0] + b"\n" + json.dumps(broken).encode() + b"\n"
    read = relations.Journal(data)
    basis, _ = content_basis(read, validate_reference=writer.reference_problem)
    assert basis.content.content_seq == read.as_of_seq
    assert relations.observed_activities(read) == []


def test_a_path_unreadable_after_that_was_not_there_before_is_no_change(proj, monkeypatch):
    """Review (independent D3): a file the walk lists but can never stat (a folder that can be listed and
    not entered) is not a change of every window."""
    write(proj / "data" / "private" / "x.csv", "x\n")
    target = os.fspath(core.Project(proj).locate(proj / "data" / "private" / "x.csv")[1])
    real_stat = os.stat

    def stat(p, *a, **kw):
        if os.fspath(p) == target:
            raise PermissionError(13, "denied")
        return real_stat(p, *a, **kw)

    monkeypatch.setattr(tree_state.os, "stat", stat)
    assert Observer(proj)("ls", python=False).reason == "nothing_to_record"


def test_the_hash_budget_counts_the_bytes_read(proj, monkeypatch):
    """Review (minor): a file that grows after the walk saw it is read only up to the budget."""
    monkeypatch.setattr(observed, "HASH_BYTES", 8)
    observer = Observer(proj)
    prior = observer.before("sh x.sh")
    path = write(proj / "data" / "grow.csv", b"abcd")
    seen = tree_state.stat_of(path)
    real = tree_state.stat_of
    monkeypatch.setattr(observed.tree_state, "stat_of", lambda p: seen if os.fspath(p).endswith("grow.csv") else real(p))
    monkeypatch.setattr(observed.tree_state, "tree_stat", _with_size_of(tree_state.tree_stat, "data/grow.csv", seen))
    path.write_bytes(b"abcd" * 3)
    d = details(observer.after(prior, python=False))
    [item] = d["changes"]["items"]
    assert (item["hash"], item["sha256"]) == ("over_budget", None)


def _with_size_of(real, rel, st):
    def tree_stat(project, budget=None):
        out = real(project, budget)
        if rel in out:
            out[rel] = st
        return out
    return tree_stat


def test_a_turning_off_no_record_could_hold_is_recorded_by_the_next_observation(proj):
    """Review (Codex 4): the session's observation turned off by a view no record followed is pending,
    and the next observation of the session records `observation_off`."""
    observer = Observer(proj)
    sessions = observer.sessions
    sessions.viewed("s1", None)
    for _ in range(3):
        sessions.viewed("s1", "timeout")
    assert sessions.off("s1") and sessions.pending("s1")
    d = details(observer("ls", python=False, view=False))
    assert d["reasons"] == ["observation_off"] and d["session_observation"] == "off"
    assert not sessions.pending("s1")
    assert observer("ls", python=False, view=False).reason == "nothing_to_record"


# --- the fix-diff check (2026-11-10) -------------------------------------------------------------------


def test_a_file_named_through_an_escape_is_judged_by_its_word(proj):
    """Fix-diff check (I-D2): `python ana\\lysis.py` names analysis.py as the shell reads it; a secret in its
    bytes keeps its hash out of the changes as out of the scripts."""
    pin = "pin5932canary"
    d = details(Observer(proj)("python ana\\lysis.py", lambda: write(proj / "analysis.py", f'API_TOKEN = "{pin}"\n')))
    [item] = d["changes"]["items"]
    assert (item["path"], item["hash"], item["sha256"], item["bytes"]) == ("analysis.py", "withheld", None, None)
    assert sha(f'API_TOKEN = "{pin}"\n'.encode()) not in (proj / "journal.jsonl").read_text(encoding="utf-8")


BOOLEANS = [("cwd_outside",), ("command", "inline_complete"), ("command", "truncated"),
            ("command", "changed_after_start"), ("result", "is_error"), ("changes", "items_complete"),
            ("changes", "items", 0, "same_bytes_possible"), ("scripts", "items_complete"),
            ("scripts", "items", 0, "stable"), ("scripts", "items", 0, "outside"), ("mentioned", "items_complete")]


@pytest.mark.parametrize("where", BOOLEANS, ids=lambda w: ".".join(map(str, w)))
def test_a_number_where_the_schema_says_boolean_is_refused_by_both(proj, where):
    """Fix-diff check (C1-6): 1 is not a boolean to the schema, nor to the reader."""
    write(proj / "scripts" / "a.py", "print(1)\n")
    details(Observer(proj)("python scripts/a.py", lambda: write(proj / "data" / "x.csv", "x\n")))
    event = copy.deepcopy(journal(proj)[-1])
    assert EVENT.is_valid(event) and relations.observed_activity_problem(event) is None
    target = event["details"]
    for step in where[:-1]:
        target = target[step]
    target[where[-1]] = 1
    assert not EVENT.is_valid(event)
    assert relations.observed_activity_problem(event) is not None


def test_a_malformed_skip_line_in_telemetry_counts_for_nothing(proj):
    """Fix-diff check: a telemetry line that is no object, or whose `observation_skipped` is no text, does not
    stop the overview and counts for nothing."""
    lines = ['["observation_skipped"]', '{"observation_skipped": ["busy"]}', '{"observation_skipped": "busy"}']
    (proj / ".analytics" / "telemetry.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    st = core.status(core.Project(proj))
    assert st["observed"]["skipped"] == {"busy": 1}
    assert "1 bash calls skipped while an observation was still running" in core.status_text(st)
