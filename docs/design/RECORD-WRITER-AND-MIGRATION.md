# 기록 writer와 프로젝트 전환 설계 — 새 형식의 공통 writer와 원본 보존 복사 전환

- 상태: **Proposed — 현재 동작이 아니다.** 새 모듈 `writer.py`·`migration.py`, 이 문서의 API, 명령 `analytics migrate`, 필드
  (`means`·`actor_basis`·표식의 `migration`), 가족(`analytics.run/2`·`analytics.dataset/1`·`analytics.trust/1`·`analytics.migration/1`),
  오류 코드는 코드에 없다. 지금 동작은 [현재 계약](../CONTRACTS.md)이 설명한다.
- 작업: [#13](https://github.com/handochan/aelix-analytics/issues/13) `[B1-c/N0-b]` 새 기록 writer와 원본 보존 프로젝트 전환.
  완료 조건 다섯과 이 문서의 대응은 1절에 있다.
- 기준: main `09d22c4`(PR #76, #12 PR-3 병합 뒤), `KIT_VERSION = "0.5.0"`. 코드 위치는 함수 이름으로 적는다. 구현 PR은 그때의
  코드와 이 문서를 다시 대조한다.
- 따르는 결정: [ADR-0005](../decisions/0005-names-first-storage-with-the-new-writer.md) 결정 2–7,
  [ADR-0006](../decisions/0006-common-record-contract.md) 1·3·6·8·9·10절, [ADR-0001](../decisions/0001-evolvable-platform-boundaries.md),
  [ADR-0003](../decisions/0003-portable-boundaries-and-in-process-kit-loading.md) 결정 1·3·6, [ADR-0007](../decisions/0007-line-endings.md),
  [ADR-0008](../decisions/0008-reuse-proposal-counting-and-deterministic-always-on.md) 결정 3(요청 주체·실행 수단),
  [용어집](../TERMINOLOGY-AND-LANGUAGE.md) §2·§3·§6·§8, [전체 용어 점검](../REVIEW-TERMINOLOGY-2026-09-18.md) §5,
  [연속성 설계](../DESIGN-CONTINUITY-AND-REUSE.md), [아키텍처](../ARCHITECTURE.md) §4·§7·§11, [베타 기준](../BETA-QUALITY-GATES.md) Q4·Q7·Q8·Q17,
  [운영 정책](../PROJECT-OPERATIONS.md)의 리뷰 예산과 검증 증거, 결정 Issue [#3](https://github.com/handochan/aelix-analytics/issues/3)·
  [#4](https://github.com/handochan/aelix-analytics/issues/4), 이 일을 기다리는 [#14](https://github.com/handochan/aelix-analytics/issues/14)·
  [#15](https://github.com/handochan/aelix-analytics/issues/15)·[#17](https://github.com/handochan/aelix-analytics/issues/17)·
  [#19](https://github.com/handochan/aelix-analytics/issues/19)·[#20](https://github.com/handochan/aelix-analytics/issues/20)([공유 build 설계](PUBLICATION-BUILD.md)).
- 만든 방법: 같은 입력으로 따로 쓴 두 설계안(단계별 PR을 앞세운 안, 원본 보존을 앞세운 안)을 비교했다. 원본 보존 안을 바탕으로
  하고 다른 안의 장점을 옮겼으며, 두 안이 놓친 것을 코드로 확인해 더했다(17절). 두 안·상태 지도·프로브는 Git 밖의 로컬 작업 폴더
  `.devstate/runs/abd7197a-3fd9-432e-b69a-76e02e5b85ba/`에 있다.
- 리뷰: #13은 기록 형식·전환 PR이므로 구현 전에 이 문서가 설계 리뷰(독립 1회·Codex 1회)를 받는다. 아직 받지 않았다.
- 소유자 결정: 입력이 정하지 않은 제품 결정 11개가 16절(OQ1–OQ11)에 권장 답과 바뀌는 것과 함께 있다. 답을 받기 전에는 권장 답을
  전제로 쓴 부분(본문에 "(OQn)" 표시)이 확정이 아니다. ADR이 #13 설계에 맡긴 선택은 15절(D1–D18)이며 설계 리뷰가 정한다.

---

## 0. 요약

1. PR은 다섯이다. D(이 문서) → ⓪ 내부 코드 이름(동작 그대로, OQ6) → ① 표식 판정·새 가족 읽기·두 표식 충돌 → ② 닫힌 전환 엔진
   → ③ 새 writer·정책 전환·저장 이름·전환 명령. ①②③은 ADR-0005 결정 7의 세 PR 그대로다.
2. 게이트를 먼저 깐다. ①이 모든 쓰기 진입점의 첫 부수 효과 전에 `writer.admit()`을 부르게 하고, 쓸 수 있는 표식은 상수 `WRITABLE`
   한 줄로 정한다(①·②는 `lab.project/1`, ③은 `analytics.project/2`). 그래서 결정 7의 불변식이 ①부터 성립하고, ③은 이 줄과 writer를 바꾼다.
3. 새 모듈은 둘이다. `writer.py`(새 계층 `writer`, 수준 1)와 `migration.py`(새 계층 `migration`, 수준 3). 둘 다 stdlib만 쓰고, `core`
   이상의 수준을 하나씩 올린다. writer가 core를 import하지 못하게 기존 계층 검사가 막는다.
4. 공통 writer는 모든 진입점에서 `Origin(actor, means, actor_basis, session, request_id)`를 명시적으로 받는다. 실행 수단 필드의 이름은
   `means`이고 값은 `cli`·`sdk`·`tool`·`command`·`hook`이다.
5. 같은 상태 변경은 journal 잠금 안에서 표식을 다시 읽고 `relations.Replay`로 재생해 판정한다. 맞지 않으면 `state_conflict`로 거절하고
   아무것도 쓰지 않는다. 끊긴 줄 뒤에는 줄바꿈을 먼저 쓴다. 잠금 안의 단계는 `Writer.locked()` 하나이며 #19의 Report 저장도 쓴다.
6. 전환 명령은 `analytics migrate --to DEST [--dry-run]`이다. 원본은 다른 명령과 같은 규칙으로 찾은 `lab.json` 프로젝트다. 런처는
   `init`처럼 `uv run --no-project`로 kit을 부른다. 프로브에서 `uv run --project`는 원본에 `.venv/`·`uv.lock`을 만들거나 다시 썼다.
7. 전환은 원본에 잠금 파일 말고는 쓰지 않는다. 목적지 ledger를 원본 잠금보다 먼저 쓰고, 원본의 `run.lock` → `lock`을 기다리지 않고
   잡아 계획·복사·원본 재확인 동안만 쥔다. `.lab/`이 없는 원본에는 디렉터리를 만들지 않고 잠금 없이 전후 비교에 맡긴다.
8. 사본은 같은 상대 경로·같은 바이트다. 기록하는 대응은 둘이다: `.lab/` 안의 이력·로그 → `.analytics/` 같은 이름(ADR-0005 결정 5가 #13에
   맡긴 배치), `lab.json` → `.analytics/migration/<id>/source-marker.json`(바이트 그대로). `.gitignore`는 줄을 덧붙이고 전후 해시와 diff를 남긴다.
9. 커밋 지점은 검증·보고서·내구 쓰기 뒤의 `analytics.json` 원자 교체 하나다. 표식이 보고서의 sha256을 담는다. 표식 전의 목적지는
   옛 kit에는 `no_project`, 새 kit에는 `migration_incomplete`다.
10. 중단된 전환은 같은 명령으로 잇는다. 목적지에 ledger가 적은 파일만 있고 해시가 맞으며, 이미 복사한 파일이 지금 원본과 같을 때만이다.
    목적지의 파일을 덮어쓰지 않고, 이 전환이 만든 임시 파일 말고는 지우지 않는다.
11. ③ 뒤 `lab.json` 프로젝트는 읽기만 된다. 일반 쓰기는 `migration_required`와 전환 명령 안내로 거절하고 부수 쓰기는 건너뛴다.
    두 표식은 모든 작업에서 `marker_conflict`다.
12. 측정 기록(md+JSON)은 ①②③에 하나씩 둔다. 실행하지 않은 것(pytest·CI·Windows·Linux·M 규모 등)은 18절에 있고 통과로 보지 않는다.

---

## 1. 완료 조건과 보장

| #13 완료 조건 | 이 문서 | PR |
|---|---|---|
| 1 원본 유지·별도 목적지·참조 매핑·전환 보고서 | 6절(목적지 규칙, 잠금, 전후 해시, 매핑, 보고서), 5.4절(매핑 읽기) | ②, 명령은 ③ |
| 2 혼합 journal·미래 형식·두 marker·부분 전환 검증 | 4.3절 판정, 5절 읽기, 6.10–6.11절 중단, 11절 T6·T8·T9·T12 | ①②③ |
| 3 작성 주체·실행 수단·선택적 세션을 공통 writer에서 | 4.1–4.2절 `Origin`, 2.5절 필드 | 배관 ①, 기록 ③ |
| 4 같은 상태 변경의 충돌과 중단을 숨기지 않음 | 4.6절, 8절 | ③(읽기 표시는 #12에 있음) |
| 5 새 모듈을 계층표에 등록 | 3절 | `writer` ①, `migration` ② |

이 설계가 지키는 보장과 그것을 확인하는 시험(11절)이다. 시험이 없는 보장은 적지 않았다.

| 번호 | 보장 | 근거 | 시험 |
|---|---|---|---|
| S1 | 전환은 원본에 잠금 파일(없을 때 생김)을 만들거나 잡는 것 말고는 쓰지 않는다. 실패·중단·거절·`--dry-run`도 같다. 잠금 파일을 새로 만들었으면 ledger와 보고서가 그 사실을 적는다 | ADR-0005 결정 4·5 | T1, T6, T18 |
| S2 | 목적지의 커밋 지점은 `analytics.json`의 원자 교체 하나다. 그 전에는 어떤 kit도 목적지에 기록하지 않고, 표식과 신뢰 항목은 검증 뒤에만 생긴다 | ADR-0005 결정 5 | T3, T6 |
| S3 | 사본마다 바이트 수·sha256을 보고서에 적고, 표식이 보고서의 sha256을 담는다. 누구든 다시 계산해 대조할 수 있다 | ADR-0005 결정 4 "전후 해시" | T4 |
| S4 | 사본은 원본과 같은 바이트다. 위치가 다른 것은 매핑이 적은 두 대응뿐이고, 내용이 다른 것은 보고서가 전후 해시·diff로 적은 `.gitignore` 덧붙임뿐이다 | ADR-0005 결정 5, 용어 점검 §5 5항 | T4 |
| S5 | 복사 구간에 원본이 바뀌면(목록·크기·해시) 표식 없이 실패하고 바뀐 경로를 알린다. 사본에서 조용히 빠지는 기록이 없다 | ADR-0005 결정 5 | T5 |
| S6 | 중단된 목적지는 "이어 쓸 수 있음" 또는 "사유와 함께 거절" 가운데 하나다. ledger에 없거나 해시가 다른 파일을 덮어쓰지 않고, 이 전환의 임시 파일 말고는 지우지 않는다 | ADR-0005 결정 5 | T6 |
| S7 | 한 목적지에는 한 전환만 돈다. 원본 잠금은 기다리지 않고 시도하며 옛 kit과 같은 잠금 파일·바이트 범위를 쓴다 | ADR-0005 결정 5, ADR-0003 결정 1 | T7, T15 |
| S8 | 모든 쓰기 작업은 첫 부수 효과 전에 표식을 새로 읽어 판정하고, journal 쓰기는 잠금 안에서 한 번 더 읽는다. 거절한 작업은 프로젝트 트리를 바꾸지 않는다 | ADR-0005 결정 6, ADR-0006 1.3 | T8, T10, T12 |
| S9 | ①부터 어느 PR이 병합된 main에서도 `analytics.json` 디렉터리에 옛 형식(`lab.*/1`, `.lab/`, `data/raw/`, `runs/<id>/charts/`)이 써지지 않는다 | ADR-0005 결정 7 | T8 |
| S10 | 같은 상태 변경의 충돌은 `state_conflict`로 거절하거나 `conflict`로 보이고, 중단은 `damaged`·`incomplete`·`migration_incomplete`로 남는다 | ADR-0006 6절, 완료 조건 4 | T13, T6 |
| S11 | 모든 새 기록에 작성 주체와 실행 수단이 있고, 진입점이 밝히지 않은 작성 주체는 그 사실이 남는다(OQ1). 세션 없는 기록도 모든 읽기에 나온다 | ARCHITECTURE §4, Q17 | T14 |
| S12 | 새 기록은 writer 한 곳에서 UTF-8(BOM 없음)·LF로 쓰고, 사용자 내용의 사본은 이진 모드로 바이트 그대로 복사한다 | ADR-0006 10절, ADR-0007 결정 2·6 | T16 |

---

## 2. #13 뒤의 저장 형식과 이름

### 2.1 ADR이 정한 것

- 표식 `analytics.json`(`analytics.project/2`: 필수 `schema, project_id, title, created_at, min_writer_version`, 선택 `kit_version`),
  관리 디렉터리 `.analytics/`, `report/` → `workspace/`, `data/raw/` → `data/imported/`, `runs/<id>/charts/` → `runs/<id>/visualizations/`,
  `lab.*/1` → `analytics.*`, `lab-projects.json`(`lab.trust/1`) → `analytics-projects.json`. 필드 이름·enum 값은 바꾼다면 #13에서
  (ADR-0005 결정 2, 용어집 §3).
- `analytics.event/2`의 필드(필수 `schema, seq, id, recorded_at, kind, actor, text, refs`, 선택 `session, request_id, details`), kind 23개
  (`gap` → `workflow_feedback`), 참조 형태와 쓰는 시점의 결박, 하나의 seq 공간, 무작위 `project_id`(ADR-0006 1–3절).
- `min_writer_version`은 PEP 440 정규형이고 낮은 kit은 `writer_too_old`로 쓰기를 거절한다. 새 쓰기 규칙을 처음 쓰는 kit이 표식 값을
  올린다(ADR-0006 1절 3항).
- 정본 먼저·완료 이벤트가 커밋 지점, 끊긴 줄 뒤 줄바꿈 먼저, `record_update`의 잠금 안 재생과 `state_conflict`(ADR-0006 6절).
- 경로 정체성(ADR-0006 9절), 새 기록의 UTF-8·LF와 사용자 내용 사본의 바이트 보존(ADR-0006 10절, ADR-0007 결정 2·6).
- 전환의 원본 보존·명시적 명령·목적지 규칙·복사·잠금·순서·중단 뒤 잇기·전환 뒤 원본·작업 스크립트 diff(ADR-0005 결정 4·5),
  `lab.json`·두 표식·미래 형식의 처리와 모든 쓰기의 첫 부수 효과 전 확인(결정 6), PR 경계와 불변식(결정 7).
- `via` 값을 #13에서 바꾼다(Issue #13의 #9-2 인계 댓글). 목표 철자는 정하지 않았다(OQ5).

### 2.2 ADR이 #13 설계에 맡긴 것과 이 설계의 선택

| 맡긴 것(근거) | 선택 | 이유 |
|---|---|---|
| `analytics.run`·`analytics.dataset`의 버전·필드(ADR-0006 1.1) | `analytics.run/2`, `analytics.dataset/1` | 숫자는 의미가 바뀔 때 오른다(용어집 §3). run은 Visualization 필드·해시가 바뀌므로 `lab.run/1` 다음 번호, dataset은 이름공간과 위치만 바뀌므로 `/1` |
| 신뢰 목록·전환 기록의 가족(용어집 §3) | `analytics.trust/1`, `analytics.migration/1` | 이름만 바뀐 가족과 새 가족 |
| 실행 수단 필드(ADR-0006 1절) | 선택 필드 `means`: `cli`·`sdk`·`tool`·`command`·`hook` | `via`(run 입출력이 어떻게 생겼는지), `channel`(게시의 최신 주소, ADR-0009), `surface`(알림 표면, ADR-0008), `entry`(Report manifest)는 이미 다른 뜻이다 |
| 다른 형식 Visualization의 해시(ADR-0006 3절) | kit이 저장하는 모든 형식에 `sha256`, 첨부 데이터에 `data_sha256` | 한 Visualization은 파일 하나다(`capture.RunState._save`). kit이 방금 쓴 바이트라 비용이 작고 #20의 대조가 기대한다(공유 build 설계 OQ14) |
| 전환 명령·오류 코드(ADR-0005 결정 5) | `analytics migrate --to DEST [--dry-run]`, 오류는 4.11절 | 6.1절 |
| `.lab/` 안의 context 이력·로그를 새 관리 디렉터리에 담는 방법(ADR-0005 결정 5) | `.lab/X` → `.analytics/X`(같은 이름, 바이트 그대로), 대응을 매핑에 적음. 목적지에 `.lab/`을 만들지 않음 | 결정 5의 "새 관리 디렉터리에 담는 방법"과 용어집 §3의 `.analytics/`(context 버전·작업 상태·보조 로그). `refs.context`는 파일 이름이라 그대로 풀리고 reader는 `.analytics/context`를 먼저 본다(`relations.CONTEXT_DIRS`) |
| 원래 경로에 두는 단계(ADR-0005 결정 5) | #13에 두지 않음 | 실행 중 run·세션의 확인과 거절이 따로 필요하고 완료 조건에 없다. 필요하면 후속 Issue |
| 본문 상한(ADR-0006 10절) | 조용히 자르지 않음. 상한은 OQ2 | 2.9절 |

### 2.3 ③ 뒤의 배치

| 대상 | ③ 뒤 새 쓰기 | 전환한 목적지에 사본으로 남는 것 | 읽기 |
|---|---|---|---|
| 표식 | `analytics.json` | 없음. `lab.json`의 바이트는 `.analytics/migration/<id>/source-marker.json` | 두 이름 모두(4.3절) |
| 관리 디렉터리 | `.analytics/{context/, cache/, adhoc/, telemetry.jsonl, queries.jsonl, lock, run.lock, workspace.lock, migration.lock, migration/}` | `.lab/{context/, adhoc/, demo/, telemetry.jsonl, queries.jsonl}`의 바이트가 `.analytics/` 같은 이름에(`cache/`·잠금은 제외, OQ4) | context는 `.analytics/context` 다음 `.lab/context` |
| Workspace | `workspace/` | 없음(재생성 대상, OQ4) | 읽지 않음(파생물) |
| 가져온 데이터 | `data/imported/<name>.parquet`·`.meta.json`(`analytics.dataset/1`) | `data/raw/*`(`lab.dataset/1`) | 둘 다. 같은 이름이면 `data/imported`가 view 이름을 먼저 받고 status가 경고 |
| Visualization | `runs/<id>/visualizations/` | `runs/<id>/charts/` | run 기록이 적은 경로 그대로 |
| journal | `journal.jsonl`에 `analytics.event/2` 줄 | 기존 줄 바이트 그대로(CRLF 포함) | 한 seq 공간으로 함께 |
| run 기록 | `runs/<id>/run.json` = `analytics.run/2` | `lab.run/1` 그대로 | 둘 다 |
| 신뢰 목록(프로젝트 밖) | `analytics-projects.json`(`analytics.trust/1`) | — | 새 목록과 옛 `lab-projects.json`(읽기 전용, 6.14절) |

`core.IGNORED_DIRS`·`MANAGED_DIRS`와 ADR-0006 9절의 관리 디렉터리 철자 예외는 ③에서 `.analytics`·`workspace`를 더하고 `.lab`·`report`를 남긴다
(옛 프로젝트를 읽기 위해서다). 결정 2의 개명에 따른 같은 규칙의 적용이며 현재 계약 B·E에 적는다.

### 2.4 표식 `analytics.project/2`

```json
{"schema": "analytics.project/2", "project_id": "prj_7f3a9c2e41d84b06", "title": "Regional revenue",
 "created_at": "2026-09-01T09:00:00.000+09:00", "min_writer_version": "<the KIT_VERSION of PR 3>",
 "kit_version": "<the kit that wrote this file>",
 "migration": {"id": "20261008T010203Z-a1b2c3d4",
               "report": ".analytics/migration/20261008T010203Z-a1b2c3d4/report.json",
               "report_sha256": "<sha256 of report.json>"}}
```

- `project_id`는 무작위 값이다(schema `opaque_id`). init·전환 모두 새로 만든다. 옛 `lab.json`에는 이 값이 없다.
- init: `created_at`은 지금, `title`은 인자. 전환: `title`·`created_at`을 원본 `lab.json`에서 옮기고, 없으면 원본 디렉터리 이름과 전환 시각을
  쓰며 보고서에 "원본에 없음"을 적는다. 값을 지어내지 않는다.
- `migration`은 전환으로 만든 프로젝트에만 있는 선택 필드다. 보고서를 바이트로 묶어 출처를 확인할 수 있게 한다. reader가 매핑을 찾는
  입구이기도 하다(5.4절). 표식 schema는 추가 필드를 허용하며, ②가 이 필드의 정의를 schema에 더한다.
- 설정(ADR-0008 결정, #17)과 게시 대상(ADR-0009)은 뒤의 PR이 선택 필드나 관리 디렉터리 파일로 더한다.

### 2.5 `analytics.event/2`의 줄

ADR-0006의 필드에 이 설계가 더하는 선택 필드는 둘이다.

| 필드 | 값 | 비고 |
|---|---|---|
| `means` | `cli`(런처·`bin/analytics`·`python -m aelix_analytics`), `sdk`(스크립트 API), `tool`(aelix 모델 도구), `command`(aelix `/analytics`), `hook`(aelix가 자동으로 부른 쓰기) | 실행 수단. 인증이 아니라 출처다 |
| `actor_basis` | `declared`(진입점·사용자가 작성 주체를 밝힘), `inferred`(밝히지 않아 kit 기본값으로 채움) | OQ1. 값은 용어집 §6의 basis 어휘를 다시 쓴다 |

키 순서: `schema, seq, id, recorded_at, kind, actor, actor_basis, means, session?, request_id?, text, refs, details?`.
`session`은 없으면 쓰지 않는다(Q17: 세션 없는 사람 기록도 같은 기록이다). `request_id`는 호출자가 준 것만 줄에 남긴다(4.9절).
두 필드는 `analytics.run/2`·`analytics.migration/1`의 보고서에도 같은 이름으로 들어간다. #17의 알림 이벤트와 #19의 Report manifest가 같은 이름을 쓴다.

### 2.6 `analytics.run/2`

`lab.run/1`(현재 계약 C2·C3)에서 바뀌는 것만 적는다. 나머지 필드는 이름과 뜻이 같다. OQ5의 답이 범위를 넓히면 이 표가 늘어난다.

| `lab.run/1` | `analytics.run/2` |
|---|---|
| `schema: lab.run/1` | `schema: analytics.run/2` |
| `charts[]`, 항목의 `id` | `visualizations[]`, 항목의 `visualization_id`(논리 키는 그대로 `<script path>::<visualization_id>`) (OQ5) |
| 그림 파일 `runs/<id>/charts/<id>.<ext>` | `runs/<id>/visualizations/<id>.<ext>` |
| 그림 해시 없음 | `sha256`(저장한 그림 파일의 바이트), `data_sha256`(첨부 데이터 parquet) |
| `via`의 `lab.chart`·`lab.read`·`lab.scan`·`lab.sql`·`lab.save` | `analytics.visualize`·`analytics.read`·`analytics.scan`·`analytics.sql`·`analytics.save`(SDK 함수와 같은 철자). 나머지 `via` 값은 그대로 (OQ5) |
| `actor`, `session` | 같은 뜻에 `actor_basis`·`means` 추가(그 run을 요청한 진입점) |

`visualization_version` 참조의 `digest`는 그 Visualization의 `sha256`을 옮긴다(ADR-0006 3절). reader는 `/2` Visualization을 해시가 있는
대상으로 판정한다.

### 2.7 `analytics.dataset/1`, `analytics.trust/1`, `analytics.migration/1`

- `analytics.dataset/1`: `lab.dataset/1`(현재 계약 C4)과 필드가 같고 `schema`와 위치(`data/imported/`)만 다르다. 완료 이벤트는 `/2`의
  `import` kind와 `dataset_import` 참조(`about`, `path`, `digest`)다(ADR-0006 6절).
- `analytics.trust/1`: `{"schema": "analytics.trust/1", "projects": [...]}`. 위치 규칙은 지금과 같다(`AELIX_ANALYTICS_TRUST_FILE`, 없으면 호스트
  agent 디렉터리 아래 `analytics-projects.json`). 대상이 있는 링크면 대상을 교체한다(ADR-0006 9절 4번).
- `analytics.migration/1`: 전환 기록의 `plan.json`·`mapping.json`·`report.json`이 이 가족이고 `"record": "plan" | "mapping" | "report"`로
  구별한다. ledger `progress.jsonl`의 줄은 가족 태그 없이 `plan.json`이 가리킨다(6.8절). schema 정의는 ②가 더한다.

### 2.8 kind와 입력 이름

- 새 writer는 `gap`을 쓰지 않고 `workflow_feedback`을 쓴다. reader는 `/1`의 `gap`을 `workflow_feedback`으로 읽는다(이미 #12).
- 입력 `gap`(`analytics record gap`, `/analytics record gap`, `analytics_record(kind="gap")`, SDK `record("gap", …)`)은 `bad_kind`와
  `workflow_feedback` 안내로 거절한다. 옛 이름은 별칭 없이 새 이름을 알리는 오류라는 용어집의 원칙(첫 문단, §8 "두 용어 체계로 안내하지
  않는다")을 kind 입력에 그대로 쓴다.
- 사람·에이전트가 기록할 수 있는 kind 목록은 ③에서 writer의 `RECORD_KINDS`(`finding, decision, assumption, question, todo, note,
  workflow_feedback, resolve`)다. `/2`의 나머지 kind는 그 기능의 Issue(#14 이후)가 연다.

### 2.9 본문 길이 (OQ2)

- 새 writer는 본문을 자르지 않는다. 지금의 `MAX_EVENT_TEXT`(4,000자)와 확장의 `note_cut` 응답은 `/2` 쓰기에서 없어진다.
- 권장(OQ2): UTF-8 32 KiB 이하는 전부 보존하고, 넘으면 아무것도 쓰지 않고 `text_too_long`(원문 길이, 상한, "긴 글은 파일이나 Report로
  저장하고 참조하라")으로 거절한다. aelix read 도구는 50 KiB를 넘는 한 줄을 읽지 않으므로(현재 계약 A) 참조 24개와 JSON 머리를 더한
  줄이 그 아래에 남게 한다.
- 화면과 맥락 블록은 지금의 생략 표시(#11, 현재 계약 A의 줄임과 다시 읽을 위치)를 그대로 쓴다.

---

## 3. 새 모듈과 계층표 (완료 조건 5)

### 3.1 모듈

| 모듈 | 책임 | 외부 | 들어오는 PR |
|---|---|---|---|
| `src/aelix_analytics/writer.py` | 표식 판정과 게이트, `Origin`, 오류 클래스, 가족 태그·경로 배치표, UTF-8·LF 텍스트와 원자 JSON 쓰기, journal 덧붙이기 규약, `record_update` 확인, 쓰는 시점의 결박 | stdlib | ①(판정·게이트·헬퍼·`Origin`), ③(`Writer`) |
| `src/aelix_analytics/migration.py` | 복사 전환: 사전 검사, 계획, 복사, 원본 재확인, 사본 검증, 매핑·보고서, 내구 쓰기, 표식, 이어 쓰기 | stdlib | ② |

둘 다 최상위 파일이다. 하위 package를 만들지 않아 줄 끝 검사(`tests/test_line_endings.py`의 최상위 glob)가 범위를 넓히지 않고 두 파일을 본다.

### 3.2 계층표의 변경

`tests/test_layers.py`의 허용 판정은 `may_import`만 보고, 수준은 위반 메시지와 문서의 규칙("낮은 수준이 높은 수준을, 같은 수준의 형제끼리
import하지 않는다")에 쓰인다. writer를 core 아래에 두기 위해 core 이상의 수준을 하나씩 올린다.

| 계층 | 수준(전 → 후) | 모듈 | import해도 되는 계층(후) | 외부 | PR |
|---|---|---|---|---|---|
| `ports`·`read_model`·`distribution` | 0 → 0 | 그대로 | 없음 | stdlib | — |
| `writer`(새) | — → 1 | `writer.py` | `ports`, `read_model` | stdlib | ① |
| `core` | 1 → 2 | `core.py` | `ports`, `read_model`, `writer` | stdlib | ① |
| `runtime` | 2 → 3 | 그대로 | 지금 목록 + `writer` | 그대로 | ① |
| `workspace` | 2 → 3 | `report.py`(⓪ 뒤 `workspace.py`) | 지금 목록 + `writer` | 그대로 | ① |
| `migration`(새) | — → 3 | `migration.py` | `core`, `writer`, `ports`, `read_model` | stdlib | ② |
| `sdk` | 3 → 4 | 그대로 | 지금 목록 + `writer` | 그대로 | ① |
| `kit_entry` | 3 → 4 | 그대로 | 지금 목록 + `writer`(①), `migration`(②) | 그대로 | ①② |
| `launcher` | 3 → 4 | `cli.py` | 그대로(`distribution`, `ports`) | stdlib | ① |
| `host_adapter` | 3 → 4 | 확장 | 그대로. writer의 이름은 `core`가 다시 내보낸 것을 쓴다 | 그대로 | ① |
| `user_scripts` | 4 → 5 | 그대로 | `sdk` | 그대로 | ① |

같은 PR에서 함께 바꾸는 것:

- ARCHITECTURE §11의 세 표(시험이 비교한다)와 `PROJECT_LAYERS`.
- aelix 프로세스: 확장은 `core.py`를 합성 package로 로드하고(ADR-0003 결정 3) `writer.py`는 상대 import로 따라 로드된다. 새 동적 로드 선언은
  없다. `tests/test_platform.py`의 호스트 쪽 로드 시험의 모듈 목록(`_platform`·`core`·`relations`)에 `writer`를 더하고(①),
  `KIT_OK:lab.run/1` 확인은 ③에서 새 run 가족으로 바꾼다. ADR-0001 적용 현황 행 1과 현재 계약 F "프로세스 경계"를 ①에서 고친다.
- `migration.py`는 aelix 프로세스가 로드하지 않는다(`core`가 import하지 않는다). 런처는 kit 자식으로 부르므로 `launcher`의 허용 목록과
  `DYNAMIC_LOADS`는 바뀌지 않는다.
- `KIT_VERSION`은 `core.py`에 남긴다. `tools/model_eval.py`가 `core.py`의 글에서 정규식으로 읽고 `tests/test_packaging.py`가 배포 버전과 비교한다.
  writer는 core를 import하지 않으므로 `writer_version`을 인자로 받고 core가 `KIT_VERSION`을 넘긴다(시험은 다른 값을 넣는다).
- 오류 클래스(⓪ 뒤 `AnalyticsError`)는 writer로 옮기고 core가 같은 이름으로 다시 내보낸다. JSON 오류 형태는 그대로다.
- 지금 `core.py`의 `open_text`·`write_text`·`newlines_as_lf`·`write_json`·`append_jsonl`은 ①에서 writer로 옮기고 core가 다시 내보낸다.
  `tests/test_line_endings.py`의 쓰기 검사가 허용하는 함수 이름을 writer 쪽으로 바꾼다. 다섯 함수의 이동이라 "대규모 파일 이동"이 아니다.

### 3.3 버린 대안

| 대안 | 버린 이유 |
|---|---|
| writer를 `core.py` 안에 둠 | 2,809줄 모듈이 더 커지고 ARCHITECTURE §11의 "#13부터 자기 모듈을 등록"과 어긋난다. 고장 주입 시험이 core 없이 writer를 부를 수 없다 |
| writer를 `core` 계층에 두고 `core`가 `core`를 import하게 함 | 계층 검사로는 writer → core의 순환을 잡지 못해 따로 순서 시험이 필요하다. 새 계층이면 기존 검사가 막는다 |
| writer를 수준 0에 둠 | 수준 0은 아무것도 import하지 않는 관례라 잠금·교체·journal 재생을 모두 주입받아야 하고 `relations`의 재생을 직접 쓸 수 없다 |
| `core/` 하위 package | `core.py`와 이름이 겹치고 줄 끝 검사의 범위를 넓혀야 한다 |

---

## 4. 공통 writer

### 4.1 `Origin`: 작성 주체·실행 수단·세션 (완료 조건 3)

```python
@dataclass(frozen=True)
class Origin:
    actor: str                     # who wrote or asked: "human" | "agent" | "system"
    means: str                     # how: "cli" | "sdk" | "tool" | "command" | "hook" (closed list)
    actor_basis: str = "declared"  # "declared" | "inferred" (OQ1)
    session: str | None = None     # the host's session reference, opaque; optional (Q17)
    request_id: str | None = None  # ADR-0006 8; checked by ADR-0003 decision 6 when given
```

- writer의 모든 쓰기 함수는 `Origin`을 필수 인자로 받는다. writer 안에는 기본 작성 주체가 없고 환경 변수를 읽지 않는다. 진입점이 만들어
  넘긴다(ARCHITECTURE §4 "작성·요청 주체와 실행 수단은 진입점에서 명시적으로 전달").
- 기록의 출처를 덮어쓰지 않는다. 재실행(`rerun_of`)의 새 run은 새 요청의 `Origin`을 쓰고 원래 run의 `actor`는 그 run 기록에 그대로 있다.
- 완료 이벤트(run·가져오기)는 그 요청의 `Origin`이다. kit이 스스로 하는 맥락 동기화는 `actor="system"`과 그 작업을 부른 진입점의 `means`다.
- ①은 `Origin`을 모든 진입점에서 배관하고 `/1` 줄에는 지금 필드(`actor`, `session`)만 쓴다. ③이 모든 필드를 기록한다.

### 4.2 진입점별 `Origin`

| 진입점 | `actor` | `actor_basis` | `means` | `session` |
|---|---|---|---|---|
| 런처 `analytics`, `bin/analytics`, `python -m aelix_analytics` | `AELIX_ANALYTICS_ACTOR`, 없으면 `human` (OQ1) | 변수가 있으면 `declared`, 없으면 `inferred` | `cli`. 확장이 부른 kit 자식은 확장이 넘긴 값 | `AELIX_ANALYTICS_SESSION` |
| 확장의 모델 도구 `analytics_*` | `agent` | `declared` | `tool` | aelix 세션 |
| 확장의 `/analytics` 명령 | `human` | `declared` | `command` | aelix 세션 |
| 확장의 hook(프롬프트 직전 맥락 동기화, `agent_end`의 Workspace 예약), 상태 도구 안의 동기화 | `system` | `declared` | `hook`(상태 도구 안이면 `tool`) | aelix 세션 |
| SDK, run 밖 | `AELIX_ANALYTICS_ACTOR`, 없으면 `human` (OQ1) | 위와 같음 | `sdk` | `AELIX_ANALYTICS_SESSION` |
| SDK, run 안 | 그 run의 `actor` | 그 run의 값 | `sdk` | 그 run의 값 |
| 전환 | 명령을 부른 쪽의 값(위 첫 행) | 위와 같음 | `cli` | 위와 같음 |

필요한 바뀜(③, 배관은 ①):

- 런처와 `bin/analytics`는 `AELIX_ANALYTICS_ACTOR`를 기본값 `human`으로 채워 넘기지 않는다(`cli.child_env`, `bin/analytics`). 사용자가 설정했을
  때만 넘기고, 기본값은 kit이 채워 `inferred`로 남긴다.
- 확장은 kit 자식에 실행 수단을 알리는 내부 변수 `AELIX_ANALYTICS_MEANS`를 넣는다(확장 `child_env`). `AELIX_ANALYTICS_CANCEL_FILE`처럼 내부
  프로토콜이라 용어집 §3의 역할 목록에 넣지 않고 현재 계약 D에 적는다. kit은 이 변수가 없으면 `cli`다.
- run 밖 SDK의 `actor="script"`는 작성 주체와 실행 수단을 섞은 값이다. `/2`에서는 `human`(또는 선언한 값)과 `means="sdk"`로 쓴다(OQ1).
  reader는 옛 값 `script`를 그대로 읽는다.

### 4.3 표식 판정과 게이트

```python
def marker_state(root: Path) -> MarkerState: ...
def admit(root: Path, op: str, *, side: bool = False, writer_version: str,
          writable: frozenset = WRITABLE) -> MarkerState | None: ...
WRITABLE = frozenset({"lab.project/1"})  # PR 3 makes it {"analytics.project/2"}
```

- `marker_state`는 부수 효과 없는 판정이고 매번 두 표식 파일을 새로 읽는다(캐시 없음). 진입점·reader·status가 같은 함수를 쓴다.
- `admit`는 쓰기 작업의 첫 부수 효과(잠금 파일 생성 포함) 전에 부른다. 이미 연 `Project`나 프로세스 시작 때의 판정을 다시 쓰지 않는다
  (ADR-0005 결정 6). 일반 쓰기가 거절이면 오류를 낸다. `side=True`(읽기 작업의 부수 쓰기)이면 오류 대신 `None`을 돌려주고 호출자는 그 쓰기를
  건너뛴다.
- 비용은 표식 두 개의 `stat`과 작은 JSON 하나의 읽기다.

| 디렉터리의 상태 | `state` | ①·② 일반 쓰기 | ③ 일반 쓰기 | 부수 쓰기 | 읽기 |
|---|---|---|---|---|---|
| 표식 없음 | `none` | `no_project` | `no_project` | — | `no_project` |
| 표식 없음, `.analytics/migration/` 또는 `.analytics/migration.lock`이 있음 | `incomplete` | `migration_incomplete`(②부터) | 같음 | 건너뜀 | `migration_incomplete`(status는 ledger의 진행 상태를 오류 `details`에) |
| `lab.json`만: `lab.project/1`, `schema` 없음, 또는 JSON으로 읽을 수 없음 | `legacy` | 허용(`/1` writer) | `migration_required` | ①·② 허용, ③ 건너뜀 | 허용 |
| `lab.json`만: 다른 가족·버전 | `unknown` | `project_too_new` | 같음 | 건너뜀 | 아는 기록만, 모르는 표식을 보고 |
| `analytics.json`만: `analytics.project/2`, `min_writer_version` ≤ kit | `current` | `project_too_new`(이 kit에는 `/2` writer가 없다) | 허용 | ①·② 건너뜀, ③ 허용 | 허용 |
| 같은데 `min_writer_version` > kit | `current` | `project_too_new` | `writer_too_old`(필요한 버전 안내) | 건너뜀 | 허용 |
| `analytics.json`만: 다른 가족·버전 | `unknown` | `project_too_new` | 같음 | 건너뜀 | 아는 기록만 |
| `analytics.json`만: JSON 객체가 아님, 필수 필드 없음, `min_writer_version`이 정규형이 아님 | `invalid` | `marker_invalid` | 같음 | 건너뜀 | 아는 기록만, 사유를 보고 |
| 두 표식 | `conflict` | `marker_conflict` | 같음 | 건너뜀 | `marker_conflict`(status는 두 표식의 내용·해시·`migration` 필드와 해결 안내를 오류 `details`에) |

- 깨진 `lab.json`을 `legacy`로 보는 것은 ①에서 main 대비 회귀를 만들지 않기 위해서다(지금 `Project.config`는 깨진 표식을 빈 객체로 읽고
  쓰기를 막지 않는다). ③ 뒤에는 어차피 쓰지 않는다.
- 두 표식이면 읽기도 거절한다. 어느 형식으로 읽을지 정할 수 없고 자동으로 고르지 않는다(용어 점검 §5 9항).
- `PEP 440` 비교는 PR #76이 더한 `relations.version_key`를 쓴다.
- 쓸 수 있는 표식은 `WRITABLE` 상수 하나로 정한다. ③은 이 값을 바꾸고 `/2` writer를 넣는다. 그래서 ①에서 만든 진입점 행렬(11.3절)이 ③에서
  기대값만 바뀐다.

프로젝트 찾기(①):

- 시작 디렉터리에서 위로 올라가며 `analytics.json`·`lab.json` 가운데 하나라도 있는 첫 디렉터리에서 멈추고(②부터는 표식 없는 전환 기록
  `.analytics/migration/`이 있는 디렉터리에서도) 그 디렉터리를 위 표로 판정한다. 지금은 `lab.json`만 찾는다(`core.find_project`). 그래서
  `lab.json` 프로젝트 아래의 `analytics.json` 디렉터리에서 위쪽 프로젝트에 쓰는 일(상태 지도의 프로브 P4)이 사라진다.
- 명시한 루트(`--project`, `AELIX_ANALYTICS_PROJECT`, 확장의 활성 프로젝트, run의 `capture.ACTIVE.project`)도 올라가지 않고 같은 판정을 거친다.
- 확장의 `_resolve_project`, 런처의 `find_project`, `bin/analytics`의 탐색도 같은 규칙으로 바꾼다. 셸·런처는 표식의 내용을 해석하지 않고 kit이 판정한다.
- ③부터 런처·`bin/analytics`는 찾은 디렉터리가 표식 없는 전환 목적지(`incomplete`)이면 `uv run --project` 대신 `uv run --no-project`로 kit을 불러
  판정만 받는다. 그 자리에서 uv가 `.venv/`를 만들면 이어 쓰기 검사가 낯선 항목으로 보기 때문이다(6.10절에서도 `.venv/`를 무시한다).
- uv 환경(`.venv/`, uv가 갱신하는 `uv.lock`)은 기록이 아니라 환경이라 게이트의 대상이 아니다. 지금처럼 프로젝트 명령은 그 프로젝트의 환경으로
  돈다. 환경을 건드리지 않아야 하는 곳은 전환 명령이 다루는 원본과 표식 없는 목적지이며, 둘 다 `--no-project`로 부른다(6.1절).

다시 읽기:

- journal에 쓰는 함수는 journal 잠금을 잡은 뒤 표식을 한 번 더 읽는다. 더 새로운 kit이 `min_writer_version`을 올리는 일(ADR-0006 1.3)도 같은
  잠금 안에서 하므로, 작업 시작 때 판정을 통과한 오래된 writer도 잠금 안에서 `writer_too_old`를 받는다. run이면 정본만 있고 완료 이벤트가
  없는 `incomplete`로 남는다(조용한 성공이 없다).
- run 안 SDK의 `save`·`visualize`·`record`도 호출마다 `admit`을 부른다. 스크립트 안에서는 거절이 예외가 되어 run이 `error`로 남는다.
- ③의 status는 `current` 프로젝트의 위쪽 디렉터리에 `lab.json`이 있으면 경고한다. 그 아래에서 옛 kit을 부르면 위쪽 프로젝트에 쓰기 때문이다.
  쓰기를 막지는 않는다.

### 4.4 한 곳의 쓰기

| 함수·상수 | 하는 일 | 지금 위치 |
|---|---|---|
| `open_text`·`write_text`·`newlines_as_lf` | UTF-8(BOM 없음)·`\n` 텍스트(ADR-0007 결정 2) | `core` → ① `writer` |
| `write_json(path, obj, *, platform, through_link=False)` | 임시 파일 + `AtomicFiles.replace`, 끝 줄바꿈 | `core` → ① `writer` |
| `append_line(path, line: bytes)` | 마지막 바이트가 `\n`이 아니면 `\n`을 먼저 쓰고, 줄과 `\n`을 한 번의 write로 | ③ 새 함수. `/1` writer의 같은 수정은 OQ8 |
| `FAMILIES` | `{"project": "analytics.project/2", "event": "analytics.event/2", "run": "analytics.run/2", "dataset": "analytics.dataset/1", "trust": "analytics.trust/1", "migration": "analytics.migration/1"}`. 쓰기 함수는 키를 받고 태그 문자열을 받지 않는다 | ③(`migration`은 ②) |
| `Layout`, `LegacyLayout` | 표식·관리 디렉터리·`workspace/`·`data/imported/`·`runs/<id>/visualizations/`·잠금 파일의 이름표. 옛 배치는 읽기용 | ①에서 `LegacyLayout`, ③에서 `Layout` |

- 저장 이름의 개명이 `Layout` 한 곳과 reader를 고치는 일이 되게 한다. 경로 문자열을 쓰는 코드에 흩어 두지 않는다.
- 일반 쓰기는 fsync하지 않는다. 지금 kit 전체가 하지 않고 현재 계약 G가 원자성·복구를 약속하지 않는다. 내구 쓰기는 전환의 커밋 전에만 한다(6.4절).

### 4.5 `Writer` (③)

```python
class Writer:
    def __init__(self, root: Path, *, platform, origin: Origin, writer_version: str,
                 clock=None): ...
    def record(self, kind: str, text: str, *, refs=(), details=None,
               requires: str | None = None) -> Written: ...
    def update(self, target: str, *, expected: str, new: str, text: str) -> Written: ...
    def write_run(self, run_dir: Path, record: dict) -> None: ...
    def write_dataset(self, meta: Path, record: dict) -> None: ...
    def locked(self): ...  # context manager: journal lock, marker re-read, parsed journal, replay
```

`record`의 순서:

1. `admit(root, "record")`. 거절이면 아무것도 쓰지 않는다.
2. kind·본문(OQ2)·참조 형태·`request_id`의 이름 규칙을 검사한다.
3. 이름만 받은 참조를 결박한다(4.7절). 읽기 모델을 잠금 밖에서 seq N 기준으로 계산한다.
4. `locked()`: journal 잠금(`.analytics/lock`)을 잡고 표식을 다시 읽는다(4.3절). journal 바이트를 `relations.Journal`로 읽어 `next_seq`(파싱·복구한
   모든 줄의 최대 seq + 1, 끊긴 조각의 seq는 세지 않음)를 얻는다. 마지막 seq가 N이 아니면 결박을 잠금 안에서 다시 계산한다.
5. `requires`가 표식의 `min_writer_version`보다 높으면 표식을 그 값으로 올린다(4.8절).
6. 줄을 만들어 `append_line`으로 쓴다. 끊긴 줄 뒤면 `\n`이 먼저다.
7. 잠금을 놓고 `Written{event, request_id, bindings, state_after?}`를 돌려준다.

`locked()`는 잠금 안에서 파싱한 journal·재생 결과와 덧붙이기를 한 문맥에 묶는다. `update`가 이것을 쓰고, #19의 Report 저장(확인부터 완료
이벤트까지 journal 잠금, ADR-0006 7절)도 같은 문맥을 쓴다. 쓸 때마다 journal 전체를 읽으므로 M 규모(10,000줄)의 비용을 ③의 측정 기록에 남긴다.
느리면 크기·mtime으로 다음 seq를 기억하는 최적화를 같은 결과의 시험과 함께 넣는다.

### 4.6 같은 상태 변경 (③, 완료 조건 4)

- `update(target, expected, new, text)`: `locked()` 안에서 `relations.Replay`로 대상의 지금 상태를 계산한다. 대상이 하나로 있고, 지금 상태가
  `expected`이며, `transition_allowed`가 그 전이를 허용할 때만 `record_update` 줄을 쓴다. 아니면 `state_conflict`이고 `details`에 지금 상태,
  허용하는 전이, 대상 판정(`target_missing`·`target_ambiguous`), 그 상태를 만든 이벤트의 id와 `request_id`를 담는다. 아무것도 쓰지 않는다.
  reader와 같은 재생 코드라 둘이 갈리지 않는다(ADR-0006 확인 5).
- 정정은 `record(kind, text, refs=[{… "relation": "supersedes"}])`이고 기대 상태가 없다. 같은 기록의 정정이 둘이면 쓰고, 응답의 `state_after`가
  `conflict`와 두 정정의 id를 보인다(ADR-0006 6절의 표, 최신 시각으로 고르지 않음).
- 이 기능의 공개 입력(CLI·SDK·도구 인자)을 ③에서 열지는 OQ11이다.

### 4.7 쓰는 시점의 참조 결박 (③)

입력은 지금처럼 문자열 목록(`--ref`, 도구의 `refs`, SDK의 `refs`)이다. 지금의 `core.classify_refs` 순서로 나누고 결박한다.

| 입력 | 참조 | 결박 |
|---|---|---|
| run id | `{type: run, run_id}` | `author`, `declared` |
| `eN` | `{type: event, event_id}` | `author`. `--resolves`면 relation `resolves` |
| CONTEXT 버전 파일 이름 | `{type: context_version, …, digest}` | `author`, digest는 그 파일의 sha256 |
| `script.py::id` | `{type: visualization_version, key}` | 같은 요청의 run이 있으면 그 run(`author`). 없으면 그 키의 최신 성공 버전(`inferred`, `token`, `digest`). 후보가 없으면 `none` |
| Visualization id만 | `{type: visualization_version, token}` | 키가 하나면 위와 같음. 여러 스크립트에 있거나 없으면 `none`과 후보 목록을 응답에 |
| 데이터셋 이름·경로 | `{type: dataset_import, path, digest}` 또는 `{type: data_observation, run_id, role: output, path, digest}` | `inferred`. 후보가 없으면 `none`과 `token` |
| 그 밖 | `{type: visualization_version, token}` | `none`(reader는 `unknown`/`unresolved_name`) |

- 기본 relation은 `cites`, basis는 `declared`다. 다른 relation을 받는 공개 입력은 OQ11과 함께 정한다.
- 최신 성공 버전은 `execution_status=ok`이고 완료 이벤트의 seq가 가장 큰 run의 것이다(ADR-0006 3절).
- 결박 결과는 응답의 `bindings`에 돌려준다. 확정 연결은 `author`뿐이며 `inferred`를 확정처럼 보이지 않는다(Q4).
- 참조는 이벤트당 24개(`MAX_EVENT_REFS`)를 그대로 둔다.

### 4.8 정본과 완료 이벤트, `min_writer_version` (③)

- run: `write_run`이 `analytics.run/2`를 `write_json`으로 쓴다(지금처럼 run 하나에 최대 세 번). run 전체는 run 잠금 안이다. 정본을 확정한 뒤 완료
  이벤트 `record("run", …, refs=[{type: run, run_id, relation: about}])`를 마지막에 쓴다.
- 가져오기: `data/imported/<name>.parquet`(임시+교체) → `meta.json`(`analytics.dataset/1`) → `import` 이벤트(`dataset_import` 참조 `about`). 둘 사이가
  끊기면 reader가 `incomplete`로 보인다(이미 `relations`).
- 맥락 동기화: 버전 파일(`.analytics/context/<stamp>_<sha12>.md`, 바이트 그대로) → `context` 이벤트. 비교는 `.analytics/context`와 `.lab/context`의
  마지막 버전이다(전환 직후 같은 CONTEXT.md를 새 버전으로 남기지 않는다).
- `min_writer_version`: init과 전환은 ③의 `KIT_VERSION`을 쓴다. 뒤의 PR이 새 쓰기 규칙(kind·전이 등)을 더하면 그 쓰기의 `requires`에 자기 버전을
  적고 writer가 잠금 안에서 표식을 올린다(ADR-0006 1.3). ③에서는 기록 예제(`normal`·`future-version`)와 시험으로만 이 경로가 돈다.

### 4.9 응답과 `request_id`

- 모든 쓰기 응답(kit JSON, 확장 도구의 details)이 `request_id`를 담는다. 호출자가 주면 그 값, 없으면 kit이 만든 값이다(ADR-0006 8절).
- 줄에는 호출자가 준 값만 남긴다("받아 남길 뿐"). 일반 쓰기의 중복 방지는 하지 않는다(베타 뒤).
- writer API는 ③부터 받는다. 공개 입력(`--request-id`, 도구 인자)을 언제 열지는 OQ11이다.

### 4.10 읽기 작업의 부수 쓰기

`admit(…, side=True)`가 `None`이면 건너뛰는 대상: `queries.jsonl`, `telemetry.jsonl`, 해시·프로필 캐시와 Workspace 지문(`cache/`), status와 hook의
맥락 동기화, `agent_end`의 Workspace 렌더 예약. 읽기 응답은 건너뛴 사실을 한 번 알린다(status JSON의 `writes_skipped`와 이유).

### 4.11 오류 코드

새 이름은 그 PR에서 용어집 §6의 표에 먼저 적고 현재 계약 D에 더한다(ADR-0005 결정 5). 글은 영어다(용어집 §7).

| 코드 | 새·기존 | 언제 | PR |
|---|---|---|---|
| `no_project` | 기존 | 표식 없음 | — |
| `project_too_new` | 기존(뜻을 넓힘) | 이 kit이 쓰지 않는 표식(①·②의 `analytics.json`, 모르는 가족·버전) | ① |
| `marker_conflict` | 새 | 한 디렉터리에 두 표식 | ① |
| `marker_invalid` | 새 | `analytics.json`을 읽을 수 없거나 필수 필드·정규형이 맞지 않음 | ① |
| `migration_incomplete` | 새 | 표식 없는 전환 목적지 | ② |
| `writer_too_old` | ADR-0006 | `min_writer_version` > kit | ③ |
| `migration_required` | 새 | ③ 뒤 `lab.json` 프로젝트의 일반 쓰기 | ③ |
| `state_conflict` | ADR-0006 | `record_update`의 대상·기대 상태·전이가 맞지 않음 | ③ |
| `text_too_long` | 새(OQ2) | 본문 상한 초과 | ③ |
| `bad_kind` | 기존 | 모르는 kind, `gap`이면 `workflow_feedback` 안내 | ③ |
| `name_not_portable` | 기존 | 이식할 수 없는 `request_id` | ③ |
| `source_not_legacy` | 새 | 전환 원본이 `legacy`가 아님(`details.state`) | ② |
| `nested_project` | 새(OQ10) | 원본 아래에 다른 표식 또는 원본에 `.analytics/` | ② |
| `destination_invalid` | 새 | `details.reason`: `overlaps_source`·`not_empty`·`inside_project`·`not_a_directory`·`name_collision` | ② |
| `destination_busy` | 새 | 다른 전환이 같은 목적지를 잡고 있음 | ② |
| `source_busy` | 새 | 원본의 run·journal 잠금을 다른 프로세스가 잡고 있음 | ② |
| `source_changed` | 새 | 복사 전후나 이어 쓰기 때 원본의 목록·해시가 다름(바뀐 경로 목록) | ② |
| `source_unreadable` | 새 | 원본 파일을 읽지 못함(권한, 사용 중 파일) | ② |
| `destination_mismatch` | 새 | 목적지에 ledger가 모르는 항목이나 해시가 다른 사본 | ② |
| `destination_write_failed` | 새 | 목적지 쓰기를 운영체제가 거절(긴 경로, 예약 이름) | ② |
| `insufficient_space` | 새 | 필요한 바이트가 목적지의 빈 공간보다 큼(쓰기 전) | ② |

다시 부른 전환이 이미 커밋된 목적지를 만나면 오류가 아니라 `ok`와 `already_migrated: true`다(6.9절).

---

## 5. 읽기: 옛·새 프로젝트와 기록의 판별

### 5.1 수준별 판별

| 수준 | 수단 | 규칙 |
|---|---|---|
| 프로젝트 | 표식 파일 이름, `schema`의 가족·버전, `min_writer_version` | 4.3절 표. reader와 writer가 같은 `marker_state`를 쓴다 |
| 기록 | 줄·파일의 `schema` | `relations.unsupported_reason`(`unknown_family`·`unknown_version`·`unknown_kind`). `relations.KNOWN`에 ①이 `analytics.run/2`·`analytics.dataset/1`을, ②가 `analytics.migration/1`을 더한다 |
| 경로 | 옛 경로와 새 경로를 함께 읽음 | 5.3절 |
| 경로 정체성의 시대 | run의 `env.kit_version` | PR #76의 `version_key`·`older_kit`·`PATH_IDENTITY_SINCE` 그대로 |

읽기 규칙이 바뀌는 PR은 `relations.READER_FORMAT`을 올린다(`source_revision`이 그 값을 담는다).

### 5.2 `/2` 기록의 표시 (①)

- 지금 `core.read_events`는 `lab.event/1`만 돌려줘 맥락 블록·status 목록·Workspace Timeline에 `/2` 이벤트가 보이지 않고 status가 `not_listed`로만 센다
  (상태 지도의 프로브 P6). ①은 `/1`·`/2`를 seq 순서로 함께 돌려준다. 화면용 보기(`id, seq, kind, actor, means?, text, recorded_at, schema`)로 맞추고,
  `/2` 참조는 화면용으로만 지금의 `refs` 모양에 투영한다(판정은 `relations.ReadModel`이 이미 낸다).
- `/2` kind는 지금 kind와 같은 자리에 둔다. `workflow_feedback`은 `gap`과 같은 "Workflow feedback" 구역이고 지표·재사용·게시 kind는 Timeline에만
  보인다. 맥락 블록의 핵심 정보·열린 항목(현재 계약 C1)은 `/2`의 상태 재생 결과를 쓴다.
- status는 표식의 판정(`format`, `writable`, 이유)을 JSON과 글에 보인다.

### 5.3 경로 (①)

- run: `runs/*/run.json`이 `lab.run/1`이든 `analytics.run/2`이든 읽고, `charts[]`와 `visualizations[]`를 같은 보기로 읽는다(`relations`, `core.list_runs`, Workspace).
- 데이터셋: `data/imported`·`data/raw`·`data/derived`를 걷는다(`core.list_datasets`, `relations`).
- CONTEXT 버전: `.analytics/context` 다음 `.lab/context`(이미 `relations.CONTEXT_DIRS`).
- 보조 로그: Workspace의 도구 사용표는 `.analytics/telemetry.jsonl`과 `.lab/telemetry.jsonl`을 함께 센다.

### 5.4 옛 경로의 추론 대응과 전환 매핑 (②)

- PR #76은 `Project.legacy_identity`로 kit 0.5.0 전 run이 기록한 경로를 지금 루트 기준의 정체성 경로로 대응하고 `basis: inferred`로만 보인다
  (`relations.read(…, legacy_path=)`). 대응은 그 run이 옛 kit의 것일 때만 하고, 대응한 경로가 지금 있거나 어떤 run의 출력일 때만 받는다.
- 전환하면 루트가 바뀐다. 원본 루트 아래의 옛 절대 경로는 목적지 루트로 대응되지 않는다. ②는 표식의 `migration` 필드로 `mapping.json`(6.7절)을
  찾아, `legacy_identity`가 지금 루트에 더해 `root.from`의 철자로 시작하는 절대 경로를 **지금** 루트 아래의 상대 경로로 바꾸고 접두 대응(`.lab/` →
  `.analytics/`)을 적용한 뒤 같은 규칙으로 판정하게 한다. 목적지를 나중에 옮겨도 대응은 지금 루트로 된다. 기록은 고쳐 쓰지 않는다
  (ADR-0006 9절 마지막 항목).
- 0.5.0 이후 run의 상대 경로는 전환 뒤에도 같은 경로라 대응이 필요 없다. 0.5.0 이후 run의 프로젝트 밖 절대 경로는 그대로 밖이다.
- `mapping.json`이 없거나 해시가 표식·보고서와 다르면 대응하지 않고 status가 그 사실을 보인다(조용히 추론하지 않는다).

### 5.5 kit마다 보이는 것

| kit | `lab.json` 프로젝트 | ③이 만든 `analytics.json` 프로젝트 | 두 표식 | 표식 없는 전환 목적지 |
|---|---|---|---|---|
| ① 전(0.5.0 이하) | 읽기·쓰기 | 찾지 못함(`no_project`). 위쪽에 `lab.json`이 없게 전환이 막는다(init은 OQ9) | `lab.json`으로 봄(P5) | `no_project` |
| ①·② | 읽기·쓰기(`/1`) | 읽기, 쓰기 `project_too_new` | `marker_conflict` | ① `no_project`, ② `migration_incomplete` |
| ③ 이후 | 읽기만, 쓰기 `migration_required` | 읽기·쓰기(`min_writer_version` 확인) | `marker_conflict` | `migration_incomplete` |

---

## 6. 복사 전환

### 6.1 명령과 실행 위치

- 사람의 명령: `analytics migrate --to DEST [--dry-run]`. 원본은 다른 명령처럼 `AELIX_ANALYTICS_PROJECT` 또는 작업 디렉터리에서 가장 가까운 표식의
  프로젝트다. kit 명령은 `python -m aelix_analytics --project SRC migrate --to DEST [--dry-run]`이고 JSON 한 줄 규칙은 다른 명령과 같다.
- 런처·`bin/analytics`는 `init`과 같은 방식으로 kit을 부른다: `uv run --no-project --python 3.12 --quiet python -m aelix_analytics …`(PYTHONPATH =
  실행 루트). 원본의 환경(`uv run --project SRC`)으로 부르지 않는다. 프로브(macOS, uv 0.11.19): `uv run --project DIR`은 `DIR/.venv`와 `DIR/uv.lock`을
  만들었고 맞지 않는 `uv.lock`을 다시 썼다. `--frozen --no-sync`도 `.venv/`를 만들었다. 프로젝트 디렉터리 안에서 `uv run --no-project`는 아무것도
  만들지 않았다. 목적지의 환경으로도 부르지 않는다(표식 전에 `.venv/`가 생긴다).
- 전환은 stdlib만 쓴다. `__main__`의 `migrate`는 분석 라이브러리를 import하지 않는다(지금 `init`과 같다).
- 커밋 뒤 런처는 목적지 환경을 `uv sync --project DEST --locked`로 준비한다. 프로브: `--locked`는 맞는 `uv.lock`을 바꾸지 않았고, 맞지 않는
  `uv.lock`에서는 종료 1로 실패하며 그 파일을 바꾸지 않았다(실패해도 `.venv/`는 만들었다). 실패해도 전환은 끝났고, 응답이 따로 알리며 다음
  명령이 지금처럼 환경을 준비한다.
- 모델 도구와 `/analytics migrate`는 만들지 않는다. 전환은 사람의 명시적 명령이고(ADR-0005 결정 5) 기본 모델 도구는 같은 여섯 역할이다(용어집 §2).
  수백 MB의 복사가 aelix 프로세스를 붙잡지 않게 하는 이유도 있다. 확장의 거절 글은 터미널 명령을 안내한다(7절).

### 6.2 사전 검사 — 아무것도 쓰기 전

| 검사 | 실패 |
|---|---|
| 원본의 판정이 `legacy` | `source_not_legacy`(`details.state`: `none`·`current`·`unknown`·`invalid`·`conflict`·`incomplete`) |
| 원본 아래(제외 목록 밖)에 `lab.json`·`analytics.json`이 없고 원본에 `.analytics/`가 없음 | `nested_project`(경로 목록, OQ10) |
| 목적지를 끝까지 푼 경로(없으면 가장 가까운 있는 조상을 풀고 나머지를 붙임)의 모든 조상과 원본을, 원본의 모든 조상과 목적지를 `samefile`로 비교 | `destination_invalid(overlaps_source)`. 대소문자·NFD 별칭, 심볼릭 링크, junction, 같은 위치의 두 철자를 잡는다 |
| 목적지의 위쪽 디렉터리에 `lab.json`·`analytics.json`이 없음 | `destination_invalid(inside_project)` |
| 목적지가 없거나 빈 디렉터리, 또는 6.10절의 이어 쓸 목적지 | `destination_invalid(not_empty)`, 파일이면 `not_a_directory` |
| 계획한 경로가 NFC+casefold로 겹치지 않고, `.lab/` 아래 이름이 새 관리 이름(`migration`·`publications`·잠금)과 겹치지 않음 | `destination_invalid(name_collision)`(겹친 쌍) |
| 목적지 쪽 빈 공간 ≥ 계획 바이트 + 여유(1% + 64 MiB) | `insufficient_space` |

같은 위치인지는 끝까지 푼 `(st_dev, st_ino)`로, `st_ino`가 0이면 끝까지 푼 경로로 판정한다(ADR-0006 9절). 대소문자 구별 여부는 플랫폼 이름이
아니라 실행 중 `samefile`로 판정한다. 목적지에서 259자를 넘을 경로와 ADR-0003 결정 6의 이름 규칙을 어기는 원본 이름은 거절하지 않고 계획·보고서에
경고로 적는다. 운영체제가 실제로 거절하면 그 파일에서 `destination_write_failed`로 멈추고 목적지는 표식 없이 남는다. 플랫폼 분기를 두지 않는다.

### 6.3 단계와 커밋 지점

```text
 DEST  [1 mkdir+dest lock] [2 ledger start] ........ [5 plan] [6 copy] ....... [8 verify] [9 report] [10 flush] [11 marker] [12 trust]
 SRC                                       [3 lock run.lock -> lock ......................... 7 re-check] release
                                            (no wait; same order as the old runner)
```

1. 목적지를 만든다(없으면 `mkdir`, 있으면 비었는지 확인). `DEST/.analytics/`를 만들고 `DEST/.analytics/migration.lock`을 기다리지 않고 잡는다.
   못 잡으면 `destination_busy`. 잡은 뒤 목적지의 내용을 다시 본다(허용: `.analytics/`, `migration.lock`, 이어 쓸 ledger).
2. `DEST/.analytics/migration/<migration_id>/progress.jsonl`에 `start` 줄을 쓴다: kit 버전, `Origin`, 원본·목적지의 정규 경로와 철자, 잡을 원본
   잠금 파일과 그 존재 여부. **원본 잠금보다 먼저 쓴다.** 잠금이 원본에 잠금 파일을 만들 수 있으므로 그 사실을 남길 곳이 먼저 있어야 한다.
3. 원본의 `.lab/run.lock`, 그다음 `.lab/lock`을 기다리지 않고 잡는다(`FileLocks.try_exclusive`, 6.12절). 옛 runner가 run 잠금을 먼저 잡고 그 안에서
   journal 잠금을 잡으므로 같은 순서다. 못 잡으면 `source_busy`(run 잠금이면 `status=running`인 run의 id와 pid). ledger에 `locked`와 각 잠금 파일을
   새로 만들었는지를 쓴다. 원본에 `.lab/`이 없으면 디렉터리를 만들지 않고 잠금 없이 진행한다(지금 포트의 `exclusive`는 부모 디렉터리를 만든다).
   그동안 생긴 옛 쓰기는 7단계의 비교가 잡는다. ledger와 보고서에 `locks: not_taken`을 적는다.
4. (잠금 안) 원본을 걷는다: 정규 파일·디렉터리·링크마다 저장된 철자의 상대 POSIX 경로, 크기, `mtime_ns`, 종류. 해시는 아직 계산하지 않는다.
   제외 목록(OQ4)과 잠금 파일은 따로 적는다.
5. 계획 `plan.json`(원본 경로 → 목적지 경로, 디렉터리, 제외, 링크 처리, 예정된 변환)을 원자 교체로 쓰고 그 sha256을 ledger의 `plan` 줄에 적는다.
6. 복사(6.4절). 파일마다 ledger에 `begin`(대상 경로와 임시 파일 이름) → 복사하며 sha256 계산 → 임시 파일을 최종 이름으로 교체 → `copied`(바이트
   수·sha256).
7. (잠금 안) 원본 재확인: 다시 걷고 모든 파일을 다시 해시해 복사 때 해시와 비교한다. 다르면 `source_changed`(바뀐·생긴·사라진 경로)로 끝내고
   ledger에 `failed`를 쓴다. 같으면 `source_checked`(원본 inventory digest)를 쓰고 원본 잠금을 놓는다. **원본 잠금은 4–7단계 동안만 잡는다.**
8. 사본 검증: 계획한 모든 사본을 다시 읽어 sha256을 비교하고, 계획에 없는 항목이 없는지 본다(이 전환의 파일과 무시하는 `.venv/`·`__pycache__/`는
   제외). 다르면 `destination_mismatch`.
9. 새 파일을 만든다: 변환한 `.gitignore`, `mapping.json`, 스크립트 제안, `source-marker.json`. 그다음 `report.json`·`report.md`를 원자 교체로 쓴다.
10. 계획한 모든 사본·새 파일과 그 디렉터리를 내구 쓰기로 flush한다(6.4절). ledger에 `durable`.
11. (목적지 잠금 안) `analytics.json`이 없음을 다시 확인하고 임시 파일 + flush + 교체로 쓴다. 표식의 `migration.report_sha256`이 보고서를 묶는다.
    **이 교체가 커밋 지점이다.** ledger에 `committed`.
12. 목적지를 새 신뢰 목록에 더한다(6.14절). ledger에 `trusted`. 실패해도 전환은 끝났고 응답이 알린다. 다시 부르면 이 단계만 한다(6.9절).

목적지 디렉터리를 다른 이름으로 만든 뒤 마지막에 이름을 바꾸는 방식은 쓰지 않는다. Windows는 안에 열린 핸들이 있는 디렉터리의 이름을 바꾸지
못하고, 다른 볼륨으로는 옮길 수 없으며, ADR-0005 결정 5가 "같은 목적지에서 잇는다"고 정했다. 표식이 없는 디렉터리는 어떤 kit에도 프로젝트가 아니므로
표식 교체 하나로 원자적인 커밋이 된다. 결정 5의 "목적지에는 먼저 전환 보고서를 만들고"는 2단계의 ledger와 5단계의 계획이고, 9단계의
`report.json`이 그것을 완결한다.

### 6.4 복사 규칙

- 이진 모드로 1 MiB 단위 스트리밍, 메모리 고정. 텍스트 모드·줄 끝 변환 없음(ADR-0007 결정 6). CRLF journal도 바이트 그대로다.
- 임시 파일은 같은 디렉터리의 짧은 이름 `.mig-<sha8(경로)>-<n>.part`다(긴 원본 이름에 접미사를 붙이면 이름 길이 한계를 넘을 수 있다). 이름은 ledger의
  `begin` 줄에 먼저 적는다. 최종 이름으로의 교체는 `AtomicFiles.replace`다(Windows의 일시적 공유 위반은 포트가 재시도). 그래서 최종 경로에는 늘 완전한
  사본만 있고, 중단되면 남는 것은 ledger에 적힌 임시 파일뿐이다.
- 권한 비트와 수정 시각은 가능한 만큼 옮긴다(`os.chmod`·`os.utime`). 보장은 바이트뿐이고 확장 속성·ACL은 옮기지 않는다고 보고서에 적는다.
- hardlink·clone을 쓰지 않는다(ADR-0005 결정 5). 원본 안의 hardlink 두 이름은 두 사본이 된다.
- 복사 순서는 경로의 코드 포인트 순이다. 계획·보고서·측정이 같은 순서를 쓴다.
- 내구 쓰기(10·11단계): 새 포트 메서드 `AtomicFiles.flush(path)`·`AtomicFiles.flush_dir(path)`(6.12절). 표식 뒤 전원이 끊겨도 표식이 가리키는 사본이
  디스크에 있게 하기 위해서다. 사본이 표식보다 늦게 디스크에 닿으면 사용자가 원본을 지운 뒤 잃을 수 있다. 일반 쓰기에는 쓰지 않는다(4.4절).

### 6.5 복사 범위와 목적지의 배치

| 원본 | 목적지 | 방식 |
|---|---|---|
| `lab.json` | `.analytics/migration/<id>/source-marker.json` | 바이트 그대로, 이름만 다름. `lab.json`이라는 이름으로 두지 않는다(그 디렉터리에서 옛 kit이 프로젝트로 찾아 쓴다) |
| `.lab/context/*.md`, `.lab/telemetry.jsonl`, `.lab/queries.jsonl`, `.lab/adhoc/`, `.lab/demo/`와 `.lab/` 아래의 그 밖의 항목 | `.analytics/` 아래 같은 상대 경로 | 바이트 그대로. 매핑의 접두 대응 `.lab/` → `.analytics/` |
| `.lab/cache/`, `.lab/lock`·`run.lock`·`report.lock` | 없음 | 재생성 가능·잠금. 보고서 `excluded`(OQ4) |
| `report/`, `.venv/`, `__pycache__/` | 없음 | 재생성 가능·환경. 보고서 `excluded`(OQ4) |
| 그 밖의 모든 것(`journal.jsonl`, `CONTEXT.md`, `runs/`, `data/raw/`, `data/derived/`, `scripts/`, `pyproject.toml`, `uv.lock`, `.git/` 등) | 같은 상대 경로 | 바이트 그대로 |
| `.gitignore` | 같은 경로, 끝에 블록 덧붙임 | 6.13절 |
| — | `analytics.json` | 새로 씀(11단계) |
| — | `.analytics/migration/<id>/`의 `progress.jsonl`·`plan.json`·`mapping.json`·`report.json`·`report.md`·`proposals/` | 새로 씀 |

- 목적지에는 `.lab/`을 만들지 않는다. 그래서 새 kit이 쓰는 `.analytics/telemetry.jsonl`·`queries.jsonl`·`context/`가 옛 기록에 이어지고, 목적지의 관리
  디렉터리가 하나다.
- 제외 목록은 원본 전후 비교에서도 뺀다. 원본에 `.git/`이 있으면 목적지에 저장소 사본이 생긴다는 것을 보고서가 알린다.
- `.analytics/migration/`은 git이 무시하지 않는다(프로젝트의 이력이다). 캐시 정리가 지우지 않는다.

### 6.6 링크와 특수 파일 (OQ3)

| 원본 항목 | 처리 |
|---|---|
| 밖을 가리키는 파일 링크, 디렉터리 링크·junction | 대상의 바이트를 링크의 경로에 보통 파일·디렉터리로 복사한다. 디렉터리는 경로 정체성의 트리 조회처럼 한 번 내려간다. 사전 공간 검사에 넣는다 |
| 안을 가리키는 상대 링크 | 같은 링크 글자로 목적지에 다시 만든다(대상이 목적지 안이라 원본에 닿지 않고, 정체성 경로가 원본과 같다). 만들 수 없으면(Windows 권한) 파일 링크는 바이트 복사, 디렉터리 링크는 복사하지 않고 경고 |
| 안을 가리키는 절대 링크 | 파일은 바이트 복사, 디렉터리는 내려가지 않고 복사하지 않음(대상의 실제 위치가 복사되므로 기록은 그 위치로 풀린다) |
| 루트·조상·관리 디렉터리로 풀리는 링크, 깨진 링크 | 복사하지 않음 |
| hardlink | 경로마다 따로 복사 |
| FIFO·소켓·장치 파일 | 복사하지 않음(바이트가 없다) |

링크마다 경로·링크 글자·처리를 보고서의 `links`에, 복사하지 않은 것은 `not_copied`에 사유와 함께 적는다. 같은 규칙이 모든 OS에서 돈다.

### 6.7 참조 매핑

파일을 옮기지 않으므로 매핑은 작다. 보고서가 `mapping.json`의 sha256을 담는다.

```json
{"schema": "analytics.migration/1", "record": "mapping", "migration_id": "20261008T010203Z-a1b2c3d4",
 "root": {"from": ["/Users/me/projects/sales-review", "/Users/me/Projects/Sales-Review"],
          "to": "/Users/me/projects/sales-review-analytics"},
 "prefixes": [{"from": ".lab/", "to": ".analytics/"}],
 "renamed": [{"from": "lab.json",
              "to": ".analytics/migration/20261008T010203Z-a1b2c3d4/source-marker.json"}],
 "kinds": [{"from": "gap", "to": "workflow_feedback", "where": "reader"}]}
```

- `root.from`: 원본의 정규 경로, 사용자가 준 철자, 원본 기록(`run.json`·`meta.json`)에 나타난 절대 경로 가운데 `samefile`로 원본 루트로 판정된 접두.
  `root.to`는 기록일 뿐이고 reader는 지금 루트로 대응한다(5.4절).
- `prefixes`·`renamed`: 위치가 바뀐 것(6.5절). 옛 run의 `charts/`·`data/raw/` 경로는 같은 경로에 사본이 있어 대응이 필요 없다.
- `kinds`: reader 규칙의 기록이다. 기록 자체는 고치지 않는다.

### 6.8 보고서와 ledger

`report.json`(`analytics.migration/1`, `record: "report"`):

| 필드 | 내용 |
|---|---|
| `migration_id` | `<UTC yyyymmddThhmmssZ>-<무작위 8자>`, `[A-Za-z0-9._-]`(ADR-0003 결정 6) |
| `kit_version`, `started_at`, `committed_at`, `actor`, `actor_basis`, `means` | 실행 정보 |
| `source` | 정규 경로·철자, 표식(가족·바이트·sha256·보존 경로), journal(바이트·줄·마지막 seq·`damaged`·`recovered`·`unsupported` 수, `relations.Journal`로 셈), inventory digest(전·후), 파일 수·바이트, 잠금(경로·원래 있었는지·잡은 시간 ms, 또는 `not_taken`) |
| `destination` | 정규 경로, `project_id`, 대소문자 구별 여부(실행 중 판정) |
| `files[]` | `path`, `to`(다를 때만), `bytes`, `sha256`(원본 전·후와 사본이 같으므로 하나), `link`(있을 때) |
| `transformed[]` | `.gitignore`의 전·후 sha256, 더한 줄, diff 경로 |
| `added[]` | 전환이 만든 파일과 sha256 |
| `excluded[]` | 경로, 사유(`environment`·`workspace`·`cache`·`lock`), 파일 수, 바이트 |
| `not_copied[]`, `links[]`, `warnings[]` | 특수 파일, 링크 처리, 긴 경로, 이식할 수 없는 이름 |
| `mapping` | `mapping.json`의 경로와 sha256 |
| `proposals` | `.gitattributes`, `scripts.diff`, `.gitignore`(링크이거나 없을 때) |
| `checks` | `source_before_after: equal`, `copies: equal`, 비교한 파일 수 |
| `notes` | 원본은 `lab.json` 프로젝트로 남고 `committed_at` 뒤 원본에 쓴 기록은 이 사본에 없다는 것(ADR-0005 결정 5), CRLF 기록을 고치지 않았다는 것(ADR-0007 결정 6), `.git/` 사본 여부 |
| `timings` | 계획·복사·재확인·검증·보고서·flush 단계의 ms |

- `report.md`는 같은 내용의 영어 요약이다(용어집 §7). 사용자 경로와 파일 이름은 그대로 둔다. 응답 JSON은 요약과 경로다.
- `progress.jsonl`은 덧붙이기만 하는 ledger다. 줄 종류: `start`, `locked`, `plan`, `begin`, `copied`, `source_checked`, `verified`, `reported`, `durable`,
  `committed`, `trusted`, `failed`(오류 코드·경로). 덧붙일 때 끊긴 줄 규칙(마지막 바이트가 LF가 아니면 LF 먼저)을 쓴다. ledger 줄은 flush하지 않는다.
  이어 쓸 때 사본을 모두 다시 해시하므로 ledger가 디스크보다 앞서도 틀린 판정이 나오지 않는다.
- 기록 schema에 `analytics.migration/1`의 세 레코드 정의를 더하고(②) `tests/test_record_contract.py`가 예제와 맞는지 본다.

### 6.9 `--dry-run`과 다시 부르기

- `--dry-run`은 읽기만 한다. 잠금도 잡지 않으므로 잠금 파일·목적지 디렉터리도 만들지 않는다. 결과는 그 순간의 추정이며 그렇게 적는다.
  보이는 것: 원본 판정, 목적지 상태(`new`·`resumable`·`committed`·`refused`와 이유), 복사할 파일 수·바이트, 제외와 이유, 링크·특수 파일 처리,
  `.gitignore`에 더할 줄, 스크립트 제안 수, 필요한 바이트와 빈 공간, 실행 중 run(읽기 모델의 `running`). 연속성 설계 §7의 "변환 계획과 영향을 먼저
  보여 준다"에 맞춘다. 종료 코드는 진행할 상태면 0, 거절될 상태면 그 오류 코드와 1(계획은 `details`).
- 이미 커밋된 목적지(표식의 `migration.id`가 ledger와 같음)로 다시 부르면 복사하지 않는다(결정 5 "표식까지 쓴 목적지에는 다시 전환하지 않는다").
  `ok`와 `already_migrated: true`를 돌려주고, 신뢰 항목이 없으면 더하며(12단계만), 사본을 보고서와 다시 대조한 결과(`same`·`changed`·`missing`,
  journal처럼 덧붙이는 파일은 기록한 길이까지의 앞부분)와 전환 뒤 원본에 생긴 변경(journal 줄 수와 seq 범위, 바뀐 파일 수)을 알린다. 이 대조는
  읽기만 한다. ADR-0005 결정 5의 "전환 뒤 원본에 쓴 기록의 안내"가 이것이다.

### 6.10 이어 쓰기와 거절

| 목적지 상태 | 같은 명령을 다시 부르면 |
|---|---|
| 없음·빈 디렉터리, `.analytics/`와 `migration.lock`만 | 새 전환 |
| ledger가 있고 `committed`가 없으며 `start`의 원본이 같음 | 이어 쓰기 검사: ① 목적지의 모든 항목이 계획한 사본(해시가 `copied`와 같음), 이 전환의 파일(ledger·계획·`begin`이 적은 임시 파일), 무시하는 `.venv/`·`__pycache__/` 가운데 하나이고, ② 원본 잠금을 다시 잡은 뒤 이미 복사한 파일마다 지금 원본의 바이트가 그 사본과 같다. 맞으면 `begin`만 있고 `copied`가 없는 임시 파일을 지우고, `begin`이 적은 대상 경로에 완전한 파일이 있으면 지금 원본과 해시가 같을 때만 사본으로 인정한다. 그리고 지금 원본으로 남은 계획을 다시 세워 잇는다 |
| 위 ①이 맞지 않음 | `destination_mismatch`(그 경로들). 덮어쓰지 않는다 |
| 위 ②가 맞지 않음(이미 복사한 파일이 원본에서 바뀌거나 사라짐) | `source_changed`(그 경로들)와 "이 목적지를 지우고 새로 시작하거나 다른 목적지를 쓰라". 자동으로 지우지 않는다 |
| ledger의 원본이 다름 | `destination_invalid(not_empty)`(다른 원본의 중단된 전환) |
| 표식이 있고 `migration.id`가 ledger와 같음 | `already_migrated`(6.9절) |
| 표식이 있고 전환으로 만든 것이 아님, ledger 없이 다른 항목이 있음 | `destination_invalid(not_empty)` |

- 이어 쓴 결과는 그 이어 쓰기의 잠금 구간에서 본 원본과 같다. 아직 복사하지 않은 파일의 바뀜은 새 계획이 담고, 이미 복사한 파일의 바뀜은 덮어쓸 수
  없으므로 거절한다. 그래서 결정 5의 "복사 전후로 원본이 다르면 실패"와 "보고서가 적은 파일만 있고 해시가 맞을 때만 잇는다"를 함께 지키면서
  중단 사이의 무관한 원본 변경 때문에 이어 쓰기를 막지 않는다.
- kit은 목적지의 사용자 파일을 지우거나 덮어쓰지 않는다. 지우는 것은 자기 `begin` 줄이 이름을 적은 임시 파일뿐이다.
- Windows에서는 프로세스가 죽은 뒤 잠금이 풀리는 데 시간이 걸릴 수 있다(미확인). 바로 다시 부르면 `source_busy`·`destination_busy`가 잠깐 날 수 있고
  오류 글이 잠시 뒤 다시 부르라고 안내한다.

### 6.11 중단 지점별 결과

강제 종료(`os._exit`, SIGKILL, 전원 손실)를 각 지점에 넣었을 때다. 모든 행에서 원본의 inventory digest는 같다(잠금 파일 제외).

| 지점 | 목적지에 남는 것 | 목적지 안의 새 kit 명령 | 옛 kit | 다시 부른 `migrate` |
|---|---|---|---|---|
| F1 1단계 뒤 | 빈 `.analytics/`, `migration.lock` | `migration_incomplete` | `no_project` | 새 전환 |
| F2 2단계 뒤 | ledger `start` | 같음 | 같음 | 이어 쓰기(계획부터) |
| F3 3단계 뒤 | + 원본에 잠금 파일(생겼다면, ledger에 적힘) | 같음 | 같음 | 같음 |
| F4 5단계 뒤 | + `plan.json` | 같음 | 같음 | 이어 쓰기 |
| F5 파일 복사 중 | + 임시 파일 하나 | 같음 | 같음 | 임시 파일을 지우고 이어 쓰기 |
| F6 교체 뒤 `copied` 전 | + 완전한 사본(ledger에 `copied` 없음) | 같음 | 같음 | 지금 원본과 해시가 같으면 인정 |
| F7 7단계 중 | 사본 전부 | 같음 | 같음 | 이어 쓰기(원본 재확인부터) |
| F8 8–10단계 | + 보고서(있거나 없음) | 같음 | 같음 | 이어 쓰기(검증부터) |
| F9 11단계 임시 파일 뒤 교체 전 | + 표식의 임시 파일(ledger에 적힘) | 같음 | 같음 | 임시 파일을 지우고 표식부터 |
| F10 11단계 뒤 | 커밋된 프로젝트, 신뢰 항목 없음 | `current`, aelix 자동 활성화 없음(status가 알림) | `no_project` | `already_migrated` + 신뢰 추가 |
| F11 12단계 뒤 | 완료 | `current` | `no_project` | `already_migrated` |

### 6.12 동시 쓰기, 잠금, 포트

| 상황 | 동작 |
|---|---|
| 옛 kit이 원본에서 run 중 | 3단계 `source_busy`(기다리지 않음) |
| 옛 kit의 `record`·맥락 동기화가 전환 중에 옴 | 4–7단계 동안 journal 잠금에서 기다린다. 옛 aelix의 프롬프트 직전 hook도 기다리므로 그동안 aelix가 멈춘 것처럼 보인다(13절 R1). 놓은 뒤의 기록은 사본에 없고 6.9절이 센다 |
| 잠금 없이 쓰는 옛 쓰기(telemetry·queries·캐시·inline 스크립트·사용자 편집) | 7단계 `source_changed`. 캐시·잠금 파일은 제외 목록이라 실패시키지 않는다 |
| 새 kit의 쓰기가 원본에 옴 | ③ 뒤 판정에서 `migration_required`. 원본에 쓰지 않는다 |
| 새 kit이 표식 전의 목적지에 옴 | `migration_incomplete` |
| 두 전환이 같은 목적지로 | 1단계 `destination_busy` |
| 두 전환이 같은 원본에서 다른 목적지로 | 두 번째가 3단계 `source_busy` |

포트 변경(ADR-0003 결정 1의 네 포트 안에서 메서드만 더함. 플랫폼마다 동작이 다른 것만):

- `FileLocks.try_exclusive(path)`: 잡으면 context manager, 다투면 `LockBusy`. 부모 디렉터리를 만들지 않는다. POSIX는 `flock(LOCK_EX | LOCK_NB)`
  (프로브: macOS에서 다른 프로세스가 kit의 잠금을 잡은 동안 `EAGAIN`(35), 놓은 뒤 획득), Windows는 `msvcrt.locking(LK_NBLCK, 1)` 한 번(지금
  `exclusive`와 같은 0번 바이트). 옛 kit의 `exclusive`와 같은 파일·같은 바이트 범위라 서로를 막는다.
- `AtomicFiles.flush(path)`·`AtomicFiles.flush_dir(path)`: POSIX는 파일 `fsync`(`F_FULLFSYNC`가 있으면 그것, 기능 확인)와 디렉터리 fd의 `fsync`,
  Windows는 파일 `fsync`이고 디렉터리는 할 일이 없다(디렉터리를 열 수 없음, 미확인). 오류를 숨기지 않는다.
- 세 구현(POSIX·Windows·시험 adapter)과 `tests/test_contracts.py`의 사례를 함께 더한다(②).

POSIX `flock`은 네트워크 파일 시스템의 클라이언트 사이에서 보장되지 않는다. 지금처럼 지원 범위 밖이고 보고서와 현재 계약 G에 적는다.

### 6.13 `.gitignore`·`.gitattributes`·작업 스크립트

- `.gitignore`: 사본 끝에 `# Added by analytics migrate <id>: the new names of the paths ignored above` 블록을 덧붙인다. 원본에 있는 템플릿의 옛 규칙
  (정확히 같은 줄)만 새 이름으로 옮겨 적고, 옛 형식에 없던 kit 관리 파일의 규칙은 늘 더한다. 원본에 없는 옛 규칙은 새 이름으로도 만들지 않는다
  (사용자가 그 파일을 커밋하기로 한 선택을 바꾸지 않는다). 이미 같은 줄이 있으면 더하지 않는다.

  | 원본의 줄 | 더하는 줄 |
  |---|---|
  | `.lab/cache/` | `.analytics/cache/` |
  | `.lab/lock` | `.analytics/lock` |
  | `.lab/adhoc/` | `.analytics/adhoc/` |
  | `report/` | `workspace/` |
  | `runs/*/charts/*.data.parquet` | `runs/*/visualizations/*.data.parquet` |
  | (늘) | `.analytics/run.lock`, `.analytics/workspace.lock`, `.analytics/migration.lock`, `.analytics/publications/`(공유 build 설계 R5) |

  전후 해시와 diff를 보고서의 `transformed`에 남긴다. `.gitignore`가 링크면 고치지 않고 제안만 한다(링크를 따라 쓰면 원본이나 밖의 파일을 고친다).
  원본에 없으면 만들지 않고 제안한다. ③의 새 템플릿(`template/gitignore`)도 같은 새 이름을 쓴다.
- `.gitattributes`: 만들지 않고 보고서가 템플릿 내용을 `proposals/gitattributes`로 제안한다(ADR-0007 결정 5).
- 작업 스크립트: 목적지 `scripts/`는 바이트 그대로다. AST로 옛 이름(`import labkit`·`from labkit`, `lab.note`·`lab.chart` 같은 #9 전 SDK 이름,
  `record("gap", …)`)을 찾으면 바꾼 사본을 `proposals/scripts/<경로>`에, 통합 diff를 `proposals/scripts.diff`에 둔다. 적용은 사람이 한다. 바꿀 수 없는
  것(동적 import 등)은 `reasons`에 `manual`로 적는다. run 스냅샷(`runs/<id>/code/`)은 건드리지 않는다(ADR-0005 결정 5·9).

### 6.14 전환 뒤의 원본과 신뢰 목록

- 원본은 `lab.json` 프로젝트로 남는다. ③의 kit은 읽기만 하고 옛 kit은 계속 쓸 수 있다. 그 기록은 목적지에 없으며 보고서의 `notes`와 6.9절의
  다시 부르기가 알린다. 원본의 그 뒤 변경을 목적지로 옮기는 기능은 #13에 없다. 새 목적지로 다시 전환하라고 안내한다.
- 신뢰 목록: 새 kit은 새 목록 `analytics-projects.json`만 쓴다. 옛 `lab-projects.json`은 읽기 전용으로 함께 보고, 거기에 있는 경로의 활성화(③에서는
  `lab.json` 프로젝트의 읽기)에만 쓴다. 옛 항목을 새 목록으로 옮기지 않는다. 사람이 고른 경로만 새 목록에 들어간다: `/analytics use`(형식과
  관계없이 그 경로), init, 전환의 목적지. 이전에 고르지 않은 경로를 자동으로 신뢰하지 않는다(ADR-0005 결정 5, 용어 점검 §5 9항).
- `AELIX_ANALYTICS_TRUST_FILE`이 있으면 그 파일 하나가 목록이다. 새 kit은 두 가족 어느 것의 `projects`도 읽고 쓸 때 `analytics.trust/1`로 쓴다
  (지금 kit은 이 파일의 `schema`를 보지 않는다).
- 신뢰 목록의 읽고-고치고-쓰기에 잠금이 없어 두 쓰기가 겹치면 하나가 사라질 수 있다. main에도 있는 한계다(13절 R8).

---

## 7. 옛 형식 프로젝트의 쓰기 차단 (③)

- 대상은 ADR-0005 결정 6의 목록 전부이고 11.3절의 진입점 행렬이 하나씩 확인한다. ①에서 모든 진입점이 `admit`을 부르므로 ③은 `WRITABLE`만 바뀐다.
- 일반 쓰기의 오류(영어, 사람과 모델에게 같은 글):

```text
code: migration_required
message: <root> uses the older record format (lab.json, lab.project/1). This kit reads it and does not write to it.
hint: Copy it into the current format with `analytics migrate --to <new empty folder>` run inside <root>
      (preview with --dry-run). The original stays as it is. In aelix, a person runs that in a terminal,
      then `/analytics use <new folder>`.
```

| 진입점 | ③ 뒤 `lab.json` 프로젝트에서 |
|---|---|
| CLI `record`·`run`·`import`·`render`·`demo` | `migration_required`, 바이트 변화 없음 |
| CLI `init` | 지금처럼 빈 디렉터리만, 새 형식으로. 위쪽 표식은 OQ9 |
| CLI `status`·`context`·`query`·`open` | 읽기만. 캐시·`queries.jsonl`·맥락 동기화는 건너뜀. 이미 있는 `report/index.html`은 열리지만 갱신되지 않는다고 알림 |
| SDK `save`·`visualize`·`record`(run 밖) | `migration_required`. run 안은 run이 시작 전에 거절되므로 생기지 않음 |
| 확장 `analytics_record`·`analytics_import`·`analytics_init`, `analytics_run`(inline 스크립트를 쓰기 **전에**, aelix 프로세스 안에서) | `migration_required` |
| 확장 `analytics_status`·`analytics_query`, hook(프롬프트 직전·도구 종료·`agent_end`) | 동기화·telemetry·렌더 예약을 건너뛰고 읽기만 |
| `/analytics record`·`render` / `use`·`status`·`open` | 오류 / 읽기와 활성화 |

- status JSON에 `project.format: "lab.project/1"`, `writable: false`, `writes_skipped`, `migration_hint`가 있고, 글과 맥락 블록의 머리에 한 줄
  "Older record format: read only here until a person migrates it (analytics migrate --to DEST)."를 둔다. 모델이 막힌 쓰기를 되풀이하지 않고 사람에게
  알리게 하기 위해서다.
- 두 표식은 모든 작업에서 `marker_conflict`다. 오류 `details`는 두 표식의 내용·해시, `analytics.json`의 `migration` 필드가 가리키는 보고서, "둘 중 하나는
  이 디렉터리의 것이 아니다. 확인한 뒤 다른 하나를 옮겨라"를 담는다. 자동으로 고르지 않는다.

---

## 8. 같은 상태 변경의 충돌과 중단 (완료 조건 4)

| 경우 | writer의 동작 | 보이는 것 | 시험 |
|---|---|---|---|
| 철회·복원·재개·승격(`record_update`)의 기대 상태가 지금과 다름, 표에 없는 전이 | journal 잠금 안 재생, `state_conflict`, 아무것도 쓰지 않음 | 지금 상태, 허용 전이, 그 상태를 만든 이벤트 id와 그 `request_id` | 예제 `normal`·`conflict`의 `writes` |
| 대상이 없거나 여럿 | `state_conflict`(`target_missing`·`target_ambiguous`) | 후보 목록 | T13 |
| 두 요청이 같은 기록을 동시에 철회 | 잠금이 순서를 정하고 뒤의 요청은 `state_conflict` | 앞 요청의 이벤트 id와 `request_id` | 두 프로세스 경주 |
| 같은 기록의 두 정정(`supersedes`) | 쓴다(ADR-0006: 둘 이상이면 `conflict`) | 응답의 `state_after: conflict`, status·Workspace의 `conflict` | 예제 `conflict` |
| 잠금 밖에서 쓴 어긋난 갱신(더 새로운 writer, 손으로 쓴 줄) | 재생에서 적용하지 않음 | `mismatched_updates`(#12) | 예제 `conflict` |
| `record_update`를 쓴 뒤 응답 전에 중단하고 같은 요청을 다시 보냄 | 두 번째는 `state_conflict` | "그 상태를 만든 이벤트"의 `request_id`가 같아 호출자가 자기 쓰기임을 안다. 중복 방지 보장은 아니다(ADR-0006 8절) | T13 |
| 줄을 쓰다 중단 | 다음 writer가 LF를 먼저 씀 | 끊긴 줄은 `damaged`, 새 이벤트는 따로 읽힘. 끊긴 쓰기는 id를 돌려준 적이 없다 | 예제 `interrupted`의 `event_append`, 쓰기 중 kill |
| `/1` writer가 붙여 쓴 줄 뒤의 `/2` 쓰기 | 복구한 기록의 seq 다음 값 | 같은 id 두 줄은 `ambiguous` | 예제 `mixed-version` + 쓰기 |
| run의 정본 뒤 완료 이벤트 전 중단 | — | `recording_status=incomplete(no_completion_event)` | 완료 이벤트 직전 kill |
| 오래된 writer가 잠금 안에서 `writer_too_old`를 받음 | 완료 이벤트를 쓰지 않고 오류 | 정본만 있는 `incomplete`와 오류 응답 | T10 |
| 전환의 중단·충돌 | 6.10–6.12절 | `migration_incomplete`, `destination_busy`, `source_busy`, `source_changed`, `destination_mismatch` | T5–T7 |

---

## 9. 미뤄 받은 이름과 PR

ADR-0005 결정 7은 이름 변경·파일 이동·기록 의미 변경을 한 PR에 섞지 않고 저장 이름의 개명만 ③에 넣는다. 그래서 내부 코드 이름은 ③에 넣을 수
없다. 전체 용어 점검 §5의 끝("단순 코드명·언어 전환은 별도 PR로 검토할 수 있게 나눈다")대로 동작을 바꾸지 않는 PR ⓪을 ① 전에 둔다(OQ6).

| 이름 | 종류 | 목표 | PR | 비고 |
|---|---|---|---|---|
| `LabError` | 코드 | `AnalyticsError` | ⓪ | JSON 오류 형태 그대로. ①에서 writer로 옮김 |
| `Project.lab` | 코드 | `state_dir` | ⓪ 이름, ③ 값 `.analytics` | ③이 읽기 전용 `legacy_state_dir`(`.lab`)를 더함 |
| `Project.report` | 코드 | `workspace_dir` | ⓪ 이름, ③ 값 `workspace/` | |
| 모듈 `report.py` | 코드 | `workspace.py` | ⓪ | 계층표 `MODULES`·`ALLOWLIST`의 키와 ARCHITECTURE §11 표. `WorkspaceRenderer` 클래스는 만들지 않음 |
| `schedule_report` | 코드 | `schedule_workspace_render` | ⓪ | |
| CLI JSON 키 `report` | 공개 JSON | `workspace` | ⓪ | 용어집 §5 "겹친 필드는 `workspace`로". #9-1 인계 댓글이 "먼저 그 응답을 바꾸는 쪽"에 맡김. `KIT_VERSION`·CHANGELOG. 값은 ③까지 `report/…` |
| `report.js`·`report.css` | 자원 | `workspace.js`·`workspace.css` | ⓪ | 렌더가 옛 자원 이름을 지움 |
| `localStorage` `lab-live`·`lab-scroll:` | 브라우저 저장 | `analytics-live`·`analytics-scroll:` | ⓪ | 새 키가 없으면 옛 키 값을 한 번 읽어 새 키에 씀. 옛 키는 지우지 않음(옛 kit의 Workspace가 읽음) |
| `window.__LAB_BUILD__` | 코드 | `window.__ANALYTICS_BUILD__` | ⓪ | `build.js`와 `tests/test_line_endings.py` |
| plotly 템플릿 `lab` | 코드 | `analytics`(`plotly_white+analytics`) | ⓪(조건부) | 템플릿 이름이 저장하는 그림 JSON에 들어가는지 확인하지 않았다. ⓪의 시험이 기존 바이트를 고정하고, 들어가면 저장 값이므로 ③으로 옮긴다 |
| `__lab_wrapped__` | 코드 | `__analytics_wrapped__` | ⓪ | |
| `LAB_API` | 시험 상수 | `ANALYTICS_API` | ⓪ | 공개 설정으로 올리지 않음(용어집 §3) |
| `core.NOTE_KINDS` | 코드 | `writer.RECORD_KINDS` | ③ | 개명이 아니라 새 writer의 목록(2.8절) |
| `core.add_note` | 코드 | `Writer.record`(진입점은 `core.record` 얇은 함수) | ③ | 새 writer가 대신함 |
| kind `gap` | 저장·입력 | `workflow_feedback` | ③ | 입력 `gap`은 오류(2.8절). CLI choices, 도구 enum, HELP, Workspace 빈 상태 문구 |
| `lab.json`, `.lab/`, `report/`, `data/raw/`, `runs/<id>/charts/`, `lab.*/1`, `lab-projects.json`·`lab.trust/1` | 저장 | 2.3절 | ③ | 새 쓰기만 새 이름. 복사한 옛 파일은 그대로 |
| `via` 값 | 저장 | 2.6절(OQ5) | ③ | 새 철자는 ③에서 용어집에 등록 |
| `kit_*` 필드 | 저장 | 그대로 | — | `project_2`에 `kit_version`이 이미 있다. OQ5가 바꾸기로 하면 ③ |

목표 이름이 용어집에 없는 것(`AnalyticsError`·`state_dir`·`workspace_dir` 등 코드 이름, `RECORD_KINDS`, `__ANALYTICS_BUILD__`, 템플릿 이름,
`__analytics_wrapped__`)은 그 이름을 처음 쓰는 PR이 용어집 §2에 먼저 적는다. 공개·저장 이름(명령 `migrate`, 오류 코드, `means`·`actor_basis`, 가족 이름,
표식의 `migration` 필드)도 같다.

---

## 10. PR 분할

| PR | 내용 | 불변식(ADR-0005 결정 7) | `KIT_VERSION` | 리뷰(운영 정책) | 측정 기록 |
|---|---|---|---|---|---|
| D 설계 | 이 문서. ARCHITECTURE·ADR-0005의 링크 | 코드 없음 | 그대로 | 결정만 담은 기록 형식 PR: 이 리뷰가 설계 리뷰(독립 1·Codex 1) | 없음 |
| ⓪ 내부 이름 | 9절의 ⓪ 행(OQ6). 동작·저장 그대로 | 저장 바이트 그대로(`STORED` 시험 통과) | 올림(공개 JSON 키) | 일반 PR(독립 1·Codex 1) | 없음 |
| ① 판정·읽기 | `writer.py` 등록(판정·게이트·헬퍼·`Origin`·오류 클래스), 계층 수준 변경, 가장 가까운 표식 탐색, 모든 진입점의 `admit`, `marker_conflict`·`marker_invalid`, `analytics.json` 디렉터리의 모든 쓰기 거절·부수 쓰기 건너뜀, `/2` 이벤트 표시, `analytics.run/2`·`analytics.dataset/1` 정의와 읽기, `Origin` 배관, (OQ8의 답에 따라) `/1` 덧붙이기 수정 | `analytics.json` 디렉터리에 아무것도 써지지 않음(T8) | 올림(새 거절) | 구현 리뷰 독립 1·Codex 1, 수정 diff 확인 1 | 있음(새 가족 정의, 진입점 행렬) |
| ② 전환 엔진 | `migration.py` 등록, 포트 메서드 셋과 계약 시험, 계획·ledger·매핑·보고서와 schema, `migration_incomplete`, 매핑 읽기, 고장 주입 시험. 명령·도구로 열지 않음 | 엔진은 시험에서만 `analytics.json`을 씀. ②의 kit은 그 목적지에 쓰지 않음(①의 게이트) | 그대로 | 같음 | 있음(전환 정확성·규모·중단 행렬) |
| ③ 새 writer·정책 | `Writer`, `WRITABLE` 뒤집기(`lab.json` → `migration_required`), 저장 이름 개명, 새 형식 init·demo, `analytics migrate`, 신뢰 목록, `.gitignore`·템플릿, `min_writer_version`, 확장의 거절 글·`AELIX_ANALYTICS_MEANS`, 현재 계약 전반, `STORED` 시험 뒤집기 | `lab.json` 디렉터리에 쓰지 않음, `analytics.json` 디렉터리에 옛 형식이 써지지 않음 | 올림, `min_writer_version` = 이 값 | 같음 | 있음(writer 지연, 명령 E2E, 전환 재측정) |

- 게이트를 ①에 먼저 넣는 이유: 위험한 부분(모든 진입점의 첫 부수 효과 전 판정)을 형식 전환과 떼어 먼저 시험한다. ③은 정책을 뒤집는다.
- ②가 병합된 뒤 ③까지는 전환을 명령으로 쓸 수 없다(ADR-0005 결과와 비용).
- 측정 기록이 있는 PR에서 측정한 커밋 뒤 문서가 아닌 변경이 생기면 병합 전에 다시 잰다(운영 정책 검증 증거).
- ③이 크다(13절 R6). 커밋을 writer → 진입점 → 저장 이름 → 명령 → 문서 순으로 나눠 리뷰한다. ③을 다시 쪼개면 두 writer가 잠시 공존하고 결정 7의
  목록이 바뀌므로 그때는 소유자 확인을 받는다.

PR별 주요 파일:

- ①: `writer.py`(새), `core.py`(탐색, `Project.markers`, `config()`의 판정, 쓰기 함수의 `admit`, `read_events`의 `/1`·`/2`, `list_runs`·`list_datasets`,
  status의 형식·`writes_skipped`), `relations.py`(`KNOWN`, `data/imported`·`visualizations[]`, `READER_FORMAT`), `runner.py`·`data.py`·`capture.py`·
  `__init__.py`·`__main__.py`·`report.py`·확장·`cli.py`·`bin/analytics`(게이트, `Origin` 배관, 탐색), 기록 schema(`analytics.run/2`·`analytics.dataset/1`,
  `event_2`의 `means`·`actor_basis`), 현재 계약(A 확장의 거절, B 탐색·두 표식, C `/2` 표시, D 오류, F 프로세스 경계), 용어집(새 오류·필드),
  ARCHITECTURE §11, ADR-0001 적용 현황, CHANGELOG.
- ②: `migration.py`(새), `_platform.py`(포트 메서드), `relations.py`·`core.py`(매핑 읽기, `KNOWN`), 기록 schema(`analytics.migration/1`, 표식 `migration`),
  `tests/legacy_records.py` 확장, 현재 계약("전환 엔진은 있으나 명령으로 열지 않음", 매핑 읽기), 용어집(전환 오류 이름).
- ③: `writer.py`(`Writer`), `core.py`(init, 새 경로, `sync_context`, `record`, 신뢰 목록, 안내), `runner.py`·`capture.py`(`analytics.run/2`, `visualizations/`,
  해시, `via`), `data.py`(`data/imported`, `analytics.dataset/1`), `workspace.py`(`workspace/`, `.analytics/workspace.lock`), `__main__.py`(`migrate`, kind
  목록, 응답의 `request_id`·`bindings`), `cli.py`·`bin/analytics`(`migrate`, 기본 actor), 확장, `demo.py`, `template/gitignore`, `tools/eval_fixtures.py`
  (OQ7), `tools/usage_report.py`, 현재 계약 전반, README, CHANGELOG, 기록 schema(`analytics.trust/1`), 예제 README.

---

## 11. 시험과 fixture

### 11.1 공통 도구

- `tree_digest(root, exclude)`: 정규 파일의 상대 경로·바이트 수·sha256, 디렉터리·링크(링크 글자) 목록의 정렬된 digest. 전환 코드가 아닌 시험의
  걷기다. 모든 전환·차단 시험이 원본(과 필요하면 목적지)에 전후로 부른다. 잠금 파일은 따로 비교한다(만들어졌는지).
- 고장 주입: `migration.migrate(…, on_step=None)`의 비공개 인자. 시험은 자식 프로세스(POSIX·Windows 모두 `subprocess`로 시작한 작은 driver)에서
  `on_step`이 지정한 단계에서 `os._exit(9)`를 불러 F1–F11을 재현한다. `os._exit`은 `finally`를 실행하지 않아 실제 강제 종료에 가깝다. 같은 콜백이
  그 지점에서 원본에 쓰게 해 동시 쓰기를 만든다.
- 옛 kit 흉내: `tests/legacy_records.py`의 `LegacyProject`로 `/1` 원본을 만들고, 옛 쓰기는 같은 잠금(`FileLocks.exclusive(".lab/lock")`)을 잡고
  덧붙이는 작은 함수로 흉내 낸다. 실제 옛 kit 바이트가 필요한 시험은 OQ7을 따른다.

### 11.2 보장별 시험

| 시험 | 내용 | 보장 | PR |
|---|---|---|---|
| T1 원본 보존 | 모든 전환 시험(성공·거절·중단·실패·`--dry-run`)에서 원본 `tree_digest` 전후 같음. 원본: CRLF journal, 끊긴 마지막 줄, P7처럼 붙여 쓴 줄, `/2` 줄이 섞인 journal, 모르는 가족 줄, 한국어·NFD 파일 이름, 빈 파일, 64 MiB 넘는 파일, 읽기 전용 파일, 링크(OQ3), `.lab/`이 없는 원본(디렉터리가 생기지 않음) | S1 | ② |
| T2 목적지 규칙 | 원본 안·원본을 품은 위·같은 위치의 다른 철자(대소문자, NFD, 링크, junction), 비지 않음, 위쪽 `lab.json`·`analytics.json`, 공간 부족(주입한 빈 공간 값), 이름 충돌, 중첩 표식(OQ10) → 각 오류이고 목적지·원본 바이트 변화 없음 | S2 | ② |
| T3 커밋 지점 | 표식 전 모든 지점에서 목적지 안 새 kit 명령은 `migration_incomplete`, 옛 탐색(지금 `find_project`의 사본)은 `no_project`. 표식은 검증 뒤에만, 신뢰 항목은 표식 뒤에만 있음 | S2 | ②·③ |
| T4 검증 가능성 | 보고서 schema, 사본 해시 = 보고서, 표식의 `report_sha256` = 보고서 바이트, 위치 대응이 매핑과 같고 `.gitignore`만 변환되며 전후 해시·diff가 맞음, 다시 부르기의 대조가 고친 사본 하나를 잡음 | S3·S4 | ② |
| T5 복사 중 원본 변경 | 지점마다 telemetry 덧붙이기·파일 추가·삭제·스크립트 편집 → `source_changed`(경로 포함), 표식 없음. journal 덧붙이기는 잠금에서 기다렸다가 놓은 뒤 쓰이고 사본에 없으며 다시 부르기가 그 줄을 셈 | S5 | ② |
| T6 중단과 이어 쓰기 | F1–F11 각각 6.11절 표대로 분류. 이어 쓴 결과의 사본 `tree_digest`가 중단 없는 전환과 같음(`.analytics/migration/` 제외). 계획에 없는 파일, 해시가 다른 사본, 중단 사이 이미 복사한 파일의 원본 변경 → 각 거절이고 아무것도 지우지 않음. 중단 사이 아직 복사하지 않은 파일의 원본 변경 → 이어 쓰고 결과가 새 원본과 같음. 목적지에 생긴 `.venv/` → 무시 | S6·S10 | ② |
| T7 동시성 | 두 프로세스가 같은 목적지로 → 하나만 성공, 다른 하나 `destination_busy`. 원본 run 잠금을 다른 프로세스가 잡은 동안 → 즉시 `source_busy` | S7 | ② |
| T8 진입점 행렬 | 11.3절 | S8·S9 | ①·③ |
| T9 혼합 journal | `mixed-version` 예제 + `/1` CRLF·`/2` LF·끊긴 줄·복구한 줄 → reader 판정이 예제 기대값과 같고, `read_events`·맥락 블록·status·Workspace가 `/2` 줄을 보임(P6 회귀), writer 덧붙이기는 LF를 먼저 쓰고 seq = 최대 + 1 | S8·S10 | ①·③ |
| T10 다시 읽기 | 쓰기 작업 사이에 표식을 `lab.json`·두 표식·더 높은 `min_writer_version`으로 바꿈 → 다음 작업이 거절. journal 잠금을 잡은 writer와 표식을 올리는 writer가 겹치면 낮은 쪽이 `writer_too_old`이고 run은 `incomplete` | S8 | ③ |
| T11 기록 비교 | 전환 전 원본과 전환 뒤 목적지를 `relations.read`로 읽은 결과(참조 판정·기록 상태·run·생산자·`unsupported`·`damaged`)가 같음. 루트에 기대는 값은 매핑으로 정규화하고, 옛 절대 경로만 `inferred`로 더 풀림. #41 사례(`correction`·`withdrawal`·`unpreserved_data`·`same_chart_id`·`reference_cycle`·`long_korean`) 포함(Q7) | S1·S3 | ②·③ |
| T12 표식 판정 단위 | 4.3절 표의 모든 행, 가장 가까운 표식에서 멈추는 탐색(위 `lab.json`·아래 `analytics.json`과 그 반대, 표식 없는 전환 목적지) | S8 | ①·② |
| T13 상태 변경 | 예제 `writes`의 `record_update`(`state_conflict`)와 `event_append`(끊긴 줄 뒤 LF, `writer_too_old`)를 writer가 그대로 냄. 두 정정 → `state_after: conflict`. 같은 `record_update`를 두 프로세스가 동시에 → 하나만 쓰이고 다른 하나 `state_conflict`. 쓰기 도중 `os._exit` → `damaged`, 상태 그대로 | S10 | ③ |
| T14 작성 출처 | 진입점마다 `actor`·`means`·`actor_basis`·`session`이 4.2절 표대로. 환경 변수 없는 CLI(OQ1), 세션 없는 사람 기록이 모든 읽기에 나옴(Q17) | S11 | ③ |
| T15 포트 계약 | `try_exclusive`가 다른 프로세스의 `exclusive`와 서로 막고 부모 디렉터리를 만들지 않음(세 구현), `flush`·`flush_dir`가 오류를 숨기지 않음 | S7 | ② |
| T16 줄 끝 | writer 헬퍼가 Windows에서도 LF, 전환 사본은 CRLF 그대로, 새 모듈이 쓰기 AST 검사 범위 안 | S12 | ①·② |
| T17 계층 | `writer`·`migration` 등록, 수준 표가 ARCHITECTURE와 같음, 호스트 쪽 로드 목록에 `writer`, `migration`은 aelix 프로세스에 로드되지 않음 | 조건 5 | ①·② |
| T18 uv | 런처의 `migrate`가 원본의 `.venv/`·`uv.lock`을 만들거나 바꾸지 않음(목록·해시), 커밋 뒤 sync가 목적지 `uv.lock`을 다시 쓰지 않음(맞는 경우·맞지 않는 경우) | S1 | ③ |

### 11.3 진입점 행렬 (T8)

- 행: ADR-0005 결정 6의 대상과 지금의 모든 쓰기 진입점 — kit `run`·`import`·`record`·`render`·`demo`·`query`·`status`·`context`, SDK의 run 밖
  `record`·`save`·`visualize`와 run 안 같은 셋, 확장의 `analytics_status`·`analytics_record`·`analytics_run`(inline 스크립트 포함)·`analytics_import`·
  `analytics_query`·`analytics_init`, hook 셋, `/analytics record`·`render`·`open`·`use`·`status`, 런처·`bin/analytics`의 각 명령. `init`·`migrate`는
  목적지 규칙으로 따로.
- 열: 표식 없음, `lab.project/1`, 깨진 `lab.json`, `lab.project/9`, `analytics.project/2`(최소 버전 충족·미충족), `analytics.project/3`, 깨진
  `analytics.json`, 두 표식, 표식 없는 전환 목적지.
- 확인: 기대 결과(쓰기·오류 코드·건너뜀)와, 거절·건너뜀이면 프로젝트 트리의 스냅샷(경로·크기·mtime·해시, 잠금 파일 생성 포함, uv 환경 `.venv/`·`uv.lock`은
  따로 기록)이 호출 전후로 같다. 한 칸이라도 쓰면 실패한다. 표식을 호출 도중에 바꾸는 경우(run 안 SDK 쓰기)도 한 행.
- ①에서 만들고 ③에서 기대값을 바꾼다. 행렬은 진입점을 상수로 두며 새 진입점을 스스로 찾지 못한다. 그래서 "새 쓰기 진입점은 이 행렬에 행을
  더한다"를 기여 기준(CONTRIBUTING의 지킬 구조)에 ①에서 적는다.

### 11.4 Windows와 경로

Windows CI 레그에서 T1–T8·T15·T16을 돌리고 다음을 더한다: 대소문자만 다른 두 원본 이름(만들 수 없으면 Linux 레그에서 만든 tar를 풀어 원본으로),
예약 장치명·끝의 `.`·`:`가 든 이름(Linux 레그에서 만든 원본 → `destination_write_failed`, 표식 없음), 260자를 넘는 목적지 경로(LongPathsEnabled 0·1,
`tests/test_windows_long_paths.py`의 방식), 밖을 가리키는 junction `data/`, 다른 프로세스가 공유 없이 연 원본 파일(`source_unreadable`, 원본 그대로),
읽기 전용 속성, 목적지의 새 파일을 잠깐 여는 프로세스(공유 위반 → 포트의 재시도), 다른 드라이브의 목적지. 실행하지 않은 조합은 측정 기록의
`not_run`에 둔다.

### 11.5 규모 (#41 M)

- M fixture(run 300, journal 10,000줄, 데이터셋 20, 원본 약 217 MB)는 ③ 전 kit 커밋으로 `tools/eval_fixtures.py scale --scale M`을 돌려 만들고 그 커밋을
  측정 기록에 적는다(OQ7). 기본 CI는 S만, M은 환경 변수로 여는 시험과 측정 기록에서 돈다.
- 그 원본을 ②의 엔진(②)과 ③의 명령(③)으로 전환한다: 원본 digest 전후 같음, 다시 부르기의 대조 통과, T11의 기록 비교.
- writer: 10,000줄 journal에서 `record` 20회의 p50·p95, `record_update`(재생 포함), 이름 참조 결박이 있는 `record`. 크면 seq 색인을 후속(#15의 인덱스와 함께)으로 둔다.

---

## 12. 측정 기록 (md+JSON)

운영 정책의 검증 증거: 기록 형식이나 프로젝트 전환을 바꾸는 PR은 `docs/verification/`에 md+JSON을 남기고, 측정한 커밋 뒤 비문서 변경이 생기면
병합 전에 다시 잰다. 형식은 [#36의 측정 기록](../verification/2026-10-06-issue-36-path-identity.md)의 최상위 키(`record_kind, observed_at, issue,
pull_request, base_revision, measured_tree, workflow, runs, legs, developer_machine, before, not_run`)를 따른다.

| 기록 | PR | 더하는 키 |
|---|---|---|
| `<날짜>-issue-13-gate.{md,json}` | ① | `entry_points`(T8의 진입점 × 디렉터리 상태: 기대·실제 결과, 트리 같음), `reader`(예제 여섯과 새 가족 예제의 판정), `gate_cost`(쓰기 한 번에 더해진 시간) |
| `<날짜>-issue-13-migration.{md,json}` | ② | `fixtures`(S·M의 생성 커밋·명령·seed, 파일 수·바이트, 원본 `tree_digest`), `phases`(계획·복사·재확인·검증·보고서·flush·표식의 ms, 원본 잠금을 쥔 시간, 최대 RSS), `sizes`(복사·제외 바이트, `report.json`·`plan.json`·ledger 크기), `preservation`(독립 걷기로 잰 원본 digest 전후), `fault_matrix`(F1–F11 × 레그), `concurrency`(T5·T7), `reader_comparison`(T11) |
| `<날짜>-issue-13-writer.{md,json}` | ③ | `writer_latency`(11.5절), `entry_points`(③의 기대값), 명령 E2E(런처·`bin/analytics`, `--dry-run`, 이어 쓰기), `migration`(②의 항목 재측정), `uv`(T18) |

시간은 앞뒤 커밋을 번갈아 두 번 재고 최솟값을 쓴다(PR #76의 방식). 실행하지 않은 레그·조합(Windows 클라이언트 판, 표준 사용자, 네트워크 파일
시스템, 전원 손실, 실제 aelix 모델 세션)은 `not_run`에 둔다. 격리는 신뢰 목록·uv를 실행 폴더 안으로 돌리고 `AELIX_LAB_*`·`LAB_*`가 없으며 `~/.aelix`를
건드리지 않았다는 것을 적는다.

---

## 13. 위험

| 번호 | 위험 | 대응 |
|---|---|---|
| R1 | 4–7단계 동안 원본 journal 잠금을 쥐어 옛 aelix의 hook·도구가 그만큼 멈춘다 | 7단계에서 놓고 검증·보고서는 밖에서. `--dry-run`이 바이트를 보임. 측정 기록의 잠금 시간. 옛 세션을 닫으라고 안내 |
| R2 | 옛 aelix 세션의 telemetry처럼 잠금 없는 쓰기가 있으면 전환이 되풀이해 `source_changed`로 끝난다 | 오류가 바뀐 경로와 흔한 원인(열린 옛 세션)을 말함. ③의 확장은 `lab.json` 프로젝트에 telemetry를 쓰지 않음. ADR-0005가 요구한 실패다 |
| R3 | writer가 쓸 때마다 journal 전체를 파싱·재생해 큰 journal에서 느려진다 | 11.5절 측정, 필요하면 후속 색인 |
| R4 | Windows: 새 파일을 잡는 백신·색인기, 디렉터리 flush 없음, 긴 경로, 예약 이름, 링크 권한, 늦게 풀리는 잠금 | 포트의 재시도, 경고·`destination_write_failed`, 다시 부르기의 대조, Windows CI. 실행하지 않은 것은 `not_run` |
| R5 | 복사라서 디스크를 두 배 쓰고, 밖을 가리키는 큰 데이터도 복사한다 | 사전 공간 검사, 제외 목록(OQ4), 링크 처리(OQ3). hardlink 금지는 ADR 그대로 |
| R6 | ③이 크다(저장 이름 + writer + 명령) | ⓪·①·②가 개명·게이트 배선·`Origin` 배관·엔진을 먼저 가져감. 커밋 단위 리뷰 |
| R7 | ③ 병합 즉시 `lab.json` 프로젝트의 기록·렌더가 막힌다. 링크·중첩 표식·이름 충돌로 전환할 수 없는 프로젝트는 읽기 전용에 갇힌다 | OQ3·OQ4·OQ10을 ③ 전에 정함. ③ 전에 소유자의 실제 프로젝트를 `--dry-run`으로 미리 봄(쓰기 없음) |
| R8 | 신뢰 목록에 잠금이 없어 두 쓰기가 겹치면 하나가 사라진다 | main에도 있는 한계. 전환은 응답과 다시 부르기로 신뢰 누락을 알리고 채움 |
| R9 | OQ1의 근거(aelix bash 도구가 확장이 정한 환경을 물려받는지)를 확인하지 않았다 | 권장안은 추정 없이 `actor_basis`를 기록. ③ 전 프로브 |
| R10 | 전환 뒤 사용자가 목적지를 옛 프로젝트 아래로 옮기면 ① 전 kit이 그 아래에서 위쪽 프로젝트에 쓴다 | 막을 수 없음. ③의 status가 위쪽 표식을 경고 |
| R11 | 누군가 전환을 `uv run --project`로 배선하면 원본의 `.venv/`·`uv.lock`이 바뀐다 | T18이 막음. `.venv/`가 원본 비교에서 빠지므로(OQ4) 이 시험이 따로 있어야 한다 |
| R12 | `.git/`을 복사하면 같은 원격을 가진 저장소가 둘 생긴다 | 보고서의 안내, OQ4 |
| R13 | `data/raw`와 `data/imported`에 같은 이름 | `data/imported`가 view 이름을 먼저 받고 status가 경고(#36 항목 6과 같은 방식) |
| R14 | 입력 `gap`을 쓰던 스크립트·습관의 실패 | 분명한 오류와 안내, 전환의 스크립트 제안이 `gap`을 바꿈 |
| R15 | plotly 템플릿 개명이 새 그림 JSON의 바이트를 바꿀 수 있다(미확인) | ⓪의 시험이 기존 기록 바이트만 고정하고, 바뀌면 그 항목을 ③으로 옮김 |
| R16 | ③ 뒤 `/1` fixture 생성이 막혀 #15·#41의 측정이 밀린다 | OQ7 |
| R17 | ③ 전까지 `/1` writer의 #49 결함이 전환할 원본에 손상을 더한다 | OQ8 |

---

## 14. 범위 밖

- 원래 경로에 두는 단계(전환한 프로젝트를 원본 자리로 옮김). 필요하면 후속 Issue다(2.2절).
- 전환 뒤 원본에 생긴 기록을 목적지로 옮기는 증분 동기화. 새 목적지로 다시 전환한다.
- 전환의 모델 도구와 `/analytics migrate`.
- run·일반 기록 쓰기의 멱등성(ADR-0006 8절, 베타 뒤). Report 저장(#19), 질의 인덱스(#15), 지표·초점의 입력(#14), 재사용 제안(#17), 게시(#20·#21).
- 새 프로젝트가 아닌 곳에 `.gitattributes`를 만드는 일(제안만, ADR-0007 결정 5).
- 일반 쓰기의 fsync와 여러 파일에 걸친 트랜잭션(현재 계약 G).
- 네트워크 파일 시스템의 동시 쓰기.
- OQ5 권장안에서 바꾸지 않는 필드(`status`·`reproduction`·`verdict`·`kit_*`).
- 브라우저의 옛 `localStorage` 키 정리.

---

## 15. 설계 리뷰가 확정할 선택

ADR이 #13 설계에 맡겼거나 입력이 정하지 않았지만 제품 결정은 아니라고 본 것이다. 설계 리뷰가 받아들이거나 바꾼다.

| 번호 | 선택 | 절 |
|---|---|---|
| D1 | 계층: `writer`(수준 1)·`migration`(수준 3) 새 계층, `core` 이상 수준 +1, `KIT_VERSION`은 core에 남김 | 3절 |
| D2 | 가족 버전: `analytics.run/2`, `analytics.dataset/1`, `analytics.trust/1`, `analytics.migration/1` | 2.2·2.6–2.7절 |
| D3 | 실행 수단 필드 `means`와 다섯 값, 내부 변수 `AELIX_ANALYTICS_MEANS` | 2.5·4.2절 |
| D4 | 모든 Visualization 형식과 첨부 데이터의 해시 | 2.6절 |
| D5 | 명령 `analytics migrate --to DEST [--dry-run]`, 원본은 지금 프로젝트, `--no-project` 실행, 도구·aelix 명령 없음 | 6.1절 |
| D6 | 오류 코드 목록과 `project_too_new`의 뜻 넓힘 | 4.11절 |
| D7 | `.lab/` 이력·로그를 `.analytics/` 같은 이름으로, 목적지에 `.lab/` 없음. 대안: `.lab/`을 같은 경로에 보관(예제 `mixed-version`처럼) | 2.2·6.5절 |
| D8 | 원래 경로 단계를 두지 않음 | 2.2절 |
| D9 | 내구 쓰기는 전환의 커밋 전에만, 포트 메서드 `flush`·`flush_dir` | 6.4·6.12절 |
| D10 | 잠금: `try_exclusive`, 순서 `run.lock` → `lock`, 7단계에서 놓음, `.lab/`이 없으면 잠금 없이 | 6.3·6.12절 |
| D11 | 이어 쓰기: 이미 복사한 파일이 지금 원본과 같을 때 남은 계획을 다시 세움 | 6.10절 |
| D12 | 전환 중 journal에 쓰지 않음. 출처는 표식의 `migration`과 보고서, status가 보임 | 2.4·6.3절 |
| D13 | 탐색: 가장 가까운 표식(또는 표식 없는 전환 기록)에서 멈춤, 두 표식이면 읽기도 거절, 깨진 `lab.json`은 `legacy` | 4.3절 |
| D14 | `.gitignore` 덧붙임 규칙, `.gitattributes`·스크립트는 제안만 | 6.13절 |
| D15 | 신뢰 목록: 새 목록만 씀, 옛 목록은 읽기 전용 | 6.14절 |
| D16 | 입력 `gap`은 별칭 없이 오류, 기본 relation `cites` | 2.8·4.7절 |
| D17 | PR별 `KIT_VERSION`: ⓪·①·③ 올림, ② 그대로 | 10절 |
| D18 | 측정 기록을 ①에도 둠(새 가족 정의를 새 기록 종류로 봄) | 12절 |

---

## 16. 소유자 결정이 필요한 것

입력이 정하지 않은 제품 결정이다. 각 항목에 질문, 권장 답, 답에 따라 바뀌는 것을 적는다. 이미 받은 승인(ADR-0005·0006, #3·#4·#9·#12·#36의 결정)을
다시 묻지 않는다.

**OQ1 작성 주체의 기본값과 에이전트가 부른 CLI.** 런처·`bin/analytics`는 `AELIX_ANALYTICS_ACTOR`가 없으면 `human`을 채워 kit을 부른다. 그래서 에이전트가
bash 도구로 부른 CLI가 사람 작성으로 남는다. 연속성 설계와 Q17-b는 이를 막으라 하고, ADR-0008 결정 3은 #13이 "에이전트 요청으로 기록한 CLI 호출"을
구별한다고 전제한다. kit은 누가 불렀는지 알 수 없고, aelix의 bash 도구가 확장이 정한 환경을 물려받는지는 확인하지 않았다.
- 권장: (1) 기본값 `human`은 두되 진입점·사용자가 밝히지 않았으면 `actor_basis: inferred`를 남긴다. 런처·`bin/analytics`는 기본값을 채워 넘기지 않는다.
  (2) 확장이 aelix bash 도구의 환경에 `AELIX_ANALYTICS_ACTOR=agent`와 세션을 줄 수 있으면 ③에서 넣는다(먼저 프로브). 줄 수 없으면 그 경로는 한계로 적는다.
  (3) run 밖 SDK의 `actor="script"`는 `human`(밝히지 않았으면 `inferred`)과 `means="sdk"`로 바꾼다. TTY로 사람을 추정하지 않는다.
- 바뀌는 것: `/2` 이벤트·`analytics.run/2`의 선택 필드 하나, 런처의 기본값 주입 제거, Q17-b 판정의 근거, #17과 SKILL-PROPOSAL-POLICY의 "사람으로 기록된
  것만 센다"가 `inferred`를 셀지(#17이 이 필드를 보고 정함). 대안 "기본값 `unknown`"은 Q17-a의 사람 CLI 기록을 모두 미확인으로 만든다.

**OQ2 본문 상한.** ADR-0006 10절은 거절과 전체 보존 가운데 #11의 측정으로 #13에서 정하라 했으나 #11은 이 선택을 남기지 않았다.
- 권장: 조용히 자르지 않고 UTF-8 32 KiB까지 전부 보존한다. 넘으면 아무것도 쓰지 않고 `text_too_long`(원문 길이, 상한, 파일이나 Report로 저장하라는
  안내)으로 거절한다. 화면·맥락은 #11의 생략 표시를 그대로 쓴다. 근거는 aelix read 도구가 50 KiB를 넘는 한 줄을 읽지 않는다는 사실(현재 계약 A)이다.
- 바뀌는 것: `MAX_EVENT_TEXT`와 `note_cut`, 현재 계약 C1의 4,000자 문장, 새 오류 이름 하나. 더 큰 상한(예: 1 MiB)을 고르면 그 줄을 에이전트가
  다시 읽지 못하고 맥락 블록·status가 매번 큰 줄을 읽는다.

**OQ3 원본 안의 링크·junction.** ADR-0005는 hardlink만 금지하고 큰 데이터도 복사가 기본이라 했다. 심볼릭 링크와 junction(예: `data/` → 외부 디스크)은
정하지 않았다.
- 권장: 밖을 가리키는 링크는 대상의 바이트를 복사한다(공유 바이트 없음, 사전 공간 검사). 안을 가리키는 상대 링크는 같은 링크 글자로 다시 만든다(대상이
  목적지 안). 다시 만들 수 없거나 절대 링크면 파일은 바이트 복사, 디렉터리는 복사하지 않고 보고서에 적는다(6.6절).
- 바뀌는 것: 큰 외부 데이터의 디스크·시간 비용, 다시 만든 링크 아래 새 run의 정체성 경로, 현재 계약 G의 링크 문장, 측정 항목. 대안 "밖을 가리키는
  링크를 링크로 유지"는 목적지의 쓰기가 원본이 기록한 바이트에 닿을 수 있다. 대안 "그런 원본은 거절"은 그 프로젝트를 ③ 뒤 읽기 전용에 가둔다.

**OQ4 복사 범위.** ADR-0005 결정 5는 "기존 파일은 같은 상대 경로에 바이트 그대로 복사"라고 적었다. 재생성 가능한 파일과 환경에도 그대로 적용할지는
정하지 않았다. 빼면 글자 그대로의 범위를 좁히므로 소유자 확인이 필요하다.
- 권장: `.venv/`, `report/`, `.lab/cache/`, `.lab/`의 잠금 파일 셋, `__pycache__/`는 복사하지 않고 보고서 `excluded`에 경로·파일 수·바이트를 적는다.
  `.git/`을 포함한 나머지는 모두 복사한다. 원본 전후 비교에서도 제외 목록을 뺀다(T18은 `.venv/`·`uv.lock`을 따로 본다).
- 바뀌는 것: 목적지 크기, 비교 범위, `.git/` 사본으로 저장소가 둘이 됨(보고서가 알림). 모두 복사하면 원본 절대 경로를 키로 쓰는 캐시와 다른 기계의
  `.venv/`가 목적지에 남는다. `.git/`을 빼면 원본을 지운 사용자가 이력을 잃을 수 있다.

**OQ5 `analytics.run/2`에서 바꿀 필드 이름과 enum 값.** ADR-0005 결정 2는 "바꾼다면 #13에서"라고만 했고, 지금 바꾸지 않은 것은 결정 3 때문에 베타까지
바꿀 수 없다. Issue #13 댓글은 `via` 값을 바꾼다고 적었으나 철자는 정하지 않았다.
- 권장: 계약이 이미 새 이름을 쓰는 것만 바꾼다. `charts[]`·`id` → `visualizations[]`·`visualization_id`(용어집 §5, ADR-0006 2절), `via`의 `lab.` 접두 →
  SDK 함수와 같은 `analytics.visualize`·`analytics.read`·`analytics.scan`·`analytics.sql`·`analytics.save`, kind `gap` → `workflow_feedback`(ADR-0006).
  run `status`(→ `execution_status`), `reproduction`·`verdict`(→ `rerun_comparison`·`comparison_result`), `kit_*`는 그대로 두고 그 이유를 용어집에 적는다.
- 바뀌는 것: `analytics.run/2`의 필드(2.6절), reader의 `/1` 대응, Workspace·status의 읽기, #20 공유 투영의 필드 목록. 더 바꾸면 그 필드마다 reader
  대응과 화면이 늘어난다.

**OQ6 내부 코드 이름의 개명 PR ⓪.** #9-1·#9-2가 이 이름들을 "새 writer와 함께" 정하라고 넘겼다. 결정 7의 섞지 않기 규칙 때문에 ③에는 넣을 수 없고,
결정 7은 #13을 세 PR로 적었다.
- 권장: 동작을 바꾸지 않는 개명 PR ⓪을 ① 전에 둔다(9절의 ⓪ 행, CLI JSON 키 `report` → `workspace` 포함, `KIT_VERSION` 올림). 전체 용어 점검 §5의
  "단순 코드명 전환은 별도 PR"과 맞는다. 승인되면 ADR-0005 결정 7의 PR 목록에 한 줄을 더한다.
- 바뀌는 것: #13의 PR 수(구현 셋 → 넷), ADR-0005 결정 7의 목록, 계층표의 `report.py` 행과 허용 목록의 키, 확장의 JSON 파싱. 대안은 ③ 뒤에
  두는 것이며, 그러면 ①②③의 새 코드를 옛 이름으로 썼다가 다시 바꾼다.

**OQ7 ③ 뒤의 `/1` fixture.** 전환 E2E·측정과 #15·#41에는 `/1` 원본이 필요한데 ③ 뒤 kit은 `/1`을 쓰지 않는다. `tools/eval_fixtures.py --format v1`과
kit으로 `/1`을 만드는 시험이 그대로는 돌지 않는다.
- 권장: 단위 시험은 손으로 쓴 `/1`(`tests/legacy_records.py`). 전환 E2E와 측정은 ③ 전 kit이 실제로 쓴 바이트를 쓴다: 생성기에 `--kit-root`(고정한
  옛 커밋의 실행 루트)를 더해 그 kit으로 만들고, CI의 v1 생성 시험은 그 커밋을 얕게 가져와 돈다. `--format v2`는 #41이 ③ 위에서 더한다.
- 바뀌는 것: 생성기의 kit 선택, CI 단계 하나와 시간, #41의 범위. 대안 "작은 `/1` fixture를 저장소에 커밋"은 S 규모(약 2.4 MB)의 이진 파일을 저장소에 둔다.

**OQ8 #49(`/1` writer의 끊긴 줄·seq 재사용)의 시기.** ③ 뒤에는 `/1` writer가 쓰이지 않지만 그때까지 소유자의 프로젝트에 기록 손실 결함이 남는다
(상태 지도의 프로브 P7). ADR-0006은 이 수정을 #49에 맡겼다.
- 권장: ③을 기다리지 않고 #49를 작은 일반 PR로 먼저 고친다(쓰기 전 마지막 바이트가 `\n`이 아니면 `\n`, seq는 `relations.Journal.next_seq`). 전환할
  원본이 ③까지 손상을 더 얻지 않는다. ③ 뒤에는 그 코드가 쓰이지 않는다.
- 바뀌는 것: B1 결함 Issue의 일정, `/1` writer의 PR 하나. ①에 넣으면 ①의 범위가 커진다. ③에 맡기면 그 사이의 기록 손실 위험을 #49에 적는다.

**OQ9 새 형식 init의 위쪽 표식.** ADR-0005는 전환 목적지의 위쪽 표식만 거절한다. ③의 init이 `lab.json` 프로젝트 안에 `analytics.json` 프로젝트를
만들면, ① 전 kit이 그 안에서 위쪽 `lab.json` 프로젝트로 올라가 그 journal에 쓴다(프로브 P4와 같은 위험).
- 권장: ③의 init(`analytics init`, `analytics_init`, `/analytics init`, demo)도 위쪽에 `lab.json`·`analytics.json`이 있으면 `destination_invalid(inside_project)`로
  거절한다(전환과 같은 규칙).
- 바뀌는 것: 지금 허용되는 중첩 프로젝트를 더 만들 수 없다(이미 있는 것은 그대로 읽음). 현재 계약 B·D. 거절하지 않으면 한계로 적는다.

**OQ10 원본 안의 중첩 프로젝트.** 원본 아래에 다른 `lab.json`·`analytics.json`이 있거나 원본에 `.analytics/`가 있으면, 그대로 복사할 때 새 프로젝트 안에
옛 kit이 쓸 수 있는 프로젝트가 생기거나 전환의 관리 디렉터리와 섞인다.
- 권장: `nested_project`로 거절하고 경로를 모두 보인다. 사용자가 옮기거나 따로 전환한다.
- 바뀌는 것: 그런 프로젝트는 고치기 전까지 전환할 수 없다(③ 뒤 읽기 전용). 대안 "다른 이름으로 복사하고 매핑에 적음"은 매핑과 reader를 넓힌다.

**OQ11 ③에서 여는 공개 입력.** 조건 4는 공통 writer의 처리이고, 정정·철회·재개를 사람·에이전트가 부를 입력(CLI·SDK·도구 인자)은 아직 없다. ADR-0006 8절의
`request_id`도 writer API에는 있으나 공개 입력이 없다. #14(지표·초점)와 #19(Report 저장)가 이 입력을 쓸 쪽이고, #15는 #13 writer가 쓴 정정을 읽는다.
- 권장: ③은 writer API(`Writer.update`, `supersedes` 참조, `Origin.request_id`)와 시험(8절), 응답의 `request_id`까지 한다. 상태 변경·정정의 공개 입력은 #14가
  지표·초점의 입력과 함께, `--request-id`·도구 인자는 #19가 연다. 그동안 reader(#12)가 충돌과 어긋난 갱신을 보인다.
- 바뀌는 것: ③의 크기, 용어집의 새 입력 이름, 도구 schema 토큰. ③에서 열면 `analytics record … --supersedes eN`, 갱신 입력(예: `analytics update eN
  --expected S --new S TEXT`), `analytics_record`의 선택 인자를 이 설계에 더해 설계 리뷰를 다시 받아야 한다.

---

## 17. 두 설계안에서 가져온 것

같은 입력으로 쓴 두 안을 다섯 완료 조건, 원본 바이트 보존, ADR 충실도, 옛·새 프로젝트의 읽기 호환, 시험 가능성, PR 크기, 제품 결정을 지어내지 않음으로
비교했다. 원본 보존을 앞세운 안이 #13의 가장 큰 위험(원본 손실과 반쯤 쓴 목적지)을 더 단단히 막고 보장마다 시험을 짝지어 바탕으로 삼았다.

- 원본 보존 안에서 가져온 것: 보장 S·시험 T의 짝, 원본 잠금 전에 쓰는 ledger, 기다리지 않는 잠금과 목적지 잠금, 계획·복사·재확인 동안만 쥐는 원본
  잠금, 파일마다 임시 파일 + 교체, 표식 전 내구 쓰기, `source-marker.json`, 표식의 `migration` 필드, `migration_incomplete`, 중단 지점 F1–F11 표,
  `.lab/` 이력을 `.analytics/`로 담는 배치, 사용자 선택을 지키는 `.gitignore` 규칙, 잠금 안의 표식 다시 읽기, 새 계층으로 강제되는 계층 배치,
  고장 주입 driver.
- 단계별 PR 안에서 옮긴 것: 게이트를 ①에 먼저 깔고 `WRITABLE` 한 줄로 ③에서 뒤집는 순서, PR마다 각 kit이 보는 것의 표, `Origin` 배관을 ①로 당김,
  필드 이름 `means`(`channel`은 게시가 쓴다), `actor_basis`의 `inferred`, 깨진 `lab.json`을 `legacy`로 보는 회귀 방지, `Writer.locked()`를 #19와 공유,
  쓰는 시점의 결박 표, 32 KiB 상한의 근거, 런처·확장·셸의 같은 탐색 규칙, OQ9·OQ11, plotly 템플릿의 조건부 처리, `NOTE_KINDS`·`add_note`를 ⓪에서
  개명하지 않고 ③에서 대체, 진입점 행렬과 트리 스냅샷, 매핑의 `kinds`, PR별 상세.
- 두 안이 놓쳐 이 문서가 더한 것(코드로 확인): `KIT_VERSION`을 writer로 옮기면 `tools/model_eval.py`의 정규식 읽기가 깨지므로 core에 남김,
  지금 `FileLocks.exclusive`는 부모 디렉터리를 만들므로 `.lab/`이 없는 원본에는 잠금 없이 비교에 맡김, 표식 없는 목적지에서 런처가 `uv run --project`를
  부르면 `.venv/`가 생겨 이어 쓰기를 막으므로 `--no-project`와 무시 규칙, 중단 사이의 무관한 원본 변경 때문에 이어 쓰기를 막지 않는 다시 세운 계획,
  옛 신뢰 목록을 읽기 전용으로 함께 보는 범위, uv 환경이 게이트 대상이 아님을 명시, `uv sync --locked` 프로브.

---

## 18. 근거와 미실행

- 코드 기준: `09d22c4`(PR #76 병합 뒤). 확인한 것: `core.find_project`(`lab.json`만), `core.read_events`(`lab.event/1`만), `Project.config`(같은 가족 번호만),
  `core.KIT_VERSION`을 읽는 곳(`tools/model_eval.py`의 정규식, `tests/test_packaging.py`), `PosixFileLocks.exclusive`의 부모 디렉터리 생성,
  런처의 `init`이 쓰는 `uv run --no-project`와 다른 명령의 `kit_argv`(`uv run --project`), `relations.read(…, legacy_path=)`와 `ReadModel.read_as`의
  옛 kit 조건, `relations.CONTEXT_DIRS`, 기록 schema의 `project_2`(추가 필드 허용), 템플릿 `gitignore`.
- 프로브(모두 macOS Darwin arm64, uv 0.11.19, 실행 폴더 안, `AELIX_ANALYTICS_TRUST_FILE`·`AELIX_ANALYTICS_UV`·uv 캐시를 실행 폴더 안으로, `AELIX_LAB_*`·
  `LAB_*` 해제, `~/.aelix`를 건드리지 않음). 파일은 실행 폴더의 `evidence/` 아래에 있다.
  - `probe-current-writer`(`ebb6fc8`, `probe.txt` sha256 `907eae44…4fb5`): P1–P7. 표식을 `lab.project/2`로 고쳐도 `record`가 `lab.event/1`을 씀(P2),
    `analytics.json`만이면 `no_project`(P3), `lab.json` 프로젝트 아래 `analytics.json` 디렉터리의 `record`가 위쪽에 씀(P4), 두 표식을 `lab.json`으로 봄(P5),
    `/2` 줄이 목록에서 빠짐(P6), 끊긴 줄 뒤에 붙여 쓰고 seq 재사용(P7).
  - `probe-safety`(`probe.txt` sha256 `457d9ba7…49a9`): `uv run --project`가 `.venv/`·`uv.lock`을 만들고 맞지 않는 `uv.lock`을 다시 씀, `--frozen --no-sync`도
    `.venv/`를 만듦, 프로젝트 안의 `uv run --no-project`는 아무것도 만들지 않음, kit의 잠금이 없던 `run.lock`을 만듦, `flock(LOCK_EX | LOCK_NB)`가 잡힌
    동안 `EAGAIN`(35)·놓은 뒤 획득.
  - `probe-uv-no-project`(`probe.txt` sha256 `5cf601a9…e29e1`): 같은 결과를 따로 확인.
  - `probe-uv-locked`(이 문서, `probe.txt` sha256 `d7e00f59…b2f7`): `uv sync --locked`는 맞는 `uv.lock`을 바꾸지 않았고, 맞지 않는 `uv.lock`에서는 종료 1로
    실패하며 그 파일을 바꾸지 않았다. 실패한 경우에도 `.venv/`는 만들었다.
- 이 문서의 검증: 문서 검사(`uv run --locked python tools/check_docs.py`)와 `git diff --check`. 이것은 링크·형식의 검사이며 설계의 시험이 아니다.
- 미실행(통과로 보지 않음): pytest·ruff·CI 전체, Windows·Linux에서의 위 프로브, aelix bash 도구의 환경(OQ1), Windows의 잠금 해제 지연과 디렉터리
  flush, plotly 템플릿 이름이 저장 바이트에 들어가는지, M 규모 생성과 전환·writer 시간, 이 문서의 모든 시험(T1–T18, 코드가 없음), 설계 리뷰(독립·Codex).
