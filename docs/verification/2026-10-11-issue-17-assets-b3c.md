# Issue17 B3-c verification: host asset commands, context and Workspace

Measured source `13c364cfc2551d84dee6af390064bf45d9ba8816` on actual design-adoption main `2e63711da62ffd9e1a6072ed65161c4361ba7d7c`, kit 0.13.0. This is the single source-result record for [PR92](https://github.com/handochan/aelix-analytics/pull/92). The separate final documentation-head CI gate is recorded in PR92/Issue17.

Exact commands, source and payload hashes, actual artifact inspection, skips, review dispositions and preserved failures are in [JSON](2026-10-11-issue-17-assets-b3c.json).

## Source and review scope

The frozen source manifest contains 313 files. Introduction floors remain metric 0.9, asset/requested 0.10, CLI/project mode/automatic publication 0.12, and host session/context/status 0.13. The six existing Analytics model tools retain the delivered 0.12 definitions, descriptions and schemas; no model tool was added.

The adopted design review and ONE independent plus ONE separate Codex implementation pass are inherited. The implementation passes reviewed `487d1eb56de87f8bd2b40bbfa4385fba0bca7378`; ONE correction diff check on `4d63d1463ac9421453d898f4b97890ca74cb9a32` passed 9 cases, with 20 deselected. C17C-1 now skips automatic native effects when the current host session cannot be identified, retaining qualified reads. C17C-2 captures callback Origin/session before an awaited read and uses it through publication. That controlled callback result does not establish general parallel-session operation.

Production runtime bytes equal reviewed 4d. Runtime and measurement-tool bytes equal the encoding-corrected 8b and first rebased eeec source. Test bytes changed after the failed first source CI: synthetic race children explicitly emit UTF-8, and both captures must be strings with successful return codes while preserving one journal notification and one suggestion output. CI's repository step is 45 minutes; combined step budgets are 148 minutes within the unchanged 150-minute job budget, with the individual 600/300-second watchdogs retained. These were bounded fixes with affected checks, without repeated full/correction reviews or a repeated local whole suite.

## Actual source CI

[CI 38114954031 attempt 1](https://github.com/handochan/aelix-analytics/actions/runs/38114954031) completed all 7 jobs successfully at exact source `13c364cfc2551d84dee6af390064bf45d9ba8816`, including Linux, macOS and Windows. Root performed one actual artifact download/audit: 586 files, 7 contexts, 14 JUnit reports, 6 smoke reports, 0 inspection errors. Artifact manifest SHA256: `203533da228686939b7bed823b1777dcb3223d763f3f76353787678f88fde226`. The provenance audit checked 134 Git document inputs and 7 workflow contexts against that exact head, with 0 errors.

| Actual leg | Passed | Skipped | Failures/errors |
|---|---:|---:|---:|
| ci-platform-macos-26/aelix-py3.11.xml | 989 | 0 | 0/0 |
| ci-platform-macos-26/aelix-py3.12.xml | 989 | 0 | 0/0 |
| ci-platform-macos-26/launcher-py3.11.xml | 208 | 1 | 0/0 |
| ci-platform-macos-26/suite.xml | 2247 | 77 | 0/0 |
| ci-platform-ubuntu-24.04/aelix-py3.11.xml | 989 | 0 | 0/0 |
| ci-platform-ubuntu-24.04/aelix-py3.12.xml | 989 | 0 | 0/0 |
| ci-platform-ubuntu-24.04/launcher-py3.11.xml | 208 | 1 | 0/0 |
| ci-platform-ubuntu-24.04/suite.xml | 2249 | 75 | 0/0 |
| ci-platform-windows-2025/aelix-py3.11.xml | 989 | 1 | 0/0 |
| ci-platform-windows-2025/aelix-py3.12.xml | 989 | 1 | 0/0 |
| ci-platform-windows-2025/console.xml | 21 | 0 | 0/0 |
| ci-platform-windows-2025/launcher-py3.11.xml | 97 | 112 | 0/0 |
| ci-platform-windows-2025/long-paths-off.xml | 7 | 0 | 0/0 |
| ci-platform-windows-2025/suite.xml | 2065 | 285 | 0/0 |

Required smoke checks hold on all three OSes. Windows retains its declared `kill_tree_after_the_leader_exited` limitation. All skip reasons and optional/not-run distinctions remain in JSON; separate pinned Aelix 0.1.0b2 host legs cover the host modules skipped by the repository-only suite.

## Q11 text measurements

The exact current-source synthetic fixture uses the registered Qwen/Qwen3.8-27B text tokenizer, revision `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`, tokenizers 0.23.2, with tokenizer SHA256 `0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3` and special tokens disabled. All nine measured UTF-8 payloads have retained hashes. Counts come from the tokenizer; bytes are not converted to tokens.

| Exact measured text | UTF-8 bytes | Tokens |
|---|---:|---:|
| On-demand context | 942 | 296 |
| Always-on context | 1178 | 366 |
| Added notice | 236 | 70 |
| Next-turn context | 942 | 297 |
| Tool prompt | 1593 | 339 |
| Six compact tool definitions | 8828 | 1964 |
| Six template tool definitions | 9119 | 2244 |
| On-demand payload | 11656 | 2881 |
| Always-on payload | 11892 | 2951 |

**Both measured payloads exceed the 2048-token target.** The inherited definitions and default context are unchanged, with 0 added tools, 1 recorded automatic notification and 0 model calls. The target remains an observation: nothing was truncated or hidden to make it pass. Earlier source487 (2893/2964) and sourceeeec (2876/2944) measurements are separate fixture/time/payload observations, without a token-optimisation claim. These measurements cover Analytics text; real-model input/output usage and latency, complete host/provider framing, frontier/image tokens and human visibility are not measured.

## Local and preserved outcomes

- Held immutable 4d pinned CPython 3.11.15 and 3.12.13 / Aelix 0.1.0b2 each passed 989 checks. Its installed whole suite remains **failed**: 2246 passed, 77 skipped, 1 failed, exit 1. The exact static encoding scan found only the measurement tool's Git metadata `check_output(text=True)` missing explicit encoding. The bounded UTF-8 fix passed that one scan and lint; the original failed trial and unchanged 309-file manifest remain preserved.
- Actual-main integration passed lint, version/floor checks and documents (770 local links, 74 Markdown files, 16 JSON files). The bounded Windows harness fix passed its single affected pinned case once on each minor, plus lint/doc checks. Those local scopes are distinct from the complete current-source three-OS CI above.
- First source [CI38111718463](https://github.com/handochan/aelix-analytics/actions/runs/38111718463) remains failed at eeec: 6 successful jobs and 1 failed Windows job. The Windows repository suite exceeded its 35-minute step limit. Its partial JUnit contains 1625 passed and 280 skipped with 0 assertion failures/errors before interruption; it is **incomplete**, not a full pass. Both pinned Windows minors separately had 988 passed, 1 skipped and 1 failed because the synthetic child's default stdout encoding disagreed with the parent's UTF-8 decoder, leaving stdout absent. Raw logs, the reader-thread UnicodeDecodeError, partial XML and the 579-file failed audit remain preserved.
- Earlier trials, 11 nonzero original command outcomes, and superseded two-OS PR runs are retained. Executed-and-cancelled runs are not relabelled not_run or complete passes. The corrected source's same-head duplicate PR run 38114956537 was cancelled after lint and both floor legs succeeded; its two platform legs remain cancelled.

## Remaining scope and delivery

[CONTRACTS C9](../CONTRACTS.md) documents the remaining P3 limitation remains: physically reordering valid unique journal lines can select an older stored Workspace score snapshot. Ordinary append order and the raw timeline remain intact. Legacy `reuse_*` kinds/raw bytes and multilingual authored content are preserved.

Actual browser rendering/navigation remains **not_run** under the explicit URL protocol security policy. No HTTP, alternate-browser or raw-CDP workaround was attempted. Generated HTML, escaping and canonical-link tests are separate evidence and do not establish actual browser interaction. No real-model/human rehearsal, user-project mutation, global registration, host-repository change, release or publication was performed.

Parent and child HOME/USERPROFILE/XDG, agent/settings/trust paths are isolated; inherited UV_PROJECT_ENVIRONMENT, VIRTUAL_ENV, PYTHONPATH and Analytics variables are removed before probes, with explicit owned Python/cache/install paths and authoritative host minors. The user's ~/.aelix was never read, listed, resolved or statted.

The single final documentation commit adds this source record and the PR92 CHANGELOG link dated 2026-10-11 KST, preserving production/test/tool bytes. It receives NEW exact final-head three-OS CI and Root's actual artifact audit. Those results belong only in PR92/Issue17, avoiding a report self-loop.
