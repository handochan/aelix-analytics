# Checkpoint와 사람·에이전트 인계 — 구현 전 설계

상태: Proposed · 2026-10-10 · Issue [#16](https://github.com/handochan/aelix-analytics/issues/16)

기준 소스는 실제 #15 main `74e5e96c4a49917ad8980b0724dc4add62bc6077`(kit 0.11.0)이다.
이 문서와 [ADR-0012](../decisions/0012-checkpoint-and-handoff.md)는 설계 리뷰 대상이며 구현 완료가 아니다.
따르는 결정은 [ADR-0006](../decisions/0006-common-record-contract.md) 1·2·3·5·6절,
[연속성 설계](../DESIGN-CONTINUITY-AND-REUSE.md#작업-구간-요약-checkpoint),
[작성 출처](../CONTRACTS.md#c-레코드), [Q2·Q12·Q17](../BETA-QUALITY-GATES.md),
[운영 리뷰 예산](../PROJECT-OPERATIONS.md#리뷰-예산)이다.

## 1. 현재 코드와 변경 범위

현재 [기록 schema](../schemas/analytics-records.schema.json)의 checkpoint는 `details.as_of_seq` 정수 0 이상과
`context_version` 문자열 또는 null을 요구한다. `relations.KINDS`가 그 종류를 이미 읽는다.
`writer.RECORD_KINDS`에는 checkpoint가 없으므로 공개 writer는 아직 작성하지 않는다.
[writer](../../src/aelix_analytics/writer.py)의 `Origin`·`Writer.locked`·`Locked.append`와
[core](../../src/aelix_analytics/core.py)의 `sync_context`·`read_model`,
[relations](../../src/aelix_analytics/relations.py)의 `Journal.through`·`prefix_revision`·`ReadModel.through`를 재사용한다.

| #16 수용 조건 | 제안하는 변경 | 검증 |
|---|---|---|
| 기준 seq·CONTEXT·근거·작성 주체 | §2의 태그 있는 checkpoint와 공통 Origin/writer | T1–T4 |
| needs_review / updates_available 구별 | §4의 독립 평가와 후속 페이지 | T5–T8 |
| 사람 → 에이전트 → 사람의 원문 보존 | 같은 CLI·SDK·도구 작성과 정확한 버전 인계 | T9–T10, 실제 Q17 별도 |
| 기본 조회·결정적 요약의 숨은 모델 호출 0 | stdlib 생성과 읽기 전용 인계 | T11–T12 |

기존 `/2` 가족과 기록·참조·상태의 의미는 유지한다. 보고서 저장·새 UI·노트북 실행·동시 공동 편집·추가 자동 모델
작업은 이 구현에 넣지 않는다. 기본 조회가 checkpoint를 생성하거나 CONTEXT를 덮어쓰지 않는다.

## 2. 저장하는 checkpoint

새 writer는 `analytics.event/2`, `kind=checkpoint`, 불변 `e<seq>`와 기존 공통 필드를 사용한다.
`text`는 작성한 요약 본문이며 기존 TEXT_LIMIT/LINE_LIMIT의 전부 저장 또는 거절 규칙을 따른다.
`actor`·`actor_basis`·`means`·선택적 session은 요청 진입점의 Origin이다. 요약을 생성한 방식은 작성 신원과 별개다.

| `details` 필드 | 뜻 / writer 규칙 |
|---|---|
| `contract` | `analysis_checkpoint/1`; 새 전문 검증을 구별하는 선택 필드 태그 |
| `as_of_seq` | 요약이 읽은 journal의 포함 상한. 정수 0 이상, bool 제외, checkpoint seq보다 작고 쓰기 때 현재 상한 이하 |
| `context_version` | 정확한 버전 파일 이름 또는 null. null은 없거나 미확인인 CONTEXT이며 필드를 생략하지 않음 |
| `summary_source` | `authored` 또는 `deterministic`. 모델이 쓴 명시적 본문은 authored이며 kit이 모델 호출 여부를 인증하지 않음 |
| `from_seq` | 작업 구간의 시작(포함), 0 이상·as_of 이하. 기본 0. 이보다 앞의 관련 근거를 인용할 수 있으며 전체 요약의 진실 범위가 아님 |
| `summary` | 아래의 구조화된 선택. 모든 목록은 명시적으로 있으며 모르는 목적/초점은 null |
| `omitted` | 각 결정적 선택 목록에서 빠진 수와 제한 이유, 더 읽는 위치. authored는 작성자가 선택한 범위이며 전체 수를 추정하지 않음 |
| `journal_basis` | `reader_format`와 `prefix_revision`의 값. 해당 기준의 원래 줄 ID·바이트 변경을 감지하는 consistency token이며 인증·파일 스냅샷 보장이 아님 |

`summary`는 `goal`(문자열|null), `focus_event_id`(정확한 ID|null), `metric_event_ids`, `finding_event_ids`,
`change_event_ids`, `question_event_ids`, `todo_event_ids`(ID 목록), `next_actions`(명시적 문자열 목록)이다.
ID 필드는 같은 프로젝트에서 그 기준까지 기록된 유일한 해당 종류의 이벤트를 가리킨다.
열린 질문/todo 목록은 그 기준에서 `open`인 대상만 포함한다. 선택한 발견의 당시 상태는 그대로 표시한다.
변경 목록은 정정/철회/복원/닫힘/재개의 기록 ID를 가리키며, 그 관계의 원래 대상과 새 기록을 그대로 따라 읽는다.
지원하는 종류인데 새 태그가 없는 지표 등은 기존 generic 기록이라는 qualification을 유지한다.

summary가 이름 붙인 대상의 `about`·`cites` 참조와 CONTEXT 버전 참조는 writer가 생성하고 같은 ID/버전을 검증한다.
CONTEXT를 알면 버전 파일의 digest를 저장한다. 호출자는 추가 `cites`·`supports`·`contradicts` 출처를 줄 수 있지만
checkpoint에 `supersedes`·`updates`·`resolves`를 붙여 다른 기록 상태를 바꾸지 못한다.
판단의 정정·철회는 기존 finding/record_update 경로로 먼저 남긴다. 본문에 쓴 자유 문장의 의미와 모든 의존성을 검증하지 않는다.
추론 결박·미확인 출처는 보존하되 확정 참조나 완료한 분석으로 올리지 않는다.
추가 이름 참조도 basis 안에서만 결박하며 이후의 최신 버전을 빌리지 않는다. 정확한 대상의 완료/기록이 기준보다 늦으면
`checkpoint_reference`로 거절한다. 원래 이름·미결박/추론 qualification과 digest는 기존 참조 계약을 따른다.

태그 없는 기존 checkpoint는 일반 기록·기준 필드를 계속 읽는다. 새 summary·출처·journal_basis를 소급 생성하지 않는다.
`analysis_checkpoint/1` 태그가 잘못된 구조면 원문을 보존하고 전문 평가를 `invalid_checkpoint`로 표시한다.
모르는 태그는 `unsupported_checkpoint_contract`이며, 알려진 이벤트 가족·kind 자체를 unknown으로 바꾸지 않는다.

## 3. 작성과 결정적 요약

기존 record 진입점에 `kind=checkpoint`와 구조화 details를 추가한다. 모델 도구 `analytics_record`의 기존 text 입력은
계속 명시적 authored 본문이다. details의 구조화 필드가 가리킨 사건과 refs를 한 공통 응용에서 검증한다.
새 상시 모델 도구나 record의 거대한 action 분기는 만들지 않는다.

CLI 편의 명령은 `analytics checkpoint [TEXT] [--ref REF ...] [--from-seq N] [--limit N]`이며,
역사 기준은 `--as-of-seq N`과 `--context-version VERSION` 또는 `--context-unknown`을 함께 받는다.
SDK 편의 함수는 `analytics.checkpoint(text=None, *, refs=(), as_of_seq=None, context_version=UNSET, from_seq=0, limit=8)`이다.
UNSET은 인자를 생략한 기본값이고 명시적 `None`은 미확인 CONTEXT다. 두 뜻을 하나의 None 기본값으로 합치지 않는다.
TEXT/text를 주면 authored, 생략하면 결정적 생성이다. `analytics record checkpoint TEXT --details-json JSON`과
SDK `analytics.record("checkpoint", text, details=...)`도 같은 writer 검증·반환 형식을 사용한다.
record의 CLI JSON 입력은 기존 `--details-json`·`--refs-json`을 사용한다. 태그·basis를 생략한 공개 authored 요청은
checkpoint 응용이 현재 basis·태그를 채우고 refs가 지정한 선택 ID를 구조화한다. 자유 본문에서 ID·의미를 추정하지 않으며,
선택하지 않은 목록은 빈 배열과 authored 선택 qualification이다. 완전한 tagged payload를 주면 그 basis/선택을 그대로 검증한다.
현재 목적은 정확한 CONTEXT 버전에서 읽거나 작성자가 명시한 goal이고, 없으면 null이다.
두 기준을 생략한 현재 작성은 kit이 CONTEXT를 버전으로 동기화한 뒤 같은 journal 기준을 채운다.
역사 기준을 지정하면 context_version도 정확한 버전 또는 명시적 null로 지정해야 하며 현재 CONTEXT를 빌려 채우지 않는다.

명시적 작성의 순서는 다음과 같다.

1. 표식·writer floor·인자·참조·본문/줄 상한을 가능한 범위에서 첫 부수 효과 전에 판정한다. legacy/unknown/conflict 프로젝트에 쓰지 않는다.
2. 현재 작성에서만 기존 sync_context 경로로 필요한 버전을 보존한다. sync의 Origin은 기존 system/진입점 means이고 checkpoint의 Origin은 요청 주체다.
3. 하나의 읽기로 기준·선택·진단을 만든다. 결정적 생성은 알려진 목적, 유일한 현재 초점, 정확한 지표 버전, 발견·변경·열린 질문·todo ID를 선택한다.
   목록별 기본 8개(허용 1–100)이며 순서는 journal seq/원래 줄 순이다. 초점/정의 충돌은 하나를 임의로 고르지 않는다.
   작업 구간은 새 발견/변경의 선택에 쓰고, 현재 초점·열린 질문과 명시적 refs는 더 오래된 사건도 포함한다.
   본문은 선택한 ID·기록 종류·현재/당시 상태와 목적/명시한 다음 행동의 결정적 표현이다. 숨은 추론이나 의미 요약은 하지 않는다.
   개별 사건의 원문은 ID로 읽고, 생성 본문이 원문 전체를 인용한 것처럼 표시하지 않는다. 생략 목록과 다음 조회 위치를 함께 저장한다.
4. journal lock 안에서 표식·기준 구간의 prefix·선택 ID·참조를 다시 판정한다. 구간이 바뀌면 `checkpoint_basis_changed`로 거절한다.
   뒤의 append만 생기면 명시한 이전 basis를 유지하고 후속 변경이 있음을 답한다. 새 기록을 옛 요약에 조용히 합치지 않는다.
5. 최초 기능 사용 전에 같은 lock 안에서 checkpoint 도입 floor 0.14.0을 올리고 완성된 한 이벤트 줄을 append한다.
   성공 뒤 written event/request_id/bindings와 basis를 답한다. 실패를 append 성공·checkpoint 생성으로 표시하지 않는다.

CONTEXT snapshot과 checkpoint append는 여러 파일의 transaction이 아니다. context 동기화 뒤 checkpoint가 실패하면
context 버전/이벤트는 남을 수 있으며 답은 그 부분 효과를 명시한다. 거절을 미리 알 수 있는 입력 오류는 sync 전에 검사한다.
잠금은 kit writer 사이의 journal 쓰기를 직렬화하며 외부 편집을 막지 않는다. 물리 파일을 쓰기 직후 바꿀 수 있으므로 reader가 다시 가용성을 판정한다.
긴 본문·refs로 기존 저장 상한을 넘으면 `text_too_long`이며, 범위를 좁히거나 본문을 직접 쓰도록 안내한다. 원문/JSON을 잘라 통과시키지 않는다.

SDK의 실행 중 호출은 기존 pending journal 규칙을 따른다. 호출 순간 읽은 basis를 payload에 유지하고 run 완료 때 재검증한다.
checkpoint에 한해서 기존 SDK가 자동으로 추가하는 진행 중 `state.run_id` 근거 참조는 생략한다. Origin과 private pending
payload/응답의 run_id는 그대로 유지하며, 그것을 summary의 과거 근거나 완료 사실로 세지 않는다. 호출자가 명시한 refs는
그대로 검증하므로 당시 미완료인 같은 run이나 basis 뒤에 완료한 다른 run을 근거로 주면 `checkpoint_reference`로 거절한다.
그때 생긴 run 완료 사실을 호출 당시 요약으로 소급하지 않는다. pending은 저장 성공이 아니며 실패는 finalize_errors로 남긴다.
완료 run 자체를 요약하려면 run 완료 뒤 별도 checkpoint를 작성한다.

## 4. 읽기 평가와 인계

평가는 한 현재 읽기와 `ReadModel.through(as_of_seq)`를 비교한다. 과거 view에 더 늦은 run/Report/import 완료와
상태 변경을 넣지 않는다. 정확한 CONTEXT 버전은 별도로 읽으며 당시 journal 기준만으로 현재 파일을 역사 맥락이라고 부르지 않는다.
역사 판단은 그 시점의 기록이고 참조 파일 존재/해시는 현재 판정이다. 보존된 본문과 해석을 함께 돌려준다.

| 출력 | 판정 |
|---|---|
| `needs_review` | 선택한 판단에 기준 뒤 정정·철회·복원·conflict·질문/todo 닫힘/재개가 적용됨, 선택한 정확한 근거/CONTEXT가 missing/changed/unsupported/ambiguous, 또는 저장 기준 prefix 변경이면 true. 알려진 구조·기준·참조에서 그런 신호가 없으면 false. 전문 평가가 불가능하면 null과 사유 |
| `updates_available` | checkpoint 자체를 제외하고 `as_of_seq` 뒤의 journal 기록이 있으면 true. 없으면 false. journal을 읽거나 기준을 확정할 수 없으면 null과 사유 |
| `review_reasons` | 대상 ID, 당시/현재 상태 또는 참조 resolution, 변경 이벤트 ID와 원문 상세 위치. 여러 정정을 하나의 최신 진실로 고르지 않음 |
| `updates` | 기준 뒤 기록의 제한 페이지. `source_revision`·as_of·returned/total/has_more/omitted/cursor/read_more는 #15와 같은 계약 |
| `qualification` | generic/invalid/unsupported checkpoint, shared ID, undated/damaged/unsupported journal, prefix token 형식 불일치, CONTEXT 미확인 등 |

두 값은 독립적이다. 새 무관한 note면 `false/true`, 정정이 추가되면 `true/true`, 원장 append 없이 근거 파일이
사라지면 `true/false`다. null을 false나 최신 유효 판단으로 바꾸지 않는다. 이미 미확인인 참조도 확인 필요 사유를 보인다.
철회 후 복원·질문/todo 닫힘 후 재개로 현재 상태 문자열이 처음과 같아도 그 사이에 적용된 판단 변경은 review 이유에 남긴다.
선택하지 않은 무관한 todo의 갱신은 그 자체로 needs_review가 아니며 새 기록의 updates_available로 구별한다.
`needs_review=false`는 선택된 기계적 신호가 없다는 뜻이며 자유 본문의 의미·통계적 검사·확인하지 않은 의존성이 검토됐다는 뜻이 아니다.
reader_format이 달라 prefix token을 비교할 수 없으면 명시적 qualification과 null을 답하며 원문을 읽을 수 있게 한다.
CONTEXT의 저장되지 않은 편집은 `context_pending`으로 따로 표시하며 read에서 sync/append하지 않는다.

기본 `analytics status --part context`와 SDK/도구의 같은 part에 가장 최근에 쓴 해석 가능한 checkpoint의 작은 요약·basis·평가·후속 위치를 더한다.
최근 checkpoint에 확인 필요/미지원 문제가 있으면 그 사실을 먼저 보이고 더 오래된 요약을 현재 것으로 조용히 대체하지 않는다.
기존 기본 overview와 6개 도구를 유지한다. 선택 ID의 records 상세는 checkpoint 본문/details를 통째로 읽고,
relations는 같은 정확한 근거/정정 링크를 제한 페이지로 확장한다. 평가·읽기는 통지 발행/창 소비·원장 변경을 하지 않는다.
후속 페이지의 held as_of/prefix/cursor는 #15 규칙을 재사용하고, live source 변경 시 retry/updating/stale_cursor를 그대로 보인다.

새 세션은 checkpoint를 현재 사실로 바로 답하지 않고 basis·needs_review·updates_available와 참조를 읽는다.
필요한 정정/새 질문/다음 행동을 후속 페이지에서 읽고 이후의 분석은 새 run·기록으로 남긴다.
사람은 같은 CLI·SDK·ID 조회로 에이전트가 남긴 기록을 보고 정정한다. 세션 파일·대화 복사·작성자 인증을 인계의 전제조건으로 삼지 않는다.

## 5. 계층·호환성과 전달 순서

checkpoint 구조 판정·결정적 선택/평가는 stdlib 의미 계층에서 처리하고, 공통 writer가 생성 참조와 잠금/floor 검증을 소유한다.
core 응용은 Project/read_model/context sync를 조율한다. CLI·SDK·확장·렌더는 같은 응용 결과를 표현하며 자체 prefix parser나 별도 상태 재생을 만들지 않는다.
`core`·`relations`는 Aelix·모델·분석 라이브러리를 import하지 않는다. 기존 reverse import/layer 시험을 유지한다.

설계 PR은 이 문서·ADR·관련 링크만 바꾼다. schema 예제/전문 필드 조건, writer/reader, CLI·SDK/호스트, 저장/인계 fixture는
채택 뒤 구현 PR에 함께 넣고 kit 0.14.0과 floor를 실제 도입한다. 과거 /1·/2 줄·CONTEXT·run/Report/import bytes는 변환하지 않는다.
실제 B3-b 0.12.0 → B3-c 0.13.0 main 순서를 지키고 구현 최종 gates 직전에 rebase한다.
현재 발견된 런처 status/context 인자 전달 누락은 #17 B3-b 통합이 고친다. #16은 그 실제 main의 두 런처 동작/읽기 경계를 검증한다.

## 6. 구현 검증 계획

| 시험 | 합성 시나리오 / 확인할 것 |
|---|---|
| T1 | 새 tagged checkpoint의 필수 basis/선택/Origin과 schema 예제. future/bool/잘못된 종류/중복 ID/모순 refs 거절, 변경 전후 원문 bytes 유지 |
| T2 | generic 기존 /2 checkpoint·미래 태그·모르는 선택 필드·mixed /1·/2·damaged journal. 원문 보존, 전문 평가 qualification, 거짓 확정 0 |
| T3 | current CONTEXT 버전+digest/명시적 historical 버전/unknown null. 현재 파일을 과거 맥락으로 채우지 않고 sync 실패/부분 효과를 표시 |
| T4 | 표식/legacy/unknown/conflict/oldwriter gate, 첫 효과 전 오류, 최초 기능 floor0.14; metric/asset floors 유지. prefix 변경과 append-only 경계 재검증 |
| T5 | finding 정정·withdrawal·복원·충돌, 선택한 question/todo resolve/reopen, 지표 정의/초점 교체; review 원인/old/new ID와 과거 원문 유지. todo 닫힘→재개로 open이 같아도 변경 이유 유지, 무관한 todo 갱신은 구별 |
| T6 | 무관한 append·checkpoint 자체·새 checkpoint·파일 missing/changed: false/true, true/true, true/false 및 평가 불가 null 구별 |
| T7 | historical as_of에 이후 run/Report/import 완료·상태가 섞이지 않음. 근거 가용성의 현재 판정과 구별 |
| T8 | 후속 변경 page/cursor/같은 기준/읽는 중 변경·cache 없음/손상. 필드/JSON 자르지 않음, omitted/read_more 위치 유지 |
| T9 | 사람이 CLI/SDK authored·deterministic checkpoint 작성 → 모의 에이전트 Origin/tool/agentCLI → 사람이 정정·새 checkpoint; 별도 ID/주체/means, session 없어도 인계. 진행 중 checkpoint의 Origin/private pending run_id 유지 |
| T10 | 실제 배포본/두 런처에서 Unicode·공백 경로·status3parts/context 인자와 exit parity, 원장 쓰기/자동 asset publication 0. SDK refs 없는 기본 pending checkpoint가 같은 call-time basis로 성공 append됨을 확인하고, 명시적 미완료/기준 뒤 완료 run 근거 거절과 finalize_errors를 별도 확인 |
| T11 | provider/send_message/model 경로를 호출 즉시 실패하도록 무장한 host 시험. 기본 읽기/결정적 생성 시 호출·시도0, 새 상시 모델 도구0 |
| T12 | exact-head 모델 없는 CLI·SDK/stdlib·layer/host 회귀, Linux/Windows/macOS artifact/context/JUnit/smoke. 실제 모델/사람 Q17은 별도 미실행으로 기록 |

구현 PR은 새 기록 형식의 구현 리뷰(독립 1회·별도 Codex 1회)와 수정 diff 1회, 영향받는 시험/CI를 따른다.
저장 필드·schema 변경의 실제 source measurement md+JSON에는 명령·환경·scope·bytes/원본 해시·실패/skip/not_run을 남긴다.
토큰은 tokenizer로 측정했을 때만 토큰이며, 결정적 fixture/모의 Origin 교대는 실제 Q17-b가 아니다.
Q2·Q12·Q17 전체 beta gate나 릴리스 완료는 이 설계/합성 구현 검증으로 선언하지 않는다.
