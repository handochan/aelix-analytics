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
  [ADR-0008](../decisions/0008-reuse-proposal-counting-and-deterministic-always-on.md) 결정 3(요청 주체·실행 수단)·결정 7(에이전트 요청의 CLI 호출),
  [용어집](../TERMINOLOGY-AND-LANGUAGE.md) §2·§3·§6·§8, [전체 용어 점검](../REVIEW-TERMINOLOGY-2026-09-18.md) §5,
  [연속성 설계](../DESIGN-CONTINUITY-AND-REUSE.md), [아키텍처](../ARCHITECTURE.md) §4·§7·§11, [베타 기준](../BETA-QUALITY-GATES.md) Q4·Q7·Q8·Q17,
  [운영 정책](../PROJECT-OPERATIONS.md)의 리뷰 예산과 검증 증거, 결정 Issue [#3](https://github.com/handochan/aelix-analytics/issues/3)·
  [#4](https://github.com/handochan/aelix-analytics/issues/4), 이 일을 기다리는 [#14](https://github.com/handochan/aelix-analytics/issues/14)·
  [#15](https://github.com/handochan/aelix-analytics/issues/15)·[#17](https://github.com/handochan/aelix-analytics/issues/17)·
  [#19](https://github.com/handochan/aelix-analytics/issues/19)·[#20](https://github.com/handochan/aelix-analytics/issues/20)([공유 build 설계](PUBLICATION-BUILD.md)).
- 만든 방법: 같은 입력으로 따로 쓴 두 설계안(단계별 PR을 앞세운 안, 원본 보존을 앞세운 안)을 비교했다. 원본 보존 안을 바탕으로
  하고 다른 안의 장점을 옮겼으며, 두 안이 놓친 것을 코드로 확인해 더했다(17절). 두 안·상태 지도·프로브는 Git 밖의 로컬 작업 폴더
  `.devstate/runs/abd7197a-3fd9-432e-b69a-76e02e5b85ba/`에 있다.
- 리뷰: #13은 기록 형식·전환 PR이므로 구현 전에 이 문서가 설계 리뷰(독립 1회·Codex 1회)를 받는다. 그 리뷰(꼭 고칠 것 M1–M8, 검토할 것
  1–21, 새 소유자 질문 2개)를 받았고, 지적마다 처리한 곳은 19.1절에 있다. 고친 diff는 리뷰 예산대로 수정 diff 확인을 한 번 받았고(꼭 고칠 것 2,
  검토할 것 6, 사소한 것 5), 그 처리는 19.2절에 있다. 그 처리 뒤의 이 문서는 다시 확인받지 않았다(자기 검토만, 리뷰 예산).
- 소유자 결정(2026-10-08, [#13](https://github.com/handochan/aelix-analytics/issues/13#issuecomment-6044853778)): 16절의 권장 답 16개(OQ1–OQ16)를
  모두 채택했고, Q17-b의 범위는 Aelix 세션으로 정했다(aelix 밖 에이전트가 밝히지 않은 CLI는 `human`·`inferred`로 남고, 다른 호스트에는
  `AELIX_ANALYTICS_ACTOR=agent`를 안내한다). 본문의 "(OQn)" 표시는 그 결정을 따른 규칙이다. OQ4·OQ15·OQ16은 ADR-0005 결정 5의 글자를
  좁히고 OQ6은 결정 7의 PR 목록에 ⓪을 더하므로, ⓪의 PR이 ADR-0005를 개정한다. ADR이 #13 설계에 맡겼거나 입력이 정하지 않은 설계 선택은
  15절(D1–D29)이며 설계 리뷰가 정한다.

---

## 0. 요약

1. PR은 다섯이다. D(이 문서) → ⓪ 내부 코드 이름(동작 그대로, OQ6) → ① 표식 판정·새 가족 읽기·두 표식 충돌 → ② 닫힌 전환 엔진
   → ③ 새 writer·정책 전환·저장 이름·전환 명령. ①②③은 ADR-0005 결정 7의 세 PR 그대로다.
2. 게이트를 먼저 깐다. ①이 모든 쓰기 진입점의 첫 부수 효과 전에 `writer.admit()`을 부르게 하고, 쓸 수 있는 표식은 상수 `WRITABLE`
   한 줄로 정한다(①·②는 `lab.project/1`, ③은 `analytics.project/2`). 그래서 결정 7의 불변식이 ①부터 성립하고, ③은 이 줄과 writer를 바꾼다.
3. 새 모듈은 둘이다. `writer.py`(새 계층 `writer`, 수준 1)와 `migration.py`(새 계층 `migration`, 수준 3). 둘 다 stdlib만 쓰고, `core`
   이상의 수준을 하나씩 올린다. writer가 core를 import하지 못하게 기존 계층 검사가 막는다.
4. 공통 writer는 모든 진입점에서 `Origin(actor, means, actor_basis, session, request_id)`를 명시적으로 받는다. 실행 수단 필드의 이름은
   `means`이고 값은 `cli`·`sdk`·`tool`·`command`·`hook`이다. aelix의 bash 도구가 확장이 프로세스 환경에 넣은 변수를 자식에게 넘긴다는 것을
   프로브로 확인했다(R9). 그래서 확장이 `AELIX_ANALYTICS_ACTOR=agent`를 넣어 에이전트가 부른 CLI를 에이전트 기록으로 남긴다(OQ1).
5. 같은 상태 변경은 journal 잠금 안에서 표식을 다시 읽고 `relations.Replay`로 재생해 판정한다. 맞지 않으면 `state_conflict`로 거절하고
   아무것도 쓰지 않는다. 끊긴 줄 뒤에는 줄바꿈을 먼저 쓴다. 잠금 안의 단계는 `Writer.locked()` 하나이며 #19의 Report 저장도 쓴다.
6. 전환 명령은 `analytics migrate --to DEST [--dry-run]`이다. 원본은 다른 명령과 같은 규칙으로 찾은 `lab.json` 프로젝트다. 원본을 다시 읽지
   않는 목적지(원본 재확인 뒤의 이어 쓰기, 커밋된 목적지)는 원본을 찾지 못해도 잇는다(6.1절). 런처는 `init`처럼 `uv run --no-project`로
   kit을 부른다. 프로브에서 `uv run --project`는 원본에 `.venv/`·`uv.lock`을 만들거나 다시 썼다.
7. 전환은 원본에 잠금 파일 말고는 쓰지 않는다. 목적지는 원본 루트뿐 아니라 복사가 읽는 모든 실제 디렉터리(따라가는 바깥 링크의 대상)와도
   겹치지 않아야 한다. 목적지 ledger를 원본 잠금보다 먼저 쓰고, 원본의 `run.lock` → `lock`을 기다리지 않고 잡아 계획·복사·원본 재확인 동안만
   쥔다. `.lab/`이 없거나 잠금 파일을 만들 수 없는(읽기 전용) 원본을 잠금 없이 전후 비교로만 지킬지는 결정 5의 글자와 달라 소유자 질문 OQ15다.
8. 사본은 같은 상대 경로·같은 바이트다. 기록하는 대응은 셋이다: `.lab/` 안의 이력·로그 → `.analytics/` 같은 이름(ADR-0005 결정 5가 #13에
   맡긴 배치), `lab.json` → `.analytics/migration/<id>/source-marker.json`(바이트 그대로), 보통 파일·디렉터리로 바꾼 링크(링크 경로와 옛 대상의
   실제 경로). 매핑이 셋을 모두 담아 옛 run의 경로 추론이 전환 뒤에도 같게 풀린다. `.gitignore`는 줄을 덧붙이고 전후 해시와 diff를 남긴다.
9. 사본·새 파일은 "없을 때만 만드는" 원자 게시로 최종 이름에 놓는다. 목적지에 사용자가 만든 파일을 덮어쓰지 않는다. 커밋 지점은 검증·보고서·
   내구 쓰기 뒤의 `analytics.json` 게시 하나다. 표식이 보고서의 sha256을 담고, 보고서에는 커밋 전에 알 수 없는 값을 넣지 않는다. 표식 전의
   목적지는 옛 kit에는 `no_project`, 새 kit에는 `migration_incomplete`다.
10. 중단된 전환은 같은 명령으로 잇는다. ledger의 줄이 정한 상태만 받는다. 그래서 전환이 목적지에 만드는 모든 파일은 그 이름을 ledger에 먼저
    적은 뒤 만든다. 원본 재확인(`source_checked`) 전이면 이미 복사한 파일이 지금 원본과 같아야 하고, 그 뒤면 원본을 다시 보지 않고 기록한
    스냅샷으로 잇는다. 목적지의 파일을 덮어쓰지 않고, ledger에 먼저 적고 이 전환이 만든 것 말고는 지우지 않는다.
11. 다시 부른 전환은 목적지를 먼저 읽기만 하고 판정한다. 두 표식·깨진 표식·모르는 표식은 그 오류이고, 같은 원본의 커밋된 전환일 때만
    `already_migrated`다. 원본 쪽 사전 검사는 원본을 다시 읽을 상태(새 전환, 원본 재확인 전의 이어 쓰기)에서만 한다.
12. 읽기는 run 기록의 `charts[]`(`/1`)와 `visualizations[]`(`/2`)를 `relations`의 한 함수로 같은 보기로 읽고, 재실행 비교를 포함한 모든 소비자가
    그 함수를 쓴다. 재실행 비교의 Visualization별 결과(`/1` `reproduction.charts`, `/2` `reproduction.visualizations`)도 한 함수로 읽는다. 관리
    디렉터리와 제외 디렉터리는 표식이 고른 배치(`Layout`·`LegacyLayout`)마다 따로 둔다.
13. ③ 뒤 `lab.json` 프로젝트는 읽기만 된다. 일반 쓰기는 `migration_required`와 전환 명령 안내로 거절하고 부수 쓰기는 건너뛴다.
    두 표식은 모든 작업에서 `marker_conflict`다.
14. 측정 기록(md+JSON)은 ①②③에 하나씩 둔다. 실행하지 않은 것(pytest·CI·Windows·Linux·M 규모·전원 손실 등)은 18절에 있고 통과로 보지 않는다.

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
| S1 | 전환은 원본에 잠금 파일(없을 때 생김)을 만들거나 잡는 것 말고는 쓰지 않는다. 복사가 읽는 바깥 링크의 대상에도 쓰지 않는다(목적지가 그 안·같은 곳·위이면 거절). 실패·중단·거절·`--dry-run`도 같다. 잠금 파일을 새로 만들었으면 ledger와 보고서가 그 사실을 적는다 | ADR-0005 결정 4·5 | T1, T2, T6, T18 |
| S2 | 목적지의 커밋 지점은 `analytics.json`이 없을 때만 만드는 원자 게시 하나다. 그 전에는 어떤 kit도 목적지에 기록하지 않고, 표식과 신뢰 항목은 검증 뒤에만 생긴다 | ADR-0005 결정 5 | T3, T6 |
| S3 | 사본마다 바이트 수·sha256을 보고서에 적고, 표식이 보고서의 sha256을 담는다. 보고서에는 커밋 전에 정해진 값만 있다. 누구든 다시 계산해 대조할 수 있다 | ADR-0005 결정 4 "전후 해시" | T4 |
| S4 | 사본은 원본과 같은 바이트다. 위치가 다른 것은 매핑이 적은 대응(`.lab/` 접두, `lab.json`, 보통 파일·디렉터리로 바꾼 링크)뿐이고, 내용이 다른 것은 보고서가 전후 해시·diff로 적은 `.gitignore` 덧붙임뿐이다 | ADR-0005 결정 5, 용어 점검 §5 5항 | T4 |
| S5 | 복사 구간에 원본이 바뀌면(목록·크기·해시) 표식 없이 실패하고 바뀐 경로를 알린다. 사본에서 조용히 빠지는 기록이 없다 | ADR-0005 결정 5 | T5 |
| S6 | 중단된 목적지는 "이어 쓸 수 있음" 또는 "사유와 함께 거절" 가운데 하나다. ledger의 줄이 정한 상태만 받고, 전환이 목적지에 만드는 파일은 모두 ledger에 먼저 적은 뒤 만든다. 목적지에 남이 만든 파일을 덮어쓰지 않고(사본·표식은 없을 때만 게시하고, 덮어쓰는 교체는 이 전환이 가진 파일에만), ledger가 이름을 적은 이 전환의 파일(임시 파일, 2단계 시험 파일, 정체성까지 적은 `direct` 파일)과 내용 없는 전환 디렉터리 말고는 지우지 않는다(6.10절) | ADR-0005 결정 5 | T6, T19 |
| S7 | 한 목적지에는 한 전환만 돈다. 원본 잠금은 기다리지 않고 시도하며 옛 kit과 같은 잠금 파일·바이트 범위를 쓴다 | ADR-0005 결정 5, ADR-0003 결정 1 | T7, T15 |
| S8 | 모든 쓰기 작업은 첫 부수 효과 전에 표식을 새로 읽어 판정하고, journal 쓰기는 잠금 안에서 한 번 더 읽는다. 거절한 작업은 프로젝트 트리를 바꾸지 않는다 | ADR-0005 결정 6, ADR-0006 1.3 | T8, T10, T12 |
| S9 | ①부터 어느 PR이 병합된 main에서도 `analytics.json` 디렉터리에 옛 형식(`lab.*/1`, `.lab/`, `data/raw/`, `runs/<id>/charts/`)이 써지지 않는다 | ADR-0005 결정 7 | T8 |
| S10 | 같은 상태 변경의 충돌은 `state_conflict`로 거절하거나 `conflict`로 보이고, 중단은 `damaged`·`incomplete`·`migration_incomplete`로 남는다 | ADR-0006 6절, 완료 조건 4 | T13, T6 |
| S11 | 모든 새 기록에 작성 주체와 실행 수단이 있고, 진입점이 밝히지 않은 작성 주체는 그 사실이 남는다. aelix 세션의 에이전트가 bash 도구로 부른 CLI는 `agent`로 남는다(OQ1). 세션 없는 기록도 모든 읽기에 나온다 | ARCHITECTURE §4, Q17-b, ADR-0008 결정 7 | T14, T22 |
| S12 | 새 기록은 writer 한 곳에서 UTF-8(BOM 없음)·LF로 쓰고, 사용자 내용의 사본은 이진 모드로 바이트 그대로 복사한다 | ADR-0006 10절, ADR-0007 결정 2·6 | T16 |
| S13 | 한 run의 Visualization은 `/1`(`charts[]`)이든 `/2`(`visualizations[]`)이든 같은 보기로 읽히고, 재실행 비교는 두 형식 사이에서도 같은 id끼리 비교한다. `/1` run을 다시 실행해도 비교가 형식 차이로 `missing`·`new`가 되지 않는다. 재실행 비교의 Visualization별 결과도 `/1`(`reproduction.charts`)이든 `/2`(`reproduction.visualizations`)이든 모든 화면에 나온다 | ADR-0006 2·3절, 현재 계약 C | T9, T11 |
| S14 | 관리 디렉터리는 그 프로젝트 형식의 것만이다. 새 형식 프로젝트에서 사람이 만든 `report/`의 출력은 run 기록에 출력으로 남는다 | ADR-0006 9절 "관리 디렉터리" | T20 |

S2·S6과 6.11절의 중단 결과는 프로세스 중단(`os._exit`, SIGKILL)에 대한 보장이며 그것으로 시험한다. 전원 손실 뒤에도 표식이 가리키는 사본이
디스크에 있게 하는 내구 쓰기(6.4절)는 목표이고 보장이 아니다. 시험할 수 없어 측정 기록의 `not_run`에 두고, 플랫폼마다 남는 한계(Windows의
디렉터리 flush, 원자 교체가 write-through가 아님)를 6.12절에 적는다.

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

관리 디렉터리(`core.MANAGED_DIRS`)와 run의 입출력에서 빼는 디렉터리(`core.IGNORED_DIRS`), ADR-0006 9절의 관리 디렉터리 철자 예외는 두 형식을
합친 집합으로 두지 않고 배치마다 따로 둔다(③). 표식이 배치를 고른다.

| 배치 | 관리 디렉터리 | 그 밖에 run 입출력에서 빼는 디렉터리 |
|---|---|---|
| `LegacyLayout`(`lab.json`) | `.venv`, `.lab`, `runs`, `report` | 지금 `IGNORED_DIRS`의 나머지(`scripts`, `.git`, `__pycache__` 등) |
| `Layout`(`analytics.json`) | `.venv`, `.analytics`, `runs`, `workspace` | 같음 |

새 형식 프로젝트에서 `report/`와 `.lab/`은 관리 디렉터리가 아니다. 목적지에는 둘 다 없고(6.5절), 사람이 나중에 `report/`를 만들어 스크립트가
거기에 쓰면 그 파일은 run의 출력으로 기록되고 저장된 철자 그대로 이름 붙는다. 두 형식을 합친 집합을 쓰면 그 출력이 run 기록에서 빠지고 kit의 이름으로
철자가 바뀌는 거짓 기록이 된다. 옛 프로젝트를 읽을 때는 `LegacyLayout`이 지금과 같은 판정을 준다. 결정 2의 개명에 따른 같은 규칙의 적용이며
현재 계약 B·E에 적는다(T20).

### 2.4 표식 `analytics.project/2`

```json
{"schema": "analytics.project/2", "project_id": "prj_7f3a9c2e41d84b06", "title": "Regional revenue",
 "created_at": "2026-09-01T09:00:00.000+09:00", "min_writer_version": "<the KIT_VERSION of PR 3>",
 "kit_version": "<the kit that wrote this file>",
 "migration": {"id": "20261008T010203Z-a1b2c3d4",
               "report": ".analytics/migration/20261008T010203Z-a1b2c3d4/report.json",
               "report_sha256": "<sha256 of report.json>"}}
```

- `project_id`는 무작위 값이다(schema `opaque_id`). init·전환 모두 새로 만든다(전환은 처음 계획에서 정해 ledger의 `plan` 줄에 적는다, 6.3절).
  옛 `lab.json`에는 이 값이 없다.
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
| `reproduction.charts`(재실행 비교의 Visualization별 결과) | `reproduction.visualizations`. 값(`same data`·`missing`·`new` 등)과 `reproduction`·`verdict`의 이름은 그대로. reader는 두 이름을 `relations.run_reproduction`(5.3절) 하나로 읽는다 (OQ5) |

`visualization_version` 참조의 `digest`는 그 Visualization의 `sha256`을 옮긴다(ADR-0006 3절). reader는 `/2` Visualization을 해시가 있는
대상으로 판정한다.

재실행 비교(`runner.compare_runs`)는 원래 run과 새 run의 Visualization을 5.3절의 한 보기로 읽어 Visualization id끼리 비교한다. `/1` run의 `id`와
`/2` run의 `visualization_id`는 같은 값 공간(같은 스크립트의 `chart(id=…)`·`visualize(id=…)`)이므로, 전환한 프로젝트에서 `/1` run을 다시
실행해도 형식 차이 때문에 모든 Visualization이 `missing`·`new`가 되고 `verdict`가 `mismatch`가 되는 거짓 기록이 생기지 않는다(S13). 비교 결과는 새
run(`/2`)의 `reproduction.visualizations`에 쓴다.

이 표(OQ5)와 D2·D4는 ①이 병합되기 전에 정해야 한다. ①이 `analytics.run/2`·`analytics.dataset/1`의 reader를 먼저 싣기 때문이다. ③에서 정의가 바뀌면
① kit이 ③의 프로젝트를 잘못 읽거나, ADR-0006 1.2에 따라 `/3`을 새로 열어야 한다.

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
- 권장(OQ2): UTF-8 32 KiB 이하의 본문은 전부 보존한다. 그리고 직렬화한 줄 전체(JSON 이스케이프, 참조, 머리 필드, 끝 줄바꿈 포함)가
  50 KiB(51,200바이트) 미만이어야 한다. 둘 중 하나라도 넘으면 아무것도 쓰지 않고 `text_too_long`(본문 바이트, 줄 바이트, 두 상한, "긴 글은
  파일이나 Report로 저장하고 참조하라")으로 거절한다. aelix read 도구는 50 KiB를 넘는 한 줄을 읽지 않는다(현재 계약 A). 본문 상한만으로는 줄을
  지키지 못한다: JSON 이스케이프는 따옴표·역슬래시를 2바이트로, 제어 문자를 6바이트(`\u0001`)로 늘리고, 참조 24개도 바이트를 더한다. 줄 검사는
  writer가 실제로 쓸 바이트를 직렬화한 뒤에 한다. 시험은 이스케이프가 많은 본문(제어 문자·따옴표·역슬래시만)과 긴 참조 24개로 두 상한의 경계를 잰다.
- 화면과 맥락 블록은 지금의 생략 표시(#11, 현재 계약 A의 줄임과 다시 읽을 위치)를 그대로 쓴다.

---

## 3. 새 모듈과 계층표 (완료 조건 5)

### 3.1 모듈

| 모듈 | 책임 | 외부 | 들어오는 PR |
|---|---|---|---|
| `src/aelix_analytics/writer.py` | 표식 판정과 게이트, `Origin`, 오류 클래스, 가족 태그·경로 배치표, 원자 JSON 쓰기, journal 덧붙이기 규약, `record_update` 확인, 참조 분류와 쓰는 시점의 결박 | stdlib | ①(판정·게이트·헬퍼·`Origin`·참조 분류), ③(`Writer`) |
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
- UTF-8·LF 텍스트 헬퍼(`open_text`·`write_text`·`newlines_as_lf`)는 ①에서 `_platform`(계층 `ports`, 수준 0)으로 옮긴다. ADR-0007 결정 2가 이
  헬퍼를 "core나 `_platform`에" 두라고 했으므로 writer로 옮기면 그 결정에서 벗어난다. `_platform`이면 writer와 core가 모두 import할 수 있고,
  플랫폼마다 다른 동작이 아니므로 포트 메서드가 아니라 모듈 함수다(결정 2의 "포트를 만들지 않는다" 그대로). `write_json`·`append_jsonl`은 writer로
  옮긴다. 다섯 함수 모두 core가 같은 이름으로 다시 내보낸다. `tests/test_line_endings.py`의 쓰기 검사가 허용하는 함수 이름을 새 위치로 바꾼다.
  다섯 함수의 이동이라 "대규모 파일 이동"이 아니다.
- writer는 core를 import하지 못하므로 쓰는 시점의 결박(4.7절)에 필요한 것을 이렇게 나눈다. 문자열 참조를 run·이벤트·Visualization 키·데이터로
  나누는 순수 함수(`core.classify_refs`와 그것이 쓰는 id 정규식)는 ①에서 writer로 옮기고 core가 다시 내보낸다. 이름만 받은 Visualization id를
  키로 푸는 일(지금 `core.resolve_ref_aliases`, `Project`가 필요)과 결박은 `relations`(수준 0)의 읽기 모델로 하고,
  옛 kit 경로의 추론(`Project.legacy_identity`, 전환 매핑, 5.4절)은 core가 `Writer`를 만들 때 `legacy_path=` 함수로 넣는다. 잠금·교체는 지금처럼
  `platform` 인자로 받는다. 그래서 writer 안에는 core의 `Project`가 없다.

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
| 위 CLI를 aelix 세션의 에이전트가 bash 도구로 부름 | `agent`(확장이 aelix 프로세스 환경에 넣은 `AELIX_ANALYTICS_ACTOR`) | `declared` | `cli` | 확장이 넣은 `AELIX_ANALYTICS_SESSION` |
| 위 CLI를 사람이 aelix의 `!`·`!!` 명령으로 부름 | `human`(확장의 `user_bash` 처리가 그 명령의 환경에 넣음) | `declared` | `cli` | 같음 |
| 확장의 모델 도구 `analytics_*` | `agent` | `declared` | `tool` | aelix 세션 |
| 확장의 `/analytics` 명령 | `human` | `declared` | `command` | aelix 세션 |
| 확장의 hook(프롬프트 직전 맥락 동기화, `agent_end`의 Workspace 예약), 상태 도구 안의 동기화 | `system` | `declared` | `hook`(상태 도구 안이면 `tool`) | aelix 세션 |
| SDK, run 밖 | `AELIX_ANALYTICS_ACTOR`, 없으면 `human` (OQ1) | 위와 같음 | `sdk` | `AELIX_ANALYTICS_SESSION` |
| SDK, run 안 | 그 run의 `actor` | 그 run의 값 | `sdk` | 그 run의 값 |
| 전환 | 명령을 부른 쪽의 값(위 첫 세 행 가운데 해당하는 것) | 위와 같음 | `cli` | 위와 같음 |

필요한 바뀜(③, 배관은 ①):

- 런처와 `bin/analytics`는 `AELIX_ANALYTICS_ACTOR`를 기본값 `human`으로 채워 넘기지 않는다(`cli.child_env`, `bin/analytics`). 사용자가 설정했을
  때만 넘기고, 기본값은 kit이 채워 `inferred`로 남긴다.
- 확장은 kit 자식에 실행 수단을 알리는 내부 변수 `AELIX_ANALYTICS_MEANS`를 넣는다(확장 `child_env`). `AELIX_ANALYTICS_CANCEL_FILE`처럼 내부
  프로토콜이라 용어집 §3의 역할 목록에 넣지 않고 현재 계약 D에 적는다. kit은 이 변수가 없으면 `cli`다.
- 확장은 로드될 때 aelix 프로세스의 `os.environ`에 `AELIX_ANALYTICS_ACTOR=agent`를 넣고, 세션이 정해지거나 바뀔 때(`before_agent_start`, 지금
  `STATE["session"]`을 정하는 곳) `AELIX_ANALYTICS_SESSION`을 그 값으로 바꾼다. aelix 0.1.0b2의 bash 도구는 호출마다 `os.environ`을 복사해 자식의
  환경을 만들므로 에이전트가 부른 `analytics`가 이 값을 받는다(R9 프로브, 18절). `AELIX_ANALYTICS_MEANS`는 `os.environ`에 넣지 않는다(bash로 부른
  CLI의 실행 수단은 `cli`다). 확장 자신의 kit 자식은 지금처럼 허용 목록으로 만든 환경에 작성 주체를 직접 넣으므로(`child_env`) 영향을 받지 않는다.
- 같은 `os.environ`은 사람이 aelix에서 친 `!` 명령에도 간다(R9의 D1). 그래서 확장은 `user_bash` 이벤트에서, kit을 부르는 명령이면(아래)
  `UserBashResult(operations=…)`로 그 명령의 환경에만 `AELIX_ANALYTICS_ACTOR=human`을 넣는 실행기를 돌려준다. R9의 E1은 `cli/repl.handle_user_bash`를 hook 발행만 흉내 낸 대역
  (발행하면 그 결과를 바로 돌려줌)으로 실행해 이 실행기가 그 명령에만 닿는 것을 확인했다. 확장 등록(`on("user_bash")`)에서 aelix의 처리기 합치기
  (`_reducer_user_bash`)까지의 전달은 코드로 읽었고 실행하지 않았다(T22가 실제 등록으로 돈다).
- aelix는 결과를 돌려준 처리기 가운데 **마지막** 것만 쓰고 앞의 처리기는 서로의 결과를 보지 못한다(`_reducer_user_bash`, 읽음). 그래서 두 방향이
  생긴다. 다른 확장(샌드박스·원격 실행 등)의 실행기가 마지막이면 사람이 부른 CLI가 `agent`로 남는다. 이 확장의 실행기가 마지막이면 **다른 확장의
  실행기를 조용히 밀어내** 그 `!` 명령이 그 확장 대신 이 확장의 로컬 실행기로 돈다. 사용자가 고른 실행 격리를 끄는 보안상의 부작용이다. 권장(OQ1)은
  명령 글이 kit의 진입점(`analytics`, `bin/analytics`, `aelix_analytics`)을 부를 때만 실행기를 돌려주고 나머지 `!` 명령에는 결과를 돌려주지 않는
  것이다. 그래도 그 명령에서는 밀어내기가 남으므로 현재 계약 A에 한계로 적는다. 확장 API가 다른 처리기의 등록을 알려 주는지는 확인하지
  않았다(hook 버스의 `has_handlers`는 자기 처리기도 세고, 확장에서 닿는지 모른다). RPC 모드의 `bash` 명령(처리기 없이 `os.environ`을 그대로 씀)은
  사람이 부른 CLI가 `agent`로 남는 쪽의 한계다(R18, OQ1).
- 확장이 aelix 프로세스의 환경을 바꾸는 것은 현재 계약 A(확장이 호스트에 남기는 것)의 변경이다. aelix가 띄우는 다른 자식(MCP 서버, 다른 확장의
  `exec`)도 이 변수를 물려받는다. 출처 기록이며 인증이 아니다: 에이전트가 명령 안에서 변수를 지우거나 바꾸면 그대로 기록된다.
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
- 없음과 읽기 실패를 구별한다. `stat`이 "없음"(`FileNotFoundError`)이면 그 표식은 없다. 있는데 열기·읽기가 운영체제 오류로 실패하면(Windows에서 다른
  프로세스가 그 표식을 교체하는 순간의 공유 위반 등, `_platform.AtomicFiles`의 설명) 교체와 같은 재시도 예산(`REPLACE_ATTEMPTS`·`REPLACE_POLL_SECONDS`)
  안에서 다시 읽고, 그래도 실패하면 `invalid`(`details.reason: unreadable`, 운영체제 오류)로 판정한다. 읽지 못한 표식을 `none`으로 보거나 내용이
  깨진 표식(다른 `details.reason`의 `invalid`)으로 보지 않으며, 표식 없는 전환 목적지(`incomplete`)로도 보지 않는다.
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
| `analytics.json`만: JSON 객체가 아님, 필수 필드 없음, `min_writer_version`이 정규형이 아님, 또는 재시도 뒤에도 읽지 못함(`unreadable`) | `invalid` | `marker_invalid` | 같음 | 건너뜀 | 아는 기록만, 사유를 보고 |
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
- 명시한 루트가 위로 올라가는지는 진입점마다 지금 규칙을 그대로 둔다. 바뀌는 것은 찾는 표식의 집합뿐이다. kit의 `--project`·
  `AELIX_ANALYTICS_PROJECT`(`__main__._project`)와 SDK(`__init__`)는 지금처럼 그 경로에서 위로 올라가며 가장 가까운 표식에서 멈춘다
  (`core.find_project(start)`). 확장의 활성 프로젝트(`/analytics use`), `--analytics-project`, 확장이 읽는 `AELIX_ANALYTICS_PROJECT`는 지금처럼
  그 디렉터리 자체를 판정한다(지금은 `project.marker.is_file()`). run 안의 `capture.ACTIVE.project`는 그 run의 루트다. 모두 찾은 디렉터리를 위 표로
  판정한다. 그래서 현재 계약 B·D의 탐색 규칙은 "어느 표식을 찾는가"만 바뀐다.
- 확장의 `_resolve_project`, 런처의 `find_project`, `bin/analytics`의 탐색도 같은 표식 집합으로 바꾼다. 셸·런처는 표식의 내용을 해석하지 않고 kit이 판정한다.
- ③부터 런처·`bin/analytics`는 찾은 디렉터리가 표식 없는 전환 목적지(`incomplete`)이면 `uv run --project` 대신 `uv run --no-project`로 kit을 불러
  판정만 받는다. 그 자리에서 uv가 `.venv/`를 만들면 이어 쓰기 검사가 낯선 항목으로 보기 때문이다(6.10절은 계획에 없는 `.venv/`를 무시하자고 권하지만 그것은 OQ16이다).
- uv 환경(`.venv/`, uv가 갱신하는 `uv.lock`)은 기록이 아니라 환경이라 게이트의 대상이 아니다(OQ13). 지금처럼 프로젝트 명령은 그 프로젝트의
  환경으로 돈다. 그래서 ③ 뒤 `lab.json` 프로젝트에서 사람이 `analytics status` 같은 읽기 명령을 부르면 uv가 그 프로젝트에 `.venv/`를 만들거나 맞지
  않는 `uv.lock`을 다시 쓸 수 있다(프로브 `probe-safety`). ADR-0005 결정 6의 "읽기 작업의 부수 쓰기" 목록에 uv 환경 동기화는 없다. 권장 답은
  이것을 기록의 부수 쓰기로 보지 않고, 현재 계약에 "읽기 전용은 uv 환경 동기화를 포함하지 않는다"를 적는 것이다(OQ13). 환경을 건드리지 않아야
  하는 곳은 전환 명령이 다루는 원본과 표식 없는 목적지이며, 둘 다 `--no-project`로 부른다(6.1절).

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
| `open_text`·`write_text`·`newlines_as_lf` | UTF-8(BOM 없음)·`\n` 텍스트(ADR-0007 결정 2) | `core` → ① `_platform`(3.2절) |
| `write_json(path, obj, *, platform, through_link=False)` | 임시 파일 + `AtomicFiles.replace`, 끝 줄바꿈 | `core` → ① `writer` |
| `append_line(path, line: bytes)` | 마지막 바이트가 `\n`이 아니면 `\n`을 먼저 쓰고, 줄과 `\n`을 한 번의 write로 | ③ 새 함수. `/1` writer의 같은 수정은 OQ8 |
| `FAMILIES` | `{"project": "analytics.project/2", "event": "analytics.event/2", "run": "analytics.run/2", "dataset": "analytics.dataset/1", "trust": "analytics.trust/1", "migration": "analytics.migration/1"}`. 쓰기 함수는 키를 받고 태그 문자열을 받지 않는다 | ③(`migration`은 ②) |
| `Layout`, `LegacyLayout` | 표식·관리 디렉터리 집합·run 입출력에서 빼는 디렉터리 집합(2.3절)·`workspace/`·`data/imported/`·`runs/<id>/visualizations/`·잠금 파일의 이름표. 옛 배치는 읽기와 ③ 전의 쓰기용 | ①에서 `LegacyLayout`, ③에서 `Layout` |

- 저장 이름의 개명이 `Layout` 한 곳과 reader를 고치는 일이 되게 한다. 경로 문자열을 쓰는 코드에 흩어 두지 않는다.
- 일반 쓰기는 fsync하지 않는다. 지금 kit 전체가 하지 않고 현재 계약 G가 원자성·복구를 약속하지 않는다. 내구 쓰기는 전환의 커밋 전에만 한다(6.4절).

### 4.5 `Writer` (③)

```python
class Writer:
    def __init__(self, root: Path, *, platform, origin: Origin, writer_version: str,
                 legacy_path=None, clock=None): ...  # legacy_path: injected by core (3.2, 5.4)
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
7. 잠금을 놓고 `Written{event, request_id, bindings, targets?}`를 돌려준다. `targets`는 상태를 바꾸는 참조(`resolves`, `supersedes`)의 대상마다
   잠금 안에서 재생한 `state_before`·`state_after`다(4.6절).

`locked()`는 잠금 안에서 파싱한 journal·재생 결과와 덧붙이기를 한 문맥에 묶는다. `update`가 이것을 쓰고, #19의 Report 저장(확인부터 완료
이벤트까지 journal 잠금, ADR-0006 7절)도 같은 문맥을 쓴다. 쓸 때마다 journal 전체를 읽으므로 M 규모(10,000줄)의 비용을 ③의 측정 기록에 남긴다.
느리면 크기·mtime으로 다음 seq를 기억하는 최적화를 같은 결과의 시험과 함께 넣는다.

### 4.6 같은 상태 변경 (③, 완료 조건 4)

- `update(target, expected, new, text)`: `locked()` 안에서 `relations.Replay`로 대상의 지금 상태를 계산한다. 대상이 하나로 있고, 지금 상태가
  `expected`이며, `transition_allowed`가 그 전이를 허용할 때만 `record_update` 줄을 쓴다. 아니면 `state_conflict`이고 `details`에 지금 상태,
  허용하는 전이, 대상 판정(`target_missing`·`target_ambiguous`), 그 상태를 만든 이벤트의 id와 `request_id`를 담는다. 아무것도 쓰지 않는다.
  reader와 같은 재생 코드라 둘이 갈리지 않는다(ADR-0006 확인 5).
- 정정은 `record(kind, text, refs=[{… "relation": "supersedes"}])`이고 기대 상태가 없다. 같은 기록의 정정이 둘이면 쓰고, 응답의 `targets`에서 `state_after`가
  `conflict`와 두 정정의 id를 보인다(ADR-0006 6절의 표, 최신 시각으로 고르지 않음).
- ③에서 공개 입력이 있는 상태 변경은 지금의 `--resolves eN`(질문·할 일을 닫는 `resolve` 기록)뿐이다. 이것은 `record_update`가 아니라 새 기록의
  relation이라 기대 상태가 없고, 지금처럼 쓴다. 다만 같은 질문을 두 번 닫는 일이 조용히 쓰이지 않도록 응답이 대상의 `state_before`·`state_after`를
  보인다. 이미 닫힌 대상이면 `state_before: resolved`와 그것을 닫은 이벤트의 id·`request_id`를 담는다. 거절로 바꾸지 않는 것은 지금 동작과 ADR-0006
  6절(새 기록은 기대 상태 없이 쓴다)을 지키기 위해서다.
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
| 그 밖 | `{type: visualization_version, token}` | `none`(reader는 `unknown`/`unresolved_name`). 지금 `refs.other`에 가는 문자열을 이 형태로 남기는 것은 15절 D25 |

- 기본 relation은 `cites`, basis는 `declared`다. 다른 relation을 받는 공개 입력은 OQ11과 함께 정한다.
- 최신 성공 버전은 `execution_status=ok`이고 완료 이벤트의 seq가 가장 큰 run의 것이다(ADR-0006 3절).
- 결박 결과는 응답의 `bindings`에 돌려준다. 확정 연결은 `author`뿐이며 `inferred`를 확정처럼 보이지 않는다(Q4).
- 참조는 이벤트당 24개(`MAX_EVENT_REFS`)를 그대로 둔다.

### 4.8 정본과 완료 이벤트, `min_writer_version` (③)

- run: `write_run`이 `analytics.run/2`를 `write_json`으로 쓴다(지금처럼 run 하나에 최대 세 번). run 전체는 run 잠금 안이다. 정본을 확정한 뒤 완료
  이벤트 `record("run", …, refs=[{type: run, run_id, relation: about}])`를 마지막에 쓴다.
- 가져오기: `data/imported/<name>.parquet`(임시+교체) → `meta.json`(`analytics.dataset/1`) → `import` 이벤트(`dataset_import` 참조 `about`).
  `meta.json`과 이벤트 사이가 끊기면 reader가 `incomplete`로 보인다(이미 `relations.ReadModel.imports`). parquet 교체 뒤 `meta.json` 전에 끊기면
  기록 없는 parquet만 남고 reader는 그것을 보이지 않는다. main과 같은 한계이고(현재 계약 G) #13은 바꾸지 않는다. 준비 상태를 따로 두는 설계는
  완료 조건에 없어 넣지 않는다.
- 맥락 동기화: 버전 파일(`.analytics/context/<stamp>_<sha12>.md`, 바이트 그대로) → `context` 이벤트. 비교는 `.analytics/context`와 `.lab/context`의
  마지막 버전이다(전환 직후 같은 CONTEXT.md를 새 버전으로 남기지 않는다).
- `min_writer_version`: init과 전환은 ③의 `KIT_VERSION`을 쓴다. 뒤의 PR이 새 쓰기 규칙(kind·전이 등)을 더하면 그 쓰기의 `requires`에 자기 버전을
  적고 writer가 잠금 안에서 표식을 올린다(ADR-0006 1.3). ③에서는 기록 예제(`normal`·`future-version`)와 시험으로만 이 경로가 돈다.
- 표식을 다시 쓸 때는 올리는 `min_writer_version`과 그 파일을 쓴 kit을 적는 `kit_version`만 바꾸고 나머지 필드(`project_id`, `title`, `created_at`,
  `migration`, 이 kit이 모르는 필드)는 값과 순서를 그대로 둔다. `migration`이 빠지면 매핑 추론(5.4절)이 조용히 멈추기 때문이다. 시험은 전환한
  프로젝트의 표식을 올린 뒤 `migration` 필드와 T11의 판정이 같은지 본다.

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
| `marker_invalid` | 새 | `analytics.json`이 JSON 객체가 아니거나 필수 필드·정규형이 맞지 않음, 또는 재시도 뒤에도 읽지 못함(`details.reason: unreadable`, 4.3절) | ① |
| `migration_incomplete` | 새 | 표식 없는 전환 목적지 | ② |
| `writer_too_old` | ADR-0006 | `min_writer_version` > kit | ③ |
| `migration_required` | 새 | ③ 뒤 `lab.json` 프로젝트의 일반 쓰기 | ③ |
| `state_conflict` | ADR-0006 | `record_update`의 대상·기대 상태·전이가 맞지 않음 | ③ |
| `text_too_long` | 새(OQ2) | 본문 상한 또는 직렬화한 줄의 상한 초과(2.9절) | ③ |
| `bad_kind` | 기존 | 모르는 kind, `gap`이면 `workflow_feedback` 안내 | ③ |
| `name_not_portable` | 기존 | 이식할 수 없는 `request_id` | ③ |
| `source_not_legacy` | 새 | 전환 원본이 `legacy`가 아님(`details.state`) | ② |
| `nested_project` | 새(OQ10) | 원본 아래에 다른 표식 또는 원본에 `.analytics/` | ② |
| `destination_invalid` | 새 | `details.reason`: `overlaps_source`(원본 루트나 복사가 읽는 바깥 링크 대상과 겹침, `details.with`)·`not_empty`(다른 원본의 전환 포함)·`inside_project`·`not_a_directory`·`name_collision` | ② |
| `destination_busy` | 새 | 다른 전환이 같은 목적지를 잡고 있음 | ② |
| `source_busy` | 새 | 원본의 run·journal 잠금을 다른 프로세스가 잡고 있음 | ② |
| `source_changed` | 새 | 복사 전후나 이어 쓰기 때 원본의 목록·해시가 다름, 또는 연 파일이 걷기 때 본 파일이 아님(바뀐 경로 목록) | ② |
| `source_unreadable` | 새 | 원본 파일을 읽지 못함(`details.reason`: `permission`·`in_use`·`link_target_missing`(OQ12)·`name_not_utf8`(OQ12와 같은 방식)) | ② |
| `destination_mismatch` | 새 | 목적지에 ledger가 모르는 항목, 해시가 다른 사본, 또는 게시하려는 최종 경로에 이미 있는 파일(`details.reason: occupied`) | ② |
| `destination_write_failed` | 새 | 목적지 쓰기를 운영체제가 거절(긴 경로, 예약 이름) | ② |
| `insufficient_space` | 새 | 필요한 바이트가 목적지의 빈 공간보다 큼(쓰기 전) | ② |

다시 부른 전환이 같은 원본의 이미 커밋된 목적지를 만나면 오류가 아니라 `ok`와 `already_migrated: true`다. 목적지의 표식 판정이 먼저이므로
두 표식·깨진 표식·모르는 표식이면 그 오류다(6.9절).

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

- run: `runs/*/run.json`이 `lab.run/1`이든 `analytics.run/2`이든 읽는다. 한 run의 Visualization은 `relations`의 한 함수
  `run_visualizations(run) -> list[dict]`가 두 형태(`charts[]`의 `id`, `visualizations[]`의 `visualization_id`)에서 같은 보기(`id`, 논리 키, 제목,
  파일, 첨부 데이터, `sha256`·`data_sha256`(`/2`에만), 원래 필드 이름)로 돌려준다. run 기록의 `charts`·`visualizations`를 직접 읽는 곳은 모두 이
  함수로 바꾼다. 지금(`09d22c4`) 직접 읽는 곳: `relations`(Visualization 버전·참조 판정), `core`(`chart_index`·`list_runs`의 수, status·context의
  목록과 글), Workspace(`report.py`의 run 페이지·목록·문서화되지 않은 그림), `runner.compare_runs`(재실행 비교, 2.6절)와 run 뒤 경고, 확장의 run
  결과 글과 PNG 첨부(`extension/aelix_analytics.py`의 run 결과 줄·첨부할 그림), 개발 도구(`tools/model_eval.py`의 `new_charts` 검사·run 요약·
  Visualization id 수집, `tools/eval_fixtures.py`, `tools/platform_smoke.py`, `tools/usage_report.py`의 `run_items(truth.run, "charts")`와 이름을
  변수로 받는 `run_items(record, scope)`·`REPLY_ID`의 `charts`).
- 재실행 비교의 Visualization별 결과는 `relations`의 다른 한 함수 `run_reproduction(run) -> dict | None`이 `/1`의 `reproduction.charts`와 `/2`의
  `reproduction.visualizations`에서 같은 보기(`of`, `verdict`, `inputs_same`, Visualization id → 값, 출력 경로 → 값)로 돌려준다. 이것을 직접 읽는 곳은
  (`09d22c4`) 확장의 run 결과 줄(`extension/aelix_analytics.py`의 `repro.get("charts", {})`)과 Workspace run 페이지의 재실행 비교(`report.py`의 같은 식)다.
  둘이 이 함수를 쓰지 않으면 `/2` 재실행에서 모든 Visualization의 비교가 조용히 빠져, `mismatch` 판정만 있고 그 원인인 Visualization이 보이지 않는다.
- ①이 두 함수와 함께 "run 기록의 `charts`와 `reproduction`의 `charts`를 이 두 함수 밖에서 읽지 않는다"는 정적 검사(AST: 문자열 `"charts"`를
  키로 쓰는 `get`·첨자, 그리고 위 `run_items`처럼 이름을 인자로 받는 읽기의 호출 인자)를 더해 새 소비자가 한 형태만 읽는 일을 막는다(S13). `/1`
  이벤트의 `refs.charts`처럼 run 기록이 아닌 같은 이름은 검사의 허용 목록에 함수 이름으로 적는다. 이름을 변수로 받는 읽기는 이 검사가 잡지
  못하므로 `tools/usage_report.py`의 그 읽기는 ①에서 두 함수를 쓰게 바꾼다.
- 데이터셋: `data/imported`·`data/raw`·`data/derived`를 걷는다(`core.list_datasets`, `relations`).
- 관리·제외 디렉터리(③, 새 형식 프로젝트에 처음 쓰는 PR): run의 입출력 판정, 스크립트 위치 검사(`runner._in_managed_dir`·`_in_managed_dir_here`,
  `core.IGNORED_DIRS`), 경로 정체성의 관리 디렉터리 철자 예외(`core.MANAGED_DIRS`를 쓰는 `Project`의 판정)는 표식이 고른 배치의 집합을 쓴다(2.3절, S14).
- CONTEXT 버전: `.analytics/context` 다음 `.lab/context`(이미 `relations.CONTEXT_DIRS`).
- 보조 로그: Workspace의 도구 사용표는 `.analytics/telemetry.jsonl`과 `.lab/telemetry.jsonl`을 함께 센다.

### 5.4 옛 경로의 추론 대응과 전환 매핑 (②)

- PR #76은 `Project.legacy_identity`로 kit 0.5.0 전 run이 기록한 경로를 지금 루트 기준의 정체성 경로로 대응하고 `basis: inferred`로만 보인다
  (`relations.read(…, legacy_path=)`). 대응은 그 run이 옛 kit의 것일 때만 하고, 대응한 경로가 지금 있거나 어떤 run의 출력일 때만 받는다.
- 전환하면 두 가지가 사라진다. 루트가 바뀌어 원본 루트 아래의 옛 절대 경로가 목적지 루트로 대응되지 않는다. 그리고 원본에서 경계였던 링크가
  목적지에서는 보통 파일·디렉터리가 되어(6.6절) 경계표가 없다. 지금 옛 run이 기록한 `/ext/data/x.parquet`는 원본에서 `data -> /ext/data` 경계를 지나
  `data/x.parquet`(inferred)로 읽히는데(`Project.legacy_identity` → `identity_of_real` → 경계표, `tests/test_relations.py`의 해당 사례), 목적지에는 그
  경계가 없어 추론이 사라진다. 그대로 두면 T11(전환 전후 `relations.read`가 같음)이 거짓이다.
- 그래서 ②는 표식의 `migration` 필드로 `mapping.json`(6.7절)을 찾아 전환한 프로젝트의 `legacy_path`를 이렇게 만든다. 옛 kit의 run(그 run의
  `env.kit_version`이 `PATH_IDENTITY_SINCE` 전)이 기록한 경로 문자열 하나에 대해:
  1. 절대 경로가 `root.from`의 철자 가운데 하나와 같거나 그 아래이면 나머지를 **지금** 루트 아래의 상대 경로로 바꾼다.
  2. 1에 해당하지 않는 절대 경로가 `links[].target_real`이나 그 별칭(`target_spellings`) 가운데 하나와 같거나 그 아래이면 그 링크의 경로(`links[].path`)에
     나머지를 붙인 상대 경로로 바꾼다. 원본에서 경계표가 하던 일을 매핑이 한다.
  1·2의 "아래"는 글자 접두가 아니라 경로 구성 요소 단위다. `core._under`처럼 둘 다 `plain_path`로 접은 뒤 같거나 구분자 경계에서 이어질 때만
  아래로 본다. 그래서 `/ext/data2/x`는 `/ext/data`의 아래가 아니다. 철자가 여럿이면 가장 긴 것을 먼저 맞춘다.
  3. 1·2에서 얻었거나 처음부터 상대 경로인 것이 `.lab/`으로 시작하면 접두 대응(`.lab/` → `.analytics/`)을 적용한다. 옛 run이 `.lab/…`을 상대 경로로
     기록한 경우도 대응한다.
  4. 그 결과를 지금의 `Project.legacy_identity`(지금 루트의 철자와 링크)로 판정한다. 어느 것에도 해당하지 않으면 `legacy_identity`만 쓴다.
  PR #76의 조건은 그대로다: 옛 kit의 run에만 하고, 0.5.0 이후 run이 같은 경로 문자열을 기록했으면 대응하지 않으며(`relations.ReadModel._mapped`),
  대응한 경로가 지금 있거나 어떤 run의 출력일 때만 받고, `basis: inferred`로만 보인다. 목적지를 나중에 옮기거나 원본·옛 링크 대상을 지워도 대응은
  지금 루트 기준이라 같다. 기록은 고쳐 쓰지 않는다(ADR-0006 9절 마지막 항목).
- 0.5.0 이후 run의 상대 경로는 전환 뒤에도 같은 경로라 대응이 필요 없다. 0.5.0 이후 kit은 경계를 지난 경로를 이미 정체성 경로(`data/x.parquet`)로
  기록했다. 0.5.0 이후 run의 프로젝트 밖 절대 경로는 그대로 밖이다.
- `mapping.json`이 없거나 해시가 표식·보고서와 다르면 대응하지 않고 status가 그 사실을 보인다(조용히 추론하지 않는다).
- 시험(T11): 원본에서 `data -> /ext/data`인 옛 run의 입력을 전환 뒤 목적지에서 읽어 원본과 같은 `inferred` 판정이 나오는지, 목적지를 다른 경로로
  옮기고 `/ext/data`를 지운 뒤에도 같은지, 옛 run이 상대 경로 `.lab/…`을 기록한 경우와 0.5.0 이후 run이 같은 문자열을 기록한 경우(대응하지 않음).

### 5.5 kit마다 보이는 것

| kit | `lab.json` 프로젝트 | ③이 만든 `analytics.json` 프로젝트 | 두 표식 | 표식 없는 전환 목적지 |
|---|---|---|---|---|
| ① 전(0.5.0 이하) | 읽기·쓰기 | 찾지 못함(`no_project`). 위쪽에 `lab.json`이 없게 전환이 막는다(init은 OQ9) | `lab.json`으로 봄(P5) | `no_project` |
| ①·② | 읽기·쓰기(`/1`) | 읽기, 쓰기 `project_too_new` | `marker_conflict` | ① `no_project`, ② `migration_incomplete` |
| ③ 이후 | 읽기만, 쓰기 `migration_required` | 읽기·쓰기(`min_writer_version` 확인) | `marker_conflict` | `migration_incomplete` |

---

## 6. 복사 전환

### 6.1 명령과 실행 위치

- 명령: `analytics migrate --to DEST [--dry-run]`. 원본은 다른 명령처럼 `AELIX_ANALYTICS_PROJECT` 또는 작업 디렉터리에서 가장 가까운 표식의
  프로젝트다. kit 명령은 `python -m aelix_analytics --project SRC migrate --to DEST [--dry-run]`이고 JSON 한 줄 규칙은 다른 명령과 같다.
- 원본 없이 부르기: 찾은 프로젝트가 없거나, 찾은 디렉터리가 목적지 자신(표식 없는 전환 목적지 안에서 부름)이면 원본이 주어지지 않은 호출이다.
  이 호출은 목적지가 원본을 다시 읽지 않는 상태일 때만 받는다: ledger에 `source_checked`가 있는 이어 쓰기, `committed`인데 표식이 없는 목적지,
  같은 전환으로 커밋된 목적지(`already_migrated`). 원본은 ledger의 `start`가 적은 것으로 보고 보고서·응답에 `source: not_given`과 그 경로를 적는다.
  그 밖의 상태(새 전환, 재확인 전의 이어 쓰기)이면 지금처럼 `no_project`(목적지 자신이면 `source_not_legacy(incomplete)`)다. 원본이 주어졌는데
  목적지에 있는 ledger의 원본과 다른 위치이면 ledger의 어느 단계에서든 `destination_invalid(not_empty)`(다른 원본의 전환)다. 그래서 원본을 지우거나 옮긴 뒤에도 이미 확인한
  전환을 끝내고 신뢰 항목을 채울 수 있다(6.9–6.10절, 15절 D28).
- 런처·`bin/analytics`는 `init`과 같은 방식으로 kit을 부른다: `uv run --no-project --python 3.12 --quiet python -m aelix_analytics …`(PYTHONPATH =
  실행 루트). 원본의 환경(`uv run --project SRC`)으로 부르지 않는다. 프로브(macOS, uv 0.11.19): `uv run --project DIR`은 `DIR/.venv`와 `DIR/uv.lock`을
  만들었고 맞지 않는 `uv.lock`을 다시 썼다. `--frozen --no-sync`도 `.venv/`를 만들었다. 프로젝트 디렉터리 안에서 `uv run --no-project`는 아무것도
  만들지 않았다. 목적지의 환경으로도 부르지 않는다(표식 전에 `.venv/`가 생긴다).
- 전환은 stdlib만 쓴다. `__main__`의 `migrate`는 분석 라이브러리를 import하지 않는다(지금 `init`과 같다).
- 커밋 뒤 런처는 목적지 환경을 `uv sync --project DEST --locked`로 준비한다. 프로브: `--locked`는 맞는 `uv.lock`을 바꾸지 않았고, 맞지 않는
  `uv.lock`에서는 종료 1로 실패하며 그 파일을 바꾸지 않았다(실패해도 `.venv/`는 만들었다). 실패해도 전환은 끝났고, 응답이 따로 알리며 다음
  명령이 지금처럼 환경을 준비한다.
- 모델 도구와 `/analytics migrate`는 만들지 않는다. ADR-0005 결정 5는 "명시적인 명령으로만 전환한다"고 정했고(누가 부르는지는 정하지 않았다),
  기본 모델 도구는 같은 여섯 역할이다(용어집 §2). 수백 MB의 복사가 aelix 프로세스를 붙잡지 않게 하는 이유도 있다. 확장의 거절 글은 사람이
  터미널에서 부르라고 안내한다(7절).
- 에이전트가 bash 도구로 `analytics migrate`를 부를 때(kit이 `actor=agent`·`declared`로 알아봄, 4.2절) 받을지 거절할지는 입력이 정하지 않았다.
  전환은 프로젝트를 만들고 그 경로를 신뢰 목록에 더하므로 소유자 질문 OQ14다. 권장 답은 받고 ledger·보고서에 `actor=agent`·`declared`로 남기는
  것이며, 이 설계의 나머지는 그 답을 전제로 쓴다(OQ14).

### 6.2 사전 검사 — 아무것도 쓰기 전

검사의 순서(새 전환, 다시 부르기, `--dry-run` 모두):

1. 목적지를 읽기만 하고 판정한다. 표식이 있으면 `marker_state`로(6.9절), 표식이 없고 전환 기록이 있으면 ledger의 상태로(6.10절), 둘 다 없으면
   새 전환이다. 잠금을 잡지 않고 아무것도 쓰지 않는다.
2. 원본 쪽 검사(아래 표의 "원본" 행)는 원본을 다시 읽을 상태, 곧 새 전환과 `source_checked` 전의 이어 쓰기에서만 한다. `source_checked` 뒤의
   이어 쓰기, `committed`인데 표식이 없는 목적지, `already_migrated`는 원본을 읽지 않으므로 원본 쪽 검사를 하지 않는다. 그동안 원본이 바뀌었거나
   없거나 바깥 링크 대상의 디스크가 빠져 있어도 막지 않는다. 원본이 주어지지 않은 호출(6.1절)은 이 상태들에서만 받는다.
3. 목적지 쪽 검사(아래 표의 "목적지" 행)는 표식이 없는 목적지에 늘 한다. 표식이 있는 목적지는 6.9절의 판정만 한다. 공간 검사는 남은 바이트로
   한다: 새 전환은 계획 전체, 이어 쓰기는 `copied`가 아직 없는 계획 파일의 바이트이고, 둘 다 여유(남은 바이트의 1% + 64 MiB)를 더한다.
4. 1단계(6.3절)에서 목적지 잠금을 잡은 뒤 1을 잠금 안에서 다시 한다. 결과가 잠금 밖의 판정과 다르면 아무것도 더 쓰지 않고, 잠금을 쥔 채
   그 판정으로 2·3을 다시 한다.

원본 쪽 검사는 잠금 없이 원본을 한 번 걷는다(해시 없음). 4단계가 잠금 안에서 다시 걷고 5단계가 그 결과로 같은 검사를 다시 한다. 원본 쪽
검사를 하는 상태이면 다시 부른 전환과 `--dry-run`도 부를 때마다 처음부터 검사한다(그 사이 링크·마운트가 바뀔 수 있다).

| 쪽 | 검사 | 실패 |
|---|---|---|
| 원본 | 원본의 판정이 `legacy` | `source_not_legacy`(`details.state`: `none`·`current`·`unknown`·`invalid`·`conflict`·`incomplete`) |
| 원본 | 원본 아래(제외 목록 밖)에 `lab.json`·`analytics.json`이 없고 원본에 `.analytics/`가 없음 | `nested_project`(경로 목록, OQ10) |
| 원본 | 복사가 읽는 실제 위치의 집합 R(원본 루트의 실제 경로, 따라갈 바깥 링크마다 그 대상의 실제 경로, 6.6절)의 어느 원소 r에 대해서도 목적지가 r과 같지 않고, r 안에 있지 않고, r을 품지 않음 | `destination_invalid(overlaps_source)`. `details.with`에 겹친 r과 그것을 읽게 한 링크의 경로 |
| 원본 | 바깥을 가리키는 링크의 대상이 지금 있음 | `source_unreadable(reason=link_target_missing)`과 링크 경로·링크 글자 목록(OQ12) |
| 원본 | 원본의 모든 이름을 UTF-8로 쓸 수 있음(POSIX가 UTF-8이 아닌 바이트를 넘기는 짝 없는 surrogate가 없음) | `source_unreadable(reason=name_not_utf8)`과 경로 목록(이름 바이트의 base64와 이스케이프한 글자). OQ12와 같은 방식 |
| 원본 | `.lab/` 아래 이름이 목적지의 새 관리 이름(`.analytics/migration/`·`publications/`·`run.lock`·`workspace.lock`·`migration.lock`)과 겹치지 않음 | `destination_invalid(name_collision)`(겹친 쌍) |
| 목적지 | 목적지의 위쪽 디렉터리에 `lab.json`·`analytics.json`이 없음 | `destination_invalid(inside_project)` |
| 목적지 | 목적지가 없거나 빈 디렉터리, 또는 6.10절의 이어 쓸 목적지. 빈 디렉터리의 판정은 init과 같다: 루트에 `.DS_Store`만 있으면 빈 것으로 보고, init과 전환이 같은 상수를 쓴다(ADR-0005 결정 5 "init과 같다") | `destination_invalid(not_empty)`(남은 항목), 파일이면 `not_a_directory`. OQ4의 권장 답을 받지 않아 운영체제 메타 파일도 복사할 때, 목적지의 `.DS_Store`가 계획이 복사할 경로(원본 루트의 `.DS_Store`)이면 `not_empty`와 그 파일이다(복사 중 `occupied`로 늘 끝나는 일을 미리 거절) |
| 목적지 | 목적지 쪽 빈 공간 ≥ 남은 바이트 + 여유(남은 바이트의 1% + 64 MiB) | `insufficient_space` |

- 같은 위치인지는 끝까지 푼 `(st_dev, st_ino)`로, `st_ino`가 0이면 끝까지 푼 경로로 판정한다(ADR-0006 9절). 목적지는 끝까지 푼 경로(없으면 가장
  가까운 있는 조상을 풀고 나머지를 붙임)의 모든 조상을 r과, r의 모든 조상을 목적지와 `samefile`로 비교한다. 대소문자·NFD 별칭, 심볼릭 링크,
  junction, 같은 위치의 두 철자를 잡는다. R에 바깥 링크의 대상을 넣는 이유: `data -> /ext/data`인 원본에 `--to /ext/data/new`(또는 `/ext/data`,
  `/ext`)를 주면 원본 루트와는 겹치지 않지만, 1–2단계가 링크 대상 안에 `.analytics/`와 ledger를 만들고 4단계가 그것을 `data/new/…`로 다시 읽어
  원본의 이름공간에 파일을 더하며 7단계는 늘 `source_changed`로 끝난다. 복사가 읽는 곳에 쓰는 일이라 ADR-0005 결정 4·5와 S1을 어긴다(T2).
- 대소문자·유니코드 정규화로 겹치는 이름의 판정은 여기서 하지 않는다. 목적지 파일 시스템이 이름을 접는지는 목적지에 시험 이름을 만들어 봐야
  알 수 있으므로 2단계 뒤에 판정하고 5단계가 계획에 적용한다(6.3절). `--dry-run`은 목적지에 쓰지 않으므로 "목적지가 이름을 접으면 겹칠 쌍"을
  보이고 판정하지 않았다고 적는다.
- 목적지에서 259자를 넘을 경로와 ADR-0003 결정 6의 이름 규칙을 어기는 원본 이름은 거절하지 않고 계획·보고서에 경고로 적는다. 운영체제가 실제로
  거절하면 그 파일에서 `destination_write_failed`로 멈추고 목적지는 표식 없이 남는다. 플랫폼 분기를 두지 않는다.

### 6.3 단계와 커밋 지점

```text
 DEST  [1 mkdir+dest lock] [2 ledger start, name probe] .... [5 plan] [6 copy] ..... [8 verify] [9 transform, mapping, report] [10 flush] [11 marker] [12 trust]
 SRC                                        [3 lock run.lock -> lock ....................... 7 re-check] release
                                            (no wait; same order as the old runner)
```

ledger 먼저: 이 전환이 목적지에 만드는 파일은 모두 그 이름을 ledger 줄(또는 ledger가 sha256으로 묶은 계획)에 먼저 적은 뒤 `O_CREAT|O_EXCL`로
만든다. 내용을 미리 알 수 있는 파일(`.gitignore`의 새 내용, 표식)은 바이트나 sha256도 먼저 적는다. 예외는 ledger보다 먼저 있어야 하는 것(1단계의
목적지 디렉터리·`.analytics/`·`migration.lock`, 2단계의 `.analytics/migration/`·전환 디렉터리·ledger 자신. 이것들만 있는 목적지는 새 전환이다,
6.10절)과 이 전환의 디렉터리 안에서 다시 쓸 수 있는 파일(계획·매핑·보고서·제안과 그 임시 파일, 디렉터리 전체가 이 전환의 것)뿐이다. 그래서
어느 지점에서 끊겨도 목적지의 모든 항목은 ledger·계획이 적은 것, 이 전환의 디렉터리, 무시하는 항목(6.10절) 가운데 하나이고, 이어 쓰기가
정의한 상태로 판정할 수 있다(6.10–6.11절).

1. 목적지를 만든다(없으면 `mkdir`, 있으면 6.2절의 빈 디렉터리인지 확인). `DEST/.analytics/`를 만들고 `DEST/.analytics/migration.lock`을 기다리지
   않고 잡는다. 못 잡으면 `destination_busy`. 잡은 뒤 6.2절 순서의 4대로 목적지의 판정(6.9–6.10절)을 이 잠금 안에서 다시 한다.
2. `DEST/.analytics/migration/<migration_id>/`를 만들고 `progress.jsonl`에 `start` 줄을 쓴다: kit 버전, `Origin`, 원본·목적지의 정규 경로와 철자,
   R(6.2절), 잡을 원본 잠금 파일과 그 존재 여부, 이름 접힘 시험에 쓸 파일 이름. **원본 잠금보다 먼저 쓴다.** 잠금이 원본에 잠금 파일을 만들 수
   있으므로 그 사실을 남길 곳이 먼저 있어야 한다. 디렉터리를 만든 뒤 `start` 줄이 끝까지 써지기 전에 끊기면 내용 없는 전환 디렉터리(ledger가
   없거나 비었거나 끊긴 첫 줄뿐)가 남고, 다시 부르면 그것을 지우고 새 전환으로 본다(6.10절). 그다음 이 전환의 디렉터리 안에 `start`가 적은 이름의
   시험 파일(ASCII 대소문자와 NFC 글자가 든 이름)을 만들고, 대소문자만 다른 이름과 NFD 이름으로 `lstat`해 목적지가 대소문자·정규화를 접는지
   실제로 판정한다. 시험 파일을 지우고 `probed`(대소문자·정규화 각각 접음·구별)를 쓴다. 목적지 아래 디렉터리는 이 전환이 만들므로 같은 동작이라고
   본다(Windows의 디렉터리별 대소문자 구별 설정이 새 하위 디렉터리에 이어지는지는 확인하지 않았다, 18절).
3. 원본의 `.lab/run.lock`, 그다음 `.lab/lock`을 기다리지 않고 잡는다(`FileLocks.try_exclusive`, 6.12절). 옛 runner가 run 잠금을 먼저 잡고 그 안에서
   journal 잠금을 잡으므로 같은 순서다. 다투면 `source_busy`(run 잠금이면 `status=running`인 run의 id와 pid). ledger에 `locked`와 각 잠금 파일을
   새로 만들었는지를 쓴다. 두 경우는 잠금을 잡을 수 없다: 원본에 `.lab/`이 없을 때(디렉터리를 만들지 않는다. 지금 포트의 `exclusive`는 부모
   디렉터리를 만든다), 그리고 다툼이 아닌 이유로 잠금 파일을 만들거나 열지 못할 때(읽기 전용 원본·매체의 `EROFS`·`EACCES` 등, 포트의
   `LockUnavailable`). 이때 잠금 없이 진행하고 ledger와 보고서에 `locks: not_taken`과 이유를 적으며 그동안 생긴 옛 쓰기는 7단계의 비교가 잡는다는 것이
   권장 답(15절 D10)이다. ADR-0005 결정 5는 "복사하는 동안 원본의 journal·run 잠금을 잡는다"고 적었으므로 이 진행은 결정의 글자를 좁히고, 옛 kit의
   쓰기를 기다리게 하지 못해 그런 쓰기가 오면 전환이 `source_changed`로 끝난다. 그래서 소유자 질문 OQ15다. 받지 않으면 두 경우는 거절한다.
4. (잠금 안) 원본을 걷는다: 항목마다 저장된 철자의 상대 POSIX 경로, 크기, `mtime_ns`, 종류, `(st_dev, st_ino)`. 해시는 아직 계산하지 않는다.
   제외 목록(OQ4)과 잠금 파일은 따로 적는다. 6.2절의 원본 쪽 검사를 이 결과로 다시 한다.
5. 계획 `plan.json`(원본 경로 → 목적지 경로, 만들 디렉터리, 제외, 링크 처리, 예정된 변환, 파일마다의 임시 이름, 새 `project_id`)을 이 전환의
   디렉터리에 쓰고 그 sha256과 `project_id`를 ledger의 `plan` 줄에 적는다. `project_id`(무작위)는 처음 계획에서 정하고, 이어 쓰기가 다시 세운
   계획도 처음 `plan` 줄의 값을 쓴다(보고서와 표식이 같은 값을 담는다). 2단계의 판정으로 목적지가 접는 방식에서 겹치는 계획 경로 쌍이 있으면
   `destination_invalid(name_collision)`(겹친 쌍)로 멈춘다. 목적지가 대소문자·정규화를 구별하면 그런 쌍도 그대로 복사한다. 임시 이름은 같은
   디렉터리의 어떤 계획 이름과도(목적지가 접는 방식으로도) 겹치지 않게 고른다. 원본에 `.mig-….part` 같은 이름이 있어도 충돌하지 않는다.
6. 복사(6.4절). 계획이 적은 디렉터리를 만들고, 파일마다 ledger에 `begin`(대상 경로, 임시 이름, 걷기 때의 크기·`(st_dev, st_ino)`) → 임시 파일을
   `O_EXCL`로 새로 만들어 복사하며 sha256 계산 → 임시 파일 핸들로 flush → 최종 이름이 없을 때만 원자 게시(`AtomicFiles.publish_new`) → 권한·시각 →
   `copied`(바이트 수·sha256). 링크를 지원하지 않는 목적지에서는 게시가 `direct_intent`·`direct` 줄을 쓰는 직접 쓰기다(6.12절). `lab.json`의 사본
   `source-marker.json`도 이 단계에서 같은 방식으로 복사한다(잠금 안에서 본 원본과 같은 바이트). 디렉터리의 권한은 그 안을 다 쓴 뒤 옮기되 루트와
   `.analytics/`에는 옮기지 않는다(6.4절).
7. (잠금 안) 원본 재확인: 다시 걷고 모든 파일을 다시 해시해 복사 때 해시와 비교한다. 다르면 `source_changed`(바뀐·생긴·사라진 경로)로 끝내고
   ledger에 `failed`를 쓴다. 같으면 매핑에 쓸 철자를 이 잠금 안에서 정한다: `root.from`(원본의 정규 경로, 사용자가 준 철자, 원본 기록
   `run.json`·`meta.json`에 나타난 절대 경로 가운데 `samefile`로 원본 루트로 판정된 접두)과 링크마다의 `target_spellings`(6.7절). 그리고
   `source_checked`를 쓴다: 원본 inventory digest, 시각(`source_checked_at`), 복사한 journal의 경계(바이트 수, 줄 수, 마지막 seq,
   `relations.Journal`로 센 `damaged`·`recovered`·`unsupported` 수), 원본 표식의 sha256, 위의 철자. 그리고 원본 잠금을 놓는다. **원본 잠금은
   4–7단계 동안만 잡는다.** 이 줄 뒤로 전환은 원본도, 바깥 링크의 대상도 다시 읽지 않는다. 8–12단계가 쓰는 원본의 사실은 모두 계획과 ledger에
   있다.
8. 사본 검증: 계획한 모든 사본을 다시 읽어 sha256을 비교하고, 계획에 없는 항목이 없는지 본다(이 전환의 파일과 6.10절의 무시하는 항목은 제외).
   다르면 `destination_mismatch`. ledger에 `verified`.
9. `.gitignore` 변환(6.13절): 사본의 바이트에서 바꾼 내용을 메모리에 만들고 `transform_begin`(경로, 임시 이름, 전·후 sha256)을 **먼저** 쓴다.
   그다음 임시 파일을 `O_EXCL`로 만들어 쓰고 flush한다. 사본이 아직 전 해시인지 다시 확인하고, 사본이 읽기 전용이면 그 속성을 잠시 풀어
   (Windows의 교체는 읽기 전용 대상에서 실패한다) 이 전환의 사본 위에 교체(`AtomicFiles.replace`)한 뒤 원래 권한을 새 파일에 다시 주고
   `transformed`를 쓴다. 그다음 `mapping.json`(계획과 `source_checked` 줄로 만든다), 스크립트 제안(목적지의 `scripts/` 사본을 읽는다),
   `report.json`·`report.md`를 이 전환의 디렉터리에 쓰고 `reported`(보고서 sha256)를 쓴다. 원본을 읽지 않는다.
10. 내구 쓰기: 9단계에서 만든 파일은 쓴 핸들로 flush했고 사본은 6단계에서 게시 전에 flush했다. 여기서는 사본·새 파일이 있는 모든 디렉터리를
    `flush_dir`한다. ledger에 `durable`.
11. (목적지 잠금 안) 표식 바이트를 메모리에 만들고 `marker_begin`(임시 이름, 표식 바이트 전체와 sha256)을 **먼저** 쓴다. 그다음 임시 파일을
    `O_EXCL`로 만들어 쓰고 flush한 뒤 `analytics.json`이 없을 때만 게시한다(`publish_new`). 이미 있으면 덮어쓰지 않고
    `destination_mismatch(occupied)`. 표식의 `migration.report_sha256`이 보고서를 묶는다. **이 게시가 커밋 지점이다.** 게시 뒤 목적지 디렉터리를
    `flush_dir`하고 ledger에 `committed`(커밋 시각)를 쓴 다음에 응답한다.
12. 목적지를 새 신뢰 목록에 더한다(6.14절). ledger에 `trusted`. 실패해도 전환은 끝났고 응답이 알린다. 다시 부르면 이 단계만 한다(6.9절).

목적지 디렉터리를 다른 이름으로 만든 뒤 마지막에 이름을 바꾸는 방식은 쓰지 않는다. Windows는 안에 열린 핸들이 있는 디렉터리의 이름을 바꾸지
못하고, 다른 볼륨으로는 옮길 수 없으며, ADR-0005 결정 5가 "같은 목적지에서 잇는다"고 정했다. 표식이 없는 디렉터리는 어떤 kit에도 프로젝트가 아니므로
표식 게시 하나로 원자적인 커밋이 된다. 결정 5의 "목적지에는 먼저 전환 보고서를 만들고"는 2단계의 ledger와 5단계의 계획이고, 9단계의
`report.json`이 그것을 완결한다.

### 6.4 복사 규칙

- 이진 모드로 1 MiB 단위 스트리밍, 메모리 고정. 텍스트 모드·줄 끝 변환 없음(ADR-0007 결정 6). CRLF journal도 바이트 그대로다.
- 원본 열기: 걷기 때 정규 파일이었던 것만 연다. 바깥 링크의 대상도 열기 전에 `stat`으로 정규 파일인지 보고(FIFO가 대상이면 `open`에서 멈춘다),
  POSIX에서는 `O_NONBLOCK`이 있으면 더해 연다(기능 확인, 정규 파일에는 영향이 없다). 연 핸들의 `fstat`이 정규 파일이고 걷기 때 본
  `(st_dev, st_ino)`와 같을 때만 읽는다. 다르면 `source_changed`(그 경로). 걷기와 열기 사이에 FIFO·장치로 바뀐 항목, Windows에서 Linux가 만든 이름
  (`con`·`aux`, 끝의 `.`)이 장치나 다른 파일을 여는 경우도 이 비교가 잡는다. 플랫폼 분기가 없다. Windows에서는 실행하지 않았다(18절).
- 임시 파일은 같은 디렉터리의 짧은 이름 `.mig-<sha8(경로)>-<n>.part`다(긴 원본 이름에 접미사를 붙이면 이름 길이 한계를 넘을 수 있다). 이름은 5단계가
  계획 이름과 겹치지 않게 고르고 ledger의 `begin` 줄에 먼저 적는다. `O_CREAT|O_EXCL`(`open(…, "xb")`)로 만들어 이미 있는 파일을 열지 않는다.
- 게시는 "없을 때만 만드는" 원자 연산인 새 포트 메서드 `AtomicFiles.publish_new(temp, final)`다(6.12절). 최종 경로에 그 사이 다른 파일이 생겼으면
  덮어쓰지 않고 `destination_mismatch(occupied)`로 멈추며 그 파일과 임시 파일을 그대로 둔다. 그래서 목적지의 최종 경로에는 늘 이 전환이 게시한 완전한
  사본이나 남이 만든 파일 가운데 하나만 있고, 중단되면 남는 것은 ledger에 적힌 임시 파일(링크를 지원하지 않는 파일 시스템에서는 `direct_intent`
  줄이 이름을 적은 최종 이름의 파일, 6.12절)뿐이다. `AtomicFiles.replace`(덮어쓰는 교체)는 이 전환이 가진 파일에만 쓴다: 이 전환의 디렉터리 안
  파일(계획·매핑·보고서를 다시 쓸 때), 9단계의 `.gitignore`(직전에 이 전환의 사본 해시인지 확인).
- 권한 비트와 수정 시각은 게시 뒤 가능한 만큼 옮긴다(`os.chmod`·`os.utime`). 디렉터리는 그 안을 다 쓴 뒤에 옮긴다. 다만 목적지 루트와
  `.analytics/`(원본의 루트와 `.lab/`)에는 권한을 옮기지 않는다. 9·11단계와 그 뒤의 kit이 두 곳에 쓰므로, 읽기 전용 원본의 권한을 옮기면 전환이
  스스로 끝나지 못하거나 새 프로젝트에 쓸 수 없다. 옮기지 않은 권한은 보고서 `warnings`에 적는다. 보장은 바이트뿐이고 확장 속성·ACL은 옮기지
  않는다고 보고서에 적는다.
- 읽기 전용 사본의 교체: 9단계의 `.gitignore` 사본이 읽기 전용이면 교체 전에 쓰기 가능으로 바꾸고(`os.chmod(…, stat.S_IWRITE)`) 교체한 새 파일에
  원래 권한을 다시 준다. 그 사이에 끊기면 `.gitignore`는 전 해시 그대로이고 권한만 다르며, 이어 쓰기는 해시로 판정하므로 같은 결과다.
- Windows의 읽기 전용 파일: 사본의 flush는 게시 전에 임시 파일의 쓰기 핸들로 한다. Windows의 `FlushFileBuffers`는 쓰기 권한이 있는 핸들이 필요해서
  읽기 전용 사본을 나중에 다시 열어 flush할 수 없기 때문이다. 읽기 전용 속성은 게시 뒤에 옮기므로 임시 파일은 늘 쓸 수 있다. 이어 쓰기가 이 전환의
  임시 파일을 지울 때 읽기 전용이면 먼저 쓰기 가능으로 바꾼다(`os.chmod(…, stat.S_IWRITE)`, POSIX에서도 해가 없는 같은 호출). Windows에서 실행하지
  않았다(18절).
- 원본과 바이트를 나누는 hardlink·clone을 쓰지 않는다(ADR-0005 결정 5). 원본 안의 hardlink 두 이름은 두 사본이 된다. POSIX의 `publish_new`가 쓰는
  `os.link`는 이 전환의 임시 파일과 그 최종 이름 사이의 것이고 곧바로 임시 이름을 지우므로 원본과 아무것도 나누지 않는다.
- 복사 순서는 경로의 코드 포인트 순이다. 계획·보고서·측정이 같은 순서를 쓴다.
- 내구 쓰기: 새 포트 메서드 `AtomicFiles.flush_handle(fh)`·`AtomicFiles.flush_dir(path)`(6.12절). 표식 뒤 전원이 끊겨도 표식이 가리키는 사본이
  디스크에 있게 하려는 것이다. 사본이 표식보다 늦게 디스크에 닿으면 사용자가 원본을 지운 뒤 잃을 수 있다. 일반 쓰기에는 쓰지 않는다(4.4절).
- 프로세스 중단과 전원 손실을 구별한다. S2·S6과 6.11절의 결과는 프로세스 중단에 대한 보장이며 `os._exit` 시험으로 확인한다. 전원 손실 뒤의
  내구성은 목표이고 보장이 아니다: 시험하지 않았고(측정 기록의 `not_run`), Windows에서는 디렉터리를 열어 flush할 방법을 확인하지 않았으며(6.12절),
  `os.replace`·`os.rename`·`os.link`는 write-through가 아니다. 그래서 Windows에서 표식 게시 직후 전원이 끊기면 표식이 없을 수 있고(다시 부르면 이어
  쓰기), 드물게 표식은 있는데 사본의 이름이 디스크에 없을 수 있다. 다시 부르기의 대조(6.9절)가 그것을 `missing`으로 알린다.

### 6.5 복사 범위와 목적지의 배치

| 원본 | 목적지 | 방식 |
|---|---|---|
| `lab.json` | `.analytics/migration/<id>/source-marker.json` | 6단계에 잠금 안에서 바이트 그대로, 이름만 다름. `lab.json`이라는 이름으로 두지 않는다(그 디렉터리에서 옛 kit이 프로젝트로 찾아 쓴다) |
| `.lab/context/*.md`, `.lab/telemetry.jsonl`, `.lab/queries.jsonl`, `.lab/adhoc/`, `.lab/demo/`와 `.lab/` 아래의 그 밖의 항목 | `.analytics/` 아래 같은 상대 경로 | 바이트 그대로. 매핑의 접두 대응 `.lab/` → `.analytics/` |
| `.lab/cache/`, `.lab/lock`·`run.lock`·`report.lock` | 없음 | 재생성 가능·잠금. 보고서 `excluded`(OQ4) |
| `report/`, `.venv/`, `__pycache__/` | 없음 | 재생성 가능·환경. 보고서 `excluded`(OQ4) |
| 운영체제 메타 파일 `.DS_Store`·`Thumbs.db`·`desktop.ini`(어느 깊이에서나) | 없음 | 운영체제가 다시 만드는 보기 설정. 보고서 `excluded`(`os_metadata`, OQ4). 원본 전후 비교에서도 빠지므로 전환 중에 Finder·탐색기가 원본을 열어도 `source_changed`가 되지 않고, 목적지의 같은 이름 파일이 계획 경로를 차지하지 않는다 |
| 루트의 `.git`이 파일(git worktree의 `gitdir:` 포인터) | 없음 | 복사하면 목적지가 원본 worktree의 관리 디렉터리(index·HEAD)를 함께 쓴다. 보고서 `not_copied`(`git_worktree_pointer`)와 경고(OQ4) |
| 그 밖의 모든 것(`journal.jsonl`, `CONTEXT.md`, `runs/`, `data/raw/`, `data/derived/`, `scripts/`, `pyproject.toml`, `uv.lock`, 디렉터리 `.git/` 등) | 같은 상대 경로 | 바이트 그대로 |
| `.gitignore` | 같은 경로, 끝에 블록 덧붙임 | 6.13절 |
| — | `analytics.json` | 새로 씀(11단계) |
| — | `.analytics/migration/<id>/`의 `progress.jsonl`·`plan.json`·`mapping.json`·`report.json`·`report.md`·`proposals/` | 새로 씀 |

- 목적지에는 `.lab/`을 만들지 않는다. 그래서 새 kit이 쓰는 `.analytics/telemetry.jsonl`·`queries.jsonl`·`context/`가 옛 기록에 이어지고, 목적지의 관리
  디렉터리가 하나다.
- 제외 목록은 원본 전후 비교에서도 뺀다. 원본에 디렉터리 `.git/`이 있으면 목적지에 저장소 사본이 생긴다는 것과, git이 추적하던 `.lab/` 파일이
  목적지에서는 `.analytics/`로 옮겨져 git에 "지워짐·새 파일"로 보인다는 것을 보고서가 알린다.
- `.analytics/migration/`은 git이 무시하지 않는다(프로젝트의 이력이다). 캐시 정리가 지우지 않는다.

### 6.6 링크와 특수 파일 (OQ3, OQ12)

| 원본 항목 | 처리 |
|---|---|
| 밖을 가리키는 파일 링크, 디렉터리 링크·junction(대상이 있음) | 대상의 바이트를 링크의 경로에 보통 파일·디렉터리로 복사한다. 디렉터리는 경로 정체성의 트리 조회처럼 한 번 내려간다. 사전 공간 검사와 6.2절의 R에 넣고, 매핑의 `links`에 링크 경로·대상의 실제 경로와 철자·처리를 적는다(5.4절) |
| 밖을 가리키는 링크인데 대상이 지금 없음(마운트하지 않은 외부 디스크 등) | 사전 검사에서 거절한다: `source_unreadable(reason=link_target_missing)`과 목록. 사람이 대상을 연결하거나 링크를 치운 뒤 다시 부른다(OQ12) |
| 안을 가리키는 상대 링크로, 글자가 루트 안에 머무는 것 | 같은 링크 글자로 목적지에 다시 만든다. 그러면 목적지에서도 같은 글자가 목적지 안의 같은 상대 위치로 풀려 원본에 닿지 않고, 정체성 경로가 원본과 같다. 대상이 없으면(원본에서도 깨진 링크) 같은 조건에서만 같은 글자로 다시 만든다. 만들 수 없으면(Windows 권한) 파일 링크는 바이트 복사(매핑 `links`에 적음), 디렉터리 링크는 복사하지 않고 경고 |
| 안을 가리키지만 글자가 루트 밖을 지나거나 다른 링크를 지나는 상대 링크(예: `/a/proj`의 `x -> ../proj/y`) | 안을 가리키는 절대 링크와 같이 처리한다(아래 행). 같은 글자로 다시 만들면 형제 목적지 `/a/proj-new`에서 `x`가 원본의 `/a/proj/y`로 풀려, 목적지의 읽기·쓰기가 원본의 바이트에 닿는다(ADR-0005 결정 5의 원본 보존과 바이트를 나누지 않음에 어긋남) |
| 안을 가리키는 절대 링크 | 파일은 바이트 복사(매핑 `links`에 적음), 디렉터리는 내려가지 않고 복사하지 않음(대상의 실제 위치가 복사되므로 기록은 그 위치로 풀린다). 대상이 없으면 복사하지 않음 |
| 루트·조상·관리 디렉터리로 풀리는 링크 | 복사하지 않음 |
| hardlink | 경로마다 따로 복사 |
| FIFO·소켓·장치 파일과 그것을 가리키는 링크 | 복사하지 않음(바이트가 없다). 판정은 `stat`과 연 핸들의 `fstat`(6.4절) |

- 안과 밖은 링크 대상의 실제 위치로 판정하고, 대상이 없으면 링크 글자를 원본 루트의 철자들과 비교해 판정한다.
- 상대 링크를 다시 만들지는 따로 글자로 판정한다. 링크 글자를 링크가 있는 디렉터리에 붙이고 앞에서부터 `.`·`..`를 글자로 접을 때 어느 단계에서도
  원본 루트 위로 나가지 않고, `..` 앞의 구성 요소가 원본에서 링크가 아니어야 "글자가 루트 안에 머문다"(POSIX에서 링크 뒤의 `..`는 링크 대상의
  부모라 글자로 접은 결과와 다르다). 대상이 없는 상대 링크도 같다. 글자가 루트 밖으로 나가면 다시 만들지 않고, 접은 경로가 루트 철자 아래이면
  안을 가리키는 절대 링크처럼(대상이 없으므로 복사하지 않음), 아니면 대상이 없는 바깥 링크(OQ12)로 처리한다.
- 링크마다 경로·링크 글자·처리를 보고서의 `links`에, 복사하지 않은 것은 `not_copied`에 사유와 함께 적는다. `--dry-run`은 바깥 링크와 그 대상의
  상태(있음·없음)를 목록의 맨 앞에 보인다. 같은 규칙이 모든 OS에서 돈다.

### 6.7 참조 매핑

파일을 옮기지 않으므로 매핑은 작다. 보고서가 `mapping.json`의 sha256을 담는다.

```json
{"schema": "analytics.migration/1", "record": "mapping", "migration_id": "20261008T010203Z-a1b2c3d4",
 "root": {"from": ["/Users/me/projects/sales-review", "/Users/me/Projects/Sales-Review"],
          "to": "/Users/me/projects/sales-review-analytics"},
 "prefixes": [{"from": ".lab/", "to": ".analytics/"}],
 "renamed": [{"from": "lab.json",
              "to": ".analytics/migration/20261008T010203Z-a1b2c3d4/source-marker.json"}],
 "links": [{"path": "data", "kind": "directory", "text": "/Volumes/ext/data",
            "target_real": "/Volumes/ext/data",
            "target_spellings": ["/Volumes/ext/data", "/Volumes/EXT/data"],
            "treatment": "copied_as_directory"}],
 "kinds": [{"from": "gap", "to": "workflow_feedback", "where": "reader"}]}
```

- `root.from`: 원본의 정규 경로, 사용자가 준 철자, 원본 기록(`run.json`·`meta.json`)에 나타난 절대 경로 가운데 `samefile`로 원본 루트로 판정된 접두.
  `samefile` 판정에는 원본이 있어야 하므로 7단계가 원본 잠금 안에서 정해 `source_checked` 줄에 적고, 9단계는 그 줄을 옮겨 적기만 한다(원본을
  지운 뒤 이어 써도 같은 매핑이 나온다). `root.to`는 기록일 뿐이고 reader는 지금 루트로 대응한다(5.4절).
- `prefixes`·`renamed`: 위치가 바뀐 것(6.5절). 옛 run의 `charts/`·`data/raw/` 경로는 같은 경로에 사본이 있어 대응이 필요 없다.
- `links`: 보통 파일·디렉터리로 바꾼 링크마다(6.6절) 경로, 링크 글자, 대상의 실제 경로, 그 대상을 가리키는 철자(링크 글자, 원본 기록에 나타난
  절대 경로 가운데 `samefile`로 그 대상으로 판정된 접두), 처리. 철자는 `root.from`과 같이 7단계에서 정해 `source_checked` 줄에 적은 것이다(바깥
  링크의 대상 디스크가 그 뒤 빠져도 같다). 다시 만든 상대 링크는 목적지에서도 경계라 적지 않는다.
- `kinds`: reader 규칙의 기록이다. 기록 자체는 고치지 않는다.

### 6.8 보고서와 ledger

`report.json`(`analytics.migration/1`, `record: "report"`)은 9단계에 쓰고 표식이 그 sha256을 묶으므로, 커밋 전에 정해진 값만 담는다. 커밋 시각과
flush·표식 단계의 시간은 ledger의 `durable`·`committed` 줄과 응답에 있다.

| 필드 | 내용 |
|---|---|
| `migration_id` | `<UTC yyyymmddThhmmssZ>-<무작위 8자>`, `[A-Za-z0-9._-]`(ADR-0003 결정 6) |
| `kit_version`, `started_at`, `source_checked_at`, `actor`, `actor_basis`, `means` | 실행 정보. `source_checked_at`은 원본 잠금 안에서 원본을 마지막으로 확인한 시각이다 |
| `source` | 정규 경로·철자, 표식(가족·바이트·sha256·보존 경로), 복사한 journal의 경계(바이트 수·줄 수·마지막 seq, `damaged`·`recovered`·`unsupported` 수, `relations.Journal`로 셈), inventory digest(전·후), 파일 수·바이트, 잠금(경로·원래 있었는지·잡은 시간 ms, 또는 `not_taken`과 이유) |
| `destination` | 정규 경로, `project_id`(`plan` 줄의 값), 대소문자·정규화를 접는지(2단계의 판정) |
| `files[]` | `path`, `to`(다를 때만), `bytes`, `sha256`(원본 전·후와 사본이 같으므로 하나), `link`(있을 때) |
| `transformed[]` | `.gitignore`의 전·후 sha256, 더한 줄, diff 경로 |
| `added[]` | 전환이 만든 파일과 sha256 |
| `excluded[]` | 경로, 사유(`environment`·`workspace`·`cache`·`lock`·`os_metadata`), 파일 수, 바이트 |
| `not_copied[]`, `links[]`, `warnings[]`, `leftover_temp[]` | 특수 파일·worktree 포인터, 링크 처리, 긴 경로, 이식할 수 없는 이름, 지우지 않고 남긴 이 전환의 임시 파일(6.10절) |
| `mapping` | `mapping.json`의 경로와 sha256 |
| `proposals` | `.gitattributes`, `scripts.diff`, `.gitignore`(링크이거나 없을 때) |
| `checks` | `source_before_after: equal`, `copies: equal`, 비교한 파일 수 |
| `notes` | 원본은 `lab.json` 프로젝트로 남고 `source_checked_at`(journal 경계 뒤) 뒤에 원본에 쓴 기록은 이 사본에 없다는 것(ADR-0005 결정 5), CRLF 기록을 고치지 않았다는 것(ADR-0007 결정 6), `.git/` 사본 여부와 git에 보일 바뀜 |
| `timings` | 계획·복사·재확인·검증 단계의 ms |

- `report.md`는 같은 내용의 영어 요약이다(용어집 §7). 사용자 경로와 파일 이름은 그대로 둔다. 응답 JSON은 요약과 경로다.
- `progress.jsonl`은 덧붙이기만 하는 ledger다. 줄 종류: `start`, `probed`, `locked`, `plan`, `begin`, `direct_intent`, `direct`, `copied`,
  `source_checked`, `verified`, `transform_begin`, `transformed`, `reported`, `durable`, `marker_begin`, `committed`, `trusted`, `failed`(오류 코드·경로).
  모든 줄에 쓴 시각 `at`이 있고 보고서의 `timings`는 이 시각으로 계산한다. 덧붙일 때 끊긴 줄 규칙(마지막 바이트가 LF가 아니면 LF 먼저)을 쓴다.
  읽을 때 JSON으로 읽을 수 없는 줄은 쓰다 끊긴 덧붙이기로 보고 건너뛴다: 그 줄이 적으려던 일은 일어나지 않았다고 본다. ledger 먼저 원칙(6.3절)
  때문에 그 줄 뒤의 파일 만들기는 시작되지 않았으므로 이 판정이 맞다. JSON으로 읽히지만 모르는 줄 종류는 6.10절대로 `destination_mismatch`다.
- ledger 줄은 flush하지 않는다. 이어 쓸 때 사본을 모두 다시 해시하므로 ledger가 디스크보다 앞서도 틀린 판정이 나오지 않는다. `failed`는 그 시도의
  결과일 뿐 상태를 닫지 않는다. 다시 부르면 6.10절로 다시 판정한다.
- 보고서는 ledger·계획·사본으로 다시 만들 수 있게 쓴다: `project_id`는 `plan` 줄, 원본의 사실은 `start`·`source_checked` 줄, 시간은 줄의 `at`이다.
  이어 쓰기가 보고서를 다시 쓰면 그때마다 새 `reported` 줄(새 sha256)을 덧붙인다. 보고서와 비교하는 판정(6.9–6.10절)은 늘 마지막 `reported` 줄과
  비교한다.
- 기록 schema에 `analytics.migration/1`의 세 레코드 정의를 더하고(②) `tests/test_record_contract.py`가 예제와 맞는지 본다.

### 6.9 `--dry-run`과 다시 부르기

- `--dry-run`은 읽기만 한다. 잠금도 잡지 않으므로 잠금 파일·목적지 디렉터리도 만들지 않는다. 결과는 그 순간의 추정이며 그렇게 적는다.
  보이는 것: 원본 판정, 목적지 상태(`new`·`resumable`·`committed`·`refused`와 이유, 아래의 다시 부르기 판정과 같은 규칙), 바깥 링크와 그 대상의
  상태(맨 앞), 복사할 파일 수·바이트, 제외와 이유, 링크·특수 파일 처리, 목적지가 이름을 접으면 겹칠 쌍(판정하지 않음), `.gitignore`에 더할 줄,
  스크립트 제안 수, 필요한 바이트와 빈 공간, 실행 중 run(읽기 모델의 `running`). 연속성 설계 §7의 "변환 계획과 영향을 먼저 보여 준다"에 맞춘다.
  종료 코드는 진행할 상태면 0, 거절될 상태면 그 오류 코드와 1(계획은 `details`).
- 다시 부르기(그리고 `--dry-run`)는 원본 쪽 검사와 6.10절의 이어 쓰기 표보다 먼저 목적지의 `marker_state`(4.3절)를 판정한다(6.2절 순서의 1).
  표식이 있는 목적지에는 다시 전환하지 않는다(결정 5). 아래 표의 "같은 원본"은 원본이 주어지지 않았거나(6.1절) 주어진 원본이 ledger `start`의
  원본과 같은 위치(`samefile`)라는 뜻이다.

  | 목적지의 판정 | 결과 |
  |---|---|
  | `conflict`(두 표식) | `marker_conflict`. 신뢰 목록을 건드리지 않는다 |
  | `invalid` | `marker_invalid` |
  | `unknown` | `project_too_new` |
  | `legacy`(목적지에 `lab.json`) | `destination_invalid(not_empty)` |
  | `current`, 표식의 `migration.id`에 맞는 ledger가 있고 같은 원본이며, ledger에 `committed`가 있거나 `marker_begin`의 sha256이 지금 표식 바이트와 같음 | `already_migrated`(아래). `committed`가 없었으면 먼저 쓴다(6.11절 F12) |
  | `current`, 같은 원본, `committed`가 없고 표식 바이트가 `marker_begin`과 다름(F12 뒤 새 kit이 표식을 다시 씀, 4.8절) | 표식의 `project_id`가 `plan` 줄의 값과 같고 `migration` 필드가 `marker_begin`이 적은 표식의 것과 같으면 `already_migrated`와 경고 `marker_rewritten`, 그리고 `committed`를 쓴다. 다르면 `destination_mismatch` |
  | `current`, 주어진 원본이 그 ledger의 원본과 다른 위치 | `destination_invalid(not_empty)`, `details`: 다른 원본의 전환으로 만든 프로젝트. "원본이 바뀌었다"고 보고하지 않는다 |
  | `current`, `migration` 필드가 없거나 그 ledger가 없음 | `destination_invalid(not_empty)` |
  | `incomplete`(표식 없음, 전환 기록 있음)·`none` | 6.10절 |

- `already_migrated`: 복사하지 않는다. `ok`와 `already_migrated: true`를 돌려주고, 신뢰 항목이 없으면 더하며(12단계만), 사본을 보고서와 다시 대조한
  결과(`same`·`changed`·`missing`, journal처럼 덧붙이는 파일은 보고서가 적은 길이까지의 앞부분, 보고서 자체의 해시가 표식과 다르면 `changed`)와
  `source_checked_at` 뒤 원본에 생긴 변경(보고서의 journal 경계 뒤 줄 수와 seq 범위, inventory와 다른 파일 수)을 알린다. 이 대조는 읽기만 한다.
  ADR-0005 결정 5의 "전환 뒤 원본에 쓴 기록의 안내"가 이것이다. 원본이 주어지지 않은 호출(6.1절)이면 그 부분은 `source: not_given`과 ledger의
  원본 경로다. 원본 쪽 사전 검사와 공간 검사를 하지 않는다(6.2절).
- `already_migrated`가 목적지에 쓰는 것은 신뢰 항목, 빠진 `committed` 줄, 그리고 남은 표식 임시 파일의 정리뿐이다. `marker_begin`이 이름을 적은
  임시 파일이 남아 있으면(POSIX `publish_new`의 `os.link`와 `unlink` 사이, 또는 링크를 지원하지 않는 목적지의 직접 쓰기 뒤에 끊김) 그것이 표식과
  같은 파일(같은 `(st_dev, st_ino)`)이거나 바이트가 같은 사본일 때만 지운다. 아니면 지우지 않고 경고한다. `--dry-run`은 이 셋을 하지 않고 할 일로만
  보인다.
- 시험(T21): 두 표식·깨진 표식·모르는 표식·`lab.json`을 둔 목적지, 다른 원본의 커밋된 목적지, 같은 원본의 커밋된 목적지(신뢰 항목 있음·없음)를 실제
  호출과 `--dry-run`으로 각각 부른다. 같은 원본의 커밋된 목적지는 원본을 지우고 원본 없이 부른 경우, 바깥 링크 대상을 치운 경우, 같은 디스크의 빈
  공간이 원본 크기보다 작은 경우도 부른다(모두 `already_migrated`). F12 뒤 표식의 `min_writer_version`을 올린 목적지(`marker_rewritten`), 표식 임시 파일이
  표식과 같은 inode로 남은 목적지(정리됨). 기대: 위 표의 결과, 신뢰 목록은 `already_migrated`의 누락 보충 말고 바뀌지 않음, 목적지·원본의
  `tree_digest` 같음(표식 임시 파일의 정리 제외).

### 6.10 이어 쓰기와 거절

목적지에 표식이 없고 전환 기록이 있으면 이어 쓰기 판정이다. 판정은 먼저 읽기만 하고(6.2절 순서의 1) 1단계의 목적지 잠금 안에서 다시 한다.
무시하는 항목은 계획에 없는 `.venv/`·`__pycache__/`와 운영체제 메타 파일(`.DS_Store`·`Thumbs.db`·`desktop.ini`, 어느 깊이에서나)이다. 이 무시는
결정 5의 "목적지에 전환 보고서가 적은 파일만 있고 해시가 맞을 때만 잇는다"보다 넓게 받는 것이라 소유자 질문 OQ16이고, 이 목록이 권장 답(15절
D11)이다. 받지 않으면 이런 파일도 계획에 없는 항목이라 `destination_mismatch`다. 계획에 있는 경로를 이런 파일이 차지하고 있으면 무시하지 않고 그
경로의 게시가 `destination_mismatch(occupied)`가 된다(OQ4의 권장 답을 받으면 메타 파일은 계획에 없다).

| 목적지 상태 | 같은 명령을 다시 부르면 |
|---|---|
| 없음·빈 디렉터리, 또는 `.analytics/`·`migration.lock`·빈 `.analytics/migration/`만, 또는 그것에 더해 내용 없는 전환 디렉터리 하나(ledger가 없거나 비었거나 끊긴 첫 줄뿐이고 다른 항목이 없음, 2단계 중의 중단)만 | 새 전환. 내용 없는 전환 디렉터리는 2단계가 만든 것이므로 지우고 새 id로 시작한다 |
| 내용 있는 전환 디렉터리가 둘 이상 | `destination_mismatch` |
| 원본이 주어졌고 ledger의 원본과 다른 위치 | `destination_invalid(not_empty)`(다른 원본의 중단된 전환) |
| 전환 디렉터리 없이 다른 항목이 있음 | `destination_invalid(not_empty)` |
| ledger의 마지막 단계가 `start`·`probed`·`locked`(계획 전) | 목적지에 이 전환의 디렉터리(ledger, `start`가 이름을 적은 시험 파일, 쓰다 끊긴 `plan.json`)와 무시하는 항목만 있으면 시험 파일을 지우고 2단계의 판정부터. 아니면 `destination_mismatch` |
| `plan`·`begin`·`direct_intent`·`direct`·`copied`(7단계 전) | ① 목적지의 모든 항목이 계획이 적은 디렉터리, `copied`가 적은 사본(해시가 같음), `begin`만 있는 경로의 최종 이름에 있는 완전한 파일(아래 조건), ledger 줄이 이름을 적은 임시 파일, `direct`가 적은 같은 정체성의 파일, 이 전환의 디렉터리, 무시하는 항목 가운데 하나이고, ② 원본 잠금을 다시 잡은 뒤 `copied`인 파일마다 지금 원본의 바이트가 그 사본과 같으면 잇는다. `begin`만 있는 경로의 최종 이름에 완전한 파일이 있으면 지금 원본과 해시가 같을 때만 사본으로 인정한다. `direct_intent`만 있고 `direct`가 없는 경로의 최종 이름에 파일이 있으면 이 전환이 만든 것인지 알 수 없으므로 `destination_mismatch(occupied)`(그 경로, R20)다. 그리고 지금 원본으로 남은 계획을 다시 세워 4단계부터 잇는다. ①이 아니면 `destination_mismatch`(그 경로들), ②가 아니면 `source_changed`(그 경로들)와 "이 목적지를 지우고 새로 시작하거나 다른 목적지를 쓰라". 자동으로 지우지 않는다 |
| `source_checked`·`verified` | ①과 같고 모든 계획 사본이 `copied`이면 8단계부터 잇는다. **원본을 다시 보지 않고** 원본 쪽 사전 검사도 하지 않는다(6.2절). `source_checked`가 기록한 스냅샷(inventory digest, `copied` 줄의 해시, 매핑의 철자)이 그 전환이 확인한 원본이다. 원본이 그 뒤 바뀌었거나, 없거나(원본 없이 부름, 6.1절), 바깥 링크의 대상이 빠져 있어도 막지 않는다 |
| `transform_begin`(`transformed` 없음) | ①과 같고 `.gitignore`가 그 줄의 전 해시나 후 해시 가운데 하나여야 한다. 그 줄이 이름을 적은 임시 파일은 내용과 관계없이(없음·일부·전부) 지운다. 전 해시면 9단계의 변환부터(새 `transform_begin`), 후 해시면 `transformed`를 쓰고 매핑부터 잇는다. 둘 다 아니면 `destination_mismatch` |
| `transformed`·`reported`·`durable` | ①과 같고 `.gitignore`가 후 해시여야 한다. 보고서가 있고 마지막 `reported`의 해시와 같으면 그대로, 없거나 다르면 이 전환의 파일이므로 ledger·계획·사본으로 다시 쓰고 새 `reported` 줄을 쓴다(6.8절). 그다음 10단계부터 |
| `marker_begin`, 표식 없음 | 위와 같고 `marker_begin`이 이름을 적은 표식 임시 파일을 내용과 관계없이(없음·일부·전부) 지운 뒤 11단계부터(새 `marker_begin`) |
| `committed`, 표식 없음(사람이 지움, 또는 Windows 전원 손실, 6.4절) | 보고서가 마지막 `reported`의 해시와 같고 모든 사본이 보고서의 해시와 같을 때만, `marker_begin`이 적은 표식 바이트를 새 `marker_begin`(같은 바이트)과 11단계의 방식으로 다시 게시하고 `already_migrated`. 원본은 보지 않는다(원본 없이 불러도 된다, 6.1절). 아니면 `destination_mismatch` |
| 표식이 있음 | 6.9절 |

- 정의한 상태는 위 표의 행뿐이다. ledger 줄의 순서가 맞지 않거나(예: `copied` 없이 `source_checked`) JSON으로 읽히지만 모르는 줄이 있으면
  `destination_mismatch`이고 아무것도 지우지 않는다. JSON으로 읽을 수 없는 줄은 끊긴 덧붙이기로 건너뛴다(6.8절). 그래서 어느 줄을 쓰다 끊겨도
  그 앞 줄의 상태로 판정되고, 그 줄이 적으려던 파일은 아직 만들어지지 않았다(6.3절의 ledger 먼저).
- 7단계 전의 이어 쓰기 결과는 그 이어 쓰기의 잠금 구간에서 본 원본과 같다. 아직 복사하지 않은 파일의 바뀜은 새 계획이 담고, 이미 복사한 파일의
  바뀜은 덮어쓸 수 없으므로 거절한다. 그래서 결정 5의 "복사 전후로 원본이 다르면 실패"와 "보고서가 적은 파일만 있고 해시가 맞을 때만 잇는다"를 함께
  지키면서 중단 사이의 무관한 원본 변경 때문에 이어 쓰기를 막지 않는다.
- `source_checked` 뒤에는 원본을 다시 보지 않는다. 원본 잠금을 놓은 뒤 옛 세션이 원본 journal에 덧붙이는 일(6.12절이 예상한다)이 이미 확인한
  사본의 이어 쓰기를 막지 않는다. 그 기록은 사본에 없고 보고서의 경계와 6.9절의 다시 부르기가 알린다.
- kit은 목적지의 사용자 파일을 지우거나 덮어쓰지 않는다. 지우는 것은 ledger가 먼저 이름을 적고 이 전환이 만든 것뿐이다: `begin`·`transform_begin`·
  `marker_begin` 줄이 이름을 적은 임시 파일, `start`가 이름을 적은 2단계의 시험 파일, `direct` 줄만 있고 `copied`가 없는 최종 이름의 파일(지금
  정체성이 그 줄과 같을 때), 내용 없는 전환 디렉터리(ledger가 없거나 비었거나 끊긴 첫 줄뿐이고 다른 항목이 없음). `transform_begin`·`marker_begin`의
  임시 파일은 그 줄이 바이트나 해시를 적은 다시 만들 수 있는 파일이라 내용과 관계없이 지운다. `begin`만 있고 `copied`가 없는 임시 파일은 그 경로를
  이번에 다시 복사해 게시한 뒤에 지운다. 새 계획에 그 경로가 없으면(원본에서 사라짐) 다시 만들 수 없는 바이트일 수 있으므로 지우지 않고 보고서의
  `leftover_temp`에 적으며, 검증은 그것을 이 전환의 파일로 본다.
- Windows에서는 프로세스가 죽은 뒤 잠금이 풀리는 데 시간이 걸릴 수 있다(미확인). 바로 다시 부르면 `source_busy`·`destination_busy`가 잠깐 날 수 있고
  오류 글이 잠시 뒤 다시 부르라고 안내한다.

### 6.11 중단 지점별 결과

프로세스 강제 종료(`os._exit`, SIGKILL)를 각 지점에 넣었을 때다. 전원 손실은 시험하지 않는다(6.4절). 모든 행에서 원본의 inventory digest는
같다(잠금 파일 제외).

| 지점 | 목적지에 남는 것 | 목적지 안의 새 kit 명령 | 옛 kit | 다시 부른 `migrate` |
|---|---|---|---|---|
| F1 1단계 뒤, 또는 2단계에서 `start` 줄이 끝나기 전 | 빈 `.analytics/`, `migration.lock`, 빈 `.analytics/migration/` 또는 내용 없는 전환 디렉터리(ledger 없음·빈 ledger·끊긴 첫 줄) | `migration_incomplete` | `no_project` | 새 전환(내용 없는 전환 디렉터리는 지움) |
| F2 2단계의 `start` 줄 뒤 | ledger `start`, 시험 파일(있거나 없음) | 같음 | 같음 | 시험 파일을 지우고 2단계의 판정부터 |
| F3 3단계 뒤 | + 원본에 잠금 파일(생겼다면, ledger에 적힘) | 같음 | 같음 | 이어 쓰기(계획 전 행: 2단계의 판정부터, 원본 잠금을 다시 잡음) |
| F4 5단계 뒤 | + `plan.json` | 같음 | 같음 | 이어 쓰기(다시 걷기부터) |
| F5 파일 복사 중(`begin` 뒤) | + 임시 파일 하나(없음·일부·전부, `begin`에 적힘) | 같음 | 같음 | 그 경로를 다시 복사해 게시한 뒤 임시 파일을 지움 |
| F6 게시 뒤 `copied` 전 | + 완전한 사본(POSIX에서 link와 unlink 사이면 임시 이름도) | 같음 | 같음 | 지금 원본과 해시가 같으면 인정, 임시 이름은 지움 |
| F7 7단계 중(`source_checked` 전) | 사본 전부 | 같음 | 같음 | 원본 잠금을 다시 잡고 이어 쓰기(원본 비교부터) |
| F8 `source_checked`·`verified` 뒤 | 같음 | 같음 | 같음 | 8단계부터, 원본을 보지 않음(원본 쪽 검사 없음, 원본 없이 불러도 됨) |
| F9 `transform_begin` 뒤 `transformed` 전(임시 파일 만들기 전·쓰는 중·교체 전·교체 뒤) | + `.gitignore` 임시 파일(없음·일부·전부, `transform_begin`에 적힘), `.gitignore`는 전 또는 후 해시 | 같음 | 같음 | 임시 파일을 지우고 해시로 판정해 변환부터 또는 매핑부터 |
| F10 `transformed` 뒤(매핑·보고서를 쓰는 중), `reported`·`durable` 뒤 | + 매핑·보고서(쓰다 끊겼을 수 있음, 이 전환의 디렉터리 안) | 같음 | 같음 | 보고서가 마지막 `reported`와 다르면 다시 쓰고 새 `reported`, 그다음 10단계(flush)부터 |
| F11 `marker_begin` 뒤 게시 전(임시 파일 만들기 전·쓰는 중·게시 직전) | + 표식의 임시 파일(없음·일부·전부, `marker_begin`에 적힘) | 같음 | 같음 | 임시 파일을 지우고 표식부터 |
| F12 게시 뒤 `committed` 전 | 커밋된 프로젝트(POSIX에서 link와 unlink 사이면 표식 임시 파일도), ledger에 `committed` 없음 | `current`, aelix 자동 활성화 없음(status가 알림) | `no_project` | 표식 바이트가 `marker_begin`과 같으므로 `committed`를 쓰고 `already_migrated` + 신뢰 추가. 표식과 같은 inode인 임시 파일은 지움(6.9절) |
| F13 `committed` 뒤 12단계 전 | 커밋된 프로젝트, 신뢰 항목 없음 | 같음 | `no_project` | `already_migrated` + 신뢰 추가 |
| F14 12단계 뒤 | 완료 | `current` | `no_project` | `already_migrated` |
| F15 링크를 지원하지 않는 목적지의 직접 쓰기에서 `direct_intent` 뒤 `direct` 전 | 최종 이름에 파일이 없거나, `O_EXCL`로 만든 파일(정체성이 ledger에 없음) | `migration_incomplete` | `no_project` | 파일이 없으면 이어 쓰기. 있으면 이 전환의 것인지 알 수 없어 `destination_mismatch(occupied)`로 거절하고 그 경로를 알림(R20). 사람이 확인해 지운 뒤 다시 부른다 |
| F16 어느 ledger 줄을 쓰다 끊김 | 끝이 LF가 아닌 끊긴 줄 | 같음 | 같음 | 그 줄을 건너뛰고 앞 줄의 상태로 판정. 다음 덧붙이기가 LF를 먼저 씀 |

### 6.12 동시 쓰기, 잠금, 포트

| 상황 | 동작 |
|---|---|
| 옛 kit이 원본에서 run 중 | 3단계 `source_busy`(기다리지 않음) |
| 옛 kit의 `record`·맥락 동기화가 전환 중에 옴 | 4–7단계 동안 journal 잠금에서 기다린다. 옛 aelix의 프롬프트 직전 hook도 기다리므로 그동안 aelix가 멈춘 것처럼 보인다(13절 R1). 놓은 뒤의 기록은 사본에 없고 6.9절이 센다. 이어 쓰기를 막지 않는다(6.10절) |
| 잠금 없이 쓰는 옛 쓰기(telemetry·queries·캐시·inline 스크립트·사용자 편집) | 7단계 `source_changed`. 캐시·잠금 파일은 제외 목록이라 실패시키지 않는다 |
| 잠금 없이 진행한 원본(`.lab/` 없음, 잠금 파일을 만들 수 없음, OQ15)에 옛 kit이 씀 | 기다리게 하지 못하고 7단계 `source_changed`(3단계) |
| 새 kit의 쓰기가 원본에 옴 | ③ 뒤 판정에서 `migration_required`. 원본에 쓰지 않는다 |
| 새 kit이 표식 전의 목적지에 옴 | `migration_incomplete` |
| 두 전환이 같은 목적지로 | 1단계 `destination_busy` |
| 두 전환이 같은 원본에서 다른 목적지로 | 두 번째가 3단계 `source_busy` |
| 다른 프로세스가 전환 중 목적지의 계획 경로에 파일을 만듦 | 그 경로의 게시가 `destination_mismatch(occupied)`. 그 파일을 덮어쓰지 않는다(T19) |

포트 변경(ADR-0003 결정 1의 네 포트 안에서 메서드만 더함. 플랫폼마다 동작이 다른 것만):

- `FileLocks.try_exclusive(path)`: 잡으면 context manager, 다투면 `LockBusy`, 다툼이 아닌 이유로 잠금 파일을 만들거나 열지 못하면 `LockUnavailable`
  (운영체제 오류). 부모 디렉터리를 만들지 않는다. 잠금 파일이 있는데 쓰기로 열 수 없으면 읽기로 연다. POSIX는 `flock(LOCK_EX | LOCK_NB)`(읽기
  fd에도 된다. 프로브: macOS에서 다른 프로세스가 kit의 잠금을 잡은 동안 `EAGAIN`(35), 놓은 뒤 획득), Windows는 `msvcrt.locking(LK_NBLCK, 1)` 한 번
  (지금 `exclusive`와 같은 0번 바이트, 읽기 전용 핸들에서 되는지는 확인하지 않았다). 옛 kit의 `exclusive`와 같은 파일·같은 바이트 범위라 서로를 막는다.
- `AtomicFiles.publish_new(src, dst)`: `dst`가 없을 때만 원자적으로 `src`를 그 이름으로 만든다. 있으면 `FileExistsError`이고 아무것도 바꾸지
  않는다. POSIX는 `os.link(src, dst)` 뒤 `os.unlink(src)`, Windows는 `os.rename(src, dst)`(있으면 실패, 공유 위반은 `replace`와 같은 예산으로 재시도).
  목적지 파일 시스템이 링크를 지원하지 않으면(POSIX에서 FAT·exFAT 등의 `EPERM`·`ENOTSUP`, 확인하지 않음) `publish_new`은 아무것도 바꾸지 않고
  `LinkUnsupported`를 낸다. 그때 전환은 직접 쓰기로 바꾼다: ledger에 `direct_intent`(최종 경로)를 **먼저** 쓰고, 최종 이름을 `O_CREAT|O_EXCL`로
  만든 직후 그 파일의 `(st_dev, st_ino)`를 `direct` 줄에 적은 뒤 임시 파일의 바이트를 옮기고 flush한다. 끊기면 이어 쓰기가 `direct`의 정체성과 같은
  파일만 이 전환의 파일로 보고 지운다. `direct_intent`와 `direct` 사이(만들기와 정체성 기록 사이)에 끊겨 최종 이름에 파일이 남으면 이 전환의 것인지
  알 수 없으므로 거절한다(6.11절 F15, R20). 최종 이름에 반쯤 쓴 파일이 보일 수 있는 것은 이 경우뿐이고 표식 전이라 어떤 kit도 읽지 않는다.
- `AtomicFiles.flush_handle(fh)`·`AtomicFiles.flush_dir(path)`: POSIX는 파일 `fsync`(`F_FULLFSYNC`가 있으면 그것, 기능 확인)와 디렉터리 fd의 `fsync`,
  Windows는 쓰기 핸들의 `fsync`(`FlushFileBuffers`)이고 디렉터리는 할 일이 없다(디렉터리를 열어 flush하는 방법을 확인하지 않음). 오류를 숨기지 않는다.
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
  `lab.json` 프로젝트의 읽기)에만 쓴다. 옛 항목을 새 목록으로 옮기지 않는다. 명시적인 명령이 고른 경로만 새 목록에 들어간다: `/analytics use`
  (형식과 관계없이 그 경로), init, 전환의 목적지. 이전에 고르지 않은 경로를 자동으로 신뢰하지 않는다(ADR-0005 결정 5, 용어 점검 §5 9항). 지금도
  모델 도구 `analytics_init`이 에이전트가 고른 경로를 신뢰 목록에 더하므로(현재 계약 B) "사람이 고른 경로"로 좁히지 않는다. 에이전트가 부른 전환의
  목적지를 받을지는 OQ14다.
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
| CLI `status`·`context`·`query`·`open` | 읽기만. 캐시·`queries.jsonl`·맥락 동기화는 건너뜀. 이미 있는 `report/index.html`은 열리지만 갱신되지 않는다고 알림. 런처가 그 프로젝트의 환경으로 부르므로 uv가 `.venv/`를 만들거나 맞지 않는 `uv.lock`을 다시 쓸 수 있다(기록이 아닌 환경, OQ13) |
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
| 같은 기록의 두 정정(`supersedes`) | 쓴다(ADR-0006: 둘 이상이면 `conflict`) | 응답의 `targets`에 `state_after: conflict`, status·Workspace의 `conflict` | 예제 `conflict` |
| 잠금 밖에서 쓴 어긋난 갱신(더 새로운 writer, 손으로 쓴 줄) | 재생에서 적용하지 않음 | `mismatched_updates`(#12) | 예제 `conflict` |
| `record_update`를 쓴 뒤 응답 전에 중단하고 같은 요청을 다시 보냄 | 두 번째는 `state_conflict` | "그 상태를 만든 이벤트"의 `request_id`가 같아 호출자가 자기 쓰기임을 안다. 중복 방지 보장은 아니다(ADR-0006 8절) | T13 |
| 줄을 쓰다 중단 | 다음 writer가 LF를 먼저 씀 | 끊긴 줄은 `damaged`, 새 이벤트는 따로 읽힘. 끊긴 쓰기는 id를 돌려준 적이 없다 | 예제 `interrupted`의 `event_append`, 쓰기 중 kill |
| `/1` writer가 붙여 쓴 줄 뒤의 `/2` 쓰기 | 복구한 기록의 seq 다음 값 | 같은 id 두 줄은 `ambiguous` | 예제 `mixed-version` + 쓰기 |
| run의 정본 뒤 완료 이벤트 전 중단 | — | `recording_status=incomplete(no_completion_event)` | 완료 이벤트 직전 kill |
| 오래된 writer가 잠금 안에서 `writer_too_old`를 받음 | 완료 이벤트를 쓰지 않고 오류 | 정본만 있는 `incomplete`와 오류 응답 | T10 |
| 전환의 중단·충돌 | 6.10–6.12절 | `migration_incomplete`, `destination_busy`, `source_busy`, `source_changed`, `destination_mismatch` | T5–T7, T19 |
| 이미 닫힌 질문·할 일을 `--resolves`로 다시 닫음 | 쓴다(새 기록의 relation, 기대 상태 없음) | 응답의 `targets`에 `state_before: resolved`와 그것을 닫은 이벤트의 id·`request_id`(4.6절). 조용히 쓰이지 않는다 | T13 |

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
| ① 판정·읽기 | `writer.py` 등록(판정·게이트·헬퍼·`Origin`·오류 클래스), 계층 수준 변경, 가장 가까운 표식 탐색, 모든 진입점의 `admit`, `marker_conflict`·`marker_invalid`, `analytics.json` 디렉터리의 모든 쓰기 거절·부수 쓰기 건너뜀, `/2` 이벤트 표시, `analytics.run/2`·`analytics.dataset/1` 정의와 읽기(OQ5·D2·D4가 먼저 정해져야 함), Visualization의 한 보기(`relations.run_visualizations`)와 재실행 비교 결과의 한 보기(`relations.run_reproduction`), 모든 소비자의 전환, `Origin` 배관, (OQ8의 답에 따라) `/1` 덧붙이기 수정 | `analytics.json` 디렉터리에 아무것도 써지지 않음(T8) | 올림(새 거절) | 구현 리뷰 독립 1·Codex 1, 수정 diff 확인 1 | 있음(새 가족 정의, 진입점 행렬) |
| ② 전환 엔진 | `migration.py` 등록, 포트 메서드 셋과 계약 시험, 계획·ledger·매핑·보고서와 schema, `migration_incomplete`, 매핑 읽기, 고장 주입 시험. 명령·도구로 열지 않음 | 엔진은 시험에서만 `analytics.json`을 씀. ②의 kit은 그 목적지에 쓰지 않음(①의 게이트) | 그대로 | 같음 | 있음(전환 정확성·규모·중단 행렬) |
| ③ 새 writer·정책 | `Writer`, `WRITABLE` 뒤집기(`lab.json` → `migration_required`), 저장 이름 개명과 배치별 관리 디렉터리(2.3절), 새 형식 init·demo, `analytics migrate`, 신뢰 목록, `.gitignore`·템플릿, `min_writer_version`, 확장의 거절 글·`AELIX_ANALYTICS_MEANS`·aelix 프로세스 환경의 작성 주체와 `user_bash` 처리(OQ1), 현재 계약 전반, `STORED` 시험 뒤집기 | `lab.json` 디렉터리에 쓰지 않음, `analytics.json` 디렉터리에 옛 형식이 써지지 않음 | 올림, `min_writer_version` = 이 값 | 같음 | 있음(writer 지연, 명령 E2E, 전환 재측정) |

- 게이트를 ①에 먼저 넣는 이유: 위험한 부분(모든 진입점의 첫 부수 효과 전 판정)을 형식 전환과 떼어 먼저 시험한다. ③은 정책을 뒤집는다.
- ①은 `analytics.run/2`·`analytics.dataset/1`의 reader를 먼저 싣는다. 그래서 OQ5와 D2·D4는 ①이 병합되기 전에 정한다. ③에서 정의를 바꾸면
  ① kit이 ③의 프로젝트를 잘못 읽거나 ADR-0006 1.2에 따라 `/3`을 열어야 한다(2.6절).
- ②가 병합된 뒤 ③까지는 전환을 명령으로 쓸 수 없다(ADR-0005 결과와 비용).
- 측정 기록이 있는 PR에서 측정한 커밋 뒤 문서가 아닌 변경이 생기면 병합 전에 다시 잰다(운영 정책 검증 증거).
- ③이 크다(13절 R6). 커밋을 writer → 진입점 → 저장 이름 → 명령 → 문서 순으로 나눠 리뷰한다. ③을 다시 쪼개면 두 writer가 잠시 공존하고 결정 7의
  목록이 바뀌므로 그때는 소유자 확인을 받는다.

PR별 주요 파일:

- ①: `writer.py`(새), `core.py`(탐색, `Project.markers`, `config()`의 판정, 쓰기 함수의 `admit`, `read_events`의 `/1`·`/2`, `list_runs`·`list_datasets`,
  status의 형식·`writes_skipped`, `chart_index`가 한 보기를 씀), `relations.py`(`KNOWN`, `data/imported`, `run_visualizations`·`run_reproduction`, `READER_FORMAT`),
  `runner.py`(`compare_runs`와 run 뒤 경고가 한 보기를 씀)·`data.py`·`capture.py`·`__init__.py`·`__main__.py`·`report.py`(run 페이지의 재실행 비교 포함)·
  확장(run 결과 글의 재실행 비교·PNG 첨부가 한 보기를 씀)·`cli.py`·`bin/analytics`(게이트, `Origin` 배관, 탐색), `_platform.py`(텍스트 헬퍼, 3.2절),
  `tools/model_eval.py`·`tools/eval_fixtures.py`·`tools/platform_smoke.py`·`tools/usage_report.py`(한 보기), 기록 schema(`analytics.run/2`·`analytics.dataset/1`,
  `event_2`의 `means`·`actor_basis`), 현재 계약(A 확장의 거절, B 탐색·두 표식, C `/2` 표시, D 오류, F 프로세스 경계), 용어집(새 오류·필드),
  ARCHITECTURE §11, ADR-0001 적용 현황, CHANGELOG.
- ②: `migration.py`(새), `_platform.py`(포트 메서드 `try_exclusive`·`publish_new`·`flush_handle`·`flush_dir`), `relations.py`·`core.py`(매핑 읽기, `KNOWN`), 기록 schema(`analytics.migration/1`, 표식 `migration`),
  `tests/legacy_records.py` 확장, 현재 계약("전환 엔진은 있으나 명령으로 열지 않음", 매핑 읽기), 용어집(전환 오류 이름).
- ③: `writer.py`(`Writer`), `core.py`(init, 새 경로, `sync_context`, `record`, 신뢰 목록, 안내), `runner.py`·`capture.py`(`analytics.run/2`, `visualizations/`,
  해시, `via`, `reproduction.visualizations`, 배치별 관리 디렉터리), `data.py`(`data/imported`, `analytics.dataset/1`), `workspace.py`(`workspace/`, `.analytics/workspace.lock`), `__main__.py`(`migrate`, kind
  목록, 응답의 `request_id`·`bindings`), `cli.py`·`bin/analytics`(`migrate`, 기본 actor), 확장, `demo.py`, `template/gitignore`, `tools/eval_fixtures.py`
  (OQ7), `tools/usage_report.py`, 현재 계약 전반, README, CHANGELOG, 기록 schema(`analytics.trust/1`), 예제 README.

---

## 11. 시험과 fixture

### 11.1 공통 도구

- `tree_digest(root, exclude)`: 정규 파일의 상대 경로·바이트 수·sha256, 디렉터리·링크(링크 글자) 목록의 정렬된 digest. 전환 코드가 아닌 시험의
  걷기다. 모든 전환·차단 시험이 원본(과 필요하면 목적지)에 전후로 부른다. 잠금 파일은 따로 비교한다(만들어졌는지).
- 고장 주입: `migration.migrate(…, on_step=None)`의 비공개 인자. 시험은 자식 프로세스(POSIX·Windows 모두 `subprocess`로 시작한 작은 driver)에서
  `on_step`이 지정한 단계에서 `os._exit(9)`를 불러 F1–F16을 재현한다. `os._exit`은 `finally`를 실행하지 않아 실제 강제 종료에 가깝다. 같은 콜백이
  그 지점에서 원본에 쓰게 해 동시 쓰기를 만들고, 게시 직전에 목적지의 계획 경로에 파일을 만들게 해 T19를 만든다. 전원 손실은 이것으로 재현되지 않는다.
- 옛 kit 흉내: `tests/legacy_records.py`의 `LegacyProject`로 `/1` 원본을 만들고, 옛 쓰기는 같은 잠금(`FileLocks.exclusive(".lab/lock")`)을 잡고
  덧붙이는 작은 함수로 흉내 낸다. 실제 옛 kit 바이트가 필요한 시험은 OQ7을 따른다.

### 11.2 보장별 시험

| 시험 | 내용 | 보장 | PR |
|---|---|---|---|
| T1 원본 보존 | 모든 전환 시험(성공·거절·중단·실패·`--dry-run`)에서 원본 `tree_digest` 전후 같음, 따라간 바깥 링크 대상의 `tree_digest`도 같음. 원본: CRLF journal, 끊긴 마지막 줄, P7처럼 붙여 쓴 줄, `/2` 줄이 섞인 journal, 모르는 가족 줄, 한국어·NFD 파일 이름, 빈 파일, 64 MiB 넘는 파일, 읽기 전용 파일, 링크(OQ3), `.lab/`이 없는 원본(디렉터리가 생기지 않음), 읽기 전용으로 마운트한 원본(잠금 없이 진행, `locks: not_taken`), worktree `.git` 파일 | S1 | ② |
| T2 목적지 규칙 | 원본 안·원본을 품은 위·같은 위치의 다른 철자(대소문자, NFD, 링크, junction), `data -> /ext/data`인 원본에 `--to /ext/data/new`·`--to /ext/data`·`--to /ext`(`overlaps_source`, `details.with`), 비지 않음, 루트에 `.DS_Store`만 있는 목적지(받음, init과 같음)와 `Thumbs.db`만 있는 목적지(`not_empty`), 위쪽 `lab.json`·`analytics.json`, 공간 부족(주입한 빈 공간 값), 대상이 없는 바깥 링크(OQ12), UTF-8이 아닌 이름(Linux), `.lab/migration.lock` 같은 관리 이름 충돌, 대소문자만 다른 두 원본 이름을 이름을 접는 목적지(macOS 기본 볼륨)와 접지 않는 목적지(대소문자 구별 디스크 이미지·Linux)로(앞은 `name_collision`, 뒤는 그대로 복사), 원본의 `.mig-….part` 이름(충돌 없음), 중첩 표식(OQ10), 형제 목적지(`/a/proj`의 `x -> ../proj/y`와 깨진 `z -> ../proj/w`를 `--to /a/proj-new`로: `x`는 링크로 다시 만들지 않고 바이트 사본, `z`는 복사하지 않음, 전환 뒤 목적지에서 `x`를 고쳐도 원본 바이트 그대로) → 각 결과이고 거절이면 목적지·원본·링크 대상 바이트 변화 없음 | S1·S2 | ② |
| T3 커밋 지점 | 표식 전 모든 지점에서 목적지 안 새 kit 명령은 `migration_incomplete`, 옛 탐색(지금 `find_project`의 사본)은 `no_project`. 표식은 검증 뒤에만, 신뢰 항목은 표식 뒤에만 있음 | S2 | ②·③ |
| T4 검증 가능성 | 보고서 schema, 사본 해시 = 보고서, 표식의 `report_sha256` = 보고서 바이트, 위치 대응이 매핑과 같고 `.gitignore`만 변환되며 전후 해시·diff가 맞음, 다시 부르기의 대조가 고친 사본 하나를 잡음 | S3·S4 | ② |
| T5 복사 중 원본 변경 | 지점마다 telemetry 덧붙이기·파일 추가·삭제·스크립트 편집 → `source_changed`(경로 포함), 표식 없음. journal 덧붙이기는 잠금에서 기다렸다가 놓은 뒤 쓰이고 사본에 없으며 다시 부르기가 그 줄을 셈 | S5 | ② |
| T6 중단과 이어 쓰기 | F1–F16 각각 6.11절 표대로 분류하고, 이어 쓴 결과의 사본 `tree_digest`가 중단 없는 전환과 같음(`.analytics/migration/` 제외, (h)는 `leftover_temp`가 적은 파일만 더 있음). 6.10절의 행마다 한 사례 이상: (a) `source_checked` 전 중단 사이 이미 복사한 파일의 원본 변경 → `source_changed`, 아무것도 지우지 않음, (b) 같은 구간에 아직 복사하지 않은 파일의 원본 변경 → 이어 쓰고 결과가 새 원본과 같음, (c) `source_checked`·`verified` 뒤 중단하고 원본 journal에 줄을 덧붙이거나, 원본을 지우고 원본 없이 부르거나(6.1절), 바깥 링크의 대상을 치우거나, 같은 디스크의 빈 공간을 원본 크기보다 작게 만듦 → 원본 쪽 검사 없이 8단계부터 이어 커밋하고 매핑이 중단 없는 전환과 같음, 다시 부르기가 덧붙인 줄을 셈, (d) `transform_begin` 뒤 교체 전과 교체 뒤 → 각각 변환부터·매핑부터, `.gitignore`를 다른 내용으로 바꿔 두면 `destination_mismatch`, (e) F12처럼 `committed` 전 → `committed`를 쓰고 `already_migrated`, (f) `committed` 뒤 표식을 지움(원본도 지우고 원본 없이 부름) → 같은 표식 바이트를 다시 게시, (g) 순서가 맞지 않는 ledger·ledger 둘 → `destination_mismatch`, (h) `begin`만 있는 임시 파일의 경로가 원본에서 사라짐 → 지우지 않고 `leftover_temp`, (i) 계획에 없는 파일, 해시가 다른 사본 → 거절, (j) 목적지에 생긴 `.venv/`·`.DS_Store`(계획에 없음) → 무시(OQ16), (k) 전환 디렉터리를 만든 뒤 `start` 줄 전·중(빈 ledger, 끊긴 첫 줄) → 새 전환, (l) `transform_begin`·`marker_begin` 뒤 임시 파일을 만들기 전·쓰는 중 → 임시 파일을 지우고 그 단계부터, (m) 직접 쓰기 경로에서 `direct_intent` 뒤 만들기 전 → 이어 씀, 만든 뒤 `direct` 전 → `occupied`로 거절, (n) 어느 ledger 줄을 쓰다 끊김(끊긴 마지막 줄) → 앞 줄의 상태로 이어 씀, (o) 읽기 전용 원본 루트·`.lab/`·`.gitignore` → 9·11단계가 끝나고 목적지 루트·`.analytics/`는 쓸 수 있음. 거절 사례는 모두 목적지·원본의 `tree_digest`가 그대로 | S6·S10 | ② |
| T7 동시성 | 두 프로세스가 같은 목적지로 → 하나만 성공, 다른 하나 `destination_busy`. 원본 run 잠금을 다른 프로세스가 잡은 동안 → 즉시 `source_busy` | S7 | ② |
| T8 진입점 행렬 | 11.3절 | S8·S9 | ①·③ |
| T9 혼합 journal과 두 run 형식 | `mixed-version` 예제 + `/1` CRLF·`/2` LF·끊긴 줄·복구한 줄 → reader 판정이 예제 기대값과 같고, `read_events`·맥락 블록·status·Workspace가 `/2` 줄을 보임(P6 회귀), writer 덧붙이기는 LF를 먼저 쓰고 seq = 최대 + 1. `/1` run과 `/2` run이 섞인 프로젝트에서 `run_visualizations`의 모든 소비자(status·context·Workspace·확장 run 결과·PNG 첨부·`compare_runs`·개발 도구)가 같은 수와 id를 보이고, `/1`·`/2` 재실행 run의 `run_reproduction`을 쓰는 확장 run 결과 줄과 Workspace run 페이지가 Visualization별 비교를 모두 보이며, 정적 검사가 두 함수 밖의 `charts` 읽기(run 기록과 `reproduction`)를 잡음 | S8·S10·S13 | ①·③ |
| T10 다시 읽기 | 쓰기 작업 사이에 표식을 `lab.json`·두 표식·더 높은 `min_writer_version`으로 바꿈 → 다음 작업이 거절. journal 잠금을 잡은 writer와 표식을 올리는 writer가 겹치면 낮은 쪽이 `writer_too_old`이고 run은 `incomplete` | S8 | ③ |
| T11 기록 비교 | 전환 전 원본과 전환 뒤 목적지를 `relations.read`로 읽은 결과(참조 판정·기록 상태·run·생산자·`unsupported`·`damaged`)가 같음. 루트에 기대는 값은 매핑으로 정규화하고, 옛 절대 경로만 `inferred`로 더 풀림. 바깥 링크 경계로 풀리던 옛 run의 경로(`data -> /ext/data`)가 목적지에서도 같은 `inferred`로 풀리고, 목적지를 옮기고 `/ext/data`를 지운 뒤에도 같음. 옛 run의 상대 `.lab/…` 경로, 0.5.0 이후 run이 같은 문자열을 기록한 경우(대응 없음)(5.4절). ③에서는 목적지에서 `/1` run을 다시 실행해 새 run의 `reproduction.visualizations`·`verdict`가 원본에서 같은 run을 다시 실행한 결과와 같고, 확장 run 결과 줄과 Workspace가 그 Visualization별 결과를 보임(S13). #41 사례(`correction`·`withdrawal`·`unpreserved_data`·`same_chart_id`·`reference_cycle`·`long_korean`) 포함(Q7) | S1·S3·S13 | ②·③ |
| T12 표식 판정 단위 | 4.3절 표의 모든 행, 가장 가까운 표식에서 멈추는 탐색(위 `lab.json`·아래 `analytics.json`과 그 반대, 표식 없는 전환 목적지) | S8 | ①·② |
| T13 상태 변경 | 예제 `writes`의 `record_update`(`state_conflict`)와 `event_append`(끊긴 줄 뒤 LF, `writer_too_old`)를 writer가 그대로 냄. 두 정정 → `state_after: conflict`. 이미 닫힌 질문을 `--resolves`로 다시 닫음 → 쓰고 응답에 `state_before: resolved`와 앞 이벤트 id. 같은 `record_update`를 두 프로세스가 동시에 → 하나만 쓰이고 다른 하나 `state_conflict`. 쓰기 도중 `os._exit` → `damaged`, 상태 그대로 | S10 | ③ |
| T14 작성 출처 | 진입점마다 `actor`·`means`·`actor_basis`·`session`이 4.2절 표대로. 환경 변수 없는 CLI(OQ1), 세션 없는 사람 기록이 모든 읽기에 나옴(Q17). 본문 32 KiB 경계와, 이스케이프가 많은 본문·긴 참조 24개로 만든 50 KiB 줄 경계(`text_too_long`, 아무것도 쓰지 않음, 2.9절) | S11 | ③ |
| T15 포트 계약 | `try_exclusive`가 다른 프로세스의 `exclusive`와 서로 막고 부모 디렉터리를 만들지 않으며 다툼이 아닌 실패는 `LockUnavailable`(세 구현), `publish_new`가 있는 이름을 바꾸지 않고 실패함(세 구현, 링크 실패를 주입한 `direct` 경로 포함), `flush_handle`·`flush_dir`가 오류를 숨기지 않음 | S6·S7 | ② |
| T16 줄 끝 | writer 헬퍼가 Windows에서도 LF, 전환 사본은 CRLF 그대로, 새 모듈이 쓰기 AST 검사 범위 안 | S12 | ①·② |
| T17 계층 | `writer`·`migration` 등록, 수준 표가 ARCHITECTURE와 같음, 호스트 쪽 로드 목록에 `writer`, `migration`은 aelix 프로세스에 로드되지 않음 | 조건 5 | ①·② |
| T18 uv | 런처의 `migrate`가 원본의 `.venv/`·`uv.lock`을 만들거나 바꾸지 않음(목록·해시), 커밋 뒤 sync가 목적지 `uv.lock`을 다시 쓰지 않음(맞는 경우·맞지 않는 경우) | S1 | ③ |
| T19 덮어쓰지 않는 게시 | 게시 직전(`on_step`)에 다른 프로세스가 계획 경로에 파일을 만듦 → `destination_mismatch(occupied)`, 그 파일의 바이트 그대로, 임시 파일은 ledger에 있음, 다시 부르면 같은 거절. 표식 게시 직전에 `analytics.json`을 만들어 둠 → 같음. 링크를 지원하지 않는 파일 시스템(시험 adapter로 `os.link` 실패를 주입)에서 `direct` 경로가 같은 결과를 냄 | S2·S6 | ② |
| T20 배치별 관리 디렉터리 | 새 형식 프로젝트에서 스크립트가 `report/out.csv`를 씀 → run 기록의 출력에 저장된 철자로 있음. 같은 프로젝트의 `workspace/x.csv` → 출력 아님. `lab.json` 프로젝트의 `report/` → 지금처럼 출력 아님 | S14 | ③ |
| T21 다시 부른 전환 | 6.9절의 표: 목적지에 두 표식·깨진 표식·모르는 표식·`lab.json`, 다른 원본의 커밋된 목적지, 같은 원본의 커밋된 목적지(신뢰 항목 있음·없음, 원본을 지우고 원본 없이 부름, 바깥 링크 대상을 치움, 같은 디스크의 빈 공간이 원본보다 작음), `marker_rewritten`, 표식과 같은 inode의 임시 파일을 실제 호출과 `--dry-run`으로. 기대 결과, 신뢰 목록은 누락 보충 말고 바뀌지 않음, 목적지·원본의 `tree_digest` 같음(임시 파일 정리 제외) | S1·S2 | ②·③ |
| T22 aelix가 부른 CLI의 작성 주체 | aelix 0.1.0b2를 격리 환경에 설치하고(가짜 HOME, 모델·네트워크·로그인 없음) 확장을 aelix의 확장 로더로 실제로 등록한 뒤(`on("user_bash")` 포함, 대역 hook 없이) aelix의 bash 도구 실행 함수로 `analytics record`를 부름 → `actor=agent`·`declared`·`means=cli`와 세션. 같은 프로세스에서 `cli/repl.handle_user_bash`가 실제 hook 버스로 `user_bash`를 발행하게 해 사람의 `!` 명령을 흉내 냄 → kit을 부르는 명령은 `human`·`declared`, kit을 부르지 않는 명령에는 실행기를 돌려주지 않음(OQ1). `user_bash` 실행기를 돌려주는 시험용 확장을 하나 더 등록해 등록 순서 둘 다에서 어느 실행기가 도는지 기록(4.2절의 두 방향). 확장 자신의 kit 자식은 영향 없음. Windows 레그의 PowerShell 자식은 실행하기 전까지 `not_run` | S11 | ③ |

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

Windows CI 레그에서 T1–T8·T15·T16·T19·T21을 돌리고 다음을 더한다: 읽기 전용 원본 파일의 사본 flush와 이어 쓰기의 임시 파일 지우기(6.4절), `publish_new`의
`os.rename`이 있는 이름에서 실패함, 다른 프로세스가 표식을 교체하는 동안의 `marker_state`(재시도 뒤 판정, 4.3절), 디렉터리별 대소문자 구별을 켠 목적지의
이름 접힘 판정, 대소문자만 다른 두 원본 이름(만들 수 없으면 Linux 레그에서 만든 tar를 풀어 원본으로),
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
| `<날짜>-issue-13-migration.{md,json}` | ② | `fixtures`(S·M의 생성 커밋·명령·seed, 파일 수·바이트, 원본 `tree_digest`), `phases`(계획·복사·재확인·검증·보고서·flush·표식의 ms, 원본 잠금을 쥔 시간, 최대 RSS), `sizes`(복사·제외 바이트, `report.json`·`plan.json`·ledger 크기), `preservation`(독립 걷기로 잰 원본 digest 전후), `fault_matrix`(F1–F16와 6.10절의 행 × 레그), `concurrency`(T5·T7), `reader_comparison`(T11) |
| `<날짜>-issue-13-writer.{md,json}` | ③ | `writer_latency`(11.5절), `entry_points`(③의 기대값), 명령 E2E(런처·`bin/analytics`, `--dry-run`, 이어 쓰기, 다시 부르기), `migration`(②의 항목 재측정), `uv`(T18), `agent_cli`(T22: aelix 버전, 레그별 bash 도구 자식이 본 작성 주체, 두 `user_bash` 처리기의 등록 순서별로 돈 실행기) |

시간은 앞뒤 커밋을 번갈아 두 번 재고 최솟값을 쓴다(PR #76의 방식). 실행하지 않은 레그·조합(Windows 클라이언트 판, 표준 사용자, 네트워크 파일
시스템, 전원 손실과 그 뒤의 내구성, Windows의 읽기 전용 사본·디렉터리 flush·잠금 해제 지연, 실제 aelix 모델 세션)은 `not_run`에 둔다. 격리는 신뢰 목록·uv를 실행 폴더 안으로 돌리고 `AELIX_LAB_*`·`LAB_*`가 없으며 `~/.aelix`를
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
| R7 | ③ 병합 즉시 `lab.json` 프로젝트의 기록·렌더가 막힌다. 대상이 없는 링크·UTF-8이 아닌 이름·중첩 표식·이름을 접는 목적지의 이름 충돌로 전환할 수 없는 프로젝트는 고칠 때까지 읽기 전용에 갇힌다 | OQ3·OQ4·OQ10·OQ12를 ③ 전에 정함. ③ 전에 소유자의 실제 프로젝트를 `--dry-run`으로 미리 봄(쓰기 없음) |
| R8 | 신뢰 목록에 잠금이 없어 두 쓰기가 겹치면 하나가 사라진다 | main에도 있는 한계. 전환은 응답과 다시 부르기로 신뢰 누락을 알리고 채움 |
| R9 | OQ1의 권장 답은 aelix bash 도구가 확장이 aelix 프로세스 환경에 넣은 변수를 자식에 넘긴다는 사실에 기댄다. aelix 0.1.0b2에서 코드와 실행으로 확인했으나(18절) aelix가 bash 도구의 환경 만들기를 바꾸면 깨진다 | T22가 지원하는 aelix 버전에서 그 동작을 고정한다. aelix 버전을 올릴 때 T22를 다시 돌린다 |
| R10 | 전환 뒤 사용자가 목적지를 옛 프로젝트 아래로 옮기면 ① 전 kit이 그 아래에서 위쪽 프로젝트에 쓴다 | 막을 수 없음. ③의 status가 위쪽 표식을 경고 |
| R11 | 누군가 전환을 `uv run --project`로 배선하면 원본의 `.venv/`·`uv.lock`이 바뀐다 | T18이 막음. `.venv/`가 원본 비교에서 빠지므로(OQ4) 이 시험이 따로 있어야 한다 |
| R12 | `.git/`을 복사하면 같은 원격을 가진 저장소가 둘 생긴다 | 보고서의 안내, OQ4 |
| R13 | `data/raw`와 `data/imported`에 같은 이름 | `data/imported`가 view 이름을 먼저 받고 status가 경고(#36 항목 6과 같은 방식) |
| R14 | 입력 `gap`을 쓰던 스크립트·습관의 실패 | 분명한 오류와 안내, 전환의 스크립트 제안이 `gap`을 바꿈 |
| R15 | plotly 템플릿 개명이 새 그림 JSON의 바이트를 바꿀 수 있다(미확인) | ⓪의 시험이 기존 기록 바이트만 고정하고, 바뀌면 그 항목을 ③으로 옮김 |
| R16 | ③ 뒤 `/1` fixture 생성이 막혀 #15·#41의 측정이 밀린다 | OQ7 |
| R17 | ③ 전까지 `/1` writer의 #49 결함이 전환할 원본에 손상을 더한다 | OQ8 |
| R18 | (1) 확장이 aelix 프로세스 환경에 넣은 `AELIX_ANALYTICS_ACTOR=agent`가 사람의 호출에도 닿는다: RPC 모드의 `bash` 명령, 다른 확장의 `user_bash` 실행기가 마지막일 때의 `!` 명령, aelix가 띄운 다른 자식. (2) 반대로 이 확장의 `user_bash` 실행기가 마지막이면 aelix가 마지막 결과만 쓰므로 다른 확장(샌드박스·원격 실행)의 실행기를 조용히 밀어내, 그 `!` 명령이 사용자가 고른 격리 없이 로컬에서 돈다 | (1)은 사람의 CLI가 `agent`로 남는 쪽의 한계로 현재 계약 A에 적음(#17의 사람 수를 줄이는 쪽의 오류). (2)는 보안상의 부작용이라 OQ1에서 소유자가 정한다. 권장: kit의 진입점을 부르는 `!` 명령에만 실행기를 돌려줘 밀어내기를 그 명령으로 줄이고, 남는 범위를 현재 계약 A에 한계로 적음. T22가 두 등록 순서를 기록 |
| R19 | 대상이 없는 바깥 링크·UTF-8이 아닌 이름이 있는 원본은 고치기 전까지 전환을 거절해 ③ 뒤 읽기 전용에 더 오래 머문다 | `--dry-run`이 목록을 맨 앞에 보이고 오류가 고칠 방법을 안내(OQ12). R7과 같은 대응 |
| R20 | 링크를 지원하지 않는 목적지 파일 시스템의 `direct` 게시는 최종 이름에 반쯤 쓴 파일을 남길 수 있다. `direct_intent` 뒤 파일을 만든 직후 `direct` 줄 전에 끊기면 그 파일이 이 전환의 것인지 알 수 없다 | 표식 전이라 어떤 kit도 읽지 않고, ledger의 정체성으로 이 전환의 파일만 지운다(6.12절). 정체성을 적기 전의 창에서 끊긴 파일은 지우지 않고 `destination_mismatch(occupied)`로 거절하며 경로를 알린다(6.11절 F15). 사람이 확인해 지운 뒤 다시 부른다. 실제 FAT·exFAT 매체는 실행하지 않았다 |

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

ADR이 #13 설계에 맡겼거나 입력이 정하지 않았지만 제품 결정은 아니라고 본 것이다. 설계 리뷰가 받아들이거나 바꾼다. D19–D27은 설계 리뷰의
지적(19.1절)으로, D28·D29는 수정 diff 확인의 지적(19.2절)으로 더하거나 드러낸 선택이며, 그 뒤 다시 리뷰를 받지 않았다. D10·D11·D23은 채택된
글자와 다르거나 그 해석이라 소유자 질문(OQ15·OQ16·OQ13)의 권장 답이며, 소유자의 답이 이 행을 정한다.

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
| D9 | 내구 쓰기는 전환의 커밋 전에만, 포트 메서드 `flush_handle`·`flush_dir`. 보장은 프로세스 중단까지이고 전원 손실 뒤의 내구성은 목표(시험하지 않음) | 1·6.4·6.12절 |
| D10 | 잠금: `try_exclusive`, 순서 `run.lock` → `lock`, 7단계에서 놓음. `.lab/`이 없거나 다툼이 아닌 이유로 잠금 파일을 만들거나 열 수 없으면 잠금 없이 전후 비교로만 지킴(이 부분은 OQ15의 권장 답: ADR-0005 결정 5의 글자 "복사하는 동안 잠금을 잡는다"를 좁히고, 보장 "바뀌면 실패"는 같다) | 6.3·6.12절, OQ15 |
| D11 | 이어 쓰기: ledger 줄이 정한 상태만 받음. `source_checked` 전이면 이미 복사한 파일이 지금 원본과 같을 때 남은 계획을 다시 세움, 그 뒤면 원본을 다시 보지 않고 기록한 스냅샷으로 이음. `committed`인데 표식이 없으면 ledger의 표식 바이트를 다시 게시. 다시 만들 수 없는 임시 파일은 지우지 않음. 계획에 없는 `.venv/`·`__pycache__/`·운영체제 메타 파일을 무시함(이 부분은 OQ16의 권장 답: 결정 5의 "보고서가 적은 파일만 있을 때만 잇는다"를 넓게 받음) | 6.10절, OQ16 |
| D12 | 전환 중 journal에 쓰지 않음. 출처는 표식의 `migration`과 보고서, status가 보임 | 2.4·6.3절 |
| D13 | 탐색: 가장 가까운 표식(또는 표식 없는 전환 기록)에서 멈춤, 명시한 루트가 위로 올라가는지는 진입점마다 지금 규칙 그대로, 두 표식이면 읽기도 거절, 깨진 `lab.json`은 `legacy` | 4.3절 |
| D14 | `.gitignore` 덧붙임 규칙, `.gitattributes`·스크립트는 제안만 | 6.13절 |
| D15 | 신뢰 목록: 새 목록만 씀, 옛 목록은 읽기 전용 | 6.14절 |
| D16 | 입력 `gap`은 별칭 없이 오류, 기본 relation `cites` | 2.8·4.7절 |
| D17 | PR별 `KIT_VERSION`: ⓪·①·③ 올림, ② 그대로 | 10절 |
| D18 | 측정 기록을 ①에도 둠(새 가족 정의를 새 기록 종류로 봄) | 12절 |
| D19 | 사본·새 파일·표식을 "없을 때만 만드는" 원자 게시(`AtomicFiles.publish_new`: POSIX `os.link`+`unlink`, Windows `os.rename`)로 놓고, 덮어쓰는 교체는 이 전환이 가진 파일에만. 링크를 지원하지 않는 파일 시스템은 `O_EXCL` 직접 쓰기와 ledger의 정체성(`direct`) | 6.4·6.12절 |
| D20 | Visualization의 한 보기 `relations.run_visualizations`, 재실행 비교 결과의 한 보기 `relations.run_reproduction`, 두 함수 밖의 `charts` 읽기를 막는 정적 검사, 재실행 비교의 형식 간 id 비교 | 2.6·5.3절 |
| D21 | 관리·제외 디렉터리를 배치(`Layout`·`LegacyLayout`)마다 따로 둠. 두 형식의 합집합을 쓰지 않음 | 2.3절 |
| D22 | UTF-8·LF 텍스트 헬퍼는 `_platform`(ADR-0007 결정 2의 위치), `classify_refs`는 writer, 옛 경로 추론은 core가 `legacy_path=`로 넣음 | 3.2·4.5절 |
| D23 | uv 환경(`.venv/`, uv가 갱신하는 `uv.lock`)과 게이트: 이 행은 따로 정하지 않고 OQ13(소유자 결정)의 답을 따른다. 4.3절은 그 권장 답(게이트 대상이 아님)을 전제로 썼다 | 4.3절, OQ13 |
| D24 | `data/imported`와 `data/raw`에 같은 이름이 있으면 `data/imported`가 view 이름을 먼저 받고 status가 경고 | 2.3절, R13 |
| D25 | 지금 `refs.other`로 가는 분류되지 않은 참조 문자열을 `/2`에서 `{type: visualization_version, token}`(결박 `none`)으로 남김. 대안은 그런 문자열을 거절하는 것 | 4.7절 |
| D26 | 대소문자·정규화 이름 충돌은 목적지가 실제로 이름을 접을 때만 거절(2단계의 시험 파일로 판정). 임시 이름은 계획 이름과 겹치지 않게 고름. `.lab/` 아래 이름이 새 관리 이름과 겹치면 늘 거절 | 6.2·6.3절 |
| D27 | 표식을 읽지 못하면(공유 위반 등) 교체와 같은 예산으로 재시도한 뒤 `invalid`(`unreadable`). `--resolves`의 응답에 대상의 `state_before`·`state_after` | 4.3·4.6절 |
| D28 | 검사의 순서: 목적지를 먼저 읽기만 하고 판정, 원본 쪽 검사는 원본을 다시 읽을 상태(새 전환, 재확인 전의 이어 쓰기)에서만, 공간 검사는 남은 바이트로. 원본을 찾지 못한 호출(`analytics migrate --to DEST`)은 ledger가 `source_checked` 뒤이거나 커밋된 목적지에만 받음. 매핑의 철자는 7단계가 원본 잠금 안에서 정해 `source_checked` 줄에 적음. 대안: 원본 없는 호출을 두지 않고 원본을 지운 경우를 한계로 적음 | 6.1·6.2·6.7·6.9·6.10절 |
| D29 | ledger 먼저: 목적지에 만드는 파일은 모두 이름(알 수 있으면 바이트·해시)을 ledger에 먼저 적고 `O_EXCL`로 만듦(`transform_begin`·`marker_begin`을 임시 파일보다 먼저, 직접 쓰기의 `direct_intent`). 내용 없는 전환 디렉터리는 새 전환, 끊긴 ledger 줄은 건너뜀, 보고서를 다시 쓸 때마다 새 `reported`, `project_id`는 `plan` 줄에. 상대 링크는 글자가 루트 안에 머물 때만 다시 만듦(OQ3). 목적지 루트와 `.analytics/`에 권한을 옮기지 않음 | 6.3·6.4·6.6·6.8·6.10·6.11절 |

---

## 16. 소유자 결정이 필요한 것

**소유자 결정(2026-10-08): 아래 권장 답 16개를 모두 채택했다.** OQ1의 Q17-b 범위는 Aelix 세션이다. 답은 [#13](https://github.com/handochan/aelix-analytics/issues/13#issuecomment-6044853778)에 기록했다.

입력이 정하지 않은 제품 결정이다. 각 항목에 질문, 권장 답, 답에 따라 바뀌는 것, 채택된 보장을 좁히는지를 적는다. 이미 받은 승인(ADR-0005·0006,
#3·#4·#9·#12·#36의 결정)을 다시 묻지 않는다. 아래 표의 권장 답은 그대로 받아들일 수 있게 한 줄로 적었고, 근거와 바뀌는 것은 표 아래에 있다.

| 번호 | 질문 | 권장 답(한 줄) | 채택된 보장을 좁히는가 |
|---|---|---|---|
| OQ1 | 작성 주체의 기본값과 에이전트가 부른 CLI | 확장이 aelix 프로세스 환경에 `AELIX_ANALYTICS_ACTOR=agent`를 넣어 aelix 에이전트의 CLI를 `agent`로 남기고, 사람의 `!` 명령은 kit을 부를 때만 `user_bash` 실행기로 `human`을 넣으며(다른 확장의 실행기를 밀어내는 범위를 그 명령으로 줄임), 밝히지 않은 CLI는 `human`·`inferred`, #17은 `human`을 basis와 관계없이 세되 둘을 나눠 보인다 | 권장 경로는 좁히지 않는다. aelix 밖 에이전트가 `human`·`inferred`로 남는 경우는 Q17-b를 호스트와 관계없이 읽으면 좁힘이며 그 범위를 소유자가 정한다 |
| OQ2 | 본문 상한 | 본문 UTF-8 32 KiB와 직렬화한 줄 50 KiB 미만을 함께 지키고 넘으면 `text_too_long`으로 거절한다 | 아니오 |
| OQ3 | 원본 안의 링크·junction | 밖을 가리키는 링크는 대상 바이트를 복사하고 매핑에 적으며, 안을 가리키는 상대 링크는 글자가 루트 안에 머물 때만 다시 만들고 아니면 절대 링크처럼 다룬다 | 아니오 |
| OQ4 | 복사 범위 | `.venv/`·`report/`·`.lab/cache/`·잠금·`__pycache__/`, 운영체제 메타 파일(`.DS_Store`·`Thumbs.db`·`desktop.ini`)과 루트의 worktree `.git` 파일은 복사하지 않고 보고서에 적는다 | 예: ADR-0005 결정 5의 "기존 파일은 바이트 그대로 복사"를 글자보다 좁힌다 |
| OQ5 | `analytics.run/2`에서 바꿀 이름 | `charts[]`·`id`·`reproduction.charts`·`via`의 `lab.` 접두만 새 이름으로 바꾸고 나머지는 둔다. ① 병합 전에 정한다 | 아니오 |
| OQ6 | 내부 코드 이름의 개명 PR ⓪ | ① 전에 동작을 바꾸지 않는 개명 PR ⓪을 둔다 | 아니오. ADR-0005 결정 7의 PR 목록에 한 줄을 더한다 |
| OQ7 | ③ 뒤의 `/1` fixture | 생성기에 `--kit-root`를 더해 ③ 전 kit이 실제로 쓴 바이트를 쓴다 | 아니오 |
| OQ8 | #49의 시기 | ③을 기다리지 않고 #49를 작은 일반 PR로 먼저 고친다 | 아니오 |
| OQ9 | 새 형식 init의 위쪽 표식 | init도 위쪽에 표식이 있으면 `destination_invalid(inside_project)`로 거절한다 | 지금 허용되는 동작(중첩 프로젝트 만들기)을 막는다. 채택된 보장은 아니다 |
| OQ10 | 원본 안의 중첩 프로젝트 | `nested_project`로 거절하고 경로를 모두 보인다 | 아니오 |
| OQ11 | ③에서 여는 공개 입력 | ③은 writer API와 응답의 `request_id`까지 하고 공개 입력은 #14·#19가 연다 | 아니오 |
| OQ12 | 대상이 없는 바깥 링크(와 UTF-8이 아닌 이름) | 사전 검사에서 `source_unreadable`과 목록으로 거절하고 사람이 고친 뒤 다시 부르게 한다 | 아니오 |
| OQ13 | 읽기 명령의 uv 환경 동기화 | 기록이 아닌 환경이라 게이트하지 않고, 현재 계약에 "읽기 전용은 uv 환경 동기화를 포함하지 않는다"를 적는다 | ADR-0005 결정 6의 부수 쓰기 목록은 닫혀 있지 않으므로 이 답은 그 목록에서 환경을 빼는 해석이다. 좁힘으로 읽힐 수 있어 소유자가 정한다 |
| OQ14 | 에이전트가 부른 전환 | 받고, ledger·보고서·응답에 `actor=agent`·`declared`·`means=cli`로 남긴다 | 아니오. 결정 5는 "명시적인 명령"만 요구한다. 거절을 고르면 결정에 없는 제한을 더한다 |
| OQ15 | 잠금을 잡을 수 없는 원본(`.lab/` 없음, 잠금 파일을 만들거나 열 수 없음) | 잠금 없이 진행하고 전후 비교로만 지키며 `locks: not_taken`과 이유를 ledger·보고서에 적는다(D10) | 예: ADR-0005 결정 5의 "복사하는 동안 원본의 journal·run 잠금을 잡는다"를 그 두 경우에 지키지 않는다. 받으면 결정 5에 예외를 적는 ADR 개정이 필요하다 |
| OQ16 | 이어 쓰기에서 보고서가 적지 않은 환경·메타 파일 | 계획에 없는 `.venv/`·`__pycache__/`·운영체제 메타 파일은 무시하고 잇는다. 계획 경로를 차지하면 `occupied`(D11) | 예: 결정 5의 "보고서에 없는 파일이 있으면 거절한다"를 그 이름들에 대해 좁힌다(덮어쓰지 않음은 그대로). 받으면 결정 5에 예외를 적는 ADR 개정이 필요하다 |

**OQ1 작성 주체의 기본값과 에이전트가 부른 CLI.** 런처·`bin/analytics`는 `AELIX_ANALYTICS_ACTOR`가 없으면 `human`을 채워 kit을 부른다. 그래서 에이전트가
bash 도구로 부른 CLI가 사람 작성으로 남는다. Q17-b는 "에이전트의 CLI 호출을 사람 작성으로 기록하지 않는다"고 하고, ADR-0008 결정 3은 요청 주체와
실행 수단을, 결정 7은 "#13이 에이전트 요청으로 기록한 CLI 호출"(텍스트 `status`에서 발행하지 않음)을 전제한다. kit은 누가 불렀는지 알 수 없다.

R9 프로브(18절)로 확인한 것: aelix 0.1.0b2의 bash 도구는 호출마다 aelix 프로세스의 `os.environ`을 복사해 자식의 환경을 만든다
(`aelix_coding_agent/util/shell_env.py:32`, `tools/bash.py:752-762`·`1033`·`1044-1051`·`247`·`293`). 확장이 로드 때 넣은 변수와 도구를 만든 뒤 hook에서
바꾼 변수를 자식이 그대로 봤다. 확장 API에는 bash 도구의 환경을 따로 정하는 선택지가 없다(`spawn_hook`은 `create_bash_tool`의 선택지인데 CLI가
확장에서 넘기지 않는다, `cli/entry.py:961-985`; 내장 도구가 같은 이름의 확장 도구보다 우선한다, `aelix_agent_core/harness/core.py:795-801`). 같은
`os.environ`이 사람의 `!` 명령에도 가지만(`cli/repl.py:30-63`), `user_bash` 처리기가 돌려준 실행기로 그 명령의 환경만 바꿀 수 있음을 확인했다
(`handle_user_bash`를 hook 발행만 흉내 낸 대역으로 실행했고, 확장 등록에서 처리기 합치기까지는 코드로 읽음, 4.2절). aelix는 마지막 처리기의
결과만 쓰고 앞의 처리기는 서로의 결과를 보지 못한다(`_reducer_user_bash`, 읽음). RPC 모드의 `bash`에는 처리기가 없다
(`rpc/rpc_mode.py:571-584`). Windows는 셸이 PowerShell·cmd여도 같은 환경을 넘긴다(코드로 읽음, 실행하지 않음). `tool_call` 처리기가 bash 도구의 인자(명령 글)를 고쳐 변수 설정을 앞에 붙이는 방법도 API로는 가능하지만
(`aelix_agent_core/harness/hooks.py:416-431`), 앞선 가드레일·승인 처리기가 본 명령과 실제로 도는 명령이 달라지고 셸마다 문법이 달라(POSIX
`export`, PowerShell `$env:`, cmd `set`) 쓰지 않는다.

- 권장(Q17-b를 지킨다):
  1. 확장이 로드될 때 aelix 프로세스 환경에 `AELIX_ANALYTICS_ACTOR=agent`를 넣고 세션이 정해질 때 `AELIX_ANALYTICS_SESSION`을 바꾼다(4.2절). aelix
     세션의 에이전트가 bash 도구로 부른 CLI는 `actor=agent`, `actor_basis=declared`, `means=cli`로 남는다. Q17-b의 시나리오(별도 Aelix 환경의 새
     세션에서 실제 모델이 후속 분석)와 ADR-0008 결정 7의 전제를 그대로 지키며 좁히지 않는다.
  2. 사람이 aelix에서 친 `!` 명령 가운데 명령 글이 kit의 진입점(`analytics`, `bin/analytics`, `aelix_analytics`)을 부르는 것에만 확장의 `user_bash`
     처리기가 그 명령의 환경에 `AELIX_ANALYTICS_ACTOR=human`을 넣는 실행기를 돌려준다. 다른 `!` 명령에는 결과를 돌려주지 않아 다른 확장의
     실행기를 밀어내지 않는다. 글로 판정하므로 스크립트 안에서 kit을 부르는 `!` 명령은 `agent`로 남는다(사람 수를 줄이는 쪽).
  3. 진입점·사용자가 밝히지 않은 CLI·SDK 호출(aelix 밖의 터미널)은 기본값 `human`과 `actor_basis: inferred`를 남긴다. 런처·`bin/analytics`는 기본값을
     채워 넘기지 않는다.
  4. run 밖 SDK의 `actor="script"`는 `human`(밝히지 않았으면 `inferred`)과 `means="sdk"`로 바꾼다. TTY로 사람을 추정하지 않는다.
  5. #17과 [재사용 제안 정책](../SKILL-PROPOSAL-POLICY.md#2-상시-모드의-제안-문턱)의 "사람의 표시는 작성 주체가 사람으로 기록된 것만 센다"는 `actor=human`을
     `actor_basis`와 관계없이 세고, 그 수를 `declared`와 `inferred`로 나눠 보인다. `inferred`를 빼면 Q17-a처럼 aelix 없이 CLI만 쓰는 사람의 표시가
     선언 없이는 하나도 세지지 않는다. 텍스트 `status`의 발행 판정(ADR-0008 결정 7)도 같은 값을 쓴다.
- 남는 경우와 좁힘(소유자 결정): aelix 밖 호스트의 에이전트(다른 코딩 에이전트가 터미널 도구로 `analytics`를 부름)는 kit도 확장도 알 수 없어
  `human`·`inferred`로 남고, 위 5에 따라 #17의 사람 수에 들어가며 텍스트 `status`에서 발행할 수 있다. Q17-b의 시나리오는 Aelix 세션이므로 이 경우는
  그 시나리오 밖이라고 본다. 그러나 Q17-b의 문장을 호스트와 관계없이 읽으면 이 경우를 한계로 두는 것은 **채택된 게이트 Q17-b의 좁힘**이며 소유자의
  결정이 필요하다. 권장: Q17-b의 범위를 Aelix 세션으로 확인하고, 다른 호스트에서는 `AELIX_ANALYTICS_ACTOR=agent`를 설정하라고 README와 현재 계약에
  적는다. 이 경우까지 막으려면 5를 "`declared`인 `human`만 센다"로 바꿔야 하며, 그러면 aelix 밖에서 사람이 선언하지 않은 CLI 표시는 세지지 않는다.
- 반대 방향의 한계(좁힘 아님): RPC 모드의 `bash`, 다른 확장의 `user_bash` 실행기가 마지막일 때의 `!` 명령은 사람이 불렀어도 `agent`로 남는다(R18).
  Q17-b를 어기지 않고 #17의 사람 수를 줄이는 쪽의 오류다.
- 다른 확장의 실행기를 밀어내는 부작용(보안, 소유자 결정): 이 확장의 실행기가 마지막이면 aelix가 그것만 써서, 샌드박스·원격 실행 확장이 돌려준
  실행기가 버려지고 그 `!` 명령이 로컬에서 돈다. 사용자가 고른 실행 격리가 그 명령에서 꺼진다. 권장 2는 이 범위를 kit을 부르는 `!` 명령으로
  줄이지만 없애지 못한다. 확장 API가 다른 처리기의 등록을 알려 주는지 확인하지 않았다(hook 버스의 `has_handlers`는 자기 처리기도 세고 확장에서 닿는지
  모른다). 그래서 권장은 남는 범위를 현재 계약 A에 한계로 적는 것이다. 이 부작용을 받을 수 없으면 대안은 `user_bash` 처리기를 두지 않는 것이며,
  그러면 aelix 안에서 사람이 `!`로 부른 CLI는 모두 `agent`로 남는다(사람 수를 줄이는 쪽, Q17-b는 지킴).
- 바뀌는 것: 확장이 aelix 프로세스 환경을 바꿈(현재 계약 A), kit을 부르는 `!` 명령에만 실행기를 돌려주는 `user_bash` 처리기 하나와 그 밀어내기
  한계, `/2` 이벤트·`analytics.run/2`의 선택 필드 `actor_basis`, 런처의 기본값 주입 제거, Q17-b 판정의 근거(T22), #17의 사람 수와 텍스트 `status`
  발행이 `actor_basis`를 읽는 방식. 에이전트가 bash로 부른 `analytics migrate`의 처리는 OQ14다. 대안 "기본값 `unknown`"은 Q17-a의 사람 CLI 기록을
  모두 미확인으로 만든다.

**OQ2 본문 상한.** ADR-0006 10절은 거절과 전체 보존 가운데 #11의 측정으로 #13에서 정하라 했으나 #11은 이 선택을 남기지 않았다.
- 권장: 조용히 자르지 않는다. 본문은 UTF-8 32 KiB까지 전부 보존하고, 직렬화한 줄 전체(이스케이프·참조·머리 포함)가 50 KiB 미만이어야 한다. 둘 중
  하나라도 넘으면 아무것도 쓰지 않고 `text_too_long`(본문·줄 바이트, 두 상한, 파일이나 Report로 저장하라는 안내)으로 거절한다(2.9절). 화면·맥락은
  #11의 생략 표시를 그대로 쓴다. 근거는 aelix read 도구가 50 KiB를 넘는 한 줄을 읽지 않는다는 사실(현재 계약 A)이다.
- 바뀌는 것: `MAX_EVENT_TEXT`와 `note_cut`, 현재 계약 C1의 4,000자 문장, 새 오류 이름 하나. 더 큰 상한(예: 1 MiB)을 고르면 그 줄을 에이전트가
  다시 읽지 못하고 맥락 블록·status가 매번 큰 줄을 읽는다.

**OQ3 원본 안의 링크·junction.** ADR-0005는 hardlink만 금지하고 큰 데이터도 복사가 기본이라 했다. 심볼릭 링크와 junction(예: `data/` → 외부 디스크)은
정하지 않았다.
- 권장: 밖을 가리키는 링크는 대상의 바이트를 복사하고(공유 바이트 없음, 사전 공간 검사), 그 링크를 매핑의 `links`에 적어 옛 run의 경로 추론이
  전환 뒤에도 같게 풀리게 한다(5.4절). 목적지는 그 대상과도 겹치지 않아야 한다(6.2절). 안을 가리키는 상대 링크는 글자가 루트 안에 머물 때만
  (`..`를 글자로 접어 루트 위로 나가지 않고 다른 링크를 지나지 않음, 6.6절) 같은 링크 글자로 다시 만든다. 그러면 목적지에서도 목적지 안으로 풀린다.
  글자가 루트 밖을 지나는 상대 링크(예: `/a/proj`의 `x -> ../proj/y`)는 형제 목적지 `/a/proj-new`에서 원본으로 풀리므로 다시 만들지 않는다. 그런
  링크, 다시 만들 수 없는 링크, 절대 링크는 파일이면 바이트 복사, 디렉터리면 복사하지 않고 보고서에 적는다(6.6절). 대상이 없는 바깥 링크는 OQ12.
- 바뀌는 것: 큰 외부 데이터의 디스크·시간 비용, 다시 만든 링크 아래 새 run의 정체성 경로, 매핑과 reader의 링크 대응, 현재 계약 G의 링크 문장, 측정
  항목. 대안 "밖을 가리키는 링크를 링크로 유지"는 목적지의 쓰기가 원본이 기록한 바이트에 닿을 수 있다. 대안 "그런 원본은 거절"은 그 프로젝트를 ③ 뒤
  읽기 전용에 가둔다.

**OQ4 복사 범위.** ADR-0005 결정 5는 "기존 파일은 같은 상대 경로에 바이트 그대로 복사"라고 적었다. 재생성 가능한 파일과 환경, git의 worktree
포인터에도 그대로 적용할지는 정하지 않았다. 빼면 글자 그대로의 범위를 좁히므로 소유자 확인이 필요하다.
- 권장: `.venv/`, `report/`, `.lab/cache/`, `.lab/`의 잠금 파일 셋, `__pycache__/`, 운영체제 메타 파일(`.DS_Store`·`Thumbs.db`·`desktop.ini`, 어느
  깊이에서나)은 복사하지 않고 보고서 `excluded`에 경로·파일 수·바이트를 적는다. 메타 파일을 빼는 이유: macOS 원본은 거의 늘 루트에 `.DS_Store`가
  있고 Finder가 목적지에도 같은 이름을 만들기 쉬워, 복사하면 그 경로의 게시가 `occupied`로 끝나고 Finder가 다시 만드는 동안 다시 불러도 같다. 원본
  전후 비교에서도 빠져 전환 중 Finder·탐색기가 원본을 열어도 `source_changed`가 되지 않는다.
  루트의 `.git`이 파일(git worktree의 `gitdir:` 포인터)이면 복사하지 않고 `not_copied`와 경고로 알린다. 복사하면 목적지가 원본 worktree의 index·HEAD를
  함께 써서 목적지의 git 작업이 원본의 git 상태를 바꾼다. 디렉터리 `.git/`을 포함한 나머지는 모두 복사한다. 원본 전후 비교에서도 제외 목록을 뺀다
  (T18은 `.venv/`·`uv.lock`을 따로 본다).
- 메타 파일을 복사하기로 하면(대안) 목적지의 `.DS_Store`가 계획 경로이면 사전 검사에서 `destination_invalid(not_empty)`와 그 파일로 거절하고(6.2절),
  전환 중 Finder가 만든 것은 `occupied`로 끝나는 한계를 적는다. 원본의 보기 설정(`.DS_Store`의 창 배치, `desktop.ini`의 폴더 아이콘 등)이 사본에
  없는 것이 권장 답의 비용이다.
- 바뀌는 것: 목적지 크기, 비교 범위, `.git/` 사본으로 저장소가 둘이 됨(보고서가 알림), worktree였던 원본의 목적지는 git 저장소가 아님(사람이 새로
  연결). 모두 복사하면 원본 절대 경로를 키로 쓰는 캐시와 다른 기계의 `.venv/`가 목적지에 남고, worktree 포인터는 위의 공유가 생긴다. `.git/`을 빼면
  원본을 지운 사용자가 이력을 잃을 수 있다.

**OQ5 `analytics.run/2`에서 바꿀 필드 이름과 enum 값.** ADR-0005 결정 2는 "바꾼다면 #13에서"라고만 했고, 지금 바꾸지 않은 것은 결정 3 때문에 베타까지
바꿀 수 없다. Issue #13 댓글은 `via` 값을 바꾼다고 적었으나 철자는 정하지 않았다.
- 권장: 계약이 이미 새 이름을 쓰는 것만 바꾼다. `charts[]`·`id` → `visualizations[]`·`visualization_id`(용어집 §5, ADR-0006 2절), 재실행 비교의
  `reproduction.charts` → `reproduction.visualizations`(값은 그대로), `via`의 `lab.` 접두 → SDK 함수와 같은 `analytics.visualize`·`analytics.read`·
  `analytics.scan`·`analytics.sql`·`analytics.save`, kind `gap` → `workflow_feedback`(ADR-0006). run `status`(→ `execution_status`),
  `reproduction`·`verdict`(→ `rerun_comparison`·`comparison_result`), `kit_*`는 그대로 두고 그 이유를 용어집에 적는다. ①이 이 가족의 reader를 먼저
  싣으므로 ① 병합 전에 정한다(2.6절).
- 바뀌는 것: `analytics.run/2`의 필드(2.6절), reader의 `/1` 대응과 재실행 비교의 형식 간 비교, Workspace·status의 읽기, #20 공유 투영의 필드 목록. 더
  바꾸면 그 필드마다 reader 대응과 화면이 늘어난다. ① 뒤에 바꾸면 ① kit이 ③의 프로젝트를 잘못 읽거나 `/3`이 필요하다.

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
- 권장: ③은 writer API(`Writer.update`, `supersedes` 참조, `Origin.request_id`)와 시험(8절), 응답의 `request_id`와 `--resolves`의 `state_before`·
  `state_after`까지 한다. 상태 변경·정정의 공개 입력은 #14가 지표·초점의 입력과 함께, `--request-id`·도구 인자는 #19가 연다. 그동안 reader(#12)가
  충돌과 어긋난 갱신을 보인다.
- 바뀌는 것: ③의 크기, 용어집의 새 입력 이름, 도구 schema 토큰. ③에서 열면 `analytics record … --supersedes eN`, 갱신 입력(예: `analytics update eN
  --expected S --new S TEXT`), `analytics_record`의 선택 인자를 이 설계에 더해 설계 리뷰를 다시 받아야 한다.

**OQ12 대상이 없는 바깥 링크.** 링크의 대상이 지금 없는 원본(예: `data -> /Volumes/X/data`인데 외부 디스크를 연결하지 않음)을 어떻게 할지 입력이 정하지
않았다(설계 리뷰의 새 질문 A).
- 권장: 사전 검사에서 `source_unreadable(reason=link_target_missing)`과 링크 경로·링크 글자 목록으로 거절한다. 사람이 대상을 연결하거나 링크를 치운
  뒤 다시 부른다. `--dry-run`은 이 목록을 맨 앞에 보인다. UTF-8로 쓸 수 없는 이름(POSIX의 짝 없는 surrogate, 현재 계약 G가 기록에 담는 방법을 정하지
  않은 것)도 같은 방식으로 `source_unreadable(reason=name_not_utf8)`과 목록으로 거절한다.
- 바뀌는 것: 그런 프로젝트는 고칠 때까지 전환할 수 없다(③ 뒤 읽기 전용에 더 오래 머문다, R19). 6.2·6.6절의 표, T1·T2, 현재 계약 G의 링크 문장.
  대안 "복사하지 않고 커밋"(이전 설계안)은 링크가 조용히 사라진 프로젝트를 완전한 것처럼 보인다. 대안 "UTF-8이 아닌 이름을 base64 필드로 기록"은
  계획·ledger·보고서의 형식과 reader를 넓힌다.

**OQ13 읽기 명령의 uv 환경 동기화.** ③ 뒤 `lab.json` 프로젝트에서 런처·확장이 `status` 같은 읽기 명령을 `uv run --project SRC`로 부르면 uv가 원본에
`.venv/`를 만들거나 맞지 않는 `uv.lock`을 다시 쓴다(프로브 `probe-safety`). 이것이 ADR-0005 결정 6의 "읽기 작업의 부수 쓰기"인지 입력이 정하지
않았다(설계 리뷰의 새 질문 B).
- 권장: 기록이 아니라 환경이므로 게이트하지 않는다(4.3절). 현재 계약에 "읽기 전용은 uv 환경 동기화를 포함하지 않는다"를 적는다. 결정 6의 목록은
  "이 목록으로 닫히지 않는다"고 했으므로 이 답은 그 목록에서 환경을 빼는 해석이며, 좁힘으로 읽힐 수 있어 소유자가 정한다. 전환의 원본과 표식 없는
  목적지는 지금 설계대로 `--no-project`로 다룬다(6.1절).
- 바뀌는 것: 코드는 없고 현재 계약의 문장 하나. 대안 "읽기 명령을 `--no-project`로 부름"은 분석 라이브러리가 필요한 `status`·`query`를 막는다.

**OQ14 에이전트가 부른 전환.** 모델 도구와 `/analytics migrate`는 만들지 않지만(6.1절), aelix 세션의 에이전트는 bash 도구로 `analytics migrate`를
부를 수 있고 kit은 그것을 `actor=agent`·`declared`로 안다(OQ1). 전환은 새 프로젝트를 만들고 그 경로를 신뢰 목록에 더한다. 이 호출을 받을지 입력이
정하지 않았다(수정 diff 확인의 지적). ADR-0005 결정 5는 "명시적인 명령으로만 전환한다"고만 적었고 누가 부르는지는 정하지 않았다.
- 권장: 받는다. ledger의 `start`, 보고서의 `actor`·`actor_basis`·`means`, 응답에 `agent`·`declared`·`cli`로 남긴다. 근거: 에이전트의 bash 호출도 조회·
  열기가 아닌 명시적인 명령이다. 원본은 보존되고 목적지는 새·빈 디렉터리뿐이다. 지금도 모델 도구 `analytics_init`이 에이전트가 고른 경로에 프로젝트를
  만들고 신뢰 목록에 더한다(현재 계약 B). 거절은 aelix 세션 안에서 선언된 `agent`에만 들고, aelix 밖 에이전트(`human`·`inferred`)나 변수를 지운
  호출은 막지 못한다(작성 주체는 출처이지 인증이 아니다, 4.2절).
- 바뀌는 것: 받으면 6.14절의 신뢰 목록 문장은 "명시적인 명령이 고른 경로"이고(에이전트가 고른 전환 목적지 포함), T14가 ledger·보고서의 작성 주체를
  본다. 거절하면 새 오류 코드 하나(용어집에 먼저)를 `actor=agent`·`declared`인 `migrate`의 첫 부수 효과 전에 내고 "사람이 터미널이나 `!`로 부르라"고
  안내하며, 4.11절·6.1절·진입점 행렬(11.3절)에 행이 늘고, 그 거절은 aelix 세션 안에서만 효과가 있다고 현재 계약에 적는다.
- 채택된 보장을 좁히는가: 아니오. 결정 5의 "명시적인 명령"을 그대로 따른다. 거절을 고르면 결정에 없는 제한을 더한다(좁힘이 아니라 더함).

**OQ15 잠금을 잡을 수 없는 원본.** ADR-0005 결정 5는 "복사하는 동안 원본의 journal·run 잠금(옛 kit과 같은 잠금)을 잡는다"고 적었다. 두 경우는 잠금을
잡을 수 없다: 원본에 `.lab/`이 없을 때(잠금 파일의 부모가 없어, 만들려면 원본에 디렉터리를 더해야 한다), 다툼이 아닌 이유로 잠금 파일을 만들거나 열지
못할 때(읽기 전용 원본·매체의 `EROFS`·`EACCES`). 결정 4는 잠금 말고는 원본에 쓰지 않게 한다. ADR-0005는 "이 결정의 내용을 바꾸지 않는다"고 했으므로
설계 리뷰가 정할 수 없다(수정 diff 확인의 지적).
- 권장(D10): 두 경우 잠금 없이 진행하고 ledger·보고서에 `locks: not_taken`과 이유를 적는다. 결정 5의 보장("복사 전후로 원본의 파일 목록이나 해시가
  다르면 실패")은 7단계의 비교가 그대로 지킨다. 읽기 전용 매체는 옛 kit도 쓸 수 없으므로 실제 차이는 `.lab/`이 없는 원본에서만 생긴다.
- 바뀌는 것: 받으면 ADR-0005 결정 5에 "잠금을 잡을 수 없는 원본은 전후 비교로만 지킨다"는 예외를 적는 개정이 필요하다(이 설계는 결정을 바꾸지
  않는다). 그 원본에서는 옛 kit의 쓰기가 기다리지 않아 전환이 `source_changed`로 끝날 수 있다. 대안 (a) 거절: 새 거절 사유 하나(용어집 먼저), 읽기
  전용 원본은 사람이 먼저 쓸 수 있는 곳으로 복사한 뒤 전환해야 한다. 대안 (b) `.lab/`이 없으면 `.lab/`과 잠금 파일을 만든다: 디렉터리 만들기가 결정
  4의 "잠금을 잡는 것"에 드는지의 해석이 필요하고 읽기 전용의 경우는 남는다.
- 채택된 보장을 좁히는가: 예. 결정 5의 "잠금을 잡는다"를 그 두 경우에 지키지 않는다. 결과의 보장(바뀌면 실패)은 좁히지 않는다.

**OQ16 이어 쓰기에서 보고서가 적지 않은 환경·메타 파일.** ADR-0005 결정 5는 "목적지에 전환 보고서가 적은 파일만 있고 해시가 맞을 때만 잇는다.
보고서에 없거나 해시가 다른 파일이 있으면 덮어쓰지 않고 거절한다"고 적었다. 중단된 목적지에는 사람의 도구가 기록이 아닌 파일을 만들 수 있다:
Finder·탐색기의 `.DS_Store`·`Thumbs.db`·`desktop.ini`(목적지를 열어 보기만 해도), 목적지의 스크립트를 import하면 `__pycache__/`, 사람이 uv를 부르면
`.venv/`. 이것을 결정 5의 글자대로 거절할지 입력이 정하지 않았다(수정 diff 확인의 지적).
- 권장(D11): 계획에 없는 이 이름들(`.venv/`·`__pycache__/`와 세 메타 파일, 어느 깊이에서나)은 무시하고 잇는다. 무시한 파일을 지우거나 덮어쓰지 않고
  사본 검증·전후 비교에서도 뺀다. 계획 경로를 이런 파일이 차지하면 무시하지 않고 `destination_mismatch(occupied)`다. OQ4의 권장 답(메타 파일을
  복사하지 않음)과 함께 읽는다.
- 바뀌는 것: 받으면 ADR-0005 결정 5에 이 이름들의 예외를 적는 개정이 필요하다. 받지 않으면 이 파일들도 `destination_mismatch`이고 오류가 그 경로를
  보이며, 사람이 지운 뒤 다시 부른다. Finder로 목적지를 열어 둔 동안에는 `.DS_Store`가 다시 생겨 같은 거절이 되풀이될 수 있다.
- 채택된 보장을 좁히는가: 예. "보고서에 없는 파일이 있으면 거절한다"를 이 이름들에 대해 좁힌다. "덮어쓰지 않는다"는 그대로다.

---

## 17. 두 설계안에서 가져온 것

같은 입력으로 쓴 두 안을 다섯 완료 조건, 원본 바이트 보존, ADR 충실도, 옛·새 프로젝트의 읽기 호환, 시험 가능성, PR 크기, 제품 결정을 지어내지 않음으로
비교했다. 원본 보존을 앞세운 안이 #13의 가장 큰 위험(원본 손실과 반쯤 쓴 목적지)을 더 단단히 막고 보장마다 시험을 짝지어 바탕으로 삼았다.

- 원본 보존 안에서 가져온 것: 보장 S·시험 T의 짝, 원본 잠금 전에 쓰는 ledger, 기다리지 않는 잠금과 목적지 잠금, 계획·복사·재확인 동안만 쥐는 원본
  잠금, 파일마다 임시 파일 + 교체, 표식 전 내구 쓰기, `source-marker.json`, 표식의 `migration` 필드, `migration_incomplete`, 중단 지점 표,
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

- 코드 기준: `09d22c4`(PR #76 병합 뒤). 확인한 것: `core.find_project`(`lab.json`만, 시작 경로에서 위로 올라감), `__main__._project`·SDK `__init__`의
  탐색(올라감)과 확장 `_resolve_project`의 명시한 루트(그 디렉터리만), `core.read_events`(`lab.event/1`만), `Project.config`(같은 가족 번호만),
  `core.KIT_VERSION`을 읽는 곳(`tools/model_eval.py`의 정규식, `tests/test_packaging.py`), `PosixFileLocks.exclusive`의 부모 디렉터리 생성,
  `PosixAtomicFiles.replace`·`WindowsAtomicFiles.replace`가 `os.replace`(덮어씀)라는 것, 런처의 `init`이 쓰는 `uv run --no-project`와 다른 명령의
  `kit_argv`(`uv run --project`), `relations.read(…, legacy_path=)`와 `ReadModel.read_as`·`_mapped`의 옛 kit 조건, `Project.legacy_identity`가 바깥
  절대 경로를 경계표로 푼다는 것, `relations.ReadModel.imports`(meta와 이벤트 사이만 `incomplete`), run 기록의 `charts`를 직접 읽는 곳(5.3절의 목록,
  `runner.compare_runs` 포함), `core.MANAGED_DIRS`·`IGNORED_DIRS`와 `runner._in_managed_dir`, `core.classify_refs`(순수 함수)와 `resolve_ref_aliases`
  (`Project` 필요), 확장의 `child_env`(허용 목록과 작성 주체를 직접 넣음)와 `STATE["session"]`을 정하는 `before_agent_start`, `relations.CONTEXT_DIRS`,
  기록 schema의 `project_2`(추가 필드 허용), 템플릿 `gitignore`, ADR-0007 결정 2의 헬퍼 위치, 현재 계약 G의 UTF-8로 쓸 수 없는 이름.
- 프로브(모두 macOS Darwin arm64, uv 0.11.19, 실행 폴더 안, `AELIX_ANALYTICS_TRUST_FILE`·`AELIX_ANALYTICS_UV`·uv 캐시를 실행 폴더 안으로, `AELIX_LAB_*`·
  `LAB_*` 해제, `~/.aelix`를 읽거나 나열하거나 건드리지 않음). 파일은 실행 폴더의 `evidence/` 아래에 있다.
  - `probe-current-writer`(`ebb6fc8`, `probe.txt` sha256 `907eae44…4fb5`): P1–P7. 표식을 `lab.project/2`로 고쳐도 `record`가 `lab.event/1`을 씀(P2),
    `analytics.json`만이면 `no_project`(P3), `lab.json` 프로젝트 아래 `analytics.json` 디렉터리의 `record`가 위쪽에 씀(P4), 두 표식을 `lab.json`으로 봄(P5),
    `/2` 줄이 목록에서 빠짐(P6), 끊긴 줄 뒤에 붙여 쓰고 seq 재사용(P7).
  - `probe-safety`(`probe.txt` sha256 `457d9ba7…49a9`): `uv run --project`가 `.venv/`·`uv.lock`을 만들고 맞지 않는 `uv.lock`을 다시 씀, `--frozen --no-sync`도
    `.venv/`를 만듦, 프로젝트 안의 `uv run --no-project`는 아무것도 만들지 않음, kit의 잠금이 없던 `run.lock`을 만듦, `flock(LOCK_EX | LOCK_NB)`가 잡힌
    동안 `EAGAIN`(35)·놓은 뒤 획득.
  - `probe-uv-no-project`(`probe.txt` sha256 `5cf601a9…e29e1`): 같은 결과를 따로 확인.
  - `probe-uv-locked`(`probe.txt` sha256 `d7e00f59…b2f7`): `uv sync --locked`는 맞는 `uv.lock`을 바꾸지 않았고, 맞지 않는 `uv.lock`에서는 종료 1로
    실패하며 그 파일을 바꾸지 않았다. 실패한 경우에도 `.venv/`는 만들었다.
  - `r9-probe`(2026-10-08, `probe.txt` sha256 `ada42b31…3d353`, `probe.sh`·`probe.py`·`notes.txt`): aelix 0.1.0b2를 `uv run --isolated --no-project
    --python 3.12 --with aelix==0.1.0b2`로 격리 설치하고, `env -i`로 가짜 HOME·`AELIX_CODING_AGENT_DIR`를 실행 폴더 안에 둔 채 aelix의 bash 도구 실행
    함수를 프로세스 안에서 불렀다(모델·로그인 없음, 설치 뒤 네트워크 없음). 본 것: `os.environ`에 도구를 만들기 전에 넣은 값과 만든 뒤 바꾼 값을 자식이
    그대로 봄, 지운 뒤에는 보지 않음, 사람의 `!` 명령(`cli/repl.handle_user_bash`)도 같은 값을 봄, `user_bash` 처리기가 돌려준 실행기로 그 명령만
    `human`을 봄(모델의 bash 도구는 여전히 `agent`), `create_bash_tool`의 `spawn_hook`은 동작하지만 확장이 닿을 수 없는 선택지. D1·E1은
    `handle_user_bash`에 hook 발행만 흉내 낸 대역(`FakeHarness`: `hooks.emit`이 준 결과를 바로 돌려줌)을 넘겨 실행했다. 확장 등록(`on("user_bash")`)에서
    `_reducer_user_bash`까지의 전달은 실행하지 않고 코드로 읽었다(마지막 결과만 씀, `hooks.py:1788-1805`). 수정 diff 확인이 같은 프로브를 오프라인으로
    다시 돌려 A1–F1이 `probe.txt`(sha256 `ada42b31…3d353`)와 같았다고 보고했다(이 문서의 작성자가 다시 돌리지는 않았다). 코드 위치(설치한 패키지
    기준): `aelix_coding_agent/util/shell_env.py:17-44`, `tools/bash.py:247`·`263-293`·`752-762`·`967-979`·`1033`·`1044-1051`, `cli/entry.py:961-985`·
    `1406`, `cli/repl.py:30-63`, `rpc/rpc_mode.py:571-584`, `extensions/api.py:1616-1623`·`2163-2165`, `aelix_agent_core/harness/hooks.py:296-306`·
    `416-431`·`939-950`·`1788-1805`, `aelix_agent_core/harness/core.py:795-801`·`3989-4000`. Windows 분기는 읽기만 했다: `tools/bash.py:121-122`·
    `148-170` → `aelix_ai/utils/_shell.py:473`(`$SHELL`이 있는 파일일 때만, 아니면 PowerShell, 그다음 `%COMSPEC%`), 어느 셸이든 같은 환경을
    `Popen(env=…)`에 넘긴다(`tools/bash.py:293`). 가짜 HOME은 비어 있었다.
- 수정 diff 확인 뒤 더 읽은 것(`09d22c4`, 실행 폴더의 aelix 0.1.0b2 설치본): 재실행 비교 결과를 직접 읽는 곳(`extension/aelix_analytics.py`의 run 결과
  줄, `report.py`의 run 페이지)과 `tools/usage_report.py`의 `run_items(truth.run, "charts")`, `core.init_project`가 빈 디렉터리로 `.DS_Store`만 받는다는 것과
  init이 신뢰 목록에 더한다는 것(`analytics_init` 도구도 이 함수를 씀), `core._under`의 구성 요소 단위 판정, `_reducer_user_bash`가 마지막 결과만 쓰고
  `UserBashHookEvent`에 앞 처리기의 결과가 없다는 것, hook 버스·확장 실행기의 `has_handlers`(확장 API로 닿는지는 확인하지 않음).
- 이 문서의 검증: 문서 검사(`uv run --locked python tools/check_docs.py`)와 `git diff --check`. 이것은 링크·형식의 검사이며 설계의 시험이 아니다.
- 미실행(통과로 보지 않음): pytest·ruff·CI 전체, Windows·Linux에서의 위 프로브, R9 프로브의 Windows(PowerShell·cmd 자식)와 실제 aelix 세션·모델,
  Windows의 잠금 해제 지연·디렉터리 flush·읽기 전용 사본의 flush·읽기 전용 핸들의 `msvcrt.locking`·디렉터리별 대소문자 구별의 상속, 링크를 지원하지
  않는 목적지 파일 시스템(FAT·exFAT)의 `os.link` 실패, 전원 손실, plotly 템플릿 이름이 저장 바이트에 들어가는지, M 규모 생성과 전환·writer 시간,
  이 문서의 모든 시험(T1–T22, 코드가 없음), 확장의 실제 등록으로 도는 `user_bash` 경로와 두 확장의 등록 순서(T22), 수정 diff 확인의 지적을 고친 뒤의
  다시 확인(리뷰 예산은 수정 diff 확인을 한 번으로 정한다).

---

## 19. 리뷰의 지적과 처리

### 19.1 설계 리뷰

설계 리뷰(독립 1회·Codex 1회)의 지적마다 처리와 고친 곳이다. 꼭 고칠 것(M)은 모두 고쳤다. 검토할 것은 모두 적용했고, 리뷰가 낸 대안과 다르게
적용한 것은 그 이유를 적었다. 그 수정은 19.2절의 수정 diff 확인을 받았다.

| 지적 | 처리 | 고친 곳 |
|---|---|---|
| M1 목적지 겹침 검사가 바깥 링크 대상을 보지 않음 | 고침. 복사가 읽는 실제 위치의 집합 R(원본 루트와 따라갈 바깥 링크 대상)과 목적지가 같거나 안이거나 위이면 `overlaps_source` | 6.2절, S1, 4.11절, T1·T2 |
| M2 매핑에 링크 경계가 없어 옛 run의 경계 추론이 사라짐, `.lab/` 접두가 절대 경로에만 적용 | 고침. 매핑에 `links`(경로, 대상의 실제 경로와 철자, 처리), reader가 PR #76의 조건 그대로 대응, 상대 `.lab/…`에도 접두 적용 | 5.4·6.6·6.7절, S4, T11 |
| M3 이어 쓰기 규칙이 중단 표와 모순(변환 미기록, 재확인 뒤 원본 재비교, `committed`만 있는 상태, 표식 뒤 flush 없음) | 고침. `transform_begin`·`transformed`, `source_checked` 뒤 원본을 보지 않음, `committed`인데 표식이 없으면 ledger의 표식 바이트를 다시 게시, 표식 게시 뒤 `flush_dir` 다음에 `committed`와 응답, 정의한 상태만 받음 | 6.3·6.8·6.10·6.11절, T6 |
| M4 `already_migrated`가 목적지 표식 판정과 원본 정체성을 보지 않음 | 고침. 다시 부르기는 목적지 `marker_state`를 먼저 판정하고, 같은 원본의 커밋된 전환일 때만 `already_migrated`, 다른 원본이면 `not_empty` | 6.9절, 4.11절, T21 |
| M5 사본 게시가 덮어쓰는 `os.replace` | 고침. 새 포트 메서드 `publish_new`(POSIX `os.link`+`unlink`, Windows `os.rename`), 임시 파일 `O_EXCL`, 교체는 이 전환의 파일에만, 표식도 없을 때만 게시 | 6.3·6.4·6.12절, S2·S6, D19, T19 |
| M6 Visualization 소비자 목록이 모자라 재실행 비교가 거짓 기록을 남김 | 고침. `relations.run_visualizations` 하나를 모든 소비자가 쓰고 정적 검사로 막음, 형식 간 재실행 비교와 `reproduction.visualizations`(OQ5) | 2.6·5.3·10절, S13, D20, T9·T11 |
| M7 관리 디렉터리를 두 형식의 합집합으로 둠 | 고침. 배치마다 따로(`LegacyLayout`·`Layout`) | 2.3·4.4·5.3절, S14, D21, T20 |
| M8 OQ1의 한계 갈래가 Q17-b·ADR-0008 결정 7을 말없이 좁힘, R9 미실행 | 고침. R9 프로브를 실행했고 권장 답이 Q17-b를 지킴. aelix 밖 에이전트의 경우를 좁힘인지 소유자가 정할 질문으로 따로 적고 #17의 `inferred` 처리를 정함 | 4.2절, 16절 OQ1, 18절, S11, R9·R18, T22 |
| 검토 1 32 KiB 본문이 50 KiB 줄을 보장하지 않음 | 적용. 직렬화한 줄 전체의 상한을 함께 검사, 이스케이프가 많은 본문과 긴 참조로 시험 | 2.9절, OQ2, 4.11절, T14 |
| 검토 2 보고서가 커밋 뒤의 값을 담음 | 적용. `committed_at`·flush 시간을 보고서에서 빼고 `source_checked_at`과 복사한 journal 경계를 더함. 커밋 시각은 ledger에만 | 6.3·6.8절, S3 |
| 검토 3 전원 손실 내구성을 `os._exit`로 증명하지 못함 | 적용. 프로세스 중단 보장과 전원 손실 목표를 나누고 후자는 한계와 `not_run` | 1절, 6.4·6.11·6.12절, 12·18절, D9 |
| 검토 4 Windows 읽기 전용 파일의 flush·지우기 | 적용(설계, 실행하지 않음). 게시 전 임시 핸들로 flush, 게시 뒤 권한, 디렉터리 권한은 나중, 지우기 전 읽기 전용 해제 | 6.4·6.12절, 11.4절 |
| 검토 5 특수 항목과 이름 정규화(FIFO, Windows 장치 이름) | 적용. 열기 전 `stat`, POSIX `O_NONBLOCK`(기능 확인), 연 핸들의 `fstat` 정체성 비교, 플랫폼 분기 없음 | 6.4·6.6절 |
| 검토 6 Windows에서 교체 중인 표식 읽기 | 적용. 없음과 읽기 실패를 구별하고 교체 예산으로 재시도한 뒤 `invalid(unreadable)` | 4.3·4.11절, D27, 11.4절 |
| 검토 7 이어 쓰기가 마지막 바이트를 담은 임시 파일을 지울 수 있음 | 적용(리뷰의 "모두 남김"보다 좁게). 그 경로를 다시 복사해 게시한 뒤에 지우고, 다시 만들 수 없으면 남겨 `leftover_temp`로 보고. 모두 남기면 커밋한 프로젝트마다 임시 파일이 쌓인다 | 6.8·6.10절, T6 |
| 검토 8 가져오기 중단의 `incomplete` 문장이 넓음 | 적용(문장을 좁힘). 준비 상태 설계는 완료 조건 밖이고 main과 같은 한계라 더하지 않음 | 4.8절 |
| 검토 9 이름 충돌을 무조건 거절 | 적용. 목적지가 실제로 이름을 접을 때만 거절(시험 파일로 판정), 임시 이름은 계획 이름과 겹치지 않게 고름 | 6.2·6.3절, D26, T2 |
| 검토 10 UTF-8로 쓸 수 없는 이름 | 적용(거절 쪽). 사전 검사에서 `source_unreadable(name_not_utf8)`과 목록. 같은 종류의 거절이라 OQ12에 묶어 소유자에게 묻는다 | 6.2절, 4.11절, OQ12, T2 |
| 검토 11 다툼이 아닌 잠금 실패, 잠금 없는 진행이 결정 5의 수단과 다름 | 적용. `LockUnavailable`이면 잠금 없이 비교로만, 결정 5의 수단과 다르다는 것을 명시 | 6.3·6.12절, D10, T1 |
| 검토 12 목적지의 운영체제 메타 파일 | 적용. 계획에 없는 `.DS_Store`·`Thumbs.db`·`desktop.ini`는 무시, 계획 경로를 차지하면 `occupied` | 6.2·6.10절, T6 |
| 검토 13 `source-marker.json`을 잠금 밖에서 만듦 | 적용. 6단계에 잠금 안에서 `begin`·`copied`로 복사 | 6.3·6.5절 |
| 검토 14 `.git`이 worktree 포인터 파일 | 적용. 복사하지 않고 보고, 복사 범위이므로 OQ4에 넣음. `.git/` 사본에서 `.lab/` 파일이 옮겨진 것으로 보인다는 안내 | 6.5·6.6절, OQ4, T1 |
| 검토 15 명시한 루트가 올라가지 않게 바뀜 | 적용. 진입점마다 지금 규칙을 그대로 둠 | 4.3절, D13 |
| 검토 16 LF 헬퍼 위치가 ADR-0007 결정 2와 다름 | 적용. `_platform`으로 옮김 | 3.1·3.2·4.4절, D22 |
| 검토 17 ①이 `/2` reader를 먼저 싣음 | 적용. OQ5·D2·D4를 ① 병합 전에 정함 | 2.6·10절, OQ5 |
| 검토 18 `--resolves`로 같은 질문을 두 번 닫음 | 적용. 응답에 `state_before`·`state_after`, 거절로 바꾸지 않음(지금 동작, ADR-0006 6절) | 4.5·4.6·8절, D27, T13 |
| 검토 19 D 목록에 빠진 선택 | 적용. D23(uv 환경)·D24(`data/imported` 우선)·D25(분류되지 않은 참조)·D26(이름 충돌)을 더하고 명시한 루트는 D13 | 15절 |
| 검토 20 수준 1의 writer가 core를 쓸 수 없음 | 적용. `classify_refs`는 writer로, 이름 풀기와 결박은 `relations`, 옛 경로 추론은 `legacy_path=` 주입 | 3.2·4.5절, D22 |
| 검토 21 깨진 바깥 링크를 복사하지 않고 커밋 | 적용. 사전 검사에서 거절하는 쪽을 권장하고 OQ12로 물음 | 6.2·6.6절, OQ12 |
| 새 질문 A 깨진 바깥 링크 | OQ12로 더함(권장 답, 바뀌는 것) | 16절 |
| 새 질문 B 읽기 명령의 uv 환경 동기화 | OQ13으로 더함(권장 답, 바뀌는 것, 결정 6의 해석이라는 점) | 4.3·7·16절 |

### 19.2 수정 diff 확인

19.1절의 수정(`ff866ce` → `135445d`)에 대한 리뷰 예산의 수정 diff 확인(한 번)의 지적이다. 꼭 고칠 것은 모두 고쳤고, 검토할 것은 모두 적용했으며
그중 채택된 글자와 다르거나 입력이 정하지 않은 것은 소유자 질문으로 옮겼다. 사소한 것도 모두 적용했다. 고친 뒤 다시 확인은 받지 않았다(리뷰 예산).

| 지적 | 처리 | 고친 곳 |
|---|---|---|
| 꼭 고칠 것 1: ledger가 적지 않은 중단 창(9단계 임시 파일이 `transform_begin`보다 먼저, 11단계 임시 파일이 `marker_begin`보다 먼저, `start` 전의 전환 디렉터리, 직접 쓰기의 만들기와 `direct` 사이, `transform_begin` 임시 파일이 지우는 목록에 없음) | 고침. ledger 먼저 원칙(목적지에 만드는 모든 파일은 이름·바이트·해시를 먼저 적고 `O_EXCL`로 만듦), `transform_begin`·`marker_begin`을 임시 파일보다 먼저, 내용 없는 전환 디렉터리는 새 전환, 직접 쓰기의 `direct_intent`와 그 창의 거절(R20), 끊긴 ledger 줄은 건너뜀, 지우는 목록에 `transform_begin` 임시 파일과 내용 없는 전환 디렉터리, F 표에 F15·F16 | 0절 10, S6, 6.3·6.4·6.8·6.10·6.11·6.12절, R20, D29, T6(k)–(n) |
| 꼭 고칠 것 2: 다시 부르기가 원본 쪽 검사를 먼저 해 재확인 뒤 이어 쓰기·신뢰 보충을 막음(빠진 바깥 디스크, 같은 디스크의 공간, 지운 원본), 9단계가 원본으로 매핑 철자를 정함 | 고침. 검사 순서(목적지 먼저 읽기만 판정 → 원본을 다시 읽을 상태에서만 원본 쪽 검사 → 남은 바이트의 공간 검사 → 잠금 안 재판정), 원본 없이 부르는 호출(`source_checked` 뒤·커밋된 목적지만), 매핑 철자를 7단계 잠금 안에서 정해 `source_checked` 줄에 적음 | 0절 6·11, 6.1·6.2·6.3·6.7·6.9·6.10·6.11절, D28, T6(c)(f), T21 |
| 검토할 것 1: `reproduction.visualizations`의 reader가 목록에 없음 | 적용. `relations.run_reproduction` 하나를 확장 run 결과 줄과 Workspace run 페이지가 씀, 정적 검사를 `reproduction`의 `charts`로 넓힘, `tools/usage_report.py`의 직접 읽기를 목록에 더함 | 0절 12, S13, 2.6·5.3·10절, D20, T9·T11 |
| 검토할 것 2: 루트 밖을 지나 안으로 돌아오는 상대 링크를 다시 만들면 목적지가 원본에 닿음 | 적용. 글자를 `..`까지 글자로 접어 루트 안에 머물고 다른 링크를 지나지 않을 때만 다시 만들고, 아니면 절대 링크처럼(대상이 없으면 바깥 링크로) 다룸. 형제 목적지 시험 | 6.6절, OQ3, D29, T2 |
| 검토할 것 3: 목적지의 `.DS_Store`를 사전 검사가 받아 복사가 늘 `occupied`로 끝나는 회귀, init과 다른 빈 디렉터리 목록 | 적용. 빈 디렉터리 판정을 init과 같게(루트의 `.DS_Store`만, 같은 상수). 운영체제 메타 파일을 복사하지 않는 것을 OQ4의 권장 답에 넣고(소유자 결정), 받지 않으면 목적지의 `.DS_Store`가 계획 경로일 때 사전 검사에서 `not_empty`로 거절 | 6.2·6.5·6.8절, OQ4, T2 |
| 검토할 것 4: `user_bash` 실행기가 다른 확장(샌드박스 등)의 실행기를 밀어내는 방향이 빠짐 | 적용. 두 방향을 4.2절·R18·OQ1에 적음. 권장 완화: kit을 부르는 `!` 명령에만 실행기를 돌려주고 남는 범위를 한계로 적음. 대안은 처리기를 두지 않음. API로 다른 처리기를 알 수 있는지는 확인하지 않음 | 4.2절, R18, OQ1, T22 |
| 검토할 것 5: 에이전트가 부른 전환을 받는다는 제품 결정을 입력 없이 정함, 결정 5를 "사람의 명령"으로 인용 | 적용. 인용을 "명시적인 명령"으로 고치고 OQ14(권장: 받고 기록)로 옮김. 6.14절의 "사람이 고른 경로"를 지금 동작(`analytics_init`도 신뢰 목록에 더함)에 맞게 "명시적인 명령이 고른 경로"로 고침 | 6.1·6.14절, OQ1·OQ14 |
| 검토할 것 6: 결정 5의 글자와 다른 두 선택(잠금 없는 진행, 이어 쓰기의 무시 목록)을 설계 리뷰에 맡김 | 적용. OQ15·OQ16으로 옮기고 둘 다 좁힘이며 받으면 ADR-0005 개정이 필요하다고 적음. D10·D11은 권장 답으로 남김 | 0절 7, 6.3·6.10절, 15절, OQ15·OQ16 |
| 사소한 것 1: R9 E1이 대역 hook으로 돌았다는 것 | 적용. 대역으로 실행했고 등록에서 합치기까지는 코드로 읽었다고 적음. T22가 실제 등록으로 돎 | 4.2·18절, OQ1, T22 |
| 사소한 것 2: 6.9절에 표식이 `marker_begin`과 달라진 커밋 전 목적지의 행이 없음, POSIX의 link·unlink 사이에 남은 표식 임시 파일 | 적용. `marker_rewritten` 행(`project_id`·`migration`이 ledger와 같으면 `already_migrated`와 경고), `already_migrated`가 표식과 같은 inode·같은 바이트의 임시 파일만 지움 | 6.9·6.11절, T21 |
| 사소한 것 3: 매핑의 접두 맞춤이 글자 접두, 표식을 다시 쓸 때 `migration`의 보존이 없음 | 적용. 구성 요소 단위(`core._under`와 같은 접기), 표식을 다시 쓸 때 `min_writer_version`·`kit_version`만 바꿈 | 4.8·5.4절 |
| 사소한 것 4: 루트·`.analytics/`의 권한 복사, Windows의 읽기 전용 `.gitignore` 교체, 보고서를 다시 쓸 때의 `project_id`와 `reported` | 적용. 두 곳에 권한을 옮기지 않음, 교체 전 읽기 전용 해제와 뒤의 복원, `project_id`를 `plan` 줄에, 다시 쓸 때마다 새 `reported`, ledger 줄의 `at` | 6.3·6.4·6.8·6.10절, T6(o) |
| 사소한 것 5: S6과 지우는 목록의 어긋남, T6의 `tree_digest`와 (h), ①에 계획 디렉터리 없음, D23이 OQ13을 설계 리뷰 항목으로 되풀이 | 적용. S6을 지우는 목록에 맞춤, T6에 (h)의 예외, ①에 계획이 적은 디렉터리, D23은 OQ13을 따른다고 적음 | S6, 6.10절, 15절, T6 |
