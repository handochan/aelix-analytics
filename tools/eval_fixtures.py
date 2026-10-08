"""Generate the evaluation fixtures once, deterministically (Issue #41).

    python tools/eval_fixtures.py scale --scale S|M [--seed N] [--format v1] --out DIR
    python tools/eval_fixtures.py q10 [--seed N] [--format v1] --out DIR
    python tools/eval_fixtures.py q13 [--seed N] [--format v1] --out DIR
    python tools/eval_fixtures.py check DIR
    python tools/eval_fixtures.py environment --out FILE [--disk PATH] [--cache-state cold|warm|unknown]
        [--browser NAME=VERSION]

Run it in the development environment (`uv run --locked python tools/eval_fixtures.py ...`): it needs the
analysis libraries the kit runs scripts with.

What it makes (docs/BETA-QUALITY-GATES.md §2-§4, tools/eval_fixtures_data/README.md):

- `scale`: a lab.json project of the S or M size of BETA §4 (SCALES), holding the cases every fixture of §4
  has: corrections, withdrawals, data no longer preserved, a repeated chart id, reference cycles and long
  Korean content (CASES, with the judgments a reader of ADR-0006 gives them), and the imported files in
  input/.
- `q10`: the Q10 analysis-check examples (aggregation key, unit, denominator, period), a flawed and a sound
  analysis of each, with the expected verdicts in expected.json.
- `q13`: a synthetic reproduction of the reference session (docs/REVIEW-REFERENCE-SESSION-2026-09-18.md):
  EDA -> step profile -> per-time scores -> every sensor's band chart, two failed runs among six, with the
  expected judgments in expected.json and the source data in input/.

How the records are written: the kit's own /1 writer does it. A project is made with core.init_project, data
is imported with data.import_file, every run of q10 and q13 and three runs of each scale fixture are executed
by runner.execute, and every journal record is written by core.add_note or core.append_event. The other runs
of a scale fixture (S 27, M 297) are not executed (each executed run rebuilds the Workspace): their files and
their run record are made here and written with core.write_json, the record's outputs described by
runner.parquet_info and its warnings by runner.run_warnings, and their completion event with
core.append_event, in the key order runner._execute_locked and runner._finalize give (the tests compare them
with the executed runs' records). Their code (analysis_code) reads the record's inputs, saves its outputs
and visualizes its charts, but their output bytes come from the seed: running that code does not give them.

Determinism: the seed drives random.Random and the data; the kit's clock (core.now) is a planned clock while
the fixture is written, so no wall-clock time reaches a record. The same seed in the same environment gives
the same bytes but for the fields VOLATILE declares; `tree_digest` hashes the tree with those fields masked,
and manifest.json holds the digest. Generated at the same --out, only the executed runs' duration_s differs.
The kit's derived files (`.lab/cache/`, `report/`) are emptied at the end of a generation and of a `check`
(the fixture is cold for the kit) and are not part of the digest.

While it writes, AELIX_ANALYTICS_TRUST_FILE and AELIX_ANALYTICS_UV point into a temporary directory and the
kit's other variables (and their names before kit 0.3.0) are unset, so no trust list of the user's is
touched; they come back afterwards.

Exit status: 0 done, 1 the kit failed or `check` found a problem, 2 a usage error (an output directory that
is not new or empty, a format not available yet). A generation that fails removes what it wrote, so the
same --out can be given again.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import fnmatch
import hashlib
import io
import json
import math
import os
import platform
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "src"
DATA = Path(__file__).resolve().parent / "eval_fixtures_data"
SCRIPTS = DATA / "scripts"
SCHEMAS = DATA / "schemas"

GENERATOR = "tools/eval_fixtures.py"
# The version of what the fixtures contain: a change to the plan, the cases or the data changes it.
GENERATOR_VERSION = 1
MANIFEST_SCHEMA = "aelix-analytics.eval-manifest/1"
EXPECTED_SCHEMA = "aelix-analytics.eval-expected/1"
ENVIRONMENT_SCHEMA = "aelix-analytics.eval-environment/1"
FORMATS = ("v1",)
# A format the plan will be written in once its writer exists (design §8).
LATER_FORMATS = {"v2": "the analytics.event/2 writer of Issue #13 is not merged yet; only v1 can be "
                       "generated"}
KST = timezone(timedelta(hours=9))
PROJECT_DIR = "project"


@dataclass(frozen=True)
class Scale:
    runs: int                  # run directories with a run.json
    events: int                # journal.jsonl lines, every one a lab.event/1 record
    datasets: int              # imported datasets (data/raw/<name>.parquet with its record)
    bulk_rows: tuple           # rows of sensor_01, sensor_02, ... (datasets - 1 of them; sales_kr is the other)
    scripts: int               # scripts of the runs written here
    context_updates: int       # CONTEXT.md versions added along the way
    resolves: int              # notes that close an open question or todo
    minutes: tuple             # the planned clock's step, in minutes (low, high)


# docs/BETA-QUALITY-GATES.md §4. L (3,000 runs) is after the beta and not here (Issue #41).
SCALES = {
    "S": Scale(runs=30, events=300, datasets=5, bulk_rows=(50_000,) * 4, scripts=6, context_updates=2,
               resolves=6, minutes=(2, 9)),
    "M": Scale(runs=300, events=10_000, datasets=20,
               bulk_rows=(250_000,) + (4_000_000,) * 4 + (250_000,) * 14, scripts=24, context_updates=10,
               resolves=60, minutes=(2, 9)),
}
# The upper bound BETA §4 gives M's data.
M_DATA_LIMIT = 1 << 30

# Fields whose value is not the seed's to decide. `tree_digest` reads them as "<volatile>"; in a file `text`
# names, a traceback's path up to the project directory (File "…/project/ or File "…\project\) reads as
# File "<project>/, so a fixture moved or copied elsewhere has the same digest.
VOLATILE = [
    {"files": "project/runs/*/run.json", "fields": ["duration_s", "env"],
     "why": "duration_s of an executed run is its measured time; env holds the Python executable's path, the "
            "platform and the library versions"},
    {"files": "project/data/raw/*.meta.json", "fields": ["source.path", "source.modified_at"],
     "why": "the source is the fixture's own input/ file named by its absolute path (the same --out gives the "
            "same path), and its modification time, the planned time, is written in the machine's time zone"},
    {"files": "project/runs/*/stderr.txt", "text": "project_path",
     "why": "a failed run's traceback names the script by its absolute path, which holds where the fixture "
            "was generated"},
]
# A traceback's frame line, up to and including the project directory, with either separator.
TRACEBACK_PROJECT = re.compile(rb'File "[^"\r\n]*[\\/]' + PROJECT_DIR.encode() + rb'[\\/]')
# Not hashed: the two files about the generation itself, and the kit's derived files, which a read of the
# project may rebuild (the hash cache, the Workspace).
NOT_DIGESTED = ["manifest.json", "environment.json", "project/.lab/cache/*", "project/report/*"]

# The variables the kit reads, and their names before kit 0.3.0 (core.RENAMED_ENV).
ISOLATED_KEYS = ("AELIX_ANALYTICS_TRUST_FILE", "AELIX_ANALYTICS_UV", "AELIX_ANALYTICS_PROJECT",
                 "AELIX_ANALYTICS_RUNTIME_PATH", "AELIX_ANALYTICS_ACTOR", "AELIX_ANALYTICS_SESSION",
                 "AELIX_ANALYTICS_STYLE", "AELIX_LAB_TRUST_FILE", "AELIX_LAB_UV", "AELIX_LAB_PROJECT",
                 "AELIX_LAB_KIT", "LAB_PROJECT", "LAB_ACTOR", "LAB_SESSION", "LAB_STYLE")
LIBRARIES = ("duckdb", "polars", "pyarrow", "numpy", "plotly", "matplotlib", "markdown-it-py", "pygments",
             "jsonschema")


class UsageError(Exception):
    """A request this tool refuses before it writes anything (exit status 2)."""


class FixtureError(Exception):
    """The fixture could not be made as planned (exit status 1)."""


# --- the kit, its clock and the environment it runs in ------------------------------------------------

def load_kit() -> SimpleNamespace:
    """The checkout's kit (src/aelix_analytics)."""
    if str(KIT) not in sys.path:
        sys.path.insert(0, str(KIT))
    from aelix_analytics import core, data, relations, runner

    return SimpleNamespace(core=core, data=data, relations=relations, runner=runner)


class PlanClock:
    """The kit's clock while a fixture is written: the planned time, one millisecond later at each call.
    `at` moves it forward to a planned time (never back)."""

    def __init__(self, start: datetime) -> None:
        self.t = start

    def __call__(self) -> datetime:
        self.t += timedelta(milliseconds=1)
        return self.t

    def at(self, when: datetime) -> datetime:
        if when > self.t:
            self.t = when
        return self.t


@contextlib.contextmanager
def kit_session(scratch: Path, start: datetime):
    """The kit with a planned clock, a trust list and a missing uv inside `scratch`; the variables, the
    clock and the working directory come back afterwards."""
    kit = load_kit()
    saved = {key: os.environ.get(key) for key in ISOLATED_KEYS}
    saved_now, cwd = kit.core.now, os.getcwd()
    clock = PlanClock(start)
    try:
        for key in ISOLATED_KEYS:
            os.environ.pop(key, None)
        os.environ["AELIX_ANALYTICS_TRUST_FILE"] = str(scratch / "trust" / "lab-projects.json")
        os.environ["AELIX_ANALYTICS_UV"] = str(scratch / "no-uv" / "uv")
        kit.core.now = clock
        yield kit, clock
    finally:
        kit.core.now = saved_now
        os.chdir(cwd)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def write_lf(path: Path, text: str) -> None:
    """A text file of the fixture's own (manifest, expected, sources): UTF-8 and LF on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def parquet_bytes(frame) -> bytes:
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    return buffer.getvalue()


def prepare_out(out) -> Path:
    """`out` as a new or empty directory; anything else is refused before anything is written."""
    out = Path(out)
    if out.exists():
        if not out.is_dir():
            raise UsageError(f"{out} exists and is not a directory")
        if any(out.iterdir()):
            raise UsageError(f"{out} is not empty: give a new or empty directory (nothing was deleted)")
    out.mkdir(parents=True, exist_ok=True)
    return out


@contextlib.contextmanager
def output_directory(out):
    """`out` prepared (prepare_out); when the generation fails, what it wrote there is removed, so the same
    --out can be given again: the directory itself when it was new, else everything in it (it was empty)."""
    created = not Path(out).exists()
    out = prepare_out(out)
    try:
        yield out
    except BaseException:
        if created:
            shutil.rmtree(out, ignore_errors=True)
        else:
            for child in sorted(out.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    with contextlib.suppress(OSError):
                        child.unlink()
        raise


# Polars sums floating-point values in an order that depends on its thread pool, so a multi-threaded
# group_by can give other last bits each time. The scripts the kit runs here use Polars; on one thread they
# give the same bits. The command sets it before Polars is imported (main); the Python API refuses a process
# whose Polars has more threads (require_deterministic_process).
POLARS_THREADS_VARIABLE = "POLARS_MAX_THREADS"


def polars_threads() -> int:
    import polars as pl

    return pl.thread_pool_size()


def require_deterministic_process() -> None:
    """Polars on one thread (see POLARS_MAX_THREADS above): a process that imported it with more threads
    before this module was loaded cannot write a fixture whose bytes the seed decides."""
    threads = polars_threads()
    if threads != 1:
        raise UsageError(f"Polars runs on {threads} threads in this process, so the analysis results' last bits "
                         "may differ between generations; run tools/eval_fixtures.py as a command (it sets "
                         "POLARS_MAX_THREADS=1 before Polars loads) or set POLARS_MAX_THREADS=1 before importing "
                         "Polars")


def check_format(fmt: str) -> None:
    if fmt in LATER_FORMATS:
        raise UsageError(f"format {fmt}: {LATER_FORMATS[fmt]}")
    if fmt not in FORMATS:
        raise UsageError(f"unknown format {fmt!r}; known: {', '.join(FORMATS)}")


# --- the digest ---------------------------------------------------------------------------------------

def _tree_files(root: Path) -> list[tuple[str, Path]]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in filenames:
            path = Path(dirpath) / name
            found.append((path.relative_to(root).as_posix(), path))
    return sorted(found)


def _digested(rel: str) -> bool:
    return not any(fnmatch.fnmatchcase(rel, pattern) for pattern in NOT_DIGESTED)


def _mask(value: dict, dotted: str) -> None:
    *parents, last = dotted.split(".")
    for part in parents:
        value = value.get(part) if isinstance(value, dict) else None
    if isinstance(value, dict) and last in value:
        value[last] = "<volatile>"


def masked_bytes(rel: str, data: bytes) -> bytes:
    """A file's bytes as the digest reads them: VOLATILE's fields masked."""
    for rule in VOLATILE:
        if not fnmatch.fnmatchcase(rel, rule["files"]):
            continue
        if "fields" in rule:
            value = json.loads(data)
            for dotted in rule["fields"]:
                _mask(value, dotted)
            return json_text(value).encode("utf-8")
        return TRACEBACK_PROJECT.sub(b'File "<project>/', data)
    return data


def tree_digest(out) -> str:
    """sha256 over (path, sha256 of the masked bytes) of every digested file, in path order."""
    out = Path(out)
    digest = hashlib.sha256()
    for rel, path in _tree_files(out):
        if not _digested(rel):
            continue
        content = masked_bytes(rel, path.read_bytes())
        digest.update(f"{rel}\0{sha256_bytes(content)}\n".encode())
    return "sha256:" + digest.hexdigest()


# --- the measurement environment ------------------------------------------------------------------------

def _not_measured(reason: str) -> dict:
    return {"status": "not_measured", "reason": reason}


def _memory_bytes():
    with contextlib.suppress(AttributeError, ValueError, OSError):
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    with contextlib.suppress(Exception):
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                        ("avail_extended", ctypes.c_ulonglong)]

        status = Status()
        status.length = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return int(status.total)
    return _not_measured("no portable way to read it on this platform")


def _sqlite() -> dict:
    connection = sqlite3.connect(":memory:")
    try:
        options = [row[0] for row in connection.execute("PRAGMA compile_options")]
        fts5 = True
        try:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        except sqlite3.OperationalError:
            fts5 = False
        json_ok = True
        try:
            connection.execute("SELECT json('{}')")
        except sqlite3.OperationalError:
            json_ok = False
    finally:
        connection.close()
    return {"version": sqlite3.sqlite_version, "fts5": fts5, "json": json_ok, "compile_options": options}


def environment_record(disk_path, *, cache_state: str = "unknown", browser: str | None = None,
                       generation: dict | None = None) -> dict:
    """What a measurement on this machine runs on (design §6). `cache_state` is the measurement's: cold (the
    kit's caches absent: the first read), warm (a repeat in the same process and machine) or unknown.
    `browser` is NAME=VERSION as the measurer gives it; it is not detected."""
    if cache_state not in ("cold", "warm", "unknown"):
        raise UsageError(f"cache state {cache_state!r}: cold, warm or unknown")
    disk_path = Path(disk_path)
    probe = disk_path if disk_path.exists() else disk_path.parent
    usage = shutil.disk_usage(probe)
    libraries = {}
    for name in LIBRARIES:
        try:
            libraries[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            libraries[name] = None
    kit = load_kit()
    if browser:
        name, _, version = browser.partition("=")
        browser_info = {"name": name, "version": version or None}
    else:
        browser_info = _not_measured("not given: pass --browser NAME=VERSION when a browser is measured")
    record = {
        "schema": ENVIRONMENT_SCHEMA,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": {"system": platform.system(), "release": platform.release(), "version": platform.version(),
                    "machine": platform.machine(), "processor": platform.processor() or None,
                    "cpu_count": os.cpu_count(), "memory_bytes": _memory_bytes()},
        "disk": {"path": str(disk_path.resolve()), "total_bytes": usage.total, "free_bytes": usage.free,
                 "device": _not_measured("the standard library has no portable way to name the device or "
                                         "its kind (SSD, HDD, network)"),
                 "filesystem": _not_measured("the standard library has no portable way to read it")},
        "python": {"version": platform.python_version(), "implementation": platform.python_implementation(),
                   "executable": sys.executable},
        "sqlite": _sqlite(),
        "browser": browser_info,
        "libraries": libraries,
        "kit": {"version": kit.core.KIT_VERSION, "sha256": kit.runner.kit_sha()},
        "threads": {"polars": polars_threads(),
                    "note": "the generator runs Polars on one thread (POLARS_MAX_THREADS=1) so its results' bits "
                            "are the seed's; a measurement records the threads it ran with"},
        "cache_state": {"measurement": cache_state, "kit_caches": "cold" if generation else cache_state,
                        "os_file_cache": "unknown",
                        "definition": "cold: the kit's caches (.lab/cache/, report/) are absent, the first read; "
                                      "warm: a repeat in the same process and machine after one full read. "
                                      "The OS file cache is not dropped (that needs privileges)."},
    }
    if generation is not None:
        record["generation"] = generation
    return record


# --- Korean text the records hold -------------------------------------------------------------------

SUBJECTS = ["서울 지역 매출", "온라인 채널 비중", "주말 주문", "재구매 고객", "센서 07의 압력", "야간 배송 주문",
            "신규 고객 전환", "반품률", "평균 주문 금액", "부산 매장 매출", "2분기 매출", "센서 12의 온도",
            "장바구니 이탈", "프로모션 기간 주문", "모바일 세션", "대구 지역 객단가", "배송 지연 건수"]
PREDICATES = ["지난 4주 동안 꾸준히 늘었다", "다른 집단보다 뚜렷하게 낮다", "주중과 주말의 차이가 크다",
              "이상치를 빼면 변화가 거의 없다", "프로모션이 끝난 뒤 원래 수준으로 돌아왔다", "분기 말에 몰려 있다",
              "기준 기간보다 12% 높다", "표본이 작아 판단을 미룬다", "데이터 수집 방식이 바뀐 시점과 겹친다",
              "정의를 다시 확인해야 한다", "지역 간 편차가 지난달보다 줄었다", "같은 기간끼리 비교하면 차이가 작다"]
QUALIFIERS = ["(최근 4주 기준)", "(이상치 제외)", "(중복 주문 제거 후)", "(합성 데이터)", "(지역별 가중 평균)", ""]
KIND_TEXT = {
    "finding": "{s} — {p} {q}",
    "note": "{s} — {p}. 다음 run에서 다시 본다 {q}",
    "decision": "{s} — 주간 단위로 집계하기로 한다 {q}",
    "assumption": "{s} — 반품과 취소는 데이터에 없다고 가정한다 {q}",
    "question": "{s} — 계절 효과로 설명되는가? {q}",
    "todo": "{s} — 같은 기간끼리 다시 비교한다 {q}",
    "gap": "{s} — 원천 데이터의 갱신 주기를 알 수 없어 확인이 막혔다 {q}",
}
NOTE_WEIGHTS = {"finding": 26, "note": 26, "decision": 10, "assumption": 8, "question": 8, "todo": 8,
                "gap": 4}


def korean_sentence(rng: random.Random, kind: str = "finding") -> str:
    text = KIND_TEXT[kind].format(s=rng.choice(SUBJECTS), p=rng.choice(PREDICATES), q=rng.choice(QUALIFIERS))
    return text.strip()


def korean_paragraph(rng: random.Random, chars: int) -> str:
    """Korean prose of at least `chars` characters, cut to exactly `chars`."""
    parts: list[str] = []
    while sum(len(p) + 1 for p in parts) < chars:
        parts.append(f"{rng.choice(SUBJECTS)}: {rng.choice(PREDICATES)} {rng.choice(QUALIFIERS)}".strip() + ".")
    return " ".join(parts)[:chars]


def scale_context(title: str, rng: random.Random, sections: int) -> str:
    """A long Korean CONTEXT.md: pinned sections first, then more sections than the prompt budget holds."""
    lines = [f"# {title}", "", "## 목표 <!-- pin -->", "- 지역·채널·센서별 지표의 변화를 설명하고 다음 분기 계획의 근거를 남긴다.",
             "", "## 핵심 질문 <!-- pin -->", "- 온라인 비중의 증가는 모든 지역에서 같은 속도인가?",
             "- 센서 이상 후보는 장비 문제인가, 측정 방식의 변화인가?", "", "## 다음 할 일 <!-- pin -->",
             "- 같은 기간끼리 비교하는 기준을 정한다.", ""]
    for n in range(1, sections + 1):
        lines += [f"## 분석 메모 {n}", korean_paragraph(rng, 900), ""]
    lines += ["## 정의와 규칙", "- 매출액: 할인 뒤 금액(원).", "- 기간: 주문일 기준(KST).", ""]
    return "\n".join(lines)


# --- data sources -------------------------------------------------------------------------------------

REGIONS = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종"]


def write_sales_kr(path: Path, rng: random.Random) -> int:
    """A small table with Korean column names and values, as a CSV a person would import (LF rows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["일자", "지역", "채널", "매출액", "주문수"])
        day = date(2025, 1, 1)
        while day.year == 2025:
            for region_index, region in enumerate(REGIONS):
                for channel in ("온라인", "매장"):
                    base = 4_000_000 - region_index * 380_000
                    amount = int(base * (1.15 if channel == "온라인" else 0.85) * rng.uniform(0.7, 1.3))
                    writer.writerow([day.isoformat(), region, channel, amount, max(1, amount // 48_000)])
                    rows += 1
            day += timedelta(days=1)
    return rows


def write_sensor_parquet(path: Path, rows: int, seed_value: int) -> None:
    """A bulk sensor table written by DuckDB (the same bytes for the same seed and DuckDB version)."""
    import duckdb

    path.parent.mkdir(parents=True, exist_ok=True)
    target = str(path).replace("'", "''")
    connection = duckdb.connect()
    try:
        connection.execute("SET threads = 1")
        connection.execute(
            f"""COPY (
                  SELECT i AS row_id,
                         TIMESTAMP '2026-01-01 00:00:00' + to_seconds(i * 37) AS ts,
                         'sensor_' || lpad(CAST(hash(i, {seed_value}, 1) % 16 AS VARCHAR), 2, '0') AS sensor,
                         round(CAST(hash(i, {seed_value}, 2) % 2000000 AS DOUBLE) / 100000.0 - 10.0, 5) AS value,
                         CAST(hash(i, {seed_value}, 3) % 100 AS INTEGER) AS qty
                  FROM range({rows}) t(i)
                ) TO '{target}' (FORMAT parquet, COMPRESSION zstd)""")
    finally:
        connection.close()


# --- the /1 writer ------------------------------------------------------------------------------------

class V1Writer:
    """A project written by the kit's /1 writer: init, import and execute through the kit, journal records
    through core.add_note and core.append_event, and the records of runs not executed through
    core.write_json in the runner's key order."""

    def __init__(self, kit: SimpleNamespace, clock: PlanClock, root: Path) -> None:
        self.kit = kit
        self.clock = clock
        self.root = root
        self.project = None
        self.env = None
        self.datasets: dict[str, dict] = {}      # name -> path, sha256, bytes, rows, source, columns
        self.derived: dict[str, tuple[str, int]] = {}  # path -> (sha256, bytes) of the bytes on disk
        self.runs: list[dict] = []               # every run: id, status, script, charts (keys)
        self.kit_runs: list[str] = []
        self.chart_keys: list[str] = []          # keys of successful runs' charts, oldest first

    # the clock
    def at(self, when: datetime) -> None:
        self.clock.at(when)

    def last_seq(self) -> int:
        return self.kit.core._last_seq(self.project.journal)

    # project, context, data
    def init(self, title: str, goal: str) -> None:
        self.project = self.kit.core.init_project(self.root, title=title, goal=goal, origin=self.kit.core.Origin("human", "sdk"))

    def write_context(self, text: str, actor: str = "human") -> dict | None:
        self.kit.core.write_text(self.project.context, text)
        return self.kit.core.sync_context(self.project, origin=self.kit.core.Origin(actor, "sdk"))

    def import_dataset(self, source: Path, name: str, description: str, when: datetime) -> dict:
        stamp = when.timestamp()
        os.utime(source, (stamp, stamp))
        result = self.kit.data.import_file(self.project, str(source), name=name, description=description,
                                           origin=self.kit.core.Origin("human", "sdk"))
        meta = result["dataset"]
        self.datasets[name] = {"name": name, "path": meta["path"], "sha256": meta["sha256"],
                               "bytes": meta["bytes"], "rows": meta["rows"],
                               "source": source.suffix.lstrip("."), "columns": [c["name"] for c in meta["columns"]]}
        return meta

    # runs the kit executes
    def execute(self, source: Path, script_rel: str, title: str, actor: str = "agent") -> dict:
        target = self.project.root / script_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        result = self.kit.runner.execute(self.project, script=script_rel, title=title, origin=self.kit.core.Origin(actor, "sdk"))
        record = result["run"]
        if result.get("workspace_error"):
            raise FixtureError(f"the kit could not build the Workspace after {script_rel}: "
                               f"{result['workspace_error']}")
        if record.get("finalize_errors"):
            raise FixtureError(f"the kit could not finish the record of {script_rel}: {record['finalize_errors']}")
        self.kit_runs.append(record["id"])
        self._remember(record)
        if self.env is None:
            self.env = record.get("env")
        for out in record.get("outputs") or []:
            if out.get("change") != "deleted" and record["status"] == "ok":
                self.derived[out["path"]] = (out["sha256"], out["bytes"])
        return record

    def rows(self, path: str) -> int:
        """The rows of a Parquet file of the project."""
        return self.kit.runner.parquet_info(self.project.root / path)["rows"]

    def _remember(self, record: dict) -> None:
        keys = [f"{record['script']['path']}::{c['id']}" for c in record.get("charts") or []]
        self.runs.append({"id": record["id"], "status": record["status"], "script": record["script"]["path"],
                          "charts": keys})
        if record["status"] == "ok":
            self.chart_keys += [k for k in keys if k not in self.chart_keys]

    # journal records
    def note(self, kind: str, text: str, refs=(), resolves: str | None = None, actor: str = "agent") -> str:
        event = self.kit.core.add_note(self.project, kind, text, refs=list(refs), resolves=resolves, origin=self.kit.core.Origin(actor, "sdk"))
        return event["id"]

    # runs not executed
    def input_entry(self, path: str, digest: bool = True) -> dict:
        """An input as the runner records it: path, the hash and size of the bytes read, how, external. The
        code reads it with analytics.read (analysis_code): a dataset by its name, a derived table by its path,
        a literal the runner's static scan of the code also finds ("scan")."""
        via = ["lab.read", "polars.read_parquet"] + (["scan"] if path.startswith("data/derived/") else [])
        known = {d["path"]: (d["sha256"], d["bytes"]) for d in self.datasets.values()}
        if path in known:
            sha, size = known[path]
        elif path in self.derived:
            sha, size = self.derived[path]
        else:
            file = self.project.root / path
            sha, size = sha256_file(file), file.stat().st_size
        return {"path": path, "sha256": sha if digest else None, "bytes": size, "via": sorted(via),
                "external": False}

    def written_run(self, *, script_rel: str, code: str, title: str, started: datetime, duration: float,
                    status: str = "ok", inputs=(), outputs=(), charts=(), error: dict | None = None,
                    stdout: str = "", stderr: str = "", actor: str = "agent", notes=()) -> tuple[str, str]:
        """A run the kit did not execute, recorded as the runner records one. `outputs` are (path, bytes);
        `charts` are dicts with id, title, line, figure (a plotly figure dict) and optionally data (a Polars
        frame). Returns the run id and the id of its completion event."""
        core, runner, project = self.kit.core, self.kit.runner, self.project
        code_bytes = code.encode("utf-8")
        script = project.root / script_rel
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_bytes(code_bytes)
        self.at(started)
        run_id = core.new_run_dir(project, Path(script_rel).stem)
        run_dir = project.runs / run_id
        snapshot = run_dir / "code" / script_rel
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(code_bytes)
        # The planned instants, not the clock's: two runs of a cycle start at the same one.
        started_at = started.isoformat(timespec="milliseconds")
        ended = started + timedelta(seconds=duration)
        chart_records = []
        for chart in charts:
            charts_dir = run_dir / "charts"
            charts_dir.mkdir(parents=True, exist_ok=True)
            figure = charts_dir / f"{chart['id']}.plotly.json"
            core.write_text(figure, json.dumps(chart["figure"], ensure_ascii=False, separators=(",", ":")))
            record = {"id": chart["id"], "kind": "plotly", "title": chart["title"], "caption": chart.get("caption"),
                      "via": "lab.chart", "files": {"plotly": project.rel(figure)}, "line": chart["line"]}
            frame = chart.get("data")
            if frame is not None:
                attached = charts_dir / f"{chart['id']}.data.parquet"
                attached.write_bytes(parquet_bytes(frame))
                record.update(data=project.rel(attached), data_rows=frame.height, data_columns=frame.columns[:60])
            chart_records.append(record)
        output_records = []
        for path, content in sorted(outputs):
            file = project.root / path
            change = "modified" if file.exists() else "created"
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
            entry = {"path": path, "change": change, "bytes": len(content), "sha256": sha256_bytes(content)}
            if path.endswith(".parquet"):
                entry.update(runner.parquet_info(file))
            entry["via"] = "lab.save"
            output_records.append(entry)
            if status == "ok":
                self.derived[path] = (entry["sha256"], entry["bytes"])
        stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"
        core.write_text(stdout_path, stdout)
        core.write_text(stderr_path, stderr)
        self.at(ended)
        record = {"schema": core.SCHEMA_RUN, "id": run_id, "title": title, "status": status,
                  "started_at": started_at, "actor": actor, "session": None, "rerun_of": None,
                  "script": {"path": script_rel, "sha256": sha256_bytes(code_bytes),
                             "snapshot": project.rel(snapshot)},
                  "ended_at": ended.isoformat(timespec="milliseconds"), "duration_s": round(duration, 3),
                  "modules": [],
                  "inputs": sorted(inputs, key=lambda entry: entry["path"]), "outputs": output_records,
                  "charts": chart_records, "error": error, "stdout": project.rel(stdout_path),
                  "stderr": project.rel(stderr_path), "env": self.env}
        record["warnings"] = runner.run_warnings(record, project)
        core.write_json(run_dir / "run.json", record, platform=project.platform)
        written = sorted({o["path"] for o in output_records})
        read = sorted({i["path"] for i in record["inputs"]} - set(written))
        refs = {"runs": [run_id],
                "charts": [core.chart_key(record, c) for c in chart_records] if status == "ok" else [],
                "data": (written + read)[:core.MAX_EVENT_REFS]}
        text = f"{title} — {status}"
        if error:
            text += f" ({error['summary']})"
        event = core.append_event(project, "run", text, refs=refs, origin=core.Origin(actor, "sdk"))
        for kind, note_text in notes:
            core.add_note(project, kind, note_text, refs=[run_id], origin=core.Origin(actor, "sdk"))
        self._remember(record)
        return run_id, event["id"]


# --- the scale fixtures -------------------------------------------------------------------------------

TOPICS = ["region_trend", "channel_mix", "sensor_daily", "anomaly_scan", "cohort_view", "funnel_check",
          "price_band", "weekly_kpi", "basket_size", "delivery_delay", "repeat_rate", "store_compare"]

def _key_value(source: str) -> tuple[str, str]:
    """The column a source's rows are grouped by and the column averaged: sales_kr's region and amount, a
    sensor table's sensor and value, a derived table's key and value."""
    if source == "sales_kr":
        return "지역", "매출액"
    if source.startswith("sensor_"):
        return "sensor", "value"
    return "key", "value"


def analysis_code(title: str, source: str, *, upstream: str | None = None, save: str | None = None,
                  charts=(), group_by: str | None = None) -> str:
    """The code of a run written here (not executed), saying what its record says: it reads `source` (a
    dataset by its name, a derived table by its path) and `upstream`, averages per key into a table of key
    and value, saves it as `save` and visualizes `charts`, (id, title, with data) each, and prints the rows
    the record's stdout has. `group_by` names a column the source lacks, for a run that fails there.
    Running it does not give the bytes the record holds: the fixture's tables are made from the seed."""
    key, value = _key_value(source)
    lines = [f'"""{title} (평가 fixture의 스크립트, kit이 실행하지 않은 run)."""', ""]
    if charts:
        lines.append("import plotly.graph_objects as go")
    lines += ["import polars as pl", "", "import aelix_analytics as analytics", "",
              f'frame = analytics.read("{source}")']
    if upstream:
        lines += [f'upstream = analytics.read("{upstream}")', 'print(f"upstream rows {upstream.height}")']
    lines.append(f'table = frame.group_by(pl.col("{group_by or key}").alias("key"), maintain_order=True)'
                 f'.agg(pl.col("{value}").mean().alias("value")).sort("key")')
    if save:
        lines.append(f'analytics.save(table, "{save}")')
    for chart_id, chart_title, with_data in charts:
        lines += ['fig = go.Figure(go.Bar(x=table["key"].to_list(), y=table["value"].to_list()))',
                  f'analytics.visualize(fig, "{chart_id}", title="{chart_title}"' + (", data=table)" if with_data
                                                                                    else ")")]
    lines.append('print(f"rows {table.height}")')
    return "\n".join(lines) + "\n"


# A case's table (ScaleFixture._small_table) has this many keys, so its code prints this many rows.
SMALL_TABLE_ROWS = 6
SMALL_TABLE_STDOUT = f"rows {SMALL_TABLE_ROWS}\n"


def _script_lines(code: str, needle: str) -> int:
    return next(n for n, line in enumerate(code.splitlines(), 1) if needle in line)


class ScaleFixture:
    """The plan of a scale fixture and its execution through V1Writer (design §3 and §5)."""

    def __init__(self, scale: str, seed: int, out: Path, scratch: Path) -> None:
        self.scale_name = scale
        self.spec = SCALES[scale]
        self.seed = seed
        self.out = out
        self.scratch = scratch
        self.rng = random.Random(f"eval-fixtures/scale/{scale}/{seed}")
        self.start = datetime(2026, 8, 3, 9, 0, tzinfo=KST)
        self.when = self.start
        self.cases: dict[str, dict] = {}
        self.open_items: list[str] = []
        self.event_ids: list[str] = []
        self.scripts: list[dict] = []
        self.generic_runs = 0
        self.timings: dict[str, float] = {}
        self.long_text_chars = 6000

    # the planned time
    def step(self) -> datetime:
        low, high = self.spec.minutes
        self.when += timedelta(minutes=self.rng.randint(low, high), seconds=self.rng.randint(0, 59))
        self.w.at(self.when)
        return self.when

    def case(self, case_id: str) -> dict:
        return self.cases.setdefault(case_id, {"records": {}})

    # sources
    def make_sources(self) -> list[tuple[str, Path, str]]:
        """The files imported, under input/ as q10's and q13's are: an import records its source's absolute
        path, which is then the same for the same --out."""
        sources = self.out / "input"
        out = [("sales_kr", sources / "sales_kr.csv", "지역·채널별 일 매출(2025년, 한국어 열, 합성)")]
        write_sales_kr(out[0][1], self.rng)
        for index, rows in enumerate(self.spec.bulk_rows, start=1):
            name = f"sensor_{index:02d}"
            path = sources / f"{name}.parquet"
            write_sensor_parquet(path, rows, self.seed * 1000 + index)
            out.append((name, path, f"센서 측정값 {rows:,}행(합성)"))
        return out

    def run(self) -> dict:
        clock_start = time.perf_counter()
        sources = self.make_sources()
        self.timings["sources"] = time.perf_counter() - clock_start
        phase = time.perf_counter()
        with kit_session(self.scratch, self.start) as (kit, clock):
            self.kit = kit
            self.w = V1Writer(kit, clock, self.out / PROJECT_DIR)
            self.w.init(f"평가 fixture {self.scale_name} (seed {self.seed})",
                        "지역·채널·센서 지표의 변화를 설명한다")
            self.step()
            self.w.write_context(scale_context(f"평가 fixture {self.scale_name}", self.rng, sections=20))
            for name, path, description in sources[:2]:
                self.step()
                self.w.import_dataset(path, name, description, self.when)
            for script, title in (("k01_region_summary.py", "지역별 매출 요약"),
                                  ("k02_sensor_profile.py", "센서별 시간대 평균"),
                                  ("k03_join_profiles.py", "지역 요약과 센서 프로파일 결합")):
                self.step()
                self.w.execute(SCRIPTS / "scale" / script, f"scripts/{script}", title)
            self.timings["kit_phase"] = time.perf_counter() - phase
            phase = time.perf_counter()
            self.plan_and_write(sources[2:])
            self.after_plan()
            self.timings["written_phase"] = time.perf_counter() - phase
        return self.finish()

    # the plan
    def plan_and_write(self, later_imports) -> None:
        spec, w = self.spec, self.w
        events_now, runs_now = w.last_seq(), len(w.runs)
        steps: list[tuple[float, int, int, object]] = []  # (position, events, runs, action)
        for index, (name, path, description) in enumerate(later_imports):
            steps.append((0.01 + 0.07 * index / max(1, len(later_imports)), 1, 0,
                          lambda n=name, p=path, d=description: w.import_dataset(p, n, d, self.when)))
        for n in range(spec.context_updates):
            steps.append(((n + 1) / (spec.context_updates + 1), 1, 0, lambda n=n: self.context_update(n + 1)))
        steps += self.case_steps()
        case_runs = sum(s[2] for s in steps)
        generic = spec.runs - runs_now - case_runs
        if generic < 0:
            raise FixtureError("the plan has more runs than the scale")
        self.make_scripts()
        for index in range(generic):
            noted = index % 15 != 7 and self.rng.random() < 0.3  # failed runs (index % 15 == 7) record none
            steps.append(((index + 0.5) / max(1, generic), 1 + noted, 1,
                          lambda i=index, n=noted: self.generic_run(i, n)))
        planned_events = sum(s[1] for s in steps) + 1  # the last CONTEXT version
        notes = spec.events - events_now - planned_events
        if notes < 0:
            raise FixtureError("the plan has more events than the scale")
        steps.sort(key=lambda s: s[0])
        gaps = [0] * (len(steps) + 1)
        for _ in range(notes):
            gaps[self.rng.randrange(len(gaps))] += 1
        resolve_every = max(1, notes // max(1, spec.resolves))
        written = 0
        for gap, (_, _, _, action) in zip(gaps, [*steps, (None, 0, 0, None)], strict=True):
            for _ in range(gap):
                written += 1
                self.generic_note(resolve=written % resolve_every == 0)
            if action is not None:
                self.step()
                action()
        self.step()
        self.context_update(spec.context_updates + 1)

    def context_update(self, n: int) -> None:
        text = self.w.project.context.read_text(encoding="utf-8")
        text += f"\n## 진행 메모 {n}\n- {korean_sentence(self.rng, 'finding')}\n- {korean_sentence(self.rng, 'todo')}\n"
        if self.w.write_context(text) is None:
            raise FixtureError("a CONTEXT.md update left no version")

    def make_scripts(self) -> None:
        for index in range(self.spec.scripts):
            topic = TOPICS[index % len(TOPICS)]
            stem = f"s{index + 1:02d}_{topic}"
            self.scripts.append({"stem": stem, "path": f"scripts/{stem}.py", "topic": topic, "version": 0,
                                 "table": f"data/derived/t{index + 1:02d}_{topic}.parquet",
                                 "upstream": index - 1 if index and index % 3 else None,
                                 "chart": topic.replace("_", "-"), "last_bytes": None, "code": None,
                                 "dataset": None, "upstream_table": None, "detail": False})

    def script_code(self, script: dict, group_by: str | None = None) -> str:
        """A version of a generic script: its dataset, its upstream table, its table and its charts (the
        detail chart only in some versions)."""
        title = f"{script['topic']} 요약 차트"
        charts = [(script["chart"], title, True)]
        if script["detail"]:
            charts.append((f"{script['chart']}-detail", f"{title} (상세)", False))
        return analysis_code(f"{script['topic']} 요약, 버전 {script['version']}", script["dataset"],
                             upstream=script["upstream_table"], save=Path(script["table"]).stem, charts=charts,
                             group_by=group_by)

    def generic_run(self, index: int, noted: bool) -> None:
        w, rng = self.w, self.rng
        # Every script runs twice before any is chosen at random, so each has versions.
        script = self.scripts[index % len(self.scripts)] if index < 2 * len(self.scripts) else rng.choice(self.scripts)
        if script["code"] is None or rng.random() < 0.15:
            names = sorted(w.datasets)
            script["dataset"] = names[(self.scripts.index(script) + script["version"]) % len(names)]
            upstream = None
            if script["upstream"] is not None:
                candidate = self.scripts[script["upstream"]]["table"]
                upstream = candidate if candidate in w.derived else None
            script["upstream_table"] = upstream
            script["version"] += 1
            script["detail"] = rng.random() < 0.5
            script["code"] = self.script_code(script)
        code = script["code"]
        dataset = w.datasets[script["dataset"]]
        inputs = [w.input_entry(dataset["path"])]
        stdout = ""
        if script["upstream_table"]:
            inputs.append(w.input_entry(script["upstream_table"]))
            stdout = f"upstream rows {w.rows(script['upstream_table'])}\n"
        failed = index % 15 == 7
        title = f"{script['topic']} 요약 #{index + 1}"
        duration = round(rng.uniform(0.4, 25.0), 3)
        if failed:
            # This run's code groups by a column the dataset lacks; the next run of the script has the
            # version's code again.
            missing = f"{script['topic']}_코드"
            code = self.script_code(script, group_by=missing)
            line = _script_lines(code, "group_by")
            message = (f'unable to find column "{missing}"; valid columns: '
                       f"{json.dumps(dataset['columns'], ensure_ascii=False)}")
            error = {"type": "ColumnNotFoundError", "message": message, "line": line,
                     "summary": f"ColumnNotFoundError: {message}"}
            # As a failed run's log is: the script named by its absolute path (VOLATILE masks it).
            stderr = (f'Traceback (most recent call last):\n  File "{w.project.root / script["path"]}", line {line}, '
                      f"in <module>\npolars.exceptions.{error['summary']}\n")
            w.written_run(script_rel=script["path"], code=code, title=title, started=self.when, duration=duration,
                          status="error", inputs=inputs, error=error, stdout=stdout, stderr=stderr)
            self.generic_runs += 1
            return
        import polars as pl

        rows = rng.randint(4, 16)
        if script["last_bytes"] is not None and rng.random() < 0.1:
            content = script["last_bytes"]  # the same bytes again: two producers of one file
        else:
            keys = [f"k{n:02d}" for n in range(rows)]
            content = parquet_bytes(pl.DataFrame({"key": keys, "value": [round(rng.uniform(0, 100), 3) for _ in keys]}))
        script["last_bytes"] = content
        frame = pl.read_parquet(io.BytesIO(content))
        figure = {"data": [{"type": "bar", "x": frame["key"].to_list(), "y": frame["value"].to_list()}],
                  "layout": {"title": {"text": f"{script['topic']} 요약 차트"}}}
        charts = [{"id": script["chart"], "title": f"{script['topic']} 요약 차트", "line": _script_lines(code, f'"{script["chart"]}"'),
                   "figure": figure, "data": frame}]
        if script["detail"]:
            charts.append({"id": f"{script['chart']}-detail", "title": f"{script['topic']} 요약 차트 (상세)",
                           "line": _script_lines(code, "-detail"), "figure": figure})
        notes = [(rng.choice(["finding", "note"]), korean_sentence(rng))] if noted else []
        w.written_run(script_rel=script["path"], code=code, title=title, started=self.when, duration=duration,
                      inputs=inputs, outputs=[(script["table"], content)], charts=charts,
                      stdout=f"{stdout}rows {frame.height}\n", notes=notes)
        self.generic_runs += 1

    def generic_note(self, resolve: bool) -> None:
        w, rng = self.w, self.rng
        self.step()
        if resolve and self.open_items:
            target = self.open_items.pop(0)
            if rng.random() < 0.5:
                self.event_ids.append(w.note("resolve", f"해결: {korean_sentence(rng)}", resolves=target))
            else:
                self.event_ids.append(w.note("finding", korean_sentence(rng), resolves=target))
            return
        kinds, weights = zip(*NOTE_WEIGHTS.items(), strict=True)
        kind = rng.choices(kinds, weights)[0]
        refs: list[str] = []
        if w.runs and rng.random() < 0.5:
            refs.append(rng.choice(w.runs)["id"])
        if self.event_ids and rng.random() < 0.3:
            refs.append(rng.choice(self.event_ids))
        if w.chart_keys and rng.random() < 0.3:
            refs.append(rng.choice(w.chart_keys))
        if rng.random() < 0.3:
            refs.append(rng.choice(sorted(w.derived) or [w.datasets["sales_kr"]["path"]]))
        if rng.random() < 0.01:
            refs.append("주간-보고서-초안")  # a name the reader cannot resolve
        text = korean_sentence(rng, kind)
        if rng.random() < 0.01:
            text = korean_paragraph(rng, rng.randint(1500, 3800))
        event_id = w.note(kind, text, refs=refs, actor=rng.choice(["agent", "agent", "human"]))
        self.event_ids.append(event_id)
        if kind in ("question", "todo"):
            self.open_items.append(event_id)

    # the cases (design §5)
    def case_steps(self) -> list[tuple[float, int, int, object]]:
        return [
            (0.03, 1, 0, self.old_finding),
            (0.05, 1, 0, self.correction_original),
            (0.10, 1, 0, self.question_open),
            (0.15, 1, 0, self.withdrawal_original),
            (0.20, 1, 0, self.long_note),
            (0.25, 1, 1, lambda: self.trend_run(1)),
            (0.30, 1, 1, lambda: self.unpreserved_run("overwritten", 1)),
            (0.35, 1, 1, lambda: self.unpreserved_run("deleted", 1)),
            (0.36, 1, 1, self.unpreserved_reader),
            (0.40, 1, 1, lambda: self.unpreserved_run("chart_gone", 1)),
            (0.40, 1, 0, self.question_close),
            (0.45, 1, 1, lambda: self.unpreserved_run("no_digest", 1)),
            (0.50, 1, 1, lambda: self.unpreserved_run("code_changed", 1)),
            (0.55, 1, 1, lambda: self.trend_run(2)),
            (0.56, 2, 2, self.cycle_runs),
            (0.58, 2, 0, self.cycle_notes),
            (0.60, 1, 1, lambda: self.unpreserved_run("overwritten", 2)),
            (0.62, 1, 1, lambda: self.same_bytes_run(1)),
            (0.63, 1, 1, lambda: self.same_bytes_run(2)),
            (0.64, 1, 1, self.same_bytes_reader),
            (0.65, 1, 1, self.region_total_run),
            (0.70, 1, 0, self.trend_note),
            (0.75, 1, 0, self.bare_name_note),
            (0.80, 1, 0, self.withdrawal),
            (0.85, 1, 0, self.display_note),
            (0.93, 1, 0, self.correction),
        ]

    def old_finding(self) -> None:
        text = "핵심 발견: 반품률이 높은 주문은 '야간 배송'(22시–02시) 구간에 몰려 있다. 낮 배송의 2.4배다."
        self.case("old_finding")["records"]["finding"] = self.w.note("finding", text, refs=[self.w.kit_runs[0]])

    def correction_original(self) -> None:
        text = "세종 지역의 3분기 성장률은 12.4%로 전 지역 가운데 가장 높다."
        self.case("correction")["records"]["original"] = self.w.note("finding", text, refs=[self.w.kit_runs[0]])

    def correction(self) -> None:
        original = self.case("correction")["records"]["original"]
        text = (f"정정: 세종 지역의 3분기 성장률은 9.8%다(중복 주문을 지운 뒤 다시 계산). 앞선 발견 {original}의 "
                "12.4%를 대체한다. 데이터 기간은 그대로 2025-07-01–2025-09-30이다.")
        self.case("correction")["records"]["correction"] = self.w.note("finding", text, refs=[original],
                                                                       resolves=original)

    def withdrawal_original(self) -> None:
        text = "결정: 지역 비교는 2025년 연간 합계로 한다."
        self.case("withdrawal")["records"]["decision"] = self.w.note("decision", text, actor="human")

    def withdrawal(self) -> None:
        decision = self.case("withdrawal")["records"]["decision"]
        text = f"철회: {decision}의 결정(연간 합계로 비교)을 철회한다. 9월 데이터가 부분 월이라 같은 기간끼리 비교해야 한다."
        self.case("withdrawal")["records"]["withdrawal"] = self.w.note("resolve", text, resolves=decision,
                                                                       actor="human")

    def question_open(self) -> None:
        self.case("question_closed")["records"]["question"] = self.w.note(
            "question", "부산 매장 매출의 감소는 계절 효과인가, 매장 수 변화인가?", actor="human")

    def question_close(self) -> None:
        question = self.case("question_closed")["records"]["question"]
        self.case("question_closed")["records"]["answer"] = self.w.note(
            "finding", "부산 매장 매출의 감소는 매장 두 곳의 폐점과 겹친다. 계절 효과는 작다.", refs=[self.w.kit_runs[0]],
            resolves=question)

    def long_note(self) -> None:
        text = korean_paragraph(self.rng, self.long_text_chars)
        record = self.case("long_korean")["records"]
        record["note"] = self.w.note("note", text)
        record["original_chars"] = len(text)

    def _case_script(self, stem: str, source: str = "sensor_01", save: str | None = None,
                     charts=()) -> tuple[str, str]:
        """A case's script and its code: it reads `source`, saves `save` and visualizes `charts`, (id, title)
        each with the table as its data, as the case's record says."""
        code = analysis_code(f"사례 {stem}", source, save=save,
                             charts=[(chart_id, title, True) for chart_id, title in charts])
        return f"scripts/{stem}.py", code

    def _small_table(self, salt: str) -> bytes:
        """A case's table: SMALL_TABLE_ROWS keys, one row each."""
        import polars as pl

        rng = random.Random(f"{self.seed}/{salt}")
        return parquet_bytes(pl.DataFrame({"key": [f"k{n}" for n in range(SMALL_TABLE_ROWS)],
                                           "value": [round(rng.uniform(0, 10), 3) for _ in range(SMALL_TABLE_ROWS)]}))

    def _chart(self, chart_id: str, title: str, code: str, table: bytes | None = None) -> dict:
        import polars as pl

        frame = pl.read_parquet(io.BytesIO(table)) if table is not None else None
        figure = {"data": [{"type": "bar", "x": frame["key"].to_list() if frame is not None else [],
                            "y": frame["value"].to_list() if frame is not None else []}],
                  "layout": {"title": {"text": title}}}
        return {"id": chart_id, "title": title, "line": _script_lines(code, f'"{chart_id}"'), "figure": figure,
                "data": frame}

    def trend_run(self, n: int) -> None:
        script_rel, code = self._case_script("case_trend", save="case_trend", charts=[("trend", "추세 비교")])
        table = self._small_table(f"trend/{n}")
        run_id, _ = self.w.written_run(
            script_rel=script_rel, code=code, title=f"추세 비교 {n}", started=self.when, duration=3.2,
            inputs=[self.w.input_entry(self.w.datasets["sensor_01"]["path"])],
            outputs=[("data/derived/case_trend.parquet", table)],
            charts=[self._chart("trend", "추세 비교", code, table)], stdout=SMALL_TABLE_STDOUT)
        self.case("same_chart_id")["records"][f"trend_run_{n}"] = run_id

    def region_total_run(self) -> None:
        script_rel, code = self._case_script("case_region_total", "sales_kr", save="case_region_total",
                                             charts=[("region-total", "지역 합계")])
        table = self._small_table("region-total")
        run_id, _ = self.w.written_run(
            script_rel=script_rel, code=code, title="지역 합계(다른 스크립트)", started=self.when, duration=2.1,
            inputs=[self.w.input_entry(self.w.datasets["sales_kr"]["path"])],
            outputs=[("data/derived/case_region_total.parquet", table)],
            charts=[self._chart("region-total", "지역 합계", code, table)], stdout=SMALL_TABLE_STDOUT)
        self.case("same_chart_id")["records"]["region_total_run"] = run_id

    def trend_note(self) -> None:
        key = "scripts/case_trend.py::trend"
        self.case("same_chart_id")["records"]["key_note"] = self.w.note(
            "finding", "추세 비교 차트에서 최근 구간의 기울기가 가장 크다.", refs=[key])

    def bare_name_note(self) -> None:
        records = self.case("same_chart_id")["records"]
        records["bare_note"] = self.w.note("note", "region-total 차트를 다음 보고서에 넣는다.", refs=["region-total"])
        records["bare_bound_to"] = "scripts/case_region_total.py::region-total"

    def unpreserved_run(self, kind: str, n: int) -> None:
        w = self.w
        records = self.case("unpreserved_data")["records"]
        stem = f"case_{kind}"
        chart_id, chart_title = kind.replace("_", "-"), f"{kind} 사례"
        script_rel, code = self._case_script(stem, save=stem, charts=[(chart_id, chart_title)])
        path = f"data/derived/{stem}.parquet"
        table = self._small_table(f"{kind}/{n}")
        inputs = [w.input_entry(w.datasets["sensor_01"]["path"])]
        charts = [self._chart(chart_id, chart_title, code, table)]
        if kind == "no_digest":
            # An input whose bytes could not be hashed when the run read it (the runner records sha256 null).
            inputs = [w.input_entry(w.datasets["sensor_01"]["path"], digest=False)]
        run_id, event_id = w.written_run(script_rel=script_rel, code=code, title=f"미보존 사례: {kind} {n}",
                                         started=self.when, duration=1.5, inputs=inputs, outputs=[(path, table)],
                                         charts=charts, stdout=SMALL_TABLE_STDOUT)
        records[f"{kind}_run_{n}"] = run_id
        records[f"{kind}_path"] = path
        if kind == "chart_gone":
            records["chart_gone_event"] = event_id
            records["chart_gone_key"] = f"{script_rel}::chart-gone"
        if kind == "code_changed":
            records["code_changed_script"] = script_rel

    def unpreserved_reader(self) -> None:
        w = self.w
        path = self.case("unpreserved_data")["records"]["deleted_path"]
        script_rel, code = self._case_script("case_deleted_reader", path)
        run_id, _ = w.written_run(script_rel=script_rel, code=code, title="지운 표를 읽은 run", started=self.when,
                                  duration=1.1, inputs=[w.input_entry(path)], stdout=SMALL_TABLE_STDOUT)
        self.case("unpreserved_data")["records"]["deleted_reader"] = run_id

    def cycle_runs(self) -> None:
        w = self.w
        x, y = "data/derived/case_cycle_x.parquet", "data/derived/case_cycle_y.parquet"
        bx, by = self._small_table("cycle/x"), self._small_table("cycle/y")
        for path, content in ((x, bx), (y, by)):
            (w.project.root / path).parent.mkdir(parents=True, exist_ok=True)
            (w.project.root / path).write_bytes(content)
        started = self.when
        ids = []
        for stem, read, write, content in (("case_cycle_a", y, x, bx), ("case_cycle_b", x, y, by)):
            script_rel, code = self._case_script(stem, read, save=Path(write).stem)
            run_id, _ = w.written_run(script_rel=script_rel, code=code, title=f"순환 사례 {stem[-1]}", started=started,
                                      duration=0.8, inputs=[w.input_entry(read)], outputs=[(write, content)],
                                      stdout=SMALL_TABLE_STDOUT)
            ids.append(run_id)
        self.case("reference_cycle")["records"].update(cycle_runs=ids)

    def cycle_notes(self) -> None:
        w = self.w
        records = self.case("reference_cycle")["records"]
        first_seq = w.last_seq() + 1
        later = f"e{first_seq + 1}"
        records["forward_note"] = w.note("note", f"다음 기록 {later}의 판단을 먼저 참조한다(순환 참조 사례).", refs=[later])
        records["back_note"] = w.note("note", f"앞 기록 {records['forward_note']}을 참조한다(순환 참조 사례).",
                                      refs=[records["forward_note"]])
        if records["back_note"] != later:
            raise FixtureError("the cycle's notes did not get the planned ids")

    def same_bytes_run(self, n: int) -> None:
        w = self.w
        path = "data/derived/case_same_bytes.parquet"
        script_rel, code = self._case_script(f"case_same_bytes_{n}", save=Path(path).stem)
        run_id, _ = w.written_run(script_rel=script_rel, code=code, title=f"같은 바이트 {n}", started=self.when,
                                  duration=0.9, inputs=[w.input_entry(w.datasets["sensor_01"]["path"])],
                                  outputs=[(path, self._small_table("same-bytes"))], stdout=SMALL_TABLE_STDOUT)
        self.case("ambiguous_producer")["records"][f"producer_{n}"] = run_id
        self.case("ambiguous_producer")["records"]["path"] = path

    def same_bytes_reader(self) -> None:
        w = self.w
        path = self.case("ambiguous_producer")["records"]["path"]
        script_rel, code = self._case_script("case_same_bytes_reader", path)
        run_id, _ = w.written_run(script_rel=script_rel, code=code, title="같은 바이트를 읽은 run", started=self.when,
                                  duration=0.7, inputs=[w.input_entry(path)], stdout=SMALL_TABLE_STDOUT)
        self.case("ambiguous_producer")["records"]["consumer"] = run_id

    def display_note(self) -> None:
        text = ('표시 경계 사례: <script>alert("fixture")</script> <img src=x onerror=alert(1)> &amp; &lt;b&gt; '
                '"><svg onload=alert(2)> ../../etc/passwd C:\\Windows\\System32\\drivers {{title}} '
                'javascript:alert(3) `rm -rf /` <!-- 주석 --> 줄\t탭')
        record = self.case("display_safety")["records"]
        record["note"] = self.w.note("note", text, actor="human")
        record["text"] = text

    def after_plan(self) -> None:
        """What happened to preserved files after they were recorded (unpreserved_data)."""
        w = self.w
        records = self.case("unpreserved_data")["records"]
        (w.project.root / records["deleted_path"]).unlink()
        gone = w.project.runs / records["chart_gone_run_1"] / "charts"
        for path in sorted(gone.iterdir()):
            path.unlink()
        script = w.project.root / records["code_changed_script"]
        script.write_bytes(script.read_bytes() + "# 실행한 뒤 고친 줄\n".encode())

    # the result
    def finish(self) -> dict:
        w = self.w
        root = w.project.root
        clear_derived(root)
        runs = sorted(d.name for d in (root / "runs").iterdir() if (d / "run.json").is_file())
        events = (root / "journal.jsonl").read_bytes().count(b"\n")
        raw = sorted((root / "data" / "raw").glob("*.parquet"))
        derived = sorted((root / "data" / "derived").glob("*.parquet"))
        counts = {"runs": len(runs), "runs_executed": len(w.kit_runs), "runs_written": len(runs) - len(w.kit_runs),
                  "events": events, "datasets": len(raw), "derived_tables": len(derived),
                  "charts": sum(len(r["charts"]) for r in w.runs),
                  "context_versions": len(list((root / ".lab" / "context").glob("*.md")))}
        expected = {"runs": self.spec.runs, "events": self.spec.events, "datasets": self.spec.datasets}
        for key, value in expected.items():
            if counts[key] != value:
                raise FixtureError(f"{key}: the fixture has {counts[key]}, the scale {self.scale_name} needs {value}")
        sizes = {"raw_parquet_bytes": sum(p.stat().st_size for p in raw),
                 "derived_parquet_bytes": sum(p.stat().st_size for p in derived),
                 "journal_bytes": (root / "journal.jsonl").stat().st_size}
        manifest = base_manifest("scale", self.scale_name, self.seed, self.start)
        manifest.update(counts=counts, sizes=sizes, datasets=[
            {k: d[k] for k in ("name", "path", "rows", "bytes", "source")} for d in sorted(w.datasets.values(),
                                                                                        key=lambda d: d["name"])],
            runs_executed=list(w.kit_runs), cases=scale_cases(self),
            inputs=sorted(path.relative_to(self.out).as_posix() for path in (self.out / "input").iterdir()))
        return manifest


def scale_cases(fixture: ScaleFixture) -> list[dict]:
    """The cases of a scale fixture with the judgments a reader gives them (design §5)."""
    c = {name: case["records"] for name, case in fixture.cases.items()}
    w = fixture.w
    k01, _, k03 = w.kit_runs
    up = c["unpreserved_data"]
    same = c["same_chart_id"]
    cycle = c["reference_cycle"]
    amb = c["ambiguous_producer"]
    busiest = max((s for s in fixture.scripts), key=lambda s: sum(1 for r in w.runs if r["script"] == s["path"]
                                                                  and r["status"] == "ok"))
    return [
        {"id": "correction", "required": True,
         "v1": "an earlier finding, and much later a finding whose refs.resolves and refs.events name it "
               "(text 정정: …); /1 has no supersedes",
         "records": c["correction"],
         "checks": [
             {"type": "reference", "from": c["correction"]["correction"], "field": "resolves",
              "value": c["correction"]["original"], "state": "resolved", "confirmed": True},
             {"type": "reference", "from": c["correction"]["correction"], "field": "events",
              "value": c["correction"]["original"], "state": "resolved", "confirmed": True},
             {"type": "record_state", "record": c["correction"]["original"], "state": "current"},
             {"type": "kit_resolved", "id": c["correction"]["original"], "by": c["correction"]["correction"]}]},
        {"id": "withdrawal", "required": True,
         "v1": "a decision, and later a kind=resolve record naming it in refs.resolves (text 철회: …); /1 has no "
               "record_update",
         "records": c["withdrawal"],
         "checks": [
             {"type": "reference", "from": c["withdrawal"]["withdrawal"], "field": "resolves",
              "value": c["withdrawal"]["decision"], "state": "resolved", "confirmed": True},
             {"type": "record_state", "record": c["withdrawal"]["decision"], "state": "current"},
             {"type": "kit_resolved", "id": c["withdrawal"]["decision"], "by": c["withdrawal"]["withdrawal"]}]},
        {"id": "unpreserved_data", "required": True,
         "v1": "a derived table another run overwrote with other bytes, a derived table deleted after it was "
               "recorded and the run that read it, a run whose chart files were deleted, an input recorded "
               "without a hash, a script edited after its run",
         "records": up,
         "checks": [
             {"type": "observation", "run": up["overwritten_run_1"], "role": "output", "path": up["overwritten_path"],
              "state": "content_changed"},
             {"type": "observation", "run": up["overwritten_run_2"], "role": "output", "path": up["overwritten_path"],
              "state": "resolved"},
             {"type": "observation", "run": up["deleted_run_1"], "role": "output", "path": up["deleted_path"],
              "state": "missing", "reason": "file_absent"},
             {"type": "observation", "run": up["deleted_reader"], "role": "input", "path": up["deleted_path"],
              "state": "missing", "reason": "file_absent"},
             {"type": "reference", "from": up["chart_gone_event"], "field": "charts", "value": up["chart_gone_key"],
              "state": "missing", "reason": "file_absent"},
             {"type": "observation", "run": up["no_digest_run_1"], "role": "input",
              "path": w.datasets["sensor_01"]["path"], "state": "unknown", "reason": "no_digest"},
             {"type": "code_state", "run": up["code_changed_run_1"], "state": "changed"}]},
        {"id": "same_chart_id", "required": True,
         "v1": "one script's chart id in several runs (versions); the id trend in two scripts; a record citing a "
               "key without a version; a record citing the bare name region-total, which two scripts have, that "
               "the /1 writer bound to the newest key",
         "records": same,
         "checks": [
             {"type": "visualization_versions", "key": f"{busiest['path']}::{busiest['chart']}", "min": 2},
             {"type": "visualization_versions", "key": "scripts/case_trend.py::trend", "min": 2},
             {"type": "visualization_versions", "key": "scripts/k02_sensor_profile.py::trend", "min": 1},
             {"type": "reference", "from": same["key_note"], "field": "charts", "value": "scripts/case_trend.py::trend",
              "state": "version_unbound", "min_candidates": 2},
             {"type": "event_refs", "id": same["bare_note"], "field": "charts", "values": [same["bare_bound_to"]]},
             {"type": "visualization_versions", "key": "scripts/k01_region_summary.py::region-total", "min": 1},
             {"type": "reference", "from": same["bare_note"], "field": "charts", "value": same["bare_bound_to"],
              "state": "version_unbound", "min_candidates": 1}]},
        {"id": "reference_cycle", "required": True,
         "v1": "two runs that started at the same instant and read each other's output; a record citing the "
               "record after it by id, which cites it back",
         "records": cycle,
         "checks": [
             {"type": "upstream_cycle", "run": cycle["cycle_runs"][0], "cycle": True},
             {"type": "upstream_cycle", "run": cycle["cycle_runs"][1], "cycle": True},
             {"type": "reference", "from": cycle["forward_note"], "field": "events", "value": cycle["back_note"],
              "state": "missing", "reason": "record_absent"},
             {"type": "reference", "from": cycle["back_note"], "field": "events", "value": cycle["forward_note"],
              "state": "resolved", "confirmed": True}]},
        {"id": "long_korean", "required": True,
         "v1": f"a Korean record of {c['long_korean']['original_chars']:,} characters (the /1 writer keeps "
               "4,000), a long Korean CONTEXT.md with more sections than the prompt budget, Korean run and "
               "chart titles and Korean column names",
         "records": c["long_korean"],
         "checks": [
             {"type": "event_text", "id": c["long_korean"]["note"], "chars": w.kit.core.MAX_EVENT_TEXT},
             {"type": "context_omitted", "min": 1},
             {"type": "run_title", "run": k01, "title": "지역별 매출 요약"}]},
        {"id": "display_safety", "required": False,
         "v1": "a record whose text looks like HTML, script, a path or a template (Q10's display boundary)",
         "records": {"note": c["display_safety"]["note"]},
         "checks": [{"type": "event_text", "id": c["display_safety"]["note"], "text": c["display_safety"]["text"]}]},
        {"id": "old_finding", "required": False,
         "v1": "an important finding near the start of the journal, far outside the recent list",
         "records": c["old_finding"],
         "checks": [{"type": "record_state", "record": c["old_finding"]["finding"], "state": "current"},
                    {"type": "not_in_status", "list": "recent", "id": c["old_finding"]["finding"]}]},
        {"id": "question_closed", "required": False,
         "v1": "a question, and a finding that closes it with refs.resolves",
         "records": c["question_closed"],
         "checks": [{"type": "record_state", "record": c["question_closed"]["question"], "state": "resolved"},
                    {"type": "reference", "from": c["question_closed"]["answer"], "field": "resolves",
                     "value": c["question_closed"]["question"], "state": "resolved", "confirmed": True}]},
        {"id": "failed_run", "required": False, "v1": "a run the kit executed that failed",
         "records": {"run": k03},
         "checks": [{"type": "run_status", "run": k03, "execution_status": "error", "recording_status": "complete"}]},
        {"id": "ambiguous_producer", "required": False,
         "v1": "two successful runs that wrote the same bytes to one path, and a run that read them",
         "records": amb,
         "checks": [{"type": "producer", "run": amb["consumer"], "path": amb["path"], "producer": "ambiguous",
                     "candidates": sorted([amb["producer_1"], amb["producer_2"]])}]},
    ]


def clear_derived(project_root: Path) -> None:
    """Empty the kit's derived directories (they stay): the hash cache and the Workspace."""
    for name in (".lab/cache", "report"):
        directory = project_root / name
        if directory.is_dir():
            for child in sorted(directory.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()


def base_manifest(kind: str, scale: str | None, seed: int, start: datetime) -> dict:
    kit = load_kit()
    return {"schema": MANIFEST_SCHEMA, "generator": {"path": GENERATOR, "version": GENERATOR_VERSION},
            "kind": kind, "scale": scale, "seed": seed, "format": "v1", "kit_version": kit.core.KIT_VERSION,
            "project": PROJECT_DIR, "clock": {"start": start.isoformat(), "timezone": "+09:00"},
            "volatile": VOLATILE, "not_digested": NOT_DIGESTED}


def write_outputs(out: Path, manifest: dict, kind: str, scale: str | None, seed: int, timings: dict,
                  started: float) -> dict:
    clear_derived(out / PROJECT_DIR)
    manifest["digest"] = tree_digest(out)
    phase = time.perf_counter()
    problems = check(out, manifest=manifest)  # leaves the project cold
    timings["self_check"] = time.perf_counter() - phase
    if problems:
        raise FixtureError("the fixture does not hold what its manifest says:\n- " + "\n- ".join(problems))
    write_lf(out / "manifest.json", json_text(manifest))
    timings["total"] = time.perf_counter() - started
    generation = {"kind": kind, "scale": scale, "seed": seed,
                  "seconds": {k: round(v, 3) for k, v in sorted(timings.items())}}
    write_lf(out / "environment.json", json_text(environment_record(out, cache_state="cold", generation=generation)))
    return manifest


def generate_scale(out, scale: str = "S", seed: int = 0, fmt: str = "v1") -> dict:
    """Write the scale fixture `scale` (S or M) under `out` (new or empty) and return its manifest. When it
    fails, nothing it wrote stays (output_directory)."""
    if scale not in SCALES:
        raise UsageError(f"unknown scale {scale!r}; known: {', '.join(SCALES)}")
    check_format(fmt)
    require_deterministic_process()
    with output_directory(out) as prepared:
        return _write_scale(prepared, scale, seed)


def _write_scale(out: Path, scale: str, seed: int) -> dict:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="eval-fixtures-") as scratch:
        fixture = ScaleFixture(scale, seed, out, Path(scratch))
        manifest = fixture.run()
    return write_outputs(out, manifest, "scale", scale, seed, fixture.timings, started)


# --- Q10: analysis checks -----------------------------------------------------------------------------

Q10_EXAMPLES = [
    # (id, check, verdict, script, title, why)
    ("aggregation_key.flawed", "aggregation_key", "fail", "aggregation_key_flawed.py", "집계 키: 결합 뒤 합계",
     "주문을 주문 품목과 결합한 뒤 주문 금액을 합해, 주문 한 건의 금액이 품목 수만큼 더해졌다. 집계 키(주문)와 결합 뒤의 행 단위(품목)가 다르다."),
    ("aggregation_key.sound", "aggregation_key", "pass", "aggregation_key_sound.py", "집계 키: 주문 단위 합계",
     "주문 단위로 금액을 합했고 품목 수는 따로 세어 붙였다. 결합이 금액 행을 늘리지 않는다."),
    ("unit.flawed", "unit", "fail", "unit_flawed.py", "단위: 원과 천원의 합",
     "온라인 주문 금액(원)과 매장 매출(천원, 열 이름 매출_천원)을 바꾸지 않고 더했다. 매장 매출이 1/1000로 들어갔다."),
    ("unit.sound", "unit", "pass", "unit_sound.py", "단위: 원으로 맞춘 합",
     "매장 매출을 1,000배 해 원으로 맞춘 뒤 더했다."),
    ("denominator.flawed", "denominator", "fail", "denominator_flawed.py", "분모: 고객 수로 나눈 전환율",
     "주장은 세션당 전환율인데 분모가 세션 수가 아니라 8월에 방문한 고객 수다. 고객이 여러 세션을 가져 전환율이 부풀었다."),
    ("denominator.sound", "denominator", "pass", "denominator_sound.py", "분모: 세션 수로 나눈 전환율",
     "주문으로 이어진 세션 수를 8월의 세션 수로 나눴다. 분자와 분모가 같은 단위(세션)다."),
    ("period.flawed", "period", "fail", "period_flawed.py", "기간: 부분 월과 전체 월의 비교",
     "9월 데이터는 20일까지만 있는데 9월 합계를 8월 한 달(31일) 합계와 비교해 감소라고 했다. 같은 일수를 비교하면 증가다."),
    ("period.sound", "period", "pass", "period_sound.py", "기간: 같은 일수의 비교",
     "9월 데이터가 있는 1–20일끼리 8월과 비교했다."),
]
Q10_LAST_DAY = date(2026, 9, 20)
# September's sessions per day against August's: enough that the same-days comparison shows an increase while
# the partial month (20 days against 31) shows a decrease; write_q10_sources makes sure of both.
Q10_SEPTEMBER_GROWTH = 1.25
Q10_ATTEMPTS = 50
Q10_TABLES = ("orders", "order_items", "sessions", "store_sales")


def _q10_rows(rng: random.Random) -> tuple[list, list, list]:
    customers = [f"U{n:04d}" for n in range(1, 1501)]
    sessions, orders, items = [], [], []
    day = date(2026, 8, 1)
    session_id = order_id = 0
    while day <= Q10_LAST_DAY:
        per_day = int(rng.uniform(175, 205) * (Q10_SEPTEMBER_GROWTH if day.month == 9 else 1.0))
        for _ in range(per_day):
            session_id += 1
            customer = rng.choice(customers)
            converted = rng.random() < 0.12
            sessions.append((f"S{session_id:06d}", customer, day.isoformat(), "true" if converted else "false"))
            if converted:
                order_id += 1
                orders.append((order_id, customer, day.isoformat(), rng.choice(REGIONS[:5]),
                               rng.randrange(12_000, 240_000, 100)))
                for line in range(rng.choice((1, 1, 2, 2, 3, 4))):
                    items.append((order_id, f"SKU-{rng.randrange(1, 400):04d}", rng.randint(1, 3) + line * 0))
        day += timedelta(days=1)
    return sessions, orders, items


def _q10_period_holds(orders: list) -> bool:
    """The partial-month comparison is a decrease and the same-days one an increase."""
    totals: dict[tuple[int, bool], int] = {}
    for _, _, day, _, amount in orders:
        month, first_days = int(day[5:7]), int(day[8:10]) <= 20
        totals[(month, first_days)] = totals.get((month, first_days), 0) + amount
    august = totals.get((8, True), 0) + totals.get((8, False), 0)
    september = totals.get((9, True), 0) + totals.get((9, False), 0)
    return september < august and totals.get((9, True), 0) > totals.get((8, True), 0)


def write_q10_sources(directory: Path, seed: int) -> dict[str, Path]:
    """Synthetic orders, items, sessions and store sales (2026-08-01 to 2026-09-20; September busier per
    day, so the partial-month comparison and the same-days comparison disagree). Data where they would not
    is drawn again from the same random stream, so the seed still decides it."""
    directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(f"eval-fixtures/q10/{seed}")
    for _ in range(Q10_ATTEMPTS):
        sessions, orders, items = _q10_rows(rng)
        if _q10_period_holds(orders):
            break
    else:
        raise FixtureError(f"seed {seed}: {Q10_ATTEMPTS} drawings of the Q10 data gave no partial-month decrease "
                           "with a same-days increase")
    store = []
    day = date(2026, 8, 1)
    while day <= Q10_LAST_DAY:
        for shop in ("강남점", "해운대점", "동성로점"):
            store.append((day.isoformat(), shop, rng.randrange(1_800, 6_500)))
        day += timedelta(days=1)
    tables = {
        "orders": (["order_id", "customer_id", "주문일", "지역", "금액_원"], orders),
        "order_items": (["order_id", "sku", "수량"], items),
        "sessions": (["session_id", "customer_id", "세션일", "주문여부"], sessions),
        "store_sales": (["일자", "매장", "매출_천원"], store),
    }
    paths = {}
    for name, (header, rows) in tables.items():
        path = directory / f"{name}.csv"
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)
        paths[name] = path
    return paths


def _quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def q10_project_tables(project_root: Path) -> dict[str, str]:
    """The imported Q10 tables as DuckDB reads them: name -> table expression."""
    return {name: f"read_parquet({_quoted(project_root / 'data' / 'raw' / f'{name}.parquet')})"
            for name in Q10_TABLES}


def q10_source_tables(directory: Path) -> dict[str, str]:
    """The Q10 source CSVs (input/) as DuckDB reads them: name -> table expression."""
    return {name: f"read_csv({_quoted(directory / f'{name}.csv')})" for name in Q10_TABLES}


def q10_numbers(tables: dict[str, str]) -> dict:
    """The numbers each Q10 example claims and the right ones, exact: integer sums and the ratios unrounded,
    which are what the scripts format (q10_claim_text). `tables`: q10_project_tables or q10_source_tables."""
    import duckdb

    connection = duckdb.connect()
    try:
        connection.execute("SET threads = 1")

        def one(sql: str):
            return connection.execute(sql).fetchone()[0]

        orders, items = tables["orders"], tables["order_items"]
        sessions, store = tables["sessions"], tables["store_sales"]
        seoul_joined = one(f"SELECT sum(o.금액_원) FROM {orders} o JOIN {items} i USING (order_id) WHERE o.지역 = '서울'")
        seoul = one(f"SELECT sum(금액_원) FROM {orders} WHERE 지역 = '서울'")
        aug_online = one(f"SELECT sum(금액_원) FROM {orders} WHERE month(주문일) = 8")
        aug_store = one(f"SELECT sum(매출_천원) FROM {store} WHERE month(일자) = 8")
        aug_sessions = one(f"SELECT count(*) FROM {sessions} WHERE month(세션일) = 8")
        aug_converted = one(f"SELECT count(*) FROM {sessions} WHERE month(세션일) = 8 AND 주문여부")
        aug_orders = one(f"SELECT count(*) FROM {orders} WHERE month(주문일) = 8")
        aug_customers = one(f"SELECT count(DISTINCT customer_id) FROM {sessions} WHERE month(세션일) = 8")
        aug_total = int(one(f"SELECT sum(금액_원) FROM {orders} WHERE month(주문일) = 8"))
        sep_total = int(one(f"SELECT sum(금액_원) FROM {orders} WHERE month(주문일) = 9"))
        aug_20 = int(one(f"SELECT sum(금액_원) FROM {orders} WHERE month(주문일) = 8 AND day(주문일) <= 20"))
        sep_20 = int(one(f"SELECT sum(금액_원) FROM {orders} WHERE month(주문일) = 9 AND day(주문일) <= 20"))
        last_day = one(f"SELECT max(주문일) FROM {orders}")
        return {
            "aggregation_key": {"orders_rows": one(f"SELECT count(*) FROM {orders}"),
                                "joined_rows": one(f"SELECT count(*) FROM {orders} JOIN {items} USING (order_id)"),
                                "region": "서울", "flawed_value": int(seoul_joined), "sound_value": int(seoul)},
            "unit": {"online_won": int(aug_online), "store_thousand_won": int(aug_store),
                     "flawed_value": int(aug_online + aug_store), "sound_value": int(aug_online + aug_store * 1000)},
            "denominator": {"sessions": aug_sessions, "converted_sessions": aug_converted, "orders": aug_orders,
                            "customers": aug_customers, "flawed_value": aug_orders / aug_customers,
                            "sound_value": aug_converted / aug_sessions},
            "period": {"last_day": last_day.isoformat(), "august_total": aug_total, "september_total": sep_total,
                       "august_1_20": aug_20, "september_1_20": sep_20, "flawed_value": sep_total / aug_total - 1,
                       "sound_value": sep_20 / aug_20 - 1},
        }
    finally:
        connection.close()


def q10_evidence(numbers: dict) -> dict:
    """q10_numbers as expected.json keeps them: the ratios rounded to 6 digits. The claims' text is made from
    the exact ratios, not from these."""
    return {check_name: {key: round(value, 6) if isinstance(value, float) else value for key, value in values.items()}
            for check_name, values in numbers.items()}


def q10_claim_text(check_name: str, verdict: str, value) -> str:
    """The text the Q10 script of `check_name` and `verdict` records for the exact `value` (the scripts'
    format strings)."""
    if check_name == "aggregation_key":
        return f"서울 지역의 주문 금액 합계(8월 1일–9월 20일)는 {value:,}원이다."
    if check_name == "unit":
        return f"8월 전체 매출(온라인+매장)은 {value:,}원이다."
    if check_name == "denominator":
        return f"8월 세션당 전환율은 {value:.1%}다."
    if check_name == "period":
        span = "9월 주문 금액은 8월보다" if verdict == "fail" else "9월 1–20일 주문 금액은 8월 1–20일보다"
        return f"{span} {abs(value):.1%} {'감소' if value < 0 else '증가'}했다."
    raise ValueError(check_name)


def q10_claims(numbers: dict) -> dict[str, str]:
    """Each Q10 example's claim (example id -> text), as its script formats the exact numbers (q10_numbers):
    a ratio rounded to 6 digits first can format otherwise (0.05049965 is 5.0%, 0.0505 is 5.1%)."""
    return {example_id: q10_claim_text(check_name, verdict,
                                       numbers[check_name]["flawed_value" if verdict == "fail" else "sound_value"])
            for example_id, check_name, verdict, *_ in Q10_EXAMPLES}


def generate_q10(out, seed: int = 0, fmt: str = "v1") -> dict:
    """Write the Q10 examples under `out` (new or empty) and return the manifest. When it fails, nothing it
    wrote stays (output_directory)."""
    check_format(fmt)
    require_deterministic_process()
    with output_directory(out) as prepared:
        return _write_q10(prepared, seed)


def _write_q10(out: Path, seed: int) -> dict:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    start = datetime(2026, 9, 21, 10, 0, tzinfo=KST)
    sources = write_q10_sources(out / "input", seed)
    with tempfile.TemporaryDirectory(prefix="eval-fixtures-") as scratch, \
            kit_session(Path(scratch), start) as (kit, clock):
        w = V1Writer(kit, clock, out / PROJECT_DIR)
        when = start
        w.init("Q10 분석 검사 예제", "집계 키·단위·분모·기간을 잘못 다룬 분석과 바르게 다룬 분석을 구별한다")
        descriptions = {"orders": "주문(2026-08-01–09-20, 합성)", "order_items": "주문 품목(합성)",
                        "sessions": "방문 세션과 주문 여부(합성)", "store_sales": "매장 일 매출, 천원 단위(합성)"}
        for name, path in sources.items():
            when += timedelta(minutes=3)
            clock.at(when)
            w.import_dataset(path, name, descriptions[name], when)
        phase = time.perf_counter()
        examples = []
        for example_id, check_name, verdict, script, title, why in Q10_EXAMPLES:
            when += timedelta(minutes=7)
            clock.at(when)
            before = w.last_seq()
            record = w.execute(SCRIPTS / "q10" / script, f"scripts/{script}", title)
            if record["status"] != "ok":
                raise FixtureError(f"the Q10 script {script} failed: {record.get('error')}")
            journal = kit.relations.read_journal(w.project.root)
            claims = [e for e in journal.events if (e.seq or 0) > before and e.kind == "finding"]
            if len(claims) != 1:
                raise FixtureError(f"{script} recorded {len(claims)} findings, not one")
            examples.append({"id": example_id, "check": check_name, "verdict": verdict, "script": f"scripts/{script}",
                             "run": record["id"], "claim_event": claims[0].id, "claim": claims[0].value["text"],
                             "why": why})
        timings["kit_runs"] = time.perf_counter() - phase
        root = w.project.root
        numbers = q10_numbers(q10_project_tables(root))
        evidence, claims = q10_evidence(numbers), q10_claims(numbers)
        for example in examples:
            example["evidence"] = evidence[example["check"]]
            text = claims[example["id"]]
            if example["claim"] != text:
                raise FixtureError(f"{example['id']}: the script claimed {example['claim']!r}, the data gives {text!r}")
        if numbers["period"]["flawed_value"] >= 0 or numbers["period"]["sound_value"] <= 0:
            raise FixtureError("the Q10 period example does not show a partial-month decrease and a same-days increase")
        expected = {"schema": EXPECTED_SCHEMA, "fixture": "q10", "gate": "Q10", "project": PROJECT_DIR,
                    "verdicts": {"fail": "the claim does not follow from the computation for the reason given",
                                 "pass": "the computation supports the claim"},
                    "examples": examples}
        write_lf(out / "expected.json", json_text(expected))
        manifest = base_manifest("q10", None, seed, start)
        manifest.update(counts=project_counts(root), runs_executed=list(w.kit_runs), cases=[],
                        inputs=sorted(f"input/{p.name}" for p in sources.values()))
        clear_derived(root)
    return write_outputs(out, manifest, "q10", None, seed, timings, started)


def project_counts(root: Path) -> dict:
    runs = [d for d in (root / "runs").iterdir() if (d / "run.json").is_file()]
    return {"runs": len(runs), "runs_executed": len(runs), "runs_written": 0,
            "events": (root / "journal.jsonl").read_bytes().count(b"\n"),
            "datasets": len(list((root / "data" / "raw").glob("*.parquet"))),
            "derived_tables": len(list((root / "data" / "derived").glob("*.parquet"))),
            "context_versions": len(list((root / ".lab" / "context").glob("*.md")))}


# --- Q13: the reference session, reproduced -----------------------------------------------------------

Q13_SENSORS = {"pressure": (12.0, 1.0), "temperature": (65.0, 1.0), "rf_power": (300.0, 1.0), "flow": (40.0, 1.0)}
Q13_CONTEXTS = [f"C{n:03d}" for n in range(1, 81)]
Q13_STEPS, Q13_POINTS = 4, 15
Q13_DISPLAY = "C017"
Q13_MAX_Z = 4.3613
Q13_TRACE_MEAN_Z = 4.0


def _q13_base(sensor: str, step: int, rel: int) -> float:
    level, _ = Q13_SENSORS[sensor]
    shapes = {1: 0.0, 2: rel / (Q13_POINTS - 1) * 6.0, 3: 6.0 + math.sin(rel / 3.0), 4: 6.0 - rel * 0.4}
    return level + shapes[step] * (1.0 + 0.1 * list(Q13_SENSORS).index(sensor))


def _z_needed(others: list[float], target: float) -> float:
    """The value that, added to `others`, has z = `target` against the mean and the sample standard
    deviation of all of them (closed form, design §7)."""
    n = len(others) + 1
    mean = sum(others) / len(others)
    ss = sum((x - mean) ** 2 for x in others)
    u = math.sqrt(target ** 2 * ss / ((n - 1) ** 3 / n ** 2 - target ** 2 * (n - 1) / n))
    return mean + u


def q13_values(rng: random.Random) -> tuple[dict, dict]:
    """The trace values and what was put in on purpose (design §7): uniform noise demeaned per trace (so
    nothing natural is beyond 3σ), one trace-mean outlier, one out-of-band sample on every sensor of the
    displayed trace, the largest |z| at Q13_MAX_Z, and more single exceedances at distinct time points."""
    values: dict[tuple, float] = {}
    for context in Q13_CONTEXTS:
        for sensor in Q13_SENSORS:
            offset = rng.uniform(-0.17, 0.17)
            noise = [rng.uniform(-1.0, 1.0) for _ in range(Q13_STEPS * Q13_POINTS)]
            middle = sum(noise) / len(noise)
            for index, value in enumerate(noise):
                step, rel = index // Q13_POINTS + 1, index % Q13_POINTS
                values[(context, sensor, step, rel)] = _q13_base(sensor, step, rel) + offset + value - middle
    shifted = rng.choice([c for c in Q13_CONTEXTS if c != Q13_DISPLAY])

    def trace_mean(context: str) -> float:
        return sum(values[(context, "pressure", s, r)] for s in range(1, Q13_STEPS + 1)
                   for r in range(Q13_POINTS)) / (Q13_STEPS * Q13_POINTS)

    shift = _z_needed([trace_mean(c) for c in Q13_CONTEXTS if c != shifted], Q13_TRACE_MEAN_Z) - trace_mean(shifted)
    for step in range(1, Q13_STEPS + 1):
        for rel in range(Q13_POINTS):
            values[(shifted, "pressure", step, rel)] += shift
    points = [(s, step, rel) for s in Q13_SENSORS for step in range(1, Q13_STEPS + 1) for rel in range(Q13_POINTS)]
    rng.shuffle(points)
    planned: list[tuple] = []
    used: set[tuple] = set()

    def take(context: str, sensor: str | None, target: float) -> None:
        point = next(p for p in points if p not in used and (sensor is None or p[0] == sensor)
                     and not (context == shifted and p[0] == "pressure"))
        used.add(point)
        planned.append((context, *point, target))

    for sensor, target in zip(Q13_SENSORS, (3.42, 3.21, 3.68, 3.35), strict=True):
        take(Q13_DISPLAY, sensor, target)
    take(rng.choice([c for c in Q13_CONTEXTS if c not in (Q13_DISPLAY, shifted)]), "rf_power", Q13_MAX_Z)
    for _ in range(18):
        take(rng.choice([c for c in Q13_CONTEXTS if c != Q13_DISPLAY]), None, round(rng.uniform(3.1, 4.0), 2))
    for context, sensor, step, rel, target in planned:
        others = [values[(c, sensor, step, rel)] for c in Q13_CONTEXTS if c != context]
        values[(context, sensor, step, rel)] = _z_needed(others, target)
    rounded = {key: round(value, 4) for key, value in values.items()}
    return rounded, {"shifted_context": shifted, "injected": planned}


def write_q13_source(path: Path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["context_id", "sensor", "step", "rel_time", "t", "value"])
        for (context, sensor, step, rel), value in sorted(values.items()):
            writer.writerow([context, sensor, step, rel, (step - 1) * Q13_POINTS + rel, f"{value:.4f}"])


def q13_facts(project_root: Path) -> dict:
    """The facts of the reproduction from its preserved tables (DuckDB over the project's Parquet)."""
    import duckdb

    def table(rel: str) -> str:
        return "read_parquet('" + str(project_root / rel).replace("'", "''") + "')"

    traces = table("data/raw/traces.parquet")
    connection = duckdb.connect()
    try:
        connection.execute("SET threads = 1")

        def rows(sql: str) -> list[tuple]:
            return connection.execute(sql).fetchall()

        z = f"""WITH p AS (SELECT sensor, step, rel_time, avg(value) AS m, stddev_samp(value) AS s
                          FROM {traces} GROUP BY ALL)
                SELECT t.context_id, t.sensor, t.t, (t.value - p.m) / p.s AS z
                FROM {traces} t JOIN p USING (sensor, step, rel_time)"""
        per = f"""SELECT context_id, sensor, max(abs(z)) AS max_abs_z, count(*) FILTER (WHERE abs(z) > 3) AS exceed
                  FROM ({z}) GROUP BY ALL"""
        [(table_rows, exceed_total, exceed_mean, with_exceed, max_z)] = rows(
            f"SELECT count(*), sum(exceed), avg(exceed), count(*) FILTER (WHERE exceed > 0), max(max_abs_z) FROM ({per})")
        [(max_context, max_sensor)] = rows(f"SELECT context_id, sensor FROM ({per}) ORDER BY max_abs_z DESC LIMIT 1")
        displayed = dict(rows(f"""SELECT sensor, count(*) FILTER (WHERE abs(z) > 3) FROM ({z})
                                  WHERE context_id = '{Q13_DISPLAY}' GROUP BY sensor ORDER BY sensor"""))
        outliers = rows(f"""WITH m AS (SELECT sensor, context_id, avg(value) AS tm FROM {traces} GROUP BY ALL),
                                s AS (SELECT sensor, avg(tm) AS mu, stddev_samp(tm) AS sd FROM m GROUP BY sensor)
                            SELECT sensor, context_id FROM m JOIN s USING (sensor)
                            WHERE abs(tm - mu) / sd > 3 ORDER BY sensor, context_id""")
        return {"score_table_rows": table_rows, "exceed_total": int(exceed_total),
                "exceed_mean": round(float(exceed_mean), 6), "contexts_sensors_with_exceed": with_exceed,
                "max_abs_z": round(float(max_z), 4), "max_abs_z_at": {"context_id": max_context, "sensor": max_sensor},
                "displayed_context": Q13_DISPLAY,
                "displayed_out_of_band": {sensor: int(n) for sensor, n in sorted(displayed.items())},
                "trace_mean_outliers": [{"sensor": s, "context_id": c} for s, c in outliers]}
    finally:
        connection.close()


Q13_METRICS = [
    {"id": "trace_mean_3sigma", "name": "트레이스 평균의 3σ 이탈", "script": "scripts/01_eda_training_traces.py",
     "computation": "센서별로 각 트레이스의 평균을 모아 그 평균들의 평균·표준편차로 표준화한 값의 |z| > 3",
     "aggregation_unit": "트레이스(context_id) × 센서", "reference_population": "같은 센서의 트레이스 평균 80개",
     "applied_to": "같은 학습 트레이스"},
    {"id": "context_step_score", "name": "컨텍스트×스텝 점수", "script": "scripts/02_step_profiles.py",
     "computation": "(트레이스의 스텝 평균 − sensor×step 전체 샘플의 평균) ÷ sensor×step 전체 샘플의 표준편차",
     "aggregation_unit": "평가 값은 컨텍스트×센서×스텝의 평균, 분모 표준편차는 sensor×step의 개별 샘플",
     "reference_population": "같은 sensor×step의 모든 샘플(80 트레이스 × 15 시점)", "applied_to": "같은 학습 트레이스"},
    {"id": "timepoint_sample_z", "name": "시점별 샘플 점수", "script": "scripts/03_sample_zscores.py",
     "computation": "개별 샘플을 같은 sensor×step×rel_time 프로파일의 평균·표준편차로 표준화",
     "aggregation_unit": "샘플(컨텍스트×센서×시점)", "reference_population": "같은 시점의 80 트레이스(timepoint_profile)",
     "applied_to": "기준을 추정한 같은 학습 트레이스"},
    {"id": "score_table_summary", "name": "최댓값·초과 횟수·비율", "script": "scripts/03_sample_zscores.py",
     "computation": "시점별 |z|를 컨텍스트×센서로 요약: 최대 |z|, |z| > 3 횟수, 그 비율",
     "aggregation_unit": "컨텍스트×센서(60 시점)", "reference_population": "시점별 샘플 점수와 같음",
     "applied_to": "같은 학습 트레이스"},
]

# What the CONTEXT's definitions add to a metric's computation (Q13_METRICS): its name and computation are
# written there as they are, so the CONTEXT and expected.json state one formula.
Q13_CONTEXT_NOTES = {"context_step_score": "평가 값(스텝 평균)과 분모(개별 샘플의 표준편차)의 집계 단위가 다르다.",
                     "timepoint_sample_z": "주요 점수."}


def q13_definition_lines() -> list[str]:
    """The definitions section's lines of the second CONTEXT version, one per metric."""
    return [" ".join(filter(None, (f"- {m['name']}: {m['computation']}.", Q13_CONTEXT_NOTES.get(m["id"]))))
            for m in Q13_METRICS]


Q13_CONTEXT_2 = """# 장비 트레이스 이상 탐지

## 목표 <!-- pin -->
- 학습 트레이스에서 장비 이상 후보를 찾고, 후보를 판단할 지표를 정한다.

## 핵심 질문 <!-- pin -->
- 센서별 분포에서 벗어나는 트레이스가 있는가?
- 기준 프로파일을 같은 학습 데이터로 추정한 판정이 별도 검증 트레이스에서도 유지되는가?

## 다음 할 일 <!-- pin -->
- 모든 센서에 대해 시점별 밴드 그림을 그린다.

## 정의
{definitions}

## 데이터
- traces: 80 컨텍스트 × 4 센서 × 4 스텝 × 15 시점(합성). 센서 단위는 추정이다.
""".replace("{definitions}", "\n".join(q13_definition_lines()))

Q13_SESSION = [
    {"step": 1, "request": "장비 트레이스 데이터(input/traces.csv)를 가져와서 EDA를 해 줘. 센서별 분포와 이상해 보이는 후보가 궁금해.",
     "reproduced_by": ["scripts/01_eda_training_traces.py"],
     "expect": ["데이터를 가져와 구조(컨텍스트·센서·스텝·시점)를 확인한다",
                "센서별 분포와 트레이스 단위의 이탈 후보를 근거(run·표·차트)와 함께 남긴다",
                "기록 API·라이브러리 오류가 나면 고쳐서 이어 간다"]},
    {"step": 2, "request": "스텝별 특성을 보고 싶어. 스텝 프로파일을 만들고, 시점마다 얼마나 벗어나는지 점수로 계산해 줘.",
     "reproduced_by": ["scripts/02_step_profiles.py", "scripts/03_sample_zscores.py"],
     "expect": ["스텝·시점 등 서로 다른 기준의 점수를 구별해 정의(집계 단위·기준 모집단)를 남긴다",
                "실패한 run과 부분 산출물을 숨기지 않고 고쳐서 같은 경로에 다시 쓴다",
                "기준을 같은 학습 데이터로 추정했다는 한계를 판단에 적는다"]},
    {"step": 3, "request": "pressure 하나만 보지 말고 모든 센서에 대해 같은 밴드 그림을 그려 줘.",
     "reproduced_by": ["scripts/04_all_sensor_bands.py"],
     "expect": ["사전 등록·닫힌 chart spec 없이 다중 패널로 확장한다",
                "그림의 인상과 저장된 수치가 다르면 수치를 따른다(밴드 밖 샘플·최대 |z|)",
                "첨부 표가 그림의 일부만 담는다는 범위를 밝힌다"]},
]


def generate_q13(out, seed: int = 0, fmt: str = "v1") -> dict:
    """Write the reproduction of the reference session under `out` (new or empty) and return the manifest.
    When it fails, nothing it wrote stays (output_directory)."""
    check_format(fmt)
    require_deterministic_process()
    with output_directory(out) as prepared:
        return _write_q13(prepared, seed)


def _write_q13(out: Path, seed: int) -> dict:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    rng = random.Random(f"eval-fixtures/q13/{seed}")
    start = datetime(2026, 9, 16, 21, 19, tzinfo=KST)
    values, planted = q13_values(rng)
    source = out / "input" / "traces.csv"
    write_q13_source(source, values)
    scripts = SCRIPTS / "q13"
    with tempfile.TemporaryDirectory(prefix="eval-fixtures-") as scratch, \
            kit_session(Path(scratch), start) as (kit, clock):
        w = V1Writer(kit, clock, out / PROJECT_DIR)
        when = start

        def later(minutes: int) -> None:
            nonlocal when
            when += timedelta(minutes=minutes)
            clock.at(when)

        w.init("장비 트레이스 이상 탐지", "학습 트레이스에서 장비 이상 후보를 찾는다")
        later(2)
        w.write_context(Q13_CONTEXT_1, actor="agent")
        later(1)
        w.import_dataset(source, "traces", "장비 트레이스(합성: 80 컨텍스트 × 4 센서 × 4 스텝 × 15 시점)", when)
        phase = time.perf_counter()
        runs = {}
        for key, script, title, minutes in (
                ("eda_failed", "01_eda_training_traces.broken.py", "학습 트레이스 EDA", 4),
                ("eda", "01_eda_training_traces.py", "학습 트레이스 EDA", 3),
                ("step_profiles", "02_step_profiles.py", "스텝 프로파일과 점수", 6)):
            later(minutes)
            runs[key] = w.execute(scripts / script, "scripts/" + script.replace(".broken", ""), title)
        later(2)
        decision = w.note("decision", "주요 이상 점수는 시점별 샘플 z로, 보조 점수는 컨텍스트×스텝 점수로 둔다. 두 점수는 "
                                      "평가 값과 분모의 집계 단위가 다르다.", refs=[runs["step_profiles"]["id"]])
        for key, script, title, minutes in (
                ("scores_failed", "03_sample_zscores.broken.py", "시점별 샘플 점수", 5),
                ("scores", "03_sample_zscores.py", "시점별 샘플 점수", 4)):
            later(minutes)
            runs[key] = w.execute(scripts / script, "scripts/" + script.replace(".broken", ""), title)
        later(2)
        claim_max = w.note("finding", "시점별 |z|는 모두 4.3 이하다. 극단적인 샘플은 없다.", refs=[runs["scores"]["id"]])
        later(1)
        claim_normal = w.note("finding", "학습 데이터의 장비에는 이상이 없다(정상).", refs=[runs["scores"]["id"]])
        later(1)
        question = w.note("question", "기준 프로파일을 같은 학습 데이터로 추정했는데, 별도 검증 트레이스에서도 같은 판정이 나오는가?")
        later(3)
        w.write_context(Q13_CONTEXT_2, actor="agent")
        later(9)
        runs["bands"] = w.execute(scripts / "04_all_sensor_bands.py", "scripts/04_all_sensor_bands.py", "전체 센서 밴드")
        later(2)
        band_text = f"전체 센서 그림에서 표시한 트레이스({Q13_DISPLAY})는 모든 센서에서 전 구간이 밴드(평균±3σ) 안에 있다."
        claim_band = w.note("finding", band_text, refs=[runs["bands"]["id"]])
        timings["kit_runs"] = time.perf_counter() - phase
        root = w.project.root
        facts = q13_facts(root)
        problems = []
        if facts["displayed_out_of_band"] != dict.fromkeys(sorted(Q13_SENSORS), 1):
            problems.append(f"displayed trace out of band: {facts['displayed_out_of_band']}")
        if not 4.3 < facts["max_abs_z"] < 4.4:
            problems.append(f"max |z| {facts['max_abs_z']}")
        if facts["trace_mean_outliers"] != [{"sensor": "pressure", "context_id": planted["shifted_context"]}]:
            problems.append(f"trace-mean outliers {facts['trace_mean_outliers']}")
        if facts["exceed_total"] != len(planted["injected"]):
            problems.append(f"exceedances {facts['exceed_total']}, planted {len(planted['injected'])}")
        if problems:
            raise FixtureError("the Q13 data does not hold its design: " + "; ".join(problems))
        journal = kit.relations.read_journal(root)
        eda_claim = next(e for e in journal.events if e.kind == "finding" and "3σ 밖인 경우" in e.value["text"])
        if planted["shifted_context"] not in eda_claim.value["text"] or "1건" not in eda_claim.value["text"]:
            raise FixtureError(f"the EDA finding does not name the one trace-mean outlier: {eda_claim.value['text']}")
        expected = {
            "schema": EXPECTED_SCHEMA, "fixture": "q13", "gate": "Q13", "project": PROJECT_DIR,
            "source": "input/traces.csv", "reference": "docs/REVIEW-REFERENCE-SESSION-2026-09-18.md",
            "facts": facts,
            "claims": [
                {"event": eda_claim.id, "text": eda_claim.value["text"], "verdict": "supported",
                 "why": "트레이스 평균이 3σ 밖인 것은 pressure의 한 컨텍스트뿐이다(facts.trace_mean_outliers)."},
                {"event": claim_max, "text": "시점별 |z|는 모두 4.3 이하다. 극단적인 샘플은 없다.", "verdict": "false",
                 "why": f"저장된 점수의 최대 |z|는 {facts['max_abs_z']}다(facts.max_abs_z)."},
                {"event": claim_normal, "text": "학습 데이터의 장비에는 이상이 없다(정상).", "verdict": "unverified",
                 "why": "기준 프로파일을 점수를 매긴 같은 학습 데이터로 추정했고 라벨·별도 검증 세트가 없다. '학습 데이터에서 관측한 "
                        "이탈 패턴'과 '이상 없음'·실제 오탐률을 구별해야 한다."},
                {"event": claim_band, "text": band_text, "verdict": "false",
                 "why": f"표시한 트레이스({Q13_DISPLAY})의 센서마다 밴드 밖 샘플이 1개씩 있다(facts.displayed_out_of_band). "
                        "그림의 인상과 저장된 수치가 다르다."},
            ],
            "records": q13_records(kit, root, runs, decision, question),
            "metrics": Q13_METRICS,
            "session": Q13_SESSION,
        }
        write_lf(out / "expected.json", json_text(expected))
        manifest = base_manifest("q13", None, seed, start)
        manifest.update(counts=project_counts(root), runs_executed=list(w.kit_runs), cases=[],
                        inputs=["input/traces.csv"])
        clear_derived(root)
    return write_outputs(out, manifest, "q13", None, seed, timings, started)


Q13_CONTEXT_1 = """# 장비 트레이스 이상 탐지

## 목표 <!-- pin -->
- 학습 트레이스에서 장비 이상 후보를 찾고, 후보를 판단할 지표를 정한다.

## 핵심 질문 <!-- pin -->
- 센서별 분포에서 벗어나는 트레이스가 있는가?

## 다음 할 일 <!-- pin -->
- 데이터 구조를 확인하고 EDA를 한다.

## 데이터
- traces: 80 컨텍스트 × 4 센서 × 4 스텝 × 15 시점(합성). 센서 단위는 추정이다.
"""



def q13_records(kit: SimpleNamespace, root: Path, runs: dict, decision: str, question: str) -> dict:
    model = kit.relations.read(root, alive=lambda pid: False, sha=lambda p: sha256_file(p) if p.is_file() else None)
    failed, fixed = runs["scores_failed"], runs["scores"]
    partial = sorted(o["path"] for o in failed["outputs"] if o.get("change") != "deleted")
    producers = {}
    for path in partial:
        current = sha256_file(root / path)
        producers[path] = model.producers(path, current)
    eda_chart = next(c for c in runs["eda"]["charts"] if c["id"] == "sensor-histograms")
    band_chart = next(c for c in runs["bands"]["charts"] if c["id"] == "all-sensor-bands")
    return {
        "runs": {key: {"run": record["id"], "status": record["status"]} for key, record in runs.items()},
        "run_counts": {"ok": sum(r["status"] == "ok" for r in runs.values()),
                       "error": sum(r["status"] == "error" for r in runs.values())},
        "failed_run_partial_outputs": {"run": failed["id"], "outputs": partial,
                                       "charts": [c["id"] for c in failed["charts"]],
                                       "rewritten_by": fixed["id"], "producers_of_current_bytes": producers},
        "attached_data": [
            {"chart": f"scripts/01_eda_training_traces.py::{eda_chart['id']}", "run": runs["eda"]["id"],
             "data_rows": eda_chart["data_rows"], "data_columns": eda_chart["data_columns"], "scope": "summary",
             "why": "센서별 요약(n·평균·표준편차·최솟값·최댓값) 4행이다. 히스토그램의 bin 경계·개수가 아니다."},
            {"chart": f"scripts/04_all_sensor_bands.py::{band_chart['id']}", "run": runs["bands"]["id"],
             "data_rows": band_chart["data_rows"], "data_columns": band_chart["data_columns"], "scope": "partial",
             "why": "관측값과 프로파일 평균만 있다. 밴드 상·하한은 입력 data/derived/timepoint_profile.parquet의 mean·std에 있다."},
        ],
        "context_versions": len(list((root / ".lab" / "context").glob("*.md"))),
        "decision": decision, "open_question": question,
    }


# --- check --------------------------------------------------------------------------------------------

def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def _validate(value, schema_name: str, label: str) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return [f"{label}: jsonschema is not installed, the schema was not checked"]
    validator = jsonschema.Draft202012Validator(_schema(schema_name),
                                                format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)
    return [f"{label}: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))]


def evaluate_case_check(kit, project, model, status, fresh, item: dict) -> str | None:
    """None when the judgment `item` names holds, else what differs."""
    kind = item["type"]
    if kind == "reference":
        found = [j for j in model.references(item["from"])
                 if j["ref"].get("field") == item["field"] and j["ref"].get("value") == item["value"]]
        if len(found) != 1:
            return f"{len(found)} references {item['field']}={item['value']} from {item['from']}"
        judged = found[0]
        for key in ("state", "confirmed", "reason"):
            if key in item and judged.get(key) != item[key]:
                return f"{key} {judged.get(key)!r}, expected {item[key]!r}"
        if "min_candidates" in item and len(judged.get("candidates") or []) < item["min_candidates"]:
            return f"{len(judged.get('candidates') or [])} candidates, expected at least {item['min_candidates']}"
        return None
    if kind == "record_state":
        state = (model.record_states().get(item["record"]) or {}).get("state")
        return None if state == item["state"] else f"state {state!r}, expected {item['state']!r}"
    if kind == "not_in_status":
        ids = [entry["id"] for entry in status[item["list"]]]
        return None if item["id"] not in ids else f"{item['id']} is in status {item['list']}"
    if kind == "kit_resolved":
        # The kit's /1 reading (core.resolved_targets): status, the context block and the Workspace leave
        # a record some later record resolves out of the current key information.
        resolver = kit.core.resolved_targets(kit.core.read_events(project, model.journal)).get(item["id"])
        got = (resolver or {}).get("id")
        return None if got == item["by"] else f"resolved by {got!r}, expected {item['by']!r}"
    if kind == "upstream_cycle":
        cycle = model.upstream(item["run"])["cycle"]
        return None if cycle == item["cycle"] else f"cycle {cycle}, expected {item['cycle']}"
    if kind == "producer":
        judged = model.producer(item["run"], item["path"]) or {}
        if judged.get("producer") != item["producer"]:
            return f"producer {judged.get('producer')!r}, expected {item['producer']!r}"
        if "candidates" in item and judged.get("candidates") != item["candidates"]:
            return f"candidates {judged.get('candidates')}, expected {item['candidates']}"
        return None
    if kind == "observation":
        judged = model.observation(item["run"], item["role"], item["path"])
        for key in ("state", "reason"):
            if key in item and judged.get(key) != item[key]:
                return f"{key} {judged.get(key)!r}, expected {item[key]!r}"
        return None
    if kind == "code_state":
        state = fresh.code_state(model.run(item["run"]) or {})
        return None if state == item["state"] else f"code {state!r}, expected {item['state']!r}"
    if kind == "visualization_versions":
        count = len(model.visualization_versions(item["key"]))
        return None if count >= item["min"] else f"{count} versions of {item['key']}, expected at least {item['min']}"
    if kind in ("event_text", "event_refs"):
        lines = model.journal.lines_of.get(item["id"], [])
        events = [e for e in model.journal.events if e.line in lines]
        if len(events) != 1:
            return f"{len(events)} records with id {item['id']}"
        value = events[0].value
        if kind == "event_refs":
            got = (value.get("refs") or {}).get(item["field"])
            return None if got == item["values"] else f"refs.{item['field']} {got}, expected {item['values']}"
        text = value.get("text") or ""
        if "chars" in item and len(text) != item["chars"]:
            return f"{len(text)} characters, expected {item['chars']}"
        if "text" in item and text != item["text"]:
            return "the text is not the one written"
        return None
    if kind == "context_omitted":
        omitted = status["project"]["context_omitted"]
        return None if len(omitted) >= item["min"] else f"{len(omitted)} sections left out of the prompt"
    if kind == "run_status":
        row = next((r for r in model.runs() if r["run_id"] == item["run"]), None)
        if row is None:
            return f"no run {item['run']}"
        got = (row["execution_status"], row["recording_status"])
        want = (item["execution_status"], item["recording_status"])
        return None if got == want else f"{got}, expected {want}"
    if kind == "run_title":
        title = (model.run(item["run"]) or {}).get("title")
        return None if title == item["title"] else f"title {title!r}"
    return f"unknown check {kind!r}"


def check(out, *, manifest: dict | None = None, verify_digest: bool = True) -> list[str]:
    """What is wrong with the fixture under `out`, as a list (empty when nothing is): the manifest, its
    digest, the counts, a read of the records (no damaged line or file, nothing unreadable), every case's
    judgments and the expected file. The kit's reads rebuild its derived files (the hash cache); they are
    emptied again at the end (clear_derived), so the fixture is cold afterwards as it was generated."""
    out = Path(out)
    if manifest is None:
        try:
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return [f"manifest.json: {exc}"]
    problems = _validate(manifest, "manifest.schema.json", "manifest.json")
    if problems:
        return problems
    if verify_digest and manifest.get("digest") != tree_digest(out):
        problems.append("the tree's digest is not the manifest's (a file changed since it was generated)")
    root = out / manifest["project"]
    try:
        return _check_records(out, root, manifest, problems)
    finally:
        clear_derived(root)


def _check_records(out: Path, root: Path, manifest: dict, problems: list[str]) -> list[str]:
    kit = load_kit()
    project = kit.core.Project(root)
    model = kit.relations.read(root, alive=project.platform.processes.alive,
                               sha=lambda p: sha256_file(p) if Path(p).is_file() else None)
    journal = model.journal
    if journal.damaged or model.damaged_files or model.unsupported:
        problems.append(f"the read found damaged lines {journal.damaged}, damaged files {model.damaged_files} or "
                        f"unsupported records {model.unsupported}")
    counts = manifest["counts"]
    actual = {"runs": len(model.runs()), "events": journal.lines,
              "datasets": len(list((root / "data" / "raw").glob("*.parquet")))}
    for key, value in actual.items():
        if counts.get(key) != value:
            problems.append(f"{key}: the project has {value}, the manifest says {counts.get(key)}")
    if manifest["kind"] == "scale":
        spec = SCALES[manifest["scale"]]
        for key in ("runs", "events", "datasets"):
            if actual[key] != getattr(spec, key):
                problems.append(f"{key}: {actual[key]}, scale {manifest['scale']} is {getattr(spec, key)}")
    status = kit.core.status(project)
    fresh = kit.core.Freshness(project, kit.core.list_runs(project))
    for case in manifest.get("cases", []):
        for item in case["checks"]:
            problem = evaluate_case_check(kit, project, model, status, fresh, item)
            if problem:
                problems.append(f"case {case['id']}: {item['type']}: {problem}")
    if manifest["kind"] in ("q10", "q13"):
        try:
            expected = json.loads((out / "expected.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return [*problems, f"expected.json: {exc}"]
        problems += _validate(expected, "expected.schema.json", "expected.json")
        problems += check_expected(kit, root, model, expected)
    return problems


def check_expected(kit, root: Path, model, expected: dict) -> list[str]:
    """The expected file's ids name the records they say, and its numbers are the data's."""
    problems = []
    texts = {e.id: e.value.get("text") for e in model.journal.events}
    runs = {r["run_id"]: r for r in model.runs()}
    if expected["fixture"] == "q10":
        evidence = q10_evidence(q10_numbers(q10_project_tables(root)))
        for example in expected["examples"]:
            if texts.get(example["claim_event"]) != example["claim"]:
                problems.append(f"{example['id']}: the claim is not the text of {example['claim_event']}")
            if (runs.get(example["run"]) or {}).get("execution_status") != "ok":
                problems.append(f"{example['id']}: run {example['run']} is not a successful run")
            if example["evidence"] != evidence[example["check"]]:
                problems.append(f"{example['id']}: the evidence is not the data's")
    else:
        facts = q13_facts(root)
        if expected["facts"] != facts:
            problems.append("q13: the facts are not the data's")
        for claim in expected["claims"]:
            if texts.get(claim["event"]) != claim["text"]:
                problems.append(f"q13: the claim is not the text of {claim['event']}")
        counts = {"ok": 0, "error": 0}
        for entry in expected["records"]["runs"].values():
            row = runs.get(entry["run"]) or {}
            counts[row.get("execution_status", "?")] = counts.get(row.get("execution_status", "?"), 0) + 1
            if row.get("execution_status") != entry["status"]:
                problems.append(f"q13: run {entry['run']} is {row.get('execution_status')}, not {entry['status']}")
        if counts != expected["records"]["run_counts"]:
            problems.append(f"q13: run counts {counts}")
    return problems


# --- the command line ---------------------------------------------------------------------------------

def main(argv=None) -> int:
    os.environ[POLARS_THREADS_VARIABLE] = "1"  # before anything imports Polars
    parser = argparse.ArgumentParser(prog="eval_fixtures.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    scale = sub.add_parser("scale", help="the S or M scale fixture")
    scale.add_argument("--scale", choices=sorted(SCALES), required=True)
    for name in ("q10", "q13"):
        sub.add_parser(name, help=f"the {name.upper()} fixture")
    for name in ("scale", "q10", "q13"):
        command = sub.choices[name]
        command.add_argument("--seed", type=int, default=0)
        command.add_argument("--format", default="v1", dest="fmt")
        command.add_argument("--out", required=True)
    checked = sub.add_parser("check", help="check a generated fixture")
    checked.add_argument("fixture")
    env = sub.add_parser("environment", help="write a measurement environment record")
    env.add_argument("--out", required=True)
    env.add_argument("--disk", help="a path on the disk measured, the fixture's directory (default: the folder "
                                    "of --out)")
    env.add_argument("--cache-state", choices=("cold", "warm", "unknown"), default="unknown")
    env.add_argument("--browser")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if args.command == "scale":
            manifest = generate_scale(args.out, args.scale, args.seed, args.fmt)
        elif args.command == "q10":
            manifest = generate_q10(args.out, args.seed, args.fmt)
        elif args.command == "q13":
            manifest = generate_q13(args.out, args.seed, args.fmt)
        elif args.command == "check":
            problems = check(args.fixture)
            for problem in problems:
                print(problem, file=sys.stderr)
            print(json.dumps({"ok": not problems, "problems": len(problems)}))
            return 1 if problems else 0
        else:
            target = Path(args.out)
            disk = Path(args.disk) if args.disk else target.parent
            write_lf(target, json_text(environment_record(disk, cache_state=args.cache_state,
                                                          browser=args.browser)))
            print(json.dumps({"ok": True, "out": str(target)}))
            return 0
    except UsageError as exc:
        print(f"eval_fixtures.py: {exc}", file=sys.stderr)
        return 2
    except FixtureError as exc:
        print(f"eval_fixtures.py: {exc}", file=sys.stderr)
        if args.command in ("scale", "q10", "q13"):
            print(f"eval_fixtures.py: nothing the generation wrote under {args.out} was kept; the same --out can "
                  "be given again", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "digest": manifest["digest"], "counts": manifest["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
