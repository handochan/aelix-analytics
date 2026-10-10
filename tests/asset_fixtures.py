"""Small synthetic skill-only project, shared by stdlib and pinned host validation.

Uses explicit project paths and no global Aelix directory. Each checking script really reads
the candidate bytes and a different declared input; the skill document is not Python code.
"""

from __future__ import annotations

import json

from conftest import analytics_project

from aelix_analytics import asset_contract as contract
from aelix_analytics import core, runner

ORIGIN = core.Origin("human", "sdk", "inferred")
SKILL = """---
name: denominator-rule
description: Handle a zero denominator consistently.
---

Use when reporting a ratio for matched periods.
Return a missing value for zero denominators; otherwise calculate n / d - 1.
Check the original result, another input and the zero case before relying on the result.
Fail when periods do not match or the required columns are absent.
사용자가 작성한 설명은 그대로 보존합니다.
"""


def skill_project(root, *, newline=None):
    project = core.Project(analytics_project(root, version="0.9.0"))
    (project.root / "draft").mkdir()
    (project.root / "draft/SKILL.md").write_text(SKILL, encoding="utf-8", newline=newline)
    (project.root / "data").mkdir()
    (project.root / "scripts").mkdir()
    for name, values in (("original", [{"n": 10, "d": 5}]), ("other", [{"n": 20, "d": 10}]),
                         ("boundary", [{"n": 1, "d": 0}])):
        (project.root / f"data/{name}.json").write_text(json.dumps(values), encoding="utf-8")

    def execute(name):
        body = ("import json\nfrom pathlib import Path\n"
                "instructions = Path('draft/SKILL.md').read_text(encoding='utf-8')\n"
                "assert 'name: denominator-rule' in instructions and 'description:' in instructions\n"
                f"values = json.loads(Path('data/{name}.json').read_text(encoding='utf-8'))\n"
                "result = [None if r['d'] == 0 else float(r['n'] / r['d'] - 1) for r in values]\n"
                + ("assert result == [None]\n" if name == "boundary" else "assert result == [1.0]\n")
                + "print(result)\n")
        (project.scripts / f"{name}.py").write_text(body, encoding="utf-8")
        value = runner.execute(project, script=f"scripts/{name}.py", title=f"Skill procedure check: {name}",
                               declared_inputs=["draft/SKILL.md", f"data/{name}.json"], origin=ORIGIN)["run"]
        assert value["status"] == "ok", value.get("error")
        return value

    source = execute("original")
    observed = next(f for f in source["inputs"] if f["path"] == "draft/SKILL.md")
    details = {"asset_id": "ratio-procedure", "title": "분모가 0인 비율의 해석 절차", "artifact_kind": "skill",
               "problem": "Report missing ratios consistently", "conditions": "Matched periods and required columns",
               "exclusions": "Unmatched periods or missing columns", "next_check": "Original, independent and zero input",
               "input_contract": "n/d records", "output_contract": "numeric ratio or explicit missing value",
               "files": [{"path": "SKILL.md", "ref": {"type": "data_observation", "run_id": source["id"],
                           "role": "input", "path": observed["path"], "digest": {"sha256": observed["sha256"]}}}]}
    proposed = core.record(project, "asset_candidate", "A skill-only procedure candidate", details=details,
                            refs=[source["id"]], origin=ORIGIN).event
    core.update_asset(project, proposed["id"], expected="proposed", new="trial", text="Check separate inputs", origin=ORIGIN)
    for name, run in (("original", source), ("other", execute("other")), ("boundary", execute("boundary"))):
        checked = {"asset_id": details["asset_id"], "candidate_event_id": proposed["id"],
                   "check_run": {"type": "run", "run_id": run["id"]}, "result": "passed", "boundary": name == "boundary",
                   "check_scope": "Declared procedure, original/independent/zero input", "check_code": {
                       "type": "code_snapshot", "run_id": run["id"], "path": run["script"]["snapshot"],
                       "digest": {"sha256": run["script"]["sha256"]}},
                   "artifact_digest": contract.bundle_digest(proposed["details"])}
        core.record(project, "asset_check", "Skill procedure assertions passed", details=checked, origin=ORIGIN)
    core.update_asset(project, proposed["id"], expected="trial", new="validated", text="Declared procedure scope checked", origin=ORIGIN)
    return project, proposed
