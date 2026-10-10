# Issue17 B3-a verification: analysis asset candidate/check/registration

Recorded: 2026-10-10T10:00:42.959649+00:00. This record measures B3-a only. Full source manifest, exact commands, prior failed attempts and CI inspection summaries are in [JSON](2026-10-10-issue-17-assets-b3a.json). Current behavior is [CONTRACTS C7](../CONTRACTS.md#c7-분석-자산의-후보검사명시적-등록-kit-0100-17-b3-a).

## Source and preserved reviews

Measured source `044c72372d92e3d72c7cb505374a5efd2f8bb068` is rebased onto actual merged Issue14 main `19c7a9212b9c18f8f20fe43992416a0096f151dd`. Production files have the same bytes as reviewed/tested correction e9c2d79. Three test/helper files now compare captured original bytes and explicitly exercise LF/CRLF; the updated226-file source manifest SHA256 `6c0292b1653ef68c0cc02bf77f2cedd2a8448acb53d4d4fc127f53e31e5c5823`. Metric floor0.9.0 and asset/kit0.10.0 remain distinct. CHANGELOG uses the actual KST main-delivery date.

Design independent/Codex reviews and one design correction check completed before PR85 delivery. Implementation independent/Codex reviews completed on frozen782e363; the two reproduced P2 findings were fixed by e9c2d79 and ONE bounded correction-diff check passed. Common Replay owns chronology and unique prior candidate/check/artifact/source proof. These runtime/record reviews were not repeated for a production-byte-equivalent rebase and test-only correction. Original invalid rows are preserved/warned rather than applied, and later live-byte drift does not rewrite historical journal proof identity.

## Actual source CI

[CI38040680262 attempt1](https://github.com/handochan/aelix-analytics/actions/runs/38040680262) executed all7 jobs on exact source `044c72372d92e3d72c7cb505374a5efd2f8bb068`: Linux/macOS/Windows platform suites, three CPython3.11 stdlib floors, and lint/documents. Artifact inspection checked7 contexts,14 JUnit reports and6 required smoke reports: **0 inspection errors**, 583 files, artifact manifest SHA256 `7a474a2ae1374ba1b6fe8f1794723fd233a3077fc40f54b931bc509fecbf507e`. Failed/skipped/not-run scope is preserved in JSON; skips are not passes.

| Actual CI leg | Result |
|---|---|
| ci-platform-macos-26/aelix-py3.11.xml | 973 passed / 0 skipped / 0 failures / 0 errors |
| ci-platform-macos-26/aelix-py3.12.xml | 973 passed / 0 skipped / 0 failures / 0 errors |
| ci-platform-macos-26/launcher-py3.11.xml | 196 passed / 1 skipped / 0 failures / 0 errors |
| ci-platform-macos-26/suite.xml | 2073 passed / 76 skipped / 0 failures / 0 errors |
| ci-platform-ubuntu-24.04/aelix-py3.11.xml | 973 passed / 0 skipped / 0 failures / 0 errors |
| ci-platform-ubuntu-24.04/aelix-py3.12.xml | 973 passed / 0 skipped / 0 failures / 0 errors |
| ci-platform-ubuntu-24.04/launcher-py3.11.xml | 196 passed / 1 skipped / 0 failures / 0 errors |
| ci-platform-ubuntu-24.04/suite.xml | 2075 passed / 74 skipped / 0 failures / 0 errors |
| ci-platform-windows-2025/aelix-py3.11.xml | 973 passed / 1 skipped / 0 failures / 0 errors |
| ci-platform-windows-2025/aelix-py3.12.xml | 973 passed / 1 skipped / 0 failures / 0 errors |
| ci-platform-windows-2025/console.xml | 21 passed / 0 skipped / 0 failures / 0 errors |
| ci-platform-windows-2025/launcher-py3.11.xml | 91 passed / 106 skipped / 0 failures / 0 errors |
| ci-platform-windows-2025/long-paths-off.xml | 7 passed / 0 skipped / 0 failures / 0 errors |
| ci-platform-windows-2025/suite.xml | 1897 passed / 278 skipped / 0 failures / 0 errors |

The final evidence-only commit receives NEW exact-head three-OS CI. Its exact commit/run/attempt and artifact inspection are recorded in [PR88](https://github.com/handochan/aelix-analytics/pull/88) and Issue17, avoiding a report commit that must cite its own future run. This source result is distinct from the final delivery gate.

## Local and prior evidence

- Correction influence checks:497 passed. Pinned aelix0.1.0b2 host/usage/agent-CLI suites on Python3.11/3.12: each972 passed, including actual project-only SKILL.md loader discovery.
- Prior install-enabled full run:2070 passed,76 skipped,2 version-document failures. Two corrected document checks then passed; those historical failures remain failed. Rebased source current-version document checks also passed2 in isolated parent HOME/config/trust and explicit uv directories.
- Actual synthetic kit CLI registration preserved exact candidate bytes and previous journal bytes, with common reader state registered. Module and SKILL.md-only candidates use actual original/distinct/boundary run assertions before explicit project-only copy. Receipt-less identical destinations are refused; only owned published receipts recover an incomplete journal append. Registration, loader discovery, execution and usefulness remain separate.
- Actual first source CI38037643566 had exactly3 Windows failures: the same LF-only expectation in asset registration and pinned3.11/3.12 loader tests. All583 artifact files,7 contexts,14 JUnit and6 smoke reports were inspected; no other concrete failures. Its failed result remains preserved. Test-only044c723 captures original bytes and explicitly covers LF/CRLF; local asset2pass plus pinned3.11/3.12 loader each2pass.
- Historical CI37962902000 and exact e7dfdb5 CI37963316892 attempt1 were GitHub failures with5 jobs/0 steps due account billing/spending admission. Tests were not_run, no macOS was dispatched, and these cited runs were never rerun or relabeled. Issue14 old admission failure is likewise retained; actual Issue14 main is now merged after its new gates.

## Remaining scope

B3-b project modes/automatic history/window/publication, B3-c session/context/UI/Q11, live model/human rehearsal and user-project registration remain unexecuted here. Loader discovery without a supplied host port is not_run. Declaration provenance does not certify universal correctness or individual function execution lineage. No global registration, release or external publication, and no user ~/.aelix read/stat/list.
