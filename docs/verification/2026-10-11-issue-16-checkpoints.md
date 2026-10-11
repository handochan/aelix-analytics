# Issue #16 checkpoint source 측정 — 2026-10-11

Kit0.14.0 · [PR #93](https://github.com/handochan/aelix-analytics/pull/93) · source `bb69fb158f39d328aeeac887b953fda075705222` · actual C3 base `14a404c0278e3aa9825a7d87b686072c6680fd83`.

채택된 [ADR-0012](../decisions/0012-checkpoint-and-handoff.md)와 [T1–T12](../design/CHECKPOINT-HANDOFF.md#6-구현-검증-계획)의 checkpoint 기록·기준·참조·작성 출처·독립 review/update 신호와 모델 없는 인계를 구현한 source 결과다.
[JSON](2026-10-11-issue-16-checkpoints.json)에 실제 명령/소스/시각/종료/원시 hash/skip/not_run을 함께 남겼다. 이 report는 source 측정만 참조하며 report commit의 자기 SHA를 넣지 않는다.

## 실제 source CI와 감사

[Source CI38124655881 attempt1](https://github.com/handochan/aelix-analytics/actions/runs/38124655881)은 workflow_dispatch/macos=true로 exactbb69의7개 job이 모두 성공했다. 조정자가 artifact를 한 번 받아586파일/7context/14JUnit/6smoke의 오류0, 문서136개 입력/checker의 Git bytes와7개 manualworkflow SHA의 source 일치를 감사했다. watcher는 실제exit0이고 중복 download/rerun은 없다.

| Actual JUnit leg | Passed | Skipped | Failures/errors |
|---|---:|---:|---:|
| ci-platform-macos-26/aelix-py3.11.xml | 989 | 0 | 0/0 |
| ci-platform-macos-26/aelix-py3.12.xml | 989 | 0 | 0/0 |
| ci-platform-macos-26/launcher-py3.11.xml | 208 | 1 | 0/0 |
| ci-platform-macos-26/suite.xml | 2288 | 78 | 0/0 |
| ci-platform-ubuntu-24.04/aelix-py3.11.xml | 989 | 0 | 0/0 |
| ci-platform-ubuntu-24.04/aelix-py3.12.xml | 989 | 0 | 0/0 |
| ci-platform-ubuntu-24.04/launcher-py3.11.xml | 208 | 1 | 0/0 |
| ci-platform-ubuntu-24.04/suite.xml | 2290 | 76 | 0/0 |
| ci-platform-windows-2025/aelix-py3.11.xml | 989 | 1 | 0/0 |
| ci-platform-windows-2025/aelix-py3.12.xml | 989 | 1 | 0/0 |
| ci-platform-windows-2025/console.xml | 21 | 0 | 0/0 |
| ci-platform-windows-2025/launcher-py3.11.xml | 97 | 112 | 0/0 |
| ci-platform-windows-2025/long-paths-off.xml | 7 | 0 | 0/0 |
| ci-platform-windows-2025/suite.xml | 2106 | 286 | 0/0 |

여섯 smoke의 required hold는 true다. Windows의 post-leader kill-tree 및 regex/subprocess 내부 CLI 취소 declared limitation과 floor smoke의 선택 범위 not_run은 그대로 보존한다. Skip/declared limitation을 통과한 보장으로 바꾸지 않는다. Windows repository JUnit의 실제 측정 시간은1,751.899초이며 운영 watcher가 기록한 step 경과는약30분46초다.

| Raw audit proof | SHA-256 |
|---|---|
| Artifact manifest | `578515452c8409c435bbda54899a610ebcc4949c361f1c74a2ee75b00eb6e34a` |
| Inspection | `11f047bf9b0d95fc12c1a049c23cbd11f83c65bcceab383229131a2ee2962e66` |
| Provenance inspection | `ac92dc192d9c8124e694f17447c0f723f8c4be8bb44a1b734cda2c9f2435c9e0` |

## 원본 바이트·상태 신호 측정

Actual sourcebb69의 합성 authored text는47 UTF-8 bytes, deterministic text는241 bytes였다. 선택1개 제한에서 finding3개 생략/count/reason/read_more를 보존했고, journal-before1,915 bytes는 journal-after9,127 bytes의 동일한 prefix였다. 기존6개 legacy journal fixture는 actual14a와bb69의 bytes/hash가 모두 같다. 선택 todo close→reopen의 두 변경 ID/이유는 needs_review/updates_available=true/true, append 없이 context 유실은 true/false였다.

| Raw artifact | Bytes | SHA-256 |
|---|---:|---|
| authored.json | 2919 | `d8e434f118b4ae07ac8c7cab6ac2de94ea6455d1de222b116725b7579f64c3a6` |
| deterministic.json | 3346 | `0fb0fbe8f0cc1be454d8c20c982798146f59e255a8eeb9ba406e6bfd96757b37` |
| journal-after.jsonl | 9127 | `37db31fa22687b4953244c9f23f9e18c702a7b91c0055d2fffc9a41312dea515` |
| journal-before.jsonl | 1915 | `1e828e5a39daaf17688cb8b29a2e4c42621c64357ed99df77c45a8f4eae41fed` |
| missing.json | 2140 | `1efe5bc29615f1ee1fceaa114f3df94cad1fb0c86db150670e505b7053f0cbae` |
| preserved-context.md | 1136 | `3f39a1a2e1d27b75909cc7a314c73e09c1be7d077bf8d89c334d6f7afa110916` |
| review.json | 2587 | `5c661289b8b367dcf4f3950dd3bd68837e8248959cdd5c34dca3dc7ce50269ca` |

Raw helper JSON의 reviews/sourceCI not_run은 그 바이트 측정 command 범위다. 다음의 실제 독립/Codex 리뷰와 위 CI를 실행하지 않았다는 현재 상태가 아니며 reviewer의 실행으로 귀속하지 않는다. 토큰은 고정 tokenizer로 측정하지 않았고 bytes를 tokens로 바꾸지 않았다.

## 리뷰·수정과 검증 범위

설계 리뷰는 PR90에서 완료해 반복하지 않았다. 같은 frozen730에서 독립 구현1회·별도 Root Codex1회를 완료했고, 같은 지적을 합쳐 세 P2로 처리했다.

| Group | Repair |
|---|---|
| F1/C16-I1 tagged pending | call-time typed/generated refs/digest를 복사; refs없음/filename refs 회귀 |
| F2/C16-I2 전문 reader | 공통 pure 선택/ref 검증, invalid qualified null/latest raw, prefix/physical positive 신호와 generic 의미 유지 |
| C16-I3 authored from_seq | 제공한 field 보존·bool/future 첫 효과 전 거절, 없을 때만0 |

독립 reviewer가6856의 수정 diff만1회 확인해 세 건 모두 해결·확인 범위에 남은 차단 지적 없음이다. checkpoint40·writer/relations/retrieval/metrics/layer/marker/LF503·새 host3122·lint가 통과했다. 기존 ordinaryCI730의 두 OS9개 실패는 기대값/encoding/중복 Goal reader로 분류해 보존했고, 일반200자/전체checkpoint 목적을 같은 reader로 읽는 boundedfix의 영향 범위63개가 통과했다(이 source의 checkpoint41개 포함). 추가 리뷰를 하지 않았다.

현재 편의 작성의 explicit CONTEXT filename-ref preview 거절은 [현재 계약 C10](../CONTRACTS.md#c10-분석-checkpoint와-결정적-인계-kit-0140-16)의 비차단 입력 경계로 남겼다. 초기20/3 fixture,376/48 layer cascade, 최초 measurement setup 실패, install25pass/README1fail도 각 원래 소스/결과에 보존하며 이후 통과로 소급하지 않았다.

## 별도 남은 게이트

실제 모델/사람 Q17, 실제 browser interaction, release/publication은 not_run이다. Browser URL 제한의 workaround를 사용하지 않았다. 합성 Origin 교대·CI 성공·resolved ref·needs_review=false는 결론 검토나 통계 검사 완료가 아니다.

이 source report만 더하는 docs-only commit 뒤 NEW exact final-head all3OS와 실제 audit를 진행한다. 그 최종 결과는 PR/Issue에만 남기며 추가 report/self-SHA loop를 만들지 않는다.
