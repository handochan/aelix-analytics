"""Compare a model's runs with aelix-analytics (A) and with aelix alone (V) by the rules of
docs/design/PRODUCT-EVALUATION.md (§6 metrics, §8 rules), and diagnose where the product could improve (§7).

    python tools/model_eval_compare.py OUT [OUT ...] --out DIR [--fixtures DIR] [--confirmations FILE]

Each OUT is an output directory of tools/model_eval.py; give all of a campaign's (one per model, a re-run).
A run is in the comparison when its surface is `default` (A) or `vanilla` (V) and its proposal condition is
`unrequested`; the other conditions (Q5, Q11) are listed apart. When the same run (model, task, condition,
repeat) is in more than one OUT, the one that executed is kept (a re-run replaces an `error` or `not_run`),
and the replaced ones are listed.

- The answer's measures are the score's `answer_result` and its fact, evidence and limit checks (§6). A
  critical check that fired counts as a context error until a person confirms it: --confirmations FILE
  (`{"schema": "aelix-analytics.model-eval-confirmations/1", "confirmed_by": "...", "runs": {RUN: {CHECK:
  "real" | "misread"}}}`). A run that failed only by critical checks a person found misread is an answer
  success.
- Rules R1-R7 are judged per model; the tier is the model configuration's role (`frontier` is the upper
  model). A rule whose data is missing (Part II's recall before its chains run) is `not_run`; one that rests
  on an unconfirmed critical check is `provisional`.
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
# Calls that read the kit's own files directly (journal, run records, the state directory).
RAW_FILE = re.compile(r"journal\.jsonl|run\.json|\.analytics/|(?:^|[\s/\"'])runs/")
HANGUL = re.compile(r"[가-힣]")
HAN = re.compile(r"[一-鿿]")


def tier(model_config: dict) -> str:
    return "upper" if model_config.get("role") == "frontier" else "lower"


def answer_text(record: dict) -> str:
    return "\n\n".join(t.get("answer") or "" for t in record.get("turns") or [])


def script_flag(record: dict) -> str | None:
    """`han` when an answer to a Korean request holds Chinese characters (a Qwen answer of the pilot did)."""
    requests = " ".join(record.get("requests") or [])
    text = answer_text(record)
    if HANGUL.search(requests) and len(HAN.findall(text)) >= 10:
        return "han"
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
    if re.fullmatch(r"[A-Za-z]?\d[\w-]*", token) and not token.replace(",", "").isdigit():
        return me.id_present(text, token)
    return token in text


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
        tokens = check_tokens(answer.get(check["id"], {}))
        if not tokens:
            continue
        missing = [t for t in tokens if not held(received, t)] if check["kind"] == "evidence" else (
            [] if any(held(received, t) for t in tokens) else tokens)
        if not missing:
            where = "received"
        elif offered is None:
            where = "unknown"
        elif all(held(offered, t) for t in missing) if check["kind"] == "evidence" else any(held(offered, t) for t in tokens):
            where = "available_not_received"
        else:
            where = "not_available"
        out.append({"check": check["id"], "kind": check["kind"], "where": where, "missing": missing})
    return out


def run_row(config: me.Config, out_dir: Path, run_dir: Path, record: dict, confirmations: dict,
            fixtures: Path | None, cache: dict) -> dict:
    sc = record.get("score") or {}
    conditions = record.get("conditions") or {}
    model_id = (record.get("model") or {}).get("config")
    name = record.get("run") or run_dir.name
    confirmed = confirmations.get(name, {})
    fired = [c["id"] for c in sc.get("checks") or [] if c.get("kind") == "critical" and not c.get("passed")]
    real = [c for c in fired if confirmed.get(c) != "misread"]
    unconfirmed = [c for c in fired if c not in confirmed]
    failed_answer = [c["id"] for c in sc.get("checks") or [] if c.get("required") and not c.get("passed")
                     and c.get("kind") in me.ANSWER_KINDS]
    executed = record.get("outcome") in ("success", "failure", "error", "timeout")
    answer_success = executed and record.get("outcome") != "error" and not failed_answer and not real
    tokens = record.get("tokens") or {}
    values = [((tokens.get(k) or {}).get("value")) for k in ("input_uncached", "input_cached", "output")]
    calls = record.get("tool_calls") or []

    def counts(key):
        part = sc.get(key) or {}
        return [part.get("passed") or 0, part.get("total") or 0]

    return {
        "run": name, "out": str(out_dir), "model": model_id, "task": record.get("task"),
        "condition": COMPARED.get(conditions.get("surface")), "surface": conditions.get("surface"),
        "proposal": conditions.get("proposal"), "repeat": record.get("repeat"),
        "outcome": record.get("outcome"), "reason": record.get("reason"), "executed": executed,
        "answer_success": bool(answer_success), "fact": counts("fact_recall"), "evidence": counts("evidence"),
        "limits": counts("limits"), "critical_fired": fired, "critical_real": real,
        "critical_unconfirmed": unconfirmed, "fabricated_ids": sc.get("fabricated_ids") or [],
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
            key = (compared, row["model"], row["task"], row["surface"], row["proposal"], row["repeat"])
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
    return compared, other, replaced


def rate(rows: list[dict], key: str) -> tuple[int, int]:
    return sum(r[key][0] for r in rows), sum(r[key][1] for r in rows)


def ratio(pair: tuple[int, int]) -> float | None:
    return pair[0] / pair[1] if pair[1] else None


def total_tokens(row: dict) -> int | None:
    return sum(row["tokens"]) if row["tokens"] else None


def median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def judge(config: me.Config, rows: list[dict]) -> dict:
    """R1-R7 for each model (docs/design/PRODUCT-EVALUATION.md §8)."""
    out = {}
    for model in sorted({r["model"] for r in rows}):
        mine = [r for r in rows if r["model"] == model and r["executed"]]
        tasks = sorted({r["task"] for r in mine if r["condition"] == "A"} & {r["task"] for r in mine if r["condition"] == "V"})
        areas = {t: set(config.tasks[t].get("areas") or []) for t in tasks}
        by = {(t, c): [r for r in mine if r["task"] == t and r["condition"] == c] for t in tasks for c in "AV"}
        rules, unconfirmed = {}, any(r["critical_unconfirmed"] for r in mine)

        def pooled(selected, key, cond, by=by):
            return rate([r for t in selected for r in by[(t, cond)]], key)

        def below(selected, key, by=by):
            """The tasks where A's pooled rate is under V's (a tie, both full, is no difference: O6)."""
            return [t for t in selected if (ratio(rate(by[(t, "A")], key)) or 0) < (ratio(rate(by[(t, "V")], key)) or 0)]

        resume = [t for t in tasks if areas[t] & {"E1", "E3"}]
        if resume:
            a, v = ratio(pooled(resume, "fact", "A")), ratio(pooled(resume, "fact", "V"))
            worse = below(resume, "fact")
            rules["R1"] = {"verdict": "pass" if a is not None and v is not None and a >= v and not worse else "fail",
                           "A": a, "V": v, "tasks": resume, "A_below_V": worse}
        else:
            rules["R1"] = {"verdict": "not_run", "reason": "no task of E1 or E3 has runs in both conditions"}
        evidence_tasks = [t for t in tasks if "E2" in areas[t]]
        fabricated = {c: sum(len(r["fabricated_ids"]) for t in tasks for r in by[(t, c)]) for c in "AV"}
        if evidence_tasks:
            ea, ev = ratio(pooled(evidence_tasks, "evidence", "A")), ratio(pooled(evidence_tasks, "evidence", "V"))
            la, lv = ratio(pooled(evidence_tasks, "limits", "A")), ratio(pooled(evidence_tasks, "limits", "V"))
            worse = {"evidence": below(evidence_tasks, "evidence"), "limits": below(evidence_tasks, "limits")}
            ok = (ea is not None and ev is not None and ea >= ev and (la is None or lv is None or la >= lv)
                  and not worse["evidence"] and not worse["limits"])
            rules["R2"] = {"verdict": "pass" if ok and fabricated["A"] <= fabricated["V"] else "fail",
                           "evidence": {"A": ea, "V": ev}, "limits": {"A": la, "V": lv},
                           "fabricated_ids": fabricated, "tasks": evidence_tasks, "A_below_V": worse}
        else:
            rules["R2"] = {"verdict": "not_run", "reason": "no task of E2 has runs in both conditions"}
        errors = {c: sum(len(r["critical_real"]) for t in tasks for r in by[(t, c)]) for c in "AV"}
        upper = tier(config.models.get(model, {})) == "upper"
        ok = errors["A"] <= errors["V"] and (errors["A"] == 0 if upper else True)
        rules["R3"] = {"verdict": ("provisional" if unconfirmed else "pass") if ok else
                       ("provisional" if unconfirmed else "fail"),
                       "context_errors": errors, "bar": "A = 0 and A <= V" if upper else "A <= V",
                       "unconfirmed_runs": sorted(r["run"] for r in mine if r["critical_unconfirmed"])}
        rules["R4"] = {"verdict": "not_run", "reason": "Part II's chains (recall, redundant work) have not run"}
        harm_tasks = [t for t in tasks if "E6" in areas[t]]
        if harm_tasks:
            short = [t for t in harm_tasks if sum(r["answer_success"] for r in by[(t, "A")]) <
                     sum(r["answer_success"] for r in by[(t, "V")]) - HARM_ALLOWANCE]
            rules["R5"] = {"verdict": "fail" if short else "pass", "tasks": harm_tasks, "A_short": short,
                           "answer_success": {t: {c: sum(r["answer_success"] for r in by[(t, c)]) for c in "AV"}
                                              for t in harm_tasks}}
        else:
            rules["R5"] = {"verdict": "not_run", "reason": "no task of E6 has runs in both conditions"}
        costly = []
        for t in tasks:
            a = median([total_tokens(r) for r in by[(t, "A")] if r["answer_success"]])
            v = median([total_tokens(r) for r in by[(t, "V")] if r["answer_success"]])
            if a is not None and v is not None and a > COST_FACTOR * v:
                costly.append({"task": t, "A": a, "V": v})
        rules["R6"] = {"verdict": "report", "over_1_2x": costly}
        resume_a = rules["R1"].get("A")
        r7 = {"resume_A": resume_a, "recall_A": None, "floor": FLOOR}
        if not upper:
            r7["verdict"] = "report"
        elif resume_a is None or resume_a < FLOOR:
            r7["verdict"] = "fail" if resume_a is not None else "not_run"
        else:
            r7["verdict"] = "not_run"  # the recall floor waits for Part II
        rules["R7"] = r7
        required = ["R1", "R2", "R3", "R4", "R5"] + (["R7"] if upper else [])
        verdicts = [rules[k]["verdict"] for k in required]
        gate = "fail" if "fail" in verdicts else ("pending" if any(v in ("not_run", "provisional") for v in verdicts) else "pass")
        out[model] = {"tier": "upper" if upper else "lower", "tasks": tasks, "rules": rules, "gate": gate}
    return out


def summary_rows(rows: list[dict]) -> list[dict]:
    out = []
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
                    "raw_reads": sum(r["raw_reads"] for r in executed)})
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
    lines += ["## 과제별", "", "| 모델 | 과제 | 조건 | run | 답 성공 | 사실 | 근거 | 한계 | 맥락 오류 | 없는 ID | 토큰 중앙값 | 호출 중앙값 | 시간 중앙값(초) | 도구 오류 | 원시 파일 읽기 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in result["tasks"]:
        lines.append("| " + " | ".join(fmt(x) for x in (
            s["model"], s["task"], s["condition"], s["runs"], s["answer_success"], "/".join(map(str, s["fact"])),
            "/".join(map(str, s["evidence"])), "/".join(map(str, s["limits"])), s["context_errors"],
            s["fabricated_ids"], s["median_tokens"], s["median_tool_calls"], s["median_elapsed_s"],
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
        if any(v not in ("real", "misread") for checks in doc["runs"].values() for v in checks.values()):
            parser.error(f"{args.confirmations}: a verdict is real or misread")
        confirmations = doc["runs"]
    rows, other, replaced = collect(config, args.outs, confirmations, args.fixtures)
    result = {"schema": SCHEMA, "outs": [str(o) for o in args.outs],
              "rules": {"floor": FLOOR, "cost_factor": COST_FACTOR, "harm_allowance_runs": HARM_ALLOWANCE},
              "confirmations": str(args.confirmations) if args.confirmations else None,
              "models": judge(config, rows), "tasks": summary_rows(rows),
              "other_conditions": summary_rows([{**r, "condition": f"{r['surface']}/{r['proposal']}"} for r in other]),
              "diagnosis": diagnose(rows, other), "replaced": replaced, "runs": rows}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8", newline="\n")
    (args.out / "comparison.md").write_text(markdown(result), encoding="utf-8", newline="\n")
    print(json.dumps({"models": {m: v["gate"] for m, v in result["models"].items()},
                      "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
