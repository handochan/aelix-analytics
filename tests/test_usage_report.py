"""The usage report measures exactly what the host receives, and tells a fact a model can read back
from one it cannot.

It runs in aelix's isolated environment with the extension tests, as CONTRIBUTING.md and the CI's
two aelix integration steps do:

    uv run --isolated --no-project --python 3.12 --with aelix==0.1.0b2 --with pytest==9.1.1
        --with pytest-timeout==2.4.0 python -m pytest -q -p no:cacheprovider --timeout 300
        --timeout-method=thread tests/test_extension.py tests/test_usage_report.py

(one command, split here for width). The repository suite skips it: the development environment has
no aelix. A required case that a defect of the measured code loses is a strict xfail named after the
defect in Issue #11's design (D1-D12); it has to be lost the way the design says, so a case lost for
another reason fails instead. PR-2 of #11 fixes the defects and removes the marks.
"""

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("aelix_coding_agent")

PACK = Path(__file__).resolve().parents[1]
TOOL = PACK / "tools" / "usage_report.py"


def tool_module():
    spec = importlib.util.spec_from_file_location("usage_report_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up while it executes
    spec.loader.exec_module(module)
    return module


usage = tool_module()

# Required facts the measured code loses today: the defect, and how the design says it is lost.
LOST_FACTS = {
    **{(f"F1b-{lang}", "preamble-end"): ("D4", "cut_without_explicit_pointer")
       for lang in ("en", "ko")},
    **{(f"F2t-{lang}", fact): ("D2", "wrong_pointer") for lang in ("en", "ko")
       for fact in ("latest-run", "dataset", "recent-note")},
    **{(f"F2s-{variant}", "section-137"): ("D7", "wrong_pointer")
       for variant in ("cr", "u2028", "ff", "lead1", "lead3")},
    **{(f"F3-{lang}", fact): ("D1", "wrong_pointer") for lang in ("en", "ko")
       for fact in ("input-300", "input-502")},
    **{(f"F3-{lang}", "chart-14"): ("D9", "wrong_pointer") for lang in ("en", "ko")},
    ("F3-en", "stdout-first"): ("D8", "silent_omission"),
    ("F3-ko", "stdout-first"): ("D8", "cut_without_explicit_pointer"),
    **{(f"F3-{lang}", "stderr-first"): ("D8", "silent_omission") for lang in ("en", "ko")},
    **{(f"J-{lang}", "finding-003"): ("D5", "silent_omission") for lang in ("en", "ko")},
    **{(f"J-{lang}", "long-finding-2500"): ("D5", "cut_without_explicit_pointer")
       for lang in ("en", "ko")},
    ("I-en", "column-250"): ("D3", "generic_pointer"),
}

# Judged invariants that do not hold today: the defects, and the checks allowed to fail with them.
FAILING_INVARIANTS = {
    **{(f"F1b-{lang}", "S1"): ("D4", {"preamble"}) for lang in ("en", "ko")},
    **{(f"F1b-{lang}", "S4"): ("D4", {"context_claim"}) for lang in ("en", "ko")},
    **{(f"F2t-{lang}", "S4"): ("D2", {"headings", "lists", "cut"}) for lang in ("en", "ko")},
    **{(f"F2s-{variant}", "S1"): ("D7", {"sections"})
       for variant in ("cr", "u2028", "ff", "lead1", "lead3")},
    **{(f"F3-{lang}", "S5"): ("D1, D8, D9", {"inputs", "charts", "warnings", "output_columns",
                                             "stdout", "stderr"}) for lang in ("en", "ko")},
    **{(f"J-{lang}", "S1"): ("D5", {"key_listed", "key_text", "open_listed"})
       for lang in ("en", "ko")},
    **{(f"J-{lang}", "S4"): ("D2", {"lists"}) for lang in ("en", "ko")},
    **{(f"N-{lang}", "S8"): ("D11", {"note_cut_tool", "note_cut_command"})
       for lang in ("en", "ko")},
    **{(f"Q-{lang}", "S6"): ("D3, D10", {"rows", "location", "types", "cells"})
       for lang in ("en", "ko")},
    ("I-en", "S7"): ("D3", {"columns"}),
    ("E-en", "S10"): ("D12", {"init_not_empty_details", "kit_failed_stderr"}),
}

# What a token value may say when no tokenizer is given.
WITHOUT_A_TOKENIZER = {"no_tokenizer_given", "model_not_pinned", "vision_tokens"}


def isolate(monkeypatch, directory: Path) -> None:
    monkeypatch.setenv("AELIX_LAB_TRUST_FILE", str(directory / "trust" / "lab-projects.json"))
    monkeypatch.setenv("AELIX_LAB_UV", str(directory / "no-uv" / "uv"))


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    """One run of the command, the fixtures kept: what most tests read."""
    directory = tmp_path_factory.mktemp("usage")
    output = directory / "report"
    with pytest.MonkeyPatch.context() as mp:
        isolate(mp, directory)
        code = usage.main(["--output", str(output), "--keep-fixtures"])
    report = json.loads((output / "usage-report.json").read_text(encoding="utf-8"))
    return SimpleNamespace(code=code, output=output, report=report)


def saved_text(measured, fixture: str, surface: str) -> str:
    entry = next(e for e in measured.report["surfaces"]
                 if e["fixture"] == fixture and e["surface"] == surface and e["variant"] is None)
    return (measured.output / entry["text_file"]).read_bytes().decode("utf-8")


def token_values(value):
    """Every token value object in a report."""
    if isinstance(value, dict):
        if value.get("status") in ("measured", "not_measured", "failed") and \
                set(value) <= {"status", "value", "reason", "error"}:
            yield value
        else:
            for item in value.values():
                yield from token_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from token_values(item)


def test_report_is_generated_from_one_command(measured):
    report = measured.report
    assert measured.code == 0 and report["failures"] == [], report["failures"]
    assert report["schema"] == "aelix-analytics.usage-report/1"
    for key in ("generated_by", "measured", "environment", "conventions", "counters", "targets",
                "fixtures", "surfaces", "always_on", "omissions", "retrieval", "task_cost_proxy",
                "targets_comparison", "checks", "not_run"):
        assert report.get(key) is not None, key
    assert report["generated_by"]["command"][1:3] == ["tools/usage_report.py", "--output"]
    markdown = (measured.output / "usage-report.md").read_text(encoding="utf-8")
    assert markdown == usage.render_markdown(report), "the Markdown is not the JSON's"
    assert "generated by tools/usage_report.py — do not edit" in markdown
    built = {f["id"] for f in report["fixtures"]}
    skipped = {n["what"] for n in report["not_run"]}
    assert all(spec.id in built or spec.id in skipped for spec in usage.SPECS)
    assert any("#24" in n.get("where", "") for n in report["not_run"]), "real model cost"
    assert any("#46" in caveat for caveat in report["caveats"]), "the F3 caveat"
    assert {(r["family"], r["surface"], r["lang"]) for r in report["retrieval"]["recall"]} >= {
        ("F1", "S1", "ascii"), ("F3", "S5", "ko"), ("J", "S1", "en")}


def test_a_surface_that_cannot_be_measured_fails_the_command(monkeypatch, tmp_path):
    """An aelix API that changed fails its surfaces, not the report: status 1, the rest measured."""
    isolate(monkeypatch, tmp_path)

    def changed_api(ctx):
        raise ImportError("cannot import name 'convert_tools'")

    monkeypatch.setattr(usage, "measure_static", changed_api)
    code, report = usage.run(tmp_path / "out", only=["F1-en"])
    assert code == 1
    assert [f["fixture"] for f in report["failures"]] == ["T"]
    assert "convert_tools" in report["failures"][0]["error"]
    assert {e["fixture"] for e in report["surfaces"]} == {"F1-en"}
    assert (tmp_path / "out" / "usage-report.md").is_file()
    assert report["tool_surface"] is None and report["always_on"] == []


def test_sizes_are_the_exact_strings_the_host_receives(measured, monkeypatch, tmp_path):
    from aelix_agent_core.harness.hooks import BeforeAgentStartHookEvent
    from aelix_ai.tools import ToolExecutionContext

    for entry in measured.report["surfaces"]:
        text = (measured.output / entry["text_file"]).read_bytes().decode("utf-8")
        assert usage.size_of(text) == entry["size"], entry["text_file"]
        assert entry["size"]["lines"] == (text.count("\n") + 1 if text else 0)
    assert measured.report["checks"]["P1_sizes_recomputed_from_saved_texts"]
    # S1 is what the hook appends to the prompt it is given, and S4 what lab_status returns: asked
    # again here of an extension loaded on its own, on the projects the run kept.
    isolate(monkeypatch, tmp_path)
    extension = usage.load_extension()
    status = {t.name: t for t in extension.TOOLS}["lab_status"]
    for fixture in ("F1-ko", "F2-en", "F3-en", "J-ko"):
        monkeypatch.setenv("AELIX_LAB_PROJECT",
                           str(measured.output / "fixtures" / fixture / "project"))
        out = asyncio.run(extension.on_before_agent_start(
            BeforeAgentStartHookEvent(prompt="hi", system_prompt="BASE"), SimpleNamespace()))
        assert out.system_prompt[len("BASE"):] == saved_text(measured, fixture, "S1"), fixture
        reply = asyncio.run(status.execute({}, ToolExecutionContext(
            tool_call_id="again", model=SimpleNamespace(input=["text"]))))
        assert reply.content[0].text == saved_text(measured, fixture, "S4"), fixture
    s2 = next(e for e in measured.report["surfaces"]
              if e["fixture"] == "T" and e["surface"] == "S2")
    assert s2["s2_equals_prompt_difference"], "S2 is not what the tools add to aelix's prompt"


def test_tokens_are_counted_only_by_an_identified_tokenizer(measured, monkeypatch, tmp_path):
    values = list(token_values(measured.report))
    assert values
    odd = [v for v in values
           if v["status"] != "not_measured" or v["reason"] not in WITHOUT_A_TOKENIZER]
    assert not odd, odd[:3]
    markdown = (measured.output / "usage-report.md").read_text(encoding="utf-8")
    assert "not measured (no_tokenizer_given)" in markdown
    isolate(monkeypatch, tmp_path)
    # A counter the report is given counts every text, under its own id, and nothing else changes.
    words = usage.TokenCounter("test-words", {"library": "test: whitespace split"},
                               encode=lambda text: len(text.split()))
    code, counted = usage.run(tmp_path / "words", only=["F1-en"], counters=[words])
    assert code == 0, counted["failures"]
    for entry in counted["surfaces"]:
        text = (tmp_path / "words" / entry["text_file"]).read_bytes().decode("utf-8")
        assert entry["tokens"]["test-words"] == {"status": "measured", "value": len(text.split())}
        assert entry["tokens"]["qwen3.8-27b"] == {"status": "not_measured",
                                                  "reason": "no_tokenizer_given"}
    # A tokenizer file that is not the registered one counts nothing, and the report is written.
    other = tmp_path / "tokenizer.json"
    other.write_text("{}", encoding="utf-8")
    output = tmp_path / "mismatch"
    assert usage.main(["--output", str(output), "--only", "T",
                       "--tokenizer", f"qwen3.8-27b={other}"]) == 0
    mismatch = json.loads((output / "usage-report.json").read_text(encoding="utf-8"))
    (counter,) = [c for c in mismatch["counters"] if c["id"] == "qwen3.8-27b"]
    assert counter["state"] == {"status": "not_measured", "reason": "tokenizer_file_mismatch"}
    assert {e["tokens"]["qwen3.8-27b"]["reason"] for e in mismatch["surfaces"]} == {
        "tokenizer_file_mismatch"}
    with pytest.raises(SystemExit) as refused:
        usage.main(["--output", str(tmp_path / "unknown"), "--tokenizer", f"other-model={other}"])
    assert refused.value.code == 2


def test_targets_never_refuse_or_cut(measured):
    report = measured.report
    assert measured.code == 0
    blocks = [e for e in report["surfaces"] if e["surface"] == "S1"]
    assert blocks and all(e["hook_returned_block"] for e in blocks)
    replies = [e for e in report["surfaces"] if e["surface"] in ("S4", "S5", "S6", "S7")]
    assert replies and not [e["fixture"] for e in replies if e["is_error"]]
    pinned = {e["fixture"]: e for e in blocks if e["fixture"] in ("F1-ascii", "F1-en", "F1-ko")}
    assert len(pinned) == 3 and all(e["pinned_section_in_block"] for e in pinned.values())
    # Far over the target, and still whole: a target is compared, never enforced.
    assert all(e["size"]["utf8_bytes"] > 10 * 1536 for e in pinned.values())
    listed = {o["fixture"]: o["block_sections_listed"] for o in report["omissions"]}
    assert all(listed[f] == 200 for f in ("F2-ascii", "F2-en", "F2-ko", "F2t-en", "F2t-ko"))
    assert [t for t in report["targets_comparison"] if (t.get("utf8_bytes") or {}).get("exceeds")]


def test_measurement_is_deterministic(monkeypatch, tmp_path):
    """The same fixtures give the same bytes, and under a root of another length the same text with
    the root replaced, where no length-based cut applied (design section 18, P4); a cut that falls
    elsewhere under a longer root is reported, not judged."""
    isolate(monkeypatch, tmp_path)
    required = sorted({f for f, _ in usage.required_facts()} | {f for f, _ in
                                                                usage.judged_invariants()})
    runs = {}
    for name, base in (("first", tmp_path / "a"), ("again", tmp_path / "a"),
                       ("longer", tmp_path / ("b" * 60))):
        code, report = usage.run(tmp_path / f"out-{name}", only=required, base=base)
        assert code == 0, report["failures"]
        runs[name] = {(e["fixture"], e["surface"], e["variant"]): e for e in report["surfaces"]
                      if (e["fixture"], e["surface"]) != ("T", "host-base-prompt")}
    first, again, longer = runs["first"], runs["again"], runs["longer"]
    assert first.keys() == again.keys() == longer.keys()
    judged = 0
    for key, entry in first.items():
        assert entry["size"] == again[key]["size"], key
        assert entry["root_normalized"]["masked_timestamps"] == 0, key
        if entry["length_cuts"] or longer[key]["length_cuts"]:
            continue
        assert entry["root_normalized"]["size"] == longer[key]["root_normalized"]["size"], key
        judged += 1
    assert judged >= 0.8 * len(first), (judged, len(first))  # the cut ones are the exception


def test_the_checker_tells_retrievable_from_lost(monkeypatch, tmp_path):
    """Hand-made cases, one per outcome: a checker that passes everything fails here."""
    isolate(monkeypatch, tmp_path)
    host = usage.Host()
    context = tmp_path / "CONTEXT.md"
    context.write_bytes(b"# Title\n\n## Goal\n- measure\n\n## Rules\n- rule one\n"
                        b"- FACT-RULE: the rule that matters\n- rule three\n\n## After\n- after\n")
    rule = usage.Fact("rule", "S1", "context", ("FACT-RULE",), (context,), True, "Rules")

    def block(offset: int) -> str:
        return ("### CONTEXT.md sections not repeated here\nThey are part of this project's context"
                f" — read the one you need from {context}:\n"
                f"- Rules (5 lines): read offset {offset} limit 5")

    exact = usage.check_fact(host, rule, block(6), usage.Truth(context))
    assert (exact["class"], exact["passed"], exact["host_calls"]) == ("R1", True, 1), exact
    off = usage.check_fact(host, rule, block(7), usage.Truth(context))
    assert off["class"] == "wrong_pointer" and off["found_at"] == 1, off  # found; the range lies
    shown = usage.check_fact(host, rule, "FACT-RULE in the text", usage.Truth(context))
    assert shown["class"] == "R0", shown

    record = {"id": "20260901-090000-x", "outputs": [], "charts": [], "warnings": [],
              "inputs": [{"path": f"data/in_{i}.csv",
                          "sha256": hashlib.sha256(bytes([i])).hexdigest()} for i in range(6)]}
    (tmp_path / "run.json").write_text(json.dumps(record), encoding="utf-8")
    hidden = record["inputs"][4]
    item = usage.Fact("input", "S5", "inputs", (hidden["path"], hidden["sha256"][:12]),
                      (tmp_path / "run.json",), True)

    def page(name: str, inputs: list) -> Path:
        path = tmp_path / f"{name}.html"
        path.write_text("<table>" + "".join(f"<tr><td>{i['path']}</td><td>{i['sha256'][:12]}</td>"
                                            "</tr>" for i in inputs) + "</table>", encoding="utf-8")
        return path

    def reply(target: Path, more: bool = True) -> str:
        shown = "".join(f"  {i['path']} sha256 {i['sha256'][:12]} via scan\n"
                        for i in record["inputs"][:2])
        note = "  … and 4 more input file(s) — the run page lists them all\n" if more else ""
        return (f"lab_run: ok — run {record['id']} (1.0 s)\ninputs (6):\n{shown}{note}"
                f"journal: e1 · report: {target}")

    truth = usage.Truth(None, record)
    complete = usage.check_fact(host, item, reply(page("all", record["inputs"])), truth)
    assert complete["class"] == "R1", complete
    partial = usage.check_fact(host, item, reply(page("some", record["inputs"][:3])), truth)
    assert partial["class"] == "wrong_pointer", partial  # "lists them all" is false
    assert [c for c in partial["claims"] if not c["holds"]]
    silent = usage.check_fact(host, item, reply(page("some", []), more=False), truth)
    assert silent["class"] == "silent_omission", silent
    run_id = usage.Fact("run", "S4", "runs", (record["id"],), (tmp_path / "run.json",), True)
    cut = usage.check_fact(host, run_id, "lab_status: ok\nproject: x\n… (truncated)", truth)
    assert cut["class"] == "cut_without_explicit_pointer", cut
    (tmp_path / "meta.json").write_text('{"columns": [{"name": "col_250"}]}', encoding="utf-8")
    column = usage.Fact("column", "S7", "columns", ("col_250",), (tmp_path / "meta.json",), True)
    derived = usage.check_fact(host, column, "lab_import: ok\ncolumns:\n  col_001: Int64\n"
                                             "… (truncated; see the report page)", usage.Truth())
    assert derived["class"] == "generic_pointer", derived
    gone = usage.Fact("gone", "S8", "note_text", ("FACT-GONE",), (tmp_path / "run.json",), False)
    assert usage.check_fact(host, gone, "lab_note: ok", truth)["class"] == "not_recorded"


def fact_cases():
    for fixture, fact in usage.required_facts():
        lost = LOST_FACTS.get((fixture, fact))
        marks = [pytest.mark.xfail(strict=True, raises=AssertionError,
                                   reason=f"{lost[0]} — #11 PR-2")] if lost else []
        yield pytest.param(fixture, fact, id=f"{fixture}-{fact}", marks=marks)


@pytest.mark.parametrize(("fixture", "fact"), list(fact_cases()))
def test_required_fact_is_retrievable(measured, fixture, fact):
    found = [f for f in measured.report["retrieval"]["facts"]
             if f["fixture"] == fixture and f["fact"] == fact]
    if len(found) != 1 or not found[0]["required"]:
        raise LookupError(f"{fixture} {fact} was not measured as a required fact")
    (result,) = found
    lost = LOST_FACTS.get((fixture, fact))
    if lost and not result["passed"] and result["class"] != lost[1]:
        raise RuntimeError(f"lost as {result['class']}, where {lost[0]} loses it as {lost[1]}")
    assert result["passed"], {k: result.get(k) for k in ("class", "claims", "route")}


def invariant_cases():
    for fixture, surface in usage.judged_invariants():
        failing = FAILING_INVARIANTS.get((fixture, surface))
        marks = [pytest.mark.xfail(strict=True, raises=AssertionError,
                                   reason=f"{failing[0]} — #11 PR-2")] if failing else []
        yield pytest.param(fixture, surface, id=f"{fixture}-{surface}", marks=marks)


@pytest.mark.parametrize(("fixture", "surface"), list(invariant_cases()))
def test_omission_invariants_hold(measured, fixture, surface):
    found = [i for i in measured.report["retrieval"]["invariants"]
             if i["fixture"] == fixture and i["surface"] == surface and i["judged"]]
    if len(found) != 1:
        raise LookupError(f"{fixture} {surface} was not judged")
    (result,) = found
    failing = {c["id"] for c in result["checks"] if not c["holds"]}
    expected = FAILING_INVARIANTS.get((fixture, surface))
    if expected and not failing <= expected[1]:
        raise RuntimeError(f"{sorted(failing - expected[1])} fail too, beyond {expected[0]}")
    assert result["holds"], [c for c in result["checks"] if not c["holds"]]
