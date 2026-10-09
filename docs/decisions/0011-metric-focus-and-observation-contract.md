# ADR-0011 — 분석 초점·지표 정의·관측의 최소 계약

작성: 2026-10-09 · 상태: Proposed · 작업: [#14](https://github.com/handochan/aelix-analytics/issues/14)

[지표 중심 분석 설계](../METRIC-DRIVEN-ANALYSIS.md)의 M0·최소 M1을 구현하기 위한 구체 계약이다.
이 ADR이 채택·병합되고 구현 증거가 기록되기 전에는 아래 입력과 읽기 보장이 현재 기능이 아니다.
기반은 [ADR-0006](0006-common-record-contract.md)의 `analytics.event/2`·정확한 버전 참조·덧붙이기·상태 재생과
[#13 writer 설계](../design/RECORD-WRITER-AND-MIGRATION.md)다. 새 계산 엔진이나 분석 승인 절차를 추가하지 않는다.

## 1. 결정할 문제와 범위

현재 writer는 지표·초점 kind를 읽지만 공개 기록 입력은 이를 쓰지 못한다. 자유 Python 실행은 유지하면서
미정 후보에서 정의·관측·방향 변경으로 이어지는 관계를 같은 journal 정본에 기록해야 한다.

이 Issue는 구조화된 정의·초점·관측의 쓰기·읽기, run 시작의 초점 결박, CLI·SDK·기존 `analytics_record` 도구의
공통 입력을 구현한다. #15의 검색·제한 상세 조회, #16의 checkpoint, #18의 상세 관계 UI는 각 후속 Issue다.
Workspace의 기존 타임라인·공통 참조 판정은 유지하고, 현재 초점과 정의·관측을 기본 status에서 읽을 수 있게 한다.

## 2. 버전과 보존

기존 kind `metric_definition`, `analysis_focus`, `metric_observation`과 기존 참조 유형을 사용한다.
family는 `analytics.event/2` 그대로이며, 새 상세 계약의 tag는 `details.contract`에 각각
`metric_definition/1`, `analysis_focus/1`, `metric_observation/1`이다.
기존 `/1`·tag 없는 최소 `/2` 기록의 바이트와 읽기는 유지한다. tag 없는 기록을 새 구조화 계약으로 추정하지 않고
`unstructured`로 구분한다. 미지원 tag·잘못된 구조화 payload·중복 ID는 원본 위치와 상태를 표시한다.
스키마는 이 tag가 있을 때에만 새 상세 조건을 적용한다.

구조화 기록을 처음 쓰는 잠금 안에서 `min_writer_version`을 구현 kit 버전(예정 `0.9.0`)으로 올린다.
#17과 같은 배치의 버전 갱신은 순차 병합 때 조율한다. 낮은 writer가 이 기록을 이해하지 않은 채 쓰는 것을 gate가 거절한다.
과거 결과·부모 정의·run 목적의 참조를 현재 정의나 현재 초점으로 덮어쓰지 않는다.

## 3. 공통 입력과 오류

`record(kind, text, details=..., refs=...)`가 기존 기록 경로다. CLI는 `record <kind> <text> --details-json <JSON>`을
추가하고, SDK `analytics.record`와 `analytics_record` 도구는 `details` 객체와 정확한 typed `refs`를 받는다.
run 밖 SDK는 작성된 이벤트 ID를 포함한 응답을 반환한다. run 안의 기록은 최종 이벤트 ID가 아직 없으므로
`pending=true`와 현재 run ID를 반환하고 최종 쓰기 오류는 run의 `finalize_errors`에 남긴다.
CLI의 `--ref`는 기존 이름 입력을 유지한다. typed refs가 필요한 경우 `--refs-json <JSON array>`를 받는다.
모델에 지표별 도구를 새로 상시 등록하지 않는다. `/analytics record` 명령도 같은 옵션을 사용한다.

텍스트·payload·refs는 유한 JSON 값으로만 기록한다. 기존 text/line 제한을 전체 줄에 적용하며 자르지 않는다.
잘못된 구조는 `bad_metric_record`, 없거나 중복·다른 kind/ID의 정확한 대상은 `metric_reference`,
이미 바뀐 정의/초점을 수정하려는 요청은 `state_conflict`다. 판정은 맥락 동기화·lock 생성 전에 가능한 만큼 하고,
잠금 안에서 journal을 다시 읽어 대상과 상태를 다시 판정한다. 거절은 새 기록이나 표식 버전 변경을 남기지 않는다.
대상은 같은 프로젝트의 이전 seq에 유일하게 존재해야 한다. 장래 이벤트·최신 이름 추론을 허용하지 않는다.
구조화 details가 생성할 관계의 정본이다. tagged 기록에 caller가 준 `supersedes`·`updates`·`resolves`는
거절한다. 작성자가 details의 이전 정의/초점 필드와 다른 대상을 함께 바꾸거나 unrelated 기록을 숨길 수 없게 한다.
같은 검사는 부수 효과 전과 잠금 안에서 한다. 상태를 바꾸지 않는 cites/supports/contradicts/질문 연결 참조는 허용한다.

`request_id` 입력 공개는 #19 범위로 남긴다. 작성 출처는 기존 Origin과 진입점별 규칙 그대로다.

## 4. 지표 정의

`details`는 다음 필드를 가진다. 각 문자열은 비어 있지 않으며 식·모집단·단위 등의 내용을 모델이나 코드가 검사한 것으로
표시하지 않는다. `definition`은 사용자 정의 JSON 객체이고 닫힌 계산 DSL이 아니다.

| 필드 | 의미 |
|---|---|
| `contract` | `metric_definition/1` |
| `metric_id` | 안정된 논리 ID. 파일명으로 쓰지 않으며 Unicode도 보존 |
| `name` | 표시 이름. 이름이 같아도 ID가 다르면 다른 지표 |
| `status` | `draft` 또는 `specified`. 분석 검사 상태와 구별 |
| `definition` | 의미·식·단위·모집단·결측·비교 조건 등을 필요한 만큼 담는 객체. 미정이면 빈 객체 가능 |
| `unresolved` | 아직 정하지 않은 질문 문자열 목록. 빈 목록 가능 |
| `change_reason` | 처음 만들거나 의미를 수정·구체화·파생하는 이유 |
| `previous_definition` | 선택: 같은 metric ID의 이전 정의 이벤트 ID |
| `derived_from` | 다른 metric ID의 정확한 부모 정의 `{metric_id, event_id}` 목록. 처음에는 빈 목록 가능 |

첫 정의는 사용되지 않은 metric ID여야 한다. 같은 ID의 정의 변경은 `previous_definition`을 명시하고,
그 정의가 재생 결과 `current`일 때만 허용한다. 이전 정의에 `supersedes` 참조를 생성해 과거를 보존하며 새 버전을 추가한다.
서로 다른 metric ID의 정의는 `derived_from` 참조로만 계산상의 부모가 된다. 같은 ID를 부모로 삼지 않는다.
부모 정의는 이전 seq의 정확한 버전이어야 해서 버전 그래프는 순환하지 않는다. 나중에 부모 정의가 바뀌어도
이미 기록한 파생 정의의 부모를 고치지 않는다. 같은 부모/대상의 중복 참조는 거절한다.
표시 이름·설명만 바뀐 것과 의미 변경은 작성자의 `change_reason`과 definition 내용으로 남긴다. 이름 차이로 자동 판정하지 않는다.

## 5. 현재 분석 초점

| 필드 | 의미 |
|---|---|
| `contract` | `analysis_focus/1` |
| `goal`, `question`, `activity`, `next_check`, `change_reason` | 목적·현재 질문·작업 의도·다음 확인·방향을 정하거나 바꾼 이유 |
| `metrics` | 정확한 정의와 질문 안 역할 `{metric_id, event_id, role}` 목록. 미정 탐색은 빈 목록도 허용 |
| `unresolved` | 미정 질문 문자열 목록 |
| `previous_focus` | 선택: 바꾸는 이전 초점 이벤트 ID |

지표 등록 없이도 자유 분석을 실행한다. 초점은 없을 수 있고 초점의 지표는 draft 정의를 가리킬 수 있다.
첫 초점 이후 새 현재 초점은 `previous_focus`와 `supersedes`로 추가한다. 명시한 대상이 `current`가 아니거나,
서로 다른 현재 초점이 존재해 모호하면 자동으로 최신을 고르지 않는다. 오래된 손작성·tag 없는 초점은
새 구조화 현재 초점을 추정하는 근거로 삼지 않고 별도 읽기 한계로 남긴다.
질문 이벤트를 연결하려면 명시적 typed event ref를 쓸 수 있다. goal/question 텍스트만으로 질문 ID 관계를 생성하지 않는다.

run은 시작하기 직전의 유일한 유효한 구조화 현재 초점 event ID를 `analysis_focus` 참조로 기록한다.
없거나 모호하면 목적이 연결되지 않은 상태와 사유를 기록하되 실행을 막지 않는다.
재실행도 현재 초점을 기록하고 원래 run의 목적은 그대로 보존한다. 초점 참조는 작업 목적이며 측정 완료 표시가 아니다.

## 6. 지표 관측과 실제 계산 범위

관측은 명시적으로 선언한 결과와 실행 근거를 연결한다. SDK가 함수 호출을 포착한 것은 선언한 결과를 기록했다는 뜻이며,
그 코드가 정의의 식을 올바르게 구현했는지 또는 인과 결론이 맞는지는 별도의 분석 검사다.

| 필드 | 의미 |
|---|---|
| `contract` | `metric_observation/1` |
| `metric_id`, `definition_event_id` | 당시 정의의 정확한 metric ID와 이벤트 버전 |
| `run_id` | 계산 코드·입력·출력·실행 상태를 가진 run |
| `scope` | 대상·기간·필터·집계 단위·실제 매개변수 등을 담는 객체. 빈 객체도 명시적 미정 범위이며 독자가 알 수 있음 |
| `result` | 유한 JSON 결과. 숫자뿐 아니라 그룹·시계열·설명 구조도 허용 |
| `result_ref` | 선택: 그 run의 output 데이터 관측 또는 Visualization 버전 참조. 큰 결과는 `result=null`과 이 참조 사용 |
| `recomputed_from` | 선택: 이전 `metric_observation` 이벤트 ID. 새 코드·새 입력으로 같은 정의를 재계산한 관계 |
| `change_reason` | 결과 기록·재계산·코드 수정 이유 |

정의는 draft여도 관측을 기록할 수 있고, 그 상태와 미정 사항을 함께 읽는다. 정의를 지정하지 않은 기존 결과는
새 관측으로 자동 바꾸지 않는다. run의 코드와 기록된 데이터 목록으로 generated_by/uses 참조를 생성하며
데이터 capture의 observed/declared/inferred 근거를 유지한다. 입력이 없는 계산도 허용한다.

CLI·도구·run 밖 SDK는 이미 기록된 run을 지정한다. run 안 SDK에서는 현재 run을 자동으로 지정하고 관측을
메모리에 보관했다가 run 기록/완료 이벤트 뒤에 쓴다. 호출 때 payload·정의·줄 크기를 검사하며, 최종 쓰기 때도
다시 검사한다. run 오류·취소나 기록 실패가 있으면 관측을 조용히 성공으로 바꾸지 않고 계산/기록 상태를 별도로 읽는다.
실행 성공도 관측의 수학적 정확성을 검증하지 않으므로 관측의 `calculation_check`는 기본 `unverified`다.
결과 JSON 선언이나 result_ref는 run 전체와 관련된 선언이며 함수 수준 실행 계보를 주장하지 않는다.

`recomputed_from`은 유일한 이전 관측이어야 하고 동일 metric ID·동일 정의 버전일 때만 허용한다.
다른 정의의 값을 비교하려면 별도 관측과 명시적 비교 조건을 기록하며 하나의 재계산 관계로 묶지 않는다.
기간·장비별 범위 변경은 같은 정의의 다른 관측이고 새 지표를 강제하지 않는다.

## 7. 공통 읽기와 구현 경계

의미 검증·구조화 projection은 stdlib 전용 경계에 둔다. writer는 그 규칙을 호출하고 잠금/파일을 담당하며,
core는 프로젝트 읽기와 응용 호출, runner는 실제 실행과 관측 완료, CLI·SDK·호스트는 입력/Origin만 담당한다.
도메인 검증은 host·UI·저장 backend·Polars·DuckDB를 import하지 않는다.

기본 status는 유효한 현재 초점, 최신 current 정의(여러 current 버전이면 conflict), 정확한 정의/부모/관측 참조와
전체 개수·생략 위치를 제공한다. journal 원본에서 재구성하며 별도 metric catalog를 정본으로 만들지 않는다.
원본 객체와 공통 참조 판정을 계속 이용한다. #15·#18가 이 projection을 소비할 수 있게 작은 조회 함수를 공개한다.

## 8. 검증과 완료 조건

M0: 미정 후보로 실행, 다중 지표·역할·한국어 사용자 내용, 이름이 같은 다른 ID, 정의 구체화/수정,
방향 변경 이유, 이전 정의/초점과 past run 보존, 동시 수정의 state_conflict를 확인한다.

최소 M1: 숫자·표/시계열 결과, 기간/장비별 범위, 부모 버전 고정, 코드만 고친 동일 정의 재계산,
목적 연결만 있는 탐색 run과 명시적 관측, 누락·중복·장래·wrong-kind 대상, 실패/cancelled run,
비교 조건이 다른 값의 보존을 확인한다. 기존 /1·최소 /2 fixture 읽기와 거절의 무부수효과를 회귀 검증한다.
CLI·SDK·호스트 입력은 같은 계약을 적용하며 SDK run 안/밖을 별도로 시험한다.

기록 형식 PR의 구현 전 설계 리뷰(독립·Codex), 구현 리뷰(독립·Codex), 지적 처분과 수정 diff 1회,
관련 회귀·문서/lint·Linux/Windows/macOS CI, md+JSON 측정 기록을 완료 조건으로 삼는다.
실제 모델 과제, 의미식 자동 검증, 후속 검색/화면/checkpoint, 사용자 프로젝트 migration과 릴리스는 이 PR의 통과로 주장하지 않는다.
