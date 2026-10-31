"""Compare a model's runs with aelix-analytics (A) and with aelix alone (V) by the rules of
docs/design/PRODUCT-EVALUATION.md (§6 metrics, §8 rules), and diagnose where the product could improve (§7).

    python tools/model_eval_compare.py OUT [OUT ...] --out DIR [--fixtures DIR] [--confirmations FILE]

Each OUT is an output directory of tools/model_eval.py; give all of a campaign's (one per model, a re-run).
A run is in the comparison when its surface is `default` (A) or `vanilla` (V) and its proposal condition is
`unrequested`; the other conditions (Q5, Q11) are listed apart. When the same run (model, kit, task,
condition, repeat) is in more than one OUT, the one that executed is kept (a re-run replaces an `error` or
`not_run`; between two that executed, the later), and the replaced ones are listed. A model's runs with
another kit than the one most of its compared runs used (an older checkout's, #9) are listed apart too.

- The answer's measures are the score's required fact, evidence and limit checks (§6) and the answer's
  result: those checks and the critical ones, in a run that finished (`success` or `failure`). A critical
  check that fired counts as a context error until a person confirms it: --confirmations FILE
  (`{"schema": "aelix-analytics.model-eval-confirmations/1", "confirmed_by": "...", "runs": {RUN:
  {"finished_at": "...", "checks": {CHECK: "real" | "misread"}}}}`). A confirmation holds for the run that
  finished at that time only: a re-run under the same name is not confirmed by it (listed as stale). A
  run that failed only by critical checks a person found misread is an answer success.
- Rules R1-R7 are judged per model; the tier is the model configuration's role (`frontier` is the upper
  model). A rule is `not_run` when its data is missing: Part II's recall before its chains run, or a task of
  its scope that has not run the same repeats in both conditions (incomplete). One whose verdict would
  change if a person confirmed or rejected the critical checks that fired is `provisional`.
- A chain (Part II) gives one run per session. R4 pairs each recall check of a chain's repeat in A and V,
  leaves out a pair where either side had nothing to recall, and is `withheld` when more than half are left
  out; redundant work is the analysis calls of the sessions that only ask what earlier ones found. R5 counts
  each chain's first session as an analysis task, R6 a chain's repeat as the sum of its sessions, R7 A's
  recall.
- Diagnosis: for each failed fact or evidence check with values a text can hold (ids and numbers), whether
  the run received them (its tool results and, for A, its starting context block), whether the kit's reads
  would have given them (its context block, status and the reads of the task's ids, on a fresh copy of the
  fixture's project: --fixtures), or neither. Tool flow per condition (calls by tool, errors, reads of the
  kit's raw files), cost, answers in another script than the request's, and a list of improvement candidates.

Writes DIR/comparison.json and DIR/comparison.md. Exit status: 0 written, 2 a usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import model_eval as me

SCHEMA = "aelix-analytics.model-eval-comparison/1"
CONFIRMATIONS_SCHEMA = "aelix-analytics.model-eval-confirmations/1"
COMPARED = {"default": "A", "vanilla": "V"}
# The rules' numbers, fixed by the owner before the runs (docs/design/PRODUCT-EVALUATION.md §8, O3).
FLOOR = 2 / 3
COST_FACTOR = 1.2
HARM_ALLOWANCE = 1  # runs per task
# R4 is withheld when more than this share of the A-V pairs of recall checks is not applicable (O7).
WITHHELD_SHARE = 1 / 2
# Calls that read the kit's own files directly (journal, run records, the state directory).
RAW_FILE = re.compile(r"journal\.jsonl|run\.json|\.analytics/|(?:^|[\s/\"'])runs/")
# A call that runs an analysis (§6 redundant work): the kit's run and query tools (both generations of
# names), or a shell command that runs Python, uv or DuckDB.
ANALYSIS_TOOLS = ("analytics_run", "analytics_query", "lab_run", "lab_query")
ANALYSIS_SHELL = re.compile(r"(?<![\w./-])(?:python3?|uv\s+run|duckdb)(?![\w-])")
HANGUL = re.compile(r"[가-힣]")
HAN = re.compile(r"[一-鿿]")
KANA = re.compile(r"[ぁ-ゟ゠-ヿ]")
# A number as a text writes it; it is held only where it is not a piece of another (2.4 in 12.4 or 2.45).
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def tier(model_config: dict) -> str:
    return "upper" if model_config.get("role") == "frontier" else "lower"


def answer_text(record: dict) -> str:
    return "\n\n".join(t.get("answer") or "" for t in record.get("turns") or [])


def script_flag(record: dict) -> str | None:
    """`han` or `kana` when an answer to a Korean request holds Chinese characters or Japanese kana (in the
    pilot, Qwen answered twice in Chinese and gpt once in Japanese)."""
    requests = " ".join(record.get("requests") or [])
    text = answer_text(record)
    if not HANGUL.search(requests):
        return None
    if len(HAN.findall(text)) >= 10:
        return "han"
    if len(KANA.findall(text)) >= 10:
        return "kana"
    return None


def received_texts(run_dir: Path) -> str:
    """What the model was given: its tool results (events.jsonl) and its starting context block (A)."""
    parts = []
    events = run_dir / "events.jsonl"
    if events.is_file():
        tr = me.parse_events(events.read_text(encoding="utf-8", errors="replace").splitlines())
        parts += [str(c.get("result") or "") for c in tr.calls]
    block = run_dir / "context_block.txt"
    if block.is_file():
        parts.append(block.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def check_tokens(check: dict) -> list[str]:
    """The strings a text holds when it holds a check's value: an id as written; a number with and
    without thousands separators; a percentage to one decimal. Word checks give none (a reason is not
    a value a read returns)."""
    ctype = check.get("type")
    if ctype in ("ids_all", "ids_any"):
        return [str(v) for v in check.get("values") or []]
    if ctype == "percent" or (ctype == "number_near" and check.get("percent")):
        value = check.get("value")
        if isinstance(value, (int, float)):
            return [f"{value * 100:.1f}"]
    if ctype in ("number", "number_near"):
        value = check.get("value")
        if isinstance(value, (int, float)):
            whole = int(value) if float(value).is_integer() else None
            return [str(whole), f"{whole:,}"] if whole is not None else [f"{value}", f"{value:,}"]
    return []


def held(text: str, token: str) -> bool:
    if NUMBER.fullmatch(token):
        return re.search(r"(?<![\d.,])" + re.escape(token) + r"(?!\d|[.,]\d)", text) is not None
    if re.fullmatch(r"[A-Za-z]?\d[\w-]*", token):
        return me.id_present(text, token)
    return token in text


def needed(check: dict, tokens: list[str]) -> int:
    """How many of a check's strings a text must hold to hold its value: every id of ids_all, `min` of
    ids_any, one form of a number."""
    if check.get("type") == "ids_all":
        return len(tokens)
    if check.get("type") == "ids_any":
        return int(check.get("min", 1))
    return 1


# The kit's reads of a project, run on a fresh copy of a fixture's project (what A could have been given).
KIT_READS = """\
import json, re, sys
from pathlib import Path
from aelix_analytics import core
p = core.Project(Path(sys.argv[1]))
out = {"context_block": core.context_block(p), "status": core.status_text(core.status(p)), "ids": {}}
for record_id in json.loads(sys.argv[2]):
    try:
        # A run is read as run:ID (the id read of analytics_status).
        name = "run:" + record_id if re.fullmatch(r"\\d{8}-\\d{6}-.+", record_id) else record_id
        out["ids"][record_id] = core.retrieval_text(core.retrieve(p, id=name))
    except Exception as exc:  # noqa: BLE001 - an id the kit cannot read is a finding, not a failure
        out["ids"][record_id] = "unreadable: " + type(exc).__name__
print(json.dumps(out, ensure_ascii=False))
"""


def kit_reads(config: me.Config, task: dict, fixtures: Path, cache: dict) -> dict | None:
    """The kit's context block, status and id reads on a fresh copy of the task's fixture project, once
    per task; None for a task that starts from a fresh project or a fixture that is not there."""
    if task["id"] in cache:
        return cache[task["id"]]
    result = None
    if task["setup"] == "fixture":
        spec = config.fixtures[task["fixture"]]
        fixture = fixtures / me.fixture_name(spec, 0)
        if (fixture / "manifest.json").is_file():
            answer = config.answers[task["id"]]
            ids = sorted({*answer.get("evidence_ids", []),
                          *[t for c in answer["checks"] if c.get("type") in ("ids_all", "ids_any")
                            for t in check_tokens(c)]})
            with tempfile.TemporaryDirectory(prefix="compare-") as tmp:
                env = me.isolated_env()
                env["AELIX_ANALYTICS_TRUST_FILE"] = str(Path(tmp) / "trust.json")
                project = me.prepare_work(task, fixture, Path(tmp) / "work", me.ROOT / "src", env)
                done = subprocess.run([sys.executable, "-c", KIT_READS, str(project), json.dumps(ids)],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      env={**env, "PYTHONPATH": str(me.ROOT / "src")}, timeout=600)
                if done.returncode == 0:
                    result = json.loads(done.stdout)
    cache[task["id"]] = result
    return result


def availability(config: me.Config, record: dict, run_dir: Path, fixtures: Path | None, cache: dict) -> list[dict]:
    """For each failed fact or evidence check with values: received / available_not_received / not_available."""
    sc = record.get("score") or {}
    # A fact the project records (resume, evidence, history: E1-E3) or any evidence id; a value the task
    # asks the model to compute (E6) is not something a read could have given.
    recorded = bool(set(config.tasks[record["task"]].get("areas") or []) & {"E1", "E2", "E3"})
    failed = [c for c in sc.get("checks") or []
              if c.get("required") and not c.get("passed")
              and (c.get("kind") == "evidence" or (c.get("kind") == "fact" and recorded))]
    if not failed:
        return []
    answer = {c["id"]: c for c in config.answers[record["task"]]["checks"]}
    received = received_texts(run_dir)
    reads = kit_reads(config, config.tasks[record["task"]], fixtures, cache) if fixtures else None
    offered = "\n".join([reads["context_block"], reads["status"], *reads["ids"].values()]) if reads else None
    out = []
    for check in failed:
        spec = answer.get(check["id"], {})
        tokens = check_tokens(spec)
        if not tokens:
            continue
        need = needed(spec, tokens)
        got = [t for t in tokens if held(received, t)]
        if len(got) >= need:
            where = "received"
        elif offered is None:
            where = "unknown"
        elif sum(t in got or held(offered, t) for t in tokens) >= need:
            where = "available_not_received"
        else:
            where = "not_available"
        out.append({"check": check["id"], "kind": check["kind"], "where": where,
                    "missing": [t for t in tokens if t not in got], "needed": need})
    return out


def analysis_call(call: dict) -> bool:
    if call.get("name") in ANALYSIS_TOOLS:
        return True
    return call.get("name") == "bash" and bool(ANALYSIS_SHELL.search(str((call.get("args") or {}).get("command") or "")))


def kit_id(record: dict) -> str:
    """The kit a run used: its generation, version and commit."""
    kit = record.get("kit") or {}
    return f"{kit.get('generation')}:{kit.get('kit_version')}:{str(kit.get('commit') or '')[:12]}"


def run_row(config: me.Config, out_dir: Path, run_dir: Path, record: dict, confirmations: dict,
            fixtures: Path | None, cache: dict) -> dict:
    sc = record.get("score") or {}
    checks = sc.get("checks") or []
    conditions = record.get("conditions") or {}
    model_id = (record.get("model") or {}).get("config")
    name = record.get("run") or run_dir.name
    entry = confirmations.get(name)
    stale = entry is not None and entry.get("finished_at") != record.get("finished_at")
    confirmed = {} if entry is None or stale else entry.get("checks") or {}
    fired = [c["id"] for c in checks if c.get("kind") == "critical" and c.get("passed") is False]
    real = [c for c in fired if confirmed.get(c) != "misread"]
    unconfirmed = [c for c in fired if c not in confirmed]
    failed_answer = [c["id"] for c in checks if c.get("required") and c.get("passed") is False
                     and c.get("kind") in me.ANSWER_KINDS]
    executed = record.get("outcome") in ("success", "failure", "error", "timeout")
    finished = record.get("outcome") in ("success", "failure")
    answer_success = finished and not failed_answer and not real
    # The same, were every unconfirmed critical check found misread: what a person's confirmation could make it.
    if_misread = finished and not failed_answer and not [c for c in real if c in unconfirmed]
    tokens = record.get("tokens") or {}
    values = [((tokens.get(k) or {}).get("value")) for k in ("input_uncached", "input_cached", "output")]
    calls = record.get("tool_calls") or []

    def counts(kind):
        """The required checks of a kind that applied (§6: the facts, ids and limits the answer must give)."""
        applied = [c for c in checks if c.get("kind") == kind and c.get("required") and c.get("passed") is not None]
        return [sum(bool(c["passed"]) for c in applied), len(applied)]

    chain = record.get("chain") or {}
    recall = {c["id"]: c.get("passed") for c in checks if c.get("kind") == "recall"}
    recalled = [sum(v is True for v in recall.values()), sum(v is not None for v in recall.values())]

    return {
        "run": name, "out": str(out_dir), "model": model_id, "task": record.get("task"), "kit": kit_id(record),
        "condition": COMPARED.get(conditions.get("surface")), "surface": conditions.get("surface"),
        "with_kit": conditions.get("kit", True),
        "proposal": conditions.get("proposal"), "repeat": record.get("repeat"),
        "outcome": record.get("outcome"), "reason": record.get("reason"), "executed": executed,
        "answer_success": bool(answer_success), "answer_success_if_misread": bool(if_misread),
        "session": chain.get("session"), "recall_only": bool(chain.get("recall_only")),
        "fact": counts("fact"), "evidence": counts("evidence"),
        # Resumption (R1): a task's state facts; in a chain, what a working session recalls of the ones before it.
        "resume": counts("fact") if not chain else [0, 0] if chain.get("recall_only") else recalled,
        "recall": recalled, "recall_checks": recall,
        "analysis_calls": sum(analysis_call(c) for c in calls),
        "limits": counts("uncertainty"), "critical_fired": fired, "critical_real": real,
        "critical_unconfirmed": unconfirmed, "stale_confirmation": stale,
        "fabricated_ids": sc.get("fabricated_ids") or [],
        "tokens": values if all(v is not None for v in values) and tokens.get("complete") else None,
        "requests": record.get("model_requests"), "tool_calls": len(calls),
        "tool_errors": sum(bool(c.get("is_error")) for c in calls),
        "argument_errors": (record.get("tool_summary") or {}).get("argument_errors") or 0,
        "tools": dict(Counter(c.get("name") for c in calls)),
        "raw_reads": sum(bool(RAW_FILE.search(json.dumps(c.get("args") or {}, ensure_ascii=False)))
                         for c in calls if c.get("name") in ("read", "bash", "grep", "find", "ls")),
        "elapsed_s": record.get("elapsed_s"), "script": script_flag(record),
        "context_block_bytes": (record.get("context_block") or {}).get("bytes"),
        "availability": availability(config, record, run_dir, fixtures, cache) if executed else [],
        "finished_at": record.get("finished_at"),
    }


def collect(config: me.Config, outs: list[Path], confirmations: dict, fixtures: Path | None):
    rows, replaced = {}, []
    cache: dict = {}
    for out in outs:
        for run_dir, record in me.stored_runs(out):
            if record is None or record.get("task") not in config.tasks:
                continue
            row = run_row(config, out, run_dir, record, confirmations, fixtures, cache)
            compared = row["condition"] is not None and row["proposal"] == "unrequested"
            key = (compared, row["model"], row["kit"], row["task"], row["surface"], row["proposal"], row["repeat"],
                   row["session"])
            kept = rows.get(key)
            if kept is None:
                rows[key] = row
            elif (row["executed"] and row["outcome"] != "error", row["finished_at"] or "") > (
                    kept["executed"] and kept["outcome"] != "error", kept["finished_at"] or ""):
                replaced.append(kept["run"] + " in " + kept["out"])
                rows[key] = row
            else:
                replaced.append(row["run"] + " in " + row["out"])
    compared = [r for k, r in rows.items() if k[0]]
    other = [r for k, r in rows.items() if not k[0]]
    # One kit per model: the one most of its compared runs used; the others' runs are listed apart.
    main_kit = {m: Counter(r["kit"] for r in compared if r["model"] == m).most_common(1)[0][0]
                for m in {r["model"] for r in compared}}
    apart = [{**r, "condition": None, "surface": f"{r['surface']} ({r['kit']})"} for r in compared
             if r["kit"] != main_kit[r["model"]]]
    compared = [r for r in compared if r["kit"] == main_kit[r["model"]]]
    return compared, other + apart, replaced


def rate(rows: list[dict], key: str) -> tuple[int, int]:
    return sum(r[key][0] for r in rows), sum(r[key][1] for r in rows)


def ratio(pair: tuple[int, int]) -> float | None:
    return pair[0] / pair[1] if pair[1] else None


def total_tokens(row: dict) -> int | None:
    return sum(row["tokens"]) if row["tokens"] else None


def successful_costs(runs: list[dict]) -> list:
    """The total tokens of each answer-successful run; a chain's repeat is one run, its sessions' sum, and
    successful when every session's answer was."""
    if not any(r["session"] for r in runs):
        return [total_tokens(r) for r in runs if r["answer_success"]]
    out = []
    for repeat in sorted({r["repeat"] for r in runs}):
        steps = [r for r in runs if r["repeat"] == repeat]
        if all(r["answer_success"] for r in steps):
            costs = [total_tokens(r) for r in steps]
            out.append(None if None in costs else sum(costs))
    return out


def median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def judge(config: me.Config, rows: list[dict]) -> dict:
    """R1-R7 for each model (docs/design/PRODUCT-EVALUATION.md §8)."""
    out = {}
    for model in sorted({r["model"] for r in rows}):
        mine = [r for r in rows if r["model"] == model and r["executed"]]
        tasks = sorted({r["task"] for r in mine})
        areas = {t: set(config.tasks[t].get("areas") or []) for t in tasks}
        by = {(t, c): [r for r in mine if r["task"] == t and r["condition"] == c] for t in tasks for c in "AV"}
        # A task is compared when both conditions ran the same repeats; in any other, a rule over it
        # would rest on one condition's runs alone, so the rule is not_run.
        incomplete = [t for t in tasks if sorted((r["repeat"], r["session"] or 0) for r in by[(t, "A")])
                      != sorted((r["repeat"], r["session"] or 0) for r in by[(t, "V")])]
        chains = [t for t in tasks if config.tasks[t].get("sessions")]
        upper = tier(config.models.get(model, {})) == "upper"
        rules = {}

        def pooled(selected, key, cond, by=by):
            return rate([r for t in selected for r in by[(t, cond)]], key)

        def below(selected, key, by=by):
            """The tasks where A's pooled rate is under V's (a tie, both full, is no difference: O6)."""
            return [t for t in selected if (ratio(rate(by[(t, "A")], key)) or 0) < (ratio(rate(by[(t, "V")], key)) or 0)]

        def missing(scope, what, incomplete=incomplete):
            if not scope:
                return {"verdict": "not_run", "reason": f"no task of {what} has run"}
            gaps = [t for t in scope if t in incomplete]
            if gaps:
                return {"verdict": "not_run", "reason": "tasks without the same repeats in both conditions",
                        "incomplete": gaps}
            return None

        resume = [t for t in tasks if areas[t] & {"E1", "E3"}]
        a, v = ratio(pooled(resume, "resume", "A")), ratio(pooled(resume, "resume", "V"))
        rules["R1"] = missing(resume, "E1 or E3") or (
            {"verdict": "not_run", "reason": "no required fact check"} if a is None or v is None else
            {"verdict": "pass" if a >= v and not below(resume, "resume") else "fail",
             "A": a, "V": v, "tasks": resume, "A_below_V": below(resume, "resume")})
        evidence_tasks = [t for t in tasks if "E2" in areas[t]]
        fabricated = {c: sum(len(r["fabricated_ids"]) for t in evidence_tasks for r in by[(t, c)]) for c in "AV"}
        ea, ev = ratio(pooled(evidence_tasks, "evidence", "A")), ratio(pooled(evidence_tasks, "evidence", "V"))
        la, lv = ratio(pooled(evidence_tasks, "limits", "A")), ratio(pooled(evidence_tasks, "limits", "V"))
        worse = {"evidence": below(evidence_tasks, "evidence"), "limits": below(evidence_tasks, "limits")}
        ok = ((ea is None or ev is None or ea >= ev) and (la is None or lv is None or la >= lv)
              and not worse["evidence"] and not worse["limits"] and fabricated["A"] <= fabricated["V"])
        rules["R2"] = missing(evidence_tasks, "E2") or {
            "verdict": "pass" if ok else "fail", "evidence": {"A": ea, "V": ev}, "limits": {"A": la, "V": lv},
            "fabricated_ids": fabricated, "tasks": evidence_tasks, "A_below_V": worse}
        # R3: context errors as confirmed (`real` keeps the unconfirmed ones) and as they would be were every
        # unconfirmed one misread; the verdict holds if it is the same both ways, else it is provisional.
        def errors(selected, cond, sure, by=by):
            return sum(len([c for c in r["critical_real"] if not sure or c not in r["critical_unconfirmed"]])
                       for t in selected for r in by[(t, cond)])
        compared = [t for t in tasks if t not in incomplete]
        a_all = {s: errors(tasks, "A", s) for s in (True, False)}  # every A run: the upper model's bar
        a_cmp = {s: errors(compared, "A", s) for s in (True, False)}
        v_cmp = {s: errors(compared, "V", s) for s in (True, False)}
        sure_fail = (upper and a_all[True] > 0) or a_cmp[True] > v_cmp[False]
        may_fail = (upper and a_all[False] > 0) or a_cmp[False] > v_cmp[True]
        r3 = {"context_errors": {"A": a_cmp[False], "V": v_cmp[False], "A_confirmed": a_cmp[True],
                                 "V_confirmed": v_cmp[True], "A_all_tasks": a_all[False]},
              "bar": "A = 0 and A <= V" if upper else "A <= V",
              "unconfirmed_runs": sorted(r["run"] for r in mine if r["critical_unconfirmed"])}
        if upper and a_all[True] > 0:
            r3["verdict"] = "fail"  # a confirmed error with the extension fails the upper model's bar
        elif incomplete:
            r3.update(verdict="not_run", reason="tasks without the same repeats in both conditions",
                      incomplete=incomplete)
        else:
            r3["verdict"] = "fail" if sure_fail else "provisional" if may_fail else "pass"
        rules["R3"] = r3
        # R4: each recall check of a chain's repeat in both conditions is a pair; a pair where either side
        # had nothing to recall (not applicable) leaves both (O7), and is counted apart.
        pairs, left_out = {"A": [0, 0], "V": [0, 0]}, 0
        for t in chains:
            for rep_a in by[(t, "A")]:
                rep_v = next((r for r in by[(t, "V")] if (r["repeat"], r["session"]) == (rep_a["repeat"], rep_a["session"])), None)
                for check, passed in rep_a["recall_checks"].items():
                    other = (rep_v or {}).get("recall_checks", {}).get(check)
                    if passed is None or other is None:
                        left_out += 1
                        continue
                    for cond, value in (("A", passed), ("V", other)):
                        pairs[cond][0] += bool(value)
                        pairs[cond][1] += 1
        redundant = {c: sum(r["analysis_calls"] for t in chains for r in by[(t, c)] if r["recall_only"]) for c in "AV"}
        total_pairs = pairs["A"][1] + left_out
        r4 = {"recall": {c: ratio(tuple(pairs[c])) for c in "AV"}, "pairs": pairs["A"][1], "not_applicable_pairs": left_out,
              "redundant_analysis_calls": redundant, "chains": chains}
        gap = missing(chains, "Part II")
        if gap is not None:
            rules["R4"] = gap
        elif total_pairs == 0 or left_out > WITHHELD_SHARE * total_pairs:
            rules["R4"] = {**r4, "verdict": "withheld",
                           "reason": "more than half of the recall pairs had nothing to recall in one condition"}
        else:
            ok = r4["recall"]["A"] > r4["recall"]["V"] and redundant["A"] <= redundant["V"]
            rules["R4"] = {**r4, "verdict": "pass" if ok else "fail"}
        # R5's units: each analysis task, and each chain's first session (the analysis the chain starts from).
        harm_tasks = [t for t in tasks if "E6" in areas[t] and t not in chains] + [f"{t}/s1" for t in chains]

        def unit(t, cond, by=by):
            if "/s" in t:
                task, step = t.split("/s")
                return [r for r in by[(task, cond)] if r["session"] == int(step)]
            return by[(t, cond)]

        def successes(t, cond, key):
            return sum(r[key] for r in unit(t, cond))
        # Short for sure: even were every unconfirmed critical check of A misread and V's real; maybe: the other way.
        sure_short = [t for t in harm_tasks if successes(t, "A", "answer_success_if_misread")
                      < successes(t, "V", "answer_success") - HARM_ALLOWANCE]
        may_short = [t for t in harm_tasks if successes(t, "A", "answer_success")
                     < successes(t, "V", "answer_success_if_misread") - HARM_ALLOWANCE]
        rules["R5"] = missing([t.split("/s")[0] for t in harm_tasks], "E6 or Part II") or {
            "verdict": "fail" if sure_short else "provisional" if may_short else "pass",
            "tasks": harm_tasks, "A_short": sure_short, "A_maybe_short": may_short,
            "answer_success": {t: {c: successes(t, c, "answer_success") for c in "AV"} for t in harm_tasks}}
        costly = []
        for t in compared:
            ca, cv = (median(successful_costs(by[(t, c)])) for c in "AV")
            if ca is not None and cv is not None and ca > COST_FACTOR * cv:
                costly.append({"task": t, "A": ca, "V": cv})
        rules["R6"] = {"verdict": "report", "over_1_2x": costly}
        # R7 is A's own floor: every A run of the resume tasks and of the chains, whatever V ran.
        resume_a = ratio(pooled(resume, "resume", "A"))
        recall_a = ratio(pooled(chains, "recall", "A"))
        r7 = {"resume_A": resume_a, "recall_A": recall_a, "floor": FLOOR}
        if not upper:
            r7["verdict"] = "report"
        elif (resume_a is not None and resume_a < FLOOR) or (recall_a is not None and recall_a < FLOOR):
            r7["verdict"] = "fail"
        elif resume_a is None or recall_a is None:
            r7["verdict"] = "not_run"
        else:
            r7["verdict"] = "pass"
        rules["R7"] = r7
        required = ["R1", "R2", "R3", "R4", "R5"] + (["R7"] if upper else [])
        verdicts = [rules[k]["verdict"] for k in required]
        gate = "fail" if "fail" in verdicts else ("pending" if any(v in ("not_run", "provisional", "withheld")
                                                                  for v in verdicts) else "pass")
        out[model] = {"tier": "upper" if upper else "lower", "tasks": tasks, "incomplete": incomplete,
                      "rules": rules, "gate": gate}
    return out


def summary_rows(rows: list[dict]) -> list[dict]:
    out = []
    rows = [{**r, "task": f"{r['task']}/s{r['session']}" if r.get("session") else r["task"]} for r in rows]
    for (model, task, cond), items in sorted(_group(rows, ("model", "task", "condition")).items()):
        executed = [r for r in items if r["executed"]]
        out.append({"model": model, "task": task, "condition": cond, "runs": len(items),
                    "not_run": len(items) - len(executed),
                    "answer_success": sum(r["answer_success"] for r in executed),
                    "fact": list(rate(executed, "fact")), "evidence": list(rate(executed, "evidence")),
                    "limits": list(rate(executed, "limits")),
                    "context_errors": sum(len(r["critical_real"]) for r in executed),
                    "fabricated_ids": sum(len(r["fabricated_ids"]) for r in executed),
                    "median_tokens": median([total_tokens(r) for r in executed]),
                    "median_tool_calls": median([r["tool_calls"] for r in executed]),
                    "median_elapsed_s": median([r["elapsed_s"] for r in executed]),
                    "tool_errors": sum(r["tool_errors"] for r in executed),
                    "raw_reads": sum(r["raw_reads"] for r in executed),
                    "recall": list(rate(executed, "recall")),
                    "analysis_calls": sum(r["analysis_calls"] for r in executed)})
    return out


def _group(rows, keys):
    groups = defaultdict(list)
    for r in rows:
        groups[tuple(str(r[k]) for k in keys)].append(r)
    return groups


def diagnose(rows: list[dict], other: list[dict]) -> dict:
    where = Counter()
    by_check = defaultdict(Counter)
    for r in rows + other:
        for a in r["availability"]:
            where[(r["condition"] or r["surface"], a["where"])] += 1
            if r["with_kit"]:  # a candidate for the product comes from a run that had it
                by_check[(r["task"], a["check"])][a["where"]] += 1
    flow = {}
    for (model, cond), items in sorted(_group([r for r in rows if r["executed"]], ("model", "condition")).items()):
        tools = Counter()
        for r in items:
            tools.update(r["tools"])
        flow[f"{model} {cond}"] = {"runs": len(items), "calls": dict(tools.most_common()),
                                   "tool_errors": sum(r["tool_errors"] for r in items),
                                   "argument_errors": sum(r["argument_errors"] for r in items),
                                   "raw_reads": sum(r["raw_reads"] for r in items),
                                   "median_context_block_bytes": median([r["context_block_bytes"] for r in items])}
    candidates = []
    for (task, check), counts in sorted(by_check.items()):
        if counts["not_available"]:
            candidates.append({"part": "kit reads", "task": task, "check": check, "runs": counts["not_available"],
                               "what": "the value is in no read of the kit (context block, status, id reads): "
                                       "a product gap"})
        if counts["available_not_received"]:
            candidates.append({"part": "discoverability", "task": task, "check": check,
                               "runs": counts["available_not_received"],
                               "what": "a read of the kit holds the value but the run did not get it"})
        if counts["received"]:
            candidates.append({"part": "use", "task": task, "check": check, "runs": counts["received"],
                               "what": "the run received the value and the answer missed it: the model, or "
                                       "a result where it is buried"})
    raw = [r for r in rows if r["condition"] == "A" and r["raw_reads"]]
    if raw:
        candidates.append({"part": "interface", "task": ",".join(sorted({r["task"] for r in raw})), "check": None,
                           "runs": len(raw), "what": "with the extension, the model read the kit's raw files "
                                                     "(journal, run records): what it looked for is a read to offer"})
    scripts = sorted(r["run"] for r in rows + other if r["script"])
    return {"availability": {f"{c} {w}": n for (c, w), n in sorted(where.items())},
            "availability_by_check": {f"{t} {c}": dict(n) for (t, c), n in sorted(by_check.items())},
            "tool_flow": flow, "answers_in_another_script": scripts, "improvement_candidates": candidates}


def fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}" if value < 10 else f"{value:,.0f}"
    return str(value)


def markdown(result: dict) -> str:
    lines = ["# 확장 유무 비교", "", "규칙: docs/design/PRODUCT-EVALUATION.md §8. 만든 도구: tools/model_eval_compare.py.", ""]
    for model, verdict in result["models"].items():
        lines += [f"## {model} ({verdict['tier']}) — 모델 게이트: {verdict['gate']}", "",
                  "| 규칙 | 판정 | 값 |", "|---|---|---|"]
        for key, rule in verdict["rules"].items():
            detail = {k: v for k, v in rule.items() if k not in ("verdict",)}
            lines.append(f"| {key} | {rule['verdict']} | {json.dumps(detail, ensure_ascii=False)[:300]} |")
        lines.append("")
    lines += ["## 과제별", "", "| 모델 | 과제 | 조건 | run | 답 성공 | 사실 | 근거 | 한계 | 회수 | 맥락 오류 | 없는 ID | 토큰 중앙값 | 호출 중앙값 | 분석 실행 | 시간 중앙값(초) | 도구 오류 | 원시 파일 읽기 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in result["tasks"]:
        lines.append("| " + " | ".join(fmt(x) for x in (
            s["model"], s["task"], s["condition"], s["runs"], s["answer_success"], "/".join(map(str, s["fact"])),
            "/".join(map(str, s["evidence"])), "/".join(map(str, s["limits"])), "/".join(map(str, s["recall"])),
            s["context_errors"], s["fabricated_ids"], s["median_tokens"], s["median_tool_calls"], s["analysis_calls"],
            s["median_elapsed_s"],
            s["tool_errors"], s["raw_reads"])) + " |")
    d = result["diagnosis"]
    lines += ["", "## 진단", "", "### 정보 가용성", ""]
    lines += [f"- {k}: {v}" for k, v in d["availability"].items()] or ["- (실패한 값·ID 검사 없음)"]
    lines += ["", "### 개선 후보", "", "| 부품 | 과제 | 검사 | run | 무엇 |", "|---|---|---|---|---|"]
    lines += [f"| {c['part']} | {c['task']} | {c['check'] or '—'} | {c['runs']} | {c['what']} |"
              for c in d["improvement_candidates"]]
    lines += ["", "### 도구 흐름", ""]
    lines += [f"- {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in d["tool_flow"].items()]
    if d["answers_in_another_script"]:
        lines += ["", "### 다른 문자로 쓴 답", ""] + [f"- {r}" for r in d["answers_in_another_script"]]
    if result["replaced"]:
        lines += ["", "### 대신한 run", ""] + [f"- {r}" for r in result["replaced"]]
    if result["stale_confirmations"]:
        lines += ["", "### 적용하지 않은 확정(다시 돈 run)", ""] + [f"- {r}" for r in result["stale_confirmations"]]
    for model, verdict in result["models"].items():
        if verdict["incomplete"]:
            lines += ["", f"### {model}: 두 조건이 같은 반복을 돌지 않은 과제", "", "- " + ", ".join(verdict["incomplete"])]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("outs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, help="the fixtures the runs used (for the kit's reads)")
    parser.add_argument("--confirmations", type=Path, help="a person's verdicts on critical checks that fired")
    args = parser.parse_args(argv)
    config = me.load_config()
    for out in args.outs:
        if not (out / "runs").is_dir():
            parser.error(f"{out} is not an output of tools/model_eval.py (no runs/)")
    confirmations = {}
    if args.confirmations:
        doc = json.loads(args.confirmations.read_text(encoding="utf-8"))
        if doc.get("schema") != CONFIRMATIONS_SCHEMA or not isinstance(doc.get("runs"), dict):
            parser.error(f"{args.confirmations}: schema is not {CONFIRMATIONS_SCHEMA}")
        for run, entry in doc["runs"].items():
            if not (isinstance(entry, dict) and isinstance(entry.get("finished_at"), str)
                    and isinstance(entry.get("checks"), dict)):
                parser.error(f"{args.confirmations}: {run} needs the run's finished_at and its checks")
            if any(v not in ("real", "misread") for v in entry["checks"].values()):
                parser.error(f"{args.confirmations}: {run}: a verdict is real or misread")
        confirmations = doc["runs"]
    rows, other, replaced = collect(config, args.outs, confirmations, args.fixtures)
    result = {"schema": SCHEMA, "outs": [str(o) for o in args.outs],
              "rules": {"floor": FLOOR, "cost_factor": COST_FACTOR, "harm_allowance_runs": HARM_ALLOWANCE},
              "confirmations": str(args.confirmations) if args.confirmations else None,
              "models": judge(config, rows), "tasks": summary_rows(rows),
              "other_conditions": summary_rows([{**r, "condition": f"{r['surface']}/{r['proposal']}"} for r in other]),
              "diagnosis": diagnose(rows, other), "replaced": replaced,
              "stale_confirmations": sorted(r["run"] for r in rows + other if r["stale_confirmation"]),
              "runs": rows}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8", newline="\n")
    (args.out / "comparison.md").write_text(markdown(result), encoding="utf-8", newline="\n")
    print(json.dumps({"models": {m: v["gate"] for m, v in result["models"].items()},
                      "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
