"""What a project's files were before something ran and after it, and what changed between the two.

One set of rules for a run's outputs (runner) and for the window of an observed activity (observed,
ADR-0015 decision 3), so a path a run records and a path an observation records are the same key
(the path a record holds, ADR-0006 9): the namespace core.Project.walk gives — every file but the
ignored top-level directories, the project's own files and its marker, through the boundaries a link
out of the project makes — stated as `{path: (size, mtime_ns, st_ino)}`, a file whose stat failed
other than for being gone kept apart as unreadable, the boundaries a link made or removed while it ran
told apart from files written.

Standard library only: the aelix process loads it with core, and a project is passed in (a
core.Project), never imported.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# A project's own top-level files, never a run's outputs or an observation's changes, with its marker
# (the layout's: changes).
PROJECT_FILES = frozenset({"journal.jsonl", "CONTEXT.md", "pyproject.toml", "uv.lock", ".python-version",
                           ".gitignore", ".gitattributes"})


class Stopped(Exception):
    """A state view a Budget stopped: `reason` is `too_many_entries`, `timeout` or `cancelled`."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Budget:
    """The bounds of an observation's state view (ADR-0015 decision 6): at most `entries` directory
    entries read, until `deadline` (time.monotonic), while `cancelled()` is false. core.Project.walk
    gives it each entry as a listing reads it, before the listing is sorted; tree_stat calls it with
    nothing before each stat, for the time and the cancel alone. Over a bound it raises Stopped."""

    def __init__(self, entries: int, deadline: float, cancelled=None):
        self.entries = entries
        self.read = 0
        self.deadline = deadline
        self.cancelled = cancelled

    def __call__(self, entry=None) -> None:
        if entry is not None:
            self.read += 1
            if self.read > self.entries:
                raise Stopped("too_many_entries")
        if time.monotonic() > self.deadline:
            raise Stopped("timeout")
        if self.cancelled is not None and self.cancelled():
            raise Stopped("cancelled")


class TreeStat(dict):
    """{path a record holds: (size, mtime_ns, st_ino)} of every file of the namespace
    (core.Project.walk), with each one's real path (`real`), the boundaries the walk went through
    (`boundaries`) and the paths the walk listed whose stat failed for another reason than the file
    being gone (`unreadable`: a sharing violation on Windows, say)."""

    def __init__(self) -> None:
        super().__init__()
        self.real: dict[str, str] = {}
        self.boundaries: tuple = ()
        self.unreadable: set[str] = set()


def tree_stat(project, budget: Budget | None = None) -> TreeStat:
    """The state of the namespace now. With a `budget` the walk stops at its bounds (Stopped)."""
    out = TreeStat()
    for rel, real, _entry in project.walk(budget=budget):
        if budget is not None:
            budget()
        try:
            st = os.stat(real)  # not the listing's: Windows updates it late for a file still open
        except FileNotFoundError:
            continue  # gone since it was listed
        except OSError:
            out.unreadable.add(rel)
            continue
        out[rel] = (st.st_size, st.st_mtime_ns, st.st_ino)
        out.real[rel] = real
    out.boundaries = project.boundaries()
    return out


def rekeyed(project, before: TreeStat) -> TreeStat:
    """`before` with each file under the path a record holds for it now: something that made or
    removed a link changed which boundary some files are seen under, and a file that did not change
    must not show as deleted and created."""
    out = TreeStat()
    out.boundaries = before.boundaries
    for rel, st in before.items():
        place = before.real.get(rel)
        now = (project.identity_of_real(place) if place else None) or rel
        out[now] = st
        if place:
            out.real[now] = place
    out.unreadable = set(before.unreadable)
    return out


def same(first: tuple, second: tuple) -> bool:
    """Whether two states of a file are the same: size, mtime and, where both have one, identity
    (st_ino 0 is a file system that gives none: not compared)."""
    if first[:2] != second[:2]:
        return False
    return len(first) < 3 or len(second) < 3 or not first[2] or not second[2] or first[2] == second[2]


def modified_ns(path) -> int:
    """When `path` was last modified, by the clock the file system stamps files with (a clock tick
    behind time.time_ns on Linux and Windows); now where it cannot be read."""
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return time.time_ns()


def below_any(rel: str, places) -> bool:
    return any(rel == ns or rel.startswith(f"{ns}/") for ns in places)


def changes(project, before: TreeStat, after: TreeStat, since: int | None = None) -> list[dict]:
    """The files created, modified, deleted or unreadable between `before` and `after`, by the path a
    record holds: first those `after` has (`created`, `modified`) in code point order, then those it
    has not (`deleted`, `unreadable`). Each is `{path, change}`, with `stat` (after's) and
    `identity_only` (only its identity changed: size and mtime are the same) for the first two.

    A boundary only one of the two walks went through is a link made, removed or repointed, not files
    written: a file below a boundary new in `after` is a change only when it was modified at or after
    `since` (the start, by the file system's clock), and a file below one gone from it is deleted only
    when its real path no longer leads to a file (docs/CONTRACTS.md G). A file `before` had that `after`
    listed but could not stat is `unreadable`, never deleted: not reading it is no evidence it is gone."""
    made = {ns for ns, _ in set(getattr(after, "boundaries", ())) -
            set(getattr(before, "boundaries", ()))}
    gone = {ns for ns, _ in set(getattr(before, "boundaries", ())) -
            set(getattr(after, "boundaries", ()))}
    own = PROJECT_FILES | {project.layout.marker}
    out = []
    for rel, st in sorted(after.items()):
        if rel in own:
            continue
        prev = before.get(rel)
        if prev is not None and same(prev, st):
            continue
        if made and since is not None and st[1] < since and below_any(rel, made):
            continue  # there before, and seen since through a link made meanwhile
        out.append({"path": rel, "change": "created" if prev is None else "modified", "stat": st,
                    "identity_only": prev is not None and prev[:2] == st[:2]})
    # Only a file that was there before can have become unreadable: one `after` could not stat that
    # `before` did not have (both views failed on it) is no change of this window.
    unreadable = set(getattr(after, "unreadable", set())) & set(before)
    real = getattr(before, "real", {})
    for rel in sorted(set(before) - set(after)):
        if rel in own:
            continue
        if rel in unreadable:
            out.append({"path": rel, "change": "unreadable"})
            continue
        if gone and rel in real and below_any(rel, gone) and os.path.isfile(real[rel]):
            continue  # still there, below a link removed or repointed meanwhile
        out.append({"path": rel, "change": "deleted"})
    return out


def links_changed(before: TreeStat, after: TreeStat) -> int:
    """How many namespace paths a boundary was made, removed or repointed at between the two."""
    first, second = set(getattr(before, "boundaries", ())), set(getattr(after, "boundaries", ()))
    return len({ns for ns, _ in first ^ second})


def stat_of(path) -> tuple | None:
    """(size, mtime_ns, st_ino) of `path` now, None when it cannot be stated."""
    try:
        st = os.stat(Path(path))
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns, st.st_ino
