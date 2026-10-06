"""Run the real-model evaluation tasks (Issue #40) through aelix and score them against the answer files.

    python tools/model_eval.py validate [--fixtures DIR]
    python tools/model_eval.py serving-check --model ID
    python tools/model_eval.py run --model ID (--task ID ... | --all) --out DIR [--repeats N]
                               [--surface NAME] [--proposal NAME] [--checkout DIR] [--fixtures DIR]
                               [--seed N] [--aelix-command CMD] [--timeout S] [--script FILE]
    python tools/model_eval.py score DIR

Run it in the development environment, where the evaluation fixtures can be made:

    uv run --locked python tools/model_eval.py run --model dry-run --task T06 --out DIR

The tasks, the answers, the model configurations and the conditions are files under
tools/model_eval_data/ (README.md there says what each holds). Per run the harness makes the task's
fixture with tools/eval_fixtures.py (seed fixed), copies only its project/ and input/ into the run's
work directory (manifest.json and expected.json hold answers), starts the real aelix 0.1.0b2 in its
JSON event mode with this checkout's extension (or another checkout's: --checkout), reads the
events, and records the model and its serving, the host and kit versions, the conditions, every tool
call and how it failed, the tokens the host reports, what changed in the project and the score.

Isolation: the trust list, uv (a link to the real one), aelix's agent directory (its models.json and
state) and its session directory are inside OUT; the old names (AELIX_LAB_*, LAB_*) are unset. Nothing
under ~/.aelix is read or written. An API key is read from the variable the model configuration
names and is never written: command.json keeps variable names only, and every written file is
checked for the key's value.

Tokens are what aelix records from the model API's usage (per assistant message). What the API or
aelix does not report is `not_measured` with the reason; bytes and characters are never turned into
tokens. The dry-run model is a scripted fake server whose usage numbers are fixed, not measured.

Exit status: 0 when every requested run was executed and recorded (a failed or timed-out task is
data), 1 when a run could not be executed or recorded, 2 on a usage or configuration error (nothing
run), 3 when a precondition is missing and nothing was run (no API key, serving differs from the
pinned configuration): the summary records those runs as not_run.
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
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
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

GATES = ("Q2", "Q3", "Q5", "Q11", "Q12", "Q13")
Q11_REQUESTS = ("single", "combination", "ambiguous", "similar_tools")
SETUPS = ("fixture", "fresh_with_input")
FIXTURE_KINDS = ("scale", "q10", "q13")
ANSWER_STATUSES = ("draft", "confirmed")
CHECK_KINDS = ("fact", "evidence", "uncertainty", "state", "behavior", "critical")
# Each check type with the keys it needs.
CHECK_TYPES = {
    "text_any": ("values",), "text_all": ("values",), "number": ("value",), "percent": ("value",),
    "number_near": ("value", "words"), "cooccur": ("all",), "claim_verdicts": ("claims",),
    "ids_all": ("values",), "ids_any": ("values",), "stated_as_current": ("patterns", "unless"),
    "new_records": (), "new_runs": (), "new_charts": (), "context_changed": (), "tool_called": ("role",),
}
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
NUMBER = re.compile(r"(?<![A-Za-z0-9_.,])[0-9][0-9,]*(?:\.[0-9]+)?")
PERCENT = re.compile(r"(?<![A-Za-z0-9_.,])([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:%|퍼센트|percent)")

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


def load_config(data: Path = DATA) -> Config:
    """Every file under model_eval_data, checked; a problem raises UsageError naming all of them."""
    problems: list[str] = []
    tasks_doc = read_json(data / "tasks.json")
    conditions = read_json(data / "conditions.json")
    answers = {}
    for path in sorted((data / "answers").glob("*.json")):
        answers[path.stem] = read_json(path)
    models = {}
    for path in sorted((data / "models").glob("*.json")):
        models[path.stem] = read_json(path)
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
    return Config(tasks, tasks_doc["fixtures"], answers, models, conditions, words)


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
        if kind == "critical" and ctype != "stated_as_current":
            out.append(f"check {cid}: a critical check is a stated_as_current check")
        if ctype == "stated_as_current":
            unless = check.get("unless")
            if isinstance(unless, str) and unless.startswith("@"):
                name = unless[1:]
                if name not in words and name != "superseded_or_reason":
                    out.append(f"check {cid}: unknown word set {unless}")
            if check.get("pattern_kind", "regex") == "regex":
                for pattern in check.get("patterns", []):
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        out.append(f"check {cid}: pattern {pattern!r} does not compile: {exc}")
        if ctype == "claim_verdicts":
            for claim, verdict in (check.get("claims") or {}).items():
                if verdict not in VERDICTS or not RECORD_ID.fullmatch(claim):
                    out.append(f"check {cid}: claim {claim} → {verdict} is not a record id and a verdict")
        if ctype == "tool_called" and check.get("role") not in ROLES:
            out.append(f"check {cid}: role {check.get('role')!r} is not one of {ROLES}")
    return out


def check_model(model, model_id: str) -> list[str]:
    out = []
    if not isinstance(model, dict) or model.get("schema") != MODEL_SCHEMA:
        return [f"schema is not {MODEL_SCHEMA}"]
    if model.get("id") != model_id:
        out.append(f"id is {model.get('id')!r}, not the file name {model_id}")
    for key in ("provider", "model", "api", "thinking", "serving"):
        if not model.get(key):
            out.append(f"{key} is missing")
    if model.get("api") not in ("openai-completions", "openai-responses"):
        out.append("api must be openai-completions or openai-responses (the adapters this harness reads)")
    env = model.get("api_key_env")
    if env is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]*", str(env)):
        out.append("api_key_env must be the NAME of an environment variable")
    provider = model.get("aelix_provider")
    if provider is not None:
        key = str(provider.get("apiKey") or "")
        if re.search(r"sk-[A-Za-z0-9_-]{16,}", key):
            out.append("aelix_provider.apiKey looks like a real key: keys are read from the environment only")
        if not provider.get("models") or not any(m.get("id") == model.get("model")
                                                 for m in provider["models"]):
            out.append("aelix_provider.models has no entry for the model")
    serving = model.get("serving") or {}
    for key in ("backend", "context_window", "output_reservation", "tokenizer", "quantization"):
        if key not in serving:
            out.append(f"serving.{key} is missing")
    if serving.get("backend") == "ollama":
        for key in ("backend_version", "endpoint", "model_tag", "manifest_digest", "server_env"):
            if not serving.get(key):
                out.append(f"serving.{key} is missing")
    return out


# --- scoring --------------------------------------------------------------------------------------

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


def numbers_in(text: str) -> list[float]:
    out = []
    for match in NUMBER.finditer(text):
        raw = match.group(0).rstrip(",").replace(",", "")
        with contextlib.suppress(ValueError):
            out.append(float(raw))
    return out


def number_matches(found: float, value: float, check: dict) -> bool:
    if "rel_tol" in check:
        return abs(found - value) <= abs(value) * float(check["rel_tol"])
    return abs(found - value) <= float(check.get("abs_tol", 0))


def id_pattern(value: str) -> re.Pattern:
    """An id as a whole token: not inside a longer id (ASCII boundaries, since Korean letters are
    word characters to `re`, and `e41의` cites e41)."""
    return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(value) + r"(?![A-Za-z0-9_]|-[A-Za-z0-9])")


def id_present(text: str, value: str) -> bool:
    return bool(id_pattern(value).search(text))


def claim_segments(text: str, claims) -> dict[str, str]:
    """For each claim id, the text from each of its mentions to the next mention of another claim."""
    marks = sorted((m.start(), claim) for claim in claims for m in id_pattern(claim).finditer(text))
    out: dict[str, list[str]] = {claim: [] for claim in claims}
    for i, (start, claim) in enumerate(marks):
        end = len(text)
        for later, other in marks[i + 1:]:
            if other != claim:
                end = later
                break
        out[claim].append(text[start:end])
    return {claim: "\n".join(parts) for claim, parts in out.items()}


@dataclass
class Evidence:
    """What a run left to score: the answers' text, the tool calls and the project's changes."""
    text: str
    last_turn: str
    calls: list
    tools: dict
    state: dict
    existing_ids: set


def run_check(check: dict, ev: Evidence, words: dict) -> dict:
    ctype = check["type"]
    text = ev.last_turn if check.get("scope") == "last_turn" else ev.text
    result: dict = {"id": check["id"], "kind": check["kind"], "type": ctype,
                    "required": check.get("required", True), "what": check.get("what")}
    passed, detail, review = False, None, False
    if ctype == "text_any":
        hits = contains_any(text, check["values"])
        passed, detail = bool(hits), {"found": hits}
    elif ctype == "text_all":
        hits = contains_any(text, check["values"])
        missing = [v for v in check["values"] if v not in hits]
        passed, detail = not missing, {"missing": missing}
    elif ctype == "number":
        found = [n for n in numbers_in(text) if number_matches(n, float(check["value"]), check)]
        passed, detail = bool(found), {"found": found[:5]}
    elif ctype == "percent":
        tol = float(check.get("abs_tol_pp", 0.1))
        want = abs(float(check["value"])) * 100
        found = []
        for match in PERCENT.finditer(text):
            with contextlib.suppress(ValueError):
                value = float(match.group(1).replace(",", ""))
                if abs(value - want) <= tol + 1e-9:
                    found.append(match.group(0))
        passed, detail = bool(found), {"found": found[:5]}
    elif ctype == "number_near":
        lines = [line for line in text.splitlines()
                 if contains_any(line, check["words"])
                 and any(number_matches(n, float(check["value"]), check) for n in numbers_in(line))]
        passed, detail = bool(lines), {"lines": [line[:160] for line in lines[:3]]}
    elif ctype == "cooccur":
        lines = [line for line in text.splitlines()
                 if all(v.casefold() in line.casefold() for v in check["all"])]
        passed, detail = bool(lines), {"lines": [line[:160] for line in lines[:3]]}
    elif ctype == "claim_verdicts":
        segments = claim_segments(text, list(check["claims"]))
        per = {}
        for claim, expected in check["claims"].items():
            segment = segments.get(claim, "")
            seen = [v for v in VERDICTS if contains_any(segment, words.get(f"verdict_{v}", []))]
            ok = expected in seen
            per[claim] = {"expected": expected, "detected": seen, "mentioned": bool(segment),
                          "passed": ok}
            if ok and len(seen) > 1:
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
        unless = words_of(check["unless"], words, check.get("reason_words", ()))
        if check.get("pattern_kind") == "ids":
            patterns = [id_pattern(v) for v in check["patterns"]]
        else:
            patterns = [re.compile(p, re.IGNORECASE) for p in check["patterns"]]
        fired = []
        for line in text.splitlines():
            if any(p.search(line) for p in patterns) and not contains_any(line, unless):
                fired.append(line.strip()[:200])
        # A critical check passes when it does not fire.
        passed, detail = not fired, {"lines": fired[:5]}
        review = bool(fired)
    elif ctype == "new_records":
        records = [r for r in ev.state.get("new_records", [])
                   if not check.get("kinds") or r.get("kind") in check["kinds"]]
        if check.get("text_any"):
            records = [r for r in records if contains_any(str(r.get("text", "")), check["text_any"])]
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
    return {
        "answer_status": answer["status"],
        "answer_sha256": sha256_bytes(json.dumps(answer, ensure_ascii=False, sort_keys=True).encode()),
        "result": "success" if success else "failure",
        "checks": checks,
        "fact_recall": {"passed": sum(c["passed"] for c in facts), "total": len(facts)},
        "evidence": {"passed": sum(c["passed"] for c in evidence), "total": len(evidence),
                     "cited": [i for i in answer.get("evidence_ids", []) if id_present(ev.text, i)]},
        "fabricated_ids": fabricated_ids(ev.text, ev.existing_ids),
        "critical": critical,
        "needs_review": [c["id"] for c in checks if c.get("needs_review")],
        "heuristic": "text checks are substring and line rules; needs_review lists what a person "
                     "confirms (#24)",
    }


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


def token_totals(messages: list, api: str, *, scripted: bool = False) -> dict:
    """The run's tokens from aelix's per-message usage, by the adapter's convention."""
    with_usage = [m for m in messages if m.get("usage")]
    out: dict = {"requests": len(messages), "requests_with_usage": len(with_usage),
                 "complete": bool(messages) and len(with_usage) == len(messages), "api": api,
                 "per_request": [m.get("usage") for m in messages]}
    if scripted:
        out["source"] = "scripted fake server: fixed numbers, not a measurement"
    else:
        out["source"] = "the model API's usage as aelix 0.1.0b2 records it on each assistant message"
    names = ("input_uncached", "input_cached", "cache_write", "output", "reasoning")
    if not with_usage:
        reason = "no request reported usage" if messages else "no model request was made"
        out.update({name: not_measured(reason) for name in names})
        return out

    def total(key: str) -> int:
        return sum(int(m["usage"].get(key) or 0) for m in with_usage)

    missing = TOKEN_NOT_REPORTED.get(api, {})
    if api == "openai-completions":
        out["input_uncached"] = measured(total("input") - total("cache_read"))
        out["input_cached"] = measured(total("cache_read"))
        out["reasoning"] = not_measured(missing["reasoning"])
    elif api == "openai-responses":
        out["input_uncached"] = measured(total("input"))
        out["input_cached"] = measured(total("cache_read"))
        out["reasoning"] = measured(total("reasoning"))
    else:
        out.update({name: not_measured(f"no usage convention known for api {api}") for name in names})
        return out
    out["output"] = measured(total("output"))
    out["cache_write"] = not_measured(missing["cache_write"])
    if not out["complete"]:
        for name in names:
            if out[name]["status"] == "measured":
                out[name]["partial"] = True
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
        new_runs.append({"id": name, "status": record.get("status"),
                         "script": (record.get("script") or {}).get("path"),
                         "charts": [c.get("id") for c in record.get("charts") or []],
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
    work.mkdir(parents=True)
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

    status = git("status", "--porcelain", "--untracked-files=no")
    extension = checkout / GENERATIONS[generation]["extension"]
    return {"path": str(checkout), "generation": generation, "kit_version": kit_version,
            "commit": git("rev-parse", "HEAD"), "dirty": None if status is None else bool(status),
            "extension": GENERATIONS[generation]["extension"],
            "extension_sha256": sha256_file(extension)}


def aelix_version(command: list[str]) -> str:
    try:
        done = subprocess.run([*command, "--version"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=600, env=isolated_env())
    except (OSError, subprocess.SubprocessError) as exc:
        raise HarnessError(f"aelix could not be started ({shlex.join(command)}): {exc}") from exc
    if done.returncode != 0:
        raise HarnessError(f"aelix --version failed ({done.returncode}): {done.stderr[-800:]}")
    return done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "?"


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
                owner.requests.append({"k": k, "messages": len(body.get("messages") or []),
                                       "tools": [t.get("function", {}).get("name")
                                                 for t in body.get("tools") or []],
                                       "stream": body.get("stream")})
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
    return [*session.aelix_command, "--mode", "json", "-p", *requests,
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
            "serving_check": session.serving}


def write_models_json(session: Session, run_dir: Path, port: int | None) -> None:
    provider = session.model.get("aelix_provider")
    if provider is None:
        return
    provider = json.loads(json.dumps(provider).replace("{port}", str(port or 0)))
    write_json(run_dir / "agent" / "models.json", {"providers": {session.model["provider"]: provider}})


def run_key(session: Session, task_id: str, repeat: int) -> str:
    return f"{task_id}-{session.model['id']}-{session.surface}-{session.proposal}-r{repeat}"


def base_record(session: Session, task: dict, repeat: int, key: str) -> dict:
    answer = session.config.answers[task["id"]]
    return {
        "schema": RUN_SCHEMA, "run": key, "task": task["id"], "title": task.get("title"),
        "gates": task.get("gates"), "q11_request": task.get("q11_request"), "repeat": repeat,
        "seed": session.seed, "model": model_record(session),
        "host": {"aelix": session.aelix_version, "aelix_command": session.aelix_command,
                 "python": platform.python_version(), "platform": platform.platform(),
                 "machine": platform.machine()},
        "kit": session.checkout,
        "conditions": {"surface": session.surface, "proposal": session.proposal},
        "fixture": {**session.config.fixtures[task["fixture"]], "seed": session.seed},
        "answer": {"status": answer["status"], "confirmed_by": answer.get("confirmed_by")},
    }


def redact(paths: list[Path], secrets: list[str]) -> bool:
    """Replace a secret's value wherever a run wrote it. True when one was found."""
    found = False
    for path in paths:
        if not path.is_file():
            continue
        data = original = path.read_bytes()
        for secret in secrets:
            if secret:
                data = data.replace(secret.encode(), b"<redacted>")
        if data != original:
            path.write_bytes(data)
            found = True
    return found


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
        if reason.startswith("api_key_missing"):
            record["command_to_run"] = command_line(session, task, repeat)
        write_json(run_dir / "run.json", record)
        return record
    tools = tools_of(session)
    fixture = ensure_fixture(session.config.fixtures[task["fixture"]], session.seed, session.fixtures_root)
    record["fixture"]["digest"] = read_json(fixture / "manifest.json").get("digest")
    work = run_dir / "work"
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
        write_models_json(session, run_dir, fake.port if fake else None)
        argv = build_argv(session, requests, extension_path, tools_argv)
        write_json(run_dir / "command.json", {
            "argv": argv, "cwd": str(project), "set_env": set_vars,
            "unset_env": "AELIX_ANALYTICS_*, AELIX_LAB_*, " + ", ".join(ISOLATED_KEYS),
            "inherited_env": "the rest of the caller's environment; values are not recorded",
            "secret_env": session.model.get("api_key_env")})
        record.update(requests=requests, before=before, started_at=now_iso())
        ports = processes()
        started = time.perf_counter()
        timed_out = False
        with open(run_dir / "events.jsonl", "wb") as stdout, open(run_dir / "stderr.txt", "wb") as stderr:
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
        elapsed = round(time.perf_counter() - started, 1)
        if fake:
            write_json(run_dir / "fake-requests.json", fake.requests)
    if fake and fake.requests:
        offered = {"tools": fake.requests[0]["tools"], "source": "the dry run's first request"}
    elif tools_argv:
        offered = {"tools": tools_argv[1].split(","), "source": "--tools"}
    else:
        offered = {"tools": None, "source": "aelix's default list: aelix does not report the tools it "
                                            "sends; a dry run with the same aelix shows it"}
    record.update(finished_at=now_iso(), elapsed_s=elapsed, exit_code=code, timed_out=timed_out,
                  timeout_s=timeout, offered_tools=offered)
    finish_record(session, task, record, run_dir, project, extra_checks, scripted)
    if redact([run_dir / name for name in ("events.jsonl", "stderr.txt", "command.json", "run.json",
                                           "fake-requests.json")], session.secrets):
        record["secret_redacted"] = True
    write_json(run_dir / "run.json", record)
    return record


def finish_record(session: Session, task: dict, record: dict, run_dir: Path, project: Path,
                  extra_checks: list, scripted: bool) -> None:
    """Read the events and the project, count and score. `score` calls it again on a stored run."""
    tools = GENERATIONS[record["kit"]["generation"]]["tools"]
    lines = (run_dir / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    tr = parse_events(lines)
    state = changes(project, record["before"])
    calls = tool_summary(tr.calls, tools)
    ev = Evidence(text="\n\n".join(tr.answers), last_turn=tr.answers[-1] if tr.answers else "",
                  calls=tr.calls, tools=tools, state=state, existing_ids=set(state["existing_ids"]))
    answer = session.config.answers[task["id"]]
    result = score(answer, ev, session.config.words, extra_checks)
    stop = tr.turns[-1]["stop_reason"] if tr.turns else None
    if record.get("timed_out"):
        outcome = "timeout"
    elif record.get("exit_code") != 0 or not tr.ended or stop in ("error", "aborted") or \
            len(tr.turns) < len(record["requests"]):
        outcome = "error"
    else:
        outcome = result["result"]
    observed = sorted({m.get("model") for m in tr.messages if m.get("model")})
    record.update(
        outcome=outcome, session=tr.session,
        turns=[{k: t[k] for k in ("request", "answer", "messages", "stop_reason", "error")}
               for t in tr.turns],
        model_requests=len(tr.messages), observed_models=observed, auto_retries=tr.retries,
        compactions=tr.compactions, unparsed_event_lines=tr.unparsed,
        tool_calls=[{**{k: c.get(k) for k in ("id", "name", "role", "args", "is_error", "error_class",
                                                "turn")},
                     "result_head": (c.get("result") or "")[:400]} for c in tr.calls],
        tool_summary=calls,
        tokens=token_totals(tr.messages, session.model["api"], scripted=scripted),
        changes={k: v for k, v in state.items() if k != "existing_ids"},
        score=result,
        proposal_mentioned=bool(contains_any(ev.text if session.proposal != "requested" else
                                             "\n\n".join(tr.answers[:-1]),
                                             ["재사용", "skill", "스킬", "다시 쓸", "reuse"])),
        errors_recovered=recovered(state))


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
    prefix = f"{env}=<key> " if env else ""
    return prefix + shlex.join(parts)


# --- summary --------------------------------------------------------------------------------------

def token_value(tokens: dict | None, name: str):
    value = (tokens or {}).get(name) or {}
    return value.get("value") if value.get("status") == "measured" else None


def summarize(out: Path) -> dict:
    runs = []
    for path in sorted((out / "runs").glob("*/run.json")):
        record = read_json(path)
        sc = record.get("score") or {}
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
            "input_uncached": token_value(record.get("tokens"), "input_uncached"),
            "input_cached": token_value(record.get("tokens"), "input_cached"),
            "output": token_value(record.get("tokens"), "output"),
            "elapsed_s": record.get("elapsed_s"), "record": str(path.relative_to(out))})
    groups: dict[tuple, list] = {}
    for run in runs:
        groups.setdefault((run["model"], run["task"], run["surface"], run["proposal"]), []).append(run)
    aggregate = []
    for (model, task, surface, proposal), items in sorted(groups.items()):
        executed = [r for r in items if r["outcome"] != "not_run"]
        totals = [(r["input_uncached"] or 0) + (r["input_cached"] or 0) + (r["output"] or 0)
                  for r in executed if r["output"] is not None]
        aggregate.append({"model": model, "task": task, "surface": surface, "proposal": proposal,
                          "runs": len(items), "not_run": len(items) - len(executed),
                          "success": sum(r["outcome"] == "success" for r in items),
                          "critical": sum(bool(r["critical"]) for r in executed),
                          "argument_errors": sum(r["argument_errors"] or 0 for r in executed),
                          "median_total_tokens": statistics.median(totals) if totals else None})
    summary = {"schema": SUMMARY_SCHEMA, "written_at": now_iso(), "runs": runs, "aggregate": aggregate,
               "note": "success counts every executed run in the denominator; answers marked draft "
                       "give provisional scores"}
    write_json(out / "summary.json", summary)
    write_text(out / "summary.md", render_summary(summary))
    return summary


def render_summary(summary: dict) -> str:
    lines = ["# Model evaluation runs", "", f"Written {summary['written_at']}. Generated from summary.json.",
             "", "| run | outcome | answer | facts | evidence | critical | review | calls | arg errors "
             "| input uncached | input cached | output | s |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in summary["runs"]:
        facts = r["fact_recall"] or {}
        ev = r["evidence"] or {}

        def cell(v):
            return "—" if v is None else (f"{v:,}" if isinstance(v, int) else str(v))

        lines.append("| " + " | ".join([
            r["run"], str(r["outcome"]) + (f" ({r['reason']})" if r.get("reason") else ""),
            r["answer_status"], f"{facts.get('passed', '—')}/{facts.get('total', '—')}",
            f"{ev.get('passed', '—')}/{ev.get('total', '—')}", ", ".join(r["critical"] or []) or "0",
            ", ".join(r["needs_review"] or []) or "—", cell(r["tool_calls"]), cell(r["argument_errors"]),
            cell(r["input_uncached"]), cell(r["input_cached"]), cell(r["output"]), cell(r["elapsed_s"])])
            + " |")
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
            if check["type"] == "claim_verdicts":
                cited |= set(check["claims"])
            if check["type"] == "cooccur":
                cited |= {v for v in check["all"] if RUN_ID.fullmatch(v)}
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
    model = config.models[args.model]
    checkout = checkout_info(Path(args.checkout).resolve() if args.checkout else ROOT)
    out = Path(args.out).resolve()
    prepare_out(out)
    command = shlex.split(args.aelix_command) if args.aelix_command else list(AELIX_COMMAND)
    key_env = model.get("api_key_env")
    missing_key = bool(key_env and not os.environ.get(key_env))
    serving = None
    if not missing_key and model["serving"].get("backend") == "ollama":
        serving = serving_check(model)
    session = Session(config=config, model=model, out=out, checkout=checkout, surface=args.surface,
                      proposal=args.proposal, seed=args.seed, aelix_command=command, aelix_version=None,
                      fixtures_root=Path(args.fixtures).resolve() if args.fixtures else out / "fixtures",
                      timeout=args.timeout, script=Path(args.script).resolve() if args.script else None,
                      serving=serving, uv=None,
                      secrets=[os.environ[key_env]] if key_env and os.environ.get(key_env) else [])
    runnable = [t for t in task_ids if not not_run_reason(session, config.tasks[t])]
    if runnable:
        session.aelix_version = aelix_version(command)
        session.uv = link_uv(out)
    records = []
    for task_id in task_ids:
        for repeat in range(args.first_repeat, args.first_repeat + args.repeats):
            record = run_one(session, config.tasks[task_id], repeat)
            records.append(record)
            print(f"{record['run']}: {record['outcome']}"
                  + (f" ({record['reason']})" if record.get("reason") else "")
                  + (f" — {record['score']['fact_recall']['passed']}/{record['score']['fact_recall']['total']}"
                     f" facts, {record['tool_summary']['calls']} calls, "
                     f"{record['tool_summary']['argument_errors']} argument errors, {record['elapsed_s']} s"
                     if record.get("score") else ""), flush=True)
    summarize(out)
    print(f"summary: {out / 'summary.json'}")
    if records and all(r["outcome"] == "not_run" for r in records):
        for record in records:
            if record.get("command_to_run"):
                print(f"to run: {record['command_to_run']}")
        return 3
    return 0


def cmd_score(args) -> int:
    config = load_config()
    out = Path(args.out).resolve()
    if not (out / "runs").is_dir():
        raise UsageError(f"{out} holds no runs/")
    for path in sorted((out / "runs").glob("*/run.json")):
        record = read_json(path)
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
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--aelix-command", help=f"default: {shlex.join(AELIX_COMMAND)}")
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
