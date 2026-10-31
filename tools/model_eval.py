"""Run the real-model evaluation tasks (Issue #40) through aelix and score them against the answer files.

    python tools/model_eval.py validate [--fixtures DIR]
    python tools/model_eval.py serving-check --model ID
    python tools/model_eval.py run --model ID (--task ID ... | --all) --out DIR [--repeats N]
                               [--surface NAME] [--proposal NAME] [--checkout DIR] [--fixtures DIR]
                               [--work-root DIR] [--seed N] [--aelix-command CMD] [--aelix-auth FILE]
                               [--timeout S] [--script FILE]
    python tools/model_eval.py score DIR
    python tools/model_eval.py scan DIR

Run it in the development environment, where the evaluation fixtures can be made:

    uv run --locked python tools/model_eval.py run --model dry-run --task T06 --out DIR

The tasks, the answers, the model configurations and the conditions are files under
tools/model_eval_data/ (README.md there says what each holds). Per run the harness makes the task's
fixture with tools/eval_fixtures.py (seed fixed: the answers are written for one seed, and another
--seed is refused; the fixtures are lab.json projects written by the kit of eval_fixtures.py's
V1_KIT_COMMIT, made once from this checkout's git history under the fixtures directory, design OQ7),
copies only its project/ and input/ into a new work root outside OUT
(manifest.json and expected.json hold answers), starts the real aelix 0.1.0b2 in its JSON event mode
there with this checkout's extension (or another checkout's: --checkout), moves the work root into
the run's directory when aelix has exited, reads the events, and records the model and its whole
serving configuration, the host and kit versions, the conditions, every tool call and how it failed,
the tokens the host reports, what changed in the project and the score.

Isolation: the trust list, uv (a link to the real one), aelix's agent directory (its models.json and
state) and its session directory are inside OUT, and so is HOME: aelix and the model's tools get the
run's home/ (uv's cache and Pythons are passed explicitly). The model works in a work root that is a
new directory under --work-root (default: the system's temporary directory), never inside OUT, so the
fixtures and the other runs are not next to it; tool calls whose paths leave the work root, and those
that name '.aelix', 'auth.json' or the login's file, are listed in the record and set needs_review.
The child gets the caller's environment without AELIX_*, PI_*, LAB_* and every variable whose name
says it holds a credential (TOKEN, KEY, SECRET, PASSWORD, AUTH, CREDENTIAL, COOKIE; command.json lists
the names), except the model's own key variable. OUT and the work root may not be in ~/.aelix or the
login's directories. The harness opens no file under ~/.aelix: of the login's file it only reads the
metadata (existence, modification time, inode, number of names).

After aelix: SIGTERM, SIGHUP and SIGINT stop the harness through the run's own end; a run on timeout or
interrupt is stopped with SIGTERM to aelix's group and SIGKILL only after STOP_GRACE (a token refresh
in progress finishes writing the login). The harness then waits until no process works in the work
root (named on its command line, its working directory or an open file there, or started by aelix: the
kit's detached report build, a job the model left in the background; after_exit, at most
SETTLE_TIMEOUT seconds, then such processes are stopped within SETTLE_STOP_TIMEOUT more seconds;
the same deadline bounds process probes and all stop attempts), removes the project's Python environment
(.venv, which the kit makes with uv from the project's uv.lock: about 450 MB of public packages and
links into uv's Python and cache) without following a link, removes the links in the work root that
reach outside it (symbolic links leaving it, every name of a file that also has a name outside it —
a hard link — or that is the login's file), moves it into the run and looks for processes again. Each
step is recorded on its own; a work root that cannot be moved is searched where it is. With a model
that uses an aelix login (POSIX) the launcher, which holds the login, then masks the run by the login's
exact values (below). A record is on disk from the start (provisional, secret_scan not_scanned): a
harness killed outright leaves it, the launcher's lifeline stops aelix (and the launcher masks the run
on its own), and `scan OUT` secures and searches such a run; the summary lists it as not_scanned and the
exit status is not 0 while one is left.

Credentials. A model whose configuration names an aelix login (auth.source aelix_login, e.g. the
frontier model through aelix's openai-codex provider) uses the caller's own aelix login in place:
aelix is started through tools/model_eval_aelix.py, which opens aelix's credential store at the
login's file (--aelix-auth, default: where the caller's aelix keeps it; auth.json of an agent
directory, a regular file with one name) while every other piece of aelix's state stays in the run's
agent directory. Only aelix opens that file; it refreshes a token there itself. The harness never
reads, copies or links it: it asks aelix to list the model with it (aelix lists a model only when its
provider has a login entry), classifies a run whose first request the API refused for the login as
not_run (aelix_login_unusable), and records the file's metadata before the run, after aelix exited
and as the very last step after the scan (a change after aelix exited, or a second name, fails the
scan). The login's exact values are compared only inside the launcher (aelix's own process, which holds
them): when aelix's main has returned (or SIGTERM stopped it) the launcher waits, and once nothing works
in the work root any more and it is in the run, it masks every file, link target and name under the run
by each value's forms (raw, JSON-escaped, URL-encoded, base64/base64url, hex, UTF-16; at least
EXACT_MIN_LENGTH characters; not a value split across lines), then run.json once more when the harness
has written it (LauncherLink). It reports counts and relative paths only, never a value, its length or
hash; the record's secret_scan.exact holds them, and a value found (exact_values), a file it could not
read (exact_unreadable) or a pass that did not run (exact_not_run: Windows, where the launcher has no
pipe; a harness killed before it asked) fails the scan. A model whose configuration names a key
variable (api_key_env) gets the key from the environment; the harness never writes it: command.json keeps variable names only, the run record is
redacted before it is written, and after every run, also one that failed after aelix exited, every
file under the run's directory is searched for the key (and the caller's credential variables) in each
of the forms the launcher masks the login's values by (its forms_of; on every platform: a shell may
write a file as UTF-16, as Windows PowerShell 5.1 does), each overwritten by a marker of the same
length. Then every file under the run's
directory is searched for credential-shaped text without knowing any value (a JWT, a Bearer token, a
refresh/access/id token, key, secret or password field with a value, an sk- key, a long mixed
base64url token; a file name of that shape too; after the launcher's masking, so what it found is not
counted twice): each one is overwritten by a marker of its length and listed (file, line, kind, length)
in the record's secret_scan, which is then failed. A masked file
replaces the old one (a temporary file, then os.replace): no file is ever written through. Links are
not followed; a directory or file that cannot be read fails the scan, as does a file written after
the scan started. A file the model wrote outside its work root is not searched.

Tokens are what aelix records from the model API's usage (per assistant message). What the API or
aelix does not report is `not_measured` with the reason; bytes and characters are never turned into
tokens. The dry-run model is a scripted fake server whose usage numbers are fixed, not measured.

Exit status: 0 when every requested run was executed and recorded (a failed or timed-out task is
data), 1 when a run could not be executed or recorded, its secret scan did not pass, a run of OUT was
never searched, or the harness was stopped by a signal, 2 on a usage or configuration error (nothing
run), 3 when a precondition is missing and nothing was run (no aelix login for the model or one the
API refuses, no API key, serving differs from the pinned configuration): the summary records those
runs as not_run.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import http.server
import importlib.util
import io
import json
import os
import platform
import re
import select
import shlex
import shutil
import signal
import stat
import statistics
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@functools.cache
def read_model():
    """The checkout's read model (src/aelix_analytics/relations.py), loaded by its file: it imports
    nothing of the package. A run's Visualizations are read through its one view of a lab.run/1
    run's charts and an analytics.run/2 run's visualizations (Issue #13, design 5.3)."""
    path = ROOT / "src" / "aelix_analytics" / "relations.py"
    spec = importlib.util.spec_from_file_location("model_eval_relations", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
DATA = Path(__file__).resolve().parent / "model_eval_data"
FIXTURE_TOOL = Path(__file__).resolve().parent / "eval_fixtures.py"
# tools/eval_fixtures.py's GENERATOR_VERSION the answers were written against (checked on manifests).
FIXTURE_GENERATOR_VERSION = 1

RUN_SCHEMA = "aelix-analytics.model-eval-run/1"
SUMMARY_SCHEMA = "aelix-analytics.model-eval-summary/1"
TASKS_SCHEMA = "aelix-analytics.model-eval-tasks/1"
ANSWER_SCHEMA = "aelix-analytics.model-eval-answer/1"
MODEL_SCHEMA = "aelix-analytics.model-eval-model/1"
CONDITIONS_SCHEMA = "aelix-analytics.model-eval-conditions/1"
SCRIPT_SCHEMA = "aelix-analytics.model-eval-script/1"

AELIX_VERSION = "0.1.0b2"
AELIX_COMMAND = ("uv", "run", "--isolated", "--no-project", "--python", "3.12", "--with",
                 f"aelix=={AELIX_VERSION}", "aelix")
# The Python of that environment, for the launcher of a model that uses the caller's aelix login.
AELIX_PYTHON = (*AELIX_COMMAND[:-1], "python")
LAUNCHER = Path(__file__).resolve().parent / "model_eval_aelix.py"
APIS = ("openai-completions", "openai-responses", "openai-codex-responses")
AUTH_SOURCES = ("aelix_login",)
LOGIN_HOWTO = ("log in with aelix 0.1.0b2 itself: run `aelix`, type /login and choose the provider "
               "(openai-codex: OpenAI Codex, the ChatGPT Plus/Pro login); aelix writes the login to "
               "~/.aelix/agent/auth.json (or $AELIX_CODING_AGENT_DIR/auth.json)")

GATES = ("Q2", "Q3", "Q5", "Q11", "Q12", "Q13")
Q11_REQUESTS = ("single", "combination", "ambiguous", "similar_tools")
SETUPS = ("fixture", "fresh_with_input")
FIXTURE_KINDS = ("scale", "q10", "q13")
ANSWER_STATUSES = ("draft", "confirmed")
CHECK_KINDS = ("fact", "evidence", "uncertainty", "state", "behavior", "critical")
# Each check type with the keys it needs.
CHECK_TYPES = {
    "text_any": ("values",), "text_all": ("values",), "number": ("value",), "percent": ("value",),
    "number_near": ("value", "words"), "cooccur": (), "claim_verdicts": ("claims",),
    "ids_all": ("values",), "ids_any": ("values",), "stated_as_current": ("patterns", "unless"),
    "new_records": (), "new_runs": (), "new_charts": (), "chart_covers": ("values",),
    "derived_table": ("min_cells", "time_columns", "score_columns"), "context_changed": (),
    "tool_called": ("role",),
}
SCOPES = ("clause", "sentence", "line", "paragraph")
# What a task measures in the comparison with aelix alone (docs/design/PRODUCT-EVALUATION.md §4).
AREAS = ("E1", "E2", "E3", "E4", "E5", "E6")
VERDICTS = ("supported", "false", "unverified")
ROLES = ("status", "query", "import", "run", "record", "init")
OUTCOMES = ("success", "failure", "error", "timeout", "not_run")

# The two generations of public names (#9): what a checkout's extension registers and reads.
GENERATIONS = {
    "analytics": {
        "extension": "extension/aelix_analytics.py",
        "tools": {"status": "analytics_status", "query": "analytics_query", "import": "analytics_import",
                  "run": "analytics_run", "record": "analytics_record", "init": "analytics_init"},
        "env": {"trust": "AELIX_ANALYTICS_TRUST_FILE", "uv": "AELIX_ANALYTICS_UV",
                "project": "AELIX_ANALYTICS_PROJECT"},
    },
    "lab": {
        "extension": "extension/aelix_lab.py",
        "tools": {"status": "lab_status", "query": "lab_query", "import": "lab_import", "run": "lab_run",
                  "record": "lab_note", "init": "lab_init"},
        "env": {"trust": "AELIX_LAB_TRUST_FILE", "uv": "AELIX_LAB_UV", "project": "AELIX_LAB_PROJECT"},
    },
}
# Unset in every child: the old names make the current kit refuse to run; a caller's own choices of
# project, trust list, uv, aelix or pi state (AELIX_SETTINGS_PATH, AELIX_AUTH_PATH, AELIX_PROJECT_DIR,
# PI_*: aelix 0.1.0b2 honours some of them even with an agent directory) must not leak into a
# measured session. The harness sets the ones it needs afterwards.
ISOLATED_PREFIXES = ("AELIX_", "PI_")
ISOLATED_KEYS = ("LAB_PROJECT", "LAB_ACTOR", "LAB_SESSION", "LAB_STYLE", "VIRTUAL_ENV")
# A variable whose name says it holds a credential is not passed on (the model's bash tool could
# print it): the caller's tokens, keys and passwords. The values of the narrower set are also
# overwritten by their bytes wherever a run holds them, should the model find them elsewhere.
SECRET_ENV = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL|COOKIE", re.IGNORECASE)
SECRET_VALUE_ENV = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE", re.IGNORECASE)
MIN_SECRET_LENGTH = 8

# Extension error codes that mean the call's arguments were wrong (the kit's AnalyticsError codes).
ARGUMENT_CODES = frozenset({
    "run_args", "run_needs_title", "run_needs_script", "run_name", "script_location", "script_outside",
    "script_missing", "rerun_id", "rerun_unknown", "bad_kind", "empty_note", "resolve_needs_target",
    "unknown_event", "dataset_name", "dataset_name_taken", "dataset_exists", "name_not_portable",
    "import_source_missing", "import_format", "query_empty", "query_single_statement",
    "query_read_only", "query_parse", "init_path", "init_not_empty", "init_not_directory"})
ARGUMENT_CLASSES = ("unknown_tool", "schema", "argument")
ERROR_REPLY = re.compile(r"^([a-z_]+): error \[([a-z0-9_]+)\]")
RUN_REPLY = re.compile(r"^[a-z_]+_run: ([a-z_]+) — run ")

RECORD_ID = re.compile(r"(?<![A-Za-z0-9_])e[0-9]+(?![A-Za-z0-9_])")
RUN_ID = re.compile(r"(?<![A-Za-z0-9_])[0-9]{8}-[0-9]{6}-[a-z0-9][a-z0-9_-]*[a-z0-9]")
# A number with its sign, thousands written with commas or (thin) spaces, a decimal part and an
# exponent: -20.6, 5,838, 5 838, 5.838e3. A hyphen between digits (2026-08-01) is not a sign.
NUMBER = re.compile(
    r"(?:(?<![A-Za-z0-9_.,])([-−+]))?(?<![A-Za-z0-9_.,])"
    r"((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,3}(?:[    ][0-9]{3})+(?![0-9])|[0-9]+)"
    r"(?:\.[0-9]+)?)(?:[eE]([-+]?[0-9]{1,3}))?(?![0-9])")
# One part of a Korean amount (3,258만 6,900원, 4억 4,596만 9,600, 3,258.69만): a number and its unit.
KO_PART = re.compile(r"(?<![0-9.,])([0-9][0-9,]*(?:\.[0-9]+)?)\s*([조억만])")
KO_UNITS = {"조": 10 ** 12, "억": 10 ** 8, "만": 10 ** 4}
KO_TAIL = re.compile(r"\s*([0-9][0-9,]*(?:\.[0-9]+)?)(?![0-9,.]*\s*[조억만])")
PERCENT_SIGN = re.compile(r"\s*(?:%p?|퍼센트|percent(?:age points?)?|pct|pp)(?![A-Za-z])", re.IGNORECASE)

TOKEN_NOT_REPORTED = {
    "openai-completions": {
        "cache_write": "the chat completions usage has no cache-write count (aelix 0.1.0b2 "
                       "openai_completions._usage_to_dict reads prompt_tokens, completion_tokens and "
                       "prompt_tokens_details.cached_tokens only)",
        "reasoning": "aelix 0.1.0b2 does not read completion_tokens_details; reasoning tokens are "
                     "inside the output count",
    },
    "openai-responses": {
        "cache_write": "aelix 0.1.0b2 writes cache_write = 0 without reading it from the Responses "
                       "API (_openai_responses_shared.py); the API's own usage object would carry it",
    },
    "openai-codex-responses": {
        "cache_write": "aelix 0.1.0b2 writes cache_write = 0 without reading it: the codex adapter "
                       "reads the backend's usage with the shared Responses engine "
                       "(_openai_responses_shared.py)",
    },
}
# What a measured value of a Responses usage cannot tell.
TOKEN_NOTES = {
    "openai-responses": {
        "reasoning": "a subset of output; aelix 0.1.0b2 writes 0 when the usage has no "
                     "output_tokens_details.reasoning_tokens, so a 0 is not told apart from not reported",
    },
    "openai-codex-responses": {
        "reasoning": "a subset of output; aelix 0.1.0b2 writes 0 when the usage has no "
                     "output_tokens_details.reasoning_tokens, so a 0 is not told apart from not reported",
    },
}


class UsageError(Exception):
    """A usage or configuration error: nothing is run (exit 2)."""


class HarnessError(Exception):
    """A run could not be executed or recorded (exit 1)."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UsageError(f"{path} does not exist") from exc
    except json.JSONDecodeError as exc:
        raise UsageError(f"{path} is not JSON: {exc}") from exc


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def measured(value: int) -> dict:
    return {"status": "measured", "value": value}


def not_measured(reason: str) -> dict:
    return {"status": "not_measured", "reason": reason}


def processes():
    """The kit's process port (spawn in a group, end a tree, find a program) for this platform."""
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from aelix_analytics import _platform

    return _platform.detect().processes


# --- configuration --------------------------------------------------------------------------------

@dataclass
class Config:
    tasks: dict
    fixtures: dict
    answers: dict
    models: dict
    conditions: dict
    words: dict
    model_files: dict = field(default_factory=dict)  # model id -> sha256 of its file's bytes


def load_config(data: Path = DATA) -> Config:
    """Every file under model_eval_data, checked; a problem raises UsageError naming all of them."""
    problems: list[str] = []
    tasks_doc = read_json(data / "tasks.json")
    conditions = read_json(data / "conditions.json")
    answers = {}
    for path in sorted((data / "answers").glob("*.json")):
        answers[path.stem] = read_json(path)
    models, model_files = {}, {}
    for path in sorted((data / "models").glob("*.json")):
        models[path.stem] = read_json(path)
        model_files[path.stem] = sha256_file(path)
    problems += check_tasks(tasks_doc)
    tasks = {t["id"]: t for t in tasks_doc.get("tasks", []) if isinstance(t, dict) and "id" in t}
    problems += check_conditions(conditions)
    words = conditions.get("word_sets", {}) if isinstance(conditions, dict) else {}
    for task_id in tasks:
        if task_id not in answers:
            problems.append(f"answers/{task_id}.json is missing")
    for task_id, answer in answers.items():
        problems += [f"answers/{task_id}.json: {p}" for p in check_answer(answer, task_id, tasks, words)]
    for model_id, model in models.items():
        problems += [f"models/{model_id}.json: {p}" for p in check_model(model, model_id)]
    if problems:
        raise UsageError("invalid evaluation configuration:\n  " + "\n  ".join(problems))
    return Config(tasks, tasks_doc["fixtures"], answers, models, conditions, words, model_files)


def check_tasks(doc) -> list[str]:
    out = []
    if not isinstance(doc, dict) or doc.get("schema") != TASKS_SCHEMA:
        return [f"tasks.json: schema is not {TASKS_SCHEMA}"]
    fixtures = doc.get("fixtures")
    if not isinstance(fixtures, dict) or not fixtures:
        out.append("tasks.json: fixtures is missing")
        fixtures = {}
    for name, spec in fixtures.items():
        if spec.get("kind") not in FIXTURE_KINDS or not isinstance(spec.get("seed"), int):
            out.append(f"tasks.json: fixture {name} needs a kind of {FIXTURE_KINDS} and an integer seed")
        if spec.get("kind") == "scale" and spec.get("scale") not in ("S", "M"):
            out.append(f"tasks.json: fixture {name} is a scale fixture without scale S or M")
        if spec.get("generator_version") != FIXTURE_GENERATOR_VERSION:
            out.append(f"tasks.json: fixture {name} is for generator version "
                       f"{spec.get('generator_version')}, not {FIXTURE_GENERATOR_VERSION}")
    tasks = doc.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return out + ["tasks.json: tasks is missing"]
    seen = set()
    for task in tasks:
        tid = task.get("id") if isinstance(task, dict) else None
        if not isinstance(tid, str) or not re.fullmatch(r"T[0-9]{2}", tid):
            out.append(f"tasks.json: a task has no id like T01: {str(task)[:80]}")
            continue
        if tid in seen:
            out.append(f"tasks.json: {tid} appears twice")
        seen.add(tid)
        where = f"tasks.json {tid}"
        if task.get("fixture") not in fixtures:
            out.append(f"{where}: fixture {task.get('fixture')!r} is not one of {sorted(fixtures)}")
        if task.get("setup") not in SETUPS:
            out.append(f"{where}: setup is not one of {SETUPS}")
        if task.get("setup") == "fresh_with_input":
            fresh = task.get("fresh_project") or {}
            if not (fresh.get("title") and fresh.get("goal") and fresh.get("inputs")):
                out.append(f"{where}: fresh_with_input needs fresh_project with title, goal and inputs")
        gates = task.get("gates") or []
        if not gates or any(g not in GATES for g in gates):
            out.append(f"{where}: gates must be among {GATES}")
        if task.get("q11_request") not in Q11_REQUESTS:
            out.append(f"{where}: q11_request is not one of {Q11_REQUESTS}")
        requests = task.get("requests")
        if not isinstance(requests, list) or not requests or not all(
                isinstance(r, str) and r.strip() for r in requests):
            out.append(f"{where}: requests must be non-empty strings")
        elif any(r.lstrip().startswith(("-", "@")) for r in requests):
            out.append(f"{where}: a request may not start with - or @ (aelix reads those as options or files)")
        for cond in task.get("proposal_conditions") or []:
            if cond not in ("unrequested", "requested", "always_on"):
                out.append(f"{where}: unknown proposal condition {cond}")
        if not isinstance(task.get("timeout_s"), int) or task["timeout_s"] <= 0:
            out.append(f"{where}: timeout_s must be a positive integer")
        areas = task.get("areas")
        if areas is not None and (not isinstance(areas, list) or not areas or any(a not in AREAS for a in areas)):
            out.append(f"{where}: areas must be among {AREAS}")
    return out


def check_conditions(doc) -> list[str]:
    if not isinstance(doc, dict) or doc.get("schema") != CONDITIONS_SCHEMA:
        return [f"conditions.json: schema is not {CONDITIONS_SCHEMA}"]
    out = []
    surfaces = doc.get("surfaces") or {}
    if "default" not in surfaces:
        out.append("conditions.json: surfaces needs default")
    for name, surface in surfaces.items():
        tools = surface.get("tools")
        if tools is not None and (not isinstance(tools, list) or not tools):
            out.append(f"conditions.json: surface {name}: tools must be null or a non-empty list")
        if not surface.get("extension") and surface.get("kit", True):
            out.append(f"conditions.json: surface {name} names no extension file")
        if not surface.get("kit", True) and (surface.get("extension") or surface.get("tools") is not None):
            out.append(f"conditions.json: surface {name} has no kit: it takes aelix's own tools and no extension")
    proposals = doc.get("proposals") or {}
    for name in ("unrequested", "requested", "always_on"):
        if name not in proposals:
            out.append(f"conditions.json: proposals needs {name}")
    words = doc.get("word_sets") or {}
    for name in ("superseded", "resolved", "verdict_supported", "verdict_false", "verdict_unverified"):
        if not words.get(name):
            out.append(f"conditions.json: word_sets needs {name}")
    return out


def check_answer(answer, task_id: str, tasks: dict, words: dict) -> list[str]:
    out = []
    if not isinstance(answer, dict) or answer.get("schema") != ANSWER_SCHEMA:
        return [f"schema is not {ANSWER_SCHEMA}"]
    if answer.get("task") != task_id:
        out.append(f"task is {answer.get('task')!r}, not {task_id}")
    if task_id not in tasks:
        out.append("no task has this id")
    if answer.get("status") not in ANSWER_STATUSES:
        out.append(f"status must be one of {ANSWER_STATUSES}")
    if answer.get("status") == "confirmed" and not answer.get("confirmed_by"):
        out.append("a confirmed answer names who confirmed it (confirmed_by)")
    if not str(answer.get("correct_completion") or "").strip():
        out.append("correct_completion is empty")
    if "always_review" in answer and not str(answer["always_review"] or "").strip():
        out.append("always_review, when present, says why a person reviews every run")
    if not isinstance((answer.get("fixture") or {}).get("seed"), int):
        out.append("fixture.seed is missing: an answer is written for one seed")
    checks = answer.get("checks")
    if not isinstance(checks, list) or not checks:
        return out + ["checks is empty"]
    ids = set()
    for check in checks:
        cid = check.get("id")
        if not cid or cid in ids:
            out.append(f"check id {cid!r} is missing or repeated")
        ids.add(cid)
        kind, ctype = check.get("kind"), check.get("type")
        if kind not in CHECK_KINDS:
            out.append(f"check {cid}: kind {kind!r} is not one of {CHECK_KINDS}")
        if ctype not in CHECK_TYPES:
            out.append(f"check {cid}: type {ctype!r} is unknown")
            continue
        for key in CHECK_TYPES[ctype]:
            if key not in check:
                out.append(f"check {cid}: {ctype} needs {key}")
        if check.get("scope") is not None and check["scope"] not in SCOPES:
            out.append(f"check {cid}: scope {check['scope']!r} is not one of {SCOPES}")
        if kind == "critical" and ctype != "stated_as_current":
            out.append(f"check {cid}: a critical check is a stated_as_current check")
        if ctype == "stated_as_current":
            unless = check.get("unless")
            if isinstance(unless, str):
                name = unless[1:] if unless.startswith("@") else None
                if name is None or (name not in words and name != "superseded_or_reason"):
                    out.append(f"check {cid}: unknown word set {unless}")
            elif not isinstance(unless, list):
                out.append(f"check {cid}: unless is a word set (@name) or a list of words")
            if check.get("pattern_kind", "regex") == "regex":
                for pattern in check.get("patterns", []):
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        out.append(f"check {cid}: pattern {pattern!r} does not compile: {exc}")
            for closed, resolver in (check.get("resolved_by") or {}).items():
                if not (RECORD_ID.fullmatch(closed) and RECORD_ID.fullmatch(str(resolver))):
                    out.append(f"check {cid}: resolved_by {closed} -> {resolver} is not two record ids")
        if ctype == "cooccur" and not (check.get("all") or check.get("any")):
            out.append(f"check {cid}: cooccur needs all (words) or any (groups of words)")
        if ctype == "number_near" and check.get("prefer", "preceding") not in ("preceding", "nearest"):
            out.append(f"check {cid}: prefer is preceding or nearest")
        if ctype == "derived_table":
            for key in ("time_columns", "score_columns"):
                try:
                    re.compile(str(check.get(key)))
                except re.error as exc:
                    out.append(f"check {cid}: {key} does not compile: {exc}")
        if ctype == "claim_verdicts":
            for claim, verdict in (check.get("claims") or {}).items():
                if verdict not in VERDICTS or not RECORD_ID.fullmatch(claim):
                    out.append(f"check {cid}: claim {claim} → {verdict} is not a record id and a verdict")
        if ctype == "tool_called" and check.get("role") not in ROLES:
            out.append(f"check {cid}: role {check.get('role')!r} is not one of {ROLES}")
    return out


def strings_in(value) -> list[str]:
    """Every key and string value in a JSON value, as written."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [k, *strings_in(v)]]
    if isinstance(value, list):
        return [s for v in value for s in strings_in(v)]
    return []


def check_model(model, model_id: str) -> list[str]:
    out = []
    if not isinstance(model, dict) or model.get("schema") != MODEL_SCHEMA:
        return [f"schema is not {MODEL_SCHEMA}"]
    if model.get("id") != model_id:
        out.append(f"id is {model.get('id')!r}, not the file name {model_id}")
    for key in ("provider", "model", "api", "thinking", "serving"):
        if not model.get(key):
            out.append(f"{key} is missing")
    if model.get("api") not in APIS:
        out.append(f"api must be one of {APIS} (the adapters whose usage this harness reads)")
    env = model.get("api_key_env")
    if env is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]*", str(env)):
        out.append("api_key_env must be the NAME of an environment variable")
    auth = model.get("auth")
    if auth is not None:
        if not isinstance(auth, dict) or auth.get("source") not in AUTH_SOURCES or \
                not isinstance(auth.get("provider"), str) or not auth["provider"]:
            out.append(f"auth must say its source ({AUTH_SOURCES}) and the aelix provider whose login it uses")
        elif auth["provider"] != model.get("provider"):
            out.append(f"auth.provider {auth['provider']!r} is not the model's provider {model.get('provider')!r}")
        if env is not None:
            out.append("a model uses an aelix login or a key variable, not both")
    provider = model.get("aelix_provider")
    if provider is not None:
        key = str(provider.get("apiKey") or "")
        if re.search(r"sk-[A-Za-z0-9_-]{16,}", key):
            out.append("aelix_provider.apiKey looks like a real key: keys are read from the environment only")
        if auth is not None and "apiKey" in provider:
            out.append("aelix_provider.apiKey is set for a model that uses an aelix login")
        if not provider.get("models") or not any(m.get("id") == model.get("model")
                                                 for m in provider["models"]):
            out.append("aelix_provider.models has no entry for the model")
    shaped = [f for text in [json.dumps(model, ensure_ascii=False), *strings_in(model)]
              for f in credential_findings(text.encode("utf-8"))]
    if shaped:
        out.append("the configuration holds credential-shaped text (" + ", ".join(
            sorted({f["kind"] for f in shaped})) + "): credentials are never part of a configuration")
    serving = model.get("serving") or {}
    for key in ("backend", "context_window", "output_reservation", "tokenizer", "quantization"):
        if key not in serving:
            out.append(f"serving.{key} is missing")
    if serving.get("backend") == "ollama":
        for key in ("backend_version", "endpoint", "model_tag", "manifest_digest", "server_env"):
            if not serving.get(key):
                out.append(f"serving.{key} is missing")
    return out


# --- reading an answer ----------------------------------------------------------------------------
#
# Heuristics, not understanding. An answer is cut into paragraphs, lines, sentences and clauses. A
# word or a value counts where it is not negated (미해결, 해결되지 않은, not resolved, 46.8%가
# 아니라) and not asked (해결됐는지, whether). A critical check that fires, and one whose pattern a
# rescuing word let pass, set needs_review: a person confirms them (#24).

SPLITTERS = {
    "paragraph": re.compile(r"\n[ \t]*\n"),
    "line": re.compile(r"\n"),
    "sentence": re.compile(r"\n|(?<=[.!?。？])\s+"),
    "clause": re.compile(r"\n|(?<=[.!?。？])\s+|[,;]\s+|:\s+|\s[—–]\s"),
}
# A quotation on one line: a period inside it does not end the answer's sentence (#24's campaign: an answer
# said e74 was 4,000 characters and cut mid-sentence, quoting its last words "…낮다. 센"). A quotation
# holds no other kind of quotation mark and is at most 300 characters, so that a mark left unpaired
# does not join the sentences after it.
QUOTED = re.compile(r"“[^“”\"\n]{0,300}”|「[^「」\n]{0,300}」|『[^『』\n]{0,300}』|\"[^\"“”\n]{0,300}\"")
CLAUSE_BOUNDARY = re.compile(r"\n|(?<=[.!?。？])\s|,\s|;|:\s|\s[—–]\s")
STRIP = ".,!?\"'`*()[]{}<>“”‘’「」『』|"
# Korean and English negation: after a word (해결되지 않은, 맞지 않다, 사실이 아니다, 안 됨, is not) or
# before it (not resolved, never, 안 맞다, 미해결).
NEG_KO = ("않", "아니", "못", "없", "안되", "안돼", "안됐", "안됨", "안된", "안해", "안했", "안함")
NEG_EN = frozenset({"not", "never", "no", "cannot", "nor", "without"})
# A word that ends its own clause: the window of a value's or a word's predicate stops after it.
CONNECTIVE_KO = ("고", "며", "지만", "는데", "으나")
CONNECTIVE_EN = frozenset({"and", "but", "while", "whereas", "because", "since", "which", "although",
                           "though"})
# What says a value is not the answer, right after it (5,838이 아니라, 46.8% is wrong).
DENY_KO = ("아니", "않", "틀렸", "틀린", "틀립", "틀리다")
DENY_EN = frozenset({"not", "never", "wrong", "incorrect", "false"})
QUESTION_KO = re.compile(r"(는지|은지|인지|을지|할지|될지|는가|은가|인가)")
# Verdict words that are a verdict only on their own: 뒷받침 as a whole word, not 뒷받침하는 근거;
# English adjectives not after a determiner (the correct maximum is not a verdict on the claim).
BARE_ONLY = frozenset({"뒷받침"})
ATTRIBUTIVE = frozenset({"correct", "incorrect", "true", "untrue", "false", "wrong", "consistent",
                         "inconsistent", "supported", "unsupported", "confirmed", "unknown"})
DETERMINERS = frozenset({"the", "a", "an", "this", "that", "these", "those", "its", "their", "his",
                         "her", "our", "any"})
# A verdict that heads its claims (Supported: e8) instead of following one (e8: supported).
HEADING = re.compile(r"\s*\**\s*[:：]\s*\**\s*e[0-9]+(?![0-9A-Za-z_])")
# What may stand between two claim ids that share one verdict (e15와 e20은, e15(…), e20).
JOINER = re.compile(r"\s*\**\s*(?:\([^()]*\))?\s*\**\s*(?:,|와|과|및|and|&|/|·|、|or|또는)?\s*",
                    re.IGNORECASE)
SYSTEM_PATHS = ("/usr/", "/bin/", "/sbin/", "/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr",
                "/opt/homebrew/", "/System/", "/Library/")
PATH_SPLIT = re.compile(r"[\s'\"`;|&<>()=,]+")
# Tool arguments that hold prose or a search pattern, not a path.
PROSE_KEYS = frozenset({"text", "title", "goal", "pattern", "regex", "summary", "caption",
                        "description", "message"})


def words_of(spec, words: dict, extra=()) -> list[str]:
    if isinstance(spec, str) and spec.startswith("@"):
        name = spec[1:]
        if name == "superseded_or_reason":
            return list(words.get("superseded", [])) + list(extra)
        return list(words.get(name, [])) + list(extra)
    return list(spec or []) + list(extra)


def contains_any(text: str, values) -> list[str]:
    low = text.casefold()
    return [v for v in values if v.casefold() in low]


def segments(text: str, scope: str) -> list[tuple[int, int]]:
    """(start, end) of each non-empty paragraph, line, sentence or clause of text."""
    quoted = [m.span() for m in QUOTED.finditer(text)] if scope == "sentence" else []
    out, pos = [], 0
    for m in SPLITTERS[scope].finditer(text):
        if any(s < m.start() < e for s, e in quoted):
            continue
        if text[pos:m.start()].strip():
            out.append((pos, m.start()))
        pos = m.end()
    if text[pos:].strip():
        out.append((pos, len(text)))
    return out


def containing(spans: list[tuple[int, int]], pos: int, default: tuple[int, int]) -> tuple[int, int]:
    return next(((s, e) for s, e in spans if s <= pos < e), default)


def clause_of(seg: str, start: int, end: int) -> tuple[int, int]:
    left = 0
    for m in CLAUSE_BOUNDARY.finditer(seg, 0, start):
        left = m.end()
    m = CLAUSE_BOUNDARY.search(seg, end)
    return left, (m.start() if m else len(seg))


def words_after(seg: str, end: int, limit: int, *, stop_at_number: bool = False) -> tuple[str, list[str]]:
    """The rest of the word `end` is in, and up to `limit` following words of its clause. A word that
    ends a clause of its own (…고, …며, and, because) is the last; with stop_at_number a word that is
    another value ends the window before it."""
    _, right = clause_of(seg, end, end)
    tail = seg[end:right]
    pieces = tail.split()
    rest = pieces.pop(0) if pieces and tail[:1] and not tail[0].isspace() else ""
    out = []
    for piece in pieces:
        if len(out) >= limit:
            break
        word = piece.strip(STRIP)
        if stop_at_number and re.match(r"[-−+]?[0-9]", word):
            break
        out.append(word)
        if word.endswith(CONNECTIVE_KO) or word.lower() in CONNECTIVE_EN:
            break
    return rest, out


def words_before(seg: str, start: int, limit: int) -> tuple[str, list[str]]:
    """The part of the word before `start` (미 of 미해결), and up to `limit` words before it in the
    clause."""
    left, _ = clause_of(seg, start, start)
    head = seg[left:start]
    pieces = head.split()
    attached = pieces.pop() if pieces and head[-1:] and not head[-1].isspace() else ""
    return attached, [p.strip(STRIP) for p in pieces[-limit:]] if limit else []


def is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def negated(seg: str, start: int, end: int) -> bool:
    """Whether the word at seg[start:end] is negated in its clause."""
    attached, before = words_before(seg, start, 3)
    if attached.endswith("미") and is_hangul(seg[start:start + 1] or " "):
        return True
    if any(w.lower() in NEG_EN or w.lower().endswith("n't") for w in before):
        return True
    if before and before[-1] in ("안", "못") and not attached:
        return True
    rest, after = words_after(seg, end, 3)
    if any(n in rest for n in NEG_KO):
        return True
    return any(w.startswith(NEG_KO) or w == "안" or w.lower() in ("not", "never")
               or w.lower().endswith("n't") for w in after)


def asked(seg: str, start: int, end: int) -> bool:
    """Whether the word is asked about rather than said (온전히 남아 있는지, whether it is intact)."""
    rest, _ = words_after(seg, end, 0)
    if QUESTION_KO.search(rest):
        return True
    _, before = words_before(seg, start, 4)
    return any(w.lower() in ("whether", "if") for w in before)


def denied(seg: str, start: int, end: int) -> bool:
    """Whether the value at seg[start:end] is said not to be the answer, right after it."""
    rest, after = words_after(seg, end, 4, stop_at_number=True)
    if any(d in rest for d in DENY_KO):
        return True
    return any(w.startswith(DENY_KO) or w.lower() in DENY_EN or w.lower().endswith("n't")
               for w in after)


@lru_cache(maxsize=1024)
def phrase_pattern(phrases: tuple[str, ...], whole: bool) -> re.Pattern | None:
    """One pattern for all phrases, the longest first. An ASCII phrase does not start inside a word
    (correct is not in incorrect); with whole it does not end inside one either."""
    items = sorted({p for p in phrases if p and p.strip()}, key=len, reverse=True)
    if not items:
        return None
    parts = []
    for p in items:
        body = re.escape(p)
        if p[0].isascii() and p[0].isalnum():
            body = r"(?<![A-Za-z0-9])" + body
        if whole and p[-1].isascii() and p[-1].isalnum():
            body += r"(?![A-Za-z])"
        parts.append(body)
    return re.compile("|".join(parts), re.IGNORECASE)


def find_phrases(seg: str, phrases, *, negation: bool = False, questions: bool = False,
                 whole: bool = False) -> list[tuple[int, int, str]]:
    """The phrases found in one segment; negated or asked ones are left out when asked to."""
    pattern = phrase_pattern(tuple(phrases), whole)
    if pattern is None:
        return []
    out = []
    for m in pattern.finditer(seg):
        if negation and negated(seg, m.start(), m.end()):
            continue
        if questions and asked(seg, m.start(), m.end()):
            continue
        out.append((m.start(), m.end(), m.group(0)))
    return out


def phrases_in(text: str, phrases, *, negation: bool = False) -> list[str]:
    """The phrases found anywhere in text (negation read per sentence)."""
    found = []
    for s, e in segments(text, "sentence"):
        found += [p for _, _, p in find_phrases(text[s:e], phrases, negation=negation)]
    return found


def masked(text: str) -> str:
    """Record and run ids blanked: a word check must not be passed by an id (case-overwritten)."""
    text = RUN_ID.sub(lambda m: "□" * len(m.group(0)), text)
    return RECORD_ID.sub(lambda m: "□" * len(m.group(0)), text)


# --- numbers --------------------------------------------------------------------------------------

def ko_amounts(seg: str) -> list[tuple[float, int, int]]:
    """Korean amounts written with 조·억·만, each as one value: 4억 4,596만 9,600 is 445,969,600."""
    parts = list(KO_PART.finditer(seg))
    out, i = [], 0
    while i < len(parts):
        start, end, total, last, j = parts[i].start(), parts[i].end(), 0.0, None, i
        while j < len(parts):
            m = parts[j]
            if last is not None and (seg[end:m.start()].strip() or KO_UNITS[m.group(2)] >= KO_UNITS[last]):
                break
            total += float(m.group(1).replace(",", "")) * KO_UNITS[m.group(2)]
            last, end, j = m.group(2), m.end(), j + 1
        tail = KO_TAIL.match(seg, end)
        if tail:
            value = float(tail.group(1).replace(",", ""))
            if value < KO_UNITS[last]:
                total, end = total + value, tail.end()
        out.append((total, start, end))
        i = j
    return out


def number_spans(seg: str) -> list[tuple[float, int, int, str]]:
    """Every number in seg with its sign, as (value, start, end, text); a Korean amount is one more
    value spanning its parts."""
    out = []
    for m in NUMBER.finditer(seg):
        sign, body, exponent = m.group(1), m.group(2), m.group(3)
        digits = re.sub(r"[,\s   ]", "", body)
        with contextlib.suppress(ValueError, OverflowError):
            value = float(digits + (f"e{exponent}" if exponent else ""))
            out.append((-value if sign in ("-", "−") else value, m.start(), m.end(), m.group(0)))
    out += [(v, s, e, seg[s:e]) for v, s, e in ko_amounts(seg)]
    return out


def percent_spans(seg: str, *, fractions: bool = True) -> list[tuple[float, int, int, str]]:
    """Percentages in seg (in percent: 11.8 for 11.8%); with fractions a written fraction such as
    0.118 counts as 11.8."""
    out = []
    for value, start, end, raw in number_spans(seg):
        sign = PERCENT_SIGN.match(seg, end)
        if sign:
            out.append((value, start, sign.end(), seg[start:sign.end()]))
        elif fractions and "." in raw and abs(value) < 1 and not re.search(r"[eE조억만]", raw):
            out.append((value * 100, start, end, raw))
    return out


def numbers_in(text: str) -> list[float]:
    return [v for v, *_ in number_spans(text)]


def number_matches(found: float, value: float, check: dict) -> bool:
    slack = 1e-9 * max(1.0, abs(value))
    if "rel_tol" in check:
        return abs(found - value) <= abs(value) * float(check["rel_tol"]) + slack
    return abs(found - value) <= float(check.get("abs_tol", 0)) + slack


def asserted_values(text: str, check: dict, *, percent: bool) -> list[tuple[int, int, int, int]]:
    """Where the check's value is said (not denied right after it): (paragraph start, paragraph end,
    start, end), start and end within the paragraph."""
    if percent:
        want, tol = float(check["value"]) * 100, float(check.get("abs_tol_pp", 0.1))
    out = []
    for ps, pe in segments(text, "paragraph"):
        para = text[ps:pe]
        sentences = segments(para, "sentence")
        spans = percent_spans(para, fractions=check.get("fractions", True)) if percent else number_spans(para)
        for value, start, end, _ in spans:
            ok = abs(value - want) <= tol + 1e-9 if percent else number_matches(value, float(check["value"]), check)
            if not ok:
                continue
            ss, se = containing(sentences, start, (0, len(para)))
            if denied(para[ss:se], start - ss, end - ss):
                continue
            out.append((ps, pe, start, end))
    return out


def bound_to_words(text: str, check: dict, *, percent: bool) -> list[str]:
    """Where the value is said with its own words nearest: in the smallest of clause, sentence (and
    paragraph, when the check's scope is paragraph) that holds any of `words` or `against`, the
    nearest preceding one decides (`prefer: nearest`: the nearest either side)."""
    own, against = check["words"], check.get("against") or []
    scopes = ["clause", "sentence"] + (["paragraph"] if check.get("scope") == "paragraph" else [])
    prefer = check.get("prefer", "preceding")
    found = []
    for ps, pe, rel_s, rel_e in asserted_values(text, check, percent=percent):
        para = text[ps:pe]
        decided = None
        for scope in scopes:
            lo, hi = containing(segments(para, scope), rel_s, (0, len(para))) if scope != "paragraph" \
                else (0, len(para))
            seg = para[lo:hi]
            marks = [(s + lo, e + lo, "own") for s, e, _ in find_phrases(seg, own, negation=True)]
            marks += [(s + lo, e + lo, "against") for s, e, _ in find_phrases(seg, against, negation=True)]
            if not marks:
                continue
            if prefer == "nearest":
                decided = min(marks, key=lambda m: rel_s - m[1] if m[1] <= rel_s else m[0] - rel_e)[2]
            else:
                before = [m for m in marks if m[1] <= rel_s]
                decided = (max(before, key=lambda m: m[1]) if before
                           else min(marks, key=lambda m: m[0]))[2]
            break
        if decided == "own":
            found.append(para[max(0, rel_s - 60):rel_e + 20].strip())
    return found


# --- claims and verdicts --------------------------------------------------------------------------

def verdict_marks(seg: str, words: dict) -> list[tuple[int, int, str, bool]]:
    """Verdict phrases in one sentence: (start, end, verdict, heading). The longest phrase wins
    (뒷받침되지 않 over 뒷받침, not supported over supported), ASCII ones as whole words; a negated
    one turns supported into false and back (not correct, 거짓이 아니다), an asked one is left out."""
    phrases: dict[str, str] = {}
    for verdict in VERDICTS:
        for w in words.get(f"verdict_{verdict}", []):
            phrases.setdefault(w.casefold(), verdict)
    out = []
    for start, end, text in find_phrases(seg, list(phrases), questions=True, whole=True):
        verdict = phrases.get(text.casefold())
        rest, _ = words_after(seg, end, 0)
        if text in BARE_ONLY and rest.strip(STRIP):
            continue  # 뒷받침하는 근거 is not a verdict; a bare 뒷받침 (a table cell) is
        _, before = words_before(seg, start, 1)
        if text.casefold() in ATTRIBUTIVE and before and before[-1].lower() in DETERMINERS:
            continue  # the correct maximum, a false impression: a word about something else
        if verdict and negated(seg, start, end):
            verdict = {"supported": "false", "false": "supported"}.get(verdict)
        if verdict:
            out.append((start, end, verdict, bool(HEADING.match(seg, end))))
    return out


def claim_verdicts(text: str, claims, words: dict) -> tuple[dict[str, set], dict[str, bool]]:
    """The verdicts said for each claim id. Per sentence, ids that only a joiner separates (e15와
    e20) share one verdict; a verdict goes to the nearest group before it, or to the next one when it
    heads them (Supported: e8) or no group precedes it. A lone id whose sentence has no verdict (a
    heading line) takes the verdicts of the sentences after it, up to the next sentence with an id."""
    info = []
    mentioned = {c: False for c in claims}
    for s, e in segments(text, "sentence"):
        seg = text[s:e]
        mentions = sorted((m.start(), m.end(), c) for c in claims for m in id_pattern(c).finditer(seg))
        groups: list[dict] = []
        for start, end, claim in mentions:
            mentioned[claim] = True
            if groups and JOINER.fullmatch(seg[groups[-1]["end"]:start]):
                groups[-1]["claims"].add(claim)
                groups[-1]["end"] = end
            else:
                groups.append({"start": start, "end": end, "claims": {claim}, "verdicts": set()})
        loose = set()
        for start, end, verdict, heading in verdict_marks(seg, words):
            after = next((g for g in groups if g["start"] >= end), None)
            before = [g for g in groups if g["end"] <= start]
            target = after if heading else (before[-1] if before else after)
            if target is None:
                loose.add(verdict)
            else:
                target["verdicts"].add(verdict)
        info.append((groups, loose))
    for i, (groups, _) in enumerate(info):
        for group in groups:
            if group["verdicts"] or len(group["claims"]) != 1:
                continue
            for later_groups, later_loose in info[i + 1:]:
                if later_groups:
                    break
                group["verdicts"] |= later_loose
    detected: dict[str, set] = {c: set() for c in claims}
    for groups, _ in info:
        for group in groups:
            for claim in group["claims"]:
                detected[claim] |= group["verdicts"]
    return detected, mentioned


def stated_as_current(check: dict, text: str, words: dict) -> tuple[list[str], list[str]]:
    """The segments (sentences by default) that state a pattern as current, and all that mention it.
    A pattern counts where it is not asked, and (unless it is an id) not negated; a check with
    `together` needs one of those words in the segment too. A segment is let pass by an `unless`
    word that is not negated, or for an id by its `resolved_by` id. A segment that ends as a question
    is left out for words, not for ids: a record listed with its question's own text (`- e56 — 감소가
    계절 효과인가?`) is said to be open, which is what an id check is for (#24 campaign). Such a segment
    is read with the rest of its line, where a resolving id may follow (`… 인가? → e139로 해결`)."""
    scope = check.get("scope", "sentence")
    unless = words_of(check["unless"], words, check.get("reason_words", ()))
    together = check.get("together") or []
    resolved_by = check.get("resolved_by") or {}
    ids = check.get("pattern_kind") == "ids"
    patterns = [(p, id_pattern(p) if ids else re.compile(p, re.IGNORECASE)) for p in check["patterns"]]
    fired, mentioned = [], []
    for s, e in segments(text, scope):
        seg = text[s:e]
        if seg.rstrip().rstrip("*").endswith(("?", "？")):
            if not ids:
                continue
            line_end = text.find("\n", e)
            seg = text[s:len(text) if line_end < 0 else line_end]
        hits = []
        for name, pattern in patterns:
            for m in pattern.finditer(seg):
                if asked(seg, m.start(), m.end()) or (not ids and negated(seg, m.start(), m.end())):
                    continue
                hits.append(name)
        if not hits or (together and not find_phrases(seg, together, negation=True)):
            continue
        mentioned.append(seg.strip()[:200])
        if find_phrases(seg, unless, negation=True, questions=True):
            continue
        resolver = {h: resolved_by.get(h) for h in hits}
        if any(not (r and id_present(seg, str(r))) for r in resolver.values()):
            fired.append(seg.strip()[:200])
    return fired, mentioned


def id_pattern(value: str) -> re.Pattern:
    """An id as a whole token: not inside a longer id (ASCII boundaries, since Korean letters are
    word characters to `re`, and `e41의` cites e41)."""
    return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(value) + r"(?![A-Za-z0-9_]|-[A-Za-z0-9])")


def id_present(text: str, value: str) -> bool:
    return bool(id_pattern(value).search(text))


# --- the project's results ------------------------------------------------------------------------

def inside(path: Path, root: Path | None) -> bool:
    """Whether path, its links resolved, is a regular file under root. The harness reads only such
    files of a project: one the model linked to a file elsewhere (the owner's login) is never opened.
    (A hard link has no target to resolve: the sweep removes those before anything is read.)"""
    if root is None:
        return False
    try:
        real, base = os.path.realpath(path), os.path.realpath(root)
    except (OSError, ValueError):
        return False
    return real.startswith(base.rstrip("\\/") + os.sep) and os.path.isfile(real)


def chart_text(project: Path | None, chart: dict) -> str:
    """What a chart holds as text: its spec files (JSON, HTML, SVG), its data's column names and the
    data's text values (when polars can read it)."""
    parts = [" ".join(str(c) for c in chart.get("data_columns") or [])]
    if project is None:
        return parts[0]
    for rel in (chart.get("files") or {}).values():
        path = project / str(rel)
        if path.suffix.lower() in (".json", ".html", ".svg", ".htm") and inside(path, project) \
                and path.stat().st_size <= 50_000_000:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    data = project / str(chart["data"]) if chart.get("data") else None
    if data is not None and inside(data, project):
        with contextlib.suppress(Exception):  # polars missing or the file unreadable: spec only
            import polars as pl

            frame = pl.read_parquet(data)
            for name, dtype in frame.schema.items():
                if dtype == pl.String:
                    parts.append(" ".join(str(v) for v in frame[name].unique().head(10_000).to_list()))
    return "\n".join(parts)


def table_shape(project: Path | None, output: dict) -> tuple[int | None, list[str]]:
    """Rows and column names of a table a run wrote: the kit records them for Parquet; a CSV file is
    read (its header and line count)."""
    if output.get("schema") is not None:
        return output.get("rows"), [str(c[0]) for c in output["schema"]]
    path = project / str(output.get("path")) if project is not None and output.get("path") else None
    if path is None or path.suffix.lower() != ".csv" or not inside(path, project) \
            or path.stat().st_size > 500_000_000:
        return None, []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        header = f.readline().strip().split(",")
        rows = sum(1 for line in f if line.strip())
    return rows, [h.strip().strip('"') for h in header]


# --- scoring --------------------------------------------------------------------------------------

@dataclass
class Evidence:
    """What a run left to score: the answers' text, the tool calls and the project's changes."""
    text: str
    last_turn: str
    calls: list
    tools: dict
    state: dict
    existing_ids: set
    project: Path | None = None


def run_check(check: dict, ev: Evidence, words: dict) -> dict:
    ctype = check["type"]
    text = ev.last_turn if check.get("scope") == "last_turn" else ev.text
    result: dict = {"id": check["id"], "kind": check["kind"], "type": ctype,
                    "required": check.get("required", True), "what": check.get("what")}
    passed, detail, review = False, None, False
    if ctype in ("text_any", "text_all"):
        hits = phrases_in(masked(text), check["values"], negation=check.get("negation", False))
        folded = {h.casefold() for h in hits}
        if ctype == "text_any":
            passed, detail = bool(hits), {"found": sorted(set(hits))[:10]}
        else:
            missing = [v for v in check["values"] if v.casefold() not in folded]
            passed, detail = not missing, {"missing": missing}
    elif ctype in ("number", "percent"):
        found = asserted_values(text, check, percent=ctype == "percent")
        passed = bool(found)
        detail = {"found": [text[ps + s:ps + e] for ps, _, s, e in found][:5]}
    elif ctype == "number_near":
        found = bound_to_words(text, check, percent=bool(check.get("percent")))
        passed, detail = bool(found), {"found": [f[:160] for f in found[:3]]}
    elif ctype == "cooccur":
        negation = check.get("negation", False)
        hits = []
        for s, e in segments(text, check.get("scope", "sentence")):
            seg = text[s:e]
            if all(find_phrases(seg, [w], negation=negation) for w in check.get("all", [])) and \
                    all(find_phrases(seg, group, negation=negation) for group in check.get("any", [])):
                hits.append(seg.strip()[:160])
        passed, detail = bool(hits), {"segments": hits[:3]}
    elif ctype == "claim_verdicts":
        detected, mentioned = claim_verdicts(text, list(check["claims"]), words)
        per = {}
        for claim, expected in check["claims"].items():
            seen = [v for v in VERDICTS if v in detected[claim]]
            per[claim] = {"expected": expected, "detected": seen, "mentioned": mentioned[claim],
                          "passed": seen == [expected]}
            if len(seen) > 1:
                review = True
        passed, detail = all(p["passed"] for p in per.values()), {"claims": per}
        review = review or not passed
    elif ctype in ("ids_all", "ids_any"):
        values = list(check["values"])
        if check.get("allow_new_runs"):
            values += [r["id"] for r in ev.state.get("new_runs", [])]
        cited = [v for v in values if id_present(text, v)]
        if ctype == "ids_all":
            missing = [v for v in check["values"] if v not in cited]
            passed, detail = not missing, {"cited": cited, "missing": missing}
        else:
            passed, detail = len(cited) >= int(check.get("min", 1)), {"cited": cited}
    elif ctype == "stated_as_current":
        fired, mentioned = stated_as_current(check, text, words)
        # A critical check passes when it does not fire; every mention is for a person to read.
        passed, detail = not fired, {"fired": fired[:5], "mentioned": mentioned[:5]}
        review = bool(mentioned)
    elif ctype == "new_records":
        records = [r for r in ev.state.get("new_records", [])
                   if not check.get("kinds") or r.get("kind") in check["kinds"]]
        if check.get("text_any") or check.get("percent"):
            records = [r for r in records if (check.get("text_any") and contains_any(
                str(r.get("text", "")), check["text_any"])) or (check.get("percent") and asserted_values(
                    str(r.get("text", "")), check["percent"], percent=True))]
        passed = len(records) >= int(check.get("min", 1))
        detail = {"records": [r.get("id") for r in records]}
    elif ctype == "new_runs":
        runs = [r for r in ev.state.get("new_runs", [])
                if not check.get("status") or r.get("status") == check["status"]]
        passed, detail = len(runs) >= int(check.get("min", 1)), {"runs": [r["id"] for r in runs]}
    elif ctype == "new_charts":
        ok_runs = [r for r in ev.state.get("new_runs", []) if r.get("status") == "ok"]
        if check.get("last_run"):
            ok_runs = ok_runs[-1:]
        charts = [c for r in ok_runs for c in r.get("charts", [])]
        passed, detail = len(charts) >= int(check.get("min", 1)), {"charts": charts[:20]}
    elif ctype == "chart_covers":
        ok_runs = [r for r in ev.state.get("new_runs", []) if r.get("status") == "ok"]
        if check.get("last_run", True):
            ok_runs = ok_runs[-1:]
        covering, seen = [], {}
        for run in ok_runs:
            for chart in run.get("chart_files") or []:
                body = chart_text(ev.project, chart).casefold()
                has = [v for v in check["values"] if v.casefold() in body]
                seen[f"{run['id']}::{chart.get('id')}"] = has
                if len(has) == len(check["values"]):
                    covering.append(f"{run['id']}::{chart.get('id')}")
        passed, detail = bool(covering), {"covering": covering, "charts": seen}
    elif ctype == "derived_table":
        time_re, score_re = re.compile(check["time_columns"]), re.compile(check["score_columns"])
        tables = []
        for run in ev.state.get("new_runs", []):
            if run.get("status") != "ok":
                continue
            for output in run.get("outputs") or []:
                rows, columns = table_shape(ev.project, output)
                scores = [c for c in columns if score_re.search(c)]
                times = [c for c in columns if time_re.search(c)]
                cells = (rows or 0) * len(scores)
                entry = {"run": run["id"], "path": output.get("path"), "rows": rows, "time": times,
                         "scores": scores, "cells": cells}
                if times and scores and cells >= int(check["min_cells"]):
                    tables.append(entry)
        passed, detail = bool(tables), {"tables": tables[:5]}
    elif ctype == "context_changed":
        kinds = check.get("or_new_records") or []
        records = [r["id"] for r in ev.state.get("new_records", []) if r.get("kind") in kinds]
        passed = bool(ev.state.get("context_changed")) or bool(records)
        detail = {"context_changed": bool(ev.state.get("context_changed")), "records": records}
    elif ctype == "tool_called":
        name = ev.tools.get(check["role"])
        count = sum(1 for c in ev.calls if c.get("name") == name)
        passed = count >= int(check.get("min", 1)) and (
            "max" not in check or count <= int(check["max"]))
        detail = {"tool": name, "calls": count}
    result.update(passed=passed, detail=detail)
    if review:
        result["needs_review"] = True
    return result


def fabricated_ids(text: str, existing: set) -> list[str]:
    found = set(RECORD_ID.findall(text)) | set(RUN_ID.findall(text))
    return sorted(i for i in found if i not in existing)


# The checks on what the answer says, which aelix alone can pass as well as the extension: the result of
# a comparison of the two (docs/design/PRODUCT-EVALUATION.md). `state` and `behavior` checks read what
# the kit recorded or which of its tools was called, which only the extension can do.
ANSWER_KINDS = ("fact", "evidence", "uncertainty")


def score(answer: dict, ev: Evidence, words: dict, extra_checks=()) -> dict:
    checks = [run_check(c, ev, words) for c in [*answer["checks"], *extra_checks]]
    required = [c for c in checks if c["required"] and c["kind"] != "critical"]
    critical = [c["id"] for c in checks if c["kind"] == "critical" and not c["passed"]]
    facts = [c for c in checks if c["kind"] == "fact"]
    evidence = [c for c in checks if c["kind"] == "evidence"]
    limits = [c for c in checks if c["kind"] == "uncertainty"]
    success = all(c["passed"] for c in required) and not critical
    answer_success = all(c["passed"] for c in required if c["kind"] in ANSWER_KINDS) and not critical
    fabricated = fabricated_ids(ev.text, ev.existing_ids)
    review = [c["id"] for c in checks if c.get("needs_review")]
    if fabricated:
        review.append("fabricated_ids")
    if answer.get("always_review"):
        review.append("always_review")
    out = {
        "answer_status": answer["status"],
        "answer_sha256": sha256_bytes(json.dumps(answer, ensure_ascii=False, sort_keys=True).encode()),
        "result": "success" if success else "failure",
        "answer_result": "success" if answer_success else "failure",
        "checks": checks,
        "fact_recall": {"passed": sum(c["passed"] for c in facts), "total": len(facts)},
        "limits": {"passed": sum(c["passed"] for c in limits), "total": len(limits)},
        "evidence": {"passed": sum(c["passed"] for c in evidence), "total": len(evidence),
                     "cited": [i for i in answer.get("evidence_ids", []) if id_present(ev.text, i)]},
        "fabricated_ids": fabricated,
        "critical": critical,
        "needs_review": review,
        "heuristic": "text checks read words, values and ids by sentence with negation; a critical "
                     "check that fires or was let pass, a mixed verdict, an id that does not exist "
                     "and a task marked always_review are listed in needs_review for a person (#24)",
    }
    if answer.get("always_review"):
        out["always_review"] = answer["always_review"]
    return out


def outside_paths(args, roots: list[str], cwd: str) -> list[str]:
    """Paths in a tool call's arguments that lead out of the work root (absolute ones elsewhere, ~,
    or ../ past it). System programs (/usr, /bin, …) are not counted."""
    def strings(value, key=None):
        if isinstance(value, dict):
            for k, v in value.items():
                yield from strings(v, k)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v, key)
        elif isinstance(value, str) and key not in PROSE_KEYS:
            yield value

    norm_roots = {os.path.normcase(os.path.normpath(r)) for r in roots}
    out = set()
    for value in strings(args):
        for token in PATH_SPLIT.split(value):
            token = token.strip()
            if len(token) < 2 or token.startswith("//"):
                continue
            if token.startswith("~"):
                out.add(token)
                continue
            if token.startswith("/") or re.match(r"[A-Za-z]:[\\/]", token):
                if token.startswith("/") and not (token[1].isalnum() or token[1] in "._~-"):
                    continue
                full = os.path.normpath(token)
            elif ".." in re.split(r"[\\/]", token):
                full = os.path.normpath(os.path.join(cwd, token))
            else:
                continue
            if full.replace("\\", "/").startswith(SYSTEM_PATHS) or full in ("/dev/null",):
                continue
            folded = os.path.normcase(full)
            if not any(folded == r or folded.startswith(r.rstrip("\\/") + os.sep) for r in norm_roots):
                out.add(token)
    return sorted(out)


# --- the event stream -----------------------------------------------------------------------------

@dataclass
class Transcript:
    session: str | None = None
    turns: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    retries: int = 0
    compactions: int = 0
    ended: bool = False
    unparsed: int = 0

    @property
    def answers(self) -> list[str]:
        return [t["answer"] for t in self.turns]


def texts_of(content) -> str:
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict)
                     and b.get("type") == "text")


def parse_events(lines) -> Transcript:
    """aelix 0.1.0b2 --mode json: a header line, then one event per line (aelix_agent_core.types)."""
    tr = Transcript()
    by_id: dict[str, dict] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            tr.unparsed += 1
            continue
        kind = event.get("type")
        if kind is None:
            tr.session = event.get("id", tr.session)
        elif kind == "message_end":
            message = event.get("message") or {}
            role = message.get("role")
            if role == "user":
                tr.turns.append({"request": texts_of(message.get("content")), "answer": "",
                                 "messages": 0, "stop_reason": None, "error": None})
            elif role == "assistant":
                if not tr.turns:
                    tr.turns.append({"request": None, "answer": "", "messages": 0, "stop_reason": None,
                                     "error": None})
                turn = tr.turns[-1]
                turn["messages"] += 1
                stop = message.get("stop_reason")
                tr.messages.append({"turn": len(tr.turns), "usage": message.get("usage"),
                                    "model": message.get("model"), "api": message.get("api"),
                                    "provider": message.get("provider"), "stop_reason": stop,
                                    "timestamp": message.get("timestamp")})
                if stop != "toolUse":
                    turn["answer"] = texts_of(message.get("content"))
                    turn["stop_reason"] = stop
                    turn["error"] = message.get("error_message")
        elif kind == "tool_execution_start":
            call = {"id": event.get("tool_call_id"), "name": event.get("tool_name"),
                    "args": event.get("args"), "turn": len(tr.turns), "result": None,
                    "is_error": None}
            tr.calls.append(call)
            by_id[str(call["id"])] = call
        elif kind == "tool_execution_end":
            call = by_id.get(str(event.get("tool_call_id")))
            if call is None:
                call = {"id": event.get("tool_call_id"), "name": event.get("tool_name"), "args": None,
                        "turn": len(tr.turns)}
                tr.calls.append(call)
            result = event.get("result") or {}
            call["result"] = texts_of(result.get("content"))
            call["is_error"] = bool(event.get("is_error") or result.get("is_error"))
        elif kind == "auto_retry_start":
            tr.retries += 1
        elif kind == "compaction_start":
            tr.compactions += 1
        elif kind == "agent_end":
            tr.ended = True
    return tr


def classify(call: dict) -> str | None:
    """How a tool call failed, from its result's text (see the design's table)."""
    text = call.get("result") or ""
    if text.startswith("Unknown tool:"):
        return "unknown_tool"
    if text.startswith("Validation failed for tool"):
        return "schema"
    match = ERROR_REPLY.match(text)
    if match:
        code = match.group(2)
        if code in ARGUMENT_CODES:
            return "argument"
        return "query_error" if code == "query_failed" else "tool_error"
    match = RUN_REPLY.match(text)
    if match and match.group(1) != "ok":
        return "run_failed"
    if call.get("is_error"):
        return "tool_error"
    return None


def tool_summary(calls: list, tools: dict) -> dict:
    roles = {name: role for role, name in tools.items()}
    by_tool: dict[str, int] = {}
    classes: dict[str, int] = {}
    for call in calls:
        by_tool[call.get("name") or "?"] = by_tool.get(call.get("name") or "?", 0) + 1
        cls = classify(call)
        call["error_class"] = cls
        call["role"] = roles.get(call.get("name"))
        if cls:
            classes[cls] = classes.get(cls, 0) + 1
    return {"calls": len(calls), "by_tool": by_tool, "errors": classes,
            "argument_errors": sum(classes.get(c, 0) for c in ARGUMENT_CLASSES),
            "argument_error_classes": list(ARGUMENT_CLASSES),
            "bash_python": sum(1 for c in calls if c.get("name") == "bash" and re.search(
                r"(^|[\s;&|(])(python3?|uv\s+run|pip3?)\b", str((c.get("args") or {}).get("command", ""))))}


TOKEN_NAMES = ("input_uncached", "input_cached", "cache_write", "output", "reasoning")
FULL_INPUT_NOT_MEASURED = ("aelix 0.1.0b2 emits no request body (system prompt, tool schemas, "
                           "messages) as an event, so the full input is known only as the API's "
                           "input tokens; a dry run with the same aelix, surface and checkout records "
                           "the request's tool-schema and system-prompt bytes (request_body)")


def usage_reported(message: dict) -> bool:
    """Whether a request reported usage. aelix fills zeros into a request that failed (stop reason
    error or aborted): those zeros are not a report."""
    usage = message.get("usage")
    if not isinstance(usage, dict) or not usage:
        return False
    if message.get("stop_reason") in ("error", "aborted") and not any(
            usage.get(k) for k in ("input", "output", "cache_read", "cache_write", "reasoning",
                                   "total_tokens")):
        return False
    return True


def usage_convention(messages: list, api: str) -> dict:
    """The token fields of these requests by the adapter's convention. A field a request's usage does
    not carry stays not_measured: it is never a zero."""
    if not messages:
        return {name: not_measured("no request reported usage") for name in TOKEN_NAMES}

    def total(key: str) -> int | None:
        values = [m["usage"].get(key) for m in messages]
        return None if any(v is None for v in values) else sum(int(v) for v in values)

    def value(amount: int | None, key: str) -> dict:
        return measured(amount) if amount is not None else not_measured(
            f"a request's usage has no {key} (aelix 0.1.0b2 records what the API reported)")

    missing = TOKEN_NOT_REPORTED.get(api, {})
    if api == "openai-completions":
        inp, cached = total("input"), total("cache_read")
        out = {"input_uncached": value(None if inp is None or cached is None else inp - cached,
                                       "input or cache_read"),
               "input_cached": value(cached, "cache_read"),
               "reasoning": not_measured(missing["reasoning"])}
    elif api in ("openai-responses", "openai-codex-responses"):
        out = {"input_uncached": value(total("input"), "input"),
               "input_cached": value(total("cache_read"), "cache_read"),
               "reasoning": value(total("reasoning"), "reasoning")}
    else:
        return {name: not_measured(f"no usage convention known for api {api}") for name in TOKEN_NAMES}
    out["output"] = value(total("output"), "output")
    out["cache_write"] = not_measured(missing["cache_write"])
    for name, note in TOKEN_NOTES.get(api, {}).items():
        if out[name]["status"] == "measured":
            out[name]["note"] = note
    return out


def token_totals(messages: list, api: str, *, scripted: bool = False,
                 proposal_turn: int | None = None) -> dict:
    """The run's tokens from aelix's per-message usage, by the adapter's convention, in total and per
    turn (the request a person sent and the model requests that answered it)."""
    with_usage = [m for m in messages if usage_reported(m)]
    complete = bool(messages) and len(with_usage) == len(messages)
    out: dict = {"requests": len(messages), "requests_with_usage": len(with_usage),
                 "complete": complete, "api": api,
                 "per_request": [{"turn": m.get("turn"), "stop_reason": m.get("stop_reason"),
                                  "model": m.get("model"),
                                  "usage": m.get("usage") if usage_reported(m) else None}
                                 for m in messages]}
    if scripted:
        out["source"] = "scripted fake server: fixed numbers, not a measurement"
    else:
        out["source"] = "the model API's usage as aelix 0.1.0b2 records it on each assistant message"
    if not with_usage:
        reason = "no request reported usage" if messages else "no model request was made"
        out.update({name: not_measured(reason) for name in TOKEN_NAMES})
    else:
        out.update(usage_convention(with_usage, api))
        if not complete:
            for name in TOKEN_NAMES:
                if out[name]["status"] == "measured":
                    out[name]["partial"] = True
    per_turn = []
    for turn in sorted({m.get("turn") for m in messages if m.get("turn") is not None}):
        in_turn = [m for m in messages if m.get("turn") == turn]
        reported = [m for m in in_turn if usage_reported(m)]
        entry = {"turn": turn, "requests": len(in_turn), "requests_with_usage": len(reported),
                 "complete": len(reported) == len(in_turn), **usage_convention(reported, api)}
        per_turn.append(entry)
    out["per_turn"] = per_turn
    if proposal_turn is not None:
        entry = next((t for t in per_turn if t["turn"] == proposal_turn), None)
        out["proposal_request"] = {
            "turn": proposal_turn, "what": "the appended reuse-proposal request (Q5 requested) and the "
            "model requests that answered it",
            **(entry or {name: not_measured("the proposal request got no model request")
                         for name in TOKEN_NAMES})}
    out["full_input"] = not_measured(FULL_INPUT_NOT_MEASURED)
    return out


# --- the project before and after ------------------------------------------------------------------

def project_file(project: Path, rel: str, skipped: list | None = None) -> Path | None:
    """A project file the harness may read: a regular file inside the project, its links resolved.
    A link that leaves the project is not followed and is added to skipped."""
    path = project / rel
    if not os.path.lexists(path):
        return None
    if inside(path, project):
        return path
    if skipped is not None and path.is_symlink():
        skipped.append(rel)
    return None


def journal(project: Path, skipped: list | None = None) -> list[dict]:
    path = project_file(project, "journal.jsonl", skipped)
    if path is None:
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            out.append(json.loads(line))
    return out


def run_dirs(project: Path) -> list[str]:
    runs = project / "runs"
    if runs.is_symlink() or not runs.is_dir():
        return []
    return sorted(p.name for p in runs.iterdir() if p.is_dir() and not p.is_symlink())


def snapshot(project: Path) -> dict:
    events = journal(project)
    context = project_file(project, "CONTEXT.md")
    return {"max_seq": max((int(e.get("seq") or 0) for e in events), default=0),
            "records": len(events), "runs": run_dirs(project),
            "context_sha256": sha256_file(context) if context is not None else None}


def changes(project: Path, before: dict) -> dict:
    skipped: list[str] = []
    events = journal(project, skipped)
    new_records = [{"id": e.get("id"), "kind": e.get("kind"), "text": e.get("text"),
                    "refs": e.get("refs")} for e in events if int(e.get("seq") or 0) > before["max_seq"]]
    new_runs = []
    for name in run_dirs(project):
        if name in before["runs"]:
            continue
        record = {}
        path = project_file(project, f"runs/{name}/run.json", skipped)
        with contextlib.suppress(OSError, json.JSONDecodeError, AttributeError):
            record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            record = {}
        charts = read_model().run_visualizations(record)
        new_runs.append({"id": name, "status": record.get("status"),
                         "script": (record.get("script") or {}).get("path"),
                         "charts": [c.get("id") for c in charts],
                         "chart_files": [{k: c.get(k) for k in ("id", "files", "data", "data_columns")}
                                         for c in charts],
                         "outputs": [{k: o.get(k) for k in ("path", "change", "rows", "schema")}
                                     for o in record.get("outputs") or []],
                         "error": (record.get("error") or {}).get("summary")})
    context = project_file(project, "CONTEXT.md", skipped)
    after_sha = sha256_file(context) if context is not None else None
    existing = {str(e.get("id")) for e in events} | set(run_dirs(project))
    return {"new_records": new_records, "new_runs": new_runs,
            "context_changed": after_sha != before["context_sha256"],
            "existing_ids": sorted(existing), "outside_links": sorted(set(skipped))}


# --- fixtures and the work directory ---------------------------------------------------------------

def stripped_name(key: str, keep=()) -> bool:
    return key not in keep and (key.upper().startswith(ISOLATED_PREFIXES) or key in ISOLATED_KEYS
                                or bool(SECRET_ENV.search(key)))


def isolated_env(base: dict | None = None, keep=()) -> dict:
    """The environment without the variables a child must not get (see ISOLATED_PREFIXES and
    SECRET_ENV); `keep` names the ones it needs anyway (a model's own key variable)."""
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if stripped_name(key, keep):
            env.pop(key)
    return env


def stripped_env(keep=()) -> list[str]:
    """The names isolated_env leaves out of the caller's environment (names only)."""
    return sorted(k for k in os.environ if stripped_name(k, keep))


def env_secrets(keep=()) -> list[str]:
    """The values of the caller's credential variables that no child gets: overwritten by their
    bytes in every file of a run, should the model find one elsewhere (a shell's startup file)."""
    return sorted({v for k, v in os.environ.items() if k not in keep and SECRET_VALUE_ENV.search(k)
                   and len(v) >= MIN_SECRET_LENGTH})


def uv_dirs(uv: str | None) -> dict:
    """Where the caller's uv keeps its cache and its Pythons, for a child whose HOME is the run's:
    uv (aelix's own environment, the project's environment the kit makes) keeps using them. The
    kit's extension passes XDG_CACHE_HOME and XDG_DATA_HOME to the uv it starts, not UV_*."""
    out: dict = {}
    if not uv:
        return out
    for var, args in (("UV_CACHE_DIR", ("cache", "dir")), ("UV_PYTHON_INSTALL_DIR", ("python", "dir"))):
        value = os.environ.get(var)
        if not value:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                done = subprocess.run([uv, *args], capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=120, env=isolated_env())
                if done.returncode == 0 and done.stdout.strip():
                    value = done.stdout.strip().splitlines()[-1].strip()
        if value:
            out[var] = value
    if os.name != "nt":  # Windows: uv finds its folders without HOME, and Python's home is USERPROFILE
        cache, pythons = out.get("UV_CACHE_DIR"), out.get("UV_PYTHON_INSTALL_DIR")
        if cache and Path(cache).name == "uv":
            out["XDG_CACHE_HOME"] = str(Path(cache).parent)
        if pythons and Path(pythons).parts[-2:] == ("uv", "python"):
            out["XDG_DATA_HOME"] = str(Path(pythons).parent.parent)
    return out


def fixture_name(spec: dict, seed: int) -> str:
    return spec["kind"] + (f"-{spec['scale']}" if spec.get("scale") else "") + f"-s{seed}"


def v1_kit(root: Path) -> Path:
    """The runtime root of the kit that writes the v1 fixtures — tools/eval_fixtures.py's
    V1_KIT_COMMIT, the last kit before the new record format (design OQ7) — made once under `root`
    from this checkout's git history (git archive of its src/)."""
    found = re.search(r'^V1_KIT_COMMIT = "([0-9a-f]{40})"', FIXTURE_TOOL.read_text(encoding="utf-8"),
                      re.MULTILINE)
    if found is None:
        raise HarnessError(f"{FIXTURE_TOOL} names no V1_KIT_COMMIT")
    commit = found.group(1)
    target = root / f"v1-kit-{commit[:12]}"
    if (target / "src" / "aelix_analytics" / "core.py").is_file():
        return target / "src"
    try:
        done = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", commit, "src"],
                              capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HarnessError(f"git cannot make the v1 fixtures' kit ({commit}): {exc}") from exc
    if done.returncode != 0:
        raise HarnessError(f"git cannot make the v1 fixtures' kit ({commit}): "
                           f"{done.stderr.decode('utf-8', 'replace')[-500:]}")
    partial = root / f".{target.name}.{os.getpid()}"
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        archive.extractall(partial, filter="data")
    os.replace(partial, target)
    return target / "src"


def ensure_fixture(spec: dict, seed: int, root: Path) -> Path:
    """The fixture made by tools/eval_fixtures.py with this seed, made once under `root`."""
    path = root / fixture_name(spec, seed)
    manifest = path / "manifest.json"
    if not manifest.is_file():
        if path.exists() and any(path.iterdir()):
            raise HarnessError(f"{path} exists without a manifest.json: remove it")
        args = (["scale", "--scale", spec["scale"]] if spec["kind"] == "scale" else [spec["kind"]])
        argv = [sys.executable, str(FIXTURE_TOOL), *args, "--seed", str(seed), "--kit-root",
                str(v1_kit(root)), "--out", str(path)]
        done = subprocess.run(argv, cwd=ROOT, env=isolated_env(), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
        if done.returncode != 0:
            raise HarnessError(f"making the {spec['kind']} fixture failed ({done.returncode}): "
                               f"{(done.stderr or done.stdout)[-1500:]}")
    data = read_json(manifest)
    generator = (data.get("generator") or {}).get("version")
    # The answers are written for the v1 fixture (a v2 one has other ids and judgments).
    if generator != FIXTURE_GENERATOR_VERSION or data.get("seed") != seed or \
            data.get("kind") != spec["kind"] or data.get("scale") != spec.get("scale") or \
            data.get("format", "v1") != "v1":
        raise HarnessError(f"{path} is not the {fixture_name(spec, seed)} v1 fixture of generator "
                           f"version {FIXTURE_GENERATOR_VERSION} (manifest: generator {generator}, "
                           f"seed {data.get('seed')}, kind {data.get('kind')}, format {data.get('format')})")
    return path


def init_project(kit_src: Path, project: Path, title: str, goal: str, env: dict) -> None:
    """A new project made by the checkout's own kit (core.init_project), as a person would."""
    code = ("import sys\nfrom pathlib import Path\nfrom aelix_analytics import core\n"
            "core.init_project(Path(sys.argv[1]), title=sys.argv[2], goal=sys.argv[3], origin=core.Origin('human', 'cli'))\n")
    done = subprocess.run([sys.executable, "-c", code, str(project), title, goal], cwd=project.parent,
                          env={**env, "PYTHONPATH": str(kit_src)}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if done.returncode != 0:
        raise HarnessError(f"making the fresh project failed: {(done.stderr or done.stdout)[-1500:]}")


# How a fixture's lab.json project reaches the work root (design OQ7): copied for a checkout whose kit
# writes lab.json projects; for one whose kit writes the new format (kit 0.8.0, Issue #13 PR 3, where a
# lab.json project is read only), copied and then migrated by that kit's own migration engine, as a
# person migrates one — its journal lines, run records and files are the same bytes, so the answers'
# ids hold. The engine takes the copy's locks, not the fixture's; no trust list is written (the run's
# own is set for aelix).
FIXTURE_PROJECT = """\
import shutil, sys
from pathlib import Path
from aelix_analytics import core
source, project = Path(sys.argv[1]), Path(sys.argv[2])
if core.MARKER != "analytics.json":
    shutil.copytree(source, project, symlinks=True)
    print("copied")
else:
    from aelix_analytics import migration
    copy = project.parent / ".fixture-project"
    shutil.copytree(source, copy, symlinks=True)
    try:
        migration.migrate(copy, project, origin=core.Origin("system", "cli"), trust=lambda root: None)
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    print("migrated")
"""


def fixture_project(kit_src: Path, source: Path, project: Path, env: dict) -> str:
    """The fixture's project in the work root as the checkout's kit writes it (FIXTURE_PROJECT):
    `copied` or `migrated`."""
    done = subprocess.run([sys.executable, "-c", FIXTURE_PROJECT, str(source), str(project)],
                          cwd=project.parent, env={**env, "PYTHONPATH": str(kit_src)}, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=900)
    if done.returncode != 0:
        raise HarnessError(f"preparing the fixture's project failed: {(done.stderr or done.stdout)[-1500:]}")
    return done.stdout.strip().splitlines()[-1]


# The always_on condition (Q5): the project's suggestion mode set as a person sets it (`analytics assets
# mode always_on`, kit 0.11.0 and later, #17) before aelix starts. It appends one asset_mode record.
PROPOSAL_MODE = """\
import json, sys
from pathlib import Path
from aelix_analytics import core
result = core.set_asset_mode(core.Project(Path(sys.argv[1])), "always_on", origin=core.Origin("human", "cli"))
print(json.dumps({"mode": result["mode"], "changed": result["changed"], "event": result["event"]["id"]}))
"""


# The context block the extension appends to the system prompt (extension: core.context_block), as the
# project is when aelix starts. Kept with an A run so that the diagnosis of what a run received can
# search it (docs/design/PRODUCT-EVALUATION.md §7).
CONTEXT_BLOCK = """\
import sys
from pathlib import Path
from aelix_analytics import core
sys.stdout.write(core.context_block(core.Project(Path(sys.argv[1]))))
"""


def context_block_of(kit_src: Path, project: Path, env: dict, run_dir: Path) -> dict:
    """The kit's context block for the project, written to the run's context_block.txt; not_measured
    when the checkout's kit cannot give one (another generation of names, say)."""
    done = subprocess.run([sys.executable, "-c", CONTEXT_BLOCK, str(project)], cwd=project,
                          env={**env, "PYTHONPATH": str(kit_src)}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if done.returncode != 0:
        return {"status": "not_measured",
                "reason": "the checkout's kit gave no context block: " + mask_text((done.stderr or done.stdout)[-300:])}
    (run_dir / "context_block.txt").write_text(done.stdout, encoding="utf-8", newline="\n")
    return {"status": "measured", "file": "context_block.txt", "bytes": len(done.stdout.encode("utf-8")),
            "what": "core.context_block of the project when aelix started; the extension appends it to the "
                    "system prompt of every prompt, so later prompts may have had a newer one"}


def kit_has_proposal_mode(kit_src: Path) -> bool:
    core = kit_src / "aelix_analytics" / "core.py"
    return core.is_file() and "def set_asset_mode(" in core.read_text(encoding="utf-8")


def set_proposal_mode(kit_src: Path, project: Path, env: dict) -> dict:
    """The project in always_on, by the checkout's kit as a human (PROPOSAL_MODE)."""
    done = subprocess.run([sys.executable, "-c", PROPOSAL_MODE, str(project)], cwd=project,
                          env={**env, "PYTHONPATH": str(kit_src)}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if done.returncode != 0:
        raise HarnessError(f"setting the always_on suggestion mode failed: {(done.stderr or done.stdout)[-1500:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def plain_project(kit_src: Path, project: Path, title: str) -> None:
    """A folder for aelix alone: the analysis libraries a kit project's environment has (the kit's
    template pyproject.toml, its placeholders filled) and the Python version a kit project pins, and
    nothing of the kit (no marker, CONTEXT.md, journal or directories)."""
    template = (kit_src / "aelix_analytics" / "template" / "pyproject.toml").read_text(encoding="utf-8")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "analysis"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(template.replace("{{slug}}", slug).replace("{{title}}", title),
                                            encoding="utf-8", newline="\n")
    (project / ".python-version").write_text("3.12\n", encoding="utf-8", newline="\n")


def prepare_work(task: dict, fixture: Path, work: Path, kit_src: Path, env: dict, kit: bool = True) -> Path:
    """project/ and input/ only: manifest.json and expected.json carry answers. A fixture's project is
    the same for aelix alone (the files the kit wrote, which it reads without the kit); a fresh one is a
    plain folder there (plain_project)."""
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()):
        raise HarnessError(f"the work root {work} is not empty")
    project = work / "project"
    if task["setup"] == "fixture":
        fixture_project(kit_src, fixture / "project", project, env)
        if (fixture / "input").is_dir():
            shutil.copytree(fixture / "input", work / "input", symlinks=True)
    else:
        fresh = task["fresh_project"]
        (work / "input").mkdir()
        for name in fresh["inputs"]:
            shutil.copy2(fixture / "input" / name, work / "input" / name)
        if kit:
            init_project(kit_src, project, fresh["title"], fresh["goal"], env)
        else:
            plain_project(kit_src, project, fresh["title"])
    return project


# --- the checkout, aelix and serving ---------------------------------------------------------------

def checkout_info(checkout: Path) -> dict:
    generation = next((g for g, spec in GENERATIONS.items()
                       if (checkout / spec["extension"]).is_file()), None)
    if generation is None:
        raise UsageError(f"{checkout} has neither extension/aelix_analytics.py nor extension/aelix_lab.py")
    core = checkout / "src" / "aelix_analytics" / "core.py"
    kit_version = None
    if core.is_file():
        match = re.search(r'^KIT_VERSION = "([^"]+)"', core.read_text(encoding="utf-8"), re.M)
        kit_version = match.group(1) if match else None

    def git(*args: str) -> str | None:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            done = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60)
            if done.returncode == 0:
                return done.stdout.strip()
        return None

    extension = checkout / GENERATIONS[generation]["extension"]
    out = {"path": str(checkout), "generation": generation, "kit_version": kit_version,
           "commit": None, "dirty": None, "extension": GENERATIONS[generation]["extension"],
           "extension_sha256": sha256_file(extension)}
    # Only a checkout that is its own repository's top level has a commit: a copy inside another
    # repository (an extracted snapshot) would otherwise get that repository's HEAD.
    top = git("rev-parse", "--show-toplevel")
    if top is None:
        out["commit_reason"] = "not a git checkout (git rev-parse failed or git is missing)"
    elif os.path.normcase(str(Path(top).resolve())) != os.path.normcase(str(checkout.resolve())):
        out["commit_reason"] = (f"not the top level of a git repository: git's top level for it is "
                                f"{top}")
    else:
        status = git("status", "--porcelain", "--untracked-files=no")
        out.update(commit=git("rev-parse", "HEAD"), dirty=None if status is None else bool(status))
    return out


def aelix_version(command: list[str]) -> str:
    try:
        done = subprocess.run([*command, "--version"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=600, env=isolated_env())
    except (OSError, subprocess.SubprocessError) as exc:
        raise HarnessError(f"aelix could not be started ({shlex.join(command)}): {exc}") from exc
    if done.returncode != 0:
        raise HarnessError(f"aelix --version failed ({done.returncode}): {done.stderr[-800:]}")
    return done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "?"


# --- the caller's aelix login ---------------------------------------------------------------------

def default_aelix_auth() -> Path:
    """Where the caller's aelix keeps its login: aelix 0.1.0b2 opens <agent directory>/auth.json, the
    agent directory being $AELIX_CODING_AGENT_DIR or ~/.aelix/agent (cli/config.py get_agent_dir,
    cli/entry.py)."""
    agent = os.environ.get("AELIX_CODING_AGENT_DIR")
    base = Path(os.path.expanduser(agent)) if agent else Path.home() / ".aelix" / "agent"
    return Path(os.path.abspath(base / "auth.json"))


def file_mtime(path: Path | None) -> str | None:
    """A file's modification time (UTC) from its metadata; the file is not opened."""
    if path is None:
        return None
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(
            timespec="microseconds").replace("+00:00", "Z")
    except OSError:
        return None


def login_stat(path: Path | None) -> dict | None:
    """The login file's metadata (its contents are never read): modification time, inode and its
    number of names. A second name (a hard link) would let a later write or copy reach it."""
    if path is None:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return {"exists": False}
    return {"exists": True, "mtime": file_mtime(path), "mtime_ns": st.st_mtime_ns, "inode": st.st_ino,
            "device": st.st_dev, "links": st.st_nlink}


def login_ids(*stats: dict | None) -> set:
    return {(s["device"], s["inode"]) for s in stats if s and s.get("exists")}


def launch_command(model: dict, auth_file: Path | None, aelix_command: list[str],
                   link: LauncherLink | None = None, redact: tuple = ()) -> list[str]:
    """How aelix is started for this model: through the launcher (its Python and the login's file)
    when the model uses an aelix login, else the aelix command itself. link: the pipes to the
    launcher (its lifeline, which stops aelix when it closes, and its reports); redact: the run's
    directory and the work root, which the launcher masks by the login's exact values."""
    if model.get("auth") is None:
        return list(aelix_command)
    options = []
    if link is not None:
        lifeline, report = link.child_fds()
        options += ["--lifeline", str(lifeline), "--report", str(report)]
    for flag, path in zip(("--redact-run", "--redact-work"), redact, strict=False):
        options += [flag, str(path)]
    return [*AELIX_PYTHON, str(LAUNCHER), *options, str(auth_file)]


def home_vars(home: Path, dirs: dict) -> dict:
    """HOME (and the configuration directory) inside the run, uv's own directories kept (uv_dirs)."""
    return {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), **dirs}


def login_check(model: dict, auth_file: Path, out: Path, dirs: dict | None = None) -> dict:
    """Whether the caller's aelix login in auth_file has the model's provider, asked of aelix itself.

    The harness does not open the file. It checks that the file exists (aelix would otherwise create
    an empty one when it locks it) and runs aelix --list-models through the launcher with an empty
    agent directory under OUT: aelix lists a model only when its provider has credentials
    (ModelRegistry.get_available, AuthStorage.has_auth: an entry's presence; nothing is refreshed,
    written or printed but the model table)."""
    provider = model["auth"]["provider"]
    result: dict = {"provider": provider, "file": str(auth_file), "file_mtime": file_mtime(auth_file),
                    "checked_by": "aelix --list-models through tools/model_eval_aelix.py "
                                  "(the harness does not open the file)"}
    if not auth_file.is_file():
        result.update(ok=False, problem=f"{auth_file} does not exist: {LOGIN_HOWTO}")
        return result
    agent = out / "iso" / "login-check-agent"
    (agent / "home").mkdir(parents=True, exist_ok=True)
    write_models_json(model, agent, None)  # a provider aelix does not know by itself
    env = {**isolated_env(), "AELIX_CODING_AGENT_DIR": str(agent),
           "AELIX_CODING_AGENT_SESSION_DIR": str(agent / "sessions"), **home_vars(agent / "home", dirs or {})}
    argv = [*launch_command(model, auth_file, []), "--offline", "--list-models",
            f"{provider} {model['model']}"]
    ports = processes()
    try:
        # cwd: the empty agent directory, so that no .env of the caller's directory is loaded. Its
        # own group: a timeout ends the launcher's Python too, not only uv.
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                env=env, cwd=agent, **ports.spawn_kwargs())
    except OSError as exc:
        raise HarnessError(f"aelix could not be started to check the login ({shlex.join(argv)}): "
                           f"{exc}") from exc
    try:
        stdout, stderr = proc.communicate(timeout=600)
    except BaseException as exc:
        ports.kill_tree(proc.pid)
        with contextlib.suppress(Exception):
            proc.communicate(timeout=60)
        if isinstance(exc, subprocess.TimeoutExpired):
            raise HarnessError("aelix --list-models did not finish in 600 s (stopped)") from exc
        raise
    finally:
        # What the check left in its agent directory (aelix writes no login there), searched.
        scan = scan_tree(agent, login=login_ids(login_stat(auth_file)))
        result["secret_scan"] = {k: scan[k] for k in ("status", "problems", "files", "findings_count")}
    rows = [line.split() for line in stdout.splitlines()]
    row = next((r for r in rows if r[:2] == [provider, model["model"]]), None)
    result.update(exit_code=proc.returncode, listed=row is not None,
                  row=" ".join(row) if row else None)
    if proc.returncode != 0:
        # aelix's own error text, credential shapes masked: it goes to the record and the console.
        result.update(ok=False, problem=f"aelix --list-models exited {proc.returncode}: "
                                        f"{mask_text(stderr.strip()[-400:])}")
    elif row is None:
        result.update(ok=False, problem=f"aelix lists no {provider} {model['model']} with the login in "
                                        f"{auth_file}: it has no {provider} entry; {LOGIN_HOWTO}")
    else:
        result["ok"] = True
    return result


def http_json(url: str, body: dict | None = None, timeout: float = 30):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else {}


def serving_check(model: dict, *, load: bool = True) -> dict:
    """Whether the backend serves what the configuration pins. Only ollama has something to ask."""
    serving = model["serving"]
    if serving.get("backend") != "ollama":
        return {"backend": serving.get("backend"), "ok": True,
                "checked": "nothing: this backend exposes no version, digest or context to ask; the "
                           "model that answered is recorded from each reply"}
    base = serving["endpoint"].rstrip("/")
    out: dict = {"backend": "ollama", "endpoint": base, "problems": []}
    try:
        out["version"] = http_json(f"{base}/api/version").get("version")
        tags = http_json(f"{base}/api/tags").get("models") or []
        tag = next((m for m in tags if m.get("name") == serving["model_tag"]), None)
        out["digest"] = tag.get("digest") if tag else None
        if load and tag:
            http_json(f"{base}/api/generate", {"model": serving["model_tag"], "prompt": "",
                                               "keep_alive": serving["server_env"].get("OLLAMA_KEEP_ALIVE", "5m")},
                      timeout=900)
            loaded = http_json(f"{base}/api/ps").get("models") or []
            entry = next((m for m in loaded if m.get("name") == serving["model_tag"]), None)
            out["context_length"] = entry.get("context_length") if entry else None
            out["size_vram"] = entry.get("size_vram") if entry else None
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        out["problems"].append(f"{base} did not answer: {exc}")
        out["ok"] = False
        return out
    if out.get("version") != serving["backend_version"]:
        out["problems"].append(f"ollama {out.get('version')} is not the pinned {serving['backend_version']}")
    if out.get("digest") != serving["manifest_digest"]:
        out["problems"].append(f"{serving['model_tag']} is {out.get('digest')}, not the pinned "
                               f"{serving['manifest_digest']}")
    if load and out.get("context_length") != serving["context_window"]:
        out["problems"].append(f"the loaded context is {out.get('context_length')}, not "
                               f"{serving['context_window']}: start the server with the pinned "
                               "server_env (OLLAMA_CONTEXT_LENGTH)")
    out["ok"] = not out["problems"]
    return out


# --- the scripted fake model -----------------------------------------------------------------------

GENERIC_SCRIPT = {"schema": SCRIPT_SCHEMA, "task": "*",
                  "what": "no script for this task: look at the project once and answer",
                  "steps": [{"tool_calls": [{"tool": "@status", "arguments": {}}]},
                            {"text": "dry-run: the scripted model looked at the project and stops here."}]}


def load_script(task_id: str, path: Path | None) -> dict:
    source = path or DATA / "dry_run" / f"{task_id}.json"
    script = read_json(source) if source.is_file() else GENERIC_SCRIPT
    if script.get("schema") != SCRIPT_SCHEMA or not isinstance(script.get("steps"), list):
        raise UsageError(f"{source} is not a {SCRIPT_SCHEMA} script")
    return script


def content_text(content) -> str:
    """A chat message's content as text (a string, or a list of parts with text)."""
    if isinstance(content, str):
        return content
    return "".join(str(p.get("text", "")) for p in content or [] if isinstance(p, dict))


def scripted_usage(k: int) -> dict:
    prompt = 1000 + 500 * k
    cached = 0 if k == 0 else 1000 + 500 * (k - 1)
    return {"prompt_tokens": prompt, "completion_tokens": 40, "total_tokens": prompt + 40,
            "prompt_tokens_details": {"cached_tokens": cached}}


class ScriptedModel:
    """An OpenAI-compatible chat completions server that answers request k with the script's step k
    (k = the assistant messages already in the request), streamed as aelix's adapter reads it."""

    def __init__(self, script: dict, tools: dict):
        self.steps = [self._resolve(step, tools) for step in script["steps"]]
        self.requests: list[dict] = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D401 - silence the default stderr log
                return

            def do_GET(self):  # noqa: N802
                self._send(404, b'{"error": "not found"}', "application/json")

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    body = {}
                if not self.path.endswith("/chat/completions"):
                    self._send(404, b'{"error": "not found"}', "application/json")
                    return
                k = sum(1 for m in body.get("messages") or [] if m.get("role") == "assistant")
                messages = body.get("messages") or []
                # The credential aelix sent, by its shape and digest only (a dry run's are placeholders
                # or a test's dummy; the digest lets a test see which one aelix read).
                scheme, _, credential = (self.headers.get("Authorization") or "").partition(" ")
                owner.requests.append({
                    "authorization": {"scheme": scheme or None, "length": len(credential),
                                      "sha256": sha256_bytes(credential.encode("utf-8")) if credential
                                      else None},
                    "k": k, "messages": len(messages),
                    "tools": [t.get("function", {}).get("name") for t in body.get("tools") or []],
                    "tool_schema_bytes": len(json.dumps(body.get("tools") or [],
                                                        ensure_ascii=False).encode("utf-8")),
                    "system_prompt_bytes": sum(len(content_text(m.get("content")).encode("utf-8"))
                                               for m in messages
                                               if m.get("role") in ("system", "developer")),
                    "body_bytes": length, "stream": body.get("stream")})
                step = owner.steps[k] if k < len(owner.steps) else {
                    "text": "dry-run: the script has no more steps."}
                if step.get("http_status"):  # the API refusing the request (a revoked login: 401)
                    self._send(int(step["http_status"]), json.dumps({"error": {
                        "message": step.get("text", "refused"), "type": "invalid_request_error",
                        "code": step.get("code")}}).encode("utf-8"), "application/json")
                    return
                payload = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n"
                                  for c in owner.chunks(step, k)) + "data: [DONE]\n\n"
                self._send(200, payload.encode("utf-8"), "text/event-stream")

            def _send(self, code: int, data: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @staticmethod
    def _resolve(step: dict, tools: dict) -> dict:
        step = dict(step)
        if "tool_calls" in step:
            calls = []
            for call in step["tool_calls"]:
                name = call["tool"]
                if name.startswith("@"):
                    name = tools[name[1:]]
                calls.append({"tool": name, "arguments": call.get("arguments", {})})
            step["tool_calls"] = calls
        return step

    @staticmethod
    def chunks(step: dict, k: int) -> list[dict]:
        base = {"id": f"dry-run-{k}", "object": "chat.completion.chunk", "created": 0, "model": "scripted"}
        out = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}]
        if step.get("tool_calls"):
            calls = [{"index": i, "id": f"call_{k}_{i}", "type": "function",
                      "function": {"name": c["tool"],
                                   "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                     for i, c in enumerate(step["tool_calls"])]
            out.append({**base, "choices": [{"index": 0, "delta": {"tool_calls": calls},
                                             "finish_reason": None}]})
            finish = "tool_calls"
        else:
            out.append({**base, "choices": [{"index": 0, "delta": {"content": step.get("text", "")},
                                             "finish_reason": None}]})
            finish = "stop"
        out.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        out.append({**base, "choices": [], "usage": step.get("usage") or scripted_usage(k)})
        return out

    def __enter__(self) -> ScriptedModel:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)


# --- one run --------------------------------------------------------------------------------------

@dataclass
class Session:
    """What every run of one invocation shares."""
    config: Config
    model: dict
    out: Path
    checkout: dict
    surface: str
    proposal: str
    seed: int
    aelix_command: list[str]
    aelix_version: str | None
    fixtures_root: Path
    timeout: int | None
    script: Path | None
    serving: dict | None
    uv: str | None
    secrets: list[str]
    work_parent: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
    auth_file: Path | None = None  # the caller's aelix login, for a model with auth (never opened here)
    login: dict | None = None  # login_check's result
    dirs: dict = field(default_factory=dict)  # uv's own directories (uv_dirs), for a child with the run's HOME


def launch_of(session: Session) -> list[str]:
    return launch_command(session.model, session.auth_file, session.aelix_command)


def tools_of(session: Session) -> dict:
    return GENERATIONS[session.checkout["generation"]]["tools"]


def has_kit(session: Session) -> bool:
    """Whether the run's surface gives the model the extension and the kit (A), or aelix alone (V:
    the `vanilla` surface of docs/design/PRODUCT-EVALUATION.md)."""
    return session.config.conditions["surfaces"][session.surface].get("kit", True)


def surface_argv(session: Session) -> tuple[list[str], str | None]:
    surface = session.config.conditions["surfaces"][session.surface]
    tools = tools_of(session)
    extension = surface.get("extension")
    if extension is None:
        return [], None
    if session.checkout["generation"] == "lab" and extension == GENERATIONS["analytics"]["extension"]:
        extension = GENERATIONS["lab"]["extension"]
    argv = []
    if surface.get("tools") is not None:
        names = []
        for item in surface["tools"]:
            names += [tools[r] for r in ROLES] if item == "@extension" else [item]
        argv = ["--tools", ",".join(names)]
    return argv, extension


def build_argv(session: Session, requests: list[str], extension: Path | None, tools_argv: list[str],
               link: LauncherLink | None = None, redact: tuple = ()) -> list[str]:
    model = session.model
    launch = launch_command(model, session.auth_file, session.aelix_command, link, redact)
    return [*launch, "--mode", "json", "-p", *requests,
            "--provider", model["provider"], "--model", model["model"], "--thinking", model["thinking"],
            "--no-session", "--offline", "--no-context-files", *tools_argv,
            *(["-e", str(extension)] if extension is not None else [])]


def key_names(session: Session) -> tuple:
    """The model's own key variable, which its child needs (it is never written: see secrets)."""
    return (session.model["api_key_env"],) if session.model.get("api_key_env") else ()


def run_env(session: Session, run_dir: Path, project: Path, kit: bool = True) -> tuple[dict, dict]:
    """The child's environment and the variables this harness set in it (paths only). HOME is the
    run's home/: the model's tools resolve ~ there, not to the caller's ~/.aelix (a path written out
    in full still reaches the caller's files: see login_mentions). Without the kit (aelix alone) the
    kit's variables are not set."""
    names = GENERATIONS[session.checkout["generation"]]["env"]
    iso = session.out / "iso"
    set_vars = {
        **({names["trust"]: str(iso / "trust" / "analytics-projects.json"), names["project"]: str(project)}
           if kit else {}),
        "AELIX_CODING_AGENT_DIR": str(run_dir / "agent"),
        "AELIX_CODING_AGENT_SESSION_DIR": str(run_dir / "agent" / "sessions"),
        **home_vars(run_dir / "home", session.dirs),
    }
    if session.uv and kit:
        set_vars[names["uv"]] = session.uv
    return {**isolated_env(keep=key_names(session)), **set_vars}, set_vars


def link_uv(out: Path) -> str | None:
    real = processes().find_program("uv")
    if not real:
        return None
    link = out / "iso" / "bin" / Path(real).name
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        return str(link)
    try:
        link.symlink_to(real)
        return str(link)
    except OSError:  # e.g. Windows without the symlink privilege: the real one, recorded as such
        return real


def task_requests(session: Session, task: dict, work: Path) -> tuple[list[str], list[dict]]:
    requests = [r.replace("{input}", str(work / "input")) for r in task["requests"]]
    extra = []
    if session.proposal == "requested":
        cond = session.config.conditions["proposals"]["requested"]
        requests.append(cond["append_request"])
        extra.append({**cond["check"]})
    return requests, extra


def not_run_reason(session: Session, task: dict) -> str | None:
    env = session.model.get("api_key_env")
    if env and not os.environ.get(env):
        return f"api_key_missing: {env} is not set"
    if session.model.get("auth") is not None and not (session.login or {}).get("ok"):
        return "aelix_login_missing: " + ((session.login or {}).get("problem") or "the login was not checked")
    if session.serving is not None and not session.serving.get("ok"):
        return "serving_differs: " + "; ".join(session.serving.get("problems") or ["check failed"])
    if session.proposal != "unrequested" and session.proposal not in (task.get("proposal_conditions") or []):
        return f"proposal condition {session.proposal} is not defined for {task['id']}"
    if session.proposal == "always_on" and not has_kit(session):
        return "always_on sets the kit's suggestion mode: aelix alone has no kit"
    if session.proposal == "always_on" and not kit_has_proposal_mode(Path(session.checkout["path"]) / "src"):
        return session.config.conditions["proposals"]["always_on"]["not_run_reason"]
    return None


def model_record(session: Session) -> dict:
    model = session.model
    serving = model["serving"]
    entry = next((m for m in (model.get("aelix_provider") or {}).get("models") or []
                  if m.get("id") == model["model"]), {})
    return {"config": model["id"], "role": model.get("role"), "provider": model["provider"],
            "model": model["model"], "api": model["api"], "thinking": model["thinking"],
            "backend": serving.get("backend"), "backend_version": serving.get("backend_version"),
            "quantization": serving.get("quantization"),
            "digest": serving.get("manifest_digest") or serving.get("snapshot"),
            "tokenizer": serving.get("tokenizer"),
            "context_window": entry.get("contextWindow", serving.get("context_window")),
            "output_reservation": entry.get("maxTokens", serving.get("output_reservation")),
            "sampling": serving.get("sampling"), "api_key_env": model.get("api_key_env"),
            "auth": None if model.get("auth") is None else {
                **model["auth"], "file": str(session.auth_file),
                "login_check": session.login,
                "read_by": "aelix only, in place, through tools/model_eval_aelix.py: the harness "
                           "never opens, copies or links the file"},
            "serving_check": session.serving,
            # The whole configuration (no secret: keys are read from the environment only, a login
            # from aelix's own store).
            "configuration": model,
            "configuration_sha256": sha256_bytes(json.dumps(model, ensure_ascii=False,
                                                            sort_keys=True).encode("utf-8")),
            "configuration_file": f"tools/model_eval_data/models/{model['id']}.json",
            "configuration_file_sha256": session.config.model_files.get(model["id"])}


def write_models_json(model: dict, agent_dir: Path, port: int | None) -> None:
    provider = model.get("aelix_provider")
    if provider is None:
        return
    provider = json.loads(json.dumps(provider).replace("{port}", str(port or 0)))
    write_json(agent_dir / "models.json", {"providers": {model["provider"]: provider}})


def run_name(task_id: str, model_id: str, surface: str, proposal: str, repeat: int) -> str:
    return f"{task_id}-{model_id}-{surface}-{proposal}-r{repeat}"


def run_key(session: Session, task_id: str, repeat: int) -> str:
    return run_name(task_id, session.model["id"], session.surface, session.proposal, repeat)


def base_record(session: Session, task: dict, repeat: int, key: str) -> dict:
    answer = session.config.answers[task["id"]]
    return {
        "schema": RUN_SCHEMA, "run": key, "task": task["id"], "title": task.get("title"),
        "gates": task.get("gates"), "q11_request": task.get("q11_request"), "repeat": repeat,
        "seed": session.seed, "model": model_record(session),
        "host": {"aelix": session.aelix_version, "aelix_command": session.aelix_command,
                 "launch": launch_of(session),
                 "launcher": None if session.model.get("auth") is None else {
                     "file": "tools/model_eval_aelix.py", "sha256": sha256_file(LAUNCHER),
                     "what": "aelix's own entry point (aelix_coding_agent.cli.entry.main_sync) with its "
                             "credential store opened at the login's file; nothing else changed"},
                 "python": platform.python_version(), "platform": platform.platform(),
                 "machine": platform.machine()},
        "kit": session.checkout,
        "conditions": {"surface": session.surface, "proposal": session.proposal, "kit": has_kit(session)},
        "fixture": {**session.config.fixtures[task["fixture"]], "seed": session.seed},
        "answer": {"status": answer["status"], "confirmed_by": answer.get("confirmed_by")},
    }


def redaction_marker(length: int) -> bytes:
    """A marker as long as the secret: a binary file keeps its layout."""
    if length < 10:
        return b"*" * length
    return b"<redacted" + b"*" * (length - 10) + b">"


@lru_cache(maxsize=1)
def launcher_module():
    """tools/model_eval_aelix.py as a module, for its forms_of and mask: the harness masks a key
    variable's value by the same forms the launcher masks the login's values by. The launcher imports
    aelix only when it runs aelix, so loading it here imports nothing else."""
    spec = importlib.util.spec_from_file_location("model_eval_aelix_forms", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def secret_forms(secrets) -> list[tuple[bytes, bytes]]:
    """Each secret's forms in a file, each with its marker: the launcher's forms_of (raw, JSON-escaped,
    URL-encoded, base64 at each alignment, hex, UTF-16LE and BE). A secret shorter than
    MIN_SECRET_LENGTH by its UTF-8 bytes only: a short value's encoded slices would mark ordinary text."""
    forms = []
    for secret in dict.fromkeys(s for s in secrets if s):
        if len(secret) >= MIN_SECRET_LENGTH:
            forms += launcher_module().forms_of(secret)
        else:
            raw = secret.encode("utf-8")
            forms.append((raw, redaction_marker(len(raw))))
    return forms


def redact_value(value, secrets: list[str]):
    """The value with every secret's text replaced, recursively: each of its forms (secret_forms) that
    is text, longest first."""
    texts = set()
    for form, _ in secret_forms(secrets):
        with contextlib.suppress(UnicodeDecodeError):
            texts.add(form.decode("utf-8"))
    ordered = sorted(texts, key=len, reverse=True)

    def redact(item):
        if isinstance(item, str):
            for text in ordered:
                item = item.replace(text, "<redacted>")
            return item
        if isinstance(item, dict):
            return {redact(k): redact(v) for k, v in item.items()}
        if isinstance(item, list):
            return [redact(v) for v in item]
        return item

    return redact(value)


# --- the run's files: links, exact secrets, credential shapes --------------------------------------

class LinkedFile(Exception):
    """A file that has a name outside the run (a hard link made after the sweep): never read."""


def rel_of(path, root: Path, prefix: str = "") -> str:
    try:
        return prefix + Path(path).relative_to(root).as_posix()
    except ValueError:
        return prefix + str(path)


def walk(root: Path, errors: list, prefix: str = ""):
    """(path, lstat) of every entry under root; links are not followed and nothing is opened. A
    directory that cannot be listed or an entry that cannot be looked at is added to errors: what was
    not searched is not passed."""
    def failed(exc: OSError) -> None:
        errors.append({"file": rel_of(exc.filename or root, root, prefix),
                       "error": f"{type(exc).__name__}: {exc.strerror or exc}"})

    for dirpath, dirnames, filenames in os.walk(root, onerror=failed, followlinks=False):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            try:
                yield path, os.lstat(path)
            except OSError as exc:
                failed(exc)


def plain_path(path: str, windows: bool = os.name == "nt") -> str:
    """A path without Windows' extended-length prefix: os.readlink gives \\\\?\\C:\\… (\\\\?\\UNC\\server\\…)
    for a link made to C:\\… (\\\\server\\…), and os.path.realpath does for a path it cannot shorten. A
    record then names a link's target the same way on every platform, and two forms of one path compare
    equal. Elsewhere the path as it is."""
    if windows:
        if path.startswith("\\\\?\\UNC\\"):
            return "\\\\" + path[8:]
        if path.startswith("\\\\?\\"):
            return path[4:]
    return path


def sweep(root: Path, login: set = frozenset(), prefix: str = "") -> dict:
    """Remove the links in root that reach a file outside it, found by lstat only (no file is opened):
    a symbolic link whose target leaves root, and every name of a regular file that also has a name
    outside root (more links than names here: a hard link) or that is the login's file (login: its
    (device, inode) pairs). Masking such a file, or a copy or archive of the run, would otherwise
    write or carry the file it reaches. Links inside root are listed and kept; `shared` holds the
    files whose every name is inside root (each is masked by replacing it). A link's target is
    recorded as plain_path gives it; the paths compared are plain and case-folded (os.path.normcase)."""
    errors: list = []
    links, outside, hard = [], [], []
    names: dict[tuple, list] = {}
    counts: dict[tuple, int] = {}
    base = os.path.normcase(plain_path(os.path.realpath(root)))
    for path, st in walk(root, errors, prefix):
        rel = rel_of(path, root, prefix)
        if stat.S_ISLNK(st.st_mode):
            try:
                target = plain_path(os.readlink(path))
                real = os.path.normcase(plain_path(os.path.realpath(path)))
            except OSError as exc:
                errors.append({"file": rel, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if real == base or real.startswith(base.rstrip("\\/") + os.sep):
                links.append({"file": rel, "target": target})
                continue
            try:
                os.unlink(path)
                outside.append({"file": rel, "target": target})
            except OSError as exc:  # left in place: failed
                errors.append({"file": rel, "error": f"a link leaving the run was not removed: {exc}"})
        elif stat.S_ISREG(st.st_mode) and (st.st_nlink > 1 or (st.st_dev, st.st_ino) in login):
            key = (st.st_dev, st.st_ino)
            names.setdefault(key, []).append((path, rel))
            counts[key] = st.st_nlink
    shared = set()
    for key, items in names.items():
        if key not in login and len(items) >= counts[key]:
            shared.add(key)
            continue
        for path, rel in items:
            try:
                os.unlink(path)
                hard.append({"file": rel, "links": counts[key], "login": key in login})
            except OSError as exc:
                errors.append({"file": rel, "error": f"a hard link was not removed: {exc}"})
    return {"links": links, "outside_links_removed": outside, "hard_links_removed": hard,
            "errors": errors, "shared": shared}


def read_private(path: Path, shared: set) -> bytes | None:
    """A regular file's bytes, opened without following a link (and without blocking on a pipe);
    None when the name is no longer a regular file. LinkedFile when it has another name the sweep
    did not see inside the run."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | \
        getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_nlink > 1 and (st.st_dev, st.st_ino) not in shared:
            raise LinkedFile(str(path))
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def replace_bytes(path: Path, data: bytes) -> None:
    """Write data as a new file in path's place (a temporary file in its directory, then os.replace),
    never into the old file: another name of it, should one be left, is not written through."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".masked-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        with contextlib.suppress(OSError):
            os.chmod(tmp, stat.S_IMODE(os.lstat(path).st_mode))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def redact_tree(root: Path, secrets: list[str], login: set = frozenset()) -> list[str]:
    """Overwrite every form of every secret (secret_forms) in every file under root by a marker of its
    length: links are not followed, links that reach outside root are removed first (sweep), and a
    masked file replaces the old one (replace_bytes). The files where one was found, relative to root."""
    forms = secret_forms(secrets)
    if not forms or not root.is_dir():
        return []
    shared = sweep(root, login)["shared"]
    found = []
    for path, st in walk(root, []):
        if not stat.S_ISREG(st.st_mode):
            continue
        try:
            data = read_private(path, shared)
        except (OSError, LinkedFile):  # the scan reports it
            continue
        if data is None:
            continue
        masked, count = launcher_module().mask(data, forms)
        if count:
            replace_bytes(path, masked)
            found.append(rel_of(path, root))
    return sorted(found)


# Credential-shaped text, found without knowing any value. Each kind: its pattern, the group that is
# the value (overwritten), and whether the value must mix two of upper case, lower case and digits
# (a placeholder like ollama-local-no-key or a hex digest is not a credential).
_B64URL = rb"[A-Za-z0-9_-]"
_VALUE = rb"[A-Za-z0-9._~+/=-]"
_QUOTE = rb"\\*[\"']"
CREDENTIAL_PATTERNS = (
    ("jwt", re.compile(rb"eyJ" + _B64URL + rb"{8,}\." + _B64URL + rb"{8,}\." + _B64URL + rb"{8,}"), 0, False),
    ("bearer", re.compile(rb"(?<![A-Za-z0-9])[Bb]earer[ \t]+(" + _VALUE + rb"{16,})"), 1, True),
    # A field with a value: quoted after a colon (JSON, YAML, a literal), quoted or bare after = (an
    # environment line, a query); a bare value stops at a dot, so code (accessToken:r._token) is not one.
    # Quotes may be escaped, as in a file's JSON printed into a JSON event line (\"refresh\": \"…\").
    # The name may have words before the keyword (GEMINI_CLI_IDE_AUTH_TOKEN=, my_api_key:).
    ("credential_field", re.compile(
        rb"(?<![A-Za-z0-9_])(?:" + _QUOTE + rb")?(?:[A-Za-z0-9]+_)*(?i:refresh|access|refresh_?token|"
        rb"access_?token|id_?token|token|api_?key|client_?secret|secret|password)(?:" + _QUOTE +
        rb")?\s*(?::\s*" + _QUOTE + rb"(" + _VALUE + rb"{16,})|=\s*" + _QUOTE + rb"(" + _VALUE +
        rb"{16,})|=\s*([A-Za-z0-9_~+/=-]{16,}))"),
     (1, 2, 3), True),
    ("sk_key", re.compile(rb"(?<![A-Za-z0-9_-])sk-" + _B64URL + rb"{20,}"), 0, False),
    # Delimited by characters outside base64 on both sides: a piece of a longer base64 run (an image
    # in a tool result) is not one; 256 at most: the codex backend's encrypted reasoning (Fernet,
    # gAAAAA…) is longer, and is also left out by its prefix. random_like() leaves out names and code.
    ("long_token", re.compile(rb"(?<![A-Za-z0-9_+/.-])" + _B64URL + rb"{32,256}(?![A-Za-z0-9_+/=-])"),
     0, True),
)
CREDENTIAL_KINDS = tuple(kind for kind, *_ in CREDENTIAL_PATTERNS)
CLASSES = (rb"[A-Z]", rb"[a-z]", rb"[0-9]")
# A digest written as algorithm=base64url (a wheel's RECORD: sha256=…) is not a credential.
DIGEST_PREFIX = re.compile(rb"(?:sha(?:1|224|256|384|512)|md5|blake2[bs])=$", re.IGNORECASE)
# Values of the kit's own that have a credential's shape (2026-10-31 campaign: 486 findings in the 24
# runs whose model worked, all of them these): the cursor of a limited read's next page, a Visualization
# reference's `token`, which is `<script path>::<name>`, and the `token` of a reference the kit could not
# bind, which is the name the model wrote (`{"type": "visualization_version", "token": …}`). Quotes may
# be escaped, as in a JSON event line.
KIT_CURSOR_BEFORE = re.compile(rb'\\*"cursor\\*"\s*:\s*\\*"$')
KIT_BINDING_BEFORE = re.compile(rb'\\*"type\\*"\s*:\s*\\*"(?:visualization_version|dataset_import)\\*"\s*,\s*$')
FIELD_NAME = re.compile(rb'\\*["\']?([A-Za-z0-9_]+)')


def mixed(value: bytes) -> bool:
    """Two of upper case, lower case and digits."""
    return sum(bool(re.search(p, value)) for p in CLASSES) >= 2


def random_like(value: bytes) -> bool:
    """Whether a long base64url run looks random rather than like a name or code: at least two each of
    upper case, lower case and digits, vowels under 35% of its letters (words and identifiers have
    more; random base64url about 19%), no four ascending characters (an alphabet: 0123, abcd) and
    not the codex backend's encrypted reasoning (gAAAAA…)."""
    if any(len(re.findall(p, value)) < 2 for p in CLASSES) or value.startswith(b"gAAAAA"):
        return False
    letters = re.findall(rb"[A-Za-z]", value)
    if sum(c in b"aeiouAEIOU" for c in letters) >= 0.35 * len(letters):
        return False
    return not any(value[i + 1] == value[i] + 1 and value[i + 2] == value[i] + 2 and value[i + 3] == value[i] + 3
                   for i in range(len(value) - 3))


def credential_findings(data: bytes, *, mask: bool = False) -> list[dict]:
    """Credential-shaped values in data: kind, offset, line and length, never the value. With mask,
    each value is overwritten in data (a bytearray) by a marker of its length, so that the kinds after
    it do not find it again."""
    work = data if mask else bytearray(data)
    found = []
    for kind, pattern, groups, needs_mix in CREDENTIAL_PATTERNS:
        line, counted = 1, 0  # matches come in order: lines are counted forward once per kind
        for match in list(pattern.finditer(bytes(work))):
            group = next(g for g in groups if match.group(g) is not None) \
                if isinstance(groups, tuple) else groups
            value = match.group(group)
            if needs_mix and not mixed(value):
                continue
            start, end = match.span(group)
            if kind == "long_token" and (not random_like(value) or DIGEST_PREFIX.search(
                    bytes(work[max(0, start - 12):start])) or KIT_CURSOR_BEFORE.search(
                    bytes(work[max(0, start - 24):start]))):
                continue
            if kind == "credential_field" and FIELD_NAME.match(match.group(0)).group(1).lower() == b"token" and (
                    bytes(work[end:end + 2]) == b"::" or KIT_BINDING_BEFORE.search(
                        bytes(work[max(0, match.start(0) - 64):match.start(0)]))):
                continue
            line += work.count(b"\n", counted, start)
            counted = start
            found.append({"kind": kind, "offset": start, "line": line, "length": end - start})
            work[start:end] = redaction_marker(end - start)
    return sorted(found, key=lambda f: f["offset"])


def mask_text(text: str) -> str:
    """text with every credential shape overwritten by its marker (for text a record or the console
    gets from elsewhere: aelix's stderr, a command line, a link's target, a file's name). Each piece
    between path separators is searched on its own too: a token that is a path's component is not
    delimited the way the long-token pattern wants."""
    def piece(part: str) -> str:
        data = bytearray(part.encode("utf-8", errors="replace"))
        return data.decode("utf-8", errors="replace") if credential_findings(data, mask=True) else part

    return re.sub(r"[^/\\]+", lambda m: piece(m.group(0)), piece(text))


def mask_name(name: str) -> str:
    """A file name with its credential shapes masked, in characters every file system takes."""
    return re.sub(r"[<>*]", "_", mask_text(name))


def mask_strings(value):
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {mask_text(k) if isinstance(k, str) else k: mask_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_strings(v) for v in value]
    return value


MAX_LISTED = 200


def scan_tree(root: Path, *, login: set = frozenset(), prefix: str = "") -> dict:
    """Search every file under root for credential-shaped text without knowing any value.

    First the sweep (links that reach outside root removed). Then each regular file is read without
    following a link; each finding is overwritten by a marker of its length — the masked file
    replaces the old one — and listed by file, line, kind and length, never by value. The scan fails
    when it found one, removed a link, or could not read a directory or a file."""
    started = time.time()
    swept = sweep(root, login, prefix)
    findings, masked_files, names = [], [], []
    unreadable = list(swept["errors"])
    hard = list(swept["hard_links_removed"])
    files = scanned = 0
    walk_errors: list = []
    for path, st in walk(root, walk_errors, prefix):
        rel = rel_of(path, root, prefix)
        if mask_name(path.name) != path.name:  # a name shaped like a credential: renamed afterwards
            names.append(path)
        if not stat.S_ISREG(st.st_mode):
            continue
        try:
            data = read_private(path, swept["shared"])
        except LinkedFile:
            with contextlib.suppress(OSError):
                os.unlink(path)
            hard.append({"file": rel, "links": None, "login": False})
            continue
        except OSError as exc:
            unreadable.append({"file": rel, "error": f"{type(exc).__name__}: {exc.strerror or exc}"})
            continue
        if data is None:
            continue
        files += 1
        scanned += len(data)
        work = bytearray(data)
        found = credential_findings(work, mask=True)
        if not found:
            continue
        try:
            replace_bytes(path, bytes(work))
            masked_files.append(rel)
        except OSError as exc:
            unreadable.append({"file": rel, "error": f"not masked: {type(exc).__name__}: {exc}"})
        findings += [{"file": rel, **{k: f[k] for k in ("line", "kind", "length")}} for f in found]
    unreadable += [e for e in walk_errors if e["file"] not in {u["file"] for u in unreadable}]
    # Names last, deepest first: a renamed directory would otherwise move what is below it.
    for path in sorted(names, key=lambda p: len(p.parts), reverse=True):
        rel = rel_of(path, root, prefix)
        try:
            os.rename(path, path.with_name(mask_name(path.name)))
            findings.append({"file": rel, "line": 0, "kind": "file_name", "length": len(path.name)})
        except OSError as exc:
            unreadable.append({"file": rel, "error": f"a name shaped like a credential was not changed: {exc}"})
    problems = [name for name, items in (("credential_shapes", findings),
                                         ("outside_links", swept["outside_links_removed"]),
                                         ("hard_links", hard), ("unreadable", unreadable)) if items]
    return {"status": "failed" if problems else "passed", "problems": problems, "files": files,
            "bytes": scanned, "kinds": list(CREDENTIAL_KINDS), "findings_count": len(findings),
            "findings": findings[:MAX_LISTED], "masked": bool(masked_files),
            "masked_files": masked_files[:MAX_LISTED], "links": swept["links"][:MAX_LISTED],
            "outside_links_removed": swept["outside_links_removed"][:MAX_LISTED],
            "hard_links_removed": hard[:MAX_LISTED], "unreadable": unreadable[:MAX_LISTED],
            "started_at": started,
            "what": "credential-shaped text searched in every file under the run's directory without "
                    "knowing any value; each finding is overwritten by a marker of its length (the file "
                    "is replaced, never written through) and listed by file, line, kind and length, "
                    "never by value; links that reach outside the run are removed"}


def written_since(root: Path, since: float, skip: set, prefix: str = "") -> list[str]:
    """Regular files under root modified after since (lstat only), but those in skip."""
    out = []
    for path, st in walk(root, [], prefix):
        rel = rel_of(path, root, prefix)
        if stat.S_ISREG(st.st_mode) and st.st_mtime > since and rel not in skip:
            out.append(rel)
    return out


# --- processes that outlive aelix ------------------------------------------------------------------

# How long the harness waits, after aelix has exited, for processes still working in the work root.
SETTLE_TIMEOUT = 60
# One shared budget for probes and stops after the natural-exit wait, including new writers that
# appear while earlier ones are stopped. Each settle call takes at most 90 seconds by default.
SETTLE_STOP_TIMEOUT = 30
# How long aelix gets after SIGTERM before SIGKILL: its token refresh (aelix_ai/oauth/openai_codex.py,
# httpx timeout 30 s for each of connect, write and read) is let finish writing the login.
STOP_GRACE = 100


def process_table(*, timeout: float = 120) -> dict[int, tuple[int, str]] | None:
    """pid -> (parent pid, command line) of every process; None when the list cannot be read."""
    if os.name == "nt":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance Win32_Process | ForEach-Object { "
                "\"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }"]
    else:
        argv = ["ps", "-A", "-ww", "-o", "pid=", "-o", "ppid=", "-o", "args="]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    table = {}
    for line in done.stdout.splitlines():
        parts = line.strip().split("\t", 2) if os.name == "nt" else line.split(None, 2)
        if len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2].strip() if len(parts) > 2 else "")
    return table


def holders(paths: list[str], *, timeout: float = 120) -> dict[int, str] | None:
    """Processes whose working directory is under one of paths or that hold a file open there (pid ->
    which): /proc on Linux, lsof elsewhere on POSIX. None when that cannot be listed (Windows: its
    process list has no working directory)."""
    def under(name: str) -> bool:
        return any(name == p or name.startswith(p.rstrip("\\/") + os.sep) for p in paths)

    if os.name == "nt":
        return None
    deadline = time.monotonic() + timeout
    found: dict[int, str] = {}
    if os.path.isdir("/proc/self/fd"):
        for entry in os.listdir("/proc"):
            if time.monotonic() >= deadline:
                return None
            if not entry.isdigit():
                continue
            with contextlib.suppress(OSError):
                if under(os.readlink(f"/proc/{entry}/cwd")):
                    found[int(entry)] = "working directory"
                    continue
            with contextlib.suppress(OSError):
                for fd in os.listdir(f"/proc/{entry}/fd"):
                    if time.monotonic() >= deadline:
                        return None
                    with contextlib.suppress(OSError):
                        if under(os.readlink(f"/proc/{entry}/fd/{fd}")):
                            found[int(entry)] = "open file"
                            break
        return found
    lsof = shutil.which("lsof") or next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof") if os.path.exists(p)),
                                        None)
    if lsof is None:
        return None
    try:
        done = subprocess.run([lsof, "-n", "-P", "-w", "-F", "pfn"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if not done.stdout:
        return None
    pid, fd = None, None
    for line in done.stdout.splitlines():
        tag, value = line[:1], line[1:]
        if tag == "p":
            pid = int(value) if value.isdigit() else None
        elif tag == "f":
            fd = value
        elif tag == "n" and pid is not None and under(value):
            found.setdefault(pid, "working directory" if fd == "cwd" else "open file")
    return found


def stop_processes(pids: list[int], grace: float = 5, *, timeout: float = SETTLE_STOP_TIMEOUT) -> None:
    """SIGTERM, then SIGKILL to what is left after grace (Windows: the tree, at once)."""
    ports = processes()
    deadline = time.monotonic() + timeout
    if os.name == "nt":
        for pid in pids:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # The kit's kill_tree has its own per-pid timeout. The harness needs one shared deadline
            # for this list instead: taskkill's outcome is checked by settle's next process probe.
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run([ports.system_program("taskkill.exe"), "/F", "/T", "/PID", str(pid)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=remaining, check=False)
        return
    for pid in pids:
        if time.monotonic() >= deadline:
            return
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
    grace_deadline = min(deadline, time.monotonic() + grace)
    while time.monotonic() < grace_deadline and any(ports.alive(p) for p in pids):
        time.sleep(min(0.1, max(0, grace_deadline - time.monotonic())))
    for pid in pids:
        if ports.alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


class Descendants:
    """aelix's descendants, from the process list every few seconds while it runs. The model's bash
    tool starts each command in a session of its own (aelix_coding_agent/tools/bash.py), so neither
    aelix's process group nor a command line naming the work root reaches all of them. seen: pid ->
    command line (the same pid with another command line later is another process)."""

    def __init__(self, proc, interval: float | None = None):
        self.proc = proc
        self.interval = interval or (2.0 if os.name == "nt" else 1.0)
        self.seen: dict[int, str] = {}
        self.unreadable = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def poll(self) -> None:
        table = process_table()
        if table is None:
            self.unreadable += 1
            return
        children: dict[int, list[int]] = {}
        for pid, (ppid, _) in table.items():
            children.setdefault(ppid, []).append(pid)
        # The root only while it has not been reaped: its pid may be another process's afterwards.
        stack = [self.proc.pid] if self.proc.returncode is None else []
        stack += [p for p, args in self.seen.items() if p in table and table[p][1][:300] == args]
        while stack:
            for child in children.get(stack.pop(), []):
                if child not in self.seen and child != os.getpid():
                    self.seen[child] = table[child][1][:300]
                    stack.append(child)

    def _run(self) -> None:
        while True:
            self.poll()
            if self._stop.wait(self.interval):
                return

    def __enter__(self) -> Descendants:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=60)
        self.poll()  # what the last ones started


def clip(text: str, limit: int) -> str:
    """text cut to limit characters at a white space, so that no part of a token is left at the end: the
    launcher masks a login's value only whole (a command line the record keeps from before its pass)."""
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    at = max(cut.rfind(c) for c in " \t\r\n")
    return (text[:at] if at > 0 else "") + " …"


def settle(work, timeout: float = SETTLE_TIMEOUT, tracked: dict | None = None,
           exclude: set | frozenset = frozenset()) -> dict:
    """Wait until no process works in the work root, so that nothing writes into it while it is
    moved, its environment removed and its files searched; stop the ones left after the timeout (at
    once when the harness has been asked to stop). A process counts when its command line names the
    work root (the kit's extension starts its report build detached at the end of every prompt: uv
    run --project <project> … render --if-changed), when its working directory is there or it holds a
    file open there (a job the model left in the background), or when it descends from aelix
    (tracked: Descendants.seen, the same pid with the same command line). exclude: the launcher, which
    waits to mask the run, and uv above it. The time is not part of the run's elapsed_s."""
    roots = [work] if isinstance(work, (str, Path)) else list(work)
    paths = sorted({str(p) for p in roots} | {os.path.realpath(p) for p in roots})
    started = time.monotonic()
    max_wait = max(0, timeout) + SETTLE_STOP_TIMEOUT
    deadline = started + max_wait
    seen: dict[int, dict] = {}
    signalled: list[int] = []
    found: dict[int, set] = {}
    cwd_checked = True
    checked, reason = True, None
    cleanup_started = False
    while True:
        if STOP["signals"] and not cleanup_started:
            deadline = min(deadline, time.monotonic() + SETTLE_STOP_TIMEOUT)
            max_wait = deadline - started
            cleanup_started = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            checked, reason = False, "the settle deadline was reached before the next process check"
            break
        table = process_table(timeout=remaining)
        if table is None:
            checked, reason = False, "the process list could not be read within the settle deadline"
            break
        me = os.getpid()
        found = {}
        for pid, (_, args) in table.items():
            if pid == me or pid in exclude:
                continue
            if any(p in args for p in paths):
                found.setdefault(pid, set()).add("command line")
            if tracked and tracked.get(pid) == args[:300]:
                found.setdefault(pid, set()).add("started by aelix")
        remaining = deadline - time.monotonic()
        held = holders(paths, timeout=remaining) if remaining > 0 else None
        if held is None:
            cwd_checked = False
        else:
            for pid, why in held.items():
                if pid != me and pid in table and pid not in exclude:
                    found.setdefault(pid, set()).add(why)
        for pid, why in found.items():
            entry = seen.setdefault(pid, {"pid": pid, "args": mask_text(clip(table[pid][1], 300)), "why": []})
            entry["why"] = sorted(set(entry["why"]) | why)
        if time.monotonic() >= deadline:
            checked, reason = False, "the settle deadline was reached during the process check"
            break
        if not found:
            break
        elapsed = time.monotonic() - started
        if elapsed >= timeout or STOP["signals"]:
            if not cleanup_started:
                deadline = min(deadline, time.monotonic() + SETTLE_STOP_TIMEOUT)
                max_wait = deadline - started
                cleanup_started = True
            fresh = [pid for pid in found if pid not in signalled]
            if not fresh or time.monotonic() >= deadline:
                break
            stop_processes(fresh, timeout=deadline - time.monotonic())
            signalled += fresh
            continue
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    left = sorted(found)
    return {"checked": checked, "reason": reason, "cwd_checked": cwd_checked,
            "waited_s": round(time.monotonic() - started, 1), "timeout_s": max(0, timeout),
            "stop_timeout_s": SETTLE_STOP_TIMEOUT, "max_wait_s": max_wait,
            "deadline_reached": time.monotonic() >= deadline,
            "processes": sorted(seen.values(), key=lambda e: e["pid"]),
            "stopped": [pid for pid in signalled if pid not in left], "left": left,
            "what": "processes still working in the work root after aelix exited (named on their command "
                    "line, their working directory or an open file there, or started by aelix): waited "
                    "for, then stopped, before the work root is moved and searched"}


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except OSError:
        return False
    return True


def stop_group(proc, grace: float = STOP_GRACE, link: LauncherLink | None = None) -> dict:
    """End aelix's process group (uv, the launcher's or aelix's Python, what stayed in the group):
    SIGTERM first, so that a token refresh in progress finishes writing the login (the launcher
    defers its exit until then), SIGKILL after grace. With the launcher's link, SIGTERM stops aelix
    and the launcher then waits to mask the run (launcher_waiting); it ends when the harness tells it
    (LauncherLink.finish). Windows: the tree at once (taskkill /T /F)."""
    ports = processes()
    started = time.monotonic()
    if os.name == "nt":
        ports.kill_tree(proc.pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=60)
        return {"how": "the tree ended at once (taskkill /T /F)", "waited_s": round(time.monotonic() - started, 1)}
    with contextlib.suppress(OSError):
        os.killpg(proc.pid, signal.SIGTERM)
    deadline, killed = started + grace, False
    while True:
        proc.poll()  # reaps uv, the group's leader: a zombie would keep the group
        if link is not None and link.waiting is not None:
            return {"how": "SIGTERM", "grace_s": grace, "launcher_waiting": True,
                    "waited_s": round(time.monotonic() - started, 1)}
        if not group_alive(proc.pid):
            break
        if time.monotonic() > deadline:
            if killed:
                break
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            killed, deadline = True, time.monotonic() + 10
        if link is not None:
            link.read(0.1)
        else:
            time.sleep(0.1)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=60)
    return {"how": "SIGTERM, SIGKILL after the grace" if killed else "SIGTERM", "grace_s": grace,
            "waited_s": round(time.monotonic() - started, 1)}


# --- the launcher's link: masking the run by the login's exact values ------------------------------

# How long the harness waits for one masking pass of the launcher, and for the launcher to end.
MASK_TIMEOUT = 600
LAUNCHER_EXIT_TIMEOUT = 60
# The shortest value the launcher compares (tools/model_eval_aelix.py MIN_VALUE_LENGTH).
EXACT_MIN_LENGTH = 16
EXACT_WHAT = ("the login's exact values (access, refresh, id_token and every other credential string of "
              "the provider entries aelix's AuthStorage held, and the API keys it resolved; at least "
              f"{EXACT_MIN_LENGTH} characters) in their raw, JSON-escaped, URL-encoded, base64/base64url "
              "and UTF-16 forms, masked by the launcher (aelix's own process, which holds them; they are "
              "never written, printed, passed on or returned) in every file, link target and name under the "
              "run once nothing works in the work root any more, before the shape scan, and in run.json "
              "once more at the end; a masked file is replaced, never written through; a file that is the "
              "login's or has a name outside the run is not read. Counts and relative paths only: never a "
              "value, its length or its hash. A value split across lines or encoded otherwise is not found")


class LauncherLink:
    """The harness's side of the two pipes to the launcher (POSIX, a model that uses an aelix login).

    The lifeline: the harness writes the launcher's commands into it (`all`: mask the run's directory
    and the work root by the login's exact values; `record`: mask run.json once more; `exit`); when the
    harness ends, even by SIGKILL, it closes, and the launcher stops aelix and masks on its own. The
    report pipe: the launcher's JSON lines (`waiting`, once aelix's main has returned or a signal
    stopped it, with its pid and aelix's exit status; `masked` after each pass: counts and relative
    paths, never a value, its length or its hash). The harness's own ends are not inheritable; the
    launcher keeps its ends from aelix's tools."""

    def __init__(self) -> None:
        self.life_r, self.life_w = os.pipe()
        self.rep_r, self.rep_w = os.pipe()
        self.proc = None
        self.buffer = b""
        self.waiting: dict | None = None
        self.reports: list[dict] = []
        self.eof = False
        self.closed = False
        self.ended: dict | None = None

    def child_fds(self) -> tuple[int, int]:
        return self.life_r, self.rep_w

    def spawned(self, proc) -> None:
        """The launcher has its ends: the harness keeps only the lifeline's write end and the reports'
        read end."""
        self.proc = proc
        for fd in (self.life_r, self.rep_w):
            with contextlib.suppress(OSError):
                os.close(fd)
        self.life_r = self.rep_w = None

    def read(self, timeout: float) -> None:
        """The launcher's report lines that arrive within timeout."""
        if self.eof or self.rep_r is None:
            time.sleep(timeout)
            return
        try:
            ready, _, _ = select.select([self.rep_r], [], [], timeout)
        except (OSError, ValueError):
            ready = []
        if not ready:
            return
        try:
            chunk = os.read(self.rep_r, 1 << 16)
        except OSError:
            chunk = b""
        if not chunk:
            self.eof = True
            return
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                message = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("event") == "waiting" and self.waiting is None:
                self.waiting = message
            elif message.get("event") == "masked":
                self.reports.append(message)

    def gone(self) -> bool:
        """aelix's group has ended (uv reaped, nothing left in the group)."""
        return self.proc is None or (self.proc.poll() is not None and not group_alive(self.proc.pid))

    def wait(self, timeout: float):
        """aelix's exit status once the launcher says aelix's main has returned (it then waits to mask
        the run), or once the group has ended without that (the launcher crashed or was killed).
        subprocess.TimeoutExpired after timeout."""
        deadline = time.monotonic() + timeout
        while self.waiting is None:
            if self.gone():
                self.read(0)
                if self.waiting is None:
                    return self.proc.returncode
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(self.proc.args, timeout)
            self.read(min(remaining, 0.5))
        return self.code()

    def code(self):
        return self.waiting.get("code") if self.waiting is not None else self.proc.returncode

    def exclude(self) -> set[int]:
        """What the settle leaves alone while the launcher waits: uv, the launcher and any process
        between them."""
        if self.proc is None or self.waiting is None:
            return set()
        pids = {self.proc.pid}
        table = process_table() or {}
        pid = self.waiting.get("pid")
        while isinstance(pid, int) and pid > 1 and pid not in pids:
            pids.add(pid)
            pid = table.get(pid, (None, ""))[0]
        return pids

    def send(self, command: str) -> bool:
        if self.closed or self.life_w is None:
            return False
        try:
            os.write(self.life_w, f"{command}\n".encode("ascii"))
        except OSError:
            return False
        return True

    def mask(self, scope: str, timeout: float = MASK_TIMEOUT) -> dict | None:
        """One masking pass of the launcher and its report; None when the launcher cannot be asked (it
        never said it waits, or it is gone) or did not answer within timeout."""
        if self.waiting is None or not self.send(scope):
            return None
        deadline = time.monotonic() + timeout
        while True:
            for i, report in enumerate(self.reports):
                if report.get("scope") == scope:
                    return self.reports.pop(i)
            if self.eof or self.gone() or time.monotonic() > deadline:
                self.read(0)
                if not any(r.get("scope") == scope for r in self.reports):
                    return None
                continue
            self.read(0.5)

    def why_not(self) -> str:
        if self.proc is None:
            return "aelix was not started"
        if self.waiting is None:
            return ("the launcher ended without saying that aelix had returned (killed, or it crashed): "
                    "nothing masked the run by the login's values")
        return f"the launcher did not answer a pass (it ended, or the pass took longer than {MASK_TIMEOUT} s)"

    def finish(self) -> dict:
        """Tell the launcher to end and wait for aelix's group, SIGKILL after LAUNCHER_EXIT_TIMEOUT;
        then the pipes are closed."""
        if self.ended is not None or self.proc is None:
            self.close()
            return self.ended or {}
        started = time.monotonic()
        told = self.send("exit")
        deadline, killed = started + LAUNCHER_EXIT_TIMEOUT, False
        while not self.gone():
            if time.monotonic() > deadline:
                if killed:
                    break
                with contextlib.suppress(OSError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
                killed, deadline = True, time.monotonic() + 10
            self.read(0.1)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=10)
        self.close()
        self.ended = {"told": told, "killed": killed, "waited_s": round(time.monotonic() - started, 1)}
        return self.ended

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for name in ("life_r", "life_w", "rep_r", "rep_w"):
            fd = getattr(self, name)
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
                setattr(self, name, None)


# --- stopping the harness --------------------------------------------------------------------------

class Stopped(BaseException):
    """The harness was asked to stop (SIGTERM, SIGHUP, SIGINT): the run in progress is still closed —
    aelix stopped, the work root secured, every file searched, the record written."""


STOP: dict = {"signals": [], "deferring": False}


def on_stop_signal(signum, _frame) -> None:
    STOP["signals"].append(signum)
    if not STOP["deferring"] and len(STOP["signals"]) == 1:
        raise Stopped(f"stopped by signal {signum}")


@contextlib.contextmanager
def stop_signals():
    """SIGTERM, SIGHUP and SIGINT become Stopped while a command runs (main thread only; the old
    handlers are put back). An agent's command timeout or task stop sends one. A kill -9 cannot be
    caught: the provisional record, the launcher's lifeline and `scan OUT` cover it."""
    STOP["signals"].clear()
    STOP["deferring"] = False
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for name in ("SIGTERM", "SIGHUP", "SIGINT"):
            sig = getattr(signal, name, None)
            if sig is not None:
                with contextlib.suppress(OSError, ValueError):
                    previous[sig] = signal.signal(sig, on_stop_signal)
    try:
        yield
    finally:
        for sig, handler in previous.items():
            with contextlib.suppress(OSError, ValueError, TypeError):
                signal.signal(sig, handler)


@contextlib.contextmanager
def deferred():
    """A signal that arrives while a run is being stopped or closed is noted, not raised: it cannot
    cut the scan short. raise_if_stopped raises it afterwards."""
    old = STOP["deferring"]
    STOP["deferring"] = True
    try:
        yield
    finally:
        STOP["deferring"] = old


def raise_if_stopped() -> None:
    if STOP["signals"]:
        raise Stopped(f"stopped by signal {STOP['signals'][0]}")


# --- after aelix: securing, searching and recording the run ---------------------------------------

def remove_environments(root: Path) -> list[dict]:
    """Remove the Python environments under root (a .venv directory with pyvenv.cfg), never following
    a link. The kit makes the project's with uv from its uv.lock, so it can be made again; it holds
    about 450 MB of public packages per run and links into uv's Python (and, where uv links files
    from its cache, hard links into the cache), which a later copy or archive of the run would follow."""
    removed = []
    for dirpath, dirnames, _ in os.walk(root, followlinks=False):
        for name in list(dirnames):
            path = Path(dirpath) / name
            if name != ".venv" or path.is_symlink() or not (path / "pyvenv.cfg").is_file():
                continue
            files = size = 0
            for inner, _, names in os.walk(path, followlinks=False):
                for item in names:
                    files += 1
                    with contextlib.suppress(OSError):
                        size += (Path(inner) / item).lstat().st_size
            shutil.rmtree(path)
            removed.append({"path": path.relative_to(root).as_posix(), "files": files, "bytes": size})
            dirnames.remove(name)
    return removed


def provisional(record: dict) -> dict:
    """The record on disk while a run is in progress: what a harness that is killed outright leaves."""
    return {**record, "outcome": "error", "provisional": True,
            "harness_error": "the harness stopped before it finished this run (killed, or it crashed): "
                             "this is the record written while aelix ran; `model_eval.py scan OUT` "
                             "secures the work root and searches the run",
            "secret_scan": {"status": "not_scanned", "problems": ["not_scanned"],
                            "what": "not searched yet: the harness stopped before its scan"}}


def secure_work(work: Path | None, kept: Path, *, login: set, tracked: dict | None = None,
                timeout: float = SETTLE_TIMEOUT, launcher: LauncherLink | None = None) -> dict:
    """The work root after aelix exited, each step on its own (a failure of one is recorded and the
    next still runs): wait for (then stop) the processes still working there, remove the project's
    Python environment, sweep the links that reach outside it (before a move that could copy what
    they reach), move it into the run, and look again for processes (a working directory moves with
    the directory). Last, with the launcher (a model that uses an aelix login, POSIX), its first
    masking pass: the run's directory and a work root left where it was, by the login's exact values,
    when nothing works there any more (the launcher and uv are left alone while it waits)."""
    post: dict = {"settle": None, "after_move": None, "environments": [], "environments_error": None,
                  "sweep": None, "move_error": None, "exact": None, "errors": []}

    def step(name: str, call):
        try:
            return call(), None
        except BaseException as exc:  # noqa: BLE001 - recorded; the next step still runs
            error = f"{name}: {type(exc).__name__}: {exc}"
            post["errors"].append(error)
            return None, error

    if work is not None and work.exists() and not kept.exists():
        exclude = launcher.exclude() if launcher is not None else set()
        post["settle"], _ = step("settle", lambda: settle(work, timeout, tracked, exclude))
        environments, post["environments_error"] = step("environments", lambda: remove_environments(work))
        post["environments"] = [{**e, "path": f"work/{e['path']}"} for e in environments or []]
        post["sweep"], _ = step("sweep", lambda: sweep(work, login, "work/"))
        _, post["move_error"] = step("move", lambda: shutil.move(str(work), str(kept)))
        if post["move_error"] is None and not kept.is_dir():
            post["move_error"] = "the work root was not moved into the run"
        post["after_move"], _ = step("settle after the move", lambda: settle([work, kept], min(timeout, 60),
                                                                             tracked, exclude))
    if launcher is not None:
        post["exact"], _ = step("exact", lambda: launcher.mask("all"))
    return post


def scan_run(run_dir: Path, left: Path | None, ids: set, post: dict) -> dict:
    """The run's secret scan: its directory, the work root when it could not be moved (searched where
    it was), what the sweep before the move removed, and whether a process could still write."""
    scan = scan_tree(run_dir, login=ids)
    scan["work_root_left"] = None
    parts = [scan]
    if left is not None:
        extra = scan_tree(left, login=ids, prefix="work_root_left/")
        parts.append(extra)
        scan["work_root_left"] = str(left)
        for key in ("files", "bytes", "findings_count"):
            scan[key] += extra[key]
        scan["masked"] = scan["masked"] or extra["masked"]
    pre = post.get("sweep") or {}
    for key in ("findings", "links", "outside_links_removed", "hard_links_removed", "unreadable",
                "masked_files"):
        before = pre.get("errors" if key == "unreadable" else key) or []
        scan[key] = [*before, *(item for part in parts for item in part.get(key) or [])][:MAX_LISTED]
    problems = set(scan["problems"]) | {p for part in parts for p in part["problems"]}
    problems |= {name for name, key in (("outside_links", "outside_links_removed"),
                                        ("hard_links", "hard_links_removed"), ("unreadable", "errors"))
                 if pre.get(key)}
    for key in ("settle", "after_move"):
        settled = post.get(key)
        if settled is not None and not settled.get("checked"):
            problems.add("processes_unchecked")
        elif settled is not None and settled.get("left"):
            problems.add("writers_left")
    if post.get("errors"):
        problems.add("after_exit_errors")
    # A backstop: nothing but the scan's own masking may change a file once the scan has started.
    since = scan.pop("started_at")
    skip = {*scan["masked_files"], "run.json"}
    late = written_since(run_dir, since, skip) + (written_since(left, since, skip, "work_root_left/")
                                                    if left is not None else [])
    scan["written_after_scan"] = late[:MAX_LISTED]
    if late:
        problems.add("written_after_scan")
    scan["problems"] = sorted(problems)
    scan["status"] = "failed" if problems else "passed"
    return scan


def exact_record(expected: bool, reason: str | None = None) -> dict:
    """secret_scan.exact before the launcher's passes: not_applicable when no login was used (or aelix
    never started), else not_run until a pass reports."""
    if not expected:
        return {"status": "not_applicable", "reason": reason or "the model uses no aelix login",
                "what": EXACT_WHAT}
    return {"status": "not_run", "files": 0, "findings": 0, "unreadable": [], "masked_files": [], "renamed": [],
            "links_rewritten": [], "values_compared": 0, "passes": [], "not_run": [], "errors": [],
            "problems": ["exact_not_run"], "reason": reason, "what": EXACT_WHAT}


def merge_exact(exact: dict, report: dict | None, scope: str, reason: str | None = None) -> None:
    """One pass's report (None: the pass was not run) into secret_scan.exact, and its status: passed,
    failed (exact_values: a value was found and masked; exact_unreadable; exact_error) or not_run
    (exact_not_run: no pass, or one that compared no value)."""
    if exact.get("status") == "not_applicable":
        return
    if report is None:
        exact["not_run"].append(scope)
        exact["reason"] = exact.get("reason") or reason
    else:
        exact["passes"].append(scope)
        if report.get("error"):
            exact["errors"].append(f"{scope}: {report['error']}")
        if (scope == "all" and "." in (report.get("missing") or [])) or \
                (scope == "record" and "run.json" in (report.get("missing") or [])):
            exact["errors"].append(f"{scope}: what it was to mask was not there")
        exact["files"] += int(report.get("files") or 0)
        exact["findings"] += int(report.get("findings") or 0)
        for key in ("masked_files", "renamed", "links_rewritten", "unreadable"):
            exact[key] = [*exact[key], *(report.get(key) or [])][:MAX_LISTED]
        exact["values_compared"] = max(exact["values_compared"], int(report.get("values") or 0))
    problems = []
    if exact["not_run"] or "all" not in exact["passes"] or not exact["values_compared"]:
        problems.append("exact_not_run")
        if exact["passes"] and not exact["values_compared"] and not exact.get("reason"):
            exact["reason"] = (f"the launcher held no credential value of {EXACT_MIN_LENGTH} characters or "
                               "more to compare")
    if exact["errors"]:
        problems.append("exact_error")
    if exact["findings"]:
        problems.append("exact_values")
    if exact["unreadable"]:
        problems.append("exact_unreadable")
    exact["problems"] = problems
    exact["status"] = "passed" if not problems else "not_run" if problems == ["exact_not_run"] else "failed"


def mask_record(record: dict, run_dir: Path, launcher: LauncherLink) -> None:
    """run.json as written, masked once more by the launcher: what the harness learned before the
    launcher's first pass (a process's command line, a removed link's target) is in it."""
    report = launcher.mask("record")
    if report is not None and report.get("findings"):  # the record held a value: read it back
        with contextlib.suppress(OSError, UsageError):
            masked = read_json(run_dir / "run.json")
            record.clear()
            record.update(masked)
    scan = record.setdefault("secret_scan", {"problems": []})
    exact = scan.get("exact") if isinstance(scan.get("exact"), dict) and "passes" in scan["exact"] \
        else exact_record(True)
    scan["exact"] = exact
    merge_exact(exact, report, "record", launcher.why_not())
    scan["problems"] = sorted({*(scan.get("problems") or []), *exact["problems"]})
    scan["status"] = "failed" if scan["problems"] else "passed"
    mark_secret_scan(record)
    write_json(run_dir / "run.json", record)
    check_record(run_dir / "run.json", record)


def close_run(record: dict, run_dir: Path, work: Path | None, kept: Path, post: dict | None,
              failure: BaseException | None, *, secrets: list[str], auth_file: Path | None,
              tracked: dict | None = None, launcher: LauncherLink | None = None,
              exact_expected: bool = False, exact_reason: str | None = None) -> dict:
    """Secure, redact, search and record a run, whatever happened before: the end of every run, and
    `scan OUT` for one whose harness was killed. The secret scan runs even when a step before it
    failed; the login's metadata is measured once more as the very last step. exact_expected: aelix
    ran with an aelix login, so the launcher's masking by the login's exact values belongs to the
    scan (its first pass in secure_work, its second on run.json at the end; then the launcher ends);
    exact_reason: why it cannot run (no launcher link)."""
    login = record.get("login") or {}
    ids = login_ids(login_stat(auth_file), login.get("before"), login.get("after_exit"))
    try:
        return close_run_steps(record, run_dir, work, kept, post, failure, secrets=secrets, auth_file=auth_file,
                               tracked=tracked, launcher=launcher, exact_expected=exact_expected,
                               exact_reason=exact_reason, ids=ids)
    finally:
        if launcher is not None:
            launcher.finish()


def close_run_steps(record: dict, run_dir: Path, work: Path | None, kept: Path, post: dict | None,
                    failure: BaseException | None, *, secrets: list[str], auth_file: Path | None,
                    tracked: dict | None, launcher: LauncherLink | None, exact_expected: bool,
                    exact_reason: str | None, ids: set) -> dict:
    if post is None:
        post = secure_work(work, kept, login=ids, tracked=tracked,
                           timeout=0 if record.get("stop") else SETTLE_TIMEOUT, launcher=launcher)
    if post["settle"] is not None:
        record["after_exit"] = {**post["settle"], "after_move": post["after_move"]}
    if post["errors"]:
        record["after_exit_errors"] = post["errors"]
    record["environments_removed"] = {
        "removed": post["environments"], "error": post["environments_error"],
        "what": "the project's Python environment (.venv) the kit made with uv: removed after aelix "
                "exited, before the work root is moved, never following a link; uv.lock stays and makes "
                "it again"}
    if failure is not None and "outcome" not in record:
        record.update(outcome="error", harness_error=f"{type(failure).__name__}: {failure}")
    left = work if work is not None and work.exists() and not kept.exists() else None
    scan = None
    try:
        try:
            found = redact_tree(run_dir, secrets, ids)
            if left is not None:
                found += [f"work_root_left/{f}" for f in redact_tree(left, secrets, ids)]
        except OSError as exc:
            found = []
            record["secret_redaction_error"] = f"{type(exc).__name__}: {exc}"
        clean = redact_value(record, secrets)
        if clean != record:
            found = sorted({*found, "run.json"})
        record.clear()
        record.update(clean)
        if found:
            record["secret_redacted"] = found
        record.pop("secret_scan", None)
        record.pop("provisional", None)
        write_json(run_dir / "run.json", record)
    finally:
        try:
            scan = scan_run(run_dir, left, ids, post)
        except BaseException as exc:  # noqa: BLE001 - not searched is not passed
            scan = {"status": "failed", "problems": ["scan_error"], "error": f"{type(exc).__name__}: {exc}",
                    "findings": [], "masked_files": []}
        if "run.json" in (scan.get("masked_files") or []):  # the record held a shape: read it back
            with contextlib.suppress(OSError, UsageError):
                masked = read_json(run_dir / "run.json")
                record.clear()
                record.update(masked)
        if record.get("secret_redaction_error"):
            scan["problems"] = sorted({*scan["problems"], "redaction_error"})
        if record.get("login_mentions"):
            scan["problems"] = sorted({*scan["problems"], "names_login"})
        exact = exact_record(exact_expected, exact_reason)
        merge_exact(exact, post.get("exact"), "all",
                    exact_reason or (launcher.why_not() if launcher is not None else None))
        scan["exact"] = exact
        scan["problems"] = sorted({*scan["problems"], *(exact.get("problems") or [])})
        if auth_file is not None:  # the very last measurement: nothing after aelix may change the login
            final = login_stat(auth_file)
            entry = record.setdefault("login", {"file": str(auth_file)})
            entry["final"] = final
            ref = entry.get("after_exit") or entry.get("before")
            if final and final.get("exists") and ref and ref.get("exists") and \
                    (final["mtime_ns"], final["inode"]) != (ref["mtime_ns"], ref["inode"]):
                scan["problems"] = sorted({*scan["problems"], "login_changed_after_exit"})
            if final and final.get("exists") and final["links"] > 1:
                scan["problems"] = sorted({*scan["problems"], "login_has_other_names"})
        scan["status"] = "failed" if scan["problems"] else "passed"
        record["secret_scan"] = mask_strings(scan)
        mark_secret_scan(record)
        write_json(run_dir / "run.json", record)
        check_record(run_dir / "run.json", record)
        if launcher is not None and exact_expected:
            mask_record(record, run_dir, launcher)
    return record


def check_record(path: Path, record: dict) -> None:
    """The record as written, searched once more (the scan's own result is written after the scan)."""
    data = bytearray(path.read_bytes())
    if credential_findings(data, mask=True):
        replace_bytes(path, bytes(data))
        record.clear()
        record.update(json.loads(data.decode("utf-8")))
        scan = record.setdefault("secret_scan", {})
        scan["problems"] = sorted({*(scan.get("problems") or []), "record"})
        scan["status"] = "failed"
        mark_secret_scan(record)
        write_json(path, record)


def run_one(session: Session, task: dict, repeat: int) -> dict:
    key = run_key(session, task["id"], repeat)
    run_dir = session.out / "runs" / key
    if run_dir.exists():
        raise UsageError(f"{run_dir} exists already: use a new --out or another repeat range")
    run_dir.mkdir(parents=True)
    record = base_record(session, task, repeat, key)
    write_json(run_dir / "run.json", provisional(record))
    reason = not_run_reason(session, task)
    if reason:
        record.update(outcome="not_run", reason=reason)
        if reason.startswith(("api_key_missing", "aelix_login_missing")):
            record["command_to_run"] = command_line(session, task, repeat)
        if reason.startswith("aelix_login_missing"):
            record["login_to_run"] = LOGIN_HOWTO
        write_json(run_dir / "run.json", record)
        record["secret_scan"] = mask_strings(scan_tree(run_dir))  # nothing of a model is here; searched
        record["secret_scan"].pop("started_at", None)
        record["secret_scan"]["exact"] = exact_record(False, "aelix did not run")
        write_json(run_dir / "run.json", record)
        return record
    tools = tools_of(session)
    work: Path | None = None
    kept = run_dir / "work"
    failure: BaseException | None = None
    post: dict | None = None
    tracked: dict | None = None
    # The launcher's link (POSIX, a model that uses an aelix login): its lifeline (the harness holds
    # the write end; when the harness dies, even by SIGKILL, the launcher reads EOF, stops aelix and
    # what the model started and masks the run on its own) and its reports.
    link = LauncherLink() if session.auth_file is not None and os.name != "nt" else None
    started_aelix = False
    try:
        fixture = ensure_fixture(session.config.fixtures[task["fixture"]], session.seed, session.fixtures_root)
        record["fixture"]["digest"] = read_json(fixture / "manifest.json").get("digest")
        # The model's work root is outside OUT (where the fixtures, with their answers, and the other
        # runs are) and holds only project/ and input/; it is kept as work/ once aelix has exited.
        session.work_parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"aelix-eval-{task['id']}-", dir=session.work_parent))
        record["work"] = {"root": str(work), "kept_at": "work",
                          "what": "the model's work root during the run, outside OUT; moved into the "
                                  "run's directory when aelix exited"}
        write_json(run_dir / "run.json", provisional(record))  # names the work root for `scan OUT`
        (run_dir / "home").mkdir()
        env_probe, _ = run_env(session, run_dir, work / "project")
        project = prepare_work(task, fixture, work, Path(session.checkout["path"]) / "src", env_probe,
                               kit=has_kit(session))
        if session.proposal == "always_on":
            record["proposal_mode"] = set_proposal_mode(Path(session.checkout["path"]) / "src", project, env_probe)
        if has_kit(session):
            record["context_block"] = context_block_of(Path(session.checkout["path"]) / "src", project,
                                                       env_probe, run_dir)
        before = snapshot(project)
        requests, extra_checks = task_requests(session, task, work)
        tools_argv, extension = surface_argv(session)
        extension_path = Path(session.checkout["path"]) / extension if extension is not None else None
        env, set_vars = run_env(session, run_dir, project, kit=has_kit(session))
        (run_dir / "agent").mkdir(parents=True, exist_ok=True)
        scripted = session.model["serving"].get("backend") == "fake"
        timeout = session.timeout or task["timeout_s"]
        fake = ScriptedModel(load_script(task["id"], session.script), tools) if scripted else None
        with fake or contextlib.nullcontext():
            write_models_json(session.model, run_dir / "agent", fake.port if fake else None)
            argv = build_argv(session, requests, extension_path, tools_argv, link,
                              (run_dir, work) if link is not None else ())
            write_json(run_dir / "command.json", {
                "argv": argv, "cwd": str(project), "set_env": set_vars,
                "unset_env": "AELIX_*, PI_*, " + ", ".join(ISOLATED_KEYS) + " and every name that holds a "
                             "credential (stripped_env)",
                "stripped_env": stripped_env(key_names(session)),
                "inherited_env": "the rest of the caller's environment; values are not recorded",
                "secret_env": session.model.get("api_key_env"),
                "login_file": None if session.auth_file is None else str(session.auth_file)})
            record.update(requests=requests, before=before, started_at=now_iso())
            if session.auth_file is not None:
                first = login_stat(session.auth_file)
                record["login"] = {"file": str(session.auth_file), "mtime_before": first.get("mtime"),
                                   "before": first,
                                   "what": "the file's metadata only (its contents are never read "
                                           "here); aelix replaces it in place when it refreshes a "
                                           "token, and so may another aelix of the caller"}
            write_json(run_dir / "run.json", provisional(record))
            ports = processes()
            started = time.perf_counter()
            timed_out = False
            with open(run_dir / "events.jsonl", "wb") as stdout, \
                    open(run_dir / "stderr.txt", "wb") as stderr:
                try:
                    proc = subprocess.Popen(argv, cwd=project, env=env, stdin=subprocess.DEVNULL,
                                            stdout=stdout, stderr=stderr, **ports.spawn_kwargs(),
                                            **({"pass_fds": link.child_fds()} if link else {}))
                except OSError as exc:
                    raise HarnessError(f"aelix could not be started: {exc}") from exc
                started_aelix = True
                if link is not None:
                    link.spawned(proc)
                with Descendants(proc) as tree:
                    try:
                        # With the launcher: until it says aelix's main has returned (it then
                        # waits to mask the run, after the work root is secured).
                        code = link.wait(timeout) if link is not None else proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        with deferred():
                            record["stop"] = stop_group(proc, link=link)
                        code = link.code() if link is not None else proc.returncode
                    except BaseException:
                        with deferred():
                            record["stop"] = stop_group(proc, link=link)
                        raise
                    finally:
                        tracked = tree.seen
            elapsed = round(time.perf_counter() - started, 1)
            if session.auth_file is not None:
                after = login_stat(session.auth_file)
                record["login"].update(mtime_after=after.get("mtime"), after_exit=after,
                                       changed=after.get("mtime_ns") != record["login"]["before"].get("mtime_ns")
                                       or after.get("inode") != record["login"]["before"].get("inode"),
                                       agent_dir_store=(run_dir / "agent" / "auth.json").exists())
            if fake:
                write_json(run_dir / "fake-requests.json", fake.requests)
        if fake and fake.requests:
            first = fake.requests[0]
            offered = {"tools": first["tools"], "source": "the dry run's first request"}
            body = {"source": "the dry run's first request: the fake server reads aelix's request body",
                    "unit": "UTF-8 bytes", "tool_schema_bytes": first["tool_schema_bytes"],
                    "system_prompt_bytes": first["system_prompt_bytes"],
                    "request_bytes": first["body_bytes"], "messages": first["messages"]}
        else:
            offered = ({"tools": tools_argv[1].split(","), "source": "--tools"} if tools_argv else
                       {"tools": None, "source": "aelix's default list: aelix does not report the "
                                                 "tools it sends; a dry run with the same aelix shows it"})
            body = {"tool_schema_bytes": not_measured(FULL_INPUT_NOT_MEASURED),
                    "system_prompt_bytes": not_measured(FULL_INPUT_NOT_MEASURED)}
        record.update(finished_at=now_iso(), elapsed_s=elapsed, exit_code=code, timed_out=timed_out,
                      timeout_s=timeout, offered_tools=offered, request_body=body)
        with deferred():  # aelix stopped (timeout): what it left running is stopped, not waited for
            post = secure_work(work, kept, login=login_ids(login_stat(session.auth_file),
                                                           (record.get("login") or {}).get("before")),
                               tracked=tracked, timeout=0 if record.get("stop") else SETTLE_TIMEOUT,
                               launcher=link)
        raise_if_stopped()
        if not kept.is_dir():
            raise HarnessError(f"the work root could not be moved into the run ({post['move_error']}): it "
                               f"was searched where it is and left at {work}")
        finish_record(session, task, record, run_dir, kept / "project", extra_checks, scripted)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        with deferred():
            # The login's exact values: masked by the launcher when aelix ran with the login.
            exact_reason = ("the model uses no aelix login" if session.auth_file is None else
                            "aelix did not start" if not started_aelix else None if link is not None else
                            "no channel to the launcher on Windows (no inherited pipe): the run was not "
                            "masked by the login's exact values")
            try:
                close_run(record, run_dir, work, kept, post, failure, secrets=session.secrets,
                          auth_file=session.auth_file, tracked=tracked,
                          launcher=link if started_aelix else None,
                          exact_expected=started_aelix and session.auth_file is not None,
                          exact_reason=exact_reason)
            finally:
                if link is not None:
                    link.close()
    return record


def mark_secret_scan(record: dict) -> None:
    """A failed secret scan, or processes that could still write after aelix exited, are for a person
    to look at."""
    review = (record.get("score") or {}).get("needs_review")
    if not isinstance(review, list):
        return
    scan = record.get("secret_scan") or {}
    if scan.get("status") == "failed" and "secret_scan" not in review:
        review.append("secret_scan")
    if {"writers_left", "processes_unchecked"} & set(scan.get("problems") or []) and "after_exit" not in review:
        review.append("after_exit")


def finish_record(session: Session, task: dict, record: dict, run_dir: Path, project: Path,
                  extra_checks: list, scripted: bool) -> None:
    """Read the events and the project, count and score. `score` calls it again on a stored run."""
    tools = GENERATIONS[record["kit"]["generation"]]["tools"]
    lines = (run_dir / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    tr = parse_events(lines)
    state = changes(project, record["before"])
    calls = tool_summary(tr.calls, tools)
    ev = Evidence(text="\n\n".join(tr.answers), last_turn=tr.answers[-1] if tr.answers else "",
                  calls=tr.calls, tools=tools, state=state, existing_ids=set(state["existing_ids"]),
                  project=project)
    answer = session.config.answers[task["id"]]
    result = score(answer, ev, session.config.words, extra_checks)
    # Paths the model's tool calls used outside its work root (where it ran).
    root = (record.get("work") or {}).get("root") or str(run_dir / "work")
    roots = [root, os.path.realpath(root)]
    outside = []
    for call in tr.calls:
        paths = outside_paths(call.get("args") or {}, roots, os.path.join(root, "project"))
        if paths:
            outside.append({"id": call.get("id"), "name": call.get("name"), "paths": paths})
    if outside:
        result["needs_review"].append("outside_work_root")
    # Tool calls that name the caller's aelix state or a login file (also as $HOME/.aelix or a path
    # written out in full): the model may have read a credential, which the shape scan may miss.
    auth_file = session.auth_file or ((record.get("model") or {}).get("auth") or {}).get("file")
    mentions = login_mentions(tr.calls, str(auth_file) if auth_file else None)
    if mentions:
        result["needs_review"].append("names_login")
    if state.get("outside_links"):
        result["needs_review"].append("outside_link")
    stop = tr.turns[-1]["stop_reason"] if tr.turns else None
    if record.get("timed_out"):
        outcome = "timeout"
    elif record.get("exit_code") != 0 or not tr.ended or stop in ("error", "aborted") or \
            len(tr.turns) < len(record["requests"]):
        outcome = "error"
    else:
        outcome = result["result"]
    unusable = login_unusable(session, tr, run_dir) if outcome == "error" else None
    if unusable:
        outcome = "not_run"
        record.update(reason=unusable, command_to_run=command_line(session, task, record.get("repeat", 1)),
                      login_to_run=LOGIN_HOWTO)
    refused = provider_refused(tr) if outcome == "error" and not unusable else None
    if refused:
        outcome = "not_run"
        record.update(reason=refused, command_to_run=command_line(session, task, record.get("repeat", 1)))
    observed = sorted({m.get("model") for m in tr.messages if m.get("model")})
    proposal_turn = len(record["requests"]) if session.proposal == "requested" else None
    record.update(
        outcome=outcome, session=tr.session,
        turns=[{k: t[k] for k in ("request", "answer", "messages", "stop_reason", "error")}
               for t in tr.turns],
        model_requests=len(tr.messages), observed_models=observed, auto_retries=tr.retries,
        compactions=tr.compactions, unparsed_event_lines=tr.unparsed,
        tool_calls=[{**{k: c.get(k) for k in ("id", "name", "role", "args", "is_error", "error_class",
                                                "turn")},
                     "result_head": (c.get("result") or "")[:400]} for c in tr.calls],
        tool_summary=calls, outside_work_root=outside, login_mentions=mentions,
        tokens=token_totals(tr.messages, session.model["api"], scripted=scripted,
                            proposal_turn=proposal_turn),
        changes={k: v for k, v in state.items() if k != "existing_ids"},
        score=result,
        proposal_mentioned=bool(contains_any(ev.text if session.proposal != "requested" else
                                             "\n\n".join(tr.answers[:-1]),
                                             ["재사용", "skill", "스킬", "다시 쓸", "reuse"])),
        errors_recovered=recovered(state))
    mark_secret_scan(record)


# aelix's text for a login it could not use: the API refused the credential, or the refresh failed.
AUTH_ERROR = re.compile(r"\b40[13]\b|unauthori[sz]ed|forbidden|invalid[_ ]?(?:grant|api[_ ]?key|token)|"
                        r"refresh|re-?authenticat|\blog ?in\b|credential|no api key|api key|access token|"
                        r"token (?:has )?expired", re.IGNORECASE)


# The provider's refusal for want of credits (OpenRouter: "Error code: 402 - … requires more credits"):
# on 2026-10-31 it ended 21 runs of the campaign before the model answered once.
PROVIDER_REFUSAL = re.compile(r"\bError code: 402\b")


def provider_refused(tr: Transcript) -> str | None:
    """The not_run reason when the provider refused the run before the model did anything: no tool
    call, no answer, and a turn that ended in the provider's refusal. A run the model had worked in
    when the refusal came stays an error."""
    if tr.calls or any(a.strip() for a in tr.answers) or any(usage_reported(m) for m in tr.messages):
        return None
    for turn in tr.turns:
        text = str(turn.get("error") or "")
        if PROVIDER_REFUSAL.search(text):
            return f"provider_refused: the provider refused the request before the model ran: {mask_text(text[:300])}"
    return None


def login_unusable(session: Session, tr: Transcript, run_dir: Path) -> str | None:
    """The not_run reason when a run that uses an aelix login failed because the login could not be
    used (a revoked or expired refresh token): the first turn ended in an error before any request
    reported usage, and aelix's error names authentication. A precondition, not the model's data."""
    if session.model.get("auth") is None or any(usage_reported(m) for m in tr.messages):
        return None
    if tr.turns:
        if tr.turns[0].get("stop_reason") not in ("error", None):
            return None
        text = tr.turns[0].get("error") or ""
    else:
        text = ""
    if not AUTH_ERROR.search(text):
        with contextlib.suppress(OSError):
            tail = (run_dir / "stderr.txt").read_bytes()[-2000:].decode("utf-8", errors="replace")
            text = tail if AUTH_ERROR.search(tail) else ""
    if not text:
        return None
    line = next((ln.strip() for ln in reversed(text.splitlines()) if AUTH_ERROR.search(ln)), text.strip())
    return f"aelix_login_unusable: aelix could not use the login: {mask_text(line[:300])}; {LOGIN_HOWTO}"


def all_strings(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from all_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from all_strings(v)
    elif isinstance(value, str):
        yield value


def login_mentions(calls: list, auth_file: str | None) -> list[dict]:
    """Tool calls whose arguments name the caller's aelix state or a login file: '.aelix', 'auth.json',
    the login file's path or its directory (a bash string included, whatever form of HOME it uses)."""
    needles = [".aelix", "auth.json"]
    if auth_file:
        needles += [auth_file, os.path.dirname(auth_file)]
    found = []
    for call in calls:
        texts = [t.lower() for t in all_strings(call.get("args") or {})]
        hits = sorted({n for n in needles if any(n.lower() in t for t in texts)})
        if hits:
            found.append({"id": call.get("id"), "name": call.get("name"), "names": hits})
    return found


def recovered(state: dict) -> dict:
    failed = [r for r in state["new_runs"] if r.get("status") not in ("ok", None)]
    later_ok = [f for f in failed if f.get("script") and any(
        r["status"] == "ok" and r["id"] > f["id"] and r.get("script") == f["script"]
        for r in state["new_runs"])]
    return {"failed_runs": len(failed), "fixed_by_a_later_run": len(later_ok)}


def command_line(session: Session, task: dict, repeat: int) -> str:
    env = session.model.get("api_key_env")
    parts = ["uv", "run", "--locked", "python", "tools/model_eval.py", "run", "--model",
             session.model["id"], "--task", task["id"], "--surface", session.surface, "--proposal",
             session.proposal, "--out", "<new directory>"]
    if session.auth_file is not None and session.auth_file != default_aelix_auth():
        parts += ["--aelix-auth", str(session.auth_file)]
    prefix = f"{env}=<key> " if env else ""
    return prefix + shlex.join(parts)


# --- summary --------------------------------------------------------------------------------------

def token_value(tokens: dict | None, name: str):
    value = (tokens or {}).get(name) or {}
    return value.get("value") if value.get("status") == "measured" else None


def token_cell(tokens: dict | None, name: str) -> dict:
    """One token field for the summary: its value only when measured, and whether it is partial."""
    value = (tokens or {}).get(name) or {}
    if value.get("status") != "measured":
        return {"status": value.get("status") or "not_measured", "value": None,
                "reason": value.get("reason")}
    return {"status": "measured", "value": value.get("value"), "partial": bool(value.get("partial"))}


def stored_runs(out: Path) -> list[tuple[Path, dict | None]]:
    """Every run directory under OUT/runs with its record (None when it has no readable run.json: the
    harness was killed before it wrote one)."""
    runs = out / "runs"
    found = []
    for run_dir in sorted(p for p in runs.iterdir() if p.is_dir() and not p.is_symlink()) if runs.is_dir() else []:
        record = None
        with contextlib.suppress(UsageError):
            record = read_json(run_dir / "run.json")
        found.append((run_dir, record if isinstance(record, dict) else None))
    return found


def scan_status(record: dict | None) -> str:
    return ((record or {}).get("secret_scan") or {}).get("status") or "not_scanned"


def summarize(out: Path) -> dict:
    runs = []
    for run_dir, record in stored_runs(out):
        path = run_dir / "run.json"
        if record is None:  # listed, so that a run the harness never finished is not lost
            record = {"run": run_dir.name, "outcome": "error",
                      "reason": "no run.json: the harness stopped before it wrote the record; run "
                                "`model_eval.py scan OUT`"}
        sc = record.get("score") or {}
        tokens = record.get("tokens")
        conditions = record.get("conditions") or {}
        runs.append({
            "run": record.get("run") or run_dir.name, "task": record.get("task"),
            "model": (record.get("model") or {}).get("config"),
            "surface": conditions.get("surface"), "proposal": conditions.get("proposal"),
            "kit": conditions.get("kit", True),
            "repeat": record.get("repeat"), "outcome": record.get("outcome"),
            "answer_result": sc.get("answer_result"),
            "reason": record.get("reason") or (record.get("harness_error") if record.get("provisional") else None),
            "answer_status": (record.get("answer") or {}).get("status"), "fact_recall": sc.get("fact_recall"),
            "limits": sc.get("limits"),
            "evidence": {k: (sc.get("evidence") or {}).get(k) for k in ("passed", "total")},
            "critical": sc.get("critical"), "needs_review": sc.get("needs_review"),
            "fabricated_ids": sc.get("fabricated_ids"),
            "tool_calls": (record.get("tool_summary") or {}).get("calls"),
            "argument_errors": (record.get("tool_summary") or {}).get("argument_errors"),
            "tokens_complete": (tokens or {}).get("complete") if tokens else None,
            "input_uncached": token_cell(tokens, "input_uncached"),
            "input_cached": token_cell(tokens, "input_cached"),
            "output": token_cell(tokens, "output"),
            "secret_scan": scan_status(record),
            "elapsed_s": record.get("elapsed_s"), "record": str(path.relative_to(out))})
    groups: dict[tuple, list] = {}
    for run in runs:
        groups.setdefault((run["model"], run["task"], run["surface"], run["proposal"]), []).append(run)
    aggregate = []
    for (model, task, surface, proposal), items in sorted(groups.items(), key=lambda g: tuple(map(str, g[0]))):
        executed = [r for r in items if r["outcome"] != "not_run"]
        # Only runs whose every request reported usage, with all three fields measured, give a
        # task's total: a partial count is not a cost.
        whole = [r for r in executed if r["tokens_complete"] and all(
            r[k]["value"] is not None for k in ("input_uncached", "input_cached", "output"))]
        totals = [r["input_uncached"]["value"] + r["input_cached"]["value"] + r["output"]["value"]
                  for r in whole]
        aggregate.append({"model": model, "task": task, "surface": surface, "proposal": proposal,
                          "runs": len(items), "not_run": len(items) - len(executed),
                          "success": sum(r["outcome"] == "success" for r in items),
                          # What the answer says only (ANSWER_KINDS): the measure aelix alone shares.
                          "answer_success": sum(r["outcome"] in ("success", "failure")
                                                and r["answer_result"] == "success" for r in items),
                          "success_without_review": sum(r["outcome"] == "success" and not r["needs_review"]
                                                        for r in items),
                          "success_with_clean_scan": sum(r["outcome"] == "success" and r["secret_scan"] == "passed"
                                                         for r in items),
                          "needs_review": sum(bool(r["needs_review"]) for r in executed),
                          "fabricated_ids": sum(bool(r["fabricated_ids"]) for r in executed),
                          "critical": sum(bool(r["critical"]) for r in executed),
                          "argument_errors": sum(r["argument_errors"] or 0 for r in executed),
                          "secret_scan_failed": sum(r["secret_scan"] == "failed" for r in executed),
                          "secret_scan_not_scanned": sum(r["secret_scan"] == "not_scanned" for r in items),
                          "median_total_tokens": statistics.median(totals) if totals else None,
                          "token_runs": len(whole),
                          "partial_usage_runs": len(executed) - len(whole)})
    summary = {"schema": SUMMARY_SCHEMA, "written_at": now_iso(), "runs": runs, "aggregate": aggregate,
               "not_scanned": [r["run"] for r in runs if r["secret_scan"] == "not_scanned"],
               "note": "success counts every executed run in the denominator, also one whose secret scan "
                       "failed (success_with_clean_scan leaves those out); answers marked draft give "
                       "provisional scores; a run with needs_review is confirmed by a person; "
                       "median_total_tokens uses only runs whose every request reported usage "
                       "(token_runs), partial_usage_runs are left out of it; not_scanned lists runs whose "
                       "files were never searched (run `model_eval.py scan OUT`)"}
    write_json(out / "summary.json", summary)
    write_text(out / "summary.md", render_summary(summary))
    return summary


def render_summary(summary: dict) -> str:
    lines = ["# Model evaluation runs", "", f"Written {summary['written_at']}. Generated from summary.json.",
             "", "| run | outcome | answer | facts | evidence | critical | review | fabricated ids | calls "
             "| arg errors | input uncached | input cached | output | s | secret scan |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]

    def cell(v):
        return "—" if v is None else (f"{v:,}" if isinstance(v, int) else str(v))

    def tokens(t):
        if not t or t.get("value") is None:
            return "—"
        return f"{t['value']:,}" + (" (partial)" if t.get("partial") else "")

    for r in summary["runs"]:
        name = str(r["outcome"]) + (f" ({r['reason']})" if r.get("reason") else "")
        if r["outcome"] == "not_run" or r.get("answer_status") is None:  # no record, or nothing run
            lines.append("| " + " | ".join([r["run"], name, str(r["answer_status"] or "—")] + ["—"] * 11
                                           + [cell(r.get("secret_scan"))]) + " |")
            continue
        facts = r["fact_recall"] or {}
        ev = r["evidence"] or {}
        lines.append("| " + " | ".join([
            r["run"], name, r["answer_status"], f"{facts.get('passed', '—')}/{facts.get('total', '—')}",
            f"{ev.get('passed', '—')}/{ev.get('total', '—')}", ", ".join(r["critical"] or []) or "0",
            ", ".join(r["needs_review"] or []) or "—", ", ".join(r["fabricated_ids"] or []) or "—",
            cell(r["tool_calls"]), cell(r["argument_errors"]), tokens(r["input_uncached"]),
            tokens(r["input_cached"]), tokens(r["output"]), cell(r["elapsed_s"]),
            cell(r.get("secret_scan"))]) + " |")
    return "\n".join(lines) + "\n"


# --- commands -------------------------------------------------------------------------------------

def prepare_out(out: Path) -> None:
    if out.exists() and not out.is_dir():
        raise UsageError(f"--out {out} is not a directory")
    if out.exists() and any(out.iterdir()) and not (out / "summary.json").is_file():
        raise UsageError(f"--out {out} is not empty and holds no earlier summary.json: nothing was "
                         "deleted; give a new or empty directory, or an earlier output to add runs to")
    out.mkdir(parents=True, exist_ok=True)
    (out / "iso" / "trust").mkdir(parents=True, exist_ok=True)


def validate_against_fixtures(config: Config, fixtures_root: Path, seed: int = 0) -> list[str]:
    """Every id an answer cites exists in the fixture it is written for."""
    problems = []
    for task_id, answer in sorted(config.answers.items()):
        task = config.tasks[task_id]
        spec = config.fixtures[task["fixture"]]
        if any(answer["fixture"].get(k) != spec.get(k) for k in ("kind", "scale", "seed",
                                                                   "generator_version")):
            problems.append(f"{task_id}: the answer is for {answer['fixture']}, the task's fixture is {spec}")
        fixture = ensure_fixture(spec, seed, fixtures_root)
        project = fixture / "project"
        records = {str(e.get("id")) for e in journal(project)}
        runs = {p.name for p in (project / "runs").iterdir() if p.is_dir()}
        charts = set()
        for run in runs:
            with contextlib.suppress(OSError, json.JSONDecodeError):
                data = json.loads((project / "runs" / run / "run.json").read_text(encoding="utf-8"))
                charts |= {c["id"] for c in read_model().run_visualizations(data)}
        cited = set(answer.get("evidence_ids", []))
        for check in answer["checks"]:
            if check["type"] in ("ids_all", "ids_any"):
                cited |= set(check["values"])
            if check["type"] == "stated_as_current" and check.get("pattern_kind") == "ids":
                cited |= set(check["patterns"])
            if check["type"] == "stated_as_current":
                for closed, resolver in (check.get("resolved_by") or {}).items():
                    cited |= {closed, str(resolver)}
            if check["type"] == "claim_verdicts":
                cited |= set(check["claims"])
            if check["type"] == "cooccur":
                words = list(check.get("all") or []) + [w for g in check.get("any") or [] for w in g]
                cited |= {v for v in words if RUN_ID.fullmatch(v)}
            if check.get("chart_ids"):
                cited |= {f"chart:{v}" for v in check["values"]}
        if task["setup"] == "fresh_with_input":
            cited = set()  # nothing to cite before the run: the checks are on what it makes
        for item in sorted(cited):
            if item.startswith("chart:"):
                ok = item[6:] in charts
            elif RECORD_ID.fullmatch(item):
                ok = item in records
            elif RUN_ID.fullmatch(item):
                ok = item in runs
            else:
                ok = (project / item).exists()
            if not ok:
                problems.append(f"{task_id}: {item} is not in the {fixture.name} fixture")
    return problems


def cmd_validate(args) -> int:
    config = load_config()
    print(f"configuration ok: {len(config.tasks)} tasks, {len(config.answers)} answers "
          f"({sum(a['status'] == 'draft' for a in config.answers.values())} draft), "
          f"{len(config.models)} models")
    if args.fixtures:
        problems = validate_against_fixtures(config, Path(args.fixtures).resolve())
        for problem in problems:
            print(f"problem: {problem}")
        if problems:
            return 1
        print("every cited id exists in its fixture")
    return 0


def cmd_serving_check(args) -> int:
    config = load_config()
    if args.model not in config.models:
        raise UsageError(f"unknown model {args.model}: one of {sorted(config.models)}")
    result = serving_check(config.models[args.model], load=not args.no_load)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 3


def cmd_run(args) -> int:
    config = load_config()
    if args.model not in config.models:
        raise UsageError(f"unknown model {args.model}: one of {sorted(config.models)}")
    if args.surface not in config.conditions["surfaces"]:
        raise UsageError(f"unknown surface {args.surface}: one of {sorted(config.conditions['surfaces'])}")
    if args.proposal not in config.conditions["proposals"]:
        raise UsageError(f"unknown proposal condition {args.proposal}")
    task_ids = sorted(config.tasks) if args.all else list(dict.fromkeys(args.task or []))
    if not task_ids:
        raise UsageError("give --task ID (repeatable) or --all")
    unknown = [t for t in task_ids if t not in config.tasks]
    if unknown:
        raise UsageError(f"unknown task(s) {unknown}: one of {sorted(config.tasks)}")
    if args.repeats < 1 or args.first_repeat < 1:
        raise UsageError("--repeats and --first-repeat are at least 1")
    # The answers hold the numbers and ids of one seed's fixtures: another seed would be scored
    # against them. Per-seed answers do not exist yet.
    other_seed = sorted(t for t in task_ids if config.answers[t]["fixture"]["seed"] != args.seed)
    if other_seed:
        seeds = sorted({config.answers[t]["fixture"]["seed"] for t in other_seed})
        raise UsageError(f"--seed {args.seed}: the answers of {', '.join(other_seed)} are written for the "
                         f"seed {', '.join(map(str, seeds))} fixtures; another seed needs its own answers")
    model = config.models[args.model]
    checkout = checkout_info(Path(args.checkout).resolve() if args.checkout else ROOT)
    out = Path(args.out).resolve()
    work_parent = Path(args.work_root).resolve() if args.work_root else Path(tempfile.gettempdir()).resolve()
    if work_parent == out or work_parent.is_relative_to(out):
        raise UsageError(f"--work-root {work_parent} is inside --out: the model's work root must not "
                         "share a tree with the fixtures and the other runs")
    # The scan masks and removes files under OUT's runs and the work root: neither may be in the
    # caller's aelix state (~/.aelix), nor hold it.
    guarded = [Path(os.path.realpath(Path.home() / ".aelix"))]
    auth_file = None
    if model.get("auth") is not None:
        if args.aelix_command:
            raise UsageError(f"{model['id']} uses an aelix login: aelix is started through "
                             "tools/model_eval_aelix.py with the Python of aelix 0.1.0b2, not --aelix-command")
        auth_file = Path(os.path.abspath(os.path.expanduser(args.aelix_auth))) if args.aelix_auth \
            else default_aelix_auth()
        check_login_path(auth_file)
        # The login's directory and the one above it (~/.aelix for ~/.aelix/agent/auth.json).
        login_dir = Path(os.path.realpath(auth_file.parent))
        guarded += [login_dir, login_dir.parent]
    elif args.aelix_auth:
        raise UsageError(f"--aelix-auth is for a model that uses an aelix login; {model['id']} does not")
    real_out, real_work = Path(os.path.realpath(out)), Path(os.path.realpath(work_parent))
    for place in guarded:
        if real_out.is_relative_to(place) or place.is_relative_to(real_out):
            raise UsageError(f"--out {out} and {place} overlap: the harness masks and removes files in its "
                             "output, which must not hold or be in aelix's state or the login's directory")
        if real_work.is_relative_to(place):  # each work root is a new directory under it
            raise UsageError(f"--work-root {work_parent} is inside {place}")
    # Every run's directory is checked before the first run: nothing is run when one exists.
    taken = [name for t in task_ids for r in range(args.first_repeat, args.first_repeat + args.repeats)
             if (out / "runs" / (name := run_name(t, model["id"], args.surface, args.proposal, r))).exists()]
    if taken:
        raise UsageError(f"runs already in {out / 'runs'}: {', '.join(taken)}; nothing was run: use a new "
                         "--out or another --first-repeat")
    prepare_out(out)
    command = shlex.split(args.aelix_command) if args.aelix_command else list(AELIX_COMMAND)
    key_env = model.get("api_key_env")
    keep = (key_env,) if key_env else ()
    missing_key = bool(key_env and not os.environ.get(key_env))
    serving = None
    if not missing_key and model["serving"].get("backend") == "ollama":
        serving = serving_check(model)
    dirs = uv_dirs(processes().find_program("uv"))
    login = login_check(model, auth_file, out, dirs) if auth_file is not None else None
    session = Session(config=config, model=model, out=out, checkout=checkout, surface=args.surface,
                      proposal=args.proposal, seed=args.seed, aelix_command=command, aelix_version=None,
                      fixtures_root=Path(args.fixtures).resolve() if args.fixtures else out / "fixtures",
                      timeout=args.timeout, script=Path(args.script).resolve() if args.script else None,
                      serving=serving, uv=None,
                      secrets=([os.environ[key_env]] if key_env and os.environ.get(key_env) else [])
                      + env_secrets(keep),
                      work_parent=work_parent, auth_file=auth_file, login=login, dirs=dirs)
    records = []
    with stop_signals():
        try:
            runnable = [t for t in task_ids if not not_run_reason(session, config.tasks[t])]
            if runnable:
                session.aelix_version = aelix_version(command)
                session.uv = link_uv(out)
            for task_id in task_ids:
                for repeat in range(args.first_repeat, args.first_repeat + args.repeats):
                    record = run_one(session, config.tasks[task_id], repeat)
                    records.append(record)
                    print(f"{record['run']}: {record['outcome']}"
                          + (f" ({record['reason']})" if record.get("reason") else "")
                          + (f" — {record['score']['fact_recall']['passed']}/"
                             f"{record['score']['fact_recall']['total']} facts, "
                             f"{record['tool_summary']['calls']} calls, "
                             f"{record['tool_summary']['argument_errors']} argument errors, "
                             f"{record['elapsed_s']} s" if record.get("score") else "")
                          + (f" — secret scan {record['secret_scan']['status']}"
                             if record.get("secret_scan") else ""), flush=True)
                    raise_if_stopped()
        finally:
            with deferred():
                summary = summarize(out)
    print(f"summary: {out / 'summary.json'}")
    if records and all(r["outcome"] == "not_run" for r in records) and not summary["not_scanned"]:
        for record in records:
            if record.get("login_to_run"):
                print(f"login: {record['login_to_run']}")
            if record.get("command_to_run"):
                print(f"to run: {mask_text(record['command_to_run'])}")
        return 3
    failed = [r["run"] for r in records if (r.get("secret_scan") or {}).get("status") != "passed"]
    if (login or {}).get("secret_scan", {}).get("status") == "failed":
        failed.append("the login check's agent directory (OUT/iso/login-check-agent)")
    if failed or summary["not_scanned"]:
        print(f"model_eval: the secret scan did not pass for {', '.join(failed + summary['not_scanned'])}: "
              "credential-shaped text was overwritten, a value of the login was masked by the launcher or "
              "its masking did not run, a link reaching outside the run was removed, a process could still "
              "write, or the run was never searched (see secret_scan in run.json; `model_eval.py scan OUT` "
              "searches a run left unscanned)", file=sys.stderr)
        return 1
    return 0


def check_login_path(auth_file: Path) -> None:
    """The login's file as aelix keeps it: auth.json in an agent directory (~/.aelix/agent or
    $AELIX_CODING_AGENT_DIR), a regular file with one name. aelix replaces it when it refreshes a
    token (aelix_ai/oauth/auth_storage.py _write_raw: os.replace), which would replace a link with a
    new file and leave the file it pointed at, or another name of it, with a used refresh token; and
    it makes the file's directory private (chmod 0700), which a shared directory must not get."""
    agent = os.environ.get("AELIX_CODING_AGENT_DIR")
    agent_dir = Path(os.path.abspath(os.path.expanduser(agent))) if agent else None
    if auth_file.name != "auth.json" or (auth_file.parent.name != "agent" and auth_file.parent != agent_dir):
        raise UsageError(f"--aelix-auth {auth_file}: give the auth.json of an aelix agent directory "
                         "(~/.aelix/agent/auth.json or $AELIX_CODING_AGENT_DIR/auth.json)")
    if auth_file.is_symlink():
        raise UsageError(f"--aelix-auth {auth_file} is a symbolic link: aelix would replace the link with a "
                         f"new file when it refreshes a token; give the file it points to "
                         f"({os.path.realpath(auth_file)}), from its own agent directory")
    with contextlib.suppress(OSError):
        if os.lstat(auth_file).st_nlink > 1:
            raise UsageError(f"--aelix-auth {auth_file} has other names (hard links): a refresh would "
                             "leave them with a used refresh token")


def cmd_score(args) -> int:
    config = load_config()
    out = Path(args.out).resolve()
    if not (out / "runs").is_dir():
        raise UsageError(f"{out} holds no runs/")
    stored = stored_runs(out)
    # A run whose files were never searched is not read: `scan OUT` first.
    unscanned = [d.name for d, r in stored if scan_status(r) == "not_scanned"]
    records = {d / "run.json": r for d, r in stored if scan_status(r) != "not_scanned" and r.get("task")}
    other_seed = [r["run"] for r in records.values() if r.get("outcome") != "not_run"
                  and r.get("seed") != config.answers[r["task"]]["fixture"]["seed"]]
    if other_seed:
        raise UsageError(f"runs made with another seed than their answers: {', '.join(other_seed)}; "
                         "nothing was scored")
    for path, record in records.items():
        if record.get("outcome") == "not_run" and not str(record.get("reason") or "").startswith(
                "aelix_login_unusable"):
            continue
        if "before" not in record or "requests" not in record:  # aelix never ran: nothing to score
            print(f"{record.get('run', path.parent.name)}: {record.get('outcome')} (not scored: aelix did not run)")
            continue
        model = config.models.get(record["model"]["config"])
        if model is None:
            raise UsageError(f"{path}: unknown model {record['model']['config']}")
        session = Session(config=config, model=model, out=out, checkout=record["kit"],
                          surface=record["conditions"]["surface"], proposal=record["conditions"]["proposal"],
                          seed=record["seed"], aelix_command=record["host"]["aelix_command"],
                          aelix_version=record["host"]["aelix"], fixtures_root=out / "fixtures",
                          timeout=None, script=None, serving=record["model"].get("serving_check"),
                          uv=None, secrets=[])
        task = config.tasks[record["task"]]
        _, extra = task_requests(session, task, path.parent / "work")
        record["answer"] = {"status": config.answers[task["id"]]["status"],
                            "confirmed_by": config.answers[task["id"]].get("confirmed_by")}
        finish_record(session, task, record, path.parent, path.parent / "work" / "project", extra,
                      model["serving"].get("backend") == "fake")
        write_json(path, record)
        print(f"{record['run']}: {record['outcome']}")
    summarize(out)
    if unscanned:
        print(f"model_eval: not scored, never searched: {', '.join(unscanned)}; run `model_eval.py scan "
              f"{out}` first", file=sys.stderr)
        return 1
    return 0


def cmd_scan(args) -> int:
    """Secure and search every run of OUT that was never searched: a run whose harness was killed (its
    provisional record names the work root, which is moved into the run first, after the processes
    still working there have been stopped). A run whose scan failed keeps that record."""
    out = Path(args.out).resolve()
    if not (out / "runs").is_dir():
        raise UsageError(f"{out} holds no runs/")
    results = {}
    with stop_signals(), deferred():
        for run_dir, record in stored_runs(out):
            if scan_status(record) != "not_scanned":  # searched (a failed scan keeps what it found)
                continue
            record = record or {"schema": RUN_SCHEMA, "run": run_dir.name, "outcome": "error",
                                "harness_error": "no run.json: the harness stopped before it wrote the record"}
            work = Path(record["work"]["root"]) if (record.get("work") or {}).get("root") else None
            auth = ((record.get("model") or {}).get("auth") or {}).get("file")
            record["scanned_by"] = {"command": "scan", "at": now_iso()}
            # aelix ran with the login, and no harness was left to ask the launcher for its passes.
            close_run(record, run_dir, work, run_dir / "work", None, None, secrets=env_secrets(),
                      auth_file=Path(auth) if auth else None,
                      exact_expected=bool(auth) and "started_at" in record,
                      exact_reason="the harness that started the launcher was gone before it asked for the "
                                   "masking passes (on POSIX the launcher masks the run on its own when its "
                                   "lifeline closes, but nothing reports what it found)")
            results[run_dir.name] = record["secret_scan"]["status"]
            print(f"{run_dir.name}: secret scan {results[run_dir.name]}", flush=True)
        summary = summarize(out)
    print(f"summary: {out / 'summary.json'}")
    failed = [name for name, status in results.items() if status != "passed"] + summary["not_scanned"]
    return 1 if failed else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="model_eval.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="check the tasks, answers, models and conditions")
    v.add_argument("--fixtures", help="also check that every cited id exists (fixtures made here)")
    s = sub.add_parser("serving-check", help="check that the backend serves the pinned configuration")
    s.add_argument("--model", required=True)
    s.add_argument("--no-load", action="store_true", help="do not load the model to read its context")
    r = sub.add_parser("run", help="run tasks through aelix and score them")
    r.add_argument("--model", required=True)
    r.add_argument("--task", action="append")
    r.add_argument("--all", action="store_true")
    r.add_argument("--out", required=True)
    r.add_argument("--repeats", type=int, default=1)
    r.add_argument("--first-repeat", type=int, default=1)
    r.add_argument("--surface", default="default")
    r.add_argument("--proposal", default="unrequested")
    r.add_argument("--checkout", help="another checkout's extension and kit (e.g. before the rename, #9)")
    r.add_argument("--fixtures", help="where the fixtures are made and reused (default OUT/fixtures)")
    r.add_argument("--work-root", help="where each run's work root is made, outside OUT (default: the "
                                       "system's temporary directory); it is moved into the run when "
                                       "aelix exits")
    r.add_argument("--seed", type=int, default=0,
                   help="the fixtures' seed; only the answers' seed (0) is accepted")
    r.add_argument("--aelix-command", help=f"default: {shlex.join(AELIX_COMMAND)}")
    r.add_argument("--aelix-auth", help="for a model that uses an aelix login: the login's file (default: "
                                        "where the caller's aelix keeps it, $AELIX_CODING_AGENT_DIR/auth.json "
                                        "or ~/.aelix/agent/auth.json); only aelix opens it")
    r.add_argument("--timeout", type=int, help="seconds per run (default: the task's timeout_s)")
    r.add_argument("--script", help="dry-run only: a script instead of dry_run/<task>.json")
    c = sub.add_parser("score", help="score the runs of an earlier output again with the current answers")
    c.add_argument("out")
    n = sub.add_parser("scan", help="secure and search the runs of an output that have no passed secret "
                                    "scan (a run whose harness was killed)")
    n.add_argument("out")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return {"validate": cmd_validate, "serving-check": cmd_serving_check, "run": cmd_run,
                "score": cmd_score, "scan": cmd_scan}[args.command](args)
    except Stopped as exc:
        print(f"model_eval: {exc}: the run in progress was stopped, searched and recorded", file=sys.stderr)
        return 1
    except UsageError as exc:
        print(f"model_eval: {exc}", file=sys.stderr)
        return 2
    except HarnessError as exc:
        print(f"model_eval: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
