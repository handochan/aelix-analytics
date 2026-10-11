# Issue17 B3-b verification: project modes and deterministic asset publication

Measured source `d548794f51f13ffe97351e97b3fdd67b01b9ffe0` on actual Issue15 main `74e5e96c4a49917ad8980b0724dc4add62bc6077`, kit 0.12.0. This record covers B3-b only; final delivery gates are reported in its PR/Issue.

Full source manifest, exact executed commands/outcomes, preserved failures/skips and artifact hashes are in [JSON](2026-10-11-issue-17-assets-b3b.json).

## Source and reviews

Source manifest: 305 files; metric floor 0.9, asset/requested floor 0.10, new project mode/automatic/known-choice floor 0.12. ONE independent and ONE separate Codex implementation pass on 0c160053 found three P2 defects; all fixed and ONE correction diff check passed. Mode-only incomplete history is visible; ambiguous relevant duplicated history cannot create a new opportunity; policy-owned nested results remain unchanged.

Actual-main integration fixes preserve detail args, validate status before publication, and retain stdlib asset bindings after host namespace cleanup. Their bounded checks and CI are separate from the earlier completed review scope; no full/design review was repeated.

## Actual source CI

[CI 38054537599 attempt 1](https://github.com/handochan/aelix-analytics/actions/runs/38054537599) ran exact `d548794f51f13ffe97351e97b3fdd67b01b9ffe0` on all 3 OSes. Actual audit: 7 contexts, 14 JUnit reports, 6 smoke reports, 586 artifact files, 0 errors; artifact manifest SHA256 `84ecd72488283fd087e0cdfabe04ee9900936f852c38d52cc6b678310d34ae57`.

| Actual leg | Passed | Skipped | Failures/errors |
|---|---:|---:|---:|
| ci-platform-macos-26/aelix-py3.11.xml | 977 | 0 | 0/0 |
| ci-platform-macos-26/aelix-py3.12.xml | 977 | 0 | 0/0 |
| ci-platform-macos-26/launcher-py3.11.xml | 208 | 1 | 0/0 |
| ci-platform-macos-26/suite.xml | 2231 | 76 | 0/0 |
| ci-platform-ubuntu-24.04/aelix-py3.11.xml | 977 | 0 | 0/0 |
| ci-platform-ubuntu-24.04/aelix-py3.12.xml | 977 | 0 | 0/0 |
| ci-platform-ubuntu-24.04/launcher-py3.11.xml | 208 | 1 | 0/0 |
| ci-platform-ubuntu-24.04/suite.xml | 2233 | 74 | 0/0 |
| ci-platform-windows-2025/aelix-py3.11.xml | 977 | 1 | 0/0 |
| ci-platform-windows-2025/aelix-py3.12.xml | 977 | 1 | 0/0 |
| ci-platform-windows-2025/console.xml | 21 | 0 | 0/0 |
| ci-platform-windows-2025/launcher-py3.11.xml | 97 | 112 | 0/0 |
| ci-platform-windows-2025/long-paths-off.xml | 7 | 0 | 0/0 |
| ci-platform-windows-2025/suite.xml | 2049 | 284 | 0/0 |

The final documentation commit updates the actual KST delivery date to 2026-10-11 and links PR91 in CHANGELOG; runtime source bytes are unchanged. It receives NEW exact finalhead three-OS CI and actual artifact inspection. Those results belong in [PR91](https://github.com/handochan/aelix-analytics/pull/91) and Issue17, avoiding a self-report loop.

## Local and preserved outcomes

- Exact d548 pinned Aelix 0.1.0b2 / CPython 3.11 host/usage/agent CLI: 977 passed. Shared affected correction: 807 passed, 1 skipped; actual-main launcher: 245 passed, 1 skipped; status-error boundary: 347 passed, 1 skipped; host binding: 3 passed plus 162 asset checks; exact expected stdlib sets: 2 passed. Commands/source/dirty scope are in JSON.
- f0 installed full: 2,231 passed, 76 skipped, exit 0 remains evidence for f0 only. Its actual loaded-host mode/notification status probe failed twice and motivated the binding fix.
- 13aaf6c installed full: 2,229 passed, 76 skipped, 2 failed, exit 1 is preserved: only obsolete exact stdlib sibling-name expectations failed. The test-only d548 correction keeps all no-analysis/package/namespace assertions. No complete local d548 full result is inferred from these separate scopes.
- Old contaminated parent UV_PROJECT_ENVIRONMENT trial: 27 failed, 51 errors, 1,168 passed, 62 skipped and dirty mixed trial: 6 failed, 1,223 passed, 62 skipped both interrupted exit 2; frozen 0c packaging: 2,194 passed, 76 skipped, 1 environment failure and bounded explicit Python 3.12 packaging: 1 passed remain distinct outcomes.
- Started 538 local full ended partial with verified owned process stop/outer exit 247. Superseded 538/f0/13 CI runs were executed and cancelled, not relabelled not_run or complete passes. Missing/unexecuted scopes remain not_run in JSON.

## Remaining scope

B3-c and actual model/human verification are separate. No global registration, release, user project writes or user ~/.aelix access. Parent and child HOME/USERPROFILE/XDG/trust/agent state are synthetic; UV_PROJECT_ENVIRONMENT and inherited VIRTUAL_ENV are removed before probes, with explicit owned Python/cache/install paths.
