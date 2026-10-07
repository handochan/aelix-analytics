"""Run the real-model evaluation tasks (Issue #40) through aelix and score them against the answer files.

    python tools/model_eval.py validate [--fixtures DIR]
    python tools/model_eval.py serving-check --model ID
    python tools/model_eval.py run --model ID (--task ID ... | --all) --out DIR [--repeats N]
                               [--surface NAME] [--proposal NAME] [--checkout DIR] [--fixtures DIR]
                               [--work-root DIR] [--seed N] [--aelix-command CMD] [--aelix-auth FILE]
                               [--timeout S] [--script FILE]
    python tools/model_eval.py score DIR

Run it in the development environment, where the evaluation fixtures can be made:

    uv run --locked python tools/model_eval.py run --model dry-run --task T06 --out DIR

The tasks, the answers, the model configurations and the conditions are files under
tools/model_eval_data/ (README.md there says what each holds). Per run the harness makes the task's
fixture with tools/eval_fixtures.py (seed fixed: the answers are written for one seed, and another
--seed is refused), copies only its project/ and input/ into a new work root outside OUT
(manifest.json and expected.json hold answers), starts the real aelix 0.1.0b2 in its JSON event mode
there with this checkout's extension (or another checkout's: --checkout), moves the work root into
the run's directory when aelix has exited, reads the events, and records the model and its whole
serving configuration, the host and kit versions, the conditions, every tool call and how it failed,
the tokens the host reports, what changed in the project and the score.

Isolation: the trust list, uv (a link to the real one), aelix's agent directory (its models.json and
state) and its session directory are inside OUT. The model works in a work root that is a new
directory under --work-root (default: the system's temporary directory), never inside OUT, so the
fixtures and the other runs are not next to it; tool calls whose paths leave the work root are listed
in the record and set needs_review. The old names (AELIX_LAB_*, LAB_*) are unset. The harness reads
and writes nothing under ~/.aelix. When aelix has exited, the harness waits until no process names
the work root (the kit's extension starts its report build detached at the end of every prompt, and
it outlives aelix; after_exit, at most SETTLE_TIMEOUT seconds, then such processes are stopped), and
only then moves it; the project's Python environment (.venv, which the kit makes with uv from the
project's uv.lock: about 450 MB of public packages and links into uv's Python) is removed from the
kept work root, never following a link.

Credentials. A model whose configuration names an aelix login (auth.source aelix_login, e.g. the
frontier model through aelix's openai-codex provider) uses the caller's own aelix login in place:
aelix is started through tools/model_eval_aelix.py, which opens aelix's credential store at the
login's file (--aelix-auth, default: where the caller's aelix keeps it) while every other piece of
aelix's state stays in the run's agent directory. Only aelix opens that file; it refreshes a token
there itself. The harness never reads, copies or links it: it checks that the file exists and asks
aelix to list the model with it (aelix lists a model only when its provider has a login entry), and
records the file's modification time before and after each run. A model whose configuration names a
key variable (api_key_env) gets the key from the environment; the harness never writes it:
command.json keeps variable names only, the run record is redacted before it is written, and after
every run, also one that failed after aelix exited, every file under the run's directory (run.json,
events.jsonl, stderr.txt, command.json, fake-requests.json, agent/ and work/) is searched for the
key's bytes, which are overwritten in place by a marker of the same length. Then every file under the
run's directory (symbolic links are not followed) is searched for credential-shaped text without
knowing any value (a JWT, a Bearer token, a refresh/access/id token or key field with a value, an
sk- key, a long mixed base64url token): each one is overwritten in place by a marker of its length
and listed (file, line, kind, length) in the record's secret_scan, which is then failed, and a link
that leaves the run's directory is removed and listed. A file the model wrote outside its work root
is not searched.

Tokens are what aelix records from the model API's usage (per assistant message). What the API or
aelix does not report is `not_measured` with the reason; bytes and characters are never turned into
tokens. The dry-run model is a scripted fake server whose usage numbers are fixed, not measured.

Exit status: 0 when every requested run was executed and recorded (a failed or timed-out task is
data), 1 when a run could not be executed or recorded or its secret scan failed, 2 on a usage or
configuration error (nothing run), 3 when a precondition is missing and nothing was run (no aelix
login for the model, no API key, serving differs from the pinned configuration): the summary records
those runs as not_run.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.server
import json
import os
import platform
import re
import shlex
import shutil
import statistics
import subprocess
import sys
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
# Unset in every child: the old names make the current kit refuse to run, a caller's own choices of
# project, trust list, uv or aelix state must not leak into a measured session.
ISOLATED_PREFIXES = ("AELIX_ANALYTICS_", "AELIX_LAB_")
ISOLATED_KEYS = ("LAB_PROJECT", "LAB_ACTOR", "LAB_SESSION", "LAB_STYLE", "VIRTUAL_ENV",
                 "AELIX_CODING_AGENT_DIR", "AELIX_CODING_AGENT_SESSION_DIR", "AELIX_MCP_CONFIG")

# Extension error codes that mean the call's arguments were wrong (the kit's LabError codes).
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
        if not surface.get("extension"):
            out.append(f"conditions.json: surface {name} names no extension file")
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
    out, pos = [], 0
    for m in SPLITTERS[scope].finditer(text):
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
    word that is not negated, or for an id by its `resolved_by` id."""
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
            continue
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

def chart_text(project: Path | None, chart: dict) -> str:
    """What a chart holds as text: its spec files (JSON, HTML, SVG), its data's column names and the
    data's text values (when polars can read it)."""
    parts = [" ".join(str(c) for c in chart.get("data_columns") or [])]
    if project is None:
        return parts[0]
    for rel in (chart.get("files") or {}).values():
        path = project / str(rel)
        if path.suffix.lower() in (".json", ".html", ".svg", ".htm") and path.is_file() \
                and path.stat().st_size <= 50_000_000:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    data = project / str(chart["data"]) if chart.get("data") else None
    if data is not None and data.is_file():
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
    if path is None or path.suffix.lower() != ".csv" or not path.is_file() \
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


def score(answer: dict, ev: Evidence, words: dict, extra_checks=()) -> dict:
    checks = [run_check(c, ev, words) for c in [*answer["checks"], *extra_checks]]
    required = [c for c in checks if c["required"] and c["kind"] != "critical"]
    critical = [c["id"] for c in checks if c["kind"] == "critical" and not c["passed"]]
    facts = [c for c in checks if c["kind"] == "fact"]
    evidence = [c for c in checks if c["kind"] == "evidence"]
    success = all(c["passed"] for c in required) and not critical
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
        "checks": checks,
        "fact_recall": {"passed": sum(c["passed"] for c in facts), "total": len(facts)},
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

def journal(project: Path) -> list[dict]:
    path = project / "journal.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            out.append(json.loads(line))
    return out


def snapshot(project: Path) -> dict:
    events = journal(project)
    runs = sorted(p.name for p in (project / "runs").iterdir() if p.is_dir()) \
        if (project / "runs").is_dir() else []
    context = project / "CONTEXT.md"
    return {"max_seq": max((int(e.get("seq") or 0) for e in events), default=0),
            "records": len(events), "runs": runs,
            "context_sha256": sha256_file(context) if context.is_file() else None}


def changes(project: Path, before: dict) -> dict:
    events = journal(project)
    new_records = [{"id": e.get("id"), "kind": e.get("kind"), "text": e.get("text"),
                    "refs": e.get("refs")} for e in events if int(e.get("seq") or 0) > before["max_seq"]]
    new_runs = []
    runs_dir = project / "runs"
    for name in sorted(p.name for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.is_dir() else []:
        if name in before["runs"]:
            continue
        record = {}
        with contextlib.suppress(OSError, json.JSONDecodeError):
            record = json.loads((runs_dir / name / "run.json").read_text(encoding="utf-8"))
        charts = record.get("charts") or []
        new_runs.append({"id": name, "status": record.get("status"),
                         "script": (record.get("script") or {}).get("path"),
                         "charts": [c.get("id") for c in charts],
                         "chart_files": [{k: c.get(k) for k in ("id", "files", "data", "data_columns")}
                                         for c in charts],
                         "outputs": [{k: o.get(k) for k in ("path", "change", "rows", "schema")}
                                     for o in record.get("outputs") or []],
                         "error": (record.get("error") or {}).get("summary")})
    context = project / "CONTEXT.md"
    after_sha = sha256_file(context) if context.is_file() else None
    existing = {str(e.get("id")) for e in events}
    if runs_dir.is_dir():
        existing |= {p.name for p in runs_dir.iterdir() if p.is_dir()}
    return {"new_records": new_records, "new_runs": new_runs,
            "context_changed": after_sha != before["context_sha256"],
            "existing_ids": sorted(existing)}


# --- fixtures and the work directory ---------------------------------------------------------------

def isolated_env(base: dict | None = None) -> dict:
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.startswith(ISOLATED_PREFIXES) or key in ISOLATED_KEYS:
            env.pop(key)
    return env


def fixture_name(spec: dict, seed: int) -> str:
    return spec["kind"] + (f"-{spec['scale']}" if spec.get("scale") else "") + f"-s{seed}"


def ensure_fixture(spec: dict, seed: int, root: Path) -> Path:
    """The fixture made by tools/eval_fixtures.py with this seed, made once under `root`."""
    path = root / fixture_name(spec, seed)
    manifest = path / "manifest.json"
    if not manifest.is_file():
        if path.exists() and any(path.iterdir()):
            raise HarnessError(f"{path} exists without a manifest.json: remove it")
        args = (["scale", "--scale", spec["scale"]] if spec["kind"] == "scale" else [spec["kind"]])
        argv = [sys.executable, str(FIXTURE_TOOL), *args, "--seed", str(seed), "--out", str(path)]
        done = subprocess.run(argv, cwd=ROOT, env=isolated_env(), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
        if done.returncode != 0:
            raise HarnessError(f"making the {spec['kind']} fixture failed ({done.returncode}): "
                               f"{(done.stderr or done.stdout)[-1500:]}")
    data = read_json(manifest)
    generator = (data.get("generator") or {}).get("version")
    if generator != FIXTURE_GENERATOR_VERSION or data.get("seed") != seed or \
            data.get("kind") != spec["kind"] or data.get("scale") != spec.get("scale"):
        raise HarnessError(f"{path} is not the {fixture_name(spec, seed)} fixture of generator "
                           f"version {FIXTURE_GENERATOR_VERSION} (manifest: generator {generator}, "
                           f"seed {data.get('seed')}, kind {data.get('kind')})")
    return path


def init_project(kit_src: Path, project: Path, title: str, goal: str, env: dict) -> None:
    """A new project made by the checkout's own kit (core.init_project), as a person would."""
    code = ("import sys\nfrom pathlib import Path\nfrom aelix_analytics import core\n"
            "core.init_project(Path(sys.argv[1]), title=sys.argv[2], goal=sys.argv[3], actor='human')\n")
    done = subprocess.run([sys.executable, "-c", code, str(project), title, goal], cwd=project.parent,
                          env={**env, "PYTHONPATH": str(kit_src)}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if done.returncode != 0:
        raise HarnessError(f"making the fresh project failed: {(done.stderr or done.stdout)[-1500:]}")


def prepare_work(task: dict, fixture: Path, work: Path, kit_src: Path, env: dict) -> Path:
    """project/ and input/ only: manifest.json and expected.json carry answers."""
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()):
        raise HarnessError(f"the work root {work} is not empty")
    project = work / "project"
    if task["setup"] == "fixture":
        shutil.copytree(fixture / "project", project, symlinks=True)
        if (fixture / "input").is_dir():
            shutil.copytree(fixture / "input", work / "input", symlinks=True)
    else:
        fresh = task["fresh_project"]
        (work / "input").mkdir()
        for name in fresh["inputs"]:
            shutil.copy2(fixture / "input" / name, work / "input" / name)
        init_project(kit_src, project, fresh["title"], fresh["goal"], env)
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


def launch_command(model: dict, auth_file: Path | None, aelix_command: list[str]) -> list[str]:
    """How aelix is started for this model: through the launcher (its Python and the login's file)
    when the model uses an aelix login, else the aelix command itself."""
    if model.get("auth") is None:
        return list(aelix_command)
    return [*AELIX_PYTHON, str(LAUNCHER), str(auth_file)]


def login_check(model: dict, auth_file: Path, out: Path) -> dict:
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
    agent.mkdir(parents=True, exist_ok=True)
    write_models_json(model, agent, None)  # a provider aelix does not know by itself
    env = {**isolated_env(), "AELIX_CODING_AGENT_DIR": str(agent),
           "AELIX_CODING_AGENT_SESSION_DIR": str(agent / "sessions")}
    argv = [*launch_command(model, auth_file, []), "--offline", "--list-models",
            f"{provider} {model['model']}"]
    try:
        # cwd: the empty agent directory, so that no .env of the caller's directory is loaded.
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=600, env=env, cwd=agent)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HarnessError(f"aelix could not be started to check the login ({shlex.join(argv)}): "
                           f"{exc}") from exc
    rows = [line.split() for line in done.stdout.splitlines()]
    row = next((r for r in rows if r[:2] == [provider, model["model"]]), None)
    result.update(exit_code=done.returncode, listed=row is not None,
                  row=" ".join(row) if row else None)
    if done.returncode != 0:
        result.update(ok=False, problem=f"aelix --list-models exited {done.returncode}: "
                                        f"{done.stderr.strip()[-400:]}")
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


def launch_of(session: Session) -> list[str]:
    return launch_command(session.model, session.auth_file, session.aelix_command)


def tools_of(session: Session) -> dict:
    return GENERATIONS[session.checkout["generation"]]["tools"]


def surface_argv(session: Session) -> tuple[list[str], str]:
    surface = session.config.conditions["surfaces"][session.surface]
    tools = tools_of(session)
    extension = surface["extension"]
    if session.checkout["generation"] == "lab" and extension == GENERATIONS["analytics"]["extension"]:
        extension = GENERATIONS["lab"]["extension"]
    argv = []
    if surface.get("tools") is not None:
        names = []
        for item in surface["tools"]:
            names += [tools[r] for r in ROLES] if item == "@extension" else [item]
        argv = ["--tools", ",".join(names)]
    return argv, extension


def build_argv(session: Session, requests: list[str], extension: Path, tools_argv: list[str]) -> list[str]:
    model = session.model
    return [*launch_of(session), "--mode", "json", "-p", *requests,
            "--provider", model["provider"], "--model", model["model"], "--thinking", model["thinking"],
            "--no-session", "--offline", "--no-context-files", *tools_argv, "-e", str(extension)]


def run_env(session: Session, run_dir: Path, project: Path) -> tuple[dict, dict]:
    """The child's environment and the variables this harness set in it (paths only)."""
    names = GENERATIONS[session.checkout["generation"]]["env"]
    iso = session.out / "iso"
    set_vars = {
        names["trust"]: str(iso / "trust" / "analytics-projects.json"),
        names["project"]: str(project),
        "AELIX_CODING_AGENT_DIR": str(run_dir / "agent"),
        "AELIX_CODING_AGENT_SESSION_DIR": str(run_dir / "agent" / "sessions"),
    }
    if session.uv:
        set_vars[names["uv"]] = session.uv
    return {**isolated_env(), **set_vars}, set_vars


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
    if session.proposal == "always_on":
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
        "conditions": {"surface": session.surface, "proposal": session.proposal},
        "fixture": {**session.config.fixtures[task["fixture"]], "seed": session.seed},
        "answer": {"status": answer["status"], "confirmed_by": answer.get("confirmed_by")},
    }


def redaction_marker(length: int) -> bytes:
    """A marker as long as the secret: a binary file keeps its layout."""
    if length < 10:
        return b"*" * length
    return b"<redacted" + b"*" * (length - 10) + b">"


def redact_value(value, secrets: list[str]):
    """The value with every secret's text replaced, recursively."""
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "<redacted>")
        return value
    if isinstance(value, dict):
        return {redact_value(k, secrets): redact_value(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, secrets) for v in value]
    return value


def redact_tree(root: Path, secrets: list[str]) -> list[str]:
    """Overwrite every secret's bytes in every file under root (symbolic links are not followed).
    The files where one was found, relative to root."""
    wanted = [s.encode("utf-8") for s in secrets if s]
    if not wanted or not root.is_dir():
        return []
    found = []
    for dirpath, _, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            data = original = path.read_bytes()
            for secret in wanted:
                data = data.replace(secret, redaction_marker(len(secret)))
            if data != original:
                path.write_bytes(data)
                found.append(path.relative_to(root).as_posix())
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
    ("credential_field", re.compile(
        rb"(?<![A-Za-z0-9_])(?:" + _QUOTE + rb")?(?i:refresh|access|refresh_?token|access_?token|id_?token|"
        rb"token|api_?key|client_?secret|password)(?:" + _QUOTE + rb")?\s*(?::\s*" + _QUOTE + rb"(" +
        _VALUE + rb"{16,})|=\s*" + _QUOTE + rb"(" + _VALUE + rb"{16,})|=\s*([A-Za-z0-9_~+/=-]{16,}))"),
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
                    bytes(work[max(0, start - 12):start]))):
                continue
            line += work.count(b"\n", counted, start)
            counted = start
            found.append({"kind": kind, "offset": start, "line": line, "length": end - start})
            work[start:end] = redaction_marker(end - start)
    return sorted(found, key=lambda f: f["offset"])


MAX_LISTED = 200


# How long the harness waits, after aelix has exited, for processes still working in the work root.
SETTLE_TIMEOUT = 600


def processes_naming(paths: list[str]) -> list[tuple[int, str]] | None:
    """The processes whose command line names one of paths (None when the list cannot be read). The
    kit's extension starts its report build detached at the end of every prompt (uv run --project
    <project> … render --if-changed): it outlives aelix and names the project."""
    if os.name == "nt":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"]
    else:
        argv = ["ps", "-A", "-ww", "-o", "pid=", "-o", "args="]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    found = []
    for line in done.stdout.splitlines():
        pid, _, args = line.strip().partition("\t" if os.name == "nt" else " ")
        if pid.strip().isdigit() and int(pid) != os.getpid() and any(p in args for p in paths):
            found.append((int(pid), args.strip()))
    return found


def settle(work: Path, timeout: float = SETTLE_TIMEOUT) -> dict:
    """Wait until no process names the work root, so that nothing writes into it while it is moved,
    its environment removed and its files searched; stop the ones left after the timeout. The time
    is not part of the run's elapsed_s."""
    paths = sorted({str(work), os.path.realpath(work)})
    started = time.monotonic()
    seen: dict[int, str] = {}
    killed: list[int] = []
    while True:
        found = processes_naming(paths)
        if found is None:
            return {"checked": False, "reason": "the process list could not be read",
                    "waited_s": round(time.monotonic() - started, 1)}
        for pid, args in found:
            seen.setdefault(pid, args[:300])
        if not found:
            break
        if time.monotonic() - started > timeout:
            if killed:  # stopped once already and still there
                break
            ports = processes()
            for pid, _ in found:
                with contextlib.suppress(OSError):
                    ports.kill_tree(pid)
                killed.append(pid)
        time.sleep(0.5)
    return {"checked": True, "waited_s": round(time.monotonic() - started, 1),
            "processes": [{"pid": pid, "args": args} for pid, args in sorted(seen.items())],
            "stopped": killed, "left": [pid for pid, _ in found] if found else [],
            "what": "processes whose command line names the work root after aelix exited (the kit's "
                    "detached report build): waited for before the work root is moved"}


def remove_environments(root: Path) -> list[dict]:
    """Remove the Python environments under root (a .venv directory with pyvenv.cfg), never following
    a link. The kit makes the project's with uv from its uv.lock, so it can be made again; it holds
    about 450 MB of public packages per run and links into uv's Python, which a later copy or archive
    of the run would follow."""
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


def scan_tree(root: Path, *, mask: bool = True) -> dict:
    """Search every file under root for credential-shaped text (symbolic links are not followed).

    Each finding is overwritten in place by a marker of its length and listed without its value. A
    symbolic link whose target leaves root is removed (a later copy or archive of the run would follow
    it) and listed; one inside root is listed. The scan fails when it found either."""
    findings, links, removed = [], [], []
    files = scanned = 0
    real_root = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        for name in sorted(dirnames + filenames):
            path = here / name
            if not path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            target = os.readlink(path)
            if os.path.realpath(path).startswith(real_root + os.sep):
                links.append({"file": rel, "target": target})
            elif mask:
                with contextlib.suppress(OSError):
                    path.unlink()
                removed.append({"file": rel, "target": target})
            else:
                removed.append({"file": rel, "target": target, "removed": False})
        for name in filenames:
            path = here / name
            if path.is_symlink() or not path.is_file():
                continue
            files += 1
            data = bytearray(path.read_bytes())
            scanned += len(data)
            found = credential_findings(data, mask=True)
            if found:
                if mask:
                    path.write_bytes(bytes(data))
                rel = path.relative_to(root).as_posix()
                findings += [{"file": rel, **{k: f[k] for k in ("line", "kind", "length")}} for f in found]
    failed = bool(findings or removed)
    return {"status": "failed" if failed else "passed", "files": files, "bytes": scanned,
            "kinds": list(CREDENTIAL_KINDS), "findings_count": len(findings),
            "findings": findings[:MAX_LISTED], "masked": mask and bool(findings),
            "links": links[:MAX_LISTED], "outside_links_removed": removed[:MAX_LISTED],
            "what": "credential-shaped text searched in every file under the run's directory without "
                    "knowing any value; each finding is overwritten in place by a marker of its length "
                    "and listed by file, line, kind and length, never by value"}


def run_one(session: Session, task: dict, repeat: int) -> dict:
    key = run_key(session, task["id"], repeat)
    run_dir = session.out / "runs" / key
    if run_dir.exists():
        raise UsageError(f"{run_dir} exists already: use a new --out or another repeat range")
    run_dir.mkdir(parents=True)
    record = base_record(session, task, repeat, key)
    reason = not_run_reason(session, task)
    if reason:
        record.update(outcome="not_run", reason=reason)
        if reason.startswith(("api_key_missing", "aelix_login_missing")):
            record["command_to_run"] = command_line(session, task, repeat)
        if reason.startswith("aelix_login_missing"):
            record["login_to_run"] = LOGIN_HOWTO
        write_json(run_dir / "run.json", record)
        return record
    tools = tools_of(session)
    fixture = ensure_fixture(session.config.fixtures[task["fixture"]], session.seed, session.fixtures_root)
    record["fixture"]["digest"] = read_json(fixture / "manifest.json").get("digest")
    # The model's work root is outside OUT (where the fixtures, with their answers, and the other
    # runs are) and holds only project/ and input/; it is kept as work/ once aelix has exited.
    session.work_parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"aelix-eval-{task['id']}-", dir=session.work_parent))
    kept = run_dir / "work"
    record["work"] = {"root": str(work), "kept_at": "work",
                      "what": "the model's work root during the run, outside OUT; moved into the "
                              "run's directory when aelix exited"}
    failure: BaseException | None = None
    settled: dict | None = None
    try:
        env_probe, _ = run_env(session, run_dir, work / "project")
        project = prepare_work(task, fixture, work, Path(session.checkout["path"]) / "src", env_probe)
        before = snapshot(project)
        requests, extra_checks = task_requests(session, task, work)
        tools_argv, extension = surface_argv(session)
        extension_path = Path(session.checkout["path"]) / extension
        env, set_vars = run_env(session, run_dir, project)
        (run_dir / "agent").mkdir(parents=True, exist_ok=True)
        scripted = session.model["serving"].get("backend") == "fake"
        timeout = session.timeout or task["timeout_s"]
        fake = ScriptedModel(load_script(task["id"], session.script), tools) if scripted else None
        with fake or contextlib.nullcontext():
            write_models_json(session.model, run_dir / "agent", fake.port if fake else None)
            argv = build_argv(session, requests, extension_path, tools_argv)
            write_json(run_dir / "command.json", {
                "argv": argv, "cwd": str(project), "set_env": set_vars,
                "unset_env": "AELIX_ANALYTICS_*, AELIX_LAB_*, " + ", ".join(ISOLATED_KEYS),
                "inherited_env": "the rest of the caller's environment; values are not recorded",
                "secret_env": session.model.get("api_key_env"),
                "login_file": None if session.auth_file is None else str(session.auth_file)})
            record.update(requests=requests, before=before, started_at=now_iso())
            if session.auth_file is not None:
                record["login"] = {"file": str(session.auth_file),
                                   "mtime_before": file_mtime(session.auth_file),
                                   "what": "the file's modification time only (its contents are never "
                                           "read here); aelix rewrites it in place when it refreshes "
                                           "a token, and so may another aelix of the caller"}
            ports = processes()
            started = time.perf_counter()
            timed_out = False
            with open(run_dir / "events.jsonl", "wb") as stdout, \
                    open(run_dir / "stderr.txt", "wb") as stderr:
                try:
                    proc = subprocess.Popen(argv, cwd=project, env=env, stdin=subprocess.DEVNULL,
                                            stdout=stdout, stderr=stderr, **ports.spawn_kwargs())
                except OSError as exc:
                    raise HarnessError(f"aelix could not be started: {exc}") from exc
                try:
                    code = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    ports.kill_tree(proc.pid)
                    code = proc.wait(timeout=60)
                except BaseException:
                    ports.kill_tree(proc.pid)
                    raise
            elapsed = round(time.perf_counter() - started, 1)
            if session.auth_file is not None:
                after = file_mtime(session.auth_file)
                record["login"].update(mtime_after=after,
                                       changed=after != record["login"]["mtime_before"],
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
        settled = settle(work)
        shutil.move(str(work), str(kept))
        finish_record(session, task, record, run_dir, kept / "project", extra_checks, scripted)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if work.exists() and not kept.exists():
            if settled is None:
                settled = settle(work)
            with contextlib.suppress(OSError):
                shutil.move(str(work), str(kept))
        if settled is not None:
            record["after_exit"] = settled
        environments, environments_error = [], None
        if kept.is_dir() and not kept.is_symlink():
            try:
                environments = remove_environments(kept)
            except OSError as exc:  # left in place: the scan below searches it
                environments_error = f"{type(exc).__name__}: {exc}"
        found = redact_tree(run_dir, session.secrets)
        clean = redact_value(record, session.secrets)
        if clean != record:
            found = sorted({*found, "run.json"})
        record.clear()
        record.update(clean)
        if found:
            record["secret_redacted"] = found
        if failure is not None and "outcome" not in record:
            record.update(outcome="error", harness_error=redact_value(
                f"{type(failure).__name__}: {failure}", session.secrets))
        record["environments_removed"] = {
            "removed": [{**e, "path": f"work/{e['path']}"} for e in environments],
            "error": environments_error,
            "what": "the project's Python environment (.venv) the kit made with uv: removed after aelix "
                    "exited, never following a link; uv.lock stays and makes it again"}
        write_json(run_dir / "run.json", record)
        # Credential-shaped text in any file of the run, the record included: overwritten in place.
        try:
            scan = scan_tree(run_dir)
        except OSError as exc:  # not searched is not passed
            scan = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "findings": [],
                    "outside_links_removed": []}
        if scan.get("masked"):  # run.json may be among the files it overwrote
            masked = read_json(run_dir / "run.json")
            record.clear()
            record.update(masked)
        record["secret_scan"] = scan
        mark_secret_scan(record)
        write_json(run_dir / "run.json", record)
    return record


def mark_secret_scan(record: dict) -> None:
    """A failed secret scan is for a person to look at."""
    review = (record.get("score") or {}).get("needs_review")
    if (record.get("secret_scan") or {}).get("status") == "failed" and isinstance(review, list) \
            and "secret_scan" not in review:
        review.append("secret_scan")


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
    stop = tr.turns[-1]["stop_reason"] if tr.turns else None
    if record.get("timed_out"):
        outcome = "timeout"
    elif record.get("exit_code") != 0 or not tr.ended or stop in ("error", "aborted") or \
            len(tr.turns) < len(record["requests"]):
        outcome = "error"
    else:
        outcome = result["result"]
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
        tool_summary=calls, outside_work_root=outside,
        tokens=token_totals(tr.messages, session.model["api"], scripted=scripted,
                            proposal_turn=proposal_turn),
        changes={k: v for k, v in state.items() if k != "existing_ids"},
        score=result,
        proposal_mentioned=bool(contains_any(ev.text if session.proposal != "requested" else
                                             "\n\n".join(tr.answers[:-1]),
                                             ["재사용", "skill", "스킬", "다시 쓸", "reuse"])),
        errors_recovered=recovered(state))
    mark_secret_scan(record)


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


def summarize(out: Path) -> dict:
    runs = []
    for path in sorted((out / "runs").glob("*/run.json")):
        record = read_json(path)
        sc = record.get("score") or {}
        tokens = record.get("tokens")
        runs.append({
            "run": record["run"], "task": record["task"], "model": record["model"]["config"],
            "surface": record["conditions"]["surface"], "proposal": record["conditions"]["proposal"],
            "repeat": record["repeat"], "outcome": record.get("outcome"), "reason": record.get("reason"),
            "answer_status": record["answer"]["status"], "fact_recall": sc.get("fact_recall"),
            "evidence": {k: (sc.get("evidence") or {}).get(k) for k in ("passed", "total")},
            "critical": sc.get("critical"), "needs_review": sc.get("needs_review"),
            "fabricated_ids": sc.get("fabricated_ids"),
            "tool_calls": (record.get("tool_summary") or {}).get("calls"),
            "argument_errors": (record.get("tool_summary") or {}).get("argument_errors"),
            "tokens_complete": (tokens or {}).get("complete") if tokens else None,
            "input_uncached": token_cell(tokens, "input_uncached"),
            "input_cached": token_cell(tokens, "input_cached"),
            "output": token_cell(tokens, "output"),
            "secret_scan": (record.get("secret_scan") or {}).get("status"),
            "elapsed_s": record.get("elapsed_s"), "record": str(path.relative_to(out))})
    groups: dict[tuple, list] = {}
    for run in runs:
        groups.setdefault((run["model"], run["task"], run["surface"], run["proposal"]), []).append(run)
    aggregate = []
    for (model, task, surface, proposal), items in sorted(groups.items()):
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
                          "success_without_review": sum(r["outcome"] == "success" and not r["needs_review"]
                                                        for r in items),
                          "needs_review": sum(bool(r["needs_review"]) for r in executed),
                          "fabricated_ids": sum(bool(r["fabricated_ids"]) for r in executed),
                          "critical": sum(bool(r["critical"]) for r in executed),
                          "argument_errors": sum(r["argument_errors"] or 0 for r in executed),
                          "secret_scan_failed": sum(r["secret_scan"] == "failed" for r in executed),
                          "median_total_tokens": statistics.median(totals) if totals else None,
                          "token_runs": len(whole),
                          "partial_usage_runs": len(executed) - len(whole)})
    summary = {"schema": SUMMARY_SCHEMA, "written_at": now_iso(), "runs": runs, "aggregate": aggregate,
               "note": "success counts every executed run in the denominator; answers marked draft "
                       "give provisional scores; a run with needs_review is confirmed by a person; "
                       "median_total_tokens uses only runs whose every request reported usage "
                       "(token_runs), partial_usage_runs are left out of it"}
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
        if r["outcome"] == "not_run":
            lines.append("| " + " | ".join([r["run"], name, r["answer_status"]] + ["—"] * 12) + " |")
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
                charts |= {c.get("id") for c in data.get("charts") or []}
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
    auth_file = None
    if model.get("auth") is not None:
        if args.aelix_command:
            raise UsageError(f"{model['id']} uses an aelix login: aelix is started through "
                             "tools/model_eval_aelix.py with the Python of aelix 0.1.0b2, not --aelix-command")
        auth_file = Path(os.path.abspath(os.path.expanduser(args.aelix_auth))) if args.aelix_auth \
            else default_aelix_auth()
        # The scan overwrites credential-shaped text under OUT's runs: OUT and the login's directory
        # do not hold each other. The model's work root (a new directory under --work-root) is not
        # inside the login's directory.
        login_dir = Path(os.path.realpath(auth_file.parent))
        real_out = Path(os.path.realpath(out))
        if real_out.is_relative_to(login_dir) or login_dir.is_relative_to(real_out):
            raise UsageError(f"--out {out} and the login's directory {login_dir} overlap")
        if Path(os.path.realpath(work_parent)).is_relative_to(login_dir):
            raise UsageError(f"--work-root {work_parent} is inside the login's directory {login_dir}")
    elif args.aelix_auth:
        raise UsageError(f"--aelix-auth is for a model that uses an aelix login; {model['id']} does not")
    # Every run's directory is checked before the first run: nothing is run when one exists.
    taken = [name for t in task_ids for r in range(args.first_repeat, args.first_repeat + args.repeats)
             if (out / "runs" / (name := run_name(t, model["id"], args.surface, args.proposal, r))).exists()]
    if taken:
        raise UsageError(f"runs already in {out / 'runs'}: {', '.join(taken)}; nothing was run: use a new "
                         "--out or another --first-repeat")
    prepare_out(out)
    command = shlex.split(args.aelix_command) if args.aelix_command else list(AELIX_COMMAND)
    key_env = model.get("api_key_env")
    missing_key = bool(key_env and not os.environ.get(key_env))
    serving = None
    if not missing_key and model["serving"].get("backend") == "ollama":
        serving = serving_check(model)
    login = login_check(model, auth_file, out) if auth_file is not None else None
    session = Session(config=config, model=model, out=out, checkout=checkout, surface=args.surface,
                      proposal=args.proposal, seed=args.seed, aelix_command=command, aelix_version=None,
                      fixtures_root=Path(args.fixtures).resolve() if args.fixtures else out / "fixtures",
                      timeout=args.timeout, script=Path(args.script).resolve() if args.script else None,
                      serving=serving, uv=None,
                      secrets=[os.environ[key_env]] if key_env and os.environ.get(key_env) else [],
                      work_parent=work_parent, auth_file=auth_file, login=login)
    records = []
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
    finally:
        summarize(out)
    print(f"summary: {out / 'summary.json'}")
    if records and all(r["outcome"] == "not_run" for r in records):
        for record in records:
            if record.get("login_to_run"):
                print(f"login: {record['login_to_run']}")
            if record.get("command_to_run"):
                print(f"to run: {record['command_to_run']}")
        return 3
    failed = [r["run"] for r in records if (r.get("secret_scan") or {}).get("status") == "failed"]
    if failed:
        print(f"model_eval: the secret scan failed for {', '.join(failed)}: credential-shaped text was "
              "overwritten or a link leaving the run was removed (see secret_scan in run.json)",
              file=sys.stderr)
        return 1
    return 0


def cmd_score(args) -> int:
    config = load_config()
    out = Path(args.out).resolve()
    if not (out / "runs").is_dir():
        raise UsageError(f"{out} holds no runs/")
    paths = sorted((out / "runs").glob("*/run.json"))
    records = {path: read_json(path) for path in paths}
    other_seed = [r["run"] for r in records.values() if r.get("outcome") != "not_run"
                  and r.get("seed") != config.answers[r["task"]]["fixture"]["seed"]]
    if other_seed:
        raise UsageError(f"runs made with another seed than their answers: {', '.join(other_seed)}; "
                         "nothing was scored")
    for path, record in records.items():
        if record.get("outcome") == "not_run":
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
    return 0


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
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return {"validate": cmd_validate, "serving-check": cmd_serving_check, "run": cmd_run,
                "score": cmd_score}[args.command](args)
    except UsageError as exc:
        print(f"model_eval: {exc}", file=sys.stderr)
        return 2
    except HarnessError as exc:
        print(f"model_eval: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
