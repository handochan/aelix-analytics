"""Model-free B3-c Q11 paired host payload measurements on one synthetic skill fixture.

Run in the pinned Aelix/pytest environment with an isolated HOME and trust path. The baseline
extension must be an explicit source path. Token values use usage_report's verified registry;
this does not measure provider framing, real model usage, latency or human visibility.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--baseline-extension", required=True, type=Path)
    parser.add_argument("--tokenizer", help="registered ID=PATH from tools/usage_report.py")
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("--out must be new or empty; prior measurements are preserved")
    args.out.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
    from aelix_agent_core.harness.hooks import BeforeAgentStartHookEvent
    from aelix_ai.providers._openai_compat import OpenAICompletionsCompat
    from aelix_ai.providers.openai_completions import convert_tools
    from aelix_coding_agent.cli.agent_context import _tool_section
    from asset_fixtures import ORIGIN, skill_project

    from aelix_analytics import core, relations, workspace

    usage = load("asset_eval_usage", ROOT / "tools/usage_report.py")
    counter = usage.registered_counter(args.tokenizer) if args.tokenizer else usage.TokenCounter(
        "qwen3.8-27b", dict(usage.COUNTERS["qwen3.8-27b"]))
    baseline = load("asset_eval_baseline", args.baseline_extension.resolve())
    extension = load("asset_eval_current", ROOT / "extension/aelix_analytics.py")
    project, candidate = skill_project(args.out / "synthetic-project")
    core.update_asset(project, candidate["id"], expected="validated", new="validated", text="Repeated need",
                      details={"demand": {"repeated_need": True}}, origin=ORIGIN)
    core.record(project, "asset_effect", "Estimated benefit", details={"asset_id": "ratio-procedure",
                "candidate_event_id": candidate["id"], "effect_basis": "estimated",
                "description": "Avoid repeated interpretation"}, origin=ORIGIN)
    host_project = extension.core.Project(project.root)
    extension.resolve_project = lambda: (host_project, "synthetic fixture")
    extension.schedule_workspace_render = lambda *a, **k: None

    attempted_calls = []

    def forbidden(*a, **k):
        attempted_calls.append("model/send_message/UI notify")
        raise AssertionError("Measurement attempted model/send_message/UI notify")

    ctx = SimpleNamespace(session_manager=SimpleNamespace(get_session=lambda: SimpleNamespace(
        session_file=str(args.out.resolve() / "synthetic-session.jsonl"))),
        provider=SimpleNamespace(complete=forbidden), send_message=forbidden,
        ui=SimpleNamespace(notify=forbidden))
    event = BeforeAgentStartHookEvent(prompt="Synthetic measurement", system_prompt="BASE")
    texts = {}
    texts["on_demand_context"] = asyncio.run(extension.on_before_agent_start(event, ctx)).system_prompt
    unchanged = "BASE\n\n" + extension.core.context_block(host_project)
    assert texts["on_demand_context"] == unchanged
    asyncio.run(extension.analytics_command("assets mode always_on --scope session", ctx))
    same_basis = "BASE\n\n" + extension.core.context_block(host_project)
    texts["always_on_context"] = asyncio.run(extension.on_before_agent_start(event, ctx)).system_prompt
    assert texts["always_on_context"].startswith(same_basis)
    texts["added_notice"] = texts["always_on_context"][len(same_basis):]
    assert "Relay this suggestion; review only when the user requests it." in texts["added_notice"]
    texts["next_turn_context"] = asyncio.run(extension.on_before_agent_start(event, ctx)).system_prompt
    assert "Asset suggestion (" not in texts["next_turn_context"]
    compat = OpenAICompletionsCompat()
    old_wire, wire = [convert_tools(module.TOOLS, compat) for module in (baseline, extension)]
    assert {tool.name for tool in extension.TOOLS} == {
        "analytics_status", "analytics_query", "analytics_import", "analytics_run", "analytics_record", "analytics_init"}
    assert wire == old_wire, "B3-c changed model tool definitions"
    lines, guidelines = _tool_section(list(extension.TOOLS))
    texts["tool_prompt"] = lines + "\n" + "\n".join(guidelines)
    texts["tool_definitions_compact"] = usage.compact_form(wire)
    texts["tool_definitions_template"] = usage.template_form(wire)
    texts["always_on_payload"] = "\n".join(texts[name] for name in (
        "always_on_context", "tool_prompt", "tool_definitions_template"))
    texts["on_demand_payload"] = "\n".join(texts[name] for name in (
        "on_demand_context", "tool_prompt", "tool_definitions_template"))
    rows = []
    for name, text in texts.items():
        raw = text.encode("utf-8")
        (args.out / f"{name}.txt").write_bytes(raw)
        rows.append({"name": name, "utf8_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                     "tokens": counter.count(text)})
    notices = [e.value for e in relations.read_journal(project.root).events
               if e.kind == "asset_notification" and e.value["details"].get("trigger") == "automatic"]
    assert len(notices) == 1
    assert not attempted_calls
    workspace.build(project)
    report = {"schema": "analysis_assets.host_measurement/1", "command": sys.argv,
              "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8").strip(),
              "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
              "packages": {name: package_version(name) for name in ("aelix", "tokenizers")},
              "counter": counter.describe(), "baseline_extension": str(args.baseline_extension.resolve()),
              "baseline_extension_sha256": hashlib.sha256(args.baseline_extension.read_bytes()).hexdigest(),
              "current_extension_sha256": hashlib.sha256((ROOT / "extension/aelix_analytics.py").read_bytes()).hexdigest(),
              "tool_names": [tool.name for tool in extension.TOOLS], "additional_tools": 0,
              "definitions_equal": wire == old_wire, "default_context_equal": texts["on_demand_context"] == unchanged,
              "automatic_events": len(notices), "model_calls": 0, "surfaces": rows,
              "q11_always_on_input_target": 2048,
              "target_comparisons": {row["name"]: {
                  "actual": row["tokens"].get("value"), "target": 2048,
                  "exceeds": row["tokens"]["value"] > 2048 if row["tokens"].get("status") == "measured" else None}
                  for row in rows if row["name"] in ("always_on_payload", "on_demand_payload")},
              "not_measured": ["real model review input/output usage and latency", "provider/chat framing",
                               "human visibility", "frontier model tokens", "image tokens"],
              "note": "Exact UTF-8 strings and verified tokenizer counts; bytes are never converted to tokens."}
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = ["# Asset host payload measurement", "", f"Source `{report['head']}`; dirty: {report['dirty']}.", "",
          "Six actual host tools; additional tools: 0; definitions equal to explicit B3-b baseline.", "",
          "| Surface | UTF-8 bytes | Verified tokenizer tokens |", "|---|---:|---:|"]
    md += [f"| {row['name']} | {row['utf8_bytes']} | {row['tokens'].get('value', 'not_measured')} |" for row in rows]
    md += ["", "The Q11 always-on input comparison target is 2,048 tokens; an exceedance is reported, not a claim of passing it.",
           "No real model, human visibility, provider framing, frontier tokens or image tokens were measured."]
    (args.out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "additional_tools": 0, "automatic_events": len(notices),
                      "counter": counter.describe()}))
    return 1 if counter.state.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
